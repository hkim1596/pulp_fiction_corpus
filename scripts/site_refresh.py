#!/usr/bin/env python3
"""Keep the website current with the corpus run (Heejin, 5 October 2026: "Let's the website shows everything we are
doing here. It must lively update eveything.").

Every settings.site.refresh_minutes (default 5):
  1. s13 publishes every assembled corpus issue whose assembly is newer than its live records (data/articles), with how
     it was assembled, the model's confidence and its flags;
  2. r00 --corpus exports every issue whose live records or corrections changed (data/export/corpus/<id>.jsonl), and
     the pilot's file (data/pilot_stories.jsonl) when a pilot issue's records or corrections changed;
  3. the explorer database (authors, magazines, issues, stories, the workbench list) is rebuilt from the exports — in a
     file beside the old one, then moved into place, so the site never reads a half-built database;
  4. (when something was published) the model's disagreements of every issue are gathered for the review page
     (data/review/model_disagreements.jsonl, s13 write_pool).
The site only reads (the file data/explorer.static tells it never to rebuild at request time). The run's board (/run)
reads the run's own progress file at every visit and does not wait for this loop. data/corpus/site_refresh.json holds
the last cycle's numbers; the file data/corpus/STOP_SITE stops the loop.

    python3 scripts/site_refresh.py          # the loop (tmux siterefresh)
    python3 scripts/site_refresh.py --once
"""
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))


def cycle(first=False):
    from corpus_lib import write_json_atomic, all_states, log
    import s13_publish
    import r00_export_stories as r00
    t0 = time.time()
    open(os.path.join(ROOT, "data", "explorer.static"), "a").close()
    pub = s13_publish.publish_all()
    n_pool = None
    if first or pub["published"] or not os.path.exists(s13_publish.POOL):
        try:
            n_pool = s13_publish.write_pool()          # the model's disagreements, for the review page (/review/model)
        except Exception:
            traceback.print_exc()
    n_exp = r00.export_corpus(log=lambda *a, **k: None)
    try:
        n_pilot = r00.export_pilot_live(log=lambda m: print(m, flush=True) if "not written" in m else None)   # the pilot's corrections
    except Exception:
        traceback.print_exc()
        n_pilot = 0
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "published": pub, "exported": n_exp, "pilot_exported": n_pilot,
           "review_pool": n_pool,
           "issues_assembled": sum(1 for s in all_states().values() if "assembled" in s.get("stages", {}))}
    if first or pub["published"] or n_exp or n_pilot:
        b0 = time.time()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "webapp", "explore_pages.py"), "--build"], cwd=ROOT,
                           capture_output=True, text=True, timeout=3 * 3600)
        rec["build_seconds"] = round(time.time() - b0, 1)
        rec["build_ok"] = r.returncode == 0
        out = r.stdout
        try:
            j = out.rfind("\n{\n")                     # the build's closing summary (json, indent 1) after its log lines
            counts = json.loads(out[j + 1:] if j >= 0 else out)["counts"]
            rec.update({"records": counts.get("records", 0), "stories": counts.get("stories", 0), "authors": counts.get("authors", 0),
                        "magazines": counts.get("magazines", 0), "needs_look": counts.get("needs_look", 0),
                        "model_checked": counts.get("model_checked", 0), "model_disagrees": counts.get("model_disagrees", 0),
                        "verified": counts.get("verified", 0),
                        "corpus_issues_shown_as_pilot": counts.get("corpus_issues_shown_as_pilot", 0)})
        except Exception:
            rec["build_tail"] = (r.stderr or out)[-800:]
    else:
        try:
            rec.update({k: v for k, v in json.load(open(os.path.join(ROOT, "data", "corpus", "site_refresh.json"))).items()
                        if k in ("records", "stories", "authors", "magazines", "needs_look", "model_checked", "model_disagrees",
                                 "verified", "build_seconds")})
        except Exception:
            pass
    rec["seconds"] = round(time.time() - t0, 1)
    write_json_atomic(os.path.join(ROOT, "data", "corpus", "site_refresh.json"), rec)
    log("site", f"refresh: published {pub['published']} ({pub.get('from_rules_and_model', 0) + pub.get('from_llm', 0)} checked by the model), "
               f"exported {n_exp}{' and the pilot' if n_pilot else ''}, records {rec.get('records', '?')}, needs a look "
               f"{rec.get('needs_look', '?')}, {rec['seconds']} s")
    return rec


def main():
    once = "--once" in sys.argv
    from corpus_lib import settings
    stop = os.path.join(ROOT, "data", "corpus", "STOP_SITE")
    first = True
    while True:
        t0 = time.time()
        try:
            cycle(first)
            first = False
        except Exception:
            traceback.print_exc()
        if once or os.path.exists(stop):
            break
        every = 60 * float((settings().get("site") or {}).get("refresh_minutes", 5))
        while time.time() - t0 < every and not os.path.exists(stop):
            time.sleep(10)
        if os.path.exists(stop):
            break


if __name__ == "__main__":
    main()
