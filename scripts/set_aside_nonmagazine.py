#!/usr/bin/env python3
"""Apply a person's decisions on the non-magazine candidates (scripts/nonmagazine_candidates.py): the items marked for
exclusion go into config/corpus_set_aside.json with the class "not a magazine issue", so that no stage touches them,
the counts report them apart and corpus 1.0's manifest lists them under their reason. The files on disk are kept.

Decision 1 of 19 October 2026 (Sujin Kang): "the corpus is pulp magazines" — standalone novels and collections,
single stories, dime-novel and nickel-weekly numbers, comics, indexes and portfolios, fanzines and items outside the
period are excluded from corpus 1.0 and their verification-sample slots redrawn (scripts/draw_verification_sample.py
--replace, which this script runs last unless --no-redraw).

The input is the review CSV with its "decision" column filled: x (or exclude) = exclude, k (or keep) = keep, blank =
not decided. With --accept-proposed, rows the rule proposed EXCLUDE are taken as excluded unless the decision says k,
so a reader can decide class by class and mark only the exceptions. Rows proposed LOOK or KEEP are excluded only
when marked x.

    python3 scripts/set_aside_nonmagazine.py --csv <reviewed.csv> --decided-by "Sujin Kang" --decided 2026-10-20 [--accept-proposed] [--dry-run]

Safe to run again: ids already set aside are skipped. Refuses an id that is not in the selection.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

CLASS = "not a magazine issue"
WORDS = {"book": "a standalone book (a novel or a collection)", "single story": "a single story or serial extracted from a magazine",
         "dime novel": "a dime-novel or nickel-weekly number", "comic": "a comic", "index or portfolio": "an index, checklist or portfolio",
         "outside the period": "a reprint, fanzine or magazine from after the period", "not a fiction magazine": "a magazine that is not a fiction magazine"}


def read_decisions(path, accept_proposed=False):
    """(excluded rows, kept rows, undecided rows) from the review CSV. Refuses a file without the id and decision columns."""
    exc, kept, undecided = [], [], []
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8-sig")                 # utf-8-sig: a spreadsheet's export may start with a BOM
    except UnicodeDecodeError:
        text = raw.decode("mac_roman")                 # Excel's plain "CSV" on a Mac; only id and decision matter, and ids are ASCII
    import io
    with io.StringIO(text, newline="") as f:
        rd = csv.DictReader(f)
        if not rd.fieldnames or "id" not in rd.fieldnames or "decision" not in rd.fieldnames:
            raise SystemExit(f"{path}: not the review table (columns id and decision missing; found {rd.fieldnames})")
        for row in rd:
            d = (row.get("decision") or "").strip().lower()
            if d in ("x", "exclude", "excluded", "e", "yes"):
                exc.append(row)
            elif d in ("k", "keep", "kept", "no"):
                kept.append(row)
            elif accept_proposed and (row.get("proposed") or "").strip().upper() == "EXCLUDE":
                exc.append(row)
            else:
                undecided.append(row)
    return exc, kept, undecided


def entry_for(row, decided_by, decided):
    cls = (row.get("class") or "").strip()
    reason = f"{CLASS}: {WORDS.get(cls, cls or 'as reviewed')}"
    note = (row.get("note") or "").strip()
    e = {"id": row["id"], "ia_identifier": row.get("ia_identifier") or None, "class": CLASS, "reason": reason,
         "kind": cls or None, "signals": (row.get("signals") or "").split(), "decided_by": decided_by, "decided": decided}
    if note:
        e["note"] = note[:500]
    return e


def apply(rows, decided_by, decided, path=None, selection=None, dry_run=False):
    """Add the entries to config/corpus_set_aside.json. Returns (added, skipped as already set aside)."""
    path = path or os.path.join(ROOT, "config", "corpus_set_aside.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        d = {"_comment": "Issues set aside after the approval: they stay in the approved list (its fingerprint is untouched), no stage touches them, the counts report them apart.",
             "issues": []}
    have = {e["id"] for e in d.get("issues", [])}
    if selection is not None:
        bad = [r["id"] for r in rows if r["id"] not in selection]
        if bad:
            raise SystemExit(f"{len(bad)} ids are not in the selection (config/corpus_issues.json): {', '.join(bad[:5])}; nothing written")
    added, skipped = [], []
    for r in rows:
        if r["id"] in have:
            skipped.append(r["id"]); continue
        added.append(entry_for(r, decided_by, decided)); have.add(r["id"])
    if added and not dry_run:
        d.setdefault("issues", []).extend(added)
        d["written"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        d.setdefault("decisions", []).append({"decided_by": decided_by, "decided": decided, "class": CLASS, "added": len(added),
                                              "source": "scripts/nonmagazine_candidates.py review table, applied by scripts/set_aside_nonmagazine.py"})
        tmp = path + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    return added, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="the review table with its decision column filled")
    ap.add_argument("--decided-by", required=True)
    ap.add_argument("--decided", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--accept-proposed", action="store_true", help="rows proposed EXCLUDE count as excluded unless the decision says k")
    ap.add_argument("--no-redraw", action="store_true", help="do not redraw the verification sample's slots afterwards")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from corpus_lib import corpus_config
    selection = {i["id"] for i in corpus_config()["issues"]}
    exc, kept, undecided = read_decisions(args.csv, args.accept_proposed)
    print(f"{len(exc)} to exclude, {len(kept)} kept, {len(undecided)} undecided rows" + (" (dry run)" if args.dry_run else ""))
    from collections import Counter
    print("to exclude by class: " + ", ".join(f"{k} {v}" for k, v in Counter((r.get('class') or '?') for r in exc).most_common()))
    added, skipped = apply(exc, args.decided_by, args.decided, selection=selection, dry_run=args.dry_run)
    print(f"{len(added)} set aside as '{CLASS}', {len(skipped)} were already set aside" + ("" if args.dry_run else "; config/corpus_set_aside.json written"))
    in_sample = []
    try:
        S = json.load(open(os.path.join(ROOT, "config", "verification_sample.json"), encoding="utf-8"))
        ids = {e["id"] for e in added}
        in_sample = [i for i in S["issues"] if i["id"] in ids]
    except (OSError, ValueError):
        pass
    print(f"{len(in_sample)} of them are in the verification sample" + (": " + ", ".join(f"#{i['order']} {i['id']}" for i in in_sample[:40]) if in_sample else ""))
    if in_sample and not args.no_redraw and not args.dry_run:
        print("redrawing their slots (scripts/draw_verification_sample.py --replace) ...")
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "draw_verification_sample.py"), "--replace"], check=True)


if __name__ == "__main__":
    main()
