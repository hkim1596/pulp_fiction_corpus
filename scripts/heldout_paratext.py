#!/usr/bin/env python3
"""Rules 2.15, the held-out check before the install (12 November 2026). Read-only: nothing in data/ is written.

The second test of R-B (paratext_stands) was written from 300 labelled records and corrected on a regression of 240
issues; its figures there are in-sample. This script draws magazine issues that were in neither — not the 10 pilot
issues, not the 30 double-read issues, not the 200 of the regression (seed 20261112), not an issue of the audit's
sample of 300, not the 16 issues of the step-2f trial, not a verification-sample issue — assembles each twice in
memory (rules 2.15 off = 2.14, and on), and lists, box by box, what a reader has to judge against the scan:

  changed      a box 2.14 made paratext and 2.15 leaves in a piece (or the reverse): every one
  short_taken  a box under 40 words that both take as paratext by R-B's cues alone, the count not confirming it
  kept_cue     a box both leave in a story although it carries selling cues (the candidates for advertisement left
               in story text)
  kept_plain   a few plain story boxes, as a baseline of what untouched story text looks like

Each row carries the issue, the scan page, the box's index and bounding box (so the scan can be cut to it), its
text, what each version made of it, the recurrence evidence (three or more issues: data/corpus/p52-2026-10-16/
box_recurrence/) and, when --evidence2-dir is given, the looser count (the same text in two or more issues), which
is what tells whether a box left in a story — or taken out of one — can meet its like in another issue at all.

    python3 scripts/heldout_paratext.py --n 150 [--seed 20261113] [--evidence2-dir DIR] [--out-dir DIR] [--workers 8]
    python3 scripts/heldout_paratext.py --selftest
"""
import argparse
import collections
import json
import os
import random
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import s08_assemble_rules as S

TRIAL_2F = ["true_detective_mysteries_1929_12_cember1929", "adventure_1925_03_0519250320", "true_story_1930_02_2n01193002",
            "ken_1938_05_0319380505", "other_worlds_1950_07_07cape1736", "fantastic_adventures_1943_02_02cape1736",
            "amazing_stories_1943_02_02cape1736", "the_arkham_sampler_1948_04s_8springbeb", "pluck_and_luck_1921_06_ndluck1204",
            "wild_west_weekly_n0807_1918_02_0119180222", "deep_red_horror_handbook_undated_orhandbook",
            "the_lovecraft_collectors_lib_1955_aphies1955", "adventure_1930_01_re19300101", "all_story_weekly_1911_07_119140711j",
            "10_story_western_1951_01_4n01195101", "wonder_stories_1934_09_6n04193409"]
SHORT = 40


def words(t):
    return len((t or "").split())


def box_map(doc):
    """(page, index) -> what the assembly made of the box: record id, type, paratext family, role, R-A or not."""
    out = {}
    for r in doc.get("articles") or []:
        roles = r.get("roles") or {}
        for fr in r.get("fragments") or []:
            for i in fr.get("region_ids") or []:
                out[(fr.get("page"), i)] = {"record": r.get("article_id"), "type": r.get("type"), "paratext": r.get("paratext"),
                                            "role": roles.get(f"{fr.get('page')}:{i}"), "ra": bool(r.get("recurrence")),
                                            "title": (r.get("title") or "")[:60]}
    return out


def _ev(e):
    if not e:
        return None
    ps = e.get("partners") or []
    return {"n_issues": e.get("n_issues"), "share": e.get("share"),
            "partner_types": dict(collections.Counter(p.get("type") or "?" for p in ps)),
            "partner_issues": sorted({p.get("issue") for p in ps if p.get("issue")})[:8]}


def rows_for(iid, pages, old, new, evidence, evidence2=None, rnd=None, plain_per_issue=2, meta=None):
    rnd = rnd or random.Random(0)
    a, b = box_map(old), box_map(new)
    meta = meta or {}
    rows, plain = [], []
    for pno, p in sorted(pages.items()):
        for i, reg in enumerate(p.get("regions") or []):
            k = (pno, i)
            t = S.region_text(reg) if hasattr(S, "region_text") else (reg.get("text") or "")
            w = words(t)
            if w < 4 or k not in a or k not in b:
                continue
            x, y = a[k], b[k]
            e = evidence.get(f"{pno}:{i}")
            cat = None
            if bool(x["paratext"]) != bool(y["paratext"]):
                cat = "changed"
            elif y["paratext"] and not y["ra"] and w < SHORT and not S.recurrence_confirms(e):
                cat = "short_taken"
            elif x["type"] == y["type"] == "story" and not x["role"] and not y["role"] and not y["paratext"]:
                if S.ad_cues(t) or S.HARD_SELL_RE.search(t):
                    cat = "kept_cue"
                elif w >= 25:
                    plain.append((k, t, w, x, y, e, reg, p))
            if not cat:
                continue
            rows.append(_row(iid, meta, cat, pno, i, reg, p, t, w, x, y, e, evidence2))
    for k, t, w, x, y, e, reg, p in rnd.sample(plain, min(plain_per_issue, len(plain))):
        rows.append(_row(iid, meta, "kept_plain", k[0], k[1], reg, p, t, w, x, y, e, evidence2))
    return rows


def _row(iid, meta, cat, pno, i, reg, p, t, w, x, y, e, evidence2):
    fam = x["paratext"] or y["paratext"]
    return {"issue": iid, "magazine": meta.get("magazine") or "", "year": meta.get("year"), "category": cat, "page": pno, "box": i,
            "bbox": reg.get("bbox"), "page_size": [p.get("width"), p.get("height")], "label": reg.get("label"), "words": w,
            "v214": {k: x[k] for k in ("type", "paratext", "role", "ra", "title")},
            "v215": {k: y[k] for k in ("type", "paratext", "role", "ra", "title")},
            "stands": S.paratext_stands(fam, t) if fam else None, "narrative": S.narrative_score(t),
            "hard_sell": sorted({m.lower()[:14] for m in S.HARD_SELL_RE.findall(t)})[:6],
            "evidence3": _ev(e), "evidence2": _ev((evidence2 or {}).get(f"{pno}:{i}")) if evidence2 is not None else "not counted",
            "text": t[:1500]}


def _load(d, iid):
    try:
        x = json.load(open(os.path.join(d, iid + ".json"), encoding="utf-8"))
        return x if isinstance(x, dict) else {}
    except (OSError, ValueError):
        return {}


def one(args):
    iid, meta, evidence2_dir, seed = args
    try:
        pages = S.load_pages(iid)
        if not pages:
            return {"issue": iid, "error": "no layout pages"}
        kind, book = S.issue_kind(iid, meta)
        mag = (meta or {}).get("magazine", "")
        docs = []
        for on in (False, True):
            S.RULES_215["on"] = on
            try:
                A = S.analyse(pages, force_scan=S.toc_page_override(iid), magazine=mag)
                A["magazine"] = mag
                docs.append(S.assemble(iid, pages, A, "rules", None, kind=kind, book=book))
            finally:
                S.RULES_215["on"] = True
        ev = S.box_recurrence_for(iid)
        ev2 = _load(evidence2_dir, iid) if evidence2_dir else None
        rows = rows_for(iid, pages, docs[0], docs[1], ev, ev2, random.Random(f"{seed}:{iid}"), meta=meta)
        st = lambda d: sum(words(r.get("text")) for r in d["articles"] if r["type"] == "story")
        return {"issue": iid, "rows": rows, "story_words": [st(docs[0]), st(docs[1])], "evidence": bool(ev),
                "paratext": [sum(1 for r in d["articles"] if r.get("paratext")) for d in docs]}
    except Exception as exc:                                    # one issue's fault must not stop the check
        return {"issue": iid, "error": f"{type(exc).__name__}: {exc}"}


def excluded_ids():
    """Every issue a rule of 2.14/2.15 was written or corrected from."""
    import compare_assembly as C
    ns = argparse.Namespace(issues_file=None, pilot=True, double_read=True, random=200, seed=20261112)
    ids = set(C.issue_ids(ns)) | set(TRIAL_2F)
    try:
        vs = json.load(open(os.path.join(ROOT, "config", "verification_sample.json"), encoding="utf-8"))
        ids |= {i["id"] for i in vs.get("issues", [])}
    except (OSError, ValueError):
        pass
    for d in sorted(x for x in os.listdir(os.path.join(ROOT, "pilot_export")) if x.startswith("paratext-audit-")) if os.path.isdir(os.path.join(ROOT, "pilot_export")) else []:
        try:
            recs = json.load(open(os.path.join(ROOT, "pilot_export", d, "paratext_audit_records.json"), encoding="utf-8"))
            ids |= {r["issue"] for r in recs if r.get("in_sample")}
        except (OSError, ValueError):
            pass
    return ids


def report(results, stamp, n_excluded, seed):
    ok = [r for r in results if "rows" in r]
    rows = [x for r in ok for x in r["rows"]]
    c = collections.Counter(x["category"] for x in rows)
    L = [f"# Rules 2.15 on held-out issues ({stamp}; read-only)", "",
         f"{len(ok)} magazine issues drawn with seed {seed}, outside the {n_excluded} issues the rules were written or corrected from; "
         f"{len(results) - len(ok)} could not be read. Story words {sum(r['story_words'][0] for r in ok):,} (2.14) → {sum(r['story_words'][1] for r in ok):,} (2.15); "
         f"paratext records {sum(r['paratext'][0] for r in ok):,} → {sum(r['paratext'][1] for r in ok):,}; evidence present for {sum(1 for r in ok if r['evidence'])} issues.", "",
         "| category | boxes | words | with the text in another issue (count of two or more) |", "|---|---|---|---|"]
    for cat in ("changed", "short_taken", "kept_cue", "kept_plain"):
        xs = [x for x in rows if x["category"] == cat]
        e2 = sum(1 for x in xs if isinstance(x["evidence2"], dict))
        L.append(f"| {cat} | {len(xs):,} | {sum(x['words'] for x in xs):,} | {e2 if any(x['evidence2'] != 'not counted' for x in xs) else 'not counted'} |")
    ch = [x for x in rows if x["category"] == "changed"]
    L += ["", "## Changed boxes by family and where they are now", "", "| family | direction | now in | boxes | words |", "|---|---|---|---|---|"]
    g = collections.Counter()
    gw = collections.Counter()
    for x in ch:
        k = (x["v214"]["paratext"] or x["v215"]["paratext"], "left in the piece by 2.15" if x["v214"]["paratext"] else "taken by 2.15 only", x["v215"]["type"])
        g[k] += 1
        gw[k] += x["words"]
    for k in sorted(g, key=lambda k: -g[k]):
        L.append(f"| {k[0]} | {k[1]} | {k[2]} | {g[k]} | {gw[k]:,} |")
    L += ["", "## Changed boxes (the longest 60)", "", "| issue | p. | box | family | words | now in | text |", "|---|---|---|---|---|---|---|"]
    for x in sorted(ch, key=lambda x: -x["words"])[:60]:
        L.append(f"| {x['issue']} | {x['page']} | {x['box']} | {x['v214']['paratext'] or x['v215']['paratext']} | {x['words']} | {x['v215']['type']} | "
                 f"{x['text'][:200].replace('|', '/').replace(chr(10), ' ')} |")
    return "\n".join(L) + "\n", dict(c)


def selftest():
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import tempfile
    from test_s08_paratext_boxes import BODY, BODY2, AD, box, page
    LIV = ('"Certainly," replied the liveryman. "I\'ll tell you what I\'ll do. I\'ll charge you $75 if I can\'t get a passenger '
           'back, and I guarantee you a free ride home; but if I can, I\'ll ask only $50."')
    PRICE = "The gun was a bargain at the price, he said, and the offer of $40 was the best he had had; she took it."
    pages = {1: page(1, [box("THE BLACK STONE", 100, "SectionHeader"), box("By Robert E. Howard", 200), box(BODY, 300, h=300),
                         box(LIV, 620, h=100), box(AD, 740, h=100), box(PRICE, 860, h=80), box(BODY2, 960, h=250)])}
    d = tempfile.mkdtemp()
    old_dir = S.RECURRENCE_DIR
    S.RECURRENCE_DIR = d
    try:
        docs = []
        for on in (False, True):
            S.RULES_215["on"] = on
            try:
                docs.append(S.assemble("x", pages, S.analyse(pages)))
            finally:
                S.RULES_215["on"] = True
    finally:
        S.RECURRENCE_DIR = old_dir
    rows = rows_for("x", pages, docs[0], docs[1], {}, {"1:3": {"n_issues": 1, "share": 1.0, "partners": [{"type": "story", "issue": "y"}]}},
                    random.Random(1), meta={"magazine": "X", "year": 1930})
    by = {(r["page"], r["box"]): r for r in rows}
    assert by[(1, 3)]["category"] == "changed" and by[(1, 3)]["v214"]["paratext"] == "advertisement" and by[(1, 3)]["v215"]["type"] == "story", by.get((1, 3))
    assert by[(1, 3)]["evidence2"]["n_issues"] == 1 and by[(1, 3)]["evidence2"]["partner_types"] == {"story": 1} and by[(1, 3)]["bbox"] == [100, 620, 900, 720]
    assert by[(1, 4)]["category"] == "short_taken" and by[(1, 4)]["stands"] is True     # a 37-word advertisement both take
    assert any(r["category"] == "kept_plain" for r in rows)
    assert all(r["category"] != "kept_cue" or r["v215"]["type"] == "story" for r in rows)
    md, counts = report([{"issue": "x", "rows": rows, "story_words": [1, 2], "evidence": False, "paratext": [1, 1]}], "test", 3, 1)
    assert "changed" in md and counts["changed"] >= 1
    print("selftest ok")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--evidence2-dir", help="per-issue files of the looser count (box_recurrence.py --min-issues 2 --out …)")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "pilot_export", "heldout-2.15-" + time.strftime("%Y-%m-%d")))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    from corpus_lib import corpus_config, magazine_issues
    ex = excluded_ids()
    mags = magazine_issues(corpus_config().get("issues", []))
    pool = sorted((i for i in mags if i["id"] not in ex), key=lambda i: i["id"])
    drawn = random.Random(a.seed).sample(pool, min(a.n, len(pool)))
    print(f"[heldout] {len(mags):,} magazine issues, {len(ex):,} excluded ids, {len(pool):,} in the pool, {len(drawn)} drawn (seed {a.seed}); "
          f"looser count: {a.evidence2_dir or 'not given'}", flush=True)
    jobs = [(i["id"], i, a.evidence2_dir, a.seed) for i in drawn]
    t0 = time.time()
    if a.workers > 1:
        from multiprocessing import Pool
        with Pool(a.workers) as pool_:
            results = pool_.map(one, jobs, chunksize=2)
    else:
        results = [one(j) for j in jobs]
    os.makedirs(a.out_dir, exist_ok=True)
    md, counts = report(results, time.strftime("%Y-%m-%d %H:%M"), len(ex), a.seed)
    open(os.path.join(a.out_dir, "heldout.md"), "w", encoding="utf-8").write(md)
    json.dump({"seed": a.seed, "issues": [r["issue"] for r in results], "errors": [r for r in results if "error" in r],
               "rows": [x for r in results for x in r.get("rows", [])]}, open(os.path.join(a.out_dir, "heldout_rows.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(results)} issues in {time.time() - t0:.0f}s ({sum(1 for r in results if 'error' in r)} errors): " + ", ".join(f"{k} {v:,}" for k, v in sorted(counts.items())))
    print(f"written {os.path.relpath(a.out_dir, ROOT)}/heldout.md and heldout_rows.json")


if __name__ == "__main__":
    main()
