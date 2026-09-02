#!/usr/bin/env python3
"""Stage 9 — the assembly harness: how accurate is each way of assembling
an issue into records?

Three yardsticks, from the strongest to the widest:

  1. Human-corrected records (the annotation log replayed over the live
     assembly, as the site does): for every VERIFIED record, the candidate
     assembly's best-overlapping record is scored on its scan regions —
     precision, recall, Jaccard, exact match — and on title, author and
     type. Records a person modified without verifying are reported
     separately (they are partial repairs, not finished ones).
  2. The contents page: every piece the issue's own index lists, with the
     scan page it starts on. A candidate assembly should start a piece on
     that page with that title and author, and should not start another
     story-type record inside the piece's span (a chapter split), nor let
     the piece run over the next start (a missed boundary).
  3. Structural checks that need no yardstick: regions owned by two
     records, text regions owned by none, story records that begin with a
     chapter head, story records with no author, records per type.

Candidates compared: the live assembly (s07, model), rules-only (s08) and
rules-on-model (s08). Output: data/assembly_v2/eval.json and a plain-text
table; docs/assembly-v2.md carries the discussion.

    python3 pipeline/s09_assembly_eval.py --all
    python3 pipeline/s09_assembly_eval.py --issue ast_1930_01
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from s08_assemble_rules import sim, norm, chapter_head, load_pages, region_text   # noqa: E402

PIECE_TYPES = ("story", "serial_part", "poem", "letters", "feature")
STORY_TYPES = ("story", "serial_part")
VARIANTS = [("model", "data/articles/{iid}/articles.json"),
            ("rules", "data/assembly_v2/rules/{iid}/articles.json"),
            ("rules_on_model", "data/assembly_v2/rules_on_model/{iid}/articles.json")]


# ---------------------------------------------------------------- inputs

def load_json(rel):
    p = os.path.join(ROOT, rel)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def keys_of(rec):
    """Region keys page:idx of a record in either fragment shape."""
    out = []
    for f in rec.get("fragments", []):
        if isinstance(f, dict):
            for i in f.get("region_ids", []):
                out.append(f"{f['page']}:{i}")
        elif isinstance(f, (list, tuple)) and len(f) == 2:
            out.append(f"{f[0]}:{f[1]}")
    return out


YARDSTICK = {"dir": None}       # an archive made by scripts/switch_assembly.py, when the live assembly has moved on


def human_records(iid):
    """The annotation log replayed over the assembly it was made on,
    through the site's own engine, so the yardstick is exactly what the
    workbench showed: the live one, or the archive named by --yardstick."""
    sys.path.insert(0, os.path.join(ROOT, "webapp"))
    for k, v in (("PULP_SITE_PASSWORD_FILE", "/nonexistent"), ("PULP_SECRET_FILE", "/tmp/.pulp_eval_secret"),
                 ("PULP_USERS_FILE", "/nonexistent"), ("PULP_API_TOKEN_FILE", "/nonexistent")):
        os.environ.setdefault(k, v)
    import app as A
    if YARDSTICK["dir"]:
        base = YARDSTICK["dir"]

        def articles_of(i):
            p = os.path.join(base, "articles", i, "articles.json")
            return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

        def ann_events(i):
            p = os.path.join(base, "annotations", f"{i}.jsonl")
            out = []
            if os.path.exists(p):
                for line in open(p, encoding="utf-8"):
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
            return out
        A.articles_of = articles_of
        A.ann_events = ann_events
    doc = A.effective_doc(iid)
    if not doc:
        return [], {}
    out = []
    for a in doc["articles"]:
        st = a.get("status", "auto")
        if st in ("verified", "modified"):
            out.append({"article_id": a["article_id"], "status": st, "type": a.get("type"), "title": a.get("title"),
                        "author": a.get("author"), "keys": keys_of(a), "pages": a.get("pages", [])})
    return out, doc.get("frag_roles", {})


# ---------------------------------------------------------------- scoring

def best_overlap(keys, records):
    ks = set(keys)
    best, bj = None, 0.0
    for r in records:
        rk = set(r["_keys"])
        if not rk:
            continue
        inter = len(ks & rk)
        if not inter:
            continue
        j = inter / len(ks | rk)
        if j > bj:
            best, bj = r, j
    return best, bj


def score_human(humans, records):
    rows = []
    for h in humans:
        r, j = best_overlap(h["keys"], records)
        ks = set(h["keys"])
        row = {"article_id": h["article_id"], "status": h["status"], "title": h["title"], "author": h["author"],
               "type": h["type"], "n_regions": len(ks), "match": None}
        if r:
            rk = set(r["_keys"])
            inter = len(ks & rk)
            others = sum(1 for x in records if x is not r and set(x["_keys"]) & ks)
            row.update({"match": r["article_id"], "match_title": r.get("title"), "match_author": r.get("author"),
                        "match_type": r.get("type"), "precision": round(inter / len(rk), 3), "recall": round(inter / len(ks), 3),
                        "jaccard": round(j, 3), "exact": rk == ks, "missing": len(ks - rk), "extra": len(rk - ks),
                        "split_over": others + 1,
                        "title_ok": sim(h["title"], r.get("title")) >= 0.85 if h["title"] else None,
                        "author_ok": sim(h["author"], r.get("author")) >= 0.85 if h["author"] else None,
                        "type_ok": (h["type"] == r.get("type")) if h["type"] else None})
        rows.append(row)
    return rows


def score_toc(toc, records, pages, content_range=None):
    """Every contents-page piece against the candidate's records."""
    pieces = [e for e in toc if e.get("scan")]
    pieces.sort(key=lambda e: e["scan"])
    last_page = (content_range[1] if content_range else max(pages))
    rows = []
    starts_by_page = defaultdict(list)
    for r in records:
        if r.get("type") in PIECE_TYPES and r["pages"]:
            starts_by_page[r["pages"][0]].append(r)
    for n, e in enumerate(pieces):
        P = e["scan"]
        nxt = pieces[n + 1]["scan"] if n + 1 < len(pieces) else None
        span_end = (nxt - 1) if nxt else last_page
        cands = starts_by_page.get(P, []) + starts_by_page.get(P - 1, []) + starts_by_page.get(P + 1, [])
        best = None
        for r in cands:
            sc = max(sim(r.get("title"), e["title"]), sim(r.get("author"), e["author"]) if e.get("author") else 0)
            if best is None or sc > best[0]:
                best = (sc, r)
        row = {"title": e["title"], "author": e.get("author"), "type": e.get("type"), "scan": P, "span": [P, span_end],
               "start_found": bool(cands), "title_ok": False, "author_ok": None, "type_ok": None, "record": None,
               "coverage": 0.0, "extra_starts_inside": 0, "runs_over": False}
        if best:
            r = best[1]
            row["record"] = r["article_id"]
            row["record_title"] = r.get("title")
            row["title_ok"] = sim(r.get("title"), e["title"]) >= 0.6
            row["author_ok"] = (sim(r.get("author"), e["author"]) >= 0.6) if e.get("author") else None
            row["type_ok"] = (r.get("type") == e.get("type")) if e.get("type") else None
            span_pages = set(range(P, span_end + 1))
            row["coverage"] = round(len(span_pages & set(r["pages"])) / max(1, len(span_pages)), 3)
            row["runs_over"] = bool(nxt) and any(pn >= nxt + 1 for pn in r["pages"] if pn <= (nxt + 3))
        inside = [r for pn, rs in starts_by_page.items() if P + 1 <= pn <= span_end for r in rs
                  if r.get("type") in STORY_TYPES and r is not (best[1] if best else None)]
        row["extra_starts_inside"] = len(inside)
        rows.append(row)
    return rows


def structure(records, pages, furniture=()):
    owned = Counter()
    for r in records:
        for k in r["_keys"]:
            owned[k] += 1
    furn = set()
    for f in furniture:
        if isinstance(f, dict) and "idx" in f:
            furn.add(f"{f['page']}:{f['idx']}")
        elif isinstance(f, dict) and "segments" in f:
            for i in f.get("segments", []):
                furn.add(f"{f['page']}:{i}")
    text_keys = set()
    for pno, p in pages.items():
        for i, rg in enumerate(p["regions"]):
            if region_text(rg) and rg["label"] not in ("PageHeader", "PageFooter", "Picture", "Figure"):
                text_keys.add(f"{pno}:{i}")
    by_type = Counter(r.get("type") or "?" for r in records)
    chap = 0
    no_author = 0
    for r in records:
        if r.get("type") in STORY_TYPES:
            if not r.get("author"):
                no_author += 1
            if r["_keys"]:
                pno, i = r["_keys"][0].split(":")
                try:
                    t = region_text(pages[int(pno)]["regions"][int(i)])
                    if chapter_head(t) or (r.get("roles", {}).get(r["_keys"][0]) == "chapter"):
                        chap += 1
                except (KeyError, IndexError):
                    pass
    return {"records": len(records), "by_type": dict(by_type), "double_owned_regions": sum(1 for k, c in owned.items() if c > 1),
            "unassigned_text_regions": len(text_keys - set(owned) - furn), "furniture": len(furn), "story_records_starting_with_chapter_head": chap,
            "story_records_without_author": no_author, "words_in_story_records": sum(len((r.get("text") or "").split()) for r in records if r.get("type") in STORY_TYPES)}


def evaluate_issue(iid, log=print):
    pages = load_pages(iid)
    if not pages:
        return None
    analysis = load_json(f"data/assembly_v2/rules/{iid}/analysis.json") or {}
    toc = analysis.get("toc", [])
    humans, roles = human_records(iid)
    out = {"issue": iid, "pages": len(pages), "contents_entries": len([e for e in toc if e.get("scan")]),
           "human_verified": sum(1 for h in humans if h["status"] == "verified"),
           "human_modified": sum(1 for h in humans if h["status"] == "modified"), "variants": {}}
    for name, rel in VARIANTS:
        d = load_json(rel.format(iid=iid))
        if not d:
            continue
        records = d["articles"]
        for r in records:
            r["_keys"] = keys_of(r)
        hs = score_human(humans, records)
        ts = score_toc(toc, records, pages, analysis.get("content_range"))
        st = structure(records, pages, d.get("furniture", []))
        ver = [h for h in hs if h["status"] == "verified"]
        mod = [h for h in hs if h["status"] == "modified"]

        def mean(xs):
            xs = [x for x in xs if x is not None]
            return round(sum(xs) / len(xs), 3) if xs else None
        summary = {
            "verified": {"n": len(ver), "exact": sum(1 for h in ver if h.get("exact")), "jaccard": mean([h.get("jaccard", 0) for h in ver]),
                         "recall": mean([h.get("recall", 0) for h in ver]), "precision": mean([h.get("precision", 0) for h in ver]),
                         "title_ok": sum(1 for h in ver if h.get("title_ok")), "author_ok": sum(1 for h in ver if h.get("author_ok"))},
            "modified": {"n": len(mod), "jaccard": mean([h.get("jaccard", 0) for h in mod]), "recall": mean([h.get("recall", 0) for h in mod])},
            "contents": {"n": len(ts), "start_found": sum(1 for t in ts if t["start_found"]), "title_ok": sum(1 for t in ts if t["title_ok"]),
                         "author_ok": sum(1 for t in ts if t["author_ok"]), "author_n": sum(1 for t in ts if t["author_ok"] is not None),
                         "type_ok": sum(1 for t in ts if t["type_ok"]), "coverage": mean([t["coverage"] for t in ts]),
                         "clean": sum(1 for t in ts if t["start_found"] and t["extra_starts_inside"] == 0 and not t["runs_over"]),
                         "extra_starts_inside": sum(t["extra_starts_inside"] for t in ts), "runs_over": sum(1 for t in ts if t["runs_over"])},
            "structure": st}
        out["variants"][name] = {"backend": d.get("backend"), "built": d.get("built"), "summary": summary, "human": hs, "contents": ts}
        for r in records:
            r.pop("_keys", None)
    return out


def table(results):
    lines = []
    head = f"{'issue':12s} {'variant':15s} {'pieces':>6s} {'found':>5s} {'title':>5s} {'author':>6s} {'clean':>5s} {'cover':>5s} {'xstart':>6s} {'over':>4s} | {'verif':>5s} {'exact':>5s} {'jacc':>5s} | {'recs':>4s} {'dbl':>4s} {'unas':>4s} {'chap':>4s} {'noauth':>6s}"
    lines.append(head)
    lines.append("-" * len(head))
    tot = defaultdict(lambda: defaultdict(float))
    for res in results:
        for name, v in res["variants"].items():
            s = v["summary"]
            c, st, ve = s["contents"], s["structure"], s["verified"]
            lines.append(f"{res['issue']:12s} {name:15s} {c['n']:6d} {c['start_found']:5d} {c['title_ok']:5d} {c['author_ok']:3d}/{c['author_n']:<2d} {c['clean']:5d} "
                         f"{(c['coverage'] or 0):5.2f} {c['extra_starts_inside']:6d} {c['runs_over']:4d} | {ve['n']:5d} {ve['exact']:5d} {(ve['jaccard'] or 0):5.2f} | "
                         f"{st['records']:4d} {st['double_owned_regions']:4d} {st['unassigned_text_regions']:4d} {st['story_records_starting_with_chapter_head']:4d} {st['story_records_without_author']:6d}")
            T = tot[name]
            for k in ("n", "start_found", "title_ok", "author_ok", "author_n", "clean", "extra_starts_inside", "runs_over"):
                T[k] += c[k]
            T["cov_sum"] += (c["coverage"] or 0) * c["n"]
            for k in ("records", "double_owned_regions", "unassigned_text_regions", "story_records_starting_with_chapter_head", "story_records_without_author"):
                T[k] += st[k]
            T["ver_n"] += ve["n"]
            T["ver_exact"] += ve["exact"]
            T["ver_j"] += (ve["jaccard"] or 0) * ve["n"]
    lines.append("-" * len(head))
    for name, T in tot.items():
        n = T["n"] or 1
        lines.append(f"{'ALL':12s} {name:15s} {int(T['n']):6d} {int(T['start_found']):5d} {int(T['title_ok']):5d} {int(T['author_ok']):3d}/{int(T['author_n']):<2d} {int(T['clean']):5d} "
                     f"{T['cov_sum'] / n:5.2f} {int(T['extra_starts_inside']):6d} {int(T['runs_over']):4d} | {int(T['ver_n']):5d} {int(T['ver_exact']):5d} {(T['ver_j'] / (T['ver_n'] or 1)):5.2f} | "
                     f"{int(T['records']):4d} {int(T['double_owned_regions']):4d} {int(T['unassigned_text_regions']):4d} {int(T['story_records_starting_with_chapter_head']):4d} {int(T['story_records_without_author']):6d}")
    lines.append("")
    lines.append("pieces = entries on the contents page with a scan page; found = a piece-type record starts on that page (±1); title/author = that record's "
                 "title/author agree with the contents page; clean = found, no story record starts inside the piece, and the record does not run past the next "
                 "piece; cover = share of the piece's pages the record touches; xstart = story records starting inside pieces (chapter splits); over = records "
                 "running past the next piece. verif/exact/jacc = human-verified records, exact region-set matches, mean Jaccard. recs = records; dbl = regions "
                 "owned by two records; unas = text regions owned by none; chap = story records that begin with a chapter head; noauth = story records without an author.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--yardstick", help="an archive made by scripts/switch_assembly.py (its folder), when the live assembly is no longer the one the annotations were made on")
    args = ap.parse_args()
    if args.yardstick:
        YARDSTICK["dir"] = os.path.join(ROOT, args.yardstick) if not os.path.isabs(args.yardstick) else args.yardstick
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    ids = [args.issue] if args.issue else ([i["id"] for i in cfg["issues"]] if args.all else [])
    if not ids:
        sys.exit("pass --all or --issue <id>")
    results = []
    for iid in ids:
        r = evaluate_issue(iid)
        if r:
            results.append(r)
    txt = table(results)
    print(txt)
    outdir = os.path.join(ROOT, "data", "assembly_v2")
    os.makedirs(outdir, exist_ok=True)
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "variants": [v for v, _ in VARIANTS], "yardstick": YARDSTICK["dir"] and os.path.relpath(YARDSTICK["dir"], ROOT),
               "issues": results, "table": txt},
              open(os.path.join(outdir, "eval.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nwritten data/assembly_v2/eval.json")


if __name__ == "__main__":
    main()
