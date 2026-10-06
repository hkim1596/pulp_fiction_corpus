"""The model check review (site v0.19.0, p50p; Heejin, 5 October 2026, choosing "Quick review page" after the first
corpus numbers: the language model disagrees with about one record in four, too many to check one by one).

  /review/model    one disagreement at a time: the rules' record, what the model would change, the scan of the page
                   with the boxes marked, the text of the box and of the boxes beside it; a person says THE RULES ARE
                   RIGHT, THE MODEL IS RIGHT, NEITHER, or CAN'T TELL. Each judgment is one line of an append-only log,
                   data/review/model_check.jsonl (time, reader, case, issue, record, kind, box, the model's
                   confidence, verdict, note). The page counts the verdicts by kind of change.

The cases come from data/review/model_disagreements.jsonl, which the site refresh writes from what s13 published
(each issue's data/articles/<id>/published.json). The next case is chosen so that the kinds of change are judged about
equally (settings.review.per_kind_target each, 25 by default), from the magazines judged least so far, and from cases
nobody has judged yet. When every kind has its share, the counts say, kind by kind, whether the model's change should
be applied, ignored, or kept as a flag for a person (the decision is Heejin's; s13 does what settings.publish says).

Bound to the site's helpers with bind(globals()) from app.py, like the other page modules. Reading is open to every
member; judging needs a named account (the log carries the name).
"""
import json
import os
import random
import sys
import time
import urllib.parse
from collections import Counter, defaultdict

_G = {}
VERDICTS = {"rules": "the rules are right", "model": "the model is right", "neither": "neither", "cannot_tell": "can't tell"}
SHORT = {"split": "split", "join": "join", "box_in": "box added", "out_ad": "box out as advertising",
         "out_furniture": "box out as furniture", "out_continues": "box to the piece before", "out_new": "new piece inside",
         "apart": "taken apart"}
_CACHE = {"pool": (None, []), "log": (None, [])}


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)


def _esc(s):
    return _G["esc"](s)


def _kinds():
    import s13_publish
    return s13_publish.KINDS


def _pool_path():
    return os.path.join(_G["DATA"], "review", "model_disagreements.jsonl")


def _log_path():
    return os.path.join(_G["DATA"], "review", "model_check.jsonl")


def _target():
    try:
        import corpus_lib
        return int((corpus_lib.settings().get("review") or {}).get("per_kind_target", 25))
    except Exception:
        return 25


def _jsonl_cached(name, path):
    try:
        st = os.stat(path)
        sig = (st.st_mtime, st.st_size)
    except OSError:
        return []
    if _CACHE[name][0] != sig:
        rows = []
        for line in open(path, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        _CACHE[name] = (sig, rows)
    return _CACHE[name][1]


def pool():
    return _jsonl_cached("pool", _pool_path())


def judgments():
    return _jsonl_cached("log", _log_path())


def latest(js):
    """case -> user -> that user's last judgment of it."""
    out = defaultdict(dict)
    for j in js:
        out[j["case"]][j["user"]] = j
    return out


def tally(js=None):
    """kind -> Counter of verdicts (each reader's last word on a case)."""
    t = defaultdict(Counter)
    for case, d in latest(judgments() if js is None else js).items():
        for j in d.values():
            t[j.get("kind") or "other"][j["verdict"]] += 1
    return t


def append_judgment(user, form):
    """One judgment from the page's form. Returns the case id, or None when the form is not a judgment."""
    verdict = form("verdict")
    case = form("case")
    if verdict not in VERDICTS or not case:
        return None
    c = next((x for x in pool() if x["case"] == case), None) or {}
    os.makedirs(os.path.dirname(_log_path()), exist_ok=True)
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": user, "case": case,
                            "issue": c.get("issue"), "record": c.get("record"), "kind": c.get("kind"), "key": c.get("key"),
                            "change": c.get("change"), "conf": c.get("conf"), "verdict": verdict,
                            "note": (form("note") or "")[:2000]}, ensure_ascii=False) + "\n")
    return case


def next_case(user, skip=()):
    """The kind judged least so far; in it, the magazine judged least so far; a case nobody has judged (else one this
    reader has not), chosen at random."""
    P = pool()
    if not P:
        return None
    by_case = latest(judgments())
    done_by_me = {c for c, d in by_case.items() if user in d}
    t = tally()
    n_kind = {k: sum(v.values()) for k, v in t.items()}
    mag_of = {c["issue"]: c.get("magazine") or c["issue"] for c in P}
    mag_n = Counter()
    for case, d in by_case.items():
        iid = case.split("|")[0]
        mag_n[mag_of.get(iid, iid)] += len(d)
    cand = [c for c in P if c["case"] not in done_by_me and c["case"] not in skip]
    if not cand:
        return None
    fresh = [c for c in cand if c["case"] not in by_case] or cand
    kinds = sorted({c["kind"] for c in fresh}, key=lambda k: (n_kind.get(k, 0), random.random()))
    in_kind = [c for c in fresh if c["kind"] == kinds[0]]
    least = min(mag_n.get(mag_of.get(c["issue"]), 0) for c in in_kind)
    return random.choice([c for c in in_kind if mag_n.get(mag_of.get(c["issue"]), 0) == least])


CSS = """
<style>
.mr{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}
.mrscan{flex:1 1 520px;max-width:760px}
.mrside{flex:1 1 360px;min-width:300px}
.mrbox{border:1px solid var(--grid);background:var(--surface);border-radius:10px;padding:10px 14px;margin:0 0 12px}
.mrbox .t{font-family:Georgia,serif;font-size:15px;line-height:1.5}
.mrbox .k{font-size:12.5px;color:var(--ink2)}
.mrbtns{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}
.mrbtns button{font-size:14px;padding:7px 14px;border:1px solid var(--grid2);background:var(--surface);cursor:pointer;font-family:inherit;border-radius:8px}
.mrbtns button.r{background:var(--okbg)}.mrbtns button.m{background:var(--warnbg)}.mrbtns button.n{background:var(--surface2)}
.mrnote{width:100%;height:52px;font-family:inherit;font-size:13px}
.fbox.gX rect.bx{stroke:#d62728;stroke-width:9;fill:rgba(214,39,40,.10)}
.fbox.gX rect.lab{fill:#d62728}
.fbox.gY rect.bx{stroke:var(--purple2);stroke-width:5;fill:rgba(138,92,199,.10)}
.fbox.gY rect.lab{fill:var(--purple2)}
.mrsum{font-size:13px;color:var(--ink2);margin:4px 0 12px}
</style>"""


def _scan(c, doc):
    """The page of the case's box, with this record's boxes (blue), the other record's (purple, for a join or a move
    to the piece before), the other records' (grey), and the box of the case (red)."""
    iid = c["issue"]
    pno, idx = map(int, c["key"].split(":"))
    lay = _G["layout_of"](iid, pno) or {}
    W, H = lay.get("width") or 1000, lay.get("height") or 1400
    regs = _G["page_regions"](iid, pno)
    owner = {}
    for a in doc.get("articles", []):
        for f in a.get("fragments", []):
            if f.get("page") == pno:
                for i in f.get("region_ids", []):
                    owner.setdefault(i, a["article_id"])
    rec, other = c.get("record"), c.get("other")
    g = []
    for i, r in enumerate(regs):
        if not r.get("bbox"):
            continue
        x0, y0, x1, y1 = r["bbox"]
        grp = "X" if i == idx else ("B" if owner.get(i) == rec else ("Y" if other and owner.get(i) == other else ("O" if i in owner else "F")))
        lab = ""
        if i == idx or (grp in ("B", "Y") and (i == 0 or owner.get(i - 1) != owner.get(i))):
            name = f"{pno}{chr(65 + i) if i < 26 else 'Z' + str(i)}"
            fs = max(22, int(H * 0.018))
            w = int(fs * 0.62 * len(name)) + 12
            bx = max(0, min(x0 - w - 4 if (x0 + x1) / 2 < W / 2 else x1 + 4, W - w))
            lab = (f"<rect class='lab' x='{bx}' y='{max(y0 - 2, 0)}' width='{w}' height='{fs + 6}' rx='4'/>"
                   f"<text class='labt' x='{bx + 6}' y='{max(y0 - 2, 0) + fs}' font-size='{fs}'>{name}</text>")
        g.append(f"<g class='fbox g{grp}'><rect class='bx' x='{x0}' y='{y0}' width='{x1 - x0}' height='{y1 - y0}'/>{lab}</g>")
    return (f"<div class='scanwrap'><img src='/img/{iid}/page_{pno:04d}.png' alt='page {pno}'>"
            f"<svg viewBox='0 0 {W} {H}' preserveAspectRatio='none'>{''.join(g)}</svg>"
            f"<div class='pgcap'>page {pno} · red: the box of the question · blue: this record as the rules made it"
            + (" · purple: the other record" if other else "") + " · grey dashes: other records and page furniture · "
            f"<a href='/issue/{iid}/p/{pno}' target='_blank'>this page full size</a></div></div>")


def _texts(c):
    """The text of the case's box and of the boxes before and after it on the page."""
    iid = c["issue"]
    pno, idx = map(int, c["key"].split(":"))
    regs = _G["page_regions"](iid, pno)

    def t(i):
        return " ".join(str(regs[i].get("text") or "").split()) if 0 <= i < len(regs) else ""
    def name(i):
        return f"{pno}{chr(65 + i) if i < 26 else 'Z' + str(i)}"
    out = []
    for i, what in ((idx - 1, "the box before"), (idx, "THE BOX"), (idx + 1, "the box after")):
        if 0 <= i < len(regs) and t(i):
            txt = t(i)
            out.append(f"<div class='k'>{what} · {name(i)} · {_esc(regs[i].get('label') or '')}</div>"
                       f"<div class='t'>{_esc(txt[:700])}{'…' if len(txt) > 700 else ''}</div>")
    return "".join(out)


def counts_table(t=None):
    """The verdicts by kind of change."""
    E = _G["EX"]
    t = tally() if t is None else t
    P = pool()
    in_pool = Counter(c["kind"] for c in P)
    target = _target()
    try:
        import s13_publish
        pol = s13_publish.kinds_policy()
    except Exception:
        pol = {}
    now = {"apply": "applied", "apply_flag": "applied, still flagged", "flag": "flagged", "ignore": "set aside (not flagged)"}
    rows = []
    for k, label in _kinds().items():
        v = t.get(k, Counter())
        n = sum(v[x] for x in ("rules", "model", "neither"))
        share = (f"{100 * v['model'] / n:.0f}%" if n else "")
        rows.append([_esc(label), E.N(in_pool.get(k, 0)), E.N(sum(v.values())) + f" <span class='fine'>of {target}</span>",
                     E.N(v["rules"]), E.N(v["model"]), E.N(v["neither"]), E.N(v["cannot_tell"]), E.N(share),
                     _esc(now.get(pol.get(k), ""))])
    return E._table(["kind of change", "#cases waiting", "#judged", "#rules right", "#model right", "#neither",
                     "#can't tell", "model right (of the decided)", "what the site does with it now"], rows)


def review_page(qs, user, render=None):
    kinds = _kinds()
    P = pool()
    want = (qs.get("case") or [""])[0]
    skip = [(qs.get("skip") or [""])[0]]
    head = [CSS, _G["howto"](
        "The language model checks every box of every corpus issue against the rules' records. What it would change is "
        "applied, flagged or set aside kind by kind, as Heejin decided on 6 October from the first 206 judgments here "
        "(the last column of the table). This page goes on asking, one case at a time, who is right, so that the "
        "decisions can be checked and changed as the counts grow. Look at the red box on the scan and "
        "the texts beside it, read what the model would change, and choose: THE RULES ARE RIGHT (the record should stay "
        "as it is), THE MODEL IS RIGHT (the change should be made), NEITHER (both are wrong; the record needs another "
        "fix — a note helps), or CAN'T TELL. Keys: 1, 2, 3, 4. The cases are spread over the kinds of change and the "
        "magazines; the table counts the verdicts by kind."),
        "<h1>Model check review</h1>",
        f"<p class='muted'>{len(P):,} disagreements waiting in the issues the model has checked so far "
        f"(the list is renewed by the site refresh). Judged so far: "
        + (", ".join(f"{_esc(_G['display_name'](u))} {n}" for u, n in Counter(j['user'] for j in judgments()).most_common()) or "nobody yet")
        + ".</p>"]
    t = tally()
    head.append("<div class='mrsum'>Judged by kind (of about " + str(_target()) + " each): " + " · ".join(
        f"{_esc(SHORT.get(k, k))} {sum(t.get(k, Counter()).values())}" for k in kinds) + " · the counts in full are below the case.</div>")
    if not P:
        return _render(render, "Model check review", head + ["<div class='empty'>No disagreements yet: the model has "
                                                              "not checked an issue the site shows.</div>", counts_table()])
    c = next((x for x in P if x["case"] == want), None) if want else next_case(user, skip=skip)
    if c is None:
        return _render(render, "Model check review", head + ["<div class='empty'>You have judged every case waiting. "
                                                              "Thank you. More come as the model checks more issues.</div>",
                                                              counts_table()])
    doc = _G["articles_of"](c["issue"]) or {"articles": []}
    rec = next((a for a in doc.get("articles", []) if a["article_id"] == c["record"]), {})
    other = next((a for a in doc.get("articles", []) if a["article_id"] == c.get("other")), None)
    mine = latest(judgments()).get(c["case"], {})
    can = bool(user) and user != "guest"
    pages = rec.get("pages") or []
    side = [f"<div class='mrbox'><div class='k'>{_esc(c.get('magazine') or c['issue'])} · {_esc(c.get('cover_date') or '')} · "
            f"<a href='/issue/{_esc(c['issue'])}'>the issue</a></div>"
            f"<div><b>{_esc(rec.get('title') or c.get('title') or '(untitled)')}</b> · {_esc(rec.get('type') or c.get('type') or '')}"
            + (f" · pages {pages[0]}–{pages[-1]}" if pages else "")
            + f" · <a href='/article/{_esc(c['record'])}' target='_blank'>open the record on the workbench</a></div>"
            + (f"<div class='k'>the other record: <a href='/article/{_esc(other['article_id'])}' target='_blank'>"
               f"{_esc(other.get('title') or other['article_id'])}</a> ({_esc(other.get('type') or '')})</div>" if other else "")
            + "</div>",
            f"<div class='mrbox'><div class='k'>{_esc(kinds.get(c['kind'], c['kind']))}</div>"
            f"<div><b>{_esc(c.get('plain') or c.get('change'))}</b></div></div>",
            f"<div class='mrbox'>{_texts(c)}</div>"]
    if can:
        side.append(
            "<form method='POST' action='/review/model' id='mrform'>"
            f"<input type='hidden' name='case' value='{_esc(c['case'])}'>"
            "<div class='mrbtns'>"
            "<button class='r' name='verdict' value='rules' accesskey='1'>1 · The rules are right</button>"
            "<button class='m' name='verdict' value='model' accesskey='2'>2 · The model is right</button>"
            "<button class='n' name='verdict' value='neither' accesskey='3'>3 · Neither</button>"
            "<button class='n' name='verdict' value='cannot_tell' accesskey='4'>4 · Can't tell</button></div>"
            "<textarea class='mrnote' name='note' placeholder='A note (optional): what decided it, or what the right fix is'></textarea>"
            f"<p class='fine'><a href='/review/model?skip={urllib.parse.quote(c['case'])}'>another case</a></p></form>"
            "<script>document.addEventListener('keydown',function(e){if(e.target.tagName==='TEXTAREA')return;"
            "var m={'1':'rules','2':'model','3':'neither','4':'cannot_tell'}[e.key];if(!m)return;"
            "var b=document.querySelector('#mrform button[value=\"'+m+'\"]');if(b)b.click();});</script>")
    else:
        side.append("<p class='muted'>Judging needs a named account; guests can look at the cases and the counts.</p>")
    if mine:
        side.append("<p class='fine'>Judged: " + "; ".join(
            f"{_esc(_G['display_name'](u))} — {_esc(VERDICTS.get(j['verdict'], j['verdict']))}"
            + (f" (“{_esc(j['note'])}”)" if j.get("note") else "") for u, j in mine.items()) + "</p>")
    side.append(f"<p class='fine'>The model's confidence in this change: {c.get('conf') if c.get('conf') is not None else '—'}. "
                f"Case {_esc(c['case'])}.</p>")
    body = head + ["<div class='mr'>", f"<div class='mrscan'>{_scan(c, doc)}</div>",
                   f"<div class='mrside'>{''.join(side)}</div>", "</div>",
                   "<h2>The verdicts so far, by kind of change</h2>", counts_table(t)]
    return _render(render, "Model check review", body)


def run_lines():
    """For the Corpus run page: the review's counts in one table, when there are judgments."""
    if not judgments():
        return ""
    return ("<h3>Who is right where the model disagrees (the <a href='/review/model'>model check review</a>)</h3>"
            + counts_table())


def _render(render, title, body):
    html = "".join(body)
    return render(title, html, "/review/model") if render else html
