"""The explorer (service side of the site, v0.10.0): every piece of data
the server holds, in layers, each layer one click from the next and the
last layer the raw record itself.

  Layer 0  /overview      charts that point at the findings; every element
                          of every chart is a link into layer 1 or 2
  Layer 1  /authors /magazines /issues /stories /pairs /reuse/clusters
                          lists with filters
  Layer 2  /author/<key> /magazine/<slug> /issue/<id> /story/<id>
           /pair/<a>/<b> /reuse/cluster/...     one entity, all its facts
  Layer 3  the article workbench and the scan viewer (the printed page),
           and /raw/... : the JSON records every page above was built
           from, with the file each came from; the same records are
           served to the read-only data door (/api/<token>/...).

Everything is computed from the files under data/ at request time, with
one in-memory index rebuilt whenever a source file changes. No database,
no framework, no JavaScript libraries; charts are inline SVG.
"""
import csv
import glob
import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict, Counter

import reuse_pages as RP

_G = {}
_IDX = {"sig": None, "index": None, "checked": 0.0}
ARCHIVE_TOTAL = 27973          # items in the archive's pulp collection, protocol count


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)


def _esc(s):
    return _G["esc"](s)


def _howto(t):
    return _G["howto"](t)


def _render(render, title, body, path):
    return (render or _G["page"])(title, body, path=path)


def _fmt(v):
    return RP._fmt(v)


def _n(v):
    return f"{v:,}" if isinstance(v, int) else _fmt(v)


# ---------------------------------------------------------------- slugs

def author_slug(key):
    return key.replace(" ", "-")


def author_unslug(slug):
    return slug.replace("-", " ")


def mag_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


MAG_ABBR = {"Weird Tales": "WT", "Astounding Stories of Super-Science": "AST",
            "Galaxy Science Fiction": "GAL", "Thrilling Detective": "TD",
            "Western Story Magazine": "WS", "Wild West Weekly": "WWW"}


def mag_abbr(name):
    if name in MAG_ABBR:
        return MAG_ABBR[name]
    return "".join(w[0] for w in (name or "").split() if w[0].isalpha()).upper()[:4]


# ---------------------------------------------------------------- index

def _sources():
    """(path, mtime) of every file the index depends on."""
    D = _G["DATA"]
    files = [os.path.join(D, "pilot_stories.jsonl"), _G["CONFIG"],
             os.path.join(D, "reuse", "machine_region_overlap.json"),
             os.path.join(D, "reuse", "background", "pairs_machine.csv.gz"),
             os.path.join(D, "reuse", "background", "summary_machine.json"),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline", "publishers.json")]
    files += glob.glob(os.path.join(D, "reuse", "machine_k*_matches.jsonl"))
    files += glob.glob(os.path.join(D, "reuse", "machine_k*_sameissue.jsonl"))
    files += glob.glob(os.path.join(D, "reuse", "para", "machine_w50s25_k10_*.jsonl"))
    files += glob.glob(os.path.join(D, "annotations", "*.jsonl"))
    files += glob.glob(os.path.join(D, "raw", "*", "meta.json"))
    return tuple((f, os.path.getmtime(f)) for f in sorted(files) if os.path.exists(f))


def get_index():
    now = time.time()
    if _IDX["index"] is not None and now - _IDX["checked"] < 20:
        return _IDX["index"]
    sig = _sources()
    _IDX["checked"] = now
    if sig != _IDX["sig"] or _IDX["index"] is None:
        _IDX["index"] = _build(sig)
        _IDX["sig"] = sig
    return _IDX["index"]


def _jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _json(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def _year(cover_date):
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?", cover_date or "")
    if not m:
        return None
    return int(m.group(1)) + (int(m.group(2) or 6) - 1) / 12


def _build(sig):
    from r01_normalize import author_key
    D = _G["DATA"]
    t0 = time.time()
    cfg = _G["cfg"]()
    pubs = (_json(os.path.join(_G["ROOT"], "pipeline", "publishers.json")) or {}).get("issues", {})
    ix = {"built": time.strftime("%Y-%m-%d %H:%M"), "records": {}, "stories": {}, "authors": {},
          "magazines": {}, "issues": {}, "matches": {}, "same_issue": {}, "aligns": [],
          "by_story": defaultdict(lambda: {"exact": [], "para": []}),
          "author_links": defaultdict(lambda: {"n": 0, "longest": 0, "pairs": set(), "stories": set()}),
          "mag_links": defaultdict(lambda: {"n": 0, "longest": 0, "pairs": set()}),
          "issue_links": defaultdict(int),
          "pairs": {}, "pair_cols": [], "summary": None, "overlap": None,
          "events": [], "sources": [os.path.relpath(p, _G["ROOT"]) for p, _ in sig]}
    # issues from the config, with archive metadata when on disk
    for i in cfg.get("issues", []):
        iid = i["id"]
        meta = _json(os.path.join(D, "raw", iid, "meta.json")) or {}
        md = meta.get("metadata", {}) if isinstance(meta, dict) else {}
        ix["issues"][iid] = {
            "id": iid, "magazine": i.get("magazine"), "cover_date": i.get("cover_date"),
            "year": _year(i.get("cover_date")), "genre": i.get("genre"), "format": i.get("format"),
            "ia_identifier": i.get("ia_identifier"), "why": i.get("why"), "gold": i.get("gold"),
            "publisher": pubs.get(iid, {}).get("publisher"),
            "publisher_group": pubs.get(iid, {}).get("publisher_group"),
            "publisher_source": pubs.get(iid, {}).get("source"),
            "ia": {k: md.get(k) for k in ("title", "uploader", "addeddate", "publicdate", "scanner",
                                          "imagecount", "collection", "ocr", "identifier-ark",
                                          "description", "contributor", "source", "year")},
            "ia_item_size": meta.get("item_size") if isinstance(meta, dict) else None,
            "ia_files": [f.get("format") for f in (meta.get("files") or [])] if isinstance(meta, dict) else [],
            "records": 0, "stories": 0, "words": 0, "authors": set(), "verified": 0, "modified": 0,
            "pages": 0, "n_annotation_events": 0,
        }
    # records from the export (titles and by-lines as they stood at export)
    for r in _jsonl(os.path.join(D, "pilot_stories.jsonl")):
        sid = r["story_id"]
        iss = ix["issues"].get(r["issue"])
        rec = {"id": sid, "issue": r["issue"], "magazine": r.get("magazine"), "cover_date": r.get("cover_date"),
               "year": _year(r.get("cover_date")), "genre": r.get("genre"), "format": r.get("format"),
               "type": r.get("type") or "other", "title": r.get("title"), "author": r.get("author"),
               "author_key": author_key(r.get("author")), "pages": r.get("pages") or [],
               "status": r.get("status", "auto"), "verified_by": r.get("verified_by"),
               "modified_by": r.get("modified_by") or [], "n_regions": len(r.get("fragments") or []),
               "fragments": r.get("fragments") or [], "n_words": r.get("n_words", 0),
               "text_sha1": r.get("text_sha1"), "is_story": (r.get("type") in ("story", "serial_part"))}
        ix["records"][sid] = rec
        if iss is not None:
            iss["records"] += 1
            if rec["is_story"]:
                iss["stories"] += 1
                iss["words"] += rec["n_words"]
                if rec["author_key"]:
                    iss["authors"].add(rec["author_key"])
                if rec["status"] == "verified":
                    iss["verified"] += 1
                elif rec["status"] == "modified":
                    iss["modified"] += 1
        if rec["is_story"]:
            ix["stories"][sid] = rec
            if rec["author_key"]:
                a = ix["authors"].setdefault(rec["author_key"], {
                    "key": rec["author_key"], "names": Counter(), "stories": [], "issues": set(),
                    "magazines": set(), "words": 0})
                a["names"][rec["author"]] += 1
                a["stories"].append(sid)
                a["issues"].add(rec["issue"])
                a["magazines"].add(rec["magazine"])
                a["words"] += rec["n_words"]
    # magazines
    for iid, iss in ix["issues"].items():
        m = ix["magazines"].setdefault(iss["magazine"], {
            "name": iss["magazine"], "slug": mag_slug(iss["magazine"]), "issues": [], "genre": iss["genre"],
            "format": iss["format"], "publisher_group": iss["publisher_group"], "publishers": set(),
            "stories": 0, "words": 0, "records": 0, "authors": set(), "verified": 0})
        m["issues"].append(iid)
        m["stories"] += iss["stories"]
        m["words"] += iss["words"]
        m["records"] += iss["records"]
        m["authors"] |= iss["authors"]
        m["verified"] += iss["verified"]
        if iss["publisher"]:
            m["publishers"].add(iss["publisher"])
    # pages on disk
    for iid in ix["issues"]:
        try:
            ix["issues"][iid]["pages"] = len(_G["pages_of"](iid))
        except Exception:
            pass
    # exact matches (machine set) per seed; entity links from seed 6
    for k in (6, 7, 8):
        ms = _jsonl(os.path.join(D, "reuse", f"machine_k{k}_matches.jsonl"))
        for n, m in enumerate(ms):
            m["k"] = k
            m["idx"] = n
        ix["matches"][k] = ms
        ix["same_issue"][k] = _jsonl(os.path.join(D, "reuse", f"machine_k{k}_sameissue.jsonl"))
    for m in ix["matches"].get(6, []):
        ra, rb = ix["stories"].get(m["a"]), ix["stories"].get(m["b"])
        ix["by_story"][m["a"]]["exact"].append(m["idx"])
        ix["by_story"][m["b"]]["exact"].append(m["idx"])
        if ra and rb:
            ka, kb = ra["author_key"], rb["author_key"]
            if ka and kb and ka != kb:
                key = tuple(sorted((ka, kb)))
                L = ix["author_links"][key]
                L["n"] += 1
                L["longest"] = max(L["longest"], m["len"])
                L["pairs"].add((m["a"], m["b"]))
                L["stories"].update((m["a"], m["b"]))
            mk = tuple(sorted((ra["magazine"], rb["magazine"])))
            ML = ix["mag_links"][mk]
            ML["n"] += 1
            ML["longest"] = max(ML["longest"], m["len"])
            ML["pairs"].add((m["a"], m["b"]))
            ix["issue_links"][tuple(sorted((m["a_issue"], m["b_issue"])))] += 1
    ix["aligns"] = _jsonl(os.path.join(D, "reuse", "para", "machine_w50s25_k10_alignments.jsonl"))
    for n, a in enumerate(ix["aligns"]):
        a["idx"] = n
        ix["by_story"][a["a"]]["para"].append(n)
        ix["by_story"][a["b"]]["para"].append(n)
    # pair table
    pp = os.path.join(D, "reuse", "background", "pairs_machine.csv.gz")
    if os.path.exists(pp):
        with gzip.open(pp, "rt", encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            cols = next(rd)
            ix["pair_cols"] = cols
            ia, ib = cols.index("a"), cols.index("b")
            for row in rd:
                ix["pairs"][(row[ia], row[ib])] = row
    ix["summary"] = _json(os.path.join(D, "reuse", "background", "summary_machine.json"))
    ix["overlap"] = _json(os.path.join(D, "reuse", "machine_region_overlap.json"))
    # annotation events
    ev = []
    for f in glob.glob(os.path.join(D, "annotations", "*.jsonl")):
        for e in _jsonl(f):
            ev.append(e)
            iid = e.get("issue")
            if iid in ix["issues"]:
                ix["issues"][iid]["n_annotation_events"] += 1
    ev.sort(key=lambda e: e.get("ts", ""))
    ix["events"] = ev
    ix["build_seconds"] = round(time.time() - t0, 2)
    return ix


def pair_row(ix, a, b):
    """The pair-table row for two stories as a dict, either order."""
    row = ix["pairs"].get((a, b)) or ix["pairs"].get((b, a))
    if not row:
        return None
    return dict(zip(ix["pair_cols"], row))


def _pf(v):
    """Pair-table cell to number when possible."""
    try:
        if v in ("", None):
            return None
        f = float(v)
        return int(f) if f.is_integer() and "." not in str(v) else f
    except ValueError:
        return v


# ---------------------------------------------------------------- shared html

def _story_link(ix, sid, with_author=True):
    r = ix["records"].get(sid)
    if not r:
        return f"<a href='/story/{_esc(sid)}'>{_esc(sid)}</a>"
    t = r["title"] or "(untitled)"
    if len(t) > 70:
        t = t[:67].rstrip() + "…"
    s = f"<a href='/story/{_esc(sid)}'>{_esc(t)}</a>"
    if with_author and r["author"]:
        s += " <span class='muted'>— </span>" + _author_link(ix, r)
    return s


def _author_link(ix, rec):
    if rec.get("author_key") and rec["author_key"] in ix["authors"]:
        return (f"<a href='/author/{_esc(author_slug(rec['author_key']))}' class='muted'>"
                f"{_esc(rec['author'])}</a>")
    return f"<span class='muted'>{_esc(rec.get('author') or '')}</span>"


def _issue_link(ix, iid):
    i = ix["issues"].get(iid)
    if not i:
        return _esc(iid)
    return (f"<a href='/issue/{_esc(iid)}'>{_esc(i['magazine'])} {_esc(i['cover_date'])}</a>")


def _mag_link(ix, name):
    m = ix["magazines"].get(name)
    if not m:
        return _esc(name or "")
    return f"<a href='/magazine/{_esc(m['slug'])}'>{_esc(name)}</a>"


def _tiles(items):
    """Headline numbers: [(label, value, href)]"""
    out = ["<div style='display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 18px'>"]
    for label, value, href in items:
        v = _n(value)
        box = (f"<div style='flex:1 1 130px;min-width:120px;background:#fff;border:1px solid #d8cfc0;"
               f"padding:8px 12px'><div style='font-size:22px;font-variant-numeric:tabular-nums'>{v}</div>"
               f"<div class='muted' style='font-size:12.5px'>{_esc(label)}</div></div>")
        out.append(f"<a href='{href}' style='text-decoration:none;color:inherit;flex:1 1 130px;display:flex'>{box}</a>"
                   if href else box)
    out.append("</div>")
    return "".join(out)


def _raw_link(path, label="raw data"):
    return (f"<a href='{path}' class='muted' style='font-size:12.5px;border:1px solid #b8a88e;"
            f"padding:1px 8px;text-decoration:none'>{_esc(label)} ↓</a>")


def _table(headers, rows, cls=""):
    h = "".join(f"<th{' class=num' if hd.startswith('#') else ''}>{_esc(hd.lstrip('#'))}</th>" for hd in headers)
    body = "".join("<tr>" + "".join(
        f"<td class='num'>{c[1:]}</td>" if isinstance(c, str) and c.startswith("\x01") else f"<td>{c}</td>"
        for c in r) + "</tr>" for r in rows)
    return f"<div style='overflow-x:auto'><table{(' class=' + cls) if cls else ''}><tr>{h}</tr>{body}</table></div>"


def N(v):
    """Mark a table cell as numeric (right-aligned)."""
    return "\x01" + (_n(v) if not isinstance(v, str) else v)


# ---------------------------------------------------------------- charts

def svg_network(ix, max_nodes=60):
    """Authors as points on a ring grouped by magazine; a curve between two
    authors whose stories share a passage (seed 6, cross-issue). Line width
    grows with the number of shared passages. Every point is a link."""
    links = ix["author_links"]
    if not links:
        return "<div class='empty'>No cross-issue shared passages between named authors.</div>"
    degree = Counter()
    for (ka, kb), L in links.items():
        degree[ka] += L["n"]
        degree[kb] += L["n"]
    nodes = [k for k, _ in degree.most_common(max_nodes)]
    # order by magazine so magazine groups sit together on the ring
    def main_mag(k):
        a = ix["authors"][k]
        return sorted(a["magazines"])[0] if a["magazines"] else ""
    nodes.sort(key=lambda k: (main_mag(k), -degree[k]))
    n = len(nodes)
    W, H = 680, 680
    cx, cy, R = W / 2, H / 2, 215
    pos = {}
    for i, k in enumerate(nodes):
        ang = 2 * math.pi * i / n - math.pi / 2
        pos[k] = (cx + R * math.cos(ang), cy + R * math.sin(ang), ang)
    parts = [f"<svg viewBox='0 0 {W} {H}' width='100%' style='max-width:{W}px' role='img' "
             f"aria-label='Author reuse network' font-family='Georgia,serif'>"]
    maxn = max(L["n"] for L in links.values())
    for (ka, kb), L in sorted(links.items(), key=lambda kv: kv[1]["n"]):
        if ka not in pos or kb not in pos:
            continue
        xa, ya, _ = pos[ka]
        xb, yb, _ = pos[kb]
        w = 1 + 3 * L["n"] / maxn
        parts.append(f"<path d='M{xa:.1f},{ya:.1f} Q{cx:.1f},{cy:.1f} {xb:.1f},{yb:.1f}' fill='none' "
                     f"stroke='{RP.PALETTE[0]}' stroke-opacity='0.45' stroke-width='{w:.1f}'>"
                     f"<title>{_esc(ix['authors'][ka]['names'].most_common(1)[0][0])} ↔ "
                     f"{_esc(ix['authors'][kb]['names'].most_common(1)[0][0])}: {L['n']} shared passage(s), "
                     f"longest {L['longest']} words</title></path>")
    mags = sorted({main_mag(k) for k in nodes})
    for k in nodes:
        x, y, ang = pos[k]
        a = ix["authors"][k]
        r = 4 + 1.5 * math.sqrt(len(a["stories"]))
        name = a["names"].most_common(1)[0][0]
        # labels run along the spoke, flipped on the left half so they read left to right
        lx = x + (r + 6) * math.cos(ang)
        ly = y + (r + 6) * math.sin(ang)
        deg = math.degrees(ang)
        if math.cos(ang) >= 0:
            anchor, rot = "start", deg
        else:
            anchor, rot = "end", deg + 180
        parts.append(f"<a href='/author/{_esc(author_slug(k))}'>"
                     f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r:.1f}' fill='{RP.PALETTE[1]}' stroke='#faf7f2' stroke-width='2'>"
                     f"<title>{_esc(name)} · {len(a['stories'])} stories · {mag_abbr(main_mag(k))} · "
                     f"{degree[k]} shared passages</title></circle>"
                     f"<text x='{lx:.1f}' y='{ly + 3.5:.1f}' font-size='10' fill='{RP.INK}' text-anchor='{anchor}' "
                     f"transform='rotate({rot:.1f} {lx:.1f} {ly:.1f})'>{_esc(name[:24])}</text></a>")
    parts.append(f"<text x='{cx:.1f}' y='{H - 8:.1f}' font-size='11' fill='{RP.INK2}' text-anchor='middle'>"
                 f"{n} authors · {len(links)} author pairs · ring order by magazine: "
                 f"{' · '.join(mag_abbr(m) for m in mags)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def html_grid(ix):
    """Magazine-by-magazine grid of shared passages (seed 6, cross-issue);
    cell shade = count; every cell links to the pair list."""
    mags = sorted(ix["magazines"], key=lambda m: min(ix["issues"][i]["year"] or 0 for i in ix["magazines"][m]["issues"]))
    counts = {}
    mx = 1
    for (a, b), L in ix["mag_links"].items():
        counts[(a, b)] = L["n"]
        counts[(b, a)] = L["n"]
        mx = max(mx, L["n"])
    head = "<tr><th></th>" + "".join(f"<th title='{_esc(m)}'>{_esc(mag_abbr(m))}</th>" for m in mags) + "</tr>"
    rows = []
    for a in mags:
        cells = []
        for b in mags:
            c = counts.get((a, b), 0)
            shade = 0 if not c else 0.15 + 0.75 * (c / mx)
            bg = f"rgba(42,120,214,{shade:.2f})" if c else "#fff"
            ink = "#fff" if shade > 0.55 else RP.INK
            href = f"/pairs?ma={urllib.parse.quote(a)}&mb={urllib.parse.quote(b)}&min=6"
            cells.append(f"<td style='background:{bg};color:{ink};text-align:center;padding:6px'>"
                         f"<a href='{href}' style='color:inherit;text-decoration:none' "
                         f"title='{_esc(a)} × {_esc(b)}: {c} shared passages'>{c if c else '·'}</a></td>")
        rows.append(f"<tr><th style='text-align:left'>{_mag_link(ix, a)}</th>{''.join(cells)}</tr>")
    return ("<div style='overflow-x:auto'><table style='width:auto'>" + head + "".join(rows) + "</table></div>"
            "<p class='muted' style='font-size:12.5px'>Same-magazine cells count passages shared by two "
            "different issues of that magazine; within-issue matches are excluded everywhere.</p>")


def svg_timeline(ix):
    issues = sorted(ix["issues"].values(), key=lambda i: (i["year"] or 0, i["id"]))
    if not issues:
        return ""
    W, H = 720, 290
    left, right, top, bottom = 44, 16, 26, 100
    pw, ph = W - left - right, H - top - bottom
    y0 = math.floor(min(i["year"] for i in issues if i["year"]) - 1)
    y1 = math.ceil(max(i["year"] for i in issues if i["year"]) + 1)
    smax = max(1, max(i["stories"] for i in issues))
    ticks = RP._ticks(smax)
    vtop = ticks[-1] or 1

    def xp(y):
        return left + pw * (y - y0) / max(1, y1 - y0)
    parts = [f"<svg viewBox='0 0 {W} {H}' width='100%' style='max-width:{W}px' role='img' "
             f"aria-label='Issues on a time axis' font-family='Georgia,serif'>",
             f"<text x='{left}' y='16' font-size='13' fill='{RP.INK}'>Issues on the time axis — bar height = stories "
             f"assembled, dark part = verified</text>"]
    for t in ticks:
        y = top + ph - ph * t / vtop
        parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{y:.1f}' y2='{y:.1f}' stroke='{RP.GRID}'/>")
        parts.append(f"<text x='{left - 6}' y='{y + 4:.1f}' font-size='11' fill='{RP.INK2}' text-anchor='end'>{_fmt(t)}</text>")
    for yr in range(y0, y1 + 1, 5 if y1 - y0 > 12 else 1):
        parts.append(f"<text x='{xp(yr):.1f}' y='{top + ph + 14}' font-size='11' fill='{RP.INK2}' text-anchor='middle'>{yr}</text>")
    for n_i, i in enumerate(issues):
        if not i["year"]:
            continue
        x = xp(i["year"])
        ly = top + ph + 30 + 16 * (n_i % 3)
        h = ph * i["stories"] / vtop
        hv = ph * i["verified"] / vtop
        parts.append(f"<a href='/issue/{_esc(i['id'])}'>"
                     f"<rect x='{x - 6:.1f}' y='{top + ph - h:.1f}' width='12' height='{h:.1f}' fill='{RP.PALETTE[0]}' fill-opacity='0.55'>"
                     f"<title>{_esc(i['magazine'])} {_esc(i['cover_date'])}: {i['stories']} stories, {i['verified']} verified, "
                     f"{i['records']} records, {i['words']:,} words</title></rect>"
                     f"<rect x='{x - 6:.1f}' y='{top + ph - hv:.1f}' width='12' height='{hv:.1f}' fill='{RP.PALETTE[0]}'/>"
                     f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{top + ph}' y2='{ly - 9}' stroke='{RP.GRID}'/>"
                     f"<text x='{x:.1f}' y='{ly}' font-size='10' fill='{RP.INK}' text-anchor='middle'>"
                     f"{_esc(mag_abbr(i['magazine']))} {_esc(i['cover_date'][:7])}</text></a>")
    parts.append(f"<line x1='{left}' x2='{left + pw}' y1='{top + ph}' y2='{top + ph}' stroke='{RP.INK2}'/>")
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- overview

def overview(render=None):
    ix = get_index()
    st = ix["stories"]
    n_words = sum(r["n_words"] for r in st.values())
    n_pages = sum(i["pages"] for i in ix["issues"].values())
    n_verified = sum(1 for r in st.values() if r["status"] == "verified")
    runs = RP._runs()
    ex6 = next((s for s in runs["exact"] if s["set"] == "machine" and s["k"] == 6), None)
    p10 = next((s for s in runs["para"] if s["set"] == "machine" and s["_tag"] == "machine_w50s25" and s["k"] == 10), None)
    out = [_howto(
        "Layer 0 of the explorer. Every number and every element of every chart is a link: "
        "a tile opens the list behind it, a point in the network opens the author, a cell in the "
        "grid opens the story pairs, a bar on the time axis opens the issue. From any list you reach "
        "the entity page (author, magazine, issue, story, pair, cluster), and from there the printed "
        "page on the scan and the raw JSON record the page was built from. The ten issues are the "
        "declared development set; the charts show what the method does, not findings about pulp fiction."),
        "<h1>Overview</h1>",
        _tiles([("issues", len(ix["issues"]), "/issues"), ("scanned pages", n_pages, "/issues"),
                ("records of all kinds", len(ix["records"]), "/stories?type=all"),
                ("stories", len(st), "/stories"), ("words in stories", n_words, "/stories?sort=words"),
                ("named authors", len(ix["authors"]), "/authors"),
                ("verified stories", n_verified, "/stories?status=verified"),
                ("annotation actions", len(ix["events"]), "/activity"),
                ("shared passages (seed 6, across issues)", ex6["matches"] if ex6 else 0, "/reuse/clusters"),
                ("reuse clusters", ex6["clusters"] if ex6 else 0, "/reuse/clusters"),
                ("paraphrase alignments", p10["alignments"] if p10 else 0, "/reuse/clusters?kind=para&k=10"),
                ("story pairs in the background table", len(ix["pairs"]), "/pairs")])]
    # 1. network
    out.append("<h2>1. Which authors share passages with which</h2>")
    top_links = sorted(ix["author_links"].items(), key=lambda kv: (-kv[1]["n"], -kv[1]["longest"]))[:12]
    rows = []
    for (ka, kb), L in top_links:
        na = ix["authors"][ka]["names"].most_common(1)[0][0]
        nb = ix["authors"][kb]["names"].most_common(1)[0][0]
        a0, b0 = sorted(L["pairs"])[0]
        rows.append([f"<a href='/author/{_esc(author_slug(ka))}'>{_esc(na)}</a>",
                     f"<a href='/author/{_esc(author_slug(kb))}'>{_esc(nb)}</a>",
                     N(L["n"]), N(L["longest"]), f"<a href='/pair/{_esc(a0)}/{_esc(b0)}'>first pair</a>"])
    out.append(RP._chart_row(svg_network(ix), _table(["author", "author", "#shared passages", "#longest (words)", ""], rows)
                             + "<p class='muted' style='font-size:12.5px'>Shared passages are exact matches of six or more "
                             "words between stories from different issues; in ten issues these are stock phrases, so the "
                             "network shows the machinery, not influence. Full list: <a href='/authors'>authors</a>.</p>"))
    # 2. grid
    out.append("<h2>2. Magazine by magazine</h2>")
    out.append(html_grid(ix))
    # 3. timeline
    out.append("<h2>3. The issues in time</h2>")
    out.append(svg_timeline(ix))
    # 4. census + background + progress
    out.append("<h2>4. Corpus census</h2>")
    mags = sorted(ix["magazines"].values(), key=lambda m: min(ix["issues"][i]["year"] or 0 for i in m["issues"]))
    cats = [mag_abbr(m["name"]) for m in mags]
    c1 = RP.svg_bars(cats, [("stories", [m["stories"] for m in mags])], "Stories per magazine (pilot issues)")
    c2 = RP.svg_bars(cats, [("words (thousands)", [round(m["words"] / 1000, 1) for m in mags])], "Words in stories per magazine, thousands")
    rows = [[_mag_link(ix, m["name"]), N(len(m["issues"])), N(m["records"]), N(m["stories"]), N(m["words"]),
             N(len(m["authors"])), N(m["verified"]), _esc(m["genre"] or ""), _esc(m["format"] or ""),
             _esc(m["publisher_group"] or "")] for m in mags]
    out.append(RP._chart_row(c1 + c2, _table(["magazine", "#issues", "#records", "#stories", "#words", "#authors",
                                              "#verified", "genre", "format", "publisher"], rows)))
    by_type = Counter(r["type"] for r in ix["records"].values())
    by_decade = Counter(f"{int(r['year'] // 10 * 10)}s" for r in st.values() if r["year"])
    by_status = Counter(r["status"] for r in st.values())
    out.append("<p class='muted'>Records by type: " + ", ".join(f"{t} {n:,}" for t, n in by_type.most_common())
               + ". Stories by decade: " + ", ".join(f"{d} {n}" for d, n in sorted(by_decade.items()))
               + ". Story status: " + ", ".join(f"{s} {n}" for s, n in by_status.most_common()) + ".</p>")
    # background + progress
    s = ix["summary"]
    if s:
        bg = s["background"]["exact"].get("k6")
        if bg:
            allx = [int(x) for x in bg["overall"].keys()]
            nonzero = [x for x in allx if (bg["overall"][str(x)] or 0) > 0]
            xs = [x for x in allx if x <= (max(nonzero) + 2 if nonzero else 12)]
            series = [("all pairs", [bg["overall"][str(x)] for x in xs])]
            for q in ("1", "4"):
                d = bg["by_topic_q"].get(q) or bg["by_topic_q"].get(int(q))
                if d:
                    series.append((f"topic quartile {q}", [d.get(str(x)) for x in xs]))
            out.append("<h2>5. How common is a shared passage of a given length?</h2>")
            out.append(RP._chart_row(
                RP.svg_lines(xs, series, "P(longest exact match ≥ L words), seed 6, log scale", ylog=True, xlabel="L (words)"),
                f"<p class='muted'>Among {s['cross_issue_pairs']:,} cross-issue story pairs, "
                f"{_fmt(bg['p_any'] * 100)}% share a passage of six or more words; the share is "
                f"{_fmt((bg['by_topic_q'].get('1') or bg['by_topic_q'].get(1) or {}).get('6', 0) * 100)}% in the "
                f"lowest topic-similarity quarter and "
                f"{_fmt((bg['by_topic_q'].get('4') or bg['by_topic_q'].get(4) or {}).get('6', 0) * 100)}% in the highest. "
                f"Details, the sampler check and the model: <a href='/reuse'>text reuse</a>. "
                f"Every pair: <a href='/pairs'>pairs</a>.</p>"))
    if ix["events"]:
        days = sorted(Counter(e.get("ts", "")[:10] for e in ix["events"]).items())
        cum, tot = [], 0
        for d, c in days:
            tot += c
            cum.append(tot)
        out.append("<h2>6. Annotation progress</h2>")
        out.append(RP._chart_row(
            RP.svg_lines(list(range(len(days))), [("actions, cumulative", cum)], "Annotation actions, cumulative by active day",
                         xlabels=[d[5:] for d, _ in days]),
            f"<p class='muted'>{len(ix['events'])} recorded actions by "
            f"{len({e.get('user') for e in ix['events']})} annotators; {n_verified} stories verified. "
            f"Per annotator and per day: <a href='/reuse/progress'>progress</a>; every action: <a href='/activity'>activity</a>.</p>"))
    out.append(f"<p class='muted' style='font-size:12.5px'>Index built {_esc(ix['built'])} from {len(ix['sources'])} files in "
               f"{ix['build_seconds']}s · {_raw_link('/raw/index', 'what this page was built from')}</p>")
    return _render(render, "Overview", "".join(out), "/overview")


# ---------------------------------------------------------------- authors

def authors_page(qs, render=None):
    ix = get_index()
    q = (qs.get("q", [""])[0] or "").strip().lower()
    sort = (qs.get("sort", ["stories"])[0] or "stories")
    rows = []
    degree = Counter()
    for (ka, kb), L in ix["author_links"].items():
        degree[ka] += L["n"]
        degree[kb] += L["n"]
    items = list(ix["authors"].values())
    if q:
        items = [a for a in items if q in a["key"] or any(q in (n or "").lower() for n in a["names"])]
    keyf = {"stories": lambda a: (-len(a["stories"]), a["key"]), "words": lambda a: (-a["words"], a["key"]),
            "shared": lambda a: (-degree[a["key"]], a["key"]), "name": lambda a: a["key"]}.get(sort, lambda a: a["key"])
    items.sort(key=keyf)
    for a in items:
        names = "; ".join(f"{n}" + (f" ×{c}" if c > 1 else "") for n, c in a["names"].most_common())
        rows.append([f"<a href='/author/{_esc(author_slug(a['key']))}'>{_esc(a['names'].most_common(1)[0][0])}</a>",
                     f"<span class='muted'>{_esc(names)}</span>", N(len(a["stories"])), N(a["words"]),
                     N(len(a["issues"])), _esc(", ".join(mag_abbr(m) for m in sorted(a["magazines"]))),
                     N(degree[a["key"]])])
    form = (f"<form method='GET' action='/authors' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>name contains <input name='q' value='{_esc(q)}'></label>"
            f"<label>sort by <select name='sort'>" + "".join(
                f"<option value='{v}'{' selected' if v == sort else ''}>{l}</option>"
                for v, l in (("stories", "stories"), ("words", "words"), ("shared", "shared passages"), ("name", "name")))
            + "</select></label><button>show</button></form>")
    unnamed = sum(1 for r in ix["stories"].values() if not r["author_key"])
    body = (_howto("Layer 1: every printed by-line, normalized (case, punctuation, and titles such as "
                   "'Captain' removed; pseudonyms NOT resolved — that is implementation-plan item 0.4). "
                   "'Shared passages' counts exact six-word matches between this author's stories and stories "
                   "by other authors in other issues. Click a name for the author's page.")
            + f"<h1>Authors ({len(ix['authors'])})</h1>" + form
            + _table(["author", "as printed", "#stories", "#words", "#issues", "magazines", "#shared passages"], rows)
            + f"<p class='muted'>{unnamed} stories carry no usable by-line and appear only under their issues. "
              f"{_raw_link('/raw/authors', 'raw list')}</p>")
    return _render(render, "Authors", body, "/authors")


def author_page(slug, render=None):
    ix = get_index()
    key = author_unslug(slug)
    a = ix["authors"].get(key)
    if not a:
        return _render(render, "Author", "<h1>No such author</h1><p><a href='/authors'>all authors</a></p>", "/authors")
    name = a["names"].most_common(1)[0][0]
    stories = sorted((ix["stories"][s] for s in a["stories"]), key=lambda r: (r["year"] or 0, r["id"]))
    rows = [[_story_link(ix, r["id"], with_author=False), _issue_link(ix, r["issue"]), N(r["n_words"]),
             _esc(r["status"]), N(len(ix["by_story"][r["id"]]["exact"])), N(len(ix["by_story"][r["id"]]["para"]))]
            for r in stories]
    # partners
    partners = []
    for (ka, kb), L in ix["author_links"].items():
        if key in (ka, kb):
            other = kb if ka == key else ka
            partners.append((other, L))
    partners.sort(key=lambda t: (-t[1]["n"], -t[1]["longest"]))
    prow = []
    for other, L in partners:
        on = ix["authors"][other]["names"].most_common(1)[0][0]
        pairs = sorted(L["pairs"])
        plinks = " · ".join(f"<a href='/pair/{_esc(x)}/{_esc(y)}'>{_esc(x)} ~ {_esc(y)}</a>" for x, y in pairs[:6])
        prow.append([f"<a href='/author/{_esc(author_slug(other))}'>{_esc(on)}</a>", N(L["n"]), N(L["longest"]), plinks])
    # the matches themselves
    mrows = []
    seen = set()
    for r in stories:
        for idx in ix["by_story"][r["id"]]["exact"]:
            if idx in seen:
                continue
            seen.add(idx)
            m = ix["matches"][6][idx]
            other = m["b"] if m["a"] == r["id"] else m["a"]
            mrows.append((m["len"], [N(m["len"]), _esc(m["excerpt"][:120]), _story_link(ix, r["id"], False),
                                     _story_link(ix, other), f"<a href='/pair/{_esc(m['a'])}/{_esc(m['b'])}'>pair</a>"]))
    mrows.sort(key=lambda t: -t[0])
    body = (_howto("Layer 2: one author. The stories table links to each story's page; the partners table "
                   "lists the other authors whose stories share passages with these, with the story pairs; "
                   "the passages table is every shared passage itself, linked to the pair page where it can be "
                   "read in both stories and followed to the scan.")
            + f"<h1>{_esc(name)}</h1>"
            + f"<p class='muted'>printed as {_esc('; '.join(a['names']))} · {len(stories)} stories · {a['words']:,} words · "
              f"{len(a['issues'])} issues · {', '.join(_mag_link(ix, m) for m in sorted(a['magazines']))} · "
              f"{_raw_link('/raw/author/' + slug)}</p>"
            + "<h2>Stories</h2>" + _table(["story", "issue", "#words", "status", "#shared passages", "#paraphrase alignments"], rows)
            + "<h2>Shares passages with</h2>"
            + (_table(["author", "#passages", "#longest", "story pairs"], prow) if prow else
               "<div class='empty'>No shared passages with other named authors.</div>")
            + "<h2>The shared passages</h2>"
            + (_table(["#words", "passage", "in this author's story", "shared with", ""], [r for _, r in mrows[:200]])
               if mrows else "<div class='empty'>None.</div>"))
    return _render(render, name, body, f"/author/{slug}")


# ---------------------------------------------------------------- magazines

def magazines_page(render=None):
    ix = get_index()
    mags = sorted(ix["magazines"].values(), key=lambda m: min(ix["issues"][i]["year"] or 0 for i in m["issues"]))
    rows = []
    for m in mags:
        partners = []
        for (a, b), L in ix["mag_links"].items():
            if m["name"] in (a, b):
                o = b if a == m["name"] else a
                partners.append((L["n"], o))
        partners.sort(reverse=True)
        rows.append([_mag_link(ix, m["name"]), N(len(m["issues"])),
                     " · ".join(_issue_link(ix, i) for i in sorted(m["issues"])),
                     N(m["records"]), N(m["stories"]), N(m["words"]), N(len(m["authors"])), N(m["verified"]),
                     _esc(m["genre"] or ""), _esc(m["format"] or ""),
                     _esc(m["publisher_group"] or "") + (f" <span class='muted'>({_esc('; '.join(sorted(m['publishers'])))})</span>" if m["publishers"] else ""),
                     "; ".join(f"{_esc(mag_abbr(o))} {n}" for n, o in partners[:4])])
    body = (_howto("Layer 1: the magazines of the pilot set, with their issues and the counts that roll up "
                   "from the stories. Publisher names come from pipeline/publishers.json (masthead or "
                   "reference; the file says which). 'Shares with' counts six-word passages shared with "
                   "stories in other magazines.")
            + f"<h1>Magazines ({len(mags)})</h1>"
            + _table(["magazine", "#issues", "issues", "#records", "#stories", "#words", "#authors", "#verified",
                      "genre", "format", "publisher", "shares with"], rows)
            + f"<p class='muted'>{_raw_link('/raw/magazines', 'raw list')}</p>")
    return _render(render, "Magazines", body, "/magazines")


def magazine_page(slug, render=None):
    ix = get_index()
    m = next((x for x in ix["magazines"].values() if x["slug"] == slug), None)
    if not m:
        return _render(render, "Magazine", "<h1>No such magazine</h1>", "/magazines")
    issues = sorted((ix["issues"][i] for i in m["issues"]), key=lambda i: i["year"] or 0)
    irows = [[_issue_link(ix, i["id"]), N(i["pages"]), N(i["records"]), N(i["stories"]), N(i["words"]),
              N(len(i["authors"])), N(i["verified"]), N(i["modified"]), N(i["n_annotation_events"]),
              _esc(i["publisher"] or ""), f"<a href='https://archive.org/details/{_esc(i['ia_identifier'])}'>{_esc(i['ia_identifier'])}</a>"]
             for i in issues]
    authors = Counter()
    for iid in m["issues"]:
        for sid, r in ix["stories"].items():
            if r["issue"] == iid and r["author_key"]:
                authors[r["author_key"]] += 1
    arows = [[f"<a href='/author/{_esc(author_slug(k))}'>{_esc(ix['authors'][k]['names'].most_common(1)[0][0])}</a>", N(n)]
             for k, n in authors.most_common(40)]
    partners = []
    for (a, b), L in ix["mag_links"].items():
        if m["name"] in (a, b):
            o = b if a == m["name"] else a
            partners.append([_mag_link(ix, o) if o != m["name"] else _esc(o) + " (another issue)", N(L["n"]), N(L["longest"]),
                             f"<a href='/pairs?ma={urllib.parse.quote(m['name'])}&mb={urllib.parse.quote(o)}&min=6'>pairs</a>"])
    body = (_howto("Layer 2: one magazine. Issues link to the issue page (scans, articles, provenance); "
                   "authors to their pages; the sharing table to the story pairs between this magazine and another.")
            + f"<h1>{_esc(m['name'])}</h1>"
            + f"<p class='muted'>{_esc(m['genre'] or '')} · {_esc(m['format'] or '')} · publisher {_esc(m['publisher_group'] or 'unknown')} · "
              f"{m['stories']} stories · {m['words']:,} words · {_raw_link('/raw/magazine/' + slug)}</p>"
            + "<h2>Issues</h2>" + _table(["issue", "#pages", "#records", "#stories", "#words", "#authors", "#verified",
                                          "#modified", "#annotation events", "publisher", "archive item"], irows)
            + "<h2>Authors</h2>" + (_table(["author", "#stories"], arows) if arows else "<div class='empty'>No usable by-lines.</div>")
            + "<h2>Shares passages with</h2>" + (_table(["magazine", "#passages", "#longest", ""], partners) if partners
                                                 else "<div class='empty'>None.</div>"))
    return _render(render, m["name"], body, f"/magazine/{slug}")


# ---------------------------------------------------------------- issue extras

def issue_extra_html(iid):
    """Sections appended to the existing issue page: provenance, census,
    reuse, assembly diagnostics, raw links."""
    ix = get_index()
    i = ix["issues"].get(iid)
    if not i:
        return ""
    ia = i["ia"]
    prov = [("archive item", f"<a href='https://archive.org/details/{_esc(i['ia_identifier'])}'>{_esc(i['ia_identifier'])}</a>"),
            ("archive title", _esc(ia.get("title") or "")),
            ("uploaded by", _esc(ia.get("uploader") or "")), ("added to the archive", _esc(ia.get("addeddate") or "")),
            ("scanner / uploader software", _esc(ia.get("scanner") or "")),
            ("archive OCR", _esc(ia.get("ocr") or "")), ("collections", _esc(", ".join(ia.get("collection") or []) if isinstance(ia.get("collection"), list) else (ia.get("collection") or ""))),
            ("page images in the item", _esc(ia.get("imagecount") or "")),
            ("item size", (f"{int(i['ia_item_size']) / 1e6:,.0f} MB" if i.get("ia_item_size") else "")),
            ("file formats offered", _esc(", ".join(sorted({f for f in i["ia_files"] if f}))[:300])),
            ("publisher", _esc(i["publisher"] or "unknown") + (f" <span class='muted'>({_esc(i['publisher_source'])})</span>" if i.get("publisher_source") else "")),
            ("why this issue is in the pilot", _esc(i["why"] or ""))]
    ptable = "<table>" + "".join(f"<tr><th style='text-align:left;width:220px'>{_esc(k)}</th><td>{v}</td></tr>" for k, v in prov if v) + "</table>"
    authors = Counter(r["author_key"] for r in ix["stories"].values() if r["issue"] == iid and r["author_key"])
    alinks = ", ".join(f"<a href='/author/{_esc(author_slug(k))}'>{_esc(ix['authors'][k]['names'].most_common(1)[0][0])}</a>"
                       for k, _ in authors.most_common())
    # reuse involving this issue
    partner = Counter()
    for (a, b), n in ix["issue_links"].items():
        if iid in (a, b):
            partner[b if a == iid else a] += n
    prows = [[_issue_link(ix, o), N(n), f"<a href='/pairs?ia={urllib.parse.quote(iid)}&ib={urllib.parse.quote(o)}&min=6'>pairs</a>"]
             for o, n in partner.most_common()]
    ov = (ix["overlap"] or {}).get(iid)
    orow = ""
    if ov:
        wp = "; ".join(f"<a href='/story/{_esc(p['a'])}'>{_esc(p['a'])}</a> ~ <a href='/story/{_esc(p['b'])}'>{_esc(p['b'])}</a> ({p['shared_keys']})"
                       for p in ov["worst_pairs"][:5])
        orow = (f"<p>{ov['keys_owned_by_2plus']} of {ov['region_keys']} text regions are listed under more than one record "
                f"({ov['stories_sharing']} records involved). {('Worst pairs: ' + wp) if wp else ''}</p>")
    out = ["<h2>Provenance (from the Internet Archive item)</h2>", ptable,
           "<h2>Census</h2>",
           f"<p>{i['records']} records, {i['stories']} stories ({i['words']:,} words), {i['verified']} verified, "
           f"{i['modified']} modified, {i['n_annotation_events']} annotation events. "
           f"Authors: {alinks or '<span class=muted>no usable by-lines</span>'}. "
           f"<a href='/stories?issue={_esc(iid)}&type=all'>all records of this issue</a>.</p>",
           "<h2>Shared passages with other issues</h2>",
           _table(["other issue", "#passages (seed 6)", ""], prows) if prows else "<div class='empty'>None.</div>",
           "<h2>Assembly check</h2>", orow or "<p class='muted'>No region is listed under two records in this issue.</p>",
           f"<p class='muted'>{_raw_link('/raw/issue/' + iid)} · <a href='/reuse/clusters?same=1&kind=exact&k=6'>same-issue diagnostics</a></p>"]
    return "".join(out)


# ---------------------------------------------------------------- stories

def stories_page(qs, render=None):
    ix = get_index()
    g = lambda k, d="": (qs.get(k, [d]) or [d])[0]
    typ = g("type", "story")
    mag = g("mag")
    issue = g("issue")
    status = g("status")
    author = g("author")
    q = g("q").strip().lower()
    sort = g("sort", "issue")
    try:
        min_w = int(g("min", "0") or 0)
    except ValueError:
        min_w = 0
    recs = list(ix["records"].values())
    if typ == "story":
        recs = [r for r in recs if r["is_story"]]
    elif typ != "all":
        recs = [r for r in recs if r["type"] == typ]
    if mag:
        recs = [r for r in recs if mag_slug(r["magazine"]) == mag or r["magazine"] == mag]
    if issue:
        recs = [r for r in recs if r["issue"] == issue]
    if status:
        recs = [r for r in recs if r["status"] == status]
    if author:
        recs = [r for r in recs if r["author_key"] == author_unslug(author)]
    if q:
        recs = [r for r in recs if q in (r["title"] or "").lower() or q in (r["author"] or "").lower() or q in r["id"]]
    recs = [r for r in recs if r["n_words"] >= min_w]
    keyf = {"issue": lambda r: (r["year"] or 0, r["id"]), "words": lambda r: (-r["n_words"], r["id"]),
            "title": lambda r: ((r["title"] or "~").lower(), r["id"]),
            "shared": lambda r: (-len(ix["by_story"][r["id"]]["exact"]), r["id"])}.get(sort)
    recs.sort(key=keyf or (lambda r: r["id"]))
    types = ["story", "all"] + sorted({r["type"] for r in ix["records"].values()} - {"story"})
    sel = lambda name, val, opts: f"<select name='{name}'>" + "".join(
        f"<option value='{_esc(v)}'{' selected' if str(v) == str(val) else ''}>{_esc(l)}</option>" for v, l in opts) + "</select>"
    form = (f"<form method='GET' action='/stories' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>type {sel('type', typ, [(t, t) for t in types])}</label>"
            f"<label>magazine {sel('mag', mag, [('', 'all')] + [(m['slug'], mag_abbr(m['name'])) for m in ix['magazines'].values()])}</label>"
            f"<label>issue {sel('issue', issue, [('', 'all')] + [(i, i) for i in sorted(ix['issues'])])}</label>"
            f"<label>status {sel('status', status, [('', 'any'), ('auto', 'automatic'), ('modified', 'modified'), ('verified', 'verified')])}</label>"
            f"<label>min words <input name='min' value='{min_w}' style='width:56px'></label>"
            f"<label>text <input name='q' value='{_esc(q)}' style='width:140px'></label>"
            f"<label>sort {sel('sort', sort, [('issue', 'issue'), ('words', 'words'), ('title', 'title'), ('shared', 'shared passages')])}</label>"
            f"<input type='hidden' name='author' value='{_esc(author)}'><button>show</button></form>")
    rows = [[_story_link(ix, r["id"]), _esc(r["type"]), _issue_link(ix, r["issue"]),
             _esc(",".join(str(p) for p in r["pages"][:3]) + ("…" if len(r["pages"]) > 3 else "")),
             N(r["n_words"]), N(r["n_regions"]), _esc(r["status"]),
             N(len(ix["by_story"][r["id"]]["exact"])), N(len(ix["by_story"][r["id"]]["para"])),
             f"<a href='/article/{_esc(r['id'])}' class='muted'>workbench</a>"] for r in recs[:600]]
    body = (_howto("Layer 1: every record the assembly produced — stories by default; switch the type to see "
                   "advertisements, features, poems, or everything. Counts and status are as exported for the "
                   "reuse run; the workbench link shows the record as it stands now with its scan regions.")
            + f"<h1>Records — {len(recs)} shown</h1>" + form
            + _table(["title — author", "type", "issue", "pages", "#words", "#regions", "status", "#shared", "#paraphrase", ""], rows)
            + (f"<p class='muted'>Only the first 600 are listed; narrow the filters.</p>" if len(recs) > 600 else "")
            + f"<p class='muted'>{_raw_link('/raw/stories', 'raw export (all records)')}</p>")
    return _render(render, "Records", body, "/stories")


def story_page(sid, render=None):
    ix = get_index()
    r = ix["records"].get(sid)
    if not r:
        return _render(render, "Story", f"<h1>No such record</h1><p class='muted'>{_esc(sid)} is not in the export; "
                                        f"<a href='/article/{_esc(sid)}'>try the workbench</a>.</p>", "/stories")
    live, _doc = _G["article_by_id"](sid)
    live_status = (live or {}).get("status")
    live_title = (live or {}).get("title")
    ex = [ix["matches"][6][i] for i in ix["by_story"][sid]["exact"]]
    ex.sort(key=lambda m: -m["len"])
    erows = []
    for m in ex:
        other = m["b"] if m["a"] == sid else m["a"]
        erows.append([N(m["len"]), _esc(m["excerpt"][:140]), _story_link(ix, other), _issue_link(ix, ix["records"][other]["issue"] if other in ix["records"] else ""),
                      f"<a href='/pair/{_esc(m['a'])}/{_esc(m['b'])}'>pair</a>"])
    pa = [ix["aligns"][i] for i in ix["by_story"][sid]["para"]]
    prows = [[N(a["cols"]), N(a["identity"]), _esc((a["text_a"] if a["a"] == sid else a["text_b"])[:140]),
              _story_link(ix, a["b"] if a["a"] == sid else a["a"]), f"<a href='/pair/{_esc(a['a'])}/{_esc(a['b'])}'>pair</a>"] for a in pa]
    # closest stories by topic (pair table)
    near = []
    if ix["pairs"]:
        cols = ix["pair_cols"]
        ci = {c: n for n, c in enumerate(cols)}
        for (a, b), row in ix["pairs"].items():
            if sid in (a, b):
                other = b if a == sid else a
                try:
                    near.append((float(row[ci["topic_tfidf"]]), other, row))
                except ValueError:
                    pass
        near.sort(key=lambda t: -t[0])
    nrows = []
    ci = {c: n for n, c in enumerate(ix["pair_cols"])} if ix["pair_cols"] else {}
    for sim, other, row in near[:12]:
        nrows.append([N(round(sim, 3)), _story_link(ix, other), _esc(row[ci["years_apart"]]) if "years_apart" in ci else "",
                      N(_pf(row[ci["exact_k6_longest"]]) or 0) if "exact_k6_longest" in ci else "",
                      f"<a href='/pair/{_esc(sid)}/{_esc(other)}'>pair</a>"])
    events = [e for e in ix["events"] if e.get("article_id") == sid]
    surprise = []
    s = ix["summary"] or {}
    for name in ("exact_k6", "para_k10"):
        for u in (s.get("unusual", {}).get(name, {}).get("most_unusual", []) or []):
            if sid in (u["a"], u["b"]):
                surprise.append((name, u))
    body = [_howto("Layer 2: one record. Facts as exported for the reuse run, the live status on the workbench, "
                   "every passage it shares with other stories (each linked to the pair page where both texts are "
                   "shown and to the scan), its closest stories by topic, and its annotation history. "
                   "Layer 3 is one click away: the workbench (regions on the scan) and the raw records."),
            f"<h1>{_esc(r['title'] or '(untitled)')}</h1>",
            f"<p class='muted'>{_author_link(ix, r)} · {_issue_link(ix, r['issue'])} · {_esc(r['type'])} · "
            f"pages {_esc(', '.join(str(p) for p in r['pages'][:12]))}{'…' if len(r['pages']) > 12 else ''} · "
            f"{r['n_words']:,} words · {r['n_regions']} regions · status at export {_esc(r['status'])}"
            + (f" (now {_esc(live_status)})" if live_status and live_status != r["status"] else "")
            + (f" · verified by {_esc(r['verified_by'])}" if r.get("verified_by") else "")
            + (f" · modified by {_esc(', '.join(r['modified_by']))}" if r.get("modified_by") else "")
            + (f" · workbench title now: {_esc(live_title)}" if live_title and live_title != r["title"] else "") + "</p>",
            f"<p><a href='/article/{_esc(sid)}'>open on the workbench</a> · "
            + (" · ".join(f"<a href='/issue/{_esc(r['issue'])}/p/{p}'>scan p.{p}</a>" for p in r["pages"][:8]))
            + f" · {_raw_link('/raw/story/' + sid)}</p>",
            "<h2>Shared passages (exact, seed 6, other issues)</h2>",
            _table(["#words", "passage", "shared with", "in issue", ""], erows) if erows else "<div class='empty'>None.</div>",
            "<h2>Paraphrase alignments</h2>",
            _table(["#columns", "#identity", "this side", "other story", ""], prows) if prows else "<div class='empty'>None.</div>",
            "<h2>Closest stories by topic (reuse-masked TF-IDF)</h2>",
            _table(["#similarity", "story", "years apart", "#longest shared", ""], nrows) if nrows else "<div class='empty'>Not in the pair table (records under 50 words or non-stories are not compared).</div>"]
    if surprise:
        body.append("<h2>In the background</h2><ul>" + "".join(
            f"<li>{_esc(name)}: longest {u['longest']}, stratum topic q{u['topic_q']} · {u['years_band']} years (n={u['stratum_n']:,}): "
            f"P(at least this) {('under 1 in ' + format(u['stratum_n'], ',')) if u['p_at_least'] == 0 else u['p_at_least']}</li>"
            for name, u in surprise) + "</ul>")
    body.append("<h2>Annotation history</h2>")
    if events:
        body.append(_table(["when", "who", "action", "detail"], [[_esc(e.get("ts", "")), _esc(_G["display_name"](e.get("user", "?"))),
                                                                  _esc(e.get("action", "")), _esc(str({k: v for k, v in e.items() if k not in ("ts", "user", "action", "issue", "article_id")})[:160])]
                                                                 for e in events]))
    else:
        body.append("<div class='empty'>No human action on this record yet.</div>")
    return _render(render, r["title"] or sid, "".join(body), f"/story/{sid}")


# ---------------------------------------------------------------- pairs

def pairs_page(qs, render=None):
    ix = get_index()
    g = lambda k, d="": (qs.get(k, [d]) or [d])[0]
    if not ix["pairs"]:
        return _render(render, "Pairs", "<h1>Story pairs</h1><div class='empty'>The pair table has not been built on this server.</div>", "/pairs")
    cols = ix["pair_cols"]
    ci = {c: n for n, c in enumerate(cols)}
    ma, mb, ia, ib = g("ma"), g("mb"), g("ia"), g("ib")
    try:
        min_len = int(g("min", "6") or 0)
    except ValueError:
        min_len = 6
    same_author = g("same_author")
    band = g("band")
    q = g("q").strip().lower()
    kind = g("kind", "exact")
    sort = g("sort", "longest")
    cross_only = g("same", "0") != "1"
    rows = []
    for (a, b), row in ix["pairs"].items():
        if cross_only and row[ci["same_issue"]] == "1":
            continue
        if ma and mb:
            mags = {row[ci["magazine_a"]], row[ci["magazine_b"]]}
            if ma == mb:
                if not (row[ci["magazine_a"]] == ma and row[ci["magazine_b"]] == ma):
                    continue
            elif mags != {ma, mb}:
                continue
        if ia and ib:
            iss = {row[ci["issue_a"]], row[ci["issue_b"]]}
            if iss != {ia, ib}:
                continue
        if same_author == "1" and row[ci["same_author"]] != "1":
            continue
        if band and row[ci["years_band"]] != band:
            continue
        L = _pf(row[ci["exact_k6_longest"]]) or 0
        C = _pf(row[ci["para_k10_longest"]]) or 0
        if kind == "exact" and L < min_len:
            continue
        if kind == "para" and C < max(min_len, 1):
            continue
        if q and q not in (row[ci["exact_excerpt"]] + " " + row[ci["para_excerpt"]]).lower() and q not in a and q not in b:
            continue
        rows.append((a, b, row, L, C))
    keyf = {"longest": lambda t: (-t[3], -t[4]), "topic": lambda t: -float(t[2][ci["topic_tfidf"]] or 0),
            "years": lambda t: float(t[2][ci["years_apart"]] or 0), "para": lambda t: (-t[4], -t[3])}.get(sort, lambda t: (-t[3], -t[4]))
    rows.sort(key=keyf)
    sel = lambda name, val, opts: f"<select name='{name}'>" + "".join(
        f"<option value='{_esc(v)}'{' selected' if str(v) == str(val) else ''}>{_esc(l)}</option>" for v, l in opts) + "</select>"
    magopts = [("", "any")] + [(m, mag_abbr(m)) for m in ix["magazines"]]
    form = (f"<form method='GET' action='/pairs' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>kind {sel('kind', kind, [('exact', 'exact (seed 6)'), ('para', 'paraphrase (K=10)')])}</label>"
            f"<label>min longest <input name='min' value='{min_len}' style='width:50px'></label>"
            f"<label>magazines {sel('ma', ma, magopts)} × {sel('mb', mb, magopts)}</label>"
            f"<label>years apart {sel('band', band, [('', 'any'), ('0-2', '0-2'), ('3-9', '3-9'), ('10-19', '10-19'), ('20+', '20+')])}</label>"
            f"<label><input type='checkbox' name='same_author' value='1'{' checked' if same_author == '1' else ''}> same author</label>"
            f"<label><input type='checkbox' name='same' value='1'{' checked' if not cross_only else ''}> include same-issue pairs</label>"
            f"<label>text <input name='q' value='{_esc(q)}' style='width:120px'></label>"
            f"<label>sort {sel('sort', sort, [('longest', 'longest exact'), ('para', 'longest paraphrase'), ('topic', 'topic similarity'), ('years', 'years apart')])}</label>"
            f"<input type='hidden' name='ia' value='{_esc(ia)}'><input type='hidden' name='ib' value='{_esc(ib)}'><button>show</button></form>")
    trows = []
    for a, b, row, L, C in rows[:400]:
        trows.append([f"<a href='/pair/{_esc(a)}/{_esc(b)}'>open</a>", _story_link(ix, a), _story_link(ix, b),
                      N(L), N(_pf(row[ci["exact_k6_n"]]) or 0), N(C), N(round(float(row[ci["topic_tfidf"]] or 0), 3)),
                      _esc(row[ci["years_apart"]]), "yes" if row[ci["same_author"]] == "1" else "",
                      "yes" if row[ci["same_magazine"]] == "1" else "", _esc((row[ci["exact_excerpt"]] or row[ci["para_excerpt"]])[:90])])
    body = (_howto("Layer 1: the story-pair table of the background stage — one row per pair of stories with "
                   "everything the protocol conditions on. Default view: cross-issue pairs that share at least a "
                   "six-word passage, longest first. Open a pair to read every shared passage in both stories.")
            + f"<h1>Story pairs — {len(rows):,} match the filters (of {len(ix['pairs']):,})</h1>" + form
            + _table(["", "story A", "story B", "#longest exact", "#exact matches", "#longest paraphrase", "#topic sim.",
                      "years apart", "same author", "same magazine", "passage"], trows)
            + (f"<p class='muted'>First 400 shown.</p>" if len(rows) > 400 else "")
            + f"<p class='muted'>{_raw_link('/raw/file?path=reuse/background/pairs_machine.csv.gz', 'download the full table (CSV)')} · "
              f"columns: {_esc(', '.join(cols))}</p>")
    return _render(render, "Story pairs", body, "/pairs")


def pair_page(a, b, render=None):
    ix = get_index()
    row = pair_row(ix, a, b)
    ra, rb = ix["records"].get(a), ix["records"].get(b)
    if row:
        a, b = row["a"], row["b"]
        ra, rb = ix["records"].get(a), ix["records"].get(b)
    if not ra or not rb:
        return _render(render, "Pair", "<h1>No such pair</h1>", "/pairs")
    ex = [m for m in ix["matches"].get(6, []) if {m["a"], m["b"]} == {a, b}]
    ex += [m for m in ix["same_issue"].get(6, []) if {m["a"], m["b"]} == {a, b}]
    ex.sort(key=lambda m: -m["len"])
    al = [x for x in ix["aligns"] if {x["a"], x["b"]} == {a, b}]

    def head(r):
        return (f"<div style='flex:1 1 300px;background:#fff;border:1px solid #d8cfc0;padding:8px 12px'>"
                f"<div style='font-size:17px'>{_story_link(ix, r['id'], False)}</div>"
                f"<div class='muted'>{_author_link(ix, r)}</div>"
                f"<div class='muted'>{_issue_link(ix, r['issue'])} · {r['n_words']:,} words · {_esc(r['status'])} · "
                f"<a href='/article/{_esc(r['id'])}'>workbench</a></div></div>")
    facts = ""
    if row:
        keys = [("topic similarity (TF-IDF, masked)", "topic_tfidf"), ("topic similarity (embedding)", "topic_emb"),
                ("topic quartile", "topic_q"), ("years apart", "years_apart"), ("years band", "years_band"),
                ("later year", "later_year"), ("same author", "same_author"), ("author known on both sides", "author_known"),
                ("same magazine", "same_magazine"), ("same publisher", "same_publisher"), ("same genre", "same_genre"),
                ("same format", "same_format"), ("same issue", "same_issue"), ("shared scan regions", "shared_regions"),
                ("longest exact match (seed 6 / 7 / 8)", None), ("exact matches (seed 6)", "exact_k6_n"),
                ("share of each story covered by exact matches (max)", "exact_k6_share_max"),
                ("longest paraphrase alignment (K=5 / 10 / 20)", None), ("best paraphrase identity (K=10)", "para_k10_best_identity")]
        cells = []
        for label, col in keys:
            if col is None and label.startswith("longest exact"):
                v = " / ".join(str(_pf(row.get(f"exact_k{k}_longest")) or 0) for k in (6, 7, 8))
            elif col is None:
                v = " / ".join(str(_pf(row.get(f"para_k{k}_longest")) or 0) for k in (5, 10, 20))
            else:
                v = row.get(col, "")
                if col.startswith("same_") or col in ("author_known",):
                    v = "yes" if v == "1" else "no"
            cells.append(f"<tr><th style='text-align:left;width:300px'>{_esc(label)}</th><td>{_esc(str(v))}</td></tr>")
        facts = "<table>" + "".join(cells) + "</table>"
    # background placement
    bgnote = ""
    s = ix["summary"]
    if s and row:
        try:
            q, band = str(row["topic_q"]), row["years_band"]
            L = _pf(row["exact_k6_longest"]) or 0
            tt = s["background"]["time_table"]["exact_k6"]
            curve = s["background"]["exact"]["k6"]["by_topic_q"].get(q) or s["background"]["exact"]["k6"]["by_topic_q"].get(int(q))
            if curve and L >= 6:
                p = curve.get(str(L))
                bgnote = (f"<p>Among cross-issue pairs in the same topic quartile ({q}), the share sharing a passage of "
                          f"at least {L} words is {_fmt(p * 100)}%.</p>")
        except Exception:
            bgnote = ""
    body = [_howto("Layer 2: two stories side by side. The facts table is this pair's row in the background "
                   "table; below it every exact passage the two share (with the words around it as each story "
                   "reads now, and links to the scan region), then every paraphrase alignment with the "
                   "differences marked. Layer 3: the raw records."),
            f"<h1>Story pair</h1><div style='display:flex;gap:12px;flex-wrap:wrap'>{head(ra)}{head(rb)}</div>",
            f"<p class='muted' style='margin-top:8px'>{_raw_link(f'/raw/pair/{a}/{b}')}</p>",
            "<h2>Facts about the pair</h2>", facts or "<div class='empty'>Not in the pair table (one side is not a story of 50+ words).</div>",
            bgnote,
            "<h2>Exact shared passages (seed 6)</h2>"]
    if ex:
        for m in ex:
            la = RP.locate_in_article(ra["issue"], a if m["a"] == a else b, m["excerpt"]) if m["a"] in (a, b) else None
            lb = RP.locate_in_article(rb["issue"], b if m["b"] == b else a, m["excerpt"])
            def side(r, loc):
                links = ""
                if loc:
                    links = (f"<a href='/article/{_esc(r['id'])}?sel={urllib.parse.quote(loc['fragkey'])}'>on the scan</a>"
                             + (f" (p.{loc['page']})" if loc.get("page") else ""))
                else:
                    links = "<span class='muted'>not located now</span>"
                return (f"<div style='flex:1 1 300px'><div class='muted' style='font-size:12px'>{_esc(r['title'] or r['id'])} · {links}</div>"
                        f"<div><span class='muted'>{_esc((loc or {}).get('before', ''))}</span>"
                        f"<span style='background:#f3e2a8'>{_esc(m['excerpt'])}</span>"
                        f"<span class='muted'>{_esc((loc or {}).get('after', ''))}</span></div></div>")
            ra_, rb_ = (ra, rb) if m["a"] == a else (rb, ra)
            body.append(f"<div class='card'><div class='ch'><span>{m['len']} words</span>"
                        + (f"<span class='muted'>{_esc(m.get('cause', ''))}</span>" if m.get("cause") else "")
                        + f"</div><div class='cardtext' style='max-height:none;display:flex;gap:16px;flex-wrap:wrap'>"
                        f"{side(ra_, la)}{side(rb_, lb)}</div></div>")
    else:
        body.append("<div class='empty'>None.</div>")
    body.append("<h2>Paraphrase alignments (K=10)</h2>")
    if al:
        for x in al:
            body.append(f"<div class='card'><div class='ch'><span>{x['cols']} columns · identity {x['identity']} · score {x['score']} · "
                        f"source {', '.join(x['sources'])}</span></div><div class='cardtext' style='max-height:none'>"
                        f"{_G['diff_html'](x['text_a'], x['text_b'])}</div></div>")
    else:
        body.append("<div class='empty'>None.</div>")
    return _render(render, "Story pair", "".join(body), f"/pair/{a}/{b}")


# ---------------------------------------------------------------- raw layer

def _clean(obj):
    """JSON-safe copy (sets to sorted lists, Counters to dicts)."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_clean(x) for x in obj)
    if isinstance(obj, (list, tuple)):
        return [_clean(x) for x in obj]
    if isinstance(obj, Counter):
        return dict(obj)
    return obj


def raw_story(sid):
    ix = get_index()
    r = ix["records"].get(sid)
    if not r:
        return None
    live, _ = _G["article_by_id"](sid)
    ex = [ix["matches"][6][i] for i in ix["by_story"][sid]["exact"]]
    pa = [ix["aligns"][i] for i in ix["by_story"][sid]["para"]]
    return {"source_files": ["data/pilot_stories.jsonl (export record)", "site replay of data/articles + data/annotations (live)",
                             "data/reuse/machine_k6_matches.jsonl", "data/reuse/para/machine_w50s25_k10_alignments.jsonl"],
            "export_record": _clean(r), "live_article": _clean(live) if live else None,
            "exact_matches_k6": _clean(ex), "paraphrase_alignments_k10": _clean(pa),
            "annotation_events": [e for e in ix["events"] if e.get("article_id") == sid]}


def raw_pair(a, b):
    ix = get_index()
    row = pair_row(ix, a, b)
    ex = [m for m in ix["matches"].get(6, []) + ix["same_issue"].get(6, []) if {m["a"], m["b"]} == {a, b}]
    al = [x for x in ix["aligns"] if {x["a"], x["b"]} == {a, b}]
    return {"source_files": ["data/reuse/background/pairs_machine.csv.gz", "data/reuse/machine_k6_matches.jsonl",
                             "data/reuse/machine_k6_sameissue.jsonl", "data/reuse/para/machine_w50s25_k10_alignments.jsonl"],
            "pair_row": row, "exact_matches_k6": _clean(ex), "paraphrase_alignments_k10": _clean(al)}


def raw_author(slug):
    ix = get_index()
    a = ix["authors"].get(author_unslug(slug))
    if not a:
        return None
    links = {f"{ka} | {kb}": _clean(L) for (ka, kb), L in ix["author_links"].items() if a["key"] in (ka, kb)}
    return {"source_files": ["data/pilot_stories.jsonl (by-lines, normalized with pipeline/r01_normalize.author_key)",
                             "data/reuse/machine_k6_matches.jsonl"],
            "author": _clean(a), "links": links, "stories": [_clean(ix["stories"][s]) for s in a["stories"]]}


def raw_issue(iid):
    ix = get_index()
    i = ix["issues"].get(iid)
    if not i:
        return None
    meta = _json(os.path.join(_G["DATA"], "raw", iid, "meta.json"))
    return {"source_files": ["config/pilot_issues.json", f"data/raw/{iid}/meta.json (Internet Archive item metadata)",
                             "data/pilot_stories.jsonl", "data/reuse/machine_region_overlap.json", "pipeline/publishers.json"],
            "issue": _clean(i), "archive_item": meta, "region_overlap": (ix["overlap"] or {}).get(iid),
            "records": [_clean(r) for r in ix["records"].values() if r["issue"] == iid]}


def raw_magazine(slug):
    ix = get_index()
    m = next((x for x in ix["magazines"].values() if x["slug"] == slug), None)
    if not m:
        return None
    return {"source_files": ["config/pilot_issues.json", "data/pilot_stories.jsonl", "pipeline/publishers.json"],
            "magazine": _clean(m), "issues": [_clean(ix["issues"][i]) for i in m["issues"]],
            "links": {f"{a} | {b}": _clean(L) for (a, b), L in ix["mag_links"].items() if m["name"] in (a, b)}}


def raw_index():
    ix = get_index()
    D = _G["DATA"]
    files = []
    for root, _dirs, fs in os.walk(os.path.join(D, "reuse")):
        for f in fs:
            p = os.path.join(root, f)
            files.append({"path": os.path.relpath(p, D), "bytes": os.path.getsize(p)})
    return {"built": ix["built"], "build_seconds": ix["build_seconds"], "sources": ix["sources"],
            "counts": {"records": len(ix["records"]), "stories": len(ix["stories"]), "authors": len(ix["authors"]),
                       "magazines": len(ix["magazines"]), "issues": len(ix["issues"]),
                       "exact_matches": {str(k): len(v) for k, v in ix["matches"].items()},
                       "paraphrase_alignments": len(ix["aligns"]), "pairs": len(ix["pairs"]), "events": len(ix["events"])},
            "reuse_files": sorted(files, key=lambda f: f["path"]),
            "data_door": ["/api/<token>/ls?path=…", "/api/<token>/get?path=…", "/api/<token>/doc/<issue>",
                          "/api/<token>/index", "/api/<token>/story/<id>", "/api/<token>/pair/<a>/<b>",
                          "/api/<token>/author/<slug>", "/api/<token>/issue/<id>", "/api/<token>/magazine/<slug>",
                          "/api/<token>/authors", "/api/<token>/magazines", "/api/<token>/stories", "/api/<token>/pairs"]}


def raw_list(kind):
    ix = get_index()
    if kind == "authors":
        return {"source_files": ["data/pilot_stories.jsonl"], "authors": [_clean(a) for a in ix["authors"].values()]}
    if kind == "magazines":
        return {"source_files": ["config/pilot_issues.json", "data/pilot_stories.jsonl"], "magazines": [_clean(m) for m in ix["magazines"].values()]}
    if kind == "stories":
        return {"source_files": ["data/pilot_stories.jsonl"], "records": [_clean(r) for r in ix["records"].values()]}
    if kind == "pairs":
        return {"source_files": ["data/reuse/background/pairs_machine.csv.gz"], "columns": ix["pair_cols"],
                "rows": [list(r) for r in ix["pairs"].values()]}
    return None


RAW_ROOTS = ("reuse", "raw", "articles", "annotations", "layout", "text", "gold", "metrics.json", "timings.jsonl",
             "pilot_stories.jsonl", "pilot_stories.jsonl.gz", "feedback.jsonl")


def raw_file_path(rel):
    """Absolute path for a members' raw-file request, or None if outside
    the allowed subtrees of data/."""
    D = os.path.realpath(_G["DATA"])
    p = os.path.realpath(os.path.join(D, rel.strip("/")))
    if not p.startswith(D + os.sep):
        return None
    sub = os.path.relpath(p, D)
    if not any(sub == r or sub.startswith(r + os.sep) for r in RAW_ROOTS):
        return None
    return p if os.path.exists(p) else None


def raw_page(kind, arg, render=None):
    """HTML view of a raw JSON object with a download link."""
    builders = {"story": lambda: raw_story(arg), "pair": lambda: raw_pair(*arg), "author": lambda: raw_author(arg),
                "issue": lambda: raw_issue(arg), "magazine": lambda: raw_magazine(arg), "index": raw_index,
                "authors": lambda: raw_list("authors"), "magazines": lambda: raw_list("magazines"),
                "stories": lambda: raw_list("stories"), "pairs": lambda: raw_list("pairs")}
    obj = builders[kind]() if kind in builders else None
    if obj is None:
        return _render(render, "Raw", "<h1>No such record</h1>", "/overview")
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    big = len(text) > 400000
    shown = text[:400000] + ("\n… (truncated on screen; download for the whole record)" if big else "")
    path = f"/raw/{kind}" + (("/" + "/".join(arg)) if isinstance(arg, tuple) else (("/" + arg) if arg else ""))
    body = (_howto("Layer 3: the raw records behind the page you came from, exactly as they are in the files "
                   "named at the top of the record. The download gives the same content as a JSON file.")
            + f"<h1>Raw: {_esc(kind)}{(' ' + _esc(' / '.join(arg) if isinstance(arg, tuple) else arg)) if arg else ''}</h1>"
            + f"<p><a href='{path}.json'>download as JSON</a> · {len(text):,} characters</p>"
            + f"<pre style='max-height:none;font-family:Menlo,Consolas,monospace;font-size:12.5px'>{_esc(shown)}</pre>")
    return _render(render, "Raw", body, path)


def raw_json(kind, arg):
    builders = {"story": lambda: raw_story(arg), "pair": lambda: raw_pair(*arg), "author": lambda: raw_author(arg),
                "issue": lambda: raw_issue(arg), "magazine": lambda: raw_magazine(arg), "index": raw_index,
                "authors": lambda: raw_list("authors"), "magazines": lambda: raw_list("magazines"),
                "stories": lambda: raw_list("stories"), "pairs": lambda: raw_list("pairs")}
    obj = builders[kind]() if kind in builders else None
    return None if obj is None else json.dumps(obj, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- method page parts

def params_html():
    """The settings actually used, read from the stats files."""
    runs = RP._runs()
    rows = []
    for st in runs["exact"]:
        rows.append(["exact matching (r02)", _esc(st["set"]), f"seed {st['k']} words · commonplace cap {st['max_df']} stories per shingle",
                     _esc(st.get("generated", "")), f"<a href='/raw/file?path=reuse/{st['set']}_k{st['k']}_stats.json'>stats</a>"])
    for st in runs["para"]:
        rows.append(["paraphrase (r04)", _esc(st["set"]),
                     f"window {st['window']} / step {st['stride']} · model {_esc(st.get('model') or 'none')} · K={st['k']} · "
                     f"keep {st['min_cols']}+ columns at identity {st['min_identity']}+ · scores {st['scores']} · "
                     f"fuzzy words {st['fuzzy']['min_len']}+ letters, {st['fuzzy']['alternates']} alternates",
                     _esc(st.get("generated", "")), f"<a href='/raw/file?path=reuse/para/{st['_tag']}_k{st['k']}_stats.json'>stats</a>"])
    for sm in runs["background"]:
        sc = sm.get("sampler_check", {})
        rows.append(["background (r05)", _esc(sm["set"]),
                     f"all {sm['pairs']:,} pairs · topic quartile cuts {sm.get('topic_quartile_cuts')} · sampler "
                     f"{sc.get('n_per_stratum')} per stratum × {sc.get('seeds')} draws · models: "
                     + ", ".join(sm.get("models", {}).keys()),
                     _esc(sm.get("generated", "")), f"<a href='/raw/file?path=reuse/background/summary_{sm['set']}.json'>summary</a>"])
    ex = runs["synthetic"].get("exact")
    if ex:
        rows.append(["planted reuse (r03)", "synthetic copy", f"{ex.get('_stories')} stories · {ex.get('_plants')} plants · seeds 6/7/8",
                     _esc(ex.get("_generated", "")), "<a href='/raw/file?path=reuse/synthetic/recall_verbatim.json'>recall</a>"])
    for tag, rp in runs["synthetic"].get("para", {}).items():
        kr = rp.get("_keep_rule", {})
        rows.append(["planted reuse, paraphrase (r04)", "synthetic copy",
                     f"{_esc(tag)} · keep {kr.get('min_cols', 20)}+ columns at identity {kr.get('min_identity', 0.6)}+",
                     _esc(rp.get("_generated", "")), f"<a href='/raw/file?path=reuse/para/synthetic/recall_paraphrase_{_esc(tag)}.json'>recall</a>"])
    if not rows:
        return "<div class='empty'>No result files on this server yet.</div>"
    return _table(["stage", "story set", "settings", "generated", "file"], rows)


DICTIONARY = [
    ("data/pilot_stories.jsonl — one line per record (r00)", [
        ("story_id", "record id, <issue>_a<number>"), ("issue", "issue id"), ("magazine, cover_date, genre, format", "from config/pilot_issues.json"),
        ("type", "story · serial_part · feature · poem · letters · ad · other (machine typing, human-correctable)"),
        ("title, author", "as printed, as they stood at export (human corrections included)"),
        ("pages", "page numbers the record spans"), ("status", "auto · modified · verified"),
        ("verified_by, modified_by", "annotator usernames"), ("fragments", "scan region keys page:region, in reading order"),
        ("n_words, text_sha1, text", "word count, checksum, reading text")]),
    ("data/reuse/<set>_k<k>_matches.jsonl — one exact match (r02)", [
        ("a, b", "story ids (a sorts before b)"), ("len", "length in words"), ("a_tok, b_tok", "token intervals [start, end)"),
        ("a_char, b_char", "character intervals into the canonical text"), ("a_issue, b_issue", "issue ids"),
        ("excerpt", "the passage (up to 300 characters)"), ("cause, shared_regions", "same-issue file only: shared-region duplicate or same-issue repeat")]),
    ("data/reuse/<set>_k<k>_clusters.json — one cluster (r02)", [
        ("witnesses, witnesses_collapsed", "distinct stories; distinct region families"), ("issues, occurrences, max_len", "counts"),
        ("representative", "longest occurrence: story_id, tok, text"), ("members", "story_id, issue, tok, len, text")]),
    ("data/reuse/para/<tag>_k<K>_alignments.jsonl — one alignment (r04)", [
        ("a, b, a_tok, b_tok, a_char, b_char, a_issue, b_issue", "as above"),
        ("cols, matches, identity, score", "alignment columns, matching columns, matches/cols, alignment score"),
        ("sources, best_rank, max_cosine", "how the candidate was found: exact seed and/or embedding neighbour (rank, cosine)"),
        ("text_a, text_b", "both texts of the aligned span")]),
    ("data/reuse/background/pairs_<set>.csv.gz — one story pair (r05)", [
        ("a, b, issue_a, issue_b, same_issue, shared_regions, same_family", "identities and flags"),
        ("tokens_a, tokens_b", "story lengths in tokens"),
        ("magazine_*, publisher_*, same_magazine, same_publisher, same_genre, same_format", "venue facts"),
        ("author_a, author_b, author_known, same_author", "printed by-lines; both usable; same normalized name"),
        ("year_a, year_b, later_year, earlier_year, years_apart, years_band, later_decade", "the two time variables"),
        ("topic_tfidf, topic_emb, topic_q", "topic similarity (masked TF-IDF cosine; embedding cosine); quartile 1–4"),
        ("exact_k<k>_longest / _n / _cover_a / _cover_b / _share_max, exact_excerpt", "exact reuse per seed"),
        ("para_k<K>_longest / _n / _best_identity / _cover_a / _cover_b / _share_max, para_excerpt", "paraphrase reuse per K")]),
    ("data/reuse/background/summary_<set>.json (r05)", [
        ("background", "P(longest ≥ L) curves overall and by stratum; time_table by later decade × years band"),
        ("unusual", "matched pairs ranked by P(at least this long) within their stratum"),
        ("sampler_check", "weighted-sample errors against the full table"), ("models", "two-part model fits: fixed effects, story effect sd")]),
    ("data/annotations/<issue>.jsonl — one human action (site)", [
        ("ts, user, issue, article_id, action", "when, who, where, what"),
        ("frag, role, to_id, into_id, order, text, …", "action details; replayed in order over the machine output")]),
    ("data/raw/<issue>/meta.json — the Internet Archive item record", [
        ("metadata", "identifier, title, date, uploader, addeddate, scanner, collection, ocr, imagecount, …"),
        ("files", "every file of the item with format and size"), ("item_size, item_last_updated", "as reported by the archive")]),
]


def data_dictionary_html():
    out = []
    for title, fields in DICTIONARY:
        out.append(f"<h3 style='font-weight:normal;font-size:16px'>{_esc(title)}</h3>")
        out.append(_table(["field", "meaning"], [[_esc(f), _esc(m)] for f, m in fields]))
    return "".join(out)


def files_html():
    D = _G["DATA"]
    rows = []
    for root, _dirs, fs in os.walk(os.path.join(D, "reuse")):
        for f in sorted(fs):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, D)
            if f.endswith(".npz"):
                continue
            rows.append([f"<a href='/raw/file?path={urllib.parse.quote(rel)}'>{_esc(rel)}</a>", N(os.path.getsize(p)),
                         _esc(time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))))])
    for rel in ("pilot_stories.jsonl", "pilot_stories.jsonl.gz", "metrics.json", "timings.jsonl"):
        p = os.path.join(D, rel)
        if os.path.exists(p):
            rows.append([f"<a href='/raw/file?path={rel}'>{_esc(rel)}</a>", N(os.path.getsize(p)),
                         _esc(time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))))])
    if not rows:
        return "<div class='empty'>None yet.</div>"
    return (_table(["file (click to download)", "#bytes", "written"], rows)
            + "<p class='muted'>Scans, layout records, text stages, article assemblies, and annotation logs are "
              "reached through the issue pages, the workbench, and the raw links on entity pages; automated "
              "readers use the data door (see the handbook).</p>")
