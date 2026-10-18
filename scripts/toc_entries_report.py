#!/usr/bin/env python3
"""Read-only, entry by entry: the contents reading each issue was assembled with (data/assembly_v2/rules/<id>/
analysis.json, the toc list) against the reading of the contents parser in this working tree, over every
assembled corpus issue on this machine. Entries are matched by printed page number and classified:

  same              title and author equal (after normalisation)
  author as title   the new title is the old author and the new entry has no author (or the other way round)
  title changed     same author, another title
  both changed      another title and another author
  only installed    the installed reading has an entry at this page that the new one has not
  only new          the new reading has an entry the installed one has not

Writes data/corpus/p52-2026-10-16/toc_entries_report.json: the totals, one line per issue (counts, the
version it was assembled with) and every changed entry; prints the totals and the issues whose reading
changes. Reads no model, no GPU; writes nothing else. (10e, 18 October 2026.)

    python3 scripts/toc_entries_report.py --workers 16
    python3 scripts/toc_entries_report.py --limit 200
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import s08_assemble_rules as rules  # noqa: E402
import toc_folio as T  # noqa: E402

rules.ROOT = ROOT                # the layout pages are read from this tree's data/layout


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def classify(old, new):
    if norm(old["title"]) == norm(new["title"]) and norm(old["author"]) == norm(new["author"]):
        return "same"
    if (norm(new["title"]) == norm(old["author"]) and not new["author"]) or (norm(old["title"]) == norm(new["author"]) and not old["author"]):
        return "author as title"
    if norm(old["author"]) == norm(new["author"]):
        return "title changed"
    return "both changed"


def compare(old, new):
    by_old, by_new = defaultdict(list), defaultdict(list)
    for e in old:
        by_old[e.get("page")].append(e)
    for e in new:
        by_new[e.get("page")].append(e)
    rows = []
    for page in sorted(set(by_old) | set(by_new), key=lambda x: (x is None, x)):
        o, n = by_old.get(page, []), by_new.get(page, [])
        for i in range(max(len(o), len(n))):
            eo, en = (o[i] if i < len(o) else None), (n[i] if i < len(n) else None)
            kind = classify(eo, en) if eo and en else ("only installed" if eo else "only new")
            rows.append({"page": page, "kind": kind, "installed": eo, "new": en})
    return rows


def one(iid):
    try:
        p = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "analysis.json")
        a = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "articles.json")
        if not os.path.exists(p):
            return {"id": iid, "error": "no analysis.json"}
        installed = json.load(open(p, encoding="utf-8"))
        backend = None
        if os.path.exists(a):
            with open(a, encoding="utf-8") as f:
                head = f.read(400)
            m = re.search(r'"backend":\s*"([^"]+)"', head)
            backend = m.group(1) if m else None
        pages = rules.load_pages(iid)
        if not pages:
            return {"id": iid, "error": "no layout pages"}
        A = rules.analyse(pages)
        keep = ("title", "author", "page", "type")
        old = [{k: e.get(k) for k in keep} for e in installed.get("toc") or []]
        new = [{k: e.get(k) for k in keep} for e in A["toc"]]
        rows = compare(old, new)
        counts = Counter(r["kind"] for r in rows)
        return {"id": iid, "backend": backend, "installed_entries": len(old), "new_entries": len(new),
                "counts": dict(counts), "changed": [r for r in rows if r["kind"] != "same"]}
    except Exception as ex:
        return {"id": iid, "error": repr(ex)[:300]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    ids = sorted(os.path.basename(d) for d in glob.glob(os.path.join(ROOT, "data", "assembly_v2", "rules", "*")) if os.path.isdir(d))
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
    kinds = Counter()
    for r in ok:
        kinds.update(r["counts"])
    changed = [r for r in ok if r["changed"]]
    by_backend = Counter((r["backend"] or "?") for r in ok)
    changed_by_backend = Counter((r["backend"] or "?") for r in changed)
    author_as_title = [r for r in ok if r["counts"].get("author as title")]
    tot = {"issues": len(rows), "errors": len(rows) - len(ok), "issues_by_backend": dict(by_backend),
           "entries_installed": sum(r["installed_entries"] for r in ok), "entries_new": sum(r["new_entries"] for r in ok),
           "entries_by_kind": dict(kinds), "issues_whose_reading_changes": len(changed),
           "issues_whose_reading_changes_by_backend": dict(changed_by_backend),
           "issues_with_author_as_title": len(author_as_title),
           "seconds": round(time.time() - t0), "toc_folio_version": T.VERSION, "rules_version": rules.VERSION}
    out = os.path.join(ROOT, "data", "corpus", "p52-2026-10-16", "toc_entries_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    json.dump({"totals": tot, "issues": rows}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, out)
    print(json.dumps(tot, indent=1))
    print("issues whose contents reading changes:", len(changed))
    for r in sorted(changed, key=lambda r: -len(r["changed"]))[:40]:
        print(f"  {r['id']}: {len(r['changed'])} entries ({r['backend']})", dict(Counter(x['kind'] for x in r['changed'])))
    print("written", out)


if __name__ == "__main__":
    main()
