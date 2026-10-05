"""The corpus run, live (Heejin, 5 October 2026: "The website download count hasn't changed I think. No live
update on the website??").

Until v0.17.0 every count on the site came from the explorer database, which is built from the pilot list, so
the corpus run (pipeline/run_corpus.py, since 4 October) never showed. The run rewrites
data/corpus/progress.json every minute and appends every stage it finishes, for every issue, to
data/corpus/events.jsonl. This module reads both at request time:

    progress()        the progress file as the run last wrote it (totals, rates, hours left, free space)
    stage_counts()    {stage: {year: issues}} and the latest events, from events.jsonl, read incrementally (only
                      the lines added since the last visit; the whole file once after a restart of the site)
    extra_counts()    the corpus run's downloaded and assembled totals, which the explorer adds to its own
                      counts (the year strip, the collection bar, the Progress page)
    board_html()      the live board: the top of the Progress page and the /run page
    run_page()        /run, which reloads itself every minute
"""
import glob
import json
import os
import threading
import time
from collections import Counter, deque

_G = {}
_LOCK = threading.Lock()
_EV = {"offset": 0, "size": 0, "done": {}, "recent": deque(maxlen=14), "failed": Counter(), "checked": 0.0}
_CFG = {"mtime": None, "year_of": {}, "selected": Counter(), "n": 0}

STAGES = [("downloaded", "downloaded from the archive"), ("imaged", "page images made"), ("read", "read: layout and text (Surya)"),
          ("cleaned", "text cleaned"), ("lemmatized", "lemmatized"), ("assembled", "assembled into records"),
          ("linked", "linked across a magazine's issues"), ("master_removed", "archive master deleted (space; re-fetchable)")]
SHOWN = ("downloaded", "read", "assembled")


def bind(g):
    _G.update(g)


def _esc(s):
    return _G["esc"](s)


def _data(*p):
    return os.path.join(_G["DATA"], *p)


def progress():
    try:
        return json.load(open(_data("corpus", "progress.json"), encoding="utf-8"))
    except Exception:
        return None


def corpus_list():
    """id -> year for the approved corpus list (config/corpus_issues.json), re-read when the file changes."""
    p = os.path.join(_G["ROOT"], "config", "corpus_issues.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return _CFG
    if _CFG["mtime"] != mt:
        try:
            issues = json.load(open(p, encoding="utf-8")).get("issues", [])
        except Exception:
            issues = []
        year_of, sel = {}, Counter()
        for i in issues:
            try:
                y = int(i.get("year") or str(i.get("cover_date") or "")[:4])
            except Exception:
                y = None
            year_of[i["id"]] = y
            if y:
                sel[y] += 1
        _CFG.update({"mtime": mt, "year_of": year_of, "selected": sel, "n": len(issues)})
    return _CFG


def stage_counts(min_interval=20):
    """Read the lines events.jsonl gained since the last call (at most every min_interval seconds)."""
    p = _data("corpus", "events.jsonl")
    now = time.time()
    if now - _EV["checked"] < min_interval:
        return _EV
    with _LOCK:
        if time.time() - _EV["checked"] < min_interval:
            return _EV
        try:
            size = os.path.getsize(p)
        except OSError:
            _EV["checked"] = time.time()
            return _EV
        if size < _EV["offset"]:                       # a new file: start again
            _EV.update({"offset": 0, "done": {}, "failed": Counter()})
            _EV["recent"].clear()
        if size > _EV["offset"]:
            with open(p, "rb") as f:
                f.seek(_EV["offset"])
                chunk = f.read(size - _EV["offset"])
            end = chunk.rfind(b"\n") + 1               # a line still being written waits for the next visit
            for line in chunk[:end].splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                ev, iid, st = e.get("event"), e.get("issue"), e.get("stage")
                if ev == "done" and iid and st:
                    _EV["done"].setdefault(st, set()).add(iid)
                    if st in ("downloaded", "read", "assembled"):
                        _EV["recent"].append({"ts": e.get("ts"), "issue": iid, "stage": st, "pages": e.get("pages") or e.get("leaves"),
                                              "seconds": e.get("seconds")})
                elif ev == "failed" and st:
                    _EV["failed"][st] += 1
            _EV["offset"] += end
        _EV["size"] = size
        _EV["checked"] = time.time()
    return _EV


def by_year():
    cfg = corpus_list()
    ev = stage_counts()
    out = {"selected": dict(cfg["selected"])}
    with _LOCK:                                  # another request may be adding to the sets
        for st in SHOWN:
            c = Counter()
            for iid in ev["done"].get(st, ()):
                y = cfg["year_of"].get(iid)
                if y:
                    c[y] += 1
            out[st] = dict(c)
    return out


def extra_counts():
    """The corpus run's totals for the explorer's own counts (0 when the run has not started)."""
    try:
        ev = stage_counts()
        return {st: len(ev["done"].get(st, ())) for st in ("downloaded", "read", "assembled")}
    except Exception:
        return {"downloaded": 0, "read": 0, "assembled": 0}


def _llm_lines():
    """The box-linking runs (pipeline/s12_llm_link.py): one line per run folder that has issues."""
    rows = []
    for d in sorted(glob.glob(_data("assembly_v2", "llm*"))):
        sm = os.path.join(d, "summary.jsonl")
        if not os.path.exists(sm):
            continue
        last = {}
        for line in open(sm, encoding="utf-8"):
            try:
                x = json.loads(line)
                last[x["issue"]] = x
            except Exception:
                pass
        if not last:
            continue
        S = list(last.values())
        tot = lambda k: sum((s.get(k) or 0) for s in S)        # noqa: E731
        asked = max(1, tot("pages") - sum((s.get("tiers") or {}).get("none", 0) for s in S))
        ts = max((s.get("ts") or "") for s in S)
        name = os.path.basename(d)
        rows.append((name, len(S), tot("pages"), 100 * tot("local_unreadable") / asked, 100 * (tot("asked_again") or tot("api_wanted")) / asked,
                     100 * tot("flagged_pages") / asked, tot("cost_usd"), ts))
    return rows


def board_html(compact=False):
    E = _G["EX"]
    p = progress()
    cfg = corpus_list()
    n_sel = (p or {}).get("issues") or cfg["n"]
    out = ["<h2 id='run'>The corpus run, live</h2>"]
    if not p:
        out.append("<div class='empty'>The corpus run has not written its progress file yet (data/corpus/progress.json).</div>")
        return "".join(out)
    age = None
    try:
        age = time.time() - time.mktime(time.strptime(p["ts"], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        pass
    fresh = ("." if age is None else (f" — {int(age // 60)} min ago." if age < 3600 else f" — <b>{age / 3600:.1f} hours ago: is the run still going?</b>"))
    done = p.get("done", {})
    out.append(f"<p class='muted'>The run on the lab server, started {_esc(p.get('process_started', '?'))}, rewrites its progress file every "
               f"minute; this board reads it at every visit. Last written {_esc(p['ts'])}{fresh} {n_sel:,} issues selected (Phase 0–1, "
               f"approved 4 October 2026).</p>")
    out.append(_G["stats_html"]([(done.get("downloaded", 0), "downloaded"), (done.get("read", 0), "read"), (done.get("assembled", 0), "assembled"),
                                 ((p.get("pages") or {}).get("read") or 0, "pages read"), (f"{p.get('free_gb', 0):,.0f} GB", "free disk")]))
    rate = p.get("per_hour_since_start", {})
    left = p.get("hours_left_at_this_rate", {})
    rows = []
    for st, label in STAGES:
        v = done.get(st, 0)
        r = rate.get(st)
        h = left.get(st)
        rows.append([_esc(label), E.N(v), E._bar(v, n_sel, 260, 12) + f" <span class='fine'>{(100 * v / n_sel if n_sel else 0):.1f}% of {n_sel:,}</span>",
                     E.N(f"{r:,.1f}" if r is not None else ""),
                     E.N(("" if h is None else f"{h:,.0f} h (about {h / 24:,.1f} days)") if st in rate else "")])
    ev0 = stage_counts()
    for st, label in (("linked_llm", "checked box by box by the language model (s12)"), ("published", "on this website, with confidence and flags (s13)")):
        v = len(ev0["done"].get(st, ()))
        rows.append([_esc(label), E.N(v), E._bar(v, n_sel, 260, 12) + f" <span class='fine'>{(100 * v / n_sel if n_sel else 0):.1f}% of {n_sel:,}</span>",
                     E.N(""), E.N("")])
    out.append(E._table(["stage", "#issues", "share of the selection", "#per hour since the start", "#left at this rate"], rows))
    rf = None
    try:
        rf = json.load(open(_data("corpus", "site_refresh.json"), encoding="utf-8"))
    except Exception:
        pass
    if rf:
        out.append(f"<p class='fine'>The site's own database (authors, magazines, issues, stories, the workbench list) was last rebuilt "
                   f"{_esc(rf.get('ts', ''))} in {rf.get('build_seconds', '?')} s: {rf.get('records', 0):,} records of {rf.get('issues_assembled', 0):,} "
                   f"assembled issues and the ten pilot issues; {rf.get('model_checked', 0):,} checked by the language model, which "
                   f"disagrees with {rf.get('model_disagrees', 0):,}; {rf.get('needs_look', 0):,} flagged for a look; "
                   f"{rf.get('verified', 0):,} verified by people. It is rebuilt every few minutes (scripts/site_refresh.py). Where the "
                   "model disagrees, the record stays as the rules made it and its flags say what the model would change "
                   "(Heejin's choice after the pilot score, 5 October).</p>")
    gu = p.get("given_up") or {}
    ev = stage_counts()
    out.append("<p class='fine'>Given up after retries: " + (", ".join(f"{_esc(k)} {v}" for k, v in gu.items()) if gu else "none")
               + f". Failed attempts so far (retried): {sum(ev['failed'].values()):,}. The run keeps the archive masters until an issue is "
               "assembled and deletes them then (the archive keeps them); the page images stay.</p>")
    if not compact:
        yrs = by_year()
        layers = [("Selected", "#1baf7a", yrs["selected"], None), ("Downloaded", "#2a78d6", yrs["downloaded"], yrs["selected"]),
                  ("Read", "#eda100", yrs["read"], yrs["selected"]), ("Assembled", "#eb6834", yrs["assembled"], yrs["selected"])]
        out.append("<figure style='margin:14px 0'><figcaption class='fine'>The selection by year, and how much of each year the run has "
                   "downloaded, read and assembled — darker means a fuller year; a cell's tooltip gives the counts.</figcaption>"
                   + E.year_strip(layers, ref_tip="issues selected for the corpus") + "</figure>")
    with _LOCK:
        recent = list(ev["recent"])
    if recent:
        rec = []
        for e in reversed(recent):
            what = {"downloaded": "downloaded", "read": "read", "assembled": "assembled"}[e["stage"]]
            extra = (f" · {e['pages']} pages" if e.get("pages") else "") + (f" · {e['seconds']:.0f} s" if isinstance(e.get("seconds"), (int, float)) else "")
            rec.append(f"<li><span class='fine'>{_esc((e.get('ts') or '')[5:16].replace('T', ' '))}</span> {what} "
                       f"<code>{_esc(e['issue'])}</code>{_esc(extra)}</li>")
        out.append("<h3>Latest</h3><ul class='fine' style='columns:2;max-width:980px'>" + "".join(rec) + "</ul>")
    llm = _llm_lines()
    if llm:
        rows = [[_esc(n), E.N(i), E.N(pg), E.N(f"{u:.1f}%"), E.N(f"{w:.1f}%"), E.N(f"{f:.1f}%"), E.N(f"${c:,.2f}"), _esc(ts[5:16].replace("T", " "))]
                for n, i, pg, u, w, f, c, ts in llm]
        out.append("<h3>Box linking (the language model on GPU 2: the corpus, the pilot runs, the trials)</h3>"
                   + E._table(["run", "#issues", "#pages", "#answers unreadable", "#pages asked again", "#pages flagged for a person",
                               "#API cost", "last issue"], rows)
                   + "<p class='fine'>llm = the corpus run's own box linking (s12 --follow: every assembled corpus issue, as it is "
                     "assembled; from 5 October); llm_pilot_… = the ten pilot issues (scored against people's corrections); "
                     "llm_trial_… = trials on the first hundred corpus issues. A page is asked again when the first reading is less than 0.95 sure of a "
                     "box, or could not be read: by the Claude API until 5 October, by the local model's second, thinking reading since "
                     "(p50l); a page is flagged only when a decision that changes a piece stays open.</p>")
    if compact:
        out.append("<p class='fine'><a href='/run'>The run's own page</a> adds the year strip and reloads itself every minute.</p>")
    return "".join(out)


def evals_html():
    """The accuracy tables (s09): every data/assembly_v2/eval_*.txt, its header and its ALL lines."""
    out = []
    for f in sorted(glob.glob(_data("assembly_v2", "eval*.txt")), key=os.path.getmtime, reverse=True):
        try:
            lines = open(f, encoding="utf-8").read().splitlines()
        except Exception:
            continue
        keep = [ln for ln in lines[:1] + [ln for ln in lines if ln.startswith("ALL")]]
        if len(keep) < 2:
            continue
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f)))
        out.append(f"<p class='fine'><b>{_esc(os.path.basename(f))}</b> · {ts}</p><pre style='font-size:11.5px;overflow-x:auto'>"
                   + _esc("\n".join(keep)) + "</pre>")
    if not out:
        return ""
    return ("<h3>Accuracy (s09): each way of assembling against people's corrections and the contents pages</h3>"
            "<p class='fine'>pieces = entries on the contents pages; found, title, author, clean = how many of them a record starts, names "
            "and covers without splitting or running over; verif / exact / jacc = the records people verified on the workbench, how "
            "many a variant reproduces exactly, and the mean overlap; recs = records; chap = story records starting at a chapter "
            "head; noauth = story records without an author. rules = the rules engine; llm… = the rules' records checked by the "
            "language model.</p>" + "".join(out))


def run_page(qs=None, render=None):
    body = (_G["howto"]("The corpus run on the lab server, as it stands: what has been downloaded from the Internet Archive, read, "
                        "assembled into records, checked by the language model and put on this site, how fast, and how long the "
                        "rest will take at that pace; then the accuracy measured so far. The page reloads itself every minute.")
            + "<h1>Corpus run</h1>" + board_html(compact=False) + evals_html()
            + "<p class='fine'>Everything done, decided and measured, day by day: <a href='/log'>the build log</a>.</p>"
            + "<script>setTimeout(function(){location.reload()},60000)</script>")
    return (render or _G["page"])("Corpus run", body, path="/run")


def _md_inline(t):
    """`code`, **bold**, and links [text](url) in one escaped line of the build log."""
    import re
    t = _esc(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<a href='\2'>\1</a>", t)
    return t


def log_page(qs=None, render=None):
    """docs/corpus-build-log.md as a page: what was done, decided and measured, day by day (the record for the data paper)."""
    p = os.path.join(_G["ROOT"], "docs", "corpus-build-log.md")
    try:
        lines = open(p, encoding="utf-8").read().splitlines()
    except Exception:
        lines = []
    out, para, code = [], [], None

    def flush():
        if para:
            out.append("<p>" + _md_inline(" ".join(x.strip() for x in para)) + "</p>")
            para.clear()
    for ln in lines:
        if code is not None:
            if ln.strip().startswith("```"):
                out.append("<pre style='font-size:12px;overflow-x:auto'>" + _esc("\n".join(code)) + "</pre>")
                code = None
            else:
                code.append(ln)
            continue
        if ln.strip().startswith("```"):
            flush()
            code = []
            continue
        if ln.startswith("    ") and not para and ln.strip():
            out.append("<pre style='font-size:12px;overflow-x:auto;margin:2px 0'>" + _esc(ln[4:]) + "</pre>")
            continue
        if ln.startswith("#"):
            flush()
            n = len(ln) - len(ln.lstrip("#"))
            tag = {1: "h1", 2: "h2", 3: "h3"}.get(n, "h4")
            out.append(f"<{tag} id='s{len(out)}'>" + _md_inline(ln.lstrip("#").strip()) + f"</{tag}>")
            continue
        if not ln.strip():
            flush()
            continue
        if ln.lstrip().startswith(("- ", "* ")) and not para:
            out.append("<p style='margin:2px 0 2px 18px'>• " + _md_inline(ln.lstrip()[2:]) + "</p>")
            continue
        para.append(ln)
    flush()
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))) if os.path.exists(p) else "?"
    body = (_G["howto"]("The build log of the corpus (docs/corpus-build-log.md in the repository): every run, decision, fault and "
                        "measurement, in order, as recorded during the work — the record the database release and the data paper "
                        "are written from. It is the version on this server, brought up to date with every change of the code.")
            + f"<p class='fine'>Last changed {ts}. <a href='/run'>The corpus run, live</a>.</p>" + "".join(out))
    return (render or _G["page"])("Build log", body, path="/log")
