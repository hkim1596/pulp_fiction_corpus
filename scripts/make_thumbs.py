#!/usr/bin/env python3
"""Pre-build the small page previews the website's workbench uses.

The site builds each preview on first request anyway; running this once
after a deploy just means no visitor ever waits for the first build.
Safe to rerun: existing previews are kept. Run on the server:
    cd ~/shared/khj/pulp_fiction_corpus && python3 scripts/make_thumbs.py
"""
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(ROOT, "data", "pages")
THUMBS = os.path.join(ROOT, "data", "thumbs")
THUMB_PX = 900  # keep in step with webapp/app.py

from PIL import Image  # noqa: E402  (import after docstring on purpose)


def main():
    pngs = sorted(glob.glob(os.path.join(PAGES, "*", "page_*.png")))
    made = kept = 0
    for i, src in enumerate(pngs, 1):
        iid = os.path.basename(os.path.dirname(src))
        tp = os.path.join(THUMBS, iid, os.path.basename(src)[:-4] + ".jpg")
        if os.path.exists(tp):
            kept += 1
        else:
            os.makedirs(os.path.dirname(tp), exist_ok=True)
            im = Image.open(src)
            if im.mode != "RGB":
                im = im.convert("RGB")
            if im.height > THUMB_PX:
                w = max(1, round(im.width * THUMB_PX / im.height))
                im = im.resize((w, THUMB_PX))
            im.save(tp + ".mkpart", "JPEG", quality=78)
            os.replace(tp + ".mkpart", tp)
            made += 1
        if i % 100 == 0:
            print(f"{i}/{len(pngs)} pages (built {made}, kept {kept})",
                  flush=True)
    print(f"THUMBS-DONE: {len(pngs)} pages, built {made}, kept {kept}")


if __name__ == "__main__":
    main()
