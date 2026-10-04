#!/usr/bin/env python3
"""s12 — box linking by a language model (Heejin, 4 October 2026: "After layout detection let the high performance
LLM read the content and decide whether a box is connected to the next one or not. If the local LLM is not sure
about it, let it use Fable or Opus API. If it is still uncertain let it flag it and a human solve the case.")

For every page of an issue, in order, the model is shown the page's text boxes (the layout detector's regions in
reading order, with their labels, positions and the head and tail of their text), the piece that was open at the
end of the previous page, and the rules engine's own proposal (s08) as a hint, and answers for every box:

    joins   previous | new | furniture | advert | caption | notice
            previous = continues the piece of the box before it (for the first box: the piece open from the page before)
            new      = a new piece begins in this box (title and author when printed)
            furniture = running head, page number, magazine name
            advert   = advertising
            caption  = an illustration caption, a pull-quote, a type label (paratext of the open piece)
            notice   = "continued on page 98", "the end", a next-issue line (paratext of the open piece)
    kind    story | serial | poem | article | department | letters | contents | filler | ad | other   (for new)
    title, author                                                                             (for new)
    confidence 0.0–1.0, why

Three tiers (settings.llm_link): the local lane first (a vLLM server on GPU 2, an OpenAI-style endpoint); a page
with any box under thresholds.accept_local is asked again from the Claude API with the page image attached
(escalate.model, Opus 5.5 by default; Fable 5.1 by setting); a page with any box still under
thresholds.accept_api is flagged for a person (flags.jsonl; the workbench on the site). Every answer is kept
with its provenance (tier, model, tokens, seconds, cost); the API spend is counted against escalate.budget_usd
and the stage stops escalating when the budget is spent (the pages are then flagged "budget").

Output, per issue, in the rules assembly's record shape so that the audits, the accuracy report and the export
read it unchanged:
    data/assembly_v2/llm/<id>/pages.jsonl    one line per page: the decisions and their provenance
    data/assembly_v2/llm/<id>/articles.json  the records built from the chains of "previous" links
    data/assembly_v2/llm/<id>/flags.jsonl    the pages for a person, with the boxes in doubt
    data/assembly_v2/llm/<id>/compare.json   agreement with the rules engine, box by box
    data/corpus/llm_link_spend.json          the API spend so far

    python3 pipeline/s12_llm_link.py --issue <id>
    python3 pipeline/s12_llm_link.py --trial 100          # the first 100 assembled issues, with a report
    python3 pipeline/s12_llm_link.py --dry-run --issue <id> --page 5   # print the prompt, call nothing
    python3 pipeline/s12_llm_link.py --selftest
"""
import argparse
import base64
import glob
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import ROOT, settings, mark, has, all_states, event, log, write_json_atomic, issue_dirs  # noqa: E402
from timing_util import stage_timer, load_pulp_env  # noqa: E402

OUT_VARIANT = "llm"
_spend_lock = threading.Lock()
SPEND_PATH = os.path.join(ROOT, "data", "corpus", "llm_link_spend.json")

SYSTEM = """You are assembling the contents of a scanned American fiction magazine (a pulp, 1890-1955) from the text boxes a layout detector found on each page. The boxes are given in reading order. Decide for every box whether it continues the piece of the box before it, or begins a new piece, or is something else, and say how sure you are.

Rules of the magazines: a story or article usually opens with a display title, often a by-line ("By John Smith"), sometimes a type label ("A Complete Novelet") and a teaser blurb, then body text in columns; it runs over several pages and may be interrupted by a page of advertising, after which it continues; running heads (the magazine's name, the story's title at the top of a page) and page numbers are furniture; "(Continued on page 98)" and "THE END" are notices; the text under an illustration is a caption. A box continues the previous piece when its text carries on the sentence or the narrative; a mid-sentence start is a strong sign of continuation.

Answer with JSON only, no prose before or after, in this exact shape:
{"boxes": [{"k": 1, "joins": "previous|new|furniture|advert|caption|notice", "kind": "story|serial|poem|article|department|letters|contents|filler|ad|other", "title": "...or null", "author": "...or null", "confidence": 0.0, "why": "a few words"}], "page_note": "anything odd about the page, or null"}
confidence is your probability that the "joins" decision is right. Use the rule-based proposal as a hint, not as truth: when the text shows otherwise, say so with your reasons."""


# ----------------------------------------------------------------------------------------------------------------
# inputs
# ----------------------------------------------------------------------------------------------------------------
def load_pages(iid):
    pages = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "layout", iid, "page_*.json"))):
        p = json.load(open(f, encoding="utf-8"))
        p["regions"] = sorted(p["regions"], key=lambda r: r.get("order", 0))
        pages[p["page"]] = p
    return pages


def load_rules(iid):
    """The rules engine's view: region key -> (record, role) and the furniture keys."""
    p = os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "articles.json")
    if not os.path.exists(p):
        return {}, set(), None
    doc = json.load(open(p, encoding="utf-8"))
    owner, furn = {}, set()
    for rec in doc.get("articles", []):
        for fr in rec.get("fragments", []):
            for i in fr["region_ids"]:
                owner[f"{fr['page']}:{i}"] = (rec, rec.get("roles", {}).get(f"{fr['page']}:{i}"))
    for f in doc.get("furniture", []):
        furn.add(f"{f['page']}:{f['idx']}")
    return owner, furn, doc


def region_text(r):
    return (r.get("text") or "").strip()


def clip(text, head, tail):
    t = " ".join(text.split())
    if len(t) <= head + tail + 5:
        return t
    return t[:head] + " […] " + t[-tail:]


def rules_hint(key, owner, furn, prev_key):
    if key in furn:
        return "furniture"
    if key not in owner:
        return "not assigned"
    rec, role = owner[key]
    prev = owner.get(prev_key)
    same = prev is not None and prev[0]["article_id"] == rec["article_id"]
    what = rec["type"] + (f' "{rec.get("title")}"' if rec.get("title") else "") + (f" by {rec.get('author')}" if rec.get("author") else "")
    if role in ("teaser", "caption", "subtitle", "note"):
        return f"{role} of the {what}"
    if same:
        return f"continues the {what}"
    return f"begins the {what}" if rec["type"] != "ad" else f"advertisement ({rec.get('ad_class') or 'ad'})"


def page_prompt(iid, meta, pno, page, pages, owner, furn, open_piece, ctx):
    """The user message for one page."""
    lines = [f"Magazine: {meta.get('magazine', '?')}, issue dated {meta.get('cover_date', '?')}. Scan page {pno} of {len(pages)}."]
    if open_piece:
        lines.append(f"The piece open at the end of the previous page: {open_piece['kind']} \"{open_piece.get('title') or '?'}\""
                     + (f" by {open_piece['author']}" if open_piece.get("author") else "")
                     + f". Its last words: \"{clip(open_piece.get('tail') or '', 0, ctx['prev_page_tail_chars'])}\"")
    else:
        lines.append("No piece is open from the previous page (this is the first page, or the previous page ended a piece).")
    lines.append("")
    lines.append("The boxes of this page, in reading order (position as percent of the page width and height):")
    W, H = max(1, page.get("width") or 1), max(1, page.get("height") or 1)
    prev_key = None
    k = 0
    keymap = {}
    for i, r in enumerate(page["regions"]):
        t = region_text(r)
        if not t:
            continue
        k += 1
        key = f"{pno}:{i}"
        keymap[k] = i
        x0, y0, x1, y1 = (r.get("bbox") or [0, 0, 0, 0])[:4]
        pos = f"x {100 * x0 / W:.0f}-{100 * x1 / W:.0f}%, y {100 * y0 / H:.0f}-{100 * y1 / H:.0f}%"
        hint = rules_hint(key, owner, furn, prev_key)
        lines.append(f"[{k}] label={r.get('label', 'Text')} at {pos}; rule-based proposal: {hint}")
        lines.append(f"    text: \"{clip(t, ctx['text_head_chars'], ctx['text_tail_chars'])}\"")
        prev_key = key
    lines.append("")
    lines.append(f"Answer for every box 1 to {k}.")
    return "\n".join(lines), keymap


# ----------------------------------------------------------------------------------------------------------------
# the two tiers of models
# ----------------------------------------------------------------------------------------------------------------
def _post_json(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def local_cfg():
    """settings.llm_link.local, with PULP_LLM_BASE_URL / PULP_LLM_MODEL from the environment on top (a trial of
    another lane or model without touching the tracked settings file on the server)."""
    cfg = dict(settings()["llm_link"]["local"])
    if os.environ.get("PULP_LLM_BASE_URL"):
        cfg["base_url"] = os.environ["PULP_LLM_BASE_URL"]
    if os.environ.get("PULP_LLM_MODEL"):
        cfg["model"] = os.environ["PULP_LLM_MODEL"]
    return cfg


def ask_local(user_text, cfg):
    """The local lane: an OpenAI-style chat endpoint (vLLM). Returns (text, usage, seconds)."""
    body = {"model": cfg["model"], "temperature": cfg.get("temperature", 0), "max_tokens": cfg.get("max_tokens", 1500),
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_text}],
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}          # Qwen's thinking mode returns nothing otherwise (pilot, s05)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {cfg.get('api_key') or 'EMPTY'}"}
    t0 = time.time()
    last = None
    for attempt in range(3):
        try:
            d = _post_json(cfg["base_url"].rstrip("/") + "/chat/completions", body, headers, cfg.get("timeout_s", 300))
            txt = d["choices"][0]["message"]["content"] or ""
            return txt, d.get("usage") or {}, round(time.time() - t0, 2)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:300]!r}"
            if e.code == 400 and ("response_format" in last or "chat_template_kwargs" in last or "guided" in last):
                body.pop("response_format", None); body.pop("chat_template_kwargs", None)   # a lane without these options
        except Exception as e:
            last = repr(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"local lane failed: {last}")


def page_image_b64(iid, pno, height_px):
    from PIL import Image
    p = os.path.join(issue_dirs(iid)["pages"], f"page_{pno:04d}.jpg")
    if not os.path.exists(p):
        return None
    im = Image.open(p)
    if im.height > height_px:
        im = im.resize((round(im.width * height_px / im.height), height_px))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def ask_api(user_text, iid, pno, cfg, model=None):
    """The Claude API, with the page image when the settings say so. Returns (text, usage, seconds, cost_usd)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (the environment file ~/shared/khj/.pulp_env)")
    model = model or cfg["model"]
    content = []
    if cfg.get("with_image", True):
        b64 = page_image_b64(iid, pno, cfg.get("image_height_px", 1400))
        if b64:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
            user_text = "The page image is attached; the boxes below are the text the detector read from it.\n\n" + user_text
    content.append({"type": "text", "text": user_text})
    body = {"model": model, "max_tokens": cfg.get("max_tokens", 1500), "temperature": 0, "system": SYSTEM,
            "messages": [{"role": "user", "content": content}]}
    headers = {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
    t0 = time.time()
    last = None
    for attempt in range(4):
        try:
            d = _post_json("https://api.anthropic.com/v1/messages", body, headers, cfg.get("timeout_s", 180))
            txt = "".join(c.get("text", "") for c in d.get("content", []) if c.get("type") == "text")
            u = d.get("usage") or {}
            cost = (u.get("input_tokens", 0) * cfg.get("price_in_per_mtok", 0) + u.get("output_tokens", 0) * cfg.get("price_out_per_mtok", 0)) / 1e6
            return txt, u, round(time.time() - t0, 2), round(cost, 5)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:300]!r}"
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:
            last = repr(e)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"api failed: {last}")


def parse_answer(txt, n_boxes):
    """The JSON in the model's answer; lenient about text around it. Returns the dict or None."""
    if not txt:
        return None
    s = txt.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    boxes = d.get("boxes")
    if not isinstance(boxes, list):
        return None
    out = {}
    for b in boxes:
        try:
            k = int(b.get("k"))
        except Exception:
            continue
        if not (1 <= k <= n_boxes):
            continue
        joins = str(b.get("joins") or "").strip().lower()
        if joins not in ("previous", "new", "furniture", "advert", "caption", "notice"):
            continue
        try:
            conf = max(0.0, min(1.0, float(b.get("confidence"))))
        except Exception:
            conf = 0.0
        out[k] = {"joins": joins, "kind": (b.get("kind") or None), "title": (b.get("title") or None) if joins == "new" else None,
                  "author": (b.get("author") or None) if joins == "new" else None, "confidence": conf, "why": (b.get("why") or "")[:200]}
    if len(out) < n_boxes:
        return None            # an incomplete answer counts as no answer: the tier above is asked
    return {"boxes": out, "page_note": d.get("page_note")}


# ----------------------------------------------------------------------------------------------------------------
# spend
# ----------------------------------------------------------------------------------------------------------------
def spend(add=0.0, add_calls=0):
    with _spend_lock:
        d = {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0}
        if os.path.exists(SPEND_PATH):
            try:
                d.update(json.load(open(SPEND_PATH, encoding="utf-8")))
            except Exception:
                pass
        if add or add_calls:
            d["usd"] = round(d["usd"] + add, 4)
            d["calls"] += add_calls
            d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            write_json_atomic(SPEND_PATH, d)
        return d


# ----------------------------------------------------------------------------------------------------------------
# one issue
# ----------------------------------------------------------------------------------------------------------------
def link_issue(iid, meta, dry_run_page=None, log_fn=None):
    log_fn = log_fn or (lambda m: log("s12", m))
    cfg = settings()["llm_link"]
    pages = load_pages(iid)
    if not pages:
        log_fn(f"{iid}: no layout pages"); return None
    owner, furn, rules_doc = load_rules(iid)
    out_dir = os.path.join(ROOT, "data", "assembly_v2", OUT_VARIANT, iid)
    os.makedirs(out_dir, exist_ok=True)
    open_piece = None
    page_records = []
    flags = []
    n_api = n_flag = 0
    cost = 0.0
    t_start = time.time()
    for pno in sorted(pages):
        page = pages[pno]
        prompt, keymap = page_prompt(iid, meta, pno, page, pages, owner, furn, open_piece, cfg["context"])
        n_boxes = len(keymap)
        if dry_run_page is not None:
            if pno == dry_run_page:
                print(SYSTEM); print("\n----- user -----\n"); print(prompt)
                return None
            continue
        if n_boxes == 0:
            page_records.append({"page": pno, "boxes": {}, "tier": "none", "n_boxes": 0})
            continue
        rec = {"page": pno, "n_boxes": n_boxes, "keymap": {str(k): i for k, i in keymap.items()}}
        # tier 1: the local lane
        ans = None
        try:
            lc = local_cfg()
            txt, usage, secs = ask_local(prompt, lc)
            ans = parse_answer(txt, n_boxes)
            rec["local"] = {"model": lc["model"], "seconds": secs, "usage": usage, "parsed": ans is not None, "raw": None if ans else txt[:600]}
        except Exception as e:
            rec["local"] = {"error": str(e)[:300]}
        tier = "local"
        low = (ans is None) or min(b["confidence"] for b in ans["boxes"].values()) < cfg["thresholds"]["accept_local"]
        # tier 2: the API, with the page image
        if low:
            if spend()["usd"] >= cfg["escalate"]["budget_usd"]:
                rec["api"] = {"skipped": "budget spent"}
            else:
                try:
                    txt, usage, secs, c = ask_api(prompt, iid, pno, cfg["escalate"])
                    ans2 = parse_answer(txt, n_boxes)
                    spend(c, 1); cost += c; n_api += 1
                    rec["api"] = {"model": cfg["escalate"]["model"], "seconds": secs, "usage": usage, "cost_usd": c, "parsed": ans2 is not None}
                    if ans2 is not None:
                        rec["local_answer"] = ans["boxes"] if ans else None
                        ans, tier = ans2, "api"
                except Exception as e:
                    rec["api"] = {"error": str(e)[:300]}
        if ans is None:
            # nothing usable from either tier: the rules' view stands for this page and the page is flagged
            ans = {"boxes": {k: {"joins": "previous", "kind": None, "title": None, "author": None, "confidence": 0.0,
                                 "why": "no model answer; rules kept"} for k in keymap}, "page_note": None}
            tier = "rules"
        still_low = min(b["confidence"] for b in ans["boxes"].values()) < cfg["thresholds"]["accept_api"]
        if tier == "rules" or (low and still_low):
            n_flag += 1
            flags.append({"page": pno, "tier": tier, "boxes": {str(k): b for k, b in ans["boxes"].items() if b["confidence"] < cfg["thresholds"]["accept_api"]},
                          "page_note": ans.get("page_note")})
        rec["tier"] = tier
        rec["boxes"] = {str(k): b for k, b in ans["boxes"].items()}
        rec["page_note"] = ans.get("page_note")
        page_records.append(rec)
        # the open piece for the next page
        open_piece = trailing_piece(page, keymap, ans["boxes"], open_piece)
    secs = round(time.time() - t_start, 1)
    with open(os.path.join(out_dir, "pages.jsonl"), "w", encoding="utf-8") as f:
        for r in page_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "flags.jsonl"), "w", encoding="utf-8") as f:
        for fl in flags:
            f.write(json.dumps(fl, ensure_ascii=False) + "\n")
    doc = build_records(iid, pages, page_records, meta)
    json.dump(doc, open(os.path.join(out_dir, "articles.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cmp = compare_with_rules(iid, pages, page_records, owner, furn)
    write_json_atomic(os.path.join(out_dir, "compare.json"), cmp)
    summary = {"pages": len(pages), "boxes": cmp["boxes"], "api_pages": n_api, "flagged_pages": n_flag, "cost_usd": round(cost, 4),
               "agreement_with_rules": cmp["agreement"], "records": doc["checks"]["records"], "story_records": doc["checks"]["story_records"],
               "seconds": secs}
    mark(iid, "linked_llm", **summary)
    log_fn(f"{iid}: {len(pages)} pages, {cmp['boxes']} boxes, agreement with rules {cmp['agreement']:.3f}, "
           f"api pages {n_api}, flagged {n_flag}, ${cost:.3f}, {secs}s, {doc['checks']['records']} records")
    return summary


def trailing_piece(page, keymap, boxes, open_piece):
    """What is open after this page: the last story-like piece touched, with its tail text."""
    cur = dict(open_piece) if open_piece else None
    for k in sorted(keymap):
        b = boxes.get(k)
        if not b:
            continue
        i = keymap[k]
        t = region_text(page["regions"][i])
        if b["joins"] == "new":
            cur = {"kind": b.get("kind") or "story", "title": b.get("title"), "author": b.get("author"), "tail": t}
        elif b["joins"] == "previous":
            if cur is None:
                cur = {"kind": "story", "title": None, "author": None, "tail": t}
            else:
                cur["tail"] = t
        # furniture, advert, caption, notice leave the open piece as it is
    return cur


def build_records(iid, pages, page_records, meta):
    """Chains of 'previous' links -> records in the rules assembly's shape."""
    records, furniture = [], []
    n = 0
    open_rec = None
    ad_rec = None

    def new_rec(typ, title=None, author=None):
        nonlocal n
        n += 1
        return {"article_id": f"{iid}_a{n:03d}", "type": typ, "title": title, "author": author, "pages": [], "fragments": [],
                "roles": {}, "flags": [], "toc": None, "keys": [], "llm": {"confidence_min": 1.0, "tiers": Counter()}}

    def add(rec, pno, i, role, conf, tier):
        rec["keys"].append((pno, i))
        if role:
            rec["roles"][f"{pno}:{i}"] = role
        rec["llm"]["confidence_min"] = min(rec["llm"]["confidence_min"], conf)
        rec["llm"]["tiers"][tier] += 1

    KIND_TYPE = {"story": "story", "serial": "story", "poem": "poem", "article": "feature", "department": "department",
                 "letters": "department", "contents": "toc", "filler": "filler", "ad": "ad", "other": "other"}
    for pr in page_records:
        pno = pr["page"]
        page = pages[pno]
        keymap = {int(k): i for k, i in (pr.get("keymap") or {}).items()}
        tier = pr.get("tier", "none")
        for k in sorted(keymap):
            b = pr["boxes"].get(str(k))
            if not b:
                continue
            i = keymap[k]
            conf = b.get("confidence", 0.0)
            j = b["joins"]
            if j == "furniture":
                furniture.append({"page": pno, "idx": i}); ad_rec = None; continue
            if j == "advert":
                if ad_rec is None:
                    ad_rec = new_rec("ad", title=" ".join(region_text(page["regions"][i]).split()[:12]) or None)
                    records.append(ad_rec)
                add(ad_rec, pno, i, None, conf, tier); continue
            ad_rec = None
            if j == "new":
                typ = KIND_TYPE.get((b.get("kind") or "story").lower(), "other")
                open_rec = new_rec(typ, b.get("title"), b.get("author"))
                if b.get("kind") == "serial":
                    open_rec["serial"] = True
                records.append(open_rec)
                add(open_rec, pno, i, "title" if b.get("title") else None, conf, tier); continue
            if open_rec is None:
                open_rec = new_rec("story", None, None)
                open_rec["flags"].append("text before any title: a piece continued from an earlier scan or an unmarked start")
                records.append(open_rec)
            role = {"caption": "caption", "notice": "note"}.get(j)
            add(open_rec, pno, i, role, conf, tier)
    for rec in records:
        rec["keys"].sort()
        frags = {}
        for pn, i in rec["keys"]:
            frags.setdefault(pn, []).append(i)
        rec["fragments"] = [{"page": pn, "region_ids": frags[pn]} for pn in sorted(frags)]
        rec["pages"] = sorted(frags)
        body = [region_text(pages[pn]["regions"][i]) for pn, i in rec["keys"] if rec["roles"].get(f"{pn}:{i}") not in ("title", "caption", "note")]
        rec["text"] = "\n\n".join(t for t in body if t)
        rec["n_regions"] = len(rec["keys"])
        rec["llm"]["tiers"] = dict(rec["llm"]["tiers"])
        del rec["keys"]
    return {"issue": iid, "backend": "llm_link_v1", "variant": OUT_VARIANT, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "magazine": meta.get("magazine"), "articles": records, "furniture": furniture, "unsorted": [],
            "checks": {"records": len(records), "story_records": sum(1 for r in records if r["type"] == "story"),
                       "unassigned_regions": 0, "double_owned_regions": 0, "flags": sum(len(r["flags"]) for r in records)}}


def compare_with_rules(iid, pages, page_records, owner, furn):
    """Box by box: did the model's 'joins' agree with what the rules did? (same piece as the previous box or not;
    furniture; advertising)."""
    agree = total = 0
    by_kind = Counter()
    disagreements = []
    prev_key = None
    for pr in page_records:
        pno = pr["page"]
        keymap = {int(k): i for k, i in (pr.get("keymap") or {}).items()}
        for k in sorted(keymap):
            b = pr["boxes"].get(str(k))
            i = keymap[k]
            key = f"{pno}:{i}"
            if not b:
                prev_key = key; continue
            # the rules' label in the model's terms
            if key in furn:
                rl = "furniture"
            elif key not in owner:
                rl = "unassigned"
            else:
                rec, role = owner[key]
                if rec["type"] == "ad":
                    rl = "advert"
                elif role in ("teaser", "caption", "subtitle"):
                    rl = "caption"
                elif role == "note":
                    rl = "notice"
                else:
                    prev = owner.get(prev_key)
                    rl = "previous" if (prev is not None and prev[0]["article_id"] == rec["article_id"]) else "new"
            ml = b["joins"]
            total += 1
            same = (ml == rl) or (rl == "unassigned")
            agree += same
            by_kind[f"rules={rl} model={ml}"] += 1
            if not same and len(disagreements) < 400:
                disagreements.append({"page": pno, "box": k, "rules": rl, "model": ml, "confidence": b["confidence"], "why": b.get("why"),
                                      "text": clip(region_text(pages[pno]["regions"][i]), 120, 60)})
            prev_key = key
    return {"boxes": total, "agree": agree, "agreement": round(agree / total, 4) if total else None,
            "pairs": dict(by_kind.most_common()), "disagreements": disagreements}


# ----------------------------------------------------------------------------------------------------------------
# the trial and the run
# ----------------------------------------------------------------------------------------------------------------
def issues_assembled(limit=None):
    from corpus_lib import corpus_config
    cfg = corpus_config()
    meta = {i["id"]: i for i in cfg["issues"]}
    states = all_states()
    ids = [i["id"] for i in cfg["issues"] if "assembled" in states.get(i["id"], {"stages": {}})["stages"]]
    if limit:
        ids = ids[:limit]
    return ids, meta


def trial(n, workers):
    load_pulp_env()
    ids, meta = issues_assembled(n)
    lc = local_cfg()
    log("s12", f"trial on {len(ids)} assembled issues, {workers} at a time; local {lc['model']} at {lc['base_url']}; "
               f"api {settings()['llm_link']['escalate']['model']}; budget ${settings()['llm_link']['escalate']['budget_usd']}")
    results = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(link_issue, iid, meta[iid]): iid for iid in ids if not has(iid, "linked_llm")}
        for f in as_completed(futs):
            iid = futs[f]
            try:
                results[iid] = f.result()
            except Exception as e:
                log("s12", f"{iid}: FAILED {e}")
                mark(iid, "linked_llm", fail=True, error=str(e)[:500])
    done = [r for r in results.values() if r]
    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "issues": len(ids), "done": len(done), "seconds": round(time.time() - t0, 1),
           "pages": sum(r["pages"] for r in done), "boxes": sum(r["boxes"] for r in done),
           "api_pages": sum(r["api_pages"] for r in done), "flagged_pages": sum(r["flagged_pages"] for r in done),
           "cost_usd": round(sum(r["cost_usd"] for r in done), 3),
           "agreement_with_rules": round(sum(r["agreement_with_rules"] * r["boxes"] for r in done if r["agreement_with_rules"] is not None)
                                         / max(1, sum(r["boxes"] for r in done if r["agreement_with_rules"] is not None)), 4),
           "spend_total": spend()}
    if done:
        rep["seconds_per_page"] = round(rep["seconds"] / max(1, rep["pages"]) * workers, 2)
        rep["escalation_share"] = round(rep["api_pages"] / max(1, rep["pages"]), 4)
        rep["flag_share"] = round(rep["flagged_pages"] / max(1, rep["pages"]), 4)
    write_json_atomic(os.path.join(ROOT, "data", "corpus", "llm_link_trial.json"), rep)
    log("s12", "trial: " + json.dumps({k: v for k, v in rep.items() if k != "spend_total"}))
    event("llm_link_trial", **{k: v for k, v in rep.items() if k != "spend_total"})
    return rep


def selftest():
    txt = '```json\n{"boxes":[{"k":1,"joins":"furniture","confidence":0.99,"why":"running head"},{"k":2,"joins":"new","kind":"story","title":"The Red Moon","author":"A. Merritt","confidence":0.9,"why":"display title and by-line"},{"k":3,"joins":"previous","confidence":0.95,"why":"mid-sentence"}],"page_note":null}\n```'
    a = parse_answer(txt, 3)
    assert a and a["boxes"][2]["title"] == "The Red Moon" and a["boxes"][3]["joins"] == "previous", a
    assert parse_answer(txt, 4) is None and parse_answer("no json here", 1) is None
    pages = {1: {"page": 1, "width": 1000, "height": 1500, "regions": [
        {"label": "PageHeader", "bbox": [0, 0, 1000, 40], "text": "WEIRD TALES"},
        {"label": "SectionHeader", "bbox": [0, 60, 1000, 160], "text": "The Red Moon"},
        {"label": "Text", "bbox": [0, 200, 480, 1400], "text": "By A. Merritt. The night was cold and the"},
        {"label": "Text", "bbox": [520, 200, 1000, 1400], "text": "moon rose red over the hills."}]},
             2: {"page": 2, "width": 1000, "height": 1500, "regions": [
        {"label": "Text", "bbox": [0, 60, 480, 1400], "text": "and the wolves came down."},
        {"label": "Text", "bbox": [520, 60, 1000, 1400], "text": "BUY WAR BONDS TODAY"}]}}
    prs = [{"page": 1, "n_boxes": 4, "keymap": {"1": 0, "2": 1, "3": 2, "4": 3}, "tier": "local", "boxes": {
        "1": {"joins": "furniture", "confidence": 0.99}, "2": {"joins": "new", "kind": "story", "title": "The Red Moon", "author": "A. Merritt", "confidence": 0.9},
        "3": {"joins": "previous", "confidence": 0.9}, "4": {"joins": "previous", "confidence": 0.95}}},
           {"page": 2, "n_boxes": 2, "keymap": {"1": 0, "2": 1}, "tier": "local", "boxes": {
        "1": {"joins": "previous", "confidence": 0.9}, "2": {"joins": "advert", "confidence": 0.8}}}]
    doc = build_records("t", pages, prs, {"magazine": "Weird Tales"})
    recs = doc["articles"]
    assert len(recs) == 2 and recs[0]["type"] == "story" and recs[0]["pages"] == [1, 2] and recs[0]["n_regions"] == 4 and recs[1]["type"] == "ad", recs
    assert "moon rose red" in recs[0]["text"] and recs[0]["roles"] == {"1:1": "title"}, recs[0]
    owner = {"1:1": (recs[0], "title"), "1:2": (recs[0], None), "1:3": (recs[0], None), "2:0": (recs[0], None), "2:1": (recs[1], None)}
    cmp = compare_with_rules("t", pages, prs, owner, {"1:0"})
    assert cmp["boxes"] == 6 and cmp["agree"] == 6, cmp
    prompt, keymap = page_prompt("t", {"magazine": "Weird Tales", "cover_date": "1934-05"}, 1, pages[1], pages, owner, {"1:0"}, None, settings()["llm_link"]["context"])
    assert "[2] label=SectionHeader" in prompt and "rule-based proposal: begins the story" in prompt and keymap == {1: 0, 2: 1, 3: 2, 4: 3}, prompt
    print("s12 selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue")
    ap.add_argument("--trial", type=int, metavar="N", help="the first N assembled issues")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="with --issue and --page: print the prompt, call nothing")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest(); return
    load_pulp_env()
    cfg = settings()["llm_link"]
    if args.issue:
        _ids, meta = issues_assembled()
        m = meta.get(args.issue) or {"id": args.issue}
        if args.dry_run:
            link_issue(args.issue, m, dry_run_page=args.page); return
        with stage_timer("s12_llm_link", args.issue):
            link_issue(args.issue, m)
        return
    if args.trial:
        trial(args.trial, args.workers or cfg["local"].get("concurrency", 4)); return
    sys.exit("pass --issue <id>, --trial N, or --selftest")


if __name__ == "__main__":
    main()
