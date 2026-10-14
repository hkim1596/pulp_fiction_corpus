#!/usr/bin/env python3
"""Apply a reviewed, bounded ID-only proposal while corpus writers are drained.

Default is preflight only. --apply archives affected artifacts by same-filesystem rename,
records every original file's SHA-256, and installs the proposed list/technical approval.
No service is started or stopped. Any unexpected condition stops with a recovery journal.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

CATEGORIES = ("raw", "masters", "pages", "thumbs", "layout", "text", "work_surya", "articles")
WRITERS = {"run_corpus.py", "s01c_fetch.py", "s02_layout_ocr.py", "s04_rules.py", "s04b_lemma.py",
           "s08_assemble_rules.py", "s12_llm_link.py", "s13_publish.py", "r00_export_stories.py",
           "site_refresh.py", "upgrade_continuity.py"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".identity-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def write_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def active_writers():
    out = []
    for process in Path("/proc").glob("[0-9]*"):
        try:
            args = (process / "cmdline").read_bytes().decode(errors="replace").split("\0")
        except (OSError, ProcessLookupError):
            continue
        if "--selftest" in args:
            continue
        scripts = [Path(arg).name for arg in args if arg and Path(arg).name in WRITERS]
        if scripts:
            out.append({"pid": int(process.name), "scripts": scripts})
    return out


def assert_drained(root):
    missing = [name for name in ("STOP", "STOP_LLM", "STOP_SITE") if not (root / "data/corpus" / name).exists()]
    if missing:
        raise RuntimeError(f"missing drain controls: {missing}")
    writers = active_writers()
    if writers:
        raise RuntimeError(f"writers are still running: {writers}")


def assert_no_human_decisions(root, old_ids):
    for iid in old_ids:
        annotation = root / "data/annotations" / f"{iid}.jsonl"
        if annotation.exists() and annotation.read_text(encoding="utf-8").strip():
            raise RuntimeError(f"human annotations now exist for {iid}")
    reviews = root / "data/review/model_check.jsonl"
    if reviews.exists():
        with reviews.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("issue") in old_ids or str(row.get("case", "")).split("|", 1)[0] in old_ids:
                    raise RuntimeError("a held issue now has a human model-review decision")


def issue_paths(root, iid):
    paths = [root / "data" / category / iid for category in CATEGORIES]
    paths += [root / "data/corpus/state" / f"{iid}.json", root / "data/export/corpus" / f"{iid}.jsonl",
              root / "data/annotations" / f"{iid}.jsonl"]
    variants = root / "data/assembly_v2"
    if variants.exists():
        paths += sorted(variants.glob(f"*/{iid}"))
    return [path for path in paths if path.exists() or path.is_symlink()]


def preflight(root, proposal):
    root, proposal = Path(root).resolve(), Path(proposal).resolve()
    assert_drained(root)
    report = read_json(proposal / "plan.json")
    original = read_json(proposal / "corpus_issues.original.json")
    proposed = read_json(proposal / "corpus_issues.proposed.json")
    original_approval = read_json(proposal / "corpus_approval.original.json")
    proposed_approval = read_json(proposal / "corpus_approval.proposed.json")
    current, approval_path = root / "config/corpus_issues.json", root / "config/corpus_approval.json"
    if read_json(current) != original or read_json(approval_path) != original_approval:
        raise RuntimeError("current list/approval changed after the reviewed proposal")
    if digest(approval_path) != report["original_approval_file_sha256"]:
        raise RuntimeError("original approval byte fingerprint changed")
    old_hash, new_hash = fingerprint(original["issues"]), fingerprint(proposed["issues"])
    if old_hash != report["original_list_sha256"] or new_hash != report["proposed_list_sha256"]:
        raise RuntimeError("proposal list fingerprints do not match its plan")
    if not original_approval.get("approved") or original_approval.get("list_sha256") != old_hash:
        raise RuntimeError("original selection approval does not match")
    if proposed_approval.get("list_sha256") != new_hash:
        raise RuntimeError("technical migration approval does not match the proposed list")
    unaffected_approval = {k: v for k, v in proposed_approval.items() if k not in ("list_sha256", "identity_migrations")}
    if unaffected_approval != {k: v for k, v in original_approval.items() if k not in ("list_sha256", "identity_migrations")}:
        raise RuntimeError("proposal changes historical approval fields")
    history = proposed_approval.get("identity_migrations") or []
    if history[:-1] != (original_approval.get("identity_migrations") or []) or not history:
        raise RuntimeError("technical migration approval history is invalid")
    entry = history[-1]
    if (entry.get("original_list_sha256"), entry.get("new_list_sha256"), entry.get("migration_id")) != (old_hash, new_hash, report["migration_id"]):
        raise RuntimeError("technical migration attestation is not bound to this plan")
    if {k: v for k, v in proposed.items() if k != "issues"} != {k: v for k, v in original.items() if k != "issues"}:
        raise RuntimeError("proposal changes selection metadata outside issue ids")
    if len(proposed["issues"]) != len(original["issues"]) or len({i["id"] for i in proposed["issues"]}) != len(proposed["issues"]):
        raise RuntimeError("proposal loses entries or still has duplicate ids")
    old_ids = {g["old_id"] for g in report["groups"]}
    mapping = {(g["old_id"], src["ia_identifier"]): src["new_id"] for g in report["groups"] for src in g["sources"]}
    for before, after in zip(original["issues"], proposed["issues"]):
        if {k: v for k, v in before.items() if k != "id"} != {k: v for k, v in after.items() if k != "id"}:
            raise RuntimeError("selected sources, order, or metadata changed")
        expected = mapping.get((before["id"], before["ia_identifier"]), before["id"])
        if after["id"] != expected:
            raise RuntimeError("ID changes do not match the reviewed mapping")
    for iid in old_ids | set(mapping.values()):
        if Path(iid).name != iid or iid in (".", ".."):
            raise RuntimeError(f"unsafe issue id {iid!r}")
    actual_collisions = {iid for iid, n in Counter(i["id"] for i in original["issues"]).items() if n > 1}
    if actual_collisions != old_ids:
        raise RuntimeError("plan does not cover exactly the original collision groups")
    assert_no_human_decisions(root, old_ids)
    for iid in mapping.values():
        if issue_paths(root, iid):
            raise RuntimeError(f"new source destination already exists: {iid}")
    archive = root / "data/identity_quarantine" / report["migration_id"]
    if archive.exists():
        raise RuntimeError(f"archive already exists; inspect its journal instead of overwriting: {archive}")
    paths = [path for iid in sorted(old_ids) for path in issue_paths(root, iid)]
    physical = [str(path.resolve()) for path in paths]
    if len(physical) != len(set(physical)):
        raise RuntimeError("different artifact paths resolve to the same source")
    device = (root / "data").stat().st_dev
    for path in paths:
        if path.is_symlink():
            raise RuntimeError(f"unexpected per-issue symlink requires explicit target handling: {path}")
        if path.stat().st_dev != device:
            raise RuntimeError(f"archive would cross filesystems: {path}; no copy/delete fallback is attempted")
    return root, proposal, report, archive, paths, original, proposed, original_approval, proposed_approval


def inventory(path):
    """Fingerprint files before renaming; retain inode evidence for lossless same-filesystem verification."""
    entries = []
    members = [path] + (sorted(path.rglob("*")) if path.is_dir() else [])
    for member in members:
        if member.is_symlink():
            raise RuntimeError(f"unexpected nested symlink requires explicit preservation: {member}")
        before = member.stat()
        row = {"relative": "." if member == path else str(member.relative_to(path)),
               "kind": "directory" if member.is_dir() else "file", "device": before.st_dev,
               "inode": before.st_ino, "size": before.st_size, "mtime_ns": before.st_mtime_ns}
        if member.is_file():
            row["sha256"] = digest(member)
            after = member.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"artifact changed while fingerprinting: {member}")
        entries.append(row)
    return entries


def verify_renamed(destination, entries):
    actual = {"."} | ({str(p.relative_to(destination)) for p in destination.rglob("*")} if destination.is_dir() else set())
    if actual != {entry["relative"] for entry in entries}:
        raise RuntimeError(f"archive tree differs after rename: {destination}")
    for entry in entries:
        member = destination if entry["relative"] == "." else destination / entry["relative"]
        stat = member.stat()
        if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != (entry["device"], entry["inode"], entry["size"], entry["mtime_ns"]):
            raise RuntimeError(f"archive inode/content metadata changed: {member}")


def apply(root, proposal):
    root, proposal, report, archive, paths, original, proposed, old_approval, new_approval = preflight(root, proposal)
    artifacts = []
    for path in paths:
        artifacts.append({"source": str(path.relative_to(root)), "physical_source": str(path.resolve()),
                          "archive": str((archive / "artifacts" / path.relative_to(root)).relative_to(root)),
                          "entries": inventory(path), "status": "pending"})
    assert_drained(root)
    assert_no_human_decisions(root, {g["old_id"] for g in report["groups"]})
    archive.mkdir(parents=True, exist_ok=False)
    journal = {"migration_id": report["migration_id"], "phase": "snapshotting", "artifacts": artifacts,
               "started_at_utc": datetime.now(timezone.utc).isoformat(), "snapshots": []}
    journal_path = archive / "journal.json"
    write_json(archive / "plan.json", report)
    write_json(journal_path, journal)
    try:
        snapshots = [root / "config/corpus_issues.json", root / "config/corpus_approval.json"]
        snapshots += [root / name for name in report["preserve_shared_audit_logs"]]
        snapshots += [root / "data" / name for name in ("explorer.sqlite", "explorer.sqlite-wal", "explorer.sqlite-shm",
                                                       "review/model_disagreements.jsonl", "corpus/llm_follow_failures.jsonl")]
        for source in snapshots:
            if not source.exists():
                continue
            dest = archive / "snapshots" / source.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            before = digest(source)
            shutil.copy2(source, dest)
            if digest(dest) != before or digest(source) != before:
                raise RuntimeError(f"snapshot changed or did not copy exactly: {source}")
            journal["snapshots"].append({"source": str(source.relative_to(root)), "archive": str(dest.relative_to(root)), "sha256": before})
        for source in sorted(proposal.glob("*.json")):
            dest = archive / "proposal" / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            if digest(source) != digest(dest):
                raise RuntimeError(f"proposal snapshot mismatch: {source}")
        journal["phase"] = "archiving"
        write_json(journal_path, journal)
        for artifact in artifacts:
            assert_drained(root)
            source, dest = root / artifact["source"], root / artifact["archive"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                raise RuntimeError(f"archive destination unexpectedly exists: {dest}")
            verify_renamed(source, artifact["entries"])
            artifact["status"] = "moving"
            write_json(journal_path, journal)
            os.rename(source, dest)  # follows parent directory symlinks; the actual page/thumbnail tree is moved
            verify_renamed(dest, artifact["entries"])
            artifact["status"] = "archived"
            write_json(journal_path, journal)
        if read_json(root / "config/corpus_issues.json") != original or read_json(root / "config/corpus_approval.json") != old_approval:
            raise RuntimeError("config or approval changed during archiving; proposal not installed")
        assert_no_human_decisions(root, {g["old_id"] for g in report["groups"]})
        journal["phase"] = "installing_config"
        write_json(journal_path, journal)
        atomic_bytes(root / "config/corpus_issues.json", (proposal / "corpus_issues.proposed.json").read_bytes())
        atomic_bytes(root / "config/corpus_approval.json", (proposal / "corpus_approval.proposed.json").read_bytes())
        if fingerprint(read_json(root / "config/corpus_issues.json")["issues"]) != new_approval["list_sha256"]:
            raise RuntimeError("installed list failed approval fingerprint verification")
        if read_json(root / "config/corpus_approval.json") != new_approval:
            raise RuntimeError("installed approval differs from the proposal")
        for group in report["groups"]:
            if issue_paths(root, group["old_id"]):
                raise RuntimeError(f"old ambiguous output is still active: {group['old_id']}")
        summary = {k: report[k] for k in ("migration_id", "original_list_sha256", "proposed_list_sha256",
                                          "selected_membership_sha256", "selected_entries", "collision_groups",
                                          "sources_to_reprocess", "unaffected_entries")}
        summary.update({"status": "complete", "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                        "archive": str(archive.relative_to(root)), "archived_artifacts": len(artifacts),
                        "archived_files": sum(e["kind"] == "file" for a in artifacts for e in a["entries"]),
                        "archived_bytes": sum(e["size"] for a in artifacts for e in a["entries"] if e["kind"] == "file"),
                        "mapping": [{"old_id": g["old_id"], **source} for g in report["groups"] for source in g["sources"]]})
        write_json(root / "data/corpus/identity_migrations" / f"{report['migration_id']}.json", summary)
        write_json(root / "data/corpus/identity_migration.json", summary)
        journal["phase"] = "complete"
        journal["result"] = summary
        write_json(journal_path, journal)
        return summary
    except BaseException as exc:
        journal["phase"] = "stopped_on_error"
        journal["error"] = repr(exc)
        write_json(journal_path, journal)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        result = apply(args.root, args.proposal)
        print(json.dumps({k: v for k, v in result.items() if k != "mapping"}, indent=1))
    else:
        _root, _proposal, report, archive, paths, *_ = preflight(args.root, args.proposal)
        print(json.dumps({"mode": "preflight_only", "migration_id": report["migration_id"], "archive": str(archive),
                          "artifact_paths": len(paths), "sources_to_reprocess": report["sources_to_reprocess"]}, indent=1))


if __name__ == "__main__":
    main()
