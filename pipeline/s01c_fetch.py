#!/usr/bin/env python3
"""s01c — the corpus downloader and imager (Phase 1 of corpus-build-plan-2026-10-04).

For every issue in config/corpus_issues.json (approved=true required; see corpus_lib.require_approved):

  data/raw/<id>/meta.json            the archive's item record (file list with sizes and md5s, scan info)
  data/raw/<id>/ia_text.txt          the archive's own OCR text (_djvu.txt), the free baseline
  data/raw/<id>/ia_djvu.xml          the archive's positional OCR with page divisions (_djvu.xml)
  data/raw/<id>/scandata.xml         per-leaf scan record (page type, which leaves the access files include, ppi)
  data/masters/<id>/<ident>_jp2.zip  the scan master, kept zipped (one file per issue, no inode flood);
                                     fallback <ident>.pdf when the item has no JP2 archive; since p50t (14 October
                                     2026) also a Comic Book ZIP/RAR, a raw zip of page images, or <ident>_pages.zip
                                     made here from the item's loose page images (20 or more)
  data/pages/<id>/page_NNNN.jpg      working page images, 2,200 px high, JPEG quality 90  (imaging step)
  data/thumbs/<id>/page_NNNN.jpg     300 px thumbnails for the site                        (imaging step)

What makes it faster than the pilot's s01 (170 s per issue): four parallel items (the archive's own guidance for
bots: "limit to 4 concurrent downloads with 1 second delay"), the JP2 archive only (a third of an item's bytes;
no PDF, no hOCR), no PNG conversion (JPEG working images, decoded at half resolution when the master is large),
and the imaging step runs in its own processes while the next items download.

Politeness and safety: descriptive User-Agent with a contact address, 1 s pause between files and 2 s between
items per worker, 429/503 honoured with the server's Retry-After, six retries with growing waits, resumable
downloads (HTTP Range into .part files), md5 verified against the item record before a file counts as fetched,
the archive's login cookies sent when `ia configure` has been run (~/.config/internetarchive/ia.ini).
A file named data/corpus/STOP makes every worker finish its current file and exit.

State: data/corpus/state/<id>.json via corpus_lib.mark ("downloaded", "imaged"); every fetched file and every
issue event is one line in data/corpus/fetch_manifest.jsonl.

    python3 pipeline/s01c_fetch.py --run                 # download + image everything not yet done (4 workers)
    python3 pipeline/s01c_fetch.py --run --limit 20      # the first 20 pending issues (a trial run)
    python3 pipeline/s01c_fetch.py --issue <id>          # one issue of the list
    python3 pipeline/s01c_fetch.py --image-only          # only the imaging step for downloaded issues
    python3 pipeline/s01c_fetch.py --smoke <ia_identifier>   # one arbitrary item into data/corpus/smoke/ (a test
                                                             of the machinery, no approval needed, not corpus data)
"""
import argparse
import configparser
import glob
import hashlib
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import (ROOT, settings, corpus_config, require_approved, mark, has, log, issue_dirs,  # noqa: E402
                        write_json_atomic, load_state, partition_issue_ids)
from timing_util import stage_timer  # noqa: E402

CORPUS_DIR = os.path.join(ROOT, "data", "corpus")
MANIFEST = os.path.join(CORPUS_DIR, "fetch_manifest.jsonl")
STOP_FILE = os.path.join(CORPUS_DIR, "STOP")
_manifest_lock = threading.Lock()


class Stop(Exception):
    pass


def stop_requested():
    return os.path.exists(STOP_FILE)


def manifest(rec):
    os.makedirs(CORPUS_DIR, exist_ok=True)
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _manifest_lock, open(MANIFEST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------------------------------------------
def ia_cookie_header():
    """The archive's login cookies from `ia configure`, when present (authenticated clients are treated better)."""
    for p in (os.path.expanduser("~/.config/internetarchive/ia.ini"), os.path.expanduser("~/.ia")):
        if os.path.exists(p):
            cp = configparser.ConfigParser()
            try:
                cp.read(p)
                u, sig = cp.get("cookies", "logged-in-user", fallback=None), cp.get("cookies", "logged-in-sig", fallback=None)
                if u and sig:
                    return f"logged-in-user={u}; logged-in-sig={sig}"
            except Exception:
                pass
    return None


_COOKIE = ia_cookie_header()


def headers(extra=None):
    h = {"User-Agent": settings()["download"]["user_agent"]}
    if _COOKIE:
        h["Cookie"] = _COOKIE
    if extra:
        h.update(extra)
    return h


def retry_wait(attempt, err):
    """How long to wait before the next try: the server's Retry-After when it sent one, else the settings list."""
    dl = settings()["download"]
    if dl.get("honor_retry_after") and isinstance(err, urllib.error.HTTPError):
        ra = err.headers.get("Retry-After") if err.headers else None
        if ra and ra.strip().isdigit():
            return max(int(ra), 5)
    waits = dl["retry_backoff_s"]
    return waits[min(attempt, len(waits) - 1)]


def get_bytes(url, timeout=120):
    """A small document (metadata, text): whole body in memory, with retries."""
    dl = settings()["download"]
    for attempt in range(dl["retry_max"] + 1):
        if stop_requested():
            raise Stop()
        try:
            req = urllib.request.Request(url, headers=headers())
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if attempt >= dl["retry_max"]:
                raise
            w = retry_wait(attempt, e)
            log("s01c", f"HTTP {e.code} on {url} — waiting {w}s (try {attempt + 1})")
            time.sleep(w)
        except Exception as e:
            if attempt >= dl["retry_max"]:
                raise
            w = retry_wait(attempt, e)
            log("s01c", f"{type(e).__name__}: {e} on {url} — waiting {w}s (try {attempt + 1})")
            time.sleep(w)


def download_file(url, dest, expect_size=None, expect_md5=None, max_tries=None):
    """Stream to dest.part, resuming with Range when a part exists; verify size/md5; rename to dest.
    Waits between tries follow settings.download.retry_backoff_s, capped at retry_wait_cap_s (300 s: a file the
    archive keeps refusing must not hold a worker for half an hour); a 404 is final at once."""
    dl = settings()["download"]
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tries = (max_tries or dl["retry_max"] + 1)
    for attempt in range(tries):
        if stop_requested():
            raise Stop()
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if expect_size and have > expect_size:
            os.remove(part); have = 0
        try:
            extra = {"Range": f"bytes={have}-"} if have else None
            req = urllib.request.Request(url, headers=headers(extra))
            with urllib.request.urlopen(req, timeout=180) as r:
                if have and r.status != 206:            # server ignored the range: start over
                    have = 0
                mode = "ab" if have else "wb"
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        if stop_requested():
                            raise Stop()
            size = os.path.getsize(part)
            if expect_size and size != expect_size:
                raise IOError(f"size {size} != expected {expect_size}")
            if expect_md5 and dl.get("verify_md5"):
                h = hashlib.md5()
                with open(part, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != expect_md5:
                    os.remove(part)
                    raise IOError("md5 mismatch (file removed, will refetch)")
            os.replace(part, dest)
            return size
        except Stop:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if e.code == 416:                           # range not satisfiable: the part is bad
                if os.path.exists(part):
                    os.remove(part)
            if attempt >= tries - 1:
                raise
            w = min(retry_wait(attempt, e), dl.get("retry_wait_cap_s", 300))
            log("s01c", f"HTTP {e.code} on {url} — waiting {w}s (try {attempt + 1} of {tries})")
            time.sleep(w)
        except Exception as e:
            if attempt >= tries - 1:
                raise
            w = min(retry_wait(attempt, e), dl.get("retry_wait_cap_s", 300))
            log("s01c", f"{type(e).__name__}: {e} on {url} — waiting {w}s (try {attempt + 1} of {tries})")
            time.sleep(w)


# ----------------------------------------------------------------------------------------------------------------
# one item
# ----------------------------------------------------------------------------------------------------------------
def pick(files, test):
    for f in files:
        if test(f.get("name", "")):
            return f
    return None


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".jp2")
IMAGE_FORMATS = ("JPEG", "PNG", "TIFF", "JPEG 2000")
MIN_LOOSE_PAGES = 20            # fewer loose images than this is a cover or an illustration, not a scanned issue (p50t)


def page_sequence(files):
    """The largest group of numbered image files in one folder of the item, as (files sorted by number, the number of
    distinct numbers, the span of the numbers), or (None, 0, 0). The archive's derivatives (JPEG Thumb, Item Tile,
    _thumb) are dropped first; ties between folders go to the larger total size (pages over thumbnails)."""
    groups = {}
    for f in files:
        name, fmt = f.get("name", ""), f.get("format", "")
        low = name.lower()
        if fmt not in IMAGE_FORMATS and not low.endswith(IMAGE_EXT):
            continue
        if fmt in ("JPEG Thumb", "Item Tile") or "_thumb" in low or "__ia_thumb" in low or f.get("source") == "derivative":
            continue
        folder, _, base = name.rpartition("/")
        m = re.search(r"(\d+)\D*$", base)
        if not m:
            continue
        groups.setdefault(folder, []).append((int(m.group(1)), f))
    if not groups:
        return None, 0, 0
    best = max(groups.values(), key=lambda g: (len(g), sum(int(x[1].get("size") or 0) for x in g)))
    nums = sorted({n for n, _ in best})
    best.sort(key=lambda t: (t[0], _natkey(t[1].get("name", ""))))
    return [f for _, f in best], len(nums), nums[-1] - nums[0] + 1 if nums else 0


def page_images(files):
    """The item's loose page images (one file per page), in page order, or [] when the item has no page sequence.
    The pages of a scan are numbered files in one folder (001.jpg … 118.jpg; Final/files/mobile/1.jpg … 100.jpg in a
    flip-book export); thumbnails, icons and the archive's own derivatives sit beside them, and an illustration set is
    named by title. So the largest numbered group of one folder (page_sequence) is the scan when it holds at least
    MIN_LOOSE_PAGES distinct numbers that fill their range to four fifths (an incomplete scan, or dates and catalogue
    numbers, do not) and are not all years (covers named by date)."""
    seq, n_distinct, span = page_sequence(files)
    if not seq or n_distinct < MIN_LOOSE_PAGES or n_distinct < 0.8 * span:
        return []
    nums = sorted({int(re.search(r"(\d+)\D*$", f.get("name", "").rpartition("/")[2]).group(1)) for f in seq})
    if all(1800 <= n <= 2030 for n in nums):
        return []
    return seq


def comic_archives(files):
    """The item's comic-book archives (.cbz/.cbr), grouped by stem: several stems mean several issues in one item."""
    arch = [f for f in files if f.get("format") in ("Comic Book ZIP", "Comic Book RAR") or f.get("name", "").lower().endswith((".cbz", ".cbr"))]
    stems = {}
    for f in arch:
        stems.setdefault(os.path.splitext(os.path.basename(f.get("name", "")))[0].lower(), []).append(f)
    return stems


def _natkey(name):
    """A sort key that puts page_2 before page_10 (numbers compared as numbers)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def choose_files(meta):
    """Which of the item's files we take, by role. The scan master, in order of preference: the archive's JP2 zip;
    else its PDF; else (p50t, 14 October 2026, for the 134 issues the run could not get) a Comic Book ZIP/RAR of page
    images, a raw zip of page images, or the loose page images themselves (zipped into one master here)."""
    files = meta.get("files", [])
    low = lambda n: n.lower()
    out = {
        "djvu_txt": pick(files, lambda n: low(n).endswith("_djvu.txt")),
        "djvu_xml": pick(files, lambda n: low(n).endswith("_djvu.xml")),
        "scandata": pick(files, lambda n: low(n).endswith("_scandata.xml") or low(n) == "scandata.xml"),
        "jp2_zip": pick(files, lambda n: low(n).endswith("_jp2.zip") and not re.search(r"_(raw|orig)_jp2\.zip$", low(n))),
        "pdf": None, "cbz": None, "cbr": None, "raw_zip": None, "images": [],
    }
    if not out["jp2_zip"]:
        out["pdf"] = pick(files, lambda n: low(n).endswith(".pdf") and "_text" not in low(n)) or pick(files, lambda n: low(n).endswith(".pdf"))
    if not out["jp2_zip"] and not out["pdf"]:
        out["cbz"] = pick(files, lambda n: low(n).endswith(".cbz")) or next((f for f in files if f.get("format") == "Comic Book ZIP"), None)
        out["cbr"] = pick(files, lambda n: low(n).endswith(".cbr")) or next((f for f in files if f.get("format") == "Comic Book RAR"), None)
        out["raw_zip"] = next((f for f in files if f.get("format") in ("Generic Raw Book Zip",) or
                               (low(f.get("name", "")).endswith(".zip") and f.get("format") == "ZIP")), None)
        out["images"] = page_images(files)
    return out


def master_choice(chosen):
    """(source kind, the file record or the list of image files) for the scan master, or (None, None)."""
    for kind in ("jp2", "pdf", "cbz", "cbr", "raw_zip"):
        key = "jp2_zip" if kind == "jp2" else kind
        if chosen.get(key):
            return kind, chosen[key]
    if chosen.get("images"):
        return "images", chosen["images"]
    return None, None


def fetch_issue(issue, root=None):
    """Download one issue's files. Returns the state record written. Safe to call again: finished files are kept."""
    root = root or ROOT
    iid, ident = issue["id"], issue["ia_identifier"]
    dl = settings()["download"]
    paths = issue_dirs(iid)
    raw = os.path.join(root, "data", "raw", iid) if root != ROOT else paths["raw"]
    master_dir = os.path.join(root, "data", "masters", iid) if root != ROOT else paths["master"]
    os.makedirs(raw, exist_ok=True)
    t0 = time.time()
    meta_path = os.path.join(raw, "meta.json")
    if root == ROOT:
        prior = load_state(iid).get("stages", {}).get("downloaded", {}).get("ia_identifier")
        if prior and prior != ident:
            raise RuntimeError(f"{iid}: cached download belongs to {prior}, requested {ident}; source audit required")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as metadata_file:
            meta = json.load(metadata_file)
        cached_ident = (meta.get("metadata") or {}).get("identifier")
        if cached_ident != ident:
            raise RuntimeError(f"{iid}: cached metadata belongs to {cached_ident!r}, requested {ident}; source audit required")
    else:
        payloads = [os.path.join(raw, name) for name in ("ia_text.txt", "ia_djvu.xml", "scandata.xml")]
        if any(os.path.exists(path) for path in payloads) or (os.path.isdir(master_dir) and os.listdir(master_dir)):
            raise RuntimeError(f"{iid}: cached files have no source metadata; source audit required before reuse")
        meta = json.loads(get_bytes(f"https://archive.org/metadata/{ident}").decode("utf-8"))
        if (meta.get("metadata") or {}).get("identifier") != ident:
            raise RuntimeError(f"{iid}: archive metadata does not identify the requested source {ident}")
        if not meta.get("files"):
            raise RuntimeError(f"{ident}: empty item record (dark or removed?)")
        write_json_atomic(meta_path, meta)
        manifest({"issue": iid, "ident": ident, "file": "metadata", "bytes": os.path.getsize(meta_path)})
        time.sleep(dl["delay_between_files_s"])
    chosen = choose_files(meta)
    server = f"https://archive.org/download/{ident}"
    got, missing = {}, []
    dests = {"djvu_txt": os.path.join(raw, "ia_text.txt"), "djvu_xml": os.path.join(raw, "ia_djvu.xml"),
             "scandata": os.path.join(raw, "scandata.xml")}
    for role in ("djvu_txt", "djvu_xml", "scandata"):
        f = chosen[role]
        if not f:
            missing.append(role); continue
        dest = dests[role]
        if os.path.exists(dest):
            got[role] = {"name": f["name"], "bytes": os.path.getsize(dest), "md5": f.get("md5")}; continue
        try:
            # the archive's text files are wanted, not essential: three tries, then the issue goes on without them
            # (4 October: a worker sat 38 minutes on one scandata.xml the archive answered with HTTP 500 every time)
            n = download_file(f"{server}/{quote(f['name'])}", dest, int(f.get("size") or 0) or None, f.get("md5"), max_tries=3)
        except Stop:
            raise
        except Exception as e:
            missing.append(f"{role} ({str(e)[:80]})")
            manifest({"issue": iid, "ident": ident, "file": role, "name": f["name"], "missing": str(e)[:200]})
            continue
        got[role] = {"name": f["name"], "bytes": n, "md5": f.get("md5")}
        manifest({"issue": iid, "ident": ident, "file": role, "name": f["name"], "bytes": n, "md5": f.get("md5")})
        time.sleep(dl["delay_between_files_s"])
    src, f = master_choice(chosen)
    if not src:
        raise RuntimeError(f"{ident}: no _jp2.zip, no pdf, no comic-book archive, no zip and fewer than {MIN_LOOSE_PAGES} loose page images in the item")
    leaves = None
    if src == "images":
        # loose page images: fetched one by one into a folder, then zipped into one master (one file per issue, as
        # the other sources; the page order is the natural order of the file names)
        dest = os.path.join(master_dir, f"{ident}_pages.zip")
        if os.path.exists(dest):
            got["master"] = {"name": os.path.basename(dest), "bytes": os.path.getsize(dest), "md5": None}
        else:
            tmp = os.path.join(master_dir, "_pages")
            os.makedirs(tmp, exist_ok=True)
            total = 0
            for k, pf in enumerate(f, 1):
                pdest = os.path.join(tmp, f"{k:04d}_{os.path.basename(pf['name'])}")
                if not os.path.exists(pdest):
                    total += download_file(f"{server}/{quote(pf['name'])}", pdest, int(pf.get("size") or 0) or None, pf.get("md5"))
                    time.sleep(dl["delay_between_files_s"])
                else:
                    total += os.path.getsize(pdest)
            part = dest + ".part"
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as z:
                for name in sorted(os.listdir(tmp)):
                    z.write(os.path.join(tmp, name), name)
            os.replace(part, dest)
            shutil.rmtree(tmp, ignore_errors=True)
            got["master"] = {"name": os.path.basename(dest), "bytes": os.path.getsize(dest), "md5": None, "images": len(f)}
            manifest({"issue": iid, "ident": ident, "file": "master", "name": os.path.basename(dest), "bytes": total, "src": src, "images": len(f)})
        leaves = len(f)
    else:
        dest = os.path.join(master_dir, os.path.basename(f["name"]))
        if os.path.exists(dest):
            got["master"] = {"name": f["name"], "bytes": os.path.getsize(dest), "md5": f.get("md5")}
        else:
            n = download_file(f"{server}/{quote(f['name'])}", dest, int(f.get("size") or 0) or None, f.get("md5"))
            got["master"] = {"name": f["name"], "bytes": n, "md5": f.get("md5")}
            manifest({"issue": iid, "ident": ident, "file": "master", "name": f["name"], "bytes": n, "md5": f.get("md5"), "src": src})
        if src == "jp2":
            with zipfile.ZipFile(dest) as z:
                leaves = sum(1 for nm in z.namelist() if nm.lower().endswith(".jp2"))
        elif src in ("cbz", "raw_zip") or (src == "cbr" and archive_kind(dest) == "zip"):
            kind = archive_kind(dest)
            if kind == "zip":
                with zipfile.ZipFile(dest) as z:
                    leaves = sum(1 for nm in z.namelist() if nm.lower().endswith(IMAGE_EXT) and not nm.endswith("/"))
                if not leaves:
                    raise RuntimeError(f"{ident}: the zip {f['name']} holds no page images")
            elif kind in ("rar", "7z"):
                leaves = None                               # a .cbz that is in fact a RAR (17 October): counted at imaging
            else:
                raise RuntimeError(f"{ident}: {f['name']} is neither a zip, a RAR nor a 7z archive")
    # provenance for the record: the archive's own dates for the item (upload and last change), from the item record
    md = meta.get("metadata") or {}
    rec = {"src": src, "master": os.path.relpath(dest, root), "bytes": sum(v["bytes"] for v in got.values()), "files": got,
           "missing": missing, "leaves": leaves, "seconds": round(time.time() - t0, 1), "ia_identifier": ident,
           "ia_publicdate": md.get("publicdate"), "ia_addeddate": md.get("addeddate"), "ia_updated": meta.get("item_last_updated"),
           "ia_ocr": md.get("ocr")}
    manifest({"issue": iid, "ident": ident, "event": "issue_downloaded", **rec})
    if root == ROOT:
        mark(iid, "downloaded", **rec)
    time.sleep(dl["delay_between_items_s"])
    return rec


# ----------------------------------------------------------------------------------------------------------------
# imaging
# ----------------------------------------------------------------------------------------------------------------
def decode_jp2(data, working_h):
    """Decode one JP2 leaf with Pillow: first at half resolution when the leaf is large (a quarter of the time),
    then at full resolution if that fails; None when neither works."""
    from PIL import Image, ImageFile
    for reduce, tolerant in ((True, False), (False, False), (False, True)):
        try:
            ImageFile.LOAD_TRUNCATED_IMAGES = tolerant        # third try: a partly decoded page beats a blank one
            im = Image.open(io.BytesIO(data))
            if reduce:
                if im.height < 2 * working_h:
                    continue                                 # nothing to gain; the full decode follows
                im.reduce = 1                                # Pillow's JPEG 2000 plugin reads this at load()
            im.load()
            return im
        except Exception:
            continue
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = False
    return None


def _save_working(im, out_path, thumb_path, img):
    """One decoded page -> working JPEG at the working height + thumbnail."""
    from PIL import Image
    h = img["working_height_px"]
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if im.height > h:                      # never upscale: a small master stays at its own size
        w = max(1, round(im.width * h / im.height))
        im = im.resize((w, h), Image.LANCZOS)
    im.save(out_path, "JPEG", quality=img["jpeg_quality"], optimize=True)
    th = img["thumb_height_px"]
    tw = max(1, round(im.width * th / im.height))
    im.resize((tw, th), Image.LANCZOS).save(thumb_path, "JPEG", quality=img["thumb_quality"])
    return im.width


def image_issue(iid, root=None):
    """Master -> data/pages/<id>/page_NNNN.jpg (+ thumbs). Returns {pages, master_px, ppi}. Runs in a worker process."""
    from PIL import Image
    root = root or ROOT
    img = settings()["images"]
    paths = issue_dirs(iid)
    if root != ROOT:
        paths = {k: os.path.join(root, os.path.relpath(v, ROOT)) for k, v in paths.items()}
    pages_dir, thumbs_dir, master_dir = paths["pages"], paths["thumbs"], paths["master"]
    os.makedirs(pages_dir, exist_ok=True); os.makedirs(thumbs_dir, exist_ok=True)
    masters = sorted(glob.glob(os.path.join(master_dir, "*")))
    masters = [m for m in masters if not m.endswith(".part")]
    if not masters:
        raise RuntimeError(f"{iid}: no master file in {master_dir}")
    master = masters[0]
    t0 = time.time()
    sizes = []
    bad_leaves = []
    n = 0
    low = master.lower()
    if low.endswith(".zip") and not _zip_has_jp2(master):            # p50t: a cbz-like zip, a raw zip, or our zip of loose images
        n, sizes = _image_from_archive(master, "zip", pages_dir, thumbs_dir, img)
    elif low.endswith((".cbz", ".cbr", ".rar")):
        # by the file's first bytes, not its name: the archive holds .cbr files that are zips and .cbz files that are
        # RARs (17 October 2026: eight "no tool could unpack the RAR" and four "File is not a zip file")
        n, sizes = _image_from_archive(master, "zip" if archive_kind(master) == "zip" else "rar", pages_dir, thumbs_dir, img)
    elif low.endswith(".zip"):
        with zipfile.ZipFile(master) as z:
            names = sorted(nm for nm in z.namelist() if nm.lower().endswith(".jp2"))
            for i, nm in enumerate(names, 1):
                out = os.path.join(pages_dir, f"page_{i:04d}.jpg")
                if os.path.exists(out) and os.path.exists(os.path.join(thumbs_dir, f"page_{i:04d}.jpg")):
                    n += 1; continue
                data = z.read(nm)
                im = decode_jp2(data, img["working_height_px"])
                if im is None:
                    # a leaf Pillow cannot decode (seen 4 October: "broken data stream", 2 of the first 83
                    # issues): the page is written blank at the median size so numbering stays intact, and the
                    # leaf is recorded for the repair pass that re-renders it from the archive's PDF
                    mleaf = re.search(r"(\d+)\.jp2$", nm.lower())
                    bad_leaves.append({"page": i, "leaf": int(mleaf.group(1)) if mleaf else i - 1, "name": nm})
                    w, h = (sorted(sizes)[len(sizes) // 2] if sizes else (1500, 2200))
                    im = Image.new("L", (max(100, w), max(100, h)), 255)
                else:
                    sizes.append(im.size)
                _save_working(im, out, os.path.join(thumbs_dir, f"page_{i:04d}.jpg"), img)
                n += 1
    else:                                                   # pdf fallback
        tmp = os.path.join(pages_dir, "_pdf")
        os.makedirs(tmp, exist_ok=True)
        subprocess.run(["pdftoppm", "-jpeg", "-r", "200", master, os.path.join(tmp, "p")], check=True)
        for i, p in enumerate(sorted(glob.glob(os.path.join(tmp, "p-*.jpg"))), 1):
            im = Image.open(p); sizes.append(im.size)
            _save_working(im, os.path.join(pages_dir, f"page_{i:04d}.jpg"), os.path.join(thumbs_dir, f"page_{i:04d}.jpg"), img)
            n += 1
        shutil.rmtree(tmp, ignore_errors=True)
    ppi, ppi_source = None, None
    sd = os.path.join(paths["raw"], "scandata.xml")
    if os.path.exists(sd):
        m = re.search(r"<ppi>(\d+)</ppi>", open(sd, encoding="utf-8", errors="replace").read())
        if m:
            ppi, ppi_source = int(m.group(1)), "scandata"
    dx = os.path.join(paths["raw"], "ia_djvu.xml")
    if not ppi and os.path.exists(dx):
        with open(dx, encoding="utf-8", errors="replace") as f:
            head = f.read(200000)
        m = re.search(r'<PARAM name="DPI" value="(\d+)"', head)
        if m:
            ppi, ppi_source = int(m.group(1)), "djvu_xml (nominal)"
    med = sorted(sizes)[len(sizes) // 2] if sizes else None
    rec = {"pages": n, "master_px": list(med) if med else None, "ppi": ppi, "ppi_source": ppi_source, "seconds": round(time.time() - t0, 1)}
    if bad_leaves:
        rec["bad_leaves"] = bad_leaves                      # blank pages to repair from the PDF (s01c --repair)
    if ppi and med:
        rec["trim_in"] = [round(med[0] / ppi, 2), round(med[1] / ppi, 2)]     # the scanned leaf in inches (from a nominal dpi: a hint, not a measurement)
    return rec


def archive_kind(path):
    """'zip', 'rar' or '7z' by the file's first bytes; None for anything else."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    return None


def _zip_has_jp2(path):
    try:
        with zipfile.ZipFile(path) as z:
            return any(nm.lower().endswith(".jp2") for nm in z.namelist())
    except zipfile.BadZipFile:
        return False


def _extract_rar(path, into):
    """A RAR (Comic Book RAR) unpacked with whichever tool the machine has: bsdtar (libarchive), 7z, unrar."""
    tools = (["bsdtar", "-xf", path, "-C", into], ["7z", "x", "-y", f"-o{into}", path], ["7za", "x", "-y", f"-o{into}", path],
             ["unrar", "x", "-o+", "-y", path, into + os.sep])
    tried = []
    for cmd in tools:
        if shutil.which(cmd[0]) is None:
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        tried.append(f"{cmd[0]} exit {r.returncode}")
        if r.returncode == 0:
            return cmd[0]
    raise RuntimeError("no tool could unpack the RAR (install bsdtar, p7zip or unrar): " + (", ".join(tried) or "none installed"))


def _image_from_archive(master, kind, pages_dir, thumbs_dir, img):
    """Page images inside a zip (cbz, a raw zip, our zip of loose images) or a RAR (cbr) -> working JPEGs and thumbs,
    in the natural order of the file names (page_2 before page_10). Returns (pages, sizes)."""
    from PIL import Image
    n, sizes = 0, []
    tmp = os.path.join(pages_dir, "_unpack")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        if kind == "zip":
            with zipfile.ZipFile(master) as z:
                names = [nm for nm in z.namelist() if nm.lower().endswith(IMAGE_EXT) and not nm.endswith("/") and "__macosx" not in nm.lower()]
                names.sort(key=_natkey)
                for i, nm in enumerate(names, 1):
                    out = os.path.join(pages_dir, f"page_{i:04d}.jpg")
                    if os.path.exists(out) and os.path.exists(os.path.join(thumbs_dir, f"page_{i:04d}.jpg")):
                        n += 1; continue
                    data = z.read(nm)
                    im = decode_jp2(data, img["working_height_px"]) if nm.lower().endswith(".jp2") else Image.open(io.BytesIO(data))
                    if im is None:
                        raise RuntimeError(f"page image {nm} could not be decoded")
                    im.load(); sizes.append(im.size)
                    _save_working(im, out, os.path.join(thumbs_dir, f"page_{i:04d}.jpg"), img)
                    n += 1
        else:
            _extract_rar(master, tmp)
            files = []
            for root_dir, _d, fs in os.walk(tmp):
                files += [os.path.join(root_dir, x) for x in fs if x.lower().endswith(IMAGE_EXT) and not x.startswith(".")]
            files.sort(key=lambda p: _natkey(os.path.relpath(p, tmp)))
            if not files:
                raise RuntimeError("the RAR holds no page images")
            for i, p in enumerate(files, 1):
                out = os.path.join(pages_dir, f"page_{i:04d}.jpg")
                if os.path.exists(out) and os.path.exists(os.path.join(thumbs_dir, f"page_{i:04d}.jpg")):
                    n += 1; continue
                im = Image.open(p); im.load(); sizes.append(im.size)
                _save_working(im, out, os.path.join(thumbs_dir, f"page_{i:04d}.jpg"), img)
                n += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if n == 0:
        raise RuntimeError(f"no page images in {os.path.basename(master)}")
    return n, sizes


def _image_job(iid):
    try:
        rec = image_issue(iid)
        mark(iid, "imaged", **rec)
        manifest({"issue": iid, "event": "issue_imaged", **rec})
        return iid, rec, None
    except Exception as e:
        mark(iid, "imaged", fail=True, error=f"{type(e).__name__}: {e}")
        return iid, None, f"{type(e).__name__}: {e}"


# ----------------------------------------------------------------------------------------------------------------
# the run
# ----------------------------------------------------------------------------------------------------------------
def pending(issues, stage):
    return [i for i in issues if not has(i["id"], stage)]


def download_worker(q, results, wid):
    while True:
        try:
            issue = q.get_nowait()
        except queue.Empty:
            return
        if stop_requested():
            return
        iid = issue["id"]
        try:
            with stage_timer("s01c_fetch", iid):
                rec = fetch_issue(issue)
            results.put((iid, rec, None))
            log("s01c", f"w{wid} {iid}: {rec['src']} {rec['bytes'] / 1e6:.1f} MB, {rec['leaves']} leaves, {rec['seconds']}s")
        except Stop:
            return
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            mark(iid, "downloaded", fail=True, error=msg)
            manifest({"issue": iid, "event": "issue_failed", "error": msg})
            results.put((iid, None, msg))
            log("s01c", f"w{wid} {iid}: FAILED {msg}")


def run(issues, workers, image_processes, image_only=False):
    issues, conflicts = partition_issue_ids(issues)
    for iid, sources in conflicts.items():
        log("s01c", f"holding ambiguous issue id {iid}: {sources}; the other issues continue")
    os.makedirs(CORPUS_DIR, exist_ok=True)
    todo_dl = [] if image_only else pending(issues, "downloaded")
    log("s01c", f"{len(issues):,} issues in the list; {len(todo_dl):,} to download; "
                f"{len([i for i in issues if has(i['id'], 'downloaded') and not has(i['id'], 'imaged')]):,} downloaded and waiting for imaging")
    q = queue.Queue()
    for i in todo_dl:
        q.put(i)
    results = queue.Queue()
    threads = [threading.Thread(target=download_worker, args=(q, results, w), daemon=True) for w in range(workers)]
    for t in threads:
        t.start()
    done_dl = n_fail = n_img = 0
    futures = {}
    with ProcessPoolExecutor(max_workers=image_processes) as pool:
        # anything downloaded earlier but not yet imaged
        for i in issues:
            if has(i["id"], "downloaded") and not has(i["id"], "imaged"):
                futures[pool.submit(_image_job, i["id"])] = i["id"]
        while True:
            alive = any(t.is_alive() for t in threads)
            try:
                iid, rec, err = results.get(timeout=5)
                if rec:
                    done_dl += 1
                    futures[pool.submit(_image_job, iid)] = iid
                else:
                    n_fail += 1
            except queue.Empty:
                pass
            for f in [f for f in futures if f.done()]:
                iid, rec, err = f.result()
                futures.pop(f)
                if rec:
                    n_img += 1
                    log("s01c", f"imaged {iid}: {rec['pages']} pages, {rec['seconds']}s" + (f", {rec['trim_in'][0]}x{rec['trim_in'][1]} in" if rec.get("trim_in") else ""))
                else:
                    log("s01c", f"imaging FAILED {iid}: {err}")
            if not alive and not futures and results.empty():
                break
            if stop_requested() and not futures:
                log("s01c", "STOP file seen — stopping after the files in progress")
                break
    log("s01c", f"done: {done_dl:,} downloaded, {n_fail:,} failed, {n_img:,} imaged")


def repair_bad_leaves(iid, root=None):
    """Re-render the blank pages of an issue (state imaged.bad_leaves) from the archive's PDF: the PDF holds the
    leaves marked addToAccessFormats in scandata.xml, in order, so leaf N is PDF page (number of such leaves
    before N) + 1. Returns the pages repaired."""
    from PIL import Image
    from corpus_lib import load_state
    root = root or ROOT
    st = load_state(iid)
    bad = (st["stages"].get("imaged") or {}).get("bad_leaves") or []
    if not bad:
        return 0
    paths = issue_dirs(iid)
    img = settings()["images"]
    meta = json.load(open(os.path.join(paths["raw"], "meta.json"), encoding="utf-8"))
    pdf = choose_files(meta)["pdf"] or pick(meta.get("files", []), lambda n: n.lower().endswith(".pdf"))
    if not pdf:
        mark(iid, "repaired", fail=True, error="no pdf in the item for the bad leaves")
        return 0
    ident = meta.get("metadata", {}).get("identifier") or st["stages"]["downloaded"].get("ia_identifier")
    pdf_path = os.path.join(paths["raw"], "repair.pdf")
    if not os.path.exists(pdf_path):
        download_file(f"https://archive.org/download/{ident}/{quote(pdf['name'])}", pdf_path, int(pdf.get("size") or 0) or None, pdf.get("md5"))
    # leaf -> pdf page
    access = []
    sd = os.path.join(paths["raw"], "scandata.xml")
    if os.path.exists(sd):
        txt = open(sd, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r'<page leafNum="(\d+)">(.*?)</page>', txt, re.S):
            if "<addToAccessFormats>true</addToAccessFormats>" in m.group(2):
                access.append(int(m.group(1)))
    done = 0
    for b in bad:
        leaf = b["leaf"]
        pdf_page = (access.index(leaf) + 1) if leaf in access else (leaf + 1)
        tmp = os.path.join(paths["pages"], f"_repair_{leaf}")
        os.makedirs(tmp, exist_ok=True)
        r = subprocess.run(["pdftoppm", "-jpeg", "-r", "200", "-f", str(pdf_page), "-l", str(pdf_page), pdf_path, os.path.join(tmp, "p")],
                           capture_output=True, text=True)
        got = sorted(glob.glob(os.path.join(tmp, "p*.jpg")))
        if r.returncode == 0 and got:
            im = Image.open(got[0])
            _save_working(im, os.path.join(paths["pages"], f"page_{b['page']:04d}.jpg"), os.path.join(paths["thumbs"], f"page_{b['page']:04d}.jpg"), img)
            done += 1
        shutil.rmtree(tmp, ignore_errors=True)
    os.remove(pdf_path)
    mark(iid, "repaired", pages=done, of=len(bad), pdf=pdf["name"])
    manifest({"issue": iid, "event": "leaves_repaired", "pages": done, "of": len(bad)})
    return done


def smoke(ident):
    """Fetch and image one arbitrary item into data/corpus/smoke/ — a test of the machinery, not corpus data."""
    root = os.path.join(CORPUS_DIR, "smoke")
    issue = {"id": f"smoke_{re.sub(r'[^A-Za-z0-9]+', '', ident)[-16:].lower()}", "ia_identifier": ident}
    t0 = time.time()
    rec = fetch_issue(issue, root=root)
    log("s01c", f"smoke download: {json.dumps(rec)}")
    img = image_issue(issue["id"], root=root)
    log("s01c", f"smoke imaging: {json.dumps(img)}  total {time.time() - t0:.1f}s")
    return rec, img


def selftest():
    """The file choice and the imaging of the new masters, on made-up items (no network; p50t)."""
    import tempfile
    from PIL import Image
    def rec(name, fmt, size=1000, source="original"):
        return {"name": name, "format": fmt, "size": size, "source": source}
    cases = (
        ("cbr and pdf", {"files": [rec("x_meta.xml", "Metadata"), rec("A.cbr", "Comic Book RAR"), rec("A.pdf", "Text PDF")]}, "pdf"),
        ("cbz", {"files": [rec("a.cbz", "Comic Book ZIP"), rec("x_thumb.jpg", "JPEG Thumb"), rec("__ia_thumb.jpg", "Item Tile")]}, "cbz"),
        ("loose pages", {"files": [rec(f"scan {i}.jpg", "JPEG") for i in range(1, 36)] + [rec("scan 1_thumb.jpg", "JPEG Thumb", source="derivative")]}, "images"),
        ("a flip-book export", {"files": [rec(f"Final/files/mobile/{i}.jpg", "JPEG", 4000000) for i in range(1, 101)]
                                + [rec(f"Final/files/thumb/{i}.jpg", "JPEG", 20000) for i in range(1, 101)]
                                + [rec(f"Final/mobile/style/icon/{i}-2x.png", "PNG") for i in range(1, 60)] + [rec("Final/files/shot.png", "PNG")]}, "images"),
        ("an illustration set", {"files": [rec(f"cover art {t} {y}.jpg", "JPEG") for y in range(1920, 1950) for t in ("Dime Western", "Puppy Love")]}, None),
        ("a cover only", {"files": [rec("cover.jpg", "JPEG"), rec("cover_thumb.jpg", "JPEG Thumb")]}, None),
        ("raw zip", {"files": [rec("aasw_images.zip", "Generic Raw Book Zip")]}, "raw_zip"),
        ("jp2 first", {"files": [rec("x_jp2.zip", "Single Page Processed JP2 ZIP"), rec("x.pdf", "Text PDF"), rec("x.cbr", "Comic Book RAR")]}, "jp2"))
    for name, item, want in cases:
        kind, _f = master_choice(choose_files(item))
        assert kind == want, (name, kind, want)
    assert [f["name"] for f in page_images(cases[2][1]["files"])][:3] == ["scan 1.jpg", "scan 2.jpg", "scan 3.jpg"]
    fb = page_images(cases[3][1]["files"])
    assert len(fb) == 100 and fb[0]["name"] == "Final/files/mobile/1.jpg" and fb[99]["name"] == "Final/files/mobile/100.jpg", fb[:2]
    assert len(comic_archives([rec("asf1933-01.cbr", "Comic Book RAR"), rec("asf1933-03.cbr", "Comic Book RAR")])) == 2
    assert len(comic_archives([rec("x.cbz", "Comic Book ZIP"), rec("x.cbr", "Comic Book RAR")])) == 1
    assert _natkey("page_10.jpg") > _natkey("page_2.jpg")
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "config"))
        shutil.copy(os.path.join(ROOT, "config", "corpus_settings.json"), os.path.join(tmp, "config", "corpus_settings.json"))
        iid = "selftest_issue"
        paths = {k: os.path.join(tmp, os.path.relpath(v, ROOT)) for k, v in issue_dirs(iid).items()}
        os.makedirs(paths["master"]); os.makedirs(paths["raw"])
        def jpg(shade):
            b = io.BytesIO(); Image.new("RGB", (1200, 1800), (shade, shade, shade)).save(b, "JPEG"); return b.getvalue()
        with zipfile.ZipFile(os.path.join(paths["master"], "issue.cbz"), "w") as z:
            z.writestr("Issue/page 10.jpg", jpg(200)); z.writestr("Issue/page 2.jpg", jpg(100)); z.writestr("Issue/page 1.jpg", jpg(50))
            z.writestr("Issue/readme.txt", "x"); z.writestr("__MACOSX/Issue/._page 1.jpg", b"junk")
        r = image_issue(iid, root=tmp)
        assert r["pages"] == 3 and sorted(os.listdir(paths["pages"])) == ["page_0001.jpg", "page_0002.jpg", "page_0003.jpg"], r
        assert Image.open(os.path.join(paths["pages"], "page_0003.jpg")).getpixel((5, 5))[0] > 150   # page 10 came last
        # a .cbr that is in fact a zip (17 October 2026) is read as a zip
        os.remove(os.path.join(paths["master"], "issue.cbz"))
        shutil.rmtree(paths["pages"]); shutil.rmtree(paths["thumbs"], ignore_errors=True)
        os.makedirs(paths["pages"]); os.makedirs(paths["thumbs"], exist_ok=True)
        with zipfile.ZipFile(os.path.join(paths["master"], "issue.cbr"), "w") as z:
            z.writestr("p1.jpg", jpg(50)); z.writestr("p2.jpg", jpg(100))
        assert archive_kind(os.path.join(paths["master"], "issue.cbr")) == "zip"
        r = image_issue(iid, root=tmp)
        assert r["pages"] == 2, r
        tools = [t for t in ("bsdtar", "7z", "7za", "unrar") if shutil.which(t)]
        print("s01c selftest ok" + (f" (RAR tools here: {', '.join(tools)})" if tools else " (NO RAR TOOL on this machine: a Comic Book RAR cannot be unpacked; install bsdtar, p7zip or unrar)"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="download and image every pending issue of the approved list")
    ap.add_argument("--issue", help="one issue id of the list")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pending issues (a trial run)")
    ap.add_argument("--image-only", action="store_true")
    ap.add_argument("--workers", type=int, default=0, help="download workers (default: settings.download.workers)")
    ap.add_argument("--image-processes", type=int, default=0, help="imaging processes (default: settings.images.processes)")
    ap.add_argument("--selftest", action="store_true", help="the file choice and the imaging of the new masters, no network (p50t)")
    ap.add_argument("--smoke", metavar="IA_IDENTIFIER", help="fetch one arbitrary item into data/corpus/smoke/ (a test)")
    ap.add_argument("--repair", action="store_true", help="re-render the blank pages (bad JP2 leaves) of imaged issues from the archive's PDF")
    args = ap.parse_args()
    if args.selftest:
        selftest(); return
    if args.smoke:
        smoke(args.smoke); return
    if args.repair:
        from corpus_lib import all_states
        _safe, conflicts = partition_issue_ids(corpus_config()["issues"])
        n = 0
        for iid, st in all_states().items():
            if iid in conflicts:
                log("s01c", f"holding repair of ambiguous issue id {iid}: {conflicts[iid]}")
                continue
            if (st["stages"].get("imaged") or {}).get("bad_leaves") and "repaired" not in st["stages"]:
                log("s01c", f"repair {iid}: {len(st['stages']['imaged']['bad_leaves'])} bad leaves")
                n += repair_bad_leaves(iid)
        log("s01c", f"repaired {n} pages"); return
    cfg = corpus_config()
    require_approved(cfg, "download")
    issues, conflicts = partition_issue_ids(cfg["issues"])
    for iid, sources in conflicts.items():
        log("s01c", f"holding ambiguous issue id {iid}: {sources}; the other issues continue")
    if args.issue:
        if args.issue in conflicts:
            sys.exit(f"ambiguous issue id {args.issue}: {conflicts[args.issue]}; source audit required")
        issues = [i for i in issues if i["id"] == args.issue]
        if not issues:
            sys.exit(f"no issue with id {args.issue}")
    elif not (args.run or args.image_only):
        sys.exit("pass --run, --image-only, --issue <id> or --smoke <identifier>")
    if args.limit:
        issues = pending(issues, "downloaded")[: args.limit]
    st = settings()
    run(issues, args.workers or st["download"]["workers"], args.image_processes or st["images"].get("processes", 4), image_only=args.image_only)


if __name__ == "__main__":
    main()
