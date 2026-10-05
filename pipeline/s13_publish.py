#!/usr/bin/env python3
"""s13 — publish the corpus's assembled records to the website (Heejin, 5 October 2026: "What I expect is whole corpus
now run on the same way. Show them by authors, Magazines, issues, and stories. And Workbench shows how they are
assembled automatically, and show confidence scores and flags when automation is not so sure. Human annotator will
fix some of them and the algorithm can improve accordingly. … It must lively update everything.")

The site's workbench and explorer read an issue's records from data/articles/<id>/articles.json, the "live" assembly
on which people's corrections are replayed. For every assembled corpus issue this stage writes that file from the best
assembly there is:

    data/assembly_v2/llm/<id>/articles.json     the rules' records checked by the language model (s12 --follow)
    data/assembly_v2/rules/<id>/articles.json   the rules' records (s08), until the model has checked the issue

and adds to every record what the site shows about how sure the automation is:

    assembly     "rules, checked by the model" or "rules (not yet checked by the model)"
    confidence   the model's lowest confidence on the record's boxes (None until checked)
    flags        the rules' own notes, each change the model made to the record, every decision the model left open
                 on one of its boxes (s12's flags.jsonl)
    needs_look   true when the model changed the record, left a decision on it open, or was under
                 settings.publish.look_below sure of it

When the rules' records changed after the model checked them (the cross-issue pass adds serial links), the model's
records are first rebuilt from its stored decisions (s12 rebuild_issue; no model is asked). An issue someone has
corrected (data/annotations/<id>.jsonl) is not written again: the corrections are replayed on the records they were
made on. Rerun freely: an issue is written only when its source is newer than the live file.

    python3 pipeline/s13_publish.py              # every assembled corpus issue whose source changed
    python3 pipeline/s13_publish.py --issue <id>
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import ROOT, settings, all_states, event, log, write_json_atomic  # noqa: E402

LIVE = os.path.join(ROOT, "data", "articles")
ANN = os.path.join(ROOT, "data", "annotations")


def _mt(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _open_decisions(var_dir):
    """region key -> the open decision the model left on it (from flags.jsonl and the page keymaps)."""
    fj, pj = os.path.join(var_dir, "flags.jsonl"), os.path.join(var_dir, "pages.jsonl")
    if not os.path.exists(fj) or not os.path.exists(pj):
        return {}
    keymaps = {}
    for line in open(pj, encoding="utf-8"):
        try:
            r = json.loads(line)
            keymaps[r["page"]] = r.get("keymap") or {}
        except Exception:
            pass
    out = {}
    for line in open(fj, encoding="utf-8"):
        try:
            f = json.loads(line)
        except Exception:
            continue
        km = keymaps.get(f.get("page"), {})
        for k, b in (f.get("boxes") or {}).items():
            i = km.get(str(k))
            if i is None:
                continue
            f1, f2 = b.get("first") or {}, b.get("second") or {}
            out[f"{f['page']}:{i}"] = (f"open decision, page {f['page']}: first reading {f1.get('joins')} ({(f1.get('confidence') or 0):.2f}), "
                                       f"second {f2.get('joins')} ({(f2.get('confidence') or 0):.2f})"
                                       + (f" — {f2.get('why')}" if f2.get("why") else ""))
    return out


def annotate(doc, source, open_dec, look_below):
    """Every record gets assembly, confidence, flags and needs_look (see the module's docstring)."""
    n_look = 0
    for r in doc.get("articles", []):
        llm = r.get("llm") or {}
        flags = list(r.get("flags") or [])
        if source == "llm":
            r["assembly"] = "rules, checked by the model" + (" (changed)" if not llm.get("kept", True) else "")
            conf = llm.get("confidence_min")
            r["confidence"] = round(float(conf), 3) if conf is not None else None
            changes = [c for c in llm.get("changes", []) if not c.startswith("split from") and not c.startswith("advertising from")]
            flags += [f"model: {c}" for c in changes]
        else:
            r["assembly"] = "rules (not yet checked by the model)"
            r["confidence"] = None
            changes = []
        keys = [f"{f['page']}:{i}" for f in r.get("fragments", []) for i in f.get("region_ids", [])]
        opens = [open_dec[k] for k in keys if k in open_dec]
        flags += [f"model: {o}" for o in opens]
        r["flags"] = flags
        r["needs_look"] = bool(changes or opens or (r["confidence"] is not None and r["confidence"] < look_below))
        n_look += r["needs_look"]
    return n_look


def publish_issue(iid, prefer="llm", look_below=0.9, force=False):
    """Write data/articles/<iid>/articles.json from the best assembly. Returns (what, source, records, needs_look)."""
    rules_p = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "articles.json")
    llm_dir = os.path.join(ROOT, "data", "assembly_v2", "llm", iid)
    llm_p = os.path.join(llm_dir, "articles.json")
    live_dir = os.path.join(LIVE, iid)
    live_p = os.path.join(live_dir, "articles.json")
    if not os.path.exists(rules_p):
        return "no assembly", None, 0, 0
    if os.path.exists(live_p) and os.path.exists(os.path.join(ANN, f"{iid}.jsonl")):
        return "held (corrected by a person)", None, 0, 0
    source, src_p = "rules", rules_p
    if prefer == "llm" and os.path.exists(llm_p):
        if _mt(rules_p) > _mt(llm_p):                  # the rules' records changed after the check (cross-issue links)
            try:
                import s12_llm_link as s12
                s12.rebuild_issue(iid, "llm")
            except Exception as e:
                log("s13", f"{iid}: the model's records could not be rebuilt on the new rules' records ({e!r}); the rules' are published")
        if _mt(llm_p) >= _mt(rules_p):
            source, src_p = "llm", llm_p
    newest = max(_mt(src_p), _mt(os.path.join(llm_dir, "flags.jsonl")) if source == "llm" else 0.0)
    if not force and os.path.exists(live_p) and _mt(live_p) >= newest:
        return "current", source, 0, 0
    doc = json.load(open(src_p, encoding="utf-8"))
    n_look = annotate(doc, source, _open_decisions(llm_dir) if source == "llm" else {}, look_below)
    doc["published"] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source,
                        "from": os.path.relpath(src_p, ROOT), "by": "pipeline/s13_publish.py"}
    os.makedirs(live_dir, exist_ok=True)
    write_json_atomic(live_p, doc)
    return "published", source, len(doc.get("articles", [])), n_look


def publish_all(force=False):
    """Every assembled corpus issue whose source is newer than its live file. Returns counts."""
    cfg = settings().get("publish", {})
    prefer, look_below = cfg.get("prefer", "llm"), float(cfg.get("look_below", 0.9))
    from corpus_lib import corpus_config
    states = all_states()
    counts = {"published": 0, "current": 0, "held": 0, "from_llm": 0, "from_rules": 0, "needs_look": 0}
    for i in corpus_config()["issues"]:
        iid = i["id"]
        st = states.get(iid)
        if not st or "assembled" not in st["stages"]:
            continue
        what, source, n, n_look = publish_issue(iid, prefer, look_below, force)
        if what == "published":
            counts["published"] += 1
            counts["from_" + source] += 1
            counts["needs_look"] += n_look
            event("done", issue=iid, stage="published", source=source, records=n, needs_look=n_look)   # events.jsonl only: the run owns the state files
        elif what == "current":
            counts["current"] += 1
        elif what.startswith("held"):
            counts["held"] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue")
    ap.add_argument("--force", action="store_true", help="write even when the live file is current")
    args = ap.parse_args()
    if args.issue:
        cfg = settings().get("publish", {})
        print(publish_issue(args.issue, cfg.get("prefer", "llm"), float(cfg.get("look_below", 0.9)), args.force))
        return
    c = publish_all(args.force)
    log("s13", "publish: " + json.dumps(c))
    write_json_atomic(os.path.join(ROOT, "data", "corpus", "publish_last.json"), dict(c, ts=time.strftime("%Y-%m-%dT%H:%M:%S")))


if __name__ == "__main__":
    main()
