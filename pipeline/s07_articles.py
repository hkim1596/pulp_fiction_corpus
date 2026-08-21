#!/usr/bin/env python3
"""Stage 7 — assemble the article database from layout regions.

The final form of the corpus: every printed unit on every page — stories,
serial installments, poems, features, letters pages, and advertisements —
becomes a separately addressable ARTICLE with a type, a title and author as
printed, its pages, its assembled cleaned text, and the exact regions on the
scans it was built from. Page furniture (page numbers, running heads) is
recorded, not silently dropped; segments the model cannot place go to an
"unsorted" list for human review.

Two passes, both through an LLM (default: the local Qwen lane; --backend
claude uses the API; --mock uses deterministic heuristics for testing
without any model):

  Pass A, per page: group that page's numbered segments into units —
    article fragments (with candidate title/author, and whether the unit
    continues from an earlier page), advertisements, furniture, unsorted.
  Pass B, per window of pages: stitch fragments across pages into whole
    articles (serials and stories rarely end on the page they start).

Input : data/layout/<id>/page_NNNN.json   (from s02, route A)
Output: data/articles/<id>/articles.json
        data/articles/index.json          (flat index across issues)

Text of each article = its regions' text in reading order, cleaned with the
same primitives as stage 4 (character normalization, dehyphenation,
paragraph joining) applied within the article.
"""
import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from timing_util import stage_timer, ROOT, load_pulp_env
from s04_rules import normalize_chars, looks_like_word

WINDOW = 40          # pages per stitching call
SNIP = 180           # characters of segment text shown to the model
ART_TYPES = ["story", "serial_part", "poem", "feature", "letters",
             "toc", "ad", "other"]

PASS_A_PROMPT = """You are indexing one scanned page of a twentieth-century pulp fiction magazine.
Below are the page's segments in reading order, each with an id, a layout label, and its text (possibly with OCR errors).
Group the segments into units and classify each unit. Rules:
- kind must be one of: fragment (part of a story/article/poem/feature), ad (advertisement), furniture (page number, running head, decorative line), unsorted (cannot tell).
- A unit of kind fragment gets: title (the displayed title exactly as printed, or null if this page shows no title for it), author (as printed, or null), continues_previous (true if the text clearly continues a unit from an earlier page, e.g. starts mid-sentence or is labeled "continued").
- An ornamental or display title segment belongs in the SAME unit as the body text it introduces.
- Do not correct or rewrite any text. Copy titles and authors exactly as printed.
Answer with JSON only, no other words:
{"units":[{"segments":[ids...],"kind":"fragment|ad|furniture|unsorted","title":null,"author":null,"continues_previous":false}]}

SEGMENTS:
"""

PASS_B_PROMPT = """You are assembling a scanned pulp magazine's table of contents from page-level fragments.
Below is an ordered list of fragments (page number, fragment index, candidate title, candidate author, whether it continues an earlier fragment, first and last words).
Join fragments that belong to the same printed work. Rules:
- Each article gets: type (one of story, serial_part, poem, feature, letters, toc, ad, other), title (as printed; null only if truly untitled), author (as printed or null), fragments (ordered list of [page, fragment_index]).
- A fragment marked continues_previous joins the nearest earlier open article whose text it plausibly continues.
- Advertisements stay separate articles of type ad, one per advertisement.
Answer with JSON only, no other words:
{"articles":[{"type":"story","title":null,"author":null,"fragments":[[page,frag],...]}]}

FRAGMENTS:
"""


# ---------------- model backends ----------------

def call_llm(prompt, backend):
    for attempt in range(3):
        try:
            if backend == "claude":
                body = {"model": os.environ.get("PULP_CLAUDE_MODEL",
                                                "claude-haiku-4-5"),
                        "max_tokens": 4096, "temperature": 0.0,
                        "messages": [{"role": "user", "content": prompt}]}
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json",
                             "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                             "anthropic-version": "2023-06-01"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    return json.load(r)["content"][0]["text"]
            base = os.environ["PULP_QWEN_BASE_URL"].rstrip("/")
            body = {"model": os.environ["PULP_QWEN_MODEL"],
                    "temperature": 0.0, "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}]}
            req = urllib.request.Request(
                base + "/chat/completions", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization":
                         f"Bearer {os.environ.get('PULP_QWEN_KEY', 'none')}"})
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.load(r)["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  llm retry {attempt+1}: {e}")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("llm failed 3x")


def parse_json_reply(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no json in reply")
    return json.loads(m.group(0))


def llm_json(prompt, backend):
    """Call, parse; on broken JSON ask once more with the error appended."""
    reply = call_llm(prompt, backend)
    try:
        return parse_json_reply(reply)
    except Exception as e:
        reply = call_llm(prompt + f"\n\nYour previous answer was not valid "
                         f"JSON ({e}). Answer again with JSON only.", backend)
        return parse_json_reply(reply)


# ---------------- mock backend (testing without a model) ----------------

def mock_pass_a(regions):
    units, cur = [], None
    for i, rg in enumerate(regions):
        lab = re.sub(r"[^a-z]", "", rg["label"].lower())
        txt = rg.get("text", "")
        if not txt.strip():
            continue
        if lab in ("pageheader", "pagefooter") or re.fullmatch(r"\d{1,4}",
                                                               txt.strip()):
            units.append({"segments": [i], "kind": "furniture",
                          "title": None, "author": None,
                          "continues_previous": False})
        elif "advertisement" in txt.lower() or txt.isupper() and "$" in txt:
            units.append({"segments": [i], "kind": "ad", "title": None,
                          "author": None, "continues_previous": False})
        elif lab in ("sectionheader", "title"):
            cur = {"segments": [i], "kind": "fragment",
                   "title": txt.strip().split("\n")[0][:120], "author": None,
                   "continues_previous": False}
            units.append(cur)
        else:
            m = re.match(r"\s*[Bb]y\s+([A-Z][\w. ]+)", txt)
            if cur is not None:
                cur["segments"].append(i)
                if m and not cur["author"]:
                    cur["author"] = m.group(1).strip()
            else:
                units.append({"segments": [i], "kind": "fragment",
                              "title": None, "author": None,
                              "continues_previous": True})
                cur = units[-1]
    return {"units": units}


def mock_pass_b(frags):
    arts, open_art = [], None
    for f in frags:
        if f["kind"] == "ad":
            arts.append({"type": "ad", "title": f.get("title"),
                         "author": None,
                         "fragments": [[f["page"], f["idx"]]]})
        elif f["continues_previous"] and open_art:
            open_art["fragments"].append([f["page"], f["idx"]])
        else:
            open_art = {"type": "story", "title": f.get("title"),
                        "author": f.get("author"),
                        "fragments": [[f["page"], f["idx"]]]}
            arts.append(open_art)
    return {"articles": arts}


# ---------------- assembly ----------------

def clean_text(text):
    lines = [normalize_chars(l) for l in text.splitlines()]
    # dehyphenate across line ends, keep punctuation attached
    for i in range(len(lines) - 1):
        m = re.search(r"([A-Za-z]{2,})-$", lines[i])
        if not m:
            continue
        nxt = lines[i + 1].lstrip()
        m2 = re.match(r"([A-Za-z]+)([^\sA-Za-z]*)(.*)", nxt)
        if m2 and looks_like_word(m.group(1) + m2.group(1)):
            lines[i] = lines[i][:m.start(1)] + m.group(1) + m2.group(1) + m2.group(2)
            lines[i + 1] = m2.group(3).lstrip()
    paras, buf = [], []
    for l in lines:
        s = l.strip()
        if not s:
            if buf:
                paras.append(" ".join(buf)); buf = []
            continue
        buf.append(s)
    if buf:
        paras.append(" ".join(buf))
    return "\n\n".join(paras)


def run_issue(iid, backend):
    laydir = os.path.join(ROOT, "data", "layout", iid)
    pagefiles = sorted(glob.glob(os.path.join(laydir, "page_*.json")))
    if not pagefiles:
        print(f"[s07] {iid}: no layout pages, run s02 first"); return

    frags, furniture, unsorted_ = [], [], []
    pageregions = {}
    with stage_timer("s07_articles", iid, pages=len(pagefiles),
                     extra={"backend": backend}):
        # pass A
        for pf in pagefiles:
            page = json.load(open(pf, encoding="utf-8"))
            pno = page["page"]
            regions = sorted(page["regions"], key=lambda r: r["order"])
            pageregions[pno] = regions
            if backend == "mock":
                out = mock_pass_a(regions)
            else:
                seglines = []
                for i, rg in enumerate(regions):
                    t = (rg.get("text") or "").strip().replace("\n", " ")
                    seglines.append(f"[{i}] label={rg['label']} "
                                    f"text=\"{t[:SNIP]}\"")
                out = llm_json(PASS_A_PROMPT + "\n".join(seglines), backend)
            for ui, u in enumerate(out.get("units", [])):
                segs = [s for s in u.get("segments", [])
                        if isinstance(s, int) and 0 <= s < len(regions)]
                if not segs:
                    continue
                rec = {"page": pno, "idx": ui, "segments": segs,
                       "kind": u.get("kind", "unsorted"),
                       "title": u.get("title"), "author": u.get("author"),
                       "continues_previous": bool(u.get("continues_previous"))}
                if rec["kind"] == "furniture":
                    furniture.append(rec)
                elif rec["kind"] == "unsorted":
                    unsorted_.append(rec)
                else:
                    txt = " ".join((pageregions[pno][s].get("text") or "")
                                   for s in segs)
                    words = txt.split()
                    rec["first_words"] = " ".join(words[:12])
                    rec["last_words"] = " ".join(words[-12:])
                    frags.append(rec)

        # pass B, windowed
        articles = []
        for w0 in range(0, len(frags), WINDOW * 6):
            chunk = frags[w0:w0 + WINDOW * 6]
            if backend == "mock":
                out = mock_pass_b(chunk)
            else:
                lines = []
                for f in chunk:
                    lines.append(
                        f"page={f['page']} frag={f['idx']} kind={f['kind']} "
                        f"title={json.dumps(f.get('title'))} "
                        f"author={json.dumps(f.get('author'))} "
                        f"continues={f['continues_previous']} "
                        f"first=\"{f.get('first_words','')}\" "
                        f"last=\"{f.get('last_words','')}\"")
                out = llm_json(PASS_B_PROMPT + "\n".join(lines), backend)
            articles.extend(out.get("articles", []))

        # build final records
        fragmap = {(f["page"], f["idx"]): f for f in frags}
        final = []
        used = set()
        for k, art in enumerate(articles, 1):
            fkeys = [tuple(x) for x in art.get("fragments", [])
                     if tuple(x) in fragmap]
            if not fkeys:
                continue
            used.update(fkeys)
            parts, fragrec, pages = [], [], []
            for fk in fkeys:
                f = fragmap[fk]
                pages.append(f["page"])
                fragrec.append({"page": f["page"],
                                "region_ids": f["segments"]})
                for s in f["segments"]:
                    parts.append(pageregions[f["page"]][s].get("text") or "")
            atype = art.get("type") if art.get("type") in ART_TYPES else "other"
            final.append({
                "article_id": f"{iid}_a{k:03d}",
                "type": atype,
                "title": art.get("title"),
                "author": art.get("author"),
                "pages": sorted(set(pages)),
                "fragments": fragrec,
                "text": clean_text("\n".join(parts)),
            })
        # fragments no article claimed -> unsorted, never lost
        for fk, f in fragmap.items():
            if fk not in used:
                unsorted_.append(f)

    outdir = os.path.join(ROOT, "data", "articles", iid)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "articles.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"issue": iid, "backend": backend,
                   "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "articles": final, "furniture": furniture,
                   "unsorted": unsorted_}, fh, ensure_ascii=False, indent=1)
    print(f"[s07] {iid}: {len(final)} articles "
          f"({sum(1 for a in final if a['type']=='ad')} ads), "
          f"{len(furniture)} furniture, {len(unsorted_)} unsorted")


def build_index():
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "articles", "*",
                                           "articles.json"))):
        d = json.load(open(f, encoding="utf-8"))
        for a in d["articles"]:
            rows.append({"article_id": a["article_id"], "issue": d["issue"],
                         "type": a["type"], "title": a.get("title"),
                         "author": a.get("author"), "pages": a["pages"],
                         "words": len(a.get("text", "").split())})
    idx = os.path.join(ROOT, "data", "articles", "index.json")
    os.makedirs(os.path.dirname(idx), exist_ok=True)
    with open(idx, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    print(f"[s07] index: {len(rows)} articles across "
          f"{len(set(r['issue'] for r in rows))} issues")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--backend", default="qwen",
                    choices=["qwen", "claude", "mock"])
    args = ap.parse_args()
    load_pulp_env()
    if args.backend == "qwen" and not os.environ.get("PULP_QWEN_BASE_URL"):
        sys.exit("missing PULP_QWEN_BASE_URL — fill ~/shared/khj/.pulp_env")
    if args.backend == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("missing ANTHROPIC_API_KEY — fill ~/shared/khj/.pulp_env")
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"),
                         encoding="utf-8"))
    ids = [i["id"] for i in cfg["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, args.backend)
    build_index()


if __name__ == "__main__":
    main()
