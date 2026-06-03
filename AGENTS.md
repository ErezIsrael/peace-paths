# ☮️ Peace Paths

AI-powered tracker of concrete peace initiatives across the Middle East.

[Live](https://peace-paths.pages.dev) · [Source](https://github.com/ErezIsrael/peace-paths)

---

## Architecture

```
75 RSS Feeds → ai-analyze-prod.py → llama.cpp (AI) → app/data.json → Cloudflare KV
                                                        ↓
                                              Pages Function → /data.json
                                                        ↓
                                              Frontend (GitHub → Pages)
```

- **AI Pipeline** (`ai-analyze-prod.py`): Fetches 60 RSS feeds → LLM classifies articles → groups by category → computes phases, momentum → writes `app/data.json` → uploads to Cloudflare KV.
- **Pages Function** (`functions/data.json.js`): Serves `data.json` from KV at `/data.json`.
- **Frontend** (`app/`): Static HTML/JS/CSS deployed via GitHub → Cloudflare Pages auto-deploy.
- **Admin Panel** (`admin/`): Local-only UI to manage categories in `categories.json`.

---

## Three Environments

| Environment | Port | Data Source | Frontend | Purpose |
|-------------|------|-------------|----------|---------|
| **dev-environment** | 8768 | `dev-environment/app/data.json` | `dev-environment/app/` | Major refactoring. Manual updates only. |
| **test** | 8766 | `app/data.json` (AI output) | `test/app/` (copy of `app/`) | Verify rendering with real AI data before committing. |
| **live** | — | Cloudflare KV | `app/` (Git → Pages) | Production site at peace-paths.pages.dev |

**Data flow:** AI writes `app/data.json` → test server reads it → verified → commit `app/` to Git → push → Cloudflare auto-deploy. Data uploaded to KV separately.

**Rule:** Changes to `app/` (frontend) are committed to Git. Changes to `dev-environment/` are NOT committed until merged into `app/`. The `test/` folder mirrors `app/` for local verification.

---

## Development Workflow

**This is the correct order — never skip steps:**

1. **Develop on `dev-environment/`** — Implement new features, refactor, fix bugs. Serve on port 8768.
2. **Test on `test/`** — Copy verified frontend to `test/app/`, serve on port 8766 against real AI data from `app/data.json`. Confirm rendering, RTL, translations.
3. **Push to `app/` (live)** — Only when explicitly told by the user. Commit `app/` to Git → push to GitHub → Cloudflare auto-deploy.

**Rules:**
- **NEVER push to Git or deploy to production unless the user explicitly tells you to.**
- When the user says "develop" or gives a feature request, **ask which environment** to work on (dev, test, or live). Change only that environment's files.
- **Default to `dev-environment/`** — we develop there first and do not proceed to `test/` or `app/` until all bugs are fixed.
- Never edit `app/` (production) directly during development. Changes flow: `dev-environment/` → `test/` → `app/`.

---

## ⚠️ Deployment Rules

| Component | Storage | Deployment |
|-----------|---------|------------|
| Frontend (`app/`) | **GitHub repo** | Commit to Git → GitHub → Cloudflare auto-deploy |
| Data (`app/data.json`) | **NOT in Git** | Written locally → uploaded to KV via `wrangler kv key put` |

**NEVER** commit `app/data.json` to Git. It is served via a Pages Function reading from KV.

**NEVER deploy the frontend via `npx wrangler pages deploy`.** Always commit + push to GitHub — Cloudflare auto-deploys in ~2 seconds. Use `npx wrangler` **only** for KV data (`kv key put`) and Workers that cannot go through GitHub.

---

## Project Structure

```
peace-paths/
├── ai-analyze-prod.py    # RSS → AI → app/data.json → KV upload
├── ai-analyze.py         # Dev/test — per-solution meta-analysis
├── dev-serve.py          # Test server (:8766) — reads app/data.json, serves app/
├── categories.json       # AI categories — gitignored
├── rss-feeds.json        # 60 feed URLs — gitignored
├── .env                  # Secrets — gitignored
├── wrangler.toml         # KV binding + Pages config
├── app/                  # LIVE — Frontend (committed to Git → Cloudflare Pages)
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   ├── _headers / _routes.json
│   ├── fonts/
│   ├── solutions.json    # Generated — gitignored
│   └── data.json         # Generated — gitignored, uploaded to KV
├── test/                 # TEST — mirrors app/, reads app/data.json
│   ├── app/              # Copy of app/ for local verification
│   └── dev-serve.py      # Serves test/app/ on :8766, proxies /data.json from app/
├── dev-environment/      # DEV — major refactoring (Narrative Pipeline v5)
│   ├── app/              # Experimental frontend with layered narrative
│   ├── admin/            # Admin panel with narrative editor
│   ├── ai-analyze-prod.py # Narrative pipeline implementation
│   ├── dev-serve.py      # Serves on :8765
│   ├── prompts.json      # Updated AI prompts
│   └── source-profiles.json # Source bias mappings
├── functions/
│   └── data.json.js      # Pages Function — serves data.json from KV
└── admin/                # Admin panel (local only, committed to Git)
    └── index.html
```

---

## Key Details

| Item | Value |
|------|-------|
| RSS feeds | 75 sources in `rss-feeds.json` (gitignored) |
| LLM | llama.cpp at local network — set via `LLAMA_CPP_URL` env var |
| AI model | Configurable via `AI_MODEL` env var (default: `Qwen3.6-27B`) |
| Categories | Defined in `categories.json` (gitignored). Skeleton: `categories.example.json` |
| KV namespace | `peace-data` (`badf4fb7acfe4d1c905db77ed8d5e70f`) — binding `peace_data` |
| Env vars | `LLAMA_CPP_URL`, `AI_MODEL`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |

---

## Local Development

```bash
# DEV environment — Narrative Pipeline v5 (port 8768)
cd dev-environment && python dev-serve.py
# → http://localhost:8768

# TEST environment — production frontend with real AI data (port 8766)
cd test && python dev-serve.py
# → http://localhost:8766

# Legacy server — serves app/ directly (port 8766)
python dev-serve.py
# → http://localhost:8766

# Admin panel (built into dev server)
# → http://localhost:8768/admin/
```

---

## Running the AI Pipeline

```bash
# Daily — full 7-day window, overwrite app/data.json, upload to KV
python ai-analyze-prod.py --daily

# Fast — last 2h, merge into existing data, upload to KV
python ai-analyze-prod.py --fast

# Skip KV upload (write local file only)
python ai-analyze-prod.py --fast --skip-upload

# Keyword fallback (skip AI inference)
python ai-analyze-prod.py --fast --fetch-only
```

Deploy data manually:
```bash
npx wrangler kv key put "data.json" --namespace-id=badf4fb7acfe4d1c905db77ed8d5e70f --path="app/data.json" --remote
```

---

## Env Setup

Copy `.env.example` → `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `LLAMA_CPP_URL` | llama.cpp server URL (`http://<IP>:8080`) |
| `AI_MODEL` | Model name |
| `CLOUDFLARE_API_TOKEN` | Token with KV + Pages permissions |
| `CLOUDFLARE_ACCOUNT_ID` | Numeric account ID |

---

## Debug Checklist

1. **No data on page?** Upload data.json to KV: `wrangler kv key put "data.json" --namespace-id=<ID> --path="app/data.json" --remote`
2. **AI failing?** Verify `LLAMA_CPP_URL` in `.env` → reachable llama.cpp server.
3. **Deploy fails?** Check `CLOUDFLARE_API_TOKEN` has KV edit permission.
4. **Wrong categories?** Edit `categories.json` directly or use `/admin/`.
5. **Missing feeds?** Copy `rss-feeds.example.json` → `rss-feeds.json`.
6. **Frontend broken?** Commit changes to Git, push to GitHub → auto-deploys.
