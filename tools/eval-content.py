#!/usr/bin/env python3
"""Measure the *content* quality of what the pipeline publishes.

    python tools/eval-content.py                                # live archives
    python tools/eval-content.py --fixture tests/fixtures/published-events-2026-11-28.json
    python tools/eval-content.py --scope strict                 # initiative-only reading

It answers the four questions that a single pipeline run cannot:

  scope       - is each event actually about the tracker it sits in?
  redundancy  - how many rows are repeat coverage of one development?
  hygiene     - rolled live blogs, outlet suffixes, link roundups, tickers
  resolution  - does the number on the card mean anything (sentiment/signal spread,
                attestation share)

The scope pass uses the LLM as annotator against the tracker's own `description`
from categories.json, so it is a measurement of the same text the classifier was
given. It is calibrated two ways on purpose: `moderate` (initiative + developments
that change its odds) and `strict` (initiative only). Both are reported because the
truth sits between them; what matters is that the same fixture moves the same way
before and after a prompt change. Temperature 0 still varies by ~2 points between
runs, so treat single-digit differences as noise.
"""
import argparse
import collections
import importlib.util
import io
import json
import os
import re
import statistics
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOLUTIONS_URL = "https://peace-paths.pages.dev/data.json"
ARCHIVE_URL = "https://peace-paths.pages.dev/events/{}.json"

TRACKER_PREFIX = re.compile(r"^\s*[-\[({|]?\s*(live|live updates|live blog|watch|report|"
                            r"video|analysis|opinion|preview|breaking|developing)\s*[:||-]", re.I)
OUTLET_SUFFIX = re.compile(r"\s[-–—|]\s?[A-Za-z0-9' .&,#-]{2,30}$")
ROUNDUP = re.compile(r"\b\d{1,2}/\d{1,2}\s+links\b|links pt\d|\broundup\b", re.I)
TICKER = re.compile(r"^[A-Z]{2,6}\.\s|#[A-Za-z]+News|\bPTV\b")


def load_pipeline():
    spec = importlib.util.spec_from_file_location("peace_paths_pipeline",
                                                  os.path.join(ROOT, "ai-analyze-prod.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text_of(ev, lang="en"):
    t = ev.get("text")
    if isinstance(t, dict):
        return t.get(lang) or t.get("en") or ""
    return t or ""


def fetch_live():
    req = urllib.request.Request(SOLUTIONS_URL, headers={"User-Agent": "peace-paths-eval"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    out = {}
    for sol in data.get("solutions", []):
        sid = sol["id"]
        try:
            req = urllib.request.Request(ARCHIVE_URL.format(sid), headers={"User-Agent": "peace-paths-eval"})
            out[sid] = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("events", [])
        except Exception as exc:
            print(f"  ⚠ {sid}: no archive ({exc})")
            out[sid] = sol.get("events", [])
    return out


# ── redundancy: the pipeline's own similarity, applied across a whole archive ──
def redundancy(events, mod, threshold=0.40):
    docs = []
    for e in events:
        raw = text_of(e)
        docs.append((set(mod._event_tokens(raw)), mod._event_entities(raw)))
    df = collections.Counter()
    for toks, _ in docs:
        df.update(toks)
    n = max(len(docs), 1)

    def wt(t):
        return 1.0 / (1.0 + df[t] / n)

    def sim(i, j):
        ta, tb = docs[i][0], docs[j][0]
        if not ta or not tb:
            return 0.0
        small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if len(small) >= 4 and small <= big:
            return 1.0
        inter = ta & tb
        if not inter:
            return 0.0
        score = sum(wt(t) for t in inter) / sum(wt(t) for t in ta | tb)
        ea, eb = docs[i][1], docs[j][1]
        if ea and eb and not (ea & eb):
            score *= 0.5
        return score

    used, groups = set(), []
    for i in range(len(docs)):
        if i in used:
            continue
        g = [i]
        used.add(i)
        for j in range(i + 1, len(docs)):
            if j in used:
                continue
            if sim(i, j) >= threshold:
                g.append(j)
                used.add(j)
        if len(g) > 1:
            groups.append(g)
    return groups


# ── scope: LLM annotator, same two readings ──
SCOPE_RULES = {
    "moderate": ("IN SCOPE if the item either (a) is about this initiative or process itself - its "
                 "diplomacy, implementation, backers, opponents or obstacles - or (b) is a development "
                 "in the same conflict that materially changes the initiative's odds (violence that "
                 "undermines a ceasefire, sanctions, a leadership change, a court ruling). "
                 "OFF TOPIC: other countries and conflicts, pure human-interest stories, sports and "
                 "arts, culture-war commentary, link roundups, and items whose only connection to the "
                 "tracker is a shared keyword or a shared actor."),
    "strict": ("A news item is IN SCOPE only if it is about this specific initiative or process: its "
               "diplomacy, its implementation, its obstacles, or a development that changes its odds. "
               "Items that merely share the region, the actors, or a passing keyword are OFF TOPIC "
               "(general war reporting, human-interest stories, third-country politics, op-eds about "
               "unrelated conflicts)."),
}


def scope_judge(mod, name, description, events, reading):
    lines = "\n".join("{}. {}".format(i + 1, text_of(e)[:120]) for i, e in enumerate(events))
    shape = 'Respond with JSON: {"in_scope": [ids], "off_topic": [ids]} - every id exactly once.'
    user = ("TRACKER: " + name + "\nWHAT IT TRACKS: " + (description or "") + "\n\n"
            + SCOPE_RULES[reading] + "\n\n" + lines + "\n\n" + shape)
    body = {"model": mod.AI_MODEL,
            "messages": [{"role": "system", "content": "You judge whether news items fall inside a tracker's stated scope. JSON only."},
                         {"role": "user", "content": user}],
            "max_tokens": min(6000, max(800, 60 * len(events))), "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}
    resp = mod._post_llm(body, timeout=900)
    if resp is None:
        return None
    parsed = mod._safe_json(resp["choices"][0]["message"]["content"]) or {}
    ins = [i for i in parsed.get("in_scope", []) if isinstance(i, int)]
    off = [i for i in parsed.get("off_topic", []) if isinstance(i, int)]
    return ins, off


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="JSON: {solutions: {id: [events]}} or {id: [events]}")
    ap.add_argument("--scope", choices=("moderate", "strict", "both"), default="moderate")
    ap.add_argument("--no-scope", action="store_true", help="skip the LLM scope pass")
    ap.add_argument("--dup-threshold", type=float, default=0.40)
    args = ap.parse_args()

    mod = load_pipeline()
    if args.fixture:
        raw = json.load(io.open(args.fixture, encoding="utf-8"))
        archives = raw.get("solutions", raw)
    else:
        archives = fetch_live()

    cats = json.load(io.open(os.path.join(ROOT, "categories.json"), encoding="utf-8"))
    cat_by_id = {c["id"]: c for c in cats}

    all_events = [e for evs in archives.values() for e in evs]
    print(f"archives: {len(archives)} trackers, {len(all_events)} published events\n")

    sent = collections.Counter()
    sigs, hygiene = [], {"outlet": 0, "tracker": 0, "roundup": 0, "ticker": 0}
    tiers = collections.Counter()
    attest = 0
    for e in all_events:
        sent[e.get("sentiment")] += 1
        sigs.append(e.get("signal_score") or 0)
        tiers[e.get("tier", "(untiered)")] += 1
        if e.get("attestations"):
            attest += 1
        t = text_of(e)
        if OUTLET_SUFFIX.search(t):
            hygiene["outlet"] += 1
        if TRACKER_PREFIX.search(t):
            hygiene["tracker"] += 1
        if ROUNDUP.search(t):
            hygiene["roundup"] += 1
        if TICKER.search(t):
            hygiene["ticker"] += 1
    n = max(len(all_events), 1)
    print("resolution of the numbers on the card")
    print(f"  sentiment : {dict(sent)}  → negative share {100*sent['negative']/n:.0f}%")
    print(f"  signal    : min {min(sigs)} median {statistics.median(sigs)} max {max(sigs)}; "
          f"exactly 5 (the default): {100*sigs.count(5)/n:.0f}%; ≥9: {100*sum(1 for s in sigs if s>=9)/n:.0f}%")
    print(f"  tiers     : {dict(tiers)}")
    print(f"  attestations: {attest}/{n} events ({100*attest/n:.0f}%) carry a second source")
    print(f"  text hygiene: outlet-suffixed {hygiene['outlet']} ({100*hygiene['outlet']/n:.0f}%), "
          f"live/tracker prefix {hygiene['tracker']}, link roundups {hygiene['roundup']}, tickers {hygiene['ticker']}")

    print("\nredundancy (IDF-weighted title similarity, threshold %.2f)" % args.dup_threshold)
    tot_dup = 0
    for sid, evs in archives.items():
        if not evs:
            continue
        groups = redundancy(evs, mod, args.dup_threshold)
        dup = sum(len(g) - 1 for g in groups)
        tot_dup += dup
        if groups:
            print(f"  {sid[:36]:36s} {len(groups)} groups, {dup} repeat rows of {len(evs)} "
                  f"({100*dup/len(evs):.0f}%)")
            for g in sorted(groups, key=len, reverse=True)[:2]:
                print("      • " + " || ".join(text_of(evs[i])[:48] for i in g[:3]))
    print(f"  TOTAL repeat rows: {tot_dup}/{len(all_events)} ({100*tot_dup/n:.0f}%)")

    if args.no_scope:
        return 0
    readings = ("moderate", "strict") if args.scope == "both" else (args.scope,)
    for reading in readings:
        print(f"\nscope against each tracker's own description ({reading} reading)")
        tot_in = tot_n = 0
        for sid, evs in archives.items():
            if not evs:
                continue
            cat = cat_by_id.get(sid, {})
            name = cat.get("name")
            name = name.get("en") if isinstance(name, dict) else name
            desc = cat.get("description")
            desc = desc.get("en") if isinstance(desc, dict) else desc
            res = scope_judge(mod, name or sid, desc, evs, reading)
            if res is None:
                print(f"  {sid[:36]:36s} LLM unavailable")
                continue
            ins, off = res
            tot_in += len(ins)
            tot_n += len(evs)
            flag = "  ⚠" if len(ins) / len(evs) < 0.5 else "   "
            print(f" {flag} {sid[:36]:36s} {len(ins):3d}/{len(evs):3d} in scope ({100*len(ins)/len(evs):3.0f}%)")
        print(f"  TOTAL: {tot_in}/{tot_n} = {100*tot_in/max(tot_n,1):.0f}% of published events are about their tracker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
