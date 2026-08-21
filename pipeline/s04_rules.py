#!/usr/bin/env python3
"""Stage 4 — rule-based cleanup. Deterministic, reversible, fully logged.

Input : a per-page text stage produced earlier —
        ia    data/raw/<id>/ia_text.txt        (split on form-feed \x0c per page)
        routeA data/text/<id>/routeA/page_NNNN.txt
        routeB data/text/<id>/routeB/page_NNNN.txt
Output: data/text/<id>/rules_<src>/page_NNNN.txt
        data/text/<id>/rules_<src>/changes.jsonl   (every change, page + rule + before/after)

Rules, in order:
  R1 normalize characters   ligatures (fi fl ffi ffl), soft hyphens, stray form
                            controls; quotation marks left as printed
  R2 drop garbage lines     lines whose non-alphanumeric share is extreme (scan
                            noise, column rules, decorations)
  R3 running heads + folios repeated first/last lines across many pages of the
                            same issue, and standalone page numbers
  R4 dehyphenate            join word- / break pairs when the joined form looks
                            like a word (dictionary or shape test); keep printed
                            hyphens in true compounds
  R5 paragraph joining      unwrap hard line breaks inside paragraphs; keep
                            paragraph breaks (blank lines, indents, short lines)

Nothing is silently deleted: every R2/R3 removal and R4 join is one JSONL line.
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT

LIG = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi",
       "ﬄ": "ffl", "­": "", "\x0c": ""}
WORD_RE = re.compile(r"[A-Za-z]+")

# small built-in wordlist fallback; server uses /usr/share/dict/words if present
_WORDS = None
def words():
    global _WORDS
    if _WORDS is None:
        _WORDS = set()
        for path in ("/usr/share/dict/words", "/usr/share/dict/american-english"):
            if os.path.exists(path):
                with open(path, errors="ignore") as f:
                    _WORDS = {w.strip().lower() for w in f if w.strip()}
                break
    return _WORDS


def looks_like_word(w):
    w = w.lower().strip(".,;:!?'\"")
    if not w:
        return False
    wl = words()
    if wl:
        return w in wl
    # shape fallback: vowels present, plausible letter run
    return bool(re.fullmatch(r"[a-z]{2,20}", w)) and bool(re.search(r"[aeiouy]", w))


def normalize_chars(line):
    out = line
    for k, v in LIG.items():
        out = out.replace(k, v)
    # collapse interior runs of spaces but PRESERVE leading indentation
    # (R5 uses indents to detect paragraph starts)
    out = re.sub(r"(?<=\S)[ \t]+", " ", out.rstrip())
    return out


def garbage_score(line):
    """High = scan noise. Two signals: share of characters outside letters,
    digits, space and sentence punctuation; and letter share of the line."""
    s = line.strip()
    if not s:
        return 0.0
    bad = sum(1 for c in s if not (c.isalnum() or c in " .,;:!?'\"-—–()"))
    alpha = sum(1 for c in s if c.isalpha())
    score = bad / len(s)
    if len(s) > 6 and alpha / len(s) < 0.3:
        score = max(score, 0.9)
    return score


def head_key(line):
    """Case-folded, digits masked, whitespace collapsed — so a running head
    matches whether spacing survived normalization or not."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", line.strip().lower()))


def find_running_heads(pages):
    """Lines that recur (per head_key) at page tops/bottoms."""
    tops, bots = collections.Counter(), collections.Counter()
    for lines in pages:
        stripped = [l for l in lines if l.strip()]
        if not stripped:
            continue
        for l in stripped[:2]:
            tops[head_key(l)] += 1
        for l in stripped[-2:]:
            bots[head_key(l)] += 1
    thresh = max(3, len(pages) // 4)
    heads = {k for k, n in (tops + bots).items() if n >= thresh and len(k) < 80}
    return heads


def clean_pages(pages, changelog):
    heads = find_running_heads(pages)
    key = head_key
    cleaned = []
    for pno, lines in enumerate(pages, 1):
        out = []
        for ln, raw in enumerate(lines, 1):
            line = normalize_chars(raw)
            if line != raw.rstrip():
                changelog.append({"page": pno, "line": ln, "rule": "R1",
                                  "before": raw.rstrip(), "after": line})
            s = line.strip()
            if not s:
                out.append("")
                continue
            if garbage_score(line) > 0.5 and len(s) > 2:
                changelog.append({"page": pno, "line": ln, "rule": "R2", "removed": line})
                continue
            near_edge = ln <= 2 or ln >= len(lines) - 1
            if near_edge and (key(line) in heads or re.fullmatch(r"\d{1,4}", s)):
                changelog.append({"page": pno, "line": ln, "rule": "R3", "removed": line})
                continue
            out.append(line)
        cleaned.append(out)

    # R4 dehyphenation within and across pages
    flat = []          # (page, text) per line, page breaks marked
    for pno, lines in enumerate(cleaned, 1):
        for l in lines:
            flat.append([pno, l])
    for i in range(len(flat) - 1):
        pno, cur = flat[i]
        m = re.search(r"([A-Za-z]{2,})-$", cur)
        if not m:
            continue
        j = i + 1
        while j < len(flat) and not flat[j][1].strip():
            j += 1
        if j >= len(flat):
            continue
        nxt = flat[j][1].lstrip()
        m2 = re.match(r"([A-Za-z]+)(.*)", nxt)
        if not m2:
            continue
        joined = m.group(1) + m2.group(1)
        if looks_like_word(joined):
            rest = m2.group(2)
            # keep punctuation attached to the joined word ("expected." not
            # "expected .")
            m3 = re.match(r"([^\sA-Za-z]+)(.*)", rest)
            if m3:
                joined += m3.group(1)
                rest = m3.group(2)
            flat[i][1] = cur[: m.start(1)] + joined
            flat[j][1] = rest.lstrip()
            changelog.append({"page": pno, "rule": "R4",
                              "joined": f"{m.group(1)}- + {m2.group(1)} -> {joined}"})

    # R5 paragraph joining, per page (page boundary = keep, serials handled later)
    result = []
    for pno in range(1, len(cleaned) + 1):
        lines = [t for p, t in flat if p == pno]
        paras, buf = [], []
        for l in lines:
            s = l.strip()
            if not s:
                if buf:
                    paras.append(" ".join(buf)); buf = []
                continue
            starts_para = bool(re.match(r"\s{2,}", l)) or (s[0] == '"' and buf and buf[-1].endswith((".", "!", "?")))
            if starts_para and buf:
                paras.append(" ".join(buf)); buf = []
            buf.append(s)
        if buf:
            paras.append(" ".join(buf))
        result.append("\n\n".join(paras))
    return result


def split_ia_text(path):
    """IA _djvu.txt uses form-feed between pages."""
    raw = open(path, encoding="utf-8", errors="replace").read()
    return [p.splitlines() for p in raw.split("\x0c")]


def load_stage_pages(iid, src):
    if src == "ia":
        # prefer per-page IA text written by s01b (from hOCR); fall back to
        # the raw file split on form-feeds (single block when none exist)
        d = os.path.join(ROOT, "data", "text", iid, "ia")
        if os.path.isdir(d):
            return [open(os.path.join(d, f), encoding="utf-8").read().splitlines()
                    for f in sorted(os.listdir(d))
                    if f.startswith("page_") and f.endswith(".txt")]
        p = os.path.join(ROOT, "data", "raw", iid, "ia_text.txt")
        if not os.path.exists(p):
            return None
        return split_ia_text(p)
    d = os.path.join(ROOT, "data", "text", iid, src)
    if not os.path.isdir(d):
        return None
    pages = []
    for f in sorted(os.listdir(d)):
        if f.startswith("page_") and f.endswith(".txt"):
            pages.append(open(os.path.join(d, f), encoding="utf-8").read().splitlines())
    return pages


def run_issue(iid, src):
    pages = load_stage_pages(iid, src)
    if pages is None:
        print(f"[s04] {iid}/{src}: no input, skipped")
        return
    outdir = os.path.join(ROOT, "data", "text", iid, f"rules_{src}")
    os.makedirs(outdir, exist_ok=True)
    changelog = []
    with stage_timer("s04_rules", iid, pages=len(pages), extra={"src": src}):
        cleaned = clean_pages(pages, changelog)
    for i, text in enumerate(cleaned, 1):
        with open(os.path.join(outdir, f"page_{i:04d}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    with open(os.path.join(outdir, "changes.jsonl"), "w", encoding="utf-8") as f:
        for c in changelog:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"[s04] {iid}/{src}: {len(pages)} pages, {len(changelog)} logged changes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--src", default="ia", choices=["ia", "routeA", "routeB"],
                    help="which text stage to clean")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    ids = [i["id"] for i in cfg["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, args.src)


if __name__ == "__main__":
    main()
