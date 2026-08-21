#!/usr/bin/env python3
"""Stage 5 — LLM post-correction of the rule-cleaned text. Two backends compared.

  --backend qwen    OpenAI-compatible endpoint (local vLLM lane; free, private)
                    env: PULP_QWEN_BASE_URL, PULP_QWEN_MODEL, PULP_QWEN_KEY
  --backend claude  Anthropic API
                    env: ANTHROPIC_API_KEY, PULP_CLAUDE_MODEL (default haiku-class)

Input : data/text/<id>/rules_<src>/page_NNNN.txt   (default src: routeA)
Output: data/text/<id>/llm_<backend>_<src>/page_NNNN.txt
        data/text/<id>/llm_<backend>_<src>/meta.jsonl   latency/tokens/cost/guard

The model is told to FIX OCR ERRORS ONLY. Guardrail: if the corrected page
differs too much from the input (similarity below GUARD_MIN), the correction is
REJECTED, the rule-cleaned text is kept, and the page is flagged for review.
The LLM must never rewrite the archive.
"""
import argparse
import difflib
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT, load_pulp_env

GUARD_MIN = 0.85   # minimum SequenceMatcher ratio input->output to accept

PROMPT = (
    "The text below is one page of a scanned twentieth-century fiction "
    "magazine, already partly cleaned by rules. Fix ONLY optical character "
    "recognition errors: wrong letters, split or merged words, garbled "
    "punctuation. Keep the original wording, spelling conventions of the "
    "period, names, and paragraph breaks exactly. Do not add, remove, "
    "summarize, modernize, or rephrase anything. If a passage is illegible, "
    "leave it as it is. Return the corrected page only.\n\n"
)

# rough public per-1M-token prices for cost logging; update in one place
COST_PER_M = {"claude": {"in": 1.0, "out": 5.0}, "qwen": {"in": 0.0, "out": 0.0}}


def call_qwen(text):
    base = os.environ["PULP_QWEN_BASE_URL"].rstrip("/")
    model = os.environ["PULP_QWEN_MODEL"]
    body = {"model": model, "temperature": 0.0, "max_tokens": 4096,
            "messages": [{"role": "user", "content": PROMPT + text}]}
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('PULP_QWEN_KEY', 'none')}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    u = out.get("usage", {})
    return (out["choices"][0]["message"]["content"], model,
            u.get("prompt_tokens", 0), u.get("completion_tokens", 0))


def call_claude(text):
    model = os.environ.get("PULP_CLAUDE_MODEL", "claude-haiku-4-5")
    body = {"model": model, "max_tokens": 4096, "temperature": 0.0,
            "messages": [{"role": "user", "content": PROMPT + text}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    u = out.get("usage", {})
    return (out["content"][0]["text"], model,
            u.get("input_tokens", 0), u.get("output_tokens", 0))


def similarity(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def run_issue(iid, backend, src):
    indir = os.path.join(ROOT, "data", "text", iid, f"rules_{src}")
    if not os.path.isdir(indir):
        print(f"[s05] {iid}: no rules_{src} input, run s04 first"); return
    outdir = os.path.join(ROOT, "data", "text", iid, f"llm_{backend}_{src}")
    os.makedirs(outdir, exist_ok=True)
    pages = sorted(f for f in os.listdir(indir)
                   if f.startswith("page_") and f.endswith(".txt"))
    call = call_qwen if backend == "qwen" else call_claude
    metaf = open(os.path.join(outdir, "meta.jsonl"), "a", encoding="utf-8")
    n_rej = 0
    with stage_timer("s05_llm_clean", iid, pages=len(pages),
                     extra={"backend": backend, "src": src}):
        for p in pages:
            dest = os.path.join(outdir, p)
            if os.path.exists(dest):
                continue  # resumable
            text = open(os.path.join(indir, p), encoding="utf-8").read()
            if not text.strip():
                open(dest, "w").write(""); continue
            t0 = time.time()
            for attempt in range(3):
                try:
                    fixed, model, tin, tout = call(text)
                    break
                except Exception as e:
                    print(f"  retry {attempt+1}: {e}"); time.sleep(15 * (attempt + 1))
            else:
                raise RuntimeError(f"{backend} failed on {iid}/{p}")
            sim = similarity(text, fixed)
            accepted = sim >= GUARD_MIN
            if not accepted:
                fixed = text  # keep the rule-cleaned page, flag it
                n_rej += 1
            with open(dest, "w", encoding="utf-8") as f:
                f.write(fixed)
            cost = (tin * COST_PER_M[backend]["in"]
                    + tout * COST_PER_M[backend]["out"]) / 1e6
            metaf.write(json.dumps({
                "page": p, "model": model, "latency_s": round(time.time() - t0, 2),
                "in_tokens": tin, "out_tokens": tout, "usd": round(cost, 5),
                "similarity": round(sim, 3), "accepted": accepted}) + "\n")
            metaf.flush()
    metaf.close()
    print(f"[s05] {iid} ({backend}/{src}): {len(pages)} pages, {n_rej} guarded")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--backend", required=True, choices=["qwen", "claude"])
    ap.add_argument("--src", default="routeA", choices=["ia", "routeA", "routeB"])
    args = ap.parse_args()
    load_pulp_env()
    need = (["PULP_QWEN_BASE_URL", "PULP_QWEN_MODEL"] if args.backend == "qwen"
            else ["ANTHROPIC_API_KEY"])
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing environment values: {', '.join(missing)} — "
                 f"fill them in ~/shared/khj/.pulp_env")
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"),
                         encoding="utf-8"))
    ids = [i["id"] for i in cfg["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, args.backend, args.src)


if __name__ == "__main__":
    main()
