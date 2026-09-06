#!/usr/bin/env python3
"""Stage 11 — the corrections audit: what people changed on the machine's
own records, region by region.

The harness (s09) scores an assembly against finished human records; the
audit of stage 10 compares the rules' records with the corrections people
made on an OLDER assembly (the archived yardstick). This stage reads the
live annotation log as what it is — a list of corrections made on the
records the machine produced — and, for every record a person touched,
compares the machine's record with the effective record (machine plus
replayed log, exactly what the site shows):

    moved in        a box the person put into the record: from another
                    record (which, of what type), from page furniture, or
                    from nowhere (a box the machine assigned to nothing)
    moved out       a box the person took out: to another record, to a new
                    record of their own, to "not story text"
    role            a box both have, whose role the person changed (a
                    teaser the machine read as body text, a chapter title
                    it did not mark …)
    text            a box whose reading the person corrected
    meta            title, author, type or a record fact the person set
    order           the person reordered the boxes

Every difference carries the box's id (as the workbench shows it, 25C),
its layout label, the first words of its text, what the machine did with
it and why (the record's flags, the furniture's reason), and what the
person did. The point is to read them, one by one, and turn what repeats
into a rule.

    python3 pipeline/s11_corrections_audit.py            # every issue with a log, against the live records
    python3 pipeline/s11_corrections_audit.py --issue wt_1925_11 --show
    python3 pipeline/s11_corrections_audit.py --since 2026-09-06   # only records touched since
    python3 pipeline/s11_corrections_audit.py --candidate data/assembly_v2/rules --show

Against the live records (data/articles) the differences are the corrections
as made — but a verified record is frozen through a refresh (the live copy
is the record as it was when it was verified, not what the current rules
would build), so to measure the CURRENT rules use --candidate with a fresh
build (python3 pipeline/s08_assemble_rules.py --all writes
data/assembly_v2/rules/<issue>/articles.json): every human record is then
matched to the candidate's best-overlapping record and compared with it.
A record the person made from scratch (an id with "_u") is listed with
where the candidate puts its boxes.

Output: data/assembly_v2/corrections.json and a plain-text report.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from s08_assemble_rules import load_pages, region_text  # noqa: E402

WB_ROLE = {"chapter": "chapter_*", "heading": "section"}       # the rules' role names as the workbench shows them


def site():
    sys.path.insert(0, os.path.join(ROOT, "webapp"))
    for k, v in (("PULP_SITE_PASSWORD_FILE", "/nonexistent"), ("PULP_SECRET_FILE", "/tmp/.pulp_eval_secret"),
                 ("PULP_USERS_FILE", "/nonexistent"), ("PULP_API_TOKEN_FILE", "/nonexistent")):
        os.environ.setdefault(k, v)
    import app as A
    return A


def region_ids(pages):
    """page:idx -> the workbench's short id (25C) — the letter is the position in reading order."""
    ids = {}
    for pno, pg in pages.items():
        regs = pg["regions"]
        order = sorted(range(len(regs)), key=lambda r: regs[r].get("order", r))
        for i, r in enumerate(order):
            ids[f"{pno}:{r}"] = f"{pno}{chr(65 + i) if i < 26 else 'Z' + str(i)}"
    return ids


def snippet(pages, k, n=90):
    try:
        pno, idx = (int(x) for x in k.split(":"))
        r = pages[pno]["regions"][idx]
        t = " ".join(region_text(r).split())
        return (t[:n] + "…") if len(t) > n else t, r.get("label", "")
    except Exception:
        return "", ""


def machine_state(doc):
    """region key -> ('record', article, machine role) | ('furniture', why) | ('unsorted',) ; else nothing."""
    st = {}
    for a in doc.get("articles", []):
        roles = a.get("roles") or {}
        for f in a.get("fragments", []):
            for i in f.get("region_ids", []):
                k = f"{f['page']}:{i}"
                st.setdefault(k, ("record", a, roles.get(k)))
    for f in doc.get("furniture", []):
        segs = f.get("segments") or ([f["idx"]] if f.get("idx") is not None else [])
        for i in segs:
            st.setdefault(f"{f['page']}:{i}", ("furniture", f.get("why") or "page furniture"))
    for u in doc.get("unsorted", []):
        segs = u.get("segments") or u.get("region_ids") or []
        for i in segs:
            st.setdefault(f"{u['page']}:{i}", ("unsorted",))
    return st


def effective_state(edoc, A):
    st = {}
    for a in edoc["articles"]:
        for fr in a["fragments"]:
            st.setdefault(A.fragkey(fr), ("record", a))
    for u in edoc.get("user_furniture", []):
        st.setdefault(u["frag"], ("user_furniture", u.get("why") or ""))
    for fr in edoc.get("machine_furniture", []):
        st.setdefault(A.fragkey(fr), ("furniture", fr.get("why") or ""))
    for fr in edoc.get("unsorted", []):
        st.setdefault(A.fragkey(fr), ("unsorted",))
    return st


def describe(a):
    return f"{a['article_id']} ({a.get('type')}: {(a.get('title') or '—')[:36]})"


def load_candidate(cand, iid):
    p = os.path.join(cand if os.path.isabs(cand) else os.path.join(ROOT, cand), iid, "articles.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def audit_issue(iid, A, since=None, show=False, candidate=None):
    mdoc = load_candidate(candidate, iid) if candidate else A.articles_of(iid)
    events = A.ann_events(iid)
    if not mdoc or not events:
        return None
    edoc = A.effective_doc(iid)
    pages = load_pages(iid)
    rid = region_ids(pages)
    mst = machine_state(mdoc)
    est = effective_state(edoc, A)
    mroles = {}
    for a in mdoc["articles"]:
        for k, r in (a.get("roles") or {}).items():
            mroles[k] = r
    eroles = edoc.get("frag_roles", {})
    overrides = edoc.get("frag_overrides", {})
    mby = {a["article_id"]: a for a in mdoc["articles"]}
    eby = {a["article_id"]: a for a in edoc["articles"]}
    ev_by_rec = defaultdict(list)
    for e in events:
        ev_by_rec[e.get("article_id")].append(e)
        if e.get("to_id") and e.get("to_id") != e.get("article_id"):
            ev_by_rec[e["to_id"]].append(e)
    cand_keys = {}
    for a in mdoc["articles"]:
        cand_keys[a["article_id"]] = {f"{f['page']}:{i}" for f in a.get("fragments", []) for i in f["region_ids"]}
    rows = []
    touched = [a for a in edoc["articles"] if a.get("status") in ("verified", "modified")]
    for a in touched:
        evs = ev_by_rec.get(a["article_id"], [])
        last = max((e["ts"] for e in evs), default="")
        if since and last < since:
            continue
        ekeys = [A.fragkey(fr) for fr in a["fragments"]]
        eset = set(ekeys)
        made = f"_u" in a["article_id"]
        if candidate:
            # the candidate's best-overlapping record (Jaccard); a record the person made counts as matched when
            # the candidate has a record of mostly the same boxes (J >= 0.6), else it is listed box by box
            m, bj = None, 0.0
            for cid, ks in cand_keys.items():
                if not ks or not (ks & eset):
                    continue
                j = len(ks & eset) / len(ks | eset)
                if j > bj:
                    m, bj = mby[cid], j
            if made and bj < 0.6:
                m = None
        else:
            m = mby.get(a["article_id"])
        mkeys = [f"{f['page']}:{i}" for f in (m or {}).get("fragments", []) for i in f["region_ids"]] if m else []
        mset = set(mkeys)
        diffs = []
        for k in sorted(eset - mset, key=lambda x: tuple(int(y) for y in x.split(":")) if ":" in x and x.split(":")[0].isdigit() else (10 ** 9, 0)):
            was = mst.get(k)
            if was is None:
                frm = "nothing (the machine assigned the box to no record)"
            elif was[0] == "record":
                frm = f"the record {describe(was[1])}" + (f", role {was[2]}" if was[2] else "")
            elif was[0] == "furniture":
                frm = f"furniture ({was[1]})"
            else:
                frm = "unsorted"
            t, lab = snippet(pages, k)
            diffs.append({"kind": "moved in", "key": k, "id": rid.get(k, k), "label": lab, "text": t, "from": frm,
                          "role": eroles.get(k)})
        for k in sorted(mset - eset, key=lambda x: tuple(int(y) for y in x.split(":"))):
            now = est.get(k)
            if now is None:
                to = "nowhere"
            elif now[0] == "record":
                to = f"the record {describe(now[1])}" + (" (a record the person made)" if "_u" in now[1]["article_id"] else "")
            elif now[0] == "user_furniture":
                to = "not story text" + (f" ({now[1]})" if now[1] else "")
            elif now[0] == "furniture":
                to = f"furniture ({now[1]})"
            else:
                to = "unsorted"
            t, lab = snippet(pages, k)
            diffs.append({"kind": "moved out", "key": k, "id": rid.get(k, k), "label": lab, "text": t, "to": to,
                          "machine_role": mroles.get(k)})
        for k in sorted(eset & mset, key=lambda x: tuple(int(y) for y in x.split(":")) if x.split(":")[0].isdigit() else (10 ** 9, 0)):
            mr = mroles.get(k)
            mr_wb = WB_ROLE.get(mr, mr)
            er = eroles.get(k)
            changed = False
            if er != mr_wb:
                if mr == "chapter" and er in ("chapter_number", "chapter_title"):
                    changed = False
                else:
                    changed = True
            if changed and any(e.get("action") == "set_role" and e.get("frag") == k for e in events):
                t, lab = snippet(pages, k)
                para = {"teaser", "note", "synopsis", "caption"}
                kind = "role (paratext)" if (mr_wb in para and er in para) else "role"
                diffs.append({"kind": kind, "key": k, "id": rid.get(k, k), "label": lab, "text": t,
                              "machine_role": mr or "body text", "role": er or "body text"})
            if k in overrides:
                t, lab = snippet(pages, k)
                diffs.append({"kind": "text", "key": k, "id": rid.get(k, k), "label": lab, "text": t,
                              "new_text": " ".join(overrides[k].split())[:90]})
        meta = {}
        if m:
            for f in ("title", "author", "type"):
                if (a.get(f) or None) != (m.get(f) or None):
                    meta[f] = {"machine": m.get(f), "person": a.get(f)}
            for f in ("ad_class", "advertiser", "excerpt_of", "department", "illustrator", "contains_excerpt"):
                if a.get(f) not in (None, False) and a.get(f) != m.get(f):
                    meta[f] = {"machine": m.get(f), "person": a.get(f)}
            if (a.get("serial") or {}).get("source") == "annotator":
                meta["serial"] = {"machine": m.get("serial"), "person": a.get("serial")}
        else:
            meta["new record"] = {"person": {"type": a.get("type"), "title": a.get("title"), "author": a.get("author")}}
        if any(e.get("action") == "set_frag_order" for e in evs):
            diffs.append({"kind": "order", "n": sum(1 for e in evs if e.get("action") == "set_frag_order")})
        if a.get("text_override") is not None:
            diffs.append({"kind": "text (whole record)"})
        acts = Counter(e.get("action") for e in evs)
        row = {"article_id": a["article_id"], "status": a["status"], "type": a.get("type"), "title": a.get("title"),
               "machine": bool(m), "machine_id": (m or {}).get("article_id"), "machine_flags": (m or {}).get("flags", []), "n_regions": len(ekeys),
               "n_machine_regions": len(mkeys), "exact": (eset == mset) and not meta and not [d for d in diffs if d["kind"] in ("role", "text")],
               "exact_boxes_roles": (eset == mset) and not [d for d in diffs if d["kind"] in ("role", "text")],
               "same_boxes": eset == mset, "actions": dict(acts), "users": sorted({e["user"] for e in evs}),
               "last": last, "meta": meta, "diffs": diffs if show else diffs[:40], "n_diffs": len(diffs),
               "diff_kinds": dict(Counter(d["kind"] for d in diffs))}
        rows.append(row)
    return {"issue": iid, "records": rows, "n_events": len(events)}


def report(results, show=False):
    out = []
    n = exact = same = exact_br = 0
    kinds = Counter()
    verified = 0
    for r in results:
        for row in r["records"]:
            n += 1
            exact += row["exact"]
            exact_br += row.get("exact_boxes_roles", False)
            same += row["same_boxes"]
            verified += row["status"] == "verified"
            kinds.update(row["diff_kinds"])
    out.append(f"{n} records people touched ({verified} verified): {exact} left exactly as the machine made them (boxes, roles, "
               f"title, author, type), {exact_br} the same boxes and roles (a title, author or type differs), {same} the same boxes; "
               "differences by kind: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
    for r in results:
        out.append(f"\n== {r['issue']} ({r['n_events']} events)")
        for row in r["records"]:
            flag = "EXACT" if row["exact"] else ("same boxes" if row["same_boxes"] else f"boxes differ")
            mid = row.get("machine_id")
            out.append(f"  {row['article_id']:18s} {row['status']:8s} {row['type'] or '?':8s} {(row['title'] or '')[:34]:34s} "
                       f"{row['n_regions']:4d} boxes (machine {row['n_machine_regions']:4d}{(' ' + mid.split('_')[-1]) if mid and mid != row['article_id'] else ''}) {flag:12s} "
                       + ", ".join(f"{k} {v}" for k, v in row["diff_kinds"].items())
                       + (" | " + ", ".join(f"{k}: {v.get('machine')!r} -> {v.get('person')!r}" for k, v in row["meta"].items()) if row["meta"] else ""))
            if show:
                for fl in row["machine_flags"]:
                    out.append(f"        machine flag: {fl}")
                for d in row["diffs"]:
                    if d["kind"] == "moved in":
                        out.append(f"        + {d['id']:6s} [{d['label']:14s}] from {d['from']}" + (f" -> role {d['role']}" if d.get("role") else "") + f" | {d['text']}")
                    elif d["kind"] == "moved out":
                        out.append(f"        - {d['id']:6s} [{d['label']:14s}] to {d['to']}" + (f" (machine role {d['machine_role']})" if d.get("machine_role") else "") + f" | {d['text']}")
                    elif d["kind"].startswith("role"):
                        out.append(f"        ~ {d['id']:6s} [{d['label']:14s}] role {d['machine_role']} -> {d['role']}{' (same group)' if d['kind'] != 'role' else ''} | {d['text']}")
                    elif d["kind"] == "text":
                        out.append(f"        t {d['id']:6s} [{d['label']:14s}] '{d['text'][:50]}' -> '{d['new_text'][:50]}'")
                    else:
                        out.append(f"        {d['kind']}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue")
    ap.add_argument("--since", help="only records whose last human action is at or after this time stamp")
    ap.add_argument("--show", action="store_true", help="print every difference")
    ap.add_argument("--candidate", help="a directory of <issue>/articles.json to measure instead of the live records (e.g. data/assembly_v2/rules)")
    args = ap.parse_args()
    A = site()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    ids = [args.issue] if args.issue else [i["id"] for i in cfg["issues"]]
    results = []
    for iid in ids:
        r = audit_issue(iid, A, since=args.since, show=True, candidate=args.candidate)
        if r and r["records"]:
            results.append(r)
    txt = report(results, show=args.show)
    print(txt)
    out = os.path.join(ROOT, "data", "assembly_v2", "corrections.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"candidate": args.candidate or "data/articles", "issues": results, "report": txt}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nwritten {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
