#!/usr/bin/env python3
"""run_corpus — the Phase 1 chain for the whole corpus, in one resumable process (corpus-build-plan-2026-10-04).

For every issue of config/corpus_issues.json (approved=true), in the list's order:

  download   s01c_fetch.fetch_issue       4 threads          -> state "downloaded"   (data/raw, data/masters)
  image      s01c_fetch.image_issue       6 processes        -> state "imaged"       (data/pages .jpg, data/thumbs)
  read       s02_layout_ocr (Surya)       1 worker per GPU   -> state "read"         (data/layout, data/text/<id>/routeA)
  clean      s04_rules --src routeA       8 processes        -> state "cleaned"      (data/text/<id>/rules_routeA)
  lemma      s04b_lemma                   (same pool)        -> state "lemmatized"   (data/text/<id>/lemma_routeA.jsonl.gz)
  assemble   s08_assemble_rules.run_issue (same pool)        -> state "assembled"    (data/assembly_v2/rules/<id>)
  link       s08.cross_issue per magazine, once every issue of the magazine is assembled or given up
  free       the JP2 master is deleted once the issue is assembled (settings.images.keep_master_until);
             when free space falls under settings.images.min_free_gb, the working images of the oldest assembled
             issues go too (thumbnails stay; both are re-creatable from the archive)

Every step is recorded in data/corpus/state/<id>.json (corpus_lib.mark) and nothing is done twice: stop the
process at any time (a file data/corpus/STOP, or Ctrl-C) and start it again. data/corpus/progress.json is
rewritten every minute (counts, rates, free space, failures, the estimated finish); --status prints it.

Reading: start a Surya inference server per GPU (docs/corpus-run.md, "The reading servers") and list their URLs
in settings.reading.servers; one reading worker runs per URL. With the list empty, one worker runs and Surya
starts its own server on the GPU named in settings.reading.spawn_env (VLLM_GPUS). To add a server later: touch
data/corpus/STOP, add the URL to the settings, start again with --run (nothing is done twice).

    python3 pipeline/run_corpus.py --run                 # everything, resumable
    python3 pipeline/run_corpus.py --run --no-read       # download, image, clean the archive text only (no GPU)
    python3 pipeline/run_corpus.py --status
    python3 pipeline/run_corpus.py --link                # the cross-issue pass over what is assembled, now
    python3 pipeline/run_corpus.py --retry-failed        # forget the failure marks and try those issues again
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import types
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import (ROOT, CORPUS_CONFIG, settings, corpus_config, require_approved, mark, load_state, all_states,  # noqa: E402
                        free_gb, issue_dirs, write_json_atomic, log, event)
import s01c_fetch as fetch  # noqa: E402


def run_info(approval, n_issues):
    """What this run was made with: the code version, the tool versions, the settings, the approved list — one record
    per start in data/corpus/run_info.jsonl (the provenance the data paper and the datasheet draw on)."""
    import platform
    from importlib import metadata as im

    def ver(pkg):
        try:
            return im.version(pkg)
        except Exception:
            return None

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip() or None
    except Exception:
        commit = None
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "host": platform.node(), "python": platform.python_version(),
           "git_commit": commit, "issues": n_issues, "approval": {k: approval.get(k) for k in ("approved_by", "approved_date", "list_sha256", "issues")},
           "versions": {k: ver(k) for k in ("surya-ocr", "spacy", "en_core_web_sm", "pillow", "internetarchive", "torch")},
           "settings": settings()}
    path = os.path.join(ROOT, "data", "corpus", "run_info.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    event("run_start", host=rec["host"], git_commit=commit, versions=rec["versions"], issues=n_issues)
    return rec

PROGRESS = os.path.join(ROOT, settings()["paths"]["corpus_progress"])
STOP_FILE = fetch.STOP_FILE
CHAIN = ["downloaded", "imaged", "read", "cleaned", "lemmatized", "assembled"]
MAX_FAILS = {"downloaded": 6, "imaged": 2, "read": 2, "cleaned": 2, "lemmatized": 2, "assembled": 2}


def fails(st, stage):
    return sum(1 for e in st.get("errors", []) if e.get("stage") == stage)


def given_up(st, stage):
    return fails(st, stage) >= MAX_FAILS.get(stage, 2)


def next_stage(st):
    """The first stage of the chain the issue has not finished (None when assembled)."""
    for s in CHAIN:
        if s not in st["stages"]:
            return s
    return None


# ----------------------------------------------------------------------------------------------------------------
# workers
# ----------------------------------------------------------------------------------------------------------------
def py_env():
    env = os.environ.copy()
    env["PULP_ISSUES"] = os.path.relpath(CORPUS_CONFIG, ROOT)
    env.setdefault("SURYA_INFERENCE_KEEP_ALIVE", "true")
    for k, v in (settings()["reading"].get("spawn_env") or {}).items():   # which GPU and batch sizes when Surya spawns its own server
        env.setdefault(k, str(v))
    return env


def read_issue(iid, server_url):
    """Surya over one issue in a subprocess (so a crash costs one issue, not the run). Returns pages read."""
    env = py_env()
    if server_url:
        env["SURYA_INFERENCE_URL"] = server_url
    t0 = time.time()
    limit = settings()["reading"].get("issue_timeout_s", 3600)
    import signal
    # start_new_session: the subprocess and its surya child form their own process group (id = the child's pid),
    # so a timeout can kill both and nothing else
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "pipeline", "s02_layout_ocr.py"), "--issue", iid],
                            env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.communicate()
        raise RuntimeError(f"reading timed out after {limit} s (Read timed out)")        # "Read timed out" -> put back, not failed
    r = types.SimpleNamespace(returncode=proc.returncode, stdout=out, stderr=err)
    out_dir = os.path.join(ROOT, "data", "text", iid, "routeA")
    n_pages = len([f for f in os.listdir(issue_dirs(iid)["pages"]) if f.endswith((".jpg", ".png"))])
    got = len([f for f in os.listdir(out_dir) if f.endswith(".txt")]) if os.path.isdir(out_dir) else 0
    if r.returncode != 0 or got == 0:
        raise RuntimeError(f"s02 exit {r.returncode}, {got} pages: {(r.stderr or r.stdout)[-600:]}")
    if abs(got - n_pages) > 2:
        raise RuntimeError(f"s02 read {got} pages of {n_pages}")
    return {"pages": got, "seconds": round(time.time() - t0, 1), "server": server_url or "spawned"}


def server_healthy(server_url):
    """Does the reading server answer at /health? (None = no URL: surya spawns its own, nothing to probe.)"""
    if not server_url:
        return True
    import urllib.request
    base = server_url.rstrip("/")
    base = base[:-3] if base.endswith("/v1") else base
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


UNREACHABLE = ("not reachable", "Connection refused", "Connection reset", "Remote end closed", "Max retries", "Read timed out")


def reading_worker(name, server_url, q, results):
    """One worker per reading server. A server that is down does not cost an issue a failure mark: the worker waits
    for it (a message every ten minutes) and puts the issue back when the error says the server was unreachable."""
    down_since = None
    while True:
        if os.path.exists(STOP_FILE) or STOP_WORKERS.is_set():
            return                                   # a STOP ends the worker after the issue in hand, queue or no queue
        if not server_healthy(server_url):
            if down_since is None or time.time() - down_since > 600:
                log("run", f"{name}: reading server {server_url} not answering at /health — waiting (no issue is marked failed for this)")
                event("reading_server_down", worker=name, server=server_url)
                down_since = time.time()
            time.sleep(30)
            continue
        down_since = None
        try:
            iid = q.get(timeout=5)
        except queue.Empty:
            continue
        if iid is None:
            return
        try:
            rec = read_issue(iid, server_url)
            mark(iid, "read", **rec)
            results.put(("read", iid, rec, None))
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if any(k in msg for k in UNREACHABLE) or not server_healthy(server_url):
                log("run", f"{name}: {iid} put back — the server failed during the reading ({msg[:160]})")
                event("reading_server_error", worker=name, server=server_url, issue=iid, error=msg[:300])
                q.put(iid)
                time.sleep(60)
                continue
            mark(iid, "read", fail=True, error=msg[:1000])
            results.put(("read", iid, None, msg))


STOP_WORKERS = threading.Event()


def _post_job(iid, meta):
    """clean -> lemma -> assemble for one issue, in a worker process. Returns (iid, stage_reached, error)."""
    os.environ["PULP_ISSUES"] = os.path.relpath(CORPUS_CONFIG, ROOT)
    import s04_rules
    import s04b_lemma
    import s08_assemble_rules as s08
    st = load_state(iid)
    stage = None
    try:
        if "cleaned" not in st["stages"]:
            stage = "cleaned"
            s04_rules.run_issue(iid, "routeA")
            if not os.path.isdir(os.path.join(ROOT, "data", "text", iid, "rules_routeA")):
                raise RuntimeError("s04 wrote nothing")
            mark(iid, "cleaned")
        if "lemmatized" not in st["stages"]:
            stage = "lemmatized"
            rec = s04b_lemma.run_issue(iid, "routeA") or {}
            mark(iid, "lemmatized", **rec)
        if "assembled" not in st["stages"]:
            stage = "assembled"
            outs = s08.run_issue(iid, meta, log=lambda *a, **k: None)
            if not outs:
                raise RuntimeError("s08 produced no records (no layout pages?)")
            ch = outs["rules"]["checks"]
            mark(iid, "assembled", records=ch.get("records"), story_records=ch.get("story_records"))
        return iid, "assembled", None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        mark(iid, stage or "assembled", fail=True, error=msg[:1000])
        return iid, stage, msg


def _image_job(iid):
    return fetch._image_job(iid)


def remove_master(iid):
    d = issue_dirs(iid)["master"]
    n = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            n += os.path.getsize(os.path.join(d, f))
        shutil.rmtree(d, ignore_errors=True)
    mark(iid, "master_removed", bytes=n, why="assembled; the master is re-fetchable from the archive (md5 in the state file)")
    return n


def remove_working(iid):
    d = issue_dirs(iid)["pages"]
    n = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            n += os.path.getsize(os.path.join(d, f))
        shutil.rmtree(d, ignore_errors=True)
    mark(iid, "working_removed", bytes=n, why="free space under the floor; re-creatable from the master or the archive")
    return n


def reap(states, min_free):
    """Free space when it is short: working images of the oldest assembled issues first (thumbnails stay)."""
    freed = 0
    if free_gb() >= min_free:
        return 0
    cands = [(s["stages"]["assembled"]["ts"], iid) for iid, s in states.items()
             if "assembled" in s["stages"] and "working_removed" not in s["stages"]]
    for _ts, iid in sorted(cands):
        freed += remove_working(iid)
        if free_gb() >= min_free + 50:
            break
    if freed:
        log("run", f"reaper: freed {freed / 1e9:.1f} GB of working images; free now {free_gb():.0f} GB")
    return freed


def link_magazines(issues, states, linked, s08=None, force=False):
    """The cross-issue pass for every magazine whose issues are all assembled or given up."""
    if s08 is None:
        import s08_assemble_rules as s08
    by_mag = defaultdict(list)
    for i in issues:
        by_mag[i["magazine"]].append(i)

    def settled(iid):
        s = states.get(iid, {"stages": {}, "errors": []})
        ns = next_stage(s)
        return ns is None or given_up(s, ns)

    n = 0
    for mag, its in by_mag.items():
        if mag in linked and not force:
            continue
        if not force and not all(settled(i["id"]) for i in its):
            continue
        have = [i for i in its if "assembled" in states.get(i["id"], {"stages": {}})["stages"]]
        if have:
            s08.cross_issue({"issues": have}, variants=("rules",), log=lambda *a, **k: None)
            for i in have:
                mark(i["id"], "linked", magazine=mag, issues=len(have))
        linked.add(mag)
        n += 1
    return n


# ----------------------------------------------------------------------------------------------------------------
# progress
# ----------------------------------------------------------------------------------------------------------------
def progress(issues, states, started, extra=None):
    c = Counter()
    pages = Counter()
    failed = Counter()
    for i in issues:
        st = states.get(i["id"], {"stages": {}, "errors": []})
        for s in CHAIN + ["linked", "master_removed", "working_removed"]:
            if s in st["stages"]:
                c[s] += 1
        if "imaged" in st["stages"]:
            pages["imaged"] += st["stages"]["imaged"].get("pages") or 0
        if "read" in st["stages"]:
            pages["read"] += st["stages"]["read"].get("pages") or 0
        ns = next_stage(st)
        if ns and given_up(st, ns):
            failed[ns] += 1
    el = max(1.0, time.time() - started)
    # rates since this process started (what was done before it started is subtracted)
    base = extra.get("base", {}) if extra else {}
    rate = {s: (c[s] - base.get(s, 0)) / el * 3600 for s in CHAIN}
    eta = {}
    for s in CHAIN:
        left = len(issues) - c[s] - failed[s]
        eta[s] = round(left / rate[s], 1) if rate[s] > 0 else None     # hours
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "issues": len(issues), "done": dict(c), "pages": dict(pages),
           "given_up": dict(failed), "per_hour_since_start": {k: round(v, 1) for k, v in rate.items()},
           "hours_left_at_this_rate": eta, "free_gb": round(free_gb(), 1),
           "disk_gb": disk_use(), "process_started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
           "stop_file": os.path.exists(STOP_FILE)}
    if extra:
        rec.update({k: v for k, v in extra.items() if k != "base"})
    write_json_atomic(PROGRESS, rec)
    return rec


_DISK = {"ts": None, "gb": {}}


def disk_use():
    """The cached directory sizes, measured by a background thread every half hour (never in the main loop: on
    4 October seven `du` calls per minute over a growing tree stalled the loop for minutes at a time — the
    reported 0.0 sizes were their timeouts — and a STOP took longer than 25 minutes to be noticed)."""
    return dict(_DISK["gb"], as_of=_DISK["ts"])


def _measure_disk():
    out = {}
    for k in ("masters", "pages", "thumbs"):
        out[k] = round(du(os.path.join(ROOT, settings()["paths"][k])) / 1e9, 1)
    for k in ("raw", "layout", "text", "assembly_v2"):
        out[k] = round(du(os.path.join(ROOT, "data", k)) / 1e9, 1)
    _DISK["gb"] = out
    _DISK["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")


def disk_thread():
    while not STOP_WORKERS.is_set():
        try:
            _measure_disk()
        except Exception:
            pass
        for _ in range(180):                       # 30 minutes, checked every 10 s so the thread ends with the run
            if STOP_WORKERS.is_set():
                return
            time.sleep(10)


def du(path):
    """Directory size via the system `du`, at the lowest disk priority; 0 when it takes longer than 20 minutes."""
    if not os.path.isdir(path):
        return 0
    try:
        r = subprocess.run(["nice", "-n", "19", "ionice", "-c", "3", "du", "-sb", path], capture_output=True, text=True, timeout=1200)
        return int(r.stdout.split()[0])
    except Exception:
        return 0


# ----------------------------------------------------------------------------------------------------------------
# the run
# ----------------------------------------------------------------------------------------------------------------
def run(args):
    cfg = corpus_config()
    approval = require_approved(cfg, "process")
    st = settings()
    issues = cfg["issues"]
    if args.limit:
        issues = issues[: args.limit]
    info = run_info(approval, len(issues))
    log("run", f"code {info['git_commit']}, versions {info['versions']}")
    meta = {i["id"]: i for i in issues}
    states = all_states()
    started = time.time()
    base = Counter()
    for i in issues:
        for s in CHAIN:
            if s in states.get(i["id"], {"stages": {}})["stages"]:
                base[s] += 1
    log("run", f"{len(issues):,} issues; already: " + ", ".join(f"{s} {base[s]:,}" for s in CHAIN) + f"; free {free_gb():.0f} GB")
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    os.makedirs(os.path.dirname(PROGRESS), exist_ok=True)

    # download threads
    dl_q, dl_results = queue.Queue(), queue.Queue()
    for i in issues:
        s = states.get(i["id"], {"stages": {}, "errors": []})
        if "downloaded" not in s["stages"] and not given_up(s, "downloaded"):
            dl_q.put(i)
    n_dl_threads = 0 if args.no_download else st["download"]["workers"]
    dl_threads = [threading.Thread(target=fetch.download_worker, args=(dl_q, dl_results, w), daemon=True) for w in range(n_dl_threads)]
    for t in dl_threads:
        t.start()

    # reading workers (one per server URL; or one that lets surya spawn its own server). PULP_SURYA_SERVERS, a
    # comma-separated list of URLs, overrides the settings without editing the tracked file on the server.
    servers = [u.strip() for u in os.environ.get("PULP_SURYA_SERVERS", "").split(",") if u.strip()] or st["reading"].get("servers") or [None]
    event("reading_servers", servers=servers)
    log("run", f"reading servers: {servers}")
    read_q, results = queue.Queue(), queue.Queue()
    readers = []
    if not args.no_read:
        for k, url in enumerate(servers):
            t = threading.Thread(target=reading_worker, args=(f"r{k}", url, read_q, results), daemon=True)
            t.start(); readers.append(t)
    queued_read = set()

    threading.Thread(target=disk_thread, daemon=True).start()
    image_pool = ProcessPoolExecutor(max_workers=st["images"].get("processes", 4))
    post_pool = ProcessPoolExecutor(max_workers=st["lemma"].get("processes", 4))
    image_futs, post_futs = {}, {}
    queued_image, queued_post = set(), set()
    linked = set(s["stages"]["linked"]["magazine"] for s in states.values() if "linked" in s["stages"])
    last_progress = last_link = 0
    last_read_done = time.time()
    stall_noted = False
    min_free = st["images"]["min_free_gb"]
    try:
        while True:
            # 1 downloads finished -> imaging
            try:
                while True:
                    iid, rec, err = dl_results.get_nowait()
                    if rec and iid not in queued_image:
                        image_futs[image_pool.submit(_image_job, iid)] = iid
                        queued_image.add(iid)
            except queue.Empty:
                pass
            # anything downloaded earlier and not imaged (resume), or imaged and not read, or read and not assembled
            states = all_states() if time.time() - last_progress > 60 or not states else states
            for i in issues:
                iid = i["id"]
                s = states.get(iid, {"stages": {}, "errors": []})
                if "downloaded" in s["stages"] and "imaged" not in s["stages"] and iid not in queued_image and not given_up(s, "imaged"):
                    image_futs[image_pool.submit(_image_job, iid)] = iid
                    queued_image.add(iid)
                if readers and "imaged" in s["stages"] and "read" not in s["stages"] and iid not in queued_read and not given_up(s, "read"):
                    read_q.put(iid); queued_read.add(iid)
                if "read" in s["stages"] and "assembled" not in s["stages"] and iid not in queued_post and not given_up(s, next_stage(s) or "assembled"):
                    post_futs[post_pool.submit(_post_job, iid, meta[iid])] = iid
                    queued_post.add(iid)
            # 2 imaging done
            for f in [f for f in image_futs if f.done()]:
                iid = image_futs.pop(f)
                _iid, rec, err = f.result()
                if rec and readers and iid not in queued_read:
                    read_q.put(iid); queued_read.add(iid)
                elif err:
                    log("run", f"imaging failed {iid}: {str(err)[:300]}")
            # 3 reading done
            try:
                while True:
                    kind, iid, rec, err = results.get_nowait()
                    last_read_done = time.time()
                    if rec:
                        # the resume scan may have queued the issue already (the worker marks the state before it
                        # reports): then there is nothing to do — the stop of 4 October 18:52 was this case
                        # falling into the failure branch with err=None
                        if iid not in queued_post:
                            post_futs[post_pool.submit(_post_job, iid, meta[iid])] = iid
                            queued_post.add(iid)
                    else:
                        log("run", f"reading failed {iid}: {str(err)[:300]}")
            except queue.Empty:
                pass
            # 4 post-processing done -> masters go
            for f in [f for f in post_futs if f.done()]:
                iid = post_futs.pop(f)
                _iid, stage, err = f.result()
                queued_post.discard(iid)
                if err:
                    log("run", f"{stage} failed {iid}: {str(err)[:300]}")
                elif st["images"].get("keep_master_until", "assembled") == "assembled":
                    remove_master(iid)
            # 5 every minute: progress, reaper, cross-issue links; the stall watch for the reading
            if time.time() - last_progress > 60:
                states = all_states()
                reap(states, min_free)
                pending_read = [i["id"] for i in issues if "imaged" in states.get(i["id"], {"stages": {}})["stages"]
                                and "read" not in states.get(i["id"], {"stages": {}})["stages"]]
                if readers and pending_read and time.time() - last_read_done > 1200:
                    srv_ok = all(server_healthy(u) for u in servers)
                    if not stall_noted:
                        log("run", f"reading stalled: no reading finished for 20 minutes with {len(pending_read)} issues waiting; "
                                   f"server answering: {srv_ok}; the stacks of every thread go to run_faults.log")
                        event("reading_stalled", pending=len(pending_read), server_ok=srv_ok)
                        try:
                            import faulthandler
                            faulthandler.dump_traceback(file=open(os.path.join(ROOT, "data", "corpus", "run_faults.log"), "a"), all_threads=True)
                        except Exception:
                            pass
                        stall_noted = True
                else:
                    stall_noted = False
                if time.time() - last_link > 1800:
                    n = link_magazines(issues, states, linked)
                    if n:
                        log("run", f"cross-issue pass: {n} magazines linked")
                    last_link = time.time()
                rec = progress(issues, states, started, {"base": base, "queues": {"download": dl_q.qsize(), "image": len(image_futs), "read": read_q.qsize(), "post": len(post_futs)}})
                log("run", "done " + ", ".join(f"{s} {rec['done'].get(s, 0):,}" for s in CHAIN) +
                    f" | pages read {rec['pages'].get('read', 0):,} | free {rec['free_gb']:.0f} GB | "
                    + ", ".join(f"{s} {v}h" for s, v in rec["hours_left_at_this_rate"].items() if v is not None))
                last_progress = time.time()
            # 6 stop?
            dl_alive = any(t.is_alive() for t in dl_threads)
            if os.path.exists(STOP_FILE):
                STOP_WORKERS.set()
                if not image_futs and not post_futs and not dl_alive:
                    busy_readers = [t for t in readers if t.is_alive()]
                    if busy_readers:
                        log("run", f"STOP: waiting for {len(busy_readers)} reading worker(s) to finish the issue in hand")
                        for t in busy_readers:
                            t.join(timeout=1800)
                        # their last results -> post-processing is left for the restart (the state says "read")
                    log("run", "STOP: queues drained, exiting (restart with --run to continue)")
                    break
            elif not dl_alive and not image_futs and not post_futs and dl_results.empty() and results.empty() and read_q.empty():
                # nothing moving: either everything is done or the readers are busy with the last issues
                busy = any("imaged" in states.get(i["id"], {"stages": {}})["stages"] and "read" not in states.get(i["id"], {"stages": {}})["stages"]
                           and not given_up(states.get(i["id"], {"stages": {}, "errors": []}), "read") for i in issues) and readers
                if not busy:
                    states = all_states()
                    link_magazines(issues, states, linked)
                    progress(issues, states, started, {"base": base})
                    log("run", "nothing left to do")
                    break
            time.sleep(2)
    except KeyboardInterrupt:
        log("run", "interrupted — state is on disk, start again with --run")
    finally:
        STOP_WORKERS.set()
        for _ in readers:
            read_q.put(None)
        image_pool.shutdown(wait=False, cancel_futures=True)
        post_pool.shutdown(wait=False, cancel_futures=True)


def status():
    if not os.path.exists(PROGRESS):
        print("no progress file yet"); return
    rec = json.load(open(PROGRESS, encoding="utf-8"))
    print(json.dumps(rec, indent=1))


def retry_failed():
    n = 0
    for iid, s in all_states().items():
        if s.get("errors"):
            s["errors"] = []
            write_json_atomic(os.path.join(ROOT, settings()["paths"]["corpus_state"], f"{iid}.json"), s)
            n += 1
    print(f"cleared the failure marks of {n} issues")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--link", action="store_true", help="cross-issue pass now, over every magazine with assembled issues")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--no-read", action="store_true", help="no Surya (no GPU): download, image only")
    ap.add_argument("--no-download", action="store_true", help="process what is on disk, fetch nothing")
    ap.add_argument("--limit", type=int, default=0, help="only the first N issues of the list (a trial)")
    args = ap.parse_args()
    if args.status:
        status(); return
    if args.retry_failed:
        retry_failed(); return
    if args.link:
        cfg = corpus_config(); require_approved(cfg, "process")
        n = link_magazines(cfg["issues"], all_states(), set(), force=True)
        print(f"linked {n} magazines"); return
    if not args.run:
        sys.exit("pass --run, --status, --link or --retry-failed")
    run(args)


def _crash_note(exc):
    """Whatever ends the orchestrator abnormally is written to a file of its own, not only to the console."""
    import traceback
    path = os.path.join(ROOT, "data", "corpus", "run_crash.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()}\n{traceback.format_exc()}\n")
        event("run_crash", error=repr(exc)[:500])
    except Exception:
        pass


if __name__ == "__main__":
    import faulthandler
    try:
        os.makedirs(os.path.join(ROOT, "data", "corpus"), exist_ok=True)
        _fault_file = open(os.path.join(ROOT, "data", "corpus", "run_faults.log"), "a")
        faulthandler.enable(file=_fault_file, all_threads=True)      # a crash of the interpreter itself leaves a trace
        import signal
        faulthandler.register(signal.SIGUSR1, file=_fault_file, all_threads=True)   # kill -USR1 <pid>: every thread's stack, for a look at a stuck run
    except Exception:
        pass
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            _crash_note(e)
        raise
    except BaseException as e:                                       # includes KeyboardInterrupt: noted, then re-raised
        _crash_note(e)
        raise
    log("run", "orchestrator process ended")
