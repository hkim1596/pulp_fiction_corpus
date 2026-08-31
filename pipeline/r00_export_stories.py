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
with its metadata and reading text. Later stages select by type. Nothing
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
                    "pages": a.get("pages") or [],
                    "status": a.get("status", "auto"),
                    "verified_by": a.get("verified_by"),
                    "modified_by": a.get("modified_by") or [],
                    "fragments": [app.fragkey(fr) for fr in a["fragments"]],
                    "n_words": len(text.split()),
                    "text_sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
                    "text": text,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_art += 1
                if rec["type"] in ("story", "serial_part"):
                    n_story += 1
                if rec["status"] == "verified":
                    n_verified += 1
            print(f"[r00] {iid}: {len(doc['articles'])} articles")
    print(f"[r00] wrote {OUT}: {n_art} articles, {n_story} stories/serial "
          f"parts, {n_verified} verified — {time.strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
