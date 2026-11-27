#!/usr/bin/env python3
"""Compare event-resolution (clustering) behaviour, old rule vs current rule.

    python tools/check-clustering.py --fixture articles.json [--age 48]

`--fixture` is a JSON array of fetched articles (the shape staging/articles.json
has: title, link, source, date, snippet). Without --fixture the script fetches
live feeds, so results move with the news cycle; keep a saved fixture next to
this script if you want a stable regression check.

Prints how many merge groups each rule produces and lists the groups that only
one of them makes, so a metric change can be judged instead of guessed at.
Measured baseline (2026-11-27, 266 articles / 48 h): old rule 5 groups, new rule
6 — the extra one being a real cross-outlet paraphrase, with no group lost.
"""
import argparse
import collections
import importlib.util
import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_pipeline():
    spec = importlib.util.spec_from_file_location(
        "peace_paths_pipeline", os.path.join(ROOT, "ai-analyze-prod.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def old_rule(mod, pairs, threshold=0.65, window_hours=6):
    """The rule that was in ai-analyze-prod.py before the IDF-weighted metric:
    Jaccard over normalized titles, 0.65 (0.55 inside `window_hours`), 24 h cap."""
    by = collections.defaultdict(list)
    for a, c in pairs:
        by[c.get("solution", "regional")].append((a, c, mod._normalize_title(a["title"])))
    groups, total = [], 0
    for sol, items in by.items():
        used = set()
        for i in range(len(items)):
            if i in used:
                continue
            cl = [items[i]]
            used.add(i)
            for j in range(i + 1, len(items)):
                if j in used:
                    continue
                try:
                    di = datetime.fromisoformat(items[i][0]["date"])
                    dj = datetime.fromisoformat(items[j][0]["date"])
                    diff = abs((di - dj).total_seconds()) / 3600
                except Exception:
                    diff = 0
                sim = mod._title_similarity(items[i][2], items[j][2])
                eff = threshold - (0.1 if diff < window_hours else 0)
                if sim >= eff and diff < 24:
                    cl.append(items[j])
                    used.add(j)
            total += 1
            if len(cl) > 1:
                groups.append((sol, tuple(sorted(x[0]["title"] for x in cl))))
    return total, groups


def pairs_from(mod, articles):
    """Cheap, deterministic classification so the comparison needs no GPU: keyword
    pass only, 'regional' for whatever matches nothing."""
    cat_map, all_ids, core_ids, all_kws = mod.load_categories()
    kw_map = {}
    for cid, kws in all_kws.items():
        for kw in kws:
            kw_map[kw.lower()] = cid
    for cid, kws in getattr(mod, "_EXPANDED_KEYWORDS", {}).items():
        if cid in all_ids:
            for kw in kws:
                kw_map[kw.lower().strip()] = cid
    pairs = []
    for a in articles:
        cid, _conf = mod._keyword_classify_one(a["title"], a.get("snippet", ""), kw_map)
        pairs.append((a, {"solution": cid or "regional", "signal_score": 5,
                          "source_weight": 2, "type": "reporting"}))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="JSON array of articles (staging/articles.json shape)")
    ap.add_argument("--age", type=int, default=48, help="hours of feeds to pull when no fixture")
    ap.add_argument("--threshold", type=float, default=None, help="override the current threshold")
    args = ap.parse_args()

    mod = load_pipeline()
    if args.fixture:
        articles = json.load(io.open(args.fixture, encoding="utf-8"))
    else:
        articles = mod.fetch_all_feeds(age_hours=args.age, skip_seen=False)
    pairs = pairs_from(mod, articles)
    print(f"articles: {len(articles)} | pairs: {len(pairs)}")

    profiles = mod.load_source_profiles()
    old_total, old_groups = old_rule(mod, pairs)
    new_all, new_by = mod.cluster_events(pairs, profiles,
                                        **({"threshold": args.threshold} if args.threshold else {}))
    title_by_link = {a.get("link"): a["title"] for a in articles}

    def title_of(ev):
        return title_by_link.get(ev.get("link")) or ev.get("text") or "?"

    new_groups = []
    for sol, evs in new_by.items():
        for ev in evs:
            atts = ev.get("attestations") or []
            if atts:
                members = [title_of(ev)] + [title_by_link.get(a.get("link"), "?") for a in atts]
                new_groups.append((sol, tuple(sorted(members))))

    print(f"\nold rule : {old_total} clusters, {len(old_groups)} merged groups")
    print(f"new rule : {len(new_all)} clusters, {len(new_groups)} merged groups")

    old_set = {g for _, g in old_groups}
    new_set = {g for _, g in new_groups}
    for label, items in (("only the OLD rule merges", old_set - new_set),
                         ("only the NEW rule merges", new_set - old_set)):
        if not items:
            continue
        print(f"\n--- {label} ({len(items)}) ---")
        for g in items:
            for t in g:
                print("   -", t[:96])
            print("   ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
