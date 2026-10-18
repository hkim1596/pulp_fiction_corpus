#!/usr/bin/env python3
"""toc_folio — the contents page and the printed page numbers, read together.

What the rules engine (s08) needs before it can assemble an issue:

  1. which printed page number each scan page carries (the folio map), even
     where the OCR missed or garbled the number, a leaf was inserted, a leaf
     was bound out of order, or the magazine numbers its pages through the
     volume (page 385 on the first story page);
  2. the issue's contents page(s), read into entries: title, author, printed
     page, section ("NOVELETS"), type label ("A Complete Novel"), part
     ("Part Two"), blurb — in any of the layouts the pulps used, including
     entries whose page number sits in a separate box, entries split over two
     lines (title, then "by Author 94"), entries in two columns, dot leaders
     the OCR turned into noise, and entries whose page number cannot be read;
  3. for every entry, the scan page where the piece begins, checked on the
     page itself (a display title like the entry's, a by-line naming its
     author) rather than taken from arithmetic alone, with the evidence and a
     confidence; an entry whose printed number is garbled or missing is
     placed by its title and author.

The old functions in s08 (folio_of, parse_toc, the offset by the most
common difference) are kept there; analyse_issue() here returns the same
keys plus more, so s08 can switch with one call (see s08's analyse()).

Input: the page JSON of s02 (regions with label, bbox, order, text, and
lines when the OCR gives them). Surya's blocks carry no line boxes; lines are
then laid out evenly inside the block, which is enough for row matching.
No dependency beyond the standard library.

    python3 pipeline/toc_folio.py --issue <id>        # print what it reads
    python3 pipeline/toc_folio.py --selftest

Measured (15 October 2026; docs/toc-folio.md has the method): on 36 corpus issues whose contents pages were
checked by hand against the scans (424 listed pieces), the share of pieces placed on the right scan page
is 57% for rules 2.6 and 98% here, with 98.6% of placed entries real pieces (2.6: 78%); on the 20 of those
issues drawn after the first round of fixes, before they were looked at, 87%. The folio map agrees with
the Internet Archive's own page numbering on 99.2% of 6,359 pages (2.6: 95.7%).
"""
import difflib
import glob
import json
import os
import re
import statistics
import sys

VERSION = "1.3"

ROMAN = re.compile(r"^[IVXLC]+$")
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
SECTION_WORDS = re.compile(
    r"^\W*(?:\d+\W*)?(?:(?:complete|full[- ]length|book[- ]length|feature|featured|smashing|thrilling|exciting|"
    r"two|three|four|five|six|seven|eight|nine|ten|one|a|an|new|our)\s+)*"
    r"(?:novels?|novellas?|novelettes?|novelets?|short\s+stor(?:y|ies)|stor(?:y|ies)|serials?|serial\s+novels?|"
    r"features?|departments?|articles?|poems?|poetry|verse|fact\s+features?|special\s+features?|miscellaneous|"
    r"true\s+stor(?:y|ies)|stories\s+complete\s+in\s+this\s+issue|editorials?|science\s+department|"
    r"shorter\s+stories|continued\s+stories|novelette\s+and\s+short\s+stories|"
    r"(?:complete\s+)?novelettes?\s+and\s+short\s+stories|special\s+articles?|fiction)\b[^a-z]{0,12}$", re.I)
_QUAL = (r"(?:complete|full[- ]length|book[- ]length|short|long|thrilling|smashing|new|feature|featured|"
         r"two[- ]part|three[- ]part|four[- ]part|five[- ]part|six[- ]part|\w+[- ]part)")
_TYPE = (r"(?:novel|novelette|novelet|novella|short\s+story|story|serial|article|poem|verse|feature|fact\s+feature|fact\s+article|"
         r"department|true\s+story|series|editorials?|serial\s+story)")
# a type label: '(A Novelette)', '(Verse)', '. A Complete Novel,' or 'A Short Story' — never a bare word inside a title
# ('THE STORY TELLERS' CIRCLE' keeps its STORY)
TYPE_LABEL = re.compile(
    r"\(\s*(?:an?\s+)?(?:" + _QUAL + r"\s+)*" + _TYPE + r"\b[^)]{0,30}\)"
    r"|[,.;:]?\s*\b(?:an?\s+)(?:" + _QUAL + r"\s+)*" + _TYPE + r"\b(?:\s*[—–-]+\s*[\d,]+\s*words)?[,.;:]?"
    r"|[,.;:]\s*(?:" + _QUAL + r"\s+)*" + _TYPE + r"\b[,.;:]?\s*$", re.I)
PART_RE = re.compile(r"\((?:\w+[- ]parts?\.?\s*)?part\s+(?:[IVX]+|\d+|\w+)\.?\)|\(?\b(?:(?:in|a)\s+(?:two|three|four|five|six|seven|\d)[- ]parts?(?:\s+story)?\s*[—–-]+\s*)?"
                     r"(?:part|pt\.?)\s+(?:[IVX]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
                     r"first|second|third|fourth|fifth|sixth|last|final)\b\.?\)?|\((?:conclusion|concluded|conclusion\s+of\s+[^)]*)\)", re.I)
BYLINE_RE = re.compile(r"^\W{0,3}(?:(?:a|an)\s+[\w\- ]{0,40}?\s+)?\b(?:by|BY|By)\s+(.{3,70})$")
LEADER_RE = re.compile(r"(?:\s*[.·•‧∙°',:;_~=…\-—]){3,}\s*|(?:\s+[.·•:;,_~=…]){2,}\s*")
NOT_ENTRY = re.compile(r"\b(?:vol(?:ume)?|no\.|number|copyright|entered|subscription|price|issue|published|publishers?|printed|"
                       r"address|street|avenue|ave\.|n\.\s*y\.|cents?|per\s+copy|\$|post\s+office|act\s+of|pat\.|registered|"
                       r"on\s+sale|phone|thru|manuscripts?|reserved|second[- ]class|trade\s*mark)\b", re.I)
STAFF_RE = re.compile(r"^\W*(?:(?:managing|associate|assistant|art|fiction|science|feature|contributing)\s+)?"
                      r"(?:editor|director|publisher|manager|production|advertising|circulation|art\s+editor)s?\b(?!['’]s\b)", re.I)
NOT_TITLE = re.compile(r"^\W*(?:illustrat(?:ed|ions?)\s+by|as\s+told\s+to|cover\s+(?:by|design|painting)|drawings?\s+by|"
                       r"decorations?\s+by|photos?\s+by|conducted\s+by|edited\s+by)\b", re.I)
MONTH_ONLY = re.compile(r"^\W*" + r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\W*$", re.I)


# ------------------------------------------------------------------ text helpers

def norm(s):
    s = (s or "").lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return " ".join(w.strip("'") for w in s.split() if w.strip("'"))


def toks(s, minlen=2):
    return [w for w in norm(s).split() if len(w) >= minlen]


STOP = {"the", "of", "a", "an", "and", "in", "to", "on", "for", "at", "by", "from", "with"}


def tok_sim(a, b):
    """How much of a's words appear in b, allowing OCR damage (0..1); a's words weighted by length."""
    ta = [w for w in toks(a) if w not in STOP] or toks(a)
    tb = toks(b, 1)
    if not ta or not tb:
        return 0.0
    tot = got = 0.0
    for w in ta:
        best = 0.0
        for v in tb:
            if v == w:
                best = 1.0
                break
            if abs(len(v) - len(w)) <= max(2, len(w) // 3):
                r = difflib.SequenceMatcher(None, w, v).ratio()
                if r > best:
                    best = r
        tot += len(w)
        got += len(w) * (best if best >= 0.75 else 0.0)
    return got / tot


def sym_sim(a, b):
    return min(tok_sim(a, b), tok_sim(b, a)) * 0.5 + max(tok_sim(a, b), tok_sim(b, a)) * 0.5


def clean_title(t):
    t = re.sub(r"\(\s*[A-Za-z\- ]*[—–\-]+\s*[\d,]+\s*(?:words)?\s*\)?", " ", t or "")
    t = re.sub(r"[—–\-]+\s*[\d,]{4,}\s*(?:words)?\s*\)?", " ", t)
    t = re.sub(r"^[^\w\"“']{0,4}(?=[\w\"“'])", "", t)
    t = re.sub(r"[\s.,;:~_\-—–=•·*]+$", "", t)
    t = re.sub(r"\s+", " ", t).strip(" .,;:'\"“”‘’-—")
    return t


def clean_author(a):
    a = re.sub(r"^\W*(?:by|BY|By)\s+", "", a or "")
    a = re.sub(r"[\s.,;:~_\-—–=•·*\d]+$", "", a)
    a = re.sub(r"\s+", " ", a).strip(" .,;:'\"“”-—")
    return a


def int_token(t):
    """A page number as printed or as the OCR commonly damages it ('l2', 'I0', 'S4'); None otherwise."""
    t = t.strip("()[]{}.,;:*•'\"‘’“”—–-_|")
    if not t or len(t) > 4:
        return None
    if re.fullmatch(r"[0-9]{1,4}", t):               # ASCII digits only: '③' is a digit to str.isdigit() but not to int()
        return int(t)
    if re.fullmatch(r"[0-9lIOoSsBZ]{1,4}", t) and sum(c in "0123456789" for c in t) >= max(1, len(t) - 1):
        return int(t.translate(str.maketrans("lIOoSsBZ", "11005582")))
    return None


# ------------------------------------------------------------------ lines with positions

TOCLIKE = ("tableofcontents", "table", "listgroup", "form")


def deglue(t):
    """Surya's tables lose their cell and row breaks when the HTML is flattened (s02 html_to_text):
    'Return of Jesse James .....Alexander Scott50' + 'EDITORIAL .....2NEWS OF THE MONTH .....158'.
    Put the breaks back: a page number glued to the name before it, a name glued to the number before it."""
    t = re.sub(r"(?<=[a-z])by(?=\s+[A-Z])", " by", t)
    t = re.sub(r"(?<=[A-Za-z.'’)\]])(\d{1,4})(?=\s*(?:$|\n))", r" \1", t, flags=re.M)
    t = re.sub(r"(?<=[A-Za-z.'’)\]])(\d{1,4})(?=[A-Z\"“'‘★*])", r" \1\n", t)
    t = re.sub(r"(?<=\.\.)(\d{1,4})(?=[A-Z\"“'‘★*])", r"\1\n", t)
    t = re.sub(r"(?<=[A-Za-z.'’)\]])(\d{1,4})(?=[ \t]{2,}\S)", r" \1\n", t)
    t = re.sub(r"(?<=\d)(?=[A-Z][A-Za-z'’]{2,}\b)", "\n", t)
    # '...would"THE NEXT TITLE.....'; not after a leader's own dots ('SPEECH.....LEE BERTRAND..... 689' is one
    # entry with the author in capitals: Argosy, 1909-11; toc_folio 1.3)
    t = re.sub(r"(?<!\.\.)(?<=[a-z?!.])(?=[A-Z]{2,}[A-Z'’ \-!?,]*\.{3,})", "\n", t)
    # a blurb run into the next title ('... but He Knew How to SufferCandle-Light .....'): break at the
    # last join of a lower-case letter and a capital before the leader
    out = []
    for line in t.split("\n"):
        m = re.search(r"\.{3,}", line)
        if m:
            pre = line[:m.start()]
            joins = [x.end() for x in re.finditer(r"(?<![MD]c)(?<!Ma)(?<!Mac)(?<!De)(?<!La)(?<!Le)(?<!O')[a-z)!?.,](?=[A-Z][a-z])", pre)
                     if x.end() > 3]
            if joins:
                k = joins[-1]
                line = line[:k] + "\n" + line[k:]
        out.append(line)
    return "\n".join(out)


def region_text_for_toc(r):
    t = (r.get("text") or "").replace("\xa0", " ")
    if (r.get("label") or "").lower() in TOCLIKE or re.search(r"\.{4,}", t):
        t = deglue(t)
    return t

def page_lines(p):
    """Every text line of a page with a box. Lines from the OCR when it gives them; otherwise the block's
    lines laid out evenly over its box (Surya's blocks have no line boxes)."""
    out = []
    for ri, r in enumerate(p.get("regions") or []):
        t = region_text_for_toc(r).strip()
        if not t:
            continue
        x0, y0, x1, y1 = (r.get("bbox") or [0, 0, 0, 0])[:4]
        lines = [l for l in (r.get("lines") or []) if (l.get("text") or "").strip() and l.get("bbox")]
        if lines:
            for l in lines:
                b = l["bbox"]
                out.append({"text": l["text"].strip(), "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3],
                            "ri": ri, "label": r.get("label", "Text"), "synth": False})
        else:
            parts = [s.strip() for s in t.split("\n") if s.strip()]
            h = (y1 - y0) / max(1, len(parts))
            for k, s in enumerate(parts):
                out.append({"text": s, "x0": x0, "y0": y0 + k * h, "x1": x1, "y1": y0 + (k + 1) * h,
                            "ri": ri, "label": r.get("label", "Text"), "synth": len(parts) > 1})
    return out


# ------------------------------------------------------------------ the folio map

def folio_candidates(p):
    """Numbers that may be the printed page number: at the start or end of a line in the top or bottom
    band of the page ('153', '— 12 —', 'ASTOUNDING STORIES 153', '12 WEIRD TALES'), or in a region
    the layout calls PageHeader / PageFooter. Each with a strength: 2 = the number alone, 1 = with a
    running head."""
    H = p.get("height") or 1
    out = []
    for l in page_lines(p):
        t = l["text"]
        lab = (l["label"] or "").lower()
        edge = l["y1"] < 0.085 * H or l["y0"] > 0.915 * H or lab in ("pageheader", "pagefooter")
        if not edge:
            continue
        if re.search(MONTHS + r"\s*,?\s*\d", t, re.I) or re.search(r"\b(?:vol|no|number)\.?\s*\d", t, re.I):
            continue
        ws = t.split()
        if not ws or len(ws) > 9:
            continue
        cand = []
        if len(ws) <= 3:
            v = [int_token(w) for w in ws]
            v = [x for x in v if x is not None]
            if len(v) == 1:
                cand.append((v[0], 2))
        if not cand:
            for w in (ws[0], ws[-1]):
                v = int_token(w)
                if v is not None and sum(c.isalpha() for c in t) >= 3:
                    cand.append((v, 1))
        for v, s in cand:
            if 1 <= v <= 2000 and not (1880 <= v <= 1965 and s == 1):
                out.append((v, s))
    seen = set()
    res = []
    for v, s in sorted(out, key=lambda x: -x[1]):
        if v not in seen:
            seen.add(v)
            res.append((v, s))
    return res


def fit_folios(pages, switch_cost=5.0):
    """The printed page number of every scan page: a path of offsets (scan − printed) through the issue,
    chosen so that as many pages as possible agree with a number read on them, with a cost for every change
    of offset (an inserted or missing leaf). Pages without a readable number take their segment's offset;
    pages outside the numbered range get None. A leaf bound out of order keeps the number it carries."""
    scans = sorted(pages)
    cands = {s: folio_candidates(pages[s]) for s in scans}
    offs = {}
    for s in scans:
        for v, st in cands[s]:
            offs[s - v] = offs.get(s - v, 0) + st
    # offsets supported by at least two pages (or two points)
    states = [d for d, w in offs.items() if w >= 3]
    res = {"printed": {s: None for s in scans}, "read": {s: (cands[s][0][0] if cands[s] else None) for s in scans},
           "segments": [], "misplaced": {}, "support": 0, "pages_with_number": sum(1 for s in scans if cands[s])}
    if not states:
        return res
    emit = {s: {s - v: st for v, st in cands[s]} for s in scans}
    # Viterbi
    NEG = -1e9
    score = {d: emit[scans[0]].get(d, 0.0) for d in states}
    back = []
    for s in scans[1:]:
        best_prev = max(score, key=score.get)
        new, bk = {}, {}
        for d in states:
            stay = score[d]
            move = score[best_prev] - switch_cost
            if stay >= move:
                new[d], bk[d] = stay + emit[s].get(d, 0.0), d
            else:
                new[d], bk[d] = move + emit[s].get(d, 0.0), best_prev
        score = new
        back.append(bk)
    d = max(score, key=score.get)
    path = [d]
    for bk in reversed(back):
        d = bk[d]
        path.append(d)
    path.reverse()
    off = dict(zip(scans, path))
    # segments and the pages that support them
    segs = []
    for s in scans:
        if segs and segs[-1]["offset"] == off[s]:
            segs[-1]["to"] = s
        else:
            segs.append({"offset": off[s], "from": s, "to": s, "support": 0})
        if off[s] in emit[s]:
            segs[-1]["support"] += 1
    # a segment with no support is not a segment: give its pages to the neighbour
    good = [g for g in segs if g["support"] >= 2] or [max(segs, key=lambda g: g["support"])]
    for s in scans:
        inside = [g for g in good if g["from"] <= s <= g["to"]]
        if inside:
            off[s] = inside[0]["offset"]
        else:
            near = min(good, key=lambda g: min(abs(s - g["from"]), abs(s - g["to"])))
            off[s] = near["offset"]
    supported = [s for s in scans if off[s] in emit[s]]
    res["support"] = len(supported)
    if not supported:
        return res
    first, last = min(supported), max(supported)
    for s in scans:
        v = s - off[s]
        # outside the numbered range, a page carries a number only when one is read on it, or
        # when it lies within a few leaves of the range (an unnumbered story opening, the last page)
        if v < 1:
            continue
        if s < first - 3 or s > last + 3:
            continue
        res["printed"][s] = v
    # a leaf bound out of order keeps the number it carries (Galaxy 1952-03 prints 156 after 158):
    # a clear number on the page, near the predicted one, that no other page carries
    carried = {}
    for s in scans:
        for v, st in cands[s]:
            if res["printed"][s] == v:
                carried.setdefault(v, []).append(s)
    for s in scans:
        strong = [v for v, st in cands[s] if st == 2]
        p = res["printed"][s]
        if not strong or p is None or p in [v for v, _ in cands[s]]:
            continue
        v = strong[0]
        if 0 < abs(v - p) <= 4 and not carried.get(v):
            res["misplaced"][s] = v
    for s, v in res["misplaced"].items():
        res["printed"][s] = v
    # an unnumbered leaf (a plate, an inserted advertisement) where the numbering steps over it: the
    # offset changes there, and the leaf would repeat its neighbour's number; it carries none
    seen = {}
    for s in scans:
        v = res["printed"][s]
        if v is None:
            continue
        if v in seen:
            o = seen[v]
            # keep the page whose own number confirms it
            if v in [x for x, _ in cands[s]] and v not in [x for x, _ in cands[o]]:
                res["printed"][o] = None
                seen[v] = s
            else:
                res["printed"][s] = None
        else:
            seen[v] = s
    res["segments"] = [{"offset": g["offset"], "from": g["from"], "to": g["to"], "support": g["support"]} for g in good]
    return res


def scan_index(fol):
    """printed page -> scan page (the first scan page carrying it)."""
    idx = {}
    for s in sorted(fol["printed"]):
        v = fol["printed"][s]
        if v is not None and v not in idx:
            idx[v] = s
    return idx


def scan_of_printed(fol, printed):
    idx = scan_index(fol)
    if printed in idx:
        return idx[printed]
    if not idx:
        return None
    near = min(idx, key=lambda f: abs(f - printed))
    if abs(near - printed) > 12:
        return None
    return idx[near] + (printed - near)


# ------------------------------------------------------------------ the contents page

def _split_tail_number(t):
    """'WEREWOLVES OF WAR D. W. HALL 153' -> ('WEREWOLVES OF WAR D. W. HALL', 153). Also tolerates the
    OCR's damage at the end ('... 13.', '... ?1' gives no number)."""
    m2 = re.match(r"^(.*?[A-Za-z].*?\S)[\s.·•…]*\s([0-9]{1,4})\s*(?:,|&|and)\s*[0-9]{1,4}[\s.,;:*'\"’)]*$", t)
    if m2:
        return m2.group(1), int(m2.group(2))                # 'Railroad Rimes ..... 57, 97': the first
    m = re.match(r"^(.*?\S)[\s.·•,;:_~=…\-—]*\s([0-9lIOS]{1,4})[\s,;:*'\"’)]*\.?[\s,;:*'\"’)]*$", t)
    if not m:
        return t, None                                       # a number followed by a leader is in the title ('In The Year 2000 . . . . .')
    v = int_token(m.group(2))
    if v is None:
        return t, None
    head = m.group(1)
    if re.search(r"(?:\bpart|\bpt\.?|\bno\.?|#|\bchapter|\bbook|\bvol\.?)\s*\(?$", head, re.I) or \
            (t.rstrip().endswith(")") and head.count("(") > head.count(")")):
        return t, None                                       # 'The Portal to Power (Part 3)'
    # '35,000 words', '1950', 'No. 4', a price
    if re.search(r"(?:words?|no\.?|vol\.?|number|\$|,)\s*$", head, re.I) or re.search(r"\d,$", head):
        return t, None
    return head, v


def _split_title_author(label):
    """Candidates (title, author, kind) for an entry's text, most likely first. kind says how the split
    was found: 'by' and 'leader' are printed; 'initials' (a name with initials) is nearly as sure; 'name'
    (capitalised words at the end) and 'tail' (the last two or three words) are guesses that stand only
    when a by-line on the page confirms them; 'whole' keeps the text as the title."""
    s = re.sub(r"\s+", " ", label).strip()
    s = PART_RE.sub(" ", s)
    s = re.sub(r"(?:\s*[.·•…]){2,}\s*$", "", s).strip()
    cands = []
    m = re.search(r"^(.*?)\s*[,.]?\s*\b(?:by|BY|By)\s+(.+)$", s)
    if m and 2 <= len(m.group(2).split()) <= 7 and _looks_like_name(m.group(2)) and m.group(1).strip():
        cands.append((m.group(1), m.group(2), "by"))
        cands.append((s, "", "whole"))
        return _clean_cands(cands)
    leader = LEADER_RE.search(s)
    if leader:
        parts = [x for x in LEADER_RE.split(s) if x and x.strip(" .,;:")]
        if len(parts) >= 2:
            cands.append((parts[0], " ".join(parts[1:]), "leader"))
            cands.append((s, "", "whole"))
            return _clean_cands(cands)
        else:
            # a leader with nothing after it: the entry has no author
            cands.append((parts[0] if parts else s, "", "leader"))
            return _clean_cands(cands)
    s2 = TYPE_LABEL.sub(" ", s)
    s2 = re.sub(r"\s+", " ", s2).strip()
    md = re.match(r"^(.{3,}?)\s[.·•]\s+([A-Z][A-Za-z.'’\- ]+(?:,\s*Jr\.?)?)$", s2)
    if md and _looks_like_name(re.sub(r",\s*Jr\.?$", "", md.group(2))):
        cands.append((md.group(1), md.group(2), "leader"))   # 'SPAWN OF THE RED GIANTS . Frank Belknap Long, Jr.'
    mc = re.match(r"^([A-Z0-9][A-Z0-9 ,'’!?\-—:&.\"“”]*[A-Z0-9!?'’\"”])\s+([A-Z][a-z][A-Za-z.'’\- ]+)$", s2)
    if mc and _looks_like_name(mc.group(2)) and re.search(r"[A-Z]{3}", mc.group(1)):
        cands.append((mc.group(1), mc.group(2), "caps"))      # 'TRIGGER TRIANGLE Don James'
    hon = r"(?:(?:Capt(?:ain)?|Dr|Major|Col(?:onel)?|Lieut(?:enant)?|Sgt|Prof(?:essor)?|Mrs?|Miss|Sir|Lt|Commander|Rev)\.?\s+)?"
    name_pats = [
        ("initials", hon + r"(?:[A-Z]\.\s*){1,3}[A-Z][A-Za-z'’\-]+(?:,?\s+Jr\.?)?"),
        ("initials", hon + r"[A-Z][a-zA-Z'’\-]+\s+(?:[A-Z]\.\s*){1,2}[A-Z][A-Za-z'’\-]+(?:,?\s+Jr\.?)?"),
        ("name", hon + r"[A-Z][a-zA-Z'’\-]+(?:\s+(?:de|del|van|von|du|la|le|St\.?|Mac|Mc)?\s*[A-Z][a-zA-Z'’\-]+){1,2}(?:,?\s+Jr\.?)?"),
    ]
    for kind, pat in name_pats:
        m = re.search(r"^(.{3,}?)[\s,.;:]+(" + pat + r")\s*$", s2)
        if m:
            cands.append((m.group(1), m.group(2), kind))
    w = s2.split()
    for k in (2, 3):
        if len(w) > k + 1:
            cands.append((" ".join(w[:-k]), " ".join(w[-k:]), "tail"))
    cands.append((s2, "", "whole"))
    return _clean_cands(cands)


def _clean_cands(cands):
    out, seen = [], set()
    for t, a, kind in cands:
        t, a = clean_title(TYPE_LABEL.sub(" ", t)), clean_author(a)
        if len(t) < 2 or (a and (len(a.split()) > 7 or not re.search(r"[A-Za-z]{2}", a))):
            continue
        k = (norm(t), norm(a))
        if k not in seen:
            seen.add(k)
            out.append((t, a, kind))
    return out


def _is_section(t):
    t = t.strip()
    if re.match(r"^[○●◆◇■□•★☆]\s*[A-Z][A-Z \-—&'’]{3,40}$", t) and not re.search(r"\d", t):
        return True                                       # '○ SPECIAL PICTURE-STORY FEATURE'
    return bool(SECTION_WORDS.match(t)) and len(t.split()) <= 8


NUM_LINE = re.compile(r"^[\W_]*([0-9lIOS]{1,4})(?:\s*(?:&|and|,)\s*\d{1,4})?[\W_]*$")


NAME_STOP = {"the", "of", "in", "a", "an", "to", "for", "from", "with", "at", "on", "is", "my", "his", "her", "its",
             "who", "what", "when", "where", "how", "why", "no", "not", "all", "page", "story", "stories", "editor's",
             "department", "contents", "issue", "magazine", "tales", "features", "letters"}


def _looks_like_name(t):
    t = re.sub(r"^\W*(?:by|BY|By)\s+", "", t.strip())
    w = t.split()
    if any(x.lower().strip(".,;:'’") in NAME_STOP for x in w if not re.fullmatch(r"[A-Za-z]\.?", x)):
        return False
    alpha = [x for x in w if re.search(r"[A-Za-z]{2}", x)]
    if len(alpha) >= 3 and any(x.isupper() for x in alpha) and any(x[:1].isupper() and not x.isupper() for x in alpha):
        return False                       # 'TRIGGER TRIANGLE Don James': a title in capitals and a name
    if STAFF_RE.match(t) or NOT_TITLE.match(t) or MONTH_ONLY.match(t):
        return False
    if not w or len(w) > (10 if re.search(r"\s(?:and|&)\s", t) else 6) or re.search(r"[.!?]$", t) and not re.search(r"\b[A-Z]\.$", t):
        return False
    caps = sum(1 for x in w if x[:1].isupper() or re.fullmatch(r"(?:de|del|der|den|van|von|ten|ter|du|da|di|dos|y|la|le|and|&)", x))
    return caps >= len(w) - 0 and not LEADER_RE.search(t)


LEAD_STRIP = re.compile(r"^[\s.·•…:;,_~=\-—]+|[\s.·•…:;,_~=]+$")


def _cell(x):
    """A cell without the leader dots at its ends ('. . . . Talbot Mundy' -> 'Talbot Mundy')."""
    return LEAD_STRIP.sub("", x).strip()


def _sec(x):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z ]+", " ", x)).strip()


def _make_entry(title, author, num, section, region, k, line, kind):
    if MONTH_ONLY.match(author or ""):
        author = ""
    title_c = clean_title(TYPE_LABEL.sub(" ", PART_RE.sub(" ", title)))
    e = {"label": (title + (" by " + author if author else "")).strip(), "page": num, "section": section,
         "y": (region.get("bbox") or [0, 0])[1] + k * 0.01, "x": (region.get("bbox") or [0])[0], "line": line}
    if author:
        e["splits"] = [(title_c, clean_author(author), kind), (clean_title(title + " " + author), "", "whole")]
    else:
        e["splits"] = _split_title_author(title) if kind == "free" or re.search(r"[\s,]\b(?:by|BY|By)\s+[A-Z]", title) \
            else [(title_c, "", kind)]
    if not e["splits"]:
        e["splits"] = [(clean_title(title) or title.strip(), clean_author(author), "whole")]
    sure = [x for x in e["splits"] if x[2] not in ("name", "tail")] or e["splits"]
    e["title"], e["author"] = sure[0][:2]
    pm = PART_RE.search(line)
    if pm:
        e["part"] = pm.group(0).strip(" ()")
    tl = TYPE_LABEL.search(title)
    if tl and re.search(r"[A-Za-z]", tl.group(0)):
        e["type_label"] = tl.group(0).strip(" ,.;:()")
    return e


def parse_blocks(p, maxpage=None):
    """Contents set as a list of cells, the way Surya returns a TableOfContents (or ListGroup) block:
    'Title . . . .' / 'AUTHOR' / '12' (or 'AUTHOR 12'), entries apart by blank lines, the blurb in a cell
    of its own. Returns (entries, indices of regions read this way)."""
    out, used = [], set()
    section = ""
    for ri, r in enumerate(p.get("regions") or []):
        t = region_text_for_toc(r)
        lab = (r.get("label") or "").lower()
        if not t.strip() or lab in ("pageheader", "pagefooter", "footnote"):
            continue
        tt = t.strip()
        if "\n" not in tt and (_is_section(tt) or (lab == "sectionheader" and len(tt.split()) <= 6 and not re.search(r"\d", tt)
                                                    and not re.search(r"contents|cover", tt, re.I))):
            section = _sec(tt)
            continue
        raw = [y for x in t.split("\n") if len(x) <= MAX_ROW_CHARS for y in _split_two(x.strip())]
        if sum(1 for x in raw if x) < 3:
            continue
        def anchor(x):
            m = NUM_LINE.match(x)
            if m and int_token(m.group(1)) is not None:
                return "", int_token(m.group(1))
            head, num = _split_tail_number(x)
            led = bool(re.search(r"(?:[.·•…]\s*){3,}\s*[0-9lIOS]{1,4}\W*$", x))
            if num is not None and _looks_like_name(_cell(head)) and not LEADER_RE.search(head) and not led:
                return _cell(head), num                      # 'H. G. Wells 581'
            if num is not None and (LEADER_RE.search(head) or led) and re.search(r"[A-Za-z]{3}", head):
                return "\x00" + head, num                   # 'The Man-Trap .....Hamilton Craigie 625'
            if num is not None and re.search(r"[A-Za-z]{3}", head) and len(head.split()) <= 10 and not _prose(head) \
                    and not NOT_ENTRY.search(head) and not re.search(r"^\W*\(|\bby\s", head, re.I):
                return "\x01" + head, num                   # 'The Camp-Fire 89': a title alone with its number
            return None, None
        n_anchor = sum(1 for x in raw if x and anchor(x)[1] is not None)
        if n_anchor < 2 and lab not in ("tableofcontents", "listgroup"):
            continue
        es, pending, blank = [], [], 0
        fresh = True
        for x in raw + [""]:
            if not x:
                blank += 1
                continue
            if re.fullmatch(r"[\s.·•…:;,_~=\-—]+", x):
                continue                                     # a line of leader dots
            blank_before = blank
            if blank and len(pending) == 2 and lab in ("tableofcontents", "listgroup") and not _prose(pending[0]) \
                    and _looks_like_name(_cell(pending[1])) and not NOT_TITLE.match(pending[0]) and not STAFF_RE.match(pending[0]) \
                    and re.search(r"[A-Za-z]{3}", pending[0]) and not pending[0].rstrip().endswith(":") and not _is_section(pending[0]):
                # 'The Wolf of St. Bonnot' / 'Seabury Quinn' with the page number lost: placed later by title and author
                es.append(_make_entry(_cell(pending[0]), _cell(pending[1]), None, section, r, len(es), " / ".join(pending), "block"))
                pending = []
                fresh = False
            if blank and len(pending) == 1 and (re.search(r"(?:[.·•…]\s*){3,}$", pending[0]) or
                                                (pending[0].upper() == pending[0] and re.search(r"[A-Z]{3}", pending[0]) and not _prose(pending[0])
                                                 and not _is_section(pending[0]))) \
                    and anchor(x)[1] is not None and anchor(x)[0] == "":
                blank = 0                                    # the number of the entry above, after a blank line
            if blank and len(pending) == 1 and re.search(r"(?:[.·•…]\s*){3,}$", pending[0]) and not _prose(pending[0]) \
                    and anchor(x)[1] is not None and anchor(x)[0] and not anchor(x)[0].startswith(("\x00", "\x01")):
                # 'The Voyage of the Neutralia (part 2) . . . . .' / blank / 'B. Wallis 726' (Weird Tales, 1937-12): the
                # title line ends in its leader, the author and the number are the next printed line. Without this the
                # title line was dropped at the blank line and the author's name became the title (toc_folio 1.3).
                blank = 0
            if blank and pending:
                one = pending[0] if len(pending) == 1 else ""
                if es and one and re.match(r"^(?:an?|the)\s", one, re.I) and TYPE_LABEL.search(one) and len(one.split()) <= 7:
                    es[-1]["type_label"] = one.strip(" .")
                    pm = PART_RE.search(one)
                    if pm:
                        es[-1]["part"] = pm.group(0).strip(" ()")
                elif len(pending) == 1 and fresh and (_is_section(one) or (one.isupper() and len(one.split()) <= 4 and not LEADER_RE.search(one))):
                    section = _sec(one)
                elif es and len(" ".join(pending).split()) >= 4:
                    es[-1].setdefault("blurb", " ".join(pending)[:400])
                pending = []
                fresh = True
            blank = 0
            head, num = anchor(x)
            if num is None and len(pending) == 1 and re.search(r"(?:[.·•…]\s*){3,}$", pending[0]) and not _prose(pending[0]):
                # under a title that ends in its leader, a line of description with the number is the entry's
                # number even when it reads as prose ('The Camp-Fire .....' / 'Where readers, writers and
                # adventurers meet 6': Adventure, 1947-09; toc_folio 1.3)
                h2, n2 = _split_tail_number(x)
                if n2 is not None and re.search(r"[A-Za-z]{3}", h2) and len(h2.split()) <= 12 and not NOT_ENTRY.search(h2) \
                        and not re.search(r"\bby\s", h2, re.I):
                    head, num = "\x01" + h2, n2
            if num is not None and head and not head.startswith(("\x00", "\x01")) and (not pending or all(_prose(c) for c in pending)):
                head = "\x01" + head                        # a name-like title with nothing above it ('Ask Adventure 92')
            if num is not None and head and head.startswith("\x01"):
                title, blurb_here = _cell(head[1:]), None
                if len(pending) == 1 and re.search(r"(?:[.·•…]\s*){3,}$", pending[0]) and not _prose(pending[0]) \
                        and re.search(r"[A-Za-z]{3}", pending[0]) and not _is_section(pending[0]) \
                        and not NOT_TITLE.match(pending[0]) and not STAFF_RE.match(pending[0]):
                    # 'The Camp-Fire .....' / 'Where readers, writers and adventurers meet 6': the title ends in its
                    # leader; the number's line is the department's description (Adventure, 1947-09; toc_folio 1.3)
                    title, blurb_here = _cell(pending[0]), title
                elif pending and es and all(_prose(c) or len(c.split()) >= 4 for c in pending):
                    es[-1].setdefault("blurb", " ".join(pending)[:400])
                if maxpage and num > maxpage + 8:
                    num = None
                if title and not NOT_ENTRY.search(title) and not NOT_TITLE.match(title) and not STAFF_RE.match(title):
                    es.append(_make_entry(title, "", num, section, r, len(es), " / ".join(pending + [x]) if blurb_here else x, "free"))
                    if blurb_here:
                        es[-1]["blurb"] = blurb_here[:400]
                pending = []
                fresh = False
                continue
            if num is not None and (pending or head):
                cells = [_cell(c) for c in pending if not (fresh and _is_section(c))]
                for c in pending:
                    if fresh and _is_section(c):
                        section = _sec(c)
                cells = [c for c in cells if c and not NOT_TITLE.match(c) and not STAFF_RE.match(c)]
                prose = _prose
                while len(cells) >= 2 and prose(cells[-2]) and not prose(cells[-1]):
                    if es:
                        es[-1].setdefault("blurb", cells[-2][:400])
                    cells.pop(-2)
                if head and head.startswith("\x00"):           # title and author on the number's line
                    parts = [q for q in LEADER_RE.split(_cell(head[1:])) if q.strip()]
                    title, author = parts[0].strip(), (" ".join(parts[1:]).strip() if len(parts) > 1 else "")
                    if pending and len(" ".join(pending).split()) >= 4 and es:
                        es[-1].setdefault("blurb", " ".join(pending)[:400])
                    cells = [title]
                elif not head and any(re.match(r"^\W*(?:by|BY|By)\s+[A-Z]", c) for c in cells[1:]):
                    # 'Title' / 'Illustrated by ...' or a blurb / 'By Author' / '25': the title is the first cell
                    bi = max(k for k, c in enumerate(cells) if re.match(r"^\W*(?:by|BY|By)\s+[A-Z]", c))
                    author = re.sub(r"^\W*(?:by|BY|By)\s+", "", " ".join(cells[bi:]))
                    title = cells[0]
                elif head:                                      # the author was in the number's cell
                    title, author = (cells[-1] if cells else ""), head
                    if len(cells) >= 2 and re.match(r"^\W*(?:by|BY|By)\s.*\b(?:and|&)\s*$", cells[-1]):
                        title, author = cells[-2], re.sub(r"^\W*(?:by|BY|By)\s+", "", cells[-1]) + " " + head
                elif len(cells) >= 3 and re.match(r"^\W*(?:by|BY|By)\s.*\b(?:and|&)\s*$", cells[-2]):
                    title, author = cells[-3], re.sub(r"^\W*(?:by|BY|By)\s+", "", cells[-2]) + " " + cells[-1]
                elif len(cells) >= 2 and (_looks_like_name(cells[-1]) or re.fullmatch(r"(?i)the\s+editors?", cells[-1].strip(" .")) or
                                          LEADER_RE.search(pending[-2] if len(pending) >= 2 else "")):
                    title, author = cells[-2], cells[-1]
                    if len(cells) >= 3:
                        # 'THE RUN OF THE CARDS. A Short Story' / 'When sharp meets crook.' / 'Percival Wilde': the title is the
                        # cell in capitals or with its type label, the cells after it the blurb
                        def capsy(c):
                            letters = [ch for ch in c.split(". ")[0] if ch.isalpha()]
                            return letters and sum(ch.isupper() for ch in letters) >= 0.6 * len(letters)
                        first = [c for c in cells[:-1] if capsy(c) or TYPE_LABEL.search(c)]
                        if first and first[0] != cells[-2]:
                            k0 = cells.index(first[0])
                            title = first[0]
                            if es is not None and cells[k0 + 1:-1]:
                                blurb_here = " ".join(cells[k0 + 1:-1])[:400]
                            else:
                                blurb_here = ""
                elif cells:
                    title, author = cells[-1], ""
                    up0 = [ch for ch in cells[0].split(". ")[0] if ch.isalpha()]
                    up1 = [ch for ch in cells[-1] if ch.isalpha()]
                    if len(cells) >= 2 and up0 and sum(c.isupper() for c in up0) >= 0.7 * len(up0) and \
                            up1 and sum(c.isupper() for c in up1) < 0.5 * len(up1):
                        title = cells[0]                  # 'WHAT SCIENCE FICTION MEANS TO ME' / 'Prize Contest Letters.....' / '88'
                else:
                    title, author = "", ""
                if title and LEADER_RE.search(title):
                    parts = [q for q in LEADER_RE.split(title) if q.strip()]
                    title = parts[0] if parts else title
                if maxpage and num > maxpage + 8:
                    num = None
                if title and not NOT_ENTRY.search(title) and not NOT_TITLE.match(title):
                    es.append(_make_entry(title, author, num, section, r, len(es), " / ".join(pending + [x]),
                                          "block" if author or len(cells) == 1 else "free"))
                pending = []
                fresh = False
                continue
            if not pending:
                fresh = fresh or bool(blank_before)
            pending.append(x)
        if len(es) >= 2 or (es and lab in ("tableofcontents", "listgroup")):
            out += es
            used.add(ri)
    return out, used


# The leader groups in TWO_RE are one character class ([\s.·•…]*), not a repeated group of optional spaces around a
# dot, which on a line of spaced dots that does not match tries every way of sharing the spaces (new_worlds_1949_49akv10jpg).
# A contents row is short. OCR can give a whole paragraph, or a whole page of prose, as one line (15,702 characters
# on one page of mammoth_detective_1946_07); the row patterns below have several lazy groups, and on lines that long
# they take hours. Lines longer than this are not read as rows.
MAX_ROW_CHARS = 300


TWO_RE = re.compile(r"^(.*?[A-Za-z]{3}.*?)[\s.·•…]*\s+([0-9]{1,4})\s+([A-Z\"“‘'][^\d]*?[A-Za-z]{3}[^\d]*?)[\s.·•…]*\s+([0-9lIOS]{1,4})\W*$")


def _split_two(x):
    """'Interglacial Forest in Alaska . . . 65 Sun Dance on Fort Hall Reservation . 98' -> two entries."""
    m = TWO_RE.match(x)
    if not m or re.search(r"(?:no\.?|vol\.?|number|,)\s*$", m.group(1), re.I) or re.search(r"\bwords?\b", x, re.I):
        return [x]
    return [m.group(1) + " . . . " + m.group(2), m.group(3) + " . . . " + m.group(4)]


def _prose(c):
    """A blurb rather than a title: six words or more, and either many words in lower case or a sentence's end
    ('The Voice of the Pack By Edison Marshall' is a title; 'Chester Fay, super-cracksman, comes back' is not)."""
    c = LEAD_STRIP.sub("", LEADER_RE.sub(" ", c or "")).strip()
    w = [x for x in c.split() if re.search(r"[A-Za-z]", x)]
    if len(w) < 6:
        return False
    low = sum(1 for x in w if x[:1].islower())
    return low >= 0.4 * len(w) or bool(re.search(r"[.!?…—–-]['’\"”]?\s*$", c)) and low >= 1


def _rows(lines):
    """Lines grouped into printed rows (title box, author box and number box side by side), left to right."""
    rows = []
    for l in sorted(lines, key=lambda l: ((l["y0"] + l["y1"]) / 2, l["x0"])):
        h = max(4.0, l["y1"] - l["y0"])
        placed = False
        for row in rows[-3:]:
            r0 = row[0]
            ov = min(l["y1"], max(q["y1"] for q in row)) - max(l["y0"], min(q["y0"] for q in row))
            rh = max(4.0, min(q["y1"] - q["y0"] for q in row))
            is_num = bool(NUM_LINE.match(l["text"].strip()))
            clash = any(q["x0"] < l["x1"] and l["x0"] < q["x1"] for q in row)
            if is_num and clash:
                clash = not all(l["x0"] >= q["x0"] + 0.6 * (q["x1"] - q["x0"]) for q in row if q["x0"] < l["x1"] and l["x0"] < q["x1"])
            # boxes join a row only when each is a single printed line (lines laid out inside a block of
            # several lines have no true position), or the newcomer is a page number on its own
            single = not l.get("synth") and not any(q.get("synth") for q in row)
            if ov >= 0.5 * min(h, rh) and not clash and (single or (is_num and not l.get("synth"))):
                row.append(l)
                placed = True
                break
        if not placed:
            rows.append([l])
    for row in rows:
        row.sort(key=lambda q: q["x0"])
    return rows


def parse_contents_page(p, maxpage=None):
    """Entries on one page: the cell lists first (parse_blocks), then the printed rows. A row is anchored
    by a page number — at its end, or in a box of its own — or by a dot leader between title and author
    when the number cannot be read. A row of title box, author box and number box gives title and author
    by box; a row that is only a name and a number takes the title from the line above ('by Theodore
    Sturgeon 4' under 'BABY IS THREE'); one line of text is split by 'by', by the leader, or by the shape
    of a name at its end."""
    bentries, bused = parse_blocks(p, maxpage)
    Hp = p.get("height") or 1
    lines = [l for l in page_lines(p) if l["ri"] not in bused and (l["label"] or "").lower() not in ("pageheader", "pagefooter", "footnote")
             and not (l["y1"] < 0.06 * Hp or l["y0"] > 0.94 * Hp) and len(l["text"]) <= MAX_ROW_CHARS]
    # two entries set side by side in one line ('Fifty Million Trees 66 Preservation Of Moles 122')
    split_lines = []
    for l in lines:
        m = re.match(r"^(.*?[A-Za-z].*?)\s+([0-9]{1,4})\s+([A-Z\"“‘'].*?[A-Za-z].*?\s[0-9lIOS]{1,4}\W*)$", l["text"])
        if m and not re.search(r"(?:no\.?|vol\.?|number|,)\s*$", m.group(1), re.I):
            mid = l["x0"] + (l["x1"] - l["x0"]) * len(m.group(1) + m.group(2)) / max(1, len(l["text"]))
            a = dict(l, text=m.group(1) + " " + m.group(2), x1=mid - 1)
            b = dict(l, text=m.group(3), x0=mid + 1)
            split_lines += [a, b]
        else:
            split_lines.append(l)
    lines = split_lines
    rows0 = _rows(lines)
    merged = []
    for row in rows0:
        if len(row) == 1 and NUM_LINE.match(row[0]["text"].strip()) and merged:
            prev = merged[-1]
            pt = " ".join(q["text"] for q in prev)
            lh = max(6.0, row[0]["y1"] - row[0]["y0"])
            if not re.search(r"\d\W*$", pt) and 0 <= row[0]["y0"] - max(q["y1"] for q in prev) <= 2.0 * lh \
                    and row[0]["x0"] >= min(q["x0"] for q in prev):
                merged[-1] = prev + row
                continue
        merged.append(row)
    rows = []
    for row in merged:
        cut, cur = [], []
        for k, q in enumerate(row):
            cur.append(q)
            if k < len(row) - 1 and len(cur) >= 1 and re.search(r"[A-Za-z]{3}.*?(?:[.·•…]\s*){2,}\s*[0-9]{1,4}\W*$", " ".join(c["text"] for c in cur)) \
                    and re.search(r"[A-Za-z]{3}.*\d{1,4}\W*$", " ".join(c["text"] for c in row[k + 1:])):
                cut.append(cur)
                cur = []
        if cur:
            cut.append(cur)
        rows += cut
    entries, consumed, row_entry = [], set(), {}
    section, section_row = "", None
    prev_section = ""
    for i, row in enumerate(rows):
        segs = [q["text"].strip() for q in row if q["text"].strip()]
        if not segs:
            continue
        # the page number: the last segment when it is a number alone, else at the end of the last segment
        num = None
        if len(segs) >= 2 and NUM_LINE.match(segs[-1]) and int_token(NUM_LINE.match(segs[-1]).group(1)) is not None:
            num = int_token(NUM_LINE.match(segs[-1]).group(1))
            segs = segs[:-1]
        else:
            head, n = _split_tail_number(segs[-1])
            if n is not None:
                num, segs = n, segs[:-1] + ([head] if head.strip() else [])
        text = " ".join(segs)
        lab0 = (row[0]["label"] or "").lower()
        if num is None and len(segs) == 1 and (_is_section(text) or (lab0 == "sectionheader" and len(text.split()) <= 6
                                                                      and not re.search(r"\d|contents", text, re.I))):
            prev_section, section, section_row = section, _sec(text), i
            continue
        has_leader = bool(LEADER_RE.search(" ".join(q["text"] for q in row))) and bool(re.search(r"[A-Za-z]{3}", text))
        led_to_num = bool(re.search(r"(?:[.·•…]\s*){3,}\s*[0-9lIOS]{1,4}\W*$", " ".join(q["text"] for q in row)))
        if num is None and not (has_leader and len(LEADER_RE.split(text)) >= 2 and all(re.search(r"[A-Za-z]{3}", x) for x in LEADER_RE.split(text) if x.strip())):
            continue
        if not text.strip() or re.fullmatch(r"[\W\d]*", text):
            continue
        if NOT_ENTRY.search(text) or NOT_TITLE.match(text) or re.search(MONTHS + r"\s*\d{1,2},?\s*$", text, re.I):
            continue
        if maxpage and num is not None and num > maxpage + 8:
            num = None
            if not has_leader:
                continue
        keep = [k for k, x in enumerate(segs) if _cell(x) and not STAFF_RE.match(_cell(x)) and not NOT_TITLE.match(_cell(x))]
        # a staff name in a masthead column beside the entry ('WILLIAM L. HAMLING' | 'by George ... 167')
        if len(keep) >= 2 and re.match(r"^\W*(?:by|BY|By)\s", _cell(segs[keep[-1]])) and _looks_like_name(_cell(segs[keep[0]])) \
                and re.search(r"\b[A-Z]\.\s", _cell(segs[keep[0]])) and (row[0]["label"] or "").lower() != "sectionheader":
            keep = keep[-1:]
        if not keep:
            continue
        ax0 = row[min(keep, key=lambda k: k)]["x0"] if len(row) > max(keep) else row[0]["x0"]
        ax1 = row[min(len(row) - 1, max(keep))]["x1"]
        segs = [_cell(segs[k]) for k in keep]
        title = author = None
        kind = "free"
        if len(segs) >= 2 and not LEADER_RE.search(segs[0]):
            title, author, kind = segs[0], " ".join(segs[1:]), "box"
        seg0 = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", segs[0])).strip() if len(segs) == 1 else ""
        # the row above read as a title alone, its leader at the end and no number ('Brick-top In Blue Rock . . . . .')
        prior = row_entry.get(i - 1)
        above_title_only = (prior is not None and prior.get("page") is None and not prior.get("author")
                            and bool(re.search(r"(?:[.·•…]\s*){3,}$", " ".join(q["text"] for q in rows[i - 1]))))
        name_only = len(segs) == 1 and (re.match(r"^\W*(?:by|BY|By)\s", seg0) and _looks_like_name(seg0)
                                        or (_looks_like_name(seg0) and not has_leader and len(seg0.split()) <= 5)
                                        # 'Johnston McCulley . . .' with its number under that title: a name followed by
                                        # its own leader (Western Story, 1935-07; toc_folio 1.3). Only under such a row:
                                        # 'Blood Money—Rafael Sabatini . . . . . 60' also has the shape of a name.
                                        or (has_leader and above_title_only and len(seg0.split()) <= 7
                                            and _looks_like_name(_cell(seg0))
                                            and len([q for q in LEADER_RE.split(seg0) if q.strip()]) == 1))
        if name_only and has_leader:
            seg0 = _cell(seg0)
        prose_anchor = len(segs) == 1 and not LEADER_RE.search(segs[0]) and not re.search(r"\s[.·•]\s|\bby\s+[A-Z]", segs[0], re.I) \
            and (_prose(segs[0]) or len(segs[0].split()) >= 10)
        if name_only:
            segs = [seg0]
        if name_only or prose_anchor:
            # the title is the nearest row above in the same column: possibly the row taken as a section label;
            # a number at the end of a blurb ('... and when the cat began talking, the cop listened! 9') belongs to
            # the title and by-line above the blurb
            lh = max(6.0, row[0]["y1"] - row[0]["y0"])
            for j in range(i - 1, max(-1, i - (9 if prose_anchor else 4)), -1):
                up = rows[j]
                if j in consumed:
                    prior = row_entry.get(j)
                    if (name_only and prior is not None and prior.get("page") is None and not prior.get("author")
                            and re.search(r"(?:[.·•…]\s*){3,}$", " ".join(q["text"] for q in up))):
                        # the row above was read as a title alone, its number unread: it is this name's title
                        # ('The Man Who Didn't Like Texas .....' / 'CLIFTON ADAMS 102': Adventure, 1947-09; toc_folio 1.3)
                        entries.remove(prior)
                        del row_entry[j]
                    else:
                        break
                gap = row[0]["y0"] - max(q["y1"] for q in up)
                if gap > 3.0 * lh:
                    break
                Wp = p.get("width") or 2000
                over = [q for q in up if q["x1"] > ax0 - 0.45 * Wp and q["x0"] < ax1 and not STAFF_RE.match(q["text"]) and not NOT_TITLE.match(q["text"])]
                if not over:
                    continue
                ut = " ".join(q["text"] for q in over).strip()
                if _prose(ut):
                    continue                                  # the blurb of the entry before
                if name_only and seg0.isupper() and len(seg0.split()) >= 2 and not re.match(r"^\W*BY\s", seg0) \
                        and not re.search(r"\b[A-Z]\.", seg0) and not ut.isupper() and len(ut.split()) >= 4 \
                        and not re.search(r"(?:[.·•…]\s*){3,}$", ut):
                    # 'SUNLIGHT KILLS SNAKES 97' under a blurb set in title case; but a line above that ends in its
                    # leader is the title, and the capitals are its author ('When Your Number's Up .....' /
                    # 'EARL SUTTERFIELD 120': Adventure, 1947-09; toc_folio 1.3)
                    title, author, kind = seg0, "", "free"
                    break
                mb_up = re.match(r"^\W*(?:by|BY|By)\s+(.{3,60})$", ut)
                if prose_anchor and mb_up and _looks_like_name(mb_up.group(1)) and title is None:
                    author = mb_up.group(1)
                    consumed.add(j)
                    lh = max(lh, 6.0)
                    continue
                if re.search(r"\d\s*$", ut) or re.search(r"contents", ut, re.I) or (_is_section(ut) and j != section_row):
                    break
                if j == section_row and _is_section(ut):
                    break
                if prose_anchor:
                    if not author and not re.search(r"[A-Z]{2}|^[A-Z]", ut):
                        break
                    title, kind = ut, "box"
                    author = author or ""
                else:
                    title, author, kind = ut, re.sub(r"^\W*(?:by|BY|By)\s+", "", segs[0]), "box"
                consumed.add(j)
                if j == section_row:
                    section = prev_section
                break
        if title is None and prose_anchor:
            continue                                      # a blurb with a number and no title above it
        if title is None:
            title = " ".join(segs) + (" ....." if led_to_num and not LEADER_RE.search(" ".join(segs)) else "")
        consumed.add(i)
        if not author and i + 1 < len(rows):
            nxt = " ".join(q["text"] for q in rows[i + 1]).strip()
            mb = re.match(r"^\W*(?:by|BY|By)\s+(.{3,60})$", nxt)
            if mb and _looks_like_name(mb.group(1)) and not re.search(r"\d\s*$", nxt) and \
                    rows[i + 1][0]["y0"] - row[0]["y1"] < 2.0 * max(6.0, row[0]["y1"] - row[0]["y0"]):
                author, kind = mb.group(1), "box"
                title = LEADER_RE.split(title)[0] if LEADER_RE.search(title) else title
                consumed.add(i + 1)
        e = _make_entry(title, author or "", num, section, {"bbox": [row[0]["x0"], row[0]["y0"]]}, 0,
                        " | ".join(q["text"] for q in row), kind)
        entries.append(e)
        row_entry[i] = e
    # a blurb: prose rows under an entry, before the next one
    for k, e in enumerate(entries):
        nxt = entries[k + 1]["y"] if k + 1 < len(entries) else 1e9
        bl = [" ".join(q["text"] for q in row) for i, row in enumerate(rows) if i not in consumed and e["y"] < row[0]["y0"] < min(nxt, e["y"] + 240)
              and len(" ".join(q["text"] for q in row).split()) >= 4 and not NOT_ENTRY.search(" ".join(q["text"] for q in row))]
        if bl:
            e["blurb"] = " ".join(bl)[:400]
    entries = bentries + entries
    entries.sort(key=lambda e: (e["y"], e["x"]))
    return entries


NOT_PIECE = re.compile(r"\breg\.?\s+u\.?\s*s\.?|\bpat\.?\s+off|,\s*(?:managing\s+|associate\s+|assistant\s+|art\s+)?(?:editor|publisher|director|manager)\b|\$\s?\d|\d\s?¢|\bcents?\b|\bcoupon\b|\b(?:see|on)\s+page\s+\d|\bpage\s+\d+\s*[;,]|"
                       r"\bpgs?\.|\b\d+\s+pages\b|\(please\s+print\)|^\W*(?:name|address|city|state|zone)\W*$", re.I)


def is_piece(e):
    return not NOT_PIECE.search(e.get("line", "") + " " + e.get("title", ""))


CHAPTER_ENTRY = re.compile(r"^\W*(?:chapter\s+)?[IVXLC]{1,6}\s*[—–.:\-]\s*\S|^\W*chapter\s+[IVXLC\d]+", re.I)


def contents_score(p, entries, maxpage):
    joined = " ".join(l["text"] for l in page_lines(p))
    word = bool(re.search(r"\bCONTENTS\b|\bContents\b|TABLE OF CONTENTS|IN THIS ISSUE", joined)) or \
        any((r.get("label") or "").lower() in ("tableofcontents",) for r in p.get("regions") or [])
    entries = [e for e in entries if is_piece(e)]
    numbered = [e for e in entries if e["page"] is not None]
    distinct = len({e["page"] for e in numbered})
    titled = sum(1 for e in entries if len(toks(e["title"])) >= 1)
    in_range = sum(1 for e in numbered if not maxpage or e["page"] <= maxpage + 8)
    score = (3 if word else 0) + min(distinct, 15) + 0.3 * min(titled - len(numbered), 8) + 0.0 * in_range
    if numbered and distinct < 0.6 * len(numbered):
        score -= 3
    return score, word


def find_contents(pages, maxpage=None, front=16):
    """The contents page(s): the best-scoring page among the first `front` scans (and the last three, where a
    few magazines print it), plus a neighbouring page that continues it."""
    scans = sorted(pages)
    look = scans[:front] + [s for s in scans[-3:] if s not in scans[:front]]
    best = None
    parsed = {}
    for s in look:
        es = parse_contents_page(pages[s], maxpage)
        parsed[s] = es
        sc, word = contents_score(pages[s], es, maxpage)
        if s not in scans[:front] and not re.search(r"\bCONTENTS\b|\bContents\b|TABLE OF CONTENTS|IN THIS ISSUE",
                                                   " ".join(l["text"] for l in page_lines(pages[s]))):
            # at the back only a page that says, in words, that it is the contents: a publisher's list of titles
            # with their numbers at the back of a paperback is laid out like one and labelled like one
            # (Avon Fantasy Reader, 1947; toc_folio 1.3)
            continue
        if sum(1 for e in es if re.match(r"^[A-Z][A-Za-z'’.\- ]+,\s+[A-Z][A-Za-z.\- ]+:", e.get("label", ""))) >= 3:
            continue                                   # an index of authors ('CHRISTIE, AGATHA: ...') is not this issue's contents
        if sum(1 for e in es if MONTH_ONLY.match(e.get("line", "").split("|")[-1].split("/")[-1].strip() or "x")) >= 3:
            continue
        if sc >= 4 and (best is None or sc > best[0]):
            best = (sc, s)
    if not best:
        return [], []
    toc_pages = [best[1]]
    for nb in (best[1] + 1, best[1] - 1):
        if nb in pages:
            es = parsed.get(nb) if nb in parsed else parse_contents_page(pages[nb], maxpage)
            parsed[nb] = es
            sc, word = contents_score(pages[nb], es, maxpage)
            joined = " ".join(l["text"] for l in page_lines(pages[nb]))
            cont = bool(re.search(r"continued\s+from\s+(?:the\s+)?preceding\s+page|continued\s+on\s+next\s+page|"
                                  r"^\W*(?:table\s+of\s+)?contents\W*$", joined if False else "\n".join(l["text"] for l in page_lines(pages[nb])), re.I | re.M))
            real = [e for e in es if e["page"] is not None and is_piece(e) and (not maxpage or e["page"] <= maxpage + 8)]
            prices = sum(1 for e in es if not is_piece(e))
            if prices >= 2 and not cont:
                continue                                 # an advertisement with prices beside the contents page
            if (sc >= 4 and len(real) >= 3) or (cont and (word or len(real) >= 1)):
                toc_pages.append(nb)
    toc_pages.sort()
    entries, seen = [], set()
    for s in toc_pages:
        for e in parsed[s]:
            if not is_piece(e):
                continue
            k = (norm(e["title"]), e["page"])
            if k in seen:
                continue
            seen.add(k)
            e["toc_scan"] = s
            entries.append(e)
    return entries, toc_pages


# ------------------------------------------------------------------ evidence of a start on a page

def start_evidence(p):
    """Display lines and by-lines of a page (what a piece's opening looks like), and whether the page has
    the marks of an opening even when its title is unreadable (a credit line 'Author of ...', an
    illustrator's credit, a by-line). Running heads and page furniture are left out: a continuation page
    repeats the title in its running head."""
    lines = page_lines(p)
    H = p.get("height") or 1
    hs = [l["y1"] - l["y0"] for l in lines if not l["synth"] and l["text"]]
    med = statistics.median(hs) if hs else 0
    display, bylines, marks = [], [], 0
    for k, l in enumerate(lines):
        t = l["text"]
        lab = (l["label"] or "").lower()
        if lab in ("pageheader", "pagefooter", "footnote") or l["y1"] < 0.06 * H or l["y0"] > 0.94 * H:
            continue
        if re.match(r"^\W*(?:author\s+of|illustrated\s+by|illustrations?\s+by|a\s+(?:complete\s+)?(?:novel|novelette|novelet|story))\b", t, re.I):
            marks += 1
        if len(t.split()) > 14:
            continue
        if re.fullmatch(r"\W*(?:by|BY|By)\W*", t) and k + 1 < len(lines) and len(lines[k + 1]["text"].split()) <= 6:
            bylines.append(lines[k + 1]["text"])
            continue
        m = BYLINE_RE.match(t)
        if m and len(m.group(1).split()) <= 8 and not re.search(r"\b(?:illustrat|cover|drawn|photo)", t, re.I) \
                and _looks_like_name(m.group(1)):
            bylines.append(m.group(1))
            continue
        big = (med and not l["synth"] and len(t.split()) <= 12 and (l["y1"] - l["y0"]) > 1.5 * med)
        caps = sum(c.isupper() for c in t) >= 0.7 * max(1, sum(c.isalpha() for c in t)) and sum(c.isalpha() for c in t) >= 3
        if lab in ("sectionheader", "title") or big or (caps and l["y0"] < 0.6 * H):
            display.append(t)
    # a title and its by-line set in one display line, sometimes glued by the OCR ('Burnt Thingsby ROBERT C SANDISON')
    for d in list(display):
        m = re.match(r"^(.*?[A-Za-z'’!?\"”])\s*(?:by|BY|By)\s+([A-Z][A-Za-z.'’\- ]{3,40})$", d) or \
            re.match(r"^(.*?[a-z!?\"”])(?:by|BY)\s+([A-Z][A-Z.'’\- ]{3,40})$", d)
        if m and _looks_like_name(m.group(2)) and len(m.group(2).split()) >= 2:
            display.append(m.group(1).strip())
            bylines.append(m.group(2).strip())
    joined = [a + " " + b for a, b in zip(display, display[1:])]
    if bylines:
        marks += 1
    # a page that lists several pieces with their authors (an announcement of the next issues, a contents
    # page) is not the opening of any of them
    listed = sum(1 for d in display if re.search(r"[,—–-]\s*(?:by|BY|By)\s+[A-Z]", d)) + max(0, len(bylines) - 2)
    if listed >= 3:
        return [], [], 0
    return display + joined, bylines, marks


def title_match(title, line):
    """The title among a display line's words, with a penalty for a line much longer than the title (a
    sentence that happens to contain its words)."""
    nt, nd = len(toks(title)), len(toks(line, 1))
    if not nt or not nd:
        return 0.0
    return tok_sim(title, line) * min(1.0, (nt + 3) / nd)


HONORIFIC = re.compile(r"\b(?:capt(?:ain)?|dr|major|col(?:onel)?|lieut(?:enant)?|lt|sgt|sergeant|prof(?:essor)?|mrs?|miss|sir|"
                       r"commander|rev(?:erend)?|m\.?d|ph\.?d|jr|sr)\b\.?", re.I)


def author_key(a):
    return HONORIFIC.sub(" ", a or "")


def evidence_score(ev, title, author, byline_only=False):
    display, bylines = ev[0], ev[1]
    author = author_key(author)
    ts = max([title_match(title, d) for d in display] + [0.0]) if title else 0.0
    as_ = max([tok_sim(author, b) for b in bylines] + [0.0]) if author else 0.0
    if author and as_ < 0.5 and not byline_only:
        # the author's name inside a display line ('A Novelet by ...' set large) or a two-box by-line
        as_ = max(as_, 0.8 * max([tok_sim(author, d) for d in display] + [0.0]))
    gen = 0.15 if bylines else 0.0
    return ts, as_, gen


# ------------------------------------------------------------------ placing the entries

def spread_evidence(evid, s):
    """A page's evidence with its facing page's: a title set across the spread ('The FLAMING Curse' /
    'of Belden Hall'), a by-line on the right-hand page."""
    d0, b0 = evid.get(s, ([], [], 0))[:2]
    d1, b1 = evid.get(s + 1, ([], [], 0))[:2]
    cross = [a + " " + b for a in d0[-3:] for b in d1[:3]]
    return d0 + cross, b0 + b1


def place_entries(entries, pages, fol, toc_pages=()):
    """For every entry, the scan page where its piece begins.

    1. The page its printed number maps to, and the pages around it (a start often sits a leaf off: the
       title across the facing page, an inserted plate), each scored for the entry's title among the
       display lines and its author among the by-lines; the nearest page with the evidence wins.
    2. Only when the entry has no usable number (unreadable, outside the issue, or pointing to a page with
       no numbered neighbour) is the whole issue searched — and then title and author must both be found,
       because the title alone recurs on continuation pages and in announcements.
    3. Otherwise the page the number gives is kept, marked unconfirmed.
    Records how each entry was placed and a confidence."""
    scans = sorted(pages)
    evid = {s: start_evidence(pages[s]) for s in scans}
    after_toc = max(toc_pages) if toc_pages else 0
    vals = [v for v in fol["printed"].values() if v]
    lo, hi = (min(vals), max(vals)) if vals else (None, None)
    for e in entries:
        splits = [tuple(x) + (("block",) if len(x) == 2 else ()) for x in (e.get("splits") or [(e.get("title", ""), e.get("author", ""))])]
        num = e.get("page")
        pred = scan_of_printed(fol, num) if num is not None else None
        if pred is not None and pred not in pages:
            pred = None
        best = None
        if pred is not None:
            for s in range(pred - 2, pred + 3):
                if s not in pages or s <= after_toc:
                    continue
                # on the numbered page a title set across the spread counts; on a page around it only its own words
                ev = spread_evidence(evid, s) if s == pred else evid[s][:2]
                for k, (t, a, kind) in enumerate(splits[:7]):
                    dist = abs(s - pred)
                    ts, as_, gen = evidence_score(ev, t, a, byline_only=kind in ("name", "tail") or dist > 0)
                    if kind in ("name", "tail") and as_ < 0.7:
                        continue                  # a guessed name stands only when a by-line confirms it
                    if dist > 0 and ts < 0.6 and as_ < 0.8:
                        continue                  # away from the numbered page, the title or a clear by-line
                    opening = any(re.match(r"^\W*(?:chapter|part)\s+(?:i|1|one)\b", d, re.I) for d in evid[s][0])
                    if dist >= 2 and not (ts >= 0.6 and as_ >= 0.7) and not (ts >= 0.85 and opening):
                        continue                  # two leaves away only with title and by-line both (a department's
                                                  # name recurs on its later pages)
                    sc = ts + 0.9 * as_ + gen - 0.15 * dist + (0.2 if dist == 0 else 0) + (0.1 if k == 0 else 0)
                    if best is None or sc > best[0]:
                        best = (sc, s, t, a, ts, as_)
        if best is not None and (best[4] >= 0.6 or best[5] >= 0.7):
            # the earliest page of the spread that shows the title
            s = best[1]
            if s - 1 in pages and s - 1 > after_toc and best[4] >= 0.6:
                ts_prev = max([title_match(best[2], d) for d in evid[s - 1][0]] + [0.0])
                if ts_prev >= best[4] - 0.05 and ts_prev >= 0.6 and abs(s - 1 - pred) <= 2:
                    s = s - 1
            e["scan_page"], e["title"], e["author"] = s, best[2], best[3]
            e["placed_by"] = ("page number, confirmed on the page" if s == pred else
                              "page number, moved %+d to the page that shows it" % (s - pred))
            e["confidence"] = round(min(1.0, 0.5 + 0.5 * max(best[4], best[5])), 2)
            continue
        if pred is not None and evid[pred][2] and not (best and (best[4] >= 0.6 or best[5] >= 0.7)):
            e["scan_page"] = pred
            e["placed_by"] = "page number; the page opens a piece (credit or by-line) but its title is not readable"
            e["confidence"] = 0.6
            sure = [x for x in splits if x[2] not in ("name", "tail")]
            if sure:
                e["title"], e["author"] = sure[0][0], sure[0][1]
            continue
        if True:
            glob_best = None
            for s in scans:
                if s <= after_toc:
                    continue
                for t, a, kind in splits[:7]:
                    if not a or kind in ("name", "tail"):
                        continue
                    ts, as_, gen = evidence_score(evid[s], t, a, byline_only=True)
                    ts2, as2, _ = evidence_score(spread_evidence(evid, s), t, a, byline_only=True)
                    sc = max(ts + 0.9 * as_, ts2 + 0.9 * as2 - 0.2)
                    if ts + 0.9 * as_ < ts2 + 0.9 * as2 - 0.2:
                        ts, as_ = ts2, as2
                    if glob_best is None or sc > glob_best[0]:
                        glob_best = (sc, s, t, a, ts, as_)
            if glob_best and glob_best[4] >= 0.8 and glob_best[5] >= 0.75 and len(toks(glob_best[2])) >= 1 and \
                    not any(o is not e and o.get("scan_page") == glob_best[1] and o.get("confidence", 0) >= 0.9 for o in entries):
                e["scan_page"], e["title"], e["author"] = glob_best[1], glob_best[2], glob_best[3]
                pv = fol["printed"].get(glob_best[1])
                e["placed_by"] = "title and by-line found on the page" + (
                    "; no readable page number" if num is None else
                    "; the printed number %s is not in the issue" % num if pred is None else
                    "; the contents says p. %s, the page carries p. %s (a misread number or a missing leaf)" % (num, pv))
                e["confidence"] = round(min(1.0, 0.4 + 0.5 * min(glob_best[4], glob_best[5])), 2)
                if num is not None:
                    e["page_read"] = num
                e["page"] = fol["printed"].get(glob_best[1])
                continue
        if pred is not None:
            e["scan_page"] = pred
            e["placed_by"] = "page number only (nothing on the page confirms it)"
            e["confidence"] = 0.35
            sure = [x for x in splits if x[2] not in ("name", "tail")]
            if sure:
                e["title"], e["author"] = sure[0][0], sure[0][1]
        else:
            e["scan_page"] = None
            e["placed_by"] = "not placed"
            e["confidence"] = 0.0
    return entries


# ------------------------------------------------------------------ the whole issue

def analyse_issue(pages):
    """Folio map, contents entries and their placement for one issue (pages: {scan: page JSON})."""
    fol = fit_folios(pages)
    vals = [v for v in fol["printed"].values() if v]
    maxpage = max(vals) if vals else None
    entries, toc_pages = find_contents(pages, maxpage)
    kind = "pieces"
    # a book's list of chapters ('I—The Airplane Chums ..... 1') is not a list of pieces: the rules must
    # not start a record at every chapter
    if entries and sum(1 for e in entries if CHAPTER_ENTRY.match(e.get("label") or e["title"])) >= max(3, 0.6 * len(entries)):
        kind = "chapters"
    chapters = []
    if kind == "chapters":
        chapters, entries = entries, []
    place_entries(entries, pages, fol, toc_pages)
    unplaced = [e for e in entries if e.get("scan_page") is None]
    entries = [e for e in entries if e.get("scan_page") is not None]
    for e in entries + unplaced + chapters:
        e.pop("splits", None)
        e.pop("y", None)
        e.pop("x", None)
    entries.sort(key=lambda e: (e.get("scan_page") or 10 ** 6, e.get("page") or 10 ** 6))
    return {"version": VERSION, "folio": fol, "toc_pages": toc_pages, "toc_kind": kind, "entries": entries,
            "unplaced": unplaced, "chapters": chapters, "scan_index": scan_index(fol)}


def load_pages(d):
    pages = {}
    for f in sorted(glob.glob(os.path.join(d, "page_*.json"))):
        if os.path.getsize(f) == 0:
            continue
        p = json.load(open(f, encoding="utf-8"))
        p["regions"] = sorted(p["regions"], key=lambda r: r.get("order", 0))
        pages[p["page"]] = p
    return pages


# ------------------------------------------------------------------ self-test

def _mk(page, regions, W=1000, H=1400):
    return {"page": page, "width": W, "height": H,
            "regions": [{"label": lab, "bbox": list(b), "order": i, "text": t, "lines": []} for i, (lab, b, t) in enumerate(regions)]}


def selftest():
    # an issue of 12 scans: cover, ad, contents, then printed pages 4..12 on scans 4..12, with an
    # inserted unnumbered plate at scan 8 (so scans 9..12 carry 8..11), a garbled folio on scan 6,
    # a running head with the number at scan 5, and a story whose contents number is misread.
    P = {}
    P[1] = _mk(1, [("Text", (100, 600, 900, 700), "AMAZING TALES")])
    P[2] = _mk(2, [("Text", (100, 600, 900, 700), "Buy our tonic! 25 cents")])
    P[3] = _mk(3, [("Text", (100, 100, 900, 160), "CONTENTS"),
                   ("Text", (100, 200, 400, 230), "NOVELETS"),
                   ("Text", (100, 250, 600, 280), "BABY IS THREE"),
                   ("Text", (500, 290, 900, 320), "by Theodore Sturgeon 4"),
                   ("Text", (100, 350, 900, 380), "THE BLACK LAMP CAPTAIN S. P. MEEK"),
                   ("Text", (860, 350, 900, 380), "7"),
                   ("Text", (100, 410, 900, 440), "A blurb about the black lamp and its maker, who is mad."),
                   ("Text", (100, 470, 900, 500), "Space War . . . . . . . Neil R. Jones 9I"),
                   ("Text", (100, 530, 900, 560), "Parasite . . . . . . Harl Vincent 99"),
                   ("Text", (100, 1300, 900, 1330), "Vol. 5, No. 1 OCTOBER, 1952")])
    P[4] = _mk(4, [("SectionHeader", (100, 100, 900, 200), "BABY IS THREE"),
                   ("Text", (300, 220, 700, 250), "by Theodore Sturgeon"),
                   ("Text", (100, 300, 900, 1200), "I finally got in to see this Stern. He was a nothing."),
                   ("PageFooter", (480, 1350, 520, 1380), "4")])
    P[5] = _mk(5, [("Text", (100, 30, 900, 60), "AMAZING TALES 5"), ("Text", (100, 300, 900, 1200), "story text goes on here")])
    P[6] = _mk(6, [("Text", (100, 30, 900, 60), "6 AMAZING TALES"), ("Text", (100, 300, 900, 1200), "more text"),
                   ("PageFooter", (480, 1350, 520, 1380), "B")])
    P[7] = _mk(7, [("SectionHeader", (100, 100, 900, 200), "THE BLACK LAMP"),
                   ("Text", (300, 220, 700, 250), "By Captain S. P. Meek"), ("Text", (100, 300, 900, 1200), "Dr. Bird looked up."),
                   ("PageFooter", (480, 1350, 520, 1380), "7")])
    P[8] = _mk(8, [("Picture", (0, 0, 1000, 1400), "")])                    # an inserted plate, no number
    P[9] = _mk(9, [("Text", (100, 300, 900, 1200), "text"), ("PageFooter", (480, 1350, 520, 1380), "8")])
    P[10] = _mk(10, [("SectionHeader", (100, 100, 900, 200), "SPACE WAR"), ("Text", (300, 220, 700, 250), "By Neil R. Jones"),
                     ("Text", (100, 300, 900, 1200), "The ships came."), ("PageFooter", (480, 1350, 520, 1380), "9")])
    P[11] = _mk(11, [("Text", (100, 300, 900, 1200), "text"), ("PageFooter", (480, 1350, 520, 1380), "10")])
    P[12] = _mk(12, [("SectionHeader", (100, 100, 900, 200), "PARASITE"), ("Text", (300, 220, 700, 250), "By Harl Vincent"),
                     ("Text", (100, 300, 900, 1200), "It crawled."), ("PageFooter", (480, 1350, 520, 1380), "11")])
    R = analyse_issue(P)
    pr = R["folio"]["printed"]
    assert pr[4] == 4 and pr[5] == 5 and pr[6] == 6 and pr[7] == 7, pr
    assert pr[9] == 8 and pr[10] == 9 and pr[12] == 11 and pr[8] is None, pr
    assert R["toc_pages"] == [3], R["toc_pages"]
    by = {norm(e["title"]): e for e in R["entries"]}
    assert "baby is three" in by and by["baby is three"]["author"] == "Theodore Sturgeon", by.keys()
    assert by["baby is three"]["scan_page"] == 4 and by["baby is three"]["page"] == 4
    lamp = [e for k, e in by.items() if k.startswith("the black lamp")]
    assert lamp and lamp[0]["scan_page"] == 7 and "meek" in lamp[0]["author"].lower(), lamp
    sw = by.get("space war")
    assert sw and sw["scan_page"] == 10 and sw["author"].startswith("Neil"), sw          # '9I' read as 91 -> found by title
    pa = by.get("parasite")
    assert pa and pa["scan_page"] == 12, pa                                             # 99 out of range -> by title
    assert all("Vol" not in e["label"] for e in R["entries"])
    # helpers
    assert _split_tail_number("WEREWOLVES OF WAR D. W. HALL 153") == ("WEREWOLVES OF WAR D. W. HALL", 153)
    assert _split_tail_number("(Novel — 35,000 words)")[1] is None
    sp = _split_title_author("THE TENTACLES FROM BELOW ANTHONY GILMORE")
    assert ("THE TENTACLES FROM BELOW", "ANTHONY GILMORE") in [x[:2] for x in sp], sp
    sp = _split_title_author("BIT OF TAPESTRY . • . . . . ... . . . . Cleve Cortmill")
    assert sp[0][:2] == ("BIT OF TAPESTRY", "Cleve Cortmill"), sp
    sp = _split_title_author("Number Six and the Borgia. A Novelette, . . Edgar Wallace")
    assert any(t == "Number Six and the Borgia" and a == "Edgar Wallace" for t, a, k in sp), sp
    sp = _split_title_author("Locomotives of the Rutland Railroad .....130")
    assert sp[0][:2] == ("Locomotives of the Rutland Railroad", ""), sp
    g = deglue("Diamond Stacks (Complete Novelette) .....G. A. Lathrop6\nReturn of Jesse James .....Alexander Scott50")
    assert g.split("\n")[0].endswith("Lathrop 6") and g.split("\n")[1].endswith("Scott 50"), g
    g = deglue("The Acid in the Laboratory .....Gordon Philip England615    Heartless and Cruel Was the Physician, but He Knew How to SufferCandle-Light .....Louise Garwood621")
    assert "\nCandle-Light ....." in g and "England 615\n" in g, g
    g = deglue("EDITORIAL .....2NEWS OF THE MONTH .....158ATMOSPHERE REVISION .....71")
    assert [x.replace(" ", "") for x in g.split("\n")] == ["EDITORIAL.....2", "NEWSOFTHEMONTH.....158", "ATMOSPHEREREVISION.....71"], g
    assert int_token("l2") == 12 and int_token("1950") == 1950 and int_token("ATS") is None
    print("toc_folio selftest OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--issue")
    ap.add_argument("--layout", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "layout"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    R = analyse_issue(load_pages(os.path.join(a.layout, a.issue)))
    f = R["folio"]
    print(f"folio: {f['pages_with_number']} pages with a number read; {f['support']} agree with the map; "
          f"segments {[(g['from'], g['to'], g['offset'], g['support']) for g in f['segments']]}; misplaced {f['misplaced']}")
    print(f"contents on scan {R['toc_pages']}: {len(R['entries'])} entries")
    for e in R["entries"]:
        print(f"  p.{e.get('page')!s:>4} -> scan {e.get('scan_page')!s:>4}  {e['title'][:45]:45s} | {e['author'][:28]:28s} | "
              f"{e.get('section', '')[:18]:18s} | {e['placed_by']} ({e['confidence']})")


import argparse  # noqa: E402

if __name__ == "__main__":
    main()
