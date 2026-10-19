"""The verification sample (site v0.22.0, p50t; corpus-build-plan-2026-10-04 section 6.1; Heejin, 14 October 2026:
"150 issues, stratified, two readers"; two levels of reading since 19 October 2026, Sujin Kang's decision 2).

  /verify/sample            the 150 sampled issues in reading order, with what is done on each at both levels — the
                            contents check (level 1) and the full verification (records verified of the records the
                            machine made, who verified them) — the second-reader marks, the checklist a reader follows,
                            the progress by decade and genre, the contents check's figure and the assembly's accuracy
                            on the verified records (data/assembly_v2/verification_accuracy.json, written by
                            pipeline/s11_corrections_audit.py --sample).
  /verify/contents/<issue>  the contents check of one issue: the entries the contents page lists (as the assembly read
                            them), beside the scan of that page and the record the machine made for each, with a
                            verdict per entry — right as made / fixed / missing / not a piece — and a note; saved as one
                            line in data/review/contents_check.jsonl (append-only, under the reader's name).

The contents check is Supplement A's check ("at least 95 percent of contents-page entries assembled with the correct
start page, title and author"): about fifteen minutes an issue, on all 150 issues first; the full verification goes
on at an issue an hour on as many as time allows. The sample is config/verification_sample.json
(scripts/draw_verification_sample.py; drawn once, seed 20261014; slots redrawn when an issue is set aside). The record
counts come from the explorer database (issues.records, issues.verified; records.verified_by). Bound to the site's
helpers with bind(globals()) from app.py, like the other page modules.
"""
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

_G = {}
_CACHE = {"sample": (None, None), "checks": (None, None)}

VERDICTS = {"right": "right as made", "fixed": "fixed", "missing": "missing", "na": "not a piece"}
READING_TYPES = ("story", "poem", "feature", "letters", "other")


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)


def _esc(s):
    return _G["esc"](s)


def sample_path():
    return os.path.join(_G["ROOT"], "config", "verification_sample.json")


def sample():
    p = sample_path()
    try:
        st = os.stat(p)
        sig = (st.st_mtime, st.st_size)
    except OSError:
        return None
    if _CACHE["sample"][0] != sig:
        try:
            _CACHE["sample"] = (sig, json.load(open(p, encoding="utf-8")))
        except Exception:
            return None
    return _CACHE["sample"][1]


def accuracy():
    p = os.path.join(_G["DATA"], "assembly_v2", "verification_accuracy.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


CHECKLIST = (
    "<p><b>Level 1 — the contents check</b> (about fifteen minutes an issue; every issue of the sample, in the order of the "
    "table). Open CHECK in the contents column: the page lists every piece the contents page announces, as the machine read "
    "it, with the record the machine made for each. For each entry, against the scan of the contents page and the record's "
    "first page: does a record start on the right page with the right title and author? Mark RIGHT AS MADE when it does; "
    "FIXED when you had to correct the record on the workbench (its start, title or author); MISSING when the piece has no "
    "record and you made one or could not; NOT A PIECE when the entry is not a piece at all (a cover line, an illustration "
    "credit, a misreading). Count the entries the machine's reading of the contents page missed. Save: the issue shows "
    "<i>checked</i> in the contents column, and the figure of the study — the share of listed pieces assembled right — "
    "appears below the table.</p>"
    "<p><b>Level 2 — the full verification</b> (about an hour an issue; in the order of the table, as time allows). "
    "Open the issue on the workbench, with its scan beside the records, and go through it from the contents page onward:</p><ol>"
    "<li>Every piece the contents page lists (stories, serial parts, poems, features, departments) has a record that "
    "starts on the right page with the right title and author. A piece without a record: make one from its boxes.</li>"
    "<li>Every record holds its own boxes and nothing else: no box of another piece, no advertisement, no page "
    "furniture; a story that continues after an advertisement or a filler is one record, not two.</li>"
    "<li>The roles are right (title, by-line, teaser, chapter headings, captions, notices), so that the reading text "
    "is the story's text.</li>"
    "<li>A serial part carries its part number and its work title; advertisements and house matter are typed as such.</li>"
    "<li>When a record is right, or once it is fixed, mark it VERIFIED. When every record of the issue is verified, "
    "the issue is done here.</li></ol>"
    "<p>Do not spend long on an OCR misreading inside a box: the reading accuracy is measured by another sample "
    "(the control set). What matters here is which boxes belong to which piece. Every action is recorded under your "
    "name; the accuracy figure of the study is the agreement between the machine's records and the records you "
    "verified, by decade and genre.</p>")


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- the contents check (level 1)

def checks_path():
    return os.path.join(_G["DATA"], "review", "contents_check.jsonl")


def checks():
    """Every saved check, oldest first (cached by the file's signature)."""
    p = checks_path()
    try:
        st = os.stat(p)
        sig = (st.st_mtime, st.st_size)
    except OSError:
        return []
    if _CACHE["checks"][0] != sig:
        rows = []
        for line in open(p, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        _CACHE["checks"] = (sig, rows)
    return _CACHE["checks"][1]


def latest_checks(rows=None):
    """issue -> user -> that user's last check of it."""
    out = defaultdict(dict)
    for c in (checks() if rows is None else rows):
        out[c["issue"]][c["user"]] = c
    return out


def contents_reading(iid):
    """The contents entries the assembly read (data/assembly_v2/rules/<issue>/analysis.json: toc, toc_page, toc_pages),
    or None when the issue has no analysis."""
    p = os.path.join(_G["DATA"], "assembly_v2", "rules", iid, "analysis.json")
    try:
        a = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    toc = a.get("toc") or []
    pages = a.get("toc_pages") or 1
    tp = a.get("toc_page")
    return {"toc": toc, "toc_page": tp, "toc_scans": [tp + k for k in range(int(pages))] if tp else [], "toc_kind": a.get("toc_kind"),
            "offset": a.get("offset")}


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split())


def _sim(a, b):
    A, B = set(_norm(a).split()), set(_norm(b).split())
    if not A or not B:
        return 0.0
    return len(A & B) / max(len(A), len(B))


def _first_page(rec):
    fr = rec.get("fragments") or []
    pages = [f.get("page") for f in fr if f.get("page")] or (rec.get("pages") or [])
    return min(pages) if pages else None


def match_entries(toc, articles):
    """For each contents entry the record the machine made for it, or None: first the record whose own contents entry
    (the assembler's 'toc' field) names the same title; then a reading record (story, poem, feature, letters, other)
    whose first page is the entry's scan page or one page off; then the reading record with the most similar title,
    at 0.6 or more. A record is used once. Pure: lists of dicts in, list of (entry, record or None, how) out."""
    recs = [r for r in articles if r.get("type") in READING_TYPES]
    taken = set()
    out = []
    for e in toc:
        hit, how = None, None
        et = _norm(e.get("title"))
        for r in recs:
            t = (r.get("toc") or {}).get("title") if isinstance(r.get("toc"), dict) else None
            if et and t and _norm(t) == et and r["article_id"] not in taken:
                hit, how = r, "the record's own contents entry"
                break
        if hit is None and e.get("scan"):
            near = sorted((abs((_first_page(r) or -99) - e["scan"]), i, r) for i, r in enumerate(recs)
                          if r["article_id"] not in taken and _first_page(r) is not None and abs(_first_page(r) - e["scan"]) <= 1)
            if near:
                # among the records starting there, the one whose title is most like the entry's
                best = max(near, key=lambda x: (_sim(x[2].get("title"), e.get("title")), -x[0]))
                hit, how = best[2], ("starts on the entry's page" if best[0] == 0 else "starts one page off")
        if hit is None and et:
            scored = sorted(((_sim(r.get("title"), e.get("title")), i, r) for i, r in enumerate(recs) if r["article_id"] not in taken), reverse=True, key=lambda x: (x[0], -x[1]))
            if scored and scored[0][0] >= 0.6:
                hit, how = scored[0][2], f"a record with a title like it ({scored[0][0]:.0%})"
        if hit is not None:
            taken.add(hit["article_id"])
        out.append((e, hit, how))
    return out


def summarize(entries_verdicts, missed=0, missed_right=0):
    """The counts of one check: listed pieces (entries that are pieces), right, fixed, missing, and the figure with the
    entries the parser missed added."""
    c = Counter(v for v in entries_verdicts if v in VERDICTS)
    listed = c["right"] + c["fixed"] + c["missing"]
    return {"n_entries": len(entries_verdicts), "n_listed": listed, "n_right": c["right"], "n_fixed": c["fixed"], "n_missing": c["missing"],
            "n_na": c["na"], "missed": missed, "missed_right": missed_right,
            "pieces": listed + missed, "pieces_right": c["right"] + missed_right}


def append_check(user, form, iid=None):
    """One check from the page's form, as one line of data/review/contents_check.jsonl. Returns the record written, or
    None when the form names no issue. form(name) -> the field's value ('' when absent)."""
    iid = iid or form("issue")
    if not iid:
        return None
    S = sample() or {"issues": []}
    slot = next((i for i in S["issues"] if i["id"] == iid), {})
    reading = contents_reading(iid) or {"toc": [], "toc_page": None}
    doc = _G["effective_doc"](iid) or {"articles": []}
    matched = match_entries(reading["toc"], doc.get("articles", []))
    entries = []
    for k, (e, rec, how) in enumerate(matched):
        v = form(f"v_{k}")
        if v not in VERDICTS:
            v = None
        entries.append({"i": k, "page": e.get("page"), "scan": e.get("scan"), "title": e.get("title"), "author": e.get("author"),
                        "type": e.get("type"), "record": rec["article_id"] if rec else None, "record_title": rec.get("title") if rec else None,
                        "verdict": v, "note": (form(f"n_{k}") or "")[:300]})

    def num(name):
        try:
            return max(0, int(form(name) or 0))
        except ValueError:
            return 0
    summ = summarize([x["verdict"] for x in entries], num("missed"), num("missed_right"))
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": user, "issue": iid, "order": slot.get("order"), "decade": slot.get("decade"),
           "genre": slot.get("genre"), "toc_page": reading.get("toc_page"), "entries": entries, **summ,
           "unlisted": num("unlisted"), "note": (form("note") or "")[:2000], "complete": all(x["verdict"] for x in entries)}
    os.makedirs(os.path.dirname(checks_path()), exist_ok=True)
    with open(checks_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _CACHE["checks"] = (None, None)
    return rec


def figure(rows):
    """The contents check's figure over a set of checks (one per issue: the latest of each reader, the first reader's
    when two have checked): pieces listed, pieces right, the share."""
    pieces = sum(r.get("pieces", 0) for r in rows)
    right = sum(r.get("pieces_right", 0) for r in rows)
    return {"issues": len(rows), "pieces": pieces, "right": right, "share": (right / pieces) if pieces else None}


def reader_agreement(lc=None):
    """Where two readers checked the same issue: the entries on which their verdicts agree (the first two readers by
    the time of their latest check)."""
    lc = latest_checks() if lc is None else lc
    out = {"issues": 0, "entries": 0, "same": 0}
    for iid, by_user in lc.items():
        if len(by_user) < 2:
            continue
        a, b = sorted(by_user.values(), key=lambda c: c["ts"])[:2]
        pairs = list(zip(a.get("entries", []), b.get("entries", [])))
        out["issues"] += 1
        out["entries"] += len(pairs)
        out["same"] += sum(1 for x, y in pairs if x.get("verdict") == y.get("verdict"))
    return out


def one_per_issue(lc=None):
    """issue -> the check that counts for the figure: the earliest reader's latest check (the second reader's check is
    reported beside it, as agreement)."""
    lc = latest_checks() if lc is None else lc
    out = {}
    for iid, by_user in lc.items():
        first = min(by_user.values(), key=lambda c: c["ts"])
        out[iid] = first
    return out


CONTENTS_CSS = """
<style>
.cc{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}
.ccscan{flex:1 1 380px;max-width:620px}
.ccscan img{width:100%;border:1px solid var(--grid);border-radius:6px;background:#fff}
.ccside{flex:2 1 560px;min-width:320px}
.cct{width:100%;border-collapse:collapse;font-size:13px}
.cct th,.cct td{padding:5px 7px;vertical-align:top;border-bottom:1px solid var(--grid)}
.cct td.v{white-space:nowrap}
.cct td.v label{margin-right:8px;cursor:pointer}
.cct input.n{width:100%;font-family:inherit;font-size:12px}
.ccbtns{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}
.ccbtns button,.ccbtns a.b{font-size:14px;padding:7px 14px;border:1px solid var(--grid2);background:var(--okbg);cursor:pointer;font-family:inherit;border-radius:8px;text-decoration:none;color:inherit}
.ccbtns button.g{background:var(--surface2)}
.ccnote{width:100%;height:52px;font-family:inherit;font-size:13px}
.ccnum input{width:54px;font-family:inherit}
.ccbox{border:1px solid var(--grid);background:var(--surface);border-radius:10px;padding:10px 14px;margin:0 0 12px;font-size:13px}
</style>"""


def contents_page(iid, qs, user, render=None):
    """The contents check of one issue (level 1)."""
    E = _G["EX"]
    S = sample()
    slot = next((i for i in (S or {"issues": []})["issues"] if i["id"] == iid), None)
    info = _G["issue_by_id"](iid) or {"id": iid, "magazine": iid, "cover_date": ""}
    reading = contents_reading(iid)
    doc = _G["effective_doc"](iid)
    can = bool(user) and user != "guest"
    title = f"Contents check · {info.get('magazine') or iid}"
    head = [CONTENTS_CSS, _G["howto"](
        "Level 1 of the verification sample (Supplement A's check: at least 95 percent of contents-page entries assembled "
        "with the correct start page, title and author). The entries are the contents page as the assembly read it; "
        "beside each is the record the machine made for it, with its first page. Look at the scan of the contents page "
        "(left) and, where in doubt, open the record or its page. Mark each entry: RIGHT AS MADE when a record starts on "
        "the right page with the right title and author; FIXED when you corrected it on the workbench; MISSING when the "
        "piece has no record; NOT A PIECE when the entry is not a piece (a cover line, a credit, a misreading). Below the "
        "table, count the pieces the contents page lists that the machine's reading of the page missed (and how many of "
        "them nevertheless have a right record), and anything in the issue the contents page does not list. Then save. "
        "Your check is kept under your name; a second check of the same issue by another reader is kept beside it."),
        f"<h1>{_esc(title)}</h1>",
        f"<p class='muted'><a href='/verify/sample'>&larr; the verification sample</a>" + (f" · slot #{slot['order']} · {_esc(slot['decade'])} · {_esc(slot['genre'])}" if slot else " · not in the sample")
        + f" · <a href='/issue/{_esc(iid)}'>the issue</a> · {_esc(info.get('cover_date') or '')}</p>"]
    if not reading or not doc:
        return _render(render, title, head + ["<div class='empty'>No assembly on disk for this issue (analysis.json or the live records are missing).</div>"], f"/verify/contents/{iid}")
    matched = match_entries(reading["toc"], doc.get("articles", []))
    mine = (latest_checks().get(iid) or {}).get(user) if can else None
    prev = {x["i"]: x for x in (mine or {}).get("entries", [])} if mine else {}
    others = {u: c for u, c in (latest_checks().get(iid) or {}).items() if u != user}
    scans = reading.get("toc_scans") or []
    left = ["<div class='ccscan'>"]
    if scans:
        for n in scans:
            left.append(f"<p class='fine'>the contents page, scan {n} · <a href='/issue/{_esc(iid)}/p/{n}'>open the page</a></p>"
                        f"<a href='/issue/{_esc(iid)}/p/{n}'><img src='/img/{_esc(iid)}/page_{n:04d}.png' alt='contents page scan {n}'></a>")
    else:
        left.append("<div class='empty'>The assembly found no contents page in this issue: the entries below (if any) come from the "
                    "pages themselves. Check the issue's pieces on the workbench and use the note.</div>")
    left.append("</div>")
    rows = []
    for k, (e, rec, how) in enumerate(matched):
        pv = (prev.get(k) or {}).get("verdict")
        pn = (prev.get(k) or {}).get("note") or ""
        if rec:
            fp = _first_page(rec)
            rtxt = (f"<a href='/article/{_esc(rec['article_id'])}' target='_blank'>{_esc(rec.get('title') or '(untitled)')}</a>"
                    + (f" · {_esc(rec.get('author'))}" if rec.get("author") else " · <span class='muted'>no author</span>")
                    + f" · {_esc(rec.get('type') or '')}"
                    + (f" · <a href='/issue/{_esc(iid)}/p/{fp}' target='_blank'>scan {fp}</a>" if fp else "")
                    + f"<div class='fine'>{_esc(how or '')}" + (f" · {_esc(rec.get('status'))}" if rec.get("status") and rec.get("status") != "auto" else "") + "</div>")
        else:
            rtxt = "<span class='muted'>no record found for it</span>" + (f" · <a href='/issue/{_esc(iid)}/p/{e['scan']}' target='_blank'>scan {e['scan']}</a>" if e.get("scan") else "")
        radios = "".join(f"<label><input type='radio' name='v_{k}' value='{v}'{' checked' if pv == v else ''}{'' if can else ' disabled'}> {l}</label>"
                         for v, l in VERDICTS.items())
        rows.append(f"<tr><td class='num'>{k + 1}</td><td class='num'>{e.get('page') if e.get('page') is not None else ''}</td>"
                    f"<td><b>{_esc(e.get('title') or '')}</b>" + (f"<br><span class='fine'>{_esc(e.get('author'))}</span>" if e.get("author") else "")
                    + (f"<br><span class='fine'>{_esc(e.get('section'))}</span>" if e.get("section") else "") + f"</td><td>{_esc(e.get('type') or '')}</td>"
                    f"<td>{rtxt}</td><td class='v'>{radios}</td><td><input class='n' name='n_{k}' value='{html.escape(pn, quote=True)}' placeholder='note'{'' if can else ' disabled'}></td></tr>")
    side = ["<div class='ccside'>"]
    side.append(f"<div class='ccbox'>{len(matched)} entries as the assembly read the contents page"
                + (f" (scan {reading['toc_page']})" if reading.get("toc_page") else "") + f"; {sum(1 for _, r, _ in matched if r)} of them have a record the machine made for them; "
                f"{len([a for a in doc.get('articles', []) if a.get('type') in READING_TYPES])} reading records in the issue in all.</div>")
    if mine:
        side.append(f"<div class='ccbox'>Your last check: {_esc(mine['ts'][:16].replace('T', ' '))} — {mine['n_right']} right, {mine['n_fixed']} fixed, "
                    f"{mine['n_missing']} missing, {mine['n_na']} not a piece; {mine.get('missed', 0)} missed by the parser. Saving again replaces it in the counts (the earlier line stays in the log).</div>")
    for u, c in others.items():
        side.append(f"<div class='ccbox'>Checked by {_esc(_G['display_name'](u))} on {_esc(c['ts'][:10])}: {c['n_right']} right, {c['n_fixed']} fixed, {c['n_missing']} missing, "
                    f"{c['n_na']} not a piece" + (f" — “{_esc(c['note'])}”" if c.get("note") else "") + "</div>")
    form_open = f"<form method='POST' action='/verify/contents' id='ccform'><input type='hidden' name='issue' value='{_esc(iid)}'>" if can else "<div>"
    form_close = "</form>" if can else "</div>"
    side.append(form_open)
    side.append("<div style='overflow-x:auto'><table class='cct'><tr><th>#</th><th>p.</th><th>the entry (title, author, section)</th><th>type</th>"
                "<th>the machine's record</th><th>verdict</th><th>note</th></tr>" + "".join(rows) + "</table></div>")
    g = lambda k: (mine or {}).get(k, 0) or 0
    side.append("<div class='ccbox ccnum'>Entries the machine's reading of the contents page <b>missed</b>: "
                f"<input name='missed' value='{g('missed')}'{'' if can else ' disabled'}> of which assembled right anyway: <input name='missed_right' value='{g('missed_right')}'{'' if can else ' disabled'}>"
                f" &nbsp;·&nbsp; pieces in the issue the contents page does not list: <input name='unlisted' value='{g('unlisted')}'{'' if can else ' disabled'}></div>")
    side.append(f"<textarea class='ccnote' name='note' placeholder='A note on this issue (optional)'{'' if can else ' disabled'}>{_esc((mine or {}).get('note') or '')}</textarea>")
    if can:
        side.append("<div class='ccbtns'><button type='button' class='g' id='allright'>Mark the unmarked entries RIGHT AS MADE</button>"
                    "<button type='submit'>Save the check</button></div>"
                    "<script>document.getElementById('allright').addEventListener('click',function(){"
                    "document.querySelectorAll('#ccform tr').forEach(function(tr){var rs=tr.querySelectorAll(\"input[type=radio]\");"
                    "if(!rs.length)return;var any=false;rs.forEach(function(r){if(r.checked)any=true;});if(!any){rs[0].checked=true;}});});</script>")
    else:
        side.append("<p class='muted'>Checking needs a named account, so the log shows who did it; guests can look.</p>")
    side.append(form_close)
    side.append("</div>")
    body = head + ["<div class='cc'>", *left, *side, "</div>"]
    return _render(render, title, body, f"/verify/contents/{iid}")


def sample_page(qs, user, render=None):
    E = _G["EX"]
    S = sample()
    head = [_G["howto"](
        "The study's accuracy figure for the assembly comes from this sample: 150 issues drawn at random, at least two "
        "from every decade-and-genre cell of the corpus, read through by people on the workbench and verified record by "
        "record; every fifth issue of the reading order is read by a second reader too, so that reader agreement can be "
        "reported. The table shows what is done; the checklist says what to look at. The accuracy by stratum appears at "
        "the bottom once the audit has run."), "<h1>Verification sample</h1>"]
    if not S:
        return _render(render, "Verification sample", head + ["<div class='empty'>No sample drawn yet "
                       "(scripts/draw_verification_sample.py writes config/verification_sample.json).</div>"])
    want = (qs.get("show") or ["all"])[0]
    saved = (qs.get("checked") or [""])[0]
    lc = latest_checks()
    counted = one_per_issue(lc)
    con = E.db()
    try:
        ids = [i["id"] for i in S["issues"]]
        q = ",".join("?" * len(ids))
        iss = {r["id"]: r for r in _rows(con, f"SELECT id, records, stories, verified, modified, pages FROM issues WHERE id IN ({q})", ids)}
        readers = defaultdict(Counter)
        for r in _rows(con, f"SELECT issue, verified_by FROM records WHERE status='verified' AND issue IN ({q})", ids):
            if r["verified_by"]:
                readers[r["issue"]][r["verified_by"]] += 1
    finally:
        con.close()
    rows, done_n, rec_total, ver_total, checked_n = [], 0, 0, 0, 0
    by_dec, by_gen = defaultdict(lambda: [0, 0, 0, 0, 0]), defaultdict(lambda: [0, 0, 0, 0, 0])     # drawn, done, checked, pieces, right
    for i in S["issues"]:
        d = iss.get(i["id"]) or {}
        n, v = d.get("records") or 0, d.get("verified") or 0
        done = bool(n) and v >= n
        chk = counted.get(i["id"])
        done_n += done; rec_total += n; ver_total += v; checked_n += bool(chk)
        for tab, key in ((by_dec, i["decade"]), (by_gen, i["genre"])):
            tab[key][0] += 1; tab[key][1] += done; tab[key][2] += bool(chk)
            if chk:
                tab[key][3] += chk.get("pieces", 0); tab[key][4] += chk.get("pieces_right", 0)
        state = "done" if done else ("started" if v else "to do")
        if want == "todo" and done:
            continue
        if want == "done" and not done:
            continue
        if want == "second" and not i["second_reader"]:
            continue
        if want == "contents" and chk:
            continue
        who = ", ".join(f"{_esc(_G['display_name'](u))} {c}" for u, c in readers.get(i["id"], Counter()).most_common(3))
        if chk:
            allc = lc.get(i["id"], {})
            ctxt = (f"<span class='chip stV'>checked</span> <span class='fine'>{chk.get('pieces_right', 0)}/{chk.get('pieces', 0)} right · "
                    + ", ".join(_esc(_G['display_name'](u)) for u in allc) + f" · <a href='/verify/contents/{_esc(i['id'])}'>open</a></span>")
        else:
            ctxt = f"<a href='/verify/contents/{_esc(i['id'])}'><b>check</b></a>"
        rows.append([E.N(i["order"]), f"<a href='/issue/{_esc(i['id'])}'>{_esc(i['magazine'] or i['id'])}</a>"
                     + ((" <span class='fine'>(replaces a set-aside issue" + ("; its cell being empty, drawn from the " + i["cell_fallback"].rsplit(" ", 1)[-1] if i.get("cell_fallback") else "") + ")</span>") if i.get("replaces") else ""),
                     _esc(i.get("cover_date") or ""),
                     _esc(i["decade"]), _esc(i["genre"]) + (f" <span class='fine'>({_esc(i['genre_as_filed'])})</span>" if i.get("genre_as_filed") and i.get("genre_as_filed") != i["genre"] else ""),
                     ctxt, E.N(n), E.N(v), f"<span class='chip {'stV' if done else ('stM' if v else '')}'>{state}</span>",
                     "yes" if i["second_reader"] else "", who])
    fig = figure(list(counted.values()))
    head.append(f"<p class='muted'>{len(S['issues'])} issues drawn on {_esc(S.get('drawn', '')[:10])} (seed {S.get('seed')}) from "
                f"{S.get('population', '?'):,} assembled issues" + (f"; {len(S.get('replaced') or [])} slot(s) redrawn after issues were set aside" if S.get("replaced") else "")
                + f". <b>Contents check (level 1):</b> {checked_n} of {len(S['issues'])} issues checked"
                + (f"; {fig['right']:,} of {fig['pieces']:,} listed pieces assembled right ({100 * fig['share']:.1f}%; the check asks for 95%)" if fig["pieces"] else "")
                + f". <b>Full verification (level 2):</b> {done_n} done, {sum(1 for i in S['issues'] if (iss.get(i['id']) or {}).get('verified'))} started; "
                f"{ver_total:,} of {rec_total:,} records verified; {sum(i['second_reader'] for i in S['issues'])} issues are read twice.</p>")
    if saved:
        head.append(f"<p class='fine' style='color:var(--green2)'>Saved: the contents check of {_esc(saved)}.</p>")
    head.append("<p class='fine'>Show: " + " · ".join(f"<a href='/verify/sample?show={k}'>{l}</a>" if want != k else f"<b>{l}</b>"
                for k, l in (("all", "all"), ("contents", "contents check to do"), ("todo", "verification to do"), ("done", "done"), ("second", "second reader"))) + "</p>")
    head.append("<details class='howto' open><summary>THE CHECKLIST</summary><div>" + CHECKLIST + "</div></details>")
    body = head + [E._table(["#", "issue", "date", "decade", "genre", "contents", "#records", "#verified", "state", "second reader", "verified by"], rows)]
    body.append("<h2>Progress by stratum (issues drawn / contents checked / done)</h2>")
    pct = lambda v: E.N(f"{100 * v[4] / v[3]:.0f}" if v[3] else "-")
    body.append(E._table(["decade", "#drawn", "#contents checked", "#listed pieces", "#right", "right %", "#done"],
                         [[_esc(k), E.N(v[0]), E.N(v[2]), E.N(v[3]), E.N(v[4]), pct(v), E.N(v[1])] for k, v in sorted(by_dec.items())]))
    body.append(E._table(["genre", "#drawn", "#contents checked", "#listed pieces", "#right", "right %", "#done"],
                         [[_esc(k), E.N(v[0]), E.N(v[2]), E.N(v[3]), E.N(v[4]), pct(v), E.N(v[1])] for k, v in sorted(by_gen.items(), key=lambda kv: -kv[1][0])]))
    body.append("<h2>The contents check's figure (level 1)</h2>")
    if fig["pieces"]:
        ag = reader_agreement(lc)
        agree = f" {ag['issues']} issue(s) checked by two readers: the readers agree on {ag['same']} of {ag['entries']} entries." if ag["issues"] else ""
        body.append(f"<p class='muted'>Over the {fig['issues']} issues checked (the first reader's latest check of each): {fig['pieces']:,} pieces the contents "
                    f"pages list ({sum(r.get('n_listed', 0) for r in counted.values()):,} read by the machine, {sum(r.get('missed', 0) for r in counted.values()):,} missed by it), "
                    f"<b>{fig['right']:,} assembled right as made ({100 * fig['share']:.1f}%)</b>; {sum(r.get('n_fixed', 0) for r in counted.values()):,} fixed by a reader, "
                    f"{sum(r.get('n_missing', 0) for r in counted.values()):,} missing; {sum(r.get('n_na', 0) for r in counted.values()):,} entries were not pieces.{agree} "
                    f"Supplement A asks for 95 percent. The log is data/review/contents_check.jsonl.</p>")
    else:
        body.append("<p class='muted'>No issue checked yet: open CHECK in the contents column of the first issue.</p>")
    acc = accuracy()
    body.append("<h2>The assembly's accuracy on the verified records (level 2)</h2>")
    if acc:
        body.append(f"<p class='muted'>From pipeline/s11_corrections_audit.py --sample, run {_esc(acc.get('ts', ''))}: {acc.get('records', 0):,} verified "
                    f"records in {acc.get('issues', 0)} sampled issues. A record is <i>exact</i> when the person left it as the machine made it "
                    f"(boxes, roles, title, author, type); <i>same boxes</i> when only a role or a field was corrected. Weighted = the strata "
                    f"weighted by their share of the corpus.</p>")
        body.append(E._table(["stratum", "#verified", "#exact", "exact %", "#same boxes", "same boxes %"],
                             [[_esc(r["stratum"]), E.N(r["n"]), E.N(r["exact"]), E.N(f"{100 * r['exact'] / r['n']:.0f}" if r["n"] else "-"),
                               E.N(r["same_boxes"]), E.N(f"{100 * r['same_boxes'] / r['n']:.0f}" if r["n"] else "-")] for r in acc.get("rows", [])]))
    else:
        body.append("<p class='muted'>Not computed yet: it appears once records are verified and the audit has run.</p>")
    return _render(render, "Verification sample", body)


def run_lines():
    """For the Corpus run page: one line on the sample's progress."""
    S = sample()
    if not S:
        return ""
    fig = figure(list(one_per_issue().values()))
    return (f"<p class='fine'>The <a href='/verify/sample'>verification sample</a>: {len(S['issues'])} issues drawn on "
            f"{_esc(S.get('drawn', '')[:10])}; the accuracy of the assembly on real corpus issues is measured on them"
            + (f" — the contents check so far: {fig['issues']} issues, {fig['right']:,} of {fig['pieces']:,} listed pieces right ({100 * fig['share']:.1f}%)" if fig["pieces"] else "") + ".</p>")


def _render(render, title, body, path="/verify/sample"):
    page = "".join(body)
    return render(title, page, path) if render else page
