#!/usr/bin/env python3
"""Read-only selection for a bounded rules patch; no assembly/model/database writes.

Compare full analyses from frozen baseline and candidate modules against the same
layout. Include held issues and continued-from cases whose assembly prelude also
changed. An explicit --output writes only the plan artifact. At most eight CPU
workers are allowed; no inference or network calls are made.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache

sys.dont_write_bytecode = True
OMIT = object()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def semantic(value):
    """Normalize optional/default fields without discarding hidden start fields."""
    if callable(value):
        return OMIT
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if str(key) == "vocab" or str(key).startswith("_") or child is None:
                continue
            normalized = semantic(child)
            if normalized is not OMIT:
                out[str(key)] = normalized
        return out
    if isinstance(value, (list, tuple, set)):
        values = [semantic(child) for child in value]
        values = [child for child in values if child is not OMIT]
        return sorted(values, key=canonical) if isinstance(value, set) else values
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported analysis value: {type(value).__name__}")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def analysis_difference(before, after, held=False):
    old, new = semantic(before), semantic(after)
    changed_fields = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
    old_pages, new_pages = old.get("pages", {}), new.get("pages", {})
    changed_pages = sorted((k for k in old_pages.keys() | new_pages.keys()
                            if old_pages.get(k) != new_pages.get(k)), key=int)
    reasons = []
    if changed_fields:
        reasons.append("analysis_changed")
    if any(page.get("cont_from") for analysis in (before, after) for page in analysis.get("pages", {}).values()):
        reasons.append("continued_from_assembly_recheck")
    if any(start.get("facing_title") for page in after.get("pages", {}).values() for start in page.get("starts", [])):
        reasons.append("facing_title_recheck")
    if held:
        reasons.append("previous_hold")
    return {"selected": bool(reasons), "reasons": reasons, "changed_fields": changed_fields,
            "changed_pages": changed_pages,
            "baseline_analysis_sha256": hashlib.sha256(canonical(old).encode()).hexdigest(),
            "candidate_analysis_sha256": hashlib.sha256(canonical(new).encode()).hexdigest()}


@lru_cache(maxsize=2)
def rules_module(path, root):
    sys.path.insert(0, str(Path(root) / "pipeline"))
    name = "_continuity_recheck_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.ROOT = str(root)
    return module


def layout_signature(directory):
    return [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(directory.glob("page_*.json"))]


def compare_issue(root, meta, baseline_path, candidate_path, held=False):
    iid = meta["id"]
    try:
        baseline = rules_module(baseline_path, root)
        candidate = rules_module(candidate_path, root)
        layout = Path(root) / "data/layout" / iid
        signature = layout_signature(layout)
        if not signature:
            raise ValueError("no layout pages")
        pages = candidate.load_pages(iid)
        if not pages:
            raise ValueError("layout loader returned no pages")
        before = baseline.analyse(copy.deepcopy(pages))
        after = candidate.analyse(copy.deepcopy(pages))
        if layout_signature(layout) != signature:
            raise ValueError("layout changed during analysis")
        result = analysis_difference(before, after, held)
        return dict(result, issue=iid, status="ok", pages=len(pages),
                    layout_signature_sha256=hashlib.sha256(canonical(signature).encode()).hexdigest())
    except Exception as exc:
        # An uncertain comparison cannot exclude an issue from guarded reassembly.
        return {"issue": iid, "status": "error", "selected": True,
                "reasons": ["comparison_error"] + (["previous_hold"] if held else []),
                "error": f"{type(exc).__name__}: {exc}"}


def inventory(root, issues):
    root = Path(root)
    held = set()
    result_file = root / "data/corpus/continuity-2026-10-07/results.json"
    if result_file.exists():
        held.update(row["issue"] for row in json.loads(result_file.read_text()).get("results", [])
                    if row.get("status") in ("failed", "committing"))
    out = []
    for meta in issues:
        iid = meta["id"]
        receipt = root / "data/corpus/continuity-2026-10-07" / iid / "receipt.json"
        if receipt.exists() and json.loads(receipt.read_text()).get("status") in ("failed", "committing"):
            held.add(iid)
        state_path = root / "data/corpus/state" / f"{iid}.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if "assembled" in state.get("stages", {}) or iid in held:
            out.append(meta)
    return out, held


def write_plan(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".recheck-plan-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline-rules", type=Path, required=True)
    parser.add_argument("--candidate-rules", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    root = args.root.resolve()
    sys.path.insert(0, str(root / "pipeline"))
    from corpus_lib import corpus_config, partition_issue_ids, require_approved
    cfg = corpus_config()
    approval = require_approved(cfg, "plan continuity recheck")
    issues, conflicts = partition_issue_ids(cfg["issues"])
    baseline_path, candidate_path = str(args.baseline_rules.resolve()), str(args.candidate_rules.resolve())
    baseline_sha, candidate_sha = digest(baseline_path), digest(candidate_path)
    results, seen = [], set()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        # Catch new assemblies completed after the initial/base-backfill snapshot.
        for _round in range(3):
            current, held = inventory(root, issues)
            todo = [meta for meta in current if meta["id"] not in seen]
            if not todo:
                break
            jobs = {pool.submit(compare_issue, str(root), meta, baseline_path, candidate_path,
                                meta["id"] in held): meta["id"] for meta in todo}
            for future in as_completed(jobs):
                iid = jobs[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"issue": iid, "status": "error", "selected": True,
                              "reasons": ["comparison_error"], "error": repr(exc)}
                results.append(result)
                seen.add(iid)
                if len(results) % 100 == 0:
                    print(f"analysed {len(results)} issues", file=sys.stderr, flush=True)
    current, held = inventory(root, issues)
    by_id = {row["issue"]: row for row in results}
    for meta in current:
        iid = meta["id"]
        if iid not in by_id:
            by_id[iid] = {"issue": iid, "status": "uncompared", "selected": True,
                          "reasons": ["assembled_during_scan"]}
        if iid in held and "previous_hold" not in by_id[iid]["reasons"]:
            by_id[iid]["reasons"].append("previous_hold")
            by_id[iid]["selected"] = True
    if digest(baseline_path) != baseline_sha or digest(candidate_path) != candidate_sha:
        raise SystemExit("rules source changed during the scan; no plan written")
    results = sorted(by_id.values(), key=lambda row: row["issue"])
    selected = [row["issue"] for row in results if row["selected"]]
    report = {"started_utc": started, "completed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "approved_list_sha256": approval.get("list_sha256"), "workers": args.workers,
              "baseline_rules": baseline_path, "baseline_rules_source_sha256": baseline_sha,
              "candidate_rules": candidate_path, "rules_source_sha256": candidate_sha,
              "scanned": len(results), "selected": len(selected), "issues": selected,
              "source_conflicts_held": conflicts,
              "reason_counts": dict(Counter(reason for row in results for reason in row["reasons"])),
              "results": results}
    if args.output:
        write_plan(args.output, report)
        print(json.dumps({key: report[key] for key in ("scanned", "selected", "reason_counts", "rules_source_sha256")}))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
