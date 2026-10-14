#!/usr/bin/env python3
"""Stage 2 — route A: layout-aware OCR with Surya (v0.22+, foundation model).

Modern Surya runs the surya-ocr-2 model behind its own OpenAI-compatible
server (it spawns a vLLM docker container on the GPU automatically) and
returns, per page, labeled BLOCKS in reading order: label, bbox, html text,
confidence. One pass gives layout + text + reading order together, so the
old separate layout pass is gone.

Input : data/pages/<id>/page_NNNN.png or .jpg  (only issues whose download is
        CONFIRMED complete — s01's data/raw/manifest.jsonl, or the corpus
        state "imaged" written by s01c — a half-downloaded issue is skipped
        with a message, never half-processed)
Output: data/layout/<id>/page_NNNN.json  regions: label, bbox, order, text
        data/text/<id>/routeA/page_NNNN.txt  text in reading order,
        pictures and page furniture dropped
        data/layout/<id>/ocr_complete.json  completion and source-image identities

Every source scan must have exactly one result, using its original page number. Partial output is read again;
it never counts as a completed issue. Each attempt uses a fresh work directory and publishes complete files.

Server behavior: SURYA_INFERENCE_KEEP_ALIVE=true is set so the spawned
model server stays up across the per-issue invocations (one model load for
the whole run). To point at a server you started yourself (e.g., pinned to a
specific GPU), set SURYA_INFERENCE_URL before running; surya then spawns
nothing. GPU use approved by Heejin 2026-08-20: GPU 0 or 1.

The adapter handles both the new blocks schema and the old text_lines
schema; on an unknown schema it stops and prints what it found, keeping the
raw results.json in data/work_surya/<id>/attempt-*/ for inspection.
"""
import argparse
import glob
import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT
from corpus_lib import write_json_atomic

MANIFEST = os.path.join(ROOT, "data", "raw", "manifest.jsonl")

# labels excluded from the reading text (kept in the page JSON for the site)
DROP = {"picture", "figure", "image", "table", "pageheader", "pagefooter",
        "caption", "footnote", "handwriting", "equation", "form"}


def norm_label(label):
    return re.sub(r"[^a-z]", "", str(label).lower())


def surya_bin(name):
    """Find a surya command even when ~/.local/bin is not on PATH."""
    p = shutil.which(name)
    if p:
        return p
    cand = os.path.expanduser(f"~/.local/bin/{name}")
    return cand if os.path.exists(cand) else None


def corpus_downloaded(iid):
    """The corpus downloader's record (data/corpus/state/<id>.json has the stage 'imaged')."""
    try:
        from corpus_lib import has
        return has(iid, "imaged")
    except Exception:
        return False


def issues_download_complete():
    """Issue ids with an issue_done event in the s01 manifest."""
    done = set()
    if os.path.exists(MANIFEST):
        for line in open(MANIFEST, encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("event") == "issue_done":
                    done.add(r.get("issue"))
            except Exception:
                pass
    return done


def html_to_text(h):
    h = re.sub(r"<br\s*/?>", "\n", h or "")
    h = re.sub(r"</p>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    return html_mod.unescape(h).strip()


def run_surya(pages_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("SURYA_INFERENCE_KEEP_ALIVE", "true")
    subprocess.run([surya_bin("surya_ocr"), pages_dir, "--output_dir",
                    os.path.join(work_dir, "ocr")], check=True, env=env)


def _find_results_json(base):
    found = []
    for root, _dirs, files in os.walk(base):
        for f in files:
            if f == "results.json":
                found.append(os.path.join(root, f))
    if len(found) > 1:
        raise RuntimeError(f"multiple OCR results.json files under {base}")
    return found[0] if found else None


def _page_number(filename):
    """Surya keys are image basenames (sometimes full paths), with or without the extension."""
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    match = re.fullmatch(r"page_(\d+)(?:\.(?:png|jpe?g))?", name, re.I)
    if not match or int(match.group(1)) < 1:
        raise RuntimeError(f"unrecognized page image name: {filename!r}")
    return int(match.group(1))


def source_pages(pages_dir):
    """The exact scan identities; missing or duplicate scans must not silently renumber later pages."""
    pages = {}
    for name in sorted(os.listdir(pages_dir)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        pno = _page_number(name)
        if pno in pages:
            raise RuntimeError(f"duplicate images for page {pno}: {pages[pno]}, {name}")
        pages[pno] = name
    if not pages:
        raise RuntimeError(f"no page images under {pages_dir}")
    missing = sorted(set(range(1, max(pages) + 1)) - set(pages))
    if missing:
        raise RuntimeError(f"missing source page images: {missing[:20]}")
    return pages


def _source_signature(pages_dir, pages):
    out = {}
    for pno, name in sorted(pages.items()):
        stat = os.stat(os.path.join(pages_dir, name))
        out[str(pno)] = {"name": name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return out


def _page_text(page):
    return "\n\n".join(rg["text"] for rg in sorted(page["regions"], key=lambda r: r["order"])
                       if rg["text"] and norm_label(rg["label"]) not in DROP)


def _write_text_atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def validate_outputs(iid, pages_dir=None):
    """Return the page count only when every scan has matching, readable layout and text output.

    Legacy complete outputs remain usable. New outputs carry a completion manifest so an interrupted rewrite or
    changed source image cannot be mistaken for a completed reading merely because every filename exists.
    """
    pages_dir = pages_dir or os.path.join(ROOT, "data", "pages", iid)
    pages = source_pages(pages_dir)
    layout = os.path.join(ROOT, "data", "layout", iid)
    text = os.path.join(ROOT, "data", "text", iid, "routeA")
    for directory, extension in ((layout, "json"), (text, "txt")):
        wanted = {f"page_{pno:04d}.{extension}" for pno in pages}
        actual = {os.path.basename(p) for p in glob.glob(os.path.join(directory, f"page_*.{extension}"))}
        if wanted != actual:
            raise RuntimeError(f"incomplete OCR under {directory}: missing {sorted(wanted - actual)[:10]}, "
                               f"unexpected {sorted(actual - wanted)[:10]}")
    has_provenance = False
    try:
        for pno, name in pages.items():
            with open(os.path.join(layout, f"page_{pno:04d}.json"), encoding="utf-8") as f:
                page = json.load(f)
            if not isinstance(page, dict) or page.get("page") != pno or not isinstance(page.get("regions"), list):
                raise ValueError(f"invalid layout identity/schema for page {pno}")
            if "source_image" in page:
                has_provenance = True
                if page["source_image"] != name:
                    raise ValueError(f"wrong source image for page {pno}")
            with open(os.path.join(text, f"page_{pno:04d}.txt"), encoding="utf-8") as f:
                if f.read() != _page_text(page):
                    raise ValueError(f"layout/text mismatch for page {pno}")
        complete = os.path.join(layout, "ocr_complete.json")
        if has_provenance or os.path.exists(complete):
            with open(complete, encoding="utf-8") as f:
                marker = json.load(f)
            if not isinstance(marker, dict) or marker.get("source_images") != _source_signature(pages_dir, pages):
                raise ValueError("source images changed since OCR completed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"incomplete OCR for {iid}: {exc}") from exc
    return len(pages)


def parse_page(pageobj, pageno):
    """One surya page -> our page JSON. Handles new (blocks) and old
    (text_lines) schemas."""
    ib = pageobj.get("image_bbox") or [0, 0, 0, 0]
    page = {"page": pageno, "width": ib[2], "height": ib[3], "regions": []}

    if "blocks" in pageobj:                       # surya >= 0.22 foundation
        for i, b in enumerate(pageobj["blocks"]):
            if b.get("skipped"):
                continue
            text = html_to_text(b.get("html") or b.get("text") or "")
            page["regions"].append({
                "label": b.get("label", "Text"),
                "bbox": b.get("bbox") or [0, 0, 0, 0],
                "order": i,
                "conf": round(float(b.get("confidence") or 0.0), 3),
                "text": text,
                "lines": [],
            })
        return page

    if "text_lines" in pageobj:                   # older surya
        lines = pageobj.get("text_lines") or []
        page["regions"].append({
            "label": "Text", "bbox": ib, "order": 0, "conf": 0.0,
            "text": "\n".join(tl.get("text", "") for tl in lines),
            "lines": [{"bbox": tl.get("bbox"), "text": tl.get("text", ""),
                       "conf": round(float(tl.get("confidence") or 0), 3)}
                      for tl in lines],
        })
        return page

    raise RuntimeError(
        "unknown surya page schema; keys = " + ", ".join(sorted(pageobj)))


def adapt(iid, work_dir, expected_signature=None):
    rj = _find_results_json(os.path.join(work_dir, "ocr"))
    if not rj:
        raise RuntimeError(f"no results.json under {work_dir}/ocr")
    with open(rj, encoding="utf-8") as f:
        data = json.load(f)
    pages_dir = os.path.join(ROOT, "data", "pages", iid)
    sources = source_pages(pages_dir)
    signature = _source_signature(pages_dir, sources)
    if expected_signature is not None and signature != expected_signature:
        raise RuntimeError("source images changed while OCR was running")
    parsed = {}
    # Validate the entire reading before publishing any page. One image must have exactly one result.
    if not isinstance(data, dict):
        raise RuntimeError("OCR results must be keyed by source image")
    for key, value in data.items():
        pno = _page_number(key)
        if pno in parsed:
            raise RuntimeError(f"duplicate OCR results for page {pno}")
        objects = value if isinstance(value, list) else [value]
        if len(objects) != 1 or not isinstance(objects[0], dict):
            raise RuntimeError(f"expected one OCR result for image {key!r}")
        parsed[pno] = parse_page(objects[0], pno)
    if set(parsed) != set(sources):
        raise RuntimeError(f"incomplete OCR reading: missing pages {sorted(set(sources) - set(parsed))[:20]}, "
                           f"unexpected pages {sorted(set(parsed) - set(sources))[:20]}")

    outdir_json = os.path.join(ROOT, "data", "layout", iid)
    outdir_txt = os.path.join(ROOT, "data", "text", iid, "routeA")
    os.makedirs(outdir_json, exist_ok=True)
    os.makedirs(outdir_txt, exist_ok=True)

    complete = os.path.join(outdir_json, "ocr_complete.json")
    # An interrupted replacement must fail validation even if an older attempt had all its filenames.
    if os.path.exists(complete):
        os.remove(complete)
    for pno in sorted(parsed):
        page = dict(parsed[pno], source_image=sources[pno])
        write_json_atomic(os.path.join(outdir_json, f"page_{pno:04d}.json"), page)
        _write_text_atomic(os.path.join(outdir_txt, f"page_{pno:04d}.txt"), _page_text(page))
    # Obsolete page outputs are removed only after a complete replacement reading has been parsed and written.
    for directory, extension in ((outdir_json, "json"), (outdir_txt, "txt")):
        wanted = {f"page_{pno:04d}.{extension}" for pno in sources}
        for path in glob.glob(os.path.join(directory, f"page_*.{extension}")):
            if os.path.basename(path) not in wanted:
                os.remove(path)
    if _source_signature(pages_dir, sources) != signature:
        raise RuntimeError("source images changed while OCR output was being written")
    write_json_atomic(complete, {"adapter": 2, "source_images": signature})
    return validate_outputs(iid, pages_dir)


def run_issue(iid, force=False):
    pages_dir = os.path.join(ROOT, "data", "pages", iid)
    if not os.path.isdir(pages_dir):
        print(f"[s02] {iid}: no pages on disk, run s01 first"); return
    if not force and iid not in issues_download_complete() and not corpus_downloaded(iid):
        print(f"[s02] {iid}: download not confirmed complete in the s01 "
              f"manifest or the corpus state — skipped (use --force to override)"); return
    sources = source_pages(pages_dir)
    if not force:
        try:
            got = validate_outputs(iid, pages_dir)
        except RuntimeError as exc:
            print(f"[s02] {iid}: reading required ({exc})")
        else:
            print(f"[s02] {iid}: {got} complete routeA pages already exist — skipped"); return
    if not surya_bin("surya_ocr"):
        sys.exit("surya not installed — run scripts/server_setup.sh")
    n = len(sources)
    work = os.path.join(ROOT, "data", "work_surya", iid)
    os.makedirs(work, exist_ok=True)
    attempt = tempfile.mkdtemp(prefix="attempt-", dir=work)
    signature = _source_signature(pages_dir, sources)
    with stage_timer("s02_layout_ocr", iid, pages=n, extra={"route": "A"}):
        run_surya(pages_dir, attempt)
        got = adapt(iid, attempt, expected_signature=signature)
    shutil.rmtree(work, ignore_errors=True)
    print(f"[s02] {iid}: {got} pages adapted (route A)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--force", action="store_true",
                    help="process even without a completed-download record")
    args = ap.parse_args()
    from corpus_lib import issues_config
    cfg = issues_config()           # the pilot list, or PULP_ISSUES=config/corpus_issues.json
    ids = [i["id"] for i in cfg["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, force=args.force)


if __name__ == "__main__":
    main()
