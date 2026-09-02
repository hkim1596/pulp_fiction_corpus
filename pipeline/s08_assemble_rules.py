#!/usr/bin/env python3
"""Stage 8 — assembly v2: the rules engine.

Assembles the article records of an issue from its layout pages (s02, route
A) with printed conventions instead of a model: the contents page, the
printed page numbers, the running heads, the display title with its by-line,
the chapter apparatus, the teaser, the fillers at the end of a story. Every
rule comes from what the annotators reported on the workbench (feedback of
22–31 August 2026; docs/assembly-notes.md):

  1. A story starts where a display-size title stands above a by-line on a
     page inside the printed page range (or where the contents page says a
     piece starts, confirmed by a by-line). Its title and author come from
     those regions, cross-checked with the contents page — never from the
     ornamental lettering the OCR garbles.
  2. Continuation stays with the open record until the next start: a section
     header with no by-line while a story is open is a chapter head, tagged,
     never a new record. The running head confirms membership.
  3. The regions above the title on a start page (illustration caption,
     artist's signature, display garble) are page furniture, as the verified
     records do it; the printed blurb below the by-line is the teaser —
     metadata, kept with the record but never story text.
  4. A display-size header inside a story page that is not a chapter head
     starts a filler (house advertisement, "in the next issue", a letter to
     the readers): its own record to the end of the page.
  5. The contents page is a record of type toc; pages before the first and
     after the last printed page number are advertisement pages, one record
     per display header; a page inside the range with no open story and no
     by-line is a feature.
  6. Every text region belongs to exactly one record or to furniture; a
     coverage check reports anything left over.

Two variants are written, both in the articles.json shape so the site can
show them: rules-only (everything from the rules) and rules-on-model (the
rules inside the printed page range, the model's records — s07 — for the
advertisement pages outside it). Output: data/assembly_v2/<variant>/<issue>/
articles.json plus analysis.json (what the rules saw on every page). The
live assembly under data/articles is never touched; s09 compares them.

    python3 pipeline/s08_assemble_rules.py --all
    python3 pipeline/s08_assemble_rules.py --issue ast_1930_01
    python3 pipeline/s08_assemble_rules.py --selftest
"""
import argparse
import difflib
import glob
import json
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERSION = "2.0"
DISPLAY_FRAC = 0.025          # a header at least this share of the page height is display size
TITLE_GAP = 520               # px: how far above a by-line the display title may stand
CHAPTER_RE = re.compile(r"^\s*(?:CHAPTER|Chapter|CHAP\.|PART|Part)\s+[IVXLC\d]+\b|^\s*[IVXL]{1,6}\.?\s*$|^\s*\d{1,2}\.?\s*$")
BYLINE_RE = re.compile(r"^\s*[Bb][Yy]\s+(.+)$", re.S)
NOT_BYLINE_RE = re.compile(r"^\s*(?:Illustrated|Painted|Drawn|Cover|Decorations?|Photo)", re.I)
FILLER_RE = re.compile(r"NEXT ISSUE|NEXT MONTH|NEXT WEEK|WRITE IN|A LETTER TO|ON SALE|NEWSSTAND|APPEARS ON|COMING UP|COMING NEXT|COMING SOON|"
                       r"WATCH FOR|DON'T MISS|IN THE (?:\w+ )?ISSUE|NOW ON|OUT NOW", re.I)
AD_WORDS = re.compile(r"\$\d|\bFREE\b|coupon|Dept\.|Send (?:no|for|me)|money back|guarantee|\bWrite (?:for|to)\b|postpaid|Address\b|catalog|Agents wanted|Learn at home|\bmail\b", re.I)
MENTION_RE = re.compile(r"Please mention|when answering advertisements", re.I)
CONT_FROM_RE = re.compile(r"continued from (?:page\s*)?(\d{1,4})", re.I)
CONT_ON_RE = re.compile(r"continued on (?:page\s*)?(\d{1,4})|continued on next page|\(continued\)|to be continued|to be concluded|concluded in", re.I)
VERSE_RE = re.compile(r"\bverse\b|\bpoem\b", re.I)
SERIAL_RE = re.compile(r"serial|part (?:one|two|three|four|five|six|i{1,3}|iv|v)\b|(?:two|three|four|five|six)-part|conclusion|to be continued|continued from|concluded in", re.I)
STOP_WORDS = {"the", "a", "an", "of", "with", "for", "and", "to", "in", "on", "at", "by", "your", "our", "this", "that"}
LETTERS_RE = re.compile(r"\beyrie\b|letters|round-?up|readers|chat with", re.I)
FEATURE_SECTIONS = re.compile(r"department|feature|introducing|miscellaneous|science department|editorial", re.I)
STORY_SECTIONS = re.compile(r"novel|story|stories|novelet|serial", re.I)


# ---------------------------------------------------------------- helpers

def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def words(s):
    return len((s or "").split())


def region_text(r):
    return (r.get("text") or "").strip()


def load_pages(iid):
    pages = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "layout", iid, "page_*.json"))):
        p = json.load(open(f, encoding="utf-8"))
        p["regions"] = sorted(p["regions"], key=lambda r: r.get("order", 0))
        pages[p["page"]] = p
    return pages


def key(pno, idx):
    return f"{pno}:{idx}"


# ---------------------------------------------------------------- page analysis

def folio_of(p):
    """Printed page number on the page, from header/footer regions or a bare
    number at the top or bottom edge."""
    H = p["height"]
    cands = []
    for i, r in enumerate(p["regions"]):
        t = region_text(r)
        m = re.fullmatch(r"\D{0,6}?(\d{1,4})\D{0,4}", t) if t else None
        if not m:
            continue
        y0, y1 = r["bbox"][1], r["bbox"][3]
        edge = y1 < 0.09 * H or y0 > 0.91 * H
        if r["label"] in ("PageHeader", "PageFooter") or (edge and words(t) <= 3):
            cands.append(int(m.group(1)))
    return cands[0] if cands else None


def running_heads(p):
    """Running heads: the magazine's name or the piece's title at the top or
    the bottom edge (Galaxy prints them at the foot)."""
    H = p["height"]
    out = []
    for i, r in enumerate(p["regions"]):
        t = region_text(r)
        if not t or re.fullmatch(r"\D{0,6}?\d{1,4}\D{0,4}", t) or MENTION_RE.search(t):
            continue
        y0, y1 = r["bbox"][1], r["bbox"][3]
        edge = y1 < 0.07 * H or y0 > 0.93 * H
        if (r["label"] in ("PageHeader", "PageFooter") and words(t) <= 10) or (edge and words(t) <= 8 and t.upper() == t):
            out.append((i, t))
    return out


def is_furniture(p, i, r):
    t = region_text(r)
    H = p["height"]
    if r["label"] in ("PageHeader", "PageFooter"):
        return True
    if not t:
        return True
    if r["label"] in ("Picture", "Figure"):
        return True
    y0, y1 = r["bbox"][1], r["bbox"][3]
    if (y1 < 0.07 * H or y0 > 0.93 * H):
        if MENTION_RE.search(t) or re.fullmatch(r"\D{0,6}?\d{1,4}\D{0,4}", t):
            return True
        if words(t) <= 3 and not re.search(r"[a-z]", t):            # signature marks: "WS—7C", "□ □ □"
            return True
        if words(t) <= 2 and all(len(w) <= 5 and w.endswith(".") for w in t.split()):      # "Ast. St."
            return True
    if re.fullmatch(r"[\W_]{1,12}", t):            # "□ □ □", rules, ornaments
        return True
    return False


def byline_author(t):
    m = BYLINE_RE.match(t.replace("\n", " "))
    if not m or NOT_BYLINE_RE.match(t):
        return None
    a = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
    a = re.sub(r"\s*\(.*?\)\s*$", "", a)            # "(age 15)"
    if not a or words(a) > 7 or len(a) > 60:
        return None
    if re.search(r"\d", a) and not re.search(r"[A-Za-z]{3}", a):
        return None
    ws = a.split()
    if sum(1 for w in ws if w.lower() in STOP_WORDS) > 1 or not any(w[:1].isupper() and len(w) >= 2 for w in ws):
        return None                                   # "By Relieving the Cause with" is not a person
    return a


def is_display(p, r):
    if r["label"] in ("Caption", "PageHeader", "PageFooter", "Picture", "Figure"):
        return False
    h = r["bbox"][3] - r["bbox"][1]
    return h >= DISPLAY_FRAC * p["height"] and words(region_text(r)) <= 14


def chapter_title_like(r, head):
    """The short line after a chapter number that is the chapter's title."""
    t = region_text(r)
    if not t or words(t) > 8 or byline_author(t):
        return False
    if r["label"] == "SectionHeader":
        return True
    if '"' in t or "\u201c" in t or t.endswith("."):
        return False
    hh = head["bbox"][3] - head["bbox"][1]
    return (r["bbox"][3] - r["bbox"][1]) <= 1.8 * hh


NARRATIVE = {"he", "she", "his", "her", "said", "asked", "was", "were", "had", "i", "you", "they", "him", "me", "my", "we"}


def prose_like(r):
    """A paragraph of narrative rather than advertisement copy."""
    t = region_text(r)
    if r["label"] != "Text" or words(t) < 25 or AD_WORDS.search(t):
        return False
    ws = re.findall(r"[a-z']+", t.lower())
    return sum(1 for w in ws if w in NARRATIVE) >= 2


def start_first(s):
    """Index of the first region a start owns on its page."""
    idx = list(s["title_idx"]) + list(s["subtitle_idx"]) + list(s.get("teaser_idx") or [])
    if s.get("byline_idx") is not None:
        idx.append(s["byline_idx"])
    if s.get("at") is not None:
        idx.append(s["at"])
    return min(idx) if idx else 0


def chapter_head(t):
    t1 = t.strip().split("\n")[0]
    return bool(CHAPTER_RE.match(t1)) and words(t) <= 8


def parse_toc(pages):
    """Entries (title, author, printed page, blurb, section) from the contents
    page, whatever its layout; empty when the issue has none we can read."""
    best = None
    for pno in sorted(pages):
        if pno > 14:
            break
        p = pages[pno]
        texts = [region_text(r) for r in p["regions"]]
        joined = "\n".join(texts)
        has_word = bool(re.search(r"\bCONTENTS\b", joined, re.I)) or any(r["label"] == "TableOfContents" for r in p["regions"])
        n_pageno = (len(re.findall(r"(?:\.\s*){3,}\s*[^\n]{0,60}?(\d{1,4})\s*(?=\n|$)", joined))
                    + len(re.findall(r"\n\s*(\d{1,4})\s*(?=\n|$)", joined)))
        score = (3 if has_word else 0) + min(n_pageno, 12)
        if score >= 4 and (best is None or score > best[0]):
            best = (score, pno)
    if not best:
        return [], None
    pno = best[1]
    p = pages[pno]
    entries = []
    section = ""
    # walk the regions; a section label is a short header without a page number
    seq = []
    for r in p["regions"]:
        t = region_text(r)
        if not t:
            continue
        seq.append((r["label"], t))
    text = "\n".join(t for _, t in seq)
    # normalise dot leaders and glue
    text = text.replace("\xa0", " ")
    text = re.sub(r"(?:\s*\.){3,}", " ….. ", text)
    text = re.sub(r"[ \t]+", " ", text)
    # candidate entries: TITLE [….. AUTHOR] [….. ] PAGE  — in several printed layouts
    pats = [
        # Title ….. Author ….. 12   /   Title ….. Author 12   /   Title..... Author 12
        re.compile(r"(?P<title>[^\n…]{3,80}?)\s*…\.\.\s*(?:(?P<author>[A-Z][^\n…\d]{2,50}?)\s*(?:…\.\.\s*)?)?(?P<page>\d{1,4})\b"),
        # TITLE \n AUTHOR \n 12      (Astounding 1930-01)
        re.compile(r"(?m)^(?P<title>[A-Z][A-Z0-9' ,\-!?.]{2,60})\n(?P<author>[A-Z][A-Za-z.' \-]{2,50})\n(?P<page>\d{1,4})\s*$"),
        # TITLE ….. \n by Author 12  (Galaxy)
        re.compile(r"(?P<title>[^\n…]{3,80}?)\s*…\.\.\s*\n?\s*by (?P<author>[^\n\d]{2,50}?)\s*(?P<page>\d{1,4})\b", re.I),
        # TITLE ….. 12 \n by Author  (Galaxy 1952-03)
        re.compile(r"(?P<title>[^\n…]{3,80}?)\s*…\.\.\s*(?P<page>\d{1,4})\s*\n\s*by (?P<author>[^\n]{2,50})", re.I),
        # TITLE \n blurb line(s) \n AUTHOR \n 12   (Astounding 1930-03)
        re.compile(r"(?m)^(?P<title>[A-Z][A-Z0-9' ,\-!?.]{2,60})\n(?:[^\n]{20,}\n){0,3}(?P<author>[A-Z][A-Z. '\-]{2,50})\n(?P<page>\d{1,4})\s*$"),
    ]
    found = {}
    for pat in pats:
        for m in pat.finditer(text):
            title = re.sub(r"\s+", " ", m.group("title")).strip(" .:-")
            title = re.sub(r"^\d{1,2}\s+(?=[A-Z])", "", title)      # OCR noise like "5 Empty Holsters"
            # the previous blurb run together with the title ("Verse ReprintThe Eyrie"): cut at the last
            # lower-to-upper join that is not a name particle (McHenry, MacDonald, DeVille)
            cuts = []
            for x in re.finditer(r"([A-Za-z']+)(?=[A-Z][a-z])", title):
                left = x.group(1)
                if len(left) >= 3 and left not in ("Mac", "Van", "Von", "Des", "Del", "Della") and left[-1].islower():
                    cuts.append(x.end())
            if cuts and words(title[cuts[-1]:]) >= 1:
                title = title[cuts[-1]:].strip()
            author = (m.group("author") or "").strip(" .:-") if "author" in m.groupdict() else ""
            author = re.sub(r"\s+", " ", author)
            try:
                page = int(m.group("page"))
            except ValueError:
                continue
            if words(title) > 12 or page > 1200:
                continue
            if re.fullmatch(r"(?:cover design|cover)\b.*", title, re.I):
                continue
            k = norm(title)
            e = {"title": title, "author": author or None, "page": page, "start": m.start(), "end": m.end()}
            if k not in found or (not found[k]["author"] and author):     # a later pattern may add the author
                if k in found:
                    e["start"], e["end"] = found[k]["start"], max(found[k]["end"], m.end())
                found[k] = e
    entries = list(found.values())
    entries.sort(key=lambda e: e["start"])
    for n, e in enumerate(entries):
        nxt = entries[n + 1]["start"] if n + 1 < len(entries) else len(text)
        tail = text[e["end"]:nxt]
        tail = re.sub(r"^\s*(?:by [^\n]*\n)?", "", tail, flags=re.I)
        blurb = " ".join(l.strip() for l in tail.split("\n") if l.strip() and not re.fullmatch(r"[A-Z][A-Z \-—&']{4,40}", l.strip()))
        e["blurb"] = re.sub(r"\s+", " ", blurb)[:400]
    # section labels (NOVEL, SHORT STORIES, DEPARTMENTS…) and blurbs: the header right before each entry
    labels = [(m.start(), m.group(0).strip()) for m in re.finditer(r"(?m)^[A-Z][A-Z \-—&']{4,40}(?:[—\-][A-Za-z]+)?$", text)]
    for e in entries:
        sec = ""
        for pos, lab in labels:
            if pos < e["start"] and not sim(lab, e["title"]) > 0.8 and (not e["author"] or sim(lab, e["author"]) < 0.8):
                sec = lab
        e["section"] = sec
        e.pop("start", None)
        e.pop("end", None)
    entries.sort(key=lambda e: e["page"])
    for e in entries:
        s = e["section"] or ""
        bl = e.get("blurb") or ""
        prose_blurb = bl and sum(1 for c in bl if c.isupper()) < 0.5 * max(1, sum(1 for c in bl if c.isalpha()))
        if e["author"] and re.fullmatch(r"(?:a |the )?(?:department|editor|editors|readers|staff)s?", e["author"].strip(), re.I):
            s = (s + " " + e["author"]).strip()
            e["author"] = None
        if VERSE_RE.search(s) or VERSE_RE.search(bl[:60]):
            e["type"] = "poem"
        elif LETTERS_RE.search(e["title"]) or LETTERS_RE.search(bl[:80]):
            e["type"] = "letters"
        elif SERIAL_RE.search(s) or (prose_blurb and SERIAL_RE.search(bl)) or SERIAL_RE.search(e["title"]):
            e["type"] = "serial_part"
        elif FEATURE_SECTIONS.search(s) or re.search(r"editorial|editor's page", e["title"], re.I):
            e["type"] = "feature"
        elif not e["author"] and not STORY_SECTIONS.search(s):
            e["type"] = "feature"                     # an unsigned short piece: a filler, a department
        else:
            e["type"] = "story"
    # a second contents page (Weird Tales prints two): the same parse on the next page
    return entries, pno


def analyse(pages):
    """Everything the rules need to know about every page."""
    A = {"pages": {}, "toc": [], "toc_page": None, "offset": None, "folio_range": None}
    A["toc"], A["toc_page"] = parse_toc(pages)
    # a second contents page right after the first (Weird Tales)
    if A["toc_page"] and (A["toc_page"] + 1) in pages:
        more, _ = parse_toc({A["toc_page"] + 1: pages[A["toc_page"] + 1]})
        known = {norm(e["title"]) for e in A["toc"]}
        A["toc"] += [e for e in more if norm(e["title"]) not in known]
        A["toc"].sort(key=lambda e: e["page"])
    offsets = Counter()
    for pno, p in pages.items():
        f = folio_of(p)
        if f:
            offsets[pno - f] += 1
    A["offset"] = offsets.most_common(1)[0][0] if offsets else None
    fol_pages = [pno for pno, p in pages.items() if folio_of(p) and A["offset"] is not None and pno - folio_of(p) == A["offset"]]
    if fol_pages:
        A["folio_range"] = [min(fol_pages), max(fol_pages)]
    # printed page -> scan page: the page that carries that number, else the nearest numbered page's offset
    # (a scan with an inserted leaf shifts the offset part-way through the issue)
    fol_index = {}
    for pno, p in sorted(pages.items()):
        f = folio_of(p)
        if f and abs((pno - f) - (A["offset"] or 0)) <= 6 and f not in fol_index:
            fol_index[f] = pno
    A["folio_index"] = fol_index

    def scan_of(printed):
        if printed in fol_index:
            return fol_index[printed]
        near = sorted(fol_index, key=lambda f: abs(f - printed))
        if near:
            f = near[0]
            return fol_index[f] + (printed - f)
        return printed + A["offset"] if A["offset"] is not None else None
    A["scan_of"] = scan_of
    toc_by_scan = {}
    if A["offset"] is not None:
        for e in A["toc"]:
            sp = scan_of(e["page"])
            if sp is not None:
                toc_by_scan.setdefault(sp, []).append(e)
    for pno, p in pages.items():
        H = p["height"]
        regs = p["regions"]
        info = {"folio": folio_of(p), "running": [t for _, t in running_heads(p)], "furniture": [], "bylines": [],
                "starts": [], "chapters": [], "fillers": [], "ad_score": 0, "toc_entries": toc_by_scan.get(pno, [])}
        body_chars = 0
        for i, r in enumerate(regs):
            t = region_text(r)
            if is_furniture(p, i, r):
                info["furniture"].append(i)
                continue
            body_chars += len(t) if len(t) > 200 else 0
            info["ad_score"] += len(AD_WORDS.findall(t))
            a = byline_author(t)
            if a:
                info["bylines"].append({"idx": i, "author": a})
            elif re.fullmatch(r"[Bb][Yy]", t) and i + 1 < len(regs):
                nt = region_text(regs[i + 1]).replace("\n", " ")
                if 0 < words(nt) <= 5 and nt[:1].isupper() and not chapter_head(nt):
                    info["bylines"].append({"idx": i, "author": nt.strip(" .,"), "name_idx": i + 1})
        info["body_chars"] = body_chars
        info["cont_from"] = []          # (idx, printed page) — the text after it continues an earlier piece
        info["cont_on"] = []            # idx — the piece pauses here
        for i, r in enumerate(regs):
            t = region_text(r)
            if words(t) > 12:
                continue
            m = CONT_FROM_RE.search(t)
            if m:
                info["cont_from"].append((i, int(m.group(1))))
                if i not in info["furniture"]:
                    info["furniture"].append(i)
            elif CONT_ON_RE.search(t):
                info["cont_on"].append(i)
                if i not in info["furniture"]:
                    info["furniture"].append(i)
        # starts: a by-line with its title — the section-header lines right above it
        for b in info["bylines"]:
            bi = b["idx"]
            by0 = regs[bi]["bbox"][1]
            lines, subs, teaser_between = [], [], []
            j = bi - 1
            while j >= 0:
                r = regs[j]
                if j in info["furniture"]:
                    j -= 1
                    continue
                t = region_text(r)
                if by0 - r["bbox"][3] > TITLE_GAP:
                    break
                if r["label"] == "SectionHeader" and words(t) <= 14 and not chapter_head(t):
                    lines.insert(0, j)
                elif r["label"] == "Caption" and not lines:
                    pass                                   # the illustration's caption sits between title and by-line
                elif not lines and len(subs) < 3 and words(t) <= 8 and not chapter_head(t):
                    subs.insert(0, j)                     # a subtitle line between the title and the by-line
                elif not lines and not teaser_between and 8 < words(t) <= 80 and r["label"] == "Text":
                    teaser_between.append(j)              # the blurb printed between the title and the by-line
                else:
                    break
                j -= 1
            if not lines:
                # no header above: a display line right below the by-line (some layouts), else the contents page
                for j in range(bi + 1, min(bi + 4, len(regs))):
                    if j == b.get("name_idx"):
                        continue
                    r = regs[j]
                    if j not in info["furniture"] and is_display(p, r) and r["label"] == "SectionHeader" and not chapter_head(region_text(r)):
                        lines = [j]
                        break
            toc_hit = None
            for e in info["toc_entries"]:
                if e["author"] and sim(e["author"], b["author"]) > 0.6:
                    toc_hit = e
            if not toc_hit and info["toc_entries"] and not lines:
                toc_hit = max(info["toc_entries"], key=lambda e: sim(e["author"] or "", b["author"]))
            if not lines and not toc_hit:
                continue
            # the title is the tallest line; other lines join it when the contents page agrees, else they are subtitles
            title_idx, sub_idx = [], list(subs)
            if lines:
                hts = {j: regs[j]["bbox"][3] - regs[j]["bbox"][1] for j in lines}
                main = max(lines, key=lambda j: hts[j])
                title_idx = [main]
                for j in lines:
                    if j == main:
                        continue
                    joined = " ".join(region_text(regs[x]).replace("\n", " ") for x in sorted(title_idx + [j]))
                    alone = " ".join(region_text(regs[x]).replace("\n", " ") for x in sorted(title_idx))
                    if toc_hit and sim(joined, toc_hit["title"]) > sim(alone, toc_hit["title"]):
                        title_idx.append(j)
                    elif not toc_hit and hts[j] >= 0.6 * hts[main]:
                        title_idx.append(j)
                    else:
                        sub_idx.append(j)
                title_idx.sort()
            title = re.sub(r"\s+", " ", " ".join(region_text(regs[j]).replace("\n", " ") for j in title_idx)).strip()
            info["starts"].append({"title_idx": title_idx, "subtitle_idx": sorted(sub_idx), "byline_idx": bi, "title": title,
                                   "author": b["author"], "toc": toc_hit, "teaser_idx": (teaser_between if lines else []),
                                   "name_idx": b.get("name_idx")})
        # chapter heads and fillers
        for i, r in enumerate(regs):
            if i in info["furniture"]:
                continue
            t = region_text(r)
            if chapter_head(t):
                info["chapters"].append(i)
                # the chapter's own title on the next short line
                if i + 1 < len(regs) and (i + 1) not in info["furniture"] and chapter_title_like(regs[i + 1], r):
                    info["chapters"].append(i + 1)
            elif (is_display(p, r) and r["label"] == "SectionHeader" and not any(i in s["title_idx"] for s in info["starts"])) or \
                    (words(t) <= 6 and FILLER_RE.search(t) and not any(i in s["title_idx"] + s["subtitle_idx"] for s in info["starts"])):
                info["fillers"].append(i)
        A["pages"][pno] = info
    # nothing starts on the contents page or before it (the cover strip and the index list pieces, they do not begin them)
    if A["toc_page"]:
        for pno, info in A["pages"].items():
            if pno <= A["toc_page"] and info["starts"]:
                for st in info["starts"]:
                    st["rejected"] = "on or before the contents page"
                info.setdefault("rejected_starts", []).extend(info["starts"])
                info["starts"] = []
    # the magazine's own running head: the most common one
    heads = Counter(h for info in A["pages"].values() for h in info["running"] if words(h) <= 8)
    A["magazine_head"] = heads.most_common(1)[0][0] if heads else ""
    vocab = Counter()
    for p in pages.values():
        for r in p["regions"]:
            for w in re.findall(r"[A-Za-z']{2,}", region_text(r)):
                vocab[w.lower().strip("'")] += 1
    A["vocab"] = vocab
    # announcements are not starts: a title and by-line with almost no text after them
    # ("in the next issue", book reviews, the cover strip)
    order = sorted(pages)
    for n, pno in enumerate(order):
        info = A["pages"][pno]
        if not info["starts"]:
            continue
        regs = pages[pno]["regions"]
        keep = []
        starts = sorted(info["starts"], key=lambda s: s["byline_idx"])
        for k, s in enumerate(starts):
            first = start_first(s)
            stop = start_first(starts[k + 1]) if k + 1 < len(starts) else len(regs)
            following = sum(len(region_text(regs[j])) for j in range(s["byline_idx"] + 1, stop) if j not in info["furniture"])
            if following < 800 and k + 1 == len(starts) and n + 1 < len(order):
                nxt = A["pages"][order[n + 1]]
                nregs = pages[order[n + 1]]["regions"]
                content = [j for j in range(len(nregs)) if j not in nxt["furniture"]]
                near_top = set(content[:3])
                starts_next = any(start_first(t) in near_top for t in nxt["starts"])
                if not starts_next and nxt["ad_score"] < 3:
                    following += sum(len(region_text(nregs[j])) for j in content)
            in_filler = any(j < first and j not in info["furniture"] and words(region_text(regs[j])) <= 12
                            and FILLER_RE.search(region_text(regs[j])) for j in range(len(regs)))
            unlisted = len(A["toc"]) >= 3 and not s.get("toc")
            chaptered = any(c > s["byline_idx"] for c in info["chapters"])          # "CHAPTER I" right after the by-line
            # inside a department (the running head names a letters or feature piece of the contents page)
            in_department = unlisted and any(sim(h, e["title"]) > 0.6 for h in info["running"]
                                             for e in A["toc"] if e.get("type") in ("letters", "feature"))
            # a listing: several by-lines on the page, none opening a chapter, all with little text
            listing = (len(starts) >= 2 and not info["chapters"] and unlisted
                       and all(sum(len(region_text(regs[j])) for j in range(x["byline_idx"] + 1, (start_first(starts[m + 1]) if m + 1 < len(starts) else len(regs)))
                                   if j not in info["furniture"]) < 800 for m, x in enumerate(starts)))
            if ((following < 250 or ((in_filler or unlisted) and following < 800) or (in_filler and following < 1500)
                 or (in_department and following < 1500) or listing) and not (chaptered and following >= 250)):
                s["rejected"] = (f"only {following} characters follow the by-line" + (" inside a filler block" if in_filler else "")
                                 + " (an announcement, not a start)")
                info.setdefault("rejected_starts", []).append(s)
            else:
                keep.append(s)
        info["starts"] = keep
    # pieces the contents page lists but no by-line announced (title and by-line drawn as
    # lettering the OCR could not read, or a department with no author): the start is
    # placed on the page the contents page gives, at the title if one is readable, else
    # at the first paragraph (the one that opens with a word in capitals)
    if A["offset"] is not None:
        claimed = set()
        for info in A["pages"].values():
            for s in info["starts"]:
                if s.get("toc"):
                    claimed.add(norm(s["toc"]["title"]))
        # entries whose printed page number the OCR garbled: link them to a by-line start by name
        for e in A["toc"]:
            if norm(e["title"]) in claimed:
                continue
            best = None
            for pno, info in A["pages"].items():
                for s in info["starts"]:
                    if s.get("toc"):
                        continue
                    score = max(sim(s["title"], e["title"]), sim(s["author"] or "", e["author"] or "") if e["author"] else 0)
                    if score > 0.6 and (best is None or score > best[0]):
                        best = (score, s, pno)
            if best:
                best[1]["toc"] = e
                best[1]["toc_linked_by_name"] = True
                e["scan_linked"] = best[2]
                claimed.add(norm(e["title"]))
        lo_hi = A["folio_range"] or [min(pages), max(pages)]
        for e in A["toc"]:
            if norm(e["title"]) in claimed:
                continue
            P = A["scan_of"](e["page"])
            if P is None or P not in pages or (A["toc_page"] and P <= A["toc_page"]) or P > lo_hi[1] + 4:
                continue                                          # a page number the OCR garbled
            cands = [P, P - 1, P + 1]
            placed = None
            for q in cands:
                if q not in pages:
                    continue
                info = A["pages"][q]
                regs = pages[q]["regions"]
                # a readable display title that matches
                for j, r in enumerate(regs):
                    if j in info["furniture"] or not is_display(pages[q], r):
                        continue
                    if sim(region_text(r), e["title"]) > 0.6:
                        placed = (q, [j], None)
                        break
                if placed:
                    break
                # a title drawn in pieces ("GALAXY'S", "STAR", "SHELF"): the display fragments near the top, joined
                frags = [j for j, r in enumerate(regs) if j not in info["furniture"] and is_display(pages[q], r) and words(region_text(r)) <= 3]
                frags = [j for j in frags if j <= (frags[0] + 5)] if frags else []
                if len(frags) >= 2 and sim(" ".join(region_text(regs[j]) for j in frags), e["title"]) > 0.6:
                    placed = (q, frags, None)
                    break
            if not placed and P in pages:
                info = A["pages"][P]
                regs = pages[P]["regions"]
                if any(sim(e["author"] or "", s["author"]) > 0.6 for s in info["starts"]):
                    continue                                  # a by-line start already covers it
                at = None
                for j, r in enumerate(regs):
                    if j in info["furniture"] or r["label"] == "Caption":
                        continue
                    t = region_text(r)
                    fw = t.split()[0] if t.split() else ""
                    if words(t) >= 8 and len(re.sub(r"[^A-Za-z]", "", fw)) >= 2 and fw.upper() == fw:
                        at = j                                # "MR. BESSEL was…": a first paragraph
                        break
                if at is None:
                    at = next((j for j, r in enumerate(regs) if j not in info["furniture"] and r["label"] != "Caption"), None)
                if at is not None:
                    placed = (P, [], at)
            if not placed:
                continue
            q, title_idx, at = placed
            info = A["pages"][q]
            st = {"title_idx": title_idx, "subtitle_idx": [], "byline_idx": None, "at": at, "title": e["title"],
                  "author": e["author"], "toc": e, "from_contents": True}
            info["starts"].append(st)
            claimed.add(norm(e["title"]))
    for info in A["pages"].values():
        info["starts"].sort(key=start_first)
    # the range of pages that carry the magazine's own matter: the printed page numbers, widened by the
    # first and last piece the contents page or a by-line places (Western Story numbers its first story
    # pages without a readable folio)
    lo, hi = A["folio_range"] or [min(pages), max(pages)]
    start_pages = [pno for pno, info in A["pages"].items() if info["starts"]]
    toc_pages = [A["scan_of"](e["page"]) for e in A["toc"] if A["offset"] is not None and A["scan_of"](e["page"]) in pages]
    lo = min([lo] + start_pages + toc_pages)
    hi = max([hi] + start_pages + toc_pages)
    A["content_range"] = [lo, hi]
    return A


# ---------------------------------------------------------------- assembly

def clean_text(text):
    try:
        from s07_articles import clean_text as ct
        return ct(text)
    except Exception:
        return text


class Assembler:
    def __init__(self, iid, pages, A):
        self.iid, self.pages, self.A = iid, pages, A
        self.records = []
        self.furniture = []
        self.owner = {}
        self.open = None            # the open record (a dict)
        self.last_text = ""         # the last text added to the open piece (continuation test)
        self.suspended = None       # the piece a full advertisement page interrupted
        self.n = 0

    def new(self, typ, title=None, author=None, page=None, toc=None):
        self.n += 1
        rec = {"article_id": f"{self.iid}_a{self.n:03d}", "type": typ, "title": title, "author": author,
               "pages": [], "fragments": [], "roles": {}, "flags": [], "toc": toc, "keys": []}
        self.records.append(rec)
        return rec

    def add(self, rec, pno, i, role=None):
        k = key(pno, i)
        if k in self.owner:
            return
        self.owner[k] = rec["article_id"]
        rec["keys"].append((pno, i))
        if role:
            rec["roles"][k] = role
        elif rec is self.open:
            self.last_text = region_text(self.pages[pno]["regions"][i])

    def furn(self, pno, i):
        k = key(pno, i)
        if k not in self.owner:
            self.owner[k] = "furniture"
            self.furniture.append({"page": pno, "idx": i})

    def run(self):
        A, pages = self.A, self.pages
        for info in A["pages"].values():          # the analysis is shared between variants
            for s in info["starts"]:
                s.pop("_rec", None)
        lo, hi = A.get("content_range") or A["folio_range"] or [min(pages), max(pages)]
        toc_page = A["toc_page"]
        for pno in sorted(pages):
            p = pages[pno]
            info = A["pages"][pno]
            regs = p["regions"]
            for i in info["furniture"]:
                self.furn(pno, i)
            if pno == toc_page or (toc_page and pno == toc_page + 1 and info["toc_entries"] == [] and
                                    sum(1 for r in regs if r["label"] == "TableOfContents")):
                rec = self.new("toc", title="Contents", page=pno)
                for i in range(len(regs)):
                    if i not in info["furniture"]:
                        self.add(rec, pno, i)
                self.open = None
                continue
            inside = lo <= pno <= hi
            if not inside or self.is_ad_page(pno, p, info):
                if self.open is not None and self.open.get("type") in ("story", "serial_part", "letters", "feature") and inside:
                    self.suspended = self.open            # a full advertisement page inside a piece
                self.open = None
                self.ad_page(pno, p, info)
                continue
            self.story_page(pno, p, info)
        # texts, pages
        for rec in self.records:
            mism = rec.pop("_head_mismatch", None)
            if mism:
                rec["flags"].append(f"running head does not name this piece on pages {mism[:8]}")
            rec["pages"] = sorted({pn for pn, _ in rec["keys"]})
            frags = {}
            for pn, i in rec["keys"]:
                frags.setdefault(pn, []).append(i)
            rec["fragments"] = [{"page": pn, "region_ids": frags[pn]} for pn in sorted(frags)]
            body = []
            for pn, i in rec["keys"]:
                role = rec["roles"].get(key(pn, i))
                if role in ("title", "subtitle", "author", "teaser", "caption"):
                    continue
                body.append(region_text(pages[pn]["regions"][i]))
            rec["text"] = clean_text("\n".join(body))
            rec["n_regions"] = len(rec["keys"])
            if rec["type"] == "story" and rec["keys"]:
                pn, i = rec["keys"][-1]
                tail = " ".join(region_text(pages[pn]["regions"][j]) for j in range(i, min(i + 3, len(pages[pn]["regions"]))))
                if re.search(r"to be continued|to be concluded|concluded in|continued next|next month", tail, re.I):
                    rec["type"] = "serial_part"
                    rec["flags"].append("ends with a 'to be continued' notice")
            del rec["keys"]
        self.records = [r for r in self.records if r["n_regions"]]
        return self

    def is_ad_page(self, pno, p, info):
        """An advertisement page inside the printed range (Weird Tales numbers
        its advertisement pages and gives them running heads): advertisement
        words, no start, and no sign that the open piece continues here."""
        if info["starts"] or info["ad_score"] < 3 or info["chapters"] or info["cont_from"]:
            return False
        regs = p["regions"]
        content = [i for i in range(len(regs)) if i not in info["furniture"]]
        if not content:
            return False
        if sum(1 for i in content if prose_like(regs[i])) >= 3:
            return False                                  # story columns beside advertisements: a mixed page
        first = region_text(regs[content[0]])
        if first[:1].islower():
            return False
        if self.open is not None and self.open.get("type") not in ("ad", "other"):
            title = self.open.get("title") or ""
            if any(sim(h, title) > 0.5 for h in info["running"]):
                return False
            if self.last_text and not re.search(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$", self.last_text):
                return False
        long = sum(1 for i in content if words(region_text(regs[i])) >= 40)
        return not (long >= 3 and info["ad_score"] < 6)

    def ad_page(self, pno, p, info):
        """A page outside the printed range: one advertisement per display
        header; the first regions join the first header's advertisement."""
        regs = p["regions"]
        rec = None
        pending = []
        for i, r in enumerate(regs):
            if i in info["furniture"]:
                continue
            if is_display(p, r) and r["label"] == "SectionHeader":
                if rec is not None and pending:
                    for j in pending:
                        self.add(rec, pno, j)
                    pending = []
                t = region_text(r).replace("\n", " ")
                rec = self.new("ad", title=t[:120], page=pno)
                self.add(rec, pno, i, "title")
            elif rec is None:
                pending.append(i)
            else:
                self.add(rec, pno, i)
        if pending:
            if rec is None:
                t = next((region_text(regs[j]) for j in pending if region_text(regs[j])), "")
                rec = self.new("ad" if info["ad_score"] else "other", title=t.split("\n")[0][:120], page=pno)
            for j in pending:
                self.add(rec, pno, j)

    def story_page(self, pno, p, info):
        regs = p["regions"]
        starts = {s["byline_idx"]: s for s in info["starts"] if s["byline_idx"] is not None}
        at_of = {s["at"]: s for s in info["starts"] if s.get("at") is not None}
        title_of = {}
        for s in info["starts"]:
            for j in s["title_idx"] + s["subtitle_idx"] + list(s.get("teaser_idx") or []):
                title_of[j] = s
        chapters = set(info["chapters"])
        fillers = set(info["fillers"])
        n = len(regs)
        filler = None
        cont_from = dict(info["cont_from"])
        if cont_from:
            # regions above the first "continued from" notice: the piece's repeated title joins the
            # piece; anything else belongs to nobody we know and gets its own record, flagged
            first_notice = min(cont_from)
            loose = [j for j in range(first_notice) if j not in info["furniture"] and key(pno, j) not in self.owner]
            if loose and not any(start_first(s) < first_notice for s in info["starts"]):
                target = self.continued_piece(cont_from[first_notice], pno)
                ttl = (target or {}).get("title") or ""
                if target is not None and len(loose) <= 2 and all(words(region_text(regs[j])) <= 12 for j in loose) and \
                        any(sim(region_text(regs[j]), ttl) > 0.5 for j in loose):
                    for j in loose:
                        self.add(target, pno, j, "heading")
                else:
                    ltxt = " ".join(region_text(regs[j]) for j in loose)
                    kind = "ad" if (len(AD_WORDS.findall(ltxt)) >= 1 or info["ad_score"] >= 3) else "other"
                    rec = self.new(kind, title=self.page_title(p, info, loose[0]) or region_text(regs[loose[0]])[:60], page=pno)
                    rec["flags"].append("text above a 'continued from' notice" + (" (reads like an advertisement)" if kind == "ad" else " with no known owner"))
                    self.open = rec
                    for j in loose:
                        self.add(rec, pno, j)
        if info["starts"]:
            # regions above the first title of the page: short ones are the illustration's caption, the
            # artist's signature or display garble (furniture, as the verified records have it); long
            # ones are the end of the open record
            first = min(start_first(s) for s in info["starts"])
            for j in range(0, first):
                if j in info["furniture"] or key(pno, j) in self.owner:
                    continue
                t = region_text(regs[j])
                if words(t) > 40 and self.open is not None and self.open.get("type") != "ad":
                    self.add(self.open, pno, j, "chapter" if j in chapters else None)
                elif words(t) > 40:
                    rec = self.new("feature", title=t.split("\n")[0][:80], page=pno)
                    rec["flags"].append("text above the first title of the page with no record open")
                    self.add(rec, pno, j)
                else:
                    self.furn(pno, j)
                    self.furniture[-1]["why"] = "above the title on a start page"
        i = 0
        while i < n:
            if i in cont_from:
                target = self.continued_piece(cont_from[i], pno)
                if target is not None:
                    self.open = target
                    filler = None
                    target["flags"].append(f"continues on p.{pno} (notice: continued from page {cont_from[i]})") if f"continues on p.{pno}" not in " ".join(target["flags"]) else None
                else:
                    rec = self.new("other", page=pno, title=f"continued from page {cont_from[i]}")
                    rec["flags"].append(f"'continued from page {cont_from[i]}' but no record is open there")
                    self.open = rec
                i += 1
                continue
            if i in info["furniture"]:
                i += 1
                continue
            r = regs[i]
            t = region_text(r)
            if i in title_of:
                s = title_of[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                role = "title" if i in s["title_idx"] else ("teaser" if i in (s.get("teaser_idx") or []) else "subtitle")
                self.add(s["_rec"], pno, i, role)
                if role == "teaser":
                    s["_rec"]["teaser"] = t.replace("\n", " ")
                self.open = s["_rec"]
                filler = None
                if s["byline_idx"] is None and i == max(s["title_idx"] + s["subtitle_idx"]):
                    self.teaser(pno, p, info, s, i + 1)
                i += 1
                continue
            if i in at_of:
                s = at_of[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                self.open = s["_rec"]
                filler = None
                # fall through: this region is the first paragraph of the piece
            if i in starts:
                s = starts[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                self.add(s["_rec"], pno, i, "author")
                if s.get("name_idx") is not None:
                    self.add(s["_rec"], pno, s["name_idx"], "author")
                self.open = s["_rec"]
                self.suspended = None
                filler = None
                self.teaser(pno, p, info, s, i + 1)
                i += 1
                continue
            if self.open is None and self.suspended is not None and \
                    sum(1 for j in range(i, n) if j not in info["furniture"] and prose_like(regs[j])) >= 2:
                self.open = self.suspended                # the piece resumes after a full advertisement page
                self.open["flags"].append(f"resumes on p.{pno} after advertisement pages")
                self.suspended = None
            if self.open is None:
                # no record open: a feature page (editorial, a letter to the readers) or an advertisement
                rec = self.new("ad" if info["ad_score"] >= 3 and not info["running"] else "feature", page=pno)
                rec["title"] = self.page_title(p, info, i)
                rec["flags"].append("no by-line")
                self.open = rec
            if filler is not None:
                prose = (words(t) >= 40 and not AD_WORDS.search(t) and r["label"] == "Text"
                         and (t[:1].islower() or words(t) >= 60))
                if prose and self.open is not None and self.open.get("type") not in ("ad", "other"):
                    filler = None                                  # the story's column resumes beside the advertisement
                else:
                    self.add(filler, pno, i)
                    i += 1
                    continue
            if i in fillers and self.open["type"] in ("story", "serial_part", "poem", "letters"):
                kind = None if t[:1] in "\"\u201c'\u2018" else self.filler_kind(pno, p, info, i)
                if kind is None and t[:1] in "\"\u201c'\u2018":
                    self.add(self.open, pno, i, "caption")           # a pull quote set in display type
                    i += 1
                    continue
                if kind:
                    filler = self.new(kind, title=t.replace("\n", " ")[:120], page=pno)
                    filler["flags"].append(f"filler after the story text on p.{pno}")
                    self.add(filler, pno, i, "title")
                    i += 1
                    continue
                self.add(self.open, pno, i, "heading")            # a display line inside the story (a headline, a part title)
                i += 1
                continue
            if i in chapters:
                self.add(self.open, pno, i, "chapter")
            elif r["label"] == "Caption":
                self.add(self.open, pno, i, "caption")
            else:
                self.add(self.open, pno, i)
            i += 1
        # the running head names the piece the page belongs to; a mismatch is worth a flag
        if self.open and self.open.get("type") in ("story", "serial_part") and info["running"]:
            title = self.open.get("title") or ""
            toc_title = (self.open.get("toc") or {}).get("title") or ""
            names = [h for h in info["running"] if words(h) <= 10 and sim(h, self.magazine_name()) < 0.6]
            if names and title and max(max(sim(h, title), sim(h, toc_title)) for h in names) < 0.35:
                self.open.setdefault("_head_mismatch", []).append(pno)

    def continued_piece(self, printed, pno):
        """The record that paused at printed page `printed` ('continued on page
        N' there, or simply the last piece owning regions on that page)."""
        if self.A["offset"] is None:
            return None
        scan = self.A["scan_of"](printed)
        cands = [r for r in self.records if r["type"] not in ("ad", "toc", "other") and any(pn == scan for pn, _ in r["keys"])]
        if not cands:
            cands = [r for r in self.records if r["type"] not in ("ad", "toc", "other") and any(pn in (scan - 1, scan + 1) for pn, _ in r["keys"])]
        if not cands:
            return None
        # the piece that paused there: the one whose last region on that page is last in reading order
        return max(cands, key=lambda r: max((i for pn, i in r["keys"] if pn == scan), default=-1))

    def filler_kind(self, pno, p, info, i):
        """A display header inside a story page: a filler (its own record) when
        it reads like one — the magazine's name, 'in the next issue', an
        advertisement — else None (a headline inside the story)."""
        regs = p["regions"]
        t = region_text(regs[i])
        rest = [j for j in range(i, len(regs)) if j not in info["furniture"]]
        near = " ".join(region_text(regs[j]) for j in rest[:4])
        # the block: from this header to where story prose resumes (a paragraph that starts mid-sentence,
        # or a long one without advertisement words)
        block_idx = []
        for j in rest:
            tj = region_text(regs[j])
            if j != i and words(tj) >= 40 and not AD_WORDS.search(tj) and regs[j]["label"] == "Text" and (tj[:1].islower() or words(tj) >= 60):
                break
            block_idx.append(j)
        block = " ".join(region_text(regs[j]) for j in block_idx)
        if sim(t, self.magazine_name()) > 0.6 or sim(t, self.A.get("magazine_head", "")) > 0.6:
            return "ad"
        if FILLER_RE.search(t) or FILLER_RE.search(near[:200]):
            return "ad" if len(AD_WORDS.findall(block)) >= 1 or re.search(r"NEXT ISSUE|NEXT MONTH|ON SALE|NEWSSTAND", block, re.I) else "feature"
        if len(AD_WORDS.findall(block)) >= 2:
            return "ad"
        return None

    def magazine_name(self):
        return self.A.get("magazine") or self.A.get("magazine_head") or ""

    def page_title(self, p, info, start_i):
        regs = p["regions"]
        parts = []
        for j in range(start_i, len(regs)):
            if j in info["furniture"]:
                continue
            if is_display(p, regs[j]):
                parts.append(region_text(regs[j]).replace("\n", " "))
                if len(parts) == 2:
                    break
            elif parts:
                break
        return " ".join(parts)[:120] if parts else None

    def begin(self, pno, p, info, s):
        """Open a new record at a title/by-line. Regions before the title on
        this page that nobody owns yet: short ones are furniture (the
        illustration's caption, the artist's signature, display garble), as
        the verified records treat them; long ones are the end of the open
        record."""
        regs = p["regions"]
        toc = s.get("toc")
        title = s["title"] or (toc["title"] if toc else None)
        as_printed = s["title"] or None
        if toc and s["title"] and norm(s["title"]) != norm(toc["title"]):
            # two readings of the same title (display lettering on the page, type on the contents page):
            # keep the one whose words the issue itself uses; the page's form when they tie
            vocab = self.A.get("vocab") or {}

            def known(t):
                ws = [w.lower() for w in re.findall(r"[A-Za-z']{2,}", t)]
                return (sum(1 for w in ws if vocab.get(w, 0) >= 2) / len(ws)) if ws else 0
            pt, tt = norm(s["title"]).split(), norm(toc["title"]).split()
            noise = set(pt) - set(tt)
            if sim(s["title"], toc["title"]) < 0.55 or known(toc["title"]) > known(s["title"]) or \
                    (tt and set(tt) <= set(pt) and all(len(w) <= 2 for w in noise)):
                title = toc["title"]              # the page's lettering carries an ornament read as a letter or digit
        sub = " ".join(region_text(regs[j]) for j in s["subtitle_idx"])
        typ = (toc or {}).get("type") or "story"
        if VERSE_RE.search(sub):
            typ = "poem"
        if SERIAL_RE.search(sub) or (title and SERIAL_RE.search(title)):
            typ = "serial_part"
        rec = self.new(typ, title=title, author=s["author"], page=pno,
                       toc=({k: toc.get(k) for k in ("title", "author", "page", "type", "blurb")} if toc else None))
        if sub:
            rec["subtitle"] = re.sub(r"\s+", " ", sub)
        if as_printed and as_printed != title:
            rec["title_as_printed"] = as_printed
        if toc and s["title"] and sim(toc["title"], s["title"]) < 0.55:
            rec["flags"].append(f"title differs from the contents page ('{toc['title']}')")
        if not s["title"] and toc:
            rec["flags"].append("title taken from the contents page (no readable title on the page)")
        if not toc and self.A["toc"]:
            rec["flags"].append("not on the contents page")
        if s.get("from_contents"):
            rec["flags"].append("start placed from the contents page (no by-line read on the page)")
        s["_rec"] = rec
        self.open = rec

    def teaser(self, pno, p, info, s, from_i):
        """The printed blurb between the by-line and the body: short, centred
        or full width, in sentence case (the body's first word is set in
        capitals). Tagged, never story text; confirmed against the contents
        page's blurb when there is one."""
        regs = p["regions"]
        W = p["width"]
        blurb = ((s.get("toc") or {}).get("blurb") or "")
        for j in s.get("teaser_idx") or []:
            if key(pno, j) not in self.owner:
                self.add(s["_rec"], pno, j, "teaser")
                s["_rec"]["teaser"] = region_text(regs[j]).replace("\n", " ")
                s["_rec"]["teaser_matches_contents"] = round(sim(region_text(regs[j]), blurb), 2) if blurb else None
                return
        best = None
        for j in range(from_i, len(regs)):
            if j in info["furniture"] or j in info["chapters"] or key(pno, j) in self.owner:
                continue
            r = regs[j]
            t = region_text(r)
            x0, y0, x1, y1 = r["bbox"]
            w = words(t)
            if w > 80 or w < 6 or r["label"] == "SectionHeader":
                continue
            fw = t.split()[0]
            if len(re.sub(r"[^A-Za-z]", "", fw)) >= 3 and fw.upper() == fw:
                continue                                     # "WHAT caused you…": the body's first paragraph
            centred = abs((x0 + x1) / 2 - W / 2) < 0.12 * W and (x1 - x0) < 0.62 * W
            wide = (x1 - x0) > 0.7 * W
            if not (centred or wide):
                continue
            score = sim(t, blurb) if blurb else 0
            if best is None or score > best[0]:
                best = (score, j)
            if not blurb:
                break
        if best is not None:
            j = best[1]
            self.add(s["_rec"], pno, j, "teaser")
            s["_rec"]["teaser"] = region_text(regs[j]).replace("\n", " ")
            s["_rec"]["teaser_matches_contents"] = round(best[0], 2) if blurb else None


def assemble(iid, pages, A, variant="rules", model=None):
    asm = Assembler(iid, pages, A).run()
    records = asm.records
    if variant == "rules_on_model" and model:
        lo, hi = A.get("content_range") or A["folio_range"] or [min(pages), max(pages)]
        keep = [r for r in records if any(lo <= pn <= hi for pn in r["pages"]) or r["type"] == "toc"]
        owned = {key(f["page"], i) for r in keep for f in r["fragments"] for i in f["region_ids"]}
        n = len(keep)
        for a in model.get("articles", []):
            if all((pn < lo or pn > hi) for pn in a.get("pages", [])) and a.get("pages"):
                ks = [key(f["page"], i) for f in a.get("fragments", []) for i in f["region_ids"]]
                if any(k in owned for k in ks):
                    continue
                n += 1
                b = dict(a)
                b["article_id"] = f"{iid}_a{n:03d}"
                b["roles"] = {}
                b["flags"] = ["from the model (page outside the printed range)"]
                b["n_regions"] = len(ks)
                keep.append(b)
                owned.update(ks)
        keep.sort(key=lambda r: (r["pages"][0] if r["pages"] else 0, r["article_id"]))
        for k, r in enumerate(keep, 1):
            r["article_id"] = f"{iid}_a{k:03d}"
        records = keep
    # coverage check
    owned = Counter()
    for r in records:
        for f in r["fragments"]:
            for i in f["region_ids"]:
                owned[key(f["page"], i)] += 1
    furn = {key(f["page"], f["idx"]) for f in asm.furniture}
    unassigned = []
    for pno, p in pages.items():
        for i, r in enumerate(p["regions"]):
            k = key(pno, i)
            if k not in owned and k not in furn and region_text(r):
                unassigned.append(k)
    double = [k for k, c in owned.items() if c > 1]
    return {"issue": iid, "backend": f"rules_v{VERSION}" if variant == "rules" else f"rules_v{VERSION}+model",
            "variant": variant, "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "articles": records,
            "furniture": asm.furniture, "unsorted": [{"key": k} for k in unassigned],
            "checks": {"unassigned_regions": len(unassigned), "double_owned_regions": len(double),
                       "records": len(records), "story_records": sum(1 for r in records if r["type"] in ("story", "serial_part")),
                       "records_starting_with_chapter_head": sum(1 for r in records if r["type"] in ("story", "serial_part") and r["fragments"] and
                                                                 r["roles"].get(key(r["fragments"][0]["page"], r["fragments"][0]["region_ids"][0])) == "chapter"),
                       "flags": sum(len(r["flags"]) for r in records)}}


def run_issue(iid, cfg_issue=None, log=print):
    pages = load_pages(iid)
    if not pages:
        log(f"[s08] {iid}: no layout pages")
        return None
    A = analyse(pages)
    A["magazine"] = (cfg_issue or {}).get("magazine", "")
    model_path = os.path.join(ROOT, "data", "articles", iid, "articles.json")
    model = json.load(open(model_path, encoding="utf-8")) if os.path.exists(model_path) else None
    outs = {}
    for variant in ("rules", "rules_on_model"):
        if variant == "rules_on_model" and not model:
            continue
        out = assemble(iid, pages, A, variant, model)
        d = os.path.join(ROOT, "data", "assembly_v2", variant, iid)
        os.makedirs(d, exist_ok=True)
        json.dump(out, open(os.path.join(d, "articles.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        outs[variant] = out
    for e in A["toc"]:
        e["scan"] = e.get("scan_linked") or (A["scan_of"](e["page"]) if A.get("scan_of") and A["offset"] is not None else None)
        if e["scan"] not in pages:
            e["scan"] = None                      # a page number the OCR garbled and no start to link it to
    an = {"issue": iid, "offset": A["offset"], "folio_range": A["folio_range"], "content_range": A.get("content_range"),
          "folio_index": {str(k): v for k, v in A.get("folio_index", {}).items()},
          "magazine_head": A.get("magazine_head"), "toc_page": A["toc_page"], "toc": A["toc"],
          "pages": {str(k): {kk: vv for kk, vv in v.items() if kk != "toc_entries"} for k, v in A["pages"].items()}}
    for v in an["pages"].values():
        v["starts"] = [{k: s[k] for k in ("title", "author", "title_idx", "byline_idx")} for s in v["starts"]]
    json.dump(an, open(os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "analysis.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    o = outs["rules"]
    log(f"[s08] {iid}: offset {A['offset']}, printed range {A['folio_range']}, contents entries {len(A['toc'])}, "
        f"{o['checks']['story_records']} story records of {o['checks']['records']}, unassigned {o['checks']['unassigned_regions']}, "
        f"chapter-head starts {o['checks']['records_starting_with_chapter_head']}")
    return outs


def selftest():
    assert chapter_head("CHAPTER II") and chapter_head("Chapter 3") and chapter_head("IV") and not chapter_head("The Beetle Horde")
    assert byline_author("By Victor Rousseau") == "Victor Rousseau"
    assert byline_author("by\nKatherine Yates") == "Katherine Yates"
    assert byline_author("Illustrated by Wesso") is None
    assert byline_author("By Relieving the Cause with this and that and more words") is None
    toc = "COVER DESIGN\nH. W. WESSOLOWSKI\n\nTHE BEETLE HORDE\nVICTOR ROUSSEAU\n8\n\nblurb\n\nTHE CAVE OF HORROR\nCAPTAIN S. P. MEEK\n32\n"
    fake = {4: {"page": 4, "width": 1400, "height": 2200, "regions": [{"label": "SectionHeader", "bbox": [0, 0, 100, 40], "order": 0, "text": "CONTENTS"},
                                                                     {"label": "TableOfContents", "bbox": [0, 50, 100, 900], "order": 1, "text": toc}]}}
    entries, pno = parse_toc(fake)
    assert pno == 4 and [e["title"] for e in entries] == ["THE BEETLE HORDE", "THE CAVE OF HORROR"], entries
    assert entries[0]["author"] == "VICTOR ROUSSEAU" and entries[1]["page"] == 32
    gal = {3: {"page": 3, "width": 1400, "height": 2200, "regions": [{"label": "SectionHeader", "bbox": [0, 0, 100, 40], "order": 0, "text": "CONTENTS"},
                                                                    {"label": "Text", "bbox": [0, 50, 100, 90], "order": 1, "text": "THE YEAR OF THE JACKPOT ..... 4\nby Robert A. Heinlein"},
                                                                    {"label": "Text", "bbox": [0, 100, 100, 140], "order": 2, "text": "MANNERS OF THE AGE ..... 38\nby H. B. Fyfe"},
                                                                    {"label": "Text", "bbox": [0, 150, 100, 190], "order": 3, "text": "THE DEMOLISHED MAN ..... 101\nby Alfred Bester"}]}}
    entries, _ = parse_toc(gal)
    assert [(e["title"], e["page"]) for e in entries] == [("THE YEAR OF THE JACKPOT", 4), ("MANNERS OF THE AGE", 38), ("THE DEMOLISHED MAN", 101)], entries
    assert entries[0]["author"] == "Robert A. Heinlein"
    td = {4: {"page": 4, "width": 1400, "height": 2200, "regions": [{"label": "SectionHeader", "bbox": [0, 0, 100, 40], "order": 0, "text": "CONTENTS"},
                                                                   {"label": "ListGroup", "bbox": [0, 50, 100, 90], "order": 1, "text": "Dead Men Talk..... Perley Poore Sheehan 6\n\nA Pulse-Stirring Story"},
                                                                   {"label": "ListGroup", "bbox": [0, 100, 100, 140], "order": 2, "text": "5 Empty Holsters . . . . . Don Alviso . . . . . 12\n\n blurb"}]}}
    entries, _ = parse_toc(td)
    assert [(e["title"], e["author"], e["page"]) for e in entries] == [("Dead Men Talk", "Perley Poore Sheehan", 6), ("Empty Holsters", "Don Alviso", 12)], entries
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    issues = {i["id"]: i for i in cfg["issues"]}
    ids = [args.issue] if args.issue else (list(issues) if args.all else [])
    if not ids:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, issues.get(iid))


if __name__ == "__main__":
    main()
