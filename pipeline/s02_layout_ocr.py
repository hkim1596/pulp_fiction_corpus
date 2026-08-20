#!/usr/bin/env python3
"""Stage 2 — route A: layout-aware OCR with Surya (v0.22+, foundation model).

Modern Surya runs the surya-ocr-2 model behind its own OpenAI-compatible
server (it spawns a vLLM docker container on the GPU automatically) and
returns, per page, labeled BLOCKS in reading order: label, bbox, html text,
confidence. One pass gives layout + text + reading order together, so the
old separate layout pass is gone.

Input : data/pages/<id>/page_NNNN.png  (only issues whose s01 download is
        CONFIRMED complete in data/raw/manifest.jsonl — a half-downloaded
        issue is skipped with a message, never half-processed)
Output: data/layout/<id>/page_NNNN.json  regions: label, bbox, order, text
        data/text/<id>/routeA/page_NNNN.txt  text in reading order,
        pictures and page furniture dropped

Server behavior: SURYA_INFERENCE_KEEP_ALIVE=true is set so the spawned
model server stays up across the per-issue invocations (one model load for
the whole run). To point at a server you started yourself (e.g., pinned to a
specific GPU), set SURYA_INFERENCE_URL before running; surya then spawns
nothing. GPU use approved by Heejin 2026-08-20: GPU 0 or 1.

The adapter handles both the new blocks schema and the old text_lines
schema; on an unknown schema it stops and prints what it found, keeping the
raw results.json in data/work_surya/<id>/ for inspection.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT

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
    for root, _dirs, files in os.walk(base):
        for f in files:
            if f.endswith(".json"):
                return os.path.join(root, f)
    return None


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


def adapt(iid, work_dir):
    rj = _find_results_json(os.path.join(work_dir, "ocr"))
    if not rj:
        raise RuntimeError(f"no results.json under {work_dir}/ocr")
    data = json.load(open(rj, encoding="utf-8"))

    outdir_json = os.path.join(ROOT, "data", "layout", iid)
    outdir_txt = os.path.join(ROOT, "data", "text", iid, "routeA")
    os.makedirs(outdir_json, exist_ok=True)
    os.makedirs(outdir_txt, exist_ok=True)

    n_pages = 0
    # results.json: {filename: [page, ...]} — filenames sort as page order
    for key in sorted(data.keys()):
        val = data[key]
        pages = val if isinstance(val, list) else [val]
        for pageobj in pages:
            n_pages += 1
            page = parse_page(pageobj, n_pages)
            with open(os.path.join(outdir_json, f"page_{n_pages:04d}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(page, f, ensure_ascii=False)
            parts = [rg["text"] for rg in
                     sorted(page["regions"], key=lambda r: r["order"])
                     if rg["text"] and norm_label(rg["label"]) not in DROP]
            with open(os.path.join(outdir_txt, f"page_{n_pages:04d}.txt"),
                      "w", encoding="utf-8") as f:
                f.write("\n\n".join(parts))
    return n_pages


def run_issue(iid, force=False):
    pages_dir = os.path.join(ROOT, "data", "pages", iid)
    if not os.path.isdir(pages_dir):
        print(f"[s02] {iid}: no pages on disk, run s01 first"); return
    if not force and iid not in issues_download_complete():
        print(f"[s02] {iid}: download not confirmed complete in the s01 "
              f"manifest — skipped (use --force to override)"); return
    if os.path.exists(os.path.join(ROOT, "data", "text", iid, "routeA")) and not force:
        print(f"[s02] {iid}: routeA output already exists — skipped"); return
    if not surya_bin("surya_ocr"):
        sys.exit("surya not installed — run scripts/server_setup.sh")
    n = len([f for f in os.listdir(pages_dir) if f.endswith(".png")])
    work = os.path.join(ROOT, "data", "work_surya", iid)
    with stage_timer("s02_layout_ocr", iid, pages=n, extra={"route": "A"}):
        run_surya(pages_dir, work)
        got = adapt(iid, work)
    shutil.rmtree(work, ignore_errors=True)
    print(f"[s02] {iid}: {got} pages adapted (route A)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--force", action="store_true",
                    help="process even without a completed-download record")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"),
                         encoding="utf-8"))
    ids = [i["id"] for i in cfg["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, force=args.force)


if __name__ == "__main__":
    main()
