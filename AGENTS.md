# ☮️ Peace Paths

AI-powered tracker of concrete peace initiatives across the Middle East.

[Live](https://peace-paths.pages.dev) · [Source](https://github.com/ErezIsrael/peace-paths)

---

## Architecture

```
29 RSS Feeds → ai-analyze-prod.py → llama.cpp (AI) → solutions.json → Cloudflare Pages
```

- **AI Pipeline** (`ai-analyze-prod.py`): Fetches 29 RSS feeds → LLM classifies articles → groups by category → computes phases, momentum, confidence → writes `app/solutions.json` → deploys via `wrangler`.
- **Frontend** (`app/`): Static HTML/JS/CSS. Dynamic solution cards with phase bars, momentum banner, activity feed, and keyword-fallback warning.
- **Admin Panel** (`admin/`): Local-only UI to manage categories in `categories.json`.

---

## ⚠️ Deployment Rules

This site has **2 major components**:

| Component | Storage | Deployment |
|-----------|---------|------------|
| Pages (frontend: `app/`, `admin/`) | **GitHub repo** | Commit to Git → GitHub → Cloudflare auto-deploy |
| Data (`solutions.json`, `categories.json`) | **NOT in Git** | Generated locally → uploaded via `wrangler pages deploy` |

**NEVER** use `wrangler` to upload files tracked in Git. **NEVER** commit data files to Git.

---

## Project Structure

```
peace-paths/
├── ai-analyze-prod.py    # RSS → AI → solutions.json → wrangler deploy
├── ai-analyze.py         # Dev/test — per-solution meta-analysis
├── dev-serve.py          # Local dev server (:8765) + admin (:8766)
├── categories.json       # AI categories — gitignored, uploaded via wrangler
├── rss-feeds.json        # 29 feed URLs — gitignored
├── .env                  # Secrets — gitignored
├── app/                  # Frontend (committed to Git)
│   ├── index.html        # Page template
│   ├── app.js            # Frontend logic, card rendering, auto-refresh
│   ├── styles.css        # Styling
│   ├── _headers          # CSP, HSTS
│   ├── solutions.json    # Generated data — gitignored
│   └── data.json         # Synced copy for dev server
└── admin/                # Admin panel (local only, committed to Git)
    └── index.html
```

---

## Key Details

| Item | Value |
|------|-------|
| RSS feeds | 29 sources in `rss-feeds.json` (gitignored) |
| LLM | llama.cpp at local network — set via `LLAMA_CPP_URL` env var |
| AI model | Configurable via `AI_MODEL` env var (default: `Qwen3.6-27B`) |
| Categories | Defined in `categories.json` (gitignored). Skeleton: `categories.example.json` |
| Output | `app/solutions.json` — deployed via `wrangler pages deploy` |
| Env vars | `LLAMA_CPP_URL`, `AI_MODEL`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |

---

## Local Development

```bash
# Dev server — syncs solutions.json → data.json, serves on :8765
python dev-serve.py
# → http://localhost:8765

# Dev server + admin panel (port 8766, no sync)
python dev-serve.py --port 8766 --no-sync
# → http://localhost:8766/admin/
```

---

## Running the AI Pipeline

```bash
# Daily — full 7-day window, overwrite solutions.json, auto-deploy
python ai-analyze-prod.py --daily

# Fast — last 2h, merge into existing data, auto-deploy
python ai-analyze-prod.py --fast

# Skip deploy (write local file only)
python ai-analyze-prod.py --fast --skip-upload

# Keyword fallback (skip AI inference)
python ai-analyze-prod.py --fast --fetch-only
```

Deploy manually:
```bash
npx wrangler pages deploy app --project-name=peace-paths --skip-caching
```

---

## Env Setup

Copy `.env.example` → `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `LLAMA_CPP_URL` | llama.cpp server URL (`http://<IP>:8080`) |
| `AI_MODEL` | Model name |
| `CLOUDFLARE_API_TOKEN` | Token with `pages_edit` |
| `CLOUDFLARE_ACCOUNT_ID` | Numeric account ID |

---

## Debug Checklist

1. **No data on page?** Check `app/solutions.json` exists. Run `dev-serve.py` to sync.
2. **AI failing?** Verify `LLAMA_CPP_URL` in `.env` → reachable llama.cpp server.
3. **Deploy fails?** Check `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` in `.env`.
4. **Wrong categories?** Edit `categories.json` directly or use `/admin/`.
5. **Missing feeds?** Copy `rss-feeds.example.json` → `rss-feeds.json`.
6. **Frontend broken?** Commit changes to Git, push to GitHub → auto-deploys. Never `wrangler` deploy frontend files.
