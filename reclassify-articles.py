#!/usr/bin/env python3
"""
Re-classify all articles in data.json using a 3-layer pipeline:

  Layer 1: Keyword pre-filter — drop obviously irrelevant articles (ads, sports, fashion, real estate)
  Layer 2: AI classify — send each surviving article through the LLM classifier
  Layer 3: Category review — batch-review per-category against research MD files to catch misclassifications

Usage:
    python reclassify-articles.py                    # full 3-layer run + KV upload
    python reclassify-articles.py --dry-run          # preview without writing
    python reclassify-articles.py --skip-upload      # write file, skip KV
    python reclassify-articles.py --layer 2          # run layers 1-2 only (skip category review)
    python reclassify-articles.py --category <id>    # reclassify one category only
"""

import json
import sys
import os
import re
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ─── Load .env ───
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=True)
except ImportError:
    pass

# ─── Configuration ───
ROOT = os.path.dirname(os.path.abspath(__file__))
LLAMA_CPP_URL = os.environ.get("LLAMA_CPP_URL", "http://localhost:8080")
LLAMA_API_KEY = os.getenv("LLAMA_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "Qwen3.6-27B")

CATEGORIES_FILE = os.path.join(ROOT, "categories.json")
PROMPTS_FILE = os.path.join(ROOT, "prompts.json")
DATA_FILE = os.path.join(ROOT, "app", "data.json")
RESEARCH_DIR = os.path.join(ROOT, "research")

# ═══════════════════════════════════════════════════════════════════════
# Layer 1: Keyword Pre-Filter
# ═══════════════════════════════════════════════════════════════════════

# Keywords that signal an article is NOT about Middle East peace/conflict
EXCLUDE_KEYWORDS = [
    # Sports
    "world cup", "fifa", "afcon", "premier league", "man city", "guardiola",
    "champions league", "transfer", "football", "basketball", "soccer",
    "tennis", "olympics", "marathon", "racing", "formula 1",
    # Fashion / lifestyle
    "fragrance", "bakhoor", "perfume", "fashion week", "runway",
    "style", "boutique", "haute couture", "wardrobe",
    # Real estate / property
    "real estate", "property investment", "metro station", "ferry crossing",
    "housing market", "apartments for sale", "villa",
    # Entertainment / celebrity
    "hollywood", "celebrity", "sydney sweeney", "euphoria", "tv show",
    "netflix", "streaming", "movie", "film festival", "award ceremony",
    # Random noise
    "sponsored", "ad", "advertisement", "billboard", "scabies",
    "poland", "nyc mayor", "parizel", "meyns", "jouan",
    "authoritarian transformation", "istanbul", "ferrari",
    "metro station guide", "ferry crossings",
    # Health / science (unless ME-specific)
    "secondhand smoke", "smoke in public", "vaccine", "pandemic",
    # Other countries not in ME scope
    "pakistan", "bangladesh", "india", "china", "europe",
]

# Keywords that OVERRIDES exclusion — if the article contains these,
# it IS relevant even if it also matches an exclude keyword
ME_OVERRIDE_KEYWORDS = [
    "israel", "palestine", "gaza", "iran", "syria", "lebanon", "jordan",
    "saudi", "uae", "qatar", "yemen", "iraq", "turkey", "egypt",
    "hezbollah", "hamas", "houthi", "west bank", "doha", "beirut",
    "tehran", "damascus", "amman", "riyadh", "dubai", "cairo",
    "abraham accords", "normalization", "mideast", "middle east",
    "ceasefire", "cease-fire", "un", "security council",
    "operation roaring lion", "strait of hormuz",
]


def layer1_pre_filter(articles):
    """Layer 1: Remove obviously irrelevant articles using keyword matching.

    Returns (kept, filtered_out) — two lists of articles.
    """
    kept = []
    filtered = []

    for article in articles:
        text = f"{article['text']} {article.get('snippet', '')}".lower()

        # Check if it matches any exclude keyword
        excluded = any(kw in text for kw in EXCLUDE_KEYWORDS)

        # But if it also matches ME override keywords, keep it
        if excluded:
            if any(kw in text for kw in ME_OVERRIDE_KEYWORDS):
                kept.append(article)
                continue
            else:
                filtered.append(article)
                continue

        kept.append(article)

    return kept, filtered


# ═══════════════════════════════════════════════════════════════════════
# Layer 2: AI Classification
# ═══════════════════════════════════════════════════════════════════════

def load_prompts():
    if os.path.exists(PROMPTS_FILE):
        with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    print(f"  {PROMPTS_FILE} not found!")
    sys.exit(1)


def load_categories():
    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        cats = json.load(f)
    return {c["id"]: c for c in cats}


def build_system_prompt(cat_map, prompts):
    """Build the classifier system prompt from categories.json."""
    cat_ids = list(cat_map.keys())
    lines = []
    for cid in cat_ids:
        c = cat_map[cid]
        line = f"  {cid}: {c['name']} — {c['description']}"
        phases = c.get("phases", [])
        if phases:
            line += f"\n    Phases: {' → '.join(phases)}"
        lines.append(line)
    block = "\n".join(lines)
    cat_list = ", ".join(cat_ids)
    return prompts["classifier"]["system"].format(
        CATEGORIES_BLOCK=block,
        CATEGORY_IDS=cat_list
    )


def classify_article(article, system_prompt, prompts):
    """Send one article to the LLM classifier. Returns parsed JSON or None."""
    snippet = article.get("snippet", "")
    source_line = f"\n<source>{article['source']}</source>" if article.get("source") else ""

    user_prompt = prompts["article_user"]["user"].format(
        TITLE=article["text"],
        SNIPPET=snippet or article["text"],
        SOURCE_LINE=source_line
    )

    body = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 8000,
        "temperature": 0.0,
    }

    headers = {"Content-Type": "application/json"}
    if LLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {LLAMA_API_KEY}"

    req = Request(
        f"{LLAMA_CPP_URL}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    try:
        with urlopen(req, timeout=180) as f:
            response = json.loads(f.read().decode())
    except Exception as e:
        print(f"  AI unavailable: {e}")
        return None

    result_text = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

    if not result_text:
        return {"_refused": True}

    # Strip markdown code fences
    if result_text.startswith("```"):
        lines = result_text.split("\n")
        result_text = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else "".join(lines[1:]).strip()

    # Extract JSON
    first_brace = result_text.find('{')
    last_brace = result_text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            obj = json.loads(result_text[first_brace:last_brace+1])
            if "me_relevant" in obj:
                return obj
        except json.JSONDecodeError:
            pass

    return {"_refused": True}


def layer2_ai_classify(articles, system_prompt, prompts, valid_ids):
    """Layer 2: Send each article through the AI classifier.

    Returns (classified, refused, not_relevant, failed) —
      classified: dict {link: {category, sentiment, risk}}
      refused: list of articles the AI refused to classify
      not_relevant: list of articles AI marked me_relevant=false
      failed: list of articles that failed (AI unreachable)
    """
    classified = {}
    refused = []
    not_relevant = []
    failed = []

    print(f"\n🧠 Layer 2: AI Classifying {len(articles)} articles...")

    for idx, article in enumerate(articles):
        t0 = time.time()
        result = classify_article(article, system_prompt, prompts)
        elapsed = time.time() - t0

        if result is None:
            # Retry once
            result = classify_article(article, system_prompt, prompts)
            if result is None:
                failed.append(article)
                if len(failed) >= 5:
                    print(f"\n  Too many AI failures ({len(failed)}), stopping.")
                    break
                continue

        if isinstance(result, dict) and result.get("_refused"):
            refused.append(article)
            continue

        if result and result.get("me_relevant"):
            cat = result.get("category") or result.get("solution")
            if cat not in valid_ids:
                # Invalid category — treat as refused
                refused.append(article)
                continue

            classified[article["link"]] = {
                "category": cat,
                "sentiment": result.get("sentiment", "neutral"),
                "risk": result.get("risk", 5),
            }
        else:
            not_relevant.append(article)

        if (idx + 1) % 20 == 0 or idx == len(articles) - 1:
            print(f"  [{idx+1}/{len(articles)}] classified={len(classified)}, refused={len(refused)}, not-relevant={len(not_relevant)}, failed={len(failed)}")

    print(f"\n  Layer 2 complete: {len(classified)} classified, {len(refused)} refused, {len(not_relevant)} not relevant, {len(failed)} failed")
    return classified, refused, not_relevant, failed


# ═══════════════════════════════════════════════════════════════════════
# Layer 3: Category Review via AI
# ═══════════════════════════════════════════════════════════════════════

def load_research_file(cat_id):
    """Load the research MD file for a category. Returns text content or None."""
    # Map category IDs to research filenames
    filename_map = {
        "india-middle-east-europe-economic-corridor": "imec-corridor.md",
        "geneva-initiative": "geneva-initiative.md",
        "abraham-accords": "abraham-accords.md",
        "20-point-gaza-peace-plan": "gaza-peace-plan.md",
        "israeli-annexation-of-the-west-bank": "west-bank-annexation.md",
        "iran-us-peace-process": "iran-us-peace-process.md",
        "lebanon-hezbollah-conflict": "lebanon-hezbollah.md",
    }
    filename = filename_map.get(cat_id)
    if not filename:
        return None
    path = os.path.join(RESEARCH_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None


def layer3_category_review(classified, cat_map, prompts, event_lookup):
    """Layer 3: For each category, batch-review the assigned articles against
    the research MD file to catch misclassifications.

    Uses the LLM with the research context to review articles in batches.

    Returns dict {link: new_category} for articles that should be moved.
    """
    # Group classified articles by category
    by_category = {}
    for link, info in classified.items():
        orig = event_lookup.get(link, {})
        by_category.setdefault(info["category"], []).append({
            "link": link,
            "text": orig.get("text", ""),
            "snippet": orig.get("snippet", ""),
        })

    moves = {}  # {link: new_category_id}

    for cat_id, articles in by_category.items():
        research = load_research_file(cat_id)
        if not research:
            continue

        # Batch size: send up to 20 articles at a time to keep prompt manageable
        batch_size = 20
        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]
            result = review_batch(cat_id, batch, research, cat_map, prompts)
            if result:
                for link, new_cat in result.items():
                    if new_cat != cat_id:
                        moves[link] = new_cat

    if moves:
        print(f"\n  Layer 3: {len(moves)} articles flagged for reclassification:")
        for link, new_cat in moves.items():
            orig = event_lookup.get(link, {})
            print(f"    [{orig.get('text', '')[:70]}] → {new_cat}")
    else:
        print(f"\n  Layer 3: No misclassifications detected")

    return moves


def review_batch(cat_id, articles, research_context, cat_map, prompts):
    """Send a batch of articles to the LLM for category review.

    The LLM reads the research MD context and determines if each article
    belongs in this category or should be moved elsewhere.

    Returns dict {link: category_id} for articles to move.
    """
    cat = cat_map.get(cat_id, {})
    cat_name = cat.get("name", cat_id)

    # Build article list for the prompt
    article_lines = []
    for idx, a in enumerate(articles):
        snippet_preview = a.get('snippet', '')[:100]
        article_lines.append(f"{idx+1}. {a['text']} — {snippet_preview}")
    articles_text = "\n".join(article_lines)

    # List all category IDs
    cat_list = []
    for cid, c in cat_map.items():
        cat_list.append(f"  {cid}: {c['name']} — {c['description'][:80]}")
    categories_block = "\n".join(cat_list)

    prompt = (
        f"You are reviewing articles assigned to the category: {cat_name}\n\n"
        f"Here is the research context for this category:\n\n"
        f"{research_context}\n\n"
        f"These articles were assigned to this category by an AI classifier.\n"
        f"Some may be misclassified. Review each one and decide:\n"
        f"1. Does it belong here? → keep\n"
        f"2. Should it be moved to another category? → specify the correct category ID\n"
        f"3. Is it not actually relevant to any peace path? → mark as 'drop'\n\n"
        f"IMPORTANT: Only mark as 'drop' if the article is clearly about a completely\n"
        f"unrelated topic (e.g., sports, entertainment, fashion). Articles about domestic\n"
        f"issues in a conflict country (human rights, economy, politics) ARE relevant\n"
        f"to that country's peace/conflict category — keep them.\n\n"
        f"Available categories:\n{categories_block}\n\n"
        f"Articles to review:\n{articles_text}\n\n"
        'Output ONLY a JSON object:\n'
        '{"review": {"1": "keep", "2": "iran-us-peace-process", "3": "drop"}}'
    )

    result = _llm_chat([
        {"role": "system", "content": "Middle East analyst. Output ONLY valid JSON with key 'review'. No explanation."},
        {"role": "user", "content": prompt}
    ], max_tokens=4000, timeout=180)

    if result and "review" in result:
        # Map numeric indices back to links
        moves = {}
        review = result["review"]
        for idx_str, decision in review.items():
            try:
                idx = int(idx_str) - 1
                if idx < len(articles):
                    link = articles[idx]["link"]
                    if decision == "drop":
                        moves[link] = None  # will be removed
                    elif decision != "keep":
                        moves[link] = decision
            except (ValueError, IndexError):
                pass
        return moves
    return None


def _llm_chat(messages, max_tokens=4000, temperature=0.0, timeout=180):
    """Generic LLM chat call. Returns parsed JSON or None on failure."""
    body = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    if LLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {LLAMA_API_KEY}"

    req = Request(
        f"{LLAMA_CPP_URL}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    try:
        with urlopen(req, timeout=timeout) as f:
            response = json.loads(f.read().decode())
    except Exception as e:
        print(f"  AI unavailable: {e}")
        return None

    result_text = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if result_text.startswith("```"):
        lines = result_text.split("\n")
        result_text = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else "".join(lines[1:]).strip()

    first_brace = result_text.find('{')
    last_brace = result_text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(result_text[first_brace:last_brace+1])
        except json.JSONDecodeError:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════════
# Build Output
# ═══════════════════════════════════════════════════════════════════════

def compute_direction(events):
    if not events:
        return "stable"
    pos = sum(1 for e in events if e["sentiment"] == "positive")
    neg = sum(1 for e in events if e["sentiment"] == "negative")
    ratio = pos / (pos + neg) if (pos + neg) > 0 else 0.5
    if ratio > 0.65:
        return "advancing"
    elif ratio < 0.35:
        return "stalling"
    return "stable"


def determine_phases_ai(solution_events, cat_map):
    """Ask the LLM to determine the current phase for each solution based on recent events.

    Returns dict {solution_id: phase_index} or None on failure.
    Uses the last 8 events per solution to keep the prompt short.
    """
    blocks = []
    for sol_id, events in solution_events.items():
        cat = cat_map.get(sol_id)
        if not cat:
            continue
        phases = cat.get("phases", [])
        if not phases:
            continue
        recent = sorted(events, key=lambda e: e.get("date", ""), reverse=True)[:8]
        event_lines = "\n".join(
            f"    - [{e['sentiment']}] {e['text']}" for e in recent
        )
        phase_names = "\n".join(f"  {i}: {p}" for i, p in enumerate(phases))
        blocks.append(
            f"<solution id=\"{sol_id}\">\n"
            f"  Name: {cat['name']}\n"
            f"  Phases:\n{phase_names}\n"
            f"  Recent events:\n{event_lines}\n"
            f"</solution>"
        )

    if not blocks:
        return None

    solutions_text = "\n\n".join(blocks)

    prompt = (
        "You are a Middle East peace analyst. For each solution below, determine which phase it is currently in.\n\n"
        "Read the recent events and match them to the phase that best describes the current state.\n\n"
        "Rules:\n"
        "- If recent events show escalation/violence, the phase should be earlier (crisis/fighting).\n"
        "- If recent events show negotiations/agreements, the phase should advance.\n"
        "- Be realistic — don't over-advance a phase based on one positive article.\n\n"
        f"{solutions_text}\n\n"
        'Output ONLY a JSON object:\n'
        '{"phases": {"solution-id": 2, "another-id": 0}}'
    )

    result = _llm_chat([
        {"role": "system", "content": "Middle East analyst. Output ONLY valid JSON with key 'phases'. No explanation."},
        {"role": "user", "content": prompt}
    ], max_tokens=4000, timeout=180)

    if result and "phases" in result:
        # Normalize: values may be strings, convert to int
        phases = {}
        for sid, val in result["phases"].items():
            try:
                phases[sid] = int(val)
            except (ValueError, TypeError):
                pass
        return phases
    return None


def build_output(classified, event_lookup, cat_map, moves=None, refused_articles=None):
    """Build the final data.json structure from classified articles."""
    if moves is None:
        moves = {}

    # Apply Layer 3 moves
    for link, new_cat in moves.items():
        if new_cat is None:
            # Drop this article
            if link in classified:
                del classified[link]
        elif link in classified:
            classified[link]["category"] = new_cat

    # For refused articles, keep them in their original category
    if refused_articles:
        for article in refused_articles:
            link = article.get("link", "")
            if link and link not in classified:
                orig = event_lookup.get(link, {})
                orig_cat = None
                # Find original category
                for sol in original_data.get("solutions", []):
                    for ev in sol.get("events", []):
                        if ev.get("link") == link:
                            orig_cat = sol["id"]
                            break
                    if orig_cat:
                        break
                if orig_cat:
                    classified[link] = {
                        "category": orig_cat,
                        "sentiment": orig.get("sentiment", "neutral"),
                        "risk": orig.get("ai_risk", 5),
                    }

    # Group by category
    by_category = {}
    for link, info in classified.items():
        cat_id = info["category"]
        if cat_id not in cat_map:
            continue  # skip unknown categories
        by_category.setdefault(cat_id, []).append({
            "link": link,
            "sentiment": info["sentiment"],
            "risk": info["risk"],
        })

    # Build event list per category
    solutions = []
    active_solutions = []
    counts = {"advancing": 0, "stable": 0, "stalling": 0}

    for cat_id, articles in by_category.items():
        if not articles:
            continue

        cat = cat_map[cat_id]

        # Build event list from original data
        events = []
        for a in articles:
            orig = event_lookup.get(a["link"], {})
            events.append({
                "date": orig.get("date", ""),
                "text": orig.get("text", ""),
                "sentiment": a["sentiment"],
                "source": orig.get("source", ""),
                "link": a["link"],
                "snippet": orig.get("snippet", ""),
                "ai_risk": a["risk"],
            })

        events.sort(key=lambda e: e.get("date", ""), reverse=True)
        direction = compute_direction(events)
        counts[direction] += 1

        summary_text = events[0]["text"] if events else ""
        stored_events = events[1:]  # exclude summary

        solutions.append({
            "id": cat_id,
            "icon": cat.get("icon", "📌"),
            "name": cat["name"],
            "phases": cat.get("phases", ["Emerged", "Developing", "Maturing", "Resolved"]),
            "phaseIndex": 0,  # placeholder — will be set by AI below
            "direction": direction,
            "keyMetric": {"label": "Events", "value": str(len(events))},
            "summary": summary_text,
            "events": stored_events,
            "confidence": "high" if len(events) > 5 else "medium" if len(events) > 2 else "low",
            "core": cat.get("core", False),
        })
        active_solutions.append(cat_id)

    # Sort by event count, keep top 8
    solutions.sort(key=lambda s: int(s["keyMetric"]["value"]), reverse=True)
    solutions = solutions[:8]
    active_solutions = [s["id"] for s in solutions]

    # AI-determine phases for each solution
    solution_events = {}
    for sol in solutions:
        all_events = sol["events"] + ([{"text": sol["summary"], "sentiment": "neutral"}] if sol["summary"] else [])
        solution_events[sol["id"]] = all_events
    ai_phases = determine_phases_ai(solution_events, cat_map)
    if ai_phases:
        print(f"\n  🧠 AI phases: {ai_phases}")
        for sol in solutions:
            if sol["id"] in ai_phases:
                sol["phaseIndex"] = min(ai_phases[sol["id"]], len(sol["phases"]) - 1)
    else:
        print("\n  ⚠ AI phase determination failed, defaulting to phase 0")

    # Momentum
    if counts["advancing"] > counts["stalling"]:
        m_dir, m_label = "advancing", "Net Positive"
    elif counts["stalling"] > counts["advancing"]:
        m_dir, m_label = "stalling", "Net Negative"
    else:
        m_dir, m_label = "stable", "Mixed Signals"

    return {
        "solutions": solutions,
        "activeSolutions": active_solutions,
        "overallMomentum": {
            "direction": m_dir,
            "label": m_label,
            "summary": f"{counts['advancing']} advancing, {counts['stable']} stable, {counts['stalling']} stalling ({len(active_solutions)} active). {len(classified)} articles classified.",
        },
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "source": "reclassify-articles",
        "feedCount": len(classified),
        "aiVersion": "3.0.0",
    }


# Global reference for original data
original_data = None

# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="3-Layer AI Article Reclassification")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing")
    parser.add_argument("--skip-upload", action="store_true", help="Don't upload to KV")
    parser.add_argument("--layer", type=int, default=3, help="Max layer to run (1=pre-filter only, 2=+AI classify, 3=+category review)")
    parser.add_argument("--category", type=str, default=None, help="Reclassify only one category")
    args = parser.parse_args()

    # Load data
    print("Loading data.json...")
    global original_data
    original_data = json.load(open(DATA_FILE, encoding="utf-8"))

    # Collect all unique articles
    all_articles = []
    seen_links = set()
    for sol in original_data.get("solutions", []):
        for ev in sol.get("events", []):
            link = ev.get("link", "")
            if link and link not in seen_links:
                seen_links.add(link)
                all_articles.append({
                    "text": ev["text"],
                    "snippet": ev.get("snippet", ""),
                    "source": ev.get("source", ""),
                    "link": link,
                    "date": ev.get("date", ""),
                })

    # Build event lookup
    event_lookup = {}
    for sol in original_data.get("solutions", []):
        for ev in sol.get("events", []):
            link = ev.get("link", "")
            if link:
                event_lookup[link] = ev

    print(f"Found {len(all_articles)} unique articles")

    # Filter by category if requested
    if args.category:
        cat_map = load_categories()
        if args.category not in cat_map:
            print(f"Category '{args.category}' not found in categories.json")
            sys.exit(1)
        cat_articles = []
        for sol in original_data["solutions"]:
            if sol["id"] == args.category:
                for ev in sol.get("events", []):
                    link = ev.get("link", "")
                    if link:
                        cat_articles.append({
                            "text": ev["text"],
                            "snippet": ev.get("snippet", ""),
                            "source": ev.get("source", ""),
                            "link": link,
                            "date": ev.get("date", ""),
                        })
        all_articles = cat_articles
        print(f"Reclassifying only {len(all_articles)} articles from '{args.category}'")

    # ─── Layer 1: Pre-filter ───
    print("\n" + "=" * 60)
    print("LAYER 1: Keyword Pre-Filter")
    print("=" * 60)
    kept, filtered_out = layer1_pre_filter(all_articles)
    print(f"  Kept: {len(kept)}, Filtered: {len(filtered_out)}")
    if filtered_out:
        print(f"  Filtered articles (first 5):")
        for a in filtered_out[:5]:
            print(f"    - {a['text'][:80]}")

    if args.layer < 2:
        print("\n  Stopping after Layer 1 (--layer 1)")
        return

    # ─── Layer 2: AI Classify ───
    prompts = load_prompts()
    cat_map = load_categories()
    system_prompt = build_system_prompt(cat_map, prompts)
    valid_ids = list(cat_map.keys())

    classified, refused, not_relevant, failed = layer2_ai_classify(
        kept, system_prompt, prompts, valid_ids
    )

    if not classified:
        print("No articles classified. Aborting.")
        return

    if args.layer < 3:
        print("\n  Stopping after Layer 2 (--layer 2)")
        moves = {}
    else:
        # ─── Layer 3: Category Review ───
        print("\n" + "=" * 60)
        print("LAYER 3: Category Review")
        print("=" * 60)

        # Fill in article text/snippet for review
        for cat_id, articles in classified.items():
            pass  # will be handled in review_batch

        moves = layer3_category_review(classified, cat_map, prompts, event_lookup)

    # ─── Build Output ───
    print("\n" + "=" * 60)
    print("BUILDING OUTPUT")
    print("=" * 60)

    data = build_output(classified, event_lookup, cat_map, moves, refused)

    # Print summary
    print("\n--- RECLASSIFICATION RESULTS ---")
    for sol in data["solutions"]:
        d = "🟢" if sol["direction"] == "advancing" else "🟡" if sol["direction"] == "stalling" else "🔵"
        phase = sol["phases"][sol["phaseIndex"]]
        print(f"  {sol['icon']} {sol['name']:35s} {sol['direction']:10s} {d} {sol['keyMetric']['value']} events → {phase}")

    print(f"\n  Layer 1 filtered: {len(filtered_out)}")
    print(f"  Layer 2 classified: {len(classified)}")
    print(f"  Layer 2 refused: {len(refused)}")
    print(f"  Layer 2 not relevant: {len(not_relevant)}")
    print(f"  Layer 3 moves: {len(moves)}")

    if args.dry_run:
        print("\n[Dry run — no file written]")
        return

    # Write file
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Written to {DATA_FILE}")

    # Upload to KV
    if not args.skip_upload:
        print("\nUploading to Cloudflare KV...")
        import subprocess
        kv_id = "badf4fb7acfe4d1c905db77ed8d5e70f"
        cmd = f'npx wrangler kv key put "data.json" --namespace-id={kv_id} --path="{DATA_FILE}" --remote'
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=60)
        if result.returncode == 0:
            print("  ✓ Uploaded to KV")
        else:
            print(f"  ⚠ Upload failed")


if __name__ == "__main__":
    main()
