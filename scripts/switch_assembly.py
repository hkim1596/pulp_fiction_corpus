#!/usr/bin/env python3
"""Make an assembly-v2 candidate the live assembly, archiving what it
replaces (decision of 2026-09-02: "archive corrections and start clean;
keep comparing human-touched correction and automated correction").

What it does, per issue:
  1. moves data/articles/<issue>/articles.json (the live machine assembly)
     to data/assembly_archive/<stamp>/articles/<issue>/articles.json
  2. moves data/annotations/<issue>.jsonl (every human action so far) to
     data/assembly_archive/<stamp>/annotations/<issue>.jsonl — the
     workbench starts clean; the archive stays the yardstick
     (pipeline/s09_assembly_eval.py --yardstick data/assembly_archive/<stamp>)
  3. copies data/assembly_v2/<variant>/<issue>/articles.json into place
  4. rebuilds data/articles/index.json

Nothing is deleted. To undo: copy the archived files back.

    python3 scripts/switch_assembly.py --variant rules --dry-run
    python3 scripts/switch_assembly.py --variant rules            (all issues)
    python3 scripts/switch_assembly.py --variant rules --issue ast_1930_01

--refresh (added 2026-09-03): a new run of the same rules replaces the live
records WITHOUT touching the annotation logs. Every candidate record takes
the id of the live record with the same scan regions (or, when the regions
moved a little, the best-overlapping live record, Jaccard >= 0.5); records
with no counterpart get fresh ids after the live numbering. A live record
that annotators have touched must keep exactly its regions, else the issue
is refused (pass --force to refresh it anyway and let the replay sort the
moved regions out). The old live file is kept under
data/assembly_archive/<stamp>_refresh/. Use it when the rules gain fields
(ad_class, chapters, author_as_printed …) or fix small things after people
have begun to annotate.

    python3 scripts/switch_assembly.py --variant rules --refresh --dry-run
    python3 scripts/switch_assembly.py --variant rules --refresh
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))


def frag_set(rec):
    return frozenset((f["page"], r) for f in rec.get("fragments", []) for r in f.get("region_ids", []))


def seq_of(aid):
    m = re.search(r"_a(\d+)$", aid or "")
    return int(m.group(1)) if m else 0


def annotated_ids(iid):
    """Every article id an annotation event names (the record acted on, the
    target of a move or a merge)."""
    ids = set()
    p = os.path.join(ROOT, "data", "annotations", f"{iid}.jsonl")
    if not os.path.exists(p):
        return ids
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        for k in ("article_id", "to_id", "into_id"):
            v = e.get(k)
            if v and v != "new":
                ids.add(v)
    return ids


def refresh_map(iid, live, cand, force=False):
    """(new candidate records with live ids, notes, refused reason)."""
    live_by_id = {a["article_id"]: a for a in live.get("articles", [])}
    live_sets = {aid: frag_set(a) for aid, a in live_by_id.items()}
    by_set = {}
    for aid, s in live_sets.items():
        by_set.setdefault(s, aid)
    touched = annotated_ids(iid)
    taken = set()
    out = []
    notes = []
    pending = []
    for c in cand.get("articles", []):
        s = frag_set(c)
        aid = by_set.get(s)
        if aid and aid not in taken:
            taken.add(aid)
            out.append((aid, c, "same regions"))
        else:
            pending.append((c, s))
    # the rest: best overlap with an unmatched live record
    for c, s in pending:
        best, bj = None, 0.0
        for aid, ls in live_sets.items():
            if aid in taken or not ls or not s:
                continue
            j = len(s & ls) / len(s | ls)
            if j > bj:
                best, bj = aid, j
        if best and bj >= 0.5:
            taken.add(best)
            out.append((best, c, f"regions changed (overlap {bj:.2f})"))
            notes.append(f"{best}: regions changed (overlap {bj:.2f}); title {c.get('title')!r}")
        else:
            out.append((None, c, "new record"))
    refused = []
    for aid in sorted(touched):
        if aid not in live_by_id:
            continue                                   # a human-made record (_u###): lives in the annotation log itself
        hit = next((x for x in out if x[0] == aid), None)
        if hit is None:
            refused.append(f"{aid} (annotated) has no counterpart in the new run")
        elif hit[2] != "same regions":
            refused.append(f"{aid} (annotated): {hit[2]}")
    if refused and not force:
        return None, notes, refused
    n = max([seq_of(a) for a in live_by_id] + [0])
    final = []
    for aid, c, why in out:
        c = dict(c)
        if aid is None:
            n += 1
            aid = f"{iid}_a{n:03d}"
            notes.append(f"{aid}: new record; title {c.get('title')!r}")
        c["article_id"] = aid
        final.append(c)
    # the candidate's order (reading order) is kept; ids may now be out of sequence, which is fine
    return final, notes, (refused if force else [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="rules", choices=["rules", "rules_on_model"])
    ap.add_argument("--issue")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="replace the live records, keep ids and annotation logs")
    ap.add_argument("--force", action="store_true", help="with --refresh: go on even when an annotated record changed")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    ids = [args.issue] if args.issue else [i["id"] for i in cfg["issues"]]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if args.refresh:
        return refresh(args, ids, stamp)
    arch = os.path.join(ROOT, "data", "assembly_archive", stamp)
    moves = []
    for iid in ids:
        src = os.path.join(ROOT, "data", "assembly_v2", args.variant, iid, "articles.json")
        if not os.path.exists(src):
            print(f"[switch] {iid}: no {args.variant} assembly (run pipeline/s08_assemble_rules.py first) — skipped")
            continue
        live = os.path.join(ROOT, "data", "articles", iid, "articles.json")
        ann = os.path.join(ROOT, "data", "annotations", f"{iid}.jsonl")
        if os.path.exists(live):
            moves.append(("move", live, os.path.join(arch, "articles", iid, "articles.json")))
        if os.path.exists(ann):
            moves.append(("move", ann, os.path.join(arch, "annotations", f"{iid}.jsonl")))
        moves.append(("copy", src, live))
    for op, a, b in moves:
        print(f"[switch] {op:4s} {os.path.relpath(a, ROOT)} -> {os.path.relpath(b, ROOT)}")
    if args.dry_run:
        print("[switch] dry run: nothing changed")
        return
    for op, a, b in moves:
        os.makedirs(os.path.dirname(b), exist_ok=True)
        if op == "move":
            shutil.move(a, b)
        else:
            shutil.copy2(a, b)
    os.makedirs(arch, exist_ok=True)
    with open(os.path.join(arch, "README.txt"), "w", encoding="utf-8") as f:
        f.write(f"Archived {time.strftime('%Y-%m-%d %H:%M:%S')} by scripts/switch_assembly.py --variant {args.variant}\n"
                f"issues: {', '.join(ids)}\n"
                "articles/ = the machine assembly that was live (s07); annotations/ = every human action on it.\n"
                "The harness scores candidates against this archive: python3 pipeline/s09_assembly_eval.py --all "
                f"--yardstick data/assembly_archive/{stamp}\n"
                "To undo: copy articles/<issue>/articles.json back to data/articles/<issue>/ and annotations/<issue>.jsonl "
                "back to data/annotations/.\n")
    from s07_articles import build_index
    build_index()
    print(f"[switch] done; archive: data/assembly_archive/{stamp}")


def refresh(args, ids, stamp):
    arch = os.path.join(ROOT, "data", "assembly_archive", stamp + "_refresh")
    plan = []
    for iid in ids:
        src = os.path.join(ROOT, "data", "assembly_v2", args.variant, iid, "articles.json")
        live_p = os.path.join(ROOT, "data", "articles", iid, "articles.json")
        if not os.path.exists(src):
            print(f"[refresh] {iid}: no {args.variant} assembly — skipped")
            continue
        if not os.path.exists(live_p):
            print(f"[refresh] {iid}: nothing live — use the plain switch for this issue")
            continue
        live = json.load(open(live_p, encoding="utf-8"))
        cand = json.load(open(src, encoding="utf-8"))
        final, notes, refused = refresh_map(iid, live, cand, force=args.force)
        n_same = sum(1 for a in final or [] if any(frag_set(a) == frag_set(b) and a["article_id"] == b["article_id"] for b in live["articles"]))
        if final is None:
            print(f"[refresh] {iid}: REFUSED — an annotated record would change:")
            for r in refused:
                print(f"           {r}")
            print("           (pass --force to refresh anyway; the replay re-places moved regions by key)")
            continue
        print(f"[refresh] {iid}: {len(live['articles'])} live -> {len(final)} records; {n_same} unchanged, "
              f"{len(notes)} changed or new" + (f"; forced past {len(refused)} annotated change(s)" if refused else ""))
        for nt in notes[:12]:
            print(f"           {nt}")
        if len(notes) > 12:
            print(f"           … and {len(notes) - 12} more")
        plan.append((iid, live_p, live, cand, final))
    if args.dry_run:
        print("[refresh] dry run: nothing changed")
        return
    if not plan:
        print("[refresh] nothing to do")
        return
    for iid, live_p, live, cand, final in plan:
        keep = os.path.join(arch, "articles", iid, "articles.json")
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        shutil.copy2(live_p, keep)
        out = dict(cand)
        out["articles"] = final
        out["refreshed"] = {"from": live.get("backend"), "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kept_copy": os.path.relpath(keep, ROOT)}
        json.dump(out, open(live_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.makedirs(arch, exist_ok=True)
    with open(os.path.join(arch, "README.txt"), "w", encoding="utf-8") as f:
        f.write(f"Kept {time.strftime('%Y-%m-%d %H:%M:%S')} by scripts/switch_assembly.py --refresh --variant {args.variant}\n"
                f"issues: {', '.join(p[0] for p in plan)}\n"
                "articles/ = the live records before the refresh (same ids; the annotation logs were not touched).\n"
                "To undo: copy articles/<issue>/articles.json back to data/articles/<issue>/.\n")
    from s07_articles import build_index
    build_index()
    print(f"[refresh] done; the previous live records are kept under data/assembly_archive/{stamp}_refresh")


if __name__ == "__main__":
    main()
