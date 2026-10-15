"""The verification sample (site v0.22.0, p50t; corpus-build-plan-2026-10-04 section 6.1; Heejin, 14 October 2026:
"150 issues, stratified, two readers").

  /verify/sample    the 150 sampled issues in reading order, with what is done on each (records verified of the
                    records the machine made, who verified them), the second-reader marks, the checklist a reader
                    follows, the progress by decade and genre, and the assembly's accuracy on the verified records
                    (data/assembly_v2/verification_accuracy.json, written by pipeline/s11_corrections_audit.py --sample).

The sample is config/verification_sample.json (scripts/draw_verification_sample.py; drawn once, seed 20261014). The
counts come from the explorer database (issues.records, issues.verified; records.verified_by). Bound to the site's
helpers with bind(globals()) from app.py, like the other page modules.
"""
import json
import os
import sys
import time
from collections import Counter, defaultdict

_G = {}
_CACHE = {"sample": (None, None)}


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
    "<p>One issue at a time, in the order of the table (the order mixes the genres). Open the issue on the workbench, "
    "with its scan beside the records, and go through it from the contents page onward:</p><ol>"
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
    rows, done_n, rec_total, ver_total = [], 0, 0, 0
    by_dec, by_gen = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for i in S["issues"]:
        d = iss.get(i["id"]) or {}
        n, v = d.get("records") or 0, d.get("verified") or 0
        done = bool(n) and v >= n
        done_n += done; rec_total += n; ver_total += v
        by_dec[i["decade"]][0] += 1; by_dec[i["decade"]][1] += done
        by_gen[i["genre"]][0] += 1; by_gen[i["genre"]][1] += done
        state = "done" if done else ("started" if v else "to do")
        if want == "todo" and done:
            continue
        if want == "done" and not done:
            continue
        if want == "second" and not i["second_reader"]:
            continue
        who = ", ".join(f"{_esc(_G['display_name'](u))} {c}" for u, c in readers.get(i["id"], {}).most_common(3))
        rows.append([E.N(i["order"]), f"<a href='/issue/{_esc(i['id'])}'>{_esc(i['magazine'] or i['id'])}</a>", _esc(i.get("cover_date") or ""),
                     _esc(i["decade"]), _esc(i["genre"]) + (f" <span class='fine'>({_esc(i['genre_as_filed'])})</span>" if i.get("genre_as_filed") and i.get("genre_as_filed") != i["genre"] else ""),
                     E.N(n), E.N(v), f"<span class='chip {'stV' if done else ('stM' if v else '')}'>{state}</span>",
                     "yes" if i["second_reader"] else "", who])
    head.append(f"<p class='muted'>{len(S['issues'])} issues drawn on {_esc(S.get('drawn', '')[:10])} (seed {S.get('seed')}) from "
                f"{S.get('population', '?'):,} assembled issues; {done_n} done, {sum(1 for i in S['issues'] if (iss.get(i['id']) or {}).get('verified'))} started; "
                f"{ver_total:,} of {rec_total:,} records verified; {sum(i['second_reader'] for i in S['issues'])} issues are read twice.</p>")
    head.append("<p class='fine'>Show: " + " · ".join(f"<a href='/verify/sample?show={k}'>{l}</a>" if want != k else f"<b>{l}</b>"
                for k, l in (("all", "all"), ("todo", "to do"), ("done", "done"), ("second", "second reader"))) + "</p>")
    head.append("<details class='howto' open><summary>THE CHECKLIST</summary><div>" + CHECKLIST + "</div></details>")
    body = head + [E._table(["#", "issue", "date", "decade", "genre", "#records", "#verified", "state", "second reader", "verified by"], rows)]
    body.append("<h2>Progress by stratum (issues done / drawn)</h2>")
    body.append(E._table(["decade", "#drawn", "#done"], [[_esc(k), E.N(v[0]), E.N(v[1])] for k, v in sorted(by_dec.items())]))
    body.append(E._table(["genre", "#drawn", "#done"], [[_esc(k), E.N(v[0]), E.N(v[1])] for k, v in sorted(by_gen.items(), key=lambda kv: -kv[1][0])]))
    acc = accuracy()
    body.append("<h2>The assembly's accuracy on the verified records</h2>")
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
    return (f"<p class='fine'>The <a href='/verify/sample'>verification sample</a>: {len(S['issues'])} issues drawn on "
            f"{_esc(S.get('drawn', '')[:10])}; the accuracy of the assembly on real corpus issues is measured on them.</p>")


def _render(render, title, body):
    html = "".join(body)
    return render(title, html, "/verify/sample") if render else html
