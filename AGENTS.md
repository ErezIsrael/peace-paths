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

**Every change follows this mandatory pipeline — no exceptions:**

1. **Implement the fix/feature** — Edit the relevant files in `app/`.
2. **Bump version** — Increment the version string in `app/app.js` (e.g. `v0.5.0` → `v0.5.1`). Patch for bugfixes, minor for features.
3. **Commit & push to Git** — `git add`, `git commit` (descriptive message), `git push`. Cloudflare Pages auto-deploys in ~2 sec.
4. **Verify on live site** — Open `https://peace-paths.pages.dev/` in the browser (use mobile viewport for responsive issues). Confirm the change is visible and correct.
5. **Only mark the task as DONE after step 4 passes.**

**If verification fails:** Fix the issue, bump version again, commit, push, and re-verify. Do not declare the task complete.

**Rules:**
- **NEVER commit `app/data.json`** to Git — served from KV.
- **NEVER deploy frontend via `npx wrangler pages deploy`** — use GitHub push only.
- `npx wrangler` is for **KV data and Workers only** — never for frontend deployment.

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
├── categories.json         # AI solution categories (7) — committed
├── rss-feeds.json          # Active feed URLs (72) — committed
├── rss-feeds.retired.json  # Feeds removed for silence/errors, with reasons
├── prompts.json            # AI prompts (committed)
├── source-profiles.json    # Source bias mappings (committed)
├── .env                    # Secrets (gitignored)
├── app/                    # LIVE frontend (committed → GitHub → Pages)
│   ├── index.html          # lang="he" dir="rtl"
│   ├── app.js              # Frontend logic (default: Hebrew)
│   ├── styles.css          # Alternating card colors, narrative layout
│   ├── translations.json   # Trilingual UI strings
│   ├── fonts/              # Subset WOFF2 built by tools/build-fonts.py (committed)
│   ├── og.png              # Social preview card (tools/build-og.py)
│   ├── 404.html            # Real 404 (Pages SPA fallback otherwise serves index.html)
│   ├── _headers            # Asset caching rules
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
| RSS feeds | 72 active in `rss-feeds.json`; 17 retired in `rss-feeds.retired.json` |
| Classification | Two-pass hybrid: keyword filter + batch LLM (size 25) |
| LLM | llama.cpp at local network — set via `LLAMA_CPP_URL` env var |
| AI model | Configurable via `AI_MODEL` env var (default: `Qwen3.6-27B`) |
| Translation | LLM-based with domain glossary (`_batch_translate_dual`) |
| Categories | Defined in `categories.json` (committed) |
| KV namespace | `peace-data` (`badf4fb7acfe4d1c905db77ed8d5e70f`) |
| Default language | Hebrew (RTL) |
| Env vars | `LLAMA_CPP_URL`, `AI_MODEL`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |
| Published payload | KV holds the **publish view** of `app/data.json` (AI 2.2.0 `_publish_view()`): compact JSON, fields the UI never reads dropped, `recentEvents` (40 newest, tagged with `solutionId`) for the activity feed, and only 2 recent events per solution inline. ~115 KB raw / ~36 KB brotli. The local file stays the long-term memory (~450 KB) and is deliberately larger. |
| Event archives | Each solution's full event history is a separate KV key `events/<id>.json`, fetched on demand by the card's "📂 כל האירועים (N)" layer and served by `functions/events/[id].json.js` (id validated against `^[a-z0-9][a-z0-9-]{0,79}$`). The frontend only asks when `data.eventsFiles` is true, which the pipeline sets only after every file uploaded. |
| `/data.json` caching | Served by `functions/data.json.js` with KV `cacheTtl: 60` + cacheable response headers. `no-store` there would mean an origin KV read per view. (The CDN itself does not cache Pages Function responses on this plan.) |
| Fonts | `tools/build-fonts.py` subsets Heebo (HE/LT) and Noto Naskh Arabic into `app/fonts/*.woff2`; `styles.css` selects them by `unicode-range` so Arabic glyph data is only fetched in Arabic mode. The old `inter-variable.ttf` had **no Hebrew or Arabic glyphs** — Hebrew was falling back to a system font. Regenerate after changing the site's text; `--dry-run` prints codepoint coverage. |

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

## Automated Updates — ⚠️ RUNS ON AI SERVER, NOT THIS LAPTOP

**The update services run on the AI server, NOT on this laptop.**
This laptop is off most of the time — the site updates regardless.

### Where the updates run

| Machine | Role | SSH |
|---------|------|-----|
| **AI Server** (`<AI_SERVER_IP>`) | Runs llama.cpp + Peace Paths update timers | `ssh erez@<AI_SERVER_IP>` |
| This laptop | Development only — do NOT run updates here | N/A |

> **Do not hardcode the AI server's LAN IP in this repo** — find it in the server's `.env` (`LLAMA_CPP_URL`).

> **Do not hardcode the AI server LAN IP in this repo.** Find it in the server's  (see ).

### Server-side update services (user-level systemd timers)

The updates run as **user-level systemd timers** on the AI server (`erez@<AI_SERVER_IP>`):

| Timer | Schedule | Service | Timeout |
|-------|----------|---------|--------|
| `peace-paths-fast.timer` | Every 2h 07:00–21:00 + 01:00/05:00 at night (10/day, ±10min jitter) | `peace-paths-fast.service` | 15 min |
| `peace-paths-daily.timer` | Daily at 2 AM | `peace-paths-daily.service` | 4 hours |

**Unit files on server:** `/home/erez/.config/systemd/user/peace-paths-{fast,daily}.{timer,service}`
**Project on server:** `/home/erez/peace-paths/` (same codebase, synced via `scp`)

### Managing the services

```bash
# Check timer status
ssh erez@<AI_SERVER_IP> "systemctl --user list-timers --all | grep peace"

# Check service logs
ssh erez@<AI_SERVER_IP> "journalctl --user -u peace-paths-fast.service --since '1 day ago' --no-pager | tail -30"
ssh erez@<AI_SERVER_IP> "journalctl --user -u peace-paths-daily.service --since '1 day ago' --no-pager | tail -30"

# Check lock/status
ssh erez@<AI_SERVER_IP> "python3 ~/peace-paths/ai-analyze-prod.py --status"

# Restart timers after editing unit files
ssh erez@<AI_SERVER_IP> "systemctl --user daemon-reload && systemctl --user restart peace-paths-fast.timer peace-paths-daily.timer"

# Stop / Start updates
ssh erez@<AI_SERVER_IP> "~/peace-paths/stop-updates.sh"
ssh erez@<AI_SERVER_IP> "~/peace-paths/start-updates.sh"
```

### Deploying pipeline changes to the server — git, not scp

Both update services run `git pull --ff-only origin main` as `ExecStartPre` before the
pipeline starts, so **committing and pushing deploys** `ai-analyze-prod.py`, `prompts.json`,
`rss-feeds.json`, `categories.json`, etc. No `scp` step.

```bash
git add -A && git commit -m "..." && git push origin main      # live on next run
ssh erez@<AI_SERVER_IP> "cd ~/peace-paths && git pull --ff-only origin main"   # or take effect now
```

Rules that keep this working:
- **Never edit files on the server by hand** — the pull is `--ff-only` and a local edit will
  block it. The last known-good copy of the runtime state is in `~/peace-paths-presync/`
  (`app/data.json`, `categories.json`, `rss-feeds.json`).
- `app/data.json` on the server is the pipeline's **long-term memory** (milestones, weekly
  history, narratives). It is gitignored and must never be deleted or overwritten by a sync.
- To change the schedule/units: edit `~/.config/systemd/user/peace-paths-{fast,daily}.{service,timer}`
  then `systemctl --user daemon-reload`. Back up the unit first; they are not in git.

### Site invariants (measured, do not regress silently)

- **Payload:** the pipeline logs `publishing N KB + M/N event files (K KB) (local data.json is M KB)`. KV holds the
  slim publish view (~115 KB raw / ~36 KB brotli); the on-disk file stays the full memory.
  If you add a field to the publish view, the frontend must actually read it — otherwise
  drop it in `_slim_for_publish()`.
- **Event archives:** `events/<id>.json` in KV, written *before* `data.json` so the
  `eventsFiles` flag can be trusted. If you change the event shape, change both the archive
  files and the inline `recentEvents`/2-per-solution floor in the same commit — the frontend
  renders all three with the same row markup.
- **Caching:** `functions/data.json.js` reads KV with `cacheTtl: 60` (edge-cached value, so a
  page view is not an origin KV read) and must never return `no-store`. Measured caveat: the
  CDN does **not** cache Pages Function responses on this plan (probe-verified — the Function
  runs per request), so there is still 1 Function invocation per page view (free tier 100k/day).
- **Quiet tracks:** the grid renders `data.activeSolutions`; every solution in `data.solutions`
  with zero events and a narrative archive renders in the "quiet tracks" section (`renderQuietTracks`
  in `app/app.js`) as a collapsed disclosure whose panel reuses `buildLayeredCard`. So dormant
  entries in the publish view **are** read by the UI now — do not drop them from `_publish_view()`,
  and do not hide them from the UI again. `t('quietTracks')` / `noNewDevelopments` / `lastActivity`
  carry the labels in all three languages.
- **Fonts:** Hebrew/Arabic text requires `app/fonts/heebo-hebrew.woff2` (+ Arabic subset).
  Regenerate with `python tools/build-fonts.py`; check `--dry-run` coverage before changing
  the typeface. Never hand-add a TTF: an uncovered script silently falls back to system fonts.
- **Contrast & keyboard:** the dark and light themes measure 0 WCAG AA failures, and every
  collapsible is a real `<button aria-expanded aria-controls>`. Keep the contrast floor
  `--text-dim ≥ 5.9:1` on `--card` and don't reintroduce `href="#"` action links or
  `div onclick` toggles.
- **Fast-run cost:** a steady-state fast run is ~10 s, not ~130 s. It stays that way only if
  `.state/seen.json` and `.state/translation-cache.json` survive (see below). If the journal
  starts showing whole-window re-classification, those stores are gone or the keys changed.
- **Batch classification:** `_batch_classify()` appends its own output contract to the classifier
  system prompt (one `{"results": […]}` array, one object per article, same order). The shared
  single-article prompt plus `response_format: json_object` makes the model answer for the first
  article only, and the whole batch is then redone one call per article. Check the journal for
  `⚠ Batch JSON parse failed` after any prompt change.
- **Event resolution:** similarity is IDF-weighted over title+snippet inside a solution bucket,
  threshold 0.52, entity-gated, with a headline-subset rule. Judge any change with
  `python tools/check-clustering.py --fixture tests/fixtures/articles-2026-11-27.json` — it prints
  the old rule's merge groups next to the new rule's. 0.45 already merges different events that
  merely share a topic.
- **Keyword pass is advisory only:** a keyword match may attach a `<hint-category>` to an article,
  it may not assign sentiment/type/signal or skip the LLM. Every published judgement carries
  `classified_by` (`llm` | `keyword`) and `aiHealth.classifiedBy` shows the split — keep it honest.
- **`events[]` is the archive; `summary` is derived.** Never pop the newest event out of
  `events[]` to build `summary` — the frontend does not render `solution.summary`, and doing that
  deleted one event per solution per run (see AUDIT §12.1). Compare per-solution `eventCount`
  between two consecutive runs: only genuine new events may move.
- **A quiet window is not an AI failure.** The keyword fallback in `main()` requires
  `not classified_pairs and llm_answered == 0`. Triggering it on "nothing was relevant" re-inserts
  keyword verdicts and publishes `classificationMethod: "keyword-fallback"`, which turns on the
  warning banner in the live UI.

### Pipeline runtime state (`.state/`, gitignored, server-owned)

Never hand-edit or commit these; they are the pipeline's memory between runs, and the server's
 copies are the live ones.

| File | What it is | If it is lost |
|---|---|---|
| `.state/translation-cache.json` | hashed `(lang, text)` → translation, 8 000-entry cap | every event title gets re-translated; translation was ~80% of a fast run's wall clock |
| `.state/seen.json` | articles with a definitive outcome in the last 30 days (link minus tracking params + title + source) | the whole 6 h window is re-classified — slower, and LLM verdicts can flip between runs |

Articles whose classification was an AI refusal or a keyword fallback are deliberately **not**
marked seen, so they get retried next run. `FAST_AGE_HOURS = 6` overlaps consecutive fast runs
so a delayed or skipped run cannot lose news; do not shrink it back to 2 h now that the seen
store makes the overlap free.

### Lock mechanism

The pipeline uses a lock file (`.locks/ai-update.lock`) to prevent concurrent runs:
- **Fast waits for daily** — if daily is running, fast skips this run (waits up to 4 hours)
- **Daily always takes priority** — overwrites any existing lock
- **Stale lock detection** — if no heartbeat for 5 min, lock is considered stale and taken over
- **Heartbeat logging** — lock file is updated at each pipeline stage (fetching, classifying, clustering, narratives, translating, uploading)
- **Check status:** `python3 ai-analyze-prod.py --status`

### Old Windows tasks (DECOMMISSIONED)

The old Windows Task Scheduler tasks (`PeacePaths-FastUpdate`, `PeacePaths-DailyUpdate`) on this laptop are **no longer used**.
They were disabled because this laptop is off most of the time.
The `auto-fast-update.bat`, `auto-daily-update.bat`, and `setup-tasks.ps1` files are kept for reference only.

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
