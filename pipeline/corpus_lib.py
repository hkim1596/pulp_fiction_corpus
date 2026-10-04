"""Shared helpers for the corpus stages (s00b, s01c, s04b, run_corpus, the reaper).

The pilot stages read config/pilot_issues.json. The corpus stages read config/corpus_issues.json, which has the
same shape (an "issues" list with id, ia_identifier, magazine, cover_date, genre, format) plus the selection
fields. The pilot stages keep reading the pilot list by default (the site and the pilot records must not change
when the corpus list appears); the orchestrator points them at the corpus list by setting the environment
variable PULP_ISSUES=config/corpus_issues.json for the processes it starts (see issues_config()). The web app
has its own switch, PULP_CONFIG.

Per-issue state lives in data/corpus/state/<id>.json: one small file per issue, written atomically, so that
four download workers, two reading workers and the post-processing chain can run at once without a database.
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(ROOT, "config", "corpus_settings.json")
CORPUS_CONFIG = os.environ.get("PULP_CORPUS_ISSUES") or os.path.join(ROOT, "config", "corpus_issues.json")   # the env var: trial lists
PILOT_CONFIG = os.path.join(ROOT, "config", "pilot_issues.json")

STAGES = ["downloaded", "imaged", "read", "cleaned", "lemmatized", "assembled", "linked", "master_removed", "working_removed"]


def settings():
    return json.load(open(SETTINGS_PATH, encoding="utf-8"))


def issues_config_path():
    """The issue list a stage should use: PULP_ISSUES if set (the orchestrator sets it to the corpus list for the
    processes it starts), else the pilot list, as before."""
    p = os.environ.get("PULP_ISSUES")
    if p:
        return p if os.path.isabs(p) else os.path.join(ROOT, p)
    return PILOT_CONFIG


def corpus_config():
    """The corpus list itself (config/corpus_issues.json), for the corpus stages."""
    return json.load(open(CORPUS_CONFIG, encoding="utf-8"))


APPROVAL_PATH = os.path.join(ROOT, "config", "corpus_approval.json")


def list_sha256(cfg):
    """The fingerprint of an issue list: the issues only, in a canonical JSON form."""
    import hashlib
    return hashlib.sha256(json.dumps(cfg["issues"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def require_approved(cfg, what="download"):
    """The audit gate: nothing is fetched or processed from a list Heejin has not approved.

    The list (config/corpus_issues.json) is generated on the server and not tracked by git; the approval is the
    small tracked file config/corpus_approval.json, written by `s00b_select.py --approve "Name"` on the Mac over the
    same list, which carries the list's fingerprint. A regenerated list no longer matches and must be approved again."""
    ap = None
    if os.path.exists(APPROVAL_PATH):
        try:
            ap = json.load(open(APPROVAL_PATH, encoding="utf-8"))
        except Exception:
            ap = None
    if not ap or not ap.get("approved"):
        sys.exit(f"REFUSING TO {what.upper()}: no approval in {APPROVAL_PATH}.\n"
                 "Heejin reviews the counts (data/survey/selection_counts.json) and the list, then runs\n"
                 '  python3 pipeline/s00b_select.py --approve "Heejin Kim"\n'
                 "over the same list and commits config/corpus_approval.json (the Registered Report audit trail).")
    if ap.get("list_sha256") != list_sha256(cfg):
        sys.exit(f"REFUSING TO {what.upper()}: the approval in {APPROVAL_PATH} is for a different list "
                 f"({ap.get('issues')} issues, approved {ap.get('approved_date')}); this list has {len(cfg['issues'])} issues. "
                 "Approve the current list again, or restore the approved one.")
    return ap


def issues_config():
    return json.load(open(issues_config_path(), encoding="utf-8"))


LOG_PATH = os.path.join(ROOT, "data", "corpus", "orchestrator.log")


def log(stage, msg):
    """Console line plus the same line appended to data/corpus/orchestrator.log — the file does not depend on the
    console pipe (a console that goes away must not stop or blind the run)."""
    line = f"[{stage}] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def state_dir():
    return os.path.join(ROOT, settings()["paths"]["corpus_state"])


def state_path(iid):
    return os.path.join(state_dir(), f"{iid}.json")


def load_state(iid):
    p = state_path(iid)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"id": iid, "stages": {}, "errors": []}


EVENTS_PATH = os.path.join(ROOT, "data", "corpus", "events.jsonl")


def event(kind, **info):
    """One line in data/corpus/events.jsonl: the chronological log of everything the corpus stages did (every
    stage finished or failed for every issue, every deletion, every run start). The per-issue state files are the
    same facts by issue; this file is the same facts by time, for the build log and the data paper."""
    os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind}
    rec.update(info)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:     # small lines, O_APPEND: safe across threads and processes
        f.write(line)


def mark(iid, stage, **info):
    """Record that a stage finished for an issue (or failed, with fail=True); the same fact goes to events.jsonl."""
    st = load_state(iid)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    rec.update(info)
    if info.get("fail"):
        st["errors"].append({"stage": stage, **rec})
        st["errors"] = st["errors"][-20:]
    else:
        st["stages"][stage] = rec
    write_json_atomic(state_path(iid), st)
    event("failed" if info.get("fail") else "done", issue=iid, stage=stage, **{k: v for k, v in info.items() if k != "fail"})
    return st


def has(iid, stage):
    return stage in load_state(iid)["stages"]


def all_states():
    d = state_dir()
    if not os.path.isdir(d):
        return {}
    out = {}
    for f in os.listdir(d):
        if f.endswith(".json") and not f.startswith("."):
            try:
                out[f[:-5]] = json.load(open(os.path.join(d, f), encoding="utf-8"))
            except Exception:
                pass
    return out


def free_gb(path=None):
    path = path or os.path.join(ROOT, "data")
    os.makedirs(path, exist_ok=True)
    u = shutil.disk_usage(path)
    return u.free / 1e9


def dir_bytes(path):
    n = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n


def issue_dirs(iid):
    s = settings()["paths"]
    return {
        "master": os.path.join(ROOT, s["masters"], iid),
        "pages": os.path.join(ROOT, s["pages"], iid),
        "thumbs": os.path.join(ROOT, s["thumbs"], iid),
        "raw": os.path.join(ROOT, "data", "raw", iid),
        "layout": os.path.join(ROOT, "data", "layout", iid),
        "text": os.path.join(ROOT, "data", "text", iid),
        "assembly": os.path.join(ROOT, "data", "assembly_v2", "rules", iid),
    }


if __name__ == "__main__":
    print("settings", SETTINGS_PATH, "issues", issues_config_path(), "free GB", round(free_gb(), 1))
    sys.exit(0)
