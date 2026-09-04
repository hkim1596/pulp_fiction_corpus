"""Collection, corpus and datasheet pages (v0.14.0) — protocol section 2 on
the site.

  /collection   the archive's collection and the sample drawn from it: the
                history of transmission (when items were added, by whom, under
                which curator and sub-collection, with which OCR engine) and
                the sample assessed by decade, genre, magazine, publisher,
                language and author — "we reconstruct the collection's history
                of transmission and assess the resulting sample by year, title,
                author, and publisher, alongside other available metadata".
                Source: data/survey/summary.json (pipeline/s00_survey.py --run,
                --enrich) and the explorer's own tables.
  /corpus       the two corpora the protocol names: the story-level corpus and
                the parallel corpus of advertisements, contents pages, editorial
                and house matter (pipeline/r00_export_stories.py writes them as
                data/export/stories.jsonl and paratext.jsonl), with downloads.
  /datasheet    a datasheet for the corpus in the form of Gebru et al. and a data
                statement in the sense of Bender and Friedman (the protocol's
                references 12 and 13), generated from the survey and the
                corpus counts; what is not yet decided is said to be pending.

Bound to the site's helpers with bind(globals()) from app.py.
"""
import json
import os
import sys
from collections import Counter

_G = {}


def bind(g):
    _G.update(g)


def _esc(s):
    return _G["esc"](s)


def _howto(t):
    return _G["howto"](t)


def _EX():
    return _G["EX"]


def _RP():
    return _G["RP"]


def _render(render, title, body, path):
    html = "".join(body) if isinstance(body, (list, tuple)) else body
    return render(title, html, path) if render else html


def _survey():
    p = os.path.join(_G["DATA"], "survey", "summary.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _n(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "—"


def _kv_table(d, h1, h2, limit=None, total=None):
    rows = []
    for k, v in list(d.items())[:limit]:
        rows.append(f"<tr><td>{_esc(str(k))}</td><td class='num'>{_n(v)}</td>"
                    + (f"<td class='num'>{_pct(v, total)}</td>" if total else "") + "</tr>")
    return (f"<table><tr><th>{_esc(h1)}</th><th class='num'>{_esc(h2)}</th>" + ("<th class='num'>Share</th>" if total else "")
            + "</tr>" + "".join(rows) + "</table>")


def _bars(d, title, width=520, height=220, unit=""):
    cats = [str(k) for k in d]
    vals = [int(v) for v in d.values()]
    try:
        return _RP().svg_bars(cats, [(title, vals)], title, width=width, height=height, unit=unit)
    except Exception:
        return ""


# ---------------------------------------------------------------- /collection

def collection_page(qs, render=None):
    sv = _survey()
    if not sv:
        return _render(render, "Collection", [_howto("The survey has not run on this server: python3 pipeline/s00_survey.py --run"),
                                             "<h1>The collection</h1><div class='empty'>No survey.</div>"], "/collection")
    w = sv.get("working_corpus", {})
    pv = sv.get("provenance", {}) or {}
    total = sv.get("total_items", 0)
    EX = _EX()
    con = EX.db()
    fr = EX.survey_frame(con)
    out = [_howto(
        "Protocol section 2: the archive's pulp collection is a repository assembled by many contributors, not a designed "
        "corpus, so the history of its transmission and the shape of the sample are reported before anything is measured on it. "
        "Every count here comes from the archive's own metadata (the survey; no page or text is downloaded for it): the search "
        "index for every item, and each item's own record for who uploaded it, when, under which curator, and with which OCR engine. "
        "The sample is then assessed by decade, genre, magazine, publisher and language; authors come from the issues processed so far."),
        "<h1>The collection and the sample</h1>",
        f"<p class='muted'>Survey of {_esc(sv.get('generated'))}, collection <code>{_esc(sv.get('collection'))}</code>, {_n(total)} items; "
        f"provenance records fetched for {_n(pv.get('items_enriched', 0))} of them"
        + (" (the enrichment is still running)" if pv.get("items_enriched", 0) < total * 0.98 else "") + ". "
        "Files: <a href='/raw/file?path=survey/summary.json'>summary.json</a>, <a href='/raw/file?path=survey/magazines.json'>magazines.json</a>, "
        "<a href='/raw/file?path=survey/items.jsonl'>items.jsonl</a>, <a href='/raw/file?path=survey/enrich.jsonl'>enrich.jsonl</a>.</p>"]
    # 1. what it holds
    out.append("<h2>1. What the collection holds, and how far the era is read</h2>")
    if fr.get("have"):
        n_dl = EX.n_downloaded(con)
        n_c = EX._val(con, "SELECT COUNT(*) FROM issues WHERE complete=1") or 0
        n_fv = EX._val(con, "SELECT COUNT(*) FROM issues WHERE stories>0 AND verified=stories") or 0
        out.append(EX.year_strip_html(con))
        out.append(EX.collection_bar(fr, n_dl, n_c, n_fv))
        out.append("<h3>The era year by year</h3><p class='fine'>Items the archive holds for each year of the era, and what this site has read of them; "
                   "a year opens its issues.</p>" + EX.year_grid_html(con))
    out.append("<div class='row' style='display:flex;gap:24px;flex-wrap:wrap'>"
               "<div><h3>By language</h3>"
               + _kv_table(sv.get("by_language_class", {}), "Language field", "Items", total=total)
               + "<p class='muted' style='max-width:340px'>“unmarked” = no language given; taken as English in the working corpus, and checked below against the language the archive's OCR detected.</p></div>"
               "<div><h3>By kind (the archive's sub-collections)</h3>"
               + _kv_table(sv.get("by_kind", {}), "Kind", "Items", total=total) + "</div>"
               "<div><h3>The working corpus</h3>"
               + _kv_table(w.get("by_kind", {}), "Kind (English or unmarked)", "Items", total=w.get("items"))
               + f"<p class='muted' style='max-width:340px'>{_n(w.get('items'))} items; the fiction magazines among them, {_n(w.get('fiction_items'))}, are the frame of the study.</p></div></div>")
    # 2. history of transmission
    out.append("<h2>2. The history of transmission</h2>")
    out.append("<p>The collection was created in 2011 and has grown by contributions since; the year each item was added, "
               "the accounts that added them, the archive's curators who admitted them, and the sub-collections they were filed "
               "under are the record of that transmission. The OCR engine named in each item's record is the provenance of the "
               "archive's own text, which the protocol replaces with a new reading.</p>")
    aby = pv.get("added_by_year", {})
    fby = pv.get("fiction_added_by_year", {})
    if aby:
        out.append(_RP()._chart_row(_bars(aby, "Items added to the archive, by year"),
                                    _kv_table({y: f"{_n(v)} ({_n(fby.get(y, 0))} fiction)" for y, v in aby.items()}, "Year added", "Items (fiction magazines)")))
    ups = pv.get("uploaders_top", [])
    if ups:
        rows = "".join(f"<tr><td>{_esc(u['handle'])}</td><td class='num'>{_n(u['items'])}</td><td class='num'>{_n(u['fiction_items'])}</td>"
                       f"<td>{_esc('–'.join(str(x) for x in u['years']) if u.get('years') else '')}</td></tr>" for u in ups)
        out.append(f"<h3>Uploader accounts ({_n(pv.get('uploader_accounts'))} distinct among the records fetched)</h3>"
                   "<table><tr><th>Account</th><th class='num'>Items</th><th class='num'>Fiction magazines</th><th>Years active</th></tr>"
                   + rows + "</table><p class='muted'>The part of the address before the @, as the archive shows it on every item page; "
                   "the full addresses stay in the data file.</p>")
    cols = "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
    if pv.get("curators"):
        cols += "<div><h3>Curators (the archive's admission record)</h3>" + _kv_table(pv["curators"], "Curator", "Items") + "</div>"
    if pv.get("collection_added"):
        cols += "<div><h3>Filed under</h3>" + _kv_table(pv["collection_added"], "Sub-collection", "Items") + "</div>"
    if pv.get("scan_tags_fiction"):
        cols += ("<div><h3>Scanning-group tags in the titles</h3>"
                 + _kv_table(pv["scan_tags_fiction"], "Tag", "Fiction items", limit=20)
                 + "<p class='muted' style='max-width:340px'>The pulp-scanning community signs its scans in the item title — “(Darwin-IA)”, “(Gorgon776)”, “(cape1736)” — which is the nearest thing to a source field.</p></div>")
    cols += "</div>"
    out.append(cols)
    if pv.get("ocr_engines_fiction"):
        by_dec = pv.get("ocr_engine_by_upload_decade", {})
        rows = "".join(f"<tr><td>{_esc(d)}</td><td>" + ", ".join(f"{_esc(e)} {_n(n)}" for e, n in c.items()) + "</td></tr>" for d, c in by_dec.items())
        out.append("<h3>The archive's own OCR: the engine named in each record</h3>"
                   "<div style='display:flex;gap:24px;flex-wrap:wrap'><div>"
                   + _kv_table(pv["ocr_engines_fiction"], "Engine (fiction magazines)", "Items", total=pv.get("items_enriched"))
                   + "</div><div><table><tr><th>Decade uploaded</th><th>Engines</th></tr>" + rows + "</table></div></div>"
                   "<p class='muted'>Different engines and versions across the years of upload are why the protocol re-reads every page with one pipeline "
                   "(section 2: “existing OCR is also uneven across a collection assembled from scans produced at different times and by different contributors”).</p>")
    if pv.get("detected_language_of_unmarked"):
        out.append("<h3>Unmarked items: the language the archive's OCR detected</h3>"
                   + _kv_table(pv["detected_language_of_unmarked"], "Detected language", "Unmarked items")
                   + "<p class='muted'>A check on the working-corpus rule that an item with no language given is English.</p>")
    if pv.get("rights_fields"):
        out.append("<h3>Rights fields present</h3>" + _kv_table(pv["rights_fields"], "Field value", "Items"))
    # 3. the sample
    out.append("<h2>3. The sample: fiction magazines in the working corpus</h2>")
    fbd = w.get("fiction_by_decade", {})
    out.append(_RP()._chart_row(_bars({k: v for k, v in fbd.items() if k != "no year"}, "Fiction magazines by decade"),
                                _kv_table(fbd, "Decade", "Items", total=w.get("fiction_items"))))
    out.append(_RP()._chart_row(_bars(w.get("fiction_by_genre", {}), "Fiction magazines by genre (the archive's filing)"),
                                _kv_table(w.get("fiction_by_genre", {}), "Genre", "Items", total=w.get("fiction_items"))))
    dg = w.get("fiction_by_decade_genre", {})
    if dg:
        genres = list(w.get("fiction_by_genre", {}).keys())
        head = "<tr><th>Decade</th>" + "".join(f"<th class='num'>{_esc(g)}</th>" for g in genres) + "</tr>"
        rows = "".join(f"<tr><td>{_esc(d)}</td>" + "".join(f"<td class='num'>{_n(c.get(g, 0)) if c.get(g) else ''}</td>" for g in genres) + "</tr>"
                       for d, c in dg.items())
        out.append("<h3>By decade and genre</h3><div style='overflow-x:auto'><table>" + head + rows + "</table></div>")
    mags = w.get("fiction_magazines_top", [])
    if mags:
        rows = "".join(f"<tr><td>{_esc(m['name'])}</td><td class='num'>{_n(m['items'])}</td><td>{_esc('–'.join(str(x) for x in m['years']) if m.get('years') else '')}</td>"
                       f"<td>{_esc(m.get('genre') or '')}</td></tr>" for m in mags[:40])
        out.append(f"<h3>By magazine title ({_n(w.get('fiction_magazines'))} names; the forty with the most items)</h3>"
                   "<table><tr><th>Magazine</th><th class='num'>Items</th><th>Years</th><th>Genre</th></tr>" + rows + "</table>"
                   "<p class='muted'>Names as the survey reads them from the item titles; one magazine can appear under two names "
                   "(“Astounding” and “Astounding Science Fiction”). Full list: magazines.json.</p>")
    fbp = w.get("fiction_by_publisher", {})
    if fbp:
        assigned = sum(v for k, v in fbp.items() if k != "not assigned")
        bdp = w.get("fiction_by_decade_publisher", {})
        rows = "".join(f"<tr><td>{_esc(d)}</td><td>" + ", ".join(f"{_esc(p)} {_n(n)}" for p, n in c.items()) + "</td></tr>" for d, c in bdp.items())
        out.append("<h3>By publisher</h3>"
                   "<div style='display:flex;gap:24px;flex-wrap:wrap'><div>" + _kv_table(fbp, "Publisher group", "Items", total=w.get("fiction_items"), limit=25)
                   + "</div><div><table><tr><th>Decade</th><th>Publishers</th></tr>" + rows + "</table></div></div>"
                   f"<p class='muted'>Publisher assigned to {_n(assigned)} of {_n(w.get('fiction_items'))} items ({_pct(assigned, w.get('fiction_items') or 1)}) from a "
                   "reference table of magazine and period (config/publishers_magazines.json), to be confirmed against the mastheads as issues are processed; "
                   "the pilot's issues carry their confirmed publishers (pipeline/publishers.json).</p>")
    # 4. authors from the processed issues
    n_au = EX._val(con, "SELECT COUNT(*) FROM authors") or 0
    n_st = EX._val(con, "SELECT COUNT(*) FROM records WHERE is_story=1") or 0
    n_signed = EX._val(con, "SELECT COUNT(*) FROM records WHERE is_story=1 AND author IS NOT NULL AND author!=''") or 0
    top = EX._rows(con, "SELECT display, slug, n_stories, n_words, first_year, last_year FROM authors ORDER BY n_stories DESC, n_words DESC LIMIT 12")
    rows = "".join(f"<tr><td><a href='/author/{_esc(a['slug'])}'>{_esc(a['display'])}</a></td><td class='num'>{_n(a['n_stories'])}</td>"
                   f"<td class='num'>{_n(a['n_words'])}</td><td>{_esc(_EX()._fmt(a['first_year']))}–{_esc(_EX()._fmt(a['last_year']))}</td></tr>" for a in top)
    out.append("<h2>4. Authors, from the issues processed so far</h2>"
               f"<p>{_n(n_au)} author names over {_n(n_st)} story records, {_n(n_signed)} of them with a by-line "
               f"({_pct(n_signed, n_st)}). By-lines are stored as printed and compared in a normalized form; pseudonyms and house "
               "names are not resolved, and a missing by-line is its own state (“unknown”) in the pair table. The whole list: <a href='/authors'>authors</a>.</p>"
               "<table><tr><th>Author</th><th class='num'>Stories</th><th class='num'>Words</th><th>Years</th></tr>" + rows + "</table>")
    return _render(render, "The collection and the sample", out, "/collection")


# ---------------------------------------------------------------- /corpus

def corpus_page(qs, render=None):
    EX = _EX()
    con = EX.db()
    D = _G["DATA"]
    try:
        cs = json.load(open(os.path.join(D, "export", "corpus_stats.json"), encoding="utf-8"))
    except Exception:
        cs = {}
    by_type = EX._rows(con, "SELECT type, COUNT(*) n, SUM(n_words) w, SUM(CASE WHEN status='verified' THEN 1 ELSE 0 END) v FROM records GROUP BY type ORDER BY n DESC")
    story_rows = [r for r in by_type if r["type"] == "story"]
    n_story = sum(r["n"] for r in story_rows)
    w_story = sum(r["w"] or 0 for r in story_rows)
    n_par = sum(r["n"] for r in by_type if r["type"] != "story")
    w_par = sum(r["w"] or 0 for r in by_type if r["type"] != "story")
    n_story50 = EX._val(con, "SELECT COUNT(*) FROM records WHERE type='story' AND n_words>=50") or 0
    ad_cls = EX._rows(con, "SELECT COALESCE(ad_class, '(none)') c, COUNT(*) n FROM records WHERE type IN ('ad','house') GROUP BY c ORDER BY n DESC")
    by_mag = EX._rows(con, "SELECT magazine, SUM(CASE WHEN type='story' THEN 1 ELSE 0 END) s, SUM(CASE WHEN type='story' THEN n_words ELSE 0 END) sw, "
                           "SUM(CASE WHEN type!='story' THEN 1 ELSE 0 END) p FROM records GROUP BY magazine ORDER BY s DESC")
    out = [_howto(
        "Protocol section 2 names two corpora: the story-level corpus, in which every work is linked to its author and its issue, "
        "and a parallel corpus of advertisements, tables of contents, editorials and other paratexts, separated from it. Both are "
        "built from the same records — every printed unit of every issue, typed on the workbench — and written by the export "
        "(pipeline/r00_export_stories.py) as two files. The reuse stages read the story-level corpus only; the parallel corpus is "
        "kept for the study of the magazines themselves and for the checks (a house announcement that quotes a story, for instance)."),
        "<h1>The two corpora</h1>",
        (f"<p class='muted'>Export of {_esc(cs.get('generated'))}: <a href='/raw/file?path=export/stories.jsonl'>stories.jsonl</a> "
         f"({_n(cs.get('story_level_records'))} records, {_n(cs.get('story_level_words'))} words) · "
         f"<a href='/raw/file?path=export/paratext.jsonl'>paratext.jsonl</a> ({_n(cs.get('parallel_records'))} records, {_n(cs.get('parallel_words'))} words; "
         f"{_n(cs.get('story_fragments_in_parallel'))} story records under {cs.get('min_story_words')} words among them) · "
         "<a href='/raw/file?path=export/corpus_stats.json'>corpus_stats.json</a> · the combined file <a href='/raw/file?path=pilot_stories.jsonl'>pilot_stories.jsonl</a>.</p>"
         if cs else "<p class='muted'>The export has not written the two files yet (run pipeline/r00_export_stories.py).</p>"),
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        "<div style='flex:1 1 320px;background:var(--surface);border:1px solid var(--grid);padding:10px 14px'>"
        f"<h2 style='margin-top:0'>Story-level corpus</h2><p><b>{_n(n_story)}</b> story records ({_n(n_story50)} of fifty words or more), <b>{_n(w_story)}</b> words; "
        f"{_n(sum(r['v'] for r in story_rows))} verified. A story record is one work as printed in one issue — a serial instalment is a story with serial fields, "
        "and instalments of one work are linked across issues. Its reading text is the body and the chapter apparatus; the title, "
        "by-line, teaser, synopsis, credits and captions are fields.</p>"
        "<p class='muted'>Enters: exact reuse (r02), paraphrase (r04), the pair table (r05). Records under fifty words are fragments and go to the parallel corpus.</p></div>"
        "<div style='flex:1 1 320px;background:var(--surface);border:1px solid var(--grid);padding:10px 14px'>"
        f"<h2 style='margin-top:0'>Parallel corpus</h2><p><b>{_n(n_par)}</b> records, <b>{_n(w_par)}</b> words: "
        + ", ".join(f"{_esc(r['type'])} {_n(r['n'])}" for r in by_type if r["type"] != "story")
        + ".</p><p class='muted'>Advertisements by class: " + ", ".join(f"{_esc(r['c'])} {_n(r['n'])}" for r in ad_cls)
        + ". House matter (the publisher's own announcements, excerpts and forms) is typed house; an announcement that quotes a story "
        "is linked to it (excerpt_of) and stays out of the reuse inventory.</p></div></div>",
        "<h2>By magazine</h2>",
        EX._table(["Magazine", "Story records", "Story words", "Parallel records"],
                  [[_esc(r["magazine"] or ""), EX.N(r["s"]), EX.N(r["sw"]), EX.N(r["p"])] for r in by_mag]),
        "<h2>By type</h2>",
        EX._table(["Type", "Records", "Words", "Verified"], [[_esc(r["type"]), EX.N(r["n"]), EX.N(r["w"] or 0), EX.N(r["v"])] for r in by_type]),
        "<p class='muted'>Type vocabulary: story, poem, feature, letters, house, ad, toc, other — decided on the workbench, replayed over the machine's draft. "
        "Records: <a href='/stories'>the record list</a> with filters by type.</p>"]
    return _render(render, "The two corpora", out, "/corpus")


# ---------------------------------------------------------------- /datasheet

def datasheet_page(qs, render=None):
    sv = _survey()
    w = sv.get("working_corpus", {}) or {}
    pv = sv.get("provenance", {}) or {}
    EX = _EX()
    con = EX.db()
    D = _G["DATA"]
    try:
        cs = json.load(open(os.path.join(D, "export", "corpus_stats.json"), encoding="utf-8"))
    except Exception:
        cs = {}
    n_issues = EX._val(con, "SELECT COUNT(*) FROM issues WHERE complete=1") or 0
    n_rec = EX._val(con, "SELECT COUNT(*) FROM records") or 0
    n_story = EX._val(con, "SELECT COUNT(*) FROM records WHERE type='story'") or 0
    n_words = EX._val(con, "SELECT SUM(n_words) FROM records WHERE type='story'") or 0
    n_ver = EX._val(con, "SELECT COUNT(*) FROM records WHERE status='verified'") or 0
    n_au = EX._val(con, "SELECT COUNT(*) FROM authors") or 0
    mags = EX._rows(con, "SELECT name, n_issues, first_year, last_year FROM magazines ORDER BY n_issues DESC")
    decades = w.get("fiction_by_decade", {})
    genres = w.get("fiction_by_genre", {})
    engines = pv.get("ocr_engines_fiction", {})
    ups = pv.get("uploaders_top", [])
    aby = pv.get("added_by_year", {})

    def sec(title, qas):
        return f"<h2>{_esc(title)}</h2>" + "".join(f"<p><b>{_esc(q)}</b> {a}</p>" for q, a in qas)
    pending = "<span class='muted'>(pending: decided at release)</span>"
    out = [_howto(
        "A datasheet for the corpus in the form proposed by Gebru et al. (motivation, composition, collection process, preprocessing, "
        "uses, distribution, maintenance) with the elements of a data statement in the sense of Bender and Friedman (curation rationale, "
        "language variety, speaker and annotator demographics, speech situation, text characteristics) — the protocol's references 12 and 13. "
        "The figures are generated from the survey of the archive's collection and from the corpus as it stands on this server; "
        "they update as the corpus grows. What the protocol leaves for the release is marked pending."),
        "<h1>Datasheet for the Pulp Fiction Corpus</h1>",
        f"<p class='muted'>Generated {_esc(sv.get('generated') or '')} (survey) · corpus state of this page's load · "
        "<a href='/collection'>the collection and the sample</a> · <a href='/corpus'>the two corpora</a> · <a href='/method'>the method</a>.</p>"]
    out.append(sec("Motivation", [
        ("For what purpose was the dataset created?", "To measure how often and how unusually pulp fiction texts repeat one another — verbatim and in paraphrase — against "
         "an empirical background of the recurrence expected between comparable works (the Stage 1 protocol, sections 1 and 3–4), and to recover literary genealogies from the exceptional cases."),
        ("Who created it?", "The Digital Humanities Engineering Center, Kyungpook National University (Heejin Kim, Sujin Kang) with the Narrative Intelligence Lab, Columbia University (Dennis Yi Tenen)."),
        ("Who funded it?", pending),
        ("Curation rationale.", "The corpus is drawn from the Internet Archive's Pulp Magazine Collection, a repository assembled by many contributors rather than a designed sample; the working corpus keeps items in English or with no language given, and the study frame is the fiction magazines among them (pulps and digests) — dime novels, comic magazines, and film and general-interest magazines held in the same collection are excluded."),
    ]))
    out.append(sec("Composition", [
        ("What do the instances represent?", "Records: every separately printed unit of a magazine issue (story, poem, feature, letters page, house matter, advertisement, contents page), with its title and by-line as printed, its page range, its scan regions, and its reading text. The story-level corpus is the story records of fifty words or more; every other record forms the parallel corpus."),
        ("How many instances are there?", f"The collection: {_n(sv.get('total_items'))} items, of which {_n(w.get('items'))} in the working corpus and {_n(w.get('fiction_items'))} fiction magazines"
         f" ({_n(w.get('fiction_magazines'))} magazine names). Processed so far: {_n(n_issues)} issues, {_n(n_rec)} records, {_n(n_story)} story records with {_n(n_words)} words, {_n(n_ver)} records verified by people, {_n(n_au)} author names."
         + (f" The two corpora as exported: {_n(cs.get('story_level_records'))} story-level records ({_n(cs.get('story_level_words'))} words) and {_n(cs.get('parallel_records'))} parallel records ({_n(cs.get('parallel_words'))} words)." if cs else "")),
        ("Is it a sample of a larger set?", "Yes: the working corpus is the English-language fiction-magazine part of the archive's collection, which is itself a sample of the pulp magazines printed — one shaped by what collectors scanned and uploaded. The distribution by decade, genre, magazine and publisher is on the collection page: by decade "
         + ", ".join(f"{_esc(k)} {_n(v)}" for k, v in decades.items()) + "; by genre " + ", ".join(f"{_esc(k)} {_n(v)}" for k, v in genres.items()) + "."),
        ("What data does each instance consist of?", "The reading text, the fields (title, subtitle, by-line, teaser, synopsis, credits, illustrator, department, serial part), the type and the advertisement class, the page range and the scan regions (page number and box coordinates), the issue metadata (magazine, cover date, publisher, genre, format, the archive's item identifier), and the annotation status (automatic, modified, verified) with the names of who changed it."),
        ("Is there a label or target?", "The type of each record and, for the paraphrase detector's validation set, the readers' judgments (paraphrase / not / unsure), each with the reader's name and time."),
        ("Is any information missing?", "Text the layout reading could not recover (words drawn inside pictures, blocks the detector missed) is absent and, where a person noticed it, flagged. Author by-lines are missing from records the magazine printed unsigned; pseudonyms and house names are not resolved."),
        ("Are relationships between instances made explicit?", "Yes: instalments of one serial are linked across issues (work id, previous and next); a house announcement that quotes a story is linked to it; the exact and paraphrastic passages shared by two records, and the pair table of section 4.1, are derived data released with the corpus."),
        ("Recommended data splits.", "A development set of ten issues (config/pilot_issues.json) on which every stage was built and tuned; the confirmatory corpus excludes it."),
        ("Errors, noise, redundancies.", "The reading text carries the residual OCR error of the layout pipeline (measured on the control set with proofread transcriptions; the error rate and its composition are on the method page); a record's regions are exactly one printed unit's when verified, and the machine's reading otherwise. Reprints within the collection (a story printed twice, a reprint department) are real redundancies of the material, kept and identified by the reuse stages."),
        ("Language variety.", "American English of the pulp magazines, 1890s–1950s mainly; some British magazines. Speaker demographics (the authors): professional and semi-professional magazine writers; by-lines as printed, unresolved."),
    ]))
    out.append(sec("Collection process", [
        ("How was the data acquired?", "Metadata for every item through the archive's search API and each item's own metadata record (the survey; no page or text); then, for the issues selected, the page images, the archive's own OCR text as a baseline, and the item metadata, fetched sequentially at a rate courteous to the archive with a contact address in the user agent, every fetch logged to a manifest. The downloader refuses to run for issues not on the approved list."),
        ("Who was involved and how were they compensated?", "The project's own members; the archive's contributors are the uploaders of the scans (" + (f"{_n(pv.get('uploader_accounts'))} accounts among the records fetched; the largest: " + ", ".join(f"{_esc(u['handle'])} ({_n(u['items'])})" for u in ups[:5]) if ups else "not yet enumerated") + ")."),
        ("Over what time frame?", (("The collection was created in 2011 and has grown since (items added by year: " + ", ".join(f"{_esc(y)} {_n(n)}" for y, n in aby.items()) + ").") if aby else "The collection was created in 2011 and has grown since.") + " The corpus is built after the protocol's acceptance; the ten development issues were processed before it."),
        ("Ethical review.", "Not applicable: published magazines of the 1890s–1950s; no personal data beyond names printed as by-lines. The uploaders' addresses in the archive's records are kept in the data file, not published."),
    ]))
    out.append(sec("Preprocessing, cleaning, labeling", [
        ("Was any preprocessing done?", "Layout detection and line-level recognition of every page (each token keeps its page and box); deterministic normalization only, every change logged; no generative correction. Records are assembled from the contents page and the printed conventions (by-lines, chapter heads, running heads, continued notices, advertisement pages) by a rules engine, every region in exactly one record or in page furniture; people verify records on a web workbench whose every action is one line of an append-only log; a verified record is never altered by a later machine run."),
        ("Was the raw data saved?", "Yes: the page images, the archive's OCR, the layout and line reading of every page, and the machine's assembly are kept beside the corpus; the annotation logs replay over the machine's draft, so the original is always recoverable."),
        ("Annotator demographics and guidelines.", "Named members of the project (literature scholars); the workbench guide fixes the conventions (what is story text, what is page furniture, the type vocabulary). Agreement between annotators is measured on the double-annotated part of the verification sample " + pending + "."),
        ("The archive's own text.", ("The engine named in each item's record — " + ", ".join(f"{_esc(e)} {_n(n)}" for e, n in list(engines.items())[:5]) + " — is the provenance of the baseline OCR; the corpus text is the project's own reading.") if engines else "Uneven across contributors and years; the corpus text is the project's own reading."),
    ]))
    out.append(sec("Uses", [
        ("Has the dataset been used for any tasks already?", "The text-reuse rehearsal on the development set (the reuse pages of this site)."),
        ("What other tasks could it be used for?", "The history of magazine fiction by author, magazine, genre and publisher; the study of advertising and editorial matter (the parallel corpus); OCR and layout research on damaged print."),
        ("Are there tasks for which it should not be used?", "The reading text is not a critical edition: it carries recognition errors and the conventions of the assembly; quotation should go back to the scan, to which every sentence is anchored."),
    ]))
    out.append(sec("Distribution", [
        ("Will the dataset be distributed to third parties?", "The protocol plans a public release of the story-level corpus with its page and region provenance for items out of copyright, the derived data (reuse inventories, the sampled pair table) for the whole corpus, the annotation logs, and the code. Until then the corpus is restricted to the project."),
        ("Licence and copyright.", "Works published in the United States before 1931 are in the public domain (as of 2026); many later pulp texts are as well through non-renewal, item by item; the rest are distributed as derived data only. Licence of the release " + pending + "."),
        ("When will it be distributed?", pending),
    ]))
    out.append(sec("Maintenance", [
        ("Who maintains it and how can they be contacted?", f"The Digital Humanities Engineering Center, Kyungpook National University; {_esc(_G.get('CONTACT') or '')}."),
        ("Will it be updated?", "The corpus grows as issues are processed and verified; every release is versioned, and the survey of the collection is rerun at acceptance and at release (the archive's collection grows by a few hundred items a year)."),
        ("Erratum and correction.", "Corrections are made on the workbench and recorded in the annotation logs; a verified record changes only by a person's action."),
    ]))
    out.append("<h2>Magazines processed so far</h2>" + EX._table(["Magazine", "Issues", "Years"],
                                                                [[_esc(m["name"]), EX.N(m["n_issues"]), f"{_esc(EX._fmt(m['first_year']))}–{_esc(EX._fmt(m['last_year']))}"] for m in mags]))
    return _render(render, "Datasheet", out, "/datasheet")
