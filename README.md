# 🕊 Peace Room — AI-Powered Peace Progress Tracker

Tracks concrete peace initiatives across the Middle East using AI-classified RSS articles.

[Live](https://peace-meter.pages.dev/peace-room/) · [Parent: Peace Meter](../README.md)

---

## Architecture

```
29 RSS Feeds → ai-analyze-prod.py → llama.cpp (AI) → solutions.json → Cloudflare Pages
```

- **AI Pipeline** (`ai-analyze-prod.py`): Fetches 29 RSS feeds → single-article LLM inference → ME relevance filter → category classification → writes `solutions.json` → uploads to Cloudflare.
- **Frontend** (`index.html` / `app.js` / `styles.css`): Dynamic grid of solution cards with phase bars, momentum banner, and activity feed. Serves from `../app/peace-room/`.
- **Dev Server** (`dev-serve.py`): Syncs `solutions.json` → `data.json` and serves on `:8765`.

---

## Project Structure

```
peace-paths/
├── ai-analyze-prod.py   # Production pipeline — RSS → AI → solutions.json → Cloudflare
├── ai-analyze.py        # Dev/test — per-solution meta-analysis
├── dev-serve.py         # Local dev server (sync data + serve :8765) + admin panel (:8766)
├── serve.bat            # Quick Windows serve (http.server :8765)
├── taxonomy.json        # AI-proposed classification categories
├── ADMIN_PLAN.md        # Admin panel design doc
└── admin/               # Admin panel frontend
├── solutions.json       # AI output (mirrors ../app/peace-room/solutions.json)
├── app.js               # Frontend — solution cards, momentum, activity feed
├── index.html           # Page template
├── styles.css           # Styling
├── sonnet-edited.OPML   # RSS feed source list
├── test_*.py            # Performance / accuracy tests
└── __pycache__/
```

---

## Key Details

| Item | Value |
|------|-------|
| RSS feeds | 29 sources (ME news, Israel EN/HE, regional, UN, think tanks, OSINT) |
| LLM | llama.cpp at `192.168.2.121:8080` — single-article inference (1200-char extract) |
| Categories | 12: ceasefire · diplomacy · governance · infrastructure · iran · lebanon · gaza-crisis · human-rights · domestic-politics · west-bank · regional + dynamic |
| Phases | 5 per category (e.g., Active Fighting → Ceasefire Talks → Draft → Signed → Holding) |
| Direction | `advancing` / `stable` / `stalling` — derived from sentiment ratios |
| Output | `../app/peace-room/solutions.json` — deployed via Cloudflare Upload API |
| Env vars | `LLAMA_CPP_URL`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |

---

## Running the AI Pipeline

```bash
# Daily — full 7-day window, overwrite solutions.json
python ai-analyze-prod.py --daily

# Fast — last 2h, merge into existing data
python ai-analyze-prod.py --fast

# Skip upload (write local file only)
python ai-analyze-prod.py --daily --skip-upload

# Keyword fallback (skip AI, for testing)
python ai-analyze-prod.py --fetch-only

# Custom category injection
python ai-analyze-prod.py --fast --categories "armistice:Ceasefire Talks:Truce negotiations"
```

### Dynamic Taxonomy Workflow

```bash
# Phase 1: AI proposes categories from current articles
python ai-analyze-prod.py --review-taxonomy
# → saves taxonomy.json

# Phase 2: classify with approved taxonomy
python ai-analyze-prod.py --use-taxonomy taxonomy.json --daily
```

### Schedule

| Cron | Command |
|------|---------|
| `0 * * * *` | `python ai-analyze-prod.py --fast` |
| `0 6 * * *` | `python ai-analyze-prod.py --daily` |

---

## Local Development

```bash
# Dev server (syncs data + serves on :8765)
python dev-serve.py
# → http://localhost:8765

# Dev server with admin panel (no sync, port 8766)
python dev-serve.py --port 8766 --no-sync
# → http://localhost:8766
# → http://localhost:8766/admin/ (Admin Panel)

# Quick serve (Windows)
serve.bat
# → http://localhost:8765
```

Frontend loads `solutions.json` from `../app/peace-room/`. Edit `index.html`, `app.js`, or `styles.css` directly there.

### Admin Panel (`/admin/`)

The admin panel lets you manage categories (SOLUTIONS) in `ai-analyze-prod.py` without editing the file manually:

| Feature | Description |
|---------|-------------|
| **Review** | View all categories with icons, phases, and keywords |
| **Add / Edit** | Create new or modify existing categories via modal |
| **Delete** | Remove categories (singles or bulk via checkbox selection) |
| **Duplicate** | Clone a category with `-copy` suffix |
| **Copy** | Copy category JSON to clipboard |
| **Import from Taxonomy** | Import AI-proposed categories from `taxonomy.json` |
| **Taxonomy Diff** | Side-by-side comparison of SOLUTIONS vs taxonomy.json |
| **Analysis** | Trigger `--fast` or `--daily` AI pipeline runs |
| **Search** | Filter categories by ID, name, or description |
| **Keyboard Shortcuts** | `Ctrl+N` new, `Ctrl+S` save in modal, `Esc` close |

API endpoints: `/api/admin/categories`, `/api/admin/taxonomy`, `/api/admin/categories/bulk-import`, `/api/admin/analysis/run`, `/api/admin/analysis/status`

---

## Debug Checklist

1. **No data on page?** Check `solutions.json` exists in `../app/peace-room/`. Run `dev-serve.py` to sync.
2. **AI classification failing?** Verify `LLAMA_CPP_URL` points to running llama.cpp instance. Check LAN connectivity (WSL2 cannot reach Windows `localhost`).
3. **Upload fails?** Verify `CLOUDFLARE_API_TOKEN` has `pages_edit` permission.
4. **Wrong categories?** Run `--review-taxonomy`, edit `taxonomy.json`, re-run with `--use-taxonomy`.
5. **HTML entities in text?** `html.unescape()` handles `&#x27;` and similar.
6. **Stale categories?** Deleted categories are re-classified on deploy; added categories classify on next run.
