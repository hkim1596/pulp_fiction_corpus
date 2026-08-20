#!/usr/bin/env python3
"""Stage 6 — measurement. Two kinds of numbers, written to data/metrics.json:

1. Error rates against proofread gold (issues with PG overlap):
   character error rate (CER) and word error rate (WER) = the share of
   characters / words that differ from the human-proofread text, computed per
   stage (ia, routeA, routeB, rules_*, llm_*_*) over the aligned span.
   Full-issue gold: whole text aligned. Story-level gold: the story is located
   inside the issue text by fuzzy anchors (first/last 200 characters) and only
   that span is scored.

2. Dictionary-word rate for every issue and stage (works without gold):
   share of alphabetic tokens found in the wordlist. Coarse but comparable.

Normalization before scoring (both sides): lowercase, straight quotes,
collapse whitespace, strip PG boilerplate (everything outside the
*** START/END OF ... *** markers) and PG transcriber notes in brackets.
"""
import difflib
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import ROOT
from s04_rules import words

OUT = os.path.join(ROOT, "data", "metrics.json")


def norm(t):
    t = re.sub(r"[‘’]", "'", t)
    t = re.sub(r"[“”]", '"', t)
    t = re.sub(r"[—–]", "-", t)
    t = t.lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def strip_pg(t):
    m = re.search(r"\*\*\* ?start of .*?\*\*\*(.*)\*\*\* ?end of", t,
                  re.S | re.I)
    if m:
        t = m.group(1)
    t = re.sub(r"\[(illustration|transcriber[^\]]*)\]", " ", t, flags=re.I)
    return t


def edit_distance_ratio(a, b):
    """Levenshtein distance via difflib opcodes (stdlib, fine at pilot scale)."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    dist = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            dist += max(i2 - i1, j2 - j1)
        elif tag in ("delete", "insert"):
            dist += (i2 - i1) + (j2 - j1)
    return dist / max(1, len(b))


def cer(hyp, ref):
    return round(edit_distance_ratio(norm(hyp), norm(ref)), 4)


def wer(hyp, ref):
    h, r = norm(hyp).split(), norm(ref).split()
    return round(edit_distance_ratio_list(h, r), 4)


def edit_distance_ratio_list(a, b):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    dist = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            dist += max(i2 - i1, j2 - j1)
        elif tag in ("delete", "insert"):
            dist += (i2 - i1) + (j2 - j1)
    return dist / max(1, len(b))


def dict_rate(text):
    wl = words()
    toks = re.findall(r"[A-Za-z]+", text.lower())
    if not toks:
        return 0.0
    if not wl:
        return None
    return round(sum(1 for t in toks if t in wl) / len(toks), 4)


def locate_span(hay, needle_start, needle_end):
    """Fuzzy-locate a story inside issue text by its first/last anchors."""
    def best_pos(anchor, from_pos=0):
        best, bpos = 0.0, -1
        step = max(1, len(anchor) // 2)
        for pos in range(from_pos, max(1, len(hay) - len(anchor)), step):
            r = difflib.SequenceMatcher(
                None, hay[pos:pos + len(anchor)], anchor).ratio()
            if r > best:
                best, bpos = r, pos
        return bpos, best
    s, rs = best_pos(needle_start)
    e, re_ = best_pos(needle_end, from_pos=max(0, s))
    if rs < 0.5 or re_ < 0.5:
        return None
    return hay[s:e + len(needle_end)]


def issue_stage_text(iid, stage):
    if stage == "ia":
        p = os.path.join(ROOT, "data", "raw", iid, "ia_text.txt")
        return open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else None
    d = os.path.join(ROOT, "data", "text", iid, stage)
    if not os.path.isdir(d):
        return None
    parts = [open(f, encoding="utf-8").read()
             for f in sorted(glob.glob(os.path.join(d, "page_*.txt")))]
    return "\n".join(parts) if parts else None


def stages_present(iid):
    out = ["ia"] if os.path.exists(os.path.join(ROOT, "data", "raw", iid, "ia_text.txt")) else []
    d = os.path.join(ROOT, "data", "text", iid)
    if os.path.isdir(d):
        out += sorted(x for x in os.listdir(d)
                      if os.path.isdir(os.path.join(d, x)))
    return out


def main():
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"),
                         encoding="utf-8"))
    report = {"issues": {}, "gold": {}}
    for issue in cfg["issues"]:
        iid = issue["id"]
        st = {}
        for stage in stages_present(iid):
            text = issue_stage_text(iid, stage)
            if text:
                st[stage] = {"chars": len(text), "dict_rate": dict_rate(text)}
        report["issues"][iid] = st

        gold = issue.get("gold")
        if not gold:
            continue
        gdir = os.path.join(ROOT, "data", "gold", iid)
        gtexts = [strip_pg(open(f, encoding="utf-8", errors="replace").read())
                  for f in sorted(glob.glob(os.path.join(gdir, "pg_*.txt")))]
        if not gtexts:
            continue
        gscores = {}
        for stage in stages_present(iid):
            hyp = issue_stage_text(iid, stage)
            if not hyp:
                continue
            per_gold = []
            for g in gtexts:
                ref = norm(g)
                h = norm(hyp)
                if gold["type"] == "stories":
                    span = locate_span(h, ref[:200], ref[-200:])
                    if span is None:
                        per_gold.append({"cer": None, "wer": None,
                                         "note": "story not located"})
                        continue
                    h = span
                per_gold.append({"cer": cer(h, ref), "wer": wer(h, ref)})
            gscores[stage] = per_gold
        report["gold"][iid] = gscores

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"[s06] wrote {OUT}")


if __name__ == "__main__":
    main()
