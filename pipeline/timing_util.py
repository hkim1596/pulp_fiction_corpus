"""Stage timing: every pipeline stage appends one JSON line per unit of work.

Usage:
    from timing_util import stage_timer
    with stage_timer("s02_layout_ocr", issue_id, pages=112, extra={"route": "A"}):
        ...work...

Lines land in data/timings.jsonl:
    {"ts": ..., "stage": ..., "issue": ..., "pages": ..., "seconds": ..., ...extra}

The website's /timing page and s06_metrics read this file. Per-page rates and
full-corpus extrapolations are computed there, not here.
"""
import json
import os
import time
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMINGS = os.path.join(ROOT, "data", "timings.jsonl")


def load_pulp_env(path=None):
    """Load export lines from ~/shared/khj/.pulp_env into os.environ.

    tmux sessions started by `rtx run` do not source that file (the s05
    failure of 2026-08-20), so every stage that needs endpoints or keys
    calls this itself. Existing environment values are never overridden.
    """
    path = path or os.path.expanduser("~/shared/khj/.pulp_env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("export ") and "=" in line:
            k, v = line[len("export "):].split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@contextmanager
def stage_timer(stage, issue, pages=None, extra=None):
    t0 = time.time()
    err = None
    try:
        yield
    except Exception as e:  # record the failure, then re-raise
        err = repr(e)
        raise
    finally:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": stage,
            "issue": issue,
            "pages": pages,
            "seconds": round(time.time() - t0, 2),
        }
        if extra:
            rec.update(extra)
        if err:
            rec["error"] = err
        os.makedirs(os.path.dirname(TIMINGS), exist_ok=True)
        with open(TIMINGS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
