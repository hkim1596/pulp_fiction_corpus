#!/usr/bin/env python3
"""Rules 2.14, step 2b, the reading (5 November 2026): what the recurring boxes look like inside the story records, so
that the recurring-box rule (R-A, docs/corpus-1.0-and-stage-2-design.md §3.2) can be written against measured numbers
rather than guessed ones.

Reads data/corpus/p52-2026-10-16/box_recurrence.json (scripts/box_recurrence.py) together with the assembled candidates
and the layout, for the magazine issues of the corpus (the set-aside items and the books left out), and reports, for
every story record:

  - its boxes in reading order, how many tokens each holds, and which of them recur (share >= 0.6 in 3+ issues);
  - the record's recurring share (recurring tokens / all tokens) and the remainder (tokens that do not recur);
  - where the recurring boxes sit: a run at the head, a run at the tail, both, the whole record, or an interior box
    — and whether a recurring box shares its page with the record's own non-recurring boxes or stands on a page of
    recurring boxes alone (the design's "isolated page group");
  - what the recurring boxes' partners are: advertisement and house records elsewhere, other stories, and whether a
    partner is the same author or the same work (a series' own formula, which R-A must leave alone);
  - the reach (how many other issues the box's text is found in) and the token size of the recurring boxes.

Then the numbers R-A's guards need: how many story records the rule would touch at the proposed guard (recurring
share below 50 % and a remainder of 200+ tokens), how many it would only flag, and how those counts move with the reach
threshold and the share threshold; how much of the evidence rests on partners that are themselves set aside (duplicate
scans), i.e. would vanish if the recurrence were recomputed on the active issues alone; the magazines in which the
recurring story boxes concentrate; and examples to read by hand — random ones, interior ones, same-author ones.

Written as Markdown and JSON to pilot_export/box-recurrence-read-<date>/ (report.md, records.json); nothing in data/ is
touched.

    python3 scripts/box_recurrence_read.py [--workers 16] [--out-dir pilot_export/box-recurrence-read-2026-11-05]
    python3 scripts/box_recurrence_read.py --selftest
"""
import argparse
import glob
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
from r01_normalize import prepare, tokenize  # noqa: E402

RUN = os.path.join(ROOT, "data", "corpus", "p52-2026-10-16")
EVIDENCE = os.path.join(RUN, "box_recurrence.json")
GUARD_SHARE = 0.5          # the proposed guard: act when less than half of the record recurs …
GUARD_REMAINDER = 200      # … and at least this many tokens remain
SHARE_BINS = [(0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.9), (0.9, 1.01)]
REACH_BINS = [(2, 2), (3, 5), (6, 20), (21, 100), (101, 10 ** 9)]
SIZE_BINS = [(15, 30), (31, 60), (61, 150), (151, 10 ** 9)]


def _bin(v, bins):
    """The label of the bin v falls in: integer bins are closed (lo–hi, or lo+ for the open last one), share bins are
    half-open [lo, hi) and shown as lo–hi with 1.01 read as 1."""
    for lo, hi in bins:
        if isinstance(lo, int):
            if lo <= v <= hi:
                return f"{lo}+" if hi >= 10 ** 9 else (f"{lo}" if lo == hi else f"{lo}–{hi}")
        elif lo <= v < hi:
            return f"{lo:g}–{min(hi, 1):g}"
    return "?"


def _norm(s):
    return " ".join((s or "").lower().split())


def record_boxes(iid, root=ROOT):
    """[(record dict, [(page, idx, n_tokens, text)])] for the story records of the issue, boxes in reading order."""
    path = os.path.join(root, "data", "assembly_v2", "rules", iid, "articles.json")
    if not os.path.exists(path):
        return []
    doc = json.load(open(path, encoding="utf-8"))
    pages = {}
    for f in glob.glob(os.path.join(root, "data", "layout", iid, "page_*.json")):
        p = json.load(open(f, encoding="utf-8"))
        pages[p["page"]] = sorted(p["regions"], key=lambda r: r.get("order", 0))
    out = []
    for rec in doc.get("articles", []):
        if rec.get("type") != "story":
            continue
        boxes = []
        for fr in rec.get("fragments") or []:
            regs = pages.get(fr["page"])
            if not regs:
                continue
            for i in fr.get("region_ids") or []:
                if i >= len(regs):
                    continue
                t = regs[i].get("text") or ""
                boxes.append((fr["page"], i, len(tokenize(prepare(t))), t))
        out.append((rec, boxes))
    return out


def position(flags):
    """Where the recurring boxes sit in a record's box sequence: none / whole / head / tail / head+tail / interior
    (an interior box present, whatever else), from a list of booleans in reading order."""
    if not any(flags):
        return "none"
    if all(flags):
        return "whole"
    n = len(flags)
    h = 0
    while h < n and flags[h]:
        h += 1
    t = 0
    while t < n and flags[n - 1 - t]:
        t += 1
    if any(flags[h:n - t]):
        return "interior"
    if h and t:
        return "head+tail"
    return "head" if h else "tail"


def read_issue(args):
    """One issue -> per story record summary rows."""
    iid, magazine, flagged, aside, root = args
    rows = []
    for rec, boxes in record_boxes(iid, root):
        total = sum(n for _, _, n, _ in boxes)
        if not boxes or total == 0:
            continue
        seq = []
        rec_tok = 0
        partner_types, same, reach_max, sizes, aside_only, thin = Counter(), False, 0, [], 0, 0
        examples = []
        for page, idx, n, t in boxes:
            f = flagged.get(f"{page}:{idx}")
            on = f is not None
            seq.append((page, on, n))
            if on:
                rec_tok += n
                reach_max = max(reach_max, f.get("n_issues", 0))
                sizes.append(n)
                ps = f.get("partners") or []
                live = [p for p in ps if p.get("issue") not in aside]
                if ps and not live:
                    aside_only += 1
                elif len(live) < 2:
                    thin += 1
                for p in ps:
                    partner_types[(p.get("type") or "?")] += 1
                    if (_norm(p.get("author")) and _norm(p.get("author")) == _norm(rec.get("author"))) or \
                       (_norm(p.get("work")) and _norm(p.get("work")) == _norm(rec.get("work_title") or rec.get("title"))):
                        same = True
                if len(examples) < 2:
                    examples.append({"page": page, "idx": idx, "n_tokens": n, "share": f.get("share"), "n_issues": f.get("n_issues"),
                                     "partner_types": sorted({p.get("type") or "?" for p in ps}),
                                     "text": " ".join(prepare(t).split()[:28])})
        flags = [on for _, on, _ in seq]
        # a recurring box on a page where the record also has non-recurring boxes ("mixed page") vs alone on its page
        by_page = defaultdict(list)
        for page, on, _ in seq:
            by_page[page].append(on)
        mixed = sum(1 for v in by_page.values() if any(v) and not all(v))
        alone = sum(1 for v in by_page.values() if all(v))
        share = rec_tok / total
        rows.append({"issue": iid, "magazine": magazine, "record": rec.get("article_id"), "title": rec.get("title"), "author": rec.get("author"),
                     "n_boxes": len(boxes), "tokens": total, "recurring_tokens": rec_tok, "share": round(share, 3),
                     "remainder": total - rec_tok, "n_flagged": sum(flags), "position": position(flags),
                     "pages_mixed": mixed, "pages_alone": alone, "partner_types": dict(partner_types), "same_author_or_work": same,
                     "reach_max": reach_max, "sizes": sizes, "aside_only_boxes": aside_only, "thin_boxes": thin, "examples": examples})
    return rows


def analyse(rows, evidence_summary):
    touched = [r for r in rows if r["n_flagged"]]
    n = len(rows)
    out = {"story_records": n, "with_recurring_box": len(touched), "evidence": evidence_summary}
    # share bins and the guard
    bins = Counter()
    for r in touched:
        bins[_bin(r["share"], SHARE_BINS)] += 1
    out["share_bins"] = dict(bins)
    act = [r for r in touched if r["share"] < GUARD_SHARE and r["remainder"] >= GUARD_REMAINDER]
    flag_only_share = [r for r in touched if r["share"] >= GUARD_SHARE]
    flag_only_rem = [r for r in touched if r["share"] < GUARD_SHARE and r["remainder"] < GUARD_REMAINDER]
    out["guard"] = {"act": len(act), "flag_only_share_half_or_more": len(flag_only_share), "flag_only_remainder_under_200": len(flag_only_rem),
                    "act_tokens_removed": sum(r["recurring_tokens"] for r in act)}
    out["position"] = dict(Counter(r["position"] for r in touched))
    out["position_among_acted"] = dict(Counter(r["position"] for r in act))
    out["pages"] = {"records_with_a_mixed_page": sum(1 for r in touched if r["pages_mixed"]),
                    "records_with_a_page_of_recurring_boxes_alone": sum(1 for r in touched if r["pages_alone"])}
    # partners
    def profile(r):
        ts = set(r["partner_types"])
        adlike = {"ad", "house", "toc", "letters", "feature", "other", "poem"}
        if r["same_author_or_work"]:
            return "same author or work among partners"
        if ts and ts <= adlike:
            return "non-story partners only"
        if ts == {"story"}:
            return "story partners only"
        if "story" in ts:
            return "story and non-story partners"
        return "no partner recorded"
    out["partner_profile"] = dict(Counter(profile(r) for r in touched))
    out["partner_profile_among_acted"] = dict(Counter(profile(r) for r in act))
    # reach and size of the recurring boxes
    reach, size = Counter(), Counter()
    for r in touched:
        reach[_bin(r["reach_max"], REACH_BINS)] += 1
        for s in r["sizes"]:
            size[_bin(s, SIZE_BINS)] += 1
    out["reach_max_bins"] = dict(reach)
    out["flagged_box_size_bins"] = dict(size)
    # sensitivity: acted records if the reach threshold rose; the share guard moved
    out["sensitivity"] = {
        "acted_if_reach_at_least": {str(k): sum(1 for r in act if r["reach_max"] >= k) for k in (2, 4, 9, 20)},
        "acted_if_share_guard": {str(g): sum(1 for r in touched if r["share"] < g and r["remainder"] >= GUARD_REMAINDER) for g in (0.25, 0.5, 0.75)},
        "acted_if_remainder_at_least": {str(m): sum(1 for r in touched if r["share"] < GUARD_SHARE and r["remainder"] >= m) for m in (100, 200, 500)},
        "acted_excluding_same_author_or_work": sum(1 for r in act if not r["same_author_or_work"]),
        "acted_excluding_interior": sum(1 for r in act if r["position"] != "interior"),
        "acted_excluding_both": sum(1 for r in act if not r["same_author_or_work"] and r["position"] != "interior"),
    }
    # the set-aside partners
    out["set_aside_evidence"] = {"records_with_a_box_whose_partners_are_all_set_aside": sum(1 for r in touched if r["aside_only_boxes"]),
                                 "records_with_a_box_with_fewer_than_2_live_partners": sum(1 for r in touched if r["thin_boxes"]),
                                 "note": "partners are a sample of up to 8 per box; a box with all its sampled partners set aside is evidence "
                                         "that may vanish when the recurrence is recomputed on the active issues alone"}
    # magazines
    by_mag = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        m = by_mag[r["magazine"] or "?"]
        m[0] += 1
        if r["n_flagged"]:
            m[1] += 1
        m[2] += r["n_flagged"]
    top = sorted(by_mag.items(), key=lambda kv: -kv[1][2])[:25]
    out["magazines_top"] = [{"magazine": k, "story_records": v[0], "touched": v[1], "recurring_boxes": v[2]} for k, v in top]
    return out


def examples(rows, k=40, seed=20261105):
    rnd = random.Random(seed)
    touched = [r for r in rows if r["n_flagged"]]
    rnd.shuffle(touched)
    def fmt(r):
        e = r["examples"][0] if r["examples"] else {}
        return {"issue": r["issue"], "title": r["title"], "author": r["author"], "share": r["share"], "remainder": r["remainder"],
                "position": r["position"], "same_author_or_work": r["same_author_or_work"], "reach": r["reach_max"],
                "box": e}
    return {"random": [fmt(r) for r in touched[:k]],
            "interior": [fmt(r) for r in touched if r["position"] == "interior"][:15],
            "same_author_or_work": [fmt(r) for r in touched if r["same_author_or_work"]][:15],
            "mostly_recurring": [fmt(r) for r in touched if r["share"] >= GUARD_SHARE][:15]}


def report(a, ex, stamp):
    L = [f"# Recurring boxes inside the story records — the reading for R-A ({stamp})", "",
         f"Evidence: box_recurrence.json of {a['evidence'].get('generated')} — {a['evidence'].get('issues'):,} issues, "
         f"{a['evidence'].get('boxes'):,} boxes, {a['evidence'].get('flagged_boxes'):,} flagged "
         f"(k={a['evidence'].get('k')}, {a['evidence'].get('min_issues')}+ issues, {a['evidence'].get('min_tokens')}+ tokens, share ≥ {a['evidence'].get('min_share')}).", "",
         f"Story records read (magazine issues, set-aside items left out): **{a['story_records']:,}**; with at least one recurring box: "
         f"**{a['with_recurring_box']:,}** ({a['with_recurring_box'] / max(a['story_records'], 1):.1%}).", ""]
    def table(title, d, head=("value", "records")):
        L.extend([f"## {title}", "", f"| {head[0]} | {head[1]} |", "|---|---|"] + [f"| {k} | {v:,} |" for k, v in d.items()] + [""])
    table("Recurring share of the record (recurring tokens / all tokens)", a["share_bins"], ("share", "records"))
    g = a["guard"]
    L += ["## The proposed guard (act when the recurring share is below 50 % and 200+ tokens remain)", "",
          f"- records the rule would act on (recurring boxes split out): **{g['act']:,}**, removing {g['act_tokens_removed']:,} tokens from the story text",
          f"- records only flagged, half or more recurring: {g['flag_only_share_half_or_more']:,}",
          f"- records only flagged, fewer than 200 tokens would remain: {g['flag_only_remainder_under_200']:,}", ""]
    table("Where the recurring boxes sit (all touched records)", a["position"], ("position", "records"))
    table("Where the recurring boxes sit (records the guard would act on)", a["position_among_acted"], ("position", "records"))
    table("Pages", a["pages"], ("case", "records"))
    table("Partners of the recurring boxes (all touched records)", a["partner_profile"], ("profile", "records"))
    table("Partners of the recurring boxes (records the guard would act on)", a["partner_profile_among_acted"], ("profile", "records"))
    table("Reach of the record's most-travelled recurring box (other issues)", a["reach_max_bins"], ("other issues", "records"))
    table("Token size of the recurring boxes", a["flagged_box_size_bins"], ("tokens", "boxes"))
    s = a["sensitivity"]
    L += ["## Sensitivity", "",
          "| if … | records acted on |", "|---|---|"]
    for k, v in s["acted_if_reach_at_least"].items():
        L.append(f"| reach ≥ {k} other issues | {v:,} |")
    for k, v in s["acted_if_share_guard"].items():
        L.append(f"| share guard < {k} | {v:,} |")
    for k, v in s["acted_if_remainder_at_least"].items():
        L.append(f"| remainder ≥ {k} tokens | {v:,} |")
    L += [f"| same author or work among partners excluded | {s['acted_excluding_same_author_or_work']:,} |",
          f"| interior boxes excluded (flag only) | {s['acted_excluding_interior']:,} |",
          f"| both excluded | {s['acted_excluding_both']:,} |", ""]
    sa = a["set_aside_evidence"]
    L += ["## Evidence resting on set-aside items", "",
          f"- records with a recurring box whose sampled partners are all set-aside items: {sa['records_with_a_box_whose_partners_are_all_set_aside']:,}",
          f"- records with a recurring box with fewer than two partners outside the set-aside items: {sa['records_with_a_box_with_fewer_than_2_live_partners']:,}",
          f"- {sa['note']}", ""]
    L += ["## Magazines with the most recurring story boxes", "", "| magazine | story records | touched | recurring boxes |", "|---|---|---|---|"]
    L += [f"| {m['magazine']} | {m['story_records']:,} | {m['touched']:,} | {m['recurring_boxes']:,} |" for m in a["magazines_top"]]
    L.append("")
    for name, items in ex.items():
        L += [f"## Examples — {name.replace('_', ' ')}", ""]
        for e in items:
            b = e.get("box") or {}
            L += [f"- **{e['issue']}** · {(e['title'] or '')[:60]} / {(e['author'] or '')[:40]} · share {e['share']} · remainder {e['remainder']} · "
                  f"{e['position']} · reach {e['reach']} · same author/work: {e['same_author_or_work']} · partners {b.get('partner_types')}  ",
                  f"  p.{b.get('page')} box {b.get('idx')} ({b.get('n_tokens')} tokens, share {b.get('share')}): “{b.get('text', '')}”"]
        L.append("")
    return "\n".join(L) + "\n"


def run(issues, flagged_all, aside, out_dir, workers=16, root=ROOT, log=print):
    t0 = time.time()
    jobs = [(i["id"], i.get("magazine"), flagged_all.get(i["id"], {}), aside, root) for i in issues]
    rows = []
    with Pool(workers) as pool:
        for n, part in enumerate(pool.imap_unordered(read_issue, jobs, chunksize=8), 1):
            rows.extend(part)
            if n % 500 == 0:
                log(f"[box_recurrence_read] {n:,}/{len(jobs):,} issues, {len(rows):,} story records ({time.time() - t0:.0f}s)")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--evidence", default=EVIDENCE)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "pilot_export", f"box-recurrence-read-{time.strftime('%Y-%m-%d')}"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    from corpus_lib import corpus_config, magazine_issues, set_aside
    ev = json.load(open(a.evidence, encoding="utf-8"))
    issues = magazine_issues(corpus_config().get("issues", []))
    aside = set(set_aside())
    print(f"[box_recurrence_read] {len(issues):,} magazine issues; {len(aside):,} set aside; evidence of {ev['summary'].get('generated')}")
    rows = run(issues, ev["issues"], aside, a.out_dir, workers=a.workers)
    an = analyse(rows, ev["summary"])
    ex = examples(rows)
    os.makedirs(a.out_dir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    open(os.path.join(a.out_dir, "report.md"), "w", encoding="utf-8").write(report(an, ex, stamp))
    json.dump({"generated": stamp, "analysis": an, "examples": ex,
               "records": [{k: v for k, v in r.items() if k != "examples"} for r in rows if r["n_flagged"]]},
              open(os.path.join(a.out_dir, "records.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(json.dumps({k: an[k] for k in ("story_records", "with_recurring_box", "share_bins", "guard", "position", "partner_profile", "sensitivity", "set_aside_evidence")}, indent=1))
    print(f"written {os.path.relpath(a.out_dir, ROOT)}/report.md and records.json")


def selftest():
    import tempfile
    ad = "Send no money today write for our free booklet and learn at home how thousands found success in radio work dept five union square new york city"
    story = ("He rode into the valley at dusk and the cattle were quiet under the first stars of the long and empty night while the men "
             "slept by the fire and the wind came down from the hills " * 6)
    with tempfile.TemporaryDirectory() as d:
        iid = "i1"
        os.makedirs(os.path.join(d, "data", "assembly_v2", "rules", iid)); os.makedirs(os.path.join(d, "data", "layout", iid))
        regs = [{"text": story, "order": 0}, {"text": ad, "order": 1}, {"text": story, "order": 2}, {"text": ad, "order": 3}]
        arts = [{"article_id": "i1_a0", "type": "story", "title": "The Valley", "author": "A. Author", "fragments": [{"page": 1, "region_ids": [0, 1]}]},
                {"article_id": "i1_a1", "type": "story", "title": "The Return", "author": "B. Writer", "fragments": [{"page": 1, "region_ids": [2, 3]}]},
                {"article_id": "i1_a2", "type": "ad", "title": None, "author": None, "fragments": [{"page": 1, "region_ids": [3]}]}]
        json.dump({"page": 1, "regions": regs}, open(os.path.join(d, "data", "layout", iid, "page_0001.json"), "w"))
        json.dump({"articles": arts}, open(os.path.join(d, "data", "assembly_v2", "rules", iid, "articles.json"), "w"))
        flagged = {"1:1": {"record": "i1_a0", "type": "story", "n_tokens": 27, "share": 1.0, "n_issues": 4,
                           "partners": [{"issue": "i2", "record": "x", "type": "ad", "author": None, "work": None}]},
                   "1:3": {"record": "i1_a1", "type": "story", "n_tokens": 27, "share": 1.0, "n_issues": 4,
                           "partners": [{"issue": "dup", "record": "y", "type": "story", "author": "B. Writer", "work": "The Return"}]}}
        rows = read_issue((iid, "Test Stories", flagged, {"dup"}, d))
        assert len(rows) == 2, rows
        r0, r1 = rows
        assert r0["position"] == "tail" and r0["n_flagged"] == 1 and r0["share"] < 0.5 and r0["remainder"] >= GUARD_REMAINDER, r0
        assert r0["partner_types"] == {"ad": 1} and not r0["same_author_or_work"], r0
        assert r1["same_author_or_work"] and r1["aside_only_boxes"] == 1, r1
        an = analyse(rows, {"generated": "t", "issues": 1, "boxes": 4, "flagged_boxes": 2, "k": 8, "min_issues": 3, "min_tokens": 15, "min_share": 0.6})
        assert an["guard"]["act"] == 2 and an["sensitivity"]["acted_excluding_same_author_or_work"] == 1, an["guard"]
        assert an["partner_profile"] == {"non-story partners only": 1, "same author or work among partners": 1}, an["partner_profile"]
        assert an["set_aside_evidence"]["records_with_a_box_whose_partners_are_all_set_aside"] == 1
        assert position([True, False, True, False]) == "interior" and position([True, True, False]) == "head" and position([True]) == "whole"
        md = report(an, examples(rows), "t")
        assert "The proposed guard" in md and "Test Stories" in md
    print("selftest ok")
    return True


if __name__ == "__main__":
    main()
