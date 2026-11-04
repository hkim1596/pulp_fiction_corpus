#!/usr/bin/env python3
"""Compare the assembly rules now in the code with the candidate files on disk, issue by issue, without writing
anything (rules 2.14 regression, 4 November 2026).

For every issue given, the pages are read, analysed and assembled in memory (the "rules" variant, the current code)
and set against data/assembly_v2/rules/<issue>/articles.json (the candidate the last upgrade wrote). Reported per issue:
records by type before and after, story records and story words before and after, the records the new rules split
out (paratext family, title, words), the story records whose text changed (title, words before and after, the first
words that left or arrived), and the author strings the by-line guard now rejects. Written as Markdown and JSON.

    python3 scripts/compare_assembly.py --issues-file ids.txt [--out pilot_export/compare-2.14.md]
    python3 scripts/compare_assembly.py --pilot --double-read      # the 10 pilot issues and the 30 double-read sample issues
"""
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


def compare_issue(iid, cfg_issue):
    pages = S.load_pages(iid)
    if not pages:
        return {"issue": iid, "error": "no layout pages"}
    old_path = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "articles.json")
    if not os.path.exists(old_path):
        return {"issue": iid, "error": "no candidate on disk"}
    old = json.load(open(old_path, encoding="utf-8"))
    A = S.analyse(pages, force_scan=S.toc_page_override(iid), magazine=(cfg_issue or {}).get("magazine", ""))
    A["magazine"] = (cfg_issue or {}).get("magazine", "")
    kind, book = S.issue_kind(iid, cfg_issue)
    new = S.assemble(iid, pages, A, "rules", None, kind=kind, book=book)
    ob, os_, ow = summarise(old)
    nb, ns, nw = summarise(new)
    split = [{"family": r.get("paratext"), "ad_class": r.get("ad_class"), "title": r.get("title"), "words": len((r.get("text") or "").split()),
              "pages": r.get("pages")} for r in new["articles"] if r.get("paratext")]
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
    return {"issue": iid, "rules_before": old.get("backend"), "rules_after": S.VERSION,
            "types_before": ob, "types_after": nb, "story_words_before": ow, "story_words_after": nw,
            "stories_before": len(os_), "stories_after": len(ns), "split_out": split, "changed_stories": changed, "notes": notes,
            "rejected_bylines": rejected}


def report(rows):
    L = [f"# Assembly comparison — rules {S.VERSION} in the code against the candidates on disk ({time.strftime('%Y-%m-%d %H:%M')})", "",
         "| issue | stories before → after | story words before → after | split out (paratext) | story records changed | notes |",
         "|---|---|---|---|---|---|"]
    tot = {"split": 0, "changed": 0, "wb": 0, "wa": 0, "sb": 0, "sa": 0}
    for r in rows:
        if r.get("error"):
            L.append(f"| {r['issue']} | — | — | {r['error']} | | |")
            continue
        L.append(f"| {r['issue']} | {r['stories_before']} → {r['stories_after']} | {r['story_words_before']:,} → {r['story_words_after']:,} | "
                 f"{len(r['split_out'])} | {len(r['changed_stories'])} | {r['notes']} |")
        tot["split"] += len(r["split_out"]); tot["changed"] += len(r["changed_stories"])
        tot["wb"] += r["story_words_before"]; tot["wa"] += r["story_words_after"]; tot["sb"] += r["stories_before"]; tot["sa"] += r["stories_after"]
    L += ["", f"Totals: stories {tot['sb']} → {tot['sa']}; story words {tot['wb']:,} → {tot['wa']:,} ({tot['wb'] - tot['wa']:,} words left the story "
          f"records); {tot['split']} paratext records split out; {tot['changed']} story records changed.", ""]
    L += ["## Split out", "", "| issue | family | class | words | pages | title |", "|---|---|---|---|---|---|"]
    for r in rows:
        for x in r.get("split_out", []):
            L.append(f"| {r['issue']} | {x['family']} | {x['ad_class']} | {x['words']} | {x['pages']} | {(x['title'] or '').replace('|', '/')[:90]} |")
    L += ["", "## Story records whose text changed", "", "| issue | title | change | words before → after | at word | left | arrived |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        for c in r.get("changed_stories", []):
            L.append(f"| {r['issue']} | {(c['title'] or '').replace('|', '/')[:60]} | {c['change']} | {c.get('words_before', '')} → {c.get('words_after', '')} | "
                     f"{c.get('at_word', '')} | {(c.get('left') or '').replace('|', '/')} | {(c.get('arrived') or '').replace('|', '/')} |")
    L += ["", "## By-lines the guard rejects", ""] + [f"- {r['issue']}: {', '.join(r['rejected_bylines'])}" for r in rows if r.get("rejected_bylines")]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issues-file")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--double-read", action="store_true")
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
        rows.append(compare_issue(iid, cfg.get(iid) or pilot.get(iid)))
        r = rows[-1]
        print(f"[{n}/{len(ids)}] {iid}: " + (r["error"] if r.get("error") else
              f"stories {r['stories_before']}→{r['stories_after']}, words {r['story_words_before']}→{r['story_words_after']}, split {len(r['split_out'])}, changed {len(r['changed_stories'])}")
              + f" ({time.time() - t0:.0f}s)")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(report(rows))
    json.dump(rows, open(args.out[:-3] + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"written {os.path.relpath(args.out, ROOT)}")


if __name__ == "__main__":
    main()
