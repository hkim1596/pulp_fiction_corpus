#!/usr/bin/env python3
"""Stage 1b — split the IA baseline OCR into real pages.

The plain _djvu.txt from these items carries no page separators (s04 saw
"1 pages" per issue). IA's positional OCR (hOCR / chOCR, downloaded by s01 as
data/raw/<id>/ia_hocr.html[.gz]) has explicit page divisions, so this stage
parses it and writes the baseline as a normal per-page stage:

    data/text/<id>/ia/page_NNNN.txt

After this, "ia" behaves like every other stage: per-page panels on the site,
proper multi-page input for the rules pass, page-aligned comparisons.
Issues with no hOCR file are reported and keep the single-block fallback.
"""
import glob
import gzip
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT


class HocrPages(HTMLParser):
    """Collect text per ocr_page: lines from ocr_line/ocrx_line spans,
    words from ocrx_word spans. Works for hOCR and chOCR."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pages = []          # list of list of lines
        self.cur_lines = None
        self.cur_words = None
        self.depth_in_word = 0

    @staticmethod
    def _cls(attrs):
        return dict(attrs).get("class", "") or ""

    def handle_starttag(self, tag, attrs):
        c = self._cls(attrs)
        if "ocr_page" in c:
            self.cur_lines = []
            self.pages.append(self.cur_lines)
        elif ("ocr_line" in c or "ocrx_line" in c or "ocr_header" in c
              or "ocr_textfloat" in c or "ocr_caption" in c):
            if self.cur_lines is None:
                self.cur_lines = []
                self.pages.append(self.cur_lines)
            self.cur_words = []
            self.cur_lines.append(self.cur_words)
        elif "ocrx_word" in c or "ocrx_cinfo" in c:
            self.depth_in_word += 1

    def handle_endtag(self, tag):
        pass  # word depth handled loosely; data lands in the current line

    def handle_data(self, data):
        if self.cur_words is not None and data.strip():
            self.cur_words.append(data.strip())

    def result(self):
        out = []
        for lines in self.pages:
            out.append("\n".join(" ".join(w for w in words if w)
                                 for words in lines if words))
        return out


def read_hocr(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    p = HocrPages()
    p.feed(raw)
    return p.result()


def run_issue(iid):
    rawdir = os.path.join(ROOT, "data", "raw", iid)
    hocr = None
    for cand in ("ia_hocr.html", "ia_hocr.html.gz"):
        if os.path.exists(os.path.join(rawdir, cand)):
            hocr = os.path.join(rawdir, cand)
            break
    if not hocr:
        print(f"[s01b] {iid}: no hOCR file — IA text stays single-block")
        return
    with stage_timer("s01b_ia_pages", iid):
        pages = read_hocr(hocr)
        outdir = os.path.join(ROOT, "data", "text", iid, "ia")
        os.makedirs(outdir, exist_ok=True)
        for i, text in enumerate(pages, 1):
            with open(os.path.join(outdir, f"page_{i:04d}.txt"), "w",
                      encoding="utf-8") as f:
                f.write(text)
    n_png = len(glob.glob(os.path.join(ROOT, "data", "pages", iid, "*.png")))
    flag = "" if abs(len(pages) - n_png) <= 2 else \
        f"  (NOTE: {n_png} scan pages — check alignment)"
    print(f"[s01b] {iid}: {len(pages)} IA pages written{flag}")


def main():
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"),
                         encoding="utf-8"))
    for issue in cfg["issues"]:
        run_issue(issue["id"])


if __name__ == "__main__":
    main()
