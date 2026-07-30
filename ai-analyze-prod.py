#!/usr/bin/env python3
"""
Peace Paths AI Analyzer — Production (Narrative Pipeline)
==========================================================

Pipeline:
  [Raw RSS Feed] -> [Extract 1200 chars] -> [Single-article LLM inference]
                                                        |
                                          [me_relevant:true]    [me_relevant:false]
                                                |                       |
                                        [classify category]     [silently drop]
                                        + type, signal_score    |
                                        + source_weight         |
                                                |
                                      [Event Clustering]
                                                |
                                      [Solution Narrative]
                                                |
                                      [Shift Detection]
                                                |
                                          [data.json -> KV]

Modes:
  --fast   — Hourly: fetch recent articles (last 2h), merge into existing solutions.json
  --daily  — Daily: full fetch (7-day window), overwrite solutions.json
  --narrative — Force narrative rewrite (overrides auto-trigger)
  (default) — Same as --daily

Flags:
  --skip-upload — Skip Cloudflare API upload
  --dry-run — Print output JSON to stdout
  --fetch-only — Only fetch RSS, skip AI (keyword fallback)
  --review-taxonomy — Phase 1 only: propose taxonomy
  --research-categories — Research each category
  --apply-research — Apply research results to categories.json

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

# Fix Windows console encoding (works for TTY; fallback for pipes)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        # stdout is a pipe — wrap in UTF-8 encoder
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ─── Load .env ──────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    # Try script dir first, then project root (parent of dev-environment)
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _env_path = os.path.join(_script_dir, ".env")
    if not os.path.exists(_env_path):
        _env_path = os.path.join(os.path.dirname(_script_dir), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=True)
        print(f"  .env loaded from {_env_path}")
except ImportError:
    pass

# ─── Version ─────────────────────────────────────────────────────────
SCRIPT_VERSION = "2.0.0-narrative"

# ─── Paths ───────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = _SCRIPT_DIR  # Root pipeline lives in project root

# ─── Configuration ───────────────────────────────────────────────────

LLAMA_CPP_URL = os.environ.get("LLAMA_CPP_URL", "http://localhost:8080")
LLAMA_API_KEY = os.getenv("LLAMA_API_KEY", "")

CLOUDFLARE_PAGES_PROJECT = "peace-paths"
CLOUDFLARE_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
AI_MODEL = os.getenv("AI_MODEL", "Qwen3.6-27B")
CLOUDFLARE_ACCOUNT = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
DATA_FILE = os.path.join(DATA_DIR, "solutions.json")
DATA_JSON_FILE = os.path.join(DATA_DIR, "data.json")
TAXONOMY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taxonomy.json")
STAGING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "staging")
os.makedirs(STAGING_DIR, exist_ok=True)

MAX_ARTICLES_PER_FEED = 8
MAX_AGE_DAYS = 7
FAST_AGE_HOURS = 2

# ─── RSS Feeds ───────────────────────────────────────────────────────

RSS_FEEDS_FILE = os.path.join(_PROJECT_ROOT, "rss-feeds.json")

def load_rss_feeds():
    if not os.path.exists(RSS_FEEDS_FILE):
        print(f"❌ {RSS_FEEDS_FILE} not found.")
        sys.exit(1)
    with open(RSS_FEEDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# ─── Categories, Prompts, Source Profiles, Stakeholders ─────────────

CATEGORIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")
PROMPTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.json")
SOURCE_PROFILES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source-profiles.json")
STAKEHOLDERS_FILE = os.path.join(_PROJECT_ROOT, "stakeholders.json")


def load_stakeholders():
    if not os.path.exists(STAKEHOLDERS_FILE):
        return {}
    with open(STAKEHOLDERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_source_profiles():
    """Load source bias profiles from source-profiles.json."""
    if not os.path.exists(SOURCE_PROFILES_FILE):
        print(f"  ⚠ {SOURCE_PROFILES_FILE} not found. Using defaults.")
        return {}
    with open(SOURCE_PROFILES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_categories():
    if not os.path.exists(CATEGORIES_FILE):
        print(f"❌ {CATEGORIES_FILE} not found.")
        sys.exit(1)
    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        cats = json.load(f)
    cat_map = {c["id"]: c for c in cats}
    all_ids = list(cat_map.keys())
    core_ids = [c["id"] for c in cats if c.get("core", False)]
    all_kws = {}
    for c in cats:
        kws = c.get("keywords", [])
        if kws:
            all_kws[c["id"]] = kws
    return cat_map, all_ids, core_ids, all_kws


def save_categories(cat_map):
    cats_list = []
    for c in cat_map.values():
        cats_list.append({
            "id": c["id"],
            "icon": c.get("icon", "📌"),
            "name": c["name"],
            "description": c.get("description", ""),
            "phases": c.get("phases", []),
            "keywords": c.get("keywords", []),
            "core": c.get("core", False),
        })
    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cats_list, f, indent=2, ensure_ascii=False)


def load_prompts():
    if os.path.exists(PROMPTS_FILE):
        with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
            prompts = json.load(f)
        print(f"  📝 Prompts loaded from {PROMPTS_FILE}")
        return prompts
    else:
        return _DEFAULT_PROMPTS

# ─── Hardcoded default prompts ───
_DEFAULT_PROMPTS = {
    "taxonomy": {
        "system": "Middle East news taxonomy designer. Output ONLY valid JSON with keys: categories, assignments. No explanation.",
        "user": (
            "You are a Middle East news analyst. Review the articles below and propose"
            " a taxonomy of categories that best organizes them."
            "{CORE_BLOCK}"
            "\nRULES:\n- Propose 6-14 categories total. No fewer than 4.\n"
            "- Each category: id, name, description, icon, phases (5), keywords (5-8)\n"
            "- Be specific. Assign each article to exactly one category.\n\n"
            "Output ONLY JSON:\n"
            '{"categories": [...], "assignments": {"1": "cat-id"}}\n\n'
            "Articles:\n{ARTICLES_TEXT}"
        ),
    },
    "classifier": {
        "system": (
            "NEWS CLASSIFIER — Middle East peace initiatives.\n"
            "Output format: single JSON object (or JSON array for batch). No text outside JSON.\n\n"
            "CATEGORIES:\n{CATEGORIES_BLOCK}\n\n"
            "DECISION LOGIC:\n"
            "- me_relevant: true if article relates to any listed category\n"
            "- category: exact ID from list above. null if me_relevant=false\n"
            "- sentiment: positive | negative | neutral\n"
            "- type: reporting | analysis | opinion\n"
            "- signal_score: integer 1-10\n\n"
            "CRITICAL RULES:\n"
            "1. Hezbollah/Lebanon articles are NOT about Gaza.\n"
            "2. Gaza in passing while about Lebanon → use Lebanon or me_relevant=false.\n"
            "3. Choose the MOST SPECIFIC matching category.\n"
            "4. Do NOT invent new category IDs.\n\n"
            "Valid IDs: {CATEGORY_IDS}"
        ),
    },
    "article_user": {
        "user": (
            "[TASK] Classify article\n"
            "[TITLE] {TITLE}\n"
            "[SNIPPET] {SNIPPET}\n"
            "[SOURCE] {SOURCE}\n"
            "[PROFILE] {SOURCE_PROFILE}\n\n"
            '[OUTPUT]\n{"me_relevant": bool, "category": "id|null", "sentiment": "str", "type": "str", "signal_score": int}'
        ),
    },
    "batch_user": {
        "user": (
            "Classify these {BATCH_SIZE} articles.\n\n"
            "{ARTICLES_TEXT}\n\n"
            "For EACH: me_relevant, category, sentiment, type, signal_score.\n"
            "Use ONLY these category IDs: {CATEGORY_IDS}\n"
            "Output JSON array in SAME ORDER. No article_num field."
        ),
    },
    "phases": {
        "system": "Middle East analyst. Output ONLY valid JSON with key 'phases'.",
        "user": (
            "Determine current phase for each solution.\n\n"
            "Rules: completed phases → pick next. Violence = stalled, not regressed.\n"
            "Weight recent events. Be realistic.\n\n{SOLUTIONS_TEXT}\n\n"
            'Output: {"phases": {"solution-id": 2}}'
        ),
    },
    "narrative": {
        "system": "Middle East peace analyst. Generate layered trilingual narrative. Output ONLY valid JSON.",
        "user": (
            "Generate layered narrative for: {SOLUTION_NAME}\n"
            "Phase: {CURRENT_PHASE} (index {PHASE_INDEX}/{PHASE_COUNT})\n"
            "Direction: {DIRECTION}\n\n"
            "Clustered events (by effective_signal):\n{EVENTS_TEXT}\n\n"
            "Previous longTerm: {PREV_LONG_TERM}\n"
            "Previous shifts: {PREV_SHIFTS}\n\n"
            "Generate JSON with en/he/ar translations:\n"
            '{"longTerm": {"en":"...","he":"...","ar":"..."}, "weeklyHighlight": {...},\n'
            ' "keyEvents": [...], "keyOpinions": [...], "shifts": [...]}\n\n'
            "longTerm: 2-4 sentences, macro arc. Only change if fundamentally shifted.\n"
            "weeklyHighlight: 1-2 sentences, what happened this period.\n"
            "keyEvents: Top 3-5 reporting/analysis by effective_signal.\n"
            "keyOpinions: Top 2-3 opinion pieces.\n"
            "shifts: Narrative-changing events this period.\n"
            "Translate ALL text to en, he, ar. Use effective_signal to prioritize."
        ),
    },
    "research": {
        "system": "Middle East analyst. Output ONLY valid JSON with keys: description, phases, keywords.",
        "user": (
            "Research and improve category: {CATEGORY_ID} ({CATEGORY_NAME})\n"
            "Description: {CATEGORY_DESCRIPTION}\nPhases: {CATEGORY_PHASES}\nKeywords: {CATEGORY_KEYWORDS}\n\n"
            "Articles:\n{ARTICLES_TEXT}\n\n"
            "Rewrite description, redesign 5 phases, suggest 6-10 keywords.\n"
            'Output: {"description": "...", "phases": [...], "keywords": [...]}'
        ),
    },
    "generate_category": {
        "system": "Expert on ME politics. Generate category metadata.",
        "user": (
            "Category about: '{CATEGORY_NAME}'\n"
            'Output: {"description": "...", "icon": "...", "phases": [...], "keywords": [...]}'
        ),
    },
}

# ─── Global prompt store ───
PROMPTS = None


def inject_category(cat_map, cat_id, name, description, icon=None):
    if cat_id in cat_map:
        cat_map[cat_id]["description"] = description
    else:
        cat_map[cat_id] = {
            "id": cat_id, "icon": icon or "📌", "name": name,
            "phases": [{"en": "Emerged", "he": "התגלה", "ar": "ظهر"}, {"en": "Developing", "he": "בפיתוח", "ar": "تتطور"}, {"en": "Gaining Traction", "he": "רוכז תאוצה", "ar": "تكتسب زخمًا"}, {"en": "Maturing", "he": "בהבשלה", "ar": "تتبلور"}, {"en": "Resolved", "he": "התפתר", "ar": "تم حله"}],
            "description": description, "keywords": [], "core": False,
        }


# ═══════════════════════════════════════════════════════════════════════
# Date Parsing
# ═══════════════════════════════════════════════════════════════════════

def _parse_rss_date(date_str):
    """Parse RSS date in any common format → ISO 8601 string.
    Handles: RFC 822 (Tue, 02 Jun 2026 21:44:08 +0000), ISO 8601, and fallback."""
    if not date_str:
        return None
    # Try ISO 8601
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.isoformat()
    except (ValueError, TypeError):
        pass
    # Try RFC 822 (emailutils.parsedate_to_datetime handles +0000, GMT, etc.)
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.isoformat()
    except Exception:
        pass
    # Try common patterns: "Jun 2, 2026", "02/06/2026", etc.
    from datetime import datetime as _dt
    for fmt in ("%b %d, %Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = _dt.strptime(date_str.strip(), fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
    # Give up — return None so caller knows date is unknown
    return None


# ═══════════════════════════════════════════════════════════════════════
# RSS Fetching & Parsing
# ═══════════════════════════════════════════════════════════════════════

def _extract_text(raw_html):
    if HAS_BS4:
        soup = BeautifulSoup(raw_html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
    else:
        text = html.unescape(raw_html)
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_rss(url, source, max_items):
    try:
        req = Request(url, headers={"User-Agent": "PeaceMeter/1.0"})
        with urlopen(req, timeout=10) as f:
            xml = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ {source}: {e}")
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

        snippet = ""
        content_m = re.search(r"<content:encoded>(.*?)</content:encoded>", block, re.DOTALL)
        if content_m:
            raw = content_m.group(1).replace("<![CDATA[", "").replace("]]>", "")
            snippet = _extract_text(raw)
        else:
            desc_m = re.search(r"<description>(.*?)</description>", block, re.DOTALL)
            if desc_m:
                raw = desc_m.group(1).replace("<![CDATA[", "").replace("]]>", "")
                snippet = _extract_text(raw)
        snippet = snippet[:1200]

        link = link_m.group(1).strip() if link_m else ""
        date_str = date_m.group(1).strip() if date_m else None
        # Parse RSS date (RFC 822, ISO 8601, or other)
        date_str = _parse_rss_date(date_str)

        articles.append({
            "title": title, "link": link, "date": date_str,
            "source": source, "snippet": snippet,
        })
    return articles


def fetch_all_feeds(age_hours=None):
    feeds = load_rss_feeds()
    print(f"\U0001f4e1 Fetching {len(feeds)} RSS feeds...")
    now = datetime.now(timezone.utc)
    max_age = now.timestamp() - (age_hours * 3600 if age_hours else MAX_AGE_DAYS * 86400)

    fetched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(fetch_rss, url, name, MAX_ARTICLES_PER_FEED): (name, url, ft)
            for name, url, ft in feeds
        }
        for future in concurrent.futures.as_completed(futures, timeout=60):
            try:
                fetched.extend(future.result())
            except Exception:
                pass

    all_articles = []
    no_date = 0
    too_old = 0
    for a in fetched:
        date_str = a.get("date")
        if not date_str:
            # No parseable date — discard, don't assume "now"
            no_date += 1
            continue
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.timestamp() < max_age:
                too_old += 1
                continue
        except Exception:
            no_date += 1
            continue
        all_articles.append(a)

    # Dedupe: first by link (exact same article), then by title
    seen_links = set()
    seen_titles = set()
    unique = []
    for a in all_articles:
        link = a.get("link", "").strip().lower()
        title = a["title"].lower().strip()
        if link and link in seen_links:
            continue
        if title in seen_titles:
            continue
        if link:
            seen_links.add(link)
        seen_titles.add(title)
        unique.append(a)

    print(f"  → {len(unique)} unique articles ({len(fetched) - len(unique)} filtered: "
          f"{too_old} too old, {no_date} no date, {len(all_articles) - len(unique)} dupes)")
    return unique


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Taxonomy Proposal
# ═══════════════════════════════════════════════════════════════════════

def propose_taxonomy(articles, core_cats=None):
    sample = articles[:100]
    lines = [f"{i+1}. {a['title']}" for i, a in enumerate(sample)]
    articles_text = "\n".join(lines)

    core_block = ""
    if core_cats:
        core_lines = [f"  - {c['id']}: {c['name']} — {c['description']}" for c in core_cats]
        core_block = "\n\nCORE categories (MUST include):\n" + "\n".join(core_lines)

    prompt = PROMPTS["taxonomy"]["user"].format(CORE_BLOCK=core_block, ARTICLES_TEXT=articles_text)
    return _llm_chat([
        {"role": "system", "content": PROMPTS["taxonomy"]["system"]},
        {"role": "user", "content": prompt}
    ], max_tokens=16000, timeout=300)


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: AI Classification (with source_weight + signal_score + type)
# ═══════════════════════════════════════════════════════════════════════

def _make_classifier_prompt(cat_map, solution_contexts=None):
    cat_ids = list(cat_map.keys())
    lines = []
    for cid in cat_ids:
        c = cat_map[cid]
        line = f"  {cid}: {c['name']} — {c['description']}"
        phases = c.get("phases", [])
        if phases:
            phase_strs = [p.get("en", p) if isinstance(p, dict) else p for p in phases]
            line += f"\n    Phases: {' → '.join(phase_strs)}"
        if solution_contexts and cid in solution_contexts:
            line += f"\n    [Context: {solution_contexts[cid]}]"
        lines.append(line)
    block = "\n".join(lines)
    cat_list = ", ".join(cat_ids)
    return PROMPTS["classifier"]["system"].format(CATEGORIES_BLOCK=block, CATEGORY_IDS=cat_list), cat_ids


# ═══════════════════════════════════════════════════════════════════════
# Expanded keyword map for two-pass classification
# ═══════════════════════════════════════════════════════════════════════

_EXPANDED_KEYWORDS = {
    "iran-us-peace-process": ["tehran", "shiraz", "isfahan", "nuclear", "enrichment", "iaea", "fatemi",
                              "khamenei", "operation roaring", "ceasefire iran", "iran war", "iran ceasefire",
                              "iran deal", "iran us", "iran united states", "iranian", "dealey",
                              "qatar mediation", "pakistan mediation", "strait of hormuz", "hormuz"],
    "20-point-gaza-peace-plan": ["gaza", "hamas", "witkoff", "ncag", "board of peace", "gaza peace",
                                  "gaza ceasefire", "gaza war", "gaza strip", "gaza reconstruction",
                                  "disarmament", "demilitarization", "gaza governance", "hostage"],
    "lebanon-hezbollah-conflict": ["hezbollah", "lebanon", "beirut", "southern lebanon", "blue line",
                                    "shebaa", "resolution 1701", "un 1701", "nabih berri", "hasbeallah",
                                    "israel-lebanon", "lebanon border", "lebanon ceasefire", "displaced lebanon"],
    "abraham-accords": ["abraham accords", "normalization", "mbs", "saudi arabia", "bahrain", "morocco",
                         "sudan", "qatar", "uae", "emirati", "pakistan", "pakistan normalization",
                         "trump accords", "diplomatic relations"],
    "geneva-initiative": ["two-state", "palestinian state", "geneva initiative", "two-state index", "tsi",
                           "mutual recognition", "saudi-france", "recognition", "palestinian",
                           "palestine state", "statehood"],
    "israeli-annexation-of-the-west-bank": ["west bank", "annexation", "area c", "smotrich", "settlements",
                                             "seam zone", "al-haq", "land registration", "settlement expansion",
                                             "hebron", "jericho", "ramallah"],
    "india-middle-east-europe-economic-corridor": ["imec", "trade corridor", "rail link", "shipping route",
                                                     "silk route", "economic corridor", "india middle east",
                                                     "india europe", "multimodal"],
}


def _build_keyword_map(cat_map):
    """Build keyword→category map from categories + expanded keywords."""
    kw_map = {}
    for c in cat_map.values():
        for kw in c.get("keywords", []):
            kw_map[kw.lower()] = c["id"]
    for cat_id, kws in _EXPANDED_KEYWORDS.items():
        if cat_id in cat_map:
            for kw in kws:
                kw_map[kw.lower().strip()] = cat_id
    return kw_map


def _keyword_classify_one(title, snippet, kw_map):
    """Classify a single article by keywords. Returns (category_id, confidence) or (None, 0)."""
    text = f"{title} {snippet}".lower()
    scores = {}
    for kw, cat_id in kw_map.items():
        if kw in text:
            scores[cat_id] = scores.get(cat_id, 0) + 1
    if scores:
        best = max(scores, key=scores.get)
        return best, scores[best]
    return None, 0


def _batch_classify(articles, system_prompt, valid_ids, source_profiles=None, batch_size=10):
    """Classify a batch of articles in a single LLM call.
    Returns list of classification dicts, one per article."""
    n = len(articles)
    txt = ""
    for j, a in enumerate(articles):
        txt += f"<article_{j+1}>\n<title>{a['title']}</title>\n<snippet>{a.get('snippet','')[:300]}</snippet>\n<source>{a['source']}</source>\n</article_{j+1}>\n"

    user = f"Classify these {n} articles.\n\n{txt}\nFor EACH article output: me_relevant, category, sentiment, type, signal_score.\nUse ONLY these category IDs: {', '.join(valid_ids)}\nOutput a JSON array in SAME ORDER. No article_num field.\n"

    body = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user},
        ],
        "max_tokens": 8000,
        "temperature": 0.0,
    }

    headers = {"Content-Type": "application/json"}
    if LLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {LLAMA_API_KEY}"

    req = Request(f"{LLAMA_CPP_URL}/v1/chat/completions", data=json.dumps(body).encode(), headers=headers)
    try:
        with urlopen(req, timeout=300) as f:
            response = json.loads(f.read().decode())
    except Exception as e:
        print(f"  AI unavailable in batch: {e}")
        return None

    raw = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else "".join(lines[1:]).strip()

    first, last = raw.find("["), raw.rfind("]")
    if first != -1 and last > first:
        try:
            parsed = json.loads(raw[first:last+1])
            if isinstance(parsed, list) and len(parsed) == n:
                # Fill in defaults
                results = []
                for item in parsed:
                    item.setdefault("type", "reporting")
                    item.setdefault("signal_score", 5)
                    item.setdefault("source_weight", 2)
                    results.append(item)
                return results
        except json.JSONDecodeError:
            pass

    # Parse failed — return None to trigger fallback
    return None


def _classify_article(article, system_prompt, valid_ids, source_profiles=None):
    """Classify single article. Returns dict with type, signal_score, source_weight."""
    snippet = article.get("snippet", "")
    context = snippet if snippet else article["title"]
    source_line = f"\n<source>{article['source']}</source>" if article.get("source") else ""

    # Source profile context
    source_name = article.get("source", "")
    source_profile = "N/A"
    if source_profiles and source_name in source_profiles:
        sp = source_profiles[source_name]
        source_profile = f"lean={sp.get('lean','unknown')}, region={sp.get('region','unknown')}"

    user_prompt = PROMPTS["article_user"]["user"].format(
        TITLE=article['title'],
        SNIPPET=context,
        SOURCE=article.get('source', ''),
        SOURCE_PROFILE=source_profile
    )

    body = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 1500,
        "temperature": 0.0,
    }

    headers = {"Content-Type": "application/json"}
    if LLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {LLAMA_API_KEY}"

    req = Request(f"{LLAMA_CPP_URL}/v1/chat/completions", data=json.dumps(body).encode(), headers=headers)
    try:
        with urlopen(req, timeout=120) as f:
            response = json.loads(f.read().decode())
    except Exception as e:
        print(f"  AI unavailable: {e}")
        return None

    result_text = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not result_text:
        return {"_refused": True, "_text": "(empty response)"}

    if result_text.startswith("```"):
        lines = result_text.split("\n")
        result_text = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else "".join(lines[1:]).strip()

    first_brace = result_text.find('{')
    last_brace = result_text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            obj = json.loads(result_text[first_brace:last_brace+1])
            if "me_relevant" in obj:
                # Ensure new fields exist
                obj.setdefault("type", "reporting")
                obj.setdefault("signal_score", 5)
                obj.setdefault("source_weight", 2)
                return obj
        except json.JSONDecodeError:
            pass
    return {"_refused": True, "_text": result_text[:100]}


def classify_articles(articles, system_prompt, valid_ids, source_profiles=None):
    """Two-pass classification: keyword filter → batch LLM for uncertain articles.
    Pass 1: Pre-filter (EXCLUDE_KW) + keyword classify (conf>=2)
    Pass 2: Batch LLM classify remaining articles (batch_size=10)
    Falls back to single-article LLM if batch parse fails."""
    EXCLUDE_KW = [
        "world cup", "fifa", "afcon", "premier league", "man city", "guardiola",
        "champions league", "transfer", "football", "basketball", "soccer",
        "tennis", "olympics", "marathon", "racing", "formula 1",
        "fragrance", "bakhoor", "perfume", "fashion week", "runway",
        "real estate", "property investment", "metro station", "ferry crossing",
        "housing market", "apartments for sale", "villa",
        "hollywood", "celebrity", "sydney sweeney", "euphoria", "tv show",
        "netflix", "streaming", "movie", "film festival", "award ceremony",
        "sponsored", "ad", "advertisement", "billboard", "scabies",
        "secondhand smoke", "smoke in public", "vaccine", "pandemic",
        "authoritarian transformation", "istanbul", "ferrari",
    ]
    ME_OVERRIDE = [
        "israel", "palestine", "gaza", "iran", "syria", "lebanon", "jordan",
        "saudi", "uae", "qatar", "yemen", "iraq", "turkey", "egypt",
        "hezbollah", "hamas", "houthi", "west bank", "doha", "beirut",
        "tehran", "damascus", "amman", "riyadh", "dubai", "cairo",
        "abraham accords", "normalization", "mideast", "middle east",
        "ceasefire", "cease-fire", "un", "security council",
        "operation roaring lion", "strait of hormuz",
    ]
    KW_THRESHOLD = 2  # Minimum keyword confidence for pass-1 acceptance
    BATCH_SIZE = 10

    print(f"\U0001f916 Classifying {len(articles)} articles (two-pass: keyword + batch LLM)...")
    pairs = []
    relevant = 0
    dropped = 0
    pre_filtered = 0
    kw_classified = 0
    ai_failures = 0
    ai_refusals = 0
    stage_file = os.path.join(STAGING_DIR, "classification.json")
    start_time = time.time()

    # Build keyword map from all_kws global + expanded keywords
    kw_map = {}
    for cat_id, kws in all_kws.items():
        for kw in kws:
            kw_map[kw.lower()] = cat_id
    # Add expanded keywords
    for cat_id, kws in _EXPANDED_KEYWORDS.items():
        if cat_id in valid_ids:
            for kw in kws:
                kw_map[kw.lower().strip()] = cat_id

    # ── PASS 1: Pre-filter + keyword classify ──
    remaining = []  # articles needing LLM
    for article in articles:
        text = f"{article['title']} {article.get('snippet', '')}".lower()
        excluded = any(kw in text for kw in EXCLUDE_KW)
        if excluded and not any(kw in text for kw in ME_OVERRIDE):
            pre_filtered += 1
            continue

        cat_id, conf = _keyword_classify_one(article['title'], article.get('snippet', ''), kw_map)
        if conf >= KW_THRESHOLD and cat_id:
            # Keyword classified — add to pairs
            pairs.append((article, {
                "solution": cat_id,
                "sentiment": "neutral",
                "risk": 5,
                "type": "reporting",
                "signal_score": 5,
                "source_weight": 2,
            }))
            relevant += 1
            kw_classified += 1
        else:
            remaining.append(article)

    print(f"  Pass 1: {kw_classified} keyword-classified, {pre_filtered} pre-filtered, {len(remaining)} need LLM")

    # ── PASS 2: Batch LLM for remaining ──
    if remaining:
        print(f"  Pass 2: Batch LLM classifying {len(remaining)} articles (batch={BATCH_SIZE})...")
        batch_idx = 0
        for bi in range(0, len(remaining), BATCH_SIZE):
            batch = remaining[bi:bi+BATCH_SIZE]
            batch_idx += 1

            # Try batch classification
            batch_results = _batch_classify(batch, system_prompt, valid_ids, source_profiles, BATCH_SIZE)

            if batch_results is None:
                # Batch parse failed — fall back to single-article
                print(f"  ⚠ Batch {batch_idx} parse failed, falling back to single-article")
                for article in batch:
                    result = _classify_article(article, system_prompt, valid_ids, source_profiles)
                    if isinstance(result, dict) and result.get("_refused"):
                        ai_refusals += 1
                        continue
                    if result is None:
                        ai_failures += 1
                        if ai_failures <= 3:
                            result = _classify_article(article, system_prompt, valid_ids, source_profiles)
                            if result: ai_failures = 0
                        if result is None:
                            dropped += 1
                            continue
                    r, d = _add_classified(pairs, result, article, valid_ids)
                    relevant += r
                    dropped += d
            else:
                for article, result in zip(batch, batch_results):
                    if isinstance(result, dict) and result.get("_refused"):
                        ai_refusals += 1
                        continue
                    r, d = _add_classified(pairs, result, article, valid_ids)
                    relevant += r
                    dropped += d

            # Stream progress
            processed = len(pairs) + dropped
            wall = time.time() - start_time
            avg = wall / max(processed, 1)
            eta = avg * (len(articles) - processed)
            stage = {
                "progress": processed,
                "total": len(articles),
                "relevant": relevant,
                "dropped": dropped,
                "refusals": ai_refusals,
                "pre_filtered": pre_filtered,
                "kw_classified": kw_classified,
                "wall_seconds": round(wall, 1),
                "avg_per_article": round(avg, 2),
                "eta_seconds": round(eta, 1),
            }
            with open(stage_file, "w", encoding="utf-8") as f:
                json.dump(stage, f, indent=2, ensure_ascii=False)
            pct = processed * 100 // len(articles)
            print(f"  [{processed}/{len(articles)} ({pct}%)] {relevant} relevant, {dropped} dropped | {wall:.0f}s elapsed, ETA {eta:.0f}s")

    # Save final
    final_stage = {
        "complete": True,
        "total": len(articles),
        "relevant": relevant,
        "dropped": dropped,
        "refusals": ai_refusals,
        "pre_filtered": pre_filtered,
        "kw_classified": kw_classified,
        "llm_classified": relevant - kw_classified,
        "wall_seconds": round(time.time() - start_time, 1),
        "pairs_count": len(pairs),
    }
    with open(stage_file, "w", encoding="utf-8") as f:
        json.dump(final_stage, f, indent=2, ensure_ascii=False)
    print(f"  💾 Staged → staging/classification.json")

    print(f"  Total: {relevant} relevant ({kw_classified} kw + {relevant-kw_classified} LLM), "
          f"{dropped} dropped, {ai_refusals} refusals, {pre_filtered} pre-filtered")
    if ai_refusals > 0:
        pct = ai_refusals / max(len(articles), 1) * 100
        print(f"  🚨 AI content filter: {ai_refusals} articles ({pct:.1f}%)")
    return pairs, ai_refusals


def _add_classified(pairs, result, article, valid_ids):
    """Helper: add a classified article to pairs list. Returns (relevant_added, dropped_added)."""
    if result.get("me_relevant"):
        sol = result.get("category") or result.get("solution")
        if sol not in valid_ids:
            sol = _fallback_classify(article, all_kws) or "regional"
            print(f"  ⚠ Unknown category '{result.get('category')}', fallback → '{sol}'")
        pairs.append((article, {
            "solution": sol,
            "sentiment": result.get("sentiment", "neutral"),
            "risk": result.get("risk", 5),
            "type": result.get("type", "reporting"),
            "signal_score": result.get("signal_score", 5),
            "source_weight": result.get("source_weight", 2),
        }))
        return (1, 0)
    else:
        return (0, 1)


# ═══════════════════════════════════════════════════════════════════════
# Keyword fallback classifier
# ═══════════════════════════════════════════════════════════════════════

POSITIVE_WORDS = ["agreed", "signed", "resumed", "reopened", "released", "deal", "progress", "restored"]
NEGATIVE_WORDS = ["killed", "attack", "strike", "bombing", "destroyed", "escalat", "crisis", "failed"]


def _fallback_classify(article, kw_map):
    lower = article["title"].lower()
    scores = {}
    for cat_id, kws in kw_map.items():
        for kw in kws:
            if kw in lower:
                weight = 2 if " " in kw else 1
                scores[cat_id] = scores.get(cat_id, 0) + weight
    if scores:
        max_score = max(scores.values())
        return [k for k, v in scores.items() if v == max_score][0]
    return None


def keyword_classify(articles, kw_map):
    """Fallback keyword classification with default new fields."""
    results = []
    for article in articles:
        cat = _fallback_classify(article, kw_map)
        if cat:
            lower = article["title"].lower()
            pos = sum(1 for w in POSITIVE_WORDS if w in lower)
            neg = sum(1 for w in NEGATIVE_WORDS if w in lower)
            sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"
            results.append((article, {
                "solution": cat, "sentiment": sentiment, "risk": 5,
                "type": "reporting", "signal_score": 5, "source_weight": 2,
            }))
    return results


# ═══════════════════════════════════════════════════════════════════════
# Phase 1b: Event Clustering
# ═══════════════════════════════════════════════════════════════════════

def _normalize_title(title):
    """Normalize title for clustering comparison."""
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    # Remove common stop words
    stop = {'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or', 'is', 'was', 'are', 'were'}
    words = t.split()
    words = [w for w in words if w not in stop and len(w) > 2]
    return ' '.join(words)


def _title_similarity(t1, t2):
    """Compute token overlap similarity between two normalized titles."""
    set1 = set(t1.split())
    set2 = set(t2.split())
    if not set1 or not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union)


def cluster_events(pairs, source_profiles=None, threshold=0.65, time_window_hours=6):
    """Cluster articles describing the same event.
    
    Returns list of clustered events, each with:
      - representative article (highest effective_signal)
      - attestations (other sources reporting same event)
      - cross_attestation_bonus if sources have different leans
    """
    print(f"\U0001f517 Clustering {len(pairs)} articles into events...")
    
    # Group by solution first
    by_solution = {}
    for article, classification in pairs:
        sol = classification.get("solution", "regional")
        by_solution.setdefault(sol, []).append((article, classification))
    
    clustered = {}
    total_clusters = 0
    
    for sol_id, sol_pairs in by_solution.items():
        # Normalize titles
        normalized = []
        for article, classification in sol_pairs:
            norm = _normalize_title(article["title"])
            eff_signal = classification.get("signal_score", 5) * (classification.get("source_weight", 2) / 2)
            normalized.append((article, classification, norm, eff_signal))
        
        if len(normalized) <= 1:
            # Single article = single cluster
            clustered[sol_id] = [_make_cluster(normalized, source_profiles)]
            continue
        
        # Heuristic clustering
        clusters = []
        used = set()
        
        for i in range(len(normalized)):
            if i in used:
                continue
            cluster = [normalized[i]]
            used.add(i)
            
            for j in range(i + 1, len(normalized)):
                if j in used:
                    continue
                
                # Check time proximity
                try:
                    dt_i = datetime.fromisoformat(normalized[i][0]["date"])
                    dt_j = datetime.fromisoformat(normalized[j][0]["date"])
                    time_diff = abs((dt_i - dt_j).total_seconds()) / 3600
                except Exception:
                    time_diff = 0
                
                # Check title similarity
                sim = _title_similarity(normalized[i][2], normalized[j][2])
                
                # Adjust threshold based on time proximity
                effective_threshold = threshold - (0.1 if time_diff < time_window_hours else 0)
                
                if sim >= effective_threshold and time_diff < 24:
                    cluster.append(normalized[j])
                    used.add(j)
            
            if cluster:
                clusters.append(cluster)
        
        # Build clustered events
        clustered_events = []
        for cluster in clusters:
            clustered_events.append(_make_cluster(cluster, source_profiles))
        
        clustered[sol_id] = clustered_events
        total_clusters += len(clustered_events)
    
    # Flatten
    all_clustered = []
    for sol_id, events in clustered.items():
        for event in events:
            event["_solution"] = sol_id
        all_clustered.extend(events)
    
    print(f"  → {total_clusters} clusters from {len(pairs)} articles")
    return all_clustered, clustered


def _make_cluster(cluster_items, source_profiles):
    """Create a clustered event from a group of similar articles.
    
    Picks representative (highest effective_signal), collects attestations,
    checks for cross-attestation bonus.
    """
    # Sort by effective_signal desc
    cluster_items.sort(key=lambda x: x[3], reverse=True)
    rep_article, rep_class, _, rep_eff_signal = cluster_items[0]
    
    # Compute effective_signal for representative
    sig = rep_class.get("signal_score", 5)
    sw = rep_class.get("source_weight", 2)
    effective_signal = sig * (sw / 2)
    
    # Attestations
    attestations = []
    source_leans = set()
    rep_source = rep_article.get("source", "")
    if source_profiles and rep_source in source_profiles:
        source_leans.add(source_profiles[rep_source].get("lean", "unknown"))
    
    for article, classification, _, eff_signal in cluster_items[1:]:
        attestations.append({
            "source": article.get("source", ""),
            "link": article.get("link", ""),
            "signal_score": classification.get("signal_score", 5),
            "source_weight": classification.get("source_weight", 2),
        })
        src = article.get("source", "")
        if source_profiles and src in source_profiles:
            source_leans.add(source_profiles[src].get("lean", "unknown"))
    
    # Cross-attestation bonus
    has_cross_bonus = len(source_leans) >= 2
    if has_cross_bonus:
        effective_signal += 2
    
    # Trilingual text from classifier, or fallback to plain title
    text = rep_class.get("text")
    if not text or not isinstance(text, dict):
        text = rep_article["title"]

    return {
        "title": rep_article["title"],
        "text": text,
        "link": rep_article["link"],
        "source": rep_article.get("source", ""),
        "date": rep_article["date"],
        "snippet": rep_article.get("snippet", ""),
        "type": rep_class.get("type", "reporting"),
        "sentiment": rep_class.get("sentiment", "neutral"),
        "signal_score": sig,
        "source_weight": sw,
        "effective_signal": round(effective_signal, 1),
        "risk": rep_class.get("risk", 5),
        "attestations": attestations,
        "cross_attestation_bonus": has_cross_bonus,
        "cluster_size": len(cluster_items),
    }


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Solution-Level Narrative Generation
# ═══════════════════════════════════════════════════════════════════════

def generate_narratives(clustered_by_solution, cat_map, existing_data, force_narrative=False):
    """Generate per-solution narratives (longTerm, weeklyHighlight, keyEvents, keyOpinions, shifts).
    
    Returns dict {solution_id: narrative_obj}.
    """
    print(f"\U0001f4dc Generating narratives for {len(clustered_by_solution)} solutions...")
    narratives = {}
    
    for sol_id, events in clustered_by_solution.items():
        cat = cat_map.get(sol_id)
        if not cat:
            continue
        
        # Sort by effective_signal desc
        events_sorted = sorted(events, key=lambda e: e.get("effective_signal", 0), reverse=True)
        
        # Get previous narrative
        prev_narrative = None
        prev_long_term = ""
        prev_shifts = []
        if existing_data:
            for sol in existing_data.get("solutions", []):
                if sol["id"] == sol_id:
                    prev_narrative = sol.get("narrative")
                    if prev_narrative:
                        lt = prev_narrative.get("longTerm", "")
                        prev_long_term = lt if isinstance(lt, str) else lt.get("en", "")
                        prev_shifts = prev_narrative.get("shifts", [])
                    break
        
        # Determine current phase
        phase_index = 0
        if existing_data:
            for sol in existing_data.get("solutions", []):
                if sol["id"] == sol_id:
                    phase_index = sol.get("phaseIndex", 0)
                    break
        
        phases = cat.get("phases", [])
        current_phase_raw = phases[phase_index] if phase_index < len(phases) else "Unknown"
        current_phase = current_phase_raw.get("en", str(current_phase_raw)) if isinstance(current_phase_raw, dict) else current_phase_raw
        
        # Build events text (top 10 by effective_signal)
        events_text = ""
        for ev in events_sorted[:10]:
            att_str = f" (+{len(ev.get('attestations', []))} sources)" if ev.get("attestations") else ""
            events_text += f"  [{ev['type']}] signal={ev['effective_signal']} {ev['title']} — {ev['source']}{att_str}\n"
        
        # Build shifts text
        shifts_text = json.dumps(prev_shifts, ensure_ascii=False)[:500]
        
        prompt = PROMPTS["narrative"]["user"].format(
            SOLUTION_NAME=cat["name"],
            CURRENT_PHASE=current_phase,
            PHASE_INDEX=phase_index,
            PHASE_COUNT=len(phases),
            DIRECTION=existing_data and next((s.get("direction", "stable") for s in existing_data.get("solutions", []) if s["id"] == sol_id), "stable") or "stable",
            EVENTS_TEXT=events_text,
            PREV_LONG_TERM=prev_long_term,
            PREV_SHIFTS=shifts_text,
        )
        
        result = _llm_chat([
            {"role": "system", "content": PROMPTS["narrative"]["system"]},
            {"role": "user", "content": prompt}
        ], max_tokens=8000, timeout=240)
        
        if result:
            narratives[sol_id] = result
            print(f"  ✓ {cat['name']}: narrative generated")
        else:
            # Fallback: create minimal narrative
            narratives[sol_id] = _fallback_narrative(cat, events_sorted, prev_long_term)
            print(f"  ⚠ {cat['name']}: fallback narrative")
    
    return narratives


def _fallback_narrative(cat, events, prev_long_term):
    """Create minimal narrative when LLM fails."""
    top_event = events[0] if events else None
    reporting = [e for e in events if e.get("type") == "reporting"][:3]
    opinions = [e for e in events if e.get("type") == "opinion"][:2]
    
    lt = prev_long_term or f"The {cat['name']} process continues to evolve."
    
    return {
        "longTerm": {"en": lt, "he": lt, "ar": lt},
        "weeklyHighlight": {"en": top_event["title"] if top_event else "", "he": "", "ar": ""},
        "keyEvents": [
            {
                "title": {"en": e["title"], "he": "", "ar": ""},
                "link": e["link"], "source": e["source"], "date": e["date"],
                "type": e["type"], "signal_score": e["signal_score"],
                "source_weight": e["source_weight"],
                "effective_signal": e["effective_signal"],
                "attestations": e.get("attestations", []),
            }
            for e in reporting
        ],
        "keyOpinions": [
            {
                "quote": {"en": e["title"], "he": "", "ar": ""},
                "link": e["link"], "source": e["source"], "date": e["date"],
                "type": "opinion", "signal_score": e["signal_score"],
                "source_weight": e["source_weight"],
                "effective_signal": e["effective_signal"],
            }
            for e in opinions
        ],
        "shifts": [],
    }


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: Shift Detection
# ═══════════════════════════════════════════════════════════════════════

def detect_shifts(current_solutions, previous_solutions):
    """Detect narrative shifts by comparing current vs previous data."""
    shifts = []
    now = datetime.now(timezone.utc).isoformat()
    
    prev_map = {s["id"]: s for s in previous_solutions}
    
    for cur in current_solutions:
        prev = prev_map.get(cur["id"])
        if not prev:
            continue
        
        # Phase changed
        if cur.get("phaseIndex") != prev.get("phaseIndex"):
            cur_phase_raw = cur["phases"][cur["phaseIndex"]] if cur["phaseIndex"] < len(cur["phases"]) else "?"
            cur_phase = cur_phase_raw.get("en", str(cur_phase_raw)) if isinstance(cur_phase_raw, dict) else cur_phase_raw
            prev_phase_raw = prev["phases"][prev["phaseIndex"]] if prev["phaseIndex"] < len(prev["phases"]) else "?"
            prev_phase = prev_phase_raw.get("en", str(prev_phase_raw)) if isinstance(prev_phase_raw, dict) else prev_phase_raw
            shifts.append({
                "solutionId": cur["id"],
                "desc": {"en": f"Phase changed: {prev_phase} → {cur_phase}", "he": "", "ar": ""},
                "direction": "positive" if cur["phaseIndex"] > prev["phaseIndex"] else "negative",
                "date": now,
            })
        
        # Direction flipped
        if cur.get("direction") != prev.get("direction"):
            if cur["direction"] == "advancing" and prev["direction"] in ("stalling", "stable"):
                shifts.append({
                    "solutionId": cur["id"],
                    "desc": {"en": f"Direction improved: {prev['direction']} → advancing", "he": "", "ar": ""},
                    "direction": "positive",
                    "date": now,
                })
            elif cur["direction"] == "stalling" and prev["direction"] in ("advancing", "stable"):
                shifts.append({
                    "solutionId": cur["id"],
                    "desc": {"en": f"Direction worsened: {prev['direction']} → stalling", "he": "", "ar": ""},
                    "direction": "negative",
                    "date": now,
                })
        
        # High-signal articles (signal_score >= 9)
        for ev in cur.get("narrative", {}).get("keyEvents", []):
            if ev.get("signal_score", 0) >= 9:
                # Check if this event was in previous data
                prev_titles = set()
                for e in prev.get("events", []):
                    t = e.get("text", "")
                    prev_titles.add(t["en"] if isinstance(t, dict) else t)
                ev_title = ev.get("title", "")
                if isinstance(ev_title, dict):
                    ev_title = ev_title.get("en", "")
                if ev_title not in prev_titles:
                    shifts.append({
                        "solutionId": cur["id"],
                        "desc": {"en": ev.get("title", {}).get("en", ev.get("title", "")), "he": "", "ar": ""},
                        "direction": "negative" if ev.get("sentiment") == "negative" else "positive",
                        "date": ev.get("date", now),
                    })
    
    return shifts


# ═══════════════════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════════════════

def parse_date(date_str):
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
    pos = sum(1 for e in events if e.get("sentiment") == "positive")
    neg = sum(1 for e in events if e.get("sentiment") == "negative")
    ratio = pos / (pos + neg) if (pos + neg) > 0 else 0.5
    if ratio > 0.65:
        return "advancing"
    elif ratio < 0.35:
        return "stalling"
    return "stable"


def _llm_chat(messages, max_tokens=4000, temperature=0.0, timeout=180, raw_text=False):
    body = {
        "model": AI_MODEL, "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    if LLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {LLAMA_API_KEY}"
    
    req = Request(f"{LLAMA_CPP_URL}/v1/chat/completions", data=json.dumps(body).encode(), headers=headers)
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
    
    if raw_text:
        return result_text
    
    first_brace = result_text.find('{')
    last_brace = result_text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(result_text[first_brace:last_brace+1])
        except json.JSONDecodeError:
            pass
    return None


# ─── Translation helpers ─────────────────────────────────────────────
_translation_cache = {}

def _translate(text, target_lang):
    """Translate text to target language (he or ar) via LLM. Uses cache."""
    cache_key = f"{target_lang}:{text[:200]}"
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]
    prompt = f"Translate this to {target_lang}. Output ONLY the translation, no other text:\n\n{text}"
    result = _llm_chat([
        {"role": "system", "content": "You are a translator. Output ONLY the translated text."},
        {"role": "user", "content": prompt}
    ], max_tokens=500, timeout=60, raw_text=True)
    translated = result if isinstance(result, str) and result else text
    _translation_cache[cache_key] = translated
    return translated

def _make_trilingual(text):
    """Create {en, he, ar} dict from English text. Translates via LLM."""
    return {"en": text, "he": _translate(text, "hebrew"), "ar": _translate(text, "arabic")}

def _batch_translate(texts, target_lang):
    """Batch translate a list of texts to target language. Returns list of translations."""
    if not texts:
        return []
    # Deduplicate
    unique = list(dict.fromkeys(texts))
    if len(unique) <= 1:
        return [_translate(t, target_lang) for t in texts]

    lang_name = "Hebrew" if "hebr" in target_lang else "Arabic"
    # Chunk into groups of 5 for reliable translation
    chunk_size = 5
    all_translations = {}

    for chunk_start in range(0, len(unique), chunk_size):
        chunk = unique[chunk_start:chunk_start + chunk_size]
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(chunk))
        prompt = f"""Translate each of these English texts to {lang_name}. Output ONLY the {lang_name} translations, one per line, same order:

{numbered}"""
        result = _llm_chat([
            {"role": "system", "content": f"You are a professional translator. Translate English to {lang_name}. Output ONLY the {lang_name} text, one per line. Do NOT include numbers or English."},
            {"role": "user", "content": prompt}
        ], max_tokens=1500, timeout=120, raw_text=True)

        if isinstance(result, str) and result.strip():
            translated_lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        else:
            translated_lines = chunk  # fallback: use single translate

        # Validate: if translation equals source or wrong script, re-translate
        for orig, trans in zip(chunk, translated_lines[:len(chunk)]):
            is_valid = trans != orig
            # Also verify correct script
            if is_valid:
                if "hebr" in target_lang:
                    # Hebrew must contain Hebrew script chars
                    is_valid = any('\u0590' <= c <= '\u05FF' for c in trans)
                elif "arab" in target_lang:
                    # Arabic must contain Arabic script chars
                    is_valid = any('\u0600' <= c <= '\u06FF' for c in trans)
            if not is_valid:
                # Fallback: single translate
                trans = _translate(orig, target_lang)
            all_translations[orig] = trans
            _translation_cache[f"{target_lang}:{orig[:200]}"] = trans

    # Map back to original order
    return [all_translations.get(t, t) for t in texts]

def _batch_translate_dual(texts):
    """Translate a list of English texts to both Hebrew and Arabic in batch.
    Returns dict {text: {en, he, ar}}.
    Uses two LLM calls per chunk (one Hebrew, one Arabic) instead of two per text.
    Uses JSON array output for reliable parsing."""
    import json, re
    if not texts:
        return {}
    unique = list(dict.fromkeys(texts))
    trilingual_map = {}
    chunk_size = 5

    for lang, lang_name in [("he", "Hebrew"), ("ar", "Arabic")]:
        for chunk_start in range(0, len(unique), chunk_size):
            chunk = unique[chunk_start:chunk_start + chunk_size]
            input_json = json.dumps(chunk, ensure_ascii=False)
            # Domain glossary for political/diplomatic terminology
            if lang == "he":
                glossary = """Use these exact Hebrew terms:
Annexation → סיפוח (NOT אנקסיה)
Ceasefire → הפסקת אש
Mediation → גישור
Displacement → העתקה
Normalization → נרמול
Two-State Solution → פתרון שתי מדינות
Diplomacy → דיפלומטיה
Reconstruction → שיקום
Stabilization → ייצוב
Framework Agreement → הסכם מסגרת
Trade Integration → אינטגרציה מסחרית
Civil Society → החברה האזרחית"""
            elif lang == "ar":
                glossary = """Use these exact Arabic terms:
Annexation → ضم (NOT مرفق)
Ceasefire → وقف إطلاق النار
Mediation → وساطة
Displacement → إزاحة (NOT تشرد)
Normalization → تطبيع
Two-State Solution → حل الدولتين
Diplomacy → دبلوماسية
Reconstruction → إعادة الإعمار
Stabilization → استقرار
Framework Agreement → اتفاقية إطار
Trade Integration → تكامل تجاري
Civil Society → المجتمع المدني"""
            else:
                glossary = ""

            prompt = f"""Translate each English text to {lang_name}.

Input (JSON array): {input_json}

Output ONLY a JSON array of {lang_name} translations in the same order.
Example: ["translation1", "translation2", "translation3"]

Rules:
- Output ONLY the JSON array, nothing else
- Use proper {lang_name} grammar and natural phrasing
- Translate ALL words, do not leave English words
- Keep abbreviations like G20, MoU, UN, NCAG as-is
{glossary}"""
            result = _llm_chat([
                {"role": "system", "content": f"You are a professional translator from English to {lang_name} specializing in Middle East political and diplomatic terminology. Output ONLY JSON arrays."},
                {"role": "user", "content": prompt}
            ], max_tokens=2000, timeout=120, raw_text=True)

            translated_lines = ["" for _ in chunk]
            if isinstance(result, str) and result.strip():
                # Try JSON parse first
                try:
                    clean = result.strip().strip('`').strip()
                    if clean.startswith('['):
                        translated_lines = json.loads(clean)
                        if not isinstance(translated_lines, list):
                            translated_lines = ["" for _ in chunk]
                except (json.JSONDecodeError, ValueError):
                    # Fallback: split by newline or |
                    translated_lines = [t.strip() for t in re.split(r'\|', result.strip())]
                    # Strip leading numbers
                    translated_lines = [re.sub(r'^\d+[\s.\u00b7]+', '', t) for t in translated_lines]

            for orig, trans in zip(chunk, translated_lines[:len(chunk)]):
                if not isinstance(trans, str):
                    trans = str(trans)
                trans = trans.strip()
                is_valid = trans != orig and trans != ""
                if is_valid:
                    if lang == "he":
                        is_valid = any('\u0590' <= c <= '\u05FF' for c in trans)
                    else:
                        is_valid = any('\u0600' <= c <= '\u06FF' for c in trans)
                if not is_valid:
                    trans = _translate(orig, "hebrew" if lang == "he" else "arabic")
                if orig not in trilingual_map:
                    trilingual_map[orig] = {"en": orig, "he": "", "ar": ""}
                trilingual_map[orig][lang] = trans
                _translation_cache[f"{lang}:{orig[:200]}"] = trans

    return trilingual_map


def _translate_events_to_trilingual(events, solution_names, max_events_per_solution=10):
    """Convert solution names and recent event texts to {en, he, ar} format.
    Only translates the most recent events per solution (those actually displayed in UI).
    Uses batch translation (10 texts per LLM call) instead of per-text calls."""
    # Collect texts to translate
    texts_to_translate = set()

    # Solution names
    for s in solution_names:
        if isinstance(s, str):
            texts_to_translate.add(s)
        elif isinstance(s, dict) and (s.get('he') == s.get('en') or s.get('ar') == s.get('en')):
            texts_to_translate.add(s['en'])

    # Events — only recent ones per solution (grouped by solution for limit)
    # events is a flat list from all solutions; we need to know boundaries.
    # Caller passes a dict {sol_id: [events]} for proper grouping.
    if isinstance(events, dict):
        for sol_id, ev_list in events.items():
            recent = ev_list[:max_events_per_solution]
            for ev in recent:
                t = ev.get("text", "")
                if isinstance(t, str) and t:
                    texts_to_translate.add(t)
                elif isinstance(t, dict) and (t.get('he') == t.get('en') or t.get('ar') == t.get('en')):
                    texts_to_translate.add(t['en'])
    else:
        # Flat list — translate all (legacy path for step 9.5)
        for ev in events:
            t = ev.get("text", "")
            if isinstance(t, str) and t:
                texts_to_translate.add(t)
            elif isinstance(t, dict) and (t.get('he') == t.get('en') or t.get('ar') == t.get('en')):
                texts_to_translate.add(t['en'])

    texts_to_translate.discard("")
    if not texts_to_translate:
        print(f"  🌐 No texts to translate (already trilingual or empty)")
        return

    texts_list = list(texts_to_translate)
    print(f"  🌐 Translating {len(texts_list)} unique texts to Hebrew & Arabic (batch)...")

    trilingual_map = _batch_translate_dual(texts_list)

    # Apply to solution names
    for i, s in enumerate(solution_names):
        if isinstance(s, str) and s in trilingual_map:
            solution_names[i] = trilingual_map[s]
        elif isinstance(s, dict) and s.get('en') in trilingual_map:
            solution_names[i] = trilingual_map[s['en']]

    # Apply to events
    if isinstance(events, dict):
        # events is {sol_id: [event_dict, ...]} — flatten to individual events
        event_list = []
        for ev_list in events.values():
            event_list.extend(ev_list)
    else:
        event_list = events
    for ev in event_list:
        if not isinstance(ev, dict):
            continue
        t = ev.get("text", "")
        if isinstance(t, str) and t in trilingual_map:
            ev["text"] = trilingual_map[t]
        elif isinstance(t, str) and t:
            ev["text"] = _make_trilingual(t)
        elif isinstance(t, dict) and t.get('en') in trilingual_map:
            ev["text"] = trilingual_map[t['en']]


def determine_phases_ai(solution_events, cat_map):
    blocks = []
    for sol_id, events in solution_events.items():
        cat = cat_map.get(sol_id)
        if not cat:
            continue
        phases = cat.get("phases", [])
        if not phases:
            continue
        recent = sorted(events, key=lambda e: e["date"], reverse=True)[:8]
        _extract_text = lambda e: e.get('text', {}).get('en', '') if isinstance(e.get('text'), dict) else e.get('text', '')
        event_lines = "\n".join(f"    - [{e['sentiment']}] {_extract_text(e)}" for e in recent)
        phase_names = "\n".join(f"  {i}: {p.get('en', p) if isinstance(p, dict) else p}" for i, p in enumerate(phases))
        blocks.append(
            f"<solution id=\"{sol_id}\">\n  Name: {cat['name']}\n  Phases:\n{phase_names}\n"
            f"  Recent events:\n{event_lines}\n</solution>"
        )
    if not blocks:
        return None
    
    prompt = PROMPTS["phases"]["user"].format(SOLUTIONS_TEXT="\n\n".join(blocks))
    result = _llm_chat([
        {"role": "system", "content": PROMPTS["phases"]["system"]},
        {"role": "user", "content": prompt}
    ], max_tokens=4000, timeout=180)
    
    if result and "phases" in result:
        return result["phases"]
    return None


def research_category(cat, articles):
    sample = [a["title"] for a in articles[:15]]
    articles_text = "\n".join(f"  - {t}" for t in sample)
    
    prompt = PROMPTS["research"]["user"].format(
        CATEGORY_ID=cat['id'], CATEGORY_NAME=cat['name'],
        CATEGORY_DESCRIPTION=cat.get('description', 'N/A'),
        CATEGORY_PHASES=', '.join(cat.get('phases', [])),
        CATEGORY_KEYWORDS=', '.join(cat.get('keywords', [])),
        ARTICLES_TEXT=articles_text
    )
    return _llm_chat([
        {"role": "system", "content": PROMPTS["research"]["system"]},
        {"role": "user", "content": prompt}
    ], max_tokens=2000, timeout=180)


def research_all_categories(articles, cat_map):
    results = []
    cat_ids = list(cat_map.keys())
    print(f"  Researching {len(cat_ids)} categories...")
    for i, cid in enumerate(cat_ids):
        cat = cat_map[cid]
        print(f"  [{i+1}/{len(cat_ids)}] Researching: {cat['name']}...")
        researched = research_category(cat, articles)
        if researched:
            results.append({
                "id": cid, "icon": cat.get("icon", "📌"), "name": cat["name"],
                "description": researched["description"],
                "phases": researched["phases"], "keywords": researched["keywords"],
                "core": cat.get("core", False),
            })
        else:
            results.append(cat.copy())
    return results


# ═══════════════════════════════════════════════════════════════════════
# Build Output Data (with narrative pipeline)
# ═══════════════════════════════════════════════════════════════════════

def build_output(clustered_events, cat_map, narratives, ai_phases=None, stakeholders=None):
    """Build final JSON with narrative structure.
    
    clustered_events: dict {solution_id: [clustered_event, ...]}
    narratives: dict {solution_id: narrative_obj}
    """
    now = datetime.now(timezone.utc)
    
    solutions = []
    counts = {"advancing": 0, "stable": 0, "stalling": 0}
    active_solutions = []
    
    for sol_id in cat_map:
        events = clustered_events.get(sol_id, [])
        if not events:
            continue
        active_solutions.append(sol_id)
        
        direction = compute_direction(events)
        phase_index = ai_phases.get(sol_id, 0) if ai_phases else 0
        counts[direction] += 1
        
        cat = cat_map.get(sol_id)
        if not cat:
            continue
        
        # Build events list from clustered events
        events_list = []
        for ev in events:
            events_list.append({
                "date": ev["date"],
                "text": ev.get("text", ev["title"]),
                "sentiment": ev.get("sentiment", "neutral"),
                "source": ev["source"],
                "link": ev["link"],
                "snippet": ev.get("snippet", ""),
                "ai_risk": ev.get("risk", 5),
                "type": ev.get("type", "reporting"),
                "signal_score": ev.get("signal_score", 5),
                "source_weight": ev.get("source_weight", 2),
                "effective_signal": ev.get("effective_signal", 0),
                "attestations": ev.get("attestations", []),
                "cross_attestation_bonus": ev.get("cross_attestation_bonus", False),
                "cluster_size": ev.get("cluster_size", 1),
            })
        
        # Narrative
        narrative = narratives.get(sol_id)
        if not narrative:
            narrative = _fallback_narrative(cat, events, "")
        
        # Summary = most significant event title
        summary = events[0]["title"] if events else ""
        
        solutions.append({
            "id": sol_id,
            "icon": cat.get("icon", "📌"),
            "name": cat["name"],
            "phases": cat.get("phases", ["Emerged", "Developing", "Maturing", "Resolved"]),
            "phaseIndex": min(phase_index, len(cat.get("phases", [])) - 1),
            "direction": direction,
            "keyMetric": {"label": _make_trilingual("Events"), "value": str(len(events))},
            "summary": summary,
            "events": events_list[1:],  # exclude summary event
            "narrative": narrative,
            "confidence": "high" if len(events) > 5 else "medium" if len(events) > 2 else "low",
            "core": cat.get("core", False),
            "stakeholders": stakeholders.get(sol_id, []) if stakeholders else [],
        })

    # Translate solution names and recent event texts to Hebrew & Arabic
    # Pass events grouped by solution so only recent ones are translated
    events_by_solution = {s["id"]: s.get("events", []) for s in solutions}
    sol_names = [s["name"] for s in solutions]
    _translate_events_to_trilingual(events_by_solution, sol_names)
    for i, s in enumerate(solutions):
        s["name"] = sol_names[i]

    # Sort by effective_signal total, keep top 8
    def signal_total(s):
        return sum(e.get("effective_signal", 0) for e in s.get("events", []))
    solutions.sort(key=signal_total, reverse=True)
    solutions = solutions[:8]
    
    active_ids = set(s["id"] for s in solutions)
    active_solutions = [sid for sid in active_solutions if sid in active_ids]
    
    # Overall momentum
    if counts["advancing"] > counts["stalling"]:
        m_dir, m_label = "advancing", "Net Positive"
    elif counts["stalling"] > counts["advancing"]:
        m_dir, m_label = "stalling", "Net Negative"
    else:
        m_dir, m_label = "stable", "Mixed Signals"
    
    en_summary = f"{counts['advancing']} advancing, {counts['stable']} stable, {counts['stalling']} stalling ({len(active_solutions)} active)."
    he_summary = f"{counts['advancing']} מתקדם, {counts['stable']} יציב, {counts['stalling']} מתעכב ({len(active_solutions)} פעיל)."
    ar_summary = f"{counts['advancing']} متقدم، {counts['stable']} مستقر، {counts['stalling']} متوقف ({len(active_solutions)} نشط)."
    return {
        "solutions": solutions,
        "activeSolutions": active_solutions,
        "overallMomentum": {
            "direction": m_dir,
            "label": m_label,
            "summary": {"en": en_summary, "he": he_summary, "ar": ar_summary},
        },
        "lastUpdated": now.isoformat(),
        "source": "ai-analyzer-prod",
        "aiVersion": SCRIPT_VERSION,
    }


# ═══════════════════════════════════════════════════════════════════════
# Upload to Cloudflare KV
# ═══════════════════════════════════════════════════════════════════════

def upload_to_cloudflare(data):
    """Push data.json to Cloudflare KV via REST API (no Node.js needed).
    Uses PUT /accounts/:id/storage/kv/namespaces/:id/values/:key
    """
    if not CLOUDFLARE_TOKEN or not CLOUDFLARE_ACCOUNT:
        print("\n⚠ CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set")
        return False
    import urllib.request, json
    kv_id = "badf4fb7acfe4d1c905db77ed8d5e70f"
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT}/storage/kv/namespaces/{kv_id}/values/data.json"
    with open(DATA_JSON_FILE, "rb") as f:
        content = f.read()
    req = urllib.request.Request(url, data=content, method="PUT")
    req.add_header("Authorization", f"Bearer {CLOUDFLARE_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = json.loads(resp.read())
            if resp_body.get("success"):
                print("  ✓ data.json uploaded to KV")
                return True
            else:
                errs = resp_body.get("errors", [])
                print(f"  ⚠ KV upload failed: {errs}")
                return False
    except Exception as e:
        print(f"  ⚠ KV upload failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
# Merge with Existing Data
# ═══════════════════════════════════════════════════════════════════════

def _merge_with_existing(data, existing, ai_phases=None, narratives=None, stakeholders=None):
    """Merge new events into existing solutions, preserving narrative.longTerm."""
    
    # Merge events
    for sol in existing.get("solutions", []):
        sol_id = sol["id"]
        # Extract plain text for comparison (text may be {en,he,ar} dict or string)
        def _text_key(e):
            t = e.get("text", "")
            return t["en"] if isinstance(t, dict) else t
        existing_texts = {_text_key(e) for e in sol.get("events", [])}

        for new_sol in data["solutions"]:
            if new_sol["id"] == sol_id:
                for ev in new_sol.get("events", []):
                    key = _text_key(ev)
                    if key not in existing_texts:
                        sol["events"].append(ev)
                        existing_texts.add(key)
    
    # Add new solutions
    existing_ids = {s["id"] for s in existing["solutions"]}
    for new_sol in data["solutions"]:
        if new_sol["id"] not in existing_ids:
            existing["solutions"].append(new_sol)
    
    # Update phases, directions, narratives
    for sol in existing["solutions"]:
        sol["events"].sort(key=lambda e: e.get("date", ""), reverse=True)
        
        if ai_phases and sol["id"] in ai_phases:
            sol["phaseIndex"] = min(ai_phases[sol["id"]], len(sol["phases"]) - 1)
        
        sol["direction"] = compute_direction(sol["events"])
        sol["keyMetric"] = {"label": _make_trilingual("Events"), "value": str(len(sol["events"]))}
        # summary: extract English text if trilingual dict
        if sol["events"]:
            _sev = sol["events"][0]
            _st = _sev.get("text", "")
            sol["summary"] = _st["en"] if isinstance(_st, dict) else _st
            sol["events"] = sol["events"][1:]  # exclude summary
        
        # Preserve narrative from existing data (fast mode skips narrative generation)
        existing_narrative = sol.get("narrative")
        if narratives and sol["id"] in narratives:
            existing_lt = sol.get("narrative", {}).get("longTerm")
            new_narrative = narratives[sol["id"]]
            # In fast mode, preserve existing longTerm
            if existing_lt:
                new_narrative["longTerm"] = existing_lt
            sol["narrative"] = new_narrative
        elif existing_narrative:
            # Fast mode: keep existing narrative intact
            sol["narrative"] = existing_narrative
        
        sol["confidence"] = "high" if len(sol["events"]) >= 5 else "medium" if len(sol["events"]) >= 3 else "low"
        if stakeholders and sol["id"] in stakeholders:
            sol["stakeholders"] = stakeholders[sol["id"]]
    
    # Recompute momentum
    all_solutions = existing["solutions"]
    active_ids = [s["id"] for s in all_solutions if s.get("events")]
    existing["activeSolutions"] = active_ids
    
    counts = {"advancing": 0, "stable": 0, "stalling": 0}
    for s in all_solutions:
        counts[s["direction"]] += 1
    
    all_solutions.sort(key=lambda s: len(s.get("events", [])), reverse=True)
    existing["solutions"] = all_solutions[:8]
    existing["activeSolutions"] = [s["id"] for s in existing["solutions"]]
    
    if counts["advancing"] > counts["stalling"]:
        m_dir, m_label = "advancing", "Net Positive"
    elif counts["stalling"] > counts["advancing"]:
        m_dir, m_label = "stalling", "Net Negative"
    else:
        m_dir, m_label = "stable", "Mixed Signals"
    
    en_s = f"{counts['advancing']} advancing, {counts['stable']} stable, {counts['stalling']} stalling."
    he_s = f"{counts['advancing']} מתקדם, {counts['stable']} יציב, {counts['stalling']} מתעכב."
    ar_s = f"{counts['advancing']} متقدم، {counts['stable']} مستقر، {counts['stalling']} متوقف."
    existing["overallMomentum"] = {
        "direction": m_dir, "label": m_label,
        "summary": {"en": en_s, "he": he_s, "ar": ar_s},
    }
    existing["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    existing["source"] = "ai-analyzer-prod"
    existing["aiVersion"] = SCRIPT_VERSION
    return existing


def _save_stage(name, data):
    """Save an intermediate pipeline stage to staging/ for debugging."""
    path = os.path.join(STAGING_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  💾 Staged → {os.path.relpath(path)}")


def _load_existing_data():
    if not os.path.exists(DATA_FILE) and not os.path.exists(DATA_JSON_FILE):
        return None
    # Prefer data.json (has narratives) over solutions.json (events-only)
    target = DATA_JSON_FILE if os.path.exists(DATA_JSON_FILE) else DATA_FILE
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _print_summary(data, articles_count, elapsed):
    print(f"\n✓ Done in {elapsed:.1f}s")
    print(f"  {articles_count} articles → {len(data['solutions'])} solutions")
    print(f"  Momentum: {data['overallMomentum']['label']}")
    for sol in data["solutions"]:
        d = "🟢" if sol["direction"] == "advancing" else "🟤" if sol["direction"] == "stalling" else "🟡"
        phase_raw = sol["phases"][sol["phaseIndex"]]
        phase = phase_raw.get("en", str(phase_raw)) if isinstance(phase_raw, dict) else phase_raw
        has_narrative = "📖" if sol.get("narrative") else ""
        name = sol['name'].get('en', str(sol['name'])) if isinstance(sol['name'], dict) else sol['name']
        print(f"  {sol['icon']} {name:35s} {sol['direction']:10s} {d} {sol['keyMetric']['value']} events → {phase} {has_narrative}")


# Global reference for fallback classifier
all_kws = {}
source_profiles = {}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Peace Paths AI Analyzer (Narrative Pipeline)")
    parser.add_argument("--fast", action="store_true", help="Hourly fast run")
    parser.add_argument("--daily", action="store_true", help="Daily full run")
    parser.add_argument("--narrative", action="store_true", help="Force narrative rewrite")
    parser.add_argument("--categories", type=str, nargs="*", help="Inject custom categories")
    parser.add_argument("--skip-upload", action="store_true", help="Skip Cloudflare deploy")
    parser.add_argument("--dry-run", action="store_true", help="Print output JSON")
    parser.add_argument("--fetch-only", action="store_true", help="Only fetch RSS, skip AI")
    parser.add_argument("--review-taxonomy", action="store_true", help="Propose taxonomy")
    parser.add_argument("--research-categories", action="store_true", help="Research categories")
    parser.add_argument("--apply-research", action="store_true", help="Apply research to categories.json")
    args = parser.parse_args()
    
    global all_kws, source_profiles
    
    cat_map, all_ids, core_ids, all_kws = load_categories()
    print(f"\U0001f4c5 Loaded {len(all_ids)} categories ({len(core_ids)} core)")
    
    stakeholders = load_stakeholders()
    if stakeholders:
        print(f"\U0001f464 Loaded {len(stakeholders)} stakeholder groups")
    
    source_profiles = load_source_profiles()
    if source_profiles:
        print(f"\U0001f4c4 Loaded {len(source_profiles)} source profiles")
    
    global PROMPTS
    PROMPTS = load_prompts()
    
    # ── Research mode ──
    if args.research_categories:
        print("\n🔬 Researching categories...")
        articles = fetch_all_feeds(age_hours=None)
        if not articles:
            return
        researched = research_all_categories(articles, cat_map)
        
        print("\n--- RESEARCH RESULTS ---")
        for r in researched:
            old = cat_map.get(r["id"], {})
            changed = old.get("description") != r["description"] or old.get("phases") != r["phases"]
            status = "🔄" if changed else "✓"
            print(f"\n  {status} {r['icon']} {r['name']}")
            if old.get("description") != r["description"]:
                print(f"     desc: {old.get('description', 'N/A')}")
                print(f"    →    {r['description']}")
            if old.get("phases") != r["phases"]:
                old_p = [p.get("en", p) if isinstance(p, dict) else p for p in old.get('phases', [])]
                new_p = [p.get("en", p) if isinstance(p, dict) else p for p in r.get('phases', [])]
                print(f"     phases: {', '.join(old_p)}")
                print(f"    →      {', '.join(new_p)}")
        
        if args.apply_research:
            with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
                json.dump(researched, f, indent=2, ensure_ascii=False)
            print(f"\n✓ Applied to {CATEGORIES_FILE}")
        else:
            preview = CATEGORIES_FILE.replace(".json", "-researched.json")
            with open(preview, "w", encoding="utf-8") as f:
                json.dump(researched, f, indent=2, ensure_ascii=False)
            print(f"\n✓ Preview saved to {preview}")
        return
    
    # Determine mode
    if args.fast:
        mode = "fast"
        age_hours = FAST_AGE_HOURS
    elif args.daily or (not args.fast):
        mode = "daily"
        age_hours = None
    else:
        mode = "fast"
        age_hours = FAST_AGE_HOURS
    
    print(f"\n{'🚀' if mode == 'daily' else '⚡'} Peace Paths AI Analyzer — {mode.upper()} mode\n")
    
    # Inject custom categories
    if args.categories:
        print("✨ Injecting custom categories:")
        for cat in args.categories:
            parts = cat.split(":", 2)
            if len(parts) >= 2:
                cat_id, name = parts[0], parts[1]
                desc = parts[2] if len(parts) == 3 else f"{name} news"
                inject_category(cat_map, cat_id, name, desc)
            all_ids = list(cat_map.keys())
            all_kws = {c["id"]: c.get("keywords", []) for c in cat_map.values() if c.get("keywords")}
    
    start = time.time()
    
    # Load existing data for context
    existing_data = _load_existing_data()
    solution_contexts = {}
    if existing_data:
        for sol in existing_data.get("solutions", []):
            events = sol.get("events", [])
            if events:
                phase_name_raw = sol["phases"][sol["phaseIndex"]] if sol.get("phaseIndex", 0) < len(sol.get("phases", [])) else "Unknown"
                phase_name = phase_name_raw.get("en", str(phase_name_raw)) if isinstance(phase_name_raw, dict) else phase_name_raw
                recent = []
                for e in events[:3]:
                    t = e.get("text", "")
                    recent.append(t["en"] if isinstance(t, dict) else t)
                solution_contexts[sol["id"]] = f"Phase: {phase_name}. Recent: {'; '.join(recent)}"
    
    # 1. Fetch RSS
    if age_hours is not None:
        print(f"  [fast] fetching last {age_hours}h")
    else:
        print(f"  [daily] fetching last {MAX_AGE_DAYS}d")
    articles = fetch_all_feeds(age_hours=age_hours)
    if not articles:
        print("No articles found.")
        return
    _save_stage("articles", articles)
    print(f"  → {len(articles)} articles fetched")
    
    # 2. Build classifier prompt
    system_prompt, valid_ids = _make_classifier_prompt(cat_map, solution_contexts=solution_contexts if not args.review_taxonomy else None)
    
    # Taxonomy review mode
    if args.review_taxonomy:
        core_cats = [c for c in cat_map.values() if c.get("core", False)]
        print(f"\n🔍 Proposing taxonomy from {len(articles)} articles...")
        taxonomy = propose_taxonomy(articles, core_cats=core_cats)
        if taxonomy and "categories" in taxonomy:
            with open(TAXONOMY_FILE, "w", encoding="utf-8") as f:
                json.dump(taxonomy, f, indent=2, ensure_ascii=False)
            print(f"\n✓ Taxonomy saved to {TAXONOMY_FILE}")
            for cat in taxonomy["categories"]:
                print(f"  {cat.get('icon', '📌')} {cat['id']:25s} → {cat['name']}")
        return
    
    # 3. AI Classification
    if args.fetch_only:
        classified_pairs = keyword_classify(articles, all_kws)
        ai_refusals = 0
    else:
        classified_pairs, ai_refusals = classify_articles(articles, system_prompt, valid_ids, source_profiles)
        if not classified_pairs:
            print("  ⚠ AI failed, using keyword fallback")
            classified_pairs = keyword_classify(articles, all_kws)
            ai_refusals = 0
    # Save classified pairs (serializable form)
    _save_stage("classified", [{"article": a, "classification": c} for a, c in classified_pairs])
    
    # 4. Event Clustering
    all_clustered, clustered_by_solution = cluster_events(classified_pairs, source_profiles=source_profiles)
    _save_stage("clustered", [{"solution": s, "events": len(e)} for s, e in clustered_by_solution.items()])
    
    # 5. AI Phase Determination (daily only)
    ai_phases = None
    if mode == "daily" and not args.fetch_only:
        solution_events_for_ai = {cid: [] for cid in cat_map}
        for article, classification in classified_pairs:
            sol = classification.get("solution", "regional")
            solution_events_for_ai.setdefault(sol, []).append({
                "date": article["date"], "text": article["title"],
                "sentiment": classification.get("sentiment", "neutral"),
            })
        print("\n🧠 Determining phases via AI...")
        ai_phases = determine_phases_ai(solution_events_for_ai, cat_map)
        if ai_phases:
            print(f"  ✓ Phases for {len(ai_phases)} solutions")
            _save_stage("phases", ai_phases)
    
    # 6. Narrative Generation (daily or --narrative flag)
    narratives = {}
    if mode == "daily" or args.narrative:
        narratives = generate_narratives(clustered_by_solution, cat_map, existing_data, force_narrative=args.narrative)
        if narratives:
            _save_stage("narratives", narratives)
    else:
        print("\n⏩ Skipping narrative generation (fast mode)")
    
    # 7. Build output
    data = build_output(clustered_by_solution, cat_map, narratives, ai_phases, stakeholders)
    
    # 8. Shift Detection (daily only)
    if mode == "daily" and existing_data:
        print("\n🔍 Detecting shifts...")
        shifts = detect_shifts(data["solutions"], existing_data.get("solutions", []))
        if shifts:
            print(f"  ✓ {len(shifts)} shifts detected")
            # Append shifts to relevant solutions
            for shift in shifts:
                for sol in data["solutions"]:
                    if sol["id"] == shift["solutionId"]:
                        sol.setdefault("narrative", {})
                        sol["narrative"].setdefault("shifts", []).append(shift)
                        break
    
    # 9. Merge or overwrite
    if mode == "fast" and existing_data:
        print(f"→ Merging {len(classified_pairs)} events into existing data")
        data = _merge_with_existing(data, existing_data, ai_phases=ai_phases, narratives=narratives, stakeholders=stakeholders)

    # 9.5 Re-translate stale translations in daily mode
    # (build_output already translated recent events; this catches stale ones)
    if mode == "daily":
        events_by_sol = {s["id"]: s.get("events", []) for s in data["solutions"]}
        sol_names = [s["name"] for s in data["solutions"]]
        _translate_events_to_trilingual(events_by_sol, sol_names)
        for i, s in enumerate(data["solutions"]):
            s["name"] = sol_names[i]

    # AI health metadata
    data["aiHealth"] = {
        "refusals": ai_refusals,
        "totalClassified": len(classified_pairs),
        "refusalRate": round(ai_refusals / max(len(articles), 1) * 100, 1),
        "lastRun": datetime.now(timezone.utc).isoformat(),
        "classificationMethod": "ai" if not args.fetch_only else "keyword-fallback",
        "status": "healthy" if ai_refusals == 0 else "warning" if ai_refusals / max(len(articles), 1) < 0.05 else "degraded",
    }
    
    # Save final output to staging for inspection
    _save_stage("output", data)
    
    # Dry run
    if args.dry_run:
        print("\n--- data.json ---")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    
    # Write local files
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    with open(DATA_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Written to {DATA_FILE} and {DATA_JSON_FILE}")
    
    # Upload to KV
    if not args.skip_upload:
        print(f"\n🚀 Uploading data.json to Cloudflare KV...")
        upload_to_cloudflare(data)
    else:
        print(f"\nℹ Upload skipped.")
    
    elapsed = time.time() - start
    _print_summary(data, len(classified_pairs), elapsed)


if __name__ == "__main__":
    main()
