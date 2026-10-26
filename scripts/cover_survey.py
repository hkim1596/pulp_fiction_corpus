#!/usr/bin/env python3
"""What the first scan of every assembled issue is — a cover, a contents list (The Black Cat printed its contents on
the cover), running text (the cover and the front matter are missing from the file), blank, or something else (an
advertisement, a title page) — from the layout pages, with pipeline/s08_assemble_rules.py first_scan_kind (rules
2.12). Sujin Kang, 26 October 2026: "the files that have no cover and begin with the text need looking at too".

    python3 scripts/cover_survey.py [--workers 8] [--limit N]

Writes data/corpus/p52-2026-10-16/cover_survey.json (one line of evidence per issue) and prints the counts and the
issues that begin with running text or with a contents list.
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))


def one(args):
    iid, magazine = args
    import s08_assemble_rules as R
    pages = R.load_pages(iid)
    if not pages:
        return {"issue": iid, "first_scan": None, "kind": "no pages", "evidence": []}
    kind, ev = R.first_scan_kind(pages, magazine)
    second = None
    if kind in ("body", "other", "blank") and len(pages) > 1:
        nxt = sorted(pages)[1]
        second = R.first_scan_kind({nxt: pages[nxt]}, magazine)[0]
    return {"issue": iid, "magazine": magazine, "first_scan": min(pages), "kind": kind, "second_scan_kind": second, "evidence": ev,
            "pages": len(pages)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    from corpus_lib import corpus_config, all_states, active_issues, corpus_kinds
    kinds = corpus_kinds()
    issues, _ = active_issues(corpus_config()["issues"])
    states = all_states()
    jobs = [(i["id"], i.get("magazine") or "") for i in issues if "assembled" in states.get(i["id"], {}).get("stages", {}) and kinds.get(i["id"], "magazine") == "magazine"]
    if args.limit:
        jobs = jobs[:args.limit]
    out_path = os.path.join(ROOT, "data", "corpus", "p52-2026-10-16", "cover_survey.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    rows = []
    with mp.get_context("fork").Pool(max(1, args.workers)) as pool:
        for r in pool.imap_unordered(one, jobs, chunksize=16):
            rows.append(r)
    rows.sort(key=lambda r: r["issue"])
    json.dump({"issues": len(rows), "by_kind": dict(Counter(r["kind"] for r in rows)), "rows": rows}, open(out_path, "w", encoding="utf-8"), indent=0)
    c = Counter(r["kind"] for r in rows)
    print(f"{len(rows)} magazine issues: " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
    body = [r for r in rows if r["kind"] == "body"]
    print(f"\n{len(body)} issues begin with running text (no cover, no front matter in the file):")
    for r in body[:60]:
        print(f"  {r['issue']}  {r['magazine']}  ({r['pages']} scans)")
    if len(body) > 60:
        print(f"  … and {len(body) - 60} more (in cover_survey.json)")
    cont = [r for r in rows if r["kind"] == "contents"]
    print(f"\n{len(cont)} issues print the contents on the first scan (the cover carries the contents):")
    for r in cont[:40]:
        print(f"  {r['issue']}  {r['magazine']}")
    print(f"\nwritten: {os.path.relpath(out_path, ROOT)}")


if __name__ == "__main__":
    main()
