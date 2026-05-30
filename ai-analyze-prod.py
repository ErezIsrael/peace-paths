#!/usr/bin/env python3
"""
Peace Room AI Analyzer — Production
====================================

Pipeline:
  [Raw RSS Feed] -> [Extract 1200 chars] -> [Single-article LLM inference]
                                                      |
                                        [me_relevant:true]    [me_relevant:false]
                                              |                       |
                                      [classify category]     [silently drop]

Modes:
  --fast   — Hourly: fetch recent articles (last 2h), merge into existing solutions.json
  --daily  — Daily: full fetch (7-day window), overwrite solutions.json
  (default) — Same as --daily

Flags:
  --deploy — After analysis, deploy to Cloudflare Pages via wrangler
  --skip-upload — Skip Cloudflare API upload (use --deploy instead)

Run: python ai-analyze-prod.py
Run: python ai-analyze-prod.py --fast
Run: python ai-analyze-prod.py --daily --deploy
Run: python ai-analyze-prod.py --categories "id:name:description"

Schedule: --fast every hour; --daily every 12h
"""

import json
import sys
import os
import re
import html
import time
import hashlib
import concurrent.futures
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ─── Load .env ──────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        print(f"  .env loaded from {_env_path}")
except ImportError:
    pass  # python-dotenv optional

# ─── Version ─────────────────────────────────────────────────────────
SCRIPT_VERSION = "1.0.0"

# ─── Configuration ───────────────────────────────────────────────────

LLAMA_CPP_URL = os.environ.get("LLAMA_CPP_URL", "http://localhost:8080")  # set in .env — not hardcoded
LLAMA_API_KEY = os.getenv("LLAMA_API_KEY", "")  # optional

CLOUDFLARE_PAGES_PROJECT = "peace-paths"
CLOUDFLARE_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
AI_MODEL = os.getenv("AI_MODEL", "Qwen3.6-27B")
CLOUDFLARE_ACCOUNT = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")

# Output — write to local file, then push to Cloudflare
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
DATA_FILE = os.path.join(DATA_DIR, "solutions.json")
TAXONOMY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taxonomy.json")

MAX_ARTICLES_PER_FEED = 8
MAX_AGE_DAYS = 7
FAST_AGE_HOURS = 2  # --fast: only articles from last N hours

# ─── RSS Feeds (loaded from rss-feeds.json) ─────────────────────────

RSS_FEEDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rss-feeds.json")


def load_rss_feeds():
    """Load RSS feeds from rss-feeds.json.
    Returns list of (name, url, type) tuples.
    """
    if not os.path.exists(RSS_FEEDS_FILE):
        print(f"❌ {RSS_FEEDS_FILE} not found. Copy rss-feeds.example.json to rss-feeds.json.")
        sys.exit(1)
    with open(RSS_FEEDS_FILE, "r", encoding="utf-8") as f:
        feeds = json.load(f)
    return feeds

# ─── Categories (loaded from categories.json) ────────────────────────

CATEGORIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")


def load_categories():
    """Load categories from categories.json.
    Returns (categories_dict, all_ids, core_ids, all_kws).
    """
    if not os.path.exists(CATEGORIES_FILE):
        print(f"\u274c {CATEGORIES_FILE} not found. Run admin to configure categories.")
        sys.exit(1)
    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        cats = json.load(f)
    # Build dict {id: category_obj}
    cat_map = {c["id"]: c for c in cats}
    all_ids = list(cat_map.keys())
    core_ids = [c["id"] for c in cats if c.get("core", False)]
    # Build keyword map for fallback classifier
    all_kws = {}
    for c in cats:
        kws = c.get("keywords", [])
        if kws:
            all_kws[c["id"]] = kws
    return cat_map, all_ids, core_ids, all_kws


def save_categories(cat_map):
    """Save categories dict back to categories.json."""
    cats_list = []
    for c in cat_map.values():
        cats_list.append({
            "id": c["id"],
            "icon": c.get("icon", "\U0001f4cc"),
            "name": c["name"],
            "description": c.get("description", ""),
            "phases": c.get("phases", []),
            "keywords": c.get("keywords", []),
            "core": c.get("core", False),
        })
    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cats_list, f, indent=2, ensure_ascii=False)


def inject_category(cat_map, cat_id, name, description, icon=None):
    """Inject a custom category. Usage: --categories "id:name:description" """
    if cat_id in cat_map:
        print(f"  \u26a0 Category '{cat_id}' already exists, updating description.")
        cat_map[cat_id]["description"] = description
    else:
        cat_map[cat_id] = {
            "id": cat_id,
            "icon": icon or "\U0001f4cc",
            "name": name,
            "phases": ["Emerged", "Developing", "Gaining Traction", "Maturing", "Resolved"],
            "description": description,
            "keywords": [],
            "core": False,
        }
    print(f"  \u2713 Category '{cat_id}' ({name})")

# ═══════════════════════════════════════════════════════════════════════
# RSS Fetching & Parsing
# ═══════════════════════════════════════════════════════════════════════

def _extract_text(raw_html):
    """Extract clean text from HTML content.
    Prefers BeautifulSoup for clean stripping; falls back to regex.
    """
    if HAS_BS4:
        soup = BeautifulSoup(raw_html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
    else:
        text = html.unescape(raw_html)
        text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_rss(url, source, max_items):
    """Fetch and parse RSS feed using regex."""
    try:
        req = Request(url, headers={"User-Agent": "PeaceMeter/1.0"})
        with urlopen(req, timeout=10) as f:
            xml = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  \u26a0 {source}: {e}")
        return []

    if "<html" in xml[:200] or "<!DOCTYPE html" in xml[:200]:
        return []

    item_blocks = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    articles = []
    for block in item_blocks[:max_items]:
        title_m = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
        link_m = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
        date_m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL)

        if not title_m:
            continue

        title = title_m.group(1).strip()
        title = title.replace("<![CDATA[", "").replace("]]>", "")
        title = html.unescape(title)
        title = re.sub(r"&\w+;|&#\d+;|&#x[0-9a-fA-F]+;", "", title)
        title = re.sub(r"<[^>]+>", "", title)

        # Extract snippet from <content:encoded> first, then <description>
        snippet = ""
        # Try <content:encoded> — usually holds full article text
        content_m = re.search(r"<content:encoded>(.*?)</content:encoded>", block, re.DOTALL)
        if content_m:
            raw = content_m.group(1)
            raw = raw.replace("<![CDATA[", "").replace("]]>", "")
            snippet = _extract_text(raw)
        else:
            # Fallback to <description>
            desc_m = re.search(r"<description>(.*?)</description>", block, re.DOTALL)
            if desc_m:
                raw = desc_m.group(1)
                raw = raw.replace("<![CDATA[", "").replace("]]>", "")
                snippet = _extract_text(raw)
        # Truncate to 1200 chars for AI prompt
        snippet = snippet[:1200]

        link = link_m.group(1).strip() if link_m else ""
        date_str = date_m.group(1).strip() if date_m else datetime.now(timezone.utc).isoformat()
        if "GMT" in date_str or "UTC" in date_str:
            try:
                dt = parsedate_to_datetime(date_str)
                date_str = dt.isoformat()
            except Exception:
                pass

        articles.append({
            "title": title,
            "link": link,
            "date": date_str,
            "source": source,
            "snippet": snippet,
        })
    return articles


def fetch_all_feeds(age_hours=None):
    """Fetch all RSS feeds, return deduplicated articles (no keyword filter).
    The LLM decides relevance.
    """
    feeds = load_rss_feeds()
    print(f"\U0001f4e1 Fetching {len(feeds)} RSS feeds...")
    now = datetime.now(timezone.utc)
    if age_hours is not None:
        max_age = now.timestamp() - (age_hours * 3600)
    else:
        max_age = now.timestamp() - (MAX_AGE_DAYS * 86400)

    fetched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(fetch_rss, url, name, MAX_ARTICLES_PER_FEED): (name, url, feed_type)
            for name, url, feed_type in feeds
        }
        for future in concurrent.futures.as_completed(futures, timeout=60):
            name, url, feed_type = futures[future]
            try:
                items = future.result()
                fetched.extend(items)
            except Exception as e:
                print(f"  \u26a0 {source}: {e}")

    # Age filter — keep articles within time window
    all_articles = []
    for a in fetched:
        try:
            dt = datetime.fromisoformat(a["date"])
            if dt.timestamp() < max_age:
                continue
        except Exception:
            pass  # keep articles with unparseable dates
        all_articles.append(a)

    # Deduplicate by title
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(a)

    print(f"  \u2192 {len(unique)} unique articles ({len(all_articles) - len(unique)} duplicates removed)")
    return unique


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Taxonomy Proposal — LLM suggests categories from articles
# ═══════════════════════════════════════════════════════════════════════

_DEFAULT_EMOJIS = ["\U0001f54a", "🏚", "\U0001f91d", "\U0001f3db", "\U0001f4a7",
                   "\u2623\ufe0f", "\U0001f1f1\U0001f1e7", "⚖️", "\U0001f3db", "🔥", "🌍", "📌"]


def propose_taxonomy(articles, core_cats=None):
    """Phase 1: Ask LLM to propose a taxonomy from all article titles.
    Uses core categories as the base and asks LLM to suggest additions.
    Returns dict {categories: [{id, name, description, icon}], assignments: {idx: cat_id}}
    or None on failure.
    """
    # Build numbered list of titles (no snippets — too many tokens)
    lines = []
    for i, a in enumerate(articles):
        lines.append(f"{i+1}. {a['title']}")
    articles_text = "\n".join(lines)

    # Build core categories block if provided
    core_block = ""
    if core_cats:
        core_lines = []
        for c in core_cats:
            core_lines.append(f"  - {c['id']}: {c['name']} — {c['description']}")
        core_block = (
            "\n\n"
            "You already have these CORE categories that MUST be included in your output.\n"
            "You may add NEW categories beyond these if the articles warrant it.\n"
            "Do not remove or rename core categories.\n\n"
            "Core categories:\n"
            + "\n".join(core_lines)
        )

    prompt = (
        "You are a Middle East news analyst. Review the articles below and propose"
        " a taxonomy of categories that best organizes them."
        f"{core_block}"
        "\n"
        "RULES:"
        "\n"
        "- Propose 6-14 categories total (core + new). No fewer than 4."
        "\n"
        "- Each category must have: id (lowercase-hyphen), name (title case),"
        " description (one sentence), icon (one emoji)"
        "\n"
        "- Categories should reflect the ACTUAL TOPICS in the articles."
        " Do not force-fit articles into generic buckets."
        "\n"
        "- Be specific: 'iran-nuclear' not 'regional'. 'west-bank' not 'palestine'."
        "\n"
        "- If articles span many countries without a clear theme, use 'regional'."
        "\n"
        "- Assign each article (by number) to exactly one category."
        "\n\n"
        "Output ONLY a JSON object:"
        "\n"
        '{"categories": [{"id": "...", "name": "...", "description": "...", "icon": "..."}], "assignments": {"1": "cat-id", "2": "cat-id"}}'
        "\n\n"
        "Articles:"
        f"\n{articles_text}"
    )

    body = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": "Middle East news taxonomy designer. Output ONLY valid JSON with keys: categories, assignments. No explanation."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 16000,
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
        print(f"  AI unavailable for taxonomy proposal: {e}")
        return None

    result_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    result_text = result_text.strip()
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


def _build_taxonomy_prompt(categories):
    """Build system prompt from LLM-proposed categories."""
    block = "\n".join(f"  {c['id']}: {c['description']}" for c in categories)
    cat_list = ", ".join(c['id'] for c in categories)
    return (
        "You are a precise Middle East news classifier. "
        "Your task is to analyze the provided news text and output a single, valid JSON object."
        "\n\n"
        "CRITICAL RULES:"
        "\n"
        "1. Choose the MOST SPECIFIC category. Do NOT put general news into broad categories—use 'regional' instead."
        "\n"
        "2. Output ONLY raw JSON. No explanations, no markdown code blocks."
        "\n\n"
        f"Categories:\n{block}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: AI Classification via llama.cpp (single-article inference)
# ═══════════════════════════════════════════════════════════════════════

def _make_classifier_prompt(cat_map):
    """Build the system prompt from the loaded category map."""
    cat_ids = list(cat_map.keys())
    block = "\n".join(
        f"  {cid}: {cat_map[cid]['description']}" for cid in cat_ids
    )
    cat_list = ", ".join(cat_ids)
    return (
        "You are a precise Middle East news classifier. "
        "Your task is to analyze the provided news text and output a single, valid JSON object."
        "\n\n"
        "CRITICAL RULES:"
        "\n"
        "1. Choose the MOST SPECIFIC category from the list below. Do NOT invent new category IDs."
        "\n"
        "2. If the article is not about the Middle East, set me_relevant to false."
        "\n"
        "3. Output ONLY raw JSON. No explanations, no markdown code blocks."
        "\n\n"
        f"Valid categories (use ONLY these IDs):\n{block}"
        f"\n\nValid IDs: {cat_list}"
    ), cat_ids


def _classify_article(article, system_prompt, valid_ids):
    """Classify a single article via llama.cpp chat API.
    Returns dict {me_relevant, category, sentiment, risk} or None on failure.
    """
    snippet = article.get("snippet", "")
    context = snippet if snippet else article["title"]

    user_prompt = (
        "Analyze this specific article and determine its category, sentiment, risk (1-10), and Middle East relevance."
        "\n\n"
        "<article>"
        "\n"
        f"<title>{article['title']}</title>"
        "\n"
        f"<snippet>{context}</snippet>"
        "\n"
        "</article>"
        "\n\n"
        "Output exactly in this JSON format:"
        "\n"
        '{"me_relevant": true, "category": "<one-of-the-valid-ids>", "sentiment": "positive|negative|neutral", "risk": 5}'
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
        with urlopen(req, timeout=60) as f:
            response = json.loads(f.read().decode())
    except Exception as e:
        print(f"  AI unavailable: {e}")
        return None

    result_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    result_text = result_text.strip()

    # Empty response — LLM content filter blocked
    if not result_text:
        return {"_refused": True, "_text": "(empty response)"}

    # Strip markdown code fences if LLM wraps them
    if result_text.startswith("```"):
        lines = result_text.split("\n")
        result_text = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else "".join(lines[1:]).strip()

    # Extract JSON object
    first_brace = result_text.find('{')
    last_brace = result_text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        json_str = result_text[first_brace:last_brace+1]
        try:
            obj = json.loads(json_str)
            if "me_relevant" in obj:
                return obj
        except json.JSONDecodeError:
            pass
    # No valid JSON found — LLM returned prose (refusal) or garbled output
    return {"_refused": True, "_text": result_text[:100]}


def classify_articles(articles, system_prompt, valid_ids):
    """Classify each article individually via llama.cpp.
    Articles with me_relevant=false are silently dropped.
    Returns list of (article, {solution, sentiment, risk}) pairs.
    Enforces that category must be one of valid_ids.
    """
    # Lightweight pre-filter: skip obvious noise before LLM call (saves tokens + time)
    HARD_EXCLUDE = {
        "world cup", "fifa", "afcon", "premier league", "man city", "guardiola",
        "real estate", "property investment", "fragrance", "bakhoor", "perfume",
        "hollywood", "celebrity", "sydney sweeney", "euphoria", "tv show",
        "secondhand smoke", "smoke in public", "sponsored",
    }
    print(f"\U0001f916 Classifying {len(articles)} articles via llama.cpp (1-by-1) [{len(valid_ids)} categories]...")
    pairs = []  # list of (article, classification)
    relevant = 0
    dropped = 0
    pre_filtered = 0
    ai_failures = 0
    ai_refusals = 0

    for idx, article in enumerate(articles):
        # Pre-filter: skip obvious noise
        lower_title = article["title"].lower()
        if any(w in lower_title for w in HARD_EXCLUDE):
            pre_filtered += 1
            continue

        t0 = time.time()
        result = _classify_article(article, system_prompt, valid_ids)
        elapsed = time.time() - t0

        # Content filter refusal — LLM refused to classify
        if isinstance(result, dict) and result.get("_refused"):
            ai_refusals += 1
            if ai_refusals <= 3:
                print(f"  \u26d4 AI content filter blocked: {result.get('_text', '?')}...")
            continue  # skip this article, try next

        if result is None:
            ai_failures += 1
            if ai_failures <= 3:
                # Retry once
                result = _classify_article(article, system_prompt, valid_ids)
                elapsed = time.time() - t0
                if result:
                    ai_failures = 0
            else:
                # AI unreachable — abort
                print(f"  \u26a0\ufe0f AI unreachable after {ai_failures} failures. Aborting classification.")
                break

        if result.get("me_relevant"):
            # Enforce: category must be one of the known valid IDs
            sol = result.get("category") or result.get("solution")
            if sol not in valid_ids:
                sol = _fallback_classify(article, all_kws) or "regional"
                print(f"  \u26a0 Unknown category '{result.get('category')}', fallback → '{sol}'")
            pairs.append((article, {
                "solution": sol,
                "sentiment": result.get("sentiment", "neutral"),
                "risk": result.get("risk", 5),
            }))
            relevant += 1
        else:
            dropped += 1

        if (idx + 1) % 20 == 0 or idx == len(articles) - 1:
            print(f"  [{idx+1}/{len(articles)}] {relevant} relevant, {dropped} dropped")

    print(f"  Total: {relevant} relevant, {dropped} dropped by LLM, {ai_refusals} AI refusals, {pre_filtered} pre-filtered / {len(articles)} articles")
    if ai_refusals > 0:
        pct = ai_refusals / len(articles) * 100
        print(f"  \U0001f6a8\ufe0f WARNING: AI content filter triggered on {ai_refusals} articles ({pct:.1f}%)")
    return pairs, ai_refusals

# ═══════════════════════════════════════════════════════════════════════
# Keyword fallback classifier
# ═══════════════════════════════════════════════════════════════════════

POSITIVE_WORDS = ["agreed", "signed", "resumed", "reopened", "released", "deal", "progress", "restored"]
NEGATIVE_WORDS = ["killed", "attack", "strike", "bombing", "destroyed", "escalat", "crisis", "failed"]


def _fallback_classify(article, kw_map):
    """Fallback keyword classifier using keywords from categories.json."""
    lower = article["title"].lower()
    scores = {}
    for cat_id, kws in kw_map.items():
        for kw in kws:
            if kw in lower:
                weight = 2 if " " in kw else 1
                scores[cat_id] = scores.get(cat_id, 0) + weight
    if scores:
        max_score = max(scores.values())
        best = [k for k, v in scores.items() if v == max_score]
        # Pick first match (categories.json order)
        return best[0]
    return None


def keyword_classify(articles, kw_map):
    """Fallback keyword-based classification using categories.json keywords."""
    results = []
    for article in articles:
        cat = _fallback_classify(article, kw_map)
        if cat:
            lower = article["title"].lower()
            pos = sum(1 for w in POSITIVE_WORDS if w in lower)
            neg = sum(1 for w in NEGATIVE_WORDS if w in lower)
            sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"
            results.append((article, {"solution": cat, "sentiment": sentiment, "risk": 5}))
    return results


# ═══════════════════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════════════════

def parse_date(date_str):
    """Parse date string (ISO 8601 or RFC 2822)."""
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            return datetime.now(timezone.utc)


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


def compute_phase(events):
    if not events:
        return 0
    total = len(events)
    now_ts = datetime.now(timezone.utc).timestamp()

    w_pos, w_total = 0, 0
    for e in events:
        age = now_ts - parse_date(e["date"]).timestamp()
        weight = 2 if age < 48 * 3600 else 1
        w_total += weight
        if e["sentiment"] == "positive":
            w_pos += weight

    ratio = w_pos / w_total if w_total > 0 else 0
    phase = min(4, int(ratio * 5))
    neg = sum(1 for e in events if e["sentiment"] == "negative")
    if neg / total > 0.6:
        phase = min(phase, 1)
    return phase


# ═══════════════════════════════════════════════════════════════════════
# Build Output Data
# ═══════════════════════════════════════════════════════════════════════

def build_output(articles, classifications, cat_map):
    """Build the final JSON structure for the Peace Room frontend.

    arguments: If 'classifications' is a list of (article, classification) tuples,
    articles is ignored and pairs are iterated directly.
    cat_map: dict of category definitions from categories.json
    """
    now = datetime.now(timezone.utc)

    # Group articles by solution (use category IDs from categories.json)
    solution_events = {cid: [] for cid in cat_map}

    # Handle new format: list of (article, classification) pairs
    if classifications and isinstance(classifications[0], tuple):
        pairs = classifications
    else:
        pairs = list(zip(articles, classifications))

    for article, classification in pairs:
        sol = classification.get("solution", "ceasefire")
        if sol not in solution_events:
            sol = "ceasefire"  # unknown category, default to ceasefire

        solution_events[sol].append({
            "date": article["date"],
            "text": article["title"],
            "sentiment": classification.get("sentiment", "neutral"),
            "source": article["source"],
            "link": article["link"],
            "snippet": article.get("snippet", ""),
            "ai_risk": classification.get("risk", 5),
        })

    # Sort events per solution by date desc
    for sol in solution_events:
        solution_events[sol].sort(key=lambda e: e["date"], reverse=True)

    solutions = []
    counts = {"advancing": 0, "stable": 0, "stalling": 0}
    active_solutions = []  # only solutions with recent articles

    for sol_id in cat_map:
        events = solution_events[sol_id]
        if not events:
            continue
        active_solutions.append(sol_id)
        direction = compute_direction(events)
        phase_index = compute_phase(events)
        counts[direction] += 1

        cat = cat_map.get(sol_id)
        if cat:
            # Known category from categories.json — use its config
            solutions.append({
                "id": sol_id,
                "icon": cat.get("icon", "\U0001f4cc"),
                "name": cat["name"],
                "phases": cat.get("phases", ["Emerged", "Developing", "Maturing", "Resolved"]),
                "phaseIndex": phase_index,
                "direction": direction,
                "keyMetric": {"label": "Events (7d)", "value": str(len(events))},
                "summary": events[0]["text"],
                "events": events[1:],  # exclude summary event to avoid duplicate
                "confidence": "high" if len(events) > 5 else "medium" if len(events) > 2 else "low",
                "core": cat.get("core", False),
            })
        else:
            # Unknown category — generate default (shouldn't happen with enforcement)
            name = sol_id.replace("-", " ").replace("_", " ").title()
            solutions.append({
                "id": sol_id,
                "icon": "📌",
                "name": name,
                "phases": ["Emerged", "Developing", "Gaining Traction", "Maturing", "Resolved"],
                "phaseIndex": phase_index,
                "direction": direction,
                "keyMetric": {"label": "Events (7d)", "value": str(len(events))},
                "summary": events[0]["text"],
                "events": events[1:],  # exclude summary event to avoid duplicate
                "confidence": "low",
                "core": False,
            })

    if not active_solutions:
        counts["stable"] = 1

    # Sort by event count desc, keep top 8
    solutions.sort(key=lambda s: s["keyMetric"]["value"], reverse=True)
    solutions = solutions[:8]
    # Re-sort activeSolutions to match
    active_ids = set(s["id"] for s in solutions)
    active_solutions = [sid for sid in active_solutions if sid in active_ids]

    # Overall momentum
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
            "summary": f"{counts['advancing']} advancing, {counts['stable']} stable, {counts['stalling']} stalling ({len(active_solutions)} active). {len(articles)} ME articles from {len(load_rss_feeds())} feeds.",
        },
        "lastUpdated": now.isoformat(),
        "source": "ai-analyzer-prod",
        "feedCount": len(articles),
        "aiVersion": SCRIPT_VERSION,
    }


# ═══════════════════════════════════════════════════════════════════════
# Upload to Cloudflare Pages via Workers API
# ═══════════════════════════════════════════════════════════════════════

def upload_to_cloudflare(data):
    """Push solutions.json to Cloudflare Pages via the API."""
    if not CLOUDFLARE_TOKEN or not CLOUDFLARE_ACCOUNT:
        print("\n\u26a0 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set")
        print("   Setting env vars will enable automatic deployment.")
        print("   Data written locally — deploy with: npx wrangler pages deploy")
        return False

    json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    boundary = "PeaceMeterBoundary"

    # Build multipart form for Pages Upload API
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="name"\r\n\r\n'
        f"solutions.json\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="solutions.json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
    ).encode() + json_bytes + f"\r\n--{boundary}--\r\n".encode()

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{CLOUDFLARE_ACCOUNT}/pages/projects/{CLOUDFLARE_PAGES_PROJECT}/uploads"
    )

    try:
        req = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {CLOUDFLARE_TOKEN}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urlopen(req, timeout=30) as f:
            resp = json.loads(f.read().decode())

        if resp.get("success"):
            print("  \u2713 Deployed to Cloudflare Pages")
            return True
        else:
            print(f"  \u26a0 Upload failed: {resp.get('errors', 'unknown')}")
            return False

    except Exception as e:
        print(f"  \u26a0 Upload failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def _load_existing_data():
    """Load existing solutions.json for merge operations."""
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  \u26a0 Could not load existing data: {e}")
        return None


def _merge_with_existing(data, existing):
    """Merge new events into existing solutions, preserving history.

    - Deduplicates events by text content within each category
    - Adds new solution categories discovered by AI
    - Recomputes phases, directions, confidence for all solutions
    """
    for sol in existing.get("solutions", []):
        sol_id = sol["id"]
        existing_texts = {e["text"] for e in sol["events"]}
        for new_sol in data["solutions"]:
            if new_sol["id"] == sol_id:
                for ev in new_sol["events"]:
                    if ev["text"] not in existing_texts:
                        sol["events"].append(ev)
                        existing_texts.add(ev["text"])

    existing_ids = {s["id"] for s in existing["solutions"]}
    for new_sol in data["solutions"]:
        if new_sol["id"] not in existing_ids:
            existing["solutions"].append(new_sol)

    # Recompute for all solutions — compute on ALL events, exclude summary from stored events
    for sol in existing["solutions"]:
        sol["events"].sort(key=lambda e: e["date"], reverse=True)
        sol["phaseIndex"] = compute_phase(sol["events"])
        sol["direction"] = compute_direction(sol["events"])
        sol["keyMetric"] = {"label": "Events (7d)", "value": str(len(sol["events"]))}
        sol["summary"] = sol["events"][0]["text"] if sol["events"] else ""
        sol["events"] = sol["events"][1:]  # exclude summary event to avoid duplicate
        sol["confidence"] = "high" if len(sol["events"]) >= 5 else "medium" if len(sol["events"]) >= 3 else "low"

    # Recompute momentum
    all_solutions = existing["solutions"]
    active_ids = [s["id"] for s in all_solutions if s["events"]]
    existing["activeSolutions"] = active_ids

    counts = {"advancing": 0, "stable": 0, "stalling": 0}
    for s in all_solutions:
        counts[s["direction"]] += 1

    # Sort by event count, keep top 8
    all_solutions.sort(key=lambda s: len(s["events"]), reverse=True)
    top8 = all_solutions[:8]
    existing["solutions"] = top8
    existing["activeSolutions"] = [s["id"] for s in top8]

    if counts["advancing"] > counts["stalling"]:
        m_dir, m_label = "advancing", "Net Positive"
    elif counts["stalling"] > counts["advancing"]:
        m_dir, m_label = "stalling", "Net Negative"
    else:
        m_dir, m_label = "stable", "Mixed Signals"

    existing["overallMomentum"] = {
        "direction": m_dir,
        "label": m_label,
        "summary": f"{counts['advancing']} advancing, {counts['stable']} stable, {counts['stalling']} stalling ({len(active_ids)} active). {sum(len(s['events']) for s in all_solutions)} events across {len(all_solutions)} categories.",
    }
    existing["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    existing["source"] = "ai-analyzer-prod"
    existing["aiVersion"] = SCRIPT_VERSION
    return existing


def _print_summary(data, articles_count, elapsed):
    """Print run summary."""
    print(f"\n\u2713 Done in {elapsed:.1f}s")
    print(f"  {articles_count} articles \u2192 {len(data['solutions'])} solutions")
    print(f"  Momentum: {data['overallMomentum']['label']}")

    for sol in data["solutions"]:
        d = "\U0001f7e2" if sol["direction"] == "advancing" else "\U0001f7e5" if sol["direction"] == "stalling" else "\U0001f7e1"
        phase = sol["phases"][sol["phaseIndex"]]
        print(f"  {sol['icon']} {sol['name']:35s} {sol['direction']:10s} {d} {sol['keyMetric']['value']} events \u2192 {phase}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Peace Room AI Analyzer (Production)")
    parser.add_argument("--fast", action="store_true",
                        help="Hourly fast run: fetch last 2h, merge into existing data")
    parser.add_argument("--daily", action="store_true",
                        help="Daily full run: fetch 7 days, overwrite solutions.json")
    parser.add_argument("--categories", type=str, nargs="*",
                        help="Inject custom categories (id:name:description). E.g., --categories \"armistice:Ceasefire Talks:Truce negotiations\"")
    parser.add_argument("--skip-upload", action="store_true", help="Skip Cloudflare deploy")
    parser.add_argument("--dry-run", action="store_true", help="Print output JSON to stdout")
    parser.add_argument("--fetch-only", action="store_true", help="Only fetch RSS, skip AI")
    parser.add_argument("--review-taxonomy", action="store_true",
                        help="Phase 1 only: propose taxonomy using core categories as base, save to taxonomy.json")
    parser.add_argument("--use-taxonomy", type=str, default=None,
                        help="[deprecated] Use approved taxonomy from file. Categories now come from categories.json.")
    parser.add_argument("--recent", type=int, default=0,
                        help="[deprecated] Only process articles from last N hours")
    args = parser.parse_args()

    # Load categories from categories.json (source of truth)
    cat_map, all_ids, core_ids, all_kws = load_categories()
    print(f"\U0001f4c5 Loaded {len(all_ids)} categories ({len(core_ids)} core) from categories.json")

    if args.use_taxonomy:
        print("  \u26a0 --use-taxonomy is deprecated. Categories are now in categories.json.")

    # Determine mode
    if args.fast:
        mode = "fast"
        age_hours = FAST_AGE_HOURS
    elif args.daily or (args.fast == False and args.recent == 0):
        mode = "daily"
        age_hours = None
    else:
        mode = "fast"
        age_hours = args.recent
        print("  \u26a0 --recent is deprecated, use --fast instead")

    print(f"\n{'\U0001f680' if mode == 'daily' else '\U0001f4a9'} Peace Room AI Analyzer — {mode.upper()} mode\n")

    # Inject custom categories (merges into cat_map)
    if args.categories:
        print("\u2728 Injecting custom categories:")
        for cat in args.categories:
            parts = cat.split(":", 2)
            if len(parts) == 3:
                cat_id, name, desc = parts
                inject_category(cat_map, cat_id, name, desc)
            elif len(parts) == 2:
                cat_id, name = parts
                inject_category(cat_map, cat_id, name, f"{name} news and updates")
            else:
                print(f"  \u26a0 Invalid format: '{cat}' (expected id:name:description)")
        # Rebuild lists after injection
        all_ids = list(cat_map.keys())
        all_kws = {c["id"]: c.get("keywords", []) for c in cat_map.values() if c.get("keywords")}

    start = time.time()

    # 1. Fetch RSS
    if age_hours is not None:
        print(f"  [fast mode] fetching articles from last {age_hours}h")
    else:
        print(f"  [daily mode] fetching articles from last {MAX_AGE_DAYS}d")
    articles = fetch_all_feeds(age_hours=age_hours)
    if not articles:
        print("No articles found, aborting.")
        return

    # ── Phase 1: Taxonomy Proposal (uses core categories as base) ──
    system_prompt, valid_ids = _make_classifier_prompt(cat_map)
    if args.review_taxonomy:
        core_cats = [c for c in cat_map.values() if c.get("core", False)]
        print(f"\n\U0001f50d Phase 1: Proposing taxonomy from {len(articles)} articles ({len(core_cats)} core categories as base)...")
        taxonomy = propose_taxonomy(articles, core_cats=core_cats)
        if taxonomy is None:
            print("  \u274c Taxonomy proposal failed.")
            return

        if taxonomy and "categories" in taxonomy:
            with open(TAXONOMY_FILE, "w", encoding="utf-8") as f:
                json.dump(taxonomy, f, indent=2, ensure_ascii=False)
            print(f"\n\u2713 Proposed taxonomy saved to {TAXONOMY_FILE}")

            print("\n--- PROPOSED CATEGORIES ---")
            for cat in taxonomy["categories"]:
                print(f"  {cat.get('icon', '📌')} {cat['id']:25s} → {cat['name']}")
                print(f"       {cat['description']}")

            cat_counts = {}
            for idx_str, cat_id in taxonomy.get("assignments", {}).items():
                cat_counts[cat_id] = cat_counts.get(cat_id, 0) + 1
            print("\n--- ARTICLE ASSIGNMENTS ---")
            for cat in taxonomy["categories"]:
                count = cat_counts.get(cat["id"], 0)
                print(f"  {cat['id']:25s} → {count} articles")

            print(f"\nReview {TAXONOMY_FILE}, then add new categories to categories.json via admin panel.")
            print(f"Then run:  python ai-analyze-prod.py --{mode}")
            return
        else:
            print("  \u274c Taxonomy proposal failed, proceeding with existing categories.")

    using_fallback = False
    # 2. AI Classification
    if args.fetch_only:
        print("[--fetch-only] Using keyword fallback")
        classified_pairs = keyword_classify(articles, all_kws)
        ai_refusals = 0
    else:
        classified_pairs, ai_refusals = classify_articles(articles, system_prompt, valid_ids)
        if not classified_pairs:
            print("  \u26a0 AI failed, falling back to keyword classification")
            classified_pairs = keyword_classify(articles, all_kws)
            ai_refusals = 0

    # 3. Build output
    data = build_output(articles, classified_pairs, cat_map)

    # 4. Merge with existing data (fast mode) or overwrite (daily mode)
    if mode == "fast":
        existing = _load_existing_data()
        if existing:
            print(f"\u2192 Merging {len(classified_pairs)} new events into existing data")
            data = _merge_with_existing(data, existing)
        else:
            print("  \u26a0 No existing data found, falling back to daily mode")
    # daily mode: data already overwrites

    # Add AI health metadata
    data["aiHealth"] = {
        "refusals": ai_refusals,
        "totalClassified": len(classified_pairs),
        "refusalRate": round(ai_refusals / max(len(articles), 1) * 100, 1),
        "lastRun": datetime.now(timezone.utc).isoformat(),
        "classificationMethod": "ai" if not using_fallback else "keyword-fallback",
    }
    if using_fallback:
        data["aiHealth"]["status"] = "fallback"
    elif ai_refusals > 0:
        pct = data["aiHealth"]["refusalRate"]
        data["aiHealth"]["status"] = "warning" if pct > 5 else "ok"
    else:
        data["aiHealth"]["status"] = "healthy"

    # 5. Dry run
    if args.dry_run:
        print("\n--- solutions.json ---")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    # 6. Write local JSON
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n\u2713 Written to {DATA_FILE}")

    # Also sync to data.json for local dev server (even with --skip-upload)
    DATA_JSON = os.path.join(os.path.dirname(DATA_FILE), "data.json")
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # Also write app/data.json for GitHub-deployed frontend
    APP_DATA_JSON = os.path.join(os.path.dirname(DATA_FILE), "app", "data.json")
    with open(APP_DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Back-propagate new category IDs to categories.json
    # (e.g., keyword fallback created a solution for a category not in categories.json)
    solution_ids = {s["id"] for s in data.get("solutions", [])}
    missing = solution_ids - set(cat_map.keys())
    if missing:
        for sid in missing:
            sol = next((s for s in data.get("solutions", []) if s["id"] == sid), None)
            inject_category(cat_map, sid, sol["name"] if sol else sid, f"{sid} news and updates")
            print(f"  \u2795 Auto-added missing category: {sid}")
        # Re-read categories.json to preserve categories not used this run,
        # then merge new ones into the existing cat_map dict
        existing_map, _, _, _ = load_categories()  # dict {id: category_obj}
        for cid, cdata in cat_map.items():
            if cid not in existing_map:
                existing_map[cid] = cdata
        save_categories(existing_map)

    # 7. Upload data.json to Cloudflare KV (served via Pages Function)
    if not args.skip_upload:
        print(f"\n\U0001f4a9 Uploading data.json to Cloudflare KV...")
        project_root = os.path.dirname(os.path.abspath(__file__))
        import subprocess
        kv_id = "badf4fb7acfe4d1c905db77ed8d5e70f"
        cmd = f'npx wrangler kv key put "data.json" --binding=peace_data --namespace-id={kv_id} < "{APP_DATA_JSON}"'
        result = subprocess.run(cmd, shell=True, cwd=project_root, capture_output=True)
        try:
            result.stdout = result.stdout.decode('utf-8', errors='replace')
            result.stderr = result.stderr.decode('utf-8', errors='replace')
        except Exception:
            pass
        if result.returncode == 0:
            print("  \u2713 data.json uploaded to KV")
        else:
            print(f"  \u26a0 KV upload failed: {result.stderr[:300]}")
            print("  Fallback: data.json written locally")
    else:
        print(f"\n\u2139\ufe0f Deploy skipped. Data written to {DATA_FILE} and {DATA_JSON}")

    elapsed = time.time() - start
    _print_summary(data, len(classified_pairs), elapsed)


if __name__ == "__main__":
    main()
