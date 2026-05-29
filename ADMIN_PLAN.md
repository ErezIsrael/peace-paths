# Peace Paths Admin Panel — Plan

> Local-only admin page to manage the category taxonomy used by `ai-analyze-prod.py`.

## Goal

A single-page admin interface at `http://localhost:8766/admin/` that lets me:
1. **Review** current categories from `ai-analyze-prod.py` (SOLUTIONS dict)
2. **Add** new categories (id, icon, name, description, phases, keywords)
3. **Delete** categories
4. **Edit** existing categories
5. **Copy** from the old taxonomy.json list (AI-proposed categories not yet in SOLUTIONS)

## Architecture

The dev server (`dev-serve.py`) already provides:
- GET `/api/admin/categories` — returns parsed SOLUTIONS from the script
- POST `/api/admin/categories` — adds a category
- PUT `/api/admin/categories/:id` — updates a category
- DELETE `/api/admin/categories/:id` — removes a category
- GET `/api/analysis/status` — AI job status
- `POST /api/analysis/run?mode=fast` — triggers analysis

The admin HTML lives in `peace-paths/admin/index.html` and is served **only** by the local dev server (never deployed to Cloudflare).

## Current State

### SOLUTIONS in ai-analyze-prod.py (12 categories)
| ID | Icon | Name | Phases |
|----|------|------|--------|
| ceasefire | 🕊 | Ceasefire & De-escalation | Active Fighting → Holding |
| diplomacy | 🤝 | Diplomacy & Regional Deals | Isolated → Regional Peace |
| governance | 🏛 | Post-War Governance | No Framework → Sustainable |
| infrastructure | 🔧 | Infrastructure & Recovery | Destroyed → Full Recovery |
| iran | ☣️ | Iran Nuclear & War | War → Resolution |
| lebanon | 🇱🇧 | Lebanon & Hezbollah | Active Fighting → Stable |
| gaza-crisis | 🏚 | Gaza Humanitarian Crisis | Blockade → Stabilized |
| human-rights | ⚖️ | Human Rights & Intl Law | Allegations → Reform |
| domestic-politics | 🏛 | Israeli Domestic Politics | Fractured → Stability |
| west-bank | 🔥 | West Bank & Settlements | Escalation → Frozen Conflict |
| regional | 🌍 | Regional Relations | Tensions → Cooperation |

### AI-Proposed Taxonomy (taxonomy.json) — 9 categories
These are the "old list" from the AI taxonomy review. Some overlap with SOLUTIONS, some are new:

| ID | Name | Icon | In SOLUTIONS? |
|----|------|------|---------------|
| us-iran-conflict | US-Iran Conflict | 🇺🇸🇮🇷 | ❌ (partially → `iran`) |
| israel-lebanon-conflict | Israel-Lebanon Conflict | 🇮🇱🇱🇧 | ❌ (matches `lebanon`) |
| israel-domestic-politics | Israeli Domestic Politics | 🏛️ | ✅ (matches `domestic-politics`) |
| gaza-palestine | Gaza and Palestine | 🇵🇸 | ❌ (partially → `gaza-crisis`) |
| gulf-regional-dynamics | Gulf and Regional Dynamics | 🕌 | ❌ (partially → `regional`) |
| cyber-security | Cyber Security | 💻 | ❌ (NEW) |
| human-rights-activism | Human Rights and Activism | ✊ | ❌ (partially → `human-rights`) |
| religious-cultural | Religious and Cultural | 📿 | ❌ (NEW) |
| international-non-mideast | International Non-Middle East | 🌍 | ❌ (filter category) |

## Changes to admin/index.html

### 1. Copy-from-Taxonomy Feature
- Add a button: **"Import from taxonomy.json"**
- Fetches taxonomy.json via new API endpoint `GET /api/admin/taxonomy`
- Shows a modal listing AI-proposed categories with checkboxes
- User selects which to import; each gets default phases and empty keywords
- On confirm, POST each selected category to `/api/admin/categories`

### 2. Diff View (SOLUTIONS vs taxonomy.json)
- Add a tab/button to show side-by-side comparison
- Left column: current SOLUTIONS categories
- Right column: taxonomy.json AI categories
- Highlight overlaps and gaps

### 3. Copy Button per Row
- In the main table, each row gets a "Copy" button
- Copies category data to clipboard (JSON) for manual reuse
- Also add "Duplicate" action that clones a category with a new ID

### 4. Keyboard Shortcut
- `Ctrl+N` → New category
- `Ctrl+S` → Save (when modal is open)
- `Escape` → Close modal

### 5. Search/Filter
- Text filter input to filter categories by name or ID

### 6. Bulk Operations
- Select multiple categories → bulk delete
- Select multiple → bulk export as JSON

## API Additions Needed (dev-serve.py)

1. `GET /api/admin/taxonomy` — returns taxonomy.json categories
2. `POST /api/admin/categories/bulk-import` — bulk create from taxonomy list

## Implementation Order

1. ✅ Write this plan (ADMIN_PLAN.md)
2. ✅ Add `/api/admin/taxonomy` endpoint to dev-serve.py
3. ✅ Rewrite admin/index.html with all features above
4. ✅ Test locally: load page, add/edit/delete/copy, import from taxonomy
5. ✅ Verify script patches correctly

## Testing Checklist

- [x] Load admin page at `http://localhost:8766/admin/`
- [x] See all 11 SOLUTIONS categories in table
- [x] Add a new category → verify it appears in SOLUTIONS dict
- [x] Edit a category (change name/phases) → verify saved
- [x] Delete a category → verify removed from script
- [x] "Import from taxonomy" → select cyber-security → verify added
- [x] Duplicate a category → verify clone with new ID
- [ ] Run `--fast` analysis → verify new categories classified
- [x] Search filter works
- [x] Keyboard shortcuts work

## Technical Fixes Applied

1. **Icon encoding**: Icons in `ai-analyze-prod.py` use Python unicode escapes (`\U0001f54a`). Added `decode_icon()` to convert to real unicode for JSON API, and `encode_icon_for_python()` to convert back when writing.
2. **`re.sub` \U escape bug**: `re.sub()` interprets `\U` in replacement strings as regex escapes. Fixed by using manual string replacement instead.
3. **`chr(92)` for backslash**: Python source cannot contain literal `\U` in strings. Used `chr(92)` to build backslash strings at runtime.
4. **`__pycache__` stale files**: Old `.pyc` files caused issues. Always clear `__pycache__` before testing.
5. **Zombie Python processes**: Multiple server instances bound to same port. Kill all PIDs before restarting.
6. **Windows console encoding**: `sys.stdout.reconfigure(encoding='utf-8')` needed in both `ai-analyze-prod.py` and `dev-serve.py` for emoji output.
7. **Analysis UTF-8 crash**: Analysis thread `print()` crashed when log text contained emoji (server stdout was cp1252). Fixed with `reconfigure` in `dev-serve.py` `main()`.
8. **Raw byte pipe reading**: `proc.stdout.readline()` reads raw bytes, decoded as UTF-8 with `errors='replace'` to avoid surrogates.
9. **Progress ticker**: Log updates every 5 articles with `[%]` progress. Admin UI shows `%` in status badge.
10. **Cancel/Stop button**: `POST /api/analysis/cancel` kills child process and resets state. UI shows "⏹ Stop" button during runs.
11. **Stale state recovery**: Server startup resets `analysis_status`. Run handler auto-detects dead child processes.
12. **30-minute timeout**: Child process is killed if analysis hangs beyond 30 minutes.
13. **Deploy taxonomy suggestions**: `deploy_categories()` auto-imports taxonomy-suggested categories into `ai-analyze-prod.py` SOLUTIONS before deploying. This ensures analysis classifies articles into them.
14. **Test env sync**: `sync_data()` merges `solutions.json` analysis results into `test-data.json`, preserving deployed test-only categories that aren't in SOLUTIONS yet.
15. **Zombie process**: Always verify only one server is LISTENING on port 8766 before testing. Multiple servers cause stale responses.
16. **Live site architecture**: `peace-meter.pages.dev` uses a GDELT Proxy Worker (`gdelt-proxy.erez4free.workers.dev`) via Pages Function (`functions/data.json.js`). The `app/data.json` is only a fallback. Peace-paths data is NOT shown on the live site.
17. **Local testing**: Dev server serves `test-data.json` as `/data.json` at `http://localhost:8766/peace-room/`.
