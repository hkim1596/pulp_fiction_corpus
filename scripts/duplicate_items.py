#!/usr/bin/env python3
"""Issues the archive holds twice — found from the exact-reuse dry run (15 October 2026).

The exact-reuse pass over the corpus (pipeline/r02_verbatim_corpus.py, data/reuse/corpus_dryrun/) reports 19,026
matches of 500 tokens or more and a longest match of 39,517 tokens: whole stories present twice or more. Some are
reprints (reprint magazines, British editions); some are the archive's duplicate items of one issue under several
identifiers (the journal's open item of September: "a duplicate check by text for issues the archive holds under
several names"). This script tells them apart from the pair table, so that duplicate items can be resolved before any
analysis counts them as reuse.

    python3 scripts/duplicate_items.py [--k 6] [--cover 0.8] [--min-len 200] [--share 0.5]

Reads data/reuse/corpus_dryrun/tokens/stories.jsonl (the stories' issue, magazine, date, title) and
data/reuse/corpus_dryrun/machine_k<k>_pairs.npz (every story pair with a match: longest match, share of each story
covered). A story pair is "the same text" when the matches cover at least --cover of both stories and the longest
match has --min-len tokens or more. Two issues are "the same issue twice" when same-text pairs join at least --share
of the stories of the smaller issue (and at least two stories, or all of a one-story issue), with the two issues'
magazines and dates deciding the kind: the same magazine and the same cover date = the archive's duplicate item; the
same magazine and another date = a date to check; different magazines = an edition or a reprint. Writes
data/reuse/corpus_dryrun/duplicates/{issue_pairs.json, story_pairs.jsonl, report.md} and prints the counts.
Nothing else is changed.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "data", "reuse", "corpus_dryrun")


def load_stories(snap):
    out = []
    with open(os.path.join(snap, "tokens", "stories.jsonl"), encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            m = s.get("meta") or {}
            out.append({"story_id": s["story_id"], "issue": s["issue"], "n_tokens": s["n_tokens"], "magazine": m.get("magazine"),
                        "cover_date": m.get("cover_date"), "title": m.get("title"), "author": m.get("author")})
    return out


def same_text_pairs(pairs, cover=0.8, min_len=200):
    sel = (pairs["cover_a"] >= cover) & (pairs["cover_b"] >= cover) & (pairs["max_len"] >= min_len)
    return {k: v[sel] for k, v in pairs.items()}


def issue_pairs(stories, same, share=0.5):
    """Aggregate the same-text story pairs by issue pair."""
    by_issue = Counter(s["issue"] for s in stories)
    agg = defaultdict(lambda: {"n": 0, "a_stories": set(), "b_stories": set(), "examples": []})
    for a, b, ml in zip(same["a"], same["b"], same["max_len"]):
        sa, sb = stories[int(a)], stories[int(b)]
        ia, ib = sa["issue"], sb["issue"]
        if ia == ib:
            continue
        key = (ia, ib) if ia < ib else (ib, ia)
        d = agg[key]
        d["n"] += 1
        d["a_stories"].add(sa["story_id"] if ia < ib else sb["story_id"])
        d["b_stories"].add(sb["story_id"] if ia < ib else sa["story_id"])
        if len(d["examples"]) < 3:
            d["examples"].append({"a": sa["story_id"], "b": sb["story_id"], "title_a": sa["title"], "title_b": sb["title"], "longest": int(ml)})
    meta = {}
    for s in stories:
        meta.setdefault(s["issue"], {"magazine": s["magazine"], "cover_date": s["cover_date"], "stories": by_issue[s["issue"]]})
    out = []
    for (ia, ib), d in agg.items():
        na, nb = by_issue[ia], by_issue[ib]
        smaller = min(na, nb)
        joined = min(len(d["a_stories"]), len(d["b_stories"]))
        if joined < max(2, int(np.ceil(share * smaller))) and not (smaller == 1 and joined == 1):
            continue
        ma, mb = meta[ia], meta[ib]
        if ma["magazine"] == mb["magazine"] and (ma["cover_date"] or "") == (mb["cover_date"] or ""):
            kind = "the same issue under two identifiers"
        elif ma["magazine"] == mb["magazine"]:
            kind = "the same magazine, another date: a date to check"
        else:
            kind = "another magazine: an edition or a reprint"
        out.append({"issue_a": ia, "issue_b": ib, "magazine_a": ma["magazine"], "magazine_b": mb["magazine"],
                    "date_a": ma["cover_date"], "date_b": mb["cover_date"], "stories_a": na, "stories_b": nb,
                    "same_text_pairs": d["n"], "stories_joined_a": len(d["a_stories"]), "stories_joined_b": len(d["b_stories"]),
                    "share_of_smaller": round(joined / smaller, 3), "kind": kind, "examples": d["examples"]})
    out.sort(key=lambda r: (r["kind"], -r["share_of_smaller"], r["issue_a"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=SNAP)
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--cover", type=float, default=0.8)
    ap.add_argument("--min-len", type=int, default=200)
    ap.add_argument("--share", type=float, default=0.5)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    t0 = time.time()
    stories = load_stories(args.snap)
    z = np.load(os.path.join(args.snap, f"machine_k{args.k}_pairs.npz"))
    pairs = {k: z[k] for k in ("a", "b", "n_matches", "max_len", "cover_a", "cover_b")}
    same = same_text_pairs(pairs, args.cover, args.min_len)
    ip = issue_pairs(stories, same, args.share)
    outdir = os.path.join(args.snap, "duplicates")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "story_pairs.jsonl"), "w", encoding="utf-8") as f:
        for a, b, ml, ca, cb in zip(same["a"], same["b"], same["max_len"], same["cover_a"], same["cover_b"]):
            sa, sb = stories[int(a)], stories[int(b)]
            f.write(json.dumps({"a": sa["story_id"], "b": sb["story_id"], "issue_a": sa["issue"], "issue_b": sb["issue"], "longest": int(ml),
                                "cover_a": round(float(ca), 3), "cover_b": round(float(cb), 3), "title_a": sa["title"], "title_b": sb["title"],
                                "magazine_a": sa["magazine"], "magazine_b": sb["magazine"], "date_a": sa["cover_date"], "date_b": sb["cover_date"]},
                               ensure_ascii=False) + "\n")
    kinds = Counter(r["kind"] for r in ip)
    issues_dup = len({r["issue_a"] for r in ip} | {r["issue_b"] for r in ip})
    story_dup_ids = set(int(x) for x in same["a"]) | set(int(x) for x in same["b"])
    summary = {"generated": time.strftime("%Y-%m-%d %H:%M"), "k": args.k, "cover": args.cover, "min_len": args.min_len, "share": args.share,
               "same_text_story_pairs": int(same["a"].size), "stories_with_a_same_text_copy": len(story_dup_ids),
               "issue_pairs": len(ip), "issues_involved": issues_dup, "by_kind": dict(kinds),
               "label": "engineering dry run of 14 October 2026 onward; not the study's numbers"}
    json.dump({"summary": summary, "issue_pairs": ip}, open(os.path.join(outdir, "issue_pairs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lines = [f"# Issues the archive holds twice — from the exact-reuse dry run, {summary['generated']}", "",
             f"Same-text story pairs (matches cover {args.cover:.0%} of both stories, longest match {args.min_len}+ tokens): {summary['same_text_story_pairs']:,}; "
             f"stories with such a copy: {summary['stories_with_a_same_text_copy']:,}. Issue pairs joined by at least {args.share:.0%} of the smaller issue's stories: "
             f"{len(ip):,}, involving {issues_dup:,} issues.", "", "| kind | issue pairs |", "|---|---|"] + [f"| {k} | {n} |" for k, n in kinds.most_common()]
    lines += ["", "| kind | issue A | issue B | magazine A | magazine B | date A | date B | stories A | stories B | joined | share |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in ip:
        lines.append(f"| {r['kind']} | {r['issue_a']} | {r['issue_b']} | {r['magazine_a']} | {r['magazine_b']} | {r['date_a']} | {r['date_b']} | "
                     f"{r['stories_a']} | {r['stories_b']} | {r['stories_joined_a']}/{r['stories_joined_b']} | {r['share_of_smaller']} |")
    open(os.path.join(outdir, "report.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[duplicates] {summary['same_text_story_pairs']:,} same-text story pairs; {len(ip):,} issue pairs ({dict(kinds)}); "
          f"{issues_dup:,} issues involved; {time.time() - t0:.0f}s; report: {os.path.relpath(os.path.join(outdir, 'report.md'), ROOT)}")


def selftest():
    stories = [{"story_id": f"s{i}", "issue": iss, "n_tokens": 1000, "magazine": mag, "cover_date": d, "title": f"t{i}", "author": None}
               for i, (iss, mag, d) in enumerate([("A", "M", "1930-01"), ("A", "M", "1930-01"), ("A", "M", "1930-01"),
                                                  ("B", "M", "1930-01"), ("B", "M", "1930-01"), ("B", "M", "1930-01"),
                                                  ("C", "N", "1931-05"), ("C", "N", "1931-05"), ("D", "M", "1930-02"), ("E", "M", "1932-01")])]
    pairs = {"a": np.array([0, 1, 2, 0, 6, 8, 4], dtype=np.int32), "b": np.array([3, 4, 5, 6, 9, 9, 7], dtype=np.int32),
             "n_matches": np.ones(7, dtype=np.int64), "max_len": np.array([900, 950, 800, 300, 20, 950, 400]),
             "cover_a": np.array([0.95, 0.99, 0.9, 0.3, 0.02, 0.97, 0.5]), "cover_b": np.array([0.96, 0.98, 0.91, 0.3, 0.02, 0.96, 0.9])}
    same = same_text_pairs(pairs)
    ip = issue_pairs(stories, same)
    kinds = {(r["issue_a"], r["issue_b"]): r["kind"] for r in ip}
    ok = same["a"].size == 4 and kinds.get(("A", "B")) == "the same issue under two identifiers" and kinds.get(("D", "E")) == "the same magazine, another date: a date to check" \
        and ("A", "C") not in kinds and ("B", "C") not in kinds
    print(f"same-text pairs {same['a'].size} (4), issue pairs {sorted(kinds.items())} -> {'OK' if ok else 'FAIL'}")
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    main()
