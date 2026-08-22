#!/usr/bin/env python3
"""Stage 6 — measurement. Two kinds of numbers, written to data/metrics.json:

1. Error rates against proofread gold (issues with PG overlap):
   character error rate (CER) and word error rate (WER) = edit distance
   from the human-proofread text divided by its length, in characters /
   words, computed per stage (ia, routeA, routeB, rules_*, llm_*_*) over
   the aligned span. Full-issue gold: whole text aligned. Story-level
   gold: the story is located inside the issue text by fuzzy anchors
   (first/last 200 characters) and only that span is scored.

2. Dictionary-word rate for every issue and stage (works without gold):
   share of alphabetic tokens found in the wordlist. Coarse but comparable.

Normalization before scoring (both sides): lowercase, straight quotes,
collapse whitespace, strip PG boilerplate (everything outside the
*** START/END OF ... *** markers) and PG transcriber notes in brackets.

Edit distances come from rapidfuzz (exact Levenshtein, C++, handles
full-issue texts in seconds). Without rapidfuzz the stdlib fallback is
used for short texts only; oversized comparisons are skipped with a note,
because difflib at issue scale needs hours.
"""
import difflib
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import ROOT
from s04_rules import words

OUT = os.path.join(ROOT, "data", "metrics.json")

try:
    from rapidfuzz.distance import Levenshtein as _RFL
    from rapidfuzz import fuzz as _rff
    HAVE_RF = True
except Exception:
    HAVE_RF = False

# difflib is quadratic; beyond this size the fallback would take hours.
DIFFLIB_MAX = 120_000


def say(msg):
    print(msg, flush=True)


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
    """Edit distance divided by reference length; None if not computable."""
    if HAVE_RF:
        return _RFL.distance(a, b) / max(1, len(b))
    if len(a) > DIFFLIB_MAX or len(b) > DIFFLIB_MAX:
        return None  # stdlib difflib would take hours at this size
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    dist = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            dist += max(i2 - i1, j2 - j1)
        elif tag in ("delete", "insert"):
            dist += (i2 - i1) + (j2 - j1)
    return dist / max(1, len(b))


def cer(hyp, ref):
    r = edit_distance_ratio(norm(hyp), norm(ref))
    return None if r is None else round(r, 4)


def wer(hyp, ref):
    r = edit_distance_ratio(norm(hyp).split(), norm(ref).split())
    return None if r is None else round(r, 4)


def dict_rate(text):
    wl = words()
    toks = re.findall(r"[A-Za-z]+", text.lower())
    if not toks:
        return 0.0
    if not wl:
        return None
    return round(sum(1 for t in toks if t in wl) / len(toks), 4)


def _anchor_ratio(a, b):
    if HAVE_RF:
        return _rff.ratio(a, b) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def locate_span(hay, needle_start, needle_end):
    """Fuzzy-locate a story inside issue text by its first/last anchors."""
    def best_pos(anchor, from_pos=0):
        best, bpos = 0.0, -1
        step = max(1, len(anchor) // 2)
        for pos in range(from_pos, max(1, len(hay) - len(anchor)), step):
            r = _anchor_ratio(hay[pos:pos + len(anchor)], anchor)
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
    say(f"[s06] rapidfuzz available: {HAVE_RF}"
        + ("" if HAVE_RF else " (long texts will be skipped with a note; "
           "pip install --user rapidfuzz)"))
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
        say(f"[s06] {iid}: dictionary rates for {len(st)} stages")

        gold = issue.get("gold")
        if not gold:
            continue
        gdir = os.path.join(ROOT, "data", "gold", iid)
        gtexts = [strip_pg(open(f, encoding="utf-8", errors="replace").read())
                  for f in sorted(glob.glob(os.path.join(gdir, "pg_*.txt")))]
        if not gtexts:
            say(f"[s06] {iid}: gold expected but no pg_*.txt found")
            continue
        gscores = {}
        for stage in stages_present(iid):
            hyp = issue_stage_text(iid, stage)
            if not hyp:
                continue
            t0 = time.time()
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
                c, w = cer(h, ref), wer(h, ref)
                rec = {"cer": c, "wer": w}
                if c is None:
                    rec["note"] = "too long for stdlib difflib; install rapidfuzz"
                per_gold.append(rec)
            gscores[stage] = per_gold
            shown = ", ".join(
                f"cer={p['cer']} wer={p['wer']}" for p in per_gold)
            say(f"[s06] gold {iid} · {stage}: {shown} "
                f"({time.time() - t0:.1f}s)")
        report["gold"][iid] = gscores

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    say(f"[s06] wrote {OUT}")


if __name__ == "__main__":
    main()
