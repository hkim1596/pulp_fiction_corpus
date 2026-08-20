#!/usr/bin/env python3
"""Stage 2 — route A: layout detection + region OCR (American Stories style).

Driver around the Surya OCR toolkit (layout + text detection + recognition with
reading order), chosen because it is one install, GPU-light (fits beside the
extraction daemon), and returns line boxes — every line of text stays anchored
to page coordinates, which the website's overlay viewer draws.

Input : data/pages/<id>/page_NNNN.png
Output: data/layout/<id>/page_NNNN.json   regions + lines with boxes + labels
        data/text/<id>/routeA/page_NNNN.txt  text in reading order,
                                              Picture/Table regions dropped,
                                              region labels kept in the JSON

Surya's Python API moves between versions, so this driver calls the CLI and
adapts its JSON. First run happens on the server (scripts/server_setup.sh
installs it); if the CLI or its output format differs, fix THIS file only —
downstream stages read our page JSON schema, never Surya's.

Our page JSON schema (consumed by s04/s07/webapp):
{
  "page": 12, "width": 1650, "height": 2200,
  "regions": [
    {"label": "Text", "bbox": [x0,y0,x1,y1], "order": 3,
     "lines": [{"bbox": [..], "text": "...", "conf": 0.97}, ...]},
    ...]
}
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT

DROP_LABELS = {"Picture", "Figure", "Table", "Page-footer", "Page-header"}
KEEP_TEXT_IN_JSON_ONLY = {"Caption", "Footnote"}


def surya_bin(name):
    """Find a surya command even when ~/.local/bin is not on PATH
    (pip --user installs land there; rtx run tmux shells miss it)."""
    p = shutil.which(name)
    if p:
        return p
    cand = os.path.expanduser(f"~/.local/bin/{name}")
    return cand if os.path.exists(cand) else None


def run_surya(pages_dir, work_dir):
    """Run surya OCR (detection+recognition, layout) over an image folder."""
    os.makedirs(work_dir, exist_ok=True)
    subprocess.run([surya_bin("surya_ocr"), pages_dir, "--output_dir",
                    os.path.join(work_dir, "ocr")], check=True)
    subprocess.run([surya_bin("surya_layout"), pages_dir, "--output_dir",
                    os.path.join(work_dir, "layout")], check=True)


def _find_results_json(base):
    for root, _dirs, files in os.walk(base):
        for f in files:
            if f.endswith(".json"):
                return os.path.join(root, f)
    return None


def bbox_inside(inner, outer, tol=8):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def adapt(iid, work_dir):
    """Merge surya ocr lines into surya layout regions -> our page JSON."""
    ocr_json = _find_results_json(os.path.join(work_dir, "ocr"))
    lay_json = _find_results_json(os.path.join(work_dir, "layout"))
    if not (ocr_json and lay_json):
        raise RuntimeError("surya output json not found — inspect work dir")
    ocr = json.load(open(ocr_json, encoding="utf-8"))
    lay = json.load(open(lay_json, encoding="utf-8"))

    outdir_json = os.path.join(ROOT, "data", "layout", iid)
    outdir_txt = os.path.join(ROOT, "data", "text", iid, "routeA")
    os.makedirs(outdir_json, exist_ok=True)
    os.makedirs(outdir_txt, exist_ok=True)

    # both files are keyed by image name (without extension) in current surya
    for pageno, key in enumerate(sorted(ocr.keys()), 1):
        o = ocr[key][0] if isinstance(ocr[key], list) else ocr[key]
        l = lay.get(key)
        l = (l[0] if isinstance(l, list) else l) if l else {"bboxes": []}
        lines = [{"bbox": tl["bbox"], "text": tl["text"],
                  "conf": round(tl.get("confidence", 0.0), 3)}
                 for tl in o.get("text_lines", [])]
        regions = []
        for i, rb in enumerate(l.get("bboxes", [])):
            regions.append({"label": rb.get("label", "Text"),
                            "bbox": rb["bbox"],
                            "order": rb.get("position", i),
                            "lines": []})
        unassigned = []
        for ln in lines:
            for rg in regions:
                if bbox_inside(ln["bbox"], rg["bbox"]):
                    rg["lines"].append(ln)
                    break
            else:
                unassigned.append(ln)
        if unassigned:
            regions.append({"label": "Text", "bbox": [0, 0, 0, 0],
                            "order": len(regions), "lines": unassigned})
        page = {"page": pageno,
                "width": o.get("image_bbox", [0, 0, 0, 0])[2],
                "height": o.get("image_bbox", [0, 0, 0, 0])[3],
                "regions": regions}
        with open(os.path.join(outdir_json, f"page_{pageno:04d}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(page, f, ensure_ascii=False)

        # reading-order text: regions by order, skip pictures/furniture
        parts = []
        for rg in sorted(regions, key=lambda r: r["order"]):
            if rg["label"] in DROP_LABELS or rg["label"] in KEEP_TEXT_IN_JSON_ONLY:
                continue
            parts.extend(ln["text"] for ln in rg["lines"])
        with open(os.path.join(outdir_txt, f"page_{pageno:04d}.txt"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(parts))
    return len(ocr)


def run_issue(iid):
    pages_dir = os.path.join(ROOT, "data", "pages", iid)
    if not os.path.isdir(pages_dir):
        print(f"[s02] {iid}: no pages, run s01 first"); return
    if not (surya_bin("surya_ocr") and surya_bin("surya_layout")):
        sys.exit("surya not installed — run scripts/server_setup.sh (GPU server)")
    n = len([f for f in os.listdir(pages_dir) if f.endswith(".png")])
    work = os.path.join(ROOT, "data", "work_surya", iid)
    with stage_timer("s02_layout_ocr", iid, pages=n, extra={"route": "A"}):
        run_surya(pages_dir, work)
        adapt(iid, work)
    shutil.rmtree(work, ignore_errors=True)   # inodes: keep only adapted output
    print(f"[s02] {iid}: {n} pages done (route A)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"),
                         encoding="utf-8"))
    ids = [i["id"] for i in cfg["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid)


if __name__ == "__main__":
    main()
