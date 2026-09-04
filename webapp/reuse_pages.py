"""Reuse pages for the pilot website (v0.9.0): the text-reuse pipeline's
results (pipeline/r02-r05) shown the same way as the rest of the site —
every number traceable to a file, every passage traceable to an article
and its scan. Dependency-free; charts are inline SVG drawn here.

Three levels, as decided 2026-08-31:
  /reuse                       overview: numbers, charts, sensitivity, planted
                               reuse, background, assembly diagnostics
  /reuse/clusters              cluster list with filters
  /reuse/cluster/<set>/<kind>/<k>/<n>   one cluster, witnesses side by side
plus
  /reuse/progress              annotation progress, pipeline status board,
                               process board (every issue at every step)

The module is bound to the site's helpers with bind(globals()) from app.py,
so it uses the same page frame, escaping, and data access.
"""
import difflib
import glob
import json
import math
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict

_G = {}          # app.py globals (esc, howto, page, effective_doc, ...)
_CACHE = {}


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)


def _esc(s):
    return _G["esc"](s)


def _howto(t):
    return _G["howto"](t)


def _reuse_dir():
    return os.path.join(_G["DATA"], "reuse")


# ---------------------------------------------------------------- data files

def _load(rel, kind="json"):
    """Parsed file under data/reuse, cached by modification time."""
    p = os.path.join(_reuse_dir(), rel)
    if not os.path.exists(p):
        return None
    mt = os.path.getmtime(p)
    key = (p, kind)
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        if kind == "json":
            val = json.load(open(p, encoding="utf-8"))
        else:
            val = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    except Exception:
        val = None
    _CACHE[key] = (mt, val)
    return val


def _runs():
    """Everything the pipeline has written, discovered from the files."""
    d = _reuse_dir()
    runs = {"exact": [], "para": [], "synthetic": {}, "background": [], "overlap": {}}
    if not os.path.isdir(d):
        return runs
    for f in sorted(glob.glob(os.path.join(d, "*_k*_stats.json"))):
        st = _load(os.path.basename(f))
        if st:
            runs["exact"].append(st)
    order = {"machine": 0, "corrected": 1, "verified": 2}
    runs["exact"].sort(key=lambda st: (order.get(st["set"], 9), st["k"]))
    for f in sorted(glob.glob(os.path.join(d, "para", "*_stats.json"))):
        st = _load(os.path.join("para", os.path.basename(f)))
        if st:
            st["_tag"] = os.path.basename(f).rsplit("_k", 1)[0]
            runs["para"].append(st)
    runs["para"].sort(key=lambda st: (order.get(st["set"], 9), st["_tag"], st["k"]))
    rv = _load(os.path.join("synthetic", "recall_verbatim.json"))
    if rv:
        runs["synthetic"]["exact"] = rv
    for f in sorted(glob.glob(os.path.join(d, "para", "synthetic", "recall_paraphrase_*.json"))):
        rp = _load(os.path.join("para", "synthetic", os.path.basename(f)))
        if rp:
            runs["synthetic"].setdefault("para", {})[os.path.basename(f)[len("recall_paraphrase_"):-5]] = rp
    for f in sorted(glob.glob(os.path.join(d, "background", "summary_*.json"))):
        s = _load(os.path.join("background", os.path.basename(f)))
        if s:
            runs["background"].append(s)
    for f in sorted(glob.glob(os.path.join(d, "*_region_overlap.json"))):
        o = _load(os.path.basename(f))
        if o:
            runs["overlap"][os.path.basename(f).split("_region_overlap")[0]] = o
    return runs


def _sets(runs):
    return sorted({st["set"] for st in runs["exact"]}, key=lambda s: {"machine": 0, "corrected": 1, "verified": 2}.get(s, 9))


def _exact_files(set_, k):
    pre = f"{set_}_k{k}"
    return (_load(pre + "_clusters.json"), _load(pre + "_matches.jsonl", "jsonl"),
            _load(pre + "_sameissue.jsonl", "jsonl"), _load(pre + "_stats.json"))


def _para_tag(runs, set_):
    tags = sorted({st["_tag"] for st in runs["para"] if st["set"] == set_})
    main = [t for t in tags if t.endswith("_w50s25")]
    return (main or tags or [None])[0]


def _para_files(tag, k):
    pre = os.path.join("para", f"{tag}_k{k}")
    return (_load(pre + "_clusters.json"), _load(pre + "_alignments.jsonl", "jsonl"),
            _load(pre + "_sameissue.jsonl", "jsonl"), _load(pre + "_stats.json"))


# ---------------------------------------------------------------- charts

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]   # fixed order, never cycled
INK, INK2, GRID = "var(--ink)", "var(--muted)", "var(--grid)"


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:,.0f}"
        if abs(v) >= 1:
            return f"{v:,.1f}".rstrip("0").rstrip(".")
        return f"{v:.3g}"
    return f"{v:,}"


def _ticks(vmax, n=4):
    if vmax <= 0:
        return [0, 1]
    if float(vmax).is_integer() and vmax <= 6:
        return list(range(0, int(vmax) + 1))
    raw = vmax / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=10) * mag
    top = step * math.ceil(vmax / step)
    t, x = [], 0.0
    while x <= top + 1e-9:
        t.append(round(x, 6))
        x += step
    return t


def svg_bars(categories, series, title, width=520, height=230, unit=""):
    """Grouped bars. series = [(name, [values...])] in fixed palette order.
    Values are shown at bar caps only when there are few bars; the table
    beside the chart carries every number."""
    n_cat, n_ser = len(categories), len(series)
    if not n_cat or not n_ser:
        return ""
    rotate = max(len(str(c)) for c in categories) * 6.5 * n_cat > width - 60
    left, right, top, bottom = 48, 12, 30, (78 if rotate else 44)
    if rotate:
        height += 34
    pw, ph = width - left - right, height - top - bottom
    vmax = max([0] + [v for _, vals in series for v in vals if v is not None])
    ticks = _ticks(vmax)
    vtop = ticks[-1] or 1
    band = pw / n_cat
    barw = min(24, (band * 0.7) / n_ser)
    gap = 2
    parts = [f"<svg viewBox='0 0 {width} {height}' width='100%' style='max-width:{width}px' "
             f"role='img' aria-label='{_esc(title)}' font-family='system-ui,-apple-system,Segoe UI,sans-serif'>",
             f"<text x='{left}' y='16' font-size='13' fill='{INK}'>{_esc(title)}</text>"]
    for t in ticks:
        y = top + ph - ph * t / vtop
        parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{y:.1f}' y2='{y:.1f}' "
                     f"stroke='{GRID}' stroke-width='1'/>")
        parts.append(f"<text x='{left - 6}' y='{y + 4:.1f}' font-size='11' fill='{INK2}' "
                     f"text-anchor='end'>{_fmt(t)}</text>")
    show_labels = n_ser <= 2 and n_cat * n_ser <= 12 and barw >= 18
    for ci, cat in enumerate(categories):
        x0 = left + ci * band + (band - (barw * n_ser + gap * (n_ser - 1))) / 2
        for si, (name, vals) in enumerate(series):
            v = vals[ci] if ci < len(vals) and vals[ci] is not None else 0
            h = ph * v / vtop
            x = x0 + si * (barw + gap)
            y = top + ph - h
            col = PALETTE[si % len(PALETTE)]
            r = min(4, barw / 2, h / 2) if h > 0 else 0
            # rounded top, square base
            path = (f"M{x:.1f},{top + ph:.1f} v{-max(h - r, 0):.1f} q0,{-r} {r},{-r} "
                    f"h{barw - 2 * r:.1f} q{r},0 {r},{r} v{max(h - r, 0):.1f} z") if h > 0 else ""
            if path:
                parts.append(f"<path d='{path}' fill='{col}'><title>{_esc(name)} · {_esc(cat)}: "
                             f"{_fmt(v)}{unit}</title></path>")
            if show_labels and v:
                parts.append(f"<text x='{x + barw / 2:.1f}' y='{y - 4:.1f}' font-size='11' "
                             f"fill='{INK2}' text-anchor='middle'>{_fmt(v)}{unit}</text>")
        cx = left + ci * band + band / 2
        if rotate:
            parts.append(f"<text x='{cx:.1f}' y='{top + ph + 14}' font-size='11' fill='{INK}' "
                         f"text-anchor='end' transform='rotate(-35 {cx:.1f} {top + ph + 14})'>{_esc(cat)}</text>")
        else:
            parts.append(f"<text x='{cx:.1f}' y='{top + ph + 16}' "
                         f"font-size='11.5' fill='{INK}' text-anchor='middle'>{_esc(cat)}</text>")
    parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{top + ph}' y2='{top + ph}' "
                 f"stroke='{INK2}' stroke-width='1'/>")
    if n_ser >= 2:
        lx = left
        for si, (name, _) in enumerate(series):
            parts.append(f"<rect x='{lx}' y='{height - 12}' width='10' height='10' "
                         f"fill='{PALETTE[si % len(PALETTE)]}'/>")
            parts.append(f"<text x='{lx + 14}' y='{height - 3}' font-size='11' fill='{INK2}'>"
                         f"{_esc(name)}</text>")
            lx += 22 + 6.5 * len(name)
    parts.append("</svg>")
    return "".join(parts)


def svg_lines(xs, series, title, width=520, height=240, ylog=False, xlabel="", xlabels=None):
    """Lines over a shared x. series = [(name, [y...])]. ylog draws a log10
    axis for probabilities (zeros are dropped from the line). xlabels, when
    given, are the tick texts for each x (dates, for instance)."""
    if not xs or not series:
        return ""
    left, right, top, bottom = 56, 16, (46 if len(series) >= 2 else 30), 42
    if len(series) >= 2:
        height += 16
    pw, ph = width - left - right, height - top - bottom
    xmin, xmax = min(xs), max(xs)
    ys = [y for _, vals in series for y in vals if y is not None and (y > 0 or not ylog)]
    if not ys:
        return ""
    if ylog:
        ymin = 10 ** math.floor(math.log10(min(ys)))
        ymax = 1.0 if max(ys) <= 1 else 10 ** math.ceil(math.log10(max(ys)))
        yticks = []
        v = ymin
        while v <= ymax + 1e-12:
            yticks.append(v)
            v *= 10

        def ypos(y):
            return top + ph - ph * (math.log10(y) - math.log10(ymin)) / max(1e-9, math.log10(ymax) - math.log10(ymin))
    else:
        yticks = _ticks(max(ys))
        ymax = yticks[-1] or 1

        def ypos(y):
            return top + ph - ph * y / ymax

    def xpos(x):
        return left + pw * (x - xmin) / max(1e-9, xmax - xmin)
    parts = [f"<svg viewBox='0 0 {width} {height}' width='100%' style='max-width:{width}px' "
             f"role='img' aria-label='{_esc(title)}' font-family='system-ui,-apple-system,Segoe UI,sans-serif'>",
             f"<text x='{left}' y='16' font-size='13' fill='{INK}'>{_esc(title)}</text>"]
    if len(series) >= 2:                      # legend, top right, one row
        lx = width - right
        for si in range(len(series) - 1, -1, -1):
            name = series[si][0]
            w = 22 + 6.3 * len(name)
            lx -= w
            parts.append(f"<rect x='{lx:.1f}' y='{top - 20}' width='10' height='10' "
                         f"fill='{PALETTE[si % len(PALETTE)]}'/>")
            parts.append(f"<text x='{lx + 14:.1f}' y='{top - 11}' font-size='11' fill='{INK2}'>"
                         f"{_esc(name)}</text>")
    for t in yticks:
        y = ypos(t)
        parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{y:.1f}' y2='{y:.1f}' stroke='{GRID}'/>")
        parts.append(f"<text x='{left - 6}' y='{y + 4:.1f}' font-size='11' fill='{INK2}' "
                     f"text-anchor='end'>{_fmt(t)}</text>")
    if xlabels:
        step = max(1, math.ceil(len(xs) / 8))
        idxs = list(range(0, len(xs), step))
        if idxs[-1] != len(xs) - 1:
            idxs.append(len(xs) - 1)
        for i in idxs:
            parts.append(f"<text x='{xpos(xs[i]):.1f}' y='{top + ph + 15}' font-size='11' fill='{INK2}' "
                         f"text-anchor='middle'>{_esc(xlabels[i])}</text>")
    else:
        xt = sorted(set([xs[0], xs[len(xs) // 2], xs[-1]] + [x for x in xs if x % 5 == 0 and len(xs) <= 40]))
        for x in xt:
            parts.append(f"<text x='{xpos(x):.1f}' y='{top + ph + 15}' font-size='11' fill='{INK2}' "
                         f"text-anchor='middle'>{_fmt(x)}</text>")
    if xlabel:
        parts.append(f"<text x='{left + pw / 2:.1f}' y='{height - 3}' font-size='11' fill='{INK2}' "
                     f"text-anchor='middle'>{_esc(xlabel)}</text>")
    for si, (name, vals) in enumerate(series):
        col = PALETTE[si % len(PALETTE)]
        pts = [(xpos(x), ypos(y), x, y) for x, y in zip(xs, vals)
               if y is not None and (y > 0 or not ylog)]
        if not pts:
            continue
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in pts)
        parts.append(f"<path d='{d}' fill='none' stroke='{col}' stroke-width='2' "
                     f"stroke-linejoin='round' stroke-linecap='round'/>")
        for x, y, xv, yv in pts:
            lab = xlabels[xs.index(xv)] if xlabels else _fmt(xv)
            parts.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='{col}' stroke='var(--page)' "
                         f"stroke-width='2'><title>{_esc(name)} · {_esc(lab)}: {_fmt(yv)}</title></circle>")
        if len(series) == 1:
            ex, ey = pts[-1][0], pts[-1][1]
            parts.append(f"<text x='{ex - 6:.1f}' y='{ey - 8:.1f}' font-size='11' fill='{INK2}' "
                         f"text-anchor='end'>{_fmt(pts[-1][3])}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _chart_row(chart, table, stack=False):
    # a wide table (more than seven columns) goes under the chart rather than beside it
    if stack or table.count("<th") > 7:
        return (f"<div style='margin:6px 0 16px'><div style='max-width:560px'>{chart}</div>"
                f"<div style='overflow-x:auto;margin-top:8px'>{table}</div></div>")
    return (f"<div style='display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start;margin:6px 0 16px'>"
            f"<div style='flex:1 1 380px;min-width:300px'>{chart}</div>"
            f"<div style='flex:1 1 320px;min-width:280px;overflow-x:auto'>{table}</div></div>")


# ---------------------------------------------------------------- lookups

def _story_meta():
    """story id -> (title, author, issue id, magazine, cover date) from the
    site's article index, so every witness can be named."""
    key = "story_meta"
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < 60:
        return hit[1]
    out = {}
    try:
        for a in _G["articles_index"]():
            out[a["article_id"]] = a
    except Exception:
        pass
    # the export the pipeline ran on carries the titles and by-lines as
    # they stood (human corrections included); prefer it when present
    exp = os.path.join(_G["DATA"], "pilot_stories.jsonl")
    if os.path.exists(exp):
        try:
            with open(exp, encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    out[r["story_id"]] = {"article_id": r["story_id"], "issue": r["issue"],
                                          "type": r.get("type"), "title": r.get("title"),
                                          "author": r.get("author"), "pages": r.get("pages"),
                                          "words": r.get("n_words")}
        except Exception:
            pass
    _CACHE[key] = (now, out)
    return out


def _name(sid, meta=None):
    meta = meta or _story_meta()
    a = meta.get(sid)
    if not a:
        return sid
    t = a.get("title") or "(untitled)"
    if len(t) > 70:
        t = t[:67].rstrip() + "…"
    au = a.get("author")
    if au and len(au) > 40:
        au = au[:37].rstrip() + "…"
    return f"{t}" + (f" — {au}" if au else "")


def _issue_label(iid):
    info = _G["issue_by_id"](iid) or {}
    return f"{info.get('magazine', iid)} {info.get('cover_date', '')}".strip()


def _tokens_of(text):
    from r01_normalize import prepare, tokenize
    canon = prepare(text or "")
    toks = tokenize(canon)
    return canon, [t[0] for t in toks], [(t[1], t[2]) for t in toks]


def locate_in_article(iid, aid, passage_text, context=40):
    """Find a passage in the article as it stands NOW on the workbench.
    Returns dict(fragkey, page, before, after) or None. Used for the
    'open on workbench' link (deep link ?sel=<key>) and for context."""
    try:
        doc = _G["effective_doc"](iid)
    except Exception:
        return None
    if not doc:
        return None
    art = next((a for a in doc["articles"] if a["article_id"] == aid), None)
    if not art:
        return None
    roles = doc.get("frag_roles", {})
    fragkey, frag_text = _G["fragkey"], _G["frag_text"]
    overrides = doc.get("frag_overrides")
    alltoks, owner = [], []
    for fr in art["fragments"]:
        k = fragkey(fr)
        if roles.get(k) in ("title", "subtitle", "author"):
            continue
        _, toks, _ = _tokens_of(frag_text(iid, fr, overrides))
        alltoks.extend(toks)
        owner.extend([(k, fr.get("page"))] * len(toks))
    _, ptoks, _ = _tokens_of(passage_text)
    ptoks = ptoks[:20]
    if not ptoks or not alltoks:
        return None
    start = None
    L = len(ptoks)
    first = ptoks[0]
    for i in range(len(alltoks) - L + 1):
        if alltoks[i] == first and alltoks[i:i + L] == ptoks:
            start = i
            break
    if start is None:
        sm = difflib.SequenceMatcher(None, alltoks, ptoks, autojunk=False)
        m = sm.find_longest_match(0, len(alltoks), 0, L)
        if m.size < 4:
            return None
        start = max(0, m.a - m.b)
    k, page = owner[start]
    # context from the article's current text
    canon, toks, offs = _tokens_of(art.get("text") or "")
    before = after = ""
    if toks:
        s2 = None
        for i in range(len(toks) - L + 1):
            if toks[i] == first and toks[i:i + L] == ptoks:
                s2 = i
                break
        if s2 is not None:
            b0 = max(0, s2 - context)
            before = canon[offs[b0][0]:offs[s2][0]] if s2 > 0 else ""
            _, full_ptoks, _ = _tokens_of(passage_text)
            e = min(len(toks), s2 + len(full_ptoks))
            e2 = min(len(toks), e + context)
            after = canon[offs[e - 1][1]:offs[e2 - 1][1]] if e2 > e else ""
    return {"fragkey": k, "page": page, "before": before, "after": after}


# ---------------------------------------------------------------- status board

def status_rows(runs):
    rows = []
    for st in runs["exact"]:
        rows.append(("exact matching (r02)", st["set"], f"seed {st['k']} words",
                     st.get("generated", "—"),
                     f"{st['stories']} stories · {st['matches']} cross-issue matches · "
                     f"{st['clusters']} clusters · longest {st['longest_match']}",
                     f"data/reuse/{st['set']}_k{st['k']}_*"))
    for st in runs["para"]:
        rows.append(("paraphrase (r04)", st["set"],
                     f"window {st['window']}/{st['stride']} · K={st['k']}", st.get("generated", "—"),
                     f"{st['passages']} passages · {st['alignments']} alignments · "
                     f"{st['clusters']} clusters · longest {st['longest_alignment']} cols",
                     f"data/reuse/para/{st['_tag']}_k{st['k']}_*"))
    ex = runs["synthetic"].get("exact")
    if ex:
        rows.append(("planted reuse, exact (r03)", "synthetic copy", "seeds 6/7/8",
                     ex.get("_generated", "—"),
                     f"{ex.get('_stories', '?')} stories · {ex.get('_plants', '?')} plants",
                     "data/reuse/synthetic/recall_verbatim.json"))
    for tag, rp in runs["synthetic"].get("para", {}).items():
        rows.append(("planted reuse, paraphrase (r04)", "synthetic copy", tag,
                     rp.get("_generated", "—"), "", f"data/reuse/para/synthetic/recall_paraphrase_{tag}.json"))
    for s in runs["background"]:
        rows.append(("background (r05)", s["set"], "all pairs + sampler check + model",
                     s.get("generated", "—"),
                     f"{s['pairs']} pairs ({s['cross_issue_pairs']} cross-issue)",
                     f"data/reuse/background/summary_{s['set']}.json"))
    return rows


def status_table(runs):
    rows = status_rows(runs)
    if not rows:
        return "<div class='empty'>No pipeline output on this server yet.</div>"
    body = "".join(f"<tr><td>{_esc(a)}</td><td>{_esc(b)}</td><td>{_esc(c)}</td>"
                   f"<td class='muted'>{_esc(d)}</td><td>{_esc(e)}</td>"
                   f"<td class='muted' style='font-size:12px'>{_esc(f)}</td></tr>"
                   for a, b, c, d, e, f in rows)
    return ("<table><tr><th>Stage</th><th>Story set</th><th>Settings</th><th>Generated</th>"
            "<th>What is in it</th><th>Files</th></tr>" + body + "</table>")


# ---------------------------------------------------------------- overview

def _render(render, title, body, path):
    return (render or _G["page"])(title, body, path=path)


WHOLE_STORY, HIGH_COVERAGE = 0.80, 0.20      # share of the shorter story covered by shared passages (protocol 3.1, "Report")


def extensive_cases_html(set_, k):
    """Protocol 3.1, Report: 'especially extensive cases, including whole-story and
    high-coverage reprints' and 'the proportion of each story involved in reuse'."""
    pairs = _load(f"{set_}_k{k}_pairs.json") or []
    shares = _load(f"{set_}_k{k}_story_share.json") or {}
    if not pairs:
        return ""
    meta = _story_meta()
    ranked = sorted(pairs, key=lambda p: -max(p.get("cover_a", 0), p.get("cover_b", 0)))
    n_whole = sum(1 for p in ranked if max(p.get("cover_a", 0), p.get("cover_b", 0)) >= WHOLE_STORY)
    n_high = sum(1 for p in ranked if HIGH_COVERAGE <= max(p.get("cover_a", 0), p.get("cover_b", 0)) < WHOLE_STORY)
    rows = []
    for p in ranked[:12]:
        cov = max(p.get("cover_a", 0), p.get("cover_b", 0))
        band = "whole story" if cov >= WHOLE_STORY else "high coverage" if cov >= HIGH_COVERAGE else "ordinary"
        rows.append(f"<tr><td>{_esc(_name(p['a'], meta))}</td><td>{_esc(_name(p['b'], meta))}</td>"
                    f"<td class='num'>{p['n_matches']}</td><td class='num'>{p['max_len']}</td>"
                    f"<td class='num'>{100 * p.get('cover_a', 0):.2f}%</td><td class='num'>{100 * p.get('cover_b', 0):.2f}%</td>"
                    f"<td>{band}</td><td><a href='/pair/{_esc(p['a'])}/{_esc(p['b'])}'>pair</a></td></tr>")
    out = [f"<h3>1b. Extensive cases (seed {k}, {_esc(set_)} set): whole-story and high-coverage matches</h3>",
           f"<p>Of {len(pairs)} story pairs sharing at least one passage across issues, <b>{n_whole}</b> are whole-story cases "
           f"(shared passages cover {int(WHOLE_STORY * 100)}% or more of the shorter story) and <b>{n_high}</b> high-coverage cases "
           f"({int(HIGH_COVERAGE * 100)}% or more). The pairs with the largest coverage:</p>",
           "<table><tr><th>Story A</th><th>Story B</th><th class='num'>Passages</th><th class='num'>Longest</th>"
           "<th class='num'>Cover A</th><th class='num'>Cover B</th><th>Band</th><th></th></tr>" + "".join(rows) + "</table>",
           "<p class='muted'>Coverage = share of a story's words inside passages it shares with the other; the bands are the report's "
           "thresholds, not evidence of anything by themselves. A whole-story case is classified by the printed record on its pair page "
           "(a reprint the magazine acknowledges, or not); the background analysis says how unusual the rest are.</p>"]
    if shares:
        vals = sorted(shares.values(), reverse=True)
        n = len(vals)
        top = sorted(shares.items(), key=lambda kv: -kv[1])[:10]
        out.append(f"<h3>The proportion of each story involved in reuse</h3>"
                   f"<p>{n} stories share at least one passage with a story of another issue: "
                   f"{sum(1 for v in vals if v >= 0.5)} have half or more of their words in shared passages, "
                   f"{sum(1 for v in vals if 0.1 <= v < 0.5)} between a tenth and a half, {sum(1 for v in vals if 0.01 <= v < 0.1)} between one percent and a tenth, "
                   f"{sum(1 for v in vals if v < 0.01)} under one percent. Largest shares: "
                   + ", ".join(f"<a href='/story/{_esc(sid)}'>{_esc(_name(sid, meta))}</a> {100 * v:.1f}%" for sid, v in top) + ".</p>")
    return "".join(out)


def capture_html(set_):
    """Protocol 3.2, Consolidate and report: 'compare how much recurrence is captured
    by each form of reuse' — story pairs and passages found by exact matching, by the
    paraphrase stage, and by both, from the pair table."""
    try:
        con = _G["EX"].db()
        row = _G["EX"]._one(con, "SELECT SUM(CASE WHEN exact_k6_n>0 AND (para_k10_n IS NULL OR para_k10_n=0) THEN 1 ELSE 0 END) ex_only, "
                                 "SUM(CASE WHEN para_k10_n>0 AND (exact_k6_n IS NULL OR exact_k6_n=0) THEN 1 ELSE 0 END) pa_only, "
                                 "SUM(CASE WHEN exact_k6_n>0 AND para_k10_n>0 THEN 1 ELSE 0 END) both, COUNT(*) n, "
                                 "SUM(exact_k6_n) ex_pass, SUM(para_k10_n) pa_pass FROM pairs WHERE same_issue=0")
    except Exception:
        return ""
    if not row or not row["n"]:
        return ""
    ex_only, pa_only, both = row["ex_only"] or 0, row["pa_only"] or 0, row["both"] or 0
    return (f"<h3>What each form of reuse captures ({_esc(set_)} set, cross-issue pairs)</h3>"
            f"<p>Of {row['n']:,} story pairs across issues, <b>{ex_only + both:,}</b> share an exact passage (seed 6) and <b>{pa_only + both:,}</b> "
            f"a kept alignment (K = 10): {both:,} both, {ex_only:,} exact only, {pa_only:,} paraphrase only. Passages: {int(row['ex_pass'] or 0):,} exact matches "
            f"against {int(row['pa_pass'] or 0):,} alignments. Exact and paraphrastic matches are kept in separate files and columns "
            "(exact_k*_ and para_k*_ in the pair table), so the two inventories can always be compared.</p>")


def overview(render=None):
    runs = _runs()
    if not runs["exact"]:
        body = (_howto("Results of the text-reuse pipeline appear here once its output "
                       "files are on this server.")
                + "<h1>Text reuse</h1><div class='empty'>Nothing to show yet: the pipeline "
                "has not written any results into this server's data folder.</div>")
        return _render(render, "Text reuse", body, "/reuse")
    sets = _sets(runs)
    out = [_howto(
        "This page shows what the text-reuse pipeline found in the ten pilot issues "
        "and how the machinery behaves. It follows the protocol: exact matches first "
        "(seed lengths 6, 7, and 8 words, each extended to its full length), then "
        "rewritten passages (paraphrase), then a background estimate that says how "
        "common a match of a given length is between comparable stories. Every table "
        "names the file it comes from; every passage can be opened on its article "
        "page and scan. The ten issues are the declared development set, so nothing "
        "here is a finding about pulp fiction; it is a rehearsal of the method. "
        "Cross-issue matches are the inventory; matches between two records of the "
        "same issue are reported separately as assembly diagnostics."),
        "<h1>Text reuse in the pilot</h1>",
        "<p class='muted'>Browse: <a href='/reuse/clusters'>reuse clusters</a> · "
        "<a href='/reuse/validate'>paraphrase review</a> · <a href='/reuse/cases'>cases</a> · "
        "<a href='/reuse/progress'>progress</a>. Method: <a href='/method#reuse'>the protocol step by step</a>.</p>"]

    # ---- exact
    out.append("<h2>1. Exact reuse (r02)</h2>")
    tbl = ["<table><tr><th>Set</th><th>Seed</th><th class='num'>Stories</th>"
           "<th class='num'>Cross-issue matches</th><th class='num'>Longest</th>"
           "<th class='num'>Clusters</th><th class='num'>3+ witnesses (raw / collapsed)</th>"
           "<th class='num'>Stories with a match (raw / collapsed)</th>"
           "<th class='num'>Same-issue (of which shared-region)</th></tr>"]
    for st in runs["exact"]:
        tbl.append(f"<tr><td>{_esc(st['set'])}</td><td>{st['k']}</td>"
                   f"<td class='num'>{st['stories']}</td><td class='num'>{st['matches']}</td>"
                   f"<td class='num'>{st['longest_match']}</td><td class='num'>{st['clusters']}</td>"
                   f"<td class='num'>{st['clusters_3plus_witnesses']} / "
                   f"{st.get('clusters_3plus_witnesses_collapsed', '—')}</td>"
                   f"<td class='num'>{st['stories_with_reuse']} / "
                   f"{st.get('stories_with_reuse_collapsed', '—')}</td>"
                   f"<td class='num'>{st['same_issue_matches']} "
                   f"({st.get('same_issue_from_shared_regions', '—')})</td></tr>")
    tbl.append("</table>")
    main_set = sets[0]
    ks = [st["k"] for st in runs["exact"] if st["set"] == main_set]
    counts = [st["matches"] for st in runs["exact"] if st["set"] == main_set]
    chart = svg_bars([f"seed {k}" for k in ks], [("cross-issue matches", counts)],
                     f"Cross-issue exact matches by seed length ({main_set} set)")
    out.append(_chart_row(chart, "".join(tbl)))
    out.append("<p class='muted'>“Collapsed” counts treat records of one issue that own the "
               "same scan regions as a single witness (the machine assembly still lists some "
               "regions under two records). Shared-region duplicates explain most same-issue "
               "matches; the rest are genuine within-issue repeats such as a contents-page "
               "teaser repeated at the head of the story.</p>")
    # length histogram by seed
    bins = ["seed-9", "10-19", "20-49", "50-99", "100-499", "500+"]
    series = [(f"seed {st['k']}", [st["length_hist"].get(b, 0) for b in bins])
              for st in runs["exact"] if st["set"] == main_set]
    if series:
        htbl = ["<table><tr><th>Match length (words)</th>" + "".join(f"<th class='num'>{_esc(n)}</th>" for n, _ in series) + "</tr>"]
        for i, b in enumerate(bins):
            htbl.append(f"<tr><td>{b}</td>" + "".join(f"<td class='num'>{vals[i]}</td>" for _, vals in series) + "</tr>")
        htbl.append("</table>")
        out.append(_chart_row(svg_bars(bins, series, f"Match lengths by seed ({main_set} set)"), "".join(htbl)))
    # most frequent matched strings
    _, matches, _, _ = _exact_files(main_set, ks[0] if ks else 6)
    if matches:
        freq = defaultdict(int)
        for m in matches:
            freq[" ".join(m["excerpt"].lower().split())] += 1
        top = sorted(freq.items(), key=lambda kv: -kv[1])[:12]
        out.append("<details><summary class='muted'>Most frequent shared strings at seed "
                   f"{ks[0] if ks else 6} ({main_set} set) — stock phrases and publisher addresses</summary><table>"
                   + "".join(f"<tr><td class='num'>{n}</td><td>{_esc(s)}</td></tr>" for s, n in top)
                   + "</table></details>")

    # ---- 1b. extensive cases and the share of each story (protocol 3.1, "Report")
    out.append(extensive_cases_html(main_set, ks[0] if ks else 6))

    # ---- paraphrase
    out.append("<h2>2. Paraphrase and near-verbatim reuse (r04)</h2>")
    if runs["para"]:
        tbl = ["<table><tr><th>Set</th><th>Window / step</th><th>Keep rule</th><th>K</th><th class='num'>Passages</th>"
               "<th class='num'>Candidate regions</th><th class='num'>Alignments kept</th>"
               "<th class='num'>Longest (cols)</th><th class='num'>Clusters</th>"
               "<th>By source</th><th class='num'>Same-issue</th></tr>"]
        for st in runs["para"]:
            src = ", ".join(f"{k} {v}" for k, v in sorted(st.get("by_source", {}).items()))
            rule = f"{st.get('min_cols', 20)}c/{int(round(st.get('min_identity', 0.6) * 100))}%"
            tbl.append(f"<tr><td>{_esc(st['set'])}</td><td>{st['window']}/{st['stride']}</td><td style='white-space:nowrap'>{rule}</td><td>{st['k']}</td>"
                       f"<td class='num'>{st['passages']}</td><td class='num'>{st['candidate_regions']}</td>"
                       f"<td class='num'>{st['alignments']}</td><td class='num'>{st['longest_alignment']}</td>"
                       f"<td class='num'>{st['clusters']}</td><td>{_esc(src)}</td>"
                       f"<td class='num'>{st['same_issue_alignments']}</td></tr>")
        tbl.append("</table>")
        tag = _para_tag(runs, main_set)
        pst = [st for st in runs["para"] if st["set"] == main_set and st["_tag"] == tag]
        cats = [f"K={st['k']}" for st in pst]
        srcs = ["exact", "embedding", "embedding+exact"]
        series = [(s, [st.get("by_source", {}).get(s, 0) for st in pst]) for s in srcs]
        out.append(_chart_row(svg_bars(cats, series, f"Kept alignments by candidate source ({main_set}, {tag})"),
                              "".join(tbl), stack=True))
        main = next((st for st in pst if st["k"] == 10), pst[0] if pst else None)
        if main and main.get("identity_hist"):
            ih = main["identity_hist"]
            cats = list(ih.keys())
            out.append(_chart_row(
                svg_bars(cats, [("alignments", [ih[c] for c in cats])],
                         f"Identity of kept alignments (K={main['k']})"),
                "<p class='muted'>Identity = matching columns over all columns of the alignment. "
                "1.0 means an exact copy, which the exact stage already found; values below 1.0 "
                "are what this stage adds: OCR-damaged copies and rewrites. Keep rule: at least "
                f"{main['min_cols']} columns and identity at least {main['min_identity']}. "
                f"Model {_esc(main.get('model') or 'none')}; words compare equal when identical or, "
                f"for words of {main['fuzzy']['min_len']}+ letters, one edit apart.</p>"))
        out.append("<p class='muted'>Sources: “exact” = the alignment grew from an exact-match seed "
                   "(the lexical near-match tier); “embedding” = from the meaning-based neighbour "
                   "search; both when the two agreed. K is the number of neighbours retrieved per "
                   "passage; 5 and 20 are the sensitivity settings around the main run at 10.</p>")
    else:
        out.append("<div class='empty'>The paraphrase stage has not been run on this server's data yet.</div>")
    out.append(capture_html(main_set))

    # ---- synthetic
    out.append("<h2>3. Planted reuse — machinery validation (kept separate)</h2>")
    syn = runs["synthetic"]
    if syn:
        out.append("<p class='muted'>A separate copy of the story corpus with known reuse planted "
                   "into it: 20 passages copied exactly, 20 copied with about 8% of words damaged, "
                   "20 heavily edited (shortened, clauses swapped, words replaced). Nothing from this "
                   "copy enters any inventory; it only answers “does the machinery find what we "
                   "know is there?”.</p>")
        kinds = ["verbatim", "near-verbatim", "heavy-edit"]
        tbl = ["<table><tr><th>Stage</th><th>Setting</th>" + "".join(f"<th>{k}</th>" for k in kinds) + "</tr>"]
        series = []
        ex = syn.get("exact", {})
        for kk in ("k6", "k7", "k8"):
            if kk in ex:
                d = ex[kk]["per_kind"]
                tbl.append(f"<tr><td>exact</td><td>seed {kk[1:]}</td>" + "".join(
                    f"<td>recall {d[k]['recall']} · words {d[k]['mean_token_share']}</td>" if k in d else "<td>—</td>"
                    for k in kinds) + "</tr>")
                if kk == "k6":
                    series.append((f"exact seed 6", [d.get(k, {}).get("mean_token_share", 0) * 100 for k in kinds]))
        for tag, rp in syn.get("para", {}).items():
            kr = rp.get("_keep_rule", {"min_cols": 20, "min_identity": 0.6})
            rule = f"{kr['min_cols']} cols · {int(round(kr['min_identity'] * 100))}%"
            for kk, d in rp.items():
                if kk.startswith("_"):
                    continue
                tbl.append(f"<tr><td>paraphrase</td><td>{_esc(tag.split('_c')[0])} · keep {rule} · K={kk[1:]}</td>" + "".join(
                    f"<td>recall {d[k]['recall']} · cover {d[k]['mean_cover']}</td>" if k in d else "<td>—</td>"
                    for k in kinds) + "</tr>")
                if kk == "k10" and len(series) < 4:
                    series.append((f"paraphrase K=10, {rule}", [d.get(k, {}).get("mean_cover", 0) * 100 for k in kinds]))
        tbl.append("</table>")
        out.append(_chart_row(svg_bars(kinds, series[:4], "Share of planted words recovered (%)", unit="%"),
                              "".join(tbl)))
        out.append("<p class='muted'>Recall = share of plants found at all (exact: 80% of the planted "
                   "words covered for verbatim plants, any match for damaged ones; paraphrase: an "
                   "alignment covering at least half the plant). Words/cover = mean share of the planted "
                   "words inside the recovered passages.</p>")
    else:
        out.append("<div class='empty'>No planted-reuse results on this server yet.</div>")

    # ---- background
    out.append("<h2>4. Background: how common is a match of a given length? (r05)</h2>")
    bgs = runs["background"]
    if bgs:
        s = bgs[0]
        bg = s["background"]
        out.append(f"<p class='muted'>Story set {_esc(s['set'])}: {s['pairs']:,} story pairs, "
                   f"{s['cross_issue_pairs']:,} across issues (the background uses these), "
                   f"{s['same_issue_pairs']:,} within an issue (flagged, excluded). Pairs with a known "
                   f"by-line on both sides: {s['author_known_pairs']:,}; same printed author across issues: "
                   f"{s['same_author_cross_issue_pairs']}. Topic similarity = TF-IDF cosine on the text left "
                   f"after masking detected reuse (quartile cuts {', '.join(str(x) for x in s['topic_quartile_cuts'])})"
                   + (f"; correlation with the embedding cosine {s['topic_tfidf_vs_embedding_corr']}" if s.get("topic_tfidf_vs_embedding_corr") is not None else "")
                   + ".</p>")
        ex6 = bg["exact"].get("k6")
        if ex6:
            allx = [int(x) for x in ex6["overall"].keys()]
            nonzero = [x for x in allx if (ex6["overall"][str(x)] or 0) > 0]
            xs = [x for x in allx if x <= (max(nonzero) + 2 if nonzero else allx[0] + 6)][:20]
            series = [("all pairs", [ex6["overall"][str(x)] for x in xs])]
            for q in ("1", "4"):
                if q in {str(k) for k in ex6["by_topic_q"].keys()}:
                    key = q if q in ex6["by_topic_q"] else int(q)
                    series.append((f"topic quartile {q}", [ex6["by_topic_q"][key].get(str(x), ex6["by_topic_q"][key].get(x)) for x in xs]))
            tbl = ["<table><tr><th>At least L words</th>" + "".join(f"<th class='num'>{_esc(n)}</th>" for n, _ in series) + "</tr>"]
            for i, x in enumerate(xs):
                tbl.append(f"<tr><td>{x}</td>" + "".join(f"<td class='num'>{_fmt(v[i]) if v[i] else '0'}</td>" for _, v in series) + "</tr>")
            tbl.append("</table>")
            out.append(_chart_row(svg_lines(xs, series, "P(longest exact match ≥ L), seed 6, log scale",
                                            ylog=True, xlabel="L (words)"), "".join(tbl)))
        # time table
        tt = bg.get("time_table", {}).get("exact_k6")
        if tt:
            tbl = ["<table><tr><th>Later decade | years apart</th><th class='num'>Pairs</th>"
                   "<th class='num'>P(any exact match, seed 6)</th><th class='num'>Mean longest, given any</th></tr>"]
            for key, d in tt.items():
                tbl.append(f"<tr><td>{_esc(key)}</td><td class='num'>{d['n']:,}</td>"
                           f"<td class='num'>{_fmt(d['p_any'])}</td><td class='num'>{_fmt(d['mean_longest_given_any'])}</td></tr>")
            tbl.append("</table>")
            out.append("<details><summary class='muted'>The two historical variables: later publication decade "
                       "and years apart</summary>" + "".join(tbl) + "</details>")
        # sampler
        sc = s.get("sampler_check", {})
        if sc.get("checks"):
            tbl = [f"<table><tr><th>Reuse definition</th><th class='num'>Matched pairs</th><th class='num'>Sample size</th>"
                   "<th>Quantity</th><th class='num'>Full table</th><th class='num'>Weighted sample, mean abs error</th>"
                   "<th class='num'>Worst</th><th class='num'>Unweighted error</th></tr>"]
            for name, c in sc["checks"].items():
                first = True
                for t, v in c["targets"].items():
                    tbl.append(f"<tr><td>{_esc(name) if first else ''}</td>"
                               f"<td class='num'>{c['matched_pairs'] if first else ''}</td>"
                               f"<td class='num'>{_fmt(c['sample_size_mean']) if first else ''}</td>"
                               f"<td>{_esc(t)}</td><td class='num'>{_fmt(v['full'])}</td>"
                               f"<td class='num'>{_fmt(v['weighted_mean_abs_err'])}</td>"
                               f"<td class='num'>{_fmt(v['weighted_max_abs_err'])}</td>"
                               f"<td class='num'>{_fmt(v['naive_mean_err'])}</td></tr>")
                    first = False
            tbl.append("</table>")
            out.append(f"<h3>Sampler check ({sc['n_per_stratum']} "
                       f"non-matching pairs per stratum, {sc['seeds']} draws)</h3>" + "".join(tbl)
                       + "<p class='muted'>At corpus scale the protocol keeps every matched pair and samples "
                       "non-matching pairs by stratum (later decade × years apart × topic quartile), reweighting "
                       "by the sampling probabilities. Here the full table exists, so the sampler is run against "
                       "it: the weighted estimates should sit on the full-table values; the unweighted column "
                       "shows the bias the weights remove.</p>")
        # models
        models = s.get("models", {})
        if models:
            out.append("<h3>Two-part hierarchical model — first version, "
                       "a proposal for the statistical design</h3>")
            for name, m in models.items():
                out.append(f"<details><summary class='muted'>{_esc(name)}: {m['n_any']} of {m['n_pairs']:,} "
                           f"cross-issue pairs with reuse at threshold {m['threshold']}</summary>")
                for part in ("part1_any", "part2_extent"):
                    pm = m.get(part, {})
                    if "fixed" in pm:
                        tbl = ["<table><tr><th>Effect</th><th class='num'>Posterior mean</th><th class='num'>Sd</th></tr>"]
                        for n, d in pm["fixed"].items():
                            tbl.append(f"<tr><td>{_esc(n)}</td><td class='num'>{d['mean']}</td><td class='num'>{d['sd']}</td></tr>")
                        tbl.append(f"<tr><td>story effect sd</td><td class='num'>{pm['story_effect_sd']}</td><td></td></tr></table>")
                        out.append(f"<p class='muted'>{'Part 1: any reuse (logistic)' if part == 'part1_any' else 'Part 2: extent given reuse — ' + _esc(pm.get('response', ''))}"
                                   f"{' · n = ' + str(pm['n']) if 'n' in pm else ''}</p>" + "".join(tbl))
                    else:
                        out.append(f"<p class='muted'>{part}: {_esc(json.dumps(pm))}</p>")
                out.append(f"<p class='muted'>{_esc(m.get('note', ''))}</p></details>")
        # unusual
        un = s.get("unusual", {})
        rows = un.get("exact_k6", {}).get("most_unusual", [])[:10]
        if rows:
            meta = _story_meta()
            tbl = ["<table><tr><th>Pair</th><th class='num'>Longest</th><th>Stratum</th>"
                   "<th class='num'>P(≥ this) among comparable pairs</th><th>Same author</th><th>Passage</th></tr>"]
            for r in rows:
                tbl.append(f"<tr><td><a href='/article/{_esc(r['a'])}'>{_esc(_name(r['a'], meta))}</a> · "
                           f"<a href='/article/{_esc(r['b'])}'>{_esc(_name(r['b'], meta))}</a></td>"
                           f"<td class='num'>{r['longest']}</td><td>topic q{r['topic_q']} · {_esc(r['years_band'])} years "
                           f"(n={r['stratum_n']:,})</td><td class='num'>"
                           f"{('under 1 in ' + format(r['stratum_n'], ',')) if r['p_at_least'] == 0 else _fmt(r['p_at_least'])}</td>"
                           f"<td>{'yes' if r['same_author'] else ''}</td><td class='muted'>{_esc(r['excerpt'])}</td></tr>")
            tbl.append("</table>")
            out.append("<h3>Most unusual exact matches, placed in their "
                       "background</h3>" + "".join(tbl))
    else:
        out.append("<div class='empty'>The background stage has not been run on this server's data yet.</div>")

    # ---- diagnostics
    out.append("<h2>5. What the reuse check says about assembly</h2>")
    ov = runs["overlap"].get(main_set)
    if ov:
        cats = list(ov.keys())
        vals = [ov[i]["keys_owned_by_2plus"] for i in cats]
        tbl = ["<table><tr><th>Issue</th><th class='num'>Stories</th><th class='num'>Regions</th>"
               "<th class='num'>Regions owned by 2+ records</th><th class='num'>Records involved</th><th>Worst pair</th></tr>"]
        for i in cats:
            d = ov[i]
            wp = d["worst_pairs"][0] if d["worst_pairs"] else None
            tbl.append(f"<tr><td><a href='/issue/{_esc(i)}'>{_esc(i)}</a></td><td class='num'>{d['stories']}</td>"
                       f"<td class='num'>{d['region_keys']}</td><td class='num'>{d['keys_owned_by_2plus']}</td>"
                       f"<td class='num'>{d['stories_sharing']}</td>"
                       f"<td>{(_esc(wp['a']) + ' ~ ' + _esc(wp['b']) + ' (' + str(wp['shared_keys']) + ')') if wp else ''}</td></tr>")
        tbl.append("</table>")
        out.append(_chart_row(svg_bars(cats, [("regions owned by 2+ records", vals)],
                                       "Scan regions listed under more than one record, per issue"), "".join(tbl)))
        out.append("<p class='muted'>Rule 7 of the assembly notes: one region, one record. Every region "
                   "counted here is a job for assembly version 2 or for the correction sprint; the "
                   "reuse inventory collapses such records into one witness meanwhile. "
                   "<a href='/reuse/clusters?kind=exact&k=6&same=1'>Browse the same-issue diagnostics</a>.</p>")
    return _render(render, "Text reuse", "".join(out), "/reuse")


# ---------------------------------------------------------------- clusters

def clusters_page(qs, render=None):
    runs = _runs()
    sets = _sets(runs) or ["machine"]
    g = lambda k, d: (qs.get(k, [d]) or [d])[0]
    set_ = g("set", sets[0])
    kind = g("kind", "exact")
    try:
        k = int(g("k", "6" if kind == "exact" else "10"))
    except ValueError:
        k = 6
    try:
        min_len = int(g("min", "0"))
        min_wit = int(g("wit", "2"))
    except ValueError:
        min_len, min_wit = 0, 2
    same = g("same", "0") == "1"
    q = g("q", "").strip().lower()
    meta = _story_meta()

    if kind == "exact":
        clusters, matches, sameissue, stats = _exact_files(set_, k)
        kopts = [6, 7, 8]
    else:
        tag = _para_tag(runs, set_)
        clusters, matches, sameissue, stats = _para_files(tag, k) if tag else (None, None, None, None)
        kopts = [5, 10, 20]

    def sel(name, val, options, labels=None):
        o = "".join(f"<option value='{_esc(v)}'{' selected' if str(v) == str(val) else ''}>"
                    f"{_esc((labels or {}).get(v, v))}</option>" for v in options)
        return f"<select name='{name}'>{o}</select>"
    form = (f"<form method='GET' action='/reuse/clusters' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>story set {sel('set', set_, sets)}</label>"
            f"<label>kind {sel('kind', kind, ['exact', 'para'], {'exact': 'exact', 'para': 'paraphrase'})}</label>"
            f"<label>{'seed' if kind == 'exact' else 'K'} {sel('k', k, kopts)}</label>"
            f"<label>min length <input name='min' value='{min_len}' style='width:50px'></label>"
            f"<label>min witnesses <input name='wit' value='{min_wit}' style='width:44px'></label>"
            f"<label>text contains <input name='q' value='{_esc(q)}' style='width:140px'></label>"
            f"<label><input type='checkbox' name='same' value='1'{' checked' if same else ''}> same-issue diagnostics instead</label>"
            f"<button>show</button></form>")
    out = [_howto(
        "A cluster is one passage with all the stories it appears in (its witnesses). "
        "Pick the story set and the setting; the list is sorted by number of witnesses, "
        "then length. Open a cluster to see every witness side by side and jump to the "
        "article page and the scan. “Same-issue diagnostics” lists matches between two "
        "records of one issue: mostly the machine assembly listing a region under two "
        "records (shared-region duplicate), sometimes a real repeat inside the issue."),
        "<h1>Reuse clusters</h1>", form]
    if same:
        rows = sameissue or []
        rows = [m for m in rows if (m.get("len") or m.get("cols", 0)) >= min_len
                and (not q or q in (m.get("excerpt") or m.get("text_a") or "").lower())]
        rows.sort(key=lambda m: -(m.get("len") or m.get("cols", 0)))
        tbl = ["<table><tr><th class='num'>Length</th><th>Cause</th><th>Record A</th><th>Record B</th><th>Passage</th></tr>"]
        for m in rows[:500]:
            L = m.get("len") or m.get("cols")
            tbl.append(f"<tr><td class='num'>{L}</td><td>{_esc(m.get('cause', ''))}"
                       f"{(' · ' + str(m.get('shared_regions')) + ' shared regions') if m.get('shared_regions') else ''}</td>"
                       f"<td><a href='/article/{_esc(m['a'])}'>{_esc(_name(m['a'], meta))}</a><br><span class='muted'>{_esc(m['a'])}</span></td>"
                       f"<td><a href='/article/{_esc(m['b'])}'>{_esc(_name(m['b'], meta))}</a><br><span class='muted'>{_esc(m['b'])}</span></td>"
                       f"<td class='muted'>{_esc((m.get('excerpt') or m.get('text_a') or '')[:220])}</td></tr>")
        tbl.append("</table>")
        out.append(f"<h2>Same-issue matches — {len(rows)} shown</h2>" + ("".join(tbl) if rows else "<div class='empty'>None for this setting.</div>"))
        return _render(render, "Reuse clusters", "".join(out), "/reuse/clusters")
    if clusters is None:
        out.append("<div class='empty'>No output for this setting on this server.</div>")
        return _render(render, "Reuse clusters", "".join(out), "/reuse/clusters")
    rows = []
    for i, c in enumerate(clusters):
        if c["witnesses"] < min_wit or c["max_len"] < min_len:
            continue
        if q and q not in c["representative"]["text"].lower():
            continue
        rows.append((i, c))
    tbl = ["<table><tr><th>#</th><th class='num'>Witnesses (collapsed)</th><th class='num'>Issues</th>"
           "<th class='num'>Longest</th><th>Passage</th><th>Stories</th></tr>"]
    for i, c in rows[:400]:
        names = "; ".join(sorted({_name(m["story_id"], meta) for m in c["members"]}))
        tbl.append(f"<tr><td><a href='/reuse/cluster/{_esc(set_)}/{kind}/{k}/{i}'>{i}</a></td>"
                   f"<td class='num'>{c['witnesses']} ({c.get('witnesses_collapsed', c['witnesses'])})</td>"
                   f"<td class='num'>{c.get('issues', '')}</td><td class='num'>{c['max_len']}</td>"
                   f"<td>{_esc(c['representative']['text'][:200])}</td>"
                   f"<td class='muted' style='font-size:12.5px'>{_esc(names[:300])}</td></tr>")
    tbl.append("</table>")
    st = stats or {}
    out.append(f"<h2>{len(rows)} of {len(clusters)} clusters shown"
               f"{' · generated ' + _esc(st.get('generated', '')) if st.get('generated') else ''}</h2>"
               + ("".join(tbl) if rows else "<div class='empty'>No cluster matches these filters.</div>"))
    return _render(render, "Reuse clusters", "".join(out), "/reuse/clusters")


def cluster_page(set_, kind, k, idx, render=None, user=None):
    runs = _runs()
    if kind == "exact":
        clusters, matches, _, stats = _exact_files(set_, k)
    else:
        tag = _para_tag(runs, set_)
        clusters, matches, _, stats = _para_files(tag, k) if tag else (None, None, None, None)
    if not clusters or idx < 0 or idx >= len(clusters):
        return _render(render, "Cluster", "<h1>No such cluster</h1>", "/reuse/clusters")
    c = clusters[idx]
    meta = _story_meta()
    out = [_howto(
        "One passage and every story it appears in. Each witness shows the passage as "
        "it stands in that story's text, with the words around it as the article reads "
        "now on the workbench. “Open on workbench” highlights the scan region where the "
        "passage begins; “scan page” opens the page image. For paraphrase clusters the "
        "pairwise alignments are shown with the differences marked: struck-through words "
        "are only in the first text, shaded words only in the second."),
        f"<h1>Cluster {idx} · {c['witnesses']} witnesses"
        f"{' (' + str(c.get('witnesses_collapsed')) + ' after collapsing duplicate records)' if c.get('witnesses_collapsed') not in (None, c['witnesses']) else ''}"
        f" · longest {c['max_len']} {'words' if kind == 'exact' else 'columns'}</h1>",
        f"<p class='muted'>{_esc(set_)} set · {'exact, seed ' if kind == 'exact' else 'paraphrase, K='}{k} · "
        f"<a href='/reuse/clusters?set={_esc(set_)}&kind={kind}&k={k}'>back to the list</a></p>",
        f"<blockquote style='border-left:4px solid var(--accent);margin:10px 0;padding:6px 12px;background:var(--surface)'>"
        f"{_esc(c['representative']['text'])}</blockquote>"]
    # protocol 4.2: a cluster can be marked as a case for the literary genealogies
    out.append(_G["RV"].case_form_html("cluster", {"set": set_, "kind": kind, "k": int(k), "idx": int(idx)}, user,
                                       f"/reuse/cluster/{set_}/{kind}/{k}/{idx}",
                                       title=(c['representative']['text'] or '')[:60]))
    out.append("<h2>Witnesses</h2>")
    members = sorted(c["members"], key=lambda m: (m["issue"], m["story_id"], m["tok"][0]))
    by_story = defaultdict(list)
    for m in members:
        by_story[m["story_id"]].append(m)
    unit_word = "words" if kind == "exact" else "columns"
    for sid, occ in by_story.items():
        iid = occ[0]["issue"]
        head = (f"<div class='ch'><span>{_esc(_name(sid, meta))}</span>"
                f"<span class='muted'>{_esc(_issue_label(iid))} · <a href='/article/{_esc(sid)}'>{_esc(sid)}</a>"
                f"{' · ' + str(len(occ)) + ' places' if len(occ) > 1 else ''}</span></div>")
        body = []
        for m in occ:
            text = m.get("text") or c["representative"]["text"]
            loc = locate_in_article(iid, sid, text)
            links = ""
            if loc:
                links = (f"<a href='/article/{_esc(sid)}?sel={urllib.parse.quote(loc['fragkey'])}'>open on workbench</a>")
                if loc.get("page"):
                    links += f" · <a href='/issue/{_esc(iid)}/p/{loc['page']}'>scan page {loc['page']}</a>"
            else:
                links = "<span class='muted'>not located in the current article text</span>"
            ctx_b = _esc(loc["before"]) if loc and loc.get("before") else ""
            ctx_a = _esc(loc["after"]) if loc and loc.get("after") else ""
            body.append(
                f"<div class='cardtext' style='max-height:none;border-top:1px solid var(--grid)'>"
                f"<div class='muted' style='font-size:12px;margin-bottom:3px'>{unit_word} {m['tok'][0]}–{m['tok'][1]} · "
                f"{m['len']} {unit_word} · {links}</div>"
                f"<span class='muted'>{ctx_b}</span><span style='background:var(--warnbg)'>{_esc(text)}</span>"
                f"<span class='muted'>{ctx_a}</span></div>")
        out.append(f"<div class='card'>{head}{''.join(body)}</div>")
    if kind == "para" and matches:
        ids = {m["story_id"] for m in members}
        spans = defaultdict(list)
        for m in members:
            spans[m["story_id"]].append(tuple(m["tok"]))

        def hits(sid, tok):
            return any(not (tok[1] <= s or tok[0] >= e) for s, e in spans.get(sid, []))
        rel = [al for al in matches if al["a"] in ids and al["b"] in ids
               and hits(al["a"], al["a_tok"]) and hits(al["b"], al["b_tok"])]
        if rel:
            out.append("<h2>Pairwise alignments</h2>")
            for al in rel[:30]:
                out.append(
                    f"<div class='card'><div class='ch'><span>{_esc(_name(al['a'], meta))} ↔ {_esc(_name(al['b'], meta))}</span>"
                    f"<span class='muted'>{al['cols']} columns · {al['matches']} matching · identity {al['identity']} · "
                    f"score {al['score']} · source {', '.join(al['sources'])}"
                    f"{' · cosine ' + str(al['max_cosine']) if al.get('max_cosine') is not None else ''}</span></div>"
                    f"<div class='cardtext' style='max-height:none'>{_G['diff_html'](al['text_a'], al['text_b'])}</div></div>")
    return _render(render, "Cluster", "".join(out), f"/reuse/cluster/{set_}/{kind}/{k}/{idx}")


# ---------------------------------------------------------------- progress

def progress_page(render=None):
    runs = _runs()
    # every human action: the live logs and the ones archived by an assembly switch
    # (app.all_ann_events; archived events carry _archive = the stamp)
    if _G.get("all_ann_events"):
        events = _G["all_ann_events"]()
    else:
        events = []
        anndir = _G["ANNDIR"]
        if os.path.isdir(anndir):
            for f in os.listdir(anndir):
                if f.endswith(".jsonl"):
                    for line in open(os.path.join(anndir, f), encoding="utf-8"):
                        try:
                            events.append(json.loads(line))
                        except Exception:
                            pass
        events.sort(key=lambda e: e.get("ts", ""))
    n_arch = sum(1 for e in events if e.get("_archive"))
    stamps = sorted({e["_archive"] for e in events if e.get("_archive")})
    out = [_howto(
        "Three boards. The process board comes first: the archive's whole pulp collection as the "
        "survey found it (every language; the working corpus is English or unmarked, fiction "
        "magazines), and every selected issue at every step — download, page images, layout OCR, "
        "text stages, assembly, export, annotation, verification — measured against it; the "
        "explorer side counts only the complete issues. Annotation progress comes from the "
        "annotation logs, the live ones and those archived by an assembly switch: what each "
        "annotator did per day, and how the count of verified and modified records grew. The "
        "pipeline board lists every result file the reuse pipeline has produced on this server, "
        "with its settings and time."),
        "<h1>Progress</h1>"]
    # ---- the whole process, every issue at every step (explorer database), against the survey
    out.append("<h2>The whole process, against the archive's collection</h2>")
    EX = _G.get("EX")
    if EX is not None:
        try:
            out.append(EX.process_board_html())
        except Exception as e:      # the board must never take the page down
            out.append(f"<div class='empty'>Process board unavailable: {_esc(str(e))}</div>")
    else:
        out.append("<div class='empty'>Explorer not loaded.</div>")
    # ---- annotation
    out.append("<h2>Annotation progress</h2>")
    if events:
        by_day_user = defaultdict(lambda: defaultdict(int))
        verified, modified = set(), set()
        cum_v, cum_m, days = [], [], []
        per_user = defaultdict(lambda: {"actions": 0, "archived": 0, "articles": set(), "verified": set(), "last": ""})
        live_verified, live_modified = set(), set()
        cur_day = None
        for e in events:
            d = (e.get("ts") or "")[:10]
            u = e.get("user", "?")
            by_day_user[d][u] += 1
            pu = per_user[u]
            pu["actions"] += 1
            if e.get("_archive"):
                pu["archived"] += 1
            # a record is one of an assembly: the same id string means a different record after a switch
            rid = (e.get("_archive") or "live", e.get("article_id"))
            pu["articles"].add(rid)
            pu["last"] = e.get("ts", "")
            act = e.get("action")
            if act == "verify":
                verified.add(rid)
                pu["verified"].add(rid)
                if not e.get("_archive"):
                    live_verified.add(rid)
            elif act == "unverify":
                verified.discard(rid)
                live_verified.discard(rid)
            elif act not in ("verify", "unverify"):
                modified.add(rid)
                if not e.get("_archive"):
                    live_modified.add(rid)
            if d != cur_day:
                if cur_day is not None:
                    days.append(cur_day)
                    cum_v.append(len(verified))
                    cum_m.append(len(modified))
                cur_day = d
        days.append(cur_day)
        cum_v.append(len(verified))
        cum_m.append(len(modified))
        users = sorted(per_user, key=lambda u: -per_user[u]["actions"])[:4]
        dlist = sorted(by_day_user)[-21:]
        series = [(_G["display_name"](u), [by_day_user[d].get(u, 0) for d in dlist]) for u in users]
        tbl = ["<table><tr><th>Annotator</th><th class='num'>Actions</th>"
               + ("<th class='num'>Of which on the archived assembly</th>" if n_arch else "")
               + "<th class='num'>Articles touched</th><th class='num'>Verified</th><th>Last active</th></tr>"]
        for u in sorted(per_user, key=lambda u: -per_user[u]["actions"]):
            pu = per_user[u]
            tbl.append(f"<tr><td>{_esc(_G['display_name'](u))}</td><td class='num'>{pu['actions']:,}</td>"
                       + (f"<td class='num'>{pu['archived']:,}</td>" if n_arch else "")
                       + f"<td class='num'>{len(pu['articles'])}</td><td class='num'>{len(pu['verified'])}</td>"
                       f"<td class='muted'>{_esc(pu['last'])}</td></tr>")
        tbl.append("</table>")
        out.append(_chart_row(svg_bars([d[5:] for d in dlist], series, "Annotation actions per day (last 21 active days)"),
                              "".join(tbl)))
        xs = list(range(len(days)))
        out.append(_chart_row(
            svg_lines(xs, [("verified stories", cum_v), ("modified stories", cum_m)],
                      "Stories verified and modified, cumulative by active day",
                      xlabels=[d[5:] for d in days]),
            "<table><tr><th>Day</th><th class='num'>Verified</th><th class='num'>Modified</th></tr>"
            + "".join(f"<tr><td>{_esc(d)}</td><td class='num'>{v}</td><td class='num'>{m}</td></tr>"
                      for d, v, m in list(zip(days, cum_v, cum_m))[-15:]) + "</table>"))
        out.append(f"<p class='muted'>Work done: {len(verified)} records verified and {len(modified)} records with at least "
                   f"one human change, {len(events):,} recorded actions in all"
                   + (f" — of which {n_arch:,} on the assembly archived by the switch of 2 September 2026 "
                      f"(data/assembly_archive/{_esc(', '.join(stamps))}): those corrections were made on the model's "
                      f"records, remain the yardstick the rules are measured against (<a href='/assembly'>assembly</a>), "
                      f"and are not repeated on the rules' records. On the live assembly now: {len(live_verified)} verified, "
                      f"{len(live_modified)} with a human change" if n_arch else "")
                   + ". Target for the correction sprint (implementation plan 0.1): 20–30 verified stories including one full issue.</p>")
    else:
        out.append("<div class='empty'>No annotation events on this server yet.</div>")
    # ---- the two review tools of the protocol (3.2 validation, 4.2 cases)
    out.append("<h2>Paraphrase review and cases</h2>")
    try:
        RV = _G["RV"]
        items = RV._items()
        if items:
            r6 = RV._r6()
            js = RV._judgments(items[0].get("set_id"))
            cal = r6.calibrate(items, js, write=False) or {}
            readers = ", ".join(f"{_esc(_G['display_name'](u))} {n}" for u, n in sorted(cal.get("readers", {}).items(), key=lambda kv: -kv[1])) or "nobody yet"
            ag = cal.get("agreement") or {}
            ch = cal.get("chosen")
            out.append(f"<p>Review set {_esc(items[0].get('set_id'))}: {len(items)} items; judged by {readers}; "
                       f"{cal.get('decided_items', 0)} decided"
                       + (f"; agreement {ag['agree']} of {ag['items_both_decided']} (kappa {ag['kappa']})" if ag.get("items_both_decided") else "")
                       + "; chosen setting " + (f"K = {ch['k']}, {ch['min_cols']} columns, identity {ch['min_identity']}" if ch else "none yet")
                       + ". <a href='/reuse/validate'>Review</a> · <a href='/reuse/validate?view=calibration'>calibration</a>.</p>")
        else:
            out.append("<p class='muted'>No paraphrase review set on this server yet (pipeline/r06_validation.py --build).</p>")
        cases = RV.load_cases()
        out.append(f"<p>Cases for the literary genealogies: {sum(1 for c in cases.values() if c['open'])} open, "
                   f"{sum(1 for c in cases.values() if not c['open'])} closed. <a href='/reuse/cases'>Cases</a>.</p>")
    except Exception as e:
        out.append(f"<p class='muted'>Review status unavailable ({_esc(str(e)[:80])}).</p>")
    # ---- pipeline board
    out.append("<h2>Pipeline status board</h2>")
    out.append(status_table(runs))
    return _render(render, "Progress", "".join(out), "/reuse/progress")


# ---------------------------------------------------------------- assembly comparison (workroom)

def assembly_page(qs, render=None):
    """The assembly harness (pipeline/s09_assembly_eval.py): every way of
    assembling an issue into records, scored against the human-verified
    records, the contents page, and the structural checks."""
    D = _G["DATA"]
    ev = None
    p = os.path.join(D, "assembly_v2", "eval.json")
    if os.path.exists(p):
        try:
            ev = json.load(open(p, encoding="utf-8"))
        except Exception:
            ev = None
    out = [_howto(
        "Assembly v2 is compared against three yardsticks: the records a person verified on the workbench "
        "(scan regions, title, author), the contents page of each issue (every piece it lists, with the page "
        "it starts on), and checks that need no yardstick (regions owned by two records, text owned by none, "
        "story records that begin with a chapter head). Three candidates: the live assembly (what the workbench "
        "shows: the model's records, s07, until 2 September 2026; the rules' records since — the backend in the "
        "heading says which), the rules engine's newest run (s08), and the rules inside the printed range with "
        "the model's advertisement pages. When the live records are an older run of the rules, the table shows "
        "what the newest run changes; scripts/switch_assembly.py --refresh makes it live without touching the "
        "annotation logs."),
        "<h1>Assembly: which method is most accurate</h1>"]
    if not ev:
        out.append("<div class='empty'>No harness output on this server yet: run pipeline/s08_assemble_rules.py --all "
                   "and pipeline/s09_assembly_eval.py --all.</div>")
        return _render(render, "Assembly", "".join(out), "/assembly")
    out.append(f"<p class='muted'>Harness run {_esc(ev.get('generated', ''))} · "
               f"<a href='/raw/file?path=assembly_v2/eval.json'>eval.json</a> · how to read the columns is under the table.</p>")
    out.append(f"<pre style='font-family:Menlo,Consolas,monospace;font-size:12px;overflow-x:auto'>{_esc(ev.get('table', ''))}</pre>")
    which = (qs.get("issue", [""]) or [""])[0]
    variant = (qs.get("variant", ["rules"]) or ["rules"])[0]
    issues = [r["issue"] for r in ev.get("issues", [])]
    sel = lambda name, val, opts: f"<select name='{name}'>" + "".join(
        f"<option value='{_esc(v)}'{' selected' if v == val else ''}>{_esc(l)}</option>" for v, l in opts) + "</select>"
    out.append("<form method='GET' action='/assembly' class='pgjump' style='display:flex;gap:12px;align-items:center;flex-wrap:wrap'>"
               f"<label>issue {sel('issue', which, [('', 'choose')] + [(i, i) for i in issues])}</label>"
               f"<label>candidate {sel('variant', variant, [(v, v) for v in ev.get('variants', [])])}</label><button>show</button></form>")
    res = next((r for r in ev.get("issues", []) if r["issue"] == which), None)
    if res and variant in res["variants"]:
        v = res["variants"][variant]
        out.append(f"<h2>{_esc(which)} — {_esc(variant)} ({_esc(v.get('backend') or '')}, built {_esc(v.get('built') or '')})</h2>")
        rows = ["<table><tr><th>Contents page says</th><th>Author</th><th>Type</th><th class='num'>Starts on scan p.</th><th>Record found</th>"
                "<th>Title agrees</th><th>Author agrees</th><th class='num'>Pages covered</th><th class='num'>Story starts inside</th><th>Runs over</th></tr>"]
        for t in v["contents"]:
            ok = t["start_found"] and t["title_ok"] and not t["runs_over"] and not t["extra_starts_inside"]
            rows.append(f"<tr style='{'' if ok else 'background:var(--warnbg)'}'><td>{_esc(t['title'])}</td><td>{_esc(t.get('author') or '')}</td>"
                        f"<td>{_esc(t.get('type') or '')}</td><td class='num'><a href='/issue/{_esc(which)}/p/{t['scan']}'>{t['scan']}</a></td>"
                        f"<td>{_esc(t.get('record_title') or ('—' if not t['start_found'] else ''))}</td>"
                        f"<td>{'yes' if t['title_ok'] else 'no'}</td><td>{'' if t['author_ok'] is None else ('yes' if t['author_ok'] else 'no')}</td>"
                        f"<td class='num'>{t['coverage']:.2f}</td><td class='num'>{t['extra_starts_inside']}</td><td>{'yes' if t['runs_over'] else ''}</td></tr>")
        rows.append("</table>")
        out.append("<h3>Against the contents page</h3>" + "".join(rows))
        hs = v.get("human", [])
        if hs:
            rows = ["<table><tr><th>Human record</th><th>Status</th><th class='num'>Regions</th><th>Best candidate</th><th class='num'>Recall</th>"
                    "<th class='num'>Precision</th><th class='num'>Jaccard</th><th>Exact</th><th>Title</th><th>Author</th></tr>"]
            for h in hs:
                rows.append(f"<tr><td><a href='/article/{_esc(h['article_id'])}'>{_esc(h.get('title') or h['article_id'])}</a></td><td>{_esc(h['status'])}</td>"
                            f"<td class='num'>{h['n_regions']}</td><td>{_esc(h.get('match_title') or h.get('match') or '—')}</td>"
                            f"<td class='num'>{h.get('recall', '')}</td><td class='num'>{h.get('precision', '')}</td><td class='num'>{h.get('jaccard', '')}</td>"
                            f"<td>{'yes' if h.get('exact') else ''}</td><td>{'' if h.get('title_ok') is None else ('yes' if h['title_ok'] else 'no')}</td>"
                            f"<td>{'' if h.get('author_ok') is None else ('yes' if h['author_ok'] else 'no')}</td></tr>")
            rows.append("</table>")
            out.append("<h3>Against the human-corrected records</h3>" + "".join(rows)
                       + "<p class='muted'>Verified records are finished repairs; modified ones are partial, so a low score there "
                         "can mean the person had not finished, not that the candidate is wrong.</p>")
        st = v["summary"]["structure"]
        out.append("<h3>Structure</h3><p>" + ", ".join(
            f"{_esc(k.replace('_', ' '))} {v_ if not isinstance(v_, dict) else _esc(str(v_))}" for k, v_ in st.items()) + ".</p>")
        if variant != "live":
            out.append(f"<p class='muted'>{_esc(variant)} records and the page analysis: "
                       f"<a href='/raw/file?path=assembly_v2/{_esc(variant)}/{_esc(which)}/articles.json'>articles.json</a> · "
                       f"<a href='/raw/file?path=assembly_v2/rules/{_esc(which)}/analysis.json'>analysis.json</a></p>")
    return _render(render, "Assembly", "".join(out), "/assembly")
