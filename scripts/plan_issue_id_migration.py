#!/usr/bin/env python3
"""Read-only plan for repairing ambiguous issue IDs without changing corpus membership.

No apply mode exists. --write-proposal DIR saves reviewable proposal/snapshot files there;
it never changes live configs, approvals, state, annotations, or corpus artifacts.
The script is stdlib-only and can also run over SSH via `python3 - --root ...`.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re


def fingerprint(issues):
    return hashlib.sha256(json.dumps(issues, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def membership(issues):
    """Compare every selected entry/field except its repairable internal id; preserve multiplicities."""
    return sorted(json.dumps({k: v for k, v in issue.items() if k != "id"}, sort_keys=True, ensure_ascii=False)
                  for issue in issues)


def canonical_id(issue):
    key = re.sub(r"[^a-z0-9]+", "_", issue["magazine"].lower()).strip("_")[:28] or "mag"
    tail = re.sub(r"[^A-Za-z0-9]+", "", issue["ia_identifier"])[-10:].lower()
    digest = hashlib.sha256(issue["ia_identifier"].encode("utf-8")).hexdigest()[:12]
    return f"{key}_{issue['cover_date'].replace('-', '_')}_{tail}_{digest}"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def article_status(path):
    if not path.exists():
        return "absent"
    obj = read_json(path)
    return "valid" if isinstance(obj, dict) and isinstance(obj.get("articles"), list) else "invalid"


def decisions_status(path):
    result = {"present": path.exists(), "valid_lines": 0, "invalid_lines": 0, "duplicate_pages": []}
    pages = Counter()
    if path.exists():
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict) or not isinstance(obj.get("page"), int):
                        raise ValueError("missing page identity")
                    pages[obj["page"]] += 1
                    result["valid_lines"] += 1
                except ValueError:
                    result["invalid_lines"] += 1
    result["duplicate_pages"] = sorted(page for page, n in pages.items() if n > 1)
    return result


def plan(root):
    root = Path(root).resolve()
    list_path, approval_path = root / "config/corpus_issues.json", root / "config/corpus_approval.json"
    cfg, approval = read_json(list_path), read_json(approval_path)
    if not isinstance(cfg, dict) or not isinstance(cfg.get("issues"), list):
        raise ValueError("cannot read the corpus issue list")
    issues = cfg["issues"]
    original_hash = fingerprint(issues)
    if not approval or not approval.get("approved") or approval.get("list_sha256") != original_hash:
        raise ValueError("the current issue list does not match its recorded approval")
    grouped = defaultdict(list)
    for issue in issues:
        grouped[issue["id"]].append(issue)
    collisions = {iid: members for iid, members in grouped.items()
                  if len({x["ia_identifier"] for x in members}) > 1}
    replacements = {(iid, issue["ia_identifier"]): canonical_id(issue)
                    for iid, members in collisions.items() for issue in members}
    proposed = dict(cfg, issues=[dict(issue, id=replacements.get((issue["id"], issue["ia_identifier"]), issue["id"]))
                               for issue in issues])
    if len({x["id"] for x in proposed["issues"]}) != len(proposed["issues"]):
        raise ValueError("proposed list is not unique; no proposal is safe to apply")
    if membership(issues) != membership(proposed["issues"]):
        raise ValueError("source membership or metadata changed; this is not an ID-only migration")
    review_log = root / "data/review/model_check.jsonl"
    reviews = Counter()
    if review_log.exists():
        with review_log.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    record = json.loads(line)  # do not silently overlook unreadable human decisions
                    reviews[record.get("issue")] += 1
    groups, blockers = [], []
    categories = ("raw", "masters", "pages", "thumbs", "layout", "text", "articles")
    variants = root / "data/assembly_v2"
    for iid, members in sorted(collisions.items()):
        if Path(iid).name != iid or iid in (".", ".."):
            raise ValueError(f"unsafe issue id: {iid!r}")
        paths = [root / "data" / category / iid for category in categories]
        paths += [root / "data/corpus/state" / f"{iid}.json", root / "data/export/corpus" / f"{iid}.jsonl"]
        paths += sorted(variants.glob(f"*/{iid}")) if variants.exists() else []
        annotation = root / "data/annotations" / f"{iid}.jsonl"
        if annotation.exists():
            paths.append(annotation)
        has_annotations = annotation.exists() and bool(annotation.read_text(encoding="utf-8").strip())
        if has_annotations:
            blockers.append(f"{iid}: human assembly corrections require source/anchor reconciliation")
        metadata = read_json(root / "data/raw" / iid / "meta.json") or {}
        state = read_json(root / "data/corpus/state" / f"{iid}.json") or {}
        target_ids = []
        for issue in members:
            new_id = replacements[iid, issue["ia_identifier"]]
            target_ids.append({"ia_identifier": issue["ia_identifier"], "new_id": new_id})
            possible = [root / "data" / category / new_id for category in categories]
            possible += [root / "data/corpus/state" / f"{new_id}.json", root / "data/export/corpus" / f"{new_id}.jsonl"]
            possible += sorted(variants.glob(f"*/{new_id}")) if variants.exists() else []
            if any(path.exists() for path in possible):
                blockers.append(f"{new_id}: a proposed destination already exists; inspect before applying")
        groups.append({
            "old_id": iid, "sources": target_ids,
            "archive_before_reprocessing": [str(p.relative_to(root)) for p in paths if p.exists()],
            "cached_metadata_source": (metadata.get("metadata") or {}).get("identifier"),
            "cached_state_source": (state.get("stages", {}).get("downloaded") or {}).get("ia_identifier"),
            "human_assembly_corrections": has_annotations, "human_model_decisions_to_preserve": reviews[iid],
            "llm_articles": article_status(root / "data/assembly_v2/llm" / iid / "articles.json"),
            "llm_decisions": decisions_status(root / "data/assembly_v2/llm" / iid / "pages.jsonl"),
        })
    result = {
        "mode": "dry_run_only", "migration_id": f"issue-id-repair-{original_hash[:12]}",
        "original_list_sha256": original_hash, "proposed_list_sha256": fingerprint(proposed["issues"]),
        "original_approval_file_sha256": file_digest(approval_path),
        "selected_membership_sha256": fingerprint(membership(issues)),
        "selection_membership_and_metadata_unchanged": True,
        "selected_entries": len(issues), "original_unique_ids": len(grouped),
        "proposed_unique_ids": len({x["id"] for x in proposed["issues"]}),
        "collision_groups": len(groups), "sources_to_reprocess": sum(len(x["sources"]) for x in groups),
        "unaffected_entries": sum(x["id"] not in collisions for x in issues),
        "preserve_shared_audit_logs": ["data/corpus/events.jsonl", "data/corpus/fetch_manifest.jsonl",
                                       "data/corpus/run_info.jsonl", "data/review/model_check.jsonl"],
        "blockers": blockers, "groups": groups,
        "apply_design": [
            "Drain corpus/model writers at issue boundaries; preserve the inference servers.",
            "Archive original list/approval, listed ambiguous paths, shared audit logs, and explorer database with checksums.",
            "Keep the old approval as historical evidence; record an authorized ID-only migration attestation binding both hashes and unchanged selected membership.",
            "Install the proposed list and its technically migrated approval together; retain original human approval identity/date and explicitly attribute only the ID repair to Codex.",
            "Move ambiguous machine outputs to the archive; preserve human logs verbatim and never guess their new source.",
            "Create clean source-specific directories and reprocess only the affected sources; cached mixed outputs are not reused.",
            "Recompute dependent serial links and publish/export validated replacement sources; preserve ambiguous old IDs as archival references.",
            "Verify one source per id, complete OCR, valid artifacts, retained human decisions, and advancing site refresh before closing the migration.",
        ],
    }
    return result, proposed, cfg, approval


def migrated_approval(report, original):
    """A technical fingerprint update, explicitly distinguished from a new selection approval."""
    proposal = dict(original)
    proposal["list_sha256"] = report["proposed_list_sha256"]
    proposal["identity_migrations"] = list(original.get("identity_migrations") or []) + [{
        "migration_id": report["migration_id"], "kind": "technical_issue_id_collision_repair",
        "performed_by": "Codex", "proposed_at_utc": datetime.now(timezone.utc).isoformat(),
        "authorization": "User request to automatically fix critical corpus defects; selected source membership is unchanged.",
        "original_list_sha256": report["original_list_sha256"],
        "new_list_sha256": report["proposed_list_sha256"],
        "selected_membership_sha256": report["selected_membership_sha256"],
        "original_approval_file_sha256": report["original_approval_file_sha256"],
        "archive_manifest": f"data/identity_quarantine/{report['migration_id']}/plan.json",
        "note": "The original human selection approval remains historical evidence; this entry authorizes only internal ID repair, not new source selection.",
    }]
    return proposal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write-proposal", type=Path, help="write review artifacts here only; never applies them")
    args = parser.parse_args()
    report, proposed, original, approval = plan(args.root)
    if args.write_proposal:
        destination = args.write_proposal.resolve()
        # Keep proposals out of live config/data directories, even if an output argument is mistaken.
        root = args.root.resolve()
        if any(path == destination or path in destination.parents for path in (root / "config", root / "data")):
            parser.error("proposal directory must be outside live config/ and data/")
        destination.mkdir(parents=True, exist_ok=False)
        for name, value in (("plan.json", report), ("corpus_issues.proposed.json", proposed),
                            ("corpus_issues.original.json", original), ("corpus_approval.original.json", approval),
                            ("corpus_approval.proposed.json", migrated_approval(report, approval))):
            (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
