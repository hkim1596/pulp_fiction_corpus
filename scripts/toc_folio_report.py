#!/usr/bin/env python3
"""Read-only comparison of the contents-page and page-number reading of rules 2.6 with toc_folio (2.7),
over every assembled corpus issue on this machine.

For each issue: the 2.6 reading as data/assembly_v2/rules/<id>/analysis.json records it (contents page,
entries, offset), and toc_folio's reading of the same layout pages (data/layout/<id>). Writes
data/corpus/toc_folio_report.json (one line per issue in "issues", and the totals) and prints the totals.
Writes nothing else; reads no model, no GPU.

    python3 scripts/toc_folio_report.py                 # every issue with layout pages
    python3 scripts/toc_folio_report.py --limit 200     # a quick look
    python3 scripts/toc_folio_report.py --workers 16
"""
import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import toc_folio as T  # noqa: E402

CONFIRMED = ("page number, confirmed", "page number, moved", "title and by-line")


def one(iid):
    try:
        pages = T.load_pages(os.path.join(ROOT, "data", "layout", iid))
        if not pages:
            return {"id": iid, "error": "no layout pages"}
        R = T.analyse_issue(pages)
        old = {}
        p = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "analysis.json")
        if os.path.exists(p):
            a = json.load(open(p, encoding="utf-8"))
            old = {"toc_page": a.get("toc_page"), "entries": len(a.get("toc") or []), "offset": a.get("offset"),
                   "placed": sum(1 for e in a.get("toc") or [] if e.get("scan"))}
        F = R["folio"]
        segs = sorted(F["segments"], key=lambda g: -g["support"])
        es = R["entries"]
        return {"id": iid, "pages": len(pages),
                "v26": old,
                "v27": {"toc_pages": R["toc_pages"], "toc_kind": R["toc_kind"], "entries": len(es),
                        "confirmed": sum(1 for e in es if e["placed_by"].startswith(CONFIRMED)),
                        "by_title": sum(1 for e in es if e["placed_by"].startswith("title and by-line")),
                        "number_only": sum(1 for e in es if e["placed_by"].startswith("page number only")),
                        "unplaced": len(R.get("unplaced") or []), "chapters": len(R.get("chapters") or []),
                        "offset": segs[0]["offset"] if segs else None, "segments": len(F["segments"]),
                        "misplaced_leaves": len(F["misplaced"]), "numbered_pages": sum(1 for v in F["printed"].values() if v),
                        "pages_with_number_read": F["pages_with_number"], "support": F["support"]}}
    except Exception as ex:
        return {"id": iid, "error": repr(ex)[:300]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    ids = sorted(os.path.basename(d) for d in glob.glob(os.path.join(ROOT, "data", "layout", "*")) if os.path.isdir(d))
    if a.limit:
        ids = ids[:a.limit]
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(a.workers) as ex:
        for k, r in enumerate(ex.map(one, ids, chunksize=8)):
            rows.append(r)
            if (k + 1) % 500 == 0:
                print(f"  {k + 1} of {len(ids)} ({time.time() - t0:.0f} s)", flush=True)
    ok = [r for r in rows if "error" not in r]
    tot = {
        "issues": len(rows), "errors": len(rows) - len(ok),
        "contents_found_v26": sum(1 for r in ok if (r["v26"] or {}).get("toc_page")),
        "contents_with_entries_v26": sum(1 for r in ok if (r["v26"] or {}).get("entries")),
        "contents_found_v27": sum(1 for r in ok if r["v27"]["toc_pages"]),
        "contents_with_entries_v27": sum(1 for r in ok if r["v27"]["entries"]),
        "entries_v26": sum((r["v26"] or {}).get("entries", 0) for r in ok),
        "entries_v27": sum(r["v27"]["entries"] for r in ok),
        "confirmed_on_page_v27": sum(r["v27"]["confirmed"] for r in ok),
        "found_by_title_v27": sum(r["v27"]["by_title"] for r in ok),
        "number_only_v27": sum(r["v27"]["number_only"] for r in ok),
        "chapter_lists_v27": sum(1 for r in ok if r["v27"]["toc_kind"] == "chapters"),
        "offset_changed": sum(1 for r in ok if r["v26"] and r["v26"].get("offset") is not None and r["v27"]["offset"] is not None
                              and r["v26"]["offset"] != r["v27"]["offset"]),
        "issues_with_inserted_or_missing_leaves": sum(1 for r in ok if r["v27"]["segments"] > 1),
        "leaves_out_of_order": sum(r["v27"]["misplaced_leaves"] for r in ok),
        "seconds": round(time.time() - t0),
        "toc_folio_version": T.VERSION,
    }
    out = os.path.join(ROOT, "data", "corpus", "toc_folio_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    json.dump({"totals": tot, "issues": rows}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, out)
    print(json.dumps(tot, indent=1))
    print("written", out)


if __name__ == "__main__":
    main()
