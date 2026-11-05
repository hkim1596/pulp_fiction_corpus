#!/usr/bin/env python3
"""Rules 2.14, step 2b (4 November 2026): which layout boxes recur, word for word, in other issues — the evidence file
for the recurring-box rule (R-A, docs/corpus-1.0-and-stage-2-design.md §3.2).

For every assembled issue (data/assembly_v2/rules/<issue>/articles.json, the candidate the last upgrade wrote), every
box that a record owns is read from the layout (data/layout/<issue>/page_*.json), prepared and tokenised as r01 does,
and cut into 8-word shingles. Over the whole corpus the shingles are hashed, sorted and counted (the array engine of
r02 at corpus scale, on boxes instead of stories): a shingle is RECURRING when it occurs in boxes of three or more
issues. A box's share is the fraction of its shingles that recur. The file written —
data/corpus/p52-2026-10-16/box_recurrence.json — holds, for every box of 15 tokens or more with a share of 0.6 or
more: the share, the number of issues its shingles reach, and up to eight partner boxes (issue, record, record type,
author, work title) so that the assembly can leave a series' own formula alone (a partner of the same author or the
same work). Nothing else is written; no pairs of stories, no dates, no topics: this is assembly evidence, like
config/toc_page_overrides.json, and it is deposited with corpus 1.0.

    python3 scripts/box_recurrence.py [--workers 16] [--min-issues 3] [--min-tokens 15] [--min-share 0.6] [--out …]
    python3 scripts/box_recurrence.py --selftest
    python3 scripts/box_recurrence.py --issues a,b,c --out /tmp/x.json      # a few issues, for a look
    python3 scripts/box_recurrence.py --split-only                            # the per-issue files from an existing box_recurrence.json

5 November 2026: the set-aside items (config/corpus_set_aside.json — the duplicate scans and other editions of D-5)
are left out of the count unless --include-set-aside is given, so that a text seen in an issue's two scans is not
"recurring"; and the result is also written issue by issue to box_recurrence/<issue>.json, which the assembly reads
(s08_assemble_rules.box_recurrence_for; the one big file is too heavy for every assembly worker to load).

Memory: about 450 M shingles × 16 bytes, sorted in place — some 8 GB; the hashes of each issue are written to a shard
first, so the workers hold little. Time: tens of minutes on the server with 16 workers.
"""
import argparse
import glob
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
from r01_normalize import prepare, tokenize  # noqa: E402

K = 8
RUN = os.path.join(ROOT, "data", "corpus", "p52-2026-10-16")
OUT = os.path.join(RUN, "box_recurrence.json")
SPLIT_DIR = os.path.join(RUN, "box_recurrence")
SHARDS = os.path.join(RUN, "box_recurrence_shards")
TYPES_INDEXED = None          # every record type: an advertisement elsewhere is evidence for the same text in a story


def shingle_hash(words_):
    return int.from_bytes(hashlib.blake2b(" ".join(words_).encode("utf-8"), digest_size=8).digest(), "little")


def box_shingles(text):
    toks = [t for t, _, _ in tokenize(prepare(text))]
    if len(toks) < K:
        return len(toks), []
    return len(toks), [shingle_hash(toks[i:i + K]) for i in range(len(toks) - K + 1)]


def issue_boxes(iid, root=ROOT):
    """[(page, idx, record_id, type, author, work_title, n_tokens, [hashes])] for the boxes the issue's records own."""
    path = os.path.join(root, "data", "assembly_v2", "rules", iid, "articles.json")
    if not os.path.exists(path):
        return []
    doc = json.load(open(path, encoding="utf-8"))
    pages = {}
    for f in glob.glob(os.path.join(root, "data", "layout", iid, "page_*.json")):
        p = json.load(open(f, encoding="utf-8"))
        pages[p["page"]] = sorted(p["regions"], key=lambda r: r.get("order", 0))
    out = []
    for rec in doc.get("articles", []):
        for fr in rec.get("fragments") or []:
            regs = pages.get(fr["page"])
            if not regs:
                continue
            for i in fr.get("region_ids") or []:
                if i >= len(regs):
                    continue
                t = regs[i].get("text") or ""
                n, hs = box_shingles(t)
                out.append((fr["page"], i, rec["article_id"], rec.get("type"), rec.get("author"), rec.get("work_title") or rec.get("title"), n, hs))
    return out


def _shard(args):
    """One worker: a list of issues -> a shard of (hash, issue_no, box_no) arrays and the boxes' metadata."""
    issues, shard_path, root = args
    meta, H, I, B = [], [], [], []
    for ino, iid in issues:
        hashes, box_no = [], []
        for page, idx, rid, typ, author, work, n, hs in issue_boxes(iid, root):
            bno = len(meta)
            meta.append({"issue": iid, "ino": ino, "page": page, "idx": idx, "record": rid, "type": typ, "author": author, "work": work,
                         "n_tokens": n, "n_shingles": len(hs)})
            hashes.extend(hs)
            box_no.extend([bno] * len(hs))
        if hashes:
            H.append(np.asarray(hashes, dtype=np.uint64))
            I.append(np.full(len(hashes), ino, dtype=np.int32))
            B.append(np.asarray(box_no, dtype=np.int32))
    np.savez(shard_path, h=np.concatenate(H) if H else np.zeros(0, np.uint64), i=np.concatenate(I) if I else np.zeros(0, np.int32),
             b=np.concatenate(B) if B else np.zeros(0, np.int32))
    return shard_path, meta


def compute(issues, workers=16, min_issues=3, min_tokens=15, min_share=0.6, partners_max=8, root=ROOT, shards_dir=SHARDS, log=print):
    t0 = time.time()
    os.makedirs(shards_dir, exist_ok=True)
    numbered = list(enumerate(issues))
    chunks = [numbered[i::workers] for i in range(workers)]
    jobs = [(c, os.path.join(shards_dir, f"shard_{n:02d}.npz"), root) for n, c in enumerate(chunks) if c]
    metas, hs, is_, bs = [], [], [], []
    offset = 0
    with Pool(len(jobs)) as pool:
        for shard_path, meta in pool.imap(_shard, jobs):
            z = np.load(shard_path)
            hs.append(z["h"]); is_.append(z["i"]); bs.append(z["b"] + offset)
            offset += len(meta)
            metas.extend(meta)
            log(f"[box_recurrence] {os.path.basename(shard_path)}: {len(meta):,} boxes, {len(z['h']):,} shingles ({time.time() - t0:.0f}s)")
    H = np.concatenate(hs); I = np.concatenate(is_); B = np.concatenate(bs)
    del hs, is_, bs
    log(f"[box_recurrence] {len(metas):,} boxes, {len(H):,} shingles; sorting ({time.time() - t0:.0f}s)")
    order = np.lexsort((I, H))                       # by hash, then by issue: distinct (hash, issue) pairs are adjacent
    H, I, B = H[order], I[order], B[order]
    del order
    new_hash = np.r_[True, H[1:] != H[:-1]]
    new_pair = new_hash | np.r_[True, I[1:] != I[:-1]]
    starts = np.flatnonzero(new_hash)
    ends = np.r_[starts[1:], len(H)]
    n_issues_run = np.add.reduceat(new_pair.astype(np.int32), starts)      # distinct issues per run of one hash
    run_recurs = n_issues_run >= min_issues
    recurring = np.repeat(run_recurs, ends - starts)
    log(f"[box_recurrence] {len(starts):,} distinct shingles, {int(run_recurs.sum()):,} in {min_issues}+ issues ({time.time() - t0:.0f}s)")
    # per box: recurring shingles / all shingles
    rec_count = np.bincount(B[recurring], minlength=len(metas))
    all_count = np.bincount(B, minlength=len(metas))
    share = np.where(all_count > 0, rec_count / np.maximum(all_count, 1), 0.0)
    n_tok = np.asarray([m["n_tokens"] for m in metas], dtype=np.int32)
    flagged = np.flatnonzero((n_tok >= min_tokens) & (all_count > 0) & (share >= min_share))
    log(f"[box_recurrence] {len(flagged):,} boxes of {min_tokens}+ tokens with a share of {min_share}+ ({time.time() - t0:.0f}s)")
    # partners and the reach, from a sample of each flagged box's recurring shingles (up to 6 runs, 60 members each)
    positions = np.flatnonzero(recurring)
    Bp = B[positions]
    keep = np.isin(Bp, flagged)
    positions, Bp = positions[keep], Bp[keep]
    o = np.argsort(Bp, kind="stable")
    positions, Bp = positions[o], Bp[o]
    bstarts = np.flatnonzero(np.r_[True, Bp[1:] != Bp[:-1]]) if len(Bp) else np.array([], dtype=np.int64)
    bends = np.r_[bstarts[1:], len(Bp)] if len(Bp) else bstarts
    out = {}
    for s_, e_ in zip(bstarts, bends):
        b = int(Bp[s_])
        m = metas[b]
        sample = positions[s_:e_]
        step = max(1, len(sample) // 6)
        sample = sample[::step][:6]
        reach, partners = 0, {}
        for pos in sample:
            r = int(np.searchsorted(starts, pos, side="right") - 1)
            reach = max(reach, int(n_issues_run[r]))
            a, e = int(starts[r]), int(ends[r])
            for q in range(a, min(e, a + 60)):
                ob = int(B[q])
                if ob == b:
                    continue
                om = metas[ob]
                if om["issue"] != m["issue"] and len(partners) < partners_max and om["record"] not in partners:
                    partners[om["record"]] = {"issue": om["issue"], "record": om["record"], "type": om["type"], "author": om["author"], "work": om["work"]}
        out.setdefault(m["issue"], {})[f"{m['page']}:{m['idx']}"] = {
            "record": m["record"], "type": m["type"], "n_tokens": m["n_tokens"], "share": round(float(share[b]), 3),
            "n_issues": max(0, reach - 1), "partners": list(partners.values())}
    flagged = [int(b) for b in flagged]
    summary = {"generated": time.strftime("%Y-%m-%d %H:%M"), "issues": len(issues), "boxes": len(metas), "shingles": int(len(H)),
               "distinct_shingles": int(len(starts)), "k": K, "min_issues": min_issues, "min_tokens": min_tokens, "min_share": min_share,
               "flagged_boxes": len(flagged), "flagged_by_type": {}, "seconds": round(time.time() - t0, 1)}
    by_type = defaultdict(int)
    for b in flagged:
        by_type[metas[b]["type"] or "?"] += 1
    summary["flagged_by_type"] = dict(by_type)
    return {"summary": summary, "issues": out}


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ad = "Send no money today write for our free booklet and learn at home how thousands found success in radio work dept five union square new york city"
        story_a = "He rode into the valley at dusk and the cattle were quiet under the first stars of the long and empty night while the men slept by the fire and the wind came down from the hills"
        story_b = "The ship turned slowly toward the red planet and the crew watched the deserts rise to meet them in silence that nobody wanted to break before the landing"
        for n, (iid, boxes) in enumerate([("i1", [("story", "The Valley", "A. Author", [story_a, ad])]),
                                            ("i2", [("story", "The Planet", "B. Writer", [story_b, ad])]),
                                            ("i3", [("ad", None, None, [ad])]),
                                            ("i4", [("story", "The Valley", "A. Author", [story_a])])]):
            os.makedirs(os.path.join(d, "data", "assembly_v2", "rules", iid)); os.makedirs(os.path.join(d, "data", "layout", iid))
            regs = []
            arts = []
            for k, (typ, title, author, texts) in enumerate(boxes):
                ids = []
                for t in texts:
                    ids.append(len(regs)); regs.append({"text": t, "order": len(regs), "label": "Text", "bbox": [0, 0, 1, 1]})
                arts.append({"article_id": f"{iid}_a{k}", "type": typ, "title": title, "author": author, "fragments": [{"page": 1, "region_ids": ids}]})
            json.dump({"page": 1, "regions": regs}, open(os.path.join(d, "data", "layout", iid, "page_0001.json"), "w"))
            json.dump({"articles": arts}, open(os.path.join(d, "data", "assembly_v2", "rules", iid, "articles.json"), "w"))
        res = compute(["i1", "i2", "i3", "i4"], workers=2, root=d, shards_dir=os.path.join(d, "shards"), log=lambda *a: None)
        f = res["issues"]
        assert "1:1" in f["i1"] and f["i1"]["1:1"]["share"] == 1.0 and f["i1"]["1:1"]["n_issues"] == 2, f.get("i1")      # the ad: in 3 issues
        assert "1:0" not in f.get("i1", {}), f.get("i1")                                                                  # story_a: only 2 issues (i1, i4)
        assert "1:0" not in f.get("i2", {})
        assert any(p["type"] == "ad" for p in f["i1"]["1:1"]["partners"])
        assert res["summary"]["flagged_boxes"] == 3
    print("selftest ok")
    return True


def split(res, split_dir=SPLIT_DIR):
    """One file per issue, box_recurrence/<issue>.json: "page:idx" -> entry. Files of issues no longer in the result
    are removed, so that the directory always mirrors the last run."""
    os.makedirs(split_dir, exist_ok=True)
    keep = set()
    for iid, boxes in res["issues"].items():
        keep.add(f"{iid}.json")
        with open(os.path.join(split_dir, f"{iid}.json"), "w", encoding="utf-8") as f:
            json.dump(boxes, f, ensure_ascii=False)
    stale = [f for f in os.listdir(split_dir) if f.endswith(".json") and f not in keep]
    for f in stale:
        os.remove(os.path.join(split_dir, f))
    with open(os.path.join(split_dir, "SUMMARY.json"), "w", encoding="utf-8") as f:
        json.dump(res["summary"], f, indent=1)
    return len(keep), len(stale)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--min-issues", type=int, default=3)
    ap.add_argument("--min-tokens", type=int, default=15)
    ap.add_argument("--min-share", type=float, default=0.6)
    ap.add_argument("--issues", help="comma-separated ids (default: every assembled issue)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--include-set-aside", action="store_true", help="count the set-aside items too (the run of 4 November did)")
    ap.add_argument("--split-only", action="store_true", help="write the per-issue files from the existing --out file, no recount")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    split_dir = os.path.join(os.path.dirname(a.out), "box_recurrence") if a.out != OUT else SPLIT_DIR
    if a.split_only:
        res = json.load(open(a.out, encoding="utf-8"))
        n, stale = split(res, split_dir)
        print(f"split: {n:,} issue files written to {os.path.relpath(split_dir, ROOT)} ({stale} stale files removed)")
        return
    if a.issues:
        issues = a.issues.split(",")
    else:
        issues = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "data", "assembly_v2", "rules", "*"))
                        if os.path.exists(os.path.join(p, "articles.json")))
    if not a.include_set_aside:
        from corpus_lib import set_aside
        aside = set(set_aside())
        before = len(issues)
        issues = [i for i in issues if i not in aside]
        print(f"[box_recurrence] {before - len(issues):,} set-aside items left out; {len(issues):,} issues counted")
    res = compute(issues, workers=a.workers, min_issues=a.min_issues, min_tokens=a.min_tokens, min_share=a.min_share)
    res["summary"]["set_aside_included"] = bool(a.include_set_aside)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    n, stale = split(res, split_dir)
    print(json.dumps(res["summary"]))
    print(f"written {os.path.relpath(a.out, ROOT)} and {n:,} issue files in {os.path.relpath(split_dir, ROOT)}")


if __name__ == "__main__":
    main()
