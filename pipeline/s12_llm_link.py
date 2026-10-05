#!/usr/bin/env python3
"""s12 — box linking by a language model (Heejin, 4 October 2026: "After layout detection let the high performance
LLM read the content and decide whether a box is connected to the next one or not. If the local LLM is not sure
about it, let it use Fable or Opus API. If it is still uncertain let it flag it and a human solve the case.")

For every page of an issue the model is shown the page's text boxes (the layout detector's regions in reading
order, with their labels, positions and the head and tail of their text), the editorial piece the rules engine
(s08) had open before the page, and the rules engine's own decision for every box as a hint, and answers for
every box:

    joins   previous | new | advert | caption | notice | furniture
            previous  = continues the editorial piece that is open: the story, article, poem or department of the
                        box before it, or, when advertising or furniture came in between, the one open before them
            new       = a new editorial piece begins in this box (kind; title and author when printed)
            advert    = advertising: the first box of an advertisement and every following box of it
            caption   = an illustration caption, a pull-quote, a type label (paratext of the open piece)
            notice    = "continued on page 98", "THE END", a next-issue line (paratext of the open piece)
            furniture = running head, page number, the magazine's name
    kind    story | serial | poem | article | department | letters | contents | filler | ad | other   (with new)
    title, author                                                                                   (with new)
    confidence 0.0-1.0, why

The pages of an issue are independent (the open piece comes from the rules, not from the model's answer for the
page before), so they are asked in parallel. The local lane is held to the answer's exact shape by a JSON schema:
the server lets the model write only what the schema allows (24% of the first trial's answers, without it, could
not be read: the model wrote a box as a quoted string partway through the list).

Two readings, both local (Heejin, 5 October 2026: "Using api costs too much. … Let the local model do the job as
much as possible and if unavoidable let it flag them for a person."). The first reading (the local lane on GPU 2,
thinking off, the answer held to a JSON schema) answers every box. A box it gives less than
thresholds.accept_local (0.95: in the first trial the local model and Opus 5.5 agreed on 95% of such boxes, 99%
of the piece-begins-or-continues decisions, against 44-71% below it), or a page whose answer could not be read,
gets a second reading: the same model in its thinking mode (it reasons before it answers; Qwen's recommended
sampling for that mode), told what the first reading said and asked about the doubtful boxes only. A doubtful box
is settled when the two readings agree or the second is at least thresholds.accept_second (0.85) sure. A page is
flagged for a person (flags.jsonl, with both readings of the boxes in question) only when a box stays unsettled
and its decision changes a piece — previous, new or advert (thresholds.flag_joins); an unsettled caption, notice
or furniture box takes the second reading without a flag. A page no model could read keeps the rules' decisions
and is flagged. The Claude API path is kept but off (settings.llm_link.escalate.enabled false); when on, the API
gives the second reading instead, within escalate.budget_usd per run and escalate.budget_total_usd in all, and a
refusal that retrying cannot cure (no credit, a refused key, an unknown model) stops it for the run.

Output, per issue, in the rules assembly's record shape so that the harness (s09), the audits and the export read
it unchanged:
    data/assembly_v2/<variant>/<id>/pages.jsonl    one line per page: the decisions and their provenance
    data/assembly_v2/<variant>/<id>/articles.json  the records built from the decisions
    data/assembly_v2/<variant>/<id>/flags.jsonl    the pages for a person, with the boxes in doubt and why
    data/assembly_v2/<variant>/<id>/compare.json   agreement with the rules engine: the kind of every box, and where pieces begin
    data/assembly_v2/<variant>/summary.jsonl       one line per issue (the trial and pilot reports add them up)
    data/corpus/llm_link_spend.json                the API spend of all runs
<variant> is llm for the corpus and the pilot issues, llm_trial_<tag> for a trial.

    python3 pipeline/s12_llm_link.py --issue <id>
    python3 pipeline/s12_llm_link.py --pilot                          # the ten pilot issues -> data/assembly_v2/llm (s09 scores them)
    python3 pipeline/s12_llm_link.py --trial 100 --tag NAME           # the first 100 assembled issues -> llm_trial_NAME
    python3 pipeline/s12_llm_link.py --trial 100 --tag NAME --same-as OLD   # the issues of the trial OLD
    python3 pipeline/s12_llm_link.py --dry-run --issue <id> --page 5  # print the prompt, call nothing
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import ROOT, PILOT_CONFIG, settings, mark, all_states, event, log, write_json_atomic, issue_dirs  # noqa: E402
from timing_util import stage_timer, load_pulp_env  # noqa: E402

OUT_VARIANT = "llm"
SPEND_PATH = os.path.join(ROOT, "data", "corpus", "llm_link_spend.json")
_spend_lock = threading.Lock()
_state_lock = threading.Lock()
_summary_lock = threading.Lock()
_api_slots = threading.Semaphore(6)                 # at most six API calls in flight (the pages of an issue are asked in parallel)
_API = {"off": None, "run_usd": 0.0}                # off: why the API is not asked any more in this run
_LOCAL_FMT = {"level": None}                        # the answer format the lane accepted: schema, json or none
_LANES = {"n": 0, "models": {}}                     # round robin over the lanes; the model name each lane serves

JOINS = ("previous", "new", "advert", "caption", "notice", "furniture")
KINDS = ("story", "serial", "poem", "article", "department", "letters", "contents", "filler", "ad", "other")
RULES_KIND = {"story": "story", "serial_part": "serial", "poem": "poem", "feature": "article", "letters": "letters",
              "department": "department", "toc": "contents", "filler": "filler"}
KIND_TYPE = {"story": "story", "serial": "story", "poem": "poem", "article": "feature", "department": "department",
             "letters": "letters", "contents": "toc", "filler": "filler", "ad": "ad", "other": "other"}

SYSTEM = """You are assembling the contents of a scanned American fiction magazine (a pulp, 1890-1955) from the text boxes a layout detector found on each page. The boxes are given in reading order. For every box decide what it is and whether it continues a piece or begins one, and say how sure you are.

How these magazines are made: a story or article usually opens with a display title, often a by-line ("By John Smith"), sometimes a type label ("A Complete Novelet") and a teaser blurb, then body text in columns; it runs over several pages and may be interrupted by advertising, after which it continues; running heads (the magazine's name, the story's title at the top of a page) and page numbers are furniture; "(Continued on page 98)" and "THE END" are notices; the text under an illustration is a caption. A box that starts in the middle of a sentence continues a piece.

The values of "joins":
previous = the box continues the editorial piece that is open: the story, article, poem or department of the box before it, or, when advertising or furniture came in between, the one open before them.
new = a new editorial piece begins in this box (give kind; title and author when printed). Never use new for advertising.
advert = advertising: the first box of an advertisement and every following box of it.
caption = an illustration caption, a pull-quote or a type label belonging to the open piece.
notice = "Continued on page 98", "THE END", a next-issue line, belonging to the open piece.
furniture = running head, page number, the magazine's name.

Answer with JSON only, on one line, for example:
{"boxes":[{"k":1,"joins":"furniture","confidence":0.99},{"k":2,"joins":"new","kind":"story","title":"The Red Moon","author":"A. Merritt","confidence":0.97},{"k":3,"joins":"previous","confidence":0.8,"why":"could be a caption"}]}
Give kind, title and author only with joins "new" and leave them out otherwise; add "page_note" only when something about the page is odd. confidence is your probability that the joins decision is right. Add "why" (at most six words) only when your confidence is below 0.95. Use the rule-based decision as a hint, not as truth: when the text shows otherwise, say so. Answer every box from 1 to the last, unless the request names the boxes to answer."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "boxes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "k": {"type": "integer"},
                "joins": {"type": "string", "enum": list(JOINS)},
                "kind": {"type": "string", "enum": list(KINDS)},
                "title": {"type": "string"},
                "author": {"type": "string"},
                "confidence": {"type": "number"},
                "why": {"type": "string"}},
            "required": ["k", "joins", "confidence"],
            "additionalProperties": False}},
        "page_note": {"type": "string"}},
    "required": ["boxes"],
    "additionalProperties": False}


class ApiRefused(RuntimeError):
    """The API said no for a reason retrying will not cure: no credit, a refused key, an unknown model."""


class ApiSkipped(RuntimeError):
    """The API is not asked: refused earlier in this run, or a budget is spent."""


# ----------------------------------------------------------------------------------------------------------------
# inputs
# ----------------------------------------------------------------------------------------------------------------
def load_pages(iid):
    pages = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "layout", iid, "page_*.json"))):
        p = json.load(open(f, encoding="utf-8"))
        p["regions"] = sorted(p["regions"], key=lambda r: r.get("order", 0))       # as s08 does: the region index is the same
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


def is_ad_type(typ):
    return typ in ("ad", "house")


def rules_labels(pages, owner, furn):
    """The rules engine's records as box decisions in the model's terms, in reading order across the whole issue:
    a box begins a piece when its record differs from the record of the last editorial box before it (furniture and
    advertising in between do not break a piece). Used for the hints in the prompt, for a page no model answered,
    and as the rules' side of the comparison. (Until p50k the hint compared a box with the box just before it on
    the same page, so the first box of every page and the box after a running head were said to begin a piece.)"""
    out = {}
    last_ed = None
    for pno in sorted(pages):
        for i, r in enumerate(pages[pno]["regions"]):
            if not region_text(r):
                continue
            key = f"{pno}:{i}"
            if key in furn:
                out[key] = {"joins": "furniture"}
                continue
            if key not in owner:
                out[key] = {"joins": "unassigned"}
                continue
            rec, role = owner[key]
            if is_ad_type(rec["type"]):
                out[key] = {"joins": "advert", "record": rec["article_id"], "ad_class": rec.get("ad_class") or rec["type"]}
                continue
            if rec["article_id"] != last_ed:
                j = "new"
            elif role in ("caption", "teaser", "subtitle"):
                j = "caption"
            elif role == "note":
                j = "notice"
            else:
                j = "previous"
            last_ed = rec["article_id"]
            out[key] = {"joins": j, "kind": RULES_KIND.get(rec["type"], "other"), "title": rec.get("title"),
                        "author": rec.get("author"), "record": rec["article_id"], "role": role}
    return out


def rules_hint(lab):
    j = lab.get("joins")
    if j in ("furniture",):
        return "furniture"
    if j == "unassigned":
        return "not assigned to any piece"
    if j == "advert":
        return f"advertisement ({lab.get('ad_class') or 'ad'})"
    what = (lab.get("kind") or "piece") + (f' "{lab["title"]}"' if lab.get("title") else "") + (f" by {lab['author']}" if lab.get("author") else "")
    if j == "new":
        return f"begins the {what}"
    if j in ("caption", "notice"):
        return f"{lab.get('role') or j} of the {what}"
    return f"continues the {what}"


def rules_open_piece(pages, owner, furn, pno, ctx, back=4):
    """The editorial piece the rules engine had open before page pno: the record of the last editorial text box on
    the nearest earlier page that has one (up to `back` pages back, so that a story interrupted by a page of
    advertising is still shown as open), with that box's tail. None on the first page or when there is none."""
    for prev in range(pno - 1, max(0, pno - 1 - back), -1):
        if prev not in pages:
            continue
        regs = pages[prev]["regions"]
        for i in range(len(regs) - 1, -1, -1):
            key = f"{prev}:{i}"
            t = region_text(regs[i])
            if not t or key in furn or key not in owner:
                continue
            rec, role = owner[key]
            if is_ad_type(rec["type"]) or rec["type"] == "toc":
                continue
            return {"kind": RULES_KIND.get(rec["type"], rec["type"]), "title": rec.get("title"), "author": rec.get("author"),
                    "tail": t, "page": prev, "skipped": pno - 1 - prev, "source": "rules"}
    return None


def page_prompt(iid, meta, pno, page, pages, rl, open_piece, ctx):
    """The user message for one page, and the map from box number (1..n) to region index."""
    lines = [f"Magazine: {meta.get('magazine', '?')}, issue dated {meta.get('cover_date', '?')}. Scan page {pno} of {len(pages)}."]
    if open_piece:
        where = "the end of the previous page" if not open_piece.get("skipped") else \
            f"the end of page {open_piece['page']} (the {open_piece['skipped']} page(s) between carry no editorial text by the rules' reading)"
        lines.append(f"According to the rule-based pass, the piece open at {where}: {open_piece['kind']} \"{open_piece.get('title') or '?'}\""
                     + (f" by {open_piece['author']}" if open_piece.get("author") else "")
                     + f". Its last words: \"{clip(open_piece.get('tail') or '', 0, ctx['prev_page_tail_chars'])}\"")
    else:
        lines.append("No piece is open from the previous pages (this is the first page, or the rule-based pass left nothing open).")
    lines.append("")
    lines.append("The boxes of this page, in reading order (position as percent of the page width and height):")
    W, H = max(1, page.get("width") or 1), max(1, page.get("height") or 1)
    n_text = sum(1 for r in page["regions"] if region_text(r))
    big = n_text > ctx.get("big_page_boxes", 60)
    head = ctx.get("big_page_head_chars", 160) if big else ctx["text_head_chars"]
    tail = ctx.get("big_page_tail_chars", 100) if big else ctx["text_tail_chars"]
    k = 0
    keymap = {}
    for i, r in enumerate(page["regions"]):
        t = region_text(r)
        if not t:
            continue
        k += 1
        keymap[k] = i
        x0, y0, x1, y1 = (r.get("bbox") or [0, 0, 0, 0])[:4]
        pos = f"x {100 * x0 / W:.0f}-{100 * x1 / W:.0f}%, y {100 * y0 / H:.0f}-{100 * y1 / H:.0f}%"
        hint = rules_hint(rl.get(f"{pno}:{i}", {"joins": "unassigned"}))
        lines.append(f"[{k}] label={r.get('label', 'Text')} at {pos}; rule-based decision: {hint}")
        lines.append(f"    text: \"{clip(t, head, tail)}\"")
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
    """settings.llm_link.local, with PULP_LLM_BASE_URL / PULP_LLM_MODEL / PULP_LLM_THINKING from the environment on
    top (a trial of another lane, model or mode without touching the tracked settings file on the server)."""
    cfg = dict(settings()["llm_link"]["local"])
    if os.environ.get("PULP_LLM_BASE_URL"):
        cfg["base_url"] = os.environ["PULP_LLM_BASE_URL"]
    if os.environ.get("PULP_LLM_MODEL"):
        cfg["model"] = os.environ["PULP_LLM_MODEL"]
    if os.environ.get("PULP_LLM_THINKING"):
        cfg["thinking"] = os.environ["PULP_LLM_THINKING"] not in ("0", "", "false", "no")
    return cfg


def lanes(cfg):
    """The lanes the local model is asked on: base_url/model, then settings.llm_link.local.extra_lanes (a list of
    {"base_url", "model"}; model "auto" = the one the lane serves), in turn."""
    out = [(cfg["base_url"], cfg["model"])]
    if not os.environ.get("PULP_LLM_BASE_URL"):
        for ln in cfg.get("extra_lanes") or []:
            out.append((ln["base_url"], ln.get("model", "auto")))
    return out


def lane_model(base_url, model):
    if model != "auto":
        return model
    if base_url not in _LANES["models"]:
        req = urllib.request.Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urllib.request.urlopen(req, timeout=20) as r:
            _LANES["models"][base_url] = json.loads(r.read().decode("utf-8"))["data"][0]["id"]
    return _LANES["models"][base_url]


def next_lane(cfg):
    ls = lanes(cfg)
    with _state_lock:
        _LANES["n"] += 1
        i = _LANES["n"] % len(ls)
    return ls[i]


def local_max_tokens(cfg, n_boxes, thinking):
    """Room for the answer: about 60 tokens a box (the first trial: 827 tokens for 19 boxes on average), never less
    than the setting, at most max_tokens_cap (a page of 100 boxes gets 6,400)."""
    base = int(cfg.get("max_tokens_thinking", 6000) if thinking else cfg.get("max_tokens", 4000))
    need = 400 + 60 * n_boxes + (4000 if thinking else 0)
    return int(min(int(cfg.get("max_tokens_cap", 12000)), max(base, need)))


FMT_LEVELS = ("schema", "json", "none")


def _fmt_body(level):
    if level == "schema":
        return {"type": "json_schema", "json_schema": {"name": "page_links", "schema": ANSWER_SCHEMA, "strict": True}}
    if level == "json":
        return {"type": "json_object"}
    return None


def ask_local(user_text, cfg, n_boxes, temperature=None, max_tokens=None, extra=None):
    """The local lane: an OpenAI-style chat endpoint (vLLM). Thinking off: the answer is held to ANSWER_SCHEMA, and a
    lane that refuses the schema is asked for plain JSON, then for nothing (the step down is kept for the run).
    Thinking on: no format (the JSON is read out of the answer after the reasoning). extra: more sampling settings
    (top_p, top_k …). Returns a dict: text, usage, seconds, finish, format, max_tokens, reasoning_chars, lane."""
    thinking = bool(cfg.get("thinking", False))
    mt = int(max_tokens or local_max_tokens(cfg, n_boxes, thinking))
    base_url, model = next_lane(cfg)
    body = {"model": lane_model(base_url, model), "temperature": cfg.get("temperature", 0) if temperature is None else temperature,
            "max_tokens": mt,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_text}],
            "chat_template_kwargs": {"enable_thinking": thinking}}
    body.update(extra or {})
    level = "none" if thinking else (_LOCAL_FMT["level"] or "schema")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {cfg.get('api_key') or 'EMPTY'}"}
    url = base_url.rstrip("/") + "/chat/completions"
    t0 = time.time()
    last = None
    tries = 0
    while tries < 3:
        fmt = _fmt_body(level)
        if fmt:
            body["response_format"] = fmt
        else:
            body.pop("response_format", None)
        try:
            d = _post_json(url, body, headers, cfg.get("timeout_s", 300) * (3 if thinking else 1))
            ch = d["choices"][0]
            msg = ch["message"]
            return {"text": msg.get("content") or "", "usage": d.get("usage") or {}, "seconds": round(time.time() - t0, 2),
                    "finish": ch.get("finish_reason"), "format": level, "max_tokens": mt, "lane": base_url,
                    "reasoning_chars": len(msg.get("reasoning_content") or msg.get("reasoning") or "")}
        except urllib.error.HTTPError as e:
            msg = e.read()[:400].decode("utf-8", "replace")
            last = f"HTTP {e.code}: {msg}"
            if e.code == 400 and "maximum context length" in msg:
                raise RuntimeError(f"the page is too long for the lane: {msg[:200]}")
            if e.code == 400 and level != "none" and any(w in msg for w in ("response_format", "json_schema", "schema", "guided", "structured", "grammar")):
                level = FMT_LEVELS[FMT_LEVELS.index(level) + 1]
                with _state_lock:
                    _LOCAL_FMT["level"] = level
                log("s12", f"the lane refused the answer format; asking for {level} from now on ({msg[:160]})")
                continue
            if e.code == 400 and "chat_template_kwargs" in msg and "chat_template_kwargs" in body:
                body.pop("chat_template_kwargs", None)                 # a lane without this option
                continue
        except Exception as e:
            last = repr(e)
        tries += 1
        time.sleep(5 * tries)
    raise RuntimeError(f"local lane failed: {last}")


def page_image_b64(iid, pno, height_px):
    from PIL import Image
    p = None
    for ext in ("jpg", "png"):                                      # the corpus has JPEG working images, the pilot PNG
        q = os.path.join(issue_dirs(iid)["pages"], f"page_{pno:04d}.{ext}")
        if os.path.exists(q):
            p = q
            break
    if not p:
        return None
    im = Image.open(p)
    if im.height > height_px:
        im = im.resize((round(im.width * height_px / im.height), height_px))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def api_max_tokens(cfg, n_boxes):
    return int(min(int(cfg.get("max_tokens_cap", 8000)), max(int(cfg.get("max_tokens", 2500)), 300 + 70 * n_boxes)))


def ask_api(user_text, iid, pno, cfg, n_boxes, model=None):
    """The Claude API, with the page image when the settings say so. Returns (text, usage, seconds, cost_usd, stop_reason).
    Raises ApiRefused when retrying cannot help (no credit, a refused key, an unknown model)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ApiRefused("ANTHROPIC_API_KEY is not set (the environment file ~/shared/khj/.pulp_env)")
    model = model or cfg["model"]
    content = []
    if cfg.get("with_image", True):
        b64 = page_image_b64(iid, pno, cfg.get("image_height_px", 1400))
        if b64:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
            user_text = "The page image is attached; the boxes below are the text the detector read from it.\n\n" + user_text
    content.append({"type": "text", "text": user_text})
    body = {"model": model, "max_tokens": api_max_tokens(cfg, n_boxes), "system": SYSTEM,       # no temperature: Opus 5.5 rejects it
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
            return txt, u, round(time.time() - t0, 2), round(cost, 5), d.get("stop_reason")
        except urllib.error.HTTPError as e:
            msg = e.read()[:400].decode("utf-8", "replace")
            last = f"HTTP {e.code}: {msg}"
            if e.code in (401, 403, 404) or (e.code == 400 and "credit balance" in msg):
                raise ApiRefused(last)
            if e.code == 400:
                break
        except Exception as e:
            last = repr(e)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"api failed: {last}")


def parse_answer(txt, n_boxes, need=None):
    """The JSON in the model's answer; lenient about text around it. Returns the dict, or None when the answer cannot
    be read or does not cover every box asked for (need: those box numbers; default all)."""
    if not txt:
        return None
    s = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    boxes = d.get("boxes") if isinstance(d, dict) else None
    if not isinstance(boxes, list):
        return None
    out = {}
    for b in boxes:
        if not isinstance(b, dict):
            continue
        try:
            k = int(b.get("k"))
        except Exception:
            continue
        if not (1 <= k <= n_boxes):
            continue
        joins = str(b.get("joins") or "").strip().lower()
        if joins not in JOINS:
            continue
        try:
            conf = max(0.0, min(1.0, float(b.get("confidence"))))
        except Exception:
            conf = 0.0
        kind = (str(b.get("kind")).strip().lower() or None) if b.get("kind") else None
        out[k] = {"joins": joins, "kind": kind if joins == "new" else None,
                  "title": (b.get("title") or None) if joins == "new" else None,
                  "author": (b.get("author") or None) if joins == "new" else None, "confidence": conf, "why": (b.get("why") or "")[:200]}
    if need is not None:
        if not set(need) <= set(out):
            return None
        out = {k: b for k, b in out.items() if k in need}
    elif len(out) < n_boxes:
        return None
    return {"boxes": out, "page_note": d.get("page_note")}


# ----------------------------------------------------------------------------------------------------------------
# spend and the API's availability
# ----------------------------------------------------------------------------------------------------------------
def spend(add=0.0, add_calls=0, usage=None):
    """The running total of what the Claude API has cost, all runs (data/corpus/llm_link_spend.json); read with no arguments."""
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
            if usage:
                d["input_tokens"] += int(usage.get("input_tokens") or 0)
                d["output_tokens"] += int(usage.get("output_tokens") or 0)
            d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            write_json_atomic(SPEND_PATH, d)
        return d


def api_unavailable(esc):
    """Why the API is not to be asked now, or None."""
    if _API["off"]:
        return "the API refused earlier in this run"
    if _API["run_usd"] >= float(esc.get("budget_usd", 50)):
        return f"this run's API budget (${esc.get('budget_usd', 50)}) is spent"
    tot = esc.get("budget_total_usd")
    if tot is not None and spend()["usd"] >= float(tot):
        return f"the total API budget (${tot}) is spent"
    return None


def note_api_refusal(msg):
    with _state_lock:
        if not _API["off"]:
            _API["off"] = msg[:300]
            log("s12", f"the Claude API refused ({msg[:200]}); it is not asked again in this run, and the pages it would have taken are flagged")


# ----------------------------------------------------------------------------------------------------------------
# one issue
# ----------------------------------------------------------------------------------------------------------------
def fallback_answer(pno, keymap, rl):
    """For a page no model answered: the rules' own decisions, at confidence 0 (so the page is flagged)."""
    boxes = {}
    for k, i in keymap.items():
        lab = rl.get(f"{pno}:{i}", {"joins": "unassigned"})
        j = lab["joins"] if lab["joins"] in JOINS else "previous"
        boxes[k] = {"joins": j, "kind": lab.get("kind") if j == "new" else None, "title": lab.get("title") if j == "new" else None,
                    "author": lab.get("author") if j == "new" else None, "confidence": 0.0, "why": "no model answer: the rules' decision"}
    return {"boxes": boxes, "page_note": None}


def ask_and_read(prompt, lc, n_boxes, need=None, extra=None, max_tokens=None):
    """One reading by the local model, with one more try: a cut-off answer is asked again with twice the room, an
    unreadable one again with a little randomness (thinking off) or a fresh sample (thinking on).
    Returns (answer or None, info)."""
    ans, info, res = None, {}, None
    thinking = bool(lc.get("thinking"))
    for attempt in (1, 2):
        temp, mt = None, max_tokens
        if attempt == 2:
            if res and res["finish"] == "length":
                mt = min(int(lc.get("max_tokens_cap", 12000)), 2 * int(res["max_tokens"]))
                if mt <= int(res["max_tokens"]):
                    break
            elif not thinking:
                temp = 0.3
        try:
            res = ask_local(prompt, lc, n_boxes, temperature=temp, max_tokens=mt, extra=extra)
        except Exception as e:
            info = {"error": str(e)[:300], "attempts": attempt}
            break
        ans = parse_answer(res["text"], n_boxes, need=need)
        info = {"model": lc["model"], "thinking": thinking, "seconds": res["seconds"], "usage": res["usage"], "finish": res["finish"],
                "format": res["format"], "max_tokens": res["max_tokens"], "reasoning_chars": res["reasoning_chars"], "attempts": attempt,
                "lane": res["lane"], "parsed": ans is not None, "raw": None if ans else res["text"][:600]}
        if ans is not None:
            break
    return ans, info


def second_prompt(prompt, first, doubtful, n_boxes):
    """The first reading's answers, and the boxes to look at again (or the whole page when it could not be read)."""
    lines = [prompt, ""]
    if first:
        lines.append("A first, quick reading of this page answered: " + "; ".join(
            f"[{k}] {b['joins']}" + (f" {b['kind']}" if b.get("kind") else "") + f" ({b['confidence']:.2f})"
            for k, b in sorted(first["boxes"].items())) + ".")
        ks = sorted(doubtful)
        one = len(ks) == 1
        lines.append(f"It was unsure of {'box' if one else 'boxes'} {', '.join(map(str, ks))}. Look again at {'that box' if one else 'those boxes'} "
                     "with care — the text before and after, the boxes around it, the piece open from the previous pages — think it "
                     f"through, and answer for {'that box' if one else 'those boxes'} only.")
    else:
        lines.append(f"A first, quick reading of this page could not be read. Read the page with care, think it through, and answer "
                     f"for every box 1 to {n_boxes}.")
    return "\n".join(lines)


def settle(first, second, doubtful, accept_second, flag_joins, fallback):
    """The final decision for every box after a second reading of the doubtful ones. A doubtful box is settled when
    the two readings agree or the second is accept_second sure; an unsettled box is flagged when its decision changes
    a piece (flag_joins), or when no reading answered it. Returns (answer, boxes to flag, counts)."""
    base = first["boxes"] if first else fallback["boxes"]
    final = {k: dict(b) for k, b in base.items()}
    flag_boxes, c = {}, Counter()
    for k in sorted(doubtful):
        p1 = first["boxes"].get(k) if first else None
        p2 = second["boxes"].get(k) if second else None
        ok = False
        if p2 is not None:
            agreed = p1 is not None and p1["joins"] == p2["joins"]
            final[k] = dict(p2, agreed=agreed, first=({"joins": p1["joins"], "confidence": p1["confidence"]} if p1 else None))
            ok = agreed or p2["confidence"] >= accept_second
            c["settled_agreement" if agreed else ("settled_confidence" if ok else "unsettled")] += 1
        else:
            c["unsettled"] += 1
        if not ok:
            j1, j2 = (p1 or {}).get("joins"), (p2 or {}).get("joins")
            if j1 in flag_joins or j2 in flag_joins or (p1 is None and p2 is None):
                flag_boxes[k] = {"first": p1, "second": p2}
                c["unsettled_flagged"] += 1
                c["unsettled_label:" + str(j2 or j1)] += 1
    note = (second or {}).get("page_note") or (first or {}).get("page_note")
    return {"boxes": final, "page_note": note}, flag_boxes, c


def link_issue(iid, meta, variant=OUT_VARIANT, dry_run_page=None, log_fn=None, mark_stage="linked_llm"):
    log_fn = log_fn or (lambda m: log("s12", m))
    cfg = settings()["llm_link"]
    pages = load_pages(iid)
    if not pages:
        log_fn(f"{iid}: no layout pages")
        return None
    owner, furn, _rules_doc = load_rules(iid)
    rl = rules_labels(pages, owner, furn)
    jobs = []
    for pno in sorted(pages):
        open_piece = rules_open_piece(pages, owner, furn, pno, cfg["context"])
        prompt, keymap = page_prompt(iid, meta, pno, pages[pno], pages, rl, open_piece, cfg["context"])
        if dry_run_page is not None:
            if pno == dry_run_page:
                print(SYSTEM)
                print("\n----- user -----\n")
                print(prompt)
                print("\n----- the answer's shape (the lane is held to it) -----\n" + json.dumps(ANSWER_SCHEMA))
                return None
            continue
        jobs.append((pno, prompt, keymap))
    if dry_run_page is not None:
        return None
    out_dir = os.path.join(ROOT, "data", "assembly_v2", variant, iid)
    os.makedirs(out_dir, exist_ok=True)
    thr = cfg["thresholds"]
    acc_local = thr["accept_local"]
    flag_joins = set(thr.get("flag_joins") or ("previous", "new", "advert"))
    esc = cfg["escalate"]
    t_start = time.time()

    def one_page(job):
        pno, prompt, keymap = job
        n_boxes = len(keymap)
        if n_boxes == 0:
            return {"page": pno, "boxes": {}, "tier": "none", "n_boxes": 0}, None, 0.0
        rec = {"page": pno, "n_boxes": n_boxes, "keymap": {str(k): i for k, i in keymap.items()}}
        lc = local_cfg()
        first, rec["local"] = ask_and_read(prompt, lc, n_boxes)
        doubtful = {k for k, b in first["boxes"].items() if b["confidence"] < acc_local} if first else set(keymap)
        rec["asked_again"] = bool(doubtful)
        if first and doubtful:
            rec["low_labels"] = dict(Counter(first["boxes"][k]["joins"] for k in doubtful))
        cost = 0.0
        fallback = fallback_answer(pno, keymap, rl)
        if not doubtful:
            rec["tier"] = "local"
            rec["boxes"] = {str(k): b for k, b in first["boxes"].items()}
            rec["page_note"] = first.get("page_note")
            return rec, None, 0.0
        second = None
        if esc.get("enabled"):                                  # the Claude API as the second reading (off since 5 October)
            why_not = api_unavailable(esc)
            if why_not:
                rec["api"] = {"skipped": why_not}
            else:
                try:
                    with _api_slots:
                        why_not = api_unavailable(esc)
                        if why_not:
                            raise ApiSkipped(why_not)
                        txt, usage, secs, c, stop = ask_api(prompt, iid, pno, esc, n_boxes)
                    cost = c
                    spend(c, 1, usage)
                    with _state_lock:
                        _API["run_usd"] += c
                    second = parse_answer(txt, n_boxes)
                    rec["api"] = {"model": esc["model"], "seconds": secs, "usage": usage, "cost_usd": c, "stop": stop,
                                  "parsed": second is not None, "raw": None if second else txt[:600]}
                except ApiSkipped as e:
                    rec["api"] = {"skipped": str(e)}
                except ApiRefused as e:
                    note_api_refusal(str(e))
                    rec["api"] = {"error": str(e)[:300], "refused": True}
                except Exception as e:
                    rec["api"] = {"error": str(e)[:300]}
            limit = thr.get("accept_api", 0.8)
        else:                                                   # the local model again, thinking, on the doubtful boxes
            sp = dict(lc.get("second_pass") or {})
            lc2 = dict(lc, thinking=True, temperature=sp.get("temperature", 0.6))    # Qwen's sampling for its thinking mode
            need = None if first is None else doubtful
            mt2 = int(sp.get("max_tokens", 6000)) + 60 * (n_boxes if need is None else len(need))
            second, rec["second"] = ask_and_read(second_prompt(prompt, first, doubtful, n_boxes), lc2, n_boxes, need=need,
                                                 extra={k: sp[k] for k in ("top_p", "top_k", "min_p", "presence_penalty") if k in sp} or None,
                                                 max_tokens=mt2) if sp.get("enabled", True) else (None, {"skipped": "second_pass.enabled is false"})
            limit = thr.get("accept_second", 0.85)
        ans, flag_boxes, counts = settle(first, second, doubtful, limit, flag_joins, fallback)
        rec["settle"] = dict(counts)
        tier = ("api" if esc.get("enabled") else "local2") if second else ("local" if first else "rules")
        flag = None
        if flag_boxes:
            reason = ("no reading could be read; the rules' decisions are kept" if not first and not second else
                      f"{len(flag_boxes)} decision(s) that change a piece stay open after the second reading")
            flag = {"page": pno, "tier": tier, "reason": reason,
                    "boxes": {str(k): dict(v, text=clip(region_text(pages[pno]["regions"][keymap[k]]), 160, 60)) for k, v in flag_boxes.items()},
                    "page_note": ans.get("page_note")}
        rec["tier"] = tier
        rec["boxes"] = {str(k): b for k, b in ans["boxes"].items()}
        rec["page_note"] = ans.get("page_note")
        return rec, flag, cost

    page_records, flags = [], []
    cost = 0.0
    with ThreadPoolExecutor(max_workers=max(1, int(cfg["local"].get("page_concurrency", 48)))) as ex:
        for rec, flag, c in ex.map(one_page, jobs):
            page_records.append(rec)
            if flag:
                flags.append(flag)
            cost += c
    page_records.sort(key=lambda r: r["page"])
    flags.sort(key=lambda f: f["page"])
    secs = round(time.time() - t_start, 1)
    with open(os.path.join(out_dir, "pages.jsonl"), "w", encoding="utf-8") as f:
        for r in page_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "flags.jsonl"), "w", encoding="utf-8") as f:
        for fl in flags:
            f.write(json.dumps(fl, ensure_ascii=False) + "\n")
    doc = build_records(iid, pages, page_records, meta, variant)
    json.dump(doc, open(os.path.join(out_dir, "articles.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cmp = compare_with_rules(iid, pages, page_records, rl)
    write_json_atomic(os.path.join(out_dir, "compare.json"), cmp)
    low_labels = Counter()
    for r in page_records:
        low_labels.update(r.get("low_labels") or {})
    summary = {"issue": iid, "variant": variant, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pages": len(pages),
               "boxes": sum(r["n_boxes"] for r in page_records),
               "tiers": dict(Counter(r["tier"] for r in page_records)),
               "local_unreadable": sum(1 for r in page_records if (r.get("local") or {}).get("parsed") is False),
               "local_errors": sum(1 for r in page_records if "error" in (r.get("local") or {})),
               "local_retried": sum(1 for r in page_records if (r.get("local") or {}).get("attempts", 1) > 1),
               "local_cut_off": sum(1 for r in page_records if (r.get("local") or {}).get("finish") == "length"),
               "asked_again": sum(1 for r in page_records if r.get("asked_again")),
               "second_answered": sum(1 for r in page_records if r["tier"] in ("local2", "api")),
               "second_unreadable": sum(1 for r in page_records if (r.get("second") or {}).get("parsed") is False),
               "second_errors": sum(1 for r in page_records if "error" in (r.get("second") or {})),
               "second_seconds": round(sum((r.get("second") or {}).get("seconds") or 0 for r in page_records), 1),
               "doubtful_boxes": sum(sum(v for k, v in (r.get("settle") or {}).items() if k in ("settled_agreement", "settled_confidence", "unsettled")) for r in page_records),
               "settled_agreement": sum((r.get("settle") or {}).get("settled_agreement", 0) for r in page_records),
               "settled_confidence": sum((r.get("settle") or {}).get("settled_confidence", 0) for r in page_records),
               "unsettled_boxes": sum((r.get("settle") or {}).get("unsettled", 0) for r in page_records),
               "flagged_boxes": sum((r.get("settle") or {}).get("unsettled_flagged", 0) for r in page_records),
               "api_answered": sum(1 for r in page_records if r["tier"] == "api"),
               "api_unreadable": sum(1 for r in page_records if (r.get("api") or {}).get("parsed") is False),
               "api_failed": sum(1 for r in page_records if "error" in (r.get("api") or {})),
               "api_skipped": sum(1 for r in page_records if "skipped" in (r.get("api") or {})),
               "flagged_pages": len(flags), "cost_usd": round(cost, 4), "low_labels": dict(low_labels),
               "agreement_with_rules": cmp["agreement"], "type_agreement": cmp["type_agreement"], "type_boxes": cmp["type_boxes"],
               "boundary_agreement": cmp["boundary_agreement"], "boundary_boxes": cmp["boundary_boxes"],
               "records": doc["checks"]["records"], "story_records": doc["checks"]["story_records"], "seconds": secs}
    with _summary_lock:
        with open(os.path.join(ROOT, "data", "assembly_v2", variant, "summary.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    if mark_stage:
        mark(iid, mark_stage, **{k: v for k, v in summary.items() if k not in ("issue", "ts")})
    log_fn(f"{iid}: {len(pages)} pages, {summary['boxes']} boxes; with the rules: kind of box {fmt3(cmp['type_agreement'])}, "
           f"piece starts {fmt3(cmp['boundary_agreement'])}; unreadable {summary['local_unreadable']}, asked again {summary['asked_again']} "
           f"({summary['doubtful_boxes']} boxes: {summary['settled_agreement']} agreed, {summary['settled_confidence']} sure the second time, "
           f"{summary['unsettled_boxes']} open), flagged {len(flags)} pages, {secs}s, {doc['checks']['records']} records")
    return summary


def fmt3(x):
    return "-" if x is None else f"{x:.3f}"


def build_records(iid, pages, page_records, meta, variant=OUT_VARIANT):
    """The decisions -> records in the rules assembly's shape. "new" opens an editorial record; "previous", "caption"
    and "notice" go to the editorial record that is open (advertising and furniture in between leave it open);
    "advert" boxes form advertisement records (a run of them is one record; "new" with kind ad starts another)."""
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
                furniture.append({"page": pno, "idx": i})
                continue
            if j == "new" and (b.get("kind") or "") == "ad":
                ad_rec = None                                   # a new advertisement
                j = "advert"
            if j == "advert":
                if ad_rec is None:
                    ad_rec = new_rec("ad", title=" ".join(region_text(page["regions"][i]).split()[:12]) or None)
                    records.append(ad_rec)
                add(ad_rec, pno, i, None, conf, tier)
                continue
            ad_rec = None
            if j == "new":
                typ = KIND_TYPE.get((b.get("kind") or "story").lower(), "other")
                open_rec = new_rec(typ, b.get("title"), b.get("author"))
                if b.get("kind") == "serial":
                    open_rec["serial"] = True
                records.append(open_rec)
                add(open_rec, pno, i, "title" if b.get("title") else None, conf, tier)
                continue
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
    return {"issue": iid, "backend": "llm_link_v2", "variant": variant, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "magazine": meta.get("magazine"), "articles": records, "furniture": furniture, "unsorted": [],
            "checks": {"records": len(records), "story_records": sum(1 for r in records if r["type"] == "story"),
                       "unassigned_regions": 0, "double_owned_regions": 0, "flags": sum(len(r["flags"]) for r in records)}}


def model_type(b):
    j = b["joins"]
    if j == "furniture":
        return "furniture"
    if j == "advert" or (j == "new" and (b.get("kind") or "") == "ad"):
        return "ad"
    return "editorial"


def rules_type(lab):
    return {"furniture": "furniture", "advert": "ad", "unassigned": "unassigned"}.get(lab["joins"], "editorial")


def compare_with_rules(iid, pages, page_records, rl):
    """Box by box, two questions apart: what kind of box it is (editorial, advertising, furniture), and, for a box
    both call editorial, whether a piece begins there. The rules' side is rules_labels() (a piece runs on across
    furniture and advertising). The single "agreement" (the joins label itself) is kept for comparison."""
    pairs, type_pairs, boundary_pairs = Counter(), Counter(), Counter()
    agree = total = t_agree = t_total = b_agree = b_total = 0
    disagreements = []
    for pr in page_records:
        pno = pr["page"]
        keymap = {int(k): i for k, i in (pr.get("keymap") or {}).items()}
        for k in sorted(keymap):
            b = pr["boxes"].get(str(k))
            if not b:
                continue
            i = keymap[k]
            lab = rl.get(f"{pno}:{i}", {"joins": "unassigned"})
            rj, mj = lab["joins"], b["joins"]
            total += 1
            agree += (rj == mj) or rj == "unassigned"
            pairs[f"rules={rj} model={mj}"] += 1
            rt, mt = rules_type(lab), model_type(b)
            if rt != "unassigned":
                t_total += 1
                t_agree += rt == mt
                type_pairs[f"rules={rt} model={mt}"] += 1
            what = None
            if rt == "editorial" and mt == "editorial":
                rn, mn = rj == "new", mj == "new"
                b_total += 1
                b_agree += rn == mn
                boundary_pairs[f"rules={'new' if rn else 'continue'} model={'new' if mn else 'continue'}"] += 1
                if rn != mn:
                    what, r_say, m_say = "boundary", ("new" if rn else "continue"), ("new" if mn else "continue")
            elif rt not in ("unassigned", mt):
                what, r_say, m_say = "type", rt, mt
            if what and len(disagreements) < 600:
                disagreements.append({"what": what, "page": pno, "box": k, "rules": r_say, "model": m_say, "model_joins": mj,
                                      "confidence": b.get("confidence"), "why": b.get("why"), "tier": pr.get("tier"),
                                      "text": clip(region_text(pages[pno]["regions"][i]), 120, 60)})
    rnd = (lambda a, t: round(a / t, 4) if t else None)
    return {"boxes": total, "agree": agree, "agreement": rnd(agree, total), "pairs": dict(pairs.most_common()),
            "type_boxes": t_total, "type_agreement": rnd(t_agree, t_total), "type_pairs": dict(type_pairs.most_common()),
            "boundary_boxes": b_total, "boundary_agreement": rnd(b_agree, b_total), "boundary_pairs": dict(boundary_pairs.most_common()),
            "disagreements": disagreements}


# ----------------------------------------------------------------------------------------------------------------
# runs: a trial, the pilot issues
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


def read_summaries(variant):
    p = os.path.join(ROOT, "data", "assembly_v2", variant, "summary.jsonl")
    out = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                d = json.loads(line)
                out[d["issue"]] = d                         # the latest line for an issue counts
            except Exception:
                pass
    return out


def aggregate(summaries):
    S = list(summaries)
    tot = lambda k: sum((s.get(k) or 0) for s in S)        # noqa: E731

    def wavg(k, w):
        xs = [(s[k], s.get(w) or 0) for s in S if s.get(k) is not None]
        den = sum(w_ for _, w_ in xs)
        return round(sum(v * w_ for v, w_ in xs) / den, 4) if den else None
    tiers, low = Counter(), Counter()
    for s in S:
        tiers.update(s.get("tiers") or {})
        low.update(s.get("low_labels") or {})
    asked = max(1, tot("pages") - tiers.get("none", 0))
    again = tot("asked_again") or tot("api_wanted")            # api_wanted: the reports before p50l
    return {"issues_done": len(S), "pages": tot("pages"), "pages_asked": asked, "boxes": tot("boxes"), "tiers": dict(tiers),
            "local_unreadable": tot("local_unreadable"), "local_errors": tot("local_errors"), "local_retried": tot("local_retried"),
            "local_cut_off": tot("local_cut_off"), "asked_again": again, "second_answered": tot("second_answered"),
            "second_unreadable": tot("second_unreadable"), "second_errors": tot("second_errors"), "second_seconds": tot("second_seconds"),
            "doubtful_boxes": tot("doubtful_boxes"), "settled_agreement": tot("settled_agreement"),
            "settled_confidence": tot("settled_confidence"), "unsettled_boxes": tot("unsettled_boxes"), "flagged_boxes": tot("flagged_boxes"),
            "api_answered": tot("api_answered"), "api_unreadable": tot("api_unreadable"), "api_failed": tot("api_failed"),
            "api_skipped": tot("api_skipped"), "flagged_pages": tot("flagged_pages"), "cost_usd": round(tot("cost_usd"), 3),
            "unreadable_share": round(tot("local_unreadable") / asked, 4), "asked_again_share": round(again / asked, 4),
            "flag_share": round(tot("flagged_pages") / asked, 4),
            "type_agreement": wavg("type_agreement", "type_boxes"), "boundary_agreement": wavg("boundary_agreement", "boundary_boxes"),
            "agreement_with_rules": wavg("agreement_with_rules", "boxes"), "low_labels": dict(low.most_common()),
            "seconds_per_issue_mean": round(tot("seconds") / max(1, len(S)), 1)}


def run_many(ids, meta, variant, workers, redo, mark_stage):
    todo = [i for i in ids if redo or not os.path.exists(os.path.join(ROOT, "data", "assembly_v2", variant, i, "compare.json"))]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(link_issue, iid, meta.get(iid, {"id": iid}), variant, None, None, mark_stage): iid for iid in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                log("s12", f"{futs[f]}: FAILED {e!r}")
    return todo


def run_report(kind, variant, ids, tag, seconds, ran):
    sm = read_summaries(variant)
    lc = local_cfg()
    esc = settings()["llm_link"]["escalate"]
    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run": kind, "tag": tag, "variant": variant,
           "local_model": lc["model"], "lanes": [b for b, _m in lanes(lc)], "thinking": bool(lc.get("thinking")),
           "local_format": _LOCAL_FMT["level"] or "schema",
           "second_reading": (f"api {esc['model']}" if esc.get("enabled") else "local, thinking"),
           "thresholds": settings()["llm_link"]["thresholds"],
           "issues": len(ids), "ran_now": len(ran), "seconds_this_run": round(seconds, 1),
           "api_off": _API["off"], "api_spent_this_run": round(_API["run_usd"], 3)}
    rep.update(aggregate(sm[i] for i in ids if i in sm))
    rep["spend_total"] = spend()
    return rep


def trial(n, workers, redo=False, tag=None, same_as=None):
    load_pulp_env()
    from corpus_lib import corpus_config
    variant = f"llm_trial_{tag}" if tag else "llm_trial"
    meta = {i["id"]: i for i in corpus_config()["issues"]}
    if same_as:
        src = os.path.join(ROOT, "data", "assembly_v2", f"llm_trial_{same_as}")
        ids = sorted(d for d in os.listdir(src) if os.path.exists(os.path.join(src, d, "compare.json")))[:n]
    else:
        ids, _m = issues_assembled(n)
    lc = local_cfg()
    esc = settings()["llm_link"]["escalate"]
    log("s12", f"trial {tag or ''}: {len(ids)} issues{' (those of ' + same_as + ')' if same_as else ''}, {workers} at a time -> data/assembly_v2/{variant}; "
               f"local {lc['model']} on {len(lanes(lc))} lane(s), thinking {'on' if lc.get('thinking') else 'off'}; second reading: "
               + (f"the Claude API ({esc['model']})" if esc.get("enabled") else "the local model, thinking"))
    t0 = time.time()
    ran = run_many(ids, meta, variant, workers, redo, None)           # a trial leaves the issues' state files alone
    rep = run_report("trial", variant, ids, tag, time.time() - t0, ran)
    write_json_atomic(os.path.join(ROOT, "data", "corpus", f"llm_link_trial{('_' + tag) if tag else ''}.json"), rep)
    log("s12", "trial report: " + json.dumps({k: v for k, v in rep.items() if k not in ("spend_total", "thresholds")}))
    event("llm_link_trial", **{k: v for k, v in rep.items() if k != "spend_total"})
    return rep


def pilot(workers, redo=False, tag=None):
    """The ten pilot issues (config/pilot_issues.json), into data/assembly_v2/llm/ (llm_pilot_<tag> with a tag),
    where s09 scores them against the human-verified records and the contents pages."""
    load_pulp_env()
    cfg = json.load(open(PILOT_CONFIG, encoding="utf-8"))
    meta = {i["id"]: i for i in cfg["issues"]}
    ids = [i for i in meta if glob.glob(os.path.join(ROOT, "data", "layout", i, "page_*.json"))]
    variant = f"llm_pilot_{tag}" if tag else OUT_VARIANT
    log("s12", f"pilot{' ' + tag if tag else ''}: {len(ids)} issues, {workers} at a time -> data/assembly_v2/{variant}")
    t0 = time.time()
    ran = run_many(ids, meta, variant, workers, redo, None)
    rep = run_report("pilot", variant, ids, tag, time.time() - t0, ran)
    write_json_atomic(os.path.join(ROOT, "data", "corpus", f"llm_link_pilot{('_' + tag) if tag else ''}.json"), rep)
    log("s12", "pilot report: " + json.dumps({k: v for k, v in rep.items() if k not in ("spend_total", "thresholds")}))
    event("llm_link_pilot", **{k: v for k, v in rep.items() if k != "spend_total"})
    return rep


def selftest():
    txt = '```json\n{"boxes":[{"k":1,"joins":"furniture","confidence":0.99,"why":"running head"},{"k":2,"joins":"new","kind":"story","title":"The Red Moon","author":"A. Merritt","confidence":0.9,"why":"display title and by-line"},{"k":3,"joins":"previous","confidence":0.95,"why":"mid-sentence"}]}\n```'
    a = parse_answer(txt, 3)
    assert a and a["boxes"][2]["title"] == "The Red Moon" and a["boxes"][3]["joins"] == "previous" and a["boxes"][3]["kind"] is None, a
    assert parse_answer(txt, 4) is None and parse_answer("no json here", 1) is None
    broken = '{"boxes":[{"k":1,"joins":"new","kind":"story","confidence":0.95,"why":"title"},"k\\":2,\\"joins\\":\\"new\\"}"]}'
    assert parse_answer(broken, 2) is None                                   # the first trial's unreadable shape
    pages = {1: {"page": 1, "width": 1000, "height": 1500, "regions": [
        {"label": "PageHeader", "bbox": [0, 0, 1000, 40], "text": "WEIRD TALES"},
        {"label": "SectionHeader", "bbox": [0, 60, 1000, 160], "text": "The Red Moon"},
        {"label": "Text", "bbox": [0, 200, 480, 1400], "text": "By A. Merritt. The night was cold and the"},
        {"label": "Text", "bbox": [520, 200, 1000, 1400], "text": "moon rose red over the hills."}]},
             2: {"page": 2, "width": 1000, "height": 1500, "regions": [
        {"label": "Text", "bbox": [0, 60, 480, 1400], "text": "BUY WAR BONDS TODAY"},
        {"label": "Text", "bbox": [520, 60, 1000, 1400], "text": "Send one dollar to the address below"}]},
             3: {"page": 3, "width": 1000, "height": 1500, "regions": [
        {"label": "PageHeader", "bbox": [0, 0, 1000, 40], "text": "THE RED MOON"},
        {"label": "Text", "bbox": [0, 60, 480, 1400], "text": "and the wolves came down."},
        {"label": "Text", "bbox": [520, 60, 1000, 1400], "text": "THE END"}]}}
    story = {"article_id": "t_a1", "type": "story", "title": "The Red Moon", "author": "A. Merritt", "roles": {"1:1": "title", "3:2": "note"},
             "fragments": [{"page": 1, "region_ids": [1, 2, 3]}, {"page": 3, "region_ids": [1, 2]}]}
    ad = {"article_id": "t_a2", "type": "ad", "ad_class": "mail order", "roles": {}, "fragments": [{"page": 2, "region_ids": [0, 1]}]}
    owner = {}
    for rec in (story, ad):
        for fr in rec["fragments"]:
            for i in fr["region_ids"]:
                owner[f"{fr['page']}:{i}"] = (rec, rec["roles"].get(f"{fr['page']}:{i}"))
    furn = {"1:0", "3:0"}
    rl = rules_labels(pages, owner, furn)
    assert [rl[k]["joins"] for k in ("1:0", "1:1", "1:2", "1:3", "2:0", "2:1", "3:0", "3:1", "3:2")] == \
        ["furniture", "new", "previous", "previous", "advert", "advert", "furniture", "previous", "notice"], rl
    op = rules_open_piece(pages, owner, furn, 3, {})
    assert op and op["page"] == 1 and op["skipped"] == 1 and op["title"] == "The Red Moon", op         # across the advertising page
    prompt, keymap = page_prompt("t", {"magazine": "Weird Tales", "cover_date": "1934-05"}, 3, pages[3], pages, rl, op, settings()["llm_link"]["context"])
    assert "[2] label=Text" in prompt and "rule-based decision: continues the story \"The Red Moon\"" in prompt and "page 1 (the 1 page(s) between" in prompt, prompt
    prs = [{"page": 1, "n_boxes": 4, "keymap": {"1": 0, "2": 1, "3": 2, "4": 3}, "tier": "local", "boxes": {
        "1": {"joins": "furniture", "confidence": 0.99}, "2": {"joins": "new", "kind": "story", "title": "The Red Moon", "author": "A. Merritt", "confidence": 0.9},
        "3": {"joins": "previous", "confidence": 0.9}, "4": {"joins": "previous", "confidence": 0.95}}},
           {"page": 2, "n_boxes": 2, "keymap": {"1": 0, "2": 1}, "tier": "local", "boxes": {
        "1": {"joins": "new", "kind": "ad", "confidence": 0.8}, "2": {"joins": "advert", "confidence": 0.8}}},
           {"page": 3, "n_boxes": 3, "keymap": {"1": 0, "2": 1, "3": 2}, "tier": "local", "boxes": {
        "1": {"joins": "furniture", "confidence": 0.99}, "2": {"joins": "previous", "confidence": 0.97}, "3": {"joins": "notice", "confidence": 0.9}}}]
    doc = build_records("t", pages, prs, {"magazine": "Weird Tales"})
    recs = doc["articles"]
    assert len(recs) == 2 and recs[0]["type"] == "story" and recs[0]["pages"] == [1, 3] and recs[1]["type"] == "ad" and recs[1]["pages"] == [2], recs
    assert "moon rose red" in recs[0]["text"] and "wolves came down" in recs[0]["text"] and "THE END" not in recs[0]["text"], recs[0]
    cmp = compare_with_rules("t", pages, prs, rl)
    assert cmp["type_agreement"] == 1.0 and cmp["boundary_agreement"] == 1.0 and cmp["boundary_boxes"] == 5, cmp
    fb = fallback_answer(3, {1: 0, 2: 1, 3: 2}, rl)
    assert [fb["boxes"][k]["joins"] for k in (1, 2, 3)] == ["furniture", "previous", "notice"] and fb["boxes"][2]["confidence"] == 0.0, fb
    assert local_max_tokens({"max_tokens": 4000, "max_tokens_cap": 12000}, 19, False) == 4000
    assert local_max_tokens({"max_tokens": 4000, "max_tokens_cap": 12000}, 100, False) == 6400
    assert local_max_tokens({"max_tokens": 4000, "max_tokens_cap": 12000}, 300, False) == 12000
    agg = aggregate([{"pages": 10, "boxes": 100, "tiers": {"local": 8, "none": 2}, "asked_again": 2, "type_agreement": 0.9, "type_boxes": 100,
                      "boundary_agreement": 0.8, "boundary_boxes": 50, "agreement_with_rules": 0.85, "seconds": 10}])
    assert agg["pages_asked"] == 8 and agg["asked_again_share"] == 0.25 and agg["type_agreement"] == 0.9, agg
    # parse with the boxes asked for
    two = '{"boxes":[{"k":3,"joins":"previous","confidence":0.9},{"k":5,"joins":"new","kind":"poem","confidence":0.7}]}'
    a2 = parse_answer(two, 6, need={3, 5})
    assert a2 and set(a2["boxes"]) == {3, 5} and a2["boxes"][5]["kind"] == "poem" and parse_answer(two, 6, need={3, 4}) is None, a2
    # the settling of doubtful boxes
    first = {"boxes": {1: {"joins": "furniture", "confidence": 0.99}, 2: {"joins": "new", "confidence": 0.7},
                       3: {"joins": "previous", "confidence": 0.6}, 4: {"joins": "caption", "confidence": 0.5},
                       5: {"joins": "previous", "confidence": 0.8}}, "page_note": None}
    second = {"boxes": {2: {"joins": "new", "confidence": 0.6}, 3: {"joins": "previous", "confidence": 0.95},
                        4: {"joins": "notice", "confidence": 0.5}, 5: {"joins": "new", "confidence": 0.6}}, "page_note": None}
    fb = {"boxes": {k: {"joins": "previous", "confidence": 0.0} for k in range(1, 6)}}
    ans, fl, c = settle(first, second, {2, 3, 4, 5}, 0.85, {"previous", "new", "advert"}, fb)
    assert c["settled_agreement"] == 2 and c["unsettled"] == 2 and set(fl) == {5}, (c, fl)     # 2 and 3 agree; 4 is a caption/notice doubt (no flag); 5 is open
    assert ans["boxes"][5]["joins"] == "new" and ans["boxes"][1]["joins"] == "furniture" and ans["boxes"][3]["agreed"], ans
    ans, fl, c = settle(None, None, {1, 2}, 0.85, {"previous", "new", "advert"}, fb)
    assert set(fl) == {1, 2} and ans["boxes"][1]["confidence"] == 0.0, fl                    # nothing could be read: the rules, flagged
    ans, fl, c = settle(first, None, {3, 4}, 0.85, {"previous", "new", "advert"}, fb)
    assert set(fl) == {3} and ans["boxes"][3]["joins"] == "previous", fl                       # the second reading failed: the first stands, flagged where it matters
    print("s12 selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue")
    ap.add_argument("--pilot", action="store_true", help="the pilot issues -> data/assembly_v2/llm (then s09 --all scores the llm variant)")
    ap.add_argument("--trial", type=int, metavar="N", help="the first N assembled issues (or N of those of --same-as)")
    ap.add_argument("--tag", help="a trial's (or pilot run's) name: outputs in data/assembly_v2/llm_trial_<tag> (llm_pilot_<tag>), report data/corpus/llm_link_trial_<tag>.json (llm_link_pilot_<tag>.json)")
    ap.add_argument("--same-as", metavar="TAG", help="with --trial: the issues of the trial TAG, for a comparison on the same issues")
    ap.add_argument("--workers", type=int, default=0, help="issues at a time (default settings.llm_link.local.concurrency)")
    ap.add_argument("--redo", action="store_true", help="ask again for issues already done in this variant")
    ap.add_argument("--dry-run", action="store_true", help="with --issue and --page: print the prompt, call nothing")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    load_pulp_env()
    cfg = settings()["llm_link"]
    workers = args.workers or cfg["local"].get("concurrency", 2)
    if args.issue:
        _ids, meta = issues_assembled()
        m = meta.get(args.issue) or {"id": args.issue}
        if args.dry_run:
            link_issue(args.issue, m, dry_run_page=args.page)
            return
        with stage_timer("s12_llm_link", args.issue):
            link_issue(args.issue, m)
        return
    if args.pilot:
        pilot(workers, redo=args.redo, tag=args.tag)
        return
    if args.trial:
        trial(args.trial, workers, redo=args.redo, tag=args.tag, same_as=args.same_as)
        return
    sys.exit("pass --issue <id>, --pilot, --trial N, or --selftest")


if __name__ == "__main__":
    main()
