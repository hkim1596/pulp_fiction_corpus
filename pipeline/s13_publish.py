#!/usr/bin/env python3
"""s13 — publish the corpus's assembled records to the website (Heejin, 5 October 2026: "What I expect is whole corpus
now run on the same way. Show them by authors, Magazines, issues, and stories. And Workbench shows how they are
assembled automatically, and show confidence scores and flags when automation is not so sure. Human annotator will
fix some of them and the algorithm can improve accordingly. … It must lively update everything.")

The site's workbench and explorer read an issue's records from data/articles/<id>/articles.json, the "live" assembly
on which people's corrections are replayed. For every assembled corpus issue this stage writes that file. What it
writes depends on settings.publish.prefer:

    rules_flagged  (the default since p50o, 5 October 2026; Heejin's decision at 11:40 after the pilot score: on the 74 records
                   people verified, the rules alone were exactly right on 65, the rules as the model changed them on
                   50) — the rules' records (data/assembly_v2/rules/<id>), each checked against the language model's
                   reading of its boxes (s12 --follow, data/assembly_v2/llm/<id>): where the model would change the
                   record, the record stays as the rules made it and the change the model would make is listed in its
                   flags, for a person to decide
    llm            the records as the model changed them (v0.18.0: 5 October, from 11:36 until p50o)
    rules          the rules' records only

and adds to every record what the site shows about how sure the automation is:

    assembly     "rules (not yet checked by the model)", "rules, checked by the model: agrees",
                 "rules, checked by the model: disagrees (see the flags)" (prefer llm: "rules, checked by the model"
                 with " (changed)")
    confidence   the model's lowest confidence on the record's boxes (None until checked)
    flags        the rules' own notes, what the model would change (or changed), every decision the model left open on
                 one of its boxes (s12's flags.jsonl)
    needs_look   true when the model disagrees (or changed the record), left a decision on it open, or was under
                 settings.publish.look_below sure of one of its boxes
    model_check  rules_flagged: {"agrees", "changes" (the model's own notes), "open"} — what the model said, kept with
                 the record for the comparison with people's decisions

When the rules' records changed after the model checked them (the cross-issue pass adds serial links), the model's
records are first rebuilt from its stored decisions (s12 rebuild_issue; no model is asked). An issue someone has
corrected (data/annotations/<id>.jsonl) is not written again: the corrections are replayed on the records they were
made on. Rerun freely: an issue is written only when one of its sources is newer than the live file, or when the live
file was written in another mode.

    python3 pipeline/s13_publish.py              # every assembled corpus issue whose source changed
    python3 pipeline/s13_publish.py --issue <id>
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import ROOT, settings, all_states, event, log, write_json_atomic  # noqa: E402

LIVE = os.path.join(ROOT, "data", "articles")
ANN = os.path.join(ROOT, "data", "annotations")
MODES = ("rules_flagged", "llm", "rules")
S13_NOTE = 2                  # the version of data/articles/<id>/published.json: 2 = with the disagreements (p50p)
POOL = os.path.join(ROOT, "data", "review", "model_disagreements.jsonl")


def _mt(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _page_records(var_dir):
    pj = os.path.join(var_dir, "pages.jsonl")
    if not os.path.exists(pj):
        return []
    out = []
    for line in open(pj, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _decisions(page_records):
    """region key -> (joins, confidence): the model's final decision on every box (pages.jsonl)."""
    out = {}
    for r in page_records:
        km = r.get("keymap") or {}
        for k, b in (r.get("boxes") or {}).items():
            i = km.get(str(k))
            if i is not None and isinstance(b, dict):
                out[f"{r['page']}:{i}"] = (b.get("joins"), float(b.get("confidence") or 0.0))
    return out


def _open_decisions(var_dir, page_records=None):
    """region key -> the open decision the model left on it (from flags.jsonl and the page keymaps)."""
    fj = os.path.join(var_dir, "flags.jsonl")
    if not os.path.exists(fj):
        return {}
    keymaps = {r["page"]: r.get("keymap") or {} for r in (page_records if page_records is not None else _page_records(var_dir))}
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


class Labels:
    """Region key "12:3" -> the workbench's name of the box, "12D". A region's id is its place on the page in reading
    order (s08 and the site both sort a page's regions by their order field), and the workbench names the boxes in the
    same order: A, B, C … (after Z: Z26, Z27 …)."""

    def __init__(self, iid=None):
        self.iid = iid

    def __call__(self, key):
        try:
            pno, idx = map(int, key.split(":"))
        except ValueError:
            return key
        return f"{pno}{chr(65 + idx) if idx < 26 else 'Z' + str(idx)}"


def plain(change, labels, titles):
    """One of the model's change notes (s12 build_records) as a sentence for the site, about what the model would do."""
    def lab(m):
        return labels(m.group(0))
    def name(aid):
        t = titles.get(aid)
        return f"“{t}”" if t else "another record"
    m = re.match(r"^split at (\d+:\d+): the rest is \S+ \(([\d.]+)\)$", change)
    if m:
        return f"the model would split this record: a new piece begins at {labels(m.group(1))} (sure {m.group(2)})"
    m = re.match(r"^(\d+:\d+) out as advertising \(([\d.]+)\)$", change)
    if m:
        return f"the model would move box {labels(m.group(1))} out of this record as advertising (sure {m.group(2)})"
    m = re.match(r"^(\d+:\d+) out as furniture \(([\d.]+)\)$", change)
    if m:
        return f"the model would move box {labels(m.group(1))} out of this record as page furniture (sure {m.group(2)})"
    m = re.match(r"^(\d+:\d+) out: a piece begins there \(([\d.]+)\)$", change)
    if m:
        return f"the model would move box {labels(m.group(1))} out of this record: a new piece begins there (sure {m.group(2)})"
    m = re.match(r"^(\d+:\d+) out: it continues (\S+) \(([\d.]+)\)$", change)
    if m:
        return f"the model would move box {labels(m.group(1))} to {name(m.group(2))}, which it continues (sure {m.group(3)})"
    m = re.match(r"^(\d+:\d+) in \((\w+), ([\d.]+)\)$", change)
    if m:
        how = {"previous": "it reads the box as continuing this text", "caption": "as a caption", "notice": "as a notice"}.get(m.group(2), m.group(2))
        return f"the model would add box {labels(m.group(1))} to this record ({how}; sure {m.group(3)})"
    m = re.match(r"^(\S+) joined at (\d+:\d+) \(([\d.]+)\)$", change)
    if m:
        return f"the model would join {name(m.group(1))} to this record at box {labels(m.group(2))} (sure {m.group(3)})"
    return "the model would change this record: " + re.sub(r"\b\d+:\d+\b", lab, change)


KINDS = {                     # the kinds of disagreement, in the words of the review page (/review/model)
    "split": "the model would split the record",
    "join": "the model would join two records",
    "box_in": "the model would add a box to the record",
    "out_ad": "the model would move a box out of the record as advertising",
    "out_furniture": "the model would move a box out of the record as page furniture",
    "out_continues": "the model would move a box to the piece before",
    "out_new": "the model would begin a new piece at a box inside the record",
    "apart": "the model would take the record apart",
}
_PATTERNS = [(re.compile(p), k, ik, io, ic) for p, k, ik, io, ic in (
    (r"^split at (\d+:\d+): the rest is (\S+) \(([\d.]+)\)$", "split", 1, None, 3),
    (r"^(\d+:\d+) out as advertising \(([\d.]+)\)$", "out_ad", 1, None, 2),
    (r"^(\d+:\d+) out as furniture \(([\d.]+)\)$", "out_furniture", 1, None, 2),
    (r"^(\d+:\d+) out: a piece begins there \(([\d.]+)\)$", "out_new", 1, None, 2),
    (r"^(\d+:\d+) out: it continues (\S+) \(([\d.]+)\)$", "out_continues", 1, 2, 3),
    (r"^(\d+:\d+) in \((\w+), ([\d.]+)\)$", "box_in", 1, None, 3),
    (r"^(\S+) joined at (\d+:\d+) \(([\d.]+)\)$", "join", 2, 1, 3),
    (r"^(\S+) joined to (\S+) at (\d+:\d+) \(([\d.]+)\)$", "join", 3, 2, 4),
    (r"^(\S+) taken apart$", "apart", None, None, None))]


def parse_change(c):
    """(kind, box key, the other record or None, confidence or None) of one of the model's change notes."""
    for rx, kind, ik, io, ic in _PATTERNS:
        m = rx.match(c)
        if m:
            return kind, (m.group(ik) if ik else None), (m.group(io) if io else None), (float(m.group(ic)) if ic else None)
    return "other", None, None, None


def cases_of(iid, doc, view):
    """The disagreements of one issue, one per change the model would make (a join, seen from both records, once):
    what the review page (/review/model) asks people to judge."""
    out, seen = [], set()
    for r in doc.get("articles", []):
        v = view.get(r["article_id"]) or {}
        keys = [f"{f['page']}:{i}" for f in r.get("fragments", []) for i in f.get("region_ids", [])]
        for raw, plain_text in zip(v.get("changes") or [], v.get("notes") or []):
            kind, key, other, conf = parse_change(raw)
            key = key or (keys[0] if keys else None)
            if key is None:
                continue
            cid = f"{iid}|join|{key}" if kind == "join" else f"{iid}|{kind}|{r['article_id']}|{key}"
            if cid in seen:
                continue
            seen.add(cid)
            out.append({"case": cid, "record": r["article_id"], "title": r.get("title"), "type": r.get("type"), "kind": kind,
                        "key": key, "other": other, "conf": conf, "change": raw, "plain": plain_text})
    return out


def model_view(iid, rules_doc, llm_doc, page_records, open_dec):
    """For each of the rules' records: does the model agree with it, what would it change, which decisions did it leave
    open, how sure was it (its lowest confidence on the record's boxes)."""
    dec = _decisions(page_records)
    labels = Labels(iid)
    titles = {a["article_id"]: a.get("title") for a in rules_doc.get("articles", [])}
    by_id = {a["article_id"]: a for a in llm_doc.get("articles", [])}
    joined = {}                                            # rules id -> (the record it would join, box, confidence)
    for a in llm_doc.get("articles", []):
        for c in (a.get("llm") or {}).get("changes", []):
            m = re.match(r"^(\S+) joined at (\d+:\d+) \(([\d.]+)\)$", c)
            if m:
                joined[m.group(1)] = (a, m.group(2), m.group(3))
    out = {}
    for R in rules_doc.get("articles", []):
        aid = R["article_id"]
        keys = [f"{f['page']}:{i}" for f in R.get("fragments", []) for i in f.get("region_ids", [])]
        confs = [dec[k][1] for k in keys if k in dec]
        L = by_id.get(aid)
        raw = []
        if L is not None and (L.get("llm") or {}).get("kept"):
            agrees, notes = True, []
        elif L is not None:
            agrees = False
            raw = [c for c in (L.get("llm") or {}).get("changes", [])
                   if not c.startswith(("split from", "advertising from", "a piece begins at", "text before any title"))]
            notes = [plain(c, labels, titles) for c in raw] or ["the model would change this record (its boxes differ from the rules')"]
        elif aid in joined:
            agrees = False
            P, k, c = joined[aid]
            raw = [f"{aid} joined to {P['article_id']} at {k} ({c})"]
            notes = [f"the model would join this record to “{P.get('title') or titles.get(P['article_id']) or 'the piece before it'}” "
                     f"at box {labels(k)} (sure {c})"]
        else:
            agrees = False
            raw = [f"{aid} taken apart"]
            notes = ["the model would take this record apart: none of its boxes stays together as one piece"]
        opens = [open_dec[k] for k in keys if k in open_dec]
        out[aid] = {"agrees": agrees, "notes": notes, "changes": raw, "open": opens,
                    "confidence": round(min(confs), 3) if confs else None}
    return out


def annotate(doc, source, look_below, view=None, open_dec=None):
    """Every record gets assembly, confidence, flags and needs_look (see the module's docstring). Returns the number of
    records that need a look."""
    n_look = 0
    open_dec = open_dec or {}
    for r in doc.get("articles", []):
        flags = list(r.get("flags") or [])
        if source == "rules+model":
            v = (view or {}).get(r["article_id"]) or {"agrees": True, "notes": [], "changes": [], "open": [], "confidence": None}
            r["assembly"] = "rules, checked by the model: " + ("agrees" if v["agrees"] else "disagrees (see the flags)")
            r["confidence"] = v["confidence"]
            flags += v["notes"] + [f"the model left a decision open — {o}" for o in v["open"]]
            r["model_check"] = {"agrees": v["agrees"], "changes": v["changes"], "open": len(v["open"])}
            r["needs_look"] = bool((not v["agrees"]) or v["open"] or (v["confidence"] is not None and v["confidence"] < look_below))
        elif source == "llm":
            llm = r.get("llm") or {}
            r["assembly"] = "rules, checked by the model" + (" (changed)" if not llm.get("kept", True) else "")
            conf = llm.get("confidence_min")
            r["confidence"] = round(float(conf), 3) if conf is not None else None
            changes = [c for c in llm.get("changes", []) if not c.startswith("split from") and not c.startswith("advertising from")]
            keys = [f"{f['page']}:{i}" for f in r.get("fragments", []) for i in f.get("region_ids", [])]
            opens = [open_dec[k] for k in keys if k in open_dec]
            flags += [f"model: {c}" for c in changes] + [f"model: {o}" for o in opens]
            r["needs_look"] = bool(changes or opens or (r["confidence"] is not None and r["confidence"] < look_below))
        else:
            r["assembly"] = "rules (not yet checked by the model)"
            r["confidence"] = None
            r["needs_look"] = False
        r["flags"] = flags
        n_look += r["needs_look"]
    return n_look


def publish_issue(iid, mode="rules_flagged", look_below=0.9, force=False):
    """Write data/articles/<iid>/articles.json. Returns (what, source, records, needs_look)."""
    mode = mode if mode in MODES else "rules_flagged"
    rules_p = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "articles.json")
    llm_dir = os.path.join(ROOT, "data", "assembly_v2", "llm", iid)
    llm_p = os.path.join(llm_dir, "articles.json")
    pages_p, flags_p = os.path.join(llm_dir, "pages.jsonl"), os.path.join(llm_dir, "flags.jsonl")
    live_dir = os.path.join(LIVE, iid)
    live_p = os.path.join(live_dir, "articles.json")
    if not os.path.exists(rules_p):
        return "no assembly", None, 0, 0
    if os.path.exists(live_p) and os.path.exists(os.path.join(ANN, f"{iid}.jsonl")):
        return "held (corrected by a person)", None, 0, 0
    checked = mode != "rules" and os.path.exists(llm_p) and os.path.exists(pages_p)
    if checked and _mt(rules_p) > _mt(llm_p):          # the rules' records changed after the check (cross-issue links)
        try:
            import s12_llm_link as s12
            s12.rebuild_issue(iid, "llm")
        except Exception as e:
            log("s13", f"{iid}: the model's records could not be rebuilt on the new rules' records ({e!r}); the rules' alone are published")
            checked = False
        checked = checked and _mt(llm_p) >= _mt(rules_p)
    if mode == "llm" and checked:
        source, src_p = "llm", llm_p
    elif mode == "rules_flagged" and checked:
        source, src_p = "rules+model", rules_p
    else:
        source, src_p = "rules", rules_p
    newest = max(_mt(rules_p), *((_mt(llm_p), _mt(pages_p), _mt(flags_p)) if checked else (0.0,)))
    side_p = os.path.join(live_dir, "published.json")       # a small note of how the live file was made (read every cycle)
    if not force and os.path.exists(live_p) and _mt(live_p) >= newest:
        try:
            pub = json.load(open(side_p, encoding="utf-8"))
        except Exception:
            pub = {}                                         # written before p50o: made again in the current mode
        if pub.get("mode") == mode and pub.get("source") == source and pub.get("s13") == S13_NOTE:
            return "current", source, 0, 0
    doc = json.load(open(src_p, encoding="utf-8"))
    cases = []
    if source == "rules+model":
        prs = _page_records(llm_dir)
        open_dec = _open_decisions(llm_dir, prs)
        view = model_view(iid, doc, json.load(open(llm_p, encoding="utf-8")), prs, open_dec)
        cases = cases_of(iid, doc, view)
        n_look = annotate(doc, source, look_below, view=view)
    elif source == "llm":
        n_look = annotate(doc, source, look_below, open_dec=_open_decisions(llm_dir))
    else:
        n_look = annotate(doc, source, look_below)
    doc["published"] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": mode, "source": source,
                        "from": [os.path.relpath(p, ROOT) for p in ((rules_p, llm_dir) if source == "rules+model" else (src_p,))],
                        "by": "pipeline/s13_publish.py"}
    os.makedirs(live_dir, exist_ok=True)
    write_json_atomic(live_p, doc)
    write_json_atomic(side_p, dict(doc["published"], s13=S13_NOTE, records=len(doc.get("articles", [])), needs_look=n_look,
                                   disagreements=cases))
    return "published", source, len(doc.get("articles", [])), n_look


def publish_all(force=False):
    """Every assembled corpus issue whose source is newer than its live file (or written in another mode). Returns
    counts."""
    cfg = settings().get("publish", {})
    mode, look_below = cfg.get("prefer", "rules_flagged"), float(cfg.get("look_below", 0.9))
    from corpus_lib import corpus_config
    states = all_states()
    counts = {"mode": mode, "published": 0, "current": 0, "held": 0, "from_rules_and_model": 0, "from_llm": 0, "from_rules": 0,
              "needs_look": 0}
    seen = set()
    for i in corpus_config()["issues"]:
        iid = i["id"]
        if iid in seen:
            continue
        seen.add(iid)
        st = states.get(iid)
        if not st or "assembled" not in st["stages"]:
            continue
        what, source, n, n_look = publish_issue(iid, mode, look_below, force)
        if what == "published":
            counts["published"] += 1
            counts[{"rules+model": "from_rules_and_model", "llm": "from_llm"}.get(source, "from_rules")] += 1
            counts["needs_look"] += n_look
            event("done", issue=iid, stage="published", source=source, records=n, needs_look=n_look)   # events.jsonl only: the run owns the state files
        elif what == "current":
            counts["current"] += 1
        elif what.startswith("held"):
            counts["held"] += 1
    return counts


def write_pool():
    """Every published issue's disagreements in one file, data/review/model_disagreements.jsonl (the review page's
    pool), with the issue's magazine and date. Written by scripts/site_refresh.py when something was published.
    Returns the number of cases."""
    from corpus_lib import corpus_config
    out, seen = [], set()
    for i in corpus_config()["issues"]:
        iid = i["id"]
        if iid in seen:
            continue
        seen.add(iid)
        try:
            pub = json.load(open(os.path.join(LIVE, iid, "published.json"), encoding="utf-8"))
        except Exception:
            continue
        if pub.get("source") != "rules+model":
            continue
        for c in pub.get("disagreements") or []:
            out.append(dict(c, issue=iid, magazine=i.get("magazine"), cover_date=i.get("cover_date"), published=pub.get("ts")))
    os.makedirs(os.path.dirname(POOL), exist_ok=True)
    tmp = POOL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for c in out:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    os.replace(tmp, POOL)
    return len(out)


def selftest():
    """The model's view of the rules' records, on a made-up issue (no files needed)."""
    rules = {"articles": [
        {"article_id": "t_a001", "title": "The Red Moon", "fragments": [{"page": 1, "region_ids": [1, 2]}, {"page": 3, "region_ids": [0, 1]}], "flags": []},
        {"article_id": "t_a002", "title": "Wolves", "fragments": [{"page": 3, "region_ids": [2, 3]}], "flags": ["a rules note"]},
        {"article_id": "t_a003", "title": "Next Month", "fragments": [{"page": 4, "region_ids": [0]}], "flags": []},
        {"article_id": "t_a004", "title": "Letters", "fragments": [{"page": 5, "region_ids": [0, 1]}], "flags": []}]}
    llm = {"articles": [
        {"article_id": "t_a001", "llm": {"kept": False, "changes": ["split at 3:1: the rest is t_a9001 (0.97)", "t_a002 joined at 3:2 (0.96)"]}},
        {"article_id": "t_a9001", "llm": {"kept": False, "changes": ["split from t_a001 at 3:1 (0.97)"]}},
        {"article_id": "t_a003", "llm": {"kept": True, "changes": []}},
        {"article_id": "t_a004", "llm": {"kept": False, "changes": ["5:1 out as advertising (0.98)"]}}]}
    prs = [{"page": 1, "keymap": {"1": 1, "2": 2}, "boxes": {"1": {"joins": "new", "confidence": 0.99}, "2": {"joins": "previous", "confidence": 0.97}}},
           {"page": 4, "keymap": {"1": 0}, "boxes": {"1": {"joins": "new", "confidence": 0.8}}}]
    v = model_view("selftest_no_such_issue", rules, llm, prs, {"5:0": "open decision, page 5: first reading new (0.90), second previous (0.70)"})
    assert not v["t_a001"]["agrees"] and len(v["t_a001"]["notes"]) == 2 and "split" in v["t_a001"]["notes"][0], v["t_a001"]
    assert "join" in v["t_a002"]["notes"][0] and "The Red Moon" in v["t_a002"]["notes"][0], v["t_a002"]
    assert v["t_a003"]["agrees"] and v["t_a003"]["confidence"] == 0.8, v["t_a003"]
    assert not v["t_a004"]["agrees"] and "advertising" in v["t_a004"]["notes"][0] and v["t_a004"]["open"], v["t_a004"]
    assert v["t_a001"]["confidence"] == 0.97
    doc = json.loads(json.dumps(rules))
    n = annotate(doc, "rules+model", 0.9, view=v)
    a = {r["article_id"]: r for r in doc["articles"]}
    assert n == 4 and a["t_a003"]["needs_look"] and a["t_a003"]["assembly"].endswith("agrees"), (n, a["t_a003"])   # agrees, but only 0.8 sure
    assert a["t_a002"]["flags"][0] == "a rules note" and a["t_a001"]["assembly"].endswith("(see the flags)")
    assert a["t_a004"]["model_check"] == {"agrees": False, "changes": ["5:1 out as advertising (0.98)"], "open": 1}
    cs = cases_of("t", rules, v)
    kinds = sorted((c["kind"], c["key"]) for c in cs)
    assert kinds == [("join", "3:2"), ("out_ad", "5:1"), ("split", "3:1")], kinds          # the join seen from both records, once
    assert parse_change("3:4 in (caption, 0.99)") == ("box_in", "3:4", None, 0.99)
    assert parse_change("7:2 out: it continues t_a001 (0.97)") == ("out_continues", "7:2", "t_a001", 0.97)
    assert parse_change("t_a009 taken apart")[0] == "apart" and parse_change("something else")[0] == "other"
    print("s13 selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue")
    ap.add_argument("--force", action="store_true", help="write even when the live file is current")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    cfg = settings().get("publish", {})
    if args.issue:
        print(publish_issue(args.issue, cfg.get("prefer", "rules_flagged"), float(cfg.get("look_below", 0.9)), args.force))
        return
    c = publish_all(args.force)
    log("s13", "publish: " + json.dumps(c))
    write_json_atomic(os.path.join(ROOT, "data", "corpus", "publish_last.json"), dict(c, ts=time.strftime("%Y-%m-%dT%H:%M:%S")))


if __name__ == "__main__":
    main()
