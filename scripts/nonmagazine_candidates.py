#!/usr/bin/env python3
"""Items in the selection that are not magazine issues (Sujin Kang's decision 1 of 19 October 2026: "the corpus is pulp
magazines"; the exclusion list B3.4 of the takeover to-do): a read-only pass over every selected issue that scores the
signs of a standalone book, a single story, a dime-novel library number, a comic, an index or a portfolio, a fanzine
or an item outside the period, or a magazine that is not a fiction magazine, and writes a review table for a person
to confirm item by item. Nothing is set aside here: scripts/set_aside_nonmagazine.py --apply reads the confirmed
table and writes config/corpus_set_aside.json.

The selection (pipeline/s00b_select.py) admitted these items because the archive files them in its pulp collections;
their magazine name is the archive's item title and most of them are "magazines" with one issue. The signals, each
with a code the table shows, come from four places:

  the archive's item record (data/raw/<id>/meta.json: title, description, subjects, collections, date, image count),
  the selection's own fields (config/corpus_issues.json: how many issues the "magazine" has, the name, the date),
  the assembly's contents reading (data/assembly_v2/rules/<id>/analysis.json: a chapter list for a contents page,
      by-lines found on the pages, chapter heads, authors on the contents entries),
  the records (data/assembly_v2/rules/<id>/articles.json, when --records is given: story records and distinct authors).

Each signal adds points to one class ("any" is evidence against being a magazine issue that fits every class: a single
issue, no numbering); the class with most points is the item's class, its score is that class's points plus the "any"
points, and the proposal is EXCLUDE at SCORE_EXCLUDE points or more, LOOK from SCORE_LOOK, else KEEP (strong magazine
evidence — several by-lines and authors on the contents page, a volume-and-number title — takes points away). An item
with only "any" points has no class and is kept. The table lists every item proposed EXCLUDE or
LOOK, and every issue of the verification sample whatever its score, so the sample is seen to have been checked.

    python3 scripts/nonmagazine_candidates.py                 # on the server; writes data/corpus/p52-2026-10-16/nonmagazine_candidates.{json,csv,md}
    python3 scripts/nonmagazine_candidates.py --records       # also reads articles.json (slower: 7,300 files)
    python3 scripts/nonmagazine_candidates.py --root <dir>    # a mirror of the data tree (the session's copy through the data door)
    python3 scripts/nonmagazine_candidates.py --selftest

The review table (CSV) has a "decision" column for the reader: x = exclude, k = keep, blank = not looked at yet; a
"note" column is free. The reader's filled table is the input of set_aside_nonmagazine.py.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

RULE_VERSION = "1.0"
SCORE_EXCLUDE = 5
SCORE_LOOK = 3
OUT_DIR = os.path.join("data", "corpus", "p52-2026-10-16")
OUT_NAME = "nonmagazine_candidates"

CLASSES = ("dime novel", "comic", "index or portfolio", "single story", "outside the period", "not a fiction magazine", "book")   # tie order: the specific first

# ---- the signals: code -> (class, points, what it says) -------------------------------------------------------------
SIGNALS = {
    "M1": ("book", 3, "the archive's title is Title - Author (Publisher, Year) or Author - Title (Publisher ...), or the file is named Surname, Initials - Title"),
    "M2": ("book", 3, "the description is a book's: page count (vii+301pp), binding (hc, pb), 'First Edition', 'hardcover', 'paperback'"),
    "M2b": ("book", 1, "the description calls the item a novel or a collection"),
    "M3": ("any", 1, "no volume, number or issue anywhere: not in the title, the description or the metadata"),
    "M4": ("book", 1, "the subjects name a novel, short stories, a collection or an anthology, or the record names a creator"),
    "M5": ("outside the period", 3, "a modern date in the archive's record: its date or year after 1960 (a reprint, a fanzine, a modern magazine)"),
    "M5b": ("outside the period", 2, "a year 1961-2029 in the title or identifier, with no date in the archive's record"),
    "M6": ("index or portfolio", 3, "an index, checklist, bibliography, price guide, newsletter, cover gallery or artwork collection by its title or description"),
    "M7": ("comic", 3, "a comic: the archive's comics collections, 'comic' in title or subjects, a Dell Four Color number"),
    "M8": ("dime novel", 4, "a dime-novel or nickel-weekly number: Library No., Half Dime Library, Pocket Library, Frontier Series, a known weekly's name, or the archive says dime novel, nickel weekly or cheap library"),
    "M9": ("not a fiction magazine", 2, "a magazine by its title or description that is not a fiction magazine: film, cinema, art, pictorial, science and mechanics, cartoons, a sports digest, true crime, a newspaper supplement, a service or hobby title"),
    "M10": ("outside the period", 2, "no date in the archive's record and a modern scanning group's tag in the title (c2c, DCP, gorbag, mattspong, Cosmic Jukebox, SproutScan, True.PDF, TC CG and the like): a fanzine or comic scanned in the 2000s"),
    "M11": ("single story", 2, "the description says the item reprints or extracts a magazine piece ('originally published in', 'appeared as a serial in', 'published in the ... issue of', 'facsimile', 'reprint')"),
    "S1": ("any", 1, "the 'magazine' has a single issue in the selection"),
    "S2": ("book", 2, "the magazine name is a book's: 'Title by Author', 'Author - Title', an editor's '(ed)', a publisher in parentheses"),
    "S3": ("book", 3, "a reprint or paperback series: 'Serials' compilations, 'Novel NN', an Ace or Avon paperback number, 'Reprint'"),
    "S4": ("single story", 2, "undated, a title with no magazine word, one by-line start on the pages and no contents page, under 60 scan pages"),
    "A1": ("book", 3, "the contents page is a chapter list (toc_kind chapters, or the entries are numbered chapters)"),
    "A2": ("book", 2, "by-lines on at most one page in an item of 48 scan pages or more"),
    "A3": ("book", 2, "ten or more chapter heads and by-lines on at most three pages"),
    "A4": ("book", 1, "five or more contents entries, none with an author"),
    "A5": ("book", 2, "a book's shape: no contents page, five or more chapter heads, at most two starts (a title over a by-line) in 48 scan pages or more"),
    "D1": ("dime novel", 4, "a dime novel's shape: 40 scan pages or fewer, dated 1925 or before, five or more chapter heads, no contents page, by-lines on at most three pages"),
    "R1": ("book", 2, "the records: at most one story record, or all story records by one author, in an item of 60 scan pages or more"),
    # evidence of a magazine takes points away from every class
    "N1": (None, -3, "a volume-and-number title (v01n01, Vol. 3 No. 2, #12, [1947-03]) or [vN #N] in the description — against book and single story"),
    "N2": (None, -3, "by-lines on four or more pages and three or more contents entries with authors — against book, single story, dime novel, index or portfolio and comic"),
    "N3": (None, -2, "a subject of the archive's record is magazine, periodical or pulp — against book and single story"),
    "N4": (None, -1, "a magazine word in the name (Magazine, Stories, Tales, Weekly, Monthly, Quarterly, Science Fiction, Detective, Western, Adventure, Mystery, Worlds ...) — against book and single story"),
}
AGAINST = {"N1": {"book", "single story"}, "N2": {"book", "single story", "dime novel", "index or portfolio", "comic"}, "N3": {"book", "single story"}, "N4": {"book", "single story"}}

RE_TITLE_BOOK = re.compile(r"^[^\-\[\]]{3,80}\s[-–—]\s[^\-\[\]]{3,80}\((?:[^)]*,\s*)?(?:1[89]\d\d|20[0-2]\d)", re.I)
RE_FILE_AUTHOR_FIRST = re.compile(r"^[A-Z][a-zA-Z'\-]+,\s+[A-Z][\w.\s'\-]{0,30}\s[-–]\s\S")
RE_BOOK_DESC = re.compile(r"\b(?:[ivxlc]+\+)?\d{2,4}\s*pp\b|\b(?:hc|pb|tp)\)|\bfirst edition\b|\bhardcover\b|\bpaperback\b|\bhardback\b", re.I)
RE_NOVEL_DESC = re.compile(r"\b(?:a|the|this|complete)\s+novel\b|\bnovel\s+by\b|\bcollection of\b|\bstory collection\b|\bomnibus\b", re.I)
RE_VOLNUM = re.compile(r"\bv(?:ol)?\.?\s*\d{1,3}\s*,?\s*n(?:o|um|r)?\.?\s*\d{1,3}\b|\bvol(?:ume)?\.?\s*\d|\bno\.?\s*\d{1,4}\b|#\s?\d{1,4}\b|\[\d{4}-\d\d(?:-\d\d)?\]|\bissue\b|\bnumber\s+\d", re.I)
RE_VOLNUM_DESC = re.compile(r"\[\s*v\d+\s*#\d+|\bv\d+\s*#\d+\b|\bvol\.?\s*\d+\s*(?:no|#)\.?\s*\d+", re.I)
RE_MODERN_YEAR = re.compile(r"(?<!\d)(19[6-9]\d|20[0-2]\d)(?!\d)")
RE_REFERENCE = re.compile(r"\b(?:index|checklist|bibliograph\w*|price guide|newsletters?|cover (?:art|artwork|gallery|collection)|covers of\b|artwork of\b|art of\b|portfolio|about the pulps|collector'?s? guide)", re.I)
RE_REFERENCE_DESC = re.compile(r"^\W*(?:an?\s+)?(?:index|checklist|bibliograph\w*|price guide)\b|\b(?:index|checklist|bibliograph\w*)\s+(?:to|of|on)\s+(?:the\s+)?[A-Z]|\bprice guide\b", re.I)
RE_COMIC = re.compile(r"\bcomics?\b|\bfour color\b|\bgraphic novel\b|\bfunnies\b", re.I)
RE_DIME = re.compile(r"\b(?:half[- ]dime|dime)\s+library\b|\blibrary\s+(?:no\.?\s*)?\d{2,4}\b|\bpocket library\b|\bfrontier series\b|\bnickel library\b|\b(?:weekly|library)\s+n\d{2,4}\b|\bbeadle"
                     r"|\b(?:nick carter|old sleuth|tip top (?:weekly|library)|wild west weekly|pluck and luck|work and win|secret service|fame and fortune|liberty boys|brave and bold"
                     r"|frank reade|young wild west|diamond dick|buffalo bill stories|jesse james stories|old cap\.? collier|old king brady|log cabin library|five cent library|wide awake library"
                     r"|new york detective library|boys of new york|golden hours|happy days|arm chair library|red raven library|o'er land and sea"
                     r"|blue and gray weekly|rough rider weekly|young rough riders|bowery boy library|might and main|do and dare|starry flag|medal library"
                     r"|boys of liberty|young klondike|frank manley|all-sports library|paul jones weekly|comrades weekly|new buffalo bill weekly|nick carter stories"
                     r"|motor stories|rover boys|bound to win|true blue|three chums|the liberty boys)\b", re.I)
RE_DIME_DESC = re.compile(r"\b(?:dime novels?|nickel weekl(?:y|ies)|nickel librar(?:y|ies)|cheap librar(?:y|ies)|story papers?)\b", re.I)
RE_NONFICTION = re.compile(r"\b(?:film|cinema|cine|movie|screen|photoplay|pictorial|arts?\s+monthly|cartoons?|mechanics|popular science|(?:baseball|football|boxing|wrestling|sports?) digest|hobbies|radio|aviation|physical culture|true\s+(?:story|confessions|detective)|real detective|police gazette|harper'?s weekly|illustrated news|revue|manual|bulletin|army talks|navy|newspaper supplement|supplement)\b", re.I)
RE_MODERN_TAG = re.compile(r"\b(?:c2c|dcp|gorbag|mattspong|cosmic[- ]jukebox|sproutscan|minutemen|darwination-dcp|wilddog|fantaco|true\.pdf|tc[- ]cg|noads|re-?edit|fixed|sampler)\b|\.tc\s|\(\w+-dcp\)", re.I)
RE_REPRINT_DESC = re.compile(r"\boriginally (?:published|appeared|printed)\b|\bappeared (?:as|in)\b.{0,40}\b(?:serial|magazine|issue)\b|\bpublished in (?:the )?\w*\s*(?:\w+ )?\d{4} issue\b|\bpublished in the [A-Z][\w' ]+ \d{4} issue|\bissue of [A-Z][\w' :]+\b|\bfacsimile\b|\breprint(?:ed|s)?\b|\bserial(?:ized|ised)? in\b|\bfrom the pages of\b", re.I)
RE_BOOK_NAME = re.compile(r"\b[Bb]y\s+[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3}\s*$|^[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3}\s[-–—]\s|\s[-–—]\s[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){1,3}\s*(?:\(|$)|\(ed\.?\)|\(ed\)\s|\((?:Methuen|Ace|Avon|Ballantine|Bantam|Dell|Signet|Pocket|Macmillan|Doubleday|Harper|Scribner|Putnam|Dodd|Lippincott|Houghton|Warne|Mills|Cassell|Hodder|Hutchinson|Ward Lock|Collins|Gollancz|Lovell|Street|Grosset|Burt|Chelsea House|Clode|Appleton|Dutton|Knopf|Simon|Lancer|Pyramid|Gold Medal|Popular Library|Berkley)\b|\([A-Z][\w.&'\-]+(?:\s+[A-Z][\w.&'\-]+)*,\s*\d{4}", re.U)
RE_SERIES = re.compile(r"\bserials?\b|\bnovels?\s*#?\s*\d{1,3}\b|^ace\s+[a-z]-?\s?\d{2,4}\b|\bavon\s+(?:books?|t-?\d)|\breprints?\b|\bpocket\s+books?\s+\d|\bbantam\s+\d|\bdell\s+(?:books?\s+)?\d{2,4}\b|\bsignet\s+\d|\bballantine\s+\d", re.I)
RE_MAGAZINE_WORD = re.compile(r"\b(?:magazine|stories|tales|weekly|monthly|quarterly|annual|journal|gazette|review|digest|adventures?|detective|western|romances?|mysteries|mystery|fiction|yarns|novels|book|library|reader|comics|fun|pictorial|paper|news|budget|companion|argosy|all-story|worlds|sports|thrilling|startling|astounding|amazing|wonder|planet|galaxy|fantastic|fantasy|weird|love|ranch|range|action|aces|confessions)\b", re.I)
RE_CHAPTER_TITLE = re.compile(r"^\s*(?:chapter|chap\.?|part|book)\s+[\divxlc]+\b|^\s*[IVXLC]{1,6}\s*[.:\-—]\s*\S|^\s*\d{1,2}\s*[.:]\s+[A-Z]", re.I)


def _list(v):
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


def _text(v):
    return " ".join(_list(v)) if not isinstance(v, str) else v


def _year(s):
    m = re.search(r"(?<!\d)(1[89]\d\d|20[0-2]\d)(?!\d)", str(s or ""))
    return int(m.group(1)) if m else None


def archive_record(root, iid):
    p = os.path.join(root, "data", "raw", iid, "meta.json")
    try:
        m = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    md = m.get("metadata") or {}
    files = [f.get("name", "") for f in (m.get("files") or []) if isinstance(f, dict)]
    return {"title": _text(md.get("title")), "description": re.sub(r"<[^>]+>", " ", _text(md.get("description"))),
            "subjects": [s.lower() for s in _list(md.get("subject"))], "collections": _list(md.get("collection")),
            "date": _text(md.get("date")), "year": _text(md.get("year")), "creator": _text(md.get("creator")),
            "publisher": _text(md.get("publisher")), "imagecount": md.get("imagecount"), "identifier": _text(md.get("identifier")),
            "files": files, "volume": _text(md.get("volume")), "issue": _text(md.get("issue"))}


def assembly_record(root, iid):
    p = os.path.join(root, "data", "assembly_v2", "rules", iid, "analysis.json")
    try:
        a = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pages = a.get("pages") or {}
    pages = list(pages.values()) if isinstance(pages, dict) else list(pages)
    toc = a.get("toc") or []
    starts = sum(len(p.get("starts") or []) for p in pages)
    byline_pages = sum(1 for p in pages if p.get("bylines"))
    byline_starts = sum(1 for p in pages for s in (p.get("starts") or []) if s.get("author") or s.get("byline_idx") is not None)
    chapters = sum(len(p.get("chapters") or []) for p in pages)
    chap_titles = sum(1 for e in toc if RE_CHAPTER_TITLE.match(str(e.get("title") or "")))
    return {"pages": len(pages), "toc_n": len(toc), "toc_kind": a.get("toc_kind"), "toc_page": a.get("toc_page"),
            "toc_authors": sum(1 for e in toc if e.get("author")), "toc_chapter_titles": chap_titles,
            "starts": starts, "byline_pages": byline_pages, "byline_starts": byline_starts, "chapters": chapters,
            "magazine_head": a.get("magazine_head")}


def records_record(root, iid):
    p = os.path.join(root, "data", "assembly_v2", "rules", iid, "articles.json")
    try:
        a = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    arts = a.get("articles") if isinstance(a, dict) else a
    stories = [r for r in arts or [] if r.get("type") == "story"]
    authors = {re.sub(r"\W+", " ", str(r.get("author") or "")).strip().lower() for r in stories if r.get("author")}
    return {"records": len(arts or []), "stories": len(stories), "story_authors": len(authors)}


def score(issue, n_in_magazine, archive, assembly, records=None):
    """The signals that fire for one item: (codes, evidence dict). Pure: the tests feed it dictionaries."""
    codes, ev = [], {}
    name = issue.get("magazine") or ""
    title = (archive or {}).get("title") or ""
    desc = (archive or {}).get("description") or ""
    subjects = (archive or {}).get("subjects") or []
    colls = (archive or {}).get("collections") or []
    files = (archive or {}).get("files") or []
    subj_text = " ".join(subjects)
    pages = (assembly or {}).get("pages") or (int((archive or {}).get("imagecount") or 0)) or (issue.get("pages_in_record") or 0)
    ev["pages"] = pages

    if archive:
        if RE_TITLE_BOOK.search(title) or any(RE_FILE_AUTHOR_FIRST.match(f) for f in files):
            codes.append("M1"); ev["M1"] = title[:90]
        mb = RE_BOOK_DESC.search(desc)
        # the archive's contents listings give magazines a page count too ("96pp, pulp"): a bare page count beside a
        # magazine-format word is not a book's description
        if mb and re.fullmatch(r"(?:[ivxlc]+\+)?\d{2,4}\s*pp", mb.group(0), re.I) and re.search(r"\b(?:pulp|digest|bedsheet|tabloid|quarto|magazine|large pulp|saddle-stapled)\b", desc[:800], re.I):
            mb = None
        if mb:
            codes.append("M2"); ev["M2"] = mb.group(0)
        elif RE_NOVEL_DESC.search(desc):
            codes.append("M2b"); ev["M2b"] = RE_NOVEL_DESC.search(desc).group(0)
        numbered = bool(RE_VOLNUM.search(title) or RE_VOLNUM_DESC.search(desc) or archive.get("volume") or archive.get("issue"))
        if not numbered:
            codes.append("M3")
        else:
            codes.append("N1"); ev["N1"] = (RE_VOLNUM.search(title) or RE_VOLNUM_DESC.search(desc) or re.search(r".{0,8}", archive.get("volume") or archive.get("issue") or "")).group(0)
        if any(re.fullmatch(r"(?:pulps?|periodicals?|magazines?|.*\bmagazines?)", sj) for sj in subjects):
            codes.append("N3")
        if re.search(r"\bnovels?\b|short stor|\bcollection\b|\banthology\b", subj_text) or archive.get("creator"):
            codes.append("M4"); ev["M4"] = (", ".join(subjects[:4])[:80] or f"creator: {archive.get('creator')}"[:80])
        yrs = [y for y in (_year(archive.get("date")), _year(archive.get("year"))) if y]
        modern = [y for y in yrs if y > 1960]
        modern_t = [int(y) for y in RE_MODERN_YEAR.findall(title + " " + (archive.get("identifier") or ""))]
        if modern:
            codes.append("M5"); ev["M5"] = str(max(modern))
        elif modern_t:
            codes.append("M5b"); ev["M5b"] = str(max(modern_t))
        elif not yrs and (issue.get("undated") or not issue.get("year")) and (RE_MODERN_TAG.search(title) or RE_MODERN_TAG.search(archive.get("identifier") or "")):
            codes.append("M10"); ev["M10"] = (RE_MODERN_TAG.search(title) or RE_MODERN_TAG.search(archive.get("identifier") or "")).group(0)
        if RE_REFERENCE.search(title) or RE_REFERENCE_DESC.search(desc[:300]):
            codes.append("M6"); ev["M6"] = (RE_REFERENCE.search(title) or RE_REFERENCE_DESC.search(desc[:300])).group(0)
        if any(c.startswith("comics") or c.endswith("comics") for c in colls) or RE_COMIC.search(title) or RE_COMIC.search(subj_text):
            codes.append("M7"); ev["M7"] = (RE_COMIC.search(title) or RE_COMIC.search(subj_text) or re.search(r"comics\w*", " ".join(colls))).group(0)
        md_ = RE_DIME.search(title) or RE_DIME.search(name) or RE_DIME_DESC.search(subj_text) or RE_DIME_DESC.search(desc[:300])
        if md_:
            codes.append("M8"); ev["M8"] = md_.group(0)
        mn = RE_NONFICTION.search(title) or RE_NONFICTION.search(name) or RE_NONFICTION.search(desc[:100])
        if mn:
            codes.append("M9"); ev["M9"] = mn.group(0)
        if RE_REPRINT_DESC.search(desc[:600]):
            codes.append("M11"); ev["M11"] = RE_REPRINT_DESC.search(desc[:600]).group(0)[:50]

    if n_in_magazine == 1:
        codes.append("S1")
    if RE_BOOK_NAME.search(name):
        codes.append("S2"); ev["S2"] = RE_BOOK_NAME.search(name).group(0).strip()[:60]
    if RE_SERIES.search(name) or RE_SERIES.search(title):
        codes.append("S3"); ev["S3"] = (RE_SERIES.search(name) or RE_SERIES.search(title)).group(0)
    if RE_MAGAZINE_WORD.search(name) and not RE_BOOK_NAME.search(name) and not RE_SERIES.search(name):
        codes.append("N4"); ev["N4"] = RE_MAGAZINE_WORD.search(name).group(0)
    if assembly:
        undated = issue.get("undated") or not issue.get("year")
        if (undated and not RE_MAGAZINE_WORD.search(name) and assembly["toc_n"] <= 2 and assembly["byline_starts"] <= 1
                and assembly["starts"] <= 3 and 0 < pages < 60):
            codes.append("S4"); ev["S4"] = f"{pages} pages, {assembly['starts']} start(s)"
        if assembly.get("toc_kind") == "chapters" or (assembly["toc_n"] >= 4 and assembly["toc_chapter_titles"] >= 0.6 * assembly["toc_n"]):
            codes.append("A1"); ev["A1"] = f"{assembly['toc_chapter_titles']} of {assembly['toc_n']} entries"
        if assembly["byline_pages"] <= 1 and pages >= 48:
            codes.append("A2"); ev["A2"] = f"{assembly['byline_pages']} by-line page(s) in {pages}"
        if assembly["chapters"] >= 10 and assembly["byline_pages"] <= 3:
            codes.append("A3"); ev["A3"] = f"{assembly['chapters']} chapter heads"
        if assembly["toc_n"] >= 5 and assembly["toc_authors"] == 0:
            codes.append("A4"); ev["A4"] = f"{assembly['toc_n']} entries, no author"
        if assembly["toc_n"] == 0 and assembly["chapters"] >= 5 and assembly["starts"] <= 2 and pages >= 48:
            codes.append("A5"); ev["A5"] = f"no contents page, {assembly['chapters']} chapter heads, {assembly['starts']} start(s)"
        yr = issue.get("year") if not issue.get("undated") else None
        if (0 < pages <= 40 and yr and int(yr) <= 1925 and assembly["chapters"] >= 5 and assembly["toc_n"] <= 2 and assembly["byline_pages"] <= 3):
            codes.append("D1"); ev["D1"] = f"{pages} pages, {yr}, {assembly['chapters']} chapter heads"
        if assembly["byline_pages"] >= 4 and assembly["toc_authors"] >= 3:
            codes.append("N2"); ev["N2"] = f"{assembly['byline_pages']} by-line pages, {assembly['toc_authors']} contents authors"
    if records and pages >= 60 and (records["stories"] <= 1 or records["story_authors"] <= 1):
        codes.append("R1"); ev["R1"] = f"{records['stories']} stories, {records['story_authors']} author(s)"
    return codes, ev


def classify(codes):
    """(class, score, points by class): the class with most points (ties to the more specific class, "book" last); the
    score is its points plus the "any" points, less the magazine evidence. Only "any" points: no class."""
    pts = Counter()
    minus = Counter()
    anyp = 0
    for c in codes:
        cls, p, _ = SIGNALS[c]
        if cls is None:
            for k in AGAINST[c]:
                minus[k] += p
        elif cls == "any":
            anyp += p
        else:
            pts[cls] += p
    if not pts:
        return None, anyp + min(minus.values(), default=0), {}
    order = {c: i for i, c in enumerate(CLASSES)}
    cls = sorted(pts, key=lambda c: (-(pts[c] + minus[c]), order[c]))[0]
    return cls, pts[cls] + anyp + minus[cls], dict(pts)


def proposal(score_):
    if score_ >= SCORE_EXCLUDE:
        return "EXCLUDE"
    if score_ >= SCORE_LOOK:
        return "LOOK"
    return "KEEP"


def run(root, with_records=False, sample_ids=None, only=None, log=print):
    from corpus_lib import set_aside
    cfg = json.load(open(os.path.join(root, "config", "corpus_issues.json"), encoding="utf-8"))
    issues = cfg["issues"]
    aside = set_aside() if root == ROOT else {}
    by_mag = Counter(i.get("magazine") for i in issues)
    sample_ids = sample_ids or {}
    items, t0 = [], time.time()
    for k, i in enumerate(issues, 1):
        if only and i["id"] not in only:
            continue
        if i["id"] in aside:
            continue
        archive = archive_record(root, i["id"])
        assembly = assembly_record(root, i["id"])
        records = records_record(root, i["id"]) if with_records else None
        codes, ev = score(i, by_mag[i.get("magazine")], archive, assembly, records)
        cls, sc, pts = classify(codes)
        prop = proposal(sc) if cls else "KEEP"
        items.append({"id": i["id"], "ia_identifier": i.get("ia_identifier"), "magazine": i.get("magazine"), "ia_title": (archive or {}).get("title"),
                      "cover_date": i.get("cover_date"), "year": i.get("year"), "genre": i.get("genre"), "pages": ev.get("pages"),
                      "issues_in_magazine": by_mag[i.get("magazine")], "in_sample": i["id"] in sample_ids, "sample_order": sample_ids.get(i["id"]),
                      "signals": codes, "evidence": {k2: v for k2, v in ev.items() if k2 != "pages"}, "points": pts, "class": cls, "score": sc,
                      "proposed": prop, "archive_read": archive is not None, "assembly_read": assembly is not None, "records_read": records is not None})
        if k % 1000 == 0:
            log(f"{k} issues, {time.time() - t0:.0f}s")
    return items, {"selected": len(issues), "set_aside_already": len([i for i in issues if i["id"] in aside])}


def write_outputs(items, meta, out_base):
    os.makedirs(os.path.dirname(out_base), exist_ok=True)
    listed = [it for it in items if it["proposed"] != "KEEP" or it["in_sample"]]
    listed.sort(key=lambda it: ({"EXCLUDE": 0, "LOOK": 1, "KEEP": 2}[it["proposed"]], it["class"] or "~", -it["score"], it["id"]))
    counts = {"proposed": Counter(it["proposed"] for it in items), "class_of_proposed_exclude": Counter(it["class"] for it in items if it["proposed"] == "EXCLUDE"),
              "class_of_proposed_look": Counter(it["class"] for it in items if it["proposed"] == "LOOK"),
              "sample_proposed": Counter(it["proposed"] for it in items if it["in_sample"]), "listed": len(listed)}
    rec = {"_comment": "Candidates for exclusion from corpus 1.0 as not magazine issues (scripts/nonmagazine_candidates.py, rule "
                       f"{RULE_VERSION}); a proposal for a person to confirm item by item in the CSV's decision column (x exclude, k keep). "
                       "Scores: EXCLUDE at %d points, LOOK at %d; the signals are in 'signals' (codes explained in 'signal_key')." % (SCORE_EXCLUDE, SCORE_LOOK),
           "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "rule_version": RULE_VERSION, "score_exclude": SCORE_EXCLUDE, "score_look": SCORE_LOOK,
           "signal_key": {c: {"class": v[0], "points": v[1], "says": v[2]} for c, v in SIGNALS.items()},
           "counts": {k: (dict(v) if isinstance(v, Counter) else v) for k, v in counts.items()}, **meta, "items": items}
    json.dump(rec, open(out_base + ".json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    cols = ["decision", "note", "proposed", "class", "score", "in_sample", "sample_order", "id", "magazine", "ia_title", "cover_date", "genre",
            "pages", "issues_in_magazine", "signals", "evidence", "ia_identifier"]
    with open(out_base + ".csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for it in listed:
            w.writerow(["", "", it["proposed"], it["class"] or "", it["score"], "yes" if it["in_sample"] else "", it["sample_order"] or "", it["id"],
                        it["magazine"], it["ia_title"] or "", it["cover_date"] or "", it["genre"] or "", it["pages"] or "", it["issues_in_magazine"],
                        " ".join(it["signals"]), "; ".join(f"{k}: {v}" for k, v in it["evidence"].items()), it["ia_identifier"] or ""])
    md = [f"# Items that may not be magazine issues — review table ({rec['written'][:10]}, rule {RULE_VERSION})", "",
          f"{meta.get('selected', 0):,} selected issues scored; proposed EXCLUDE {counts['proposed'].get('EXCLUDE', 0)}, LOOK {counts['proposed'].get('LOOK', 0)}, "
          f"KEEP {counts['proposed'].get('KEEP', 0)}. Of the verification sample's issues: " + ", ".join(f"{k} {v}" for k, v in sorted(counts["sample_proposed"].items())) + ".",
          "", "Proposed EXCLUDE by class: " + ", ".join(f"{k} {v}" for k, v in counts["class_of_proposed_exclude"].most_common()) + ".",
          "Proposed LOOK by class: " + ", ".join(f"{k} {v}" for k, v in counts["class_of_proposed_look"].most_common()) + ".", "",
          "**How to review.** The CSV beside this file has the same rows with a `decision` column (x = exclude, k = keep, blank = not "
          "decided) and a free `note` column; the filled CSV is what scripts/set_aside_nonmagazine.py reads. The quick way: decide the "
          "EXCLUDE groups class by class (the dime-novel weeklies, the books, the single stories ...) and mark only the exceptions with k; "
          "then go down the LOOK rows and mark x where the item is not a magazine issue (the apply step with --accept-proposed takes an "
          "unmarked EXCLUDE row as excluded and an unmarked LOOK row as kept). An item set aside keeps its files on disk, leaves the site and "
          "the reuse stages, and is listed in corpus 1.0's manifest under its reason; a sample issue set aside has its slot redrawn in the same "
          "decade-and-genre cell. Columns: *class* is the rule's reading of what the item is; *score* its points (EXCLUDE from "
          f"{SCORE_EXCLUDE}, LOOK from {SCORE_LOOK}); *signals* the codes below; *evidence* what each code saw; *sample* the slot in the verification sample.",
          "", "**The signals** (code: what it says — class, points):", ""]
    md += [f"- **{c}** {v[2]} — " + (v[0] if v[0] not in (None, "any") else ("evidence of a magazine issue, against " + ", ".join(sorted(AGAINST[c])) if v[0] is None
                                                                               else "evidence against a magazine issue, counted for whichever class")) + f", {v[1]:+d}" for c, v in SIGNALS.items()]
    md += [""]
    for prop in ("EXCLUDE", "LOOK", "KEEP"):
        rows = [it for it in listed if it["proposed"] == prop]
        if not rows:
            continue
        md += [f"## Proposed {prop} ({len(rows)})" + (" — the verification sample's issues that the rule keeps" if prop == "KEEP" else ""), "",
               "| # | id | magazine (selection) | archive's title | date | genre | pages | class | score | signals | evidence | sample |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for n, it in enumerate(rows, 1):
            evid = "; ".join(f"{k}: {v}" for k, v in it["evidence"].items()).replace("|", "/")
            md.append(f"| {n} | `{it['id']}` | {str(it['magazine']).replace('|', '/')[:60]} | {str(it['ia_title'] or '').replace('|', '/')[:70]} | {it['cover_date'] or ''} | {it['genre'] or ''} | "
                      f"{it['pages'] or ''} | {it['class'] or ''} | {it['score']} | {' '.join(it['signals'])} | {evid[:160]} | {('#%s' % it['sample_order']) if it['in_sample'] else ''} |")
        md.append("")
    open(out_base + ".md", "w", encoding="utf-8").write("\n".join(md))
    return counts


def selftest():
    mag = {"id": "x", "magazine": "Weird Tales", "year": 1937, "undated": False}
    book = {"id": "b", "magazine": "The Plattner Story And Others - H. G. Wells (Methuen", "year": 1897, "undated": False}
    arch_book = {"title": "The Plattner Story And Others - H. G. Wells (Methuen, 1897)", "description": "Scan of the UK First Edition ... (Methuen, 1897, 6/-, vii+301pp, hc)",
                 "subjects": ["science fiction", "short stories", "h. g. wells"], "collections": ["pulp_misc_sf"], "date": "1897", "year": "1897", "files": ["Wells, H. G. - The Plattner Story And Others (Methuen, 1897).pdf"], "imagecount": 320}
    asm_book = {"pages": 320, "toc_n": 17, "toc_kind": "pieces", "toc_authors": 0, "toc_chapter_titles": 0, "starts": 20, "byline_pages": 1, "byline_starts": 0, "chapters": 4}
    codes, ev = score(book, 1, arch_book, asm_book)
    assert {"M1", "M2", "M3", "M4", "S1", "S2", "A2", "A4"} <= set(codes) and "N3" not in codes, codes
    cls, sc, _ = classify(codes)
    assert cls == "book" and proposal(sc) == "EXCLUDE", (cls, sc)
    arch_mag = {"title": "Weird Tales v30 n06 [1937-12]", "description": "CONTENTS: Weird Tales [v30 #6, December 1937] ...", "subjects": ["magazine", "pulp"],
                "collections": ["weirdtalesmagazine"], "date": "1937-12", "year": "1937", "files": ["Weird Tales v30 n06 [1937-12].pdf"], "imagecount": 132}
    asm_mag = {"pages": 132, "toc_n": 14, "toc_kind": "pieces", "toc_authors": 13, "toc_chapter_titles": 0, "starts": 14, "byline_pages": 13, "byline_starts": 13, "chapters": 6}
    codes, ev = score(mag, 40, arch_mag, asm_mag)
    cls, sc, _ = classify(codes)
    assert "N1" in codes and "N2" in codes and "N3" in codes and proposal(sc) == "KEEP", (codes, sc)
    # a novel whose contents page is a chapter list (Silken Threads, 1890)
    novel = {"id": "s", "magazine": "Silken Threads", "year": 1890, "undated": False}
    asm_novel = {"pages": 300, "toc_n": 31, "toc_kind": None, "toc_authors": 0, "toc_chapter_titles": 31, "starts": 31, "byline_pages": 0, "byline_starts": 0, "chapters": 59}
    codes, ev = score(novel, 1, None, asm_novel)
    cls, sc, _ = classify(codes)
    assert {"A1", "A2", "A3", "A4", "S1"} <= set(codes) and proposal(sc) == "EXCLUDE", (codes, sc)
    # a dime-novel library number
    dime = {"id": "d", "magazine": "Beadles Half Dime Library 1020", "year": 1897, "undated": False}
    codes, ev = score(dime, 1, {"title": "Beadle's Half Dime Library 1020 - 1897", "description": "", "subjects": ["dime novel"], "collections": [], "date": "1897", "files": [], "imagecount": 32}, None)
    cls, sc, _ = classify(codes)
    assert cls == "dime novel" and proposal(sc) == "EXCLUDE", (cls, sc, codes)
    # a nickel weekly with a volume-and-number title: the numbering does not count against a dime novel
    weekly = {"id": "w", "magazine": "Old Sleuth Weekly", "year": 1908, "undated": False}
    asm_weekly = {"pages": 36, "toc_n": 0, "toc_kind": "pieces", "toc_authors": 0, "toc_chapter_titles": 0, "starts": 2, "byline_pages": 3, "byline_starts": 2, "chapters": 46}
    codes, ev = score(weekly, 2, {"title": "Old Sleuth Weekly v01 n02 [1908-05-01]", "description": "Nickel Weekly", "subjects": ["magazine"], "collections": [], "date": "1908-05-01", "files": [], "imagecount": 36}, asm_weekly)
    cls, sc, _ = classify(codes)
    assert {"N1", "N3", "M8", "D1"} <= set(codes) and cls == "dime novel" and proposal(sc) == "EXCLUDE", (codes, cls, sc)
    # a reprinted pulp novel with a chapter list for its contents (The Shadow #1, 1931 text in a later edition)
    shadow = {"id": "sh", "magazine": "The Shadow", "year": 1931, "undated": False}
    asm_shadow = {"pages": 252, "toc_n": 0, "toc_kind": "chapters", "toc_authors": 0, "toc_chapter_titles": 0, "starts": 0, "byline_pages": 3, "byline_starts": 0, "chapters": 74}
    codes, ev = score(shadow, 1, {"title": "The Shadow #1 : The Living Shadow", "description": "The Shadow by Maxwell Grant #1 - The Living Shadow. Originally published in The Shadow Magazine, April/June 1931",
                                  "subjects": ["comic books and graphic novels"], "collections": ["pulp_misc_mystery"], "date": "1931", "files": [], "imagecount": 252}, asm_shadow)
    cls, sc, _ = classify(codes)
    assert {"A1", "A3", "A5", "M11"} <= set(codes) and cls == "book" and proposal(sc) == "EXCLUDE", (codes, cls, sc)
    # a magazine whose archive description gives a page count ("96pp, pulp") is not a book by that
    codes, ev = score({"id": "at", "magazine": "Air Trails", "year": 1937}, 12, {"title": "Air Trails v07n05 [1937-02]", "description": "CONTENTS: Air Trails [v7 #5, February 1937] (Street & Smith, 15c, 96pp, pulp) 4 · This Winged World",
                                  "subjects": ["pulps", "aviation pulps"], "collections": [], "date": "1937-02", "files": [], "imagecount": 100}, None)
    assert "M2" not in codes and "N1" in codes and "N3" in codes, codes
    # a single undated story upload
    single = {"id": "g", "magazine": "A Gift for Terra", "year": None, "undated": True}
    asm_single = {"pages": 24, "toc_n": 0, "toc_kind": None, "toc_authors": 0, "toc_chapter_titles": 0, "starts": 1, "byline_pages": 1, "byline_starts": 1, "chapters": 0}
    codes, ev = score(single, 1, {"title": "A Gift for Terra", "description": "", "subjects": [], "collections": ["pulp_misc_sf"], "date": "", "files": [], "imagecount": 24}, asm_single)
    cls, sc, _ = classify(codes)
    assert "S4" in codes and cls in ("single story", "book"), (codes, cls)
    # a magazine with one issue in the selection and no archive record stays KEEP on S1 alone
    codes, ev = score({"id": "o", "magazine": "Action Caravan", "year": 1951}, 1, None, {"pages": 130, "toc_n": 9, "toc_kind": "pieces", "toc_authors": 9, "toc_chapter_titles": 0, "starts": 9, "byline_pages": 9, "byline_starts": 9, "chapters": 2})
    cls, sc, _ = classify(codes)
    assert proposal(sc) == "KEEP", (codes, sc)
    print("nonmagazine_candidates selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT, help="the project root whose data/ and config/ to read (default: this checkout)")
    ap.add_argument("--records", action="store_true", help="also read articles.json for the record signals (R1)")
    ap.add_argument("--out", help="output base path without extension (default: <root>/data/corpus/p52-2026-10-16/nonmagazine_candidates)")
    ap.add_argument("--only", help="a file of issue ids to score (a test run)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    sample = {}
    try:
        S = json.load(open(os.path.join(ROOT, "config", "verification_sample.json"), encoding="utf-8"))
        sample = {i["id"]: i["order"] for i in S["issues"]}
    except (OSError, ValueError):
        pass
    only = set(l.strip() for l in open(args.only) if l.strip()) if args.only else None
    items, meta = run(args.root, args.records, sample, only)
    out_base = args.out or os.path.join(args.root, OUT_DIR, OUT_NAME)
    counts = write_outputs(items, meta, out_base)
    print(f"{meta['selected']:,} selected; proposed: " + ", ".join(f"{k} {v}" for k, v in sorted(counts["proposed"].items())))
    print("EXCLUDE by class: " + ", ".join(f"{k} {v}" for k, v in counts["class_of_proposed_exclude"].most_common()))
    print("LOOK by class: " + ", ".join(f"{k} {v}" for k, v in counts["class_of_proposed_look"].most_common()))
    print("sample: " + ", ".join(f"{k} {v}" for k, v in sorted(counts["sample_proposed"].items())))
    print("written", out_base + ".json", ".csv", ".md")


if __name__ == "__main__":
    main()
