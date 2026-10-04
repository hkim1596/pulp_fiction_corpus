#!/usr/bin/env python3
"""s00b — the selection list: which items of the archive enter the corpus, and why.

Reads data/survey/items.jsonl (from s00_survey.py --run; every item of the collection with the derived fields)
and data/survey/enrich.jsonl if present (the per-item metadata record: page count, OCR engine, detected
language). Applies the rule the authors settled on 23 September 2026, clause by clause, and writes:

  config/corpus_issues.json      the issue list in the pilot config's shape (id, ia_identifier, magazine,
                                 cover_date, genre, format ...) + selection fields; not tracked by git (generated
                                 on the server, archived in Dropbox); the stages refuse it until it is approved
  config/corpus_approval.json    written by --approve "Name" over the list: the list's fingerprint, the counts, the
                                 settings (tracked by git: the audit trail of the run)
  data/survey/selection.jsonl    every item of the collection with its decision and reason
  data/survey/selection_counts.json  the counts under each clause (for the datasheet)
  data/survey/duplicates.json    the duplicate groups and the item kept in each

The clauses, in order (an item is set aside at the first clause it fails):
  1 language: marked English, or no language record (settings.selection.languages)
  2 dated: a cover year from the date field, else the year field, else the title; undated items are KEPT in the
    corpus but flagged undated (the protocol: "retained but excluded from dated analyses")
  3 window: year_from <= year <= year_to
  4 kind: fiction magazine (not dime-novel series, not non-fiction magazines, not general-interest magazines)
  5 duplicate: one record per issue (same magazine; same volume+number, whole number, full date, or cover month for
    magazines that are not more than monthly); the kept one is the most complete scan, the others are alternates

    python3 pipeline/s00b_select.py            # write the list
    python3 pipeline/s00b_select.py --selftest
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import ROOT, settings, write_json_atomic, log  # noqa: E402
import s00_survey as s00  # noqa: E402  (the survey's own rules: kind, genre, year, magazine name)

SURVEY = os.path.join(ROOT, "data", "survey")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SEASON_MONTH = {"spring": 4, "summer": 7, "fall": 10, "autumn": 10, "winter": 1}


MON_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
SEASON_RE = r"(spring|summer|fall|autumn|winter)"


def _month_from_text(s, year):
    """'YYYY-MM' (or 'YYYY-MMs' for a season) read from one string, else None."""
    m = re.search(rf"{year}[-/. ]?(\d{{1,2}})(?!\d)", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{year}-{int(m.group(1)):02d}"
    m = re.search(MON_RE + r"[a-z]*\.?,?\s+\d{0,2},?\s*" + str(year), s, re.I)
    if m:
        return f"{year}-{MONTHS[m.group(1).lower()]:02d}"
    m = re.search(r"\b(\d{1,2})\s+" + MON_RE + r"[a-z]*\.?,?\s+" + str(year), s, re.I)
    if m:
        return f"{year}-{MONTHS[m.group(2).lower()]:02d}"
    m = re.search(SEASON_RE + r"\s*[-,]?\s*" + str(year), s, re.I)       # "Fall 1939", "Summer, 1952"
    if m:
        return f"{year}-{SEASON_MONTH[m.group(1).lower()]:02d}s"
    m = re.search(str(year) + r"\s*[-,./]?\s*" + SEASON_RE, s, re.I)     # "(1952 Summer)", "[1952-Fall]", "1952.Summer"
    if m:
        return f"{year}-{SEASON_MONTH[m.group(1).lower()]:02d}s"
    return None


def date_field(it):
    """(month, day) from the archive's date field, else (None, None)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(it.get("date") or ""))
    if not m:
        return None, None
    return int(m.group(2)), int(m.group(3))


def cover_month(it, year):
    """'YYYY-MM' when a month can be read, else 'YYYY' (quarterlies, annuals, numbered series).

    The title is read first. The archive's date field is used next, except that a date of January 1st is treated
    as "year only": measured on 4 October 2026, 258 of the window's 01-01 dates stood under a title that named
    another month or a season, so 01-01 is a placeholder as often as it is a cover date."""
    t = _month_from_text(it.get("title") or "", year)
    if t:
        return t
    mm, dd = date_field(it)
    if mm and not (mm == 1 and dd == 1):
        return f"{year}-{mm:02d}"
    return str(year)


def cover_day(it, year):
    """The day of the cover date (weeklies), else None.  Read from the title; from the date field only when its
    day is not the 1st (4,885 of 6,200 dated items in the window carry day 01, the archive's placeholder)."""
    s = str(it.get("title") or "")
    m = re.search(rf"{year}[-/. ](\d{{1,2}})[-/. ](\d{{1,2}})(?!\d)", s)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        return int(m.group(2))
    m = re.search(MON_RE + r"[a-z]*\.?\s+(\d{1,2}),?\s+" + str(year), s, re.I)
    if m and 1 <= int(m.group(2)) <= 31:
        return int(m.group(2))
    m = re.search(r"\b(\d{1,2})\s+" + MON_RE + r"[a-z]*\.?,?\s+" + str(year), s, re.I)
    if m and 1 <= int(m.group(1)) <= 31:
        return int(m.group(1))
    mm, dd = date_field(it)
    if dd and dd != 1:
        return dd
    return None


def vol_num(title):
    """(volume, number) from forms like v06n05, V 12 no 07, v-03-n-02, v026n003, Vol. 12, No. 3; else None."""
    t = title or ""
    # (?<![A-Za-z0-9]) instead of \b: archive titles join words with underscores ("Tales_v26n03_Popular"), and an
    # underscore counts as a word character, so \b would never see the "v" there.
    m = re.search(r"(?<![A-Za-z0-9])v(?:ol(?:ume)?)?\.?[\s\-_]*(\d{1,3})[\s\-_,/]*n(?:o|um(?:ber)?|r)?\.?[\s\-_]*(\d{1,3})(?![0-9])", t, re.I)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def whole_number(title):
    """The issue's whole number from '#08', '# 24', 'No. 856', 'n432' when the title carries no volume; else None."""
    t = title or ""
    if vol_num(t):
        return None
    m = re.search(r"#\s*(\d{1,4})(?![0-9])", t) or re.search(r"(?<![A-Za-z0-9])n(?:o|um(?:ber)?|r)?\.?\s*(\d{1,4})(?![0-9])", t, re.I)
    return int(m.group(1)) if m else None


def issue_key(d):
    """What identifies an issue inside its magazine: ('vn', volume, number) or ('no', whole number) or None."""
    vn = d.get("vol_num")
    if vn:
        return ("vn",) + tuple(vn)
    if d.get("whole_number") is not None:
        return ("no", d["whole_number"])
    return None


def same_issue(a, b, frequent):
    """Two kept items of one magazine: are they the same issue?  Only positive evidence merges them.
    1 both carry a volume+number, or both a whole number: equal keys (a whole number also needs the same year);
    2 otherwise the cover month must agree and be a real month (year-only records never merge);
    3 when either carries a day, both must, and agree;
    4 month alone merges only magazines that are not more than monthly (frequent = more than 14 distinct issues
      in some year)."""
    ka, kb = issue_key(a), issue_key(b)
    if ka and kb and ka[0] == kb[0]:
        if ka[0] == "no":
            return ka == kb and a["year"] == b["year"]
        return ka == kb
    if a["cover_month"] != b["cover_month"] or "-" not in a["cover_month"]:
        return False
    da, db = a.get("day"), b.get("day")
    if da or db:
        return bool(da and db and da == db)
    return not frequent


def is_frequent(members):
    """More than 14 distinct issues in one year means weekly or semi-monthly; a month alone is then not an issue."""
    per_year = defaultdict(set)
    for m in members:
        per_year[m["year"]].add(issue_key(m) or (m["cover_month"], m.get("day")))
    return max((len(v) for v in per_year.values()), default=0) > 14


def issue_id(magazine, cm, ident):
    key = re.sub(r"[^a-z0-9]+", "_", magazine.lower()).strip("_")[:28] or "mag"
    return f"{key}_{cm.replace('-', '_')}_{re.sub(r'[^A-Za-z0-9]+', '', ident)[-10:].lower()}"


def classify_general(it):
    """Split the survey's 'film or general magazine' into non-fiction and general-interest (the meeting deck)."""
    subs = set(s00.subcollections(it))
    gi = set(settings()["selection"]["general_interest_subcollections"])
    return "general-interest magazine" if subs & gi else "non-fiction magazine"


def load_items():
    p = os.path.join(SURVEY, "items.jsonl")
    if not os.path.exists(p):
        sys.exit(f"missing {p}: run  python3 pipeline/s00_survey.py --run  first")
    items = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    enrich = {}
    pe = os.path.join(SURVEY, "enrich.jsonl")
    if os.path.exists(pe):
        for l in open(pe, encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                enrich[r.get("identifier")] = r
    return items, enrich


def select(items, enrich, cfg=None):
    cfg = cfg or settings()["selection"]
    counts = Counter()
    decisions = []
    kept = []
    for it in items:
        ident = it["identifier"]
        en = enrich.get(ident, {})
        d = {"identifier": ident, "title": it.get("title"), "magazine": it.get("magazine") or s00.magazine_name(it.get("title")),
             "lang_class": it.get("lang_class") or s00.lang_class(it.get("language")), "kind": it.get("kind"),
             "genre": it.get("genre"), "year": it.get("year_derived"), "pages": it.get("imagecount") or en.get("imagecount") or 0,
             "ocr_engine": en.get("ocr"), "ocr_detected_lang": en.get("ocr_detected_lang"), "decision": None, "reason": None}
        counts["0 items in the collection"] += 1
        if d["lang_class"] not in cfg["languages"]:
            d["decision"], d["reason"] = "out", "language: marked as not English"
            counts["1 set aside: not English"] += 1
            decisions.append(d); continue
        counts["1 English or no language record"] += 1
        if d["ocr_detected_lang"] and str(d["ocr_detected_lang"]).lower() not in ("en", "eng", "english"):
            d["decision"], d["reason"] = "out", f"language: archive text detected as {d['ocr_detected_lang']}"
            counts["1b set aside: archive text not English"] += 1
            decisions.append(d); continue
        y = d["year"]
        if y is None:
            d["undated"] = True
            counts["2 undated (kept, out of dated analyses)"] += 1
        else:
            counts["2 dated"] += 1
            if not (cfg["year_from"] <= y <= cfg["year_to"]):
                d["decision"], d["reason"] = "out", f"window: {y} outside {cfg['year_from']}-{cfg['year_to']}"
                counts["3 set aside: outside the window"] += 1
                decisions.append(d); continue
            counts["3 in the window"] += 1
        kind = d["kind"]
        if kind == "film or general magazine":
            kind = classify_general(it)
        d["kind"] = kind
        if kind not in cfg["kinds_in"]:
            d["decision"], d["reason"] = "out", f"kind: {kind}"
            counts[f"4 set aside: {kind}"] += 1
            decisions.append(d); continue
        counts["4 fiction magazine"] += 1
        d["cover_month"] = cover_month(it, y) if y else "undated"
        d["day"] = cover_day(it, y) if y else None
        d["vol_num"] = vol_num(it.get("title"))
        d["whole_number"] = whole_number(it.get("title"))
        d["magazine_key"] = s00.mag_key(d["magazine"])
        d["decision"] = "in"
        kept.append(d)
        decisions.append(d)
    # 5 duplicates: one record per issue (same_issue() decides: volume+number, whole number, full date, or month for
    #   magazines that are not more than monthly; the most complete scan is kept and the others listed as alternates)
    by_mag = defaultdict(list)
    for d in kept:
        if not d.get("undated"):
            by_mag[d["magazine_key"]].append(d)
    dup_groups = {}
    for mk, members in by_mag.items():
        frequent = is_frequent(members)
        members.sort(key=lambda m: (-(m["pages"] or 0), m["identifier"]))
        groups = []  # list of lists; the first member of each is the kept one
        for m in members:
            for g in groups:
                if same_issue(g[0], m, frequent):
                    g.append(m); break
            else:
                groups.append([m])
        for g in groups:
            if len(g) < 2:
                continue
            keep = g[0]
            for m in g[1:]:
                m["decision"], m["reason"] = "alternate", f"duplicate scan of {keep['identifier']}"
                m["alternate_of"] = keep["identifier"]
                counts["5 set aside: duplicate scan (kept as alternate)"] += 1
            keep["alternates"] = [m["identifier"] for m in g[1:]]
            dup_groups[f"{mk} | {keep['cover_month']}" + (f"-{keep['day']:02d}" if keep.get("day") else "") + (f" | v{keep['vol_num'][0]}n{keep['vol_num'][1]}" if keep.get("vol_num") else "")] = {"kept": keep["identifier"], "alternates": keep["alternates"]}
    final = [d for d in kept if d["decision"] == "in"]
    # one display name per magazine key ("The Popular Magazine" and "Popular Magazine" are one magazine: the most
    # frequent spelling among its issues is used for all of them, so cross-issue assembly sees one magazine)
    names = defaultdict(Counter)
    for d in final:
        names[d["magazine_key"]][d["magazine"]] += 1
    for d in final:
        d["magazine"] = names[d["magazine_key"]].most_common(1)[0][0]
    counts["5 issues in the corpus"] = len(final)
    counts["5b magazines"] = len(names)
    counts["5a of them undated"] = sum(1 for d in final if d.get("undated"))
    return decisions, final, dup_groups, counts


def to_issue_record(d):
    """The pilot config's shape, so every stage can read the corpus list."""
    cm = d.get("cover_month", "undated")
    return {"id": issue_id(d["magazine"], cm if cm != "undated" else "undated", d["identifier"]),
            "ia_identifier": d["identifier"], "magazine": d["magazine"], "cover_date": cm, "year": d.get("year"),
            "genre": d.get("genre") or "unsorted",
            # pulp or digest cannot be read from the archive's records; s01c records the page size of the scans
            # (pixels and ppi) and a later pass sets this field from the measured trim size
            "format": "unknown",
            "undated": bool(d.get("undated")), "pages_in_record": d.get("pages") or 0, "ocr_engine": d.get("ocr_engine"),
            "alternates": d.get("alternates", []), "gold": None}


def write_outputs(decisions, final, dup_groups, counts):
    os.makedirs(SURVEY, exist_ok=True)
    with open(os.path.join(SURVEY, "selection.jsonl"), "w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    write_json_atomic(os.path.join(SURVEY, "selection_counts.json"), dict(sorted(counts.items())))
    write_json_atomic(os.path.join(SURVEY, "duplicates.json"), dup_groups)
    issues = [to_issue_record(d) for d in final]
    ids = Counter(i["id"] for i in issues)
    for i in issues:  # make ids unique if two items collapse to one key
        if ids[i["id"]] > 1:
            i["id"] = i["id"] + "_" + re.sub(r"[^A-Za-z0-9]+", "", i["ia_identifier"])[:6].lower()
    # the download order: dated issues first (by magazine, then date), the undated ones at the end, so that the
    # decision about them (604 items, many of them fan magazines of unknown date) can wait without holding anything up
    order = settings()["download"].get("order", "magazine_then_date")
    if order == "magazine_then_date":
        issues.sort(key=lambda i: (i["undated"], i["magazine"].lower(), i["cover_date"]))
    else:
        issues.sort(key=lambda i: (i["undated"], i["cover_date"], i["magazine"].lower()))
    cfg = {"_comment": "The corpus issue list written by s00b_select.py from the archive's records (not tracked by git: "
                       "generated on the server, archived in the Dropbox folder). The stages refuse to run until Heejin "
                       "reviews the counts (data/survey/selection_counts.json) and the list and runs "
                       "`s00b_select.py --approve \"Heejin Kim\"` over it, which writes the tracked config/corpus_approval.json "
                       "with this list's fingerprint. Same shape as pilot_issues.json so every stage can read it.",
           "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "selection_settings": settings()["selection"], "counts": dict(sorted(counts.items())), "issues": issues}
    write_json_atomic(os.path.join(ROOT, "config", "corpus_issues.json"), cfg)
    return cfg


def selftest():
    items = [
        {"identifier": "A", "title": "Weird Tales v06n05 (1925-11)", "language": "eng", "collection": ["weirdtalesmagazine"], "imagecount": 148},
        {"identifier": "B", "title": "Weird Tales v06n05 (1925-11) alt scan", "language": "eng", "collection": ["weirdtalesmagazine"], "imagecount": 100},
        {"identifier": "C", "title": "Beadle's Half Dime Library (no. 856) (1893 12 19)", "language": "eng", "collection": ["beadlesdimenovels"]},
        {"identifier": "D", "title": "Galaxy v03n04 (1952-01)", "language": "spa", "collection": ["galaxymagazine"]},
        {"identifier": "E", "title": "Interzone 056 (1992 02)", "language": "eng", "collection": ["interzonemagazine"]},
        {"identifier": "F", "title": "Planet Stories Fall 1939", "language": None, "collection": ["planetstories"]},
        {"identifier": "G", "title": "Collier's 1930-05-10", "language": "eng", "collection": ["colliersmagazine"]},
        {"identifier": "H", "title": "Some Pulp (no date)", "language": "eng", "collection": ["pulp_fiction_misc"]},
        {"identifier": "I", "title": "Argosy v148n01 (1923-05-19)", "language": "eng", "collection": ["argosymagazine"], "imagecount": 144},
        {"identifier": "J", "title": "Argosy v148n02 (1923-05-26)", "language": "eng", "collection": ["argosymagazine"], "imagecount": 144},
        {"identifier": "K", "title": "Argosy v148n01 (1923-05-19) second scan", "language": "eng", "collection": ["argosymagazine"], "imagecount": 140},
        # placeholder dates: the archive says 1897-01-01 for every number; the title carries the real date and number
        {"identifier": "L", "title": "New Nick Carter Weekly #08 [1897-02-20]", "date": "1897-01-01T00:00:00Z", "language": "eng", "collection": ["pulpmagazinearchive"]},
        {"identifier": "M", "title": "New Nick Carter Weekly #09 [1897-02-27]", "date": "1897-01-01T00:00:00Z", "language": "eng", "collection": ["pulpmagazinearchive"]},
        # whole numbers, year only: different numbers are different issues, the same number twice is one issue
        {"identifier": "N", "title": "American Science Fiction #24 (1954)", "language": "eng", "collection": ["pulpmagazinearchive"], "imagecount": 36},
        {"identifier": "O", "title": "American Science Fiction #24 (1954) (BL)", "language": "eng", "collection": ["pulpmagazinearchive"], "imagecount": 37},
        {"identifier": "P", "title": "American Science Fiction #26 (1954)", "language": "eng", "collection": ["pulpmagazinearchive"], "imagecount": 36},
        # a season written after the year, and a date field of January 1st that the title contradicts
        {"identifier": "Q", "title": "Fantastic v01n01 (1952 Summer)", "date": "1952-01-01T00:00:00Z", "language": "eng", "collection": ["pulpmagazinearchive"], "imagecount": 164},
        {"identifier": "R", "title": "Fantastic Summer 1952", "date": "1952-01-01T00:00:00Z", "language": "eng", "collection": ["pulpmagazinearchive"], "imagecount": 160},
        {"identifier": "S", "title": "Fantastic Fall 1952", "date": "1952-01-01T00:00:00Z", "language": "eng", "collection": ["pulpmagazinearchive"], "imagecount": 160},
        # year only, no number, no month: never merged
        {"identifier": "T", "title": "Thrilling Wonder Stories 1940", "language": "eng", "collection": ["pulpmagazinearchive"]},
        {"identifier": "U", "title": "Thrilling Wonder Stories 1940 (another scan)", "language": "eng", "collection": ["pulpmagazinearchive"]},
    ]
    for it in items:
        s00.derive(it)
    decisions, final, dups, counts = select(items, {})
    dec = {d["identifier"]: d["decision"] for d in decisions}
    assert dec == {"A": "in", "B": "alternate", "C": "out", "D": "out", "E": "out", "F": "in", "G": "out", "H": "in",
                   "I": "in", "J": "in", "K": "alternate", "L": "in", "M": "in", "N": "alternate", "O": "in", "P": "in",
                   "Q": "in", "R": "alternate", "S": "in", "T": "in", "U": "in"}, dec
    cm = {d["identifier"]: d.get("cover_month") for d in decisions}
    assert cm["L"] == "1897-02" and cm["Q"] == "1952-07s" and cm["S"] == "1952-10s" and cm["T"] == "1940", cm
    assert whole_number("New Nick Carter Weekly #08 [1897-02-20]") == 8 and whole_number("Weird Tales v06n05 (1925-11)") is None
    assert whole_number("Boys' First-Rate Pocket Library n432") == 432 and whole_number("Beadle's Half Dime Library (no. 856) (1893 12 19)") == 856
    assert cover_day({"title": "New Nick Carter Weekly #08 [1897-02-20]", "date": "1897-01-01T00:00:00Z"}, 1897) == 20
    assert cover_day({"title": "Blue Book May 1923", "date": "1923-05-01T00:00:00Z"}, 1923) is None
    assert cover_day({"title": "Argosy All-Story Weekly", "date": "1923-05-19T00:00:00Z"}, 1923) == 19
    assert vol_num("Weird Tales v06n05 (1925-11)") == (6, 5) and vol_num("Fifteen_Western_Tales_v26n03_Popular_Jan_1953") == (26, 3)
    assert vol_num("popular-magazine-v-071-n-06-1924-04-07-sas") == (71, 6) and vol_num("Planet Stories Fall 1939") is None
    assert cover_day({"title": "popular-magazine-v-071-n-06-1924-04-07-sas"}, 1924) == 7
    assert next(d for d in decisions if d["identifier"] == "F")["cover_month"] == "1939-10s"
    assert next(d for d in decisions if d["identifier"] == "H").get("undated") is True
    assert counts["5 issues in the corpus"] == 13 and counts["5a of them undated"] == 1, counts
    assert dups["weird tales | 1925-11 | v6n5"] == {"kept": "A", "alternates": ["B"]} and dups["argosy | 1923-05-19 | v148n1"]["alternates"] == ["K"], dups
    recs = [to_issue_record(d) for d in final]
    assert all(r["id"] and r["cover_date"] for r in recs)
    print("s00b selftest ok")


def approve(name, path=None):
    """Write config/corpus_approval.json for the list at hand (its fingerprint, counts and settings)."""
    from corpus_lib import CORPUS_CONFIG, APPROVAL_PATH, list_sha256
    path = path or CORPUS_CONFIG
    cfg = json.load(open(path, encoding="utf-8"))
    rec = {"_comment": "The approval of the corpus issue list (the Registered Report audit trail). list_sha256 is the "
                       "fingerprint of the approved config/corpus_issues.json; the stages refuse any other list.",
           "approved": True, "approved_by": name, "approved_date": time.strftime("%Y-%m-%d"), "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "issues": len(cfg["issues"]), "undated": sum(1 for i in cfg["issues"] if i.get("undated")),
           "list_sha256": list_sha256(cfg), "list_path": os.path.relpath(path, ROOT),
           "counts": cfg.get("counts"), "selection_settings": cfg.get("selection_settings")}
    write_json_atomic(APPROVAL_PATH, rec)
    log("s00b", f"approval written to {os.path.relpath(APPROVAL_PATH, ROOT)}: {rec['issues']:,} issues, fingerprint {rec['list_sha256'][:12]}…")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--approve", metavar="NAME", help='approve the current list: --approve "Heejin Kim"')
    ap.add_argument("--list", help="with --approve: the list file (default config/corpus_issues.json)")
    args = ap.parse_args()
    if args.selftest:
        selftest(); return
    if args.approve:
        approve(args.approve, args.list); return
    items, enrich = load_items()
    decisions, final, dup_groups, counts = select(items, enrich)
    cfg = write_outputs(decisions, final, dup_groups, counts)
    for k, v in sorted(counts.items()):
        log("s00b", f"{k}: {v:,}")
    log("s00b", f"wrote config/corpus_issues.json with {len(cfg['issues']):,} issues (not yet approved) and data/survey/selection*.json")


if __name__ == "__main__":
    main()
