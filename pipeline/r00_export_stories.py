#!/usr/bin/env python3
"""r00 — export the pilot's article records as one JSONL file.

The text-reuse pipeline (r01…) works on STORIES, not pages. This script
asks the website's own replay engine (webapp/app.py: machine assembly
plus every human correction, replayed in order) for the current state of
every article in every pilot issue, and writes one JSON line per
article. It must run on a machine that holds the data/ tree (the main
server, or the Studio while it is the live server):

    cd <project folder> && python3 pipeline/r00_export_stories.py

With --corpus (since 5 October 2026): the corpus issues, one file per issue in data/export/corpus/,
written again only when an issue's live records or corrections changed:

    python3 pipeline/r00_export_stories.py --corpus [--issue <id>] [--force]

Output: data/pilot_stories.jsonl — every article of every type (stories,
serial parts, poems, features, letters, advertisements, contents pages),
with its metadata and reading text. Later stages select by type; a record
with contains_excerpt (a house announcement quoting a story) is an
advertisement and stays out of the reuse inventory by that rule. Nothing
here touches the machine output or the annotation logs; it only reads.
"""
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webapp"))
import app  # noqa: E402  (the website module; read-only use of its replay)

OUT = os.path.join(ROOT, "data", "pilot_stories.jsonl")
EXPORT_DIR = os.path.join(ROOT, "data", "export")
STORY_CORPUS_TYPES = ("story",)      # the story-level corpus (protocol section 2); every other type is the parallel corpus
MIN_STORY_WORDS = 50                 # the reuse stages' floor (r02.MIN_TOKENS): shorter story records are fragments


def record_of(a, iid, meta):
    """One record of the live assembly (people's corrections replayed) as an export line."""
    text = (a.get("text") or "").strip()
    rec = {
        "story_id": a["article_id"],
        "issue": iid,
        "magazine": meta.get("magazine"),
        "cover_date": meta.get("cover_date"),
        "genre": meta.get("genre"),
        "format": meta.get("format"),
        "type": a.get("type") or "other",
        "title": a.get("title"),
        "author": a.get("author"),
        "teaser": a.get("teaser"),
        "author_credit": a.get("author_credit"),        # "Author of 'Men Like Gods,' etc." (assembly v2.1.2)
        "illustrator": a.get("illustrator"),            # "Illustrated by WILLER" (assembly v2.2)
        "synopsis": a.get("synopsis"),                  # the recap on a later instalment: not story text, not in the reuse inventory
        "department": a.get("department"),              # the standing department the record belongs to (config/departments.json)
        "serial": a.get("serial"),                      # {part_label, part_n, part_total, source, prev, next} for a serial instalment
        "work_title": a.get("work_title"),              # the work's title without the instalment marker
        "work_id": a.get("work_id"),                    # shared by every instalment of one work (cross_issue pass)
        "subtitle": a.get("subtitle"),
        "title_as_printed": a.get("title_as_printed"),
        "author_as_printed": a.get("author_as_printed"),
        "title_source": a.get("title_source"),
        "author_source": a.get("author_source"),
        # advertisements (assembly v2.1): class, advertiser, the works a house
        # announcement names, and whether it quotes one of them verbatim
        "ad_class": a.get("ad_class"),
        "advertiser": a.get("advertiser"),
        "announces": a.get("announces") or [],
        "contains_excerpt": bool(a.get("contains_excerpt")),
        "excerpt_of": a.get("excerpt_of"),
        "chapters": [{k: c.get(k) for k in ("number", "n", "title", "page")} for c in (a.get("chapters") or [])],
        "flags": a.get("flags") or [],
        "date": meta.get("cover_date"),
        "date_source": "issue",
        "pages": a.get("pages") or [],
        "status": a.get("status", "auto"),
        "verified_by": a.get("verified_by"),
        "modified_by": a.get("modified_by") or [],
        "fragments": [app.fragkey(fr) for fr in a["fragments"]],
        "n_words": len(text.split()),
        "text_sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
        "text": text,
    }
    if rec["type"] == "serial_part":
        rec["type"] = "story"                      # instalments are stories with serial fields since 2026-09-04
        rec["serial"] = rec.get("serial") or {"part_label": None, "part_n": None, "part_total": None, "source": "annotator"}
    rec["assembly"] = a.get("assembly")              # how the automation made it (s13; the corpus issues)
    rec["confidence"] = a.get("confidence")          # the model's lowest confidence on its boxes, when it checked them
    rec["needs_look"] = bool(a.get("needs_look"))    # a change by the model, an open decision, or low confidence
    if rec["type"] in STORY_CORPUS_TYPES and rec["n_words"] >= MIN_STORY_WORDS:
        rec["corpus"] = "story-level"
    else:
        rec["corpus"] = "parallel"
    return rec


def main():
    cfg = app.cfg()
    issues = {i["id"]: i for i in cfg.get("issues", [])}
    n_art = n_story = n_verified = 0
    os.makedirs(EXPORT_DIR, exist_ok=True)
    f_st = open(os.path.join(EXPORT_DIR, "stories.jsonl"), "w", encoding="utf-8")
    f_pt = open(os.path.join(EXPORT_DIR, "paratext.jsonl"), "w", encoding="utf-8")
    n_corpus = {"story-level": 0, "parallel": 0, "story-level words": 0, "parallel words": 0, "story fragments": 0}
    with open(OUT, "w", encoding="utf-8") as f:
        for iid, meta in issues.items():
            doc = app.effective_doc(iid)
            if not doc:
                print(f"[r00] {iid}: no article assembly yet, skipped")
                continue
            for a in doc["articles"]:
                rec = record_of(a, iid, meta)
                # the two corpora the protocol names: the story-level corpus (stories of fifty words or
                # more) and the parallel corpus (advertisements, contents pages, editorial matter, house
                # matter, poems, letters pages — and story records too short to be stories)
                if rec["type"] in STORY_CORPUS_TYPES and rec["n_words"] >= MIN_STORY_WORDS:
                    rec["corpus"] = "story-level"
                else:
                    rec["corpus"] = "parallel"
                    if rec["type"] in STORY_CORPUS_TYPES:
                        n_corpus["story fragments"] += 1
                (f_st if rec["corpus"] == "story-level" else f_pt).write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_corpus[rec["corpus"]] += 1
                n_corpus[rec["corpus"] + " words"] += rec["n_words"]
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_art += 1
                if rec["type"] == "story":
                    n_story += 1
                if rec["status"] == "verified":
                    n_verified += 1
            print(f"[r00] {iid}: {len(doc['articles'])} articles")
    f_st.close()
    f_pt.close()
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M"), "story_level_records": n_corpus["story-level"],
               "story_level_words": n_corpus["story-level words"], "parallel_records": n_corpus["parallel"],
               "parallel_words": n_corpus["parallel words"], "story_fragments_in_parallel": n_corpus["story fragments"],
               "min_story_words": MIN_STORY_WORDS, "files": ["stories.jsonl", "paratext.jsonl"]},
              open(os.path.join(EXPORT_DIR, "corpus_stats.json"), "w", encoding="utf-8"), indent=1)
    print(f"[r00] wrote {OUT}: {n_art} articles, {n_story} stories (instalments included), "
          f"{n_verified} verified — {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"[r00] export/stories.jsonl: {n_corpus['story-level']} records, {n_corpus['story-level words']:,} words; "
          f"export/paratext.jsonl: {n_corpus['parallel']} records, {n_corpus['parallel words']:,} words "
          f"({n_corpus['story fragments']} story records under {MIN_STORY_WORDS} words among them)")


CORPUS_EXPORT = os.path.join(ROOT, "data", "export", "corpus")


def _mt(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def export_corpus(ids=None, force=False, log=print):
    """The corpus issues (config/corpus_issues.json), one file per issue: data/export/corpus/<id>.jsonl, the records of
    its live assembly (s13 publishes it; people's corrections replayed) as export lines. An issue is written again only
    when its live records or its correction log are newer than its file, so a rerun every few minutes costs little
    (pipeline/../scripts/site_refresh.py). The explorer database reads these files with the pilot's. Returns the number
    of issues written."""
    corpus = {i["id"]: i for i in json.load(open(os.path.join(ROOT, "config", "corpus_issues.json"), encoding="utf-8"))["issues"]}
    os.makedirs(CORPUS_EXPORT, exist_ok=True)
    n = 0
    for iid in (ids or sorted(corpus)):
        live = os.path.join(ROOT, "data", "articles", iid, "articles.json")
        if not os.path.exists(live) or iid not in corpus:
            continue
        dst = os.path.join(CORPUS_EXPORT, f"{iid}.jsonl")
        src_mt = max(_mt(live), _mt(os.path.join(ROOT, "data", "annotations", f"{iid}.jsonl")))
        if not force and os.path.exists(dst) and _mt(dst) >= src_mt:
            continue
        doc = app.effective_doc(iid)
        if not doc:
            continue
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for a in doc["articles"]:
                f.write(json.dumps(record_of(a, iid, corpus[iid]), ensure_ascii=False) + "\n")
        os.replace(tmp, dst)
        n += 1
    if n:
        log(f"[r00] corpus: {n} issues exported to data/export/corpus/")
    return n


if __name__ == "__main__":
    if "--corpus" in sys.argv:
        ids = [sys.argv[sys.argv.index("--issue") + 1]] if "--issue" in sys.argv else None
        export_corpus(ids, force="--force" in sys.argv)
    else:
        main()
