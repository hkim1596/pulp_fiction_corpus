#!/usr/bin/env python3
"""Re-assemble the items of kind "book" (config/corpus_kinds.json) in the assembler's book mode (pipeline/s08_book.py):
one story record for a novel with its chapters, one record per listed piece for a collection, the front matter and the
publisher's catalogue set apart. Then each book is published to the site (pipeline/s13_publish.py). A book's annotation
log (a person's corrections made on the chapter-split records) is moved to data/assembly_archive/<stamp>_books/
annotations/ with --archive-annotations, since the publisher holds an issue with a log and the corrections were made
against records that no longer exist; nothing is deleted.

    python3 scripts/reassemble_books.py [--ids a,b,c] [--archive-annotations] [--no-publish] [--workers 4]

Writes a line per book to data/corpus/p52-2026-10-16/reassemble_books.log and a summary at the end.
"""
import argparse
import json
import multiprocessing as mp
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))


def one(args):
    iid, meta = args
    import s08_assemble_rules as rules
    try:
        outs = rules.run_issue(iid, dict(meta, kind="book"), log=lambda s: None)
    except Exception as e:
        return {"issue": iid, "status": "error", "error": repr(e)[:300]}
    if not outs:
        return {"issue": iid, "status": "no pages"}
    o = outs["rules"]
    return {"issue": iid, "status": "ok", "book": o.get("book"), "records": len(o["articles"]),
            "stories": sum(1 for r in o["articles"] if r["type"] == "story"),
            "unassigned": o["checks"]["unassigned_regions"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma-separated ids (default: every item of kind book)")
    ap.add_argument("--archive-annotations", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    from corpus_lib import corpus_config, corpus_kinds, all_states
    kinds = corpus_kinds()
    cfg = {i["id"]: i for i in corpus_config()["issues"]}
    ids = [i.strip() for i in args.ids.split(",")] if args.ids else sorted(i for i, k in kinds.items() if k == "book")
    states = all_states()
    ids = [i for i in ids if i in cfg and "assembled" in states.get(i, {}).get("stages", {})]
    stamp = time.strftime("%Y-%m-%d_%H%M")
    arch = os.path.join(ROOT, "data", "assembly_archive", f"{stamp}_books")
    log_path = os.path.join(ROOT, "data", "corpus", "p52-2026-10-16", "reassemble_books.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    print(f"{len(ids)} books to re-assemble")
    moved = 0
    if args.archive_annotations:
        for iid in ids:
            p = os.path.join(ROOT, "data", "annotations", f"{iid}.jsonl")
            if os.path.exists(p):
                os.makedirs(os.path.join(arch, "annotations"), exist_ok=True)
                live = os.path.join(ROOT, "data", "articles", iid, "articles.json")
                if os.path.exists(live):
                    os.makedirs(os.path.join(arch, "articles", iid), exist_ok=True)
                    shutil.copy(live, os.path.join(arch, "articles", iid, "articles.json"))
                shutil.move(p, os.path.join(arch, "annotations", f"{iid}.jsonl"))
                moved += 1
        print(f"{moved} annotation logs moved to {os.path.relpath(arch, ROOT)}/annotations/ (with the old live records)")
    results = []
    with mp.get_context("fork").Pool(max(1, args.workers)) as pool, open(log_path, "a", encoding="utf-8") as log:
        for r in pool.imap_unordered(one, [(i, cfg[i]) for i in ids]):
            results.append(r)
            log.write(json.dumps(r, ensure_ascii=False) + "\n"); log.flush()
            if r["status"] == "ok":
                b = r.get("book") or {}
                print(f"  {r['issue']}: {b.get('kind')} — {r['stories']} stories of {r['records']} records; {b.get('merged')} joined; unassigned {r['unassigned']}")
            else:
                print(f"  {r['issue']}: {r['status']} {r.get('error', '')}")
    ok = [r for r in results if r["status"] == "ok"]
    if ok and not args.no_publish:
        import s13_publish as s13
        from corpus_lib import settings
        cfgp = settings().get("publish", {})
        held = published = 0
        for r in ok:
            what = s13.publish_issue(r["issue"], cfgp.get("prefer", "rules_flagged"), float(cfgp.get("look_below", 0.9)), True)[0]
            if what == "published":
                published += 1
            elif what.startswith("held"):
                held += 1
        print(f"published {published}, held {held} (an annotation log still present)")
    print(f"done: {len(ok)} ok, {len(results) - len(ok)} not; log {os.path.relpath(log_path, ROOT)}")


if __name__ == "__main__":
    main()
