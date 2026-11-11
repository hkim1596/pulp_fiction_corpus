#!/usr/bin/env python3
"""Rules 2.14, the audit of the paratext records (11 November 2026). Read-only.

Why: the trial of step 2f on 16 issues, read record by record, found every R-A split (a box the corpus-wide count
finds in other issues) to be an advertisement or house matter, and the R-B splits (typographic cues: a price, an
address, a coupon, "published by") right in the fiction and true-story magazines but wrong on non-fiction prose — the
news briefs of Ken (936 and 416 words read as advertisements), the bibliography of a film handbook and of a Lovecraft
checklist (publishers' addresses read as mastheads). The same rules were installed on 6,301 issues on 5 November, so
the question is how much of that there is in the whole corpus, and where.

What it counts, over every active issue's rules file (data/assembly_v2/rules/<issue>/articles.json): each record that
carries `paratext` — by rule (R-A when it carries `recurrence`, R-B otherwise), by family (advertisement, masthead,
ownership statement, recurring box), by the item's kind (magazine, book) and by length. An R-B record is *confirmed*
when at least one of its boxes is in the issue's recurrence evidence (box_recurrence/<issue>.json: the same text in
three or more issues) — the count then agrees with the cue; it is *unconfirmed* when none is. The unconfirmed R-B
records of 40 words or more are the ones a reader has to judge: they are listed with their text, the longest of every
magazine and a seeded random sample, and summed by magazine so that a title where the cues misfire (a news magazine, a
handbook) stands out by its unconfirmed words per issue.

Nothing in data/ is written. Output: <out-dir>/paratext_audit.md, paratext_audit.json (totals, by magazine),
paratext_audit_records.json (the unconfirmed R-B records of 40+ words, with 400 characters of text).

    python3 scripts/paratext_audit.py [--workers 8] [--sample 300] [--seed 20261111] [--limit N] [--out-dir DIR]
    python3 scripts/paratext_audit.py --selftest
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

BINS = ((0, 40, "under 40"), (40, 100, "40–99"), (100, 200, "100–199"), (200, 400, "200–399"), (400, 10 ** 9, "400 or more"))
MIN_WORDS = 40          # below this an unconfirmed record is not listed (a price line, a short address)
PIECE_TYPES = ("story", "feature", "letters", "poem")


def _bin(n):
    return next(label for lo, hi, label in BINS if lo <= n < hi)


def _words(rec):
    return len((rec.get("text") or "").split())


def _boxes(rec):
    return [f"{fr.get('page')}:{i}" for fr in rec.get("fragments") or [] for i in fr.get("region_ids") or []]


def magazine_of(iid, cfg_issue=None):
    name = (cfg_issue or {}).get("magazine")
    return name or re.sub(r"_(18|19)\d\d.*$|_undated.*$", "", iid).replace("_", " ")


def audit_doc(doc, evidence, iid="", magazine="", kind="magazine"):
    """The rows of one assembly: one per paratext record. evidence: the issue's recurrence evidence ("page:idx" -> entry)."""
    recs = doc.get("articles") or []
    by_id = {r.get("article_id"): r for r in recs}
    on_page = collections.defaultdict(set)              # page -> types of the pieces with a box on it
    for r in recs:
        if r.get("type") in PIECE_TYPES and not r.get("paratext"):
            for fr in r.get("fragments") or []:
                on_page[fr.get("page")].add(r.get("type"))
    rows = []
    for r in recs:
        fam = r.get("paratext")
        if not fam:
            continue
        boxes = _boxes(r)
        rule = "R-A" if r.get("recurrence") else "R-B"
        hits = [evidence[b] for b in boxes if b in evidence]
        src = r.get("split_from")
        src = src[0] if isinstance(src, list) and src else src
        pages = sorted({fr.get("page") for fr in r.get("fragments") or [] if fr.get("page") is not None})
        around = sorted({t for p in pages for t in on_page.get(p, ())})
        rows.append({"issue": iid, "magazine": magazine, "kind": kind, "id": r.get("article_id"), "rule": rule, "family": fam,
                     "type": r.get("type"), "words": _words(r), "confirmed": rule == "R-A" or bool(hits),
                     "n_issues": max((h.get("n_issues") or 0 for h in hits), default=(r.get("recurrence") or {}).get("n_issues") or 0),
                     "from": (by_id.get(src) or {}).get("type") if src else None, "around": around, "page": pages[0] if pages else None,
                     "text": (r.get("text") or "")[:400]})
    return rows


def _one(args):
    iid, magazine, kind, root = args
    p = os.path.join(root, "data", "assembly_v2", "rules", iid, "articles.json")
    try:
        doc = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {"issue": iid, "error": "no rules file"}
    try:
        ev = json.load(open(os.path.join(root, "data", "corpus", "p52-2026-10-16", "box_recurrence", iid + ".json"), encoding="utf-8"))
        ev = ev.get("boxes", ev) if isinstance(ev, dict) else {}
    except (OSError, ValueError):
        ev = {}
    backend = doc.get("backend") or ""
    state = "2.14" if backend.startswith("rules_v2.14") else "post-processed" if doc.get("paratext_postprocess") else "not yet"
    return {"issue": iid, "magazine": magazine, "kind": kind, "state": state, "has_evidence": bool(ev),
            "rows": audit_doc(doc, ev, iid, magazine, kind)}


def summarise(results, sample=300, seed=20261111, per_magazine=3):
    issues = [r for r in results if "rows" in r]
    rows = [x for r in issues for x in r["rows"]]
    tot = collections.Counter()
    by = collections.defaultdict(collections.Counter)
    mags = collections.defaultdict(collections.Counter)
    for r in issues:
        mags[(r["magazine"], r["kind"])]["issues"] += 1
        tot["issues " + r["state"]] += 1
        tot["issues without evidence file"] += 0 if r["has_evidence"] else 1
    for x in rows:
        key = (x["kind"], x["rule"], x["family"], "confirmed" if x["confirmed"] else "unconfirmed")
        by[key]["records"] += 1
        by[key]["words"] += x["words"]
        if x["rule"] == "R-B" and not x["confirmed"]:
            by[("bins", x["kind"], x["family"], _bin(x["words"]))]["records"] += 1
            by[("bins", x["kind"], x["family"], _bin(x["words"]))]["words"] += x["words"]
            m = mags[(x["magazine"], x["kind"])]
            m["unconfirmed records"] += 1
            m["unconfirmed words"] += x["words"]
            if x["words"] >= 100:
                m["unconfirmed 100+ records"] += 1
                m["unconfirmed 100+ words"] += x["words"]
        elif x["rule"] == "R-B":
            mags[(x["magazine"], x["kind"])]["confirmed words"] += x["words"]
    listed = [x for x in rows if x["rule"] == "R-B" and not x["confirmed"] and x["words"] >= MIN_WORDS]
    listed.sort(key=lambda x: (-x["words"], x["issue"], x["id"] or ""))
    rnd = random.Random(seed)
    pool = sorted(listed, key=lambda x: (x["issue"], x["id"] or ""))
    drawn = {(x["issue"], x["id"]) for x in (rnd.sample(pool, sample) if len(pool) > sample else pool)}
    seen = collections.Counter()
    for x in listed:
        k = (x["magazine"], x["kind"])
        x["longest_of_magazine"] = seen[k] < per_magazine
        seen[k] += 1
        x["in_sample"] = (x["issue"], x["id"]) in drawn
    mag_rows = []
    for (name, kind), c in mags.items():
        n = c["issues"]
        mag_rows.append({"magazine": name, "kind": kind, "issues": n, "unconfirmed_records": c["unconfirmed records"],
                         "unconfirmed_words": c["unconfirmed words"], "unconfirmed_100_records": c["unconfirmed 100+ records"],
                         "unconfirmed_100_words": c["unconfirmed 100+ words"], "confirmed_words": c["confirmed words"],
                         "unconfirmed_100_words_per_issue": round(c["unconfirmed 100+ words"] / n, 1) if n else 0})
    mag_rows.sort(key=lambda m: (-m["unconfirmed_100_words_per_issue"], -m["unconfirmed_100_words"], m["magazine"]))
    return {"totals": dict(tot), "errors": [r for r in results if "error" in r],
            "by": [{"key": list(k), **dict(v)} for k, v in sorted(by.items(), key=lambda kv: [str(s) for s in kv[0]])],
            "magazines": mag_rows, "listed": listed}


def report(S, stamp):
    L = [f"# Rules 2.14: the paratext records audited ({stamp}; read-only)", ""]
    t = S["totals"]
    L += [f"Issues read: " + ", ".join(f"{k[7:]} {v:,}" for k, v in sorted(t.items()) if k.startswith("issues ") and v) + f"; {len(S['errors'])} without a rules file.", ""]
    L += ["## Paratext records by kind, rule and family", "", "| kind | rule | family | confirmed by the recurrence count | records | words |", "|---|---|---|---|---|---|"]
    for b in S["by"]:
        if b["key"][0] != "bins":
            L.append(f"| {b['key'][0]} | {b['key'][1]} | {b['key'][2]} | {b['key'][3]} | {b.get('records', 0):,} | {b.get('words', 0):,} |")
    L += ["", "## Unconfirmed R-B records by length", "", "| kind | family | length (words) | records | words |", "|---|---|---|---|---|"]
    order = {label: n for n, (_, _, label) in enumerate(BINS)}
    for b in sorted((b for b in S["by"] if b["key"][0] == "bins"), key=lambda b: (b["key"][1], b["key"][2], order[b["key"][3]])):
        L.append(f"| {b['key'][1]} | {b['key'][2]} | {b['key'][3]} | {b.get('records', 0):,} | {b.get('words', 0):,} |")
    L += ["", "## Magazines by unconfirmed R-B words in records of 100 words or more, per issue (the first 60)", "",
          "| magazine | kind | issues | unconfirmed 100+: records | words | words per issue | all unconfirmed words | confirmed words |", "|---|---|---|---|---|---|---|---|"]
    for m in S["magazines"][:60]:
        L.append(f"| {m['magazine']} | {m['kind']} | {m['issues']:,} | {m['unconfirmed_100_records']:,} | {m['unconfirmed_100_words']:,} | "
                 f"{m['unconfirmed_100_words_per_issue']:,} | {m['unconfirmed_words']:,} | {m['confirmed_words']:,} |")
    lst = S["listed"]
    L += ["", f"## The unconfirmed R-B records of {MIN_WORDS} words or more: {len(lst):,} records, {sum(x['words'] for x in lst):,} words", "",
          f"In paratext_audit_records.json, each with 400 characters of text; {sum(1 for x in lst if x['in_sample']):,} are the seeded random sample "
          f"(`in_sample`), {sum(1 for x in lst if x['longest_of_magazine']):,} the longest of their magazine (`longest_of_magazine`). The 40 longest:", "",
          "| issue | record | family | words | pieces on its page | text |", "|---|---|---|---|---|---|"]
    for x in lst[:40]:
        L.append(f"| {x['issue']} | {(x['id'] or '')[-4:]} | {x['family']} | {x['words']:,} | {', '.join(x['around']) or '—'} | "
                 f"{x['text'][:140].replace('|', '/').replace(chr(10), ' ')} |")
    return "\n".join(L) + "\n"


def selftest():
    doc = {"backend": "rules_v2.14", "articles": [
        {"article_id": "x_a001", "type": "story", "text": "story " * 500, "fragments": [{"page": 5, "region_ids": [0, 1, 4]}]},
        {"article_id": "x_a002", "type": "ad", "paratext": "advertisement", "text": "send no money " * 20, "fragments": [{"page": 5, "region_ids": [2]}]},
        {"article_id": "x_a003", "type": "ad", "paratext": "advertisement", "text": "news brief " * 300, "fragments": [{"page": 5, "region_ids": [3]}]},
        {"article_id": "x_a004", "type": "ad", "paratext": "recurring box", "split_from": "x_a001", "recurrence": {"n_issues": 12, "share": 1.0},
         "text": "learn music " * 30, "fragments": [{"page": 6, "region_ids": [0]}]},
        {"article_id": "x_a005", "type": "house", "paratext": "masthead", "text": "Published monthly", "fragments": [{"page": 1, "region_ids": [0]}]}]}
    ev = {"5:2": {"n_issues": 40, "share": 1.0}}
    rows = audit_doc(doc, ev, "x", "X Magazine", "magazine")
    by = {r["id"]: r for r in rows}
    assert len(rows) == 4
    assert by["x_a002"]["rule"] == "R-B" and by["x_a002"]["confirmed"] and by["x_a002"]["n_issues"] == 40
    assert by["x_a003"]["rule"] == "R-B" and not by["x_a003"]["confirmed"] and by["x_a003"]["words"] == 600 and by["x_a003"]["around"] == ["story"]
    assert by["x_a004"]["rule"] == "R-A" and by["x_a004"]["confirmed"] and by["x_a004"]["from"] == "story"
    S = summarise([{"issue": "x", "magazine": "X Magazine", "kind": "magazine", "state": "2.14", "has_evidence": True, "rows": rows}], sample=10)
    assert [x["id"] for x in S["listed"]] == ["x_a003"] and S["listed"][0]["in_sample"] and S["listed"][0]["longest_of_magazine"]
    m = S["magazines"][0]
    assert m["unconfirmed_100_words"] == 600 and m["unconfirmed_100_words_per_issue"] == 600.0 and m["confirmed_words"] == 60
    assert _bin(39) == "under 40" and _bin(40) == "40–99" and _bin(400) == "400 or more"
    assert "X Magazine" in report(S, "test")
    print("selftest ok")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "pilot_export", "paratext-audit-" + time.strftime("%Y-%m-%d")))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    from corpus_lib import corpus_config, active_issues, corpus_kinds
    cfg = corpus_config().get("issues", [])
    active, n_aside = active_issues(cfg)
    kinds = corpus_kinds()
    jobs = [(i["id"], magazine_of(i["id"], i), kinds.get(i["id"], "magazine"), ROOT) for i in active]
    jobs = jobs[:a.limit] if a.limit else jobs
    print(f"[paratext_audit] {len(jobs)} active issues ({n_aside} set aside left out); {a.workers} workers", flush=True)
    t0 = time.time()
    if a.workers > 1:
        from multiprocessing import Pool
        with Pool(a.workers) as pool:
            results = pool.map(_one, jobs, chunksize=32)
    else:
        results = [_one(j) for j in jobs]
    S = summarise(results, sample=a.sample, seed=a.seed)
    os.makedirs(a.out_dir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    open(os.path.join(a.out_dir, "paratext_audit.md"), "w", encoding="utf-8").write(report(S, stamp))
    json.dump({k: S[k] for k in ("totals", "by", "magazines", "errors")}, open(os.path.join(a.out_dir, "paratext_audit.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(S["listed"], open(os.path.join(a.out_dir, "paratext_audit_records.json"), "w", encoding="utf-8"), ensure_ascii=False)
    lst = S["listed"]
    print(f"{len(results)} issues in {time.time() - t0:.0f}s: {sum(len(r.get('rows', [])) for r in results):,} paratext records; "
          f"unconfirmed R-B of {MIN_WORDS}+ words: {len(lst):,} records, {sum(x['words'] for x in lst):,} words")
    print(f"written {os.path.relpath(a.out_dir, ROOT)}/paratext_audit.md, .json and paratext_audit_records.json")


if __name__ == "__main__":
    main()
