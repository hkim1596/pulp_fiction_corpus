#!/usr/bin/env python3
"""corpus_reaper — free disk space by hand (the orchestrator does the same on its own when free space falls
under settings.images.min_free_gb; this script is for a look, or for a run while the orchestrator is stopped).

What may go, and when (corpus-build-plan-2026-10-04, the storage lifecycle):
  masters   data/masters/<id>/  (the JP2 zip)   once the issue is assembled   — re-fetchable from the archive
  working   data/pages/<id>/    (2,200 px JPEG) once the issue is assembled   — re-creatable from the master or the archive
  thumbs    data/thumbs/<id>/                   never (the site needs them)
  text, layout, lemmas, assembly                never

    python3 scripts/corpus_reaper.py                  # report: what is on disk, what could be freed
    python3 scripts/corpus_reaper.py --masters        # delete the masters of every assembled issue
    python3 scripts/corpus_reaper.py --working --until-free 600   # oldest assembled issues' working images until 600 GB are free
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))
from corpus_lib import all_states, free_gb, issue_dirs, dir_bytes, settings  # noqa: E402
import run_corpus  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--masters", action="store_true", help="delete the masters of assembled issues")
    ap.add_argument("--working", action="store_true", help="delete working images of assembled issues (oldest first)")
    ap.add_argument("--until-free", type=float, default=0, help="with --working: stop once this many GB are free")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    states = all_states()
    assembled = sorted((s["stages"]["assembled"]["ts"], iid) for iid, s in states.items() if "assembled" in s["stages"])
    m_cands = [iid for _t, iid in assembled if "master_removed" not in states[iid]["stages"] and os.path.isdir(issue_dirs(iid)["master"])]
    w_cands = [iid for _t, iid in assembled if "working_removed" not in states[iid]["stages"] and os.path.isdir(issue_dirs(iid)["pages"])]
    print(f"free: {free_gb():.0f} GB (the orchestrator reaps under {settings()['images']['min_free_gb']} GB)")
    print(f"assembled issues: {len(assembled):,}; masters still on disk: {len(m_cands):,}; working images still on disk: {len(w_cands):,}")
    if not (args.masters or args.working):
        mb = sum(dir_bytes(issue_dirs(i)["master"]) for i in m_cands[:200]) / max(1, min(200, len(m_cands)))
        wb = sum(dir_bytes(issue_dirs(i)["pages"]) for i in w_cands[:200]) / max(1, min(200, len(w_cands)))
        print(f"could free about {mb * len(m_cands) / 1e9:.0f} GB of masters and {wb * len(w_cands) / 1e9:.0f} GB of working images (sampled)")
        return
    freed = 0
    if args.masters:
        for iid in m_cands:
            freed += dir_bytes(issue_dirs(iid)["master"]) if args.dry_run else run_corpus.remove_master(iid)
    if args.working:
        for iid in w_cands:
            if args.until_free and free_gb() >= args.until_free:
                break
            freed += dir_bytes(issue_dirs(iid)["pages"]) if args.dry_run else run_corpus.remove_working(iid)
    print(f"{'would free' if args.dry_run else 'freed'} {freed / 1e9:.1f} GB; free now {free_gb():.0f} GB")


if __name__ == "__main__":
    main()
