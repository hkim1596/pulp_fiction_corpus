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
with no counterpart get fresh ids after the live numbering. The old live
file is kept under data/assembly_archive/<stamp>_refresh/. Use it when the
rules gain fields (ad_class, chapters, author_as_printed …) or fix small
things after people have begun to annotate.

Two rules protect the annotators' work (added 2026-09-04, after a forced
refresh changed records people had verified):

  * A VERIFIED record (a verify event with no unverify after it) is never
    changed by a refresh: its live machine record — fragments, roles,
    fields — is carried into the new file as it is, and the regions it
    holds are taken away from the candidate records that wanted them.
    Regions a candidate would have added to a verified record go to the
    file's `unsorted` list, where the workbench shows them for a person to
    pull in. There is no flag to override this: to let the machine at a
    verified record, a person unverifies it on the site first.
  * Every other ANNOTATED record (named by any event) must keep exactly
    its regions, else the issue is refused — unless --force, which goes on
    and REPORTS each such record (the replay re-places moved regions by
    key, so the person's actions hold, but the record's machine regions
    differ from what they saw).

--verified-from DIR: the verified state of a record is what the person saw
when they verified it. After a refresh has already changed verified records
(as on 2026-09-04), the records verified BEFORE that refresh live in its
kept copy, data/assembly_archive/<stamp>_refresh/; pass that directory and
the script takes, for each verified record, the copy from there when the
verification is older than the directory's stamp, else the live one.

    python3 scripts/switch_assembly.py --variant rules --refresh --dry-run
    python3 scripts/switch_assembly.py --variant rules --refresh
    python3 scripts/switch_assembly.py --variant rules --refresh --force
    python3 scripts/switch_assembly.py --variant rules --refresh --force \\
            --verified-from data/assembly_archive/20260904_100220_refresh
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


def read_events(iid):
    out = []
    p = os.path.join(ROOT, "data", "annotations", f"{iid}.jsonl")
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def annotated_ids(iid):
    """Every article id an annotation event names (the record acted on, the
    target of a move or a merge)."""
    ids = set()
    for e in read_events(iid):
        for k in ("article_id", "to_id", "into_id"):
            v = e.get(k)
            if v and v != "new":
                ids.add(v)
    return ids


def verified_ids(iid):
    """article id -> time of its last verify, for records verified and not
    unverified since (in log order)."""
    ver = {}
    for e in read_events(iid):
        aid = e.get("article_id")
        if not aid:
            continue
        if e.get("action") == "verify":
            ver[aid] = e.get("ts") or ""
        elif e.get("action") == "unverify":
            ver.pop(aid, None)
    return ver


def stamp_of(path):
    """'20260904_100220' from an archive directory name -> '2026-09-04T10:02:20'."""
    m = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", os.path.basename(path.rstrip("/")))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}" if m else None


def verified_state(iid, live, ver, verified_from):
    """The machine record each verified id keeps: the live one, or — when the
    verification is older than the kept copy under --verified-from — the
    copy from there (what the person saw)."""
    live_by_id = {a["article_id"]: a for a in live.get("articles", [])}
    out = {}
    src = {}
    old_by_id = {}
    old_stamp = None
    if verified_from:
        p = os.path.join(verified_from, "articles", iid, "articles.json")
        if os.path.exists(p):
            old_by_id = {a["article_id"]: a for a in json.load(open(p, encoding="utf-8")).get("articles", [])}
            old_stamp = stamp_of(verified_from)
    for aid, ts in ver.items():
        if old_by_id and old_stamp and aid in old_by_id and ts < old_stamp:
            out[aid], src[aid] = old_by_id[aid], "kept copy"
        elif aid in live_by_id:
            out[aid], src[aid] = live_by_id[aid], "live"
    return out, src


def strip_regions(rec, taken):
    """rec without the regions in `taken` (a set of (page, idx)); the record's
    text/pages are recomputed by nobody here — s07's index and the site read
    fragments, and r00 re-exports — so only fragments and roles change."""
    rec = dict(rec)
    frags = []
    for f in rec.get("fragments", []):
        ids = [r for r in f.get("region_ids", []) if (f["page"], r) not in taken]
        if ids:
            frags.append({**f, "region_ids": ids})
    rec["fragments"] = frags
    rec["pages"] = sorted({f["page"] for f in frags})
    if rec.get("roles"):
        rec["roles"] = {k: v for k, v in rec["roles"].items() if tuple(int(x) for x in k.split(":")) not in taken}
    rec["n_regions"] = sum(len(f["region_ids"]) for f in frags)
    return rec


def refresh_map(iid, live, cand, force=False, verified_from=None):
    """(new candidate records with live ids, notes, refused reasons, report).

    report: one line per annotated record, saying whether its machine regions
    are unchanged, changed (how), or kept as verified."""
    live_by_id = {a["article_id"]: a for a in live.get("articles", [])}
    live_sets = {aid: frag_set(a) for aid, a in live_by_id.items()}
    ver = verified_ids(iid)
    keep, keep_src = verified_state(iid, live, ver, verified_from)
    # 1. verified records first: they are carried over as they are; their regions leave every candidate
    protected = set()
    for aid, rec in keep.items():
        protected |= frag_set(rec)
    cand_recs = []
    unsorted_extra = []
    for c in cand.get("articles", []):
        s = frag_set(c)
        if s & protected:
            # what the candidate would have put into (or beside) a verified record: taken away; if the
            # candidate was mostly that record, its leftovers are shown as unsorted for a person
            leftover = strip_regions(c, protected)
            if not leftover["fragments"]:
                continue
            if len(s & protected) / len(s) >= 0.5:
                for f in leftover["fragments"]:
                    unsorted_extra.append({"page": f["page"], "segments": f["region_ids"],
                                           "why": f"the machine would add these to the verified record; a person decides"})
                continue
            cand_recs.append(leftover)
        else:
            cand_recs.append(c)
    # 2. ids: same regions -> same id; else best overlap >= 0.5; else a fresh id
    by_set = {}
    for aid, s in live_sets.items():
        by_set.setdefault(s, aid)
    touched = annotated_ids(iid)
    taken = set(keep)
    out = []
    notes = []
    pending = []
    for c in cand_recs:
        s = frag_set(c)
        aid = by_set.get(s)
        if aid and aid not in taken:
            taken.add(aid)
            out.append((aid, c, "same regions"))
        else:
            pending.append((c, s))
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
    # 3. the annotated records: refused unless forced; every one reported
    refused = []
    report = []
    for aid in sorted(touched):
        if aid not in live_by_id:
            continue                                   # a human-made record (_u###): lives in the annotation log itself
        if aid in keep:
            report.append(f"{aid}: verified {ver[aid][:16]} — kept as verified ({keep_src[aid]})")
            continue
        hit = next((x for x in out if x[0] == aid), None)
        if hit is None:
            refused.append(f"{aid} (annotated) has no counterpart in the new run")
            report.append(f"{aid}: annotated — NO COUNTERPART in the new run")
        elif hit[2] != "same regions":
            s_old, s_new = live_sets[aid], frag_set(hit[1])
            gained = sorted(s_new - s_old)
            lost = sorted(s_old - s_new)
            how = (f"+{len(gained)} region(s) {['%d:%d' % g for g in gained[:6]]}" if gained else "") + \
                  (" " if gained and lost else "") + (f"-{len(lost)} region(s) {['%d:%d' % g for g in lost[:6]]}" if lost else "")
            refused.append(f"{aid} (annotated): {hit[2]} {how}")
            report.append(f"{aid}: annotated — REGIONS CHANGE {how}")
        else:
            report.append(f"{aid}: annotated — regions unchanged")
    if refused and not force:
        return None, notes, refused, report, None
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
    # the verified records go in where they belong in reading order (first page, first region)
    for aid, rec in keep.items():
        rec = dict(rec)
        rec.setdefault("flags", [])
        if "kept as verified through a refresh" not in " ".join(rec["flags"]):
            rec["flags"] = list(rec["flags"]) + [f"kept as verified through a refresh ({time.strftime('%Y-%m-%d')})"]
        final.append(rec)

    def first_key(a):
        s = frag_set(a)
        return min(s) if s else (10 ** 6, 0)
    final.sort(key=first_key)
    extra = {"unsorted": unsorted_extra, "kept": {aid: keep_src[aid] for aid in keep}}
    return final, notes, (refused if force else []), report, extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="rules", choices=["rules", "rules_on_model"])
    ap.add_argument("--issue")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="replace the live records, keep ids and annotation logs")
    ap.add_argument("--force", action="store_true", help="with --refresh: go on even when an annotated (not verified) record changed")
    ap.add_argument("--verified-from", help="with --refresh: the kept copy (data/assembly_archive/<stamp>_refresh) whose records "
                                            "are the verified state of records verified before its stamp")
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
    vfrom = None
    if args.verified_from:
        vfrom = args.verified_from if os.path.isabs(args.verified_from) else os.path.join(ROOT, args.verified_from)
        if not os.path.isdir(vfrom) or not stamp_of(vfrom):
            print(f"[refresh] --verified-from {args.verified_from}: not a kept copy (needs a <stamp>_refresh directory)")
            return
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
        final, notes, refused, report, extra = refresh_map(iid, live, cand, force=args.force, verified_from=vfrom)
        if final is None:
            print(f"[refresh] {iid}: REFUSED — an annotated record would change:")
            for r in refused:
                print(f"           {r}")
            print("           (pass --force to refresh anyway; the replay re-places moved regions by key)")
            for r in report:
                print(f"           {r}")
            continue
        n_same = sum(1 for a in final if any(frag_set(a) == frag_set(b) and a["article_id"] == b["article_id"] for b in live["articles"]))
        print(f"[refresh] {iid}: {len(live['articles'])} live -> {len(final)} records; {n_same} unchanged, "
              f"{len(notes)} changed or new; {len(extra['kept'])} kept as verified"
              + (f"; forced past {len(refused)} annotated change(s)" if refused else "")
              + (f"; {len(extra['unsorted'])} region group(s) left unsorted beside verified records" if extra["unsorted"] else ""))
        for r in report:
            print(f"           {r}")
        for nt in notes[:12]:
            print(f"           {nt}")
        if len(notes) > 12:
            print(f"           … and {len(notes) - 12} more")
        plan.append((iid, live_p, live, cand, final, extra))
    if args.dry_run:
        print("[refresh] dry run: nothing changed")
        return
    if not plan:
        print("[refresh] nothing to do")
        return
    for iid, live_p, live, cand, final, extra in plan:
        keep = os.path.join(arch, "articles", iid, "articles.json")
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        shutil.copy2(live_p, keep)
        out = dict(cand)
        out["articles"] = final
        if extra["unsorted"]:
            out["unsorted"] = list(cand.get("unsorted") or []) + extra["unsorted"]
        if extra["kept"]:
            # regions the verified records hold are theirs: not furniture, not unsorted, in no other record
            held = set()
            for a in final:
                if a["article_id"] in extra["kept"]:
                    held |= frag_set(a)
            out["furniture"] = [f for f in out.get("furniture", [])
                                if not ((f["page"], f["idx"]) in held if f.get("idx") is not None
                                        else any((f["page"], i) in held for i in f.get("segments", [])))]
            out["unsorted"] = [u for u in out.get("unsorted", [])
                               if not any((u["page"], i) in held for i in (u.get("segments") or u.get("region_ids") or []))]
        out["refreshed"] = {"from": live.get("backend"), "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kept_copy": os.path.relpath(keep, ROOT),
                            "verified_kept": extra["kept"]}
        json.dump(out, open(live_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.makedirs(arch, exist_ok=True)
    with open(os.path.join(arch, "README.txt"), "w", encoding="utf-8") as f:
        f.write(f"Kept {time.strftime('%Y-%m-%d %H:%M:%S')} by scripts/switch_assembly.py --refresh --variant {args.variant}\n"
                f"issues: {', '.join(p[0] for p in plan)}\n"
                "articles/ = the live records before the refresh (same ids; the annotation logs were not touched).\n"
                "Verified records were carried over unchanged (see 'refreshed.verified_kept' in each live file).\n"
                "To undo: copy articles/<issue>/articles.json back to data/articles/<issue>/.\n")
    from s07_articles import build_index
    build_index()
    print(f"[refresh] done; the previous live records are kept under data/assembly_archive/{stamp}_refresh")


if __name__ == "__main__":
    main()
