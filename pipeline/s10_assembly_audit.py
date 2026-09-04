#!/usr/bin/env python3
"""Stage 10 — the assembly audit: what the machine still gets wrong,
record by record, against the people who corrected it.

The harness (s09) gives the scores. This audit gives the picture behind
them: for every record a person verified or changed, the live assembly's
best-matching record, the regions the two disagree on, and WHAT the
machine did with each disputed region (page furniture, another record —
of what type —, nothing). Each disagreement is sorted into a cause the
annotators would recognise:

    furniture       the person kept a box the machine calls page furniture
                    (a caption, a signature mark) or the other way round
    advertisement   the person cut advertising the machine had left in the
                    piece, or the machine cut advertising the person kept
    announcement    a next-issue block the machine now reads as advertising
    split           the machine's piece is in two records where the person
                    has one (a chapter split, a missed continuation)
    merged          the machine's record runs into a neighbouring piece
    continuation    pages of the piece the machine's record does not touch
    roles/metadata  title, author, type differ
    text            the person corrected reading errors or typed text by
                    hand — never a matter of assembly

It also counts what people did (their actions by kind), because that is
the honest measure of what remains for a person to do.

    python3 pipeline/s10_assembly_audit.py --yardstick data/assembly_archive/20260902_203349
    python3 pipeline/s10_assembly_audit.py --yardstick ... --issue wt_1934_05 --show

Output: data/assembly_v2/audit.json and a plain-text report.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import s09_assembly_eval as E9  # noqa: E402
from s08_assemble_rules import load_pages, region_text, words, sim, FILLER_RE, AD_WORDS  # noqa: E402

LIVE = os.path.join(ROOT, "data", "articles", "{iid}", "articles.json")


def load_live(iid):
    p = LIVE.format(iid=iid)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def owner_map(doc):
    """region key -> (record, role) for a machine assembly; furniture keys -> ('furniture', why)."""
    own = {}
    for a in doc.get("articles", []):
        for k in E9.keys_of(a):
            own.setdefault(k, (a, (a.get("roles") or {}).get(k)))
    furn = {}
    for f in doc.get("furniture", []):
        segs = f.get("segments") or ([f["idx"]] if f.get("idx") is not None else [])
        for i in segs:
            furn[f"{f['page']}:{i}"] = f.get("why") or "page furniture"
    return own, furn


def cause_of_missing(k, own, furn, pages, human, best):
    """Why a region the person kept is not in the machine's best record."""
    pno, idx = (int(x) for x in k.split(":"))
    t = region_text(pages[pno]["regions"][idx]) if pno in pages and idx < len(pages[pno]["regions"]) else ""
    if k in furn:
        return "furniture", f"machine: furniture ({furn[k]})"
    if k in own:
        rec, role = own[k]
        typ = rec.get("type")
        ttl = (rec.get("title") or "")[:40]
        same_piece = sim(rec.get("title") or "", human["title"] or "") > 0.6 or sim(rec.get("title") or "", best.get("title") or "") > 0.6
        if typ == "ad":
            return "advertisement", f"machine: an advertisement record '{ttl}' ({rec.get('ad_class') or '?'})"
        if typ in ("story", "serial_part", "letters", "feature", "poem") and same_piece:
            return "split", f"machine: a second record of the same piece '{ttl}'"
        if typ in ("story", "serial_part", "letters", "feature", "poem"):
            return "merged into another piece" if False else "other piece", f"machine: the {typ} record '{ttl}'"
        return "other piece", f"machine: the {typ} record '{ttl}'"
    return "unassigned", "machine: assigned to nothing"


def cause_of_extra(k, human_all_keys, human_furn, pages, hdoc_owner):
    """Why a region the machine's record holds is not in the person's record."""
    pno, idx = (int(x) for x in k.split(":"))
    t = region_text(pages[pno]["regions"][idx]) if pno in pages and idx < len(pages[pno]["regions"]) else ""
    if k in human_furn:
        return "furniture", "person: marked not story text"
    if k in hdoc_owner:
        rec = hdoc_owner[k]
        typ = rec.get("type")
        ttl = (rec.get("title") or "")[:40]
        if typ == "ad":
            return "advertisement", f"person: an advertisement record '{ttl}'"
        return "other piece", f"person: the {typ} record '{ttl}'"
    if FILLER_RE.search(t) or len(AD_WORDS.findall(t)) >= 2:
        return "advertisement", "person: left out (reads like advertising)"
    return "left out", "person: left out of every record"


def audit_issue(iid, yardstick, show=False):
    E9.YARDSTICK["dir"] = yardstick
    humans, roles = E9.human_records(iid)
    if not humans:
        return None
    # the person's whole state, for the reverse look-up (which of their records holds a region)
    sys.path.insert(0, os.path.join(ROOT, "webapp"))
    import app as A
    hdoc = A.effective_doc(iid)
    hdoc_owner = {}
    for a in hdoc["articles"]:
        for k in E9.keys_of(a):
            hdoc_owner.setdefault(k, a)
    human_furn = {u.get("frag") for u in hdoc.get("user_furniture", [])}
    for fr in hdoc.get("machine_furniture", []):
        human_furn.add(A.fragkey(fr))
    live = load_live(iid)
    pages = load_pages(iid)
    own, furn = owner_map(live)
    records = live["articles"]
    for r in records:
        r["_keys"] = E9.keys_of(r)
    rows = []
    for h in humans:
        best, j = E9.best_overlap(h["keys"], records)
        hk = set(h["keys"])
        row = {"article_id": h["article_id"], "status": h["status"], "type": h["type"], "title": h["title"], "author": h["author"],
               "n_regions": len(hk), "pages": h["pages"]}
        if not best:
            row.update({"match": None, "jaccard": 0, "causes": {"unmatched": len(hk)}})
            rows.append(row)
            continue
        bk = set(best["_keys"])
        missing = sorted(hk - bk, key=lambda k: tuple(int(x) for x in k.split(":")))
        extra = sorted(bk - hk, key=lambda k: tuple(int(x) for x in k.split(":")))
        causes = Counter()
        details = []
        for k in missing:
            c, why = cause_of_missing(k, own, furn, pages, h, best)
            causes[c] += 1
            details.append(("person has, machine lacks", k, c, why))
        for k in extra:
            c, why = cause_of_extra(k, hk, human_furn, pages, hdoc_owner)
            causes[c] += 1
            details.append(("machine has, person lacks", k, c, why))
        hp = set(h["pages"])
        mp = set(best.get("pages") or [])
        title_ok = sim(h["title"], best.get("title")) >= 0.85 if h["title"] else None
        author_ok = sim(h["author"], best.get("author")) >= 0.85 if h["author"] else None
        # the person's type against the machine's; the person's title case is not a difference
        row.update({"match": best["article_id"], "match_title": best.get("title"), "match_author": best.get("author"),
                    "match_type": best.get("type"), "jaccard": round(j, 3), "exact": hk == bk,
                    "missing": len(missing), "extra": len(extra), "causes": dict(causes),
                    "pages_person_only": sorted(hp - mp), "pages_machine_only": sorted(mp - hp),
                    "title_ok": title_ok, "author_ok": author_ok, "type_ok": (h["type"] == best.get("type")) if h["type"] else None,
                    "details": details if show else details[:12]})
        rows.append(row)
    # what people did on this issue, by action
    acts = Counter(e.get("action") for e in A.ann_events(iid))
    return {"issue": iid, "records": rows, "actions": dict(acts), "n_human": len(humans)}


def report(results):
    lines = []
    tot = Counter()
    exact = 0
    n = 0
    jac = []
    title_bad = author_bad = type_bad = 0
    for r in results:
        for row in r["records"]:
            n += 1
            if row.get("exact"):
                exact += 1
            jac.append(row.get("jaccard", 0))
            for c, v in (row.get("causes") or {}).items():
                tot[c] += v
            if row.get("title_ok") is False:
                title_bad += 1
            if row.get("author_ok") is False:
                author_bad += 1
            if row.get("type_ok") is False:
                type_bad += 1
    lines.append(f"{n} human-touched records in {len(results)} issues: {exact} identical to the machine's record, mean Jaccard "
                 f"{(sum(jac) / n) if n else 0:.3f}; titles differing {title_bad}, authors differing {author_bad}, types differing {type_bad}")
    lines.append("disputed regions by cause: " + ", ".join(f"{c} {v}" for c, v in tot.most_common()))
    for r in results:
        lines.append(f"\n{r['issue']}: {r['n_human']} human-touched records; actions: " + ", ".join(f"{k} {v}" for k, v in sorted(r["actions"].items(), key=lambda t: -t[1])))
        for row in r["records"]:
            c = row.get("causes") or {}
            lines.append(f"  {row['article_id']:18s} {row['status']:8s} {row['type'] or '?':11s} {(row['title'] or '')[:34]:34s} "
                         f"regions {row['n_regions']:4d} -> {row.get('match') or '-':18s} J={row.get('jaccard', 0):.2f} "
                         f"{'EXACT' if row.get('exact') else ('miss ' + str(row.get('missing', 0)) + ' extra ' + str(row.get('extra', 0)))} "
                         + (" | " + ", ".join(f"{k} {v}" for k, v in c.items()) if c else "")
                         + ("" if row.get("title_ok") in (True, None) else f" | title: machine '{(row.get('match_title') or '')[:30]}'")
                         + ("" if row.get("author_ok") in (True, None) else f" | author: machine '{row.get('match_author')}' person '{row.get('author')}'")
                         + ("" if row.get("type_ok") in (True, None) else f" | type: machine {row.get('match_type')} person {row.get('type')}"))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yardstick", required=True)
    ap.add_argument("--issue")
    ap.add_argument("--show", action="store_true", help="keep every disputed region's detail in the JSON")
    args = ap.parse_args()
    yard = args.yardstick if os.path.isabs(args.yardstick) else os.path.join(ROOT, args.yardstick)
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    ids = [args.issue] if args.issue else [i["id"] for i in cfg["issues"]]
    results = []
    for iid in ids:
        r = audit_issue(iid, yard, show=args.show)
        if r:
            results.append(r)
    txt = report(results)
    print(txt)
    out = os.path.join(ROOT, "data", "assembly_v2", "audit.json")
    json.dump({"yardstick": os.path.relpath(yard, ROOT), "issues": results, "report": txt}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nwritten {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
