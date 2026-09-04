"""Review pages for the pilot website (v0.14.0): the two hand-review tools the
protocol asks for beside the machine's results.

  /reuse/validate     the paraphrase review set (protocol 3.2, "Parameter
                      selection and validation"): a reader sees two passages
                      the detector paired and says paraphrase-or-copy / not /
                      unsure, with a note; each judgment is one line of an
                      append-only log (data/reuse/validation/judgments.jsonl:
                      time, reader, set id, item, judgment, note). The page
                      shows the calibration of every setting in the fixed
                      grid against the judgments (pipeline/r06_validation.py),
                      the readers' agreement, and the review set's design.
  /reuse/cases        the cases for the literary genealogies (protocol 4.2):
                      a cluster or a story pair marked as a case with a note,
                      in an append-only log (data/reuse/cases.jsonl); the page
                      lists them with links to witnesses, pair and scans.

Bound to the site's helpers with bind(globals()) from app.py, like the
other page modules. Reading is open to every member; judging and marking
need a named account (the log carries the name).
"""
import difflib
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict

_G = {}


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)


def _esc(s):
    return _G["esc"](s)


def _howto(t):
    return _G["howto"](t)


def _r6():
    import r06_validation as r6
    return r6


def _vdir():
    return os.path.join(_G["DATA"], "reuse", "validation")


def _render(render, title, body, path):
    html = "".join(body) if isinstance(body, (list, tuple)) else body
    return render(title, html, path) if render else html


# ---------------------------------------------------------------- the review set

_CACHE = {}


def _items():
    r6 = _r6()
    p = os.path.join(_vdir(), "review_set.jsonl")
    if not os.path.exists(p):
        return []
    mt = os.path.getmtime(p)
    if _CACHE.get("items_mt") != mt:
        _CACHE["items"] = r6.load_review_set(p)
        _CACHE["items_mt"] = mt
    return _CACHE["items"]


def _stats():
    p = os.path.join(_vdir(), "review_set_stats.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _judgments(set_id):
    r6 = _r6()
    return r6.load_judgments(os.path.join(_vdir(), "judgments.jsonl"), set_id=set_id)


def append_judgment(user, set_id, item, judgment, note):
    r6 = _r6()
    if judgment not in r6.JUDGMENTS_ALLOWED:
        return False
    os.makedirs(_vdir(), exist_ok=True)
    with open(os.path.join(_vdir(), "judgments.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": user, "set_id": set_id,
                            "item": item, "judgment": judgment, "note": (note or "")[:2000]}, ensure_ascii=False) + "\n")
    return True


def _shared_runs(ta, tb, min_run=2):
    """Word-level shared runs between two passages, for highlighting: returns
    (html_a, html_b) with runs of min_run+ shared words marked."""
    wa, wb = ta.split(), tb.split()
    sm = difflib.SequenceMatcher(None, [w.lower().strip(".,;:!?\"'()") for w in wa],
                                 [w.lower().strip(".,;:!?\"'()") for w in wb], autojunk=False)
    ma, mb = [False] * len(wa), [False] * len(wb)
    for blk in sm.get_matching_blocks():
        if blk.size >= min_run:
            for i in range(blk.a, blk.a + blk.size):
                ma[i] = True
            for j in range(blk.b, blk.b + blk.size):
                mb[j] = True

    def render(words, marks):
        out, cur = [], None
        for w, m in zip(words, marks):
            if m != cur:
                if cur is not None:
                    out.append("</mark>" if cur else "")
                out.append("<mark>" if m else "")
                cur = m
            out.append(_esc(w) + " ")
        if cur:
            out.append("</mark>")
        return "".join(out)
    return render(wa, ma), render(wb, mb)


def _story_head(meta, sid):
    t = meta.get("title") or "(untitled)"
    au = meta.get("author")
    au = _G["EX"].display_author(au) if au else ""
    iss = meta.get("issue") or ""
    return (f"<div class='vhead'><a href='/story/{_esc(sid)}'>{_esc(t)}</a>"
            + (f" <span class='muted'>by {_esc(au)}</span>" if au else " <span class='muted'>(no by-line)</span>")
            + f"<br><span class='muted'>{_esc(meta.get('magazine') or '')} · {_esc(meta.get('cover_date') or '')} · "
            f"<a href='/issue/{_esc(iss)}'>{_esc(iss)}</a></span></div>")


CSS = """
<style>
.vwrap{display:flex;gap:16px;flex-wrap:wrap;margin:10px 0}
.vcol{flex:1 1 380px;background:var(--surface);border:1px solid var(--grid);padding:10px 14px}
.vhead{margin-bottom:8px;font-size:14px}
.vtext{font-family:Georgia,serif;font-size:15px;line-height:1.5}
.vtext mark{background:#fff1a8}
.vbtns{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}
.vbtns button{font-size:14px;padding:6px 14px;border:1px solid var(--grid2);background:var(--surface);cursor:pointer;font-family:inherit}
.vbtns button.p{background:var(--okbg)}.vbtns button.n{background:var(--warnbg)}.vbtns button.u{background:var(--surface2)}
.vnote{width:100%;max-width:640px;height:56px;font-family:inherit;font-size:13px}
.vprog{font-size:13px;color:var(--ink2);margin:6px 0 10px}
.vgrid td.chosen{background:var(--okbg);font-weight:bold}
</style>"""


def validate_page(qs, user, render=None):
    items = _items()
    stats = _stats()
    view = (qs.get("view") or [""])[0]
    if not items:
        body = [_howto("The review set does not exist on this server yet: run "
                       "<code>python3 pipeline/r06_validation.py --build</code> after the paraphrase stage."),
                "<h1>Paraphrase review</h1><div class='empty'>No review set.</div>"]
        return _render(render, "Paraphrase review", body, "/reuse/validate")
    set_id = items[0].get("set_id")
    js = _judgments(set_id)
    r6 = _r6()
    by_item = r6.latest_by_user(js)
    mine = {i: d[user] for i, d in by_item.items() if user in d} if user else {}
    readers = Counter(u for d in by_item.values() for u in d)
    head = [CSS, _howto(
        "Protocol 3.2, parameter selection and validation. Each item shows two passages from stories of different "
        "issues that the paraphrase detector paired — some it keeps under its default rule, some only a looser rule "
        "would keep, some it found close in meaning although the words differ. Read both and say whether the second "
        "is a rewritten or copied version of the first (or both share a rewritten passage): PARAPHRASE OR COPY. "
        "Two stories that merely treat the same subject, or share stock phrasing, are NOT A PARAPHRASE. UNSURE is "
        "allowed; a note helps the other reader. You do not see the machine's numbers until you have judged — the "
        "calibration below shows how every setting fares against the readers. Keys: 1 paraphrase, 2 not, 3 unsure."),
        "<h1>Paraphrase review</h1>",
        f"<p class='muted'>Review set {_esc(set_id)} · {len(items)} items · "
        + " · ".join(f"<a href='/reuse/validate?view={v}'>{lab}</a>" for v, lab in
                     (("", "review"), ("mine", "my judgments"), ("calibration", "calibration"), ("design", "the set's design")))
        + "</p>",
        f"<div class='vprog'>Judged: " + (", ".join(f"{_esc(_G['display_name'](u))} {n}" for u, n in readers.most_common()) or "nobody yet")
        + f" · items with two readers: {sum(1 for d in by_item.values() if len(d) >= 2)}"
        + (f" · you: {len(mine)} of {len(items)}" if user and user != "guest" else "") + "</div>"]
    if view == "calibration":
        return _render(render, "Paraphrase review — calibration", head + calibration_html(items, js), "/reuse/validate")
    if view == "design":
        return _render(render, "Paraphrase review — design", head + design_html(stats), "/reuse/validate")
    if view == "mine":
        return _render(render, "Paraphrase review — my judgments", head + mine_html(items, mine, by_item), "/reuse/validate")
    # the item to show: ?item=, else the first unjudged (after ?after=)
    want = (qs.get("item") or [""])[0]
    after = (qs.get("after") or [""])[0]
    item = None
    if want:
        item = next((it for it in items if it["id"] == want), None)
    else:
        ids = [it["id"] for it in items]
        start = ids.index(after) + 1 if after in ids else 0
        for it in items[start:] + items[:start]:
            if it["id"] not in mine:
                item = it
                break
    if item is None:
        body = head + ["<div class='empty'>You have judged every item. Thank you — the calibration shows the result."
                       " <a href='/reuse/validate?view=calibration'>Calibration</a></div>"]
        return _render(render, "Paraphrase review", body, "/reuse/validate")
    body = head + item_html(item, user, mine.get(item["id"]), by_item.get(item["id"], {}))
    return _render(render, f"Paraphrase review — {item['id']}", body, "/reuse/validate")


def item_html(it, user, my_judgment, others):
    ta, tb = it["window_a"], it["window_b"]
    ha, hb = _shared_runs(ta, tb)
    can = bool(user) and user != "guest"
    out = [f"<h2>Item {_esc(it['id'])}</h2>",
           "<div class='vwrap'>"
           f"<div class='vcol'>{_story_head(it['a_meta'], it['a'])}<div class='vtext'>{ha}</div></div>"
           f"<div class='vcol'>{_story_head(it['b_meta'], it['b'])}<div class='vtext'>{hb}</div></div></div>",
           "<p class='muted'>Shared runs of two or more words are marked; the passages are the detector's candidate "
           "windows with 25 words of context on each side, as the stories read now.</p>"]
    if can:
        out.append(
            f"<form method='POST' action='/reuse/validate' id='vform'>"
            f"<input type='hidden' name='item' value='{_esc(it['id'])}'><input type='hidden' name='set_id' value='{_esc(it['set_id'])}'>"
            "<div class='vbtns'>"
            "<button class='p' name='judgment' value='paraphrase' accesskey='1'>1 · Paraphrase or copy</button>"
            "<button class='n' name='judgment' value='not' accesskey='2'>2 · Not a paraphrase</button>"
            "<button class='u' name='judgment' value='unsure' accesskey='3'>3 · Unsure</button>"
            f"<a class='muted' href='/reuse/validate?after={_esc(it['id'])}'>skip for now</a></div>"
            "<textarea class='vnote' name='note' placeholder='A note for the other reader (optional): what decided it?'></textarea>"
            "</form>"
            "<script>document.addEventListener('keydown',function(e){if(e.target.tagName==='TEXTAREA')return;"
            "var m={'1':'paraphrase','2':'not','3':'unsure'}[e.key];if(!m)return;"
            "var b=document.querySelector('#vform button[value=\"'+m+'\"]');if(b)b.click();});</script>")
    else:
        out.append("<p class='muted'>Judging needs a named account; guests can read the set and the calibration.</p>")
    if my_judgment:
        al = it.get("aligned")
        out.append(f"<p>Your judgment: <b>{_esc(my_judgment['judgment'])}</b> at {_esc(my_judgment['ts'])}"
                   + (f" — “{_esc(my_judgment['note'])}”" if my_judgment.get("note") else "") + ". "
                   "The machine's reading (shown only after you have judged): "
                   f"source {_esc(it['source'])}, rank {it['rank']}, cosine {it['cosine'] if it['cosine'] is not None else '—'}, "
                   + (f"alignment {al['cols']} columns at identity {al['identity']}" if al else "no alignment reaches the loose band")
                   + f"; stratum {_esc(it['stratum']['source'])} / {_esc(it['stratum']['score_band'])}"
                   + (f" / {_esc(it['stratum']['cosine_band'])}" if it['stratum'].get('cosine_band') else "") + ".</p>")
        if others:
            out.append("<p class='muted'>Other readers: " + ", ".join(
                f"{_esc(_G['display_name'](u))} — {_esc(j['judgment'])}" + (f" (“{_esc(j['note'])}”)" if j.get("note") else "")
                for u, j in others.items() if u != user) + "</p>")
    return out


def mine_html(items, mine, by_item):
    rows = []
    for it in items:
        j = mine.get(it["id"])
        al = it.get("aligned")
        others = ", ".join(f"{_esc(_G['display_name'](u))}: {_esc(x['judgment'])}" for u, x in by_item.get(it["id"], {}).items() if x is not j)
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in (
            f"<a href='/reuse/validate?item={_esc(it['id'])}'>{_esc(it['id'])}</a>",
            _esc(j["judgment"]) if j else "<span class='muted'>—</span>",
            _esc(j.get("note") or "") if j else "",
            others,
            _esc(it["source"]), str(it["rank"]) if it["source"] == "embedding" else "—",
            f"{al['cols']} / {al['identity']}" if al else "—",
            _esc(it["stratum"]["score_band"]))) + "</tr>")
    return ["<h2>My judgments</h2>",
            "<table><tr><th>Item</th><th>Judgment</th><th>Note</th><th>Others</th><th>Source</th><th>Rank</th>"
            "<th>Columns / identity</th><th>Score band</th></tr>" + "".join(rows) + "</table>"]


def calibration_html(items, js):
    r6 = _r6()
    cal = r6.calibrate(items, js, write=False)
    if not cal:
        return ["<div class='empty'>No calibration yet.</div>"]
    out = ["<h2>Calibration of the settings grid</h2>",
           "<p class='muted'>Each setting keeps an alignment when its rank is within K (embedding candidates), its length reaches "
           "the minimum columns and its identity the minimum. Precision = kept items the readers called paraphrase; recall = "
           "paraphrase items the setting keeps; both weighted by the strata's inclusion probabilities (raw counts beside). "
           f"Chosen: the highest weighted recall at precision ≥ {cal['precision_floor']}, ties to the smaller pool. "
           f"Judged {cal['judged_items']} of {cal['items']} items; decided (readers agree, unsure not counted) {cal['decided_items']}; "
           + ", ".join(f"{_esc(k)} {v}" for k, v in sorted(cal["labels"].items())) + ".</p>"]
    ag = cal.get("agreement")
    if ag:
        out.append(f"<p>Agreement between {_esc(_G['display_name'](ag['readers'][0]))} and {_esc(_G['display_name'](ag['readers'][1]))}: "
                   f"{ag['agree']} of {ag['items_both_decided']} decided items ({ag['items_both_judged']} judged by both), "
                   f"Cohen's kappa {ag['kappa'] if ag['kappa'] is not None else '—'}.</p>")
    ch = cal.get("chosen")
    rows = []
    for g in cal["grid"]:
        chosen = ch and (g["k"], g["min_cols"], g["min_identity"]) == (ch["k"], ch["min_cols"], ch["min_identity"])
        cls = " class='chosen'" if chosen else ""
        rows.append("<tr>" + "".join(f"<td{cls}>{c}</td>" for c in (
            g["k"], g["min_cols"], g["min_identity"], g["kept"], g["true_positives"],
            "—" if g["precision"] is None else g["precision"], "—" if g["recall"] is None else g["recall"],
            "—" if g["precision_weighted"] is None else g["precision_weighted"],
            "—" if g["recall_weighted"] is None else g["recall_weighted"], g["pool_weighted"])) + "</tr>")
    out.append("<table class='vgrid'><tr><th>K</th><th>Min columns</th><th>Min identity</th><th>Kept</th><th>True</th>"
               "<th>Precision</th><th>Recall</th><th>Precision (weighted)</th><th>Recall (weighted)</th><th>Pool (weighted)</th></tr>"
               + "".join(rows) + "</table>")
    out.append(f"<p><b>Chosen setting:</b> " + (f"K = {ch['k']}, {ch['min_cols']} columns, identity {ch['min_identity']} "
               f"(weighted precision {ch['precision_weighted']}, recall {ch['recall_weighted']})." if ch
               else "none reaches the precision floor yet — judge more items, or the floor decides the default stays.") + "</p>")
    return out


def design_html(stats):
    if not stats:
        return ["<div class='empty'>No design file.</div>"]
    rows = "".join("<tr>" + "".join(f"<td>{_esc(str(c))}</td>" for c in (
        s["source"], s["score_band"], s.get("cosine_band") or "—", s["candidates"], s["drawn"], s["weight"])) + "</tr>"
        for s in stats.get("strata", []))
    return ["<h2>The review set's design</h2>",
            f"<p class='muted'>Set {_esc(stats.get('set_id'))}, generated {_esc(stats.get('generated'))}: {stats.get('stories')} stories, "
            f"{stats.get('passages')} passages, K = {stats.get('k_retrieve')} neighbours retrieved, {stats.get('candidates_cross_issue')} cross-issue "
            f"candidate regions; {stats.get('per_stratum')} drawn per stratum with seed {stats.get('seed')}; cosine cut-points "
            f"{_esc(json.dumps(stats.get('cosine_cuts')))}. Score bands: below any rule (&lt; 9), loose rule only (9–15), default rule (16–29), "
            f"strong (30+). Grid: K {_esc(str(stats.get('grid', {}).get('k')))} × rules {_esc(str(stats.get('grid', {}).get('rules')))}; "
            f"precision floor {stats.get('precision_floor')}.</p>",
            "<table><tr><th>Source</th><th>Score band</th><th>Cosine band</th><th>Candidates</th><th>Drawn</th><th>Weight</th></tr>"
            + rows + "</table>"]


# ---------------------------------------------------------------- cases (protocol 4.2)

def _cases_path():
    return os.path.join(_G["DATA"], "reuse", "cases.jsonl")


def load_cases():
    """Replay the case log: {case_id: {...}} in creation order."""
    cases = {}
    p = _cases_path()
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            a = e.get("action")
            if a == "add":
                cases[e["case_id"]] = {"case_id": e["case_id"], "kind": e.get("kind"), "ref": e.get("ref"),
                                       "title": e.get("title") or "", "by": e["user"], "ts": e["ts"], "open": True,
                                       "notes": [{"ts": e["ts"], "user": e["user"], "text": e.get("note") or ""}]}
            elif a == "note" and e.get("case_id") in cases:
                cases[e["case_id"]]["notes"].append({"ts": e["ts"], "user": e["user"], "text": e.get("note") or ""})
            elif a == "close" and e.get("case_id") in cases:
                cases[e["case_id"]]["open"] = False
                cases[e["case_id"]]["notes"].append({"ts": e["ts"], "user": e["user"], "text": "closed" + (": " + e["note"] if e.get("note") else "")})
            elif a == "reopen" and e.get("case_id") in cases:
                cases[e["case_id"]]["open"] = True
                cases[e["case_id"]]["notes"].append({"ts": e["ts"], "user": e["user"], "text": "reopened"})
    return cases


def append_case(user, action, payload):
    os.makedirs(os.path.dirname(_cases_path()), exist_ok=True)
    with open(_cases_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": user, "action": action, **payload},
                           ensure_ascii=False) + "\n")


def new_case_id():
    cases = load_cases()
    n = 1
    while f"c{n:03d}" in cases:
        n += 1
    return f"c{n:03d}"


def ref_link(kind, ref):
    if kind == "cluster":
        return (f"/reuse/cluster/{urllib.parse.quote(str(ref.get('set')))}/{urllib.parse.quote(str(ref.get('kind')))}/"
                f"{int(ref.get('k', 0))}/{int(ref.get('idx', 0))}")
    if kind == "pair":
        return f"/pair/{urllib.parse.quote(str(ref.get('a')))}/{urllib.parse.quote(str(ref.get('b')))}"
    return "/reuse/cases"


def case_form_html(kind, ref, user, back, title=""):
    """The 'mark as a case' form for a cluster or pair page (named accounts only)."""
    if not user or user == "guest":
        return ""
    existing = [c for c in load_cases().values() if c["kind"] == kind and c["ref"] == ref]
    if existing:
        c = existing[0]
        return (f"<p class='muted'>This is <a href='/reuse/cases#{_esc(c['case_id'])}'>case {_esc(c['case_id'])}</a>"
                f"{' (closed)' if not c['open'] else ''}: {_esc(c['title'])}.</p>")
    return (f"<form method='POST' action='/reuse/case' style='margin:8px 0'>"
            f"<input type='hidden' name='action' value='add'><input type='hidden' name='kind' value='{_esc(kind)}'>"
            f"<input type='hidden' name='ref' value='{_esc(json.dumps(ref))}'><input type='hidden' name='back' value='{_esc(back)}'>"
            f"<input type='text' name='title' placeholder='name this case' value='{_esc(title[:80])}' style='width:260px'> "
            "<input type='text' name='note' placeholder='why it is a case (optional)' style='width:320px'> "
            "<button>Mark as a case</button> <span class='muted'>protocol 4.2: extensive, unexpected or historically suggestive</span></form>")


def cases_page(qs, user, render=None):
    cases = load_cases()
    show_closed = (qs.get("closed") or [""])[0] == "1"
    rows = []
    for c in cases.values():
        if not c["open"] and not show_closed:
            continue
        link = ref_link(c["kind"], c["ref"])
        ref = c["ref"] or {}
        what = (f"cluster {ref.get('set')} · {ref.get('kind')} · seed/K {ref.get('k')} · #{ref.get('idx')}" if c["kind"] == "cluster"
                else f"pair {ref.get('a')} ~ {ref.get('b')}")
        notes = "".join(f"<div class='muted'>{_esc(n['ts'][:16])} {_esc(_G['display_name'](n['user']))}: {_esc(n['text'])}</div>"
                        for n in c["notes"] if n["text"])
        form = ""
        if user and user != "guest":
            form = (f"<form method='POST' action='/reuse/case' style='margin-top:6px'><input type='hidden' name='case_id' value='{_esc(c['case_id'])}'>"
                    "<input type='text' name='note' placeholder='add a note' style='width:320px'> "
                    "<button name='action' value='note'>Add note</button> "
                    + ("<button name='action' value='close'>Close</button>" if c["open"] else "<button name='action' value='reopen'>Reopen</button>")
                    + "</form>")
        rows.append(f"<div class='card' id='{_esc(c['case_id'])}' style='padding:10px 14px;margin:8px 0;background:var(--surface);border:1px solid var(--grid)'>"
                    f"<b>{_esc(c['case_id'])}</b> · <a href='{link}'>{_esc(c['title'] or what)}</a>"
                    f"{'' if c['open'] else ' <span class=muted>(closed)</span>'}<br><span class='muted'>{_esc(what)} · opened by "
                    f"{_esc(_G['display_name'](c['by']))} {_esc(c['ts'][:16])}</span>{notes}{form}</div>")
    body = [_howto("Protocol 4.2, literary genealogies: from the larger set of reuse clusters, a small number of cases that appear "
                   "especially extensive, unexpected, or historically suggestive, to be read in the texts themselves. A case is opened "
                   "from a cluster page or a pair page (\"Mark as a case\"), carries its notes in order, and is closed when written up or "
                   "dropped. Every action is one line of an append-only log with the name of who did it."),
            "<h1>Cases</h1>",
            f"<p class='muted'>{sum(1 for c in cases.values() if c['open'])} open, {sum(1 for c in cases.values() if not c['open'])} closed · "
            + (f"<a href='/reuse/cases'>open only</a>" if show_closed else "<a href='/reuse/cases?closed=1'>show closed</a>")
            + " · open a case from a <a href='/reuse/clusters'>cluster</a> or a <a href='/pairs'>pair</a> page.</p>"]
    body += rows or ["<div class='empty'>No cases yet.</div>"]
    return _render(render, "Cases", body, "/reuse/cases")


def do_case(get, user):
    """POST /reuse/case: add / note / close / reopen. Returns the redirect path."""
    action = get("action")
    back = get("back") or "/reuse/cases"
    if action == "add":
        try:
            ref = json.loads(get("ref") or "{}")
        except ValueError:
            return back
        kind = get("kind")
        if kind not in ("cluster", "pair") or not isinstance(ref, dict):
            return back
        cid = new_case_id()
        append_case(user, "add", {"case_id": cid, "kind": kind, "ref": ref, "title": get("title")[:120], "note": get("note")[:2000]})
        return f"/reuse/cases#{cid}"
    cid = get("case_id")
    if action in ("note", "close", "reopen") and re.fullmatch(r"c\d{3,}", cid or ""):
        if action == "note" and not get("note"):
            return back
        append_case(user, action, {"case_id": cid, "note": get("note")[:2000]})
        return f"/reuse/cases#{cid}"
    return back
