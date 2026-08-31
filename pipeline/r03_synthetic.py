#!/usr/bin/env python3
"""r03 — planted reuse for machinery validation (kept strictly separate).

Ten issues from five magazines may contain little genuine cross-magazine
reuse, so a run that returns nothing cannot tell us whether the corpus
has no reuse or the pipeline missed it. This module makes a SEPARATE
test copy of the story corpus with known reuse planted into it, then
scores how much of it each stage recovers. Everything it writes lives
under data/reuse/synthetic/ and every synthetic record carries
"synthetic": true and a "~synth" suffix on its id — nothing here can be
mistaken for a finding.

Three kinds of plant, from real passages of real stories, copied into a
recipient story from a DIFFERENT issue:
  verbatim       the passage copied exactly (30-150 tokens)
  near-verbatim  the copy lightly damaged — about 8% of tokens replaced,
                 dropped, or inserted — the shape of OCR noise and small
                 edits (Sujin's point: this tier should be caught by a
                 lexical near-match pass before any semantics)
  heavy-edit     the copy shortened by ~20%, clauses swapped, 15% of
                 tokens replaced — a stand-in for paraphrase until the
                 hand-reviewed paraphrase set exists; it exists to show
                 what the verbatim stage cannot see, not to validate the
                 paraphrase stage.

Run:  python3 pipeline/r03_synthetic.py            (needs data/pilot_stories.jsonl)
      python3 pipeline/r03_synthetic.py --selftest
Outputs: synthetic/stories_synth.jsonl, synthetic/plants.json,
         synthetic/recall_verbatim.json (per kind, per seed length)
"""
import argparse
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r02_verbatim as r2  # noqa: E402
from r01_normalize import prepare  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNDIR = os.path.join(ROOT, "data", "reuse", "synthetic")
N_PER_KIND = 20
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentences(text):
    return [s for s in SENT_SPLIT.split(text) if s.strip()]


def _damage(tokens, rng, rate, vocab):
    out = []
    for t in tokens:
        r = rng.random()
        if r < rate / 3:
            continue                                  # drop
        if r < 2 * rate / 3:
            out.append(rng.choice(vocab))             # replace
            continue
        out.append(t)
        if r < rate:
            out.append(rng.choice(vocab))             # insert after
    return out


def _heavy_edit(text, rng, vocab):
    sents = _sentences(text)
    if len(sents) >= 3:                               # swap two clauses/sentences
        i = rng.randrange(len(sents) - 1)
        sents[i], sents[i + 1] = sents[i + 1], sents[i]
    toks = " ".join(sents).split()
    keep = [t for t in toks if rng.random() > 0.20]   # shorten ~20%
    keep = [rng.choice(vocab) if rng.random() < 0.15 else t for t in keep]
    return " ".join(keep)


def make_synthetic(recs, seed=11, n_per_kind=N_PER_KIND):
    """Return (synthetic records, plant manifest). Only stories with at
    least 400 tokens serve as donors or recipients."""
    rng = random.Random(seed)
    base = [dict(r) for r in recs
            if r.get("type") in r2.STORY_TYPES and len((r.get("text") or "").split()) >= 400]
    for r in base:
        r["text"] = prepare(r["text"])
    vocab = sorted({w for r in base for w in r["text"].split()})
    plants = []
    kinds = ["verbatim"] * n_per_kind + ["near-verbatim"] * n_per_kind + ["heavy-edit"] * n_per_kind
    for pid, kind in enumerate(kinds):
        for _ in range(200):                          # find a cross-issue pair
            donor, recip = rng.sample(base, 2)
            if donor["issue"] != recip["issue"]:
                break
        dsents = _sentences(donor["text"])
        span_n = rng.randint(2, 6)
        start = rng.randrange(max(1, len(dsents) - span_n))
        passage = " ".join(dsents[start:start + span_n])
        ptoks = passage.split()
        if len(ptoks) < 30:
            passage = " ".join(dsents[start:start + span_n + 3])
            ptoks = passage.split()
        ptoks = ptoks[:150]
        passage = " ".join(ptoks)
        if kind == "verbatim":
            planted = passage
        elif kind == "near-verbatim":
            planted = " ".join(_damage(ptoks, rng, 0.08, vocab))
        else:
            planted = _heavy_edit(passage, rng, vocab)
        rsents = _sentences(recip["text"])
        at = rng.randrange(1, max(2, len(rsents)))
        recip["text"] = " ".join(rsents[:at] + [planted] + rsents[at:])
        plants.append({"plant_id": pid, "kind": kind,
                       "donor": donor["story_id"], "recipient": recip["story_id"],
                       "donor_issue": donor["issue"], "recipient_issue": recip["issue"],
                       "len_tokens": len(ptoks), "passage": passage,
                       "planted_text": planted})
    synth = []
    for r in base:
        s = dict(r)
        s["story_id"] = r["story_id"] + "~synth"
        s["synthetic"] = True
        synth.append(s)
    for p in plants:
        p["donor"] += "~synth"
        p["recipient"] += "~synth"
    return synth, plants


def score_verbatim(synth, plants, ks=(6, 7, 8), coverage=0.8):
    """For each seed length: share of plants recovered, per kind. A plant
    counts as recovered when donor and recipient share matches covering
    at least `coverage` of the planted passage's tokens (verbatim) or
    any match at all (the two damaged kinds, where partial recovery is
    the expected and informative outcome)."""
    units = r2.build_units(synth)
    idx = {u["story_id"]: i for i, u in enumerate(units)}
    report = {}
    for k in ks:
        matches, _si, _sk = r2.find_matches(units, k)
        pair_cov = {}
        for m in matches:
            key = tuple(sorted((units[m["a"]]["story_id"], units[m["b"]]["story_id"])))
            pair_cov[key] = pair_cov.get(key, 0) + m["len"]
        per_kind = {}
        for p in plants:
            key = tuple(sorted((p["donor"], p["recipient"])))
            got = pair_cov.get(key, 0)
            hit = (got >= coverage * p["len_tokens"]) if p["kind"] == "verbatim" else got > 0
            d = per_kind.setdefault(p["kind"], {"plants": 0, "recovered": 0,
                                                "tokens_recovered_share": []})
            d["plants"] += 1
            d["recovered"] += int(hit)
            d["tokens_recovered_share"].append(min(1.0, got / max(1, p["len_tokens"])))
        for d in per_kind.values():
            d["recall"] = round(d["recovered"] / d["plants"], 3)
            d["mean_token_share"] = round(sum(d["tokens_recovered_share"])
                                          / len(d["tokens_recovered_share"]), 3)
            del d["tokens_recovered_share"]
        report[f"k{k}"] = {"total_matches": len(matches), "per_kind": per_kind}
    return report


def selftest():
    recs = r2._fake_corpus()
    # make the fake stories long enough to donate/receive
    for r in recs:
        r["text"] = ". ".join(r["text"].split(" w")[:1]) + ". " + \
            ". ".join(" ".join(r["text"].split()[i:i + 12]) for i in range(0, len(r["text"].split()), 12))
    synth, plants = make_synthetic(recs, n_per_kind=3)
    rep = score_verbatim(synth, plants)
    ok = all(v["per_kind"]["verbatim"]["recall"] == 1.0 for v in rep.values())
    print(json.dumps(rep, indent=1))
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stories", default=r2.STORIES)
    ap.add_argument("--out", default=SYNDIR)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    recs = r2.load_stories(args.stories, "machine")
    synth, plants = make_synthetic(recs)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "stories_synth.jsonl"), "w", encoding="utf-8") as f:
        for s in synth:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    json.dump(plants, open(os.path.join(args.out, "plants.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    rep = score_verbatim(synth, plants)
    import time
    rep["_generated"] = time.strftime("%Y-%m-%d %H:%M")
    rep["_stories"] = len(synth)
    rep["_plants"] = len(plants)
    json.dump(rep, open(os.path.join(args.out, "recall_verbatim.json"), "w"), indent=1)
    print(f"[r03] {len(synth)} synthetic stories, {len(plants)} plants")
    for k, v in rep.items():
        if k.startswith("_"):
            continue
        print(f"[r03] {k}: " + ", ".join(
            f"{kind} recall {d['recall']} (mean token share {d['mean_token_share']})"
            for kind, d in v["per_kind"].items()))


if __name__ == "__main__":
    main()
