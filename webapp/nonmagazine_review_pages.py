"""The review of the non-magazine candidates on the site (v0.25.0; decision 1 of 19 October 2026, Sujin Kang).

  /review/nonmagazine   the candidate table scripts/nonmagazine_candidates.py wrote (data/corpus/p52-2026-10-16/
                        nonmagazine_candidates.json), one row per item proposed EXCLUDE or LOOK (and the verification
                        sample's issues), with the cover thumbnail, the archive's title, the signals and their evidence;
                        a reader marks each item EXCLUDE (not a magazine issue) or KEEP by a click, with an optional
                        note, or marks every unmarked row of the view at once. Each click is one line in
                        data/review/nonmagazine_review.jsonl (time, reader, id, decision, note); the latest line per
                        item counts. scripts/set_aside_nonmagazine.py --decisions reads that log.

Bound to the site's helpers with bind(globals()) from app.py, like the other page modules.
"""
import html
import json
import os
import sys
import time
import urllib.parse
from collections import Counter, defaultdict

_G = {}
_CACHE = {"table": (None, None), "log": (None, None)}
DECISIONS = {"x": "exclude", "k": "keep", "?": "not sure"}


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)


def _esc(s):
    return _G["esc"](s)


def table_path():
    return os.path.join(_G["DATA"], "corpus", "p52-2026-10-16", "nonmagazine_candidates.json")


def log_path():
    return os.path.join(_G["DATA"], "review", "nonmagazine_review.jsonl")


def _cached(name, path, loader):
    try:
        st = os.stat(path)
        sig = (st.st_mtime, st.st_size)
    except OSError:
        return None
    if _CACHE[name][0] != sig:
        try:
            _CACHE[name] = (sig, loader(path))
        except Exception:
            return None
    return _CACHE[name][1]


def table():
    return _cached("table", table_path(), lambda p: json.load(open(p, encoding="utf-8")))


def _load_log(p):
    rows = []
    for line in open(p, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def decisions():
    """id -> the latest decision line."""
    rows = _cached("log", log_path(), _load_log) or []
    out = {}
    for r in rows:
        if r.get("id") and r.get("decision") in DECISIONS:
            out[r["id"]] = r
    return out


def listed(T):
    """The rows the review shows: every item proposed EXCLUDE or LOOK, and every sample issue."""
    return [it for it in T.get("items", []) if it.get("proposed") != "KEEP" or it.get("in_sample")]


def append_decision(user, iid, decision, note=""):
    if decision not in DECISIONS or not iid:
        return False
    os.makedirs(os.path.dirname(log_path()), exist_ok=True)
    with open(log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": user, "id": iid, "decision": decision,
                            "note": (note or "")[:500]}, ensure_ascii=False) + "\n")
    _CACHE["log"] = (None, None)
    return True


def handle_post(user, form):
    """One click: a decision on one item, or on every unmarked item of a view (ids listed in the form). Returns the
    query string to return to."""
    back = form("back") or ""
    if form("bulk") in DECISIONS:
        ids = [i for i in (form("ids") or "").split() if i]
        done = decisions()
        n = 0
        for iid in ids:
            if iid not in done and append_decision(user, iid, form("bulk"), "marked with the view"):
                n += 1
        return back
    append_decision(user, form("id"), form("decision"), form("note"))
    return back


CSS = """
<style>
.nm table{font-size:12.5px}
.nm td{vertical-align:top}
.nm img.cov{width:54px;height:auto;border:1px solid var(--grid);border-radius:3px;background:#fff}
.nm .sig{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--ink2)}
.nm .ev{font-size:11.5px;color:var(--ink2);max-width:360px}
.nm tr.dx{background:rgba(214,39,40,.07)}
.nm tr.dk{background:var(--okbg)}
.nm tr.dq{background:var(--warnbg)}
.nm form.d{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.nm form.d button{font-size:11.5px;padding:3px 8px;border:1px solid var(--grid2);border-radius:6px;background:var(--surface);cursor:pointer;font-family:inherit}
.nm form.d button.x{background:rgba(214,39,40,.10)}
.nm form.d button.k{background:var(--okbg)}
.nm form.d input.n{width:120px;font-size:11px;font-family:inherit}
.nm .bulk{border:1px solid var(--grid);background:var(--surface);border-radius:10px;padding:8px 12px;margin:8px 0 12px;font-size:13px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.nm .bulk button{font-size:12.5px;padding:5px 10px;border:1px solid var(--grid2);border-radius:8px;cursor:pointer;font-family:inherit;background:var(--surface2)}
.nm .cls{font-size:12px}
</style>"""


def review_page(qs, user, render=None):
    T = table()
    head = [CSS, _G["howto"](
        "Decision 1 of 19 October 2026: the corpus is pulp magazines. A rule scored every selected issue for the signs of a "
        "standalone book, a single story, a dime-novel or nickel-weekly number, a comic, an index or a portfolio, a fanzine or an "
        "item from after the period, or a magazine that is not a fiction magazine (scripts/nonmagazine_candidates.py; the codes in "
        "the SIGNALS column are explained when you hover over them). The rule proposes EXCLUDE at 5 points and LOOK at 3; the "
        "decision is yours, item by item: EXCLUDE means the item leaves corpus 1.0 (its files stay on disk; it leaves the site and "
        "the reuse stages; the datasheet lists it under its reason), KEEP means it stays. Look at the cover, the archive's title and "
        "the evidence; open the issue when in doubt. Filter the view by proposal and class, then mark the exceptions and use the "
        "button to mark the rest of the view at once. Every click is saved under your name; clicking again changes the decision. "
        "The verification sample's issues are listed whatever their score. When you are done, the apply step (PASTE P52u) writes "
        "the set-aside list and redraws the sample's slots."),
        "<h1>Items that may not be magazine issues</h1>"]
    if not T:
        return _render(render, "Non-magazine review", head + ["<div class='empty'>No candidate table yet (scripts/nonmagazine_candidates.py writes data/corpus/p52-2026-10-16/nonmagazine_candidates.json).</div>"])
    rows_all = listed(T)
    D = decisions()
    can = bool(user) and user != "guest"
    prop = (qs.get("proposed") or ["EXCLUDE"])[0]
    cls = (qs.get("class") or [""])[0]
    only = (qs.get("only") or ["all"])[0]          # all | undecided | decided | sample
    rows = [it for it in rows_all if (prop == "all" or it["proposed"] == prop) and (not cls or (it.get("class") or "") == cls)]
    if only == "undecided":
        rows = [it for it in rows if it["id"] not in D]
    elif only == "decided":
        rows = [it for it in rows if it["id"] in D]
    elif only == "sample":
        rows = [it for it in rows if it.get("in_sample")]
    back = "/review/nonmagazine?" + urllib.parse.urlencode({"proposed": prop, "class": cls, "only": only})
    n_dec = sum(1 for it in rows_all if it["id"] in D)
    dc = Counter(D[it["id"]]["decision"] for it in rows_all if it["id"] in D)
    head.append(f"<p class='muted'>{len(rows_all):,} items listed of {T.get('selected', 0):,} selected issues (rule {_esc(T.get('rule_version', ''))}, "
                f"written {_esc((T.get('written') or '')[:16].replace('T', ' '))}); proposed " + ", ".join(f"{k} {v}" for k, v in sorted((T.get("counts") or {}).get("proposed", {}).items()))
                + f". <b>Decided: {n_dec:,} of {len(rows_all):,}</b> — exclude {dc.get('x', 0)}, keep {dc.get('k', 0)}, not sure {dc.get('?', 0)}; "
                + ", ".join(f"{_esc(_G['display_name'](u))} {n}" for u, n in Counter(d['user'] for d in D.values()).most_common(3)) + ".</p>")
    classes = sorted({it.get("class") or "" for it in rows_all if it.get("class")})

    def link(label, **kw):
        p = {"proposed": prop, "class": cls, "only": only}
        p.update(kw)
        cur = all(p[k] == {"proposed": prop, "class": cls, "only": only}[k] for k in p)
        return f"<b>{_esc(label)}</b>" if cur else f"<a href='/review/nonmagazine?{urllib.parse.urlencode(p)}'>{_esc(label)}</a>"
    head.append("<p class='fine'>Proposed: " + " · ".join(link(l, proposed=v) for v, l in (("EXCLUDE", "exclude"), ("LOOK", "look"), ("KEEP", "keep (the sample's)"), ("all", "all")))
                + " &nbsp;|&nbsp; Class: " + " · ".join([link("any", **{"class": ""})] + [link(c, **{"class": c}) for c in classes])
                + " &nbsp;|&nbsp; Show: " + " · ".join(link(l, only=v) for v, l in (("all", "all"), ("undecided", "undecided"), ("decided", "decided"), ("sample", "in the sample"))) + "</p>")
    undecided_ids = [it["id"] for it in rows if it["id"] not in D]
    if can and rows:
        head.append("<form method='POST' action='/review/nonmagazine' class='bulk'><input type='hidden' name='back' value='" + html.escape(back, quote=True) + "'>"
                    f"<input type='hidden' name='ids' value='{' '.join(undecided_ids)}'>"
                    f"<span>{len(rows)} rows in this view, {len(undecided_ids)} undecided — mark the undecided ones:</span>"
                    "<button name='bulk' value='x'>all EXCLUDE</button><button name='bulk' value='k'>all KEEP</button></form>")
    key = T.get("signal_key") or {}
    body_rows = []
    for it in rows:
        d = D.get(it["id"])
        klass = {"x": "dx", "k": "dk", "?": "dq"}.get(d["decision"]) if d else ""
        sig = " ".join(f"<span title='{html.escape((key.get(c) or {}).get('says', ''), quote=True)}'>{_esc(c)}</span>" for c in it.get("signals", []))
        ev = "; ".join(f"{_esc(k)}: {_esc(str(v))}" for k, v in (it.get("evidence") or {}).items())
        name = (f"<a href='/issue/{_esc(it['id'])}'>{_esc(it.get('magazine') or it['id'])}</a>"
                + (f"<br><span class='fine'>{_esc(it.get('ia_title'))}</span>" if it.get("ia_title") and it.get("ia_title") != it.get("magazine") else "")
                + (f"<br><span class='fine'>sample slot #{it['sample_order']}</span>" if it.get("in_sample") else "")
                + (f"<br><a class='fine' href='https://archive.org/details/{_esc(it['ia_identifier'])}' target='_blank'>archive.org</a>" if it.get("ia_identifier") else ""))
        if can:
            act = (f"<form method='POST' action='/review/nonmagazine' class='d'><input type='hidden' name='id' value='{_esc(it['id'])}'>"
                   f"<input type='hidden' name='back' value='{html.escape(back, quote=True)}'>"
                   "<button class='x' name='decision' value='x'>EXCLUDE</button><button class='k' name='decision' value='k'>KEEP</button>"
                   "<button name='decision' value='?'>?</button>"
                   f"<input class='n' name='note' placeholder='note' value='{html.escape((d or {}).get('note') or '', quote=True)}'></form>")
        else:
            act = "<span class='muted'>log in to decide</span>"
        state = (f"<span class='chip {'stV' if d['decision'] == 'k' else ''}'>{_esc(DECISIONS[d['decision']])}</span><br><span class='fine'>{_esc(_G['display_name'](d['user']))} {_esc(d['ts'][5:16].replace('T', ' '))}</span>"
                 + (f"<br><span class='fine'>“{_esc(d['note'])}”</span>" if d.get("note") and d["note"] != "marked with the view" else "")) if d else "<span class='muted'>—</span>"
        body_rows.append(f"<tr class='{klass}'><td><a href='/issue/{_esc(it['id'])}'><img class='cov' src='/thumb/{_esc(it['id'])}/page_0001.jpg' alt=''></a></td>"
                         f"<td>{name}</td><td>{_esc(it.get('cover_date') or '')}</td><td class='num'>{it.get('pages') or ''}</td>"
                         f"<td class='cls'>{_esc(it.get('proposed'))}<br>{_esc(it.get('class') or '')}<br>{it.get('score')}</td>"
                         f"<td class='sig'>{sig}</td><td class='ev'>{ev}</td><td>{state}</td><td>{act}</td></tr>")
    body = head + ["<div class='nm'><div style='overflow-x:auto'><table><tr><th>cover</th><th>item (selection name · archive title)</th><th>date</th><th>pages</th>"
                   "<th>proposal · class · score</th><th>signals</th><th>evidence</th><th>decision</th><th>decide</th></tr>" + "".join(body_rows) + "</table></div></div>"]
    if not rows:
        body.append("<div class='empty'>Nothing in this view.</div>")
    return _render(render, "Non-magazine review", body)


def _render(render, title, body):
    page = "".join(body)
    return render(title, page, "/review/nonmagazine") if render else page
