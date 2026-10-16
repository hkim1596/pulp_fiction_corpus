"""Text reuse over the whole corpus — the engineering dry run (site v0.23.0, p50w; docs/reuse-at-scale-plan-2026-10-14.md).

  /reuse/corpus                           what the corpus-scale stages found (data/reuse/corpus_dryrun/): exact reuse
                                          per seed, near-verbatim reuse per K when r04 has run, the background when
                                          r05 has run, the duplicate items when scripts/duplicate_items.py has run
  /reuse/corpus/clusters?kind=&k=&page=   the clusters, paged (the lists at corpus scale run to a hundred thousand)
  /reuse/corpus/cluster/<kind>/<k>/<i>    one cluster, witnesses side by side (read from the .jsonl by its byte offset)
  /reuse/corpus/pairs?k=&page=            the story pairs with the most exact reuse (the 20,000 longest)
  /reuse/corpus/duplicates                issues the archive holds twice, from the pair table

Every page carries the dry-run line: these are the stages run on the corpus as it stands, to prove the code and show
the material; the study's numbers come from the frozen corpus after the protocol's acceptance. Bound to the site's
helpers with bind(globals()) from app.py; the pilot's reuse pages (reuse_pages.py) are untouched.
"""
import json
import os
import sys
import time
import urllib.parse
from collections import defaultdict

_G = {}
_CACHE = {}
PAGE = 100
KINDS = {"exact": ("machine_k{k}", [6, 7, 8], "words"), "para": ("para/machine_w50s25_k{k}", [5, 10, 20], "columns")}


def bind(g):
    _G.update(g)
    p = os.path.join(_G["ROOT"], "pipeline")
    if p not in sys.path:
        sys.path.insert(0, p)


def _esc(s):
    return _G["esc"](s)


def _snap():
    return os.path.join(_G["DATA"], "reuse", "corpus_dryrun")


def _label():
    return ("<p class='fine' style='border-left:3px solid var(--accent);padding:4px 10px'>Engineering dry run of 14 October 2026 onward; "
            "not the study's numbers. The stages run on the corpus as it stands, to prove the code and show the material; the study's run "
            "repeats them on the frozen corpus after the protocol's acceptance (manuscript CHR-2026-0073).</p>")


def _load(rel, kind="json"):
    """A file under data/reuse/corpus_dryrun, cached by modification time (None when absent)."""
    p = os.path.join(_snap(), rel)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    hit = _CACHE.get(p)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        if kind == "json":
            val = json.load(open(p, encoding="utf-8"))
        else:
            val = open(p, encoding="utf-8").read()
    except Exception:
        val = None
    _CACHE[p] = (mt, val)
    return val


def _stories():
    """story id -> title, author, issue, magazine, cover date, from the snapshot's story list."""
    p = os.path.join(_snap(), "tokens", "stories.jsonl")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    hit = _CACHE.get(p)
    if hit and hit[0] == mt:
        return hit[1]
    out = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                s = json.loads(line)
                m = s.get("meta") or {}
                out[s["story_id"]] = {"title": m.get("title"), "author": m.get("author"), "issue": s["issue"], "magazine": m.get("magazine"),
                                      "cover_date": m.get("cover_date"), "n_tokens": s.get("n_tokens")}
    except Exception:
        out = {}
    _CACHE[p] = (mt, out)
    return out


def _name(sid, st=None):
    st = st if st is not None else _stories()
    a = st.get(sid)
    if not a:
        return sid
    t = a.get("title") or "(untitled)"
    if len(t) > 70:
        t = t[:67].rstrip() + "…"
    au = a.get("author")
    if au and len(au) > 40:
        au = au[:37].rstrip() + "…"
    return t + (f" — {au}" if au else "")


def _issue_label(iid, st=None):
    info = _G["issue_by_id"](iid) or {}
    return f"{info.get('magazine', iid)} {info.get('cover_date', '')}".strip()


def _render(render, title, body, path):
    html = "".join(body) if isinstance(body, list) else body
    return render(title, html, path) if render else html


def _N(v):
    return _G["EX"].N(v)


def _table(headers, rows):
    return _G["EX"]._table(headers, rows)


def _pageno(qs, name="page"):
    try:
        return max(1, int((qs.get(name) or ["1"])[0]))
    except ValueError:
        return 1


def _pager(base, page, n, per=PAGE):
    last = max(1, -(-n // per))
    parts = []
    if page > 1:
        parts.append(f"<a href='{base}&page={page - 1}'>previous</a>")
    parts.append(f"page {page} of {last:,}")
    if page < last:
        parts.append(f"<a href='{base}&page={page + 1}'>next</a>")
    return "<p class='fine'>" + " · ".join(parts) + "</p>"


# ---------------------------------------------------------------- overview

def overview_note():
    """One line for the pilot's /reuse page when the corpus dry run has files."""
    if not os.path.isdir(_snap()):
        return ""
    return ("<p class='muted'>The same stages over the whole corpus (an engineering dry run, labelled as such): "
            "<a href='/reuse/corpus'>text reuse in the corpus</a>.</p>")


def overview(render=None):
    RP = _G["RP"]
    snap = _snap()
    body = [RP._howto(
        "The text-reuse stages rebuilt for the whole corpus (pipeline/r02_verbatim_corpus.py, r04_paraphrase_corpus.py, "
        "r05_background_corpus.py): the pilot's method step for step, with engines that fit sixty thousand stories. Every number "
        "names its file under data/reuse/corpus_dryrun/. The matches of a few words are stock phrases; the clusters and the text "
        "files hold the matches of ten words or more (the stats say how many lie under that floor), and every match is kept in "
        "the arrays."), "<h1>Text reuse in the corpus</h1>", _label()]
    meta = _load("tokens/meta.json")
    if not meta:
        body.append("<div class='empty'>Nothing yet: the corpus-scale stages have not written their files on this server.</div>")
        return _render(render, "Text reuse in the corpus", body, "/reuse/corpus")
    body.append(f"<p class='muted'>The story set: {meta['stories']:,} stories (type story, {meta['min_tokens']} tokens or more) of "
                f"{meta['issues']:,} issues, {meta['tokens']:,} tokens, {meta['vocabulary']:,} distinct; snapshot of {_esc(meta['generated'])} "
                f"from {meta['files']:,} export files. Browse: <a href='/reuse/corpus/clusters?kind=exact&k=6'>clusters</a> · "
                f"<a href='/reuse/corpus/pairs?k=6'>story pairs</a> · <a href='/reuse/corpus/duplicates'>duplicate items</a>.</p>")
    # exact
    body.append("<h2>1. Exact reuse (r02 at corpus scale)</h2>")
    rows, cats, series = [], [], []
    for k in KINDS["exact"][1]:
        st = _load(f"machine_k{k}_stats.json")
        if not st:
            continue
        rows.append([_N(k), _N(st["matches"]), _N(st["same_issue_matches"]), _N(st["skipped_commonplace_shingles"]), _N(st["pairs_with_reuse"]),
                     _N(st["stories_with_reuse"]), _N(st["matches_written"]), _N(st["clusters"]), _N(st["clusters_3plus_witnesses"]),
                     _N(st["longest_match"]), _N(f"{st['seconds_total']:.0f}")])
        series.append((f"seed {k}", [st["length_hist"].get(n, 0) for n in ("seed-9", "10-19", "20-49", "50-99", "100-499", "500+")]))
    cats = ["6-9", "10-19", "20-49", "50-99", "100-499", "500+"]
    if rows:
        body.append(_table(["seed", "#cross-issue matches", "#same-issue", "#commonplace skipped", "#story pairs", "#stories with a match",
                            "#matches of 10+ tokens", "#clusters (10+)", "#3+ witnesses", "#longest (tokens)", "#seconds"], rows))
        body.append(RP._chart_row(RP.svg_bars(cats, series, "Cross-issue matches by length (tokens)"),
                                  _table(["length"] + [s[0] for s in series], [[_esc(c)] + [_N(s[1][i]) for s in series] for i, c in enumerate(cats)])))
        body.append("<p class='fine'>Files: machine_k&lt;k&gt;_stats.json, _matches.npy (every match), _matches.jsonl and _clusters.json (ten tokens or "
                    "more), _pairs.npz (every story pair with a match), _skipped_shingles.json (the commonplace shingles, in more than 50 stories).</p>")
    else:
        body.append("<div class='empty'>No exact-reuse pass yet.</div>")
    # near-verbatim
    body.append("<h2>2. Near-verbatim and rewritten reuse (r04 at corpus scale)</h2>")
    rows = []
    for k in KINDS["para"][1]:
        st = _load(f"para/machine_w50s25_k{k}_stats.json")
        if not st:
            continue
        rows.append([_N(k), _N(st["alignments"]), _N(st["same_issue_alignments"]), _N(st["pairs_with_paraphrase"]), _N(st["stories_with_paraphrase"]),
                     _N(st["alignments_written"]), _N(st["clusters"]), _N(st["longest_alignment"]),
                     _esc(", ".join(f"{a} {b:,}" for a, b in sorted(st.get("by_source", {}).items())))])
    if rows:
        em = _load("para/emb_meta.json") or {}
        kn = _load("knn_meta.json") or _load("para/knn_meta.json") or {}
        body.append(f"<p class='muted'>Passages of 50 words at a step of 25, embedded with {_esc(em.get('model', ''))} ({_esc(em.get('backend', ''))}); "
                    f"the {kn.get('k', 20)} nearest passages in other stories by {_esc(kn.get('backend', ''))}{' (approximate)' if kn.get('approximate') else ' (exact)'}; "
                    f"every exact match a candidate too; alignments kept at 20 columns and identity 0.60.</p>")
        body.append(_table(["K", "#cross-issue alignments", "#same-issue", "#story pairs", "#stories", f"#written (40+ columns)", "#clusters", "#longest (columns)", "by source"], rows))
    else:
        em = _load("para/emb_meta.json")
        body.append("<div class='empty'>Not run yet" + (f": the embeddings are being computed ({em.get('n', 0):,} passages)." if em and not em.get("done") else ".") + "</div>")
    # background
    body.append("<h2>3. The background (r05 at corpus scale)</h2>")
    sm = _load("background/summary_machine.json")
    if sm:
        bg = sm.get("background", {})
        body.append(f"<p class='muted'>{sm['rows']:,} rows: every pair with a match ({sm['matched_rows']:,}) and a stratified sample of the pairs without "
                    f"one ({sm['sampled_rows_draw1']:,} in the first draw, {sm['sampled_rows_draw2']:,} in the second), standing for "
                    f"{sm['cross_issue_pairs_represented']:,} cross-issue pairs of dated stories. The share of pairs with any match is exact by design; "
                    f"the finer splits carry sampling error (the two draws' estimates are in the summary file).</p>")
        rows = []
        for k in (6, 7, 8):
            e = bg.get("exact", {}).get(f"k{k}")
            if e:
                rows.append([f"exact, seed {k}", _N(f"{100 * e['p_any']:.3f}%"), _esc(", ".join(f"{b}: {100 * v.get(k, 0):.2f}%" for b, v in sorted(e.get("by_years_band", {}).items()))),
                             _esc(", ".join(f"q{q}: {100 * v.get(k, 0):.2f}%" for q, v in sorted(e.get("by_topic_q", {}).items())))])
        for k in (5, 10, 20):
            pq = bg.get("paraphrase", {}).get(f"k{k}")
            if pq:
                rows.append([f"near-verbatim, K {k}", _N(f"{100 * pq['p_any']:.3f}%"), _esc(", ".join(f"{b}: {100 * v.get(20, 0):.2f}%" for b, v in sorted(pq.get("by_years_band", {}).items()))),
                             _esc(", ".join(f"q{q}: {100 * v.get(20, 0):.2f}%" for q, v in sorted(pq.get("by_topic_q", {}).items())))])
        body.append(_table(["measure", "#pairs with any match", "by years apart", "by topic quartile"], rows))
        tt = bg.get("time_table", {}).get("exact_k6", {})
        if tt:
            cells = sorted(tt.items())
            body.append("<h3>Pairs with a six-word match, by decade of the later story and years apart</h3>")
            body.append(_table(["decade | years apart", "#pairs", "#with a match (%)", "#mean longest, given a match"],
                               [[_esc(c), _N(v["n_pairs"]), _N(f"{100 * (v['p_any'] or 0):.3f}"), _N(v["mean_longest_given_any"] if v["mean_longest_given_any"] is not None else "-")] for c, v in cells]))
    else:
        body.append("<div class='empty'>Not run yet.</div>")
    # duplicates
    body.append("<h2>4. Issues the archive holds twice</h2>")
    dup = _load("duplicates/issue_pairs.json")
    if dup:
        s = dup["summary"]
        body.append(f"<p class='muted'>{s['same_text_story_pairs']:,} story pairs are the same text (the matches cover {s['cover']:.0%} of both and the longest "
                    f"has {s['min_len']}+ tokens); {s['issue_pairs']:,} issue pairs are joined by at least {s['share']:.0%} of the smaller issue's stories, "
                    f"involving {s['issues_involved']:,} issues: " + _esc("; ".join(f"{k} {v}" for k, v in s["by_kind"].items())) +
                    f". <a href='/reuse/corpus/duplicates'>The list.</a> These are resolved before any analysis counts them as reuse.</p>")
    else:
        body.append("<div class='empty'>Not computed yet (scripts/duplicate_items.py).</div>")
    return _render(render, "Text reuse in the corpus", body, "/reuse/corpus")


# ---------------------------------------------------------------- clusters

def _index(kind, k):
    pre, _, _ = KINDS[kind]
    return _load(pre.format(k=k) + "_clusters_index.json")


def clusters_page(qs, render=None):
    RP = _G["RP"]
    g = lambda n, d: (qs.get(n, [d]) or [d])[0]
    kind = g("kind", "exact")
    if kind not in KINDS:
        kind = "exact"
    pre, kopts, unit = KINDS[kind]
    try:
        k = int(g("k", str(kopts[0] if kind == "exact" else 10)))
    except ValueError:
        k = kopts[0]
    try:
        min_len, min_wit = int(g("min", "0")), int(g("wit", "2"))
    except ValueError:
        min_len, min_wit = 0, 2
    sort = g("sort", "wit")
    page = _pageno(qs)
    st = _stories()
    sel = lambda name, val, options: f"<select name='{name}'>" + "".join(
        f"<option value='{_esc(str(v))}'{' selected' if str(v) == str(val) else ''}>{_esc(str(v))}</option>" for v in options) + "</select>"
    form = (f"<form method='GET' action='/reuse/corpus/clusters' class='pgjump' style='display:flex;gap:12px;flex-wrap:wrap;align-items:center'>"
            f"<label>kind {sel('kind', kind, ['exact', 'para'])}</label><label>{'seed' if kind == 'exact' else 'K'} {sel('k', k, kopts)}</label>"
            f"<label>min length <input name='min' value='{min_len}' style='width:50px'></label>"
            f"<label>min witnesses <input name='wit' value='{min_wit}' style='width:44px'></label>"
            f"<label>sort by {sel('sort', sort, ['wit', 'len'])}</label><button>show</button></form>")
    body = [RP._howto("A cluster is one passage with all the stories it appears in (its witnesses). The list is paged; it holds the clusters "
                      "over the matches of ten tokens or more (exact) or forty columns or more (near-verbatim). Open a cluster to see every "
                      "witness side by side and jump to the article and the scan."),
            "<h1>Reuse clusters in the corpus</h1>", _label(), form]
    idx = _index(kind, k)
    if not idx:
        body.append("<div class='empty'>No clusters for this setting on this server.</div>")
        return _render(render, "Reuse clusters in the corpus", body, "/reuse/corpus/clusters")
    rows = [c for c in idx["clusters"] if c["witnesses"] >= min_wit and c["max_len"] >= min_len]
    if sort == "len":
        rows.sort(key=lambda c: (-c["max_len"], -c["witnesses"]))
    n = len(rows)
    rows = rows[(page - 1) * PAGE: page * PAGE]
    base = f"/reuse/corpus/clusters?kind={kind}&k={k}&min={min_len}&wit={min_wit}&sort={sort}"
    body.append(f"<h2>{n:,} of {idx['n']:,} clusters" + (f" · generated {_esc(idx.get('generated', ''))}" if idx.get("generated") else "") + "</h2>")
    body.append(_pager(base, page, n))
    tbl = []
    for c in rows:
        names = "; ".join(_name(s, st) for s in c.get("stories", [])[:6]) + (" …" if len(c.get("stories", [])) > 6 else "")
        tbl.append([f"<a href='/reuse/corpus/cluster/{kind}/{k}/{c['i']}'>{c['i']}</a>", _N(f"{c['witnesses']} ({c['witnesses_collapsed']})"), _N(c.get("issues") or ""),
                    _N(c["max_len"]), _esc(c["text"][:160]), f"<span class='muted' style='font-size:12.5px'>{_esc(names[:300])}</span>"])
    body.append(_table(["#", "#witnesses (collapsed)", "#issues", f"#longest ({unit})", "passage", "stories"], tbl))
    body.append(_pager(base, page, n))
    return _render(render, "Reuse clusters in the corpus", body, "/reuse/corpus/clusters")


def _cluster(kind, k, i):
    idx = _index(kind, k)
    if not idx or i < 0 or i >= idx["n"]:
        return None
    rec = idx["clusters"][i]
    pre, _, _ = KINDS[kind]
    p = os.path.join(_snap(), pre.format(k=k) + "_clusters.jsonl")
    try:
        with open(p, "rb") as f:
            f.seek(rec["off"])
            return json.loads(f.read(rec["n"]).decode("utf-8"))
    except Exception:
        return None


def cluster_page(kind, k, idx, render=None, user=None):
    RP = _G["RP"]
    if kind not in KINDS:
        return _render(render, "Cluster", "<h1>No such cluster</h1>", "/reuse/corpus/clusters")
    c = _cluster(kind, k, idx)
    if not c:
        return _render(render, "Cluster", "<h1>No such cluster</h1>", "/reuse/corpus/clusters")
    st = _stories()
    unit = KINDS[kind][2]
    body = [RP._howto("One passage and every story it appears in. Each witness shows the passage as it stands in that story's text "
                      "(the first 300 characters), with the words around it as the article reads now on the workbench when the passage "
                      "can be located there. “Open on workbench” highlights the scan region where the passage begins."),
            f"<h1>Cluster {idx} · {c['witnesses']} witnesses"
            f"{' (' + str(c.get('witnesses_collapsed')) + ' after collapsing duplicate records)' if c.get('witnesses_collapsed') not in (None, c['witnesses']) else ''}"
            f" · longest {c['max_len']} {unit}</h1>", _label(),
            f"<p class='muted'>corpus dry run · {'exact, seed ' if kind == 'exact' else 'near-verbatim, K='}{k} · "
            f"<a href='/reuse/corpus/clusters?kind={kind}&k={k}'>back to the list</a></p>",
            f"<blockquote style='border-left:4px solid var(--accent);margin:10px 0;padding:6px 12px;background:var(--surface)'>"
            f"{_esc(c['representative'].get('text') or '')}</blockquote>", "<h2>Witnesses</h2>"]
    members = sorted(c["members"], key=lambda m: (m["issue"], m["story_id"], m["tok"][0]))
    by_story = defaultdict(list)
    for m in members:
        by_story[m["story_id"]].append(m)
    for sid, occ in list(by_story.items())[:200]:
        iid = occ[0]["issue"]
        head = (f"<div class='ch'><span>{_esc(_name(sid, st))}</span><span class='muted'>{_esc(_issue_label(iid))} · "
                f"<a href='/article/{_esc(sid)}'>{_esc(sid)}</a>{' · ' + str(len(occ)) + ' places' if len(occ) > 1 else ''}</span></div>")
        parts = []
        for m in occ[:20]:
            text = m.get("text") or c["representative"].get("text") or ""
            loc = RP.locate_in_article(iid, sid, text[:200]) if text else None
            if loc:
                links = f"<a href='/article/{_esc(sid)}?sel={urllib.parse.quote(loc['fragkey'])}'>open on workbench</a>"
                if loc.get("page"):
                    links += f" · <a href='/issue/{_esc(iid)}/p/{loc['page']}'>scan page {loc['page']}</a>"
            else:
                links = "<span class='muted'>not located in the current article text</span>"
            parts.append(f"<div class='cardtext' style='max-height:none;border-top:1px solid var(--grid)'>"
                         f"<div class='muted' style='font-size:12px;margin-bottom:3px'>{unit} {m['tok'][0]}–{m['tok'][1]} · {m['len']} {unit} · {links}</div>"
                         f"<span class='muted'>{_esc(loc['before']) if loc and loc.get('before') else ''}</span><span style='background:var(--warnbg)'>{_esc(text)}</span>"
                         f"<span class='muted'>{_esc(loc['after']) if loc and loc.get('after') else ''}</span></div>")
        body.append(f"<div class='card'>{head}{''.join(parts)}</div>")
    if len(by_story) > 200:
        body.append(f"<p class='muted'>{len(by_story) - 200} more witnesses not shown.</p>")
    return _render(render, "Cluster", body, f"/reuse/corpus/cluster/{kind}/{k}/{idx}")


# ---------------------------------------------------------------- pairs and duplicates

def pairs_page(qs, render=None):
    RP = _G["RP"]
    g = lambda n, d: (qs.get(n, [d]) or [d])[0]
    try:
        k = int(g("k", "6"))
    except ValueError:
        k = 6
    page = _pageno(qs)
    st = _stories()
    stats = _load(f"machine_k{k}_stats.json") or {}
    pairs = _load(f"machine_k{k}_pairs.json") or []
    body = [RP._howto("Every pair of stories with an exact match, sorted by the longest match: the whole table is machine_k&lt;k&gt;_pairs.npz; "
                      "this list holds its 20,000 longest. Cover = the share of a story's tokens inside some match with the other story."),
            f"<h1>Story pairs with exact reuse (seed {k})</h1>", _label(),
            "<p class='fine'>Seed: " + " · ".join(f"<a href='/reuse/corpus/pairs?k={kk}'>{kk}</a>" if kk != k else f"<b>{kk}</b>" for kk in (6, 7, 8)) + "</p>"]
    if not pairs:
        body.append("<div class='empty'>No pair table for this seed on this server.</div>")
        return _render(render, "Story pairs", body, "/reuse/corpus/pairs")
    n = len(pairs)
    body.append(f"<p class='muted'>{stats.get('pairs_with_reuse', n):,} pairs with a match in all; the {n:,} longest listed.</p>")
    base = f"/reuse/corpus/pairs?k={k}"
    body.append(_pager(base, page, n))
    rows = []
    for p in pairs[(page - 1) * PAGE: page * PAGE]:
        a, b = p["a"], p["b"]
        ia, ib = (st.get(a) or {}).get("issue", ""), (st.get(b) or {}).get("issue", "")
        rows.append([f"<a href='/article/{_esc(a)}'>{_esc(_name(a, st))}</a><br><span class='fine'>{_esc(_issue_label(ia))}</span>",
                     f"<a href='/article/{_esc(b)}'>{_esc(_name(b, st))}</a><br><span class='fine'>{_esc(_issue_label(ib))}</span>",
                     _N(p["max_len"]), _N(p["n_matches"]), _N(f"{100 * p['cover_a']:.0f}%"), _N(f"{100 * p['cover_b']:.0f}%")])
    body.append(_table(["story A", "story B", "#longest match (tokens)", "#matches", "#cover A", "#cover B"], rows))
    body.append(_pager(base, page, n))
    return _render(render, "Story pairs", body, "/reuse/corpus/pairs")


def duplicates_page(qs, render=None):
    RP = _G["RP"]
    dup = _load("duplicates/issue_pairs.json")
    body = [RP._howto("Issue pairs whose stories are the same text: the archive's duplicate items of one issue (the same magazine and date "
                      "under two identifiers), a date to check (the same magazine, another date), or an edition or reprint (another magazine). "
                      "From scripts/duplicate_items.py over the exact-reuse pair table; to be resolved before any analysis counts them as reuse."),
            "<h1>Issues the archive holds twice</h1>", _label()]
    if not dup:
        body.append("<div class='empty'>Not computed yet.</div>")
        return _render(render, "Duplicate items", body, "/reuse/corpus/duplicates")
    s = dup["summary"]
    body.append(f"<p class='muted'>{s['same_text_story_pairs']:,} same-text story pairs; {s['issue_pairs']:,} issue pairs involving {s['issues_involved']:,} issues; "
                + _esc("; ".join(f"{k}: {v}" for k, v in s["by_kind"].items())) + f". Computed {_esc(s['generated'])}.</p>")
    rows = []
    for r in dup["issue_pairs"][:2000]:
        rows.append([_esc(r["kind"]), f"<a href='/issue/{_esc(r['issue_a'])}'>{_esc(r['magazine_a'] or r['issue_a'])}</a><br><span class='fine'>{_esc(r['date_a'] or '')} · {_esc(r['issue_a'])}</span>",
                     f"<a href='/issue/{_esc(r['issue_b'])}'>{_esc(r['magazine_b'] or r['issue_b'])}</a><br><span class='fine'>{_esc(r['date_b'] or '')} · {_esc(r['issue_b'])}</span>",
                     _N(f"{r['stories_joined_a']}/{r['stories_a']}"), _N(f"{r['stories_joined_b']}/{r['stories_b']}"), _N(f"{100 * r['share_of_smaller']:.0f}%"),
                     _esc("; ".join(f"{(e.get('title_a') or '')[:40]} ({e['longest']})" for e in r["examples"][:2]))])
    body.append(_table(["kind", "issue A", "issue B", "#stories joined A", "#stories joined B", "#share", "examples (longest match)"], rows))
    return _render(render, "Duplicate items", body, "/reuse/corpus/duplicates")
