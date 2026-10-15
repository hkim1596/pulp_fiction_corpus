#!/usr/bin/env python3
"""The issues the corpus run could not download (p50t; Heejin's decision of 14 October 2026: "Fetch the real scans,
set the rest aside"): what each archive item holds, which can be fetched with the downloader's new file formats
(s01c: Comic Book ZIP/RAR, a raw zip of page images, loose page images), and which are set aside with a reason.

    python3 scripts/missing_issues.py --events data/corpus/events.jsonl --list config/corpus_issues.json \\
        --cache pilot_export/missing-issues-2026-10-14/archive_metadata.json \\
        --out config/corpus_set_aside.json --report pilot_export/missing-issues-2026-10-14/missing_issues.md

Reads: the run's events (the last download failure of every issue that never downloaded), the issue list (the
archive identifier of each), and the archive's item record of each (https://archive.org/metadata/<identifier>,
fetched once and cached; one request every 0.4 s with a contact address). Writes the set-aside file that
corpus_lib.set_aside() reads (the run skips those issues; the datasheet counts them) and a plain-English report.
Nothing on the server is changed by this script itself. Safe to run again: the cache is reused, the files rewritten.

The classes (the rule, from the item's file list):
    retry                   the download failed for a passing reason (HTTP 500, a timeout): try again, nothing else
    fetch: cbz/cbr          a Comic Book ZIP or RAR of page images
    fetch: raw zip          a zip of page images (the archive's "Generic Raw Book Zip", or a plain zip)
    fetch: loose pages      20 or more page images as separate files, numbered in sequence (fewer, or images without
                            page numbers in their names, are a cover or illustrations)
    set aside: not a scan   a cover image, illustrations, a checklist, an ebook: no scanned issue in the item
    set aside: bound volume 600 or more page images under one item (several issues; its dates and contents would be wrong)
    set aside: no images    text-only or DjVu-only items (no page images the pipeline reads)
    set aside: restricted   HTTP 401/403: the archive restricts the item (a lending book, not a magazine)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))

IMAGE_FORMATS = ("JPEG", "PNG", "TIFF", "JPEG 2000")
BOUND_VOLUME_PAGES = 600
USER_AGENT = "pulp-fiction-corpus (Digital Humanities Engineering Center, KNU; hkim1596@gmail.com)"


def never_downloaded(events_path):
    """issue id -> the last failure message, for the issues whose download never succeeded."""
    fails, done = {}, set()
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "failed" and e.get("stage") == "downloaded":
                fails[e["issue"]] = e.get("error") or ""
            if (e.get("status") == "done" or e.get("event") == "done") and e.get("stage") == "downloaded":
                done.add(e.get("issue"))
    return {iid: msg for iid, msg in fails.items() if iid not in done}


def fetch_metadata(ident, cache, sleep=0.4):
    if ident in cache and "files" in cache[ident]:
        return cache[ident]
    req = urllib.request.Request(f"https://archive.org/metadata/{ident}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.load(r)
        cache[ident] = {"files": [{"name": f.get("name"), "format": f.get("format"), "size": int(f.get("size") or 0), "source": f.get("source")}
                                  for f in d.get("files", [])],
                        "is_dark": d.get("is_dark"), "mediatype": (d.get("metadata") or {}).get("mediatype"),
                        "title": (d.get("metadata") or {}).get("title"), "fetched": time.strftime("%Y-%m-%dT%H:%M:%S")}
    except Exception as e:                                   # noqa: BLE001 — recorded, classified as "unknown"
        cache[ident] = {"error": repr(e)[:200], "fetched": time.strftime("%Y-%m-%dT%H:%M:%S")}
    time.sleep(sleep)
    return cache[ident]


def numbered_sequence(names):
    """True when the image names carry page numbers that run in sequence (xxx_0001.jpg, xxx_0002.jpg …): a scan of
    an issue; a set of illustrations or covers is named by title. At least four of five names must end in a number and
    the numbers must fill their range to four fifths."""
    nums = []
    for n in names:
        m = re.search(r"(\d+)\D*$", n)
        if m:
            nums.append(int(m.group(1)))
    if len(nums) < 0.8 * len(names) or not nums:
        return False
    span = max(nums) - min(nums) + 1
    return len(set(nums)) >= 0.8 * span


def classify(msg, meta):
    """(class, reason) for one issue from its failure message and the archive's item record."""
    m = msg or ""
    if "401" in m or "403" in m:
        return "set aside: restricted", "the archive restricts the item (HTTP 401/403); a lending book, not a magazine scan"
    if "no _jp2.zip" not in m:
        return "retry", f"a passing failure ({m[:60]}); the download is tried again"
    if not meta or "error" not in meta and not meta.get("files"):
        return "set aside: no images", "the item record is empty (dark or removed)"
    if "error" in meta:
        return "unknown", f"the item record could not be read ({meta['error'][:60]}); look again"
    files = meta["files"]
    fmts = Counter(f.get("format") for f in files)
    names = [str(f.get("name") or "").lower() for f in files]
    img_names = [str(f.get("name") or "") for f in files if f.get("format") in IMAGE_FORMATS and f.get("source") != "derivative"]
    n_img = len(img_names)
    if any(k in fmts for k in ("Comic Book ZIP", "Comic Book RAR")) or any(n.endswith((".cbz", ".cbr")) for n in names):
        return "fetch: cbz/cbr", "a Comic Book ZIP/RAR of page images"
    if "Generic Raw Book Zip" in fmts:
        return "fetch: raw zip", "a zip of page images"
    if n_img >= BOUND_VOLUME_PAGES:
        return "set aside: bound volume", f"a bound volume of several issues ({n_img} page images under one item)"
    if n_img >= 20 and numbered_sequence(img_names):
        return "fetch: loose pages", f"{n_img} loose page images, numbered in sequence"
    if n_img >= 20:
        return "set aside: not a scan", f"{n_img} images without page numbers in their names: illustrations or covers, not a scanned issue"
    if "ZIP" in fmts or "RAR" in fmts:
        if any(("rtf" in n or "txt" in n or "epub" in n) and n.endswith((".zip", ".rar")) for n in names):
            return "set aside: no images", "a zip/RAR of text, not of page images"
        return "fetch: raw zip", "a zip or RAR of unknown content; tried as page images"
    if any(k in fmts for k in ("DjVu",)):
        return "set aside: no images", "DjVu only: no page images the pipeline reads"
    if n_img >= 1:
        return "set aside: not a scan", f"{n_img} image(s) only: a cover or illustrations, not a scanned issue"
    return "set aside: not a scan", "no page images at all: " + ", ".join(sorted(k for k in fmts if k))[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--list", required=True, help="the issue list (config/corpus_issues.json or its archived copy)")
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True, help="config/corpus_set_aside.json")
    ap.add_argument("--report", required=True)
    ap.add_argument("--no-network", action="store_true", help="classify from the cache only")
    args = ap.parse_args()
    lst = json.load(open(args.list, encoding="utf-8"))
    ids = {i["id"]: i for i in lst["issues"]}
    missing = never_downloaded(args.events)
    cache = json.load(open(args.cache, encoding="utf-8")) if os.path.exists(args.cache) else {}
    rows = []
    for iid, msg in sorted(missing.items()):
        ident = (ids.get(iid) or {}).get("ia_identifier")
        if not ident:
            m = re.match(r"RuntimeError: (\S+): no _jp2.zip", msg)
            ident = m.group(1) if m else None
        meta = None
        if ident and "no _jp2.zip" in msg:
            meta = cache.get(ident) if args.no_network else fetch_metadata(ident, cache)
        cls, reason = classify(msg, meta)
        rows.append({"id": iid, "ia_identifier": ident, "class": cls, "reason": reason, "failure": msg[:120],
                     "title": (meta or {}).get("title"), "n_files": len((meta or {}).get("files") or []),
                     "mb": round(sum(f.get("size") or 0 for f in (meta or {}).get("files") or []) / 1e6, 1)})
    os.makedirs(os.path.dirname(args.cache) or ".", exist_ok=True)
    json.dump(cache, open(args.cache, "w", encoding="utf-8"), indent=1)
    aside = [r for r in rows if r["class"].startswith("set aside")]
    out = {"_comment": "Issues of the approved list set aside after the download (p50t; Heejin, 14 October 2026: 'Fetch the real scans, "
                       "set the rest aside'). They stay in the approved list (its fingerprint is untouched); corpus_lib.set_aside() keeps "
                       "every stage off them; the datasheet counts them by reason. Made by scripts/missing_issues.py from the run's events "
                       "and the archive's item records.",
           "decided_by": "Heejin Kim", "decided": "2026-10-14", "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "issues": [{"id": r["id"], "ia_identifier": r["ia_identifier"], "class": r["class"], "reason": r["reason"]} for r in aside]}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump(out, open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    c = Counter(r["class"] for r in rows)
    lines = [f"# The {len(rows)} issues the run could not download — {time.strftime('%Y-%m-%d %H:%M')}", "",
             "From the run's events and the archive's item records (scripts/missing_issues.py). Heejin's decision of 14 October:",
             "the real scans are fetched with the downloader's new file formats, the rest is set aside with a reason.", "",
             "| class | issues |", "|---|---|"] + [f"| {k} | {n} |" for k, n in c.most_common()] + ["", "| issue | class | reason | title | files | MB |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['class']} | {r['reason']} | {(r['title'] or '')[:50]} | {r['n_files']} | {r['mb']} |")
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    open(args.report, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"{len(rows)} issues never downloaded: " + ", ".join(f"{k} {n}" for k, n in c.most_common()))
    print(f"set aside: {len(aside)} -> {args.out}; to fetch or retry: {len(rows) - len(aside)}; report: {args.report}")
    fetch_ids = [r["id"] for r in rows if not r["class"].startswith("set aside")]
    print("ids to retry/fetch:", ",".join(fetch_ids))


if __name__ == "__main__":
    main()
