#!/usr/bin/env python3
"""s01c — the corpus downloader and imager (Phase 1 of corpus-build-plan-2026-10-04).

For every issue in config/corpus_issues.json (approved=true required; see corpus_lib.require_approved):

  data/raw/<id>/meta.json            the archive's item record (file list with sizes and md5s, scan info)
  data/raw/<id>/ia_text.txt          the archive's own OCR text (_djvu.txt), the free baseline
  data/raw/<id>/ia_djvu.xml          the archive's positional OCR with page divisions (_djvu.xml)
  data/raw/<id>/scandata.xml         per-leaf scan record (page type, which leaves the access files include, ppi)
  data/masters/<id>/<ident>_jp2.zip  the scan master, kept zipped (one file per issue, no inode flood);
                                     fallback <ident>.pdf when the item has no JP2 archive
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
from corpus_lib import ROOT, settings, corpus_config, require_approved, mark, has, log, issue_dirs, write_json_atomic  # noqa: E402
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


def choose_files(meta):
    """Which of the item's files we take, by role."""
    files = meta.get("files", [])
    low = lambda n: n.lower()
    out = {
        "djvu_txt": pick(files, lambda n: low(n).endswith("_djvu.txt")),
        "djvu_xml": pick(files, lambda n: low(n).endswith("_djvu.xml")),
        "scandata": pick(files, lambda n: low(n).endswith("_scandata.xml") or low(n) == "scandata.xml"),
        "jp2_zip": pick(files, lambda n: low(n).endswith("_jp2.zip") and not re.search(r"_(raw|orig)_jp2\.zip$", low(n))),
        "pdf": None,
    }
    if not out["jp2_zip"]:
        out["pdf"] = pick(files, lambda n: low(n).endswith(".pdf") and "_text" not in low(n)) or pick(files, lambda n: low(n).endswith(".pdf"))
    return out


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
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path, encoding="utf-8"))
    else:
        meta = json.loads(get_bytes(f"https://archive.org/metadata/{ident}").decode("utf-8"))
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
    src = None
    f = chosen["jp2_zip"] or chosen["pdf"]
    if not f:
        raise RuntimeError(f"{ident}: no _jp2.zip and no pdf in the item")
    src = "jp2" if chosen["jp2_zip"] else "pdf"
    dest = os.path.join(master_dir, os.path.basename(f["name"]))
    if os.path.exists(dest):
        got["master"] = {"name": f["name"], "bytes": os.path.getsize(dest), "md5": f.get("md5")}
    else:
        n = download_file(f"{server}/{quote(f['name'])}", dest, int(f.get("size") or 0) or None, f.get("md5"))
        got["master"] = {"name": f["name"], "bytes": n, "md5": f.get("md5")}
        manifest({"issue": iid, "ident": ident, "file": "master", "name": f["name"], "bytes": n, "md5": f.get("md5"), "src": src})
    leaves = None
    if src == "jp2":
        with zipfile.ZipFile(dest) as z:
            leaves = sum(1 for nm in z.namelist() if nm.lower().endswith(".jp2"))
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
    if master.lower().endswith(".zip"):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="download and image every pending issue of the approved list")
    ap.add_argument("--issue", help="one issue id of the list")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pending issues (a trial run)")
    ap.add_argument("--image-only", action="store_true")
    ap.add_argument("--workers", type=int, default=0, help="download workers (default: settings.download.workers)")
    ap.add_argument("--image-processes", type=int, default=0, help="imaging processes (default: settings.images.processes)")
    ap.add_argument("--smoke", metavar="IA_IDENTIFIER", help="fetch one arbitrary item into data/corpus/smoke/ (a test)")
    ap.add_argument("--repair", action="store_true", help="re-render the blank pages (bad JP2 leaves) of imaged issues from the archive's PDF")
    args = ap.parse_args()
    if args.smoke:
        smoke(args.smoke); return
    if args.repair:
        from corpus_lib import all_states
        n = 0
        for iid, st in all_states().items():
            if (st["stages"].get("imaged") or {}).get("bad_leaves") and "repaired" not in st["stages"]:
                log("s01c", f"repair {iid}: {len(st['stages']['imaged']['bad_leaves'])} bad leaves")
                n += repair_bad_leaves(iid)
        log("s01c", f"repaired {n} pages"); return
    cfg = corpus_config()
    require_approved(cfg, "download")
    issues = cfg["issues"]
    if args.issue:
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
