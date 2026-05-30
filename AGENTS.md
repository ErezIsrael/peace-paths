# ☮️ Peace Paths

AI-powered tracker of concrete peace initiatives across the Middle East.

[Live](https://peace-paths.pages.dev) · [Source](https://github.com/ErezIsrael/peace-paths)

---

## Architecture

```
29 RSS Feeds → ai-analyze-prod.py → llama.cpp (AI) → app/data.json → Cloudflare KV
                                                        ↓
                                              Pages Function → /data.json
                                                        ↓
                                              Frontend (GitHub → Pages)
```

- **AI Pipeline** (`ai-analyze-prod.py`): Fetches 29 RSS feeds → LLM classifies articles → groups by category → computes phases, momentum → writes `app/data.json` → uploads to Cloudflare KV.
- **Pages Function** (`functions/data.json.js`): Serves `data.json` from KV at `/data.json`.
- **Frontend** (`app/`): Static HTML/JS/CSS deployed via GitHub → Cloudflare Pages auto-deploy.
- **Admin Panel** (`admin/`): Local-only UI to manage categories in `categories.json`.

---

## ⚠️ Deployment Rules

| Component | Storage | Deployment |
|-----------|---------|------------|
| Frontend (`app/`) | **GitHub repo** | Commit to Git → GitHub → Cloudflare auto-deploy |
| Data (`app/data.json`) | **NOT in Git** | Written locally → uploaded to KV via `wrangler kv key put` |

**NEVER** commit `app/data.json` to Git. It is served via a Pages Function reading from KV.

---

## Project Structure

```
peace-paths/
├── ai-analyze-prod.py    # RSS → AI → app/data.json → KV upload
├── ai-analyze.py         # Dev/test — per-solution meta-analysis
├── dev-serve.py          # Local dev server (:8765) + admin (:8766)
├── categories.json       # AI categories — gitignored
├── rss-feeds.json        # 29 feed URLs — gitignored
├── .env                  # Secrets — gitignored
├── wrangler.toml         # KV binding + Pages config
├── app/                  # Frontend (committed to Git)
│   ├── index.html        # Page template
│   ├── app.js            # Frontend logic, card rendering, auto-refresh
│   ├── styles.css        # Styling
│   ├── _headers          # CSP, HSTS
│   ├── _routes.json      # Routes /data.json → Pages Function
│   ├── fonts/            # Self-hosted fonts
│   ├── solutions.json    # Generated data — gitignored
│   └── data.json         # Generated data — gitignored, uploaded to KV
├── functions/
│   └── data.json.js      # Pages Function — serves data.json from KV
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
| KV namespace | `peace-data` (`badf4fb7acfe4d1c905db77ed8d5e70f`) — binding `peace_data` |
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
