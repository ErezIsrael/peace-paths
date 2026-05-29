# Event Clustering + Player Stance Tracking

> **Goal:** Group articles covering the same real-world event into a single `Event`,
> extract key players involved, and label each player's stance (peace / delay / neutral).
>
> Current state: every article = 1 event in its category.
> Target state: 30 articles about the same event → 1 Event with 30 coverage entries.

---

## Data Model

```
Event
├── id: "iran-us-strikes-2026-05-26"        # generated hash or LLM-suggested slug
├── title: "US Strikes on Iran Violate Ceasefire"
├── category: "iran-us-conflict"
├── firstSeen: "2026-05-26T20:25:00Z"
├── lastSeen: "2026-05-27T14:05:14Z"
├── coverageCount: 23
├── coverage: [                              // the articles covering this event
│     { article, source, date, link, sentiment, ai_risk }
│     ...
├── players: [                               // key entities involved
│     {
│       name: "Donald Trump",
│       type: "person",                       // person | org | government | corporation | militia
│       org: "US White House",
│       stance: "peace" | "delay" | "neutral" // did they push peace or escalate?
│       mentions: 12                          // how many articles mentioned them
│     },
│     {
│       name: "IRGC",
│       type: "militia",
│       stance: "delay",
│       mentions: 8
│     }
├── aggregateSentiment: "negative"           // weighted across coverage
└── aggregateRisk: 7                         // median/mean of ai_risk
```

---

## Pipeline Changes

### Current flow

```
RSS → AI classify (category + sentiment + risk) → group by category → solutions.json
```

### New flow

```
RSS → AI classify (category + sentiment + risk + event_cluster + players+stance)
     → cluster by event → aggregate → solutions.json
```

The AI already processes each article individually. The change is asking it for
**two more fields**:

```json
{
  "me_relevant": true,
  "category": "iran-us-conflict",
  "sentiment": "negative",
  "risk": 8,
  "event_cluster": "us-strikes-iran-ceasefire",
  "players": [
    {"name": "Donald Trump", "type": "person", "org": "US White House", "stance": "delay"},
    {"name": "IRGC", "type": "militia", "stance": "delay"}
  ]
}
```

---

## Clustering Strategy

Two approaches — **Option B recommended**.

### Option A: AI-generated cluster IDs (simpler)

The AI assigns a slug like `"us-strikes-iran-ceasefire"`. Articles sharing the
same slug within ±48h get grouped. Simple, but depends on AI consistency.

### Option B: Title-based dedup (more robust) — **RECOMMENDED**

After AI classification, run a post-processing step:

1. Compute similarity between article titles (Jaccard on words, or cosine on embeddings)
2. Articles with similarity > 0.7 within 72h → same event
3. Merge players across coverage articles

The AI's `event_cluster` slug serves as a helpful hint, but fuzzy title matching
is the real dedup mechanism.

---

## Implementation Phases

### Phase 1 — AI Prompt Extension (low risk, additive)

- Add `event_cluster` and `players` to the LLM output schema in `_classify_article()`
- New prompt fields asking AI to extract 2-5 key players with stance
- Backward compatible — if AI doesn't return these fields, fall through silently

**Files:** `ai-analyze-prod.py` — `_classify_article()`, `_make_classifier_prompt()`

### Phase 2 — Event Clustering (new module)

- After classification, cluster articles by `event_cluster` slug + date proximity (±48h)
- Fallback: fuzzy title matching (Jaccard similarity on title words, threshold 0.7)
- Compute aggregate sentiment/risk per event
- Merge player mentions across coverage articles
- Normalize player names ("Trump" / "Donald Trump" / "the US president" → same player)

**Files:** New module or section in `ai-analyze-prod.py` — `cluster_events()`

### Phase 3 — Output Format (solutions.json schema change)

- Solutions still have `events` array, but each event now has:
  - `coverage[]` (articles) instead of being an article itself
  - `players[]` with stance info
  - `coverageCount`
- Frontend renders "23 articles about this event" instead of listing each separately

**Files:** `ai-analyze-prod.py` — `build_output()`

### Phase 4 — Frontend Display

- Solution cards show "X events, Y articles" instead of "Y events"
- Click an event → expand to see coverage list + player cards with stance indicators
- Player chips: green dot = peace, red = delay, gray = neutral
- Aggregate view: "Most active players this week" across all events

**Files:** `app/peace-room/app.js`, `app/peace-room/styles.css`

---

## Display Ideas

| View | Description |
|------|-------------|
| **Event Card** | Title, date range, coverage count, aggregate sentiment bar, player chips |
| **Player Chip** | Name + type icon + stance color (🟢 peace / 🔴 delay / ⚪ neutral) |
| **Event Detail Modal** | List of all coverage articles, player breakdown with mention counts |
| **Player Index** (future) | Cross-cutting view: "Netanyahu — 15 mentions across 4 events, overall: delay" |
| **Peace Score per Player** | Running tally: % of stances that were "peace" vs "delay" |

---

## Risks & Considerations

1. **AI consistency** — Will the AI assign the same `event_cluster` slug for the
   same event across 30 articles? Probably not perfectly. Hence the fuzzy title
   matching fallback in Phase 2.

2. **Token budget** — Adding players extraction costs more tokens per article.
   Monitor latency and consider limiting to `--daily` mode initially.

3. **Stance subjectivity** — "Did Trump push peace or delay?" is interpretive.
   The AI can make a judgment call, but a confidence score may be useful.

4. **Player normalization** — "Trump", "Donald Trump", "the US president" → same
   player. Need entity normalization (simple string match + alias map to start).

5. **Type classification** — Determining if a player is `person`, `org`, `government`,
   `corporation`, or `militia` adds complexity. Can start with just `person` and `org`
   and expand later.

---

## Next Steps

1. Implement Phase 1 (AI prompt extension) as a proof of concept
2. Add `--dry-run` output showing the new fields on a small article sample
3. Review output quality before committing to full pipeline change
4. Iterate on prompt wording for consistent player extraction
