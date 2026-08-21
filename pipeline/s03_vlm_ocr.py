#!/usr/bin/env python3
"""Stage 3 — route B: whole-page reading by a vision LLM.

Sends each page image to an OpenAI-compatible vision endpoint (a vLLM lane on
the rtx6000 serving e.g. olmOCR or a Qwen-VL model) and stores the transcription.
The model handles two-column reading order itself; coordinates are not produced
(that is route A's advantage — the pilot compares them).

Input : data/pages/<id>/page_NNNN.png
Output: data/text/<id>/routeB/page_NNNN.txt
        data/text/<id>/routeB/meta.jsonl   (per page: latency, tokens, model)

Endpoint config comes from environment (put it in ~/shared/khj/.pulp_env):
  PULP_VLM_BASE_URL   e.g. http://127.0.0.1:8010/v1
  PULP_VLM_MODEL      served model name
  PULP_VLM_KEY        any string for vLLM (it ignores keys) unless a real API
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT, load_pulp_env

PROMPT = (
    "This is a scanned page from a twentieth-century fiction magazine. "
    "Transcribe ALL printed text on the page in natural reading order "
    "(for two columns: left column top to bottom, then right column). "
    "Preserve paragraph breaks. Do not describe images. Do not add, omit, "
    "summarize, correct, or modernize anything - transcribe exactly what is "
    "printed, including running heads and page numbers on their own lines. "
    "Output the transcription only."
)


def call_vlm(base_url, model, key, png_path, max_retries=3):
    b64 = base64.b64encode(open(png_path, "rb").read()).decode()
    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key or 'none'}"},
    )
    for attempt in range(max_retries):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=600) as r:
                out = json.load(r)
            dt = time.time() - t0
            text = out["choices"][0]["message"]["content"]
            usage = out.get("usage", {})
            return text, {"latency_s": round(dt, 2),
                          "prompt_tokens": usage.get("prompt_tokens"),
                          "completion_tokens": usage.get("completion_tokens")}
        except Exception as e:
            print(f"  retry {attempt+1}: {e}")
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"VLM failed on {png_path}")


def run_issue(iid, base_url, model, key):
    pages_dir = os.path.join(ROOT, "data", "pages", iid)
    if not os.path.isdir(pages_dir):
        print(f"[s03] {iid}: no pages, run s01 first"); return
    outdir = os.path.join(ROOT, "data", "text", iid, "routeB")
    os.makedirs(outdir, exist_ok=True)
    pngs = sorted(f for f in os.listdir(pages_dir) if f.endswith(".png"))
    metaf = open(os.path.join(outdir, "meta.jsonl"), "a", encoding="utf-8")
    with stage_timer("s03_vlm_ocr", iid, pages=len(pngs),
                     extra={"route": "B", "model": model}):
        for png in pngs:
            dest = os.path.join(outdir, png.replace(".png", ".txt"))
            if os.path.exists(dest):
                continue  # resumable
            text, meta = call_vlm(base_url, model, key,
                                  os.path.join(pages_dir, png))
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
            meta.update({"page": png, "model": model})
            metaf.write(json.dumps(meta) + "\n")
            metaf.flush()
    metaf.close()
    print(f"[s03] {iid}: {len(pngs)} pages done (route B, {model})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    args = ap.parse_args()
    load_pulp_env()
    base_url = os.environ.get("PULP_VLM_BASE_URL")
    model = os.environ.get("PULP_VLM_MODEL")
    key = os.environ.get("PULP_VLM_KEY", "")
    if not (base_url and model):
        sys.exit("set PULP_VLM_BASE_URL and PULP_VLM_MODEL "
                 "(see ~/shared/khj/.pulp_env; HANDBOOK.md §2)")
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"),
                         encoding="utf-8"))
    ids = [i["id"] for i in cfg["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, base_url, model, key)


if __name__ == "__main__":
    main()
