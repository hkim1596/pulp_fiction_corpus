#!/usr/bin/env python3
"""Create a small fake issue so the website can be seen before any real data.

Writes demo pages, layout boxes, stage texts, timing rows for issue id
demo_1930_01 (added to nothing — the site lists config issues only, so the demo
issue is injected by temporarily using --demo-config). Local development only;
never run on the server against the real config.

Usage:
    python3 make_demo_data.py           # writes data under the repo
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IID = "demo_1930_01"

RAW_OCR = """ASTOUNDING STORIES 17

The Sky Wanderer

By  RAY   COLEMAN

THE  great  ship  hung  mo-
tionless  above  the  frozen
plain.  Cap|ain  Marsh  stared
through   the   quartz   port,
watching  the  grey  clouds  boil
against  the  m0untains.

"We  can't  stay  here,"  said
Dorn.  "The  fuel  wi11  not  last
another  week."

Marsh  said  nothing.  He  was
think-
ing  of  the  city  they  had  left
behind,  and  of  the  long  road
home."""

RULES_TXT = """The Sky Wanderer

By RAY COLEMAN

THE great ship hung mo- tionless above the frozen plain. Cap|ain Marsh stared through the quartz port, watching the grey clouds boil against the m0untains.

"We can't stay here," said Dorn. "The fuel wi11 not last another week."

Marsh said nothing. He was thinking of the city they had left behind, and of the long road home."""

LLM_TXT = """The Sky Wanderer

By RAY COLEMAN

THE great ship hung motionless above the frozen plain. Captain Marsh stared through the quartz port, watching the grey clouds boil against the mountains.

"We can't stay here," said Dorn. "The fuel will not last another week."

Marsh said nothing. He was thinking of the city they had left behind, and of the long road home."""


def make_page_png(path):
    from PIL import Image, ImageDraw
    W, H = 760, 1100
    im = Image.new("RGB", (W, H), (243, 234, 217))
    d = ImageDraw.Draw(im)
    d.rectangle([60, 40, 700, 90], outline=(60, 50, 40))       # running head
    d.text((80, 55), "ASTOUNDING STORIES        17", fill=(60, 50, 40))
    d.rectangle([90, 120, 670, 190], outline=(122, 48, 32))    # story title
    d.text((150, 145), "THE SKY WANDERER - by Ray Coleman", fill=(60, 50, 40))
    for col_x in (70, 400):                                    # two text columns
        y = 230
        while y < 980:
            d.line([col_x, y, col_x + 290, y], fill=(120, 105, 85))
            y += 16
    d.rectangle([60, 210, 380, 1000], outline=(122, 48, 32))
    d.rectangle([390, 210, 710, 1000], outline=(122, 48, 32))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)
    return W, H


def main():
    # pages
    W = H = None
    for n in (1, 2, 3):
        W, H = make_page_png(os.path.join(ROOT, "data", "pages", IID,
                                          f"page_{n:04d}.png"))
    # layout jsons with region-level text, so the article assembly (s07)
    # can run on the demo in --mock mode
    ldir = os.path.join(ROOT, "data", "layout", IID)
    os.makedirs(ldir, exist_ok=True)
    pages_lay = [
        [  # page 1: header, story title, byline+body, ad
            {"label": "PageHeader", "bbox": [60, 40, 700, 90], "order": 0,
             "text": "ASTOUNDING STORIES    17", "lines": []},
            {"label": "SectionHeader", "bbox": [90, 120, 670, 190], "order": 1,
             "text": "THE SKY WANDERER", "lines": []},
            {"label": "Text", "bbox": [60, 210, 380, 1000], "order": 2,
             "text": "By RAY COLEMAN\n\nTHE great ship hung mo-\ntionless above the frozen\nplain.", "lines": []},
            {"label": "Text", "bbox": [390, 210, 710, 1000], "order": 3,
             "text": "ADVERTISEMENT $1 LEARN RADIO AT HOME. AMAZING OFFER.", "lines": []},
        ],
        [  # page 2: header, continuation of the story
            {"label": "PageHeader", "bbox": [60, 40, 700, 90], "order": 0,
             "text": "18    ASTOUNDING STORIES", "lines": []},
            {"label": "Text", "bbox": [60, 210, 710, 1000], "order": 1,
             "text": "ing of the city they had left be-\nhind, and of the long road home.", "lines": []},
        ],
        [  # page 3: header, a second story
            {"label": "PageHeader", "bbox": [60, 40, 700, 90], "order": 0,
             "text": "ASTOUNDING STORIES    19", "lines": []},
            {"label": "SectionHeader", "bbox": [90, 120, 670, 190], "order": 1,
             "text": "THE MOON POOL MYSTERY", "lines": []},
            {"label": "Text", "bbox": [60, 210, 710, 1000], "order": 2,
             "text": "By A. K. BARNES\n\nNobody believed the diver's story\nat first.", "lines": []},
        ],
    ]
    for n, regions in enumerate(pages_lay, 1):
        json.dump({"page": n, "width": W, "height": H, "regions": regions},
                  open(os.path.join(ldir, f"page_{n:04d}.json"), "w"))

    # stage texts
    def put(stage, text):
        d = os.path.join(ROOT, "data", "text", IID, stage)
        os.makedirs(d, exist_ok=True)
        for n in (1, 2, 3):
            open(os.path.join(d, f"page_{n:04d}.txt"), "w").write(text)

    put("routeA", RAW_OCR)
    put("rules_routeA", RULES_TXT)
    put("llm_claude_routeA", LLM_TXT)
    put("llm_qwen_routeA", LLM_TXT.replace("motionless", "motionless,"))

    # per-page meta so the panel headers show model / latency / cost
    for stage, model, lat, usd in [
        ("llm_claude_routeA", "claude-haiku-4-5", 3.1, 0.0042),
        ("llm_qwen_routeA", "qwen3.5-9b", 5.8, 0.0),
    ]:
        mp = os.path.join(ROOT, "data", "text", IID, stage, "meta.jsonl")
        with open(mp, "w") as f:
            for nn in (1, 2, 3):
                f.write(json.dumps({
                    "page": f"page_{nn:04d}.txt", "model": model,
                    "latency_s": lat, "in_tokens": 780, "out_tokens": 760,
                    "usd": usd, "similarity": 0.97, "accepted": True}) + "\n")

    # ia baseline
    rdir = os.path.join(ROOT, "data", "raw", IID)
    os.makedirs(rdir, exist_ok=True)
    open(os.path.join(rdir, "ia_text.txt"), "w").write(
        "\x0c".join([RAW_OCR.replace("Cap|ain", "CapIain")] * 3))

    # timings
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "timings.jsonl"), "a") as f:
        for stage, sec in [("s01_download", 74.2), ("s02_layout_ocr", 128.9),
                           ("s04_rules", 1.4), ("s05_llm_clean", 96.0)]:
            f.write(json.dumps({"ts": "2026-08-20T12:00:00", "stage": stage,
                                "issue": IID, "pages": 3, "seconds": sec}) + "\n")

    # demo config (used with PULP_CONFIG override if ever needed)
    demo_cfg = {"approved": True, "issues": [{
        "id": IID, "ia_identifier": "demo_item", "magazine": "Demo Magazine",
        "cover_date": "1930-01", "genre": "science fiction", "format": "pulp",
        "gold": None, "why": "website demo"}]}
    json.dump(demo_cfg, open(os.path.join(ROOT, "config", "demo_config.json"),
                             "w"), indent=1)
    print("demo data written; to view: swap config or add the demo issue to "
          "pilot_issues.json temporarily")


if __name__ == "__main__":
    main()
