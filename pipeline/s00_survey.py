#!/usr/bin/env python3
"""Stage 0 — the survey: what the archive's pulp collection holds.

Metadata only. One record per item of the Internet Archive collection
`pulpmagazinearchive` (identifier, title, date, language, sub-collections,
page count, size), fetched through the archive's search API. No page
image and no text is downloaded here; the Registered Report's gate on the
downloader (config/pilot_issues.json, approved=…) is untouched. Decision
of 2026-09-03: the metadata may be harvested before protocol acceptance,
so that the site's boards can show the whole collection — English and
not, fiction magazines and not — and the project's progress against it.

    python3 pipeline/s00_survey.py --run        fetch everything (about a minute)
    python3 pipeline/s00_survey.py --enrich     the provenance fields of every item, one metadata
                                                call per item (resumable; an hour or two)
    python3 pipeline/s00_survey.py --summary    recompute the summaries from items.jsonl (+ enrich.jsonl)
    python3 pipeline/s00_survey.py --selftest

The enrichment (protocol section 2: "we reconstruct the collection's
history of transmission and assess the resulting sample by year, title,
author, and publisher") reads each item's own metadata record
(archive.org/metadata/<id>) for the fields the search index does not
carry: who uploaded it and when, which sub-collection it was added to and
by which curator, which OCR engine produced the archive's text, the
language the OCR detected, the rights fields. data/survey/enrich.jsonl
keeps one line per item; the summary folds them into a "provenance"
section (items added by year, uploader accounts, curators, OCR engines,
detected language of the unmarked items, scanning-group tags read from
the titles) and a "by publisher" table from config/publishers_magazines.json.
Uploader addresses stay in the data file; the summary carries only the
part before the @ and a count.

Output: data/survey/items.jsonl (one archive item per line, as the API
gave it, plus the derived fields year, lang_class, kind, genre, magazine),
data/survey/summary.json (the counts the site's boards show) and
data/survey/magazines.json (one entry per magazine name). The pilot's ten
issues are recognised by their identifiers (config/pilot_issues.json).

Three derived fields need a word. `lang_class` is english / other /
unmarked from the archive's language field; the working corpus is
english + unmarked, because a scan with no language given is usually an
American pulp whose uploader filled in nothing. `kind` comes from the
archive's sub-collections: "fiction magazine" (pulps and digests),
"dime novel" (Beadle's and the story papers), "film or general magazine"
(Photoplay, Collier's …), "comic magazine" (Warren's Creepy, Eerie …)
and "other" (podcasts, theses). `genre` is the sub-collection's genre in
the pilot's vocabulary (science fiction, weird, detective, western …);
"general" is Argosy, Blue Book and The Popular, "unsorted" is an item the
archive filed under no genre. These are the archive's labels, not ours:
the protocol decides what the corpus is, the survey shows what there is.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "data", "survey")
COLLECTION = "pulpmagazinearchive"
FIELDS = ["identifier", "title", "date", "year", "language", "creator", "publisher", "collection", "subject",
          "mediatype", "imagecount", "item_size", "publicdate", "addeddate"]
API = "https://archive.org/services/search/v1/scrape"
ENGLISH = {"eng", "english", "en", "en-us", "en-gb", "eng.", "english.", "englisch"}
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
SEASONS = r"(?:spring|summer|fall|autumn|winter)"
UMBRELLA = {COLLECTION, "magazine_rack"}

# kind and genre by sub-collection (the archive's filing; see the module note)
FICTION_SF = ["pulp_misc_sf", "amazingstoriesmagazine", "fantasyandsciencefiction", "astoundingstories", "galaxymagazine",
              "asimovmagazine", "fantasticsfstories", "interzonemagazine", "startling_stories", "fantasticadventures",
              "ifmagazine", "newworldssf", "planetstories", "thrillingwonderstories", "famousfantasticmysteries",
              "authenticsciencefiction", "sciencefantasy", "sciencefictionstories", "sciencefictionquarterly",
              "sciencewonderstories", "thrustsfmagazine"]
GENRE_OF = {}
for _c in FICTION_SF:
    GENRE_OF[_c] = "science fiction"
GENRE_OF.update({"pulp_misc_fantasy": "fantasy", "unknownworlds": "fantasy",
                 "weirdtalesmagazine": "weird", "pulp_misc_horror": "weird", "twilightzonemagazine": "weird",
                 "pulp_misc_mystery": "detective", "eleryqueenmagazine": "detective", "thrillingdetective": "detective",
                 "pulp_misc_adventure": "adventure", "adventuremagazine": "adventure",
                 "bluebookmagazine": "general", "popularmagazine": "general", "argosymagazine": "general",
                 "pulp_misc_western": "western", "pulp_misc_romance": "romance", "pulp_misc_sports": "sports",
                 "pulp_misc_youth": "youth", "pulp_misc_humor": "humor",
                 "pulp_fiction_misc": "unsorted", "pulpmagazine_inbox": "unsorted"})
DIME = {"nickles-and-dimes", "beadlesdimenovels", "fameandfortuneweekly", "wideawakelibrary", "deadwooddicklibrary",
        "secretservicemag"}
FILM_GENERAL = {"photoplaymagazine", "modernscreen", "famousmonstersmagazine", "classichollywood", "radiostarsmagazine",
                "thenewmovie", "filmfunmmagazine", "starburstmagazine", "psychotronic", "fatemagazine", "libertymagazine",
                "colliersmagazine", "mccallsmagazine", "mccluresmagazine", "modernmechanix", "mechanixillustrated",
                "calvacademagazine", "aboutpulps", "helpmagazine", "mensmagazines", "choogleonuncleweed"}
COMICS = {"creepy_warren", "eerie_magazines", "warren-vampirella", "warren-1984-magazine", "rook_warren",
          "warrenpublishingmisc", "comixinternational", "teenagelovestorieswarren", "goblin-warren"}
OTHER = {"podcasts", "community", "theses-and-dissertations", "deemphasize", "no-preview", "loggedin"}
# Spanish and other non-English series are their own sub-collections; the language field sorts them out.
SPANISH_SERIES = {"bolsilibros_la_conquista_del_espacio", "bolsilibros_seleccion_terror", "heroes_de_la_pradera",
                  "espacio_el_mundo_futuro", "brigitte_en_accion", "heroes_del_espacio", "cineargentino",
                  "luchadores_del_espacio", "la_huella_pulp", "el_pirata_negro"}


def fetch(url, tries=4):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pulp-fiction-corpus survey (metadata only)"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            err = e
            time.sleep(5 * (n + 1))
    raise RuntimeError(f"{url}: {err}")


def lang_class(v):
    """'english', 'other' or 'unmarked' from the archive's language field."""
    if v is None or v == "" or v == []:
        return "unmarked"
    vals = v if isinstance(v, list) else [v]
    low = [str(x).strip().lower() for x in vals if str(x).strip()]
    if not low:
        return "unmarked"
    if any(x in ENGLISH for x in low):
        return "english"
    return "other"


def subcollections(it):
    cols = it.get("collection") or []
    if isinstance(cols, str):
        cols = [cols]
    return [c for c in cols if c not in UMBRELLA and not c.startswith("fav-")]


def kind_of(subs):
    subs = set(subs)
    if subs & set(GENRE_OF) or subs & SPANISH_SERIES:
        return "fiction magazine"
    if subs & DIME:
        return "dime novel"
    if subs & COMICS:
        return "comic magazine"
    if subs & FILM_GENERAL:
        return "film or general magazine"
    if subs & OTHER:
        return "other"
    return "fiction magazine" if not subs else "unclassified"


def genre_of(subs):
    for c in subs:
        if c in GENRE_OF and GENRE_OF[c] != "unsorted":
            return GENRE_OF[c]
    for c in subs:
        if c in GENRE_OF:
            return GENRE_OF[c]
    return None


def year_of(it):
    """The item's year: the archive's year, else its date, else the first
    plausible four-digit year in the title."""
    for v in (it.get("year"), it.get("date")):
        if v:
            m = re.match(r"\s*((?:18|19|20)\d{2})", str(v))
            if m:
                return int(m.group(1))
    for m in re.finditer(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)", it.get("title") or ""):
        y = int(m.group(1))
        if 1830 <= y <= 2030:
            return y
    return None


def magazine_name(title):
    """The magazine's name from an item title such as
    'Weird Tales v06n05 (1925-11)', '10-Story Detective v01 n01 [1938-01]',
    "Beadle's Half Dime Library (no. 856) (1893 12 19)", 'Interzone 056 (1992 02)',
    'Creepy (Warren Publishing) Issue 092', 'Planet Stories Fall 1939'."""
    t = (title or "").strip()
    t = re.sub(r"^\[[^\]]*\]\s*", "", t)                                   # leading series code [SS-314]
    t = re.sub(r"^\d{1,2}[/.\- ]\d{1,2}[/.\- ](?:18|19|20)\d{2}\s*", "", t)     # leading 15/05/1926
    t = re.sub(r"^(?:18|19|20)\d{2}(?:[ \-.]\d{1,2}){0,2}\s+", "", t)         # leading 1963 09 01
    cuts = [
        r"\s+v(?:ol(?:ume)?)?\.?\s*\d+.*$",                             # v06n05, Vol. 12, Volume 12, V 12no 07
        r"\s*[\(\[]\s*(?:vol|no)\.?\s*\d+.*$",                          # (no. 14) (1877 09 27)
        r"\s+(?:issue|no\.?|#|number|nr\.?)\s*\d+.*$",                  # Issue 092
        r"\s+\d{4}[-–/. ]\d{1,2}.*$",                                   # 1946-05, 1931 11
        r"\s*[\(\[]\s*(?:18|19|20)\d{2}.*$",                            # (1987 Winter) (1934-05)
        rf"\s+{SEASONS}\s*[-,]?\s*(?:18|19|20)\d{{2}}.*$",              # Fall 1939
        rf"\s+{MONTHS}\.?\s+\d{{1,2}},?\s+(?:18|19|20)\d{{2}}.*$",     # March 15, 1952
        rf"\s+{MONTHS}\.?,?\s+(?:18|19|20)\d{{2}}.*$",                  # March 1952
        rf"\s+\d{{1,2}}\s+{MONTHS}\.?,?\s+(?:18|19|20)\d{{2}}.*$",     # 15 May 1926
        r"\s+(?:18|19|20)\d{2}\b.*$",                                   # 1950 - Ziff-Davis
        r"\s+\d{2,3}(?:\s*[-–]\s*\d{2,3})?\s*(?:[\(\[].*)?$",           # Interzone 056, Robot 28-29
    ]
    for c in cuts:
        t = re.sub(c, "", t, flags=re.I)
    t = re.sub(r"(\s*[\(\[][^\)\]]*[\)\]])+\s*$", "", t)                # trailing (Warren Publishing) (c2c)
    t = re.sub(r"[\s\-–—:,]+$", "", t).strip()
    return t or (title or "").strip()


def mag_key(name):
    """Names differing only in a leading 'The' or in punctuation are one magazine."""
    k = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return re.sub(r"^the ", "", k)


def derive(it):
    """Add the derived fields to an item, in place."""
    it["collection"] = [c for c in (it.get("collection") if isinstance(it.get("collection"), list) else [it.get("collection")])
                        if c and not c.startswith("fav-")]
    subs = subcollections(it)
    it["lang_class"] = lang_class(it.get("language"))
    it["kind"] = kind_of(subs)
    it["genre"] = genre_of(subs)
    it["year_derived"] = year_of(it)
    it["magazine"] = magazine_name(it.get("title"))
    return it


def run():
    os.makedirs(OUTDIR, exist_ok=True)
    out = os.path.join(OUTDIR, "items.jsonl")
    tmp = out + ".part"
    n = 0
    cursor = None
    total = None
    with open(tmp, "w", encoding="utf-8") as f:
        while True:
            q = {"q": f"collection:{COLLECTION}", "fields": ",".join(FIELDS), "count": "10000"}
            if cursor:
                q["cursor"] = cursor
            d = fetch(API + "?" + urllib.parse.urlencode(q))
            if total is None:
                total = d.get("total")
            for it in d.get("items", []):
                f.write(json.dumps(derive(it), ensure_ascii=False) + "\n")
                n += 1
            print(f"[s00] {n:,} items fetched" + (f" of {total:,}" if isinstance(total, int) else ""), flush=True)
            cursor = d.get("cursor")
            if not cursor:
                break
    os.replace(tmp, out)
    print(f"[s00] wrote {out}")
    summary()


ENRICH_FIELDS = ["uploader", "contributor", "source", "sponsor", "scanner", "publisher", "creator", "rights",
                 "licenseurl", "possible-copyright-status", "scanningcenter", "curation", "collection_added",
                 "ocr", "ocr_module_version", "ocr_detected_lang", "ocr_detected_lang_conf", "language",
                 "imagecount", "date", "year", "addeddate", "publicdate"]
META_API = "https://archive.org/metadata/"
TAG_RE = re.compile(r"\(([^()]{2,40})\)\s*$")


def enrich(threads=6, limit=None):
    """One metadata call per item (resumable): data/survey/enrich.jsonl."""
    import concurrent.futures
    out = os.path.join(OUTDIR, "enrich.jsonl")
    done = set()
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                done.add(json.loads(line)["identifier"])
            except Exception:
                pass
    ids = [json.loads(l)["identifier"] for l in open(os.path.join(OUTDIR, "items.jsonl"), encoding="utf-8") if l.strip()]
    todo = [i for i in ids if i not in done]
    if limit:
        todo = todo[:limit]
    print(f"[s00] enrich: {len(done):,} done, {len(todo):,} to fetch", flush=True)

    def one(ident):
        try:
            d = fetch(META_API + urllib.parse.quote(ident) + "/metadata", tries=3)
            m = (d or {}).get("result") or {}
            rec = {"identifier": ident, "fetched": time.strftime("%Y-%m-%d")}
            for k in ENRICH_FIELDS:
                if k in m:
                    rec[k] = m[k]
            return rec
        except Exception as e:
            return {"identifier": ident, "fetched": time.strftime("%Y-%m-%d"), "error": str(e)[:200]}
    n = 0
    t0 = time.time()
    with open(out, "a", encoding="utf-8") as f, concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        for rec in ex.map(one, todo):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if n % 500 == 0:
                f.flush()
                print(f"[s00]   {n:,}/{len(todo):,} in {time.time() - t0:.0f}s", flush=True)
    print(f"[s00] enrich: wrote {n:,} records to {out}")
    summary()


def load_enrich():
    p = os.path.join(OUTDIR, "enrich.jsonl")
    out = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if "error" not in r:
                out[r["identifier"]] = r
    return out


def uploader_handle(u):
    """The part before the @, plus the domain for institutional addresses."""
    u = str(u or "").strip().lower()
    if "@" not in u:
        return u or "(none)"
    local, dom = u.split("@", 1)
    return f"{local} ({dom})" if dom in ("archive.org", "textfiles.com") else local


def ocr_engine(v):
    """'ABBYY FineReader 11.0' / 'tesseract 5.3' / ... from the archive's ocr field."""
    v = str(v or "").strip()
    if not v:
        return "(none)"
    m = re.match(r"(ABBYY FineReader \d+(?:\.\d)?|tesseract \d+\.\d+|[A-Za-z]+(?: [A-Za-z]+)? \d+(?:\.\d+)?)", v)
    return m.group(1) if m else v[:40]


def curator_of(v):
    m = re.search(r"\[curator\]([^\[]+)\[/curator\]", str(v or ""))
    return uploader_handle(m.group(1)) if m else None


def scan_tag(title):
    """The scanning-group tag the community writes at the end of a title:
    (Darwin-IA), (Gorgon776), (c2c), (cape1736), (Darwination-McCoy-IA)."""
    m = TAG_RE.search(title or "")
    if not m:
        return None
    t = m.group(1).strip()
    if re.fullmatch(r"[\d\s\-–/.]+|(?:18|19|20)\d{2}.*|vol.*|no\.?.*|spring.*|summer.*|fall.*|winter.*|selections|c2c|pdf|cbr|cbz|epub|djvu|ocr|scan|missing.*|incomplete.*|partial.*", t, flags=re.I):
        return t.lower() if t.lower() == "c2c" else None
    return t


_PUBMAGS = None


def publisher_of(magazine, year):
    """Publisher group of a magazine in a year, from config/publishers_magazines.json."""
    global _PUBMAGS
    if _PUBMAGS is None:
        try:
            cfg = json.load(open(os.path.join(ROOT, "config", "publishers_magazines.json"), encoding="utf-8"))
            _PUBMAGS = [(re.compile(e["pattern"], re.I), e["periods"]) for e in cfg["magazines"]]
        except Exception:
            _PUBMAGS = []
    name = (magazine or "").strip().lower()
    for rx, periods in _PUBMAGS:
        if rx.search(name):
            if year is None:
                return None
            for lo, hi, group in periods:
                if lo <= year <= hi:
                    return group
            return None
    return None


def provenance_summary(items, enrich):
    """The history of transmission and the OCR provenance, from the enriched records."""
    added_year_all, added_year_fiction = Counter(), Counter()
    uploaders, uploaders_fiction = Counter(), Counter()
    upl_years = defaultdict(list)
    curators = Counter()
    engines_fiction, engines_all = Counter(), Counter()
    engine_by_upload_decade = defaultdict(Counter)
    detected_unmarked = Counter()
    tags = Counter()
    coll_added = Counter()
    rights = Counter()
    n_enriched = 0
    for it in items:
        lc = it["lang_class"]
        working = lc in ("english", "unmarked")
        fiction = working and it["kind"] == "fiction magazine"
        ad = it.get("addeddate") or it.get("publicdate") or ""
        ay = int(ad[:4]) if re.match(r"(19|20)\d{2}", ad) else None
        if ay:
            added_year_all[ay] += 1
            if fiction:
                added_year_fiction[ay] += 1
        tg = scan_tag(it.get("title"))
        if tg and fiction:
            tags[tg] += 1
        e = enrich.get(it.get("identifier"))
        if not e:
            continue
        n_enriched += 1
        h = uploader_handle(e.get("uploader"))
        uploaders[h] += 1
        if fiction:
            uploaders_fiction[h] += 1
        if ay:
            upl_years[h].append(ay)
        c = curator_of(e.get("curation"))
        if c:
            curators[c] += 1
        eng = ocr_engine(e.get("ocr"))
        engines_all[eng] += 1
        if fiction:
            engines_fiction[eng] += 1
            if ay:
                engine_by_upload_decade[f"{ay // 10 * 10}s"][eng] += 1
        if lc == "unmarked":
            dl = e.get("ocr_detected_lang")
            try:
                conf = float(e.get("ocr_detected_lang_conf") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            detected_unmarked[(str(dl) if dl else "(none)") + ("" if conf >= 0.9 or not dl else " (low confidence)")] += 1
        ca = e.get("collection_added")
        for x in (ca if isinstance(ca, list) else [ca] if ca else []):
            coll_added[str(x)] += 1
        r = e.get("possible-copyright-status") or e.get("rights") or e.get("licenseurl")
        if r:
            rights[str(r)[:60]] += 1
    return {
        "items_enriched": n_enriched,
        "added_by_year": dict(sorted(added_year_all.items())),
        "fiction_added_by_year": dict(sorted(added_year_fiction.items())),
        "uploader_accounts": len(uploaders),
        "uploaders_top": [{"handle": h, "items": n, "fiction_items": uploaders_fiction.get(h, 0),
                           "years": [min(upl_years[h]), max(upl_years[h])] if upl_years.get(h) else None}
                          for h, n in uploaders.most_common(20)],
        "curators": dict(curators.most_common(10)),
        "ocr_engines_all": dict(engines_all.most_common(12)),
        "ocr_engines_fiction": dict(engines_fiction.most_common(12)),
        "ocr_engine_by_upload_decade": {d: dict(c.most_common(6)) for d, c in sorted(engine_by_upload_decade.items())},
        "detected_language_of_unmarked": dict(detected_unmarked.most_common(12)),
        "scan_tags_fiction": dict(tags.most_common(25)),
        "collection_added": dict(coll_added.most_common(15)),
        "rights_fields": dict(rights.most_common(10)),
    }


def summary():
    items = [json.loads(l) for l in open(os.path.join(OUTDIR, "items.jsonl"), encoding="utf-8") if l.strip()]
    enrich_map = load_enrich()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    pilot = {i["ia_identifier"]: i["id"] for i in cfg.get("issues", [])}
    by_lang, by_class, by_kind, by_media, by_sub = Counter(), Counter(), Counter(), Counter(), Counter()
    work = {"items": 0, "pages": 0, "bytes": 0, "by_decade": Counter(), "by_genre": Counter(), "by_kind": Counter(),
            "fiction_items": 0, "fiction_pages": 0, "fiction_by_decade": Counter(), "fiction_by_genre": Counter(),
            "fiction_by_decade_genre": defaultdict(Counter), "fiction_by_year": Counter()}
    mags = {}
    pages_all = 0
    bytes_all = 0
    with_year = 0
    by_decade_all = Counter()
    by_publisher = Counter()
    by_publisher_decade = defaultdict(Counter)
    for it in items:
        derive(it)
        lc = it["lang_class"]
        by_class[lc] += 1
        lv = it.get("language")
        by_lang[str(lv[0] if isinstance(lv, list) and lv else lv).strip().lower() if lv else "(none)"] += 1
        by_kind[it["kind"]] += 1
        by_media[it.get("mediatype") or "?"] += 1
        for c in subcollections(it):
            by_sub[c] += 1
        y = it["year_derived"]
        dec = f"{y // 10 * 10}s" if y else "no year"
        if y:
            with_year += 1
        by_decade_all[dec] += 1
        try:
            np = int(it.get("imagecount") or 0)
            nb = int(it.get("item_size") or 0)
        except (TypeError, ValueError):
            np, nb = 0, 0
        pages_all += np
        bytes_all += nb
        working = lc in ("english", "unmarked")
        fiction = working and it["kind"] == "fiction magazine"
        if fiction:
            pub = publisher_of(it["magazine"], y)
            by_publisher[pub or "not assigned"] += 1
            if pub:
                by_publisher_decade[dec][pub] += 1
        if working:
            work["items"] += 1
            work["pages"] += np
            work["bytes"] += nb
            work["by_decade"][dec] += 1
            work["by_kind"][it["kind"]] += 1
            if fiction:
                work["fiction_items"] += 1
                work["fiction_pages"] += np
                work["fiction_by_decade"][dec] += 1
                if y:
                    work["fiction_by_year"][y] += 1
                work["fiction_by_genre"][it["genre"] or "unsorted"] += 1
                work["fiction_by_decade_genre"][dec][it["genre"] or "unsorted"] += 1
        k = mag_key(it["magazine"])
        if not k:
            continue
        m = mags.setdefault(k, {"names": Counter(), "items": 0, "pages": 0, "years": [], "languages": Counter(),
                                "kinds": Counter(), "genres": Counter(), "subcollections": Counter(), "pilot": [],
                                "working_items": 0, "fiction_items": 0})
        m["names"][it["magazine"]] += 1
        m["items"] += 1
        m["pages"] += np
        m["languages"][lc] += 1
        m["kinds"][it["kind"]] += 1
        if it["genre"]:
            m["genres"][it["genre"]] += 1
        if y:
            m["years"].append(y)
        for c in subcollections(it):
            m["subcollections"][c] += 1
        if working:
            m["working_items"] += 1
        if fiction:
            m["fiction_items"] += 1
        if it.get("identifier") in pilot:
            m["pilot"].append(pilot[it["identifier"]])
    mag_out = {}
    for k, m in mags.items():
        mag_out[k] = {"name": m["names"].most_common(1)[0][0], "items": m["items"], "pages": m["pages"],
                      "working_items": m["working_items"], "fiction_items": m["fiction_items"],
                      "years": [min(m["years"]), max(m["years"])] if m["years"] else None,
                      "kind": m["kinds"].most_common(1)[0][0],
                      "genre": (m["genres"].most_common(1)[0][0] if m["genres"] else None),
                      "languages": dict(m["languages"]), "subcollections": dict(m["subcollections"].most_common(3)),
                      "pilot_issues": m["pilot"]}
    fiction_mags = [m for m in mag_out.values() if m["fiction_items"] > 0]
    summ = {"generated": time.strftime("%Y-%m-%d %H:%M"), "collection": COLLECTION, "source": API,
            "total_items": len(items), "page_images_total": pages_all, "bytes_total": bytes_all,
            "items_with_year": with_year, "by_decade": dict(sorted(by_decade_all.items())),
            "by_language_class": dict(by_class), "by_language": dict(by_lang.most_common(30)),
            "by_kind": dict(by_kind.most_common()), "by_mediatype": dict(by_media),
            "by_subcollection": dict(by_sub.most_common()),
            "working_corpus": {
                "definition": "items marked English plus items with no language given; items marked as another language are excluded",
                "items": work["items"], "page_images": work["pages"], "bytes": work["bytes"],
                "by_kind": dict(work["by_kind"].most_common()), "by_decade": dict(sorted(work["by_decade"].items())),
                "fiction_definition": "working-corpus items the archive files as fiction magazines (pulps and digests): not dime novels, film magazines or comics",
                "fiction_items": work["fiction_items"], "fiction_page_images": work["fiction_pages"],
                "fiction_by_decade": dict(sorted(work["fiction_by_decade"].items())),
                "fiction_by_year": {str(y): n for y, n in sorted(work["fiction_by_year"].items())},
                "fiction_by_genre": dict(work["fiction_by_genre"].most_common()),
                "fiction_by_decade_genre": {d: dict(c.most_common()) for d, c in sorted(work["fiction_by_decade_genre"].items())},
                "fiction_by_publisher": dict(by_publisher.most_common()),
                "fiction_by_decade_publisher": {d: dict(c.most_common(8)) for d, c in sorted(by_publisher_decade.items())},
                "fiction_magazines": len(fiction_mags),
                "fiction_magazines_top": [{"name": m["name"], "items": m["fiction_items"], "years": m["years"], "genre": m["genre"]}
                                          for m in sorted(fiction_mags, key=lambda m: -m["fiction_items"])[:60]]},
            "provenance": provenance_summary(items, enrich_map),
            "magazines_distinct": len(mag_out),
            "pilot_identifiers_found": sorted(pid for it in items for ident, pid in pilot.items() if it.get("identifier") == ident),
            "pilot_issues": len(pilot)}
    json.dump(summ, open(os.path.join(OUTDIR, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(mag_out, open(os.path.join(OUTDIR, "magazines.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    w = summ["working_corpus"]
    print(f"[s00] {len(items):,} items: {by_class['english']:,} marked English, {by_class['unmarked']:,} unmarked, "
          f"{by_class['other']:,} other languages; working corpus {w['items']:,} items, of which {w['fiction_items']:,} "
          f"fiction magazines ({w['fiction_page_images']:,} page images, {w['fiction_magazines']:,} magazine names); "
          f"pilot identifiers found {len(summ['pilot_identifiers_found'])}/{len(pilot)}")


def selftest():
    cases = {
        "Weird Tales v06n05 (1925-11)": "Weird Tales",
        "10-Story Detective v01 n01 [1938-01]": "10-Story Detective",
        "10 Story Book v37n03 1938-08.Sun (Darwination-McCoy-IA)": "10 Story Book",
        "10 Story Detective April 1946": "10 Story Detective",
        "Astounding Stories 1930-01": "Astounding Stories",
        "Astounding Science Fiction v41n5": "Astounding Science Fiction",
        "Galaxy Magazine March 1952": "Galaxy Magazine",
        "Thrilling Detective v63 n01 1948-12": "Thrilling Detective",
        "Beadle's Half Dime Library (no. 856) (1893 12 19)": "Beadle's Half Dime Library",
        "New York Saturday Journal (vol. 7) (1876 07 08)": "New York Saturday Journal",
        "Creepy (Warren Publishing) Issue 092": "Creepy",
        "Interzone 056 (1992 02) (Gorgon776)": "Interzone",
        "Deathrealm 04 (1987 Winter)": "Deathrealm",
        "Modern Screen 1935-12 Vol. 12, No. 1": "Modern Screen",
        "Amazing Stories V 12no 07 ( 1938 12. Ziff Davis) (selections)": "Amazing Stories",
        "Fantastic Adventures v12n01 - 1950 - Ziff-Davis cape1736": "Fantastic Adventures",
        "Planet Stories Fall 1939": "Planet Stories",
        "Wonder Stories Quarterly Winter 1930": "Wonder Stories Quarterly",
        "Love Story 15 May 1926": "Love Story",
        "15/05/1926 Love Story": "Love Story",
        "1963 09 01 Return Of The Shadow": "Return Of The Shadow",
        "[SS-314] Keith Luger (1956) Argos 3 no contesta": "Keith Luger",
        "Robot 28-29": "Robot",
        "Amazing Stories Volume 12 Number 3": "Amazing Stories",
        "Radio Stars (1934-05)": "Radio Stars",
    }
    bad = [(t, magazine_name(t), want) for t, want in cases.items() if magazine_name(t) != want]
    assert not bad, bad
    assert lang_class("eng") == "english" and lang_class(["English"]) == "english"
    assert lang_class(None) == "unmarked" and lang_class("fre") == "other"
    assert year_of({"title": "Beadle's Half Dime Library (no. 856) (1893 12 19)"}) == 1893
    assert year_of({"date": "1936-11-01T00:00:00Z", "title": "x"}) == 1936
    assert year_of({"year": "1952", "title": "x"}) == 1952
    assert year_of({"title": "Robot 28-29"}) is None
    assert mag_key("The Popular Magazine") == mag_key("Popular Magazine")
    assert kind_of(["pulp_misc_sf"]) == "fiction magazine" and genre_of(["pulp_misc_sf"]) == "science fiction"
    assert kind_of(["beadlesdimenovels", "nickles-and-dimes"]) == "dime novel"
    assert kind_of(["photoplaymagazine"]) == "film or general magazine" and genre_of(["photoplaymagazine"]) is None
    assert genre_of(["pulp_fiction_misc", "weirdtalesmagazine"]) == "weird"
    assert uploader_handle("pulp@textfiles.com") == "pulp (textfiles.com)" and uploader_handle("someone@gmail.com") == "someone"
    assert ocr_engine("ABBYY FineReader 11.0 (Extended OCR)") == "ABBYY FineReader 11.0"
    assert ocr_engine("tesseract 5.3.0-6-g76ae") == "tesseract 5.3" and ocr_engine(None) == "(none)"
    assert curator_of("[curator]jscott@archive.org[/curator][date]20250104070210[/date]") == "jscott (archive.org)"
    assert scan_tag("10-Story Book v28n02 (1928-02) (Darwin-IA)") == "Darwin-IA"
    assert scan_tag("Interzone 056 (1992 02) (Gorgon776)") == "Gorgon776" and scan_tag("Weird Tales v06n05 (1925-11)") is None
    assert scan_tag("Amazing Stories v12n07 (1938-12) (selections)") is None
    assert publisher_of("Astounding Stories", 1930) == "Clayton" and publisher_of("Astounding Science Fiction", 1945) == "Street & Smith"
    assert publisher_of("Weird Tales", 1925) == "Popular Fiction (Weird Tales)" and publisher_of("Nowhere Tales", 1925) is None
    assert publisher_of("Galaxy", 1952) == "Galaxy Publishing" and publisher_of("Wild West Weekly", 1936) == "Street & Smith"
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--enrich", action="store_true", help="fetch each item's own metadata record (resumable)")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None, help="enrich at most this many items (a test run)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.run:
        run()
    elif a.enrich:
        enrich(a.threads, a.limit)
    elif a.summary:
        summary()
    else:
        sys.exit("pass --run, --enrich, --summary or --selftest")


if __name__ == "__main__":
    main()
