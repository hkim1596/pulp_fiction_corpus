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
