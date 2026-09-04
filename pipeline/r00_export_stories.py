#!/usr/bin/env python3
"""r00 — export the pilot's article records as one JSONL file.

The text-reuse pipeline (r01…) works on STORIES, not pages. This script
asks the website's own replay engine (webapp/app.py: machine assembly
plus every human correction, replayed in order) for the current state of
every article in every pilot issue, and writes one JSON line per
article. It must run on a machine that holds the data/ tree (the main
server, or the Studio while it is the live server):

    cd <project folder> && python3 pipeline/r00_export_stories.py

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


def main():
    cfg = app.cfg()
    issues = {i["id"]: i for i in cfg.get("issues", [])}
    n_art = n_story = n_verified = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for iid, meta in issues.items():
            doc = app.effective_doc(iid)
            if not doc:
                print(f"[r00] {iid}: no article assembly yet, skipped")
                continue
            for a in doc["articles"]:
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
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_art += 1
                if rec["type"] == "story":
                    n_story += 1
                if rec["status"] == "verified":
                    n_verified += 1
            print(f"[r00] {iid}: {len(doc['articles'])} articles")
    print(f"[r00] wrote {OUT}: {n_art} articles, {n_story} stories (instalments included), "
          f"{n_verified} verified — {time.strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
