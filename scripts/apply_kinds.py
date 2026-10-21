#!/usr/bin/env python3
"""Apply the decisions clicked on /review/nonmagazine (data/review/nonmagazine_review.jsonl, joined to the candidate
table data/corpus/p52-2026-10-16/nonmagazine_candidates.json): the kinds go to config/corpus_kinds.json (book), the
reasons to config/corpus_set_aside.json (excerpt of a magazine, not English, not a text, comic, outside the period),
the verification sample's slots held by books or set-aside items are redrawn (scripts/draw_verification_sample.py
--replace), and the books are re-assembled in the book mode (scripts/reassemble_books.py) unless --no-reassemble.

Decision 1 of 19 October 2026 as revised by Sujin Kang on 21 October: items are sorted by kind, not thrown out.
A magazine issue is the default kind and is not written. With --accept-proposed, an undecided row takes the rule's
suggestion: class book or dime novel -> book; index or portfolio -> not a text; comic -> comic; outside the period ->
outside the period; single story stays undecided (an excerpt is a duplicate only when its issue is in the corpus:
decide it by hand); not a fiction magazine -> magazine. "?" stays undecided.

    python3 scripts/apply_kinds.py --decided-by "Sujin Kang" --decided 2026-10-21 [--accept-proposed] [--dry-run] [--no-reassemble]
    python3 scripts/apply_kinds.py --migrate            # the 19 October clicks (x/k) turned into kinds, appended to the log

Safe to run again: an item already of that kind or already set aside is skipped; a kind can change (the file holds the
latest). Refuses an id that is not in the selection.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

LOG = os.path.join(ROOT, "data", "review", "nonmagazine_review.jsonl")
TABLE = os.path.join(ROOT, "data", "corpus", "p52-2026-10-16", "nonmagazine_candidates.json")
KINDS = ("magazine", "book")
REASONS = {"excerpt": ("excerpt of a magazine", "pages cut out of a magazine issue; a duplicate of the issue when it is in the corpus"),
           "not_english": ("not English", "not in English"),
           "not_text": ("not a text", "an index, a checklist, a cover gallery or another item without reading text"),
           "comic": ("comic", "a comic"),
           "outside": ("outside the period", "a reprint, fanzine or magazine from after the period")}
PROPOSED = {"book": "book", "dime novel": "book", "index or portfolio": "not_text", "comic": "comic", "outside the period": "outside",
            "not a fiction magazine": "magazine"}


def read_log(path=LOG):
    rows = []
    try:
        for line in open(path, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return rows


def latest(rows, legacy=False):
    out = {}
    for r in rows:
        d = r.get("decision")
        if r.get("id") and (d in KINDS or d in REASONS or d == "?" or (legacy and d in ("x", "k"))):
            out[r["id"]] = r
    return out


def migrate(user="sujin", table_path=TABLE, log_path=LOG, now=None):
    """The 19 October exclude/keep clicks as kinds: x on a 'Serials' compilation or with 'excerpt' in the note ->
    excerpt; x on a book or dime novel -> book; k on a book or dime novel -> book (Sujin kept the paperbacks and the
    nickel weekly as books on 19 October); other x/k -> left as they are (undecided). Appends the lines; returns them."""
    T = json.load(open(table_path, encoding="utf-8"))
    cls = {it["id"]: it for it in T.get("items", [])}
    rows = read_log(log_path)
    done = latest(rows)
    lines = []
    for iid, r in latest(rows, legacy=True).items():
        if r["decision"] not in ("x", "k") or iid in done:
            continue
        it = cls.get(iid, {})
        note = (r.get("note") or "").lower()
        name = (it.get("magazine") or "") + " " + (it.get("ia_title") or "")
        if r["decision"] == "x" and ("excerpt" in note or "serials" in name.lower()):
            new = "excerpt"
        elif it.get("class") in ("book", "dime novel"):
            new = "book"
        else:
            continue
        lines.append({"ts": now or time.strftime("%Y-%m-%dT%H:%M:%S"), "user": r.get("user") or user, "id": iid, "decision": new,
                      "note": (r.get("note") or "") + " [migrated from the 19 October click: " + r["decision"] + "]"})
    if lines:
        with open(log_path, "a", encoding="utf-8") as f:
            for l in lines:
                f.write(json.dumps(l, ensure_ascii=False) + "\n")
    return lines


def decide(table_path=TABLE, log_path=LOG, accept_proposed=False):
    """(kinds: id -> kind, reasons: id -> reason key, undecided ids, rows by id) over the table's listed items."""
    T = json.load(open(table_path, encoding="utf-8"))
    D = latest(read_log(log_path))
    kinds, reasons, undecided, rows = {}, {}, [], {}
    for it in T.get("items", []):
        if it.get("proposed") == "KEEP" and not it.get("in_sample"):
            continue
        rows[it["id"]] = it
        d = (D.get(it["id"]) or {}).get("decision")
        if d is None and accept_proposed and it.get("proposed") != "KEEP":
            d = PROPOSED.get(it.get("class") or "")
        if d in KINDS:
            kinds[it["id"]] = d
        elif d in REASONS:
            reasons[it["id"]] = d
        else:
            undecided.append(it["id"])
    return kinds, reasons, undecided, rows, D


def write_kinds(kinds, rows, D, decided_by, decided, path=None, dry_run=False):
    path = path or os.path.join(ROOT, "config", "corpus_kinds.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        d = {"_comment": "Items that are not magazine issues but stay in the data, by kind (Sujin Kang, 21 October 2026): 'book' — a standalone "
                         "book, a story collection, a paperback, a dime-novel or nickel-weekly number with one novel. An item not listed is a "
                         "magazine issue. The protocol's corpus is the magazine issues; the books are assembled as books (pipeline/s08_book.py) "
                         "and are the other side of a reuse match. Written by scripts/apply_kinds.py from the clicks on /review/nonmagazine.",
             "kinds": {}}
    have = d.setdefault("kinds", {})
    added, changed = [], []
    for iid, k in sorted(kinds.items()):
        if k == "magazine":
            if iid in have:
                del have[iid]; changed.append(iid)
            continue
        it = rows.get(iid, {})
        e = {"kind": k, "ia_identifier": it.get("ia_identifier"), "magazine": it.get("magazine"), "class_by_rule": it.get("class"),
             "decided_by": decided_by, "decided": decided}
        note = (D.get(iid) or {}).get("note")
        if note and note != "marked with the view":
            e["note"] = note[:500]
        if iid in have and have[iid].get("kind") == k:
            continue
        (changed if iid in have else added).append(iid)
        have[iid] = e
    if (added or changed) and not dry_run:
        d["written"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = path + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    return added, changed


def write_set_aside(reasons, rows, D, decided_by, decided, path=None, selection=None, dry_run=False):
    path = path or os.path.join(ROOT, "config", "corpus_set_aside.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        d = {"_comment": "Issues set aside after the approval: they stay in the approved list, no stage touches them, the counts report them apart.", "issues": []}
    have = {e["id"] for e in d.get("issues", [])}
    if selection is not None:
        bad = [i for i in reasons if i not in selection]
        if bad:
            raise SystemExit(f"{len(bad)} ids are not in the selection: {', '.join(bad[:5])}; nothing written")
    added, skipped = [], []
    for iid, key in sorted(reasons.items()):
        if iid in have:
            skipped.append(iid); continue
        cls, words = REASONS[key]
        it = rows.get(iid, {})
        e = {"id": iid, "ia_identifier": it.get("ia_identifier"), "class": cls, "reason": f"{cls}: {words}", "kind_by_rule": it.get("class"),
             "signals": it.get("signals") or [], "decided_by": decided_by, "decided": decided}
        note = (D.get(iid) or {}).get("note")
        if note and note != "marked with the view":
            e["note"] = note[:500]
        added.append(e); have.add(iid)
    if added and not dry_run:
        d.setdefault("issues", []).extend(added)
        d["written"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        d.setdefault("decisions", []).append({"decided_by": decided_by, "decided": decided, "added": len(added), "by_class": dict(Counter(e["class"] for e in added)),
                                              "source": "/review/nonmagazine clicks, applied by scripts/apply_kinds.py"})
        tmp = path + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    return added, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decided-by", default="Sujin Kang")
    ap.add_argument("--decided", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--accept-proposed", action="store_true")
    ap.add_argument("--migrate", action="store_true", help="turn the 19 October exclude/keep clicks into kinds (appended to the log) and stop")
    ap.add_argument("--no-redraw", action="store_true")
    ap.add_argument("--no-reassemble", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.migrate:
        lines = migrate()
        print(f"{len(lines)} earlier clicks migrated: " + ", ".join(f"{k} {v}" for k, v in Counter(l['decision'] for l in lines).items()))
        return
    from corpus_lib import corpus_config
    selection = {i["id"] for i in corpus_config()["issues"]}
    kinds, reasons, undecided, rows, D = decide(accept_proposed=args.accept_proposed)
    print(f"{len(rows)} listed items: kinds {dict(Counter(kinds.values()))}, set aside {dict(Counter(REASONS[r][0] for r in reasons.values()))}, "
          f"undecided {len(undecided)}" + (" (dry run)" if args.dry_run else ""))
    if undecided:
        print("  undecided (first 12): " + ", ".join(undecided[:12]))
    added, changed = write_kinds(kinds, rows, D, args.decided_by, args.decided, dry_run=args.dry_run)
    print(f"kinds: {len(added)} added, {len(changed)} changed" + ("" if args.dry_run else "; config/corpus_kinds.json written"))
    a2, skipped = write_set_aside(reasons, rows, D, args.decided_by, args.decided, selection=selection, dry_run=args.dry_run)
    print(f"set aside: {len(a2)} added, {len(skipped)} were already set aside" + ("" if args.dry_run else "; config/corpus_set_aside.json written"))
    if args.dry_run:
        return
    try:
        S = json.load(open(os.path.join(ROOT, "config", "verification_sample.json"), encoding="utf-8"))
        leaving = [i for i in S["issues"] if kinds.get(i["id"]) == "book" or i["id"] in reasons]
    except (OSError, ValueError):
        leaving = []
    print(f"{len(leaving)} sample slots to redraw" + (": " + ", ".join(f"#{i['order']}" for i in leaving[:40]) if leaving else ""))
    if leaving and not args.no_redraw:
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "draw_verification_sample.py"), "--replace"], check=True)
    books = [i for i, k in kinds.items() if k == "book"]
    if books and not args.no_reassemble:
        print(f"re-assembling {len(books)} books in the book mode (scripts/reassemble_books.py) ...")
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "reassemble_books.py"), "--archive-annotations"], check=True)


if __name__ == "__main__":
    main()
