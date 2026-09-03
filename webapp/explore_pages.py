"""The explorer (service side of the site, v0.12.0): every piece of data
the server holds, in layers, each layer one click from the next and the
last layer the raw record itself.

  Layer 0  /overview      charts by decade and genre that point at the
                          findings; every element of every chart is a
                          link into layer 1 or 2
  Layer 1  /authors /magazines /issues /stories /pairs /reuse/clusters
                          lists with filters, 100 rows a page
  Layer 2  /author/<key> /magazine/<slug> /issue/<id> /story/<id>
           /pair/<a>/<b> /reuse/cluster/...     one entity, all its facts
  Layer 3  the article workbench and the scan viewer (the printed page),
           and /raw/... : the JSON records every page above was built
           from, with the file each came from; the same records are
           served to the read-only data door (/api/<token>/...).

Built for the whole corpus, not only the pilot: the pages read from one
SQLite file, data/explorer.sqlite, which this module builds from the files
under data/ whenever one of them changes (or by hand:
``python3 webapp/explore_pages.py --build``). Lists are paged; charts are
drawn from counts, and the drawings that show individual entities (the
author ring, the magazine grid, the issue time axis) are drawn only when
the chosen slice is small enough to read, otherwise a table of the top
entries stands in. Only complete issues — assembled into records by the
machine — appear on the explorer side; the workroom progress page shows
every issue at every step. No framework, no JavaScript libraries; charts
are inline SVG.
"""
import csv
import glob
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from collections import defaultdict, Counter

import reuse_pages as RP

_G = {}
_DB = {"path": None, "sig": None, "checked": 0.0}
BUILD_LOCK = threading.Lock()
ARCHIVE_TOTAL = 27973          # items in the archive's pulp collection, protocol count (the survey, data/survey/summary.json, supersedes it when present)
PER_PAGE = 100                 # rows per page in every list
DRAW_LIMITS = {"authors": 80, "magazines": 40, "issues": 120}   # entity drawings above this become tables
CHECK_EVERY = 20               # seconds between looks at the source files


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)
    _DB["path"] = os.path.join(_G["DATA"], "explorer.sqlite")


def _esc(s):
    return _G["esc"](s)


def _howto(t):
    return _G["howto"](t)


def _render(render, title, body, path):
    return (render or _G["page"])(title, body, path=path)


def _fmt(v):
    return RP._fmt(v)


def _n(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,}" if isinstance(v, int) else _fmt(v)


def _j(s, default=None):
    """A JSON column back to a Python value."""
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


# ---------------------------------------------------------------- slugs and names

def author_slug(key):
    return key.replace(" ", "-")


def author_unslug(slug):
    return slug.replace("-", " ")


def mag_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


MAG_ABBR = {"Weird Tales": "WT", "Astounding Stories of Super-Science": "AST",
            "Galaxy Science Fiction": "GAL", "Thrilling Detective": "TD",
            "Western Story Magazine": "WS", "Wild West Weekly": "WWW"}


def mag_abbr(name):
    if name in MAG_ABBR:
        return MAG_ABBR[name]
    return "".join(w[0] for w in (name or "").split() if w[0].isalpha()).upper()[:4]


PARTICLES = {"de", "la", "le", "van", "von", "der", "den", "du", "da", "di", "del", "y", "of", "the", "and"}


def display_author(printed):
    """A printed by-line in title case for display: 'H. G. WELLS' -> 'H. G. Wells',
    'ray cummings' -> 'Ray Cummings', 'VICTOR ROUSSEAU' -> 'Victor Rousseau'.
    Particles stay lower-case after the first word, initials stay upper-case,
    'Mc' and apostrophe names keep their inner capital. The printed form itself
    is kept in the record; this is only how the name is shown."""
    s = re.sub(r"\s+", " ", (printed or "")).strip()
    if not s:
        return ""

    def cap(part):
        if not part:
            return part
        low = part.lower()
        if len(low) > 3 and low.startswith("mc"):
            return "Mc" + low[2].upper() + low[3:]
        return low[0].upper() + low[1:]
    out = []
    for i, w in enumerate(s.split(" ")):
        low = w.lower()
        if i > 0 and low in PARTICLES:
            out.append(low)
        elif re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]?", w):
            out.append(w.upper())
        else:
            out.append("-".join("'".join(cap(p) for p in h.split("'")) for h in w.split("-")))
    return " ".join(out)


SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "2nd", "3rd"}


def last_name(display):
    """The surname to sort by: the last word that is not a suffix (Jr., III);
    a particle before it stays with it ('van Vogt' sorts under V, as the
    alphabet of the pulps' own indexes has it)."""
    ws = [w for w in (display or "").replace(",", " ").split() if w]
    while ws and ws[-1].lower() in SUFFIXES:
        ws.pop()
    if not ws:
        return ""
    last = ws[-1]
    if len(ws) >= 2 and ws[-2].lower().rstrip(".") in ("st", "ste", "de", "van", "von", "la", "le", "du", "del", "della", "der", "den", "di", "da", "mac", "mc", "o"):
        last = ws[-2] + " " + last                  # 'St. Clair', 'van Vogt', 'de Camp' sort as one name
    return re.sub(r"[^A-Za-z' \-]", "", last).lower()


# ---------------------------------------------------------------- sources

def _sources():
    """(path, mtime, size) of every file or directory the database depends on."""
    D = _G["DATA"]
    files = [os.path.join(D, "pilot_stories.jsonl"), _G["CONFIG"], os.path.join(D, "survey", "summary.json"),
             os.path.join(D, "reuse", "machine_region_overlap.json"),
             os.path.join(D, "reuse", "background", "pairs_machine.csv.gz"),
             os.path.join(D, "reuse", "background", "summary_machine.json"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline", "publishers.json")]
    files += glob.glob(os.path.join(D, "reuse", "machine_k*_matches.jsonl"))
    files += glob.glob(os.path.join(D, "reuse", "machine_k*_sameissue.jsonl"))
    files += glob.glob(os.path.join(D, "reuse", "para", "machine_w50s25_k10_*.jsonl"))
    files += glob.glob(os.path.join(D, "annotations", "*.jsonl"))
    files += glob.glob(os.path.join(D, "raw", "*", "meta.json"))
    files += glob.glob(os.path.join(D, "articles", "*", "articles.json"))
    # directories whose contents mark a stage of the process (their mtime moves when files are added)
    files += glob.glob(os.path.join(D, "pages", "*"))
    files += glob.glob(os.path.join(D, "layout", "*"))
    files += glob.glob(os.path.join(D, "text", "*", "*"))
    out = []
    for f in sorted(set(files)):
        try:
            st = os.stat(f)
        except OSError:
            continue
        out.append((os.path.normpath(f), st.st_mtime, st.st_size))
    return tuple(out)


def _signature(sig):
    return hashlib.sha1(json.dumps([(os.path.relpath(p, _G["ROOT"]), m, s) for p, m, s in sig]).encode("utf-8")).hexdigest()


def _year(cover_date):
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?", cover_date or "")
    if not m:
        return None
    return int(m.group(1)) + (int(m.group(2) or 6) - 1) / 12


def _decade(year):
    return int(year // 10 * 10) if year else None


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE issues(id TEXT PRIMARY KEY, magazine TEXT, mag_slug TEXT, cover_date TEXT, year REAL, decade INTEGER,
  genre TEXT, format TEXT, ia_identifier TEXT, why TEXT, gold INTEGER, publisher TEXT, publisher_group TEXT,
  publisher_source TEXT, ia TEXT, ia_item_size INTEGER, ia_files TEXT, records INTEGER, stories INTEGER,
  words INTEGER, n_authors INTEGER, verified INTEGER, modified INTEGER, pages INTEGER, layout_pages INTEGER,
  text_stages TEXT, assembled INTEGER, exported INTEGER, events INTEGER, complete INTEGER);
CREATE TABLE records(id TEXT PRIMARY KEY, issue TEXT, magazine TEXT, mag_slug TEXT, cover_date TEXT, year REAL,
  decade INTEGER, genre TEXT, format TEXT, type TEXT, title TEXT, author TEXT, display_author TEXT, author_key TEXT,
  teaser TEXT, date TEXT, date_source TEXT, pages TEXT, first_page INTEGER, status TEXT, verified_by TEXT,
  modified_by TEXT, n_regions INTEGER, fragments TEXT, n_words INTEGER, text_sha1 TEXT, is_story INTEGER,
  n_exact INTEGER, n_para INTEGER, ad_class TEXT, advertiser TEXT, contains_excerpt INTEGER, excerpt_of TEXT,
  n_chapters INTEGER, chapters TEXT, announces TEXT, title_as_printed TEXT, author_as_printed TEXT, subtitle TEXT,
  title_source TEXT, author_source TEXT, flags TEXT);
CREATE INDEX rec_adclass ON records(ad_class);
CREATE INDEX rec_issue ON records(issue);
CREATE INDEX rec_author ON records(author_key);
CREATE INDEX rec_story ON records(is_story, year);
CREATE TABLE authors(key TEXT PRIMARY KEY, slug TEXT, display TEXT, names TEXT, n_stories INTEGER, n_words INTEGER,
  n_issues INTEGER, magazines TEXT, genres TEXT, first_year REAL, last_year REAL, degree INTEGER, last_name TEXT);
CREATE INDEX au_last ON authors(last_name);
CREATE INDEX au_stories ON authors(n_stories);
CREATE TABLE author_links(ka TEXT, kb TEXT, n INTEGER, longest INTEGER, pairs TEXT, stories TEXT);
CREATE INDEX al_a ON author_links(ka);
CREATE INDEX al_b ON author_links(kb);
CREATE TABLE magazines(name TEXT PRIMARY KEY, slug TEXT, genre TEXT, format TEXT, publisher_group TEXT,
  publishers TEXT, issues TEXT, n_issues INTEGER, records INTEGER, stories INTEGER, words INTEGER,
  n_authors INTEGER, verified INTEGER, first_year REAL, last_year REAL);
CREATE TABLE mag_links(ma TEXT, mb TEXT, n INTEGER, longest INTEGER, pairs TEXT);
CREATE TABLE issue_links(ia TEXT, ib TEXT, n INTEGER);
CREATE TABLE matches(k INTEGER, idx INTEGER, a TEXT, b TEXT, len INTEGER, a_issue TEXT, b_issue TEXT,
  excerpt TEXT, same_issue INTEGER, cause TEXT, raw TEXT);
CREATE INDEX m_a ON matches(k, a);
CREATE INDEX m_b ON matches(k, b);
CREATE INDEX m_len ON matches(k, same_issue, len);
CREATE TABLE aligns(idx INTEGER, a TEXT, b TEXT, cols INTEGER, identity REAL, score REAL, text_a TEXT, text_b TEXT, raw TEXT);
CREATE INDEX ag_a ON aligns(a);
CREATE INDEX ag_b ON aligns(b);
CREATE TABLE events(ts TEXT, user TEXT, issue TEXT, article_id TEXT, action TEXT, raw TEXT);
CREATE INDEX ev_art ON events(article_id);
CREATE INDEX ev_issue ON events(issue);
"""

PAIR_TEXT_COLS = {"a", "b", "issue_a", "issue_b", "magazine_a", "magazine_b", "publisher_a", "publisher_b",
                  "author_a", "author_b", "years_band", "exact_excerpt", "para_excerpt", "genre_a", "genre_b"}


def _jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _json(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def _num(v):
    if v in ("", None):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def build_db(sig, path, log=None):
    """Build the explorer database from the files under data/ into path
    (written beside it first, then moved into place)."""
    from r01_normalize import author_key
    D = _G["DATA"]
    t0 = time.time()
    say = log or (lambda *a: None)
    tmp = path + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.executescript(SCHEMA)
    cfg = _G["cfg"]()
    pubs = (_json(os.path.join(_G["ROOT"], "pipeline", "publishers.json")) or {}).get("issues", {})
    # ---- issues from the config, with archive metadata and the state of every stage
    issues = {}
    for i in cfg.get("issues", []):
        iid = i["id"]
        meta = _json(os.path.join(D, "raw", iid, "meta.json")) or {}
        md = meta.get("metadata", {}) if isinstance(meta, dict) else {}
        try:
            pages = len(_G["pages_of"](iid))
        except Exception:
            pages = 0
        layout_pages = len(glob.glob(os.path.join(D, "layout", iid, "page_*.json")))
        try:
            stages = _G["stages_of"](iid)
        except Exception:
            stages = []
        try:
            arts = _G["articles_of"](iid)
        except Exception:
            arts = None
        n_assembled = len(arts.get("articles", arts) if isinstance(arts, dict) else (arts or [])) if arts else 0
        y = _year(i.get("cover_date"))
        issues[iid] = {
            "id": iid, "magazine": i.get("magazine"), "mag_slug": mag_slug(i.get("magazine")),
            "cover_date": i.get("cover_date"), "year": y, "decade": _decade(y), "genre": i.get("genre"),
            "format": i.get("format"), "ia_identifier": i.get("ia_identifier"), "why": i.get("why"),
            "gold": 1 if i.get("gold") else 0,
            "publisher": pubs.get(iid, {}).get("publisher"), "publisher_group": pubs.get(iid, {}).get("publisher_group"),
            "publisher_source": pubs.get(iid, {}).get("source"),
            "ia": {k: md.get(k) for k in ("title", "uploader", "addeddate", "publicdate", "scanner", "imagecount",
                                          "collection", "ocr", "identifier-ark", "description", "contributor",
                                          "source", "year")},
            "ia_item_size": meta.get("item_size") if isinstance(meta, dict) else None,
            "ia_files": sorted({f.get("format") for f in (meta.get("files") or []) if f.get("format")}) if isinstance(meta, dict) else [],
            "downloaded": 1 if md else 0,
            "records": 0, "stories": 0, "words": 0, "authors": set(), "verified": 0, "modified": 0,
            "pages": pages, "layout_pages": layout_pages, "text_stages": stages, "assembled": n_assembled,
            "exported": 0, "events": 0, "complete": 0,
        }
    # ---- records from the export
    authors = {}
    n_rec = 0
    rec_rows = []
    story_meta = {}       # id -> (issue, author_key, magazine, genre, decade, year)
    for r in _jsonl(os.path.join(D, "pilot_stories.jsonl")):
        sid = r["story_id"]
        iss = issues.get(r["issue"])
        y = _year(r.get("cover_date"))
        key = author_key(r.get("author"))
        is_story = 1 if (r.get("type") in ("story", "serial_part")) else 0
        pages = r.get("pages") or []
        typ = r.get("type") or "other"
        rec_rows.append((sid, r["issue"], r.get("magazine"), mag_slug(r.get("magazine")), r.get("cover_date"), y, _decade(y),
                         r.get("genre"), r.get("format"), typ, r.get("title"), r.get("author"),
                         display_author(r.get("author")), key, r.get("teaser"), r.get("date") or r.get("cover_date"),
                         r.get("date_source") or "issue", json.dumps(pages), (pages[0] if pages else None),
                         r.get("status", "auto"), r.get("verified_by"), json.dumps(r.get("modified_by") or []),
                         len(r.get("fragments") or []), json.dumps(r.get("fragments") or []), r.get("n_words", 0),
                         r.get("text_sha1"), is_story,
                         r.get("ad_class"), r.get("advertiser"), 1 if r.get("contains_excerpt") else 0,
                         (json.dumps(r["excerpt_of"], ensure_ascii=False) if isinstance(r.get("excerpt_of"), dict) else (r.get("excerpt_of") or None)),
                         len(r.get("chapters") or []), json.dumps(r.get("chapters") or [], ensure_ascii=False),
                         json.dumps(r.get("announces") or [], ensure_ascii=False), r.get("title_as_printed"), r.get("author_as_printed"),
                         r.get("subtitle"), r.get("title_source"), r.get("author_source"), json.dumps(r.get("flags") or [], ensure_ascii=False)))
        n_rec += 1
        story_meta[sid] = (r["issue"], key, r.get("magazine"), r.get("genre"), _decade(y), y, is_story)
        if iss is not None:
            iss["records"] += 1
            iss["exported"] += 1
            if is_story:
                iss["stories"] += 1
                iss["words"] += r.get("n_words", 0)
                if key:
                    iss["authors"].add(key)
                if r.get("status") == "verified":
                    iss["verified"] += 1
                elif r.get("status") == "modified":
                    iss["modified"] += 1
        if is_story and key:
            a = authors.setdefault(key, {"key": key, "names": Counter(), "stories": [], "issues": set(),
                                         "magazines": set(), "genres": set(), "words": 0, "years": []})
            a["names"][r.get("author")] += 1
            a["stories"].append(sid)
            a["issues"].add(r["issue"])
            a["magazines"].add(r.get("magazine"))
            if r.get("genre"):
                a["genres"].add(r.get("genre"))
            a["words"] += r.get("n_words", 0)
            if y:
                a["years"].append(y)
    say(f"  records {n_rec:,}, authors {len(authors):,}")
    # ---- exact matches and paraphrase alignments; entity links from seed 6
    n_exact, n_para = Counter(), Counter()
    author_links = defaultdict(lambda: {"n": 0, "longest": 0, "pairs": set(), "stories": set()})
    mag_links = defaultdict(lambda: {"n": 0, "longest": 0, "pairs": set()})
    issue_links = Counter()
    mrows = []
    for k in (6, 7, 8):
        for n, m in enumerate(_jsonl(os.path.join(D, "reuse", f"machine_k{k}_matches.jsonl"))):
            mrows.append((k, n, m["a"], m["b"], m["len"], m.get("a_issue"), m.get("b_issue"), m.get("excerpt", ""), 0, None,
                          json.dumps(m, ensure_ascii=False)))
            if k == 6:
                n_exact[m["a"]] += 1
                n_exact[m["b"]] += 1
                sa, sb = story_meta.get(m["a"]), story_meta.get(m["b"])
                if sa and sb:
                    ka, kb = sa[1], sb[1]
                    if ka and kb and ka != kb:
                        L = author_links[tuple(sorted((ka, kb)))]
                        L["n"] += 1
                        L["longest"] = max(L["longest"], m["len"])
                        L["pairs"].add((m["a"], m["b"]))
                        L["stories"].update((m["a"], m["b"]))
                    ML = mag_links[tuple(sorted((sa[2] or "", sb[2] or "")))]
                    ML["n"] += 1
                    ML["longest"] = max(ML["longest"], m["len"])
                    ML["pairs"].add((m["a"], m["b"]))
                    issue_links[tuple(sorted((sa[0], sb[0])))] += 1
        for n, m in enumerate(_jsonl(os.path.join(D, "reuse", f"machine_k{k}_sameissue.jsonl"))):
            mrows.append((k, n, m["a"], m["b"], m["len"], m.get("a_issue"), m.get("b_issue"), m.get("excerpt", ""), 1,
                          m.get("cause"), json.dumps(m, ensure_ascii=False)))
    con.executemany("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?)", mrows)
    say(f"  matches {len(mrows):,}")
    arows = []
    for n, a in enumerate(_jsonl(os.path.join(D, "reuse", "para", "machine_w50s25_k10_alignments.jsonl"))):
        arows.append((n, a["a"], a["b"], a.get("cols"), a.get("identity"), a.get("score"), a.get("text_a", ""), a.get("text_b", ""),
                      json.dumps(a, ensure_ascii=False)))
        n_para[a["a"]] += 1
        n_para[a["b"]] += 1
    con.executemany("INSERT INTO aligns VALUES (?,?,?,?,?,?,?,?,?)", arows)
    con.executemany("INSERT INTO records VALUES (" + ",".join("?" * 42) + ")",
                    [row[:27] + (n_exact.get(row[0], 0), n_para.get(row[0], 0)) + row[27:] for row in rec_rows])
    degree = Counter()
    for (ka, kb), L in author_links.items():
        degree[ka] += L["n"]
        degree[kb] += L["n"]
    con.executemany("INSERT INTO author_links VALUES (?,?,?,?,?,?)",
                    [(ka, kb, L["n"], L["longest"], json.dumps(sorted(L["pairs"])), json.dumps(sorted(L["stories"])))
                     for (ka, kb), L in author_links.items()])
    con.executemany("INSERT INTO mag_links VALUES (?,?,?,?,?)",
                    [(ma, mb, L["n"], L["longest"], json.dumps(sorted(L["pairs"]))) for (ma, mb), L in mag_links.items()])
    con.executemany("INSERT INTO issue_links VALUES (?,?,?)", [(ia, ib, n) for (ia, ib), n in issue_links.items()])
    con.executemany("INSERT INTO authors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(k, author_slug(k), display_author(a["names"].most_common(1)[0][0]), json.dumps(dict(a["names"]), ensure_ascii=False),
                      len(a["stories"]), a["words"], len(a["issues"]), json.dumps(sorted(a["magazines"])),
                      json.dumps(sorted(a["genres"])), (min(a["years"]) if a["years"] else None),
                      (max(a["years"]) if a["years"] else None), degree.get(k, 0),
                      last_name(display_author(a["names"].most_common(1)[0][0]))) for k, a in authors.items()])
    # ---- annotation events
    erows = []
    for f in glob.glob(os.path.join(D, "annotations", "*.jsonl")):
        for e in _jsonl(f):
            erows.append((e.get("ts", ""), e.get("user"), e.get("issue"), e.get("article_id"), e.get("action"),
                          json.dumps(e, ensure_ascii=False)))
            if e.get("issue") in issues:
                issues[e["issue"]]["events"] += 1
    erows.sort()
    con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", erows)
    # ---- issues and magazines
    mags = {}
    for iid, i in issues.items():
        # complete = assembled into records by the machine (an export proves an assembly too)
        i["complete"] = 1 if (i["assembled"] or i["exported"]) else 0
        con.execute("INSERT INTO issues VALUES (" + ",".join("?" * 30) + ")",
                    (iid, i["magazine"], i["mag_slug"], i["cover_date"], i["year"], i["decade"], i["genre"], i["format"],
                     i["ia_identifier"], i["why"], i["gold"], i["publisher"], i["publisher_group"], i["publisher_source"],
                     json.dumps(i["ia"], ensure_ascii=False), i["ia_item_size"], json.dumps(i["ia_files"]), i["records"],
                     i["stories"], i["words"], len(i["authors"]), i["verified"], i["modified"], i["pages"], i["layout_pages"],
                     json.dumps(i["text_stages"]), i["assembled"], i["exported"], i["events"], i["complete"]))
        m = mags.setdefault(i["magazine"], {"name": i["magazine"], "slug": i["mag_slug"], "genre": i["genre"], "format": i["format"],
                                            "publisher_group": i["publisher_group"], "publishers": set(), "issues": [],
                                            "records": 0, "stories": 0, "words": 0, "authors": set(), "verified": 0, "years": []})
        m["issues"].append(iid)
        m["records"] += i["records"]
        m["stories"] += i["stories"]
        m["words"] += i["words"]
        m["authors"] |= i["authors"]
        m["verified"] += i["verified"]
        if i["publisher"]:
            m["publishers"].add(i["publisher"])
        if i["year"]:
            m["years"].append(i["year"])
    con.executemany("INSERT INTO magazines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(m["name"], m["slug"], m["genre"], m["format"], m["publisher_group"], json.dumps(sorted(m["publishers"])),
                      json.dumps(sorted(m["issues"])), len(m["issues"]), m["records"], m["stories"], m["words"],
                      len(m["authors"]), m["verified"], (min(m["years"]) if m["years"] else None),
                      (max(m["years"]) if m["years"] else None)) for m in mags.values()])
    # ---- the pair table
    pp = os.path.join(D, "reuse", "background", "pairs_machine.csv.gz")
    n_pairs = 0
    pair_cols = []
    if os.path.exists(pp):
        with gzip.open(pp, "rt", encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            header = next(rd)
            pair_cols = header + ["genre_a", "genre_b", "decade_a", "decade_b"]
            defs = ", ".join(f'"{c}" {"TEXT" if c in PAIR_TEXT_COLS else "REAL"}' for c in pair_cols)
            con.execute(f"CREATE TABLE pairs({defs})")
            ia, ib = header.index("a"), header.index("b")
            ins = "INSERT INTO pairs VALUES (" + ",".join("?" * len(pair_cols)) + ")"
            batch = []
            for row in rd:
                vals = [(v if c in PAIR_TEXT_COLS else _num(v)) for c, v in zip(header, row)]
                sa, sb = story_meta.get(row[ia]), story_meta.get(row[ib])
                vals += [sa[3] if sa else None, sb[3] if sb else None, sa[4] if sa else None, sb[4] if sb else None]
                batch.append(vals)
                n_pairs += 1
                if len(batch) >= 5000:
                    con.executemany(ins, batch)
                    batch = []
            if batch:
                con.executemany(ins, batch)
        for name, cols in (("p_a", "a"), ("p_b", "b"), ("p_exact", "same_issue, exact_k6_longest"),
                           ("p_mags", "magazine_a, magazine_b"), ("p_issues", "issue_a, issue_b"),
                           ("p_decade", "later_decade"), ("p_genres", "genre_a, genre_b"), ("p_para", "same_issue, para_k10_longest")):
            con.execute(f"CREATE INDEX {name} ON pairs({cols})")
    say(f"  pairs {n_pairs:,}")
    # ---- meta
    counts = {"issues": len(issues), "complete_issues": sum(i["complete"] for i in issues.values()), "records": n_rec,
              "stories": sum(1 for v in story_meta.values() if v[6]), "authors": len(authors), "magazines": len(mags),
              "exact_matches": {str(k): sum(1 for r in mrows if r[0] == k and r[8] == 0) for k in (6, 7, 8)},
              "same_issue_matches": {str(k): sum(1 for r in mrows if r[0] == k and r[8] == 1) for k in (6, 7, 8)},
              "paraphrase_alignments": len(arows), "pairs": n_pairs, "events": len(erows),
              "author_links": len(author_links), "magazine_links": len(mag_links),
              "ad_classes": dict(Counter(row[27] or "?" for row in rec_rows if row[9] == "ad")),
              "house_excerpts": sum(1 for row in rec_rows if row[29])}
    meta = {"signature": _signature(sig), "built": time.strftime("%Y-%m-%d %H:%M:%S"),
            "build_seconds": round(time.time() - t0, 2), "counts": json.dumps(counts),
            "sources": json.dumps([os.path.relpath(p, _G["ROOT"]) for p, _, _ in sig]),
            "pair_cols": json.dumps(pair_cols),
            "summary": json.dumps(_json(os.path.join(D, "reuse", "background", "summary_machine.json")) or {}),
            "overlap": json.dumps(_json(os.path.join(D, "reuse", "machine_region_overlap.json")) or {}),
            "survey": json.dumps(_json(os.path.join(D, "survey", "summary.json")) or {}),
            "version": "0.12.0"}
    con.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    con.commit()
    con.close()
    os.replace(tmp, path)
    say(f"  built {path} in {meta['build_seconds']}s")
    return meta


def ensure_db(force=False):
    """Rebuild data/explorer.sqlite when a source file changed (checked at
    most every CHECK_EVERY seconds). The first build blocks the request;
    later rebuilds run in whichever request notices, while other requests
    keep reading the old file."""
    path = _DB["path"]
    now = time.time()
    exists = os.path.exists(path)
    if not force and exists and os.environ.get("PULP_EXPLORER_STATIC"):
        return                     # corpus scale: the database is built by hand (--build), never at request time
    if not force and exists and _DB["sig"] is not None and now - _DB["checked"] < CHECK_EVERY:
        return
    _DB["checked"] = now
    if _DB["sig"] is None and exists:
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            v = c.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
            c.close()
            _DB["sig"] = v[0] if v else ""
        except Exception:
            _DB["sig"] = ""
    sig = _sources()
    h = _signature(sig)
    if not force and exists and h == _DB["sig"]:
        return
    if BUILD_LOCK.acquire(blocking=(force or not exists)):
        try:
            if force or not os.path.exists(path) or h != _DB["sig"]:
                build_db(sig, path)
                _DB["sig"] = h
        finally:
            BUILD_LOCK.release()


def db():
    """A read-only connection to the explorer database (one per call; the
    server is threaded)."""
    ensure_db()
    c = sqlite3.connect(f"file:{_DB['path']}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def _one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r is not None else None


def _val(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return r[0] if r is not None else None


def meta_value(con, key, default=None):
    v = _val(con, "SELECT value FROM meta WHERE key=?", (key,))
    return v if v is not None else default


def meta_json(con, key, default=None):
    return _j(meta_value(con, key), default)


def pair_row(con, a, b):
    """The pair-table row for two stories as a dict, either order."""
    if not _val(con, "SELECT COUNT(*) FROM sqlite_master WHERE name='pairs'"):
        return None
    return _one(con, "SELECT * FROM pairs WHERE (a=? AND b=?) OR (a=? AND b=?)", (a, b, b, a))


def _has_pairs(con):
    return bool(_val(con, "SELECT COUNT(*) FROM sqlite_master WHERE name='pairs'"))


# ---------------------------------------------------------------- shared html

def _story_link(con, sid, with_author=True, rec=None):
    r = rec or _one(con, "SELECT id, title, author, display_author, author_key FROM records WHERE id=?", (sid,))
    if not r:
        return f"<a href='/story/{_esc(sid)}'>{_esc(sid)}</a>"
    t = r["title"] or "(untitled)"
    if len(t) > 70:
        t = t[:67].rstrip() + "…"
    s = f"<a href='/story/{_esc(sid)}'>{_esc(t)}</a>"
    if with_author and r.get("author"):
        s += " <span class='muted'>— </span>" + _author_link(r)
    return s


def _author_link(rec):
    name = rec.get("display_author") or display_author(rec.get("author"))
    if rec.get("author_key"):
        return f"<a href='/author/{_esc(author_slug(rec['author_key']))}' class='muted'>{_esc(name)}</a>"
    return f"<span class='muted'>{_esc(name)}</span>"


def _author_link_key(key, display):
    return f"<a href='/author/{_esc(author_slug(key))}'>{_esc(display)}</a>"


def _issue_link(con, iid, issue=None):
    i = issue or _one(con, "SELECT magazine, cover_date FROM issues WHERE id=?", (iid,))
    if not i:
        return _esc(iid or "")
    return f"<a href='/issue/{_esc(iid)}'>{_esc(i['magazine'])} {_esc(i['cover_date'])}</a>"


def _mag_link(name, slug=None):
    if not name:
        return ""
    return f"<a href='/magazine/{_esc(slug or mag_slug(name))}'>{_esc(name)}</a>"


def _tiles(items):
    """Headline numbers: [(label, value, href)]"""
    out = ["<div style='display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 18px'>"]
    for label, value, href in items:
        v = _n(value or 0)
        box = (f"<div style='flex:1 1 130px;min-width:120px;background:#fff;border:1px solid #d8cfc0;"
               f"padding:8px 12px'><div style='font-size:22px;font-variant-numeric:tabular-nums'>{v}</div>"
               f"<div class='muted' style='font-size:12.5px'>{_esc(label)}</div></div>")
        out.append(f"<a href='{href}' style='text-decoration:none;color:inherit;flex:1 1 130px;display:flex'>{box}</a>"
                   if href else box)
    out.append("</div>")
    return "".join(out)


def _raw_link(path, label="raw data"):
    return (f"<a href='{path}' class='muted' style='font-size:12.5px;border:1px solid #b8a88e;"
            f"padding:1px 8px;text-decoration:none'>{_esc(label)} ↓</a>")


def _table(headers, rows, cls=""):
    h = "".join(f"<th{' class=num' if hd.startswith('#') else ''}>{_esc(hd.lstrip('#'))}</th>" for hd in headers)
    body = "".join("<tr>" + "".join(
        f"<td class='num'>{c[1:]}</td>" if isinstance(c, str) and c.startswith("\x01") else f"<td>{c}</td>"
        for c in r) + "</tr>" for r in rows)
    return f"<div style='overflow-x:auto'><table{(' class=' + cls) if cls else ''}><tr>{h}</tr>{body}</table></div>"


def N(v):
    """Mark a table cell as numeric (right-aligned)."""
    return "\x01" + (_n(v) if not isinstance(v, str) else v)


def _sel(name, val, opts):
    return f"<select name='{name}'>" + "".join(
        f"<option value='{_esc(str(v))}'{' selected' if str(v) == str(val) else ''}>{_esc(str(l))}</option>" for v, l in opts) + "</select>"


def _pageno(qs):
    try:
        return max(1, int((qs.get("page", ["1"]) or ["1"])[0] or 1))
    except ValueError:
        return 1


def _pager(qs, total, path, per_page=PER_PAGE):
    """'showing a–b of n' with previous/next links that keep the other
    query parameters."""
    page = _pageno(qs)
    n_pages = max(1, math.ceil(total / per_page))
    page = min(page, n_pages)
    keep = {k: v[0] for k, v in qs.items() if k != "page" and v and v[0] != ""}

    def link(p, label):
        q = dict(keep)
        q["page"] = str(p)
        return f"<a href='{path}?{urllib.parse.urlencode(q)}'>{label}</a>"
    lo, hi = (page - 1) * per_page + 1, min(total, page * per_page)
    parts = [f"<span class='muted'>showing {lo:,}–{hi:,} of {total:,}</span>" if total else "<span class='muted'>nothing matches</span>"]
    if page > 1:
        parts.append(link(page - 1, "← previous"))
    if page < n_pages:
        parts.append(link(page + 1, "next →"))
    if n_pages > 2:
        parts.append(f"<span class='muted'>page {page} of {n_pages}</span>")
    return ("<p style='display:flex;gap:14px;flex-wrap:wrap'>" + "".join(f"<span>{p}</span>" for p in parts) + "</p>",
            per_page, (page - 1) * per_page)


def _g(qs, k, d=""):
    v = (qs.get(k, [d]) or [d])[0]
    return v if v is not None else d


def _slice(qs):
    """The decade / genre / magazine slice chosen on the overview."""
    decade = _g(qs, "decade").strip()
    try:
        decade = int(decade) if decade else None
    except ValueError:
        decade = None
    return {"decade": decade, "genre": _g(qs, "genre").strip() or None, "mag": _g(qs, "mag").strip() or None}


def _slice_sql(sl, alias=""):
    p = alias + "." if alias else ""
    conds, args = [], []
    if sl["decade"] is not None:
        conds.append(f"{p}decade=?")
        args.append(sl["decade"])
    if sl["genre"]:
        conds.append(f"{p}genre=?")
        args.append(sl["genre"])
    if sl["mag"]:
        conds.append(f"{p}mag_slug=?")
        args.append(sl["mag"])
    return conds, args


def _slice_qs(sl):
    q = {k: v for k, v in (("decade", sl["decade"]), ("genre", sl["genre"]), ("mag", sl["mag"])) if v not in (None, "")}
    return urllib.parse.urlencode(q)


def _slice_label(sl, con):
    parts = []
    if sl["decade"] is not None:
        parts.append(f"{sl['decade']}s")
    if sl["genre"]:
        parts.append(sl["genre"])
    if sl["mag"]:
        parts.append(_val(con, "SELECT name FROM magazines WHERE slug=?", (sl["mag"],)) or sl["mag"])
    return " · ".join(parts) if parts else "all complete issues"


# ---------------------------------------------------------------- charts

def svg_stacked(categories, series, title, href=None, width=560, height=250):
    """Stacked bars: series = [(name, [values...])] in fixed palette order;
    every segment is a link when href(category, name) is given."""
    n_cat = len(categories)
    if not n_cat or not series:
        return ""
    rotate = max(len(str(c)) for c in categories) * 6.5 * n_cat > width - 60
    left, right, top, bottom = 48, 12, 30, (78 if rotate else 44)
    if rotate:
        height += 34
    pw, ph = width - left - right, height - top - bottom
    totals = [sum((vals[i] or 0) for _, vals in series if i < len(vals)) for i in range(n_cat)]
    ticks = RP._ticks(max(totals) if totals else 0)
    vtop = ticks[-1] or 1
    band = pw / n_cat
    barw = min(28, band * 0.72)
    parts = [f"<svg viewBox='0 0 {width} {height}' width='100%' style='max-width:{width}px' role='img' "
             f"aria-label='{_esc(title)}' font-family='Georgia,serif'>",
             f"<text x='{left}' y='16' font-size='13' fill='{RP.INK}'>{_esc(title)}</text>"]
    for t in ticks:
        y = top + ph - ph * t / vtop
        parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{y:.1f}' y2='{y:.1f}' stroke='{RP.GRID}'/>")
        parts.append(f"<text x='{left - 6}' y='{y + 4:.1f}' font-size='11' fill='{RP.INK2}' text-anchor='end'>{_fmt(t)}</text>")
    label_every = max(1, math.ceil(n_cat / (10 if rotate else 14)))
    for ci, cat in enumerate(categories):
        x = left + ci * band + (band - barw) / 2
        y = top + ph
        for si, (name, vals) in enumerate(series):
            v = vals[ci] if ci < len(vals) and vals[ci] else 0
            if not v:
                continue
            h = ph * v / vtop
            y -= h
            col = RP.PALETTE[si % len(RP.PALETTE)] if si < len(RP.PALETTE) else "#9a917f"
            rect = (f"<rect x='{x:.1f}' y='{y:.1f}' width='{barw:.1f}' height='{h:.1f}' fill='{col}' stroke='#faf7f2' stroke-width='0.5'>"
                    f"<title>{_esc(name)} · {_esc(str(cat))}: {_fmt(v)}</title></rect>")
            parts.append(f"<a href='{href(cat, name)}'>{rect}</a>" if href else rect)
        if ci % label_every == 0 or ci == n_cat - 1:
            cx = left + ci * band + band / 2
            if rotate:
                parts.append(f"<text x='{cx:.1f}' y='{top + ph + 14}' font-size='11' fill='{RP.INK}' text-anchor='end' "
                             f"transform='rotate(-35 {cx:.1f} {top + ph + 14})'>{_esc(str(cat))}</text>")
            else:
                parts.append(f"<text x='{cx:.1f}' y='{top + ph + 16}' font-size='11.5' fill='{RP.INK}' text-anchor='middle'>{_esc(str(cat))}</text>")
    parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{top + ph}' y2='{top + ph}' stroke='{RP.INK2}'/>")
    lx = left
    for si, (name, _) in enumerate(series):
        col = RP.PALETTE[si % len(RP.PALETTE)] if si < len(RP.PALETTE) else "#9a917f"
        parts.append(f"<rect x='{lx}' y='{height - 12}' width='10' height='10' fill='{col}'/>")
        parts.append(f"<text x='{lx + 14}' y='{height - 3}' font-size='11' fill='{RP.INK2}'>{_esc(name)}</text>")
        lx += 22 + 6.5 * len(name)
    parts.append("</svg>")
    return "".join(parts)


def svg_network(con, links, max_nodes=DRAW_LIMITS["authors"]):
    """Authors as points on a ring grouped by magazine; a curve between two
    authors whose stories share a passage (seed 6, cross-issue). Line width
    grows with the number of shared passages. Every point is a link."""
    if not links:
        return "<div class='empty'>No cross-issue shared passages between named authors in this slice.</div>"
    degree = Counter()
    for L in links:
        degree[L["ka"]] += L["n"]
        degree[L["kb"]] += L["n"]
    nodes = [k for k, _ in degree.most_common(max_nodes)]
    keep = set(nodes)
    info = {}
    for k in nodes:
        a = _one(con, "SELECT display, n_stories, magazines FROM authors WHERE key=?", (k,))
        if a:
            a["mags"] = _j(a["magazines"], [])
            info[k] = a
    nodes = [k for k in nodes if k in info]

    def main_mag(k):
        return info[k]["mags"][0] if info[k]["mags"] else ""
    nodes.sort(key=lambda k: (main_mag(k), -degree[k]))
    n = len(nodes)
    if not n:
        return ""
    W, H = 680, 680
    cx, cy, R = W / 2, H / 2, 215
    pos = {}
    for i, k in enumerate(nodes):
        ang = 2 * math.pi * i / n - math.pi / 2
        pos[k] = (cx + R * math.cos(ang), cy + R * math.sin(ang), ang)
    parts = [f"<svg viewBox='0 0 {W} {H}' width='100%' style='max-width:{W}px' role='img' "
             f"aria-label='Author reuse network' font-family='Georgia,serif'>"]
    maxn = max(L["n"] for L in links)
    drawn = 0
    for L in sorted(links, key=lambda L: L["n"]):
        ka, kb = L["ka"], L["kb"]
        if ka not in pos or kb not in pos:
            continue
        drawn += 1
        xa, ya, _ = pos[ka]
        xb, yb, _ = pos[kb]
        w = 1 + 3 * L["n"] / maxn
        parts.append(f"<path d='M{xa:.1f},{ya:.1f} Q{cx:.1f},{cy:.1f} {xb:.1f},{yb:.1f}' fill='none' "
                     f"stroke='{RP.PALETTE[0]}' stroke-opacity='0.45' stroke-width='{w:.1f}'>"
                     f"<title>{_esc(info[ka]['display'])} ↔ {_esc(info[kb]['display'])}: {L['n']} shared passage(s), "
                     f"longest {L['longest']} words</title></path>")
    mags = sorted({main_mag(k) for k in nodes})
    for k in nodes:
        x, y, ang = pos[k]
        a = info[k]
        r = 4 + 1.5 * math.sqrt(a["n_stories"] or 1)
        name = a["display"]
        lx = x + (r + 6) * math.cos(ang)
        ly = y + (r + 6) * math.sin(ang)
        deg = math.degrees(ang)
        if math.cos(ang) >= 0:
            anchor, rot = "start", deg
        else:
            anchor, rot = "end", deg + 180
        parts.append(f"<a href='/author/{_esc(author_slug(k))}'>"
                     f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r:.1f}' fill='{RP.PALETTE[1]}' stroke='#faf7f2' stroke-width='2'>"
                     f"<title>{_esc(name)} · {a['n_stories']} stories · {mag_abbr(main_mag(k))} · "
                     f"{degree[k]} shared passages</title></circle>"
                     f"<text x='{lx:.1f}' y='{ly + 3.5:.1f}' font-size='10' fill='{RP.INK}' text-anchor='{anchor}' "
                     f"transform='rotate({rot:.1f} {lx:.1f} {ly:.1f})'>{_esc(name[:24])}</text></a>")
    more = f" · {len(degree) - n} more authors not drawn" if len(degree) > n else ""
    parts.append(f"<text x='{cx:.1f}' y='{H - 8:.1f}' font-size='11' fill='{RP.INK2}' text-anchor='middle'>"
                 f"{n} authors · {drawn} author pairs{more} · ring order by magazine: "
                 f"{' · '.join(mag_abbr(m) for m in mags)[:120]}</text>")
    parts.append("</svg>")
    return "".join(parts)


def html_grid(con, mags, links):
    """Magazine-by-magazine grid of shared passages (seed 6, cross-issue);
    cell shade = count; every cell links to the pair list."""
    counts = {}
    mx = 1
    for L in links:
        counts[(L["ma"], L["mb"])] = L["n"]
        counts[(L["mb"], L["ma"])] = L["n"]
        mx = max(mx, L["n"])
    names = [m["name"] for m in mags]
    head = "<tr><th></th>" + "".join(f"<th title='{_esc(m)}'>{_esc(mag_abbr(m))}</th>" for m in names) + "</tr>"
    rows = []
    for a in names:
        cells = []
        for b in names:
            c = counts.get((a, b), 0)
            shade = 0 if not c else 0.15 + 0.75 * (c / mx)
            bg = f"rgba(42,120,214,{shade:.2f})" if c else "#fff"
            ink = "#fff" if shade > 0.55 else RP.INK
            href = f"/pairs?ma={urllib.parse.quote(a)}&mb={urllib.parse.quote(b)}&min=6"
            cells.append(f"<td style='background:{bg};color:{ink};text-align:center;padding:6px'>"
                         f"<a href='{href}' style='color:inherit;text-decoration:none' "
                         f"title='{_esc(a)} × {_esc(b)}: {c} shared passages'>{c if c else '·'}</a></td>")
        rows.append(f"<tr><th style='text-align:left'>{_mag_link(a)}</th>{''.join(cells)}</tr>")
    return ("<div style='overflow-x:auto'><table style='width:auto'>" + head + "".join(rows) + "</table></div>"
            "<p class='muted' style='font-size:12.5px'>Same-magazine cells count passages shared by two "
            "different issues of that magazine; within-issue matches are excluded everywhere.</p>")


def html_genre_grid(cells, genres):
    """Genre-by-genre grid: share of cross-issue story pairs that share a
    six-word passage, count in the tooltip; every cell links to the pairs."""
    if not genres:
        return "<div class='empty'>No cross-issue pairs in this slice.</div>"
    mx = max((s / n for (n, s) in cells.values() if n), default=0) or 1
    head = "<tr><th></th>" + "".join(f"<th>{_esc(g)}</th>" for g in genres) + "</tr>"
    rows = []
    for a in genres:
        cs = []
        for b in genres:
            n, s = cells.get((a, b), (0, 0))
            rate = (s / n) if n else 0
            shade = 0 if not n else 0.12 + 0.78 * (rate / mx)
            bg = f"rgba(235,104,52,{shade:.2f})" if n else "#fff"
            ink = "#fff" if shade > 0.6 else RP.INK
            href = f"/pairs?ga={urllib.parse.quote(a)}&gb={urllib.parse.quote(b)}&min=6"
            cs.append(f"<td style='background:{bg};color:{ink};text-align:center;padding:6px'>"
                      f"<a href='{href}' style='color:inherit;text-decoration:none' title='{_esc(a)} × {_esc(b)}: "
                      f"{s:,} of {n:,} pairs share a passage'>{(_fmt(rate * 100) + '%') if n else '·'}</a></td>")
        rows.append(f"<tr><th style='text-align:left'>{_esc(a)}</th>{''.join(cs)}</tr>")
    return ("<div style='overflow-x:auto'><table style='width:auto'>" + head + "".join(rows) + "</table></div>"
            "<p class='muted' style='font-size:12.5px'>Cell = share of cross-issue story pairs between the two genres "
            "that share an exact passage of six or more words (count in the tooltip). Click a cell for the pairs.</p>")


def svg_timeline(issues):
    if not issues:
        return ""
    W, H = 720, 290
    left, right, top, bottom = 44, 16, 26, 100
    pw, ph = W - left - right, H - top - bottom
    years = [i["year"] for i in issues if i["year"]]
    if not years:
        return ""
    y0 = math.floor(min(years) - 1)
    y1 = math.ceil(max(years) + 1)
    smax = max(1, max(i["stories"] for i in issues))
    ticks = RP._ticks(smax)
    vtop = ticks[-1] or 1

    def xp(y):
        return left + pw * (y - y0) / max(1, y1 - y0)
    parts = [f"<svg viewBox='0 0 {W} {H}' width='100%' style='max-width:{W}px' role='img' "
             f"aria-label='Issues on a time axis' font-family='Georgia,serif'>",
             f"<text x='{left}' y='16' font-size='13' fill='{RP.INK}'>Issues on the time axis — bar height = stories "
             f"assembled, dark part = verified</text>"]
    for t in ticks:
        y = top + ph - ph * t / vtop
        parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{y:.1f}' y2='{y:.1f}' stroke='{RP.GRID}'/>")
        parts.append(f"<text x='{left - 6}' y='{y + 4:.1f}' font-size='11' fill='{RP.INK2}' text-anchor='end'>{_fmt(t)}</text>")
    for yr in range(y0, y1 + 1, 5 if y1 - y0 > 12 else 1):
        parts.append(f"<text x='{xp(yr):.1f}' y='{top + ph + 14}' font-size='11' fill='{RP.INK2}' text-anchor='middle'>{yr}</text>")
    label = len(issues) <= 40
    for n_i, i in enumerate(sorted(issues, key=lambda i: (i["year"] or 0, i["id"]))):
        if not i["year"]:
            continue
        x = xp(i["year"])
        ly = top + ph + 30 + 16 * (n_i % 3)
        h = ph * i["stories"] / vtop
        hv = ph * i["verified"] / vtop
        parts.append(f"<a href='/issue/{_esc(i['id'])}'>"
                     f"<rect x='{x - 6:.1f}' y='{top + ph - h:.1f}' width='12' height='{h:.1f}' fill='{RP.PALETTE[0]}' fill-opacity='0.55'>"
                     f"<title>{_esc(i['magazine'])} {_esc(i['cover_date'])}: {i['stories']} stories, {i['verified']} verified, "
                     f"{i['records']} records, {i['words']:,} words</title></rect>"
                     f"<rect x='{x - 6:.1f}' y='{top + ph - hv:.1f}' width='12' height='{hv:.1f}' fill='{RP.PALETTE[0]}'/>"
                     + (f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{top + ph}' y2='{ly - 9}' stroke='{RP.GRID}'/>"
                        f"<text x='{x:.1f}' y='{ly}' font-size='10' fill='{RP.INK}' text-anchor='middle'>"
                        f"{_esc(mag_abbr(i['magazine']))} {_esc((i['cover_date'] or '')[:7])}</text>" if label else "")
                     + "</a>")
    parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{top + ph}' y2='{top + ph}' stroke='{RP.INK2}'/>")
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- overview

def _pair_slice_sql(sl):
    """Pairs with at least one side in the slice."""
    conds, args = [], []
    if sl["decade"] is not None:
        conds.append("(decade_a=? OR decade_b=?)")
        args += [sl["decade"], sl["decade"]]
    if sl["genre"]:
        conds.append("(genre_a=? OR genre_b=?)")
        args += [sl["genre"], sl["genre"]]
    if sl["mag"]:
        name = sl["mag"]
        conds.append("(magazine_a IN (SELECT name FROM magazines WHERE slug=?) OR magazine_b IN (SELECT name FROM magazines WHERE slug=?))")
        args += [name, name]
    return conds, args


def overview(qs=None, render=None):
    qs = qs or {}
    con = db()
    sl = _slice(qs)
    counts = meta_json(con, "counts", {})
    sc, sa = _slice_sql(sl)
    where_rec = " AND ".join(["is_story=1"] + sc)
    where_iss = " AND ".join(["complete=1"] + sc)
    stories_in = f"SELECT id FROM records WHERE {where_rec}"
    issues_in = f"SELECT id FROM issues WHERE {where_iss}"
    n_issues = _val(con, f"SELECT COUNT(*) FROM issues WHERE {where_iss}", sa)
    n_pages = _val(con, f"SELECT COALESCE(SUM(pages),0) FROM issues WHERE {where_iss}", sa)
    n_records = _val(con, f"SELECT COUNT(*) FROM records WHERE " + " AND ".join(["1=1"] + sc), sa)
    n_stories = _val(con, f"SELECT COUNT(*) FROM records WHERE {where_rec}", sa)
    n_words = _val(con, f"SELECT COALESCE(SUM(n_words),0) FROM records WHERE {where_rec}", sa)
    n_authors = _val(con, f"SELECT COUNT(DISTINCT author_key) FROM records WHERE author_key IS NOT NULL AND {where_rec}", sa)
    n_verified = _val(con, f"SELECT COUNT(*) FROM records WHERE status='verified' AND {where_rec}", sa)
    n_events = _val(con, f"SELECT COUNT(*) FROM events WHERE issue IN ({issues_in})", sa)
    n_shared = _val(con, f"SELECT COUNT(*) FROM matches WHERE k=6 AND same_issue=0 AND (a IN ({stories_in}) OR b IN ({stories_in}))", sa + sa)
    n_para = _val(con, f"SELECT COUNT(*) FROM aligns WHERE a IN ({stories_in}) OR b IN ({stories_in})", sa + sa)
    has_pairs = _has_pairs(con)
    pc, pa = _pair_slice_sql(sl)
    where_pairs = " AND ".join(["same_issue=0"] + pc)
    n_pairs = _val(con, f"SELECT COUNT(*) FROM pairs WHERE {where_pairs}", pa) if has_pairs else 0
    n_all_issues = _val(con, "SELECT COUNT(*) FROM issues")
    n_complete = _val(con, "SELECT COUNT(*) FROM issues WHERE complete=1")
    sq = _slice_qs(sl)
    tail = ("&" + sq) if sq else ""
    # the slice form
    decades = [r["decade"] for r in _rows(con, "SELECT DISTINCT decade FROM issues WHERE complete=1 AND decade IS NOT NULL ORDER BY decade")]
    genres = [r["genre"] for r in _rows(con, "SELECT DISTINCT genre FROM issues WHERE complete=1 AND genre IS NOT NULL ORDER BY genre")]
    mags_all = _rows(con, "SELECT name, slug FROM magazines ORDER BY name")
    form = (f"<form method='GET' action='/overview' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>decade {_sel('decade', sl['decade'] if sl['decade'] is not None else '', [('', 'all')] + [(d, f'{d}s') for d in decades])}</label>"
            f"<label>genre {_sel('genre', sl['genre'] or '', [('', 'all')] + [(g, g) for g in genres])}</label>"
            f"<label>magazine {_sel('mag', sl['mag'] or '', [('', 'all')] + [(m['slug'], m['name']) for m in mags_all])}</label>"
            f"<button>show</button>" + (" <a href='/overview' class='muted'>reset</a>" if sq else "") + "</form>")
    out = [_howto(
        "Layer 0 of the explorer. Choose a decade, a genre, or a magazine to slice everything on this page; "
        "every number and every element of every chart is a link: a tile opens the list behind it, a bar "
        "opens the records it counts, a point in the network opens the author, a cell in a grid opens the "
        "story pairs, a bar on the time axis opens the issue. From any list you reach the entity page "
        "(author, magazine, issue, story, pair, cluster), and from there the printed page on the scan and "
        "the raw JSON record the page was built from. Only complete issues (assembled into records by the "
        "machine) are counted here; the workroom's progress page shows every issue at every step."),
        f"<h1>Overview <span class='muted' style='font-size:16px;font-weight:normal'>— {_esc(_slice_label(sl, con))}</span></h1>", form,
        _tiles([("complete issues", n_issues, "/issues"), ("scanned pages", n_pages, "/issues"),
                ("records of all kinds", n_records, f"/stories?type=all{tail}"),
                ("stories", n_stories, f"/stories?{sq}" if sq else "/stories"),
                ("words in stories", n_words, f"/stories?sort=words{tail}"),
                ("named authors", n_authors, f"/authors?{sq}" if sq else "/authors"),
                ("verified stories", n_verified, f"/stories?status=verified{tail}"),
                ("annotation actions", n_events, "/activity"),
                ("shared passages (seed 6, across issues)", n_shared, "/reuse/clusters"),
                ("paraphrase alignments", n_para, "/reuse/clusters?kind=para&k=10"),
                ("cross-issue story pairs in the table", n_pairs, f"/pairs?{sq}" if sq else "/pairs")])]
    # 1. stories per year by genre (stacked); per decade when the span is wide
    yrs = _rows(con, f"SELECT CAST(year AS INTEGER) AS y, genre, COUNT(*) AS n, SUM(n_words) AS w FROM records WHERE {where_rec} "
                     f"AND year IS NOT NULL GROUP BY y, genre ORDER BY y", sa)
    if yrs:
        span = max(r["y"] for r in yrs) - min(r["y"] for r in yrs)
        by_dec = span > 45
        key = (lambda r: r["y"] // 10 * 10) if by_dec else (lambda r: r["y"])
        gtot = Counter()
        cells = defaultdict(Counter)
        for r in yrs:
            gtot[r["genre"] or "?"] += r["n"]
            cells[key(r)][r["genre"] or "?"] += r["n"]
        top = [g for g, _ in gtot.most_common(3)]
        cats = sorted(cells)
        series = [(g, [cells[c].get(g, 0) for c in cats]) for g in top]
        rest = [sum(v for g, v in cells[c].items() if g not in top) for c in cats]
        if any(rest):
            series.append(("other genres", rest))

        def href(cat, name):
            q = {"decade" if by_dec else "year": cat}
            if name != "other genres":
                q["genre"] = name
            return "/stories?" + urllib.parse.urlencode(q)
        unit = "decade" if by_dec else "year"
        trows = [[_esc(f"{c}s" if by_dec else str(c))] + [N(cells[c].get(g, 0)) for g in top] + [N(sum(cells[c].values()))] for c in cats]
        out.append(f"<h2>1. Stories by {unit} and genre</h2>")
        out.append(RP._chart_row(svg_stacked(cats, series, f"Stories per {unit}, by genre", href),
                                 _table([unit] + [f"#{g}" for g in top] + ["#all"], trows[-40:])
                                 + "<p class='muted' style='font-size:12.5px'>A story's date is its issue's cover date unless "
                                   "other evidence is recorded (the record's date_source says which). Click a bar segment "
                                   "for the stories it counts.</p>"))
    # 2. reuse by decade and genre × genre (from the pair table)
    if has_pairs and n_pairs:
        dec = _rows(con, f"SELECT CAST(later_decade AS INTEGER) AS d, COUNT(*) AS n, SUM(exact_k6_longest>=6) AS s, "
                         f"SUM(para_k10_longest>=20) AS p FROM pairs WHERE {where_pairs} AND later_decade IS NOT NULL GROUP BY d ORDER BY d", pa)
        cats = [f"{r['d']}s" for r in dec]
        rate = [round(100 * r["s"] / r["n"], 2) if r["n"] else 0 for r in dec]
        prate = [round(100 * r["p"] / r["n"], 2) if r["n"] else 0 for r in dec]
        trows = [[f"<a href='/pairs?decade={r['d']}&min=6'>{r['d']}s</a>", N(r["n"]), N(r["s"]), N(f"{rate[i]}%"), N(r["p"])]
                 for i, r in enumerate(dec)]
        gc = _rows(con, f"SELECT genre_a, genre_b, COUNT(*) AS n, SUM(exact_k6_longest>=6) AS s FROM pairs WHERE {where_pairs} "
                        f"GROUP BY genre_a, genre_b", pa)
        cells = {}
        gset = set()
        for r in gc:
            ga, gb = r["genre_a"] or "?", r["genre_b"] or "?"
            gset.update((ga, gb))
            for kk in {(ga, gb), (gb, ga)}:          # one key when both sides are the same genre
                n0, s0 = cells.get(kk, (0, 0))
                cells[kk] = (n0 + r["n"], s0 + (r["s"] or 0))
        out.append("<h2>2. How often stories share a passage — by decade of the later story, and genre by genre</h2>")
        out.append(RP._chart_row(
            RP.svg_bars(cats, [("% of pairs sharing an exact passage", rate), ("% with a paraphrase alignment", prate)],
                        "Cross-issue story pairs with shared text, by decade of the later story", unit="%"),
            _table(["decade", "#pairs", "#share a passage", "#rate", "#paraphrase"], trows)))
        out.append(html_genre_grid(cells, sorted(gset)))
    # 3. authors network
    links = _rows(con, f"SELECT ka, kb, n, longest, pairs FROM author_links WHERE ka IN (SELECT author_key FROM records WHERE {where_rec}) "
                       f"OR kb IN (SELECT author_key FROM records WHERE {where_rec}) ORDER BY n DESC, longest DESC", sa + sa)
    out.append("<h2>3. Which authors share passages with which</h2>")
    rows = []
    for L in links[:30]:
        da = _val(con, "SELECT display FROM authors WHERE key=?", (L["ka"],)) or L["ka"]
        dbn = _val(con, "SELECT display FROM authors WHERE key=?", (L["kb"],)) or L["kb"]
        p0 = (_j(L["pairs"], []) or [[None, None]])[0]
        rows.append([_author_link_key(L["ka"], da), _author_link_key(L["kb"], dbn), N(L["n"]), N(L["longest"]),
                     f"<a href='/pair/{_esc(p0[0])}/{_esc(p0[1])}'>first pair</a>" if p0[0] else ""])
    n_linked = len({L["ka"] for L in links} | {L["kb"] for L in links})
    note = (f"<p class='muted' style='font-size:12.5px'>Shared passages are exact matches of six or more words between "
            f"stories from different issues; at pilot scale these are stock phrases, so the network shows the machinery, "
            f"not influence. {n_linked} linked authors, {len(links)} author pairs"
            + (f"; the ring draws the {DRAW_LIMITS['authors']} with most shared passages" if n_linked > DRAW_LIMITS["authors"] else "")
            + f". Full list: <a href='/authors?sort=shared{tail}'>authors</a>.</p>")
    out.append(RP._chart_row(svg_network(con, links), _table(["author", "author", "#shared passages", "#longest (words)", ""], rows) + note))
    # 4. magazine grid
    ic, iargs = _slice_sql(sl, "i")
    mags = _rows(con, "SELECT m.name, m.slug, m.first_year FROM magazines m WHERE EXISTS (SELECT 1 FROM issues i WHERE i.magazine=m.name AND "
                      + " AND ".join(["i.complete=1"] + ic) + ") ORDER BY m.first_year, m.name", iargs)
    out.append("<h2>4. Magazine by magazine</h2>")
    names = {m["name"] for m in mags}
    mlinks = [L for L in _rows(con, "SELECT ma, mb, n, longest FROM mag_links ORDER BY n DESC") if L["ma"] in names or L["mb"] in names]
    if len(mags) <= DRAW_LIMITS["magazines"]:
        out.append(html_grid(con, mags, mlinks))
    else:
        rows = [[_mag_link(L["ma"]), _mag_link(L["mb"]), N(L["n"]), N(L["longest"]),
                 f"<a href='/pairs?ma={urllib.parse.quote(L['ma'])}&mb={urllib.parse.quote(L['mb'])}&min=6'>pairs</a>"] for L in mlinks[:30]]
        out.append(f"<p class='muted'>{len(mags)} magazines in this slice — too many for a grid; the 30 magazine pairs "
                   f"sharing most passages:</p>" + _table(["magazine", "magazine", "#passages", "#longest", ""], rows))
    # 5. issues in time
    out.append("<h2>5. The issues in time</h2>")
    iss = _rows(con, f"SELECT id, magazine, cover_date, year, stories, verified, records, words FROM issues WHERE {where_iss}", sa)
    if len(iss) <= DRAW_LIMITS["issues"]:
        out.append(svg_timeline(iss))
    else:
        per_year = Counter(int(i["year"]) for i in iss if i["year"])
        ys = sorted(per_year)
        out.append(RP._chart_row(RP.svg_bars([str(y) for y in ys], [("issues", [per_year[y] for y in ys])], "Complete issues per year"),
                                 f"<p class='muted'>{len(iss):,} issues in this slice — the time axis with one bar per issue "
                                 f"is drawn for slices of {DRAW_LIMITS['issues']} or fewer; narrow the slice by magazine or decade.</p>"))
    # 6. census by magazine
    out.append("<h2>6. Corpus census</h2>")
    mrows = _rows(con, f"SELECT m.* FROM magazines m WHERE m.name IN (SELECT magazine FROM issues WHERE {where_iss}) ORDER BY m.stories DESC, m.name", sa)
    top = mrows[:12]
    cats = [mag_abbr(m["name"]) for m in top]
    c1 = RP.svg_bars(cats, [("stories", [m["stories"] for m in top])], "Stories per magazine" + (" (top 12)" if len(mrows) > 12 else ""))
    c2 = RP.svg_bars(cats, [("words (thousands)", [round(m["words"] / 1000, 1) for m in top])], "Words in stories per magazine, thousands")
    rows = [[_mag_link(m["name"], m["slug"]), N(m["n_issues"]), N(m["records"]), N(m["stories"]), N(m["words"]),
             N(m["n_authors"]), N(m["verified"]), _esc(m["genre"] or ""), _esc(m["format"] or ""),
             _esc(m["publisher_group"] or "")] for m in mrows[:40]]
    out.append(RP._chart_row(c1 + c2, _table(["magazine", "#issues", "#records", "#stories", "#words", "#authors",
                                              "#verified", "genre", "format", "publisher"], rows)
                             + (f"<p class='muted'>First 40 of {len(mrows)} magazines; <a href='/magazines'>all magazines</a>.</p>" if len(mrows) > 40 else "")))
    by_type = _rows(con, f"SELECT type, COUNT(*) AS n FROM records WHERE " + " AND ".join(["1=1"] + sc) + " GROUP BY type ORDER BY n DESC", sa)
    by_status = _rows(con, f"SELECT status, COUNT(*) AS n FROM records WHERE {where_rec} GROUP BY status ORDER BY n DESC", sa)
    by_src = _rows(con, f"SELECT date_source, COUNT(*) AS n FROM records WHERE {where_rec} GROUP BY date_source ORDER BY n DESC", sa)
    out.append("<p class='muted'>Records by type: " + ", ".join(f"<a href='/stories?type={_esc(t['type'])}{tail}'>{_esc(t['type'])}</a> {t['n']:,}" for t in by_type)
               + ". Story status: " + ", ".join(f"{_esc(s['status'])} {s['n']:,}" for s in by_status)
               + ". Story dates from: " + ", ".join(f"{_esc(s['date_source'] or 'issue')} {s['n']:,}" for s in by_src) + ".</p>")
    # 7. background curve (whole table; not sliced)
    s = meta_json(con, "summary", {})
    bg = ((s.get("background") or {}).get("exact") or {}).get("k6") if s else None
    if bg:
        allx = [int(x) for x in bg["overall"].keys()]
        nonzero = [x for x in allx if (bg["overall"][str(x)] or 0) > 0]
        xs = [x for x in allx if x <= (max(nonzero) + 2 if nonzero else 12)]
        series = [("all pairs", [bg["overall"][str(x)] for x in xs])]
        for q in ("1", "4"):
            d = bg["by_topic_q"].get(q) or bg["by_topic_q"].get(int(q))
            if d:
                series.append((f"topic quartile {q}", [d.get(str(x)) for x in xs]))
        q1 = (bg["by_topic_q"].get("1") or bg["by_topic_q"].get(1) or {}).get("6", 0)
        q4 = (bg["by_topic_q"].get("4") or bg["by_topic_q"].get(4) or {}).get("6", 0)
        out.append("<h2>7. How common is a shared passage of a given length?</h2>")
        out.append(RP._chart_row(
            RP.svg_lines(xs, series, "P(longest exact match ≥ L words), seed 6, log scale", ylog=True, xlabel="L (words)"),
            f"<p class='muted'>Among {s.get('cross_issue_pairs', 0):,} cross-issue story pairs (whole table, not sliced), "
            f"{_fmt(bg['p_any'] * 100)}% share a passage of six or more words; the share is {_fmt(q1 * 100)}% in the "
            f"lowest topic-similarity quarter and {_fmt(q4 * 100)}% in the highest. Details, the sampler check and the "
            f"model: <a href='/reuse'>text reuse</a>. Every pair: <a href='/pairs'>pairs</a>.</p>"))
    # 8. progress on the explorer side: complete issues
    out.append("<h2>8. What the explorer covers</h2>")
    out.append(explore_progress_html(con))
    out.append(f"<p class='muted' style='font-size:12.5px'>Database built {_esc(meta_value(con, 'built', ''))} from "
               f"{len(meta_json(con, 'sources', []))} files in {meta_value(con, 'build_seconds', '?')}s · "
               f"{_raw_link('/raw/index', 'what this page was built from')}</p>")
    return _render(render, "Overview", "".join(out), "/overview")


def survey_frame(con):
    """The counts the boards are measured against: the survey of the archive's
    collection (data/survey/summary.json) when it has run, else the protocol
    count."""
    sv = meta_json(con, "survey", {}) or {}
    w = sv.get("working_corpus") or {}
    if sv.get("total_items"):
        return {"have": True, "generated": sv.get("generated", ""), "total": sv["total_items"],
                "by_class": sv.get("by_language_class", {}), "english": sv.get("by_language_class", {}).get("english", 0),
                "unmarked": sv.get("by_language_class", {}).get("unmarked", 0), "other": sv.get("by_language_class", {}).get("other", 0),
                "working": w.get("items", 0), "working_pages": w.get("page_images", 0), "fiction": w.get("fiction_items", 0),
                "fiction_pages": w.get("fiction_page_images", 0), "fiction_magazines": w.get("fiction_magazines", 0),
                "by_kind": sv.get("by_kind", {}), "working_by_kind": w.get("by_kind", {}),
                "fiction_by_decade": w.get("fiction_by_decade", {}), "fiction_by_genre": w.get("fiction_by_genre", {}),
                "magazines_distinct": sv.get("magazines_distinct", 0), "pages_total": sv.get("page_images_total", 0),
                "top": w.get("fiction_magazines_top", [])}
    return {"have": False, "total": ARCHIVE_TOTAL, "working": ARCHIVE_TOTAL, "fiction": ARCHIVE_TOTAL, "fiction_magazines": None}


def _bar(v, of, width=560, height=14):
    pct = (100 * v / of) if of else 0
    return (f"<div style='background:#eee5d5;height:{height}px;max-width:{width}px;border:1px solid #d8cfc0'>"
            f"<div style='background:{RP.PALETTE[0]};height:100%;width:{min(100, pct):.2f}%'></div></div>")


def explore_progress_html(con=None):
    """Complete issues against the working corpus: the explorer's own progress
    line (the workroom board has every step)."""
    con = con or db()
    n_all = _val(con, "SELECT COUNT(*) FROM issues") or 0
    comp = _rows(con, "SELECT id, magazine, cover_date, stories, verified, records, events FROM issues WHERE complete=1 ORDER BY year, id")
    pend = _rows(con, "SELECT id, magazine, cover_date, pages, layout_pages, text_stages FROM issues WHERE complete=0 ORDER BY year, id")
    n_c = len(comp)
    fr = survey_frame(con)
    out = []
    if fr["have"]:
        out.append(f"<p>{n_c:,} of the {n_all:,} selected issues are complete (assembled into records by the machine, human "
                   f"corrections applied as they come) and appear in the explorer. Measured against the collection: the archive's "
                   f"pulp collection holds {fr['total']:,} items, {fr['working']:,} of them in English or with no language given "
                   f"(the working corpus; {fr['other']:,} in other languages are left out), and {fr['fiction']:,} of those are "
                   f"fiction magazines — the frame the complete-issue count is drawn against below.</p>")
        out.append(_bar(n_c, fr["fiction"]))
        out.append(f"<p class='muted' style='font-size:12.5px'>{n_c:,} complete of {fr['fiction']:,} fiction-magazine items "
                   f"({_fmt(100 * n_c / fr['fiction'] if fr['fiction'] else 0)}%). Survey of {_esc(fr['generated'])}, metadata only "
                   f"(pipeline/s00_survey.py).</p>")
    else:
        out.append(f"<p>{n_c} of {n_all} selected issues are complete (assembled into records by the machine, human corrections "
                   f"applied as they come) and appear in the explorer; the archive's pulp collection holds {ARCHIVE_TOTAL:,} items "
                   f"(protocol count; the survey has not run on this server).</p>")
        out.append(_bar(n_c, n_all))
    if pend:
        out.append("<p class='muted'>Not yet complete: " + ", ".join(
            f"<a href='/issue/{_esc(p['id'])}'>{_esc(p['magazine'])} {_esc(p['cover_date'])}</a>" for p in pend[:20])
            + (f" and {len(pend) - 20} more" if len(pend) > 20 else "") + f". Every step per issue: <a href='/reuse/progress'>progress (workroom)</a>.</p>")
    else:
        out.append("<p class='muted'>Every selected issue is complete. Every step per issue: <a href='/reuse/progress'>progress (workroom)</a>.</p>")
    return "".join(out)


# ---------------------------------------------------------------- workroom process board

def process_board_html():
    """Every step of the process against the archive's collection, and every
    issue at every step: for the workroom progress page. The frame is the
    survey (data/survey/summary.json): all items, English and not; the
    complete bar is drawn against the working corpus (English or unmarked),
    fiction magazines only."""
    con = db()
    iss = _rows(con, "SELECT * FROM issues ORDER BY year, id")
    n = len(iss)
    fr = survey_frame(con)
    counts = meta_json(con, "counts", {}) or {}
    out = []
    if fr["have"]:
        bk = fr["by_kind"]
        wbk = fr["working_by_kind"]
        out.append(f"<h3 style='font-weight:normal;font-size:16px'>The collection (survey of {_esc(fr['generated'])}, metadata only)</h3>")
        crows = [["archive items in the collection, every language", N(fr["total"]), _bar(fr["total"], fr["total"], 220),
                  f"<span class='muted'>{fr['pages_total']:,} page images; {fr['magazines_distinct']:,} magazine names as the archive titles them</span>"],
                 ["marked English", N(fr["english"]), _bar(fr["english"], fr["total"], 220), ""],
                 ["no language given (taken as English)", N(fr["unmarked"]), _bar(fr["unmarked"], fr["total"], 220), ""],
                 ["other languages (left out)", N(fr["other"]), _bar(fr["other"], fr["total"], 220),
                  "<span class='muted'>Spanish, French, Italian … — the protocol's corpus is English</span>"],
                 ["working corpus: English or unmarked", N(fr["working"]), _bar(fr["working"], fr["total"], 220),
                  f"<span class='muted'>{fr['working_pages']:,} page images</span>"],
                 ["of which fiction magazines (pulps and digests)", N(fr["fiction"]), _bar(fr["fiction"], fr["total"], 220),
                  f"<span class='muted'>{fr['fiction_pages']:,} page images, {fr['fiction_magazines']:,} magazine names; the rest: "
                  + ", ".join(f"{k} {v:,}" for k, v in wbk.items() if k != "fiction magazine") + "</span>"]]
        out.append(_table(["the collection", "#items", "share of the collection", "note"], crows))
        dec = fr["fiction_by_decade"]
        if dec:
            cats = [d for d in dec if d != "no year"]
            out.append(RP._chart_row(RP.svg_bars(cats, [("fiction-magazine items", [dec[d] for d in cats])],
                                                 "Fiction magazines in the working corpus, by decade"),
                                     "<p class='muted' style='font-size:12.5px'>By genre, as the archive files them: "
                                     + ", ".join(f"{_esc(k)} {v:,}" for k, v in fr["fiction_by_genre"].items())
                                     + f". {dec.get('no year', 0):,} items carry no year. Every magazine name with its item count: "
                                       "<a href='/raw/file?path=survey/magazines.json'>survey/magazines.json</a>.</p>"))
    of = fr["fiction"]
    steps = [("surveyed (metadata from the archive)", fr["total"] if fr["have"] else 0, fr["total"], "every item, every language"),
             ("issues selected for processing", n, of, "the pilot development set; the full-study sample after protocol acceptance"),
             ("downloaded (archive record on disk)", sum(1 for i in iss if _j(i["ia"], {}).get("title") or i["pages"]), of, ""),
             ("page images on disk", sum(1 for i in iss if i["pages"]), of, f"{sum(i['pages'] for i in iss):,} pages"),
             ("read by layout OCR (layout records)", sum(1 for i in iss if i["layout_pages"]), of, f"{sum(i['layout_pages'] for i in iss):,} pages"),
             ("text stages present", sum(1 for i in iss if _j(i["text_stages"], [])), of, ""),
             ("assembled into records (complete)", sum(1 for i in iss if i["assembled"] or i["exported"]), of,
              f"{sum(i['assembled'] for i in iss):,} records in the assemblies"),
             ("exported for the reuse run", sum(1 for i in iss if i["exported"]), of, f"{sum(i['exported'] for i in iss):,} records"),
             ("touched by an annotator", sum(1 for i in iss if i["events"]), of, f"{sum(i['events'] for i in iss):,} actions"),
             ("with verified stories", sum(1 for i in iss if i["verified"]), of, f"{sum(i['verified'] for i in iss):,} stories verified"),
             ("fully verified", sum(1 for i in iss if i["stories"] and i["verified"] == i["stories"]), of, "")]
    rows = []
    for label, v, den, note in steps:
        pct = (100 * v / den) if den else 0
        rows.append([_esc(label), N(v), _bar(v, den, 220, 12).replace("max-width:220px", "width:220px;display:inline-block;vertical-align:middle")
                     + f" <span class='muted' style='font-size:12px'>{_fmt(pct)}% of {den:,}</span>", f"<span class='muted'>{_esc(note)}</span>"])
    out.append("<h3 style='font-weight:normal;font-size:16px'>The process, issue by issue, against the working corpus's fiction magazines</h3>")
    out.append(_table(["step", "#issues", "share", "note"], rows))
    out.append(f"<p class='muted'>What the explorer holds now: {counts.get('magazines', 0):,} magazines, {counts.get('complete_issues', 0):,} "
               f"complete issues, {counts.get('records', 0):,} records of all kinds, {counts.get('stories', 0):,} stories, "
               f"{counts.get('authors', 0):,} named authors"
               + (f"; advertisements by class: " + ", ".join(f"{_esc(k)} {v:,}" for k, v in (counts.get("ad_classes") or {}).items())
                  + f"; {counts.get('house_excerpts', 0):,} house announcements quoting a story" if counts.get("ad_classes") else "") + ".</p>")
    irows = []
    for i in iss:
        stages = _j(i["text_stages"], [])
        done = "yes" if i["complete"] else "—"
        irows.append([_issue_link(con, i["id"], i), N(i["pages"]), N(i["layout_pages"]), _esc(", ".join(stages)) or "—",
                      N(i["assembled"] or ""), N(i["exported"] or ""), N(i["stories"] or ""), N(i["events"] or ""),
                      N(i["modified"] or ""), N(i["verified"] or ""), done])
    out.append("<h3 style='font-weight:normal;font-size:16px'>Every issue, every step</h3>")
    out.append(_table(["issue", "#pages", "#layout pages", "text stages", "#assembled", "#exported", "#stories", "#actions",
                       "#modified", "#verified", "complete"], irows))
    out.append("<p class='muted'>Complete = assembled into records by the machine; such issues appear on the explorer "
               "side. The survey is metadata only (decision of 2026-09-03: allowed before protocol acceptance); the downloader's "
               "gate is untouched. The full-study rows (rolling download, reading, assembly, the stratified verification sample) "
               "fill in after acceptance; the board keeps the same shape.</p>")
    return "".join(out)


# ---------------------------------------------------------------- authors

def authors_page(qs, render=None):
    con = db()
    q = _g(qs, "q").strip().lower()
    sort = _g(qs, "sort", "name") or "name"
    sl = _slice(qs)
    conds, args = [], []
    if q:
        conds.append("(LOWER(display) LIKE ? OR key LIKE ? OR LOWER(names) LIKE ?)")
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if sl["genre"]:
        conds.append("genres LIKE ?")
        args.append(f'%"{sl["genre"]}"%')
    if sl["mag"]:
        conds.append("magazines LIKE ?")
        args.append("%" + json.dumps(_val(con, "SELECT name FROM magazines WHERE slug=?", (sl["mag"],)) or "\x00") + "%")
    if sl["decade"] is not None:
        conds.append("key IN (SELECT DISTINCT author_key FROM records WHERE is_story=1 AND decade=?)")
        args.append(sl["decade"])
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    order = {"stories": "n_stories DESC, last_name, display", "words": "n_words DESC, last_name, display",
             "shared": "degree DESC, n_stories DESC, last_name, display",
             "name": "last_name, display", "first": "first_year, last_name, display"}.get(sort, "last_name, display")
    total = _val(con, f"SELECT COUNT(*) FROM authors{where}", args)
    pager, lim, off = _pager(qs, total, "/authors")
    items = _rows(con, f"SELECT * FROM authors{where} ORDER BY {order} LIMIT ? OFFSET ?", args + [lim, off])
    rows = []
    for a in items:
        mags = _j(a["magazines"], [])
        rows.append([_author_link_key(a["key"], a["display"]), N(a["n_stories"]), N(a["n_words"]), N(a["n_issues"]),
                     ", ".join(_mag_link(m) for m in mags), _esc(", ".join(_j(a["genres"], []))),
                     _esc(f"{int(a['first_year'])}–{int(a['last_year'])}" if a["first_year"] and a["last_year"] and int(a["first_year"]) != int(a["last_year"])
                          else (str(int(a["first_year"])) if a["first_year"] else "")), N(a["degree"])])
    decades = [r["decade"] for r in _rows(con, "SELECT DISTINCT decade FROM issues WHERE complete=1 AND decade IS NOT NULL ORDER BY decade")]
    genres = [r["genre"] for r in _rows(con, "SELECT DISTINCT genre FROM issues WHERE complete=1 AND genre IS NOT NULL ORDER BY genre")]
    mags_all = _rows(con, "SELECT name, slug FROM magazines ORDER BY name")
    form = (f"<form method='GET' action='/authors' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>name contains <input name='q' value='{_esc(q)}' style='width:140px'></label>"
            f"<label>decade {_sel('decade', sl['decade'] if sl['decade'] is not None else '', [('', 'all')] + [(d, f'{d}s') for d in decades])}</label>"
            f"<label>genre {_sel('genre', sl['genre'] or '', [('', 'all')] + [(g, g) for g in genres])}</label>"
            f"<label>magazine {_sel('mag', sl['mag'] or '', [('', 'all')] + [(m['slug'], m['name']) for m in mags_all])}</label>"
            f"<label>sort by {_sel('sort', sort, [('name', 'last name'), ('stories', 'stories'), ('words', 'words'), ('shared', 'shared passages'), ('first', 'first year')])}</label>"
            f"<button>show</button></form>")
    unnamed = _val(con, "SELECT COUNT(*) FROM records WHERE is_story=1 AND author_key IS NULL")
    body = (_howto("Layer 1: every printed by-line, normalized (case, punctuation, and titles such as "
                   "'Captain' removed; pseudonyms NOT resolved — that is implementation-plan item 0.4), shown in "
                   "title case and listed in alphabetical order of last names; the forms as printed are on each "
                   "author's page. 'Shared passages' counts exact six-word matches between this author's stories "
                   "and stories by other authors in other issues. One hundred authors a page.")
            + f"<h1>Authors ({total:,})</h1>" + form + pager
            + _table(["author", "#stories", "#words", "#issues", "magazines", "genres", "years", "#shared passages"], rows)
            + pager + f"<p class='muted'>{unnamed:,} stories carry no usable by-line and appear only under their issues. "
              f"{_raw_link('/raw/authors', 'raw list')}</p>")
    return _render(render, "Authors", body, "/authors")


def author_page(slug, render=None, qs=None):
    qs = qs or {}
    con = db()
    key = author_unslug(slug)
    a = _one(con, "SELECT * FROM authors WHERE key=?", (key,))
    if not a:
        return _render(render, "Author", "<h1>No such author</h1><p><a href='/authors'>all authors</a></p>", "/authors")
    name = a["display"]
    names = _j(a["names"], {})
    printed = "; ".join(f"{n}" + (f" ×{c}" if c > 1 else "") for n, c in sorted(names.items(), key=lambda t: -t[1]))
    total = a["n_stories"]
    pager, lim, off = _pager(qs, total, f"/author/{slug}")
    stories = _rows(con, "SELECT * FROM records WHERE author_key=? AND is_story=1 ORDER BY year, id LIMIT ? OFFSET ?", (key, lim, off))
    rows = [[_story_link(con, r["id"], with_author=False, rec=r), _issue_link(con, r["issue"]), _esc(r["date"] or ""), N(r["n_words"]),
             _esc(r["status"]), N(r["n_exact"]), N(r["n_para"])] for r in stories]
    partners = _rows(con, "SELECT * FROM author_links WHERE ka=? OR kb=? ORDER BY n DESC, longest DESC LIMIT 100", (key, key))
    prow = []
    for L in partners:
        other = L["kb"] if L["ka"] == key else L["ka"]
        on = _val(con, "SELECT display FROM authors WHERE key=?", (other,)) or other
        pairs = _j(L["pairs"], [])
        plinks = " · ".join(f"<a href='/pair/{_esc(x)}/{_esc(y)}'>{_esc(x)} ~ {_esc(y)}</a>" for x, y in pairs[:6])
        if len(pairs) > 6:
            plinks += f" <a href='/pairs?author={_esc(slug)}&min=6' class='muted'>… all</a>"
        prow.append([_author_link_key(other, on), N(L["n"]), N(L["longest"]), plinks])
    ms = _rows(con, "SELECT m.* FROM matches m WHERE m.k=6 AND m.same_issue=0 AND (m.a IN (SELECT id FROM records WHERE author_key=?) "
                    "OR m.b IN (SELECT id FROM records WHERE author_key=?)) ORDER BY m.len DESC LIMIT 200", (key, key))
    mine = {r["id"] for r in _rows(con, "SELECT id FROM records WHERE author_key=?", (key,))}
    mrows = []
    for m in ms:
        this, other = (m["a"], m["b"]) if m["a"] in mine else (m["b"], m["a"])
        mrows.append([N(m["len"]), _esc(m["excerpt"][:120]), _story_link(con, this, False), _story_link(con, other),
                      f"<a href='/pair/{_esc(m['a'])}/{_esc(m['b'])}'>pair</a>"])
    body = (_howto("Layer 2: one author. The stories table links to each story's page; the partners table "
                   "lists the other authors whose stories share passages with these, with the story pairs; "
                   "the passages table is every shared passage itself (longest 200), linked to the pair page where "
                   "it can be read in both stories and followed to the scan.")
            + f"<h1>{_esc(name)}</h1>"
            + f"<p class='muted'>{total:,} stories · {a['n_words']:,} words · {a['n_issues']:,} issues · "
              f"{', '.join(_mag_link(m) for m in _j(a['magazines'], []))} · {', '.join(_esc(g) for g in _j(a['genres'], []))} · "
              f"{_raw_link('/raw/author/' + slug)}</p>"
            + f"<p class='muted' style='font-size:12px'>printed as: {_esc(printed)} · normalized key: {_esc(key)}</p>"
            + "<h2>Stories</h2>" + pager + _table(["story", "issue", "date", "#words", "status", "#shared passages", "#paraphrase alignments"], rows) + pager
            + "<h2>Shares passages with</h2>"
            + (_table(["author", "#passages", "#longest", "story pairs"], prow) if prow else
               "<div class='empty'>No shared passages with other named authors.</div>")
            + "<h2>The shared passages</h2>"
            + (_table(["#words", "passage", "in this author's story", "shared with", ""], mrows) if mrows else "<div class='empty'>None.</div>"))
    return _render(render, name, body, f"/author/{slug}")


# ---------------------------------------------------------------- magazines

def magazines_page(qs=None, render=None):
    qs = qs or {}
    con = db()
    q = _g(qs, "q").strip().lower()
    sort = _g(qs, "sort", "first") or "first"
    sl = _slice(qs)
    conds, args = [], []
    if q:
        conds.append("LOWER(name) LIKE ?")
        args.append(f"%{q}%")
    if sl["genre"]:
        conds.append("genre=?")
        args.append(sl["genre"])
    if sl["decade"] is not None:
        conds.append("name IN (SELECT DISTINCT magazine FROM issues WHERE decade=?)")
        args.append(sl["decade"])
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    order = {"first": "first_year, name", "name": "name", "issues": "n_issues DESC, name", "stories": "stories DESC, name",
             "words": "words DESC, name"}.get(sort, "first_year, name")
    total = _val(con, f"SELECT COUNT(*) FROM magazines{where}", args)
    pager, lim, off = _pager(qs, total, "/magazines")
    mags = _rows(con, f"SELECT * FROM magazines{where} ORDER BY {order} LIMIT ? OFFSET ?", args + [lim, off])
    rows = []
    for m in mags:
        partners = _rows(con, "SELECT ma, mb, n FROM mag_links WHERE ma=? OR mb=? ORDER BY n DESC LIMIT 3", (m["name"], m["name"]))
        issues = _j(m["issues"], [])
        span = (f"{int(m['first_year'])}–{int(m['last_year'])}" if m["first_year"] and m["last_year"] and int(m["first_year"]) != int(m["last_year"])
                else (str(int(m["first_year"])) if m["first_year"] else ""))
        about = (_esc(m["genre"] or "") + (f" · {_esc(m['format'])}" if m.get("format") else "")
                 + (f"<br><span class='muted'>{_esc(m['publisher_group'])}</span>" if m.get("publisher_group") else "")
                 + (f"<br><span class='muted' style='font-size:12px'>{_esc('; '.join(_j(m['publishers'], [])))}</span>" if _j(m["publishers"], []) else ""))
        shares = "<br>".join(
            (f"{_mag_link(p['mb'] if p['ma'] == m['name'] else p['ma'])}" if (p['mb'] if p['ma'] == m['name'] else p['ma']) != m["name"]
             else "another issue of the same magazine") + f" <span class='muted'>{p['n']:,} passages</span>"
            for p in partners) or "<span class='muted'>none</span>"
        counts = (f"{m['n_issues']:,} issues · {m['records']:,} records · <b>{m['stories']:,} stories</b><br>"
                  f"<span class='muted'>{m['words']:,} words · {m['n_authors']:,} authors · {m['verified']:,} verified</span>")
        rows.append([f"{_mag_link(m['name'], m['slug'])}<br><span class='muted'>{_esc(span)}</span> · "
                     f"<a href='/issues?mag={_esc(m['slug'])}' class='muted'>issues</a>",
                     about, counts, shares])
    decades = [r["decade"] for r in _rows(con, "SELECT DISTINCT decade FROM issues WHERE decade IS NOT NULL ORDER BY decade")]
    genres = [r["genre"] for r in _rows(con, "SELECT DISTINCT genre FROM magazines WHERE genre IS NOT NULL ORDER BY genre")]
    form = (f"<form method='GET' action='/magazines' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>name contains <input name='q' value='{_esc(q)}' style='width:140px'></label>"
            f"<label>decade {_sel('decade', sl['decade'] if sl['decade'] is not None else '', [('', 'all')] + [(d, f'{d}s') for d in decades])}</label>"
            f"<label>genre {_sel('genre', sl['genre'] or '', [('', 'all')] + [(g, g) for g in genres])}</label>"
            f"<label>sort by {_sel('sort', sort, [('first', 'first year'), ('name', 'name'), ('issues', 'issues'), ('stories', 'stories'), ('words', 'words')])}</label>"
            f"<button>show</button></form>")
    body = (_howto("Layer 1: the magazines, with the counts that roll up from their stories. Names are given in "
                   "full everywhere. Publisher names come from pipeline/publishers.json (masthead or reference; the file "
                   "says which). 'Shares passages with' lists the three magazines whose stories share most six-word "
                   "passages with this one's, one per line; the magazine's own page has the full list and its issues. "
                   "One hundred magazines a page.")
            + f"<h1>Magazines ({total:,})</h1>" + form + pager
            + _table(["magazine", "genre · format · publisher", "what the explorer holds", "shares passages with"], rows)
            + pager + f"<p class='muted'>{_raw_link('/raw/magazines', 'raw list')}</p>")
    return _render(render, "Magazines", body, "/magazines")


def magazine_page(slug, render=None, qs=None):
    qs = qs or {}
    con = db()
    m = _one(con, "SELECT * FROM magazines WHERE slug=?", (slug,))
    if not m:
        return _render(render, "Magazine", "<h1>No such magazine</h1>", "/magazines")
    total = m["n_issues"]
    pager, lim, off = _pager(qs, total, f"/magazine/{slug}")
    issues = _rows(con, "SELECT * FROM issues WHERE magazine=? ORDER BY year, id LIMIT ? OFFSET ?", (m["name"], lim, off))
    irows = [[_issue_link(con, i["id"], i), N(i["pages"]), N(i["records"]), N(i["stories"]), N(i["words"]),
              N(i["n_authors"]), N(i["verified"]), N(i["modified"]), N(i["events"]),
              _esc(i["publisher"] or ""), f"<a href='https://archive.org/details/{_esc(i['ia_identifier'])}'>{_esc(i['ia_identifier'])}</a>",
              "yes" if i["complete"] else "—"] for i in issues]
    authors = _rows(con, "SELECT a.key, a.display, COUNT(*) AS n FROM records r JOIN authors a ON a.key=r.author_key "
                         "WHERE r.magazine=? AND r.is_story=1 GROUP BY a.key ORDER BY n DESC, a.display LIMIT 40", (m["name"],))
    arows = [[_author_link_key(a["key"], a["display"]), N(a["n"])] for a in authors]
    partners = []
    for L in _rows(con, "SELECT * FROM mag_links WHERE ma=? OR mb=? ORDER BY n DESC LIMIT 40", (m["name"], m["name"])):
        o = L["mb"] if L["ma"] == m["name"] else L["ma"]
        partners.append([_mag_link(o) if o != m["name"] else _esc(o) + " (another issue)", N(L["n"]), N(L["longest"]),
                         f"<a href='/pairs?ma={urllib.parse.quote(m['name'])}&mb={urllib.parse.quote(o)}&min=6'>pairs</a>"])
    body = (_howto("Layer 2: one magazine. Issues link to the issue page (scans, articles, provenance); "
                   "authors to their pages; the sharing table to the story pairs between this magazine and another.")
            + f"<h1>{_esc(m['name'])}</h1>"
            + f"<p class='muted'>{_esc(m['genre'] or '')} · {_esc(m['format'] or '')} · publisher {_esc(m['publisher_group'] or 'unknown')} · "
              f"{m['n_issues']:,} issues · {m['stories']:,} stories · {m['words']:,} words · {_raw_link('/raw/magazine/' + slug)}</p>"
            + "<h2>Issues</h2>" + pager + _table(["issue", "#pages", "#records", "#stories", "#words", "#authors", "#verified",
                                                  "#modified", "#annotation events", "publisher", "archive item", "complete"], irows) + pager
            + "<h2>Authors</h2>" + (_table(["author", "#stories"], arows) + (f"<p class='muted'><a href='/authors?mag={_esc(slug)}'>all authors of this magazine</a></p>"
                                                                            if len(arows) == 40 else "") if arows else "<div class='empty'>No usable by-lines.</div>")
            + "<h2>Shares passages with</h2>" + (_table(["magazine", "#passages", "#longest", ""], partners) if partners
                                                 else "<div class='empty'>None.</div>"))
    return _render(render, m["name"], body, f"/magazine/{slug}")


# ---------------------------------------------------------------- issues (the explorer's list)

def issues_page(qs=None, render=None):
    """Every issue the explorer holds, paged, with filters: built for the
    whole corpus (the workroom's list of the pilot's ten issues with their
    processing stages is /workroom/issues)."""
    qs = qs or {}
    con = db()
    q = _g(qs, "q").strip().lower()
    sort = _g(qs, "sort", "date") or "date"
    complete = _g(qs, "complete", "1")
    sl = _slice(qs)
    conds, args = [], []
    if complete == "1":
        conds.append("complete=1")
    elif complete == "0":
        conds.append("complete=0")
    if q:
        conds.append("(LOWER(magazine) LIKE ? OR LOWER(id) LIKE ? OR LOWER(cover_date) LIKE ? OR LOWER(ia_identifier) LIKE ?)")
        args += [f"%{q}%"] * 4
    sc, sa = _slice_sql(sl)
    conds += sc
    args += sa
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    order = {"date": "year, id", "magazine": "magazine, year, id", "stories": "stories DESC, year, id",
             "words": "words DESC, year, id", "verified": "verified DESC, year, id", "pages": "pages DESC, year, id"}.get(sort, "year, id")
    total = _val(con, f"SELECT COUNT(*) FROM issues{where}", args)
    pager, lim, off = _pager(qs, total, "/issues")
    items = _rows(con, f"SELECT * FROM issues{where} ORDER BY {order} LIMIT ? OFFSET ?", args + [lim, off])
    rows = []
    for i in items:
        rows.append([_issue_link(con, i["id"], i), _mag_link(i["magazine"], i["mag_slug"]), _esc(i["cover_date"] or ""),
                     _esc(i["genre"] or ""), _esc(i["format"] or ""), N(i["pages"]), N(i["records"]), N(i["stories"]), N(i["words"]),
                     N(i["n_authors"]), N(i["verified"]), "yes" if i["complete"] else "—",
                     f"<a href='https://archive.org/details/{_esc(i['ia_identifier'])}' class='muted'>{_esc(i['ia_identifier'])}</a>" if i.get("ia_identifier") else ""])
    decades = [r["decade"] for r in _rows(con, "SELECT DISTINCT decade FROM issues WHERE decade IS NOT NULL ORDER BY decade")]
    genres = [r["genre"] for r in _rows(con, "SELECT DISTINCT genre FROM issues WHERE genre IS NOT NULL ORDER BY genre")]
    mags_all = _rows(con, "SELECT name, slug FROM magazines ORDER BY name")
    form = (f"<form method='GET' action='/issues' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>magazine {_sel('mag', sl['mag'] or '', [('', 'all')] + [(m['slug'], m['name']) for m in mags_all])}</label>"
            f"<label>decade {_sel('decade', sl['decade'] if sl['decade'] is not None else '', [('', 'all')] + [(d, f'{d}s') for d in decades])}</label>"
            f"<label>genre {_sel('genre', sl['genre'] or '', [('', 'all')] + [(g, g) for g in genres])}</label>"
            f"<label>{_sel('complete', complete, [('1', 'complete issues'), ('0', 'not yet complete'), ('all', 'all selected issues')])}</label>"
            f"<label>text <input name='q' value='{_esc(q)}' style='width:140px'></label>"
            f"<label>sort {_sel('sort', sort, [('date', 'cover date'), ('magazine', 'magazine'), ('stories', 'stories'), ('words', 'words'), ('verified', 'verified'), ('pages', 'pages')])}</label>"
            f"<button>show</button></form>")
    n_all = _val(con, "SELECT COUNT(*) FROM issues") or 0
    n_c = _val(con, "SELECT COUNT(*) FROM issues WHERE complete=1") or 0
    sv = meta_json(con, "survey", {}) or {}
    frame = ""
    if sv.get("total_items"):
        w = sv.get("working_corpus", {})
        frame = (f"<p class='muted'>The frame: the archive's pulp collection holds {sv['total_items']:,} items "
                 f"(survey of {_esc(sv.get('generated', ''))}); {w.get('items', 0):,} are marked English or unmarked, of which "
                 f"{w.get('fiction_items', 0):,} are fiction magazines in {w.get('fiction_magazines', 0):,} magazine names. "
                 f"{n_c:,} of the {n_all:,} issues selected so far are complete and listed here; the whole process against "
                 f"the collection is on <a href='/reuse/progress'>progress</a>.</p>")
    body = (_howto("Layer 1: the issues the explorer holds — complete ones by default (assembled into records by the "
                   "machine, human corrections applied as they come). Built for the whole corpus: one hundred issues a page, "
                   "filters by magazine, decade, genre and completeness, a text search over magazine, id, date and archive "
                   "item. Each issue opens on its page (scans, records, provenance). The workroom's list of the pilot's "
                   "issues with their processing stages is <a href='/workroom/issues'>workroom/issues</a>.")
            + f"<h1>Issues ({total:,})</h1>" + frame + form + pager
            + _table(["issue", "magazine", "cover date", "genre", "format", "#pages", "#records", "#stories", "#words",
                      "#authors", "#verified", "complete", "archive item"], rows)
            + pager + f"<p class='muted'>{_raw_link('/raw/index', 'raw index')}</p>")
    return _render(render, "Issues", body, "/issues")


# ---------------------------------------------------------------- issue extras

def issue_extra_html(iid):
    """Sections appended to the existing issue page: provenance, census,
    reuse, assembly diagnostics, raw links."""
    con = db()
    i = _one(con, "SELECT * FROM issues WHERE id=?", (iid,))
    if not i:
        return ""
    ia = _j(i["ia"], {})
    files = _j(i["ia_files"], [])
    prov = [("archive item", f"<a href='https://archive.org/details/{_esc(i['ia_identifier'])}'>{_esc(i['ia_identifier'])}</a>"),
            ("archive title", _esc(ia.get("title") or "")),
            ("uploaded by", _esc(ia.get("uploader") or "")), ("added to the archive", _esc(ia.get("addeddate") or "")),
            ("scanner / uploader software", _esc(ia.get("scanner") or "")),
            ("archive OCR", _esc(ia.get("ocr") or "")),
            ("collections", _esc(", ".join(ia.get("collection") or []) if isinstance(ia.get("collection"), list) else (ia.get("collection") or ""))),
            ("page images in the item", _esc(ia.get("imagecount") or "")),
            ("item size", (f"{int(i['ia_item_size']) / 1e6:,.0f} MB" if i.get("ia_item_size") else "")),
            ("file formats offered", _esc(", ".join(files))[:300]),
            ("publisher", _esc(i["publisher"] or "unknown") + (f" <span class='muted'>({_esc(i['publisher_source'])})</span>" if i.get("publisher_source") else "")),
            ("why this issue is in the pilot", _esc(i["why"] or ""))]
    ptable = "<table>" + "".join(f"<tr><th style='text-align:left;width:220px'>{_esc(k)}</th><td>{v}</td></tr>" for k, v in prov if v) + "</table>"
    authors = _rows(con, "SELECT a.key, a.display, COUNT(*) AS n FROM records r JOIN authors a ON a.key=r.author_key "
                         "WHERE r.issue=? AND r.is_story=1 GROUP BY a.key ORDER BY n DESC, a.display", (iid,))
    alinks = ", ".join(_author_link_key(a["key"], a["display"]) for a in authors)
    partner = _rows(con, "SELECT ia, ib, n FROM issue_links WHERE ia=? OR ib=? ORDER BY n DESC", (iid, iid))
    prows = [[_issue_link(con, p["ib"] if p["ia"] == iid else p["ia"]), N(p["n"]),
              f"<a href='/pairs?ia={urllib.parse.quote(iid)}&ib={urllib.parse.quote(p['ib'] if p['ia'] == iid else p['ia'])}&min=6'>pairs</a>"]
             for p in partner]
    ov = (meta_json(con, "overlap", {}) or {}).get(iid)
    orow = ""
    if ov:
        wp = "; ".join(f"<a href='/story/{_esc(p['a'])}'>{_esc(p['a'])}</a> ~ <a href='/story/{_esc(p['b'])}'>{_esc(p['b'])}</a> ({p['shared_keys']})"
                       for p in ov["worst_pairs"][:5])
        orow = (f"<p>{ov['keys_owned_by_2plus']} of {ov['region_keys']} text regions are listed under more than one record "
                f"({ov['stories_sharing']} records involved). {('Worst pairs: ' + wp) if wp else ''}</p>")
    stages = _j(i["text_stages"], [])
    steps = [("page images", i["pages"]), ("layout records", i["layout_pages"]), ("text stages", ", ".join(stages) or "none"),
             ("assembled records", i["assembled"] or "none on disk"), ("exported records", i["exported"]),
             ("annotation actions", i["events"]), ("complete", "yes" if i["complete"] else "no")]
    out = ["<h2>Provenance (from the Internet Archive item)</h2>", ptable,
           "<h2>Census</h2>",
           f"<p>{i['records']} records, {i['stories']} stories ({i['words']:,} words), {i['verified']} verified, "
           f"{i['modified']} modified, {i['events']} annotation events. "
           f"Authors: {alinks or '<span class=muted>no usable by-lines</span>'}. "
           f"<a href='/stories?issue={_esc(iid)}&type=all'>all records of this issue</a>.</p>",
           "<p class='muted'>Process: " + " · ".join(f"{k} {_esc(str(v))}" for k, v in steps) + ".</p>",
           "<h2>Shared passages with other issues</h2>",
           _table(["other issue", "#passages (seed 6)", ""], prows) if prows else "<div class='empty'>None.</div>",
           "<h2>Assembly check</h2>", orow or "<p class='muted'>No region is listed under two records in this issue.</p>",
           f"<p class='muted'>{_raw_link('/raw/issue/' + iid)} · <a href='/reuse/clusters?same=1&kind=exact&k=6'>same-issue diagnostics</a></p>"]
    return "".join(out)


# ---------------------------------------------------------------- stories

def stories_page(qs, render=None):
    con = db()
    typ = _g(qs, "type", "story")
    mag = _g(qs, "mag")
    issue = _g(qs, "issue")
    status = _g(qs, "status")
    author = _g(qs, "author")
    q = _g(qs, "q").strip().lower()
    sort = _g(qs, "sort", "issue")
    sl = _slice(qs)
    try:
        min_w = int(_g(qs, "min", "0") or 0)
    except ValueError:
        min_w = 0
    try:
        year = int(_g(qs, "year") or 0) or None
    except ValueError:
        year = None
    conds, args = [], []
    ad_class = _g(qs, "ad_class")
    if typ == "story":
        conds.append("is_story=1")
    elif typ != "all":
        conds.append("type=?")
        args.append(typ)
    if ad_class:
        conds.append("ad_class=?")
        args.append(ad_class)
    if mag or sl["mag"]:
        conds.append("(mag_slug=? OR magazine=?)")
        args += [mag or sl["mag"], mag or sl["mag"]]
    if issue:
        conds.append("issue=?")
        args.append(issue)
    if status:
        conds.append("status=?")
        args.append(status)
    if author:
        conds.append("author_key=?")
        args.append(author_unslug(author))
    if sl["decade"] is not None:
        conds.append("decade=?")
        args.append(sl["decade"])
    if year:
        conds.append("CAST(year AS INTEGER)=?")
        args.append(year)
    if sl["genre"]:
        conds.append("genre=?")
        args.append(sl["genre"])
    if q:
        conds.append("(LOWER(title) LIKE ? OR LOWER(author) LIKE ? OR id LIKE ? OR LOWER(teaser) LIKE ?)")
        args += [f"%{q}%"] * 4
    if min_w:
        conds.append("n_words>=?")
        args.append(min_w)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    order = {"issue": "year, id", "words": "n_words DESC, id", "title": "LOWER(title), id", "shared": "n_exact DESC, id",
             "para": "n_para DESC, id"}.get(sort, "id")
    total = _val(con, f"SELECT COUNT(*) FROM records{where}", args)
    pager, lim, off = _pager(qs, total, "/stories")
    recs = _rows(con, f"SELECT * FROM records{where} ORDER BY {order} LIMIT ? OFFSET ?", args + [lim, off])
    types = ["story", "all"] + [r["type"] for r in _rows(con, "SELECT DISTINCT type FROM records WHERE type<>'story' AND type IS NOT NULL ORDER BY type")]
    mags_all = _rows(con, "SELECT name, slug FROM magazines ORDER BY name")
    issues_all = [r["id"] for r in _rows(con, "SELECT id FROM issues ORDER BY id")]
    decades = [r["decade"] for r in _rows(con, "SELECT DISTINCT decade FROM issues WHERE decade IS NOT NULL ORDER BY decade")]
    genres = [r["genre"] for r in _rows(con, "SELECT DISTINCT genre FROM issues WHERE genre IS NOT NULL ORDER BY genre")]
    form = (f"<form method='GET' action='/stories' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>type {_sel('type', typ, [(t, t) for t in types])}</label>"
            f"<label>decade {_sel('decade', sl['decade'] if sl['decade'] is not None else '', [('', 'all')] + [(d, f'{d}s') for d in decades])}</label>"
            f"<label>genre {_sel('genre', sl['genre'] or '', [('', 'all')] + [(g, g) for g in genres])}</label>"
            f"<label>magazine {_sel('mag', mag or sl['mag'] or '', [('', 'all')] + [(m['slug'], mag_abbr(m['name'])) for m in mags_all])}</label>"
            + (f"<label>issue {_sel('issue', issue, [('', 'all')] + [(i, i) for i in issues_all])}</label>" if len(issues_all) <= 200
               else f"<label>issue <input name='issue' value='{_esc(issue)}' style='width:120px'></label>")
            + f"<label>status {_sel('status', status, [('', 'any'), ('auto', 'automatic'), ('modified', 'modified'), ('verified', 'verified')])}</label>"
            + f"<label>ad class {_sel('ad_class', ad_class, [('', 'any')] + [(c, c) for c in ('house_next_issue', 'house_self', 'house_sibling', 'house_form', 'trade', 'classified')])}</label>"
            f"<label>min words <input name='min' value='{min_w}' style='width:56px'></label>"
            f"<label>text <input name='q' value='{_esc(q)}' style='width:140px'></label>"
            f"<label>sort {_sel('sort', sort, [('issue', 'issue'), ('words', 'words'), ('title', 'title'), ('shared', 'shared passages'), ('para', 'paraphrase alignments')])}</label>"
            f"<input type='hidden' name='author' value='{_esc(author)}'>" + (f"<input type='hidden' name='year' value='{year}'>" if year else "")
            + "<button>show</button></form>")
    rows = []
    for r in recs:
        pages = _j(r["pages"], [])
        typ_txt = _esc(r["type"]) + (f" <span class='muted'>· {_esc(r['ad_class'])}</span>" if r["type"] == "ad" and r["ad_class"] else "") \
            + (" <span class='muted'>· quotes a story</span>" if r["contains_excerpt"] else "")
        rows.append([_story_link(con, r["id"], rec=r), typ_txt, _issue_link(con, r["issue"]), _esc(r["date"] or ""),
                     _esc(",".join(str(p) for p in pages[:3]) + ("…" if len(pages) > 3 else "")),
                     N(r["n_words"]), N(r["n_regions"]), _esc(r["status"]), N(r["n_exact"]), N(r["n_para"]),
                     f"<a href='/article/{_esc(r['id'])}' class='muted'>workbench</a>"])
    body = (_howto("Layer 1: every record the assembly produced — stories by default; switch the type to see "
                   "advertisements, features, poems, or everything. Counts and status are as exported for the "
                   "reuse run; the workbench link shows the record as it stands now with its scan regions. "
                   "One hundred records a page.")
            + f"<h1>Records — {total:,} match</h1>" + form + pager
            + _table(["title — author", "type", "issue", "date", "pages", "#words", "#regions", "status", "#shared", "#paraphrase", ""], rows)
            + pager + f"<p class='muted'>{_raw_link('/raw/stories', 'raw export (paged JSON)')} · "
              f"{_raw_link('/raw/file?path=pilot_stories.jsonl.gz', 'download the export')}</p>")
    return _render(render, "Records", body, "/stories")


def story_page(sid, render=None):
    con = db()
    r = _one(con, "SELECT * FROM records WHERE id=?", (sid,))
    if not r:
        return _render(render, "Story", f"<h1>No such record</h1><p class='muted'>{_esc(sid)} is not in the export; "
                                        f"<a href='/article/{_esc(sid)}'>try the workbench</a>.</p>", "/stories")
    live, _doc = _G["article_by_id"](sid)
    live_status = (live or {}).get("status")
    live_title = (live or {}).get("title")
    pages = _j(r["pages"], [])
    ex = _rows(con, "SELECT * FROM matches WHERE k=6 AND same_issue=0 AND (a=? OR b=?) ORDER BY len DESC LIMIT 300", (sid, sid))
    erows = []
    for m in ex:
        other = m["b"] if m["a"] == sid else m["a"]
        oi = m["b_issue"] if m["a"] == sid else m["a_issue"]
        erows.append([N(m["len"]), _esc(m["excerpt"][:140]), _story_link(con, other), _issue_link(con, oi),
                      f"<a href='/pair/{_esc(m['a'])}/{_esc(m['b'])}'>pair</a>"])
    pa = _rows(con, "SELECT * FROM aligns WHERE a=? OR b=? ORDER BY cols DESC LIMIT 300", (sid, sid))
    prows = [[N(a["cols"]), N(a["identity"]), _esc((a["text_a"] if a["a"] == sid else a["text_b"])[:140]),
              _story_link(con, a["b"] if a["a"] == sid else a["a"]), f"<a href='/pair/{_esc(a['a'])}/{_esc(a['b'])}'>pair</a>"] for a in pa]
    nrows = []
    if _has_pairs(con):
        near = _rows(con, "SELECT a, b, topic_tfidf, years_apart, exact_k6_longest FROM pairs WHERE (a=? OR b=?) AND topic_tfidf IS NOT NULL "
                          "ORDER BY topic_tfidf DESC LIMIT 12", (sid, sid))
        for p in near:
            other = p["b"] if p["a"] == sid else p["a"]
            nrows.append([N(round(p["topic_tfidf"], 3)), _story_link(con, other), _esc(_fmt(p["years_apart"])),
                          N(int(p["exact_k6_longest"] or 0)), f"<a href='/pair/{_esc(sid)}/{_esc(other)}'>pair</a>"])
    events = [_j(e["raw"], {}) for e in _rows(con, "SELECT raw FROM events WHERE article_id=? ORDER BY ts", (sid,))]
    surprise = []
    s = meta_json(con, "summary", {}) or {}
    for name in ("exact_k6", "para_k10"):
        for u in (s.get("unusual", {}).get(name, {}).get("most_unusual", []) or []):
            if sid in (u["a"], u["b"]):
                surprise.append((name, u))
    body = [_howto("Layer 2: one record. Facts as exported for the reuse run, the live status on the workbench, "
                   "every passage it shares with other stories (each linked to the pair page where both texts are "
                   "shown and to the scan), its closest stories by topic, and its annotation history. "
                   "Layer 3 is one click away: the workbench (regions on the scan) and the raw records."),
            f"<h1>{_esc(r['title'] or '(untitled)')}</h1>",
            "<p class='muted'>" + (f"{_author_link(r)} · " if r.get("author") else "")
            + f"{_issue_link(con, r['issue'])} · {_esc(r['type'])} · "
            f"date {_esc(r['date'] or '')} ({_esc(r['date_source'] or 'issue')}) · "
            f"pages {_esc(', '.join(str(p) for p in pages[:12]))}{'…' if len(pages) > 12 else ''} · "
            f"{r['n_words']:,} words · {r['n_regions']} regions · status at export {_esc(r['status'])}"
            + (f" (now {_esc(live_status)})" if live_status and live_status != r["status"] else "")
            + (f" · verified by {_esc(r['verified_by'])}" if r.get("verified_by") else "")
            + (f" · modified by {_esc(', '.join(_j(r['modified_by'], [])))}" if _j(r.get("modified_by"), []) else "")
            + (f" · workbench title now: {_esc(live_title)}" if live_title and live_title != r["title"] else "") + "</p>"]
    facts = []
    if r.get("title_as_printed") or r.get("author_as_printed") or r.get("title_source"):
        facts.append("title" + (f" from the {_esc(r['title_source'])}" if r.get("title_source") else "")
                     + (f", printed on the page as '{_esc(r['title_as_printed'])}'" if r.get("title_as_printed") else "")
                     + (f"; author printed on the page as '{_esc(r['author_as_printed'])}'" if r.get("author_as_printed") else ""))
    if r.get("subtitle"):
        facts.append(f"subtitle '{_esc(r['subtitle'])}'")
    if r.get("ad_class"):
        facts.append(f"advertisement class <b>{_esc(r['ad_class'])}</b>" + (f", advertiser {_esc(r['advertiser'])}" if r.get("advertiser") else ""))
    ann = _j(r.get("announces"), [])
    if ann:
        facts.append("announces " + "; ".join(_esc(x.get("title") or "?") + (f" by {_esc(display_author(x['author']))}" if x.get("author") else "") for x in ann[:8]))
    if r.get("contains_excerpt"):
        eo = _j(r.get("excerpt_of"), None) if (r.get("excerpt_of") or "").startswith("{") else r.get("excerpt_of")
        eo_txt = (eo.get("title", "") + (f" by {display_author(eo['author'])}" if eo.get("author") else "")) if isinstance(eo, dict) else (eo or "")
        facts.append("<b>quotes a story verbatim</b>" + (f": {_esc(eo_txt)}" if eo_txt else "") + " — left out of the reuse inventory")
    chs = _j(r.get("chapters"), [])
    if chs:
        facts.append(f"{len(chs)} chapters: " + "; ".join(
            ((c.get("number") or "") + (" " if c.get("number") and c.get("title") else "") + (c.get("title") or "")).strip() for c in chs[:30]))
    flags = _j(r.get("flags"), [])
    if flags:
        facts.append("machine notes: " + "; ".join(_esc(f) for f in flags[:8]))
    if facts:
        body.append("<p class='muted' style='font-size:12.5px'>" + " · ".join(facts) + "</p>")
    if r.get("teaser"):
        body.append(f"<p style='font-style:italic;border-left:3px solid #8a6d1f;padding-left:10px'>{_esc(r['teaser'])} "
                    f"<span class='muted' style='font-style:normal;font-size:12px'>— teaser as printed (metadata, not story text)</span></p>")
    body += [f"<p><a href='/article/{_esc(sid)}'>open on the workbench</a> · "
             + (" · ".join(f"<a href='/issue/{_esc(r['issue'])}/p/{p}'>scan p.{p}</a>" for p in pages[:8]))
             + f" · {_raw_link('/raw/story/' + sid)}</p>",
             "<h2>Shared passages (exact, seed 6, other issues)</h2>",
             _table(["#words", "passage", "shared with", "in issue", ""], erows) if erows else "<div class='empty'>None.</div>",
             "<h2>Paraphrase alignments</h2>",
             _table(["#columns", "#identity", "this side", "other story", ""], prows) if prows else "<div class='empty'>None.</div>",
             "<h2>Closest stories by topic (reuse-masked TF-IDF)</h2>",
             _table(["#similarity", "story", "years apart", "#longest shared", ""], nrows) if nrows
             else "<div class='empty'>Not in the pair table (records under 50 words or non-stories are not compared).</div>"]
    if surprise:
        body.append("<h2>In the background</h2><ul>" + "".join(
            f"<li>{_esc(name)}: longest {u['longest']}, stratum topic q{u['topic_q']} · {u['years_band']} years (n={u['stratum_n']:,}): "
            f"P(at least this) {('under 1 in ' + format(u['stratum_n'], ',')) if u['p_at_least'] == 0 else u['p_at_least']}</li>"
            for name, u in surprise) + "</ul>")
    body.append("<h2>Annotation history</h2>")
    if events:
        body.append(_table(["when", "who", "action", "detail"],
                           [[_esc(e.get("ts", "")), _esc(_G["display_name"](e.get("user", "?"))), _esc(e.get("action", "")),
                             _esc(str({k: v for k, v in e.items() if k not in ("ts", "user", "action", "issue", "article_id")})[:160])]
                            for e in events]))
    else:
        body.append("<div class='empty'>No human action on this record yet.</div>")
    return _render(render, r["title"] or sid, "".join(body), f"/story/{sid}")


# ---------------------------------------------------------------- pairs

def pairs_page(qs, render=None):
    con = db()
    if not _has_pairs(con):
        return _render(render, "Pairs", "<h1>Story pairs</h1><div class='empty'>The pair table has not been built on this server.</div>", "/pairs")
    ma, mb, ia, ib = _g(qs, "ma"), _g(qs, "mb"), _g(qs, "ia"), _g(qs, "ib")
    ga, gb = _g(qs, "ga"), _g(qs, "gb")
    try:
        min_len = int(_g(qs, "min", "6") or 0)
    except ValueError:
        min_len = 6
    same_author = _g(qs, "same_author")
    band = _g(qs, "band")
    q = _g(qs, "q").strip().lower()
    kind = _g(qs, "kind", "exact")
    sort = _g(qs, "sort", "longest")
    cross_only = _g(qs, "same", "0") != "1"
    author = _g(qs, "author")
    sl = _slice(qs)
    conds, args = [], []
    if cross_only:
        conds.append("same_issue=0")
    if ma and mb:
        conds.append("((magazine_a=? AND magazine_b=?) OR (magazine_a=? AND magazine_b=?))")
        args += [ma, mb, mb, ma]
    if ia and ib:
        conds.append("((issue_a=? AND issue_b=?) OR (issue_a=? AND issue_b=?))")
        args += [ia, ib, ib, ia]
    if ga and gb:
        conds.append("((genre_a=? AND genre_b=?) OR (genre_a=? AND genre_b=?))")
        args += [ga, gb, gb, ga]
    if same_author == "1":
        conds.append("same_author=1")
    if band:
        conds.append("years_band=?")
        args.append(band)
    if author:
        conds.append("(a IN (SELECT id FROM records WHERE author_key=?) OR b IN (SELECT id FROM records WHERE author_key=?))")
        args += [author_unslug(author)] * 2
    if kind == "exact":
        conds.append("exact_k6_longest>=?")
        args.append(min_len)
    else:
        conds.append("para_k10_longest>=?")
        args.append(max(min_len, 1))
    if q:
        conds.append("(LOWER(exact_excerpt) LIKE ? OR LOWER(para_excerpt) LIKE ? OR a LIKE ? OR b LIKE ?)")
        args += [f"%{q}%"] * 4
    c2, a2 = _pair_slice_sql(sl)
    conds += c2
    args += a2
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    order = {"longest": "exact_k6_longest DESC, para_k10_longest DESC", "topic": "topic_tfidf DESC", "years": "years_apart",
             "para": "para_k10_longest DESC, exact_k6_longest DESC"}.get(sort, "exact_k6_longest DESC")
    total = _val(con, f"SELECT COUNT(*) FROM pairs{where}", args)
    n_all = _val(con, "SELECT COUNT(*) FROM pairs")
    pager, lim, off = _pager(qs, total, "/pairs")
    rows = _rows(con, f"SELECT * FROM pairs{where} ORDER BY {order} LIMIT ? OFFSET ?", args + [lim, off])
    magopts = [("", "any")] + [(m["name"], mag_abbr(m["name"])) for m in _rows(con, "SELECT name FROM magazines ORDER BY name")]
    genres = [r["genre"] for r in _rows(con, "SELECT DISTINCT genre FROM issues WHERE genre IS NOT NULL ORDER BY genre")]
    decades = [r["decade"] for r in _rows(con, "SELECT DISTINCT decade FROM issues WHERE decade IS NOT NULL ORDER BY decade")]
    form = (f"<form method='GET' action='/pairs' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>kind {_sel('kind', kind, [('exact', 'exact (seed 6)'), ('para', 'paraphrase (K=10)')])}</label>"
            f"<label>min longest <input name='min' value='{min_len}' style='width:50px'></label>"
            f"<label>magazines {_sel('ma', ma, magopts)} × {_sel('mb', mb, magopts)}</label>"
            f"<label>genres {_sel('ga', ga, [('', 'any')] + [(g, g) for g in genres])} × {_sel('gb', gb, [('', 'any')] + [(g, g) for g in genres])}</label>"
            f"<label>decade of either side {_sel('decade', sl['decade'] if sl['decade'] is not None else '', [('', 'any')] + [(d, f'{d}s') for d in decades])}</label>"
            f"<label>years apart {_sel('band', band, [('', 'any'), ('0-2', '0-2'), ('3-9', '3-9'), ('10-19', '10-19'), ('20+', '20+')])}</label>"
            f"<label><input type='checkbox' name='same_author' value='1'{' checked' if same_author == '1' else ''}> same author</label>"
            f"<label><input type='checkbox' name='same' value='1'{' checked' if not cross_only else ''}> include same-issue pairs</label>"
            f"<label>text <input name='q' value='{_esc(q)}' style='width:120px'></label>"
            f"<label>sort {_sel('sort', sort, [('longest', 'longest exact'), ('para', 'longest paraphrase'), ('topic', 'topic similarity'), ('years', 'years apart')])}</label>"
            f"<input type='hidden' name='ia' value='{_esc(ia)}'><input type='hidden' name='ib' value='{_esc(ib)}'>"
            f"<input type='hidden' name='author' value='{_esc(author)}'><button>show</button></form>")
    trows = []
    for p in rows:
        L, C = int(p["exact_k6_longest"] or 0), int(p["para_k10_longest"] or 0)
        trows.append([f"<a href='/pair/{_esc(p['a'])}/{_esc(p['b'])}'>open</a>", _story_link(con, p["a"]), _story_link(con, p["b"]),
                      N(L), N(int(p["exact_k6_n"] or 0)), N(C), N(round(p["topic_tfidf"] or 0, 3)),
                      _esc(_fmt(p["years_apart"])), "yes" if p["same_author"] == 1 else "",
                      "yes" if p["same_magazine"] == 1 else "", _esc(((p["exact_excerpt"] or p["para_excerpt"]) or "")[:90])])
    cols = meta_json(con, "pair_cols", [])
    body = (_howto("Layer 1: the story-pair table of the background stage — one row per pair of stories with "
                   "everything the protocol conditions on. Default view: cross-issue pairs that share at least a "
                   "six-word passage, longest first. Open a pair to read every shared passage in both stories. "
                   "One hundred pairs a page.")
            + f"<h1>Story pairs — {total:,} match the filters (of {n_all:,})</h1>" + form + pager
            + _table(["", "story A", "story B", "#longest exact", "#exact matches", "#longest paraphrase", "#topic sim.",
                      "years apart", "same author", "same magazine", "passage"], trows)
            + pager
            + f"<p class='muted'>{_raw_link('/raw/file?path=reuse/background/pairs_machine.csv.gz', 'download the full table (CSV)')} · "
              f"columns: {_esc(', '.join(cols))}</p>")
    return _render(render, "Story pairs", body, "/pairs")


def pair_page(a, b, render=None):
    con = db()
    row = pair_row(con, a, b)
    if row:
        a, b = row["a"], row["b"]
    ra, rb = _one(con, "SELECT * FROM records WHERE id=?", (a,)), _one(con, "SELECT * FROM records WHERE id=?", (b,))
    if not ra or not rb:
        return _render(render, "Pair", "<h1>No such pair</h1>", "/pairs")
    ex = _rows(con, "SELECT * FROM matches WHERE k=6 AND ((a=? AND b=?) OR (a=? AND b=?)) ORDER BY len DESC", (a, b, b, a))
    al = _rows(con, "SELECT * FROM aligns WHERE (a=? AND b=?) OR (a=? AND b=?) ORDER BY cols DESC", (a, b, b, a))

    def head(r):
        return (f"<div style='flex:1 1 300px;background:#fff;border:1px solid #d8cfc0;padding:8px 12px'>"
                f"<div style='font-size:17px'>{_story_link(con, r['id'], False, rec=r)}</div>"
                f"<div class='muted'>{_author_link(r)}</div>"
                f"<div class='muted'>{_issue_link(con, r['issue'])} · {r['n_words']:,} words · {_esc(r['status'])} · "
                f"<a href='/article/{_esc(r['id'])}'>workbench</a></div></div>")
    facts = ""
    if row:
        keys = [("topic similarity (TF-IDF, masked)", "topic_tfidf"), ("topic similarity (embedding)", "topic_emb"),
                ("topic quartile", "topic_q"), ("years apart", "years_apart"), ("years band", "years_band"),
                ("later year", "later_year"), ("same author", "same_author"), ("author known on both sides", "author_known"),
                ("same magazine", "same_magazine"), ("same publisher", "same_publisher"), ("same genre", "same_genre"),
                ("same format", "same_format"), ("same issue", "same_issue"), ("shared scan regions", "shared_regions"),
                ("longest exact match (seed 6 / 7 / 8)", None), ("exact matches (seed 6)", "exact_k6_n"),
                ("share of each story covered by exact matches (max)", "exact_k6_share_max"),
                ("longest paraphrase alignment (K=5 / 10 / 20)", None), ("best paraphrase identity (K=10)", "para_k10_best_identity")]
        cells = []
        for label, col in keys:
            if col is None and label.startswith("longest exact"):
                v = " / ".join(_n(row.get(f"exact_k{k}_longest") or 0) for k in (6, 7, 8))
            elif col is None:
                v = " / ".join(_n(row.get(f"para_k{k}_longest") or 0) for k in (5, 10, 20))
            else:
                v = row.get(col)
                if col == "shared_regions" and row.get("same_issue") != 1:
                    v = 0          # region keys are page:region within an issue; across issues the count means nothing
                if col.startswith("same_") or col in ("author_known",):
                    v = "yes" if v == 1 else "no"
                elif col.endswith("year") and isinstance(v, float):
                    v = str(int(v)) if v.is_integer() else f"{v:.3f}"
                elif isinstance(v, float):
                    v = _n(v) if v.is_integer() else _fmt(v)
                v = "" if v is None else str(v)
            cells.append(f"<tr><th style='text-align:left;width:300px'>{_esc(label)}</th><td>{_esc(str(v))}</td></tr>")
        facts = "<table>" + "".join(cells) + "</table>"
    bgnote = ""
    s = meta_json(con, "summary", {})
    if s and row:
        try:
            q, band = str(int(row["topic_q"])), row["years_band"]
            L = int(row["exact_k6_longest"] or 0)
            curve = s["background"]["exact"]["k6"]["by_topic_q"].get(q) or s["background"]["exact"]["k6"]["by_topic_q"].get(int(q))
            if curve and L >= 6:
                p = curve.get(str(L))
                if p is not None:
                    bgnote = (f"<p>Among cross-issue pairs in the same topic quartile ({q}), the share sharing a passage of "
                              f"at least {L} words is {_fmt(p * 100)}%.</p>")
        except Exception:
            bgnote = ""
    body = [_howto("Layer 2: two stories side by side. The facts table is this pair's row in the background "
                   "table; below it every exact passage the two share (with the words around it as each story "
                   "reads now, and links to the scan region), then every paraphrase alignment with the "
                   "differences marked. Layer 3: the raw records."),
            f"<h1>Story pair</h1><div style='display:flex;gap:12px;flex-wrap:wrap'>{head(ra)}{head(rb)}</div>",
            f"<p class='muted' style='margin-top:8px'>{_raw_link(f'/raw/pair/{a}/{b}')}</p>",
            "<h2>Facts about the pair</h2>", facts or "<div class='empty'>Not in the pair table (one side is not a story of 50+ words).</div>",
            bgnote,
            "<h2>Exact shared passages (seed 6)</h2>"]
    if ex:
        for m in ex:
            la = RP.locate_in_article(ra["issue"], a, m["excerpt"])
            lb = RP.locate_in_article(rb["issue"], b, m["excerpt"])

            def side(r, loc):
                if loc:
                    links = (f"<a href='/article/{_esc(r['id'])}?sel={urllib.parse.quote(loc['fragkey'])}'>on the scan</a>"
                             + (f" (p.{loc['page']})" if loc.get("page") else ""))
                else:
                    links = "<span class='muted'>not located now</span>"
                return (f"<div style='flex:1 1 300px'><div class='muted' style='font-size:12px'>{_esc(r['title'] or r['id'])} · {links}</div>"
                        f"<div><span class='muted'>{_esc((loc or {}).get('before', ''))}</span>"
                        f"<span style='background:#f3e2a8'>{_esc(m['excerpt'])}</span>"
                        f"<span class='muted'>{_esc((loc or {}).get('after', ''))}</span></div></div>")
            body.append(f"<div class='card'><div class='ch'><span>{m['len']} words</span>"
                        + (f"<span class='muted'>{_esc(m.get('cause') or '')}</span>" if m.get("cause") else "")
                        + f"</div><div class='cardtext' style='max-height:none;display:flex;gap:16px;flex-wrap:wrap'>"
                        f"{side(ra, la)}{side(rb, lb)}</div></div>")
    else:
        body.append("<div class='empty'>None.</div>")
    body.append("<h2>Paraphrase alignments (K=10)</h2>")
    if al:
        for x in al:
            raw = _j(x["raw"], {})
            body.append(f"<div class='card'><div class='ch'><span>{x['cols']} columns · identity {x['identity']} · score {x['score']} · "
                        f"source {', '.join(raw.get('sources', []))}</span></div><div class='cardtext' style='max-height:none'>"
                        f"{_G['diff_html'](x['text_a'], x['text_b'])}</div></div>")
    else:
        body.append("<div class='empty'>None.</div>")
    return _render(render, "Story pair", "".join(body), f"/pair/{a}/{b}")


# ---------------------------------------------------------------- raw layer

def _rec_out(r):
    """A records row as the export record shape (JSON fields decoded)."""
    if not r:
        return None
    o = dict(r)
    for k in ("pages", "modified_by", "fragments"):
        o[k] = _j(o.get(k), [])
    return o


def raw_story(sid):
    con = db()
    r = _one(con, "SELECT * FROM records WHERE id=?", (sid,))
    if not r:
        return None
    live, _ = _G["article_by_id"](sid)
    ex = [_j(m["raw"], {}) for m in _rows(con, "SELECT raw FROM matches WHERE k=6 AND same_issue=0 AND (a=? OR b=?) ORDER BY len DESC", (sid, sid))]
    pa = [_j(x["raw"], {}) for x in _rows(con, "SELECT raw FROM aligns WHERE a=? OR b=? ORDER BY cols DESC", (sid, sid))]
    return {"source_files": ["data/pilot_stories.jsonl (export record)", "site replay of data/articles + data/annotations (live)",
                             "data/reuse/machine_k6_matches.jsonl", "data/reuse/para/machine_w50s25_k10_alignments.jsonl",
                             "data/explorer.sqlite (records, matches, aligns, events)"],
            "export_record": _rec_out(r), "live_article": live if live else None,
            "exact_matches_k6": ex, "paraphrase_alignments_k10": pa,
            "annotation_events": [_j(e["raw"], {}) for e in _rows(con, "SELECT raw FROM events WHERE article_id=? ORDER BY ts", (sid,))]}


def raw_pair(a, b):
    con = db()
    row = pair_row(con, a, b)
    ex = [_j(m["raw"], {}) for m in _rows(con, "SELECT raw FROM matches WHERE k=6 AND ((a=? AND b=?) OR (a=? AND b=?)) ORDER BY len DESC", (a, b, b, a))]
    al = [_j(x["raw"], {}) for x in _rows(con, "SELECT raw FROM aligns WHERE (a=? AND b=?) OR (a=? AND b=?)", (a, b, b, a))]
    return {"source_files": ["data/reuse/background/pairs_machine.csv.gz", "data/reuse/machine_k6_matches.jsonl",
                             "data/reuse/machine_k6_sameissue.jsonl", "data/reuse/para/machine_w50s25_k10_alignments.jsonl"],
            "pair_row": row, "exact_matches_k6": ex, "paraphrase_alignments_k10": al}


def _author_out(a):
    o = dict(a)
    o["names"] = _j(o.get("names"), {})
    o["magazines"] = _j(o.get("magazines"), [])
    o["genres"] = _j(o.get("genres"), [])
    return o


def raw_author(slug):
    con = db()
    a = _one(con, "SELECT * FROM authors WHERE key=?", (author_unslug(slug),))
    if not a:
        return None
    links = {f"{L['ka']} | {L['kb']}": {"n": L["n"], "longest": L["longest"], "pairs": _j(L["pairs"], []), "stories": _j(L["stories"], [])}
             for L in _rows(con, "SELECT * FROM author_links WHERE ka=? OR kb=?", (a["key"], a["key"]))}
    return {"source_files": ["data/pilot_stories.jsonl (by-lines, normalized with pipeline/r01_normalize.author_key)",
                             "data/reuse/machine_k6_matches.jsonl"],
            "author": _author_out(a), "links": links,
            "stories": [_rec_out(r) for r in _rows(con, "SELECT * FROM records WHERE author_key=? AND is_story=1 ORDER BY year, id", (a["key"],))]}


def _issue_out(i):
    o = dict(i)
    o["ia"] = _j(o.get("ia"), {})
    o["ia_files"] = _j(o.get("ia_files"), [])
    o["text_stages"] = _j(o.get("text_stages"), [])
    return o


def raw_issue(iid):
    con = db()
    i = _one(con, "SELECT * FROM issues WHERE id=?", (iid,))
    if not i:
        return None
    meta = _json(os.path.join(_G["DATA"], "raw", iid, "meta.json"))
    return {"source_files": ["config/pilot_issues.json", f"data/raw/{iid}/meta.json (Internet Archive item metadata)",
                             "data/pilot_stories.jsonl", "data/reuse/machine_region_overlap.json", "pipeline/publishers.json",
                             "data/pages, data/layout, data/text, data/articles (stage counts)"],
            "issue": _issue_out(i), "archive_item": meta, "region_overlap": (meta_json(con, "overlap", {}) or {}).get(iid),
            "records": [_rec_out(r) for r in _rows(con, "SELECT * FROM records WHERE issue=? ORDER BY first_page, id", (iid,))]}


def _mag_out(m):
    o = dict(m)
    o["publishers"] = _j(o.get("publishers"), [])
    o["issues"] = _j(o.get("issues"), [])
    return o


def raw_magazine(slug):
    con = db()
    m = _one(con, "SELECT * FROM magazines WHERE slug=?", (slug,))
    if not m:
        return None
    return {"source_files": ["config/pilot_issues.json", "data/pilot_stories.jsonl", "pipeline/publishers.json"],
            "magazine": _mag_out(m), "issues": [_issue_out(i) for i in _rows(con, "SELECT * FROM issues WHERE magazine=? ORDER BY year, id", (m["name"],))],
            "links": {f"{L['ma']} | {L['mb']}": {"n": L["n"], "longest": L["longest"], "pairs": _j(L["pairs"], [])}
                      for L in _rows(con, "SELECT * FROM mag_links WHERE ma=? OR mb=?", (m["name"], m["name"]))}}


def raw_index():
    con = db()
    D = _G["DATA"]
    files = []
    for root, _dirs, fs in os.walk(os.path.join(D, "reuse")):
        for f in fs:
            p = os.path.join(root, f)
            files.append({"path": os.path.relpath(p, D), "bytes": os.path.getsize(p)})
    dbp = _DB["path"]
    return {"built": meta_value(con, "built"), "build_seconds": meta_value(con, "build_seconds"),
            "database": {"path": os.path.relpath(dbp, _G["ROOT"]), "bytes": os.path.getsize(dbp) if os.path.exists(dbp) else 0,
                         "tables": [r["name"] for r in _rows(con, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")],
                         "rebuilt_when": "a source file changes (checked every 20 s), or: python3 webapp/explore_pages.py --build"},
            "sources": meta_json(con, "sources", []), "counts": meta_json(con, "counts", {}),
            "reuse_files": sorted(files, key=lambda f: f["path"]),
            "lists_are_paged": "raw lists take ?page=N&limit=M (limit up to 5000); the 'next' field gives the following page",
            "data_door": ["/api/<token>/ls?path=…", "/api/<token>/get?path=…", "/api/<token>/doc/<issue>",
                          "/api/<token>/index", "/api/<token>/story/<id>", "/api/<token>/pair/<a>/<b>",
                          "/api/<token>/author/<slug>", "/api/<token>/issue/<id>", "/api/<token>/magazine/<slug>",
                          "/api/<token>/authors", "/api/<token>/magazines", "/api/<token>/stories", "/api/<token>/pairs",
                          "/api/<token>/locate/<story>?text=…"]}


def raw_list(kind, qs=None):
    qs = qs or {}
    con = db()
    try:
        limit = max(1, min(5000, int(_g(qs, "limit", "1000") or 1000)))
    except ValueError:
        limit = 1000
    page = _pageno(qs)
    off = (page - 1) * limit
    spec = {"authors": ("authors", "n_stories DESC, key", _author_out, ["data/pilot_stories.jsonl"]),
            "magazines": ("magazines", "first_year, name", _mag_out, ["config/pilot_issues.json", "data/pilot_stories.jsonl"]),
            "stories": ("records", "year, id", _rec_out, ["data/pilot_stories.jsonl"]),
            "pairs": ("pairs", "rowid", dict, ["data/reuse/background/pairs_machine.csv.gz"])}.get(kind)
    if not spec:
        return None
    table, order, conv, srcs = spec
    if table == "pairs" and not _has_pairs(con):
        return {"source_files": srcs, "total": 0, "rows": []}
    total = _val(con, f"SELECT COUNT(*) FROM {table}")
    rows = [conv(r) for r in _rows(con, f"SELECT * FROM {table} ORDER BY {order} LIMIT ? OFFSET ?", (limit, off))]
    nxt = f"/raw/{kind}.json?page={page + 1}&limit={limit}" if off + limit < total else None
    return {"source_files": srcs, "total": total, "page": page, "limit": limit, "next": nxt, kind if kind != "stories" else "records": rows}


RAW_ROOTS = ("reuse", "raw", "articles", "annotations", "layout", "text", "gold", "assembly_v2", "survey", "metrics.json", "timings.jsonl",
             "pilot_stories.jsonl", "pilot_stories.jsonl.gz", "feedback.jsonl", "explorer.sqlite")


def raw_file_path(rel):
    """Absolute path for a members' raw-file request, or None if outside
    the allowed subtrees of data/."""
    D = os.path.realpath(_G["DATA"])
    p = os.path.realpath(os.path.join(D, rel.strip("/")))
    if not p.startswith(D + os.sep):
        return None
    sub = os.path.relpath(p, D)
    if not any(sub == r or sub.startswith(r + os.sep) for r in RAW_ROOTS):
        return None
    return p if os.path.exists(p) else None


def _builders(arg, qs):
    return {"story": lambda: raw_story(arg), "pair": lambda: raw_pair(*arg), "author": lambda: raw_author(arg),
            "issue": lambda: raw_issue(arg), "magazine": lambda: raw_magazine(arg), "index": raw_index,
            "authors": lambda: raw_list("authors", qs), "magazines": lambda: raw_list("magazines", qs),
            "stories": lambda: raw_list("stories", qs), "pairs": lambda: raw_list("pairs", qs)}


def raw_page(kind, arg, render=None, qs=None):
    """HTML view of a raw JSON object with a download link."""
    b = _builders(arg, qs or {})
    obj = b[kind]() if kind in b else None
    if obj is None:
        return _render(render, "Raw", "<h1>No such record</h1>", "/overview")
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    big = len(text) > 400000
    shown = text[:400000] + ("\n… (truncated on screen; download for the whole record)" if big else "")
    path = f"/raw/{kind}" + (("/" + "/".join(arg)) if isinstance(arg, tuple) else (("/" + arg) if arg else ""))
    keep = {k: v[0] for k, v in (qs or {}).items() if v and k in ("page", "limit")}
    tail = ("?" + urllib.parse.urlencode(keep)) if keep else ""
    nav = ""
    if isinstance(obj, dict) and obj.get("next"):
        nav = f" · <a href='{_esc(obj['next'].replace('.json', ''))}'>next page</a>"
    body = (_howto("Layer 3: the raw records behind the page you came from, exactly as they are in the files "
                   "named at the top of the record. The download gives the same content as a JSON file; lists "
                   "come in pages (page and limit parameters).")
            + f"<h1>Raw: {_esc(kind)}{(' ' + _esc(' / '.join(arg) if isinstance(arg, tuple) else arg)) if arg else ''}</h1>"
            + f"<p><a href='{path}.json{tail}'>download as JSON</a> · {len(text):,} characters{nav}</p>"
            + f"<pre style='max-height:none;font-family:Menlo,Consolas,monospace;font-size:12.5px'>{_esc(shown)}</pre>")
    return _render(render, "Raw", body, path)


def raw_json(kind, arg, qs=None):
    b = _builders(arg, qs or {})
    obj = b[kind]() if kind in b else None
    return None if obj is None else json.dumps(obj, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- method page parts

def params_html():
    """The settings actually used, read from the stats files."""
    runs = RP._runs()
    rows = []
    for st in runs["exact"]:
        rows.append(["exact matching (r02)", _esc(st["set"]), f"seed {st['k']} words · commonplace cap {st['max_df']} stories per shingle",
                     _esc(st.get("generated", "")), f"<a href='/raw/file?path=reuse/{st['set']}_k{st['k']}_stats.json'>stats</a>"])
    for st in runs["para"]:
        rows.append(["paraphrase (r04)", _esc(st["set"]),
                     f"window {st['window']} / step {st['stride']} · model {_esc(st.get('model') or 'none')} · K={st['k']} · "
                     f"keep {st['min_cols']}+ columns at identity {st['min_identity']}+ · scores {st['scores']} · "
                     f"fuzzy words {st['fuzzy']['min_len']}+ letters, {st['fuzzy']['alternates']} alternates",
                     _esc(st.get("generated", "")), f"<a href='/raw/file?path=reuse/para/{st['_tag']}_k{st['k']}_stats.json'>stats</a>"])
    for sm in runs["background"]:
        sc = sm.get("sampler_check", {})
        rows.append(["background (r05)", _esc(sm["set"]),
                     f"all {sm['pairs']:,} pairs · topic quartile cuts {sm.get('topic_quartile_cuts')} · sampler "
                     f"{sc.get('n_per_stratum')} per stratum × {sc.get('seeds')} draws · models: "
                     + ", ".join(sm.get("models", {}).keys()),
                     _esc(sm.get("generated", "")), f"<a href='/raw/file?path=reuse/background/summary_{sm['set']}.json'>summary</a>"])
    ex = runs["synthetic"].get("exact")
    if ex:
        rows.append(["planted reuse (r03)", "synthetic copy", f"{ex.get('_stories')} stories · {ex.get('_plants')} plants · seeds 6/7/8",
                     _esc(ex.get("_generated", "")), "<a href='/raw/file?path=reuse/synthetic/recall_verbatim.json'>recall</a>"])
    for tag, rp in runs["synthetic"].get("para", {}).items():
        kr = rp.get("_keep_rule", {})
        rows.append(["planted reuse, paraphrase (r04)", "synthetic copy",
                     f"{_esc(tag)} · keep {kr.get('min_cols', 20)}+ columns at identity {kr.get('min_identity', 0.6)}+",
                     _esc(rp.get("_generated", "")), f"<a href='/raw/file?path=reuse/para/synthetic/recall_paraphrase_{_esc(tag)}.json'>recall</a>"])
    try:
        con = db()
        rows.append(["explorer database (site)", "—", f"data/explorer.sqlite · {len(meta_json(con, 'sources', []))} source files · "
                     f"tables: {', '.join(r['name'] for r in _rows(con, 'SELECT name FROM sqlite_master WHERE type=' + chr(39) + 'table' + chr(39) + ' ORDER BY name'))}",
                     _esc(meta_value(con, "built", "")), "<a href='/raw/index'>index</a>"])
    except Exception:
        pass
    if not rows:
        return "<div class='empty'>No result files on this server yet.</div>"
    return _table(["stage", "story set", "settings", "generated", "file"], rows)


DICTIONARY = [
    ("data/pilot_stories.jsonl — one line per record (r00)", [
        ("story_id", "record id, <issue>_a<number>"), ("issue", "issue id"), ("magazine, cover_date, genre, format", "from config/pilot_issues.json"),
        ("type", "story · serial_part · feature · poem · letters · ad · toc · other (machine typing, human-correctable)"),
        ("title, author", "as printed, as they stood at export (human corrections included)"),
        ("teaser", "the printed blurb on a story's first page, when an annotator marked it (metadata, never story text)"),
        ("subtitle, title_as_printed, author_as_printed", "the page's own forms when the title or author came from the contents page (assembly v2.1: the contents page has authority; decision of 2026-09-03)"),
        ("title_source, author_source", "'contents' or 'page'"),
        ("ad_class, advertiser", "advertisements only: house_next_issue · house_self · house_sibling · house_form · trade · classified; the advertiser's name when read"),
        ("announces", "house advertising only: the works it names [{title, author, page}]"),
        ("contains_excerpt, excerpt_of", "a house announcement quoting a story verbatim, and which story; such records stay out of the reuse inventory"),
        ("chapters", "[{number, n, title, page}] — the chapter heads, number and title apart (n is the number's value)"),
        ("flags", "the assembler's notes for a person to look at"),
        ("date, date_source", "the story's date; 'issue' = the issue's cover date, the default until other evidence is recorded"),
        ("pages", "page numbers the record spans"), ("status", "auto · modified · verified"),
        ("verified_by, modified_by", "annotator usernames"), ("fragments", "scan region keys page:region, in reading order"),
        ("n_words, text_sha1, text", "word count, checksum, reading text")]),
    ("data/explorer.sqlite — the explorer's database, rebuilt from the files above (site)", [
        ("issues", "one row per selected issue with its archive record, counts, and the state of every step (pages, layout_pages, text_stages, assembled, exported, events, complete)"),
        ("records", "the export records plus display_author (title case), decade, first_page, n_exact, n_para, and the v2.1 fields (ad_class, advertiser, contains_excerpt, excerpt_of, n_chapters, chapters, announces, title_as_printed, author_as_printed, subtitle, title_source, author_source, flags)"),
        ("authors, author_links", "normalized by-lines with display name, printed forms, last_name (sort key), counts; author pairs sharing passages"),
        ("magazines, mag_links, issue_links", "magazines with counts; magazine and issue pairs sharing passages"),
        ("matches, aligns, events", "exact matches (all seeds, cross- and same-issue), paraphrase alignments, annotation events, each with the original record as JSON"),
        ("pairs", "the pair table (below) plus genre_a, genre_b, decade_a, decade_b"), ("meta", "signature of the sources, build time, counts, the background summary, the survey summary")]),
    ("data/survey/ — the survey of the archive's collection (s00; metadata only, decision of 2026-09-03)", [
        ("items.jsonl", "one archive item per line as the search API gave it (identifier, title, date, year, language, collection, imagecount, item_size, publicdate …) plus the derived lang_class (english · other · unmarked), kind (fiction magazine · dime novel · film or general magazine · comic magazine · other), genre (the sub-collection's genre in the pilot's vocabulary), year_derived, magazine (the name read from the title)"),
        ("summary.json", "total_items, by_language_class, by_kind, by_decade, by_subcollection, and working_corpus (English or unmarked: items, page_images, fiction_items, fiction_by_decade, fiction_by_genre, fiction_magazines, fiction_magazines_top)"),
        ("magazines.json", "one entry per magazine name: items, pages, working_items, fiction_items, years, kind, genre, languages, subcollections, pilot_issues")]),
    ("data/reuse/<set>_k<k>_matches.jsonl — one exact match (r02)", [
        ("a, b", "story ids (a sorts before b)"), ("len", "length in words"), ("a_tok, b_tok", "token intervals [start, end)"),
        ("a_char, b_char", "character intervals into the canonical text"), ("a_issue, b_issue", "issue ids"),
        ("excerpt", "the passage (up to 300 characters)"), ("cause, shared_regions", "same-issue file only: shared-region duplicate or same-issue repeat")]),
    ("data/reuse/<set>_k<k>_clusters.json — one cluster (r02)", [
        ("witnesses, witnesses_collapsed", "distinct stories; distinct region families"), ("issues, occurrences, max_len", "counts"),
        ("representative", "longest occurrence: story_id, tok, text"), ("members", "story_id, issue, tok, len, text")]),
    ("data/reuse/para/<tag>_k<K>_alignments.jsonl — one alignment (r04)", [
        ("a, b, a_tok, b_tok, a_char, b_char, a_issue, b_issue", "as above"),
        ("cols, matches, identity, score", "alignment columns, matching columns, matches/cols, alignment score"),
        ("sources, best_rank, max_cosine", "how the candidate was found: exact seed and/or embedding neighbour (rank, cosine)"),
        ("text_a, text_b", "both texts of the aligned span")]),
    ("data/reuse/background/pairs_<set>.csv.gz — one story pair (r05)", [
        ("a, b, issue_a, issue_b, same_issue, shared_regions, same_family", "identities and flags"),
        ("tokens_a, tokens_b", "story lengths in tokens"),
        ("magazine_*, publisher_*, same_magazine, same_publisher, same_genre, same_format", "venue facts"),
        ("author_a, author_b, author_known, same_author", "printed by-lines; both usable; same normalized name"),
        ("year_a, year_b, later_year, earlier_year, years_apart, years_band, later_decade", "the two time variables"),
        ("topic_tfidf, topic_emb, topic_q", "topic similarity (masked TF-IDF cosine; embedding cosine); quartile 1–4"),
        ("exact_k<k>_longest / _n / _cover_a / _cover_b / _share_max, exact_excerpt", "exact reuse per seed"),
        ("para_k<K>_longest / _n / _best_identity / _cover_a / _cover_b / _share_max, para_excerpt", "paraphrase reuse per K")]),
    ("data/reuse/background/summary_<set>.json (r05)", [
        ("background", "P(longest ≥ L) curves overall and by stratum; time_table by later decade × years band"),
        ("unusual", "matched pairs ranked by P(at least this long) within their stratum"),
        ("sampler_check", "weighted-sample errors against the full table"), ("models", "two-part model fits: fixed effects, story effect sd")]),
    ("data/annotations/<issue>.jsonl — one human action (site)", [
        ("ts, user, issue, article_id, action", "when, who, where, what"),
        ("frag, role, to_id, into_id, order, text, frags, …", "action details; replayed in order over the machine output; role may be title, subtitle, author, teaser, chapter_number, chapter_title, section; new_article (v0.12.0) makes one record from several boxes (frags, type, title, from_id); set_meta may also carry ad_class, advertiser, excerpt_of, contains_excerpt")]),
    ("data/feedback.jsonl — one feedback entry (site)", [
        ("id, ts, path, name, user", "entry id, when, the page it was written on, the display name, the account"),
        ("comment, history", "the text as it stands; earlier versions with their timestamps when it was edited"),
        ("done, done_by, done_at", "marked as handled by an administrator")]),
    ("data/raw/<issue>/meta.json — the Internet Archive item record", [
        ("metadata", "identifier, title, date, uploader, addeddate, scanner, collection, ocr, imagecount, …"),
        ("files", "every file of the item with format and size"), ("item_size, item_last_updated", "as reported by the archive")]),
]


def data_dictionary_html():
    out = []
    for title, fields in DICTIONARY:
        out.append(f"<h3 style='font-weight:normal;font-size:16px'>{_esc(title)}</h3>")
        out.append(_table(["field", "meaning"], [[_esc(f), _esc(m)] for f, m in fields]))
    return "".join(out)


def files_html():
    D = _G["DATA"]
    rows = []
    for root, _dirs, fs in os.walk(os.path.join(D, "reuse")):
        for f in sorted(fs):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, D)
            if f.endswith(".npz"):
                continue
            rows.append([f"<a href='/raw/file?path={urllib.parse.quote(rel)}'>{_esc(rel)}</a>", N(os.path.getsize(p)),
                         _esc(time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))))])
    for rel in ("pilot_stories.jsonl", "pilot_stories.jsonl.gz", "explorer.sqlite", "metrics.json", "timings.jsonl"):
        p = os.path.join(D, rel)
        if os.path.exists(p):
            rows.append([f"<a href='/raw/file?path={rel}'>{_esc(rel)}</a>", N(os.path.getsize(p)),
                         _esc(time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))))])
    if not rows:
        return "<div class='empty'>None yet.</div>"
    return (_table(["file (click to download)", "#bytes", "written"], rows)
            + "<p class='muted'>Scans, layout records, text stages, article assemblies, and annotation logs are "
              "reached through the issue pages, the workbench, and the raw links on entity pages; automated "
              "readers use the data door (see the handbook).</p>")


# ---------------------------------------------------------------- command line

def selftest():
    assert display_author("H. G. WELLS") == "H. G. Wells"
    assert display_author("ray cummings") == "Ray Cummings"
    assert display_author("CAPT. S. P. MEEK") == "Capt. S. P. Meek"
    assert display_author("victor rousseau") == "Victor Rousseau"
    assert display_author("robert e. howard") == "Robert E. Howard"
    assert display_author("j. r. mcdonald") == "J. R. McDonald"
    assert display_author("HENRI DE LA FALAISE") == "Henri de la Falaise"
    assert display_author("SEAN O'BRIEN") == "Sean O'Brien"
    assert display_author("mary smith-jones") == "Mary Smith-Jones"
    assert display_author("") == ""
    assert _decade(1936.5) == 1930 and _decade(None) is None
    print("selftest OK")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return
    import app as A                       # the site's globals: paths, cfg, helpers
    bind(vars(A))
    if "--build" in argv:
        sig = _sources()
        meta = build_db(sig, _DB["path"], log=print)
        print(json.dumps({"built": meta["built"], "seconds": meta["build_seconds"], "counts": json.loads(meta["counts"])}, indent=1))
        return
    print("usage: python3 webapp/explore_pages.py --build | --selftest")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main(sys.argv[1:])
