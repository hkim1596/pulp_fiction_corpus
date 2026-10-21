"""The book mode of the assembler (rules 2.8, book mode 1.0; Sujin Kang's decision of 21 October 2026: items of kind
"book" stay in the data, labelled, and are assembled as books).

The rules engine (s08_assemble_rules.py) reads a magazine: a piece starts where a display title stands over a by-line
or where the contents page says, and a chapter head inside a piece is a chapter head. A standalone book has no
by-lines and its contents page is a list of chapters, so the engine cuts a novel into one "story" per chapter and
types the pieces of a collection as features. This module is a second pass over the engine's finished records for an
item of kind "book" (config/corpus_kinds.json):

  novel      — the contents page is a chapter list (toc_kind "chapters", or most entries carry a chapter number), or
               there is no contents page: every reading record from the first chapter on becomes ONE story record
               titled with the book's title and author, chapter heads kept as chapter roles; what comes before the
               first chapter (title page, dedication, contents) is one "other" record, the front matter;
  collection — the contents page lists pieces: one record per listed piece, its boxes being every reading record from
               the piece's first page up to the next piece's; the author is the entry's or the book's.

Advertisements, house matter and the publisher's catalogue at the back stay as the engine typed them; a reading record
after the body that reads like a publisher's list (prices, "net", "crown 8vo") becomes house matter. The book's title
and author come from the archive's record (its title "Title - Author (Publisher, Year)" or "Author - Title", its
creator) or the selection's name. Nothing here touches a magazine.
"""
import re
from collections import Counter

BOOK_MODE = "1.0"
READING = ("story", "feature", "poem", "letters", "other")
CHAPTER_TITLE = re.compile(r"^\s*(?:chapter|chap\.?|part|book)\s+[\divxlc]+\b|^\s*[IVXLC]{1,6}\s*[.:\-—]\s*\S|^\s*\d{1,2}\s*[.:]\s+[A-Z]", re.I)
PUBLISHER_LIST = re.compile(r"\b(?:crown 8vo|demy 8vo|fcap\.? 8vo|\d+s\.\s*(?:\d+d\.)?\s*net|post free|publishers?'? (?:list|catalogue)|"
                            r"messrs\.|list of (?:new )?books|now ready|uniform with|other books by|\d+ illustrations|second edition|third edition)\b", re.I)
NAME = re.compile(r"^(?:[A-Z][\w'\-]*\.?\s+){0,3}[A-Z][\w'\-]+$")
SURNAME_FIRST = re.compile(r"^[A-Z][\w'\-]+,\s+[A-Z][\w.\s'\-]{0,30}$")


def _is_name(s):
    s = (s or "").strip()
    return bool(s) and 2 <= len(s.split()) <= 4 and (bool(NAME.match(s)) or bool(SURNAME_FIRST.match(s))) and not re.search(r"\b(?:the|of|and|a|in|stories|tales|novel)\b", s, re.I)


def book_title_author(name=None, archive_title=None, creator=None):
    """(title, author) of a book from what the archive and the selection say: 'The Plattner Story And Others - H. G.
    Wells (Methuen, 1897)', 'Errol Flynn - Showdown (1946)', 'Wells, H. G. - The Island Of Dr. Moreau', 'Death at Sea,
    by Richard Sale', 'Blood Brother By Elliott Arnold'; else the name and the archive's creator."""
    cands = [t for t in (archive_title, name) if t]
    author = (creator or "").strip() or None
    for t in cands:
        s = re.sub(r"\s*[\(\[].*$", "", t).strip()              # drop "(Methuen, 1897)" / "[1946]"
        m = re.match(r"^(.*?)[,]?\s+[Bb]y\s+([A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3})\s*$", s)
        if m:
            return m.group(1).strip(" ,.-"), (m.group(2).strip() if not author else author)
        parts = re.split(r"\s+[-–—]\s+", s, maxsplit=1)
        if len(parts) == 2:
            a, b = parts[0].strip(), parts[1].strip()
            if author:                                        # the archive names the author: a part that is the author is the author
                if _norm(a) == _norm(author):
                    return b.strip(" ,.-"), author
                if _norm(b) == _norm(author):
                    return a.strip(" ,.-"), author
                return s.strip(" ,.-"), author
            if _is_name(b) and not _is_name(a):
                return a.strip(" ,.-"), author or b
            if _is_name(a) and not _is_name(b):
                if SURNAME_FIRST.match(a):                        # "Wells, H. G."
                    sur, rest = a.split(",", 1)
                    a = f"{rest.strip()} {sur.strip()}"
                return b.strip(" ,.-"), author or a
        if s:
            return s.strip(" ,.-"), author
    return (name or "").strip() or None, author


def book_kind_of_contents(A):
    """'novel' or 'collection' from the analysis: a chapter list, mostly chapter-numbered entries or no contents is a
    novel; a list of two or more pieces with their own titles is a collection."""
    toc = [e for e in (A.get("toc") or []) if e.get("title")]
    if A.get("toc_kind") == "chapters" or not toc:
        return "novel"
    chap = sum(1 for e in toc if CHAPTER_TITLE.match(str(e.get("title") or "")))
    if len(toc) >= 2 and chap < 0.6 * len(toc):
        return "collection"
    return "novel"


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


SMALL = {"a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "by", "or", "nor", "with", "from", "as", "but"}


def _title_case(t):
    words = t.lower().split()
    return " ".join(w if (k and w in SMALL) else (w[:1].upper() + w[1:]) for k, w in enumerate(words))


def _first_page(r):
    return r["fragments"][0]["page"] if r.get("fragments") else (r.get("pages") or [None])[0]


def _merge(into, recs, pages=None):
    """Fold the records into one: fragments in page order, roles and chapters joined, texts joined."""
    frags = {}
    order = []
    for r in [into] + recs:
        for f in r.get("fragments", []):
            if f["page"] not in frags:
                order.append(f["page"])
            frags.setdefault(f["page"], []).extend(f["region_ids"])
        into["roles"].update(r.get("roles") or {})
        if r is not into:
            if r.get("chapters"):
                into["chapters"] = (into.get("chapters") or []) + r["chapters"]
            into["flags"] = (into.get("flags") or []) + [f for f in (r.get("flags") or []) if "book mode" not in f]
    order.sort()
    into["fragments"] = [{"page": p, "region_ids": sorted(set(frags[p]))} for p in order]
    into["pages"] = order
    into["n_regions"] = sum(len(f["region_ids"]) for f in into["fragments"])
    texts = [t for t in [into.get("text")] + [r.get("text") for r in recs] if t]
    into["text"] = "\n\n".join(texts)
    return into


PUBLISHER_TITLE = re.compile(r"\b(?:messrs|publishers?|catalogue|series|classics|library of|list of|books by|new books|now ready)\b", re.I)


def _looks_like_publishers_list(r):
    """A publisher's catalogue read as a piece: prices and formats several times per thousand words (a novel's text
    mentions "cloth" or "net" too, but not at that rate), or a catalogue's title."""
    t = r.get("text") or ""
    n = len(t.split())
    if n < 40:
        return False
    hits = len(PUBLISHER_LIST.findall(t))
    return (hits * 1000 / n >= 2.0 and hits >= 3) or (bool(PUBLISHER_TITLE.search(r.get("title") or "")) and hits >= 2)


def _scan_of(e, A, pages=None):
    """The scan page where a contents entry's piece begins (what run_issue writes later as e['scan'])."""
    sc = e.get("scan") or e.get("scan_linked") or e.get("scan_page")
    if sc is None and A.get("scan_of") and A.get("offset") is not None and e.get("page") is not None:
        try:
            sc = A["scan_of"](e["page"])
        except Exception:
            sc = None
    if pages is not None and sc is not None and sc not in pages:
        return None
    return sc


def book_mode(iid, records, A, title, author, pages=None):  # noqa: C901
    """The second pass (see the module's docstring). records: the engine's finished records (fragments, roles, text).
    Returns the new record list, renumbered in page order, and a summary dict."""
    kind = book_kind_of_contents(A)
    reading = sorted([r for r in records if r["type"] in READING], key=lambda r: (_first_page(r) or 0, r["article_id"]))
    others = [r for r in records if r["type"] not in READING]
    toc_page = A.get("toc_page") or 0
    if not reading:
        return records, {"book_mode": BOOK_MODE, "kind": kind, "pieces": 0, "merged": 0}
    # the publisher's list at the back, typed as reading matter by the engine
    tail = []
    starts = {_scan_of(e, A, pages) for e in A.get("toc", []) if e.get("title")} - {None}
    for r in list(reading):
        if _looks_like_publishers_list(r) and _first_page(r) not in starts:      # never the holder of a listed piece
            r["type"] = "house"
            r["ad_class"] = "house_publisher_list"
            r["flags"] = (r.get("flags") or []) + ["book mode: a publisher's list at the back, typed house"]
            reading.remove(r); others.append(r); tail.append(r)
    if not reading:
        return records, {"book_mode": BOOK_MODE, "kind": kind, "pieces": 0, "merged": 0}
    if kind == "novel":
        # the body starts at the first reading record after the contents page that has a chapter head, else at the
        # first reading record after the contents page, else at the first reading record
        with_chapter = [r for r in reading if (_first_page(r) or 0) > toc_page and
                        (any(v == "chapter" for v in (r.get("roles") or {}).values()) or r.get("chapters"))]
        after_toc = [r for r in reading if (_first_page(r) or 0) > toc_page]
        start = (with_chapter or after_toc or reading)[0]
        pieces = [{"start": _first_page(start), "title": title, "author": author, "type": "story", "toc": None}]
    else:
        entries = [(_scan_of(e, A, pages), e) for e in A.get("toc", []) if e.get("title")]
        entries = sorted([(sc, e) for sc, e in entries if sc], key=lambda x: x[0])
        pieces = [{"start": sc, "title": e["title"], "author": e.get("author") or author,
                   "type": "poem" if e.get("type") == "poem" else "story", "toc": e} for sc, e in entries]
        if not pieces:
            pieces = [{"start": _first_page(reading[0]), "title": title, "author": author, "type": "story", "toc": None}]
    front, groups = [], [[] for _ in pieces]
    for r in reading:
        p = _first_page(r) or 0
        idx = max((k for k, pc in enumerate(pieces) if pc["start"] <= p), default=None)
        if idx is None:
            front.append(r)
        else:
            groups[idx].append(r)
    out = []
    if front:
        fm = front[0]
        fm.update(type="other", title="Front matter", author=None, toc=None)
        _merge(fm, front[1:])
        fm["flags"] = (fm.get("flags") or []) + [f"book mode: {len(front)} record(s) before the first piece joined as the front matter"]
        out.append(fm)
    merged = 0
    for pc, grp in zip(pieces, groups):
        if not grp:
            continue
        head = grp[0]
        t = pc["title"]
        if _norm(head.get("title")) == _norm(t):
            t = head["title"]                                # the engine's reading of the printed head (cased as printed)
        elif t == t.upper():
            t = _title_case(t)
        head.update(type=pc["type"], title=t, author=pc["author"], toc=pc["toc"], work_title=t)
        head["title_source"], head["author_source"] = "book", "book"
        _merge(head, grp[1:])
        merged += len(grp) - 1
        head["flags"] = (head.get("flags") or []) + [f"book mode ({kind}): {len(grp)} record(s) joined as one piece; title and author from the book's record"]
        out.append(head)
    out += others
    out.sort(key=lambda r: ((_first_page(r) or 0), r["article_id"]))
    for k, r in enumerate(out, 1):
        r["article_id"] = f"{iid}_a{k:03d}"
    return out, {"book_mode": BOOK_MODE, "kind": kind, "pieces": sum(1 for g in groups if g), "merged": merged,
                 "front_matter": len(front), "publisher_list": len(tail)}


def selftest():
    assert book_title_author(archive_title="The Plattner Story And Others - H. G. Wells (Methuen, 1897)") == ("The Plattner Story And Others", "H. G. Wells")
    assert book_title_author(archive_title="Errol Flynn - Showdown (1946)") == ("Showdown", "Errol Flynn")
    assert book_title_author(archive_title="Wells, H. G. - The Island Of Dr. Moreau") == ("The Island Of Dr. Moreau", "H. G. Wells")
    assert book_title_author(name="Death at Sea, by Richard Sale") == ("Death at Sea", "Richard Sale")
    assert book_title_author(name="Blood Brother By Elliott Arnold") == ("Blood Brother", "Elliott Arnold")
    assert book_title_author(name="Silken Threads", creator="Harold William Gardner") == ("Silken Threads", "Harold William Gardner")
    assert book_kind_of_contents({"toc_kind": "chapters", "toc": []}) == "novel"
    assert book_kind_of_contents({"toc": [{"title": "I. SUNSET"}, {"title": "II. THE HEIR"}, {"title": "III. JUDD"}]}) == "novel"
    assert book_kind_of_contents({"toc": [{"title": "THE PLATTNER STORY"}, {"title": "IN THE ABYSS"}]}) == "collection"
    print("s08_book selftest OK")


if __name__ == "__main__":
    selftest()
