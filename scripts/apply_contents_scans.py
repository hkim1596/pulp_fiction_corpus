#!/usr/bin/env python3
"""The contents pages the readers found where the machine did not look: from the contents checks
(data/review/contents_check.jsonl, the answer "a contents page the machine did not read, at scan N") to
config/toc_page_overrides.json, which the assembler reads (pipeline/s08_assemble_rules.py toc_page_override:
toc_folio then reads that scan as the contents page). Rules 2.10, 26 October 2026.

    python3 scripts/apply_contents_scans.py [--dry-run]

Prints the issues whose scan is new or changed; those are the ones to re-assemble (scripts/upgrade_continuity.py
--issues-file ... --force).
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = os.path.join(ROOT, "data", "review", "contents_check.jsonl")
OUT = os.path.join(ROOT, "config", "toc_page_overrides.json")


def scans_from_checks(path=CHECKS):
    """issue -> (scan, user, ts): the latest check per issue that names a contents scan."""
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("issue") and r.get("no_contents") == "unread" and r.get("contents_scan"):
                out[r["issue"]] = (int(r["contents_scan"]), r.get("user"), r.get("ts"))
            elif r.get("issue") and r.get("no_contents") == "none":
                out.pop(r["issue"], None)                     # a later check says there is no contents page
    except OSError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        d = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        d = {"_comment": "The contents page of an issue as a reader found it (scan number), when the assembler's search did not find it: "
                         "written by scripts/apply_contents_scans.py from the contents checks; read by pipeline/s08_assemble_rules.py.", "scans": {}}
    have = d.setdefault("scans", {})
    changed = []
    for iid, (scan, user, ts) in sorted(scans_from_checks().items()):
        if have.get(iid) != scan:
            changed.append((iid, have.get(iid), scan))
            have[iid] = scan
            d.setdefault("by", {})[iid] = {"user": user, "ts": ts}
    print(f"{len(have)} issues with a contents scan named by a reader; {len(changed)} new or changed" + (" (dry run)" if args.dry_run else ""))
    for iid, old, new in changed:
        print(f"  {iid}: scan {new}" + (f" (was {old})" if old else ""))
    if changed and not args.dry_run:
        d["written"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = OUT + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(tmp, OUT)
        json.dump([i for i, _, _ in changed], open(os.path.join(ROOT, "data", "review", "contents_scans_to_reassemble.json"), "w"))
        print(f"written: config/toc_page_overrides.json; the ids to re-assemble: data/review/contents_scans_to_reassemble.json")


if __name__ == "__main__":
    main()
