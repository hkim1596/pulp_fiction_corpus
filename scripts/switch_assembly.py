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
"""
import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="rules", choices=["rules", "rules_on_model"])
    ap.add_argument("--issue")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    ids = [args.issue] if args.issue else [i["id"] for i in cfg["issues"]]
    stamp = time.strftime("%Y%m%d_%H%M%S")
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


if __name__ == "__main__":
    main()
