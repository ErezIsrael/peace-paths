# ☮️ Peace Paths

AI-powered tracker of concrete peace initiatives across the Middle East.

[Live](https://peace-paths.pages.dev) · [Source](https://github.com/ErezIsrael/peace-paths)

---

## Architecture

```
75 RSS Feeds → ai-analyze-prod.py (two-pass: keyword + batch LLM) → app/data.json → Cloudflare KV
                                                        ↓
                                              Pages Function → /data.json
                                                        ↓
                                              Frontend (GitHub → Pages)
```

- **AI Pipeline** (`ai-analyze-prod.py`): Fetches 75 RSS feeds → keyword filter + batch LLM classifies articles → groups by solution → generates narratives, phases, momentum → writes `app/data.json` → uploads to Cloudflare KV. All text is trilingual (`{en, he, ar}`).
- **Pages Function** (`functions/data.json.js`): Serves `data.json` from KV at `/data.json`.
- **Frontend** (`app/`): Static HTML/JS/CSS. Default language is Hebrew (RTL). Deployed via GitHub → Cloudflare Pages auto-deploy.
- **Admin Panel** (`admin/`): Local-only UI to manage categories, run analysis, and deploy data.

---

## Environments

| Environment | Port | Data Source | Purpose |
|-------------|------|-------------|---------|
| **dev-environment** | 8769 | `dev-environment/app/data.json` | Active development. |
| **test** | 8766 | `app/data.json` (AI output) | Verify rendering before committing. |

**Data flow:** AI writes `app/data.json` → test server reads it → verified → commit `app/` to Git → push → Cloudflare auto-deploy. Data uploaded to KV separately.

---

## Development Workflow

1. **Develop on `dev-environment/`** — Implement features, fix bugs. Serve on port 8769.
2. **Test on `test/`** — Copy verified frontend to `test/app/`, serve on port 8766 against real AI data from `app/data.json`. Confirm rendering, RTL, translations.
3. **Push to `app/` (live)** — Only when explicitly told by the user. Commit `app/` to Git → push to GitHub → Cloudflare auto-deploy.

**Rules:**
- **NEVER push to Git or deploy to production unless the user explicitly tells you to.**
- **Default to `dev-environment/`** — do not proceed to `test/` or `app/` until all bugs are fixed.
- Changes flow: `dev-environment/` → `test/` → `app/`.

---

## ⚠️ Deployment Rules

| Component | Deployment |
|-----------|------------|
| Frontend (`app/`) | **Commit to Git → push to GitHub → Cloudflare auto-deploy** (~2 sec) |
| Data (`app/data.json`) | Upload to KV via `wrangler kv key put` |

- **NEVER commit `app/data.json`** to Git — served from KV.
- **NEVER deploy frontend via `npx wrangler pages deploy`** — use GitHub push only. This is MANDATORY.
- `npx wrangler` is for **KV data and Workers only** — never for frontend deployment.

---

## Project Structure

```
peace-paths/
├── ai-analyze-prod.py      # Production pipeline (two-pass, trilingual, narratives)
├── categories.json         # AI solution categories (gitignored)
├── rss-feeds.json          # 75 feed URLs (gitignored)
├── prompts.json            # AI prompts (committed)
├── source-profiles.json    # Source bias mappings (committed)
├── .env                    # Secrets (gitignored)
├── app/                    # LIVE frontend (committed → GitHub → Pages)
│   ├── index.html          # lang="he" dir="rtl"
│   ├── app.js              # Frontend logic (default: Hebrew)
│   ├── styles.css          # Alternating card colors, narrative layout
│   ├── translations.json   # Trilingual UI strings
│   └── data.json           # Generated (gitignored, uploaded to KV)
├── test/                   # TEST — mirrors app/
│   ├── app/                # Copy of app/ for local verification
│   └── dev-serve.py        # Serves on :8766, proxies /data.json from app/
├── dev-environment/        # DEV — active development
│   ├── app/                # Experimental frontend
│   ├── admin/              # Admin panel
│   ├── ai-analyze-prod.py  # Dev pipeline
│   ├── dev-serve.py        # Serves on :8769
│   ├── prompts.json        # AI prompts
│   └── source-profiles.json
├── functions/
│   └── data.json.js        # Pages Function — serves data.json from KV
├── admin/                  # Admin panel (local only, committed)
└── staging/                # Intermediate pipeline outputs
```

---

## Key Details

| Item | Value |
|------|-------|
| RSS feeds | 75 sources in `rss-feeds.json` (gitignored) |
| Classification | Two-pass hybrid: keyword filter + batch LLM (size 10) |
| LLM | llama.cpp at local network — set via `LLAMA_CPP_URL` env var |
| AI model | Configurable via `AI_MODEL` env var (default: `Qwen3.6-27B`) |
| Translation | LLM-based with domain glossary (`_batch_translate_dual`) |
| Categories | Defined in `categories.json` (gitignored) |
| KV namespace | `peace-data` (`badf4fb7acfe4d1c905db77ed8d5e70f`) |
| Default language | Hebrew (RTL) |
| Env vars | `LLAMA_CPP_URL`, `AI_MODEL`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |

---

## Local Development

```bash
# DEV — active development (port 8769)
cd dev-environment && python dev-serve.py

# TEST — verify against real AI data (port 8766)
cd test && python dev-serve.py
```

---

## Running the AI Pipeline

```bash
# Daily — full 7-day window, overwrite data.json, upload to KV
python ai-analyze-prod.py --daily

# Fast — last 2h, merge into existing data, upload to KV
python ai-analyze-prod.py --fast

# Skip KV upload (write local file only)
python ai-analyze-prod.py --fast --skip-upload

# Keyword fallback (skip AI inference)
python ai-analyze-prod.py --fast --fetch-only
```

Deploy data to KV:
```bash
npx wrangler kv key put "data.json" --namespace-id=badf4fb7acfe4d1c905db77ed8d5e70f --path="app/data.json" --remote
```

---

## Automated Updates

| Task | Schedule | Script |
|------|----------|--------|
| `PeacePaths-FastUpdate` | Every hour | `auto-fast-update.bat` (`--fast`) |
| `PeacePaths-DailyUpdate` | Daily at 2 AM | `auto-daily-update.bat` (`--daily`) |

Registered via `setup-tasks.ps1` (Windows Task Scheduler). Both run from project root.

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

1. **No data on page?** Upload `data.json` to KV.
2. **AI failing?** Verify `LLAMA_CPP_URL` in `.env` → reachable llama.cpp server.
3. **Wrong translations?** Check `_batch_translate_dual` glossary in pipeline.
4. **Wrong categories?** Edit `categories.json` directly or use `/admin/`.
5. **Missing feeds?** Copy `rss-feeds.example.json` → `rss-feeds.json`.
6. **Frontend broken?** Commit + push to GitHub → auto-deploys.
