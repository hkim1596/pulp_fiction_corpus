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

VERSION = "2.1.1"
# 2.1.1 (2026-09-03, evening): from the audit against the human corrections (s10): the end of
# a piece's column above the next piece's title stays with the piece (evidence: a paragraph that
# starts in lower case or a long one; the rest of the Text follows); blurbs and synopsis headings
# above the title go to the new piece (teaser, heading); a repeated title over a "continued from"
# notice is furniture; a plain subheading inside a department with selling words under it is an
# advertisement; a filler exits only at narrative prose; a headline inside advertising matter
# starts the next advertisement (stacked headlines joined); an announcement header runs on to a
# later notice on the page; a by-line followed by selling words on its page is a book or magazine
# announcement, not a story.
# 2.1 (2026-09-03): the contents page has authority over title and author (the page's
# forms are kept as title_as_printed / author_as_printed); chapters listed with number
# and title split; advertisements classified (ad_class, advertiser) with the works a
# house announcement names (announces) and the verbatim excerpt it may carry
# (contains_excerpt, excerpt_of); ballots and coupons are their own record (house_form).
DISPLAY_FRAC = 0.025          # a header at least this share of the page height is display size
TITLE_GAP = 520               # px: how far above a by-line the display title may stand
CHAPTER_RE = re.compile(r"^\s*(?:CHAPTER|Chapter|CHAP\.|PART|Part)\s+[IVXLC\d]+\b|^\s*[IVXL]{1,6}\.?\s*$|^\s*\d{1,2}\.?\s*$|"
                        r"^\s*(?:[IVXL]{1,6}|\d{1,2})\.\s+[A-Z][^\n]{2,60}$")     # 'IV. The Coming of the Beast' on one line
BYLINE_RE = re.compile(r"^\s*[Bb][Yy]\s+(.+)$", re.S)
NOT_BYLINE_RE = re.compile(r"^\s*(?:Illustrated|Painted|Drawn|Cover|Decorations?|Photo)", re.I)
FILLER_RE = re.compile(r"NEXT ISSUE|NEXT MONTH|NEXT WEEK|WRITE IN|A LETTER TO|ON SALE|NEWSSTAND|APPEARS ON|COMING UP|COMING NEXT|COMING SOON|"
                       r"WATCH FOR|DON'T MISS|IN THE (?:\w+ )?ISSUE|NOW ON SALE|OUT NOW", re.I)
AD_WORDS = re.compile(r"\$\d|\bFREE\b|coupon|Dept\.|Send (?:no|for|me)|money back|guarantee|\bWrite (?:for|to)\b|postpaid|Address\b|catalog|Agents wanted|Learn at home|\bmail\b", re.I)
MENTION_RE = re.compile(r"Please mention|when answering advertisements", re.I)
CONT_FROM_RE = re.compile(r"continued from (?:page\s*)?(\d{1,4})", re.I)
CONT_ON_RE = re.compile(r"continued on (?:page\s*)?(\d{1,4})|continued on next page|\(continued\)|to be continued|to be concluded|concluded in|"
                        r"\[?\s*turn (?:the )?page\s*\]?|turn to page \d+", re.I)
VERSE_RE = re.compile(r"\bverse\b|\bpoem\b", re.I)
SERIAL_RE = re.compile(r"serial|part (?:one|two|three|four|five|six|i{1,3}|iv|v)\b|(?:two|three|four|five|six)-part|conclusion|to be continued|continued from|concluded in", re.I)
STOP_WORDS = {"the", "a", "an", "of", "with", "for", "and", "to", "in", "on", "at", "by", "your", "our", "this", "that"}
LETTERS_RE = re.compile(r"\beyrie\b|letters|round-?up|readers|chat with", re.I)
FEATURE_SECTIONS = re.compile(r"department|feature|introducing|miscellaneous|science department|editorial", re.I)
STORY_SECTIONS = re.compile(r"novel|story|stories|novelet|serial", re.I)
# advertisement classes (feedback of 2026-09-02, Sujin Kang; decision of 2026-09-03):
# house_next_issue, house_self, house_sibling, house_form, trade, classified
HOUSE_NEXT_RE = re.compile(r"next (?:issue|month|week)|coming (?:next|soon|in)|in the (?:\w+ )?issue|on sale|newsstand|"
                           r"appears? in|watch for|don't miss|will appear|forthcoming|out (?:next|on)\b|in our next", re.I)
HOUSE_SELF_RE = re.compile(r"subscri|back (?:number|issue|cop)|anniversary number|numbers? combined|binder|renew|a year|per year|"
                           r"\b(?:12|twelve|six) issues|enclosed? find|use (?:the |this )?coupon|(?:copy|copies) of|"
                           r"every (?:month|week)|write (?:us|to us|in|the editor)|the editor|your favou?rite|ballot|vote|"
                           r"tell us|let us know|readers?['’]? (?:opinion|vote|choice)", re.I)
FORM_RE = re.compile(r"ballot|my favou?rite stor|coupon|fill (?:in|out)|tear (?:out|off)|clip (?:this|and|the)|"
                     r"check (?:the|one|here)|mark (?:the|your)|please send me|enclosed? (?:find|is)|i enclose|"
                     r"(?:reader'?s? )?name and address|^\s*name\b|^\s*address\b|^\s*city\b|^\s*street\b", re.I | re.M)
SIBLING_RE = re.compile(r"\b((?:[A-Z][A-Za-z'\-]+ ){1,3}(?:Magazine|Stories|Quarterly|Detective|Western|Romances|Adventures|"
                        r"Novels|Mysteries|Tales|Weekly|Monthly))\b")
SIBLING_STOP = {"write", "have", "big", "famous", "easy", "your", "concerning", "spiritual", "these", "how", "to", "short",
                "the", "a", "all", "new", "true", "real", "great", "best", "complete", "other", "many", "more", "love", "our"}
SIBLING_SALE_RE = re.compile(r"\bcents?\b|a copy|on sale|newsstand|every (?:month|week)|ask your (?:news|dealer)|now on|"
                             r"the (?:new|big|great|popular) (?:magazine|monthly|weekly)|in the current issue|companion magazine|"
                             r"publishers? of|by the same publishers|sister magazine", re.I)
COMPANY_RE = re.compile(r"\b((?:[A-Z][A-Za-z&'\.\-]*,? ){1,4}(?:Co\.|Company|Inc\.|Corp\.|Corporation|Institute|Laboratories|"
                        r"Laboratory|Bureau|School(?:s)?|Studios?|Products|Bros\.|Mfg\.? Co\.|Manufacturing Co\.|Publications|"
                        r"Publishing Co\.|Press|Supply Co\.|Foundation|Society|Association|System|Service|Exchange|Sales Co\.|"
                        r"Distributors|Importers|Novelty Co\.))(?=[ ,.\n]|$)")
COMPANY_STOP = re.compile(r"^(?:by |the |free |use |mail |send |write |this |your |our |at |to |for |of |in |and |a )|"
                          r"\b(?:civil|secret|public|employment|mail|high|night|day|home study|correspondence) (?:service|school)s?$|"
                          r"^(?:free |write |mail |send |use )?(?:coupon|today|now)", re.I)
ADDRESS_RE = re.compile(r"\b(?:Dept\.?|Desk|Box|Bldg\.?|Building|Ave\.?|Avenue|St\.?|Street|Broadway|Blvd\.?|Station)\b\s*[A-Z0-9\-]*|"
                        r"\b[A-Z][a-z]+, (?:[A-Z][a-z]+\.?|[A-Z]{2})\s*$", re.M)
SALE_RE = re.compile(r"\b\d{2}c\b|\bcents\b|a copy|postage|handling charge|Publishing Corp|Publishing Co|Corp\.|\bInc\.|send \$|"
                     r"only \$|price is|order (?:now|today|from)|at your (?:news|book)", re.I)
CATEGORY_HEAD_RE = re.compile(r"(?m)^\s*[A-Z][A-Z&' \-]{3,30}\s*$")
CHAPTER_SPLIT_RE = re.compile(r"^\s*(?:(?:CHAPTER|Chapter|CHAP\.|PART|Part)\s+([IVXLC\d]+)|([IVXL]{1,6})|(\d{1,2}))\.?\s*[:\-—–]?\s*(.*)$", re.S)
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
EXCERPT_MIN_WORDS = 120       # a house announcement carrying this much narrative prose quotes the work


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
        if words(t) <= 3 and not re.search(r"[a-z]", t) and not FILLER_RE.search(t):     # signature marks: "WS—7C", "□ □ □"
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


# words of narrative prose; "you", "I", "my", "we" are left out because advertisement copy is written in them
NARRATIVE = {"he", "she", "his", "her", "said", "asked", "was", "were", "had", "they", "him", "hers", "himself", "herself"}


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


def roman_int(s):
    s = (s or "").upper()
    if not s or any(c not in ROMAN for c in s):
        return int(s) if s.isdigit() else None
    total = 0
    for i, c in enumerate(s):
        v = ROMAN[c]
        if i + 1 < len(s) and ROMAN[s[i + 1]] > v:
            total -= v
        else:
            total += v
    return total


def split_chapter(text):
    """'CHAPTER II\\nThe Thing in the Vault' -> ('II', 2, 'The Thing in the Vault');
    'IV. The Coming of the Beast' -> ('IV', 4, 'The Coming of the Beast'); a bare
    title -> (None, None, title). The number is kept as printed; n is its value."""
    t = (text or "").strip()
    m = CHAPTER_SPLIT_RE.match(t)
    if not m or not chapter_head(t.split("\n")[0]):
        return None, None, re.sub(r"\s+", " ", t)
    num = m.group(1) or m.group(2) or m.group(3)
    rest = re.sub(r"\s+", " ", (m.group(4) or "")).strip(" .:-—–")
    return num, roman_int(num), (rest or None)


def form_block(p, i, info):
    """A ballot or coupon starting at region i: a form line ('My favorite
    stories in the May WEIRD TALES are:', 'Please send me…', 'Name…') followed
    by Table/Form regions or more form lines. Returns the block's indices, or
    None."""
    regs = p["regions"]
    t = region_text(regs[i])
    if words(t) > 40 or not FORM_RE.search(t):
        return None
    block = [i]
    j = i + 1
    while j < len(regs):
        if j in info["furniture"]:
            j += 1
            continue
        r = regs[j]
        tj = region_text(r)
        if r["label"] in ("Table", "Form") or (words(tj) <= 40 and (FORM_RE.search(tj) or re.fullmatch(r"[\W_\d]+", tj)
                                                                   or re.search(r"[-_.…]{3,}", tj)
                                                                   or (words(tj) <= 20 and tj.rstrip().endswith(":")))):
            block.append(j)
        elif words(tj) <= 60 and re.search(r"fill (?:in|out)|help us|it will help|mail (?:this|it|to)|send (?:this|it|to)|address(?:ed)? to", tj, re.I):
            block.append(j)
        else:
            break
        j += 1
    if len(block) < 2 or not any(regs[j]["label"] in ("Table", "Form") or re.search(r"[-_.…]{3,}", region_text(regs[j])) for j in block):
        return None
    return block


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
                if words(t) <= 12 and FILLER_RE.search(t):
                    break                                  # "NEXT MONTH" above a title: the announcement's own header, not the title
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
            page_title_guess = " ".join(region_text(regs[j]).replace("\n", " ") for j in lines)
            hits = [e for e in info["toc_entries"] if e["author"] and sim(e["author"], b["author"]) > 0.6]
            if hits:
                # two entries by one author on one page (a story and a poem): the one whose title the page prints
                toc_hit = max(hits, key=lambda e: (sim(page_title_guess, e["title"]) if page_title_guess else 0, sim(e["author"], b["author"])))
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
        # ballots and coupons: a form block is its own record (house_form), never text of the piece around it
        info["forms"] = {}
        taken = set()
        for i, r in enumerate(regs):
            if i in info["furniture"] or i in taken or any(i in s["title_idx"] + s["subtitle_idx"] for s in info["starts"]):
                continue
            blk = form_block(p, i, info)
            if blk:
                info["forms"][i] = blk
                taken.update(blk)
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
            # inside a house announcement the text that belongs to the by-line ends at the next notice
            # ("in the June issue of"), display header or coupon; what follows is the department's own text
            stop_f = next((j for j in range(s["byline_idx"] + 1, stop) if j not in info["furniture"] and
                           ((words(region_text(regs[j])) <= 12 and FILLER_RE.search(region_text(regs[j])))
                            or (is_display(pages[pno], regs[j]) and regs[j]["label"] == "SectionHeader")
                            or j in (info.get("forms") or {}))), stop)
            following_f = sum(len(region_text(regs[j])) for j in range(s["byline_idx"] + 1, stop_f) if j not in info["furniture"])
            if following < 800 and k + 1 == len(starts) and n + 1 < len(order):
                nxt = A["pages"][order[n + 1]]
                nregs = pages[order[n + 1]]["regions"]
                content = [j for j in range(len(nregs)) if j not in nxt["furniture"]]
                near_top = set(content[:3])
                starts_next = any(start_first(t) in near_top for t in nxt["starts"])
                if not starts_next and nxt["ad_score"] < 3:
                    following += sum(len(region_text(nregs[j])) for j in content)
            in_filler = any(j < first and words(region_text(regs[j])) <= 12
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
            # a book or magazine announcement with a by-line (Galaxy's "The Current GALAXY Science Fiction
            # Novel … By Olaf Stapledon … 35c a copy"): selling words follow on the same page
            page_after = " ".join(region_text(regs[j]) for j in range(s["byline_idx"] + 1, len(regs)) if j not in info["furniture"])
            selling = unlisted and following < 2500 and bool(SALE_RE.search(page_after))
            if ((following < 250 or ((in_filler or unlisted) and following < 800) or (in_filler and following_f < 1500)
                 or (in_department and following < 1500) or listing or selling) and not (chaptered and following >= 250)):
                s["rejected"] = (f"only {following} characters follow the by-line" + (" inside a filler block" if in_filler else "")
                                 + (" with selling words (a book or magazine announcement)" if selling else "")
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
            chs = self.chapters_of(rec)
            if chs:
                rec["chapters"] = chs
            if rec["type"] == "ad":
                self.classify_ad(rec)
            del rec["keys"]
        self.records = [r for r in self.records if r["n_regions"]]
        return self

    def chapters_of(self, rec):
        """The chapter heads of a record, in order, number and title apart:
        'CHAPTER II' + 'The Thing in the Vault' on two lines or one."""
        out = []
        prev = None
        for pn, i in rec["keys"]:
            k = key(pn, i)
            if rec["roles"].get(k) != "chapter":
                prev = None
                continue
            t = region_text(self.pages[pn]["regions"][i])
            num, n, title = split_chapter(t)
            if num is None and prev is not None and not prev.get("title") and prev["page"] == pn:
                prev["title"] = title                      # the chapter's title on the line after its number
                prev["region_ids"].append(i)
                prev = None
                continue
            ch = {"number": num, "n": n, "title": title, "page": pn, "region_ids": [i]}
            out.append(ch)
            prev = ch if num is not None else None
        return out

    def classify_ad(self, rec):
        """ad_class and advertiser for an advertisement record, plus the works a
        house announcement names (announces) and whether it quotes one of them
        (contains_excerpt, excerpt_of). The classes: house_next_issue,
        house_self, house_sibling, house_form, trade, classified."""
        pages = self.pages
        texts = [region_text(pages[pn]["regions"][i]) for pn, i in rec["keys"]]
        full = "\n".join(texts)
        title = rec.get("title") or ""
        own = [n for n in (self.magazine_name(), self.A.get("magazine_head") or "") if n]
        own_re = re.compile("|".join(re.escape(n) for n in own), re.I) if own else None
        body_only = "\n".join(t_ for t_ in texts if not MENTION_RE.search(t_))     # not "please mention Weird Tales"
        mentions_self = bool(own_re and own_re.search(body_only))
        # announced works: the by-lines the analysis rejected as announcements inside this record
        mine = {(pn, i) for pn, i in rec["keys"]}
        announces = []
        for pn in sorted({pn for pn, _ in rec["keys"]}):
            for s in self.A["pages"][pn].get("rejected_starts", []):
                bi = s.get("byline_idx")
                if bi is not None and (pn, bi) in mine and s.get("title"):
                    announces.append({"title": s["title"], "author": s.get("author"), "page": pn, "idx": start_first(s)})
        # a verbatim excerpt: narrative prose of some length inside house copy
        prose_words = sum(words(region_text(pages[pn]["regions"][i])) for pn, i in rec["keys"] if prose_like(pages[pn]["regions"][i]))
        n_ad = len(AD_WORDS.findall(full))
        company = COMPANY_RE.search(full)
        siblings = [m.group(1).strip() for m in SIBLING_RE.finditer(full)
                    if not (own_re and own_re.search(m.group(1))) and words(m.group(1)) <= 4
                    and not re.fullmatch(r"(?:The |A )?(?:Magazine|Stories|Weekly|Monthly|Tales|Love|Wonder)", m.group(1).strip())]
        # works named as "TITLE, by Author" / "TITLE\nBy Author" inside house copy (no by-line region of their own)
        for m in re.finditer(r"(?m)^([A-Z][A-Z0-9' ,\-!?.]{3,60}?),?\s*(?:\n|\s)[Bb]y ([A-Z][A-Za-z.' \-]{2,50}?)\s*$", full):
            t_, a_ = m.group(1).strip(" ,"), m.group(2).strip(" .,")
            if words(t_) <= 10 and not any(norm(t_) == norm(x["title"]) for x in announces):
                announces.append({"title": t_, "author": a_, "page": rec["keys"][0][0], "idx": 10 ** 6})
        siblings = [x for x in siblings if x.split()[0].lower() not in SIBLING_STOP]
        cls, advertiser = None, None
        next_issue = bool(HOUSE_NEXT_RE.search(full) or FILLER_RE.search(title) or announces)
        self_copy = bool(mentions_self and HOUSE_SELF_RE.search(full))
        toc_page = self.A.get("toc_page")
        if rec.get("ad_class") == "house_form":
            cls, advertiser = "house_form", (own[0] if own else None)
        elif toc_page and all(pn <= toc_page for pn, _ in rec["keys"]) and (mentions_self or announces):
            cls, advertiser = "house_self", (own[0] if own else None)     # front matter: the cover strip, the title page
            rec["flags"].append("front matter (on or before the contents page): the issue's own cover strip or title page")
        elif next_issue and (mentions_self or FILLER_RE.search(title) or n_ad == 0 or announces) and not self_copy \
                and not (company and n_ad >= 2 and not mentions_self and not FILLER_RE.search(title)):
            cls, advertiser = "house_next_issue", (own[0] if own else None)
        elif siblings and SIBLING_SALE_RE.search(full) and not company and \
                (not mentions_self or re.search(r"publishers? of|companion|sister magazine|by the same", full, re.I)):
            cls, advertiser = "house_sibling", siblings[0]
        elif mentions_self and (self_copy or n_ad == 0 or next_issue):
            cls, advertiser = "house_self", (own[0] if own else None)
        elif FORM_RE.search(full) and re.search(r"ballot|favou?rite stor", full, re.I):
            cls, advertiser = "house_form", (own[0] if own else None)
        else:
            heads = len(CATEGORY_HEAD_RE.findall(full))
            addrs = len(ADDRESS_RE.findall(full))
            if heads >= 4 and addrs >= 4 and len(rec["keys"]) >= 4:
                cls = "classified"
                rec["n_items"] = max(heads, addrs)
            else:
                cls = "trade"
                cands = [m.group(1).strip(" ,.") for m in COMPANY_RE.finditer(full)]
                cands = [c for c in cands if words(c) >= 2 and not COMPANY_STOP.search(c)]
                advertiser = cands[-1] if cands else None      # the signature line at the end of the copy
                if not advertiser:
                    m = re.search(r"(?m)^([A-Z][A-Za-z&'\.\-]+(?: [A-Z][A-Za-z&'\.\-]+){0,4}),\s*(?:Dept|Desk|Box|Studio)", full)
                    advertiser = m.group(1) if m else None
        rec["ad_class"] = cls
        rec["advertiser"] = advertiser
        if announces:
            rec["announces"] = [{"title": a["title"], "author": a["author"], "page": a["page"]} for a in announces]
            if cls in ("trade", "classified"):
                rec["flags"].append("a house announcement shares this block with the advertisement (" +
                                    "; ".join(a["title"][:40] for a in announces[:3]) + ")")
        rec["contains_excerpt"] = bool(cls == "house_next_issue" and prose_words >= EXCERPT_MIN_WORDS)
        if rec["contains_excerpt"]:
            # the quoted work: the announced piece whose title follows the prose (its blurb precedes the title)
            first_prose = next(((pn, i) for pn, i in rec["keys"] if prose_like(pages[pn]["regions"][i])), None)
            after = [a for a in announces if first_prose and (a["page"], a["idx"]) > first_prose]
            pick = (after or announces or [None])[0]
            rec["excerpt_of"] = ({"title": pick["title"], "author": pick["author"]} if pick else None)
            rec["flags"].append("house announcement quoting a story (contains_excerpt); left out of the reuse inventory")
        rec["author"] = None                          # an advertisement has no author; the works it names are in announces

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
        if self.open is not None and self.open.get("type") not in ("ad", "other") and \
                any(region_text(regs[i])[:1].islower() and words(region_text(regs[i])) >= 25 for i in content):
            return False                                  # a paragraph that starts mid-sentence: the open piece continues here
        if self.open is not None and self.open.get("type") not in ("ad", "other"):
            title = self.open.get("title") or ""
            if any(sim(h, title) > 0.5 for h in info["running"]):
                return False
            if self.last_text and not re.search(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$", self.last_text):
                # the piece paused mid-sentence: it continues here unless this page is plainly an
                # advertisement page (no narrative prose, many selling words) — then the piece is suspended
                if sum(1 for i in content if prose_like(regs[i])) >= 2 or info["ad_score"] < 8 or first[:1].islower():
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
                if rec is not None and len(rec["keys"]) == 1 and not re.search(r"\d", rec["title"] or "") and words(rec["title"]) <= 6:
                    # two display lines with nothing between them: one headline in two sizes ("FREE FREE FREE" /
                    # "A $1.50 Self-filling Stylographic Pen")
                    rec["title"] = (rec["title"] + " " + t)[:120]
                    self.add(rec, pno, i, "title")
                    continue
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
        pages_ = self.pages
        starts = {s["byline_idx"]: s for s in info["starts"] if s["byline_idx"] is not None}
        at_of = {s["at"]: s for s in info["starts"] if s.get("at") is not None}
        title_of = {}
        for s in info["starts"]:
            for j in s["title_idx"] + s["subtitle_idx"] + list(s.get("teaser_idx") or []):
                title_of[j] = s
        chapters = set(info["chapters"])
        fillers = set(info["fillers"])
        forms = info.get("forms") or {}
        # announcement by-lines the analysis rejected inside a filler block: where one begins with no
        # filler open, the announcement (title, by-line, blurb, a quoted excerpt) is house advertising
        announce_at = {}
        for s in info.get("rejected_starts", []):
            if "inside a filler block" in (s.get("rejected") or ""):
                announce_at.setdefault(start_first(s), s)
        n = len(regs)
        filler = None
        filler_hard = False        # the filler runs to the end of the page (a house announcement with its excerpt)
        cont_on = set(info.get("cont_on") or [])
        tail = False               # below a "continued on page N" / "[turn page]" notice: the rest of the page is not the piece
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
                        self.furn(pno, j)                          # the piece's title repeated over the continuation
                        self.furniture[-1]["why"] = "repeated title above a 'continued from' notice"
                else:
                    ltxt = " ".join(region_text(regs[j]) for j in loose)
                    kind = "ad" if (len(AD_WORDS.findall(ltxt)) >= 1 or info["ad_score"] >= 3) else "other"
                    rec = self.new(kind, title=self.page_title(p, info, loose[0]) or region_text(regs[loose[0]])[:60], page=pno)
                    rec["flags"].append("text above a 'continued from' notice" + (" (reads like an advertisement)" if kind == "ad" else " with no known owner"))
                    self.open = rec
                    for j in loose:
                        self.add(rec, pno, j)
        pre_new = []               # regions above the title that belong to the piece starting on this page
        if info["starts"]:
            # regions above the first title of the page: the end of the open piece's column when there is
            # evidence it runs on here; else the illustration's caption (furniture, as the verified records
            # have it), the blurb, a "The Story Thus Far" heading or a second setting of the title (the new
            # piece's: teaser, heading), signature marks and display garble (furniture)
            first = min(start_first(s) for s in info["starts"])
            end_re = re.compile(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$")
            above = [j for j in range(0, first) if j not in info["furniture"] and key(pno, j) not in self.owner]
            open_ok = self.open is not None and self.open.get("type") not in ("ad", "other")
            # evidence that the open piece's column runs on at the top of this page: a paragraph that starts
            # in lower case (a sentence carried over) or a long one; every Text region down to the last such
            # paragraph is the column, and so is the Text that follows it — except a short quoted line right
            # before the title, which is the illustration's caption
            evidence = [j for j in above if regs[j]["label"] == "Text" and (region_text(regs[j])[:1].islower() or words(region_text(regs[j])) > 40)]
            col_last = max(evidence) if (evidence and open_ok) else -1
            new_titles = [s_["title"] for s_ in info["starts"] if s_.get("title")] + \
                         [s_["toc"]["title"] for s_ in info["starts"] if s_.get("toc") and s_["toc"].get("title")]
            joined = False
            for j in above:
                r_ = regs[j]
                t = region_text(r_)
                last_above = (j == above[-1])
                run_on = joined and r_["label"] == "Text" and not (last_above and t[:1] in "\"\u201c'\u2018" and words(t) <= 15)
                if open_ok and r_["label"] == "Text" and (j <= col_last or run_on):
                    self.add(self.open, pno, j, "chapter" if j in chapters else None)     # the end of the open piece
                    joined = True
                    continue
                joined = False
                if words(t) > 40 and self.open is None:
                    rec = self.new("feature", title=t.split("\n")[0][:80], page=pno)
                    rec["flags"].append("text above the first title of the page with no record open")
                    self.add(rec, pno, j)
                elif r_["label"] == "Caption" or t[:1] in "\"\u201c'\u2018":
                    self.furn(pno, j)                          # the illustration's caption: not story text (the verified records' rule)
                    self.furniture[-1]["why"] = "illustration caption above the title on a start page"
                elif any(sim(t, nt) > 0.6 for nt in new_titles) or \
                        (words(t) >= 4 and re.search(r"[a-z]", t) and (end_re.search(t) or r_["label"] == "SectionHeader" or
                                                                        (8 <= words(t) <= 80 and not (len(t.split()[0]) >= 2 and t.split()[0].isupper())))):
                    pre_new.append(j)                          # the blurb, the synopsis heading or a second setting of the title
                else:
                    self.furn(pno, j)
                    self.furniture[-1]["why"] = "above the title on a start page"
        pre_set = set(pre_new)
        i = 0
        while i < n:
            if i in pre_set:
                i += 1                                    # attached to the new piece when its record opens
                continue
            if i in cont_from:
                target = self.continued_piece(cont_from[i], pno)
                if target is not None:
                    self.open = target
                    filler, filler_hard = None, False
                    target["flags"].append(f"continues on p.{pno} (notice: continued from page {cont_from[i]})") if f"continues on p.{pno}" not in " ".join(target["flags"]) else None
                else:
                    rec = self.new("other", page=pno, title=f"continued from page {cont_from[i]}")
                    rec["flags"].append(f"'continued from page {cont_from[i]}' but no record is open there")
                    self.open = rec
                i += 1
                continue
            if i in cont_on and self.open is not None and self.open.get("type") not in ("ad", "other"):
                tail = True
            if i in info["furniture"]:
                i += 1
                continue
            r = regs[i]
            t = region_text(r)
            if tail and i not in title_of and i not in starts and i not in at_of and \
                    (filler is None or (is_display(p, r) and r["label"] == "SectionHeader")):
                # advertisement matter printed under the piece's "continued on" notice: one record per display header
                filler = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                filler["flags"].append(f"below the 'continued on' notice on p.{pno}")
                filler_hard = True
                self.add(filler, pno, i, "title")
                i += 1
                continue
            if i in title_of:
                s = title_of[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                    self.attach_pre(pno, p, s["_rec"], pre_new)
                    pre_new = []
                role = "title" if i in s["title_idx"] else ("teaser" if i in (s.get("teaser_idx") or []) else "subtitle")
                self.add(s["_rec"], pno, i, role)
                if role == "teaser":
                    s["_rec"]["teaser"] = t.replace("\n", " ")
                self.open = s["_rec"]
                filler, filler_hard = None, False
                if s["byline_idx"] is None and i == max(s["title_idx"] + s["subtitle_idx"]):
                    self.teaser(pno, p, info, s, i + 1)
                i += 1
                continue
            if i in at_of:
                s = at_of[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                    self.attach_pre(pno, p, s["_rec"], pre_new)
                    pre_new = []
                self.open = s["_rec"]
                filler, filler_hard = None, False
                # fall through: this region is the first paragraph of the piece
            if i in starts:
                s = starts[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                    self.attach_pre(pno, p, s["_rec"], pre_new)
                    pre_new = []
                self.add(s["_rec"], pno, i, "author")
                if s.get("name_idx") is not None:
                    self.add(s["_rec"], pno, s["name_idx"], "author")
                self.open = s["_rec"]
                self.suspended = None
                filler, filler_hard = None, False
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
            if filler is not None and i in announce_at and not filler_hard:
                # a house announcement after a trade advertisement on the same page: its own record
                s = announce_at[i]
                filler = self.new("ad", title=(s.get("title") or "announcement").replace("\n", " ")[:120], page=pno)
                filler["flags"].append(f"announcement block on p.{pno} after another advertisement")
                filler_hard = True
                self.add(filler, pno, i, "title" if i in (s.get("title_idx") or []) else None)
                i += 1
                continue
            if filler is not None and i in forms:
                # the coupon closes the advertisement; the piece may resume after it
                for j in forms[i]:
                    self.add(filler, pno, j)
                i = forms[i][-1] + 1
                filler_hard = False
                continue
            if filler is not None and r["label"] == "SectionHeader" and words(t) <= 12 and \
                    (filler_hard or (i in fillers and is_display(p, r))) and \
                    not any(i in (s_.get("title_idx") or []) for s_ in announce_at.values()) and \
                    not any(k_ in (s_.get("title_idx") or []) for s_ in announce_at.values() for k_ in range(i + 1, min(i + 3, n))):
                # a headline inside advertising matter: the next advertisement on the page — unless the block so
                # far is only a stacked headline ("NEXT MONTH" / "—The—" / "Tenants of Broussac")
                stacked = (len(filler["keys"]) <= 3 and all(words(region_text(pages_[pn_]["regions"][j_])) <= 3 for pn_, j_ in filler["keys"])
                           and not re.search(r"\d", filler["title"] or ""))
                if stacked:
                    filler["title"] = (filler["title"] + " " + t.replace("\n", " "))[:120]
                    self.add(filler, pno, i, "title")
                else:
                    filler = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                    filler["flags"].append(f"advertisement after another on p.{pno}")
                    self.add(filler, pno, i, "title")
                    filler_hard = False
                i += 1
                continue
            if filler is not None:
                prose = (words(t) >= 40 and not AD_WORDS.search(t) and r["label"] == "Text"
                         and (t[:1].islower() or (words(t) >= 60 and prose_like(r))))
                if prose and (not filler_hard or t[:1].islower()) and self.open is not None and self.open.get("type") not in ("ad", "other"):
                    filler, filler_hard = None, False              # the piece's column resumes beside the advertisement
                else:
                    self.add(filler, pno, i)
                    i += 1
                    continue
            if i in forms and self.open["type"] not in ("ad", "other"):
                # a ballot or coupon inside a piece, never text of the piece: the magazine's own ballot is its
                # own record (house_form); a trade coupon joins the advertisement printed above it
                blk = forms[i]
                btxt = " ".join(region_text(regs[j]) for j in blk)
                own_names = [x for x in (self.magazine_name(), self.A.get("magazine_head") or "") if x]
                house = bool(re.search(r"ballot|favou?rite stor|the editor|readers?['’]? (?:vote|choice|opinion)", btxt, re.I)
                             or any(re.search(re.escape(x), btxt, re.I) for x in own_names))
                prev = next((self.owner.get(key(pno, j)) for j in range(i - 1, -1, -1) if j not in info["furniture"]), None)
                prev_rec = next((r_ for r_ in self.records if r_["article_id"] == prev), None) if prev else None
                if house:
                    rec = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                    rec["ad_class"] = "house_form"
                    rec["flags"].append(f"ballot or coupon on p.{pno}, kept apart from the piece around it")
                elif prev_rec is not None and prev_rec["type"] == "ad":
                    rec = prev_rec                                 # the coupon of the advertisement above it
                else:
                    rec = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                    rec["flags"].append(f"coupon on p.{pno} with no advertisement read above it")
                for j in blk:
                    self.add(rec, pno, j, "title" if (j == i and rec is not prev_rec) else None)
                i = blk[-1] + 1
                continue
            if i in announce_at and self.open["type"] in ("story", "serial_part", "poem", "letters", "feature"):
                # an announcement block ("in the next issue": title, by-line, blurb, often a quoted excerpt)
                # with no filler header on this page: house advertising to the end of the page
                s = announce_at[i]
                filler = self.new("ad", title=(s.get("title") or "announcement").replace("\n", " ")[:120], page=pno)
                filler["flags"].append(f"announcement block on p.{pno} (a rejected start)")
                filler_hard = True
                self.add(filler, pno, i, "title" if i in (s.get("title_idx") or []) else None)
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
                    # a house announcement followed by announcement by-lines, or by another notice ("in the
                    # December issue of"), on this page: the block, with the blurb or excerpt it carries, runs on
                    filler_hard = bool(FILLER_RE.search(t)) and (any(j > i for j in announce_at) or
                                                                 any(j > i and j not in info["furniture"] and words(region_text(regs[j])) <= 12
                                                                     and FILLER_RE.search(region_text(regs[j])) for j in range(i + 1, n)))
                    i += 1
                    continue
                self.add(self.open, pno, i, "heading")            # a display line inside the story (a headline, a part title)
                i += 1
                continue
            if i in chapters:
                self.add(self.open, pno, i, "chapter")
            elif r["label"] == "Caption":
                self.add(self.open, pno, i, "caption")
            elif r["label"] == "SectionHeader" and words(t) <= 12 and self.open.get("type") in ("letters", "feature") and \
                    self.filler_kind(pno, p, info, i, strict=True) == "ad":
                filler = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)   # an advertisement set in the department's type
                filler["flags"].append(f"advertisement inside the department on p.{pno} (selling words under a plain heading)")
                self.add(filler, pno, i, "title")
            elif r["label"] == "SectionHeader" and words(t) <= 12 and self.open.get("type") not in ("ad", "other"):
                self.add(self.open, pno, i, "heading")            # a subheading inside the piece (a letter's title in The Eyrie)
            else:
                self.add(self.open, pno, i)
            i += 1
        for j in pre_new:
            if key(pno, j) not in self.owner:
                self.furn(pno, j)
                self.furniture[-1]["why"] = "above the title on a start page"
        # the running head names the piece the page belongs to; a mismatch is worth a flag
        if self.open and self.open.get("type") in ("story", "serial_part") and info["running"]:
            title = self.open.get("title") or ""
            toc_title = (self.open.get("toc") or {}).get("title") or ""
            names = [h for h in info["running"] if words(h) <= 10 and sim(h, self.magazine_name()) < 0.6]
            if names and title and max(max(sim(h, title), sim(h, toc_title)) for h in names) < 0.35:
                self.open.setdefault("_head_mismatch", []).append(pno)

    def attach_pre(self, pno, p, rec, pre_new):
        """Regions printed above the title on a piece's first page: the
        illustration's caption (role caption), the blurb (teaser, when the
        piece has none yet), a synopsis heading such as "The Story Thus Far"
        (heading)."""
        regs = p["regions"]
        for j in pre_new:
            if key(pno, j) in self.owner:
                continue
            r = regs[j]
            t = region_text(r)
            if r["label"] == "Caption" or (t[:1] in "\"\u201c'\u2018" and words(t) <= 40):
                self.add(rec, pno, j, "caption")
            elif r["label"] == "SectionHeader" or words(t) < 8:
                self.add(rec, pno, j, "heading")
            elif not rec.get("teaser") and 8 <= words(t) <= 80:
                self.add(rec, pno, j, "teaser")
                rec["teaser"] = t.replace("\n", " ")
            else:
                self.add(rec, pno, j)

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

    def filler_kind(self, pno, p, info, i, strict=False):
        """A display header inside a story page: a filler (its own record) when
        it reads like one — the magazine's name, 'in the next issue', an
        advertisement — else None (a headline inside the story). strict: only
        the two-selling-word test (for plain subheadings inside departments)."""
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
            if strict and j != i and (j in (info.get("forms") or {}) or j in info["fillers"]):
                break                                     # a ballot/coupon or the next display header is its own block
            block_idx.append(j)
        block = " ".join(region_text(regs[j]) for j in block_idx)
        n_ad = len(AD_WORDS.findall(block))
        if strict:
            return "ad" if (n_ad >= 2 and not any(prose_like(regs[j]) for j in block_idx if j != i)) else None
        if sim(t, self.magazine_name()) > 0.6 or sim(t, self.A.get("magazine_head", "")) > 0.6:
            return "ad"
        if FILLER_RE.search(t) or FILLER_RE.search(near[:200]):
            return "ad"                                   # house copy: an ad of class house_next_issue / house_self
        if n_ad >= 2:
            return "ad"
        if n_ad >= 1 and len(block_idx) >= 2 and not any(prose_like(regs[j]) for j in block_idx if j != i) \
                and re.search(r"free (?:offer|trial|book|sample|booklet|details|information|catalog)|\boffer\b|\bsend\b", block, re.I):
            return "ad"                                   # a small trade advertisement with one selling word
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
        page_title = s["title"] or None
        page_author = s["author"] or None
        # the contents page has authority over title and author (decision of 2026-09-03); the page's own
        # forms are kept beside them, and a strong disagreement is flagged for a person to look at
        title = (toc["title"] if toc and toc.get("title") else page_title)
        author = (toc["author"] if toc and toc.get("author") else page_author)

        def same_words_better_case(chosen, other):
            # the same words in both places: keep the form set in upper and lower case (the contents
            # page is often all capitals) — authority is about the words, not the type case
            if chosen and other and norm(chosen) == norm(other) and chosen.upper() == chosen and other.upper() != other:
                return other
            return chosen
        title = same_words_better_case(title, page_title)
        author = same_words_better_case(author, page_author)
        sub = " ".join(region_text(regs[j]) for j in s["subtitle_idx"])
        typ = (toc or {}).get("type") or "story"
        if VERSE_RE.search(sub):
            typ = "poem"
        if SERIAL_RE.search(sub) or (title and SERIAL_RE.search(title)):
            typ = "serial_part"
        rec = self.new(typ, title=title, author=author, page=pno,
                       toc=({k: toc.get(k) for k in ("title", "author", "page", "type", "blurb")} if toc else None))
        if sub:
            rec["subtitle"] = re.sub(r"\s+", " ", sub)
        if page_title and norm(page_title) != norm(title or ""):
            rec["title_as_printed"] = page_title
        if page_author and norm(page_author) != norm(author or ""):
            rec["author_as_printed"] = page_author
        if toc and toc.get("title"):
            rec["title_source"] = "contents"
            if page_title and sim(toc["title"], page_title) < 0.55:
                rec["flags"].append(f"title from the contents page; the page prints '{page_title}'")
        elif page_title:
            rec["title_source"] = "page"
        if toc and toc.get("author"):
            rec["author_source"] = "contents"
            if page_author and sim(toc["author"], page_author) < 0.6:
                rec["flags"].append(f"author from the contents page; the page prints '{page_author}'")
        elif page_author:
            rec["author_source"] = "page"
        if not page_title and toc:
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
        if s["_rec"].get("teaser"):
            return                                          # the blurb above the title was already taken
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
                       "flags": sum(len(r["flags"]) for r in records),
                       "ad_classes": dict(Counter(r.get("ad_class") or "?" for r in records if r["type"] == "ad")),
                       "house_excerpts": sum(1 for r in records if r.get("contains_excerpt"))}}


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
    assert split_chapter("CHAPTER II\nThe Thing in the Vault") == ("II", 2, "The Thing in the Vault")
    assert split_chapter("IV. The Coming of the Beast") == ("IV", 4, "The Coming of the Beast")
    assert split_chapter("Chapter 3") == ("3", 3, None)
    assert split_chapter("The Thing in the Vault") == (None, None, "The Thing in the Vault")
    assert roman_int("XIV") == 14 and roman_int("IX") == 9
    fpage = {"width": 1400, "height": 2200, "regions": [
        {"label": "Text", "bbox": [0, 0, 100, 40], "order": 0, "text": "Bernard J. Kenton, of Cleveland, writes:"},
        {"label": "SectionHeader", "bbox": [0, 50, 100, 90], "order": 1, "text": "My favorite stories in the May WEIRD TALES are:"},
        {"label": "Table", "bbox": [0, 100, 100, 300], "order": 2, "text": "Story\nRemarks\n(1) -----\n(2) -----"},
        {"label": "Text", "bbox": [0, 310, 100, 340], "order": 3, "text": "I do not like the following stories:"},
        {"label": "Table", "bbox": [0, 350, 100, 500], "order": 4, "text": "(1) -----\nWhy? -----"},
        {"label": "Text", "bbox": [0, 510, 100, 560], "order": 5, "text": "It will help us to know what kind of stories you want if you will fill out this coupon and mail it."},
        {"label": "Text", "bbox": [0, 570, 100, 600], "order": 6, "text": "Reader's name and address:"},
        {"label": "Form", "bbox": [0, 610, 100, 700], "order": 7, "text": "-----\n-----"}]}
    assert form_block(fpage, 1, {"furniture": []}) == [1, 2, 3, 4, 5, 6, 7], form_block(fpage, 1, {"furniture": []})
    assert form_block(fpage, 0, {"furniture": []}) is None
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
