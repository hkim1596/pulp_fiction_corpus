#!/usr/bin/env python3
"""r06 at corpus scale — the hand-reviewed validation set for the near-verbatim detector, drawn from the corpus candidates.

The pilot (r06_validation.py) draws a review set from every candidate pair the paraphrase stage scored: strata by
source (embedding-only, or seeded by an exact match) and by the alignment score band — below any rule, loose rule
only, default rule, strong — and, in the two lowest bands of the embedding-only candidates, by the embedding cosine
(closest 5%, next 20%, the rest); a fixed number from every stratum with a fixed seed, each item carrying the inverse
of its inclusion probability. Two readers judge the items on the site (/reuse/validate?set=corpus), and the
calibration scores every setting of the grid against the judgments (the pilot's calibrate, unchanged).

Here the same draw is made from the arrays r04_paraphrase_corpus wrote (para/aligned/b*_scored.npy: every candidate
region with its batch alignment score), cross-issue only, and the items are written in the pilot's shape under
data/reuse/corpus_dryrun/validation/ (review_set.jsonl, review_set_stats.json; the judgments go beside them). One
difference of treatment is kept from the pilot: a candidate region longer than the window with its context (an exact
seed of more than fifty words) counts as "strong" without a score, as in the pilot's score_candidates.

    python3 pipeline/r06_validation_corpus.py [--per-stratum 25] [--seed 20261015]
    python3 pipeline/r06_validation_corpus.py --selftest
Everything is the engineering dry run of 14 October 2026 onward, not the study's numbers.
"""
import argparse
import glob
import json
import os
import random
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r02_verbatim_corpus as r2c  # noqa: E402
import r04_paraphrase as r4  # noqa: E402
import r04_paraphrase_corpus as r4c  # noqa: E402
import r06_validation as r6  # noqa: E402

SNAP = r2c.OUTDIR
OUTDIR = os.path.join(SNAP, "validation")
LABEL = r2c.LABEL
PER_STRATUM = 25
SEED = 20261015


def load_scored(para_dir):
    files = sorted(glob.glob(os.path.join(para_dir, "aligned", "b*_scored.npy")))
    parts = [np.load(f) for f in files]
    parts = [p for p in parts if p.size]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=r4c.CAND), len(files)


def strata_of(C, corpus):
    """Per candidate row: the source label, the score band, the cosine band (or None), as the pilot assigns them."""
    Lmax = r4.WINDOW + 2 * r4.PAD
    long_ = (~C["direct"].astype(bool)) & ((C["a1"] - C["a0"] > Lmax) | (C["b1"] - C["b0"] > Lmax))
    score = np.where(long_, 10 ** 6, C["score"].astype(np.int64))
    src = np.where(C["src"] & r4c.SRC_EXACT, "exact-seeded", "embedding-only")
    has_sim = C["sim"] > -8
    sims = np.sort(C["sim"][has_sim].astype(np.float64))
    cuts = (float(sims[int(0.95 * (sims.size - 1))]), float(sims[int(0.75 * (sims.size - 1))])) if sims.size else (1.0, 1.0)
    bands = np.array([r6._band(int(s)) for s in score], dtype=object)
    low = np.isin(bands, ["below any rule", "loose rule only"]) & (src == "embedding-only")
    sim_band = np.array([r6._sim_band(float(s) if h else None, cuts) if lo else None for s, h, lo in zip(C["sim"], has_sim, low)], dtype=object)
    return src, bands, sim_band, score, cuts


def _span(u, s, e):
    s, e = max(0, int(s)), min(u["n_tokens"], int(e))
    if e <= s:
        return ""
    off = u["offsets"]
    return u["canon"][off[s][0]:off[e - 1][1]]


def _item(corpus, c, score, st, w):
    ua, ub = corpus.units[int(c["a"])], corpus.units[int(c["b"])]
    tok, bounds = corpus.tok, corpus.bounds
    tr = None
    if score >= r6.SCORE_BANDS[1][0]:
        if c["direct"]:
            n = int(c["a1"] - c["a0"])
            tr = {"a0": int(c["a0"]), "a1": int(c["a1"]), "b0": int(c["b0"]), "b1": int(c["b1"]), "cols": n, "matches": n, "score": n * r4.MATCH}
        else:
            a, b = int(c["a"]), int(c["b"])
            a0, a1 = int(c["a0"]), min(int(c["a1"]), int(c["a0"]) + r4.LONG_SIDE)
            b0, b1 = int(c["b0"]), min(int(c["b1"]), int(c["b0"]) + r4.LONG_SIDE)
            ida = tok[bounds[a] + a0:bounds[a] + a1].astype(np.int32)
            idb = tok[bounds[b] + b0:bounds[b] + b1].astype(np.int32)
            res = r4.sw_single(ida, idb, _item.alts)
            if res is not None:
                sc, x0, x1, y0, y1, cols, matches = res
                tr = {"a0": a0 + x0, "a1": a0 + x1, "b0": b0 + y0, "b1": b0 + y1, "cols": cols, "matches": matches, "score": sc}
    src_name = {1: "embedding", 2: "exact", 3: "embedding+exact"}.get(int(c["src"]), str(int(c["src"])))
    it = {"a": ua["story_id"], "b": ub["story_id"],
          "a_meta": {k: ua["meta"].get(k) for k in ("title", "author", "issue", "magazine", "cover_date")},
          "b_meta": {k: ub["meta"].get(k) for k in ("title", "author", "issue", "magazine", "cover_date")},
          "source": src_name, "rank": int(c["rank"]), "cosine": None if c["sim"] <= -8 else round(float(c["sim"]), 4),
          "score": int(score) if score < 10 ** 6 else None,
          "stratum": {"source": st[0], "score_band": st[1], "cosine_band": st[2]}, "weight": round(w, 3),
          "window_a": _span(ua, c["a0"], c["a1"]), "window_b": _span(ub, c["b0"], c["b1"]),
          "window_a_tok": [int(c["a0"]), int(c["a1"])], "window_b_tok": [int(c["b0"]), int(c["b1"])]}
    if tr:
        it["aligned"] = {"cols": tr["cols"], "matches": tr["matches"], "score": tr["score"],
                         "identity": round(tr["matches"] / tr["cols"], 4) if tr["cols"] else 0.0,
                         "a_tok": [tr["a0"], tr["a1"]], "b_tok": [tr["b0"], tr["b1"]],
                         "text_a": _span(ua, tr["a0"], tr["a1"]), "text_b": _span(ub, tr["b0"], tr["b1"])}
    else:
        it["aligned"] = None
    for u in (ua, ub):
        u.drop_text()
    return it


def build(snap=SNAP, outdir=OUTDIR, per_stratum=PER_STRATUM, seed=SEED, log=print):
    os.makedirs(outdir, exist_ok=True)
    corpus = r2c.Corpus.load(snap)
    para = os.path.join(snap, "para")
    C, n_files = load_scored(para)
    cross = corpus.issue_of[C["a"]] != corpus.issue_of[C["b"]]
    C = C[cross]
    log(f"[r06c] {C.size:,} cross-issue candidate regions from {n_files} bucket files")
    src, bands, sim_band, score, cuts = strata_of(C, corpus)
    strata = defaultdict(list)
    for i in range(C.size):
        strata[(str(src[i]), str(bands[i]), sim_band[i])].append(i)
    _item.alts = np.load(os.path.join(para, "alts.npy"))
    rng = random.Random(seed)
    items, table = [], []
    for st in sorted(strata, key=lambda s: (s[0], [b[2] for b in r6.SCORE_BANDS].index(s[1]), str(s[2]))):
        pool = strata[st]
        n = min(per_stratum, len(pool))
        draw = rng.sample(pool, n) if n else []
        w = len(pool) / n if n else 0.0
        table.append({"source": st[0], "score_band": st[1], "cosine_band": st[2], "candidates": len(pool), "drawn": n, "weight": round(w, 3)})
        for i in draw:
            items.append(_item(corpus, C[i], int(score[i]), st, w))
    rng.shuffle(items)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for i, it in enumerate(items, 1):
        it["id"] = f"c{i:03d}"
        it["set_id"] = stamp
    with open(os.path.join(outdir, "review_set.jsonl"), "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    stats = {"set": "machine", "collection": os.path.basename(snap), "label": LABEL, "set_id": stamp, "generated": time.strftime("%Y-%m-%d %H:%M"),
             "stories": corpus.n_stories, "k_retrieve": r4c.K_RETRIEVE, "candidates_cross_issue": int(C.size),
             "cosine_cuts": {"closest_5pct": round(cuts[0], 4), "next_20pct": round(cuts[1], 4)},
             "per_stratum": per_stratum, "seed": seed, "items": len(items), "strata": table,
             "grid": {"k": list(r6.GRID_K), "rules": [list(r) for r in r6.GRID_RULES]}, "precision_floor": r6.PRECISION_FLOOR,
             "score_bands": [[lo if lo > -10 ** 8 else None, hi if hi < 10 ** 8 else None, name] for lo, hi, name in r6.SCORE_BANDS]}
    json.dump(stats, open(os.path.join(outdir, "review_set_stats.json"), "w", encoding="utf-8"), indent=1)
    log(f"[r06c] review set {stamp}: {len(items)} items from {C.size:,} cross-issue candidates in {len(table)} strata")
    return stats


def selftest():
    """The strata table against the pilot's r06.build on the same fabricated stories and the same neighbours (fake
    embeddings), and the items' shape as the review page reads it."""
    import tempfile
    import r02_verbatim as r2
    ok = True
    rng = random.Random(31)
    recs = r2c._random_corpus(rng, n_stories=20, n_issues=8, vocab=400, planted=10)
    for r in recs:
        words = r["text"].split()
        r["text"] = ". ".join(" ".join(words[i:i + 10]) for i in range(0, len(words), 10))
        r["title"] = "T " + r["story_id"]; r["magazine"] = "M"; r["cover_date"] = "1930-01"
    by_issue = defaultdict(list)
    for r in recs:
        by_issue[r["issue"]].append(r)
    with tempfile.TemporaryDirectory() as td:
        exp = os.path.join(td, "export"); os.makedirs(exp)
        for iid, rs in by_issue.items():
            with open(os.path.join(exp, f"{iid}.jsonl"), "w", encoding="utf-8") as f:
                for r in rs:
                    f.write(json.dumps(r) + "\n")
        snap = os.path.join(td, "corpus_dryrun")
        r2c.tokenize_corpus(exp, snap, workers=2, log=lambda *a: None)
        corpus = r2c.Corpus.load(snap)
        r2c.run(corpus, r4.EXACT_SEED_K, "machine", snap, log=lambda *a: None)
        para = os.path.join(snap, "para")
        saved = (r4c.BUCKET_CANDS, r4c.EMB_CHUNK)
        r4c.BUCKET_CANDS, r4c.EMB_CHUNK = 400, 50
        try:
            r4c.step_alts(corpus, para, log=lambda *a: None); r4c.step_passages(corpus, para, log=lambda *a: None)
            r4c.step_embed(snap, para, backend="fake", workers=2, log=lambda *a: None)
            r4c.step_knn(para, backend="numpy", k=r6.K_RETRIEVE, log=lambda *a: None)
            r4c.step_candidates(corpus, para, snap, log=lambda *a: None)
            r4c.step_align(snap, para, workers=2, ks=(10,), log=lambda *a: None)
        finally:
            r4c.BUCKET_CANDS, r4c.EMB_CHUNK = saved
        st_c = build(snap, os.path.join(snap, "validation"), per_stratum=4, seed=7, log=lambda *a: None)
        # the pilot's build on the same stories and neighbours
        units = r2.build_units([r for iid in sorted(by_issue) for r in by_issue[iid]])
        idx = np.load(os.path.join(para, "knn_idx.npy")); sim = np.load(os.path.join(para, "knn_sim.npy"))
        P = np.load(os.path.join(para, "passages.npy"))
        pl = r4.make_passages(units, r4.WINDOW, r4.STRIDE)
        nb = [(int(i), int(idx[i, r]), r + 1, float(sim[i, r])) for i in range(P.shape[0]) for r in range(idx.shape[1]) if idx[i, r] >= 0]
        orig_knn, orig_embed = r4.knn, r4.embed_passages
        r4.knn = lambda vec, passages, k, block=1024: nb
        r4.embed_passages = lambda units_, passages, cache_path, log=print: r4c.fake_embed([r4.passage_text(units_, p) for p in passages])
        saved_out = r4.OUTDIR
        r4.OUTDIR = os.path.join(td, "para_pilot"); os.makedirs(r4.OUTDIR)
        stories = os.path.join(td, "stories.jsonl")
        with open(stories, "w", encoding="utf-8") as f:
            for iid in sorted(by_issue):
                for r in by_issue[iid]:
                    f.write(json.dumps(r) + "\n")
        try:
            st_p = r6.build("machine", stories, per_stratum=4, outdir=os.path.join(td, "validation_pilot"), log=lambda *a: None, seed=7)
        finally:
            r4.knn, r4.embed_passages, r4.OUTDIR = orig_knn, orig_embed, saved_out
        tp = {(t["source"], t["score_band"], t["cosine_band"]): (t["candidates"], t["drawn"]) for t in st_p["strata"]}
        tc = {(t["source"], t["score_band"], t["cosine_band"]): (t["candidates"], t["drawn"]) for t in st_c["strata"]}
        same = tp == tc and st_p["candidates_cross_issue"] == st_c["candidates_cross_issue"] and st_p["cosine_cuts"] == st_c["cosine_cuts"]
        items = [json.loads(l) for l in open(os.path.join(snap, "validation", "review_set.jsonl"), encoding="utf-8")]
        shape = all(set(it) >= {"a", "b", "a_meta", "b_meta", "source", "rank", "cosine", "score", "stratum", "weight", "window_a", "window_b", "aligned", "id", "set_id"}
                    and it["window_a"] and it["window_b"] for it in items)
        aligned_ok = all((it["aligned"] is None) == (it["score"] is not None and it["score"] < 9) for it in items)
        print(f"strata: pilot {len(tp)} / corpus {len(tc)} ({st_p['candidates_cross_issue']} vs {st_c['candidates_cross_issue']} cross-issue candidates; cuts "
              f"{st_p['cosine_cuts']} vs {st_c['cosine_cuts']}) {'same' if same else 'DIFFER'}; items {len(items)} ({st_p['items']} by the pilot), shape "
              f"{'ok' if shape else 'BAD'}, aligned where the score reaches the loose band {'ok' if aligned_ok else 'BAD'} -> {'OK' if same and shape and aligned_ok else 'FAIL'}")
        if not same:
            print("  pilot:", sorted(tp.items())); print("  corpus:", sorted(tc.items()))
        ok = ok and same and shape and aligned_ok
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=SNAP)
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--per-stratum", type=int, default=PER_STRATUM)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    build(args.snap, args.out, args.per_stratum, args.seed)


if __name__ == "__main__":
    main()
