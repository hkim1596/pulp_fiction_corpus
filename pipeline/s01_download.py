#!/usr/bin/env python3
"""Stage 1 — download pilot issues from the Internet Archive.

For each approved issue in config/pilot_issues.json:
  data/raw/<id>/meta.json          item metadata (files list, scan info)
  data/raw/<id>/ia_text.txt        the existing IA OCR text (_djvu.txt) = baseline
  data/raw/<id>/ia_hocr.html.gz    positional OCR if present (kept for reference)
  data/raw/<id>/jp2/               original page images (from _jp2.zip), then
  data/pages/<id>/page_0001.png    working page images (max height PAGE_PX)
Gold (when configured): data/gold/<id>/pg_<num>.txt from Project Gutenberg.

Refuses to run while config "approved" is false — the pilot set is the declared
development set of the Registered Report and Heejin approves it explicitly.

Polite by design: sequential, one item at a time, throttle pause between files,
official 'ia' client if available, plain HTTPS otherwise, custom User-Agent with
contact address. Every fetch logged to data/raw/manifest.jsonl.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT

CONFIG = os.path.join(ROOT, "config", "pilot_issues.json")
RAW = os.path.join(ROOT, "data", "raw")
PAGES = os.path.join(ROOT, "data", "pages")
GOLD = os.path.join(ROOT, "data", "gold")
MANIFEST = os.path.join(RAW, "manifest.jsonl")
UA = "pulp_fiction_corpus pilot (research; contact hkim1596@knu.ac.kr)"
THROTTLE_S = 2.0          # pause between file downloads
PAGE_PX = 2200            # working image max height


def log(msg):
    print(f"[s01] {msg}", flush=True)


def manifest(rec):
    os.makedirs(RAW, exist_ok=True)
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(MANIFEST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fetch(url, dest=None, binary=True):
    """Plain HTTPS GET with UA; returns bytes or writes to dest. Retries 3x."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            if dest:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(data)
            manifest({"url": url, "bytes": len(data), "dest": dest})
            time.sleep(THROTTLE_S)
            return data
        except Exception as e:
            log(f"  retry {attempt+1}/3 after error: {e}")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"failed after 3 attempts: {url}")


def item_meta(ident):
    return json.loads(fetch(f"https://archive.org/metadata/{ident}").decode("utf-8"))


def pick_file(files, suffixes):
    """First file whose name ends with any suffix (case-insensitive)."""
    for suf in suffixes:
        for f in files:
            if f.get("name", "").lower().endswith(suf):
                return f["name"]
    return None


def dl_issue(issue):
    ident = issue["ia_identifier"]
    iid = issue["id"]
    out = os.path.join(RAW, iid)
    os.makedirs(out, exist_ok=True)

    meta = item_meta(ident)
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    files = meta.get("files", [])
    server = f"https://archive.org/download/{ident}"

    # 1) existing IA OCR text = the free baseline
    txt = pick_file(files, ["_djvu.txt"])
    if txt:
        fetch(f"{server}/{quote(txt)}", os.path.join(out, "ia_text.txt"))
    else:
        log(f"  {iid}: no _djvu.txt (note for the record)")

    # 2) positional OCR, kept for reference (hocr preferred, chocr fallback)
    hocr = pick_file(files, ["_hocr.html", "_chocr.html.gz", "_hocr.html.gz"])
    if hocr:
        dest = os.path.join(out, "ia_hocr" + (".html.gz" if hocr.endswith(".gz") else ".html"))
        fetch(f"{server}/{quote(hocr)}", dest)

    # 3) page images: prefer the JP2 archive (scan master), fallback to the PDF
    jp2 = pick_file(files, ["_jp2.zip"])
    pdf = pick_file(files, [".pdf"])
    if jp2:
        zpath = os.path.join(out, "jp2.zip")
        fetch(f"{server}/{quote(jp2)}", zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(os.path.join(out, "jp2"))
        os.remove(zpath)  # keep disk + inode use low (server lesson)
        src_kind = "jp2"
    elif pdf:
        fetch(f"{server}/{quote(pdf)}", os.path.join(out, "scan.pdf"))
        src_kind = "pdf"
    else:
        raise RuntimeError(f"{ident}: no jp2.zip and no pdf")

    n_pages = to_working_pages(iid, out, src_kind)
    log(f"  {iid}: {n_pages} working pages from {src_kind}")
    return n_pages


def to_working_pages(iid, out, src_kind):
    """Convert scan masters to data/pages/<id>/page_NNNN.png at PAGE_PX height."""
    pdir = os.path.join(PAGES, iid)
    os.makedirs(pdir, exist_ok=True)
    if src_kind == "pdf":
        # pdftoppm: uniform, fast, no jp2 decoder needed
        subprocess.run(
            ["pdftoppm", "-png", "-r", "150", os.path.join(out, "scan.pdf"),
             os.path.join(pdir, "page")],
            check=True,
        )
        # normalize names to page_NNNN.png
        for i, p in enumerate(sorted(glob.glob(os.path.join(pdir, "page-*.png"))), 1):
            os.rename(p, os.path.join(pdir, f"page_{i:04d}.png"))
        return len(glob.glob(os.path.join(pdir, "page_*.png")))

    # jp2 route: decode with Pillow (needs openjpeg) or opencv; resize to PAGE_PX
    jp2s = sorted(glob.glob(os.path.join(out, "jp2", "**", "*.jp2"), recursive=True))
    if not jp2s:
        raise RuntimeError(f"{iid}: jp2 folder empty")
    try:
        from PIL import Image
        for i, jp in enumerate(jp2s, 1):
            im = Image.open(jp)
            if im.height > PAGE_PX:
                w = round(im.width * PAGE_PX / im.height)
                im = im.resize((w, PAGE_PX))
            im.convert("RGB").save(os.path.join(pdir, f"page_{i:04d}.png"))
    except Exception as e:
        raise RuntimeError(
            f"JP2 decode failed ({e}). Install openjpeg support "
            f"(pip install pillow, apt libopenjp2-7) or rerun with the PDF route."
        )
    shutil.rmtree(os.path.join(out, "jp2"))  # masters re-fetchable; save inodes
    return len(jp2s)


def dl_gold(issue):
    """Project Gutenberg proofread text for gold issues (plain-text format)."""
    gold = issue.get("gold")
    if not gold:
        return
    nums = gold.get("pg_ebooks") or [gold.get("pg_ebook")]
    gdir = os.path.join(GOLD, issue["id"])
    for num in [n for n in nums if n]:
        # standard PG plain-text locations, tried in order
        for url in (
            f"https://www.gutenberg.org/cache/epub/{num}/pg{num}.txt",
            f"https://www.gutenberg.org/files/{num}/{num}-0.txt",
            f"https://www.gutenberg.org/files/{num}/{num}.txt",
        ):
            try:
                fetch(url, os.path.join(gdir, f"pg_{num}.txt"))
                break
            except RuntimeError:
                continue
        else:
            log(f"  gold pg#{num}: not fetched — resolve URL by hand")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="download every issue in config")
    ap.add_argument("--issue", help="download one issue by id")
    ap.add_argument("--gold-only", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG, encoding="utf-8"))
    if not cfg.get("approved"):
        sys.exit(
            "REFUSING TO DOWNLOAD: config/pilot_issues.json has approved=false.\n"
            "Heejin reviews the 10-issue list and sets approved=true first\n"
            "(this is the Registered Report development-set audit trail)."
        )

    issues = cfg["issues"]
    if args.issue:
        issues = [i for i in issues if i["id"] == args.issue]
        if not issues:
            sys.exit(f"no issue with id {args.issue}")
    elif not args.all:
        sys.exit("pass --all or --issue <id>")

    for issue in issues:
        log(f"issue {issue['id']} ({issue['ia_identifier']})")
        if not args.gold_only:
            with stage_timer("s01_download", issue["id"]):
                n = dl_issue(issue)
            manifest({"issue": issue["id"], "pages": n, "event": "issue_done"})
        with stage_timer("s01_gold", issue["id"]):
            dl_gold(issue)
    log("done")


if __name__ == "__main__":
    main()
