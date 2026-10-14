#!/usr/bin/env python3
"""Freeze human assembly labels, then compare raw candidate story continuity.

No command writes source data or annotations. ``snapshot`` replays annotations
once through the website's read-only effective_doc; ``compare`` needs only the
saved JSON and candidate <issue>/articles.json trees. Candidates never receive
the human corrections being used to score them. Use an explicit --out or stdout.

The primary score is verified-story body-word recall in ONE candidate record.
Missing internal runs, fragmented ownership, retained-body order and inserted
non-story material are separate diagnostics, not a blended accuracy percentage.
Modified-but-unverified records are reported separately and are not gold labels.
This is a development benchmark, not an independent corpus accuracy estimate.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

STORY_TYPES = {"story", "serial_part"}
# These are exactly the site's running-text exclusions. Chapter apparatus and
# captions may still be in its reading text; prose recall is measured separately.
READING_EXCLUDED = {"title", "subtitle", "author", "teaser", "note", "synopsis"}
BODY_EXCLUDED = READING_EXCLUDED | {"caption", "chapter", "heading", "section",
                                    "chapter_number", "chapter_title"}


def keys(record):
    """Explode fragments in their recorded order; keep repeats for diagnostics."""
    out = []
    for f in record.get("fragments", []):
        if isinstance(f, dict):
            if f.get("mid") is not None:
                out.append(f"m:{f['mid']}")
            else:
                out.extend(f"{f['page']}:{i}" for i in f.get("region_ids", []))
        elif isinstance(f, (list, tuple)) and len(f) == 2:
            out.append(f"{f[0]}:{f[1]}")
    return out


def unique(seq):
    return list(dict.fromkeys(seq))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def snapshot(project, issue_ids=None):
    """Load project modules without bytecode writes; read no account secrets."""
    project = Path(project).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(project / "webapp"))
    spec = importlib.util.spec_from_file_location("continuity_site", project / "webapp/app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    issues = {}
    ids = issue_ids or sorted(p.stem for p in (project / "data/annotations").glob("*.jsonl"))
    for iid in ids:
        art_path = project / "data/articles" / iid / "articles.json"
        ann_path = project / "data/annotations" / f"{iid}.jsonl"
        before = {"articles": digest(art_path), "annotations": digest(ann_path)}
        if not before["articles"] or not before["annotations"]:
            continue
        # Fail on malformed annotations rather than silently accepting the site's
        # tolerant replay and calling an incomplete record human-verified.
        for line in ann_path.read_text().splitlines():
            if line.strip():
                json.loads(line)
        doc = app.effective_doc(iid)
        if not doc:
            continue
        regions = {}
        layout_hashes = {}
        for path in sorted((project / "data/layout" / iid).glob("page_*.json")):
            page = json.loads(path.read_text())
            layout_hashes[path.name] = digest(path)
            for idx, region in enumerate(sorted(page["regions"], key=lambda r: r.get("order", 0))):
                regions[f"{page['page']}:{idx}"] = {
                    "words": len((region.get("text") or "").split()),
                    "label": region.get("label"),
                }
        roles = doc.get("frag_roles", {})
        records = []
        for rec in doc["articles"]:
            if rec.get("status") not in ("verified", "modified") or rec.get("type") not in STORY_TYPES:
                continue
            kk = unique(keys(rec))
            weights = {}
            for fr in rec["fragments"]:
                k = app.fragkey(fr)
                weights[k] = len(app.frag_text(iid, fr, doc.get("frag_overrides", {})).split())
            # Whole-record text overrides cannot be mapped reliably to boxes.
            # Keep these records visible, but do not include them in metrics.
            missing_layout = [k for k in kk if not k.startswith("m:") and k not in regions]
            if missing_layout:
                raise ValueError(f"{iid}/{rec['article_id']}: missing layout for {missing_layout[:5]}")
            records.append({
                "article_id": rec["article_id"], "title": rec.get("title"),
                "type": rec.get("type"), "status": rec["status"],
                "verified_at": rec.get("verified_at"), "keys": kk,
                "reading_keys": [k for k in kk if roles.get(k) not in READING_EXCLUDED],
                "body_keys": [k for k in kk if roles.get(k) not in BODY_EXCLUDED
                              and weights.get(k, 0) > 0 and not k.startswith("m:")],
                "word_counts": weights,
                "manual_body_words": sum(weights.get(k, 0) for k in kk
                                         if k.startswith("m:") and roles.get(k) not in BODY_EXCLUDED),
                "whole_text_override": rec.get("text_override") is not None,
            })
        after = {"articles": digest(art_path), "annotations": digest(ann_path)}
        if before != after:
            raise RuntimeError(f"{iid}: labels changed while snapshotting; retry")
        issues[iid] = {"source_sha256": before, "layout_sha256": layout_hashes,
                       "regions": regions, "records": records}
    return {"schema": "pulp-continuity-v1", "created_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "Human verified stories; modified records are diagnostics only."
                     " Frozen development labels, not an independent accuracy sample.",
            "definitions": {"reading_excluded_roles": sorted(READING_EXCLUDED),
                            "body_excluded_roles": sorted(BODY_EXCLUDED),
                            "manual_text": "Reported separately; not recoverable by a layout-box assembler."},
            "issues": issues}


def inversion_count(seq):
    """Count discordant retained gold-body pairs in O(n log n)."""
    tree = [0] * (len(seq) + 2)
    rank = {v: i + 1 for i, v in enumerate(sorted(seq))}
    total = 0
    for seen, v in enumerate(seq):
        p = rank[v]
        at_most = 0
        while p:
            at_most += tree[p]
            p -= p & -p
        total += seen - at_most
        p = rank[v]
        while p < len(tree):
            tree[p] += 1
            p += p & -p
    return total


def missing_runs(body, present, weights):
    runs = []
    start = None
    for i in range(len(body) + 1):
        missing = i < len(body) and body[i] not in present
        if missing and start is None:
            start = i
        elif not missing and start is not None:
            kk = body[start:i]
            runs.append({"keys": kk, "words": sum(weights[k] for k in kk),
                         "internal": start > 0 and i < len(body)})
            start = None
    return runs


def score_record(gold, candidates, regions):
    body = gold["body_keys"]
    body_set = set(body)
    weights = gold["word_counts"]
    expected = sum(weights[k] for k in body)
    prepared = []
    for rec in candidates:
        kk = keys(rec)
        roles = rec.get("roles") or {}
        reading = [k for k in kk if roles.get(k) not in READING_EXCLUDED]
        # For recall, a paragraph demoted to any non-body role is absent.
        predicted_body = [k for k in kk if roles.get(k) not in BODY_EXCLUDED]
        retained = body_set & set(predicted_body)
        covered = sum(weights[k] for k in retained)
        prepared.append((covered, rec, reading, predicted_body))
    # Prefer maximum retained story content, then the less contaminated record.
    # Do not require matching IDs: rules may renumber or split records.
    prepared.sort(key=lambda x: (-x[0], len(set(x[2]) - set(gold["reading_keys"])),
                                 x[1].get("article_id", "")))
    found = next((x for x in prepared if x[0] and x[1].get("type") in STORY_TYPES), None)
    covered, rec, reading, predicted_body = found or (0, {}, [], [])
    present = body_set & set(predicted_body)
    missing = missing_runs(body, present, weights)
    internal = [x for x in missing if x["internal"]]
    retained_sequence = unique(k for k in predicted_body if k in body_set)
    positions = {k: i for i, k in enumerate(body)}
    inversions = inversion_count([positions[k] for k in retained_sequence])
    # Ignore legitimate gold chapter apparatus between prose boxes; retain all
    # unexpected reading material, including ads, page numbers and other stories.
    ancillary = set(gold["reading_keys"]) - body_set
    continuity_sequence = [k for k in reading if k not in ancillary]
    links = set(zip(continuity_sequence, continuity_sequence[1:]))
    expected_links = list(zip(body, body[1:]))
    intact = sum((a, b) in links and a in present and b in present for a, b in expected_links)
    owners = []
    for n, (_, other, _, pb) in enumerate(prepared):
        overlap = body_set & set(pb)
        if other.get("type") in STORY_TYPES and overlap:
            owners.append({"article_id": other.get("article_id", str(n)),
                           "body_words": sum(weights[k] for k in overlap), "regions": len(overlap)})
    other_owners = [x for x in owners if x["article_id"] != rec.get("article_id")]
    extra = unique(k for k in reading if k not in set(gold["reading_keys"]))
    extra_words = sum(regions.get(k, {}).get("words", 0) for k in extra)
    return {
        "article_id": gold["article_id"], "title": gold.get("title"), "status": gold["status"],
        "matched_id": rec.get("article_id"), "matched_type": rec.get("type"),
        "body_words": expected, "retained_body_words": covered,
        "body_recall": covered / expected if expected else None,
        "missing_body_regions": [k for k in body if k not in present],
        "missing_runs": missing, "internal_missing_runs": len(internal),
        "edge_missing_words": sum(x["words"] for x in missing if not x["internal"]),
        "internal_missing_words": sum(x["words"] for x in internal),
        "longest_internal_missing_words": max((x["words"] for x in internal), default=0),
        "story_owner_count": len(owners), "other_story_owners": other_owners,
        "false_split": bool(other_owners),
        "retained_body_inversions": inversions,
        "retained_body_pairs": len(retained_sequence) * (len(retained_sequence) - 1) // 2,
        "expected_body_links": len(expected_links), "intact_body_links": intact,
        "body_link_recall": intact / len(expected_links) if expected_links else None,
        "extra_reading_regions": extra, "extra_reading_words": extra_words,
        "body_words_in_nonstory_records": sum(weights[k] for k in body_set & {
            k for _, r, _, pb in prepared if r.get("type") not in STORY_TYPES for k in pb}),
        "duplicate_reading_regions": len(reading) - len(set(reading)),
        "manual_body_words_excluded": gold.get("manual_body_words", 0),
    }


def summarize(rows):
    n = len(rows)
    total = sum(r["body_words"] for r in rows)
    retained = sum(r["retained_body_words"] for r in rows)
    links = sum(r["expected_body_links"] for r in rows)
    intact = sum(r["intact_body_links"] for r in rows)
    return {"stories": n, "body_words": total, "retained_body_words": retained,
            "body_word_recall": retained / total if total else None,
            "stories_with_all_body": sum(r["retained_body_words"] == r["body_words"] for r in rows),
            "stories_with_internal_gaps": sum(r["internal_missing_runs"] > 0 for r in rows),
            "internal_missing_words": sum(r["internal_missing_words"] for r in rows),
            "edge_missing_words": sum(r["edge_missing_words"] for r in rows),
            "longest_internal_missing_words": max((r["longest_internal_missing_words"] for r in rows), default=0),
            "false_split_stories": sum(r["false_split"] for r in rows),
            "stories_with_order_errors": sum(r["retained_body_inversions"] > 0 for r in rows),
            "retained_body_inversions": sum(r["retained_body_inversions"] for r in rows),
            "intact_body_links": intact, "expected_body_links": links,
            "body_link_recall": intact / links if links else None,
            "extra_reading_words": sum(r["extra_reading_words"] for r in rows),
            "body_words_in_nonstory_records": sum(r["body_words_in_nonstory_records"] for r in rows),
            "duplicate_reading_regions": sum(r["duplicate_reading_regions"] for r in rows)}


def evaluate(gold, directory):
    directory = Path(directory)
    rows, missing, excluded = [], [], []
    for iid, issue in gold["issues"].items():
        path = directory / iid / "articles.json"
        records = json.loads(path.read_text())["articles"] if path.exists() else []
        if not path.exists():
            missing.append(iid)
        for human in issue["records"]:
            if human.get("whole_text_override") or not human["body_keys"]:
                excluded.append({"issue": iid, "article_id": human["article_id"],
                                 "reason": "whole-text override" if human.get("whole_text_override") else "no scorable body"})
                continue
            row = score_record(human, records, issue["regions"])
            row["issue"] = iid
            rows.append(row)
    matches = defaultdict(list)
    for row in rows:
        if row["status"] == "verified" and row["matched_id"]:
            matches[row["issue"], row["matched_id"]].append(row["article_id"])
    merges = [{"issue": iid, "candidate_id": cid, "human_ids": aids}
              for (iid, cid), aids in sorted(matches.items()) if len(aids) > 1]
    return {"directory": str(directory.resolve()), "missing_issues": missing, "excluded": excluded,
            "candidate_records_matching_multiple_verified_stories": merges,
            "verified": summarize([r for r in rows if r["status"] == "verified"]),
            "modified_diagnostic_only": summarize([r for r in rows if r["status"] == "modified"]),
            "records": rows}


def compare(gold, baseline, candidate):
    before, after = evaluate(gold, baseline), evaluate(gold, candidate)
    b = {(r["issue"], r["article_id"]): r for r in before["records"]}
    changes = []
    for row in after["records"]:
        old = b[row["issue"], row["article_id"]]
        fields = ("retained_body_words", "internal_missing_words", "longest_internal_missing_words",
                  "story_owner_count", "retained_body_inversions", "intact_body_links", "extra_reading_words")
        delta = {k: row[k] - old[k] for k in fields if row[k] != old[k]}
        if delta:
            changes.append({"issue": row["issue"], "article_id": row["article_id"],
                            "title": row["title"], "status": row["status"], "delta": delta,
                            "continuity_regression": any((delta.get("retained_body_words", 0) < 0,
                                delta.get("internal_missing_words", 0) > 0,
                                delta.get("longest_internal_missing_words", 0) > 0,
                                delta.get("story_owner_count", 0) > 0,
                                delta.get("retained_body_inversions", 0) > 0,
                                delta.get("intact_body_links", 0) < 0)),
                            "contamination_regression": delta.get("extra_reading_words", 0) > 0})
    return {"schema": "pulp-continuity-comparison-v1", "gold_created_utc": gold.get("created_utc"),
            "baseline": before, "candidate": after, "changed_records": changes,
            "verified_continuity_regressions": sum(c["status"] == "verified" and c["continuity_regression"] for c in changes),
            "verified_contamination_regressions": sum(c["status"] == "verified" and c["contamination_regression"] for c in changes)}


def selftest():
    gold = {"article_id": "human", "status": "verified", "title": "Story",
            "body_keys": ["1:0", "1:1", "3:0"], "reading_keys": ["1:0", "1:1", "3:0"],
            "word_counts": {"1:0": 20, "1:1": 30, "3:0": 40}}
    regions = {k: {"words": w} for k, w in gold["word_counts"].items()}
    regions["2:0"] = {"words": 12}  # advertisement between continuation pages
    def rec(aid, seq, role=None):
        return {"article_id": aid, "type": "story", "fragments": [
            {"page": int(k.split(":")[0]), "region_ids": [int(k.split(":")[1])]} for k in seq],
            "roles": role or {}}
    full = rec("one", gold["body_keys"])
    row = score_record(gold, [full], regions)
    assert row["body_recall"] == 1 and row["intact_body_links"] == 2
    gap = score_record(gold, [rec("one", ["1:0", "3:0"])], regions)
    assert gap["internal_missing_words"] == 30 and gap["longest_internal_missing_words"] == 30
    split = score_record(gold, [rec("one", ["1:0", "1:1"]), rec("two", ["3:0"])], regions)
    assert split["false_split"] and split["retained_body_words"] == 50
    order = score_record(gold, [rec("one", ["1:1", "1:0", "3:0"])], regions)
    assert order["body_recall"] == 1 and order["retained_body_inversions"] == 1
    ad = score_record(gold, [rec("one", ["1:0", "1:1", "2:0", "3:0"])], regions)
    assert ad["extra_reading_words"] == 12 and ad["intact_body_links"] == 1
    demoted = score_record(gold, [rec("one", gold["body_keys"], {"1:1": "teaser"})], regions)
    assert demoted["internal_missing_words"] == 30
    wrong_type = rec("ad", gold["body_keys"])
    wrong_type["type"] = "ad"
    typed = score_record(gold, [wrong_type], regions)
    assert typed["body_recall"] == 0 and typed["body_words_in_nonstory_records"] == 90
    empty = score_record(gold, [], regions)
    assert empty["body_recall"] == 0 and empty["internal_missing_runs"] == 0
    assert inversion_count([2, 0, 1]) == 2
    print("PASS: intact continuation, internal gap, false split, reading order, advertisement insertion, role loss, nonstory ownership, missing candidate")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    snap = subs.add_parser("snapshot")
    snap.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    snap.add_argument("--issue", action="append")
    snap.add_argument("--out", type=Path)
    cmp = subs.add_parser("compare")
    cmp.add_argument("--gold", type=Path, required=True)
    cmp.add_argument("--baseline", type=Path, required=True)
    cmp.add_argument("--candidate", type=Path, required=True)
    cmp.add_argument("--out", type=Path)
    subs.add_parser("selftest")
    args = parser.parse_args()
    if args.command == "selftest":
        selftest()
        return
    if args.command == "snapshot":
        result = snapshot(args.project, args.issue)
    else:
        result = compare(json.loads(args.gold.read_text()), args.baseline, args.candidate)
        print(json.dumps({"baseline": result["baseline"]["verified"],
                          "candidate": result["candidate"]["verified"],
                          "verified_continuity_regressions": result["verified_continuity_regressions"]}, indent=2),
              file=sys.stderr)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
