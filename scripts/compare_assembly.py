#!/usr/bin/env python3
"""Compare the assembly rules now in the code with the candidate files on disk, issue by issue, without writing
anything (rules 2.14 regression, 4 November 2026).

For every issue given, the pages are read, analysed and assembled in memory twice — with the 2.14 rules on (the current
code) and with them off (the 2.13 behaviour: s08.RULES_214) — and the two are set against each other, so that every
difference is the 2.14 rules' own; the candidate on disk (data/assembly_v2/rules/<issue>/articles.json, whatever rules
version wrote it) is read only for its backend stamp. Reported per issue:
records by type before and after, story records and story words before and after, the records the new rules split
out (paratext family, title, words), the story records whose text changed (title, words before and after, the first
words that left or arrived), and the author strings the by-line guard now rejects. Written as Markdown and JSON.

    python3 scripts/compare_assembly.py --issues-file ids.txt [--out pilot_export/compare-2.14.md]
    python3 scripts/compare_assembly.py --pilot --double-read      # the 10 pilot issues and the 30 double-read sample issues
    python3 scripts/compare_assembly.py --random 50 [--seed 20261105]   # 50 magazine issues drawn at random (set-aside left out)

5 November 2026 (R-A): the records the recurring-box rule split out are listed with their reach and partners; the
records it only flagged (mostly recurring, commonplace?, same work or author) are counted and listed too.
"""
import random
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import s08_assemble_rules as S  # noqa: E402


def issue_ids(args):
    ids = []
    if args.issues_file:
        ids += [l.strip() for l in open(args.issues_file, encoding="utf-8") if l.strip() and not l.startswith("#")]
    if args.pilot:
        cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
        ids += [i["id"] for i in cfg.get("issues", [])]
    if args.double_read:
        vs = json.load(open(os.path.join(ROOT, "config", "verification_sample.json"), encoding="utf-8"))
        ids += [i["id"] for i in vs.get("issues", []) if i.get("second_reader")]
    if args.random:
        from corpus_lib import corpus_config, magazine_issues
        pool = sorted(i["id"] for i in magazine_issues(corpus_config().get("issues", [])) if i["id"] not in ids)
        ids += random.Random(args.seed).sample(pool, min(args.random, len(pool)))
    out, seen = [], set()
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def summarise(doc):
    recs = doc["articles"]
    by_type = {}
    for r in recs:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    stories = {(r.get("title") or "", r.get("author") or ""): r for r in recs if r["type"] == "story"}
    words = sum(len((r.get("text") or "").split()) for r in recs if r["type"] == "story")
    return by_type, stories, words


def _boxes(rec):
    return {(fr.get("page"), i) for fr in rec.get("fragments") or [] for i in fr.get("region_ids") or []}


def withheld_boxes(old, new):
    """Rules 2.15 against 2.14: the paratext records of the baseline whose boxes the new assembly leaves in a piece —
    family, words, the text, and the record that holds them now."""
    new_para = set()
    owner = {}
    for r in new["articles"]:
        b = _boxes(r)
        if r.get("paratext"):
            new_para |= b
        else:
            for k in b:
                owner[k] = r
    out = []
    for r in old["articles"]:
        if not r.get("paratext"):
            continue
        b = _boxes(r)
        if not b or b <= new_para:
            continue
        now = next((owner[k] for k in sorted(b - new_para, key=str) if k in owner), None)
        out.append({"family": r.get("paratext"), "words": len((r.get("text") or "").split()), "pages": r.get("pages"),
                    "recurrence": bool(r.get("recurrence")), "boxes": len(b), "boxes_still_paratext": len(b & new_para),
                    "now_in": (now or {}).get("type"), "now_title": ((now or {}).get("title") or "")[:60], "text": (r.get("text") or "")[:600]})
    return out


def compare_issue(iid, cfg_issue, against="2.13"):
    pages = S.load_pages(iid)
    if not pages:
        return {"issue": iid, "error": "no layout pages"}
    old_path = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "articles.json")
    disk = json.load(open(old_path, encoding="utf-8")) if os.path.exists(old_path) else {}
    kind, book = S.issue_kind(iid, cfg_issue)
    mag = (cfg_issue or {}).get("magazine", "")
    switch = S.RULES_215 if against == "2.14" else S.RULES_214      # the baseline: 2.14 (R-B's second test off) or 2.13
    switch["on"] = False
    try:
        A0 = S.analyse(pages, force_scan=S.toc_page_override(iid), magazine=mag)
        A0["magazine"] = mag
        old = S.assemble(iid, pages, A0, "rules", None, kind=kind, book=book)
    finally:
        switch["on"] = True
    A = S.analyse(pages, force_scan=S.toc_page_override(iid), magazine=mag)
    A["magazine"] = mag
    new = S.assemble(iid, pages, A, "rules", None, kind=kind, book=book)
    ob, os_, ow = summarise(old)
    nb, ns, nw = summarise(new)
    split = [{"family": r.get("paratext"), "ad_class": r.get("ad_class"), "title": r.get("title"), "words": len((r.get("text") or "").split()),
              "pages": r.get("pages"), "recurrence": r.get("recurrence")} for r in new["articles"] if r.get("paratext")]
    ra_flags = []
    for r in new["articles"]:
        if r["type"] != "story":
            continue
        for f in r.get("flags") or []:
            if "rules 2.14, R-A" in f and "split out" not in f:
                ra_flags.append({"title": r.get("title"), "author": r.get("author"), "flag": f.split(" (rules")[0][:160]})
    changed = []
    for k, r in ns.items():
        o = os_.get(k)
        if o is None:
            changed.append({"title": k[0], "author": k[1], "change": "new story record", "words_after": len((r.get("text") or "").split())})
            continue
        ot, nt = (o.get("text") or "").split(), (r.get("text") or "").split()
        if ot != nt:
            # the first run of words that left and that arrived
            i = 0
            while i < min(len(ot), len(nt)) and ot[i] == nt[i]:
                i += 1
            changed.append({"title": k[0], "author": k[1], "change": "text", "words_before": len(ot), "words_after": len(nt),
                            "left": " ".join(ot[i:i + 12]), "arrived": " ".join(nt[i:i + 12]), "at_word": i})
    for k in os_:
        if k not in ns:
            changed.append({"title": k[0], "author": k[1], "change": "story record gone"})
    notes = sum(1 for r in new["articles"] for v in (r.get("roles") or {}).values() if v == "note")
    rejected = []
    for pn, info in A["pages"].items():
        for s_ in info.get("rejected_starts", []) or []:
            if "not a person" in (s_.get("rejected") or ""):
                rejected.append(s_.get("author") or s_.get("title"))
    return {"issue": iid, "rules_before": ("2.14 (rules 2.15 off, in memory)" if against == "2.14" else "2.14 rules off (in memory)"),
            "withheld": withheld_boxes(old, new) if against == "2.14" else [],
            "paratext_before": sum(1 for r in old["articles"] if r.get("paratext")), "on_disk": disk.get("backend"), "rules_after": S.VERSION,
            "types_before": ob, "types_after": nb, "story_words_before": ow, "story_words_after": nw,
            "stories_before": len(os_), "stories_after": len(ns), "split_out": split, "changed_stories": changed, "notes": notes,
            "rejected_bylines": rejected, "ra_flags": ra_flags, "evidence": bool(S.box_recurrence_for(iid))}


def report(rows):
    L = [f"# Assembly comparison — the 2.14 rules on against off, both in memory ({time.strftime('%Y-%m-%d %H:%M')})", "",
         "| issue | stories before → after | story words before → after | split out (paratext) | story records changed | notes | R-A flags | evidence |",
         "|---|---|---|---|---|---|---|---|"]
    tot = {"split": 0, "changed": 0, "wb": 0, "wa": 0, "sb": 0, "sa": 0, "ra": 0, "fam": {}}
    for r in rows:
        if r.get("error"):
            L.append(f"| {r['issue']} | — | — | {r['error']} | | |")
            continue
        L.append(f"| {r['issue']} | {r['stories_before']} → {r['stories_after']} | {r['story_words_before']:,} → {r['story_words_after']:,} | "
                 f"{len(r['split_out'])} | {len(r['changed_stories'])} | {r['notes']} | {len(r.get('ra_flags') or [])} | {'yes' if r.get('evidence') else 'no'} |")
        tot["split"] += len(r["split_out"]); tot["changed"] += len(r["changed_stories"]); tot["ra"] += len(r.get("ra_flags") or [])
        for x in r["split_out"]:
            tot["fam"][x["family"]] = tot["fam"].get(x["family"], 0) + 1
        tot["wb"] += r["story_words_before"]; tot["wa"] += r["story_words_after"]; tot["sb"] += r["stories_before"]; tot["sa"] += r["stories_after"]
    fam = ", ".join(f"{k} {v}" for k, v in sorted(tot["fam"].items(), key=lambda kv: -kv[1]))
    L += ["", f"Totals: stories {tot['sb']} → {tot['sa']}; story words {tot['wb']:,} → {tot['wa']:,} ({tot['wb'] - tot['wa']:,} words left the story "
          f"records); {tot['split']} paratext records split out ({fam}); {tot['changed']} story records changed; {tot['ra']} story records flagged by R-A without a split.", ""]
    L += ["## Split out", "", "| issue | family | class | words | pages | reach | partners | title |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        for x in r.get("split_out", []):
            rc = x.get("recurrence") or {}
            L.append(f"| {r['issue']} | {x['family']} | {x['ad_class']} | {x['words']} | {x['pages']} | {rc.get('n_issues', '')} | {', '.join(rc.get('partners') or [])} | "
                     f"{(x['title'] or '').replace('|', '/')[:90]} |")
    L += ["", "## Flagged by R-A, not split", "", "| issue | title | flag |", "|---|---|---|"]
    for r in rows:
        for x in r.get("ra_flags", []):
            L.append(f"| {r['issue']} | {(x['title'] or '').replace('|', '/')[:60]} | {x['flag'].replace('|', '/')} |")
    L += ["", "## Story records whose text changed", "", "| issue | title | change | words before → after | at word | left | arrived |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        for c in r.get("changed_stories", []):
            L.append(f"| {r['issue']} | {(c['title'] or '').replace('|', '/')[:60]} | {c['change']} | {c.get('words_before', '')} → {c.get('words_after', '')} | "
                     f"{c.get('at_word', '')} | {(c.get('left') or '').replace('|', '/')} | {(c.get('arrived') or '').replace('|', '/')} |")
    L += ["", "## By-lines the guard rejects", ""] + [f"- {r['issue']}: {', '.join(r['rejected_bylines'])}" for r in rows if r.get("rejected_bylines")]
    wh = [(r["issue"], w) for r in rows for w in r.get("withheld") or []]
    if wh:
        L += ["", f"## Rules 2.15 against 2.14: paratext records of 2.14 now left in their piece ({len(wh):,}; {sum(w['words'] for _, w in wh):,} words)", "",
              "| issue | family | words | now in | text |", "|---|---|---|---|---|"]
        for iid, w in sorted(wh, key=lambda x: -x[1]["words"]):
            L.append(f"| {iid} | {w['family']} | {w['words']} | {w.get('now_in') or '—'}: {(w.get('now_title') or '').replace('|', '/')[:40]} | "
                     f"{w['text'][:220].replace('|', '/').replace(chr(10), ' ')} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issues-file")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--double-read", action="store_true")
    ap.add_argument("--random", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--against", choices=("2.13", "2.14"), default="2.13",
                    help="the baseline assembled in memory: 2.13 (the 2.14 rules off) or 2.14 (rules 2.15, R-B's second test, off)")
    ap.add_argument("--out", default=os.path.join(ROOT, "pilot_export", f"compare-{S.VERSION}.md"))
    args = ap.parse_args()
    ids = issue_ids(args)
    if not ids:
        sys.exit("no issues: pass --issues-file, --pilot or --double-read")
    from corpus_lib import issues_config
    cfg = {i["id"]: i for i in issues_config().get("issues", [])}
    try:
        pilot = {i["id"]: i for i in json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8")).get("issues", [])}
    except (OSError, ValueError):
        pilot = {}
    rows = []
    for n, iid in enumerate(ids, 1):
        t0 = time.time()
        rows.append(compare_issue(iid, cfg.get(iid) or pilot.get(iid), against=args.against))
        r = rows[-1]
        if n % 10 == 0 or n == len(ids):
            print(f"[{n}/{len(ids)}] {iid}: " + (r["error"] if r.get("error") else
                  f"stories {r['stories_before']}→{r['stories_after']}, words {r['story_words_before']}→{r['story_words_after']}, split {len(r['split_out'])}, changed {len(r['changed_stories'])}")
                  + f" ({time.time() - t0:.0f}s)")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(report(rows))
    json.dump(rows, open(args.out[:-3] + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tot_split = sum(len(r.get("split_out") or []) for r in rows)
    tot_changed = sum(len(r.get("changed_stories") or []) for r in rows)
    tot_ra = sum(len(r.get("ra_flags") or []) for r in rows)
    sb = sum(r.get("stories_before", 0) for r in rows); sa = sum(r.get("stories_after", 0) for r in rows)
    wb = sum(r.get("story_words_before", 0) for r in rows); wa = sum(r.get("story_words_after", 0) for r in rows)
    print(f"stories {sb} -> {sa}; story words {wb:,} -> {wa:,}; {tot_split} paratext records split out; {tot_changed} story records changed; "
          f"{tot_ra} flagged by R-A; evidence present for {sum(1 for r in rows if r.get('evidence'))}/{len(rows)} issues")
    if args.against == "2.14":
        wh = [w for r in rows for w in r.get("withheld") or []]
        print(f"rules 2.15 against 2.14: {sum(r.get('paratext_before', 0) for r in rows):,} paratext records before; {len(wh):,} left in their piece now "
              f"({sum(w['words'] for w in wh):,} words; {sum(1 for w in wh if w['words'] >= 40):,} of 40 words or more); by family: "
              + ", ".join(f"{k} {v}" for k, v in sorted(__import__('collections').Counter(w['family'] for w in wh).items())))
    print(f"written {os.path.relpath(args.out, ROOT)}")


if __name__ == "__main__":
    main()
