#!/usr/bin/env python3
"""Rules 2.14, step 2f (5 November 2026): the paratext splits of R-A and R-B reach the issues the continuity guard
refused — decision §3.4 (ii) of docs/corpus-1.0-and-stage-2-design.md.

The upgrade to 2.14 (P52bq) installed 6,301 issues and refused 429 ("cannot safely retain cross-issue metadata for …":
a linked record whose identity the new candidate did not preserve). Those issues keep their assembly as it stands —
its record identities, its links, its hand corrections — and this script applies to it only what 2.14 would have
split out as paratext: for every refused active issue the 2.14 candidate is assembled in memory (nothing of it is
installed), the boxes that the candidate gives to a paratext record (an advertisement, a masthead, an ownership
statement, a recurring box) are taken out of the piece that owns them in the installed assembly, that piece's text is
rebuilt from its remaining boxes, and the paratext boxes become new records at the end of the file, with the
candidate's family, class and evidence and `split_from` naming the piece they left. Record identities do not change;
every box stays owned exactly once (checked); nothing else moves.

Applied to the rules file (data/assembly_v2/rules/<issue>/articles.json) and then to the live file (data/articles):
an issue without hand corrections is republished from the rules file by s13_publish; an issue with corrections
(data/annotations/<issue>.jsonl — the live file is held) has the same change made to its live file in place, leaving
alone any box or record a correction names (a moved box, a record whose text was set by hand). A piece whose text
cannot be rebuilt from its boxes exactly as it stands (an older assembly) has the paratext paragraphs removed from
its text instead; a box whose text is not found in the piece is left where it is and reported. Each touched file is
copied first to data/corpus/paratext-<date>/<issue>/. The documents carry `paratext_postprocess` (rules 2.14, the
date, the counts); their `backend` is unchanged, so the datasheet can say "2.13 + 2.14 paratext".

    python3 scripts/paratext_postprocess.py                      # the plan: every active issue not at rules 2.14, nothing written
    python3 scripts/paratext_postprocess.py --apply [--limit N] [--issues-file ids.json]
    python3 scripts/paratext_postprocess.py --selftest
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import s08_assemble_rules as S  # noqa: E402

DISPLAY_ROLES = {"title", "subtitle", "author", "teaser", "caption", "note"}
PIECE_TYPES = {"story", "feature", "letters", "poem"}
STAMP = time.strftime("%Y-%m-%d")
BACKUP = os.path.join(ROOT, "data", "corpus", f"paratext-{STAMP}")
OUT_DIR = os.path.join(ROOT, "pilot_export", "upgrade-2.14-2026-11-05")


def fkey(page, idx):
    return f"{page}:{idx}"


def candidate(iid, cfg_issue, root=ROOT):
    """The 2.14 candidate, assembled in memory (as scripts/compare_assembly.py does)."""
    pages = S.load_pages(iid) if root == ROOT else _load_pages(root, iid)
    if not pages:
        return None, None
    kind, book = S.issue_kind(iid, cfg_issue)
    mag = (cfg_issue or {}).get("magazine", "")
    A = S.analyse(pages, force_scan=S.toc_page_override(iid), magazine=mag)
    A["magazine"] = mag
    return pages, S.assemble(iid, pages, A, "rules", None, kind=kind, book=book)


def _load_pages(root, iid):
    import glob
    pages = {}
    for f in sorted(glob.glob(os.path.join(root, "data", "layout", iid, "page_*.json"))):
        p = json.load(open(f, encoding="utf-8"))
        p["regions"] = sorted(p["regions"], key=lambda r: r.get("order", 0))
        pages[p["page"]] = p
    return pages


def paratext_boxes(cand):
    """(page, idx) -> the candidate's paratext record that owns the box."""
    out = {}
    for rec in cand.get("articles", []):
        if not rec.get("paratext"):
            continue
        for fr in rec.get("fragments") or []:
            for i in fr.get("region_ids") or []:
                out[(fr["page"], i)] = rec
    return out


def owned_keys(doc):
    """Every (page, idx) a record of the document owns, as a sorted list (a box owned twice appears twice)."""
    keys = []
    for rec in doc.get("articles", []):
        for fr in rec.get("fragments") or []:
            keys += [(fr["page"], i) for i in fr.get("region_ids") or []]
    return sorted(keys)


def rebuild_text(pages, rec, remove=frozenset()):
    """The record's text and synopsis from its boxes in fragment order, as s08 builds them, without the boxes in
    remove; (None, None) when a box is missing from the layout."""
    body, syn = [], []
    roles = rec.get("roles") or {}
    for fr in rec.get("fragments") or []:
        regs = (pages.get(fr["page"]) or {}).get("regions")
        if regs is None:
            return None, None
        for i in fr.get("region_ids") or []:
            if (fr["page"], i) in remove or i >= len(regs):
                if i >= len(regs):
                    return None, None
                continue
            role = roles.get(fkey(fr["page"], i))
            if role in DISPLAY_ROLES:
                continue
            t = S.region_text(regs[i])
            (syn if role == "synopsis" else body).append(t)
    return S.clean_text(S.join_boxes(body)), (S.clean_text(S.join_boxes(syn)) if syn else None)


def annotation_refs(path):
    """The boxes and records a correction log names: (frag keys, record ids whose text was set by hand)."""
    keys, set_text = set(), set()
    if not os.path.exists(path):
        return keys, set_text
    for line in open(path, encoding="utf-8"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        # webapp/app.py ann_append: {"ts", "user", "issue", "action", **payload}; a payload names boxes as "frag" or
        # "frags" ("page:idx") and records as "article_id" / "to_id" / "from_id"
        if isinstance(ev.get("frag"), str):
            keys.add(ev["frag"])
        if isinstance(ev.get("frags"), list):
            keys.update(x for x in ev["frags"] if isinstance(x, str))
        if ev.get("action") == "set_text" and ev.get("article_id"):
            set_text.add(ev["article_id"])
    return keys, set_text


def next_number(doc, iid):
    n = 0
    for rec in doc.get("articles", []):
        m = re.fullmatch(re.escape(iid) + r"_a(\d+)", rec.get("article_id") or "")
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def apply_doc(doc, pages, para, iid, protected=frozenset(), set_text=frozenset(), status_default=None):
    """Apply the paratext splits to one document in place. Returns the statistics; the document is unchanged when
    nothing applies. Every box stays owned exactly once (checked against the document as it was)."""
    before = owned_keys(doc)
    stats = {"pieces_changed": 0, "boxes_removed": 0, "words_removed": 0, "records_added": 0, "boxes_kept_protected": 0,
             "boxes_kept_unmatched": 0, "pieces_text_set_by_hand": 0, "rebuilt_exact": 0, "rebuilt_by_paragraph": 0}
    taken = {}            # candidate paratext record id -> [(page, idx, old record id)]
    for rec in doc.get("articles", []):
        if rec.get("type") not in PIECE_TYPES:
            continue
        hits = [(fr["page"], i) for fr in rec.get("fragments") or [] for i in fr.get("region_ids") or [] if (fr["page"], i) in para]
        if not hits:
            continue
        if rec.get("article_id") in set_text:
            stats["pieces_text_set_by_hand"] += 1
            rec.setdefault("flags", []).append("rules 2.14 paratext post-process: paratext boxes left in place because the text of this record was set by hand")
            continue
        hits_ok = [h for h in hits if fkey(*h) not in protected]
        stats["boxes_kept_protected"] += len(hits) - len(hits_ok)
        if not hits_ok:
            continue
        exact, _ = rebuild_text(pages, rec)
        old_text = rec.get("text") or ""
        remove = set()
        if exact is not None and exact == old_text:
            remove = set(hits_ok)
            new_text, new_syn = rebuild_text(pages, rec, remove)
            stats["rebuilt_exact"] += 1
        else:
            # an older assembly whose text this code does not reproduce exactly: take the boxes' paragraphs out of the
            # text as it stands; a box whose text is not in the record stays where it is
            new_text = old_text
            for h in hits_ok:
                t = S.clean_text(S.join_boxes([S.region_text(pages[h[0]]["regions"][h[1]])]))
                if t and t in new_text:
                    new_text = new_text.replace(t, "", 1)
                    remove.add(h)
                else:
                    stats["boxes_kept_unmatched"] += 1
            new_text = re.sub(r"\n{3,}", "\n\n", new_text).strip()
            new_syn = rec.get("synopsis")
            if remove:
                stats["rebuilt_by_paragraph"] += 1
        if not remove:
            continue
        words_before = len(old_text.split())
        for h in sorted(remove):
            taken.setdefault(para[h]["article_id"], []).append((h[0], h[1], rec["article_id"]))
        frags = []
        for fr in rec.get("fragments") or []:
            ids = [i for i in fr.get("region_ids") or [] if (fr["page"], i) not in remove]
            if ids:
                frags.append({"page": fr["page"], "region_ids": ids})
        rec["fragments"] = frags
        rec["pages"] = sorted({fr["page"] for fr in frags})
        for h in remove:
            (rec.get("roles") or {}).pop(fkey(*h), None)
        rec["text"] = new_text
        if new_syn:
            rec["synopsis"] = new_syn
        rec["n_regions"] = sum(len(fr["region_ids"]) for fr in frags)
        rec.setdefault("flags", []).append(f"rules 2.14 paratext post-process ({STAMP}): {len(remove)} paratext box(es), "
                                           f"{words_before - len(new_text.split())} words, split out of this record; its identity unchanged")
        stats["pieces_changed"] += 1
        stats["boxes_removed"] += len(remove)
        stats["words_removed"] += words_before - len(new_text.split())
    n = next_number(doc, iid)
    for cid, boxes in taken.items():
        src = next(r for r in para.values() if r["article_id"] == cid)
        boxes.sort()
        frags = {}
        for page, i, _ in boxes:
            frags.setdefault(page, []).append(i)
        new = {"article_id": f"{iid}_a{n:03d}", "type": src.get("type", "ad"), "title": src.get("title"), "author": None,
               "pages": sorted(frags), "fragments": [{"page": p, "region_ids": frags[p]} for p in sorted(frags)], "roles": {},
               "flags": list(src.get("flags") or []) + [f"rules 2.14 paratext post-process ({STAMP}): split out of "
                                                        f"{', '.join(sorted({b[2] for b in boxes}))}, whose assembly the continuity guard holds"],
               "paratext": src.get("paratext"), "split_from": sorted({b[2] for b in boxes}), "toc": None, "keys": []}
        for k in ("recurrence", "ad_class", "advertiser", "department", "title_as_printed"):
            if src.get(k) is not None:
                new[k] = copy.deepcopy(src[k])
        new["text"], _ = rebuild_text(pages, new)
        new["n_regions"] = len(boxes)
        new.pop("keys", None)
        if status_default is not None:
            new["status"] = status_default
        doc["articles"].append(new)
        stats["records_added"] += 1
        n += 1
    after = owned_keys(doc)
    if after != before:
        raise ValueError(f"{iid}: the boxes owned changed in number — {len(before)} before, {len(after)} after; nothing written")
    if stats["pieces_changed"] or stats["records_added"]:
        doc["paratext_postprocess"] = {"rules": S.VERSION, "date": STAMP, "script": "scripts/paratext_postprocess.py", **{k: v for k, v in stats.items() if v}}
    return stats


def process_issue(iid, cfg_issue, apply=False, root=ROOT, log=print):
    rules_p = os.path.join(root, "data", "assembly_v2", "rules", iid, "articles.json")
    live_p = os.path.join(root, "data", "articles", iid, "articles.json")
    ann_p = os.path.join(root, "data", "annotations", f"{iid}.jsonl")
    if not os.path.exists(rules_p):
        return {"issue": iid, "error": "no rules file"}
    pages, cand = candidate(iid, cfg_issue, root)
    if not pages:
        return {"issue": iid, "error": "no layout pages"}
    para = paratext_boxes(cand)
    doc = json.load(open(rules_p, encoding="utf-8"))
    row = {"issue": iid, "backend": doc.get("backend"), "candidate_paratext_boxes": len(para), "held": os.path.exists(ann_p) and os.path.exists(live_p)}
    if doc.get("paratext_postprocess"):
        row["error"] = "already post-processed"
        return row
    rules_stats = apply_doc(doc, pages, para, iid)
    row["rules"] = rules_stats
    live_doc, live_stats = None, None
    if row["held"] and os.path.exists(live_p):
        protected, set_text = annotation_refs(ann_p)
        live_doc = json.load(open(live_p, encoding="utf-8"))
        status_default = next((r.get("status") for r in live_doc.get("articles", []) if r.get("type") in ("ad", "house") and r.get("status")), None)
        live_stats = apply_doc(live_doc, pages, para, iid, protected={k for k in protected}, set_text=set_text, status_default=status_default)
        row["live"] = live_stats
    if not apply or not (rules_stats["pieces_changed"] or rules_stats["records_added"]):
        row["written"] = False
        return row
    from article_guard import article_write_lock
    from corpus_lib import write_json_atomic
    with article_write_lock(root, iid):
        bdir = os.path.join(BACKUP if root == ROOT else os.path.join(root, "data", "corpus", f"paratext-{STAMP}"), iid)
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(rules_p, os.path.join(bdir, "rules.before.json"))
        write_json_atomic(rules_p, doc)
        if live_doc is not None:
            shutil.copy2(live_p, os.path.join(bdir, "live.before.json"))
            write_json_atomic(live_p, live_doc)
            row["published"] = "held: the live file changed in place"
        elif root == ROOT:
            import s13_publish as s13
            from corpus_lib import settings
            pub = settings().get("publish", {})
            # the issue's write lock is already held here: publish_issue would take it a second time on another
            # file handle and wait for itself for ever (11 November 2026: the first application stopped after one issue
            # and held the site refresh behind the same lock), so the locked body is called directly
            what = s13._publish_issue_locked(iid, pub.get("prefer", "rules_flagged"), float(pub.get("look_below", .9)), force=True)
            row["published"] = what[0] if isinstance(what, tuple) else str(what)
    row["written"] = True
    return row


def report(rows, apply):
    L = [f"# Rules 2.14 paratext post-process over the issues the continuity guard holds ({time.strftime('%Y-%m-%d %H:%M')}; {'applied' if apply else 'plan'})", "",
         "| issue | backend | held | pieces changed | boxes out | words out | records added | kept (protected / unmatched / hand text) | published |", "|---|---|---|---|---|---|---|---|---|"]
    tot = {"issues": 0, "changed": 0, "boxes": 0, "words": 0, "added": 0, "held": 0, "errors": 0}
    for r in rows:
        if r.get("error"):
            L.append(f"| {r['issue']} | {r.get('backend', '')} | | {r['error']} | | | | | |")
            tot["errors"] += 1
            continue
        s = r["live"] if r.get("live") else r["rules"]
        tot["issues"] += 1
        tot["changed"] += s["pieces_changed"]; tot["boxes"] += s["boxes_removed"]; tot["words"] += s["words_removed"]; tot["added"] += s["records_added"]
        tot["held"] += 1 if r.get("held") else 0
        L.append(f"| {r['issue']} | {r.get('backend', '')} | {'yes' if r.get('held') else ''} | {s['pieces_changed']} | {s['boxes_removed']} | {s['words_removed']:,} | {s['records_added']} | "
                 f"{s['boxes_kept_protected']} / {s['boxes_kept_unmatched']} / {s['pieces_text_set_by_hand']} | {r.get('published', '')} |")
    L.insert(2, f"{tot['issues']:,} issues read ({tot['held']} held by hand corrections, {tot['errors']} errors): {tot['changed']:,} pieces changed, "
                f"{tot['boxes']:,} paratext boxes out, {tot['words']:,} words out, {tot['added']:,} records added.")
    L.insert(3, "")
    return "\n".join(L) + "\n", tot


def restore_issue(iid, root=ROOT, publish=True):
    """Undo the post-process for one issue (12 November 2026: the 16 issues of the trial go back to their state before
    it, so that the continuity guard judges rules 2.15 against the assembly it held, not against a hand-made mixture).
    The rules file comes back from data/corpus/paratext-<date>/<issue>/rules.before.json (the earliest such backup),
    the live file from live.before.json where the issue is held by hand corrections; the post-processed files are
    kept beside the backups (rules.postprocessed.json, live.postprocessed.json): nothing is deleted. An issue whose
    corrections changed after the post-process is left as it is, for a person to decide."""
    import glob
    rules_p = os.path.join(root, "data", "assembly_v2", "rules", iid, "articles.json")
    live_p = os.path.join(root, "data", "articles", iid, "articles.json")
    ann_p = os.path.join(root, "data", "annotations", f"{iid}.jsonl")
    row = {"issue": iid}
    try:
        doc = json.load(open(rules_p, encoding="utf-8"))
    except (OSError, ValueError):
        return dict(row, error="no rules file")
    if not doc.get("paratext_postprocess"):
        return dict(row, error="not post-processed: nothing to restore")
    dirs = sorted(d for d in glob.glob(os.path.join(root, "data", "corpus", "paratext-*", iid)) if os.path.exists(os.path.join(d, "rules.before.json")))
    if not dirs:
        return dict(row, error="no rules.before.json backup")
    bdir = dirs[0]
    before = json.load(open(os.path.join(bdir, "rules.before.json"), encoding="utf-8"))
    if before.get("paratext_postprocess"):
        return dict(row, error="the backup is itself post-processed")
    live_before = os.path.join(bdir, "live.before.json")
    if os.path.exists(ann_p) and os.path.getmtime(ann_p) > os.path.getmtime(rules_p) + 1:
        return dict(row, error="hand corrections changed after the post-process: left as it is")
    from article_guard import article_write_lock
    from corpus_lib import write_json_atomic
    with article_write_lock(root, iid):
        shutil.copy2(rules_p, os.path.join(bdir, "rules.postprocessed.json"))
        write_json_atomic(rules_p, before)
        row["backend"] = before.get("backend")
        if os.path.exists(live_before):
            shutil.copy2(live_p, os.path.join(bdir, "live.postprocessed.json"))
            write_json_atomic(live_p, json.load(open(live_before, encoding="utf-8")))
            row["published"] = "held: the live file restored in place"
        elif publish and root == ROOT:
            import s13_publish as s13
            from corpus_lib import settings
            pub = settings().get("publish", {})
            what = s13._publish_issue_locked(iid, pub.get("prefer", "rules_flagged"), float(pub.get("look_below", .9)), force=True)
            row["published"] = what[0] if isinstance(what, tuple) else str(what)
    row["restored"] = True
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true", help="undo the post-process for the issues of --issues-file (their backups come back; nothing is deleted)")
    ap.add_argument("--issues-file")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.restore:
        if not a.issues_file or a.apply:
            ap.error("--restore needs --issues-file and excludes --apply")
        rows = []
        for iid in json.load(open(a.issues_file, encoding="utf-8")):
            try:
                rows.append(restore_issue(iid))
            except Exception as exc:
                rows.append({"issue": iid, "error": f"{type(exc).__name__}: {exc}"})
            r = rows[-1]
            print(f"{iid}: " + (f"restored ({r.get('backend')}; {r.get('published', 'not published')})" if r.get("restored") else f"NOT restored — {r.get('error')}"), flush=True)
        print(f"restored {sum(1 for r in rows if r.get('restored'))} of {len(rows)}")
        sys.exit(0 if all(r.get("restored") for r in rows) else 1)
    from corpus_lib import corpus_config, active_issues
    cfg = {i["id"]: i for i in corpus_config().get("issues", [])}
    active, _ = active_issues(list(cfg.values()))
    if a.issues_file:
        ids = json.load(open(a.issues_file, encoding="utf-8"))
    else:
        ids = []
        for i in active:
            p = os.path.join(ROOT, "data", "assembly_v2", "rules", i["id"], "articles.json")
            if os.path.exists(p):
                try:
                    b = json.load(open(p, encoding="utf-8")).get("backend") or ""
                except ValueError:
                    continue
                if not b.startswith(f"rules_v{S.VERSION}"):
                    ids.append(i["id"])
    ids = ids[:a.limit] if a.limit else ids
    print(f"[paratext_postprocess] {len(ids)} issues not at rules {S.VERSION}; {'applying' if a.apply else 'plan only'}", flush=True)
    rows = []
    t0 = time.time()
    for n, iid in enumerate(ids, 1):
        try:
            rows.append(process_issue(iid, cfg.get(iid), apply=a.apply))
        except Exception as exc:
            rows.append({"issue": iid, "error": f"{type(exc).__name__}: {exc}"})
        if n % 25 == 0 or n == len(ids):
            print(f"[{n}/{len(ids)}] {time.time() - t0:.0f}s", flush=True)
    os.makedirs(a.out_dir, exist_ok=True)
    md, tot = report(rows, a.apply)
    name = "paratext_postprocess" + ("" if a.apply else "_plan")
    open(os.path.join(a.out_dir, name + ".md"), "w", encoding="utf-8").write(md)
    json.dump(rows, open(os.path.join(a.out_dir, name + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(md.splitlines()[2])
    print(f"written {os.path.relpath(a.out_dir, ROOT)}/{name}.md and .json")


def selftest():
    """An ownership statement inside a story, assembled with the 2.14 rules off (the held assembly) and on (the
    candidate): the post-process takes the statement out, adds the house record, keeps every box owned once."""
    import tempfile
    body = ("He watched the road while she stood beside him. They had travelled far and he knew that his friends were waiting beyond the forest. ") * 12
    body2 = ("the river ran high that spring and the men worked the ford in silence, waiting for the water to fall before the wagons could cross. ") * 10
    own = ("STATEMENT OF THE OWNERSHIP, MANAGEMENT, CIRCULATION, ETC., REQUIRED BY THE ACT OF CONGRESS OF AUGUST 24, 1912. Of Weird Tales, "
           "published monthly at Indianapolis, Indiana, for October 1, 1936. State of Indiana, County of Marion, ss. Before me, a Notary Public in and "
           "for the State and county aforesaid, personally appeared William Sprenger, who, having been duly sworn according to law, deposes and says "
           "that he is the Business Manager. 1. That the names and addresses of the publisher, editor, managing editor, and business managers are: "
           "Publisher, Popular Fiction Publishing Co. 3. That the known bondholders, mortgagees, and other security holders owning or holding 1 per cent "
           "or more of total amount of bonds are: None.")

    def box(text, y, label="Text", h=100):
        return {"text": text, "label": label, "bbox": [100, y, 900, y + h], "order": y}
    regs = [box("THE BLACK STONE", 100, "SectionHeader"), box("By Robert E. Howard", 200), box(body, 300, h=500), box(own, 820, h=300),
            box(body2, 1140, h=150), box("1", 1330, "PageFooter", 30)]
    pages = {1: {"page": 1, "width": 1000, "height": 1400, "regions": regs}}
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "data", "layout", "x")); os.makedirs(os.path.join(d, "data", "assembly_v2", "rules", "x"))
        json.dump(pages[1], open(os.path.join(d, "data", "layout", "x", "page_0001.json"), "w"))
        S.RULES_214["on"] = False
        try:
            held = S.assemble("x", pages, S.analyse(pages))
        finally:
            S.RULES_214["on"] = True
        held["backend"] = "rules_v2.13"
        json.dump(held, open(os.path.join(d, "data", "assembly_v2", "rules", "x", "articles.json"), "w"))
        story = next(r for r in held["articles"] if r["type"] == "story")
        assert "Notary Public" in story["text"]
        row = process_issue("x", {"id": "x", "magazine": "Weird Tales"}, apply=True, root=d, log=lambda *a: None)
        assert row["written"] and row["rules"]["pieces_changed"] == 1 and row["rules"]["records_added"] == 1 and row["rules"]["rebuilt_exact"] == 1, row
        after = json.load(open(os.path.join(d, "data", "assembly_v2", "rules", "x", "articles.json")))
        s2 = next(r for r in after["articles"] if r["type"] == "story")
        assert "Notary Public" not in s2["text"] and "wagons could cross" in s2["text"] and s2["article_id"] == story["article_id"], s2["text"][:80]
        new = [r for r in after["articles"] if r.get("split_from")]
        assert len(new) == 1 and new[0]["paratext"] == "ownership statement" and "Notary Public" in new[0]["text"] and new[0]["split_from"] == [story["article_id"]], new
        assert after["backend"] == "rules_v2.13" and after["paratext_postprocess"]["rules"] == S.VERSION
        assert owned_keys(after) == owned_keys(held)
        assert os.path.exists(os.path.join(d, "data", "corpus", f"paratext-{STAMP}", "x", "rules.before.json"))
        # the second pass does nothing
        row2 = process_issue("x", {"id": "x", "magazine": "Weird Tales"}, apply=True, root=d, log=lambda *a: None)
        assert row2.get("error") == "already post-processed", row2
        # the restore brings the held assembly back, keeps the post-processed file, and does nothing the second time
        r3 = restore_issue("x", root=d)
        back = json.load(open(os.path.join(d, "data", "assembly_v2", "rules", "x", "articles.json")))
        assert r3.get("restored") and back == held and not back.get("paratext_postprocess"), r3
        assert json.load(open(os.path.join(d, "data", "corpus", f"paratext-{STAMP}", "x", "rules.postprocessed.json"))) == after
        assert restore_issue("x", root=d).get("error", "").startswith("not post-processed")
    print("selftest ok")
    return True


if __name__ == "__main__":
    main()
