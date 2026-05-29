#!/usr/bin/env python3
"""
Peace Room AI Analyzer
======================
Runs every 3 hours (via cron/scheduled task):
  1. Fetches RSS feeds
  2. Sends articles to local AI (llama.cpp or Ollama) for classification + sentiment
  3. Aggregates into solution buckets
  4. Computes phase progress, direction, risks
  5. Writes JSON → uploads to Cloudflare Pages
"""

import json
import sys
import os
import re
import time
import hashlib
import concurrent.futures
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

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

# ─── Configuration ───────────────────────────────────────────────────

LLAMA_CPP_URL = os.environ.get("LLAMA_CPP_URL", "http://localhost:8080")  # set in .env — not hardcoded
OLLAMA_URL = "http://localhost:11434"  # fallback
AI_BACKEND = "ollama"  # "llamacpp" or "ollama"
AI_MODEL = "qwen3.5:9b"  # fast model for classification (ollama)
                         # or Qwen3.6-27B on llama.cpp for deeper analysis

CLOUDFLARE_PAGES_PROJECT = "peace-paths"
CLOUDFLARE_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")

# Output files
DATA_FILE = os.path.join(os.path.dirname(__file__), "app", "solutions.json")
CACHE_FILE = os.path.join(os.path.dirname(__file__), "ai_cache.json")

MAX_ARTICLES_PER_FEED = 8
MAX_AGE_DAYS = 7
BATCH_SIZE = 15  # articles per AI call

# ─── RSS Feeds ──────────────────────────────────────────────────────

RSS_FEEDS = [
    # ── International ME news ──────────────────────────────────
    ("BBC ME", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Guardian", "https://www.theguardian.com/world/israel/rss"),
    ("NYT ME", "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml"),
    ("Al Monitor", "https://www.al-monitor.com/rss"),
    ("ME Monitor", "https://www.middleeastmonitor.com/feed/"),
    ("France24", "https://www.france24.com/en/middle-east/rss"),
    ("Middle East Eye", "https://www.middleeasteye.net/rss"),
    ("Foreign Policy", "https://foreignpolicy.com/feed/"),
    # ── Israel-focused (English) ───────────────────────────────
    ("Times of Israel", "https://www.timesofisrael.com/feed/"),
    ("Haaretz", "https://www.haaretz.com/srv/haaretz-latest-headlines"),
    ("Haaretz - ME", "https://www.haaretz.com/srv/middle-east-news-rss"),
    ("Haaretz - Domestic", "https://www.haaretz.com/srv/israel-news-rss"),
    ("JPost", "https://rss.jpost.com/rss/rssfeedsfrontpage.aspx"),
    ("Arutz Sheva", "https://www.israelnationalnews.com/Rss.aspx?act=.1"),
    ("JNS", "https://www.jns.org/feed/"),
    ("JFeed", "https://a.jfeed.com/v1/rss/articles/latest/rss2"),
    ("The Forward", "https://forward.com/rss/"),
    # ── Israel-focused (Hebrew — parsed by title keywords) ─────
    ("Maariv", "https://www.maariv.co.il/Rss/RssChadashot"),
    ("Walla", "https://rss.walla.co.il/feed/1"),
    # ── Regional / Arab world ──────────────────────────────────
    ("Al Bawaba", "https://www.albawaba.com/rss/all"),
    ("ME News", "https://menews247.com/feed/"),
    # ── Aggregators ────────────────────────────────────────────
    ("Google News Israel", "https://news.google.com/rss/search?hl=en-US&gl=US&q=israel&um=1&ie=UTF-8&ceid=US:en"),
    # ── Humanitarian / UN ──────────────────────────────────────
    ("UN News", "https://news.un.org/feed/subscribe/en/news/region/middle-east/feed/rss.xml"),
    ("Amnesty", "https://www.amnesty.org/en/location/middle-east-and-north-africa/feed/"),
    # ── OSINT / Think tanks ────────────────────────────────────
    ("Crisis Group", "https://www.crisisgroup.org/rss/91"),
    ("bellingcat", "https://www.bellingcat.com/feed/"),
    ("Mitvim", "https://mitvim.org.il/en/feed/"),
    ("Alma", "https://israel-alma.org/feed/"),
]

# ─── Solution Definitions ───────────────────────────────────────────

SOLUTIONS = {
    "ceasefire": {
        "icon": "🕊", "name": "Ceasefire & De-escalation",
        "phases": ["Active Fighting", "Ceasefire Talks", "Draft Agreement", "Signed", "Holding"],
        "description": "Ceasefire negotiations, de-escalation efforts, truce agreements across all conflict zones",
    },
    "aid": {
        "icon": "🚚", "name": "Humanitarian Aid",
        "phases": ["Blocked", "Limited Access", "Corridors Open", "Steady Flow", "Full Access"],
        "description": "Humanitarian aid delivery, relief supplies, food/water/medicine access, crossing operations",
    },
    "diplomacy": {
        "icon": "🤝", "name": "Diplomacy & Regional Deals",
        "phases": ["Isolated", "Back-channel", "Framework", "New Partners", "Regional Peace"],
        "description": "Diplomatic normalization, Abraham Accords expansion, peace deals, regional cooperation",
    },
    "governance": {
        "icon": "🏛", "name": "Post-War Governance",
        "phases": ["No Framework", "Proposals", "Consensus", "Interim Gov", "Sustainable"],
        "description": "Post-war governance plans, Palestinian Authority reform, transitional authority, political frameworks",
    },
    "infrastructure": {
        "icon": "💧", "name": "Infrastructure & Recovery",
        "phases": ["Destroyed", "Emergency Repairs", "Partial", "Reconstruction", "Full Recovery"],
        "description": "Infrastructure reconstruction, power/water/hospitals rebuilding, recovery efforts",
    },
    "iran": {
        "icon": "☢️", "name": "Iran Nuclear & War",
        "phases": ["War", "Ceasefire Talks", "Armistice", "Nuclear Deal", "Resolution"],
        "description": "Iran-US conflict, nuclear program, Strait of Hormuz, Iran peace negotiations",
    },
    "lebanon": {
        "icon": "🇱🇧", "name": "Lebanon & Hezbollah",
        "phases": ["Active Fighting", "De-escalation", "Ceasefire", "Withdrawal", "Stable"],
        "description": "Lebanon conflict, Hezbollah-Israel hostilities, southern Lebanon situation",
    },
    "gaza-crisis": {
        "icon": "🏚", "name": "Gaza Humanitarian Crisis",
        "phases": ["Blockade", "Aid Inflow", "Recovery", "Rebuilding", "Stabilized"],
        "description": "Gaza humanitarian crisis, displacement, medicine/food blockade, disease, civilian suffering",
    },
    "human-rights": {
        "icon": "⚖️", "name": "Human Rights & Intl Law",
        "phases": ["Allegations", "Investigations", "Sanctions", "Accountability", "Reform"],
        "description": "Human rights violations, war crimes, flotilla activists, ICC/ICJ, international law, abuse allegations",
    },
    "domestic-politics": {
        "icon": "🏛", "name": "Israeli Domestic Politics",
        "phases": ["Fractured", "Coalition Shift", "Policy Change", "Elections", "Stability"],
        "description": "Israeli internal politics, coalition dynamics, Knesset, party struggles, Netanyahu, Herzog, liberal center",
    },
    "west-bank": {
        "icon": "🔥", "name": "West Bank & Settlements",
        "phases": ["Escalation", "Violence Spike", "Mediation", "Calming", "Frozen Conflict"],
        "description": "West Bank settler violence, occupation policies, East Jerusalem, Palestinian communities",
    },
    "regional": {
        "icon": "🌍", "name": "Regional Relations",
        "phases": ["Tensions", "Diplomatic Push", "Accord", "Integration", "Cooperation"],
        "description": "Regional diplomacy, Arab states positions, Jordan, Egypt, Syria, Türkiye, Morocco, UAE, China influence",
    },
}


# ═══════════════════════════════════════════════════════════════════════
# RSS Fetching & Parsing
# ═══════════════════════════════════════════════════════════════════════

def fetch_rss(url, source, max_items):
    """Fetch and parse RSS feed using regex (faster than XML parser)."""
    try:
        req = Request(url, headers={"User-Agent": "PeaceMeter/1.0"})
        with urlopen(req, timeout=5) as f:
            xml = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ {source}: {e}")
        return []

    # Check if this is actually XML (skip HTML pages)
    if "<html" in xml[:200] or "<!DOCTYPE html" in xml[:200]:
        print(f"  ⚠ {source}: returned HTML, skipping")
        return []

    # Regex-based RSS parsing (much faster than XML DOM)
    item_blocks = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
    articles = []
    for block in item_blocks[:max_items]:
        title_m = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
        link_m = re.search(r'<link>(.*?)</link>', block, re.DOTALL)
        date_m = re.search(r'<pubDate>(.*?)</pubDate>', block, re.DOTALL)

        if not title_m:
            continue

        title = title_m.group(1).strip()
        # Strip CDATA
        title = title.replace('<![CDATA[', '').replace(']]>', '')
        # Strip HTML entities
        title = re.sub(r"&\w+;|&#\d+;", "", title)
        # Strip any remaining HTML tags
        title = re.sub(r'<[^>]+>', '', title)

        link = link_m.group(1).strip() if link_m else ""
        date_str = date_m.group(1).strip() if date_m else datetime.now(timezone.utc).isoformat()
        # Normalize date to ISO format if it is RFC 2822
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
        })
    return articles


def fetch_all_feeds():
    """Fetch all RSS feeds, return deduplicated ME-relevant articles."""
    print(f"📡 Fetching {len(RSS_FEEDS)} RSS feeds...")
    all_articles = []

    me_keywords = [
        "israel", "palestine", "gaza", "west bank", "hamas", "iran",
        "lebanon", "hezbollah", "syria", "yemen", "houthi", "red sea",
        "egypt", "saudi", "uae", "qatar", "doha", "jordan",
        "bahrain", "morocco", "iraq", "baghdad",
        "tel aviv", "jerusalem", "beirut", "damascus", "riyadh",
        "middle east", "sinai", "hormuz", "arab",
        "knesset", "netanyahu", "herzog", "settler", "west bank",
    ]

    now = datetime.now(timezone.utc)
    max_age = now.timestamp() - (MAX_AGE_DAYS * 86400)

    # Parallel RSS fetching
    fetched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_rss, url, source, MAX_ARTICLES_PER_FEED): (source, url) for source, url in RSS_FEEDS}
        for future in concurrent.futures.as_completed(futures, timeout=30):
            source, url = futures[future]
            try:
                items = future.result()
                fetched.extend(items)
            except Exception as e:
                print(f"  ⚠ {source}: {e}")

    for a in fetched:
            # ME relevance check
            title_lower = a["title"].lower()
            if not any(kw in title_lower for kw in me_keywords):
                continue
            # Age check
            try:
                dt = datetime.fromisoformat(a["date"])
                if dt.timestamp() < max_age:
                    continue
            except Exception:
                pass
            all_articles.append(a)

    # Deduplicate by title
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(a)

    print(f"  → {len(unique)} unique ME articles ({len(all_articles) - len(unique)} duplicates removed)")
    return unique


# ═══════════════════════════════════════════════════════════════════════
# AI Classification via Ollama
# ═══════════════════════════════════════════════════════════════════════

def classify_batch_ollama(articles):
    """Send a batch of articles to Ollama for classification."""
    solution_ids = list(SOLUTIONS.keys())

    prompt = f"""You are a Middle East news analyst. Classify each article title into ONE of these solution categories:

{chr(10).join(f'{sid}: {sol["description"]}' for sid, sol in SOLUTIONS.items())}

Rules:
- Pick the SINGLE best matching category from the list above
- If NONE fit, create a new category id (lowercase, hyphenated, e.g. "yemen-war")
- If about fighting/strikes but not clearly about a specific solution, use the most relevant conflict zone (ceasefire, iran, lebanon, gaza-crisis)
- Sentiment: "positive" = progress toward peace, "negative" = setback/escalation, "neutral" = mixed/informational
- Risk score: 1-10 (10 = highest risk to peace progress)

Output ONLY valid JSON, no markdown, no explanation:
[
  {{"solution": "<id>", "sentiment": "positive", "risk": 3}},
  ...
]"""

    articles_text = json.dumps([a["title"] for a in articles], ensure_ascii=False)

    body = {
        "model": AI_MODEL,
        "prompt": f"{prompt}\n\nClassify these titles:\n{articles_text}",
        "stream": False,
        "options": {"temperature": 0, "top_p": 0.1},
    }

    import urllib.request, urllib.error
    try:
        req = Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=120) as f:
            response = json.loads(f.read().decode())
        result_text = response.get("response", "")
        # Strip markdown code fences if present
        result_text = result_text.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip().rstrip("```").strip()
        return json.loads(result_text)
    except Exception as e:
        print(f"  ⚠ Ollama classification failed: {e}")
        print(f"  Prompt preview: {prompt[:200]}...")
        return None


def classify_batch_llamacpp(articles):
    """Send batch to llama.cpp for classification."""
    solution_ids = list(SOLUTIONS.keys())

    prompt = f"""You are a Middle East news analyst. Classify each article title into ONE category:
{chr(10).join(f'{sid}: {sol["description"]}' for sid, sol in SOLUTIONS.items())}

Output ONLY JSON array: [{"solution": "...", "sentiment": "...", "risk": N}, ...]

Articles:
{chr(10).join(f'{i+1}. {a["title"]}' for i, a in enumerate(articles))}"""

    body = {
        "prompt": prompt,
        "n_predict": 16000,
        "temperature": 0.0,
        "top_p": 0.1,
    }

    import urllib.request, urllib.error
    try:
        req = Request(
            f"{LLAMA_CPP_URL}/v1/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=180) as f:
            response = json.loads(f.read().decode())
        result_text = response.get("choices", [{}])[0].get("text", "")

        # Extract JSON from response
        import re
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        print(f"  ⚠ No JSON found in response")
        print(f"  Response preview: {result_text[:300]}")
        return None
    except Exception as e:
        print(f"  ⚠ llama.cpp classification failed: {e}")
        return None


def classify_articles(articles):
    """Classify all articles, batching as needed."""
    print(f"🤖 Classifying {len(articles)} articles via AI...")

    results = []
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        print(f"  Batch {i // BATCH_SIZE + 1} ({len(batch)} articles)...", flush=True)

        if AI_BACKEND == "ollama":
            batch_results = classify_batch_ollama(batch)
        else:
            batch_results = classify_batch_llamacpp(batch)

        if batch_results:
            results.extend(batch_results)
        else:
            # Fallback: keyword-based classification
            print(f"  ⚠ Falling back to keyword classification for batch {i // BATCH_SIZE + 1}")
            results.extend(keyword_classify(batch))

    return results[:len(articles)]


# ═══════════════════════════════════════════════════════════════════════
# Keyword fallback classifier
# ═══════════════════════════════════════════════════════════════════════

KEYWORD_MAP = {
    "ceasefire": ["ceasefire", "truce", "cease fire", "armistice", "de-escalation", "peace talks"],
    "aid": ["humanitarian aid", "relief", "wfp", "unrwa", "food delivery"],
    "diplomacy": ["abraham accords", "normalization", "saudi", "nuclear deal", "peace deal"],
    "governance": ["governance", "authority", "two state", "pa reform", "election"],
    "infrastructure": ["reconstruction", "rebuild", "infrastructure", "hospital", "water"],
    "iran": ["iran", "tehran", "hormuz", "khamenei"],
    "lebanon": ["lebanon", "hezbollah", "beirut", "southern lebanon"],
    "gaza-crisis": ["gaza", "displaced", "blockade", "medicine", "disease", "sumud", "flotilla"],
    "human-rights": ["abuse", "rights", "war crime", "icj", "icc", "flotilla", "torture", "eurovision", "flotilla"],
    "domestic-politics": ["netanyahu", "herzog", "knesset", "coalition", "arab parties", "liberal center", "death penalty"],
    "west-bank": ["west bank", "settler", "east jerusalem", "occupied"],
    "regional": ["jordan", "egypt", "syria", "türkiye", "turkey", "morocco", "uae", "china", "arab", "somaliland"],
}

POSITIVE_WORDS = ["agreed", "signed", "resumed", "reopened", "released", "deal", "progress", "restored"]
NEGATIVE_WORDS = ["killed", "attack", "strike", "bombing", "destroyed", "escalat", "crisis", "failed"]


def keyword_classify(articles):
    """Fallback keyword-based classification."""
    results = []
    for article in articles:
        lower = article["title"].lower()

        # Score each solution
        scores = {}
        for sol, kws in KEYWORD_MAP.items():
            for kw in kws:
                if kw in lower:
                    scores[sol] = scores.get(sol, 0) + 1

        if scores:
            best = max(scores, key=scores.get)
        else:
            continue  # drop unclassifiable articles

        # Sentiment
        pos = sum(1 for w in POSITIVE_WORDS if w in lower)
        neg = sum(1 for w in NEGATIVE_WORDS if w in lower)
        sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"

        results.append({"solution": best, "sentiment": sentiment, "risk": 5})

    return results


# ═══════════════════════════════════════════════════════════════════════
# AI Meta-Analysis (overall assessment per solution)
# ═══════════════════════════════════════════════════════════════════════

def meta_analyze(solution_id, events):
    """Ask AI for an overall assessment of a solution's progress."""
    solution = SOLUTIONS[solution_id]

    # Build context from events
    recent_titles = [e["text"] for e in events[:20]]

    prompt = f"""You are a Middle East peace analyst. Analyze the current state of:
**{solution['name']}**

Recent news headlines:
{chr(10).join(f'- {t}' for t in recent_titles)}

Current phase options: {', '.join(solution['phases'])}

Output ONLY valid JSON:
{{
  "phase_index": 0-4,
  "direction": "advancing"|"stable"|"stalling",
  "confidence": "high"|"medium"|"low",
  "summary": "one sentence summarizing current status",
  "key_risk": "main risk to progress",
  "key_opportunity": "main opportunity",
  "trend_48h": "improving"|"declining"|"mixed"
}}"""

    import urllib.request, urllib.error
    try:
        body = {
            "model": AI_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "top_p": 0.3},
        }
        req = Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=180) as f:
            response = json.loads(f.read().decode())
        result_text = response.get("response", "")
        # Strip markdown
        if result_text.startswith("```"):
            parts = result_text.split("```")
            if len(parts) > 1:
                result_text = parts[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            result_text = result_text.strip()
        return json.loads(result_text)
    except Exception as e:
        print(f"  ⚠ Meta-analysis failed for {solution_id}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# Build Output Data
# ═══════════════════════════════════════════════════════════════════════

def build_output(articles, classifications, skip_meta=False):
    """Build the final JSON structure for the Peace Room frontend."""
    now = datetime.now(timezone.utc)

    # Group articles by solution — allow dynamic categories from AI
    solution_events: dict[str, list] = {}

    for article, classification in zip(articles, classifications):
        sol = classification.get("solution", "ceasefire")
        solution_events.setdefault(sol, [])

        solution_events[sol].append({
            "date": article["date"],
            "text": article["title"],
            "sentiment": classification.get("sentiment", "neutral"),
            "source": article["source"],
            "link": article["link"],
            "ai_risk": classification.get("risk", 5),
        })

    # Sort events per solution by date desc
    for sol in solution_events:
        solution_events[sol].sort(key=lambda e: e["date"], reverse=True)

    # Compute direction per solution
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

    def parse_date(date_str):
        """Parse date string (ISO 8601 or RFC 2822)."""
        try:
            return datetime.fromisoformat(date_str)
        except (ValueError, TypeError):
            try:
                return parsedate_to_datetime(date_str)
            except Exception:
                return datetime.now(timezone.utc)

    def compute_phase(events):
        if not events:
            return 0
        total = len(events)
        now_ts = now.timestamp()
        pos = sum(1 for e in events if e["sentiment"] == "positive")
        neg = sum(1 for e in events if e["sentiment"] == "negative")

        # Weighted ratio (recent events count double)
        w_pos, w_total = 0, 0
        for e in events:
            age = now_ts - parse_date(e["date"]).timestamp()
            weight = 2 if age < 48 * 3600 else 1
            w_total += weight
            if e["sentiment"] == "positive":
                w_pos += weight

        ratio = w_pos / w_total if w_total > 0 else 0
        phase = min(4, int(ratio * 5))
        if neg / total > 0.6:
            phase = min(phase, 1)
        return phase

    solutions = []
    counts = {"advancing": 0, "stable": 0, "stalling": 0}
    active_solutions = []

    for sol_id in solution_events:
        events = solution_events[sol_id]
        if not events:
            continue
        active_solutions.append(sol_id)
        direction = compute_direction(events)
        phase_index = compute_phase(events)
        counts[direction] = counts.get(direction, 0) + 1

        sol_cfg = SOLUTIONS.get(sol_id)
        if sol_cfg:
            # Known category — run AI meta-analysis (skip with --fetch-only)
            meta = meta_analyze(sol_id, events) if not skip_meta else None
            solutions.append({
                "id": sol_id,
                "icon": sol_cfg["icon"],
                "name": sol_cfg["name"],
                "phases": sol_cfg["phases"],
                "phaseIndex": meta["phase_index"] if meta else phase_index,
                "direction": meta["direction"] if meta else direction,
                "keyMetric": {"label": "Events (7d)", "value": str(len(events))},
                "summary": meta["summary"] if meta and meta.get("summary") else events[0]["text"],
                "events": events[:12],
                "confidence": meta["confidence"] if meta else ("high" if len(events) > 5 else "medium" if len(events) > 2 else "low"),
            })
            if meta and meta.get("key_risk"):
                solutions[-1]["keyRisk"] = meta["key_risk"]
            if meta and meta.get("key_opportunity"):
                solutions[-1]["keyOpportunity"] = meta["key_opportunity"]
            if meta and meta.get("trend_48h"):
                solutions[-1]["trend48h"] = meta["trend_48h"]
        else:
            # Dynamic category discovered by AI
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
                "events": events[:12],
                "confidence": "low",
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
        "overallMomentum": {
            "direction": m_dir,
            "label": m_label,
            "summary": f"{counts['advancing']} advancing, {counts['stable']} stable, {counts['stalling']} stalling ({len(active_solutions)}/{len(SOLUTIONS)} active). {len(articles)} ME articles from {len(RSS_FEEDS)} feeds.",
        },
        "activeSolutions": active_solutions,
        "lastUpdated": now.isoformat(),
        "source": "ai-analyzer",
        "feedCount": len(articles),
    }


# ═══════════════════════════════════════════════════════════════════════
# Upload to Cloudflare Pages
# ═══════════════════════════════════════════════════════════════════════

def upload_to_cloudflare(data):
    """Upload JSON to Cloudflare Pages as a static file."""
    if not CLOUDFLARE_TOKEN or not CLOUDFLARE_ACCOUNT:
        print("  ⚠ CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT not set, skipping upload")
        print("  Data written locally — deploy with: npx wrangler pages deploy app")
        return False

    import urllib.request
    try:
        # Upload via Cloudflare Pages Upload API
        url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT}/pages/projects/{CLOUDFLARE_PAGES_PROJECT}/deployments"

        # Alternative: use wrangler CLI
        import subprocess
        local_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "solutions.json")
        subprocess.run(
            ["npx", "wrangler", "pages", "deploy", "app",
             "--project-name", CLOUDFLARE_PAGES_PROJECT],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            check=True,
        )
        print("  ✓ Deployed to Cloudflare Pages")
        return True
    except Exception as e:
        print(f"  ⚠ Upload failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Peace Room AI Analyzer")
    parser.add_argument("--fetch-only", action="store_true", help="Only fetch RSS feeds, skip AI")
    parser.add_argument("--skip-upload", action="store_true", help="Skip Cloudflare upload")
    parser.add_argument("--backend", choices=["ollama", "llamacpp"], default="ollama", help="AI backend")
    args = parser.parse_args()

    if args.backend:
        global AI_BACKEND
        AI_BACKEND = args.backend

    start = time.time()

    # 1. Fetch RSS
    articles = fetch_all_feeds()
    if not articles:
        print("No articles found, aborting.")
        return

    # 2. AI Classification (skip if --fetch-only)
    if args.fetch_only:
        print("[--fetch-only] Skipping AI classification, using keyword fallback")
        classifications = keyword_classify(articles)
    else:
        classifications = classify_articles(articles)

    # 3. Build output
    data = build_output(articles, classifications, skip_meta=args.fetch_only)

    # 4. Write local JSON
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✓ Written to {DATA_FILE}")

    # 5. Write cache for comparison
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "articleCount": len(articles)}, f)

    elapsed = time.time() - start
    print(f"\n✓ Done in {elapsed:.1f}s")
    print(f"  {len(articles)} articles → {len(data['solutions'])} solutions")
    print(f"  Momentum: {data['overallMomentum']['label']}")

    # Print summary
    for sol in data["solutions"]:
        d = "🟢" if sol["direction"] == "advancing" else "🔴" if sol["direction"] == "stalling" else "🟡"
        phase = sol["phases"][sol["phaseIndex"]]
        print(f"  {sol['icon']} {sol['name']:35s} {sol['direction']:10s} {d} {sol['keyMetric']['value']} events → {phase}")


if __name__ == "__main__":
    main()
