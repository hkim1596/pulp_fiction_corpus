#!/usr/bin/env python3
"""r04 at corpus scale — near-verbatim and rewritten reuse (protocol section 3.2) over the whole corpus.

The method is r04_paraphrase.py's, step for step: every story cut into passages of 50 words at a step of 25; every
passage embedded (BAAI/bge-small-en-v1.5) and its K nearest passages in other stories retrieved (K = 20; 5 and 10
read off by rank); every exact match of r02 (seed 6) a candidate too; each candidate region, widened by 25 words,
aligned word by word (Smith-Waterman, match +2, mismatch -1, gap -1, words of five letters or more equal when one edit
apart); alignments of one story pair that touch are joined and re-aligned; an alignment is kept at 20 columns and
identity 0.60; same-issue alignments set apart. The pilot's own functions do the alignment, the joining, the keep
rule and the serialization (imported from r04_paraphrase). What changes is the engineering, in six steps that each
write their result under data/reuse/corpus_dryrun/para/ and can be run again from where they stopped:

    alts        the fuzzy alternates of the snapshot's vocabulary (one edit apart, words of five letters or more)
    passages    the passage table (story, start, end) over the r02 snapshot (data/reuse/corpus_dryrun/tokens/)
    embed       the passage vectors, float16, in parallel processes (fastembed on the processor, the pilot's model
                and runtime) or on a card (sentence-transformers, when present and chosen)
    knn         the K nearest passages in other stories: exact, by blocks of matrix products on a card (torch) or
                the processor (numpy; slow at corpus scale); faiss (approximate, recorded as such) when chosen
    align       the candidate regions (neighbours and exact seeds) bucketed by story pair on disk, scored in batches
                in parallel processes, traced back when they reach the keep score, joined and kept per K
    report      the pilot's files per K under para/: machine_w50s25_k<K>_{alignments.jsonl, sameissue.jsonl,
                clusters.json, pairs.json, story_share.json, stats.json}, plus the arrays (_alignments.npy,
                _pairs.npz) with every kept alignment

    python3 pipeline/r04_paraphrase_corpus.py all --workers 60            # every step in order (each skips what is done)
    python3 pipeline/r04_paraphrase_corpus.py embed --backend fastembed --workers 30
    python3 pipeline/r04_paraphrase_corpus.py knn --backend torch --device cuda:2
    python3 pipeline/r04_paraphrase_corpus.py --selftest
    python3 pipeline/r04_paraphrase_corpus.py --pilot-check [--stories data/pilot_stories.jsonl]

Sizes at corpus scale (half a billion tokens): about 20 million passages; the vectors 15 GB (float16); the
neighbour table 400 million pairs plus the exact seeds; the alignments in 60 processes a few hours (the batch scorer
does about 5,000 candidate pairs a second per process). Limits of size, all recorded in the stats: the text files
and the clusters hold the kept alignments of WRITE_FLOOR_COLS columns or more; every kept alignment is in the arrays.
Everything here is the engineering dry run of 14 October 2026 onward, not the study's numbers.
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r02_verbatim as r2  # noqa: E402
import r02_verbatim_corpus as r2c  # noqa: E402
import r04_paraphrase as r4  # noqa: E402

ROOT = r2c.ROOT
SNAP = r2c.OUTDIR                                  # data/reuse/corpus_dryrun (the r02 snapshot and results)
OUTDIR = os.path.join(SNAP, "para")
LABEL = r2c.LABEL
MODEL = r4.MODEL
WINDOW, STRIDE, PAD = r4.WINDOW, r4.STRIDE, r4.PAD
K_ALL = r4.K_ALL
K_RETRIEVE = max(K_ALL)
EMB_DIM = 384
EMB_CHUNK = 20_000           # passages per embedding chunk (one done-mark each)
KNN_Q, KNN_C = 2048, 1_000_000
BUCKET_CANDS = 4_000_000     # candidate regions per alignment bucket
WRITE_FLOOR_COLS = 40        # kept alignments of this many columns or more go to the text files and the clusters
PAIRS_JSON = 20_000
SET_NAME = "machine"

CAND = np.dtype([("a", "<i4"), ("b", "<i4"), ("a0", "<i4"), ("a1", "<i4"), ("b0", "<i4"), ("b1", "<i4"),
                 ("src", "u1"), ("rank", "u1"), ("direct", "u1"), ("sim", "<f4"), ("score", "<i4")])
RAW = np.dtype([("a", "<i4"), ("b", "<i4"), ("a0", "<i4"), ("a1", "<i4"), ("b0", "<i4"), ("b1", "<i4"),
                ("cols", "<i4"), ("matches", "<i4"), ("score", "<i4"), ("src", "u1"), ("rank", "u1"), ("sim", "<f4")])
SRC_EMB, SRC_EXACT = 1, 2
SRC_NAME = {1: "embedding", 2: "exact", 3: "embedding+exact"}
NO_SIM = np.float32(-9.0)    # "no cosine" (an exact seed that no neighbour pair repeats)

_W = {}                      # worker state (set once per process by the pool initializers)


def _log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 1. the fuzzy alternates

def build_alts(vocab, freq, log=_log):
    """r04_paraphrase.build_vocab's alternates for the snapshot's vocabulary: for every type of FUZZY_MIN_LEN letters
    or more, up to FUZZY_ALTS types one edit apart, ranked by corpus frequency (then name), as the pilot ranks them."""
    t0 = time.time()
    alts = np.full((len(vocab), r4.FUZZY_ALTS), -1, dtype=np.int32)
    buckets = defaultdict(list)
    long_ids = [i for i, t in enumerate(vocab) if len(t) >= r4.FUZZY_MIN_LEN]
    for i in long_ids:
        t = vocab[i]
        buckets[t].append(i)
        for j in range(len(t)):
            buckets[t[:j] + t[j + 1:]].append(i)
    for n, i in enumerate(long_ids):
        t = vocab[i]
        cands = set(buckets.get(t, ()))
        for j in range(len(t)):
            cands.update(buckets.get(t[:j] + t[j + 1:], ()))
        cands.discard(i)
        good = [c for c in cands if r4._lev_le1(t, vocab[c])]
        good.sort(key=lambda c: (-int(freq[c]), vocab[c]))
        for j, c in enumerate(good[:r4.FUZZY_ALTS]):
            alts[i, j] = c
        if log and n and n % 1_000_000 == 0:
            log(f"[r04c]   alternates for {n:,} of {len(long_ids):,} types, {time.time() - t0:.0f}s")
    if log:
        log(f"[r04c] alternates: {len(vocab):,} types, {len(long_ids):,} of five letters or more, "
            f"{int((alts[:, 0] >= 0).sum()):,} with an alternate; {time.time() - t0:.0f}s")
    return alts


def step_alts(corpus, outdir=OUTDIR, log=_log):
    p = os.path.join(outdir, "alts.npy")
    if os.path.exists(p):
        a = np.load(p)
        if a.shape[0] == len(corpus.vocab):
            return a
    os.makedirs(outdir, exist_ok=True)
    freq = np.bincount(corpus.tok, minlength=len(corpus.vocab))
    alts = build_alts(corpus.vocab, freq, log)
    np.save(p, alts)
    return alts


# ---------------------------------------------------------------- 2. the passages

def make_passages(bounds, window=WINDOW, stride=STRIDE):
    """r04_paraphrase.make_passages over story bounds, as arrays: int32 (N, 3) of story, start, end (positions within
    the story). A story of `window` tokens or fewer is one passage; otherwise starts 0, stride, 2*stride … up to n - window,
    and n - window itself when the last start did not reach it."""
    n = np.diff(bounds).astype(np.int64)
    short = n <= window
    cnt = np.where(short, 1, (n - window) // stride + 1)
    extra = (~short) & ((n - window) % stride != 0)
    cnt = cnt + extra
    story = np.repeat(np.arange(n.size, dtype=np.int64), cnt)
    first = np.repeat(np.cumsum(cnt) - cnt, cnt)
    r = np.arange(story.size, dtype=np.int64) - first
    start = r * stride
    last = np.repeat(cnt, cnt) - 1
    is_extra = np.repeat(extra, cnt) & (r == last)
    start = np.where(is_extra, n[story] - window, start)
    end = np.where(np.repeat(short, cnt), n[story], start + window)
    start = np.where(np.repeat(short, cnt), 0, start)
    return np.stack([story, start, end], axis=1).astype(np.int32)


def step_passages(corpus, outdir=OUTDIR, log=_log):
    p = os.path.join(outdir, "passages.npy")
    if os.path.exists(p):
        return np.load(p)
    os.makedirs(outdir, exist_ok=True)
    P = make_passages(corpus.bounds)
    np.save(p, P)
    log(f"[r04c] {P.shape[0]:,} passages (window {WINDOW}, stride {STRIDE}) over {corpus.n_stories:,} stories")
    return P


def passage_texts(corpus, P, rows):
    """The canonical text of the passages P[rows] (rows sorted, so each story's text is read once)."""
    out = []
    last_s, u = -1, None
    for s, a, b in P[rows]:
        if s != last_s:
            if u is not None:
                u.drop_text()
            u = corpus.units[int(s)]
            last_s = s
        off = u["offsets"]
        out.append(u["canon"][off[int(a)][0]:off[int(b) - 1][1]])
    if u is not None:
        u.drop_text()
    return out


# ---------------------------------------------------------------- 3. the embeddings

def fake_embed(texts, dim=64):
    """A deterministic stand-in for the model in the tests: hashed word counts, L2-normalised."""
    import hashlib
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in t.lower().split():
            h = int.from_bytes(hashlib.blake2b(w.encode("utf-8"), digest_size=4).digest(), "little")
            out[i, h % dim] += 1.0 if (h >> 20) & 1 else -1.0
    out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)
    return out


def _model_init(backend, device, threads):
    """Pool initializer: the embedding model, once per process (the corpus and the passages are inherited from the
    parent, which loads them before the pool forks)."""
    _W["backend"] = backend
    if backend == "fastembed":
        from fastembed import TextEmbedding
        _W["model"] = TextEmbedding(MODEL, threads=threads)
    elif backend == "st":
        from sentence_transformers import SentenceTransformer
        _W["model"] = SentenceTransformer(MODEL, device=device)


def embed_texts(texts, backend="fake", model=None):
    if backend == "fake":
        return fake_embed(texts)
    if backend == "fastembed":
        vec = np.vstack([np.asarray(v, dtype=np.float32) for v in model.embed(texts, batch_size=64)])
    elif backend == "st":
        vec = np.asarray(model.encode(texts, batch_size=256, convert_to_numpy=True, show_progress_bar=False), dtype=np.float32)
    else:
        raise ValueError(backend)
    vec /= np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-9)
    return vec


def _emb_chunk(c):
    """One worker: the passages of chunk c embedded and written into the float16 memmap; a done-mark afterwards."""
    P, corpus, outdir = _W["P"], _W["corpus"], _W["outdir"]
    n = P.shape[0]
    r0, r1 = c * EMB_CHUNK, min(n, (c + 1) * EMB_CHUNK)
    mark = os.path.join(outdir, "emb_done", f"{c:06d}.ok")
    if os.path.exists(mark):
        return c, 0, 0.0
    t0 = time.time()
    texts = passage_texts(corpus, P, np.arange(r0, r1))
    vec = embed_texts(texts, _W["backend"], _W.get("model"))
    dim = vec.shape[1]
    mm = np.memmap(os.path.join(outdir, "emb.f16"), dtype=np.float16, mode="r+", shape=(n, dim))
    mm[r0:r1] = vec.astype(np.float16)
    mm.flush()
    del mm
    open(mark, "w").close()
    return c, r1 - r0, time.time() - t0


def step_embed(snap=SNAP, outdir=OUTDIR, backend="fastembed", workers=30, device="cpu", threads=2, log=_log):
    """All passages embedded into outdir/emb.f16 (N x dim float16), chunk by chunk, resumable."""
    P = np.load(os.path.join(outdir, "passages.npy"))
    n = P.shape[0]
    dim = EMB_DIM if backend != "fake" else 64
    meta_p = os.path.join(outdir, "emb_meta.json")
    emb_p = os.path.join(outdir, "emb.f16")
    if os.path.exists(meta_p):
        meta = json.load(open(meta_p, encoding="utf-8"))
        if meta.get("done") and meta["n"] == n:
            log(f"[r04c] embeddings present: {n:,} x {meta['dim']} ({meta['backend']})")
            return meta
        if meta["n"] != n or meta["backend"] != backend:
            raise SystemExit(f"emb_meta.json is for {meta['n']} passages / {meta['backend']}; remove para/emb* to start over")
    else:
        np.memmap(emb_p, dtype=np.float16, mode="w+", shape=(n, dim)).flush()
        meta = {"n": n, "dim": dim, "backend": backend, "model": MODEL if backend != "fake" else "fake",
                "device": device, "chunk": EMB_CHUNK, "started": time.strftime("%Y-%m-%d %H:%M"), "done": False}
        json.dump(meta, open(meta_p, "w", encoding="utf-8"), indent=1)
    os.makedirs(os.path.join(outdir, "emb_done"), exist_ok=True)
    if backend == "fastembed":                       # the model files are fetched once here, not by every worker at once
        from fastembed import TextEmbedding
        TextEmbedding(MODEL, threads=1)
    chunks = [c for c in range(-(-n // EMB_CHUNK)) if not os.path.exists(os.path.join(outdir, "emb_done", f"{c:06d}.ok"))]
    log(f"[r04c] embedding {n:,} passages in {len(chunks)} chunks of {EMB_CHUNK:,} ({backend}, {workers} process{'es' if workers > 1 else ''})")
    t0 = time.time()
    done_p = 0
    if backend == "st":
        workers = 1
    _W["corpus"] = r2c.Corpus.load(snap, mmap=True)
    _W["P"] = P
    _W["outdir"] = outdir
    with Pool(workers, initializer=_model_init, initargs=(backend, device, threads)) as pool:
        for i, (c, np_, dt) in enumerate(pool.imap_unordered(_emb_chunk, chunks)):
            done_p += np_
            if (i + 1) % 20 == 0 or i + 1 == len(chunks):
                rate = done_p / max(time.time() - t0, 1e-9)
                log(f"[r04c]   {i + 1}/{len(chunks)} chunks, {done_p:,} passages, {rate:.0f}/s, {time.time() - t0:.0f}s")
    meta["done"] = True
    meta["finished"] = time.strftime("%Y-%m-%d %H:%M")
    meta["seconds"] = round(time.time() - t0, 1)
    json.dump(meta, open(meta_p, "w", encoding="utf-8"), indent=1)
    return meta


def load_emb(outdir=OUTDIR):
    meta = json.load(open(os.path.join(outdir, "emb_meta.json"), encoding="utf-8"))
    return np.memmap(os.path.join(outdir, "emb.f16"), dtype=np.float16, mode="r", shape=(meta["n"], meta["dim"])), meta


# ---------------------------------------------------------------- 4. the nearest passages

def _story_ranges(pstory):
    """first and last+1 passage index of every story (passages are in story order)."""
    n_st = int(pstory.max()) + 1 if pstory.size else 0
    first = np.searchsorted(pstory, np.arange(n_st), side="left")
    last = np.searchsorted(pstory, np.arange(n_st), side="right")
    return first, last


def knn_exact(emb, pstory, k=K_RETRIEVE, backend="numpy", device="cpu", q_block=KNN_Q, c_block=KNN_C, log=_log):
    """For every passage its k most similar passages in other stories, exactly (every pair compared, by blocks of
    matrix products), as the pilot's knn: (idx int32 N x k, sim float32 N x k), sorted by similarity, index -1 and
    sim -2 where fewer than k exist. The corpus is taken in blocks of c_block passages (each on the card once, with
    torch), the queries in blocks of q_block; the running best k per query live in memory on the processor."""
    n = emb.shape[0]
    first, last = _story_ranges(pstory)
    run_v = np.full((n, k), -2.0, dtype=np.float32)
    run_i = np.full((n, k), -1, dtype=np.int64)
    t0 = time.time()
    if backend == "torch":
        import torch
        dev = torch.device(device)
    for c0 in range(0, n, c_block):
        c1 = min(n, c0 + c_block)
        if backend == "torch":
            Cm = torch.from_numpy(np.ascontiguousarray(emb[c0:c1])).to(dev)          # float16
        else:
            Cm = np.asarray(emb[c0:c1], dtype=np.float32)
        kk = min(k, c1 - c0)
        for q0 in range(0, n, q_block):
            q1 = min(n, q0 + q_block)
            st = pstory[q0:q1]
            lo_all, hi_all = int(first[st.min()]), int(last[st.max()])
            if backend == "torch":
                Q = torch.from_numpy(np.ascontiguousarray(emb[q0:q1])).to(dev)
                sims = Q @ Cm.T
            else:
                sims = np.asarray(emb[q0:q1], dtype=np.float32) @ Cm.T
            if c0 < hi_all and c1 > lo_all:                           # the block holds passages of the queries' own stories
                m0, m1 = max(c0, lo_all), min(c1, hi_all)
                cols = np.arange(m0, m1)
                mask = (cols[None, :] >= first[st][:, None]) & (cols[None, :] < last[st][:, None])
                if backend == "torch":
                    sub = sims[:, m0 - c0:m1 - c0]
                    sub[torch.from_numpy(mask).to(dev)] = -2.0
                else:
                    sims[:, m0 - c0:m1 - c0][mask] = -2.0
            if backend == "torch":
                v, i = torch.topk(sims, kk, dim=1)
                v, i = v.float().cpu().numpy(), i.cpu().numpy().astype(np.int64)
            else:
                i = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
                v = np.take_along_axis(sims, i, axis=1)
            i = i + c0
            cat_v = np.concatenate([run_v[q0:q1], v], axis=1)
            cat_i = np.concatenate([run_i[q0:q1], i], axis=1)
            o = np.argsort(-cat_v, axis=1, kind="stable")[:, :k]
            run_v[q0:q1] = np.take_along_axis(cat_v, o, axis=1)
            run_i[q0:q1] = np.take_along_axis(cat_i, o, axis=1)
        if log:
            log(f"[r04c]   corpus block {c0:,}-{c1:,} of {n:,} compared with every passage, {time.time() - t0:.0f}s")
    bad = run_v <= -1.5
    idx_out = np.where(bad, -1, run_i).astype(np.int32)
    sim_out = np.where(bad, -2.0, run_v).astype(np.float32)
    return idx_out, sim_out


def knn_faiss(emb, pstory, k=K_RETRIEVE, nlist=16384, nprobe=64, extra=64, log=_log):
    """Approximate nearest passages with a faiss inverted-list index (recorded as approximate in the stats):
    k + extra neighbours searched, the same story's passages removed afterwards."""
    import faiss
    n, d = emb.shape
    first, last = _story_ranges(pstory)
    t0 = time.time()
    quant = faiss.IndexFlatIP(d)
    index = faiss.IndexIVFFlat(quant, d, min(nlist, max(1, n // 39)), faiss.METRIC_INNER_PRODUCT)
    rng = np.random.default_rng(0)
    train = np.asarray(emb[np.sort(rng.choice(n, min(n, 2_000_000), replace=False))], dtype=np.float32)
    index.train(train)
    for s in range(0, n, 1_000_000):
        index.add(np.asarray(emb[s:s + 1_000_000], dtype=np.float32))
    index.nprobe = nprobe
    log(f"[r04c] faiss index built ({index.nlist} lists, nprobe {nprobe}), {time.time() - t0:.0f}s")
    idx_out = np.full((n, k), -1, dtype=np.int32)
    sim_out = np.full((n, k), -2.0, dtype=np.float32)
    for q0 in range(0, n, 100_000):
        q1 = min(n, q0 + 100_000)
        D, I = index.search(np.asarray(emb[q0:q1], dtype=np.float32), k + extra)
        st = pstory[q0:q1]
        own = (I >= first[st][:, None]) & (I < last[st][:, None])
        D = np.where(own | (I < 0), -2.0, D)
        o = np.argsort(-D, axis=1, kind="stable")[:, :k]
        idx_out[q0:q1] = np.take_along_axis(I, o, axis=1)
        sim_out[q0:q1] = np.take_along_axis(D, o, axis=1)
        if log and (q0 // 100_000) % 20 == 0:
            log(f"[r04c]   neighbours for {q1:,}/{n:,} passages, {time.time() - t0:.0f}s")
    idx_out[sim_out <= -1.5] = -1
    return idx_out, sim_out


def step_knn(outdir=OUTDIR, backend="torch", device="cuda:0", k=K_RETRIEVE, log=_log):
    p_idx, p_sim, p_meta = (os.path.join(outdir, f) for f in ("knn_idx.npy", "knn_sim.npy", "knn_meta.json"))
    if os.path.exists(p_meta):
        meta = json.load(open(p_meta, encoding="utf-8"))
        log(f"[r04c] neighbours present: K={meta['k']}, {meta['backend']}{' (approximate)' if meta.get('approximate') else ''}")
        return np.load(p_idx), np.load(p_sim), meta
    emb, emeta = load_emb(outdir)
    P = np.load(os.path.join(outdir, "passages.npy"))
    pstory = P[:, 0].astype(np.int64)
    t0 = time.time()
    if backend == "faiss":
        idx, sim = knn_faiss(emb, pstory, k, log=log)
    else:
        idx, sim = knn_exact(emb, pstory, k, backend=backend, device=device, log=log)
    np.save(p_idx, idx)
    np.save(p_sim, sim)
    meta = {"k": k, "backend": backend, "device": device if backend == "torch" else None, "approximate": backend == "faiss",
            "passages": int(P.shape[0]), "embedding": emeta.get("backend"), "seconds": round(time.time() - t0, 1),
            "generated": time.strftime("%Y-%m-%d %H:%M")}
    json.dump(meta, open(p_meta, "w", encoding="utf-8"), indent=1)
    log(f"[r04c] neighbours: {P.shape[0]:,} passages x {k}, {meta['seconds']}s ({backend})")
    return idx, sim, meta


# ---------------------------------------------------------------- 5. the candidates and the alignment

def _regions_from_neighbours(P, n_tok, idx, sim, r0, r1):
    """Candidate regions of the neighbour pairs of passages r0..r1 (CAND rows): the pilot's gather_candidates.add."""
    k = idx.shape[1]
    qi = np.repeat(np.arange(r0, r1), k)
    nj = idx[r0:r1].ravel()
    rank = np.tile(np.arange(1, k + 1, dtype=np.int64), r1 - r0)
    sm = sim[r0:r1].ravel()
    ok = nj >= 0
    qi, nj, rank, sm = qi[ok], nj[ok], rank[ok], sm[ok]
    sa, sb = P[qi, 0].astype(np.int64), P[nj, 0].astype(np.int64)
    sw = sa > sb
    pa, pb = np.where(sw, nj, qi), np.where(sw, qi, nj)
    sa, sb = np.where(sw, sb, sa), np.where(sw, sa, sb)
    out = np.zeros(sa.size, dtype=CAND)
    out["a"], out["b"] = sa, sb
    out["a0"] = np.maximum(0, P[pa, 1] - PAD)
    out["a1"] = np.minimum(n_tok[sa], P[pa, 2] + PAD)
    out["b0"] = np.maximum(0, P[pb, 1] - PAD)
    out["b1"] = np.minimum(n_tok[sb], P[pb, 2] + PAD)
    out["src"] = SRC_EMB
    out["rank"] = rank
    out["sim"] = sm
    return out[sa != sb]


def _regions_from_exact(M, n_tok):
    """Candidate regions of r02's exact matches (int32 n x 6: a, b, a0, a1, b0, b1): widened by PAD, rank 0; matches of
    LONG_EXACT tokens or more are recorded directly (identity 1.0 by construction)."""
    out = np.zeros(M.shape[0], dtype=CAND)
    a, b = M[:, 0].astype(np.int64), M[:, 1].astype(np.int64)
    L = M[:, 3] - M[:, 2]
    direct = L >= r4.LONG_EXACT
    out["a"], out["b"] = a, b
    out["a0"] = np.where(direct, M[:, 2], np.maximum(0, M[:, 2] - PAD))
    out["a1"] = np.where(direct, M[:, 3], np.minimum(n_tok[a], M[:, 3] + PAD))
    out["b0"] = np.where(direct, M[:, 4], np.maximum(0, M[:, 4] - PAD))
    out["b1"] = np.where(direct, M[:, 5], np.minimum(n_tok[b], M[:, 5] + PAD))
    out["src"] = SRC_EXACT
    out["rank"] = 0
    out["direct"] = direct
    out["sim"] = NO_SIM
    return out


def step_candidates(corpus, outdir=OUTDIR, snap=SNAP, log=_log):
    """The neighbour pairs and the exact seeds as candidate regions, written to buckets by story pair (bucket of a
    pair = hash of its first story), outdir/cands/b<i>.cand. Returns the number of buckets."""
    cdir = os.path.join(outdir, "cands")
    done = os.path.join(cdir, "DONE.json")
    if os.path.exists(done):
        d = json.load(open(done, encoding="utf-8"))
        log(f"[r04c] candidates present: {d['candidates']:,} in {d['buckets']} buckets")
        return d["buckets"]
    if os.path.isdir(cdir):
        shutil.rmtree(cdir)
    os.makedirs(cdir)
    P = np.load(os.path.join(outdir, "passages.npy"))
    idx = np.load(os.path.join(outdir, "knn_idx.npy"), mmap_mode="r")
    sim = np.load(os.path.join(outdir, "knn_sim.npy"), mmap_mode="r")
    n_tok = np.diff(corpus.bounds)
    exact = [np.load(os.path.join(snap, f"{SET_NAME}_k{r4.EXACT_SEED_K}_{nm}.npy")) for nm in ("matches", "sameissue")
             if os.path.exists(os.path.join(snap, f"{SET_NAME}_k{r4.EXACT_SEED_K}_{nm}.npy"))]
    n_exact = sum(int(m.shape[0]) for m in exact)
    total = int((np.asarray(idx) >= 0).sum()) + n_exact
    n_b = max(1, -(-total // BUCKET_CANDS))
    handles = [open(os.path.join(cdir, f"b{i:04d}.cand"), "wb") for i in range(n_b)]
    t0 = time.time()
    n_written = 0

    def put(rows):
        nonlocal n_written
        if not rows.size:
            return
        bk = (rows["a"].astype(np.int64) * 2654435761 % n_b)
        o = np.argsort(bk, kind="stable")
        rows, bk = rows[o], bk[o]
        cuts = np.r_[0, np.flatnonzero(bk[1:] != bk[:-1]) + 1, bk.size]
        for c0, c1 in zip(cuts[:-1], cuts[1:]):
            handles[int(bk[c0])].write(rows[c0:c1].tobytes())
        n_written += int(rows.size)
    step = 500_000
    for r0 in range(0, P.shape[0], step):
        r1 = min(P.shape[0], r0 + step)
        put(_regions_from_neighbours(P, n_tok, idx, sim, r0, r1))
        if log and (r0 // step) % 10 == 0:
            log(f"[r04c]   candidates of {r1:,}/{P.shape[0]:,} passages written, {time.time() - t0:.0f}s")
    for m in exact:
        for s in range(0, m.shape[0], 2_000_000):
            put(_regions_from_exact(m[s:s + 2_000_000], n_tok))
    for h in handles:
        h.close()
    d = {"candidates": n_written, "buckets": n_b, "exact_seeds": n_exact, "neighbour_pairs": n_written - n_exact,
         "generated": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t0, 1)}
    json.dump(d, open(done, "w", encoding="utf-8"), indent=1)
    log(f"[r04c] {n_written:,} candidate regions ({n_exact:,} exact seeds) in {n_b} buckets, {d['seconds']}s")
    return n_b


def dedup_candidates(C):
    """One row per (a, b, a0, a1, b0, b1): sources combined, the best rank, the highest cosine (the pilot's rule)."""
    if not C.size:
        return C
    o = np.lexsort((C["direct"], C["b1"], C["b0"], C["a1"], C["a0"], C["b"], C["a"]))
    C = C[o]
    same = np.ones(C.size, dtype=bool)
    same[0] = False
    for f in ("a", "b", "a0", "a1", "b0", "b1", "direct"):
        same[1:] &= C[f][1:] == C[f][:-1]
    starts = np.flatnonzero(~same)
    out = C[starts].copy()
    out["src"] = np.bitwise_or.reduceat(C["src"], starts)
    out["rank"] = np.minimum.reduceat(C["rank"], starts)
    out["sim"] = np.maximum.reduceat(C["sim"], starts)
    return out


def _ids_matrix(tok, bounds, s, p0, p1, L, pad):
    """int32 (rows, L): the token ids of story s from p0 to p1 (each shorter than L), padded with `pad`."""
    base = bounds[s] + p0
    cols = np.arange(L, dtype=np.int64)
    idx = base[:, None] + cols[None, :]
    valid = cols[None, :] < (p1 - p0)[:, None]
    idx = np.clip(idx, 0, tok.shape[0] - 1)
    out = tok[idx].astype(np.int32)
    out[~valid] = pad
    return out


def score_and_trace(corpus, C, alts, batch=1024):
    """The pilot's align_candidates over an array of candidates: the batch scorer on every short candidate, a traceback
    for those at or above the keep score (and for long and direct candidates); returns (C with its 'score' filled, RAW
    rows of the alignments found)."""
    tok, bounds = corpus.tok, corpus.bounds
    thr = r4.min_score_for_keep()
    Lmax = WINDOW + 2 * PAD
    la, lb = C["a1"] - C["a0"], C["b1"] - C["b0"]
    short = (~C["direct"].astype(bool)) & (la <= Lmax) & (lb <= Lmax)
    rows_short = np.flatnonzero(short)
    scores = np.zeros(C.size, dtype=np.int32)
    for s in range(0, rows_short.size, batch):
        rr = rows_short[s:s + batch]
        IDA = _ids_matrix(tok, bounds, C["a"][rr].astype(np.int64), C["a0"][rr].astype(np.int64), C["a1"][rr].astype(np.int64), Lmax, -1)
        IDB = _ids_matrix(tok, bounds, C["b"][rr].astype(np.int64), C["b0"][rr].astype(np.int64), C["b1"][rr].astype(np.int64), Lmax, -2)
        sc, _, _ = r4.sw_batch_scores(IDA, IDB, alts)
        scores[rr] = sc
    raw = []
    todo = np.flatnonzero((short & (scores >= thr)) | (~short))
    for r in todo:
        c = C[r]
        if c["direct"]:
            n = int(c["a1"] - c["a0"])
            scores[r] = n * r4.MATCH
            raw.append((c["a"], c["b"], c["a0"], c["a1"], c["b0"], c["b1"], n, n, n * r4.MATCH, c["src"], c["rank"], c["sim"]))
            continue
        a, b = int(c["a"]), int(c["b"])
        a0, a1 = int(c["a0"]), min(int(c["a1"]), int(c["a0"]) + r4.LONG_SIDE)
        b0, b1 = int(c["b0"]), min(int(c["b1"]), int(c["b0"]) + r4.LONG_SIDE)
        ida = tok[bounds[a] + a0:bounds[a] + a1].astype(np.int32)
        idb = tok[bounds[b] + b0:bounds[b] + b1].astype(np.int32)
        res = r4.sw_single(ida, idb, alts)
        if res is None:
            continue
        score, x0, x1, y0, y1, cols, matches = res
        if not short[r]:
            scores[r] = score
        raw.append((a, b, a0 + x0, a0 + x1, b0 + y0, b0 + y1, cols, matches, score, c["src"], c["rank"], c["sim"]))
    C = C.copy()
    C["score"] = scores
    return C, np.array(raw, dtype=RAW) if raw else np.zeros(0, dtype=RAW)


def _raw_dicts(R):
    out = []
    for r in R:
        srcs = set()
        if r["src"] & SRC_EMB:
            srcs.add("embedding")
        if r["src"] & SRC_EXACT:
            srcs.add("exact")
        out.append({"a": int(r["a"]), "b": int(r["b"]), "a0": int(r["a0"]), "a1": int(r["a1"]), "b0": int(r["b0"]), "b1": int(r["b1"]),
                    "cols": int(r["cols"]), "matches": int(r["matches"]), "score": int(r["score"]), "srcs": srcs,
                    "rank": int(r["rank"]), "sim": None if r["sim"] <= -8 else float(r["sim"])})
    return out


def _dicts_raw(ds):
    out = np.zeros(len(ds), dtype=RAW)
    for i, d in enumerate(ds):
        out[i] = (d["a"], d["b"], d["a0"], d["a1"], d["b0"], d["b1"], d["cols"], d["matches"], d["score"],
                  (SRC_EMB if "embedding" in d["srcs"] else 0) | (SRC_EXACT if "exact" in d["srcs"] else 0), d["rank"],
                  NO_SIM if d["sim"] is None else d["sim"])
    return out


def join_and_keep(corpus, raw, alts, k):
    """The pilot's join_alignments and keep for one K over RAW rows (rank <= k; exact seeds have rank 0): kept RAW rows."""
    sel = raw[raw["rank"] <= k]
    if not sel.size:
        return sel
    joined = r4.join_alignments(corpus.units, _raw_dicts(sel), alts)
    kept = r4.keep(joined)
    return _dicts_raw(kept)


def _align_bucket(i):
    outdir, corpus, alts = _W["outdir"], _W["corpus"], _W["alts"]
    cdir, adir = os.path.join(outdir, "cands"), os.path.join(outdir, "aligned")
    mark = os.path.join(adir, f"b{i:04d}.ok")
    if os.path.exists(mark):
        return i, None
    t0 = time.time()
    C = np.fromfile(os.path.join(cdir, f"b{i:04d}.cand"), dtype=CAND)
    n_in = int(C.size)
    C = dedup_candidates(C)
    C, raw = score_and_trace(corpus, C, alts)
    np.save(os.path.join(adir, f"b{i:04d}_scored.npy"), C)
    np.save(os.path.join(adir, f"b{i:04d}_raw.npy"), raw)
    kept_n = {}
    for k in _W.get("ks", K_ALL):
        kept = join_and_keep(corpus, raw, alts, k)
        np.save(os.path.join(adir, f"b{i:04d}_kept_k{k}.npy"), kept)
        kept_n[k] = int(kept.size)
    info = {"bucket": i, "candidates_in": n_in, "candidates": int(C.size), "raw": int(raw.size), "kept": kept_n,
            "seconds": round(time.time() - t0, 1)}
    json.dump(info, open(mark, "w", encoding="utf-8"))
    return i, info


def step_align(snap=SNAP, outdir=OUTDIR, workers=60, ks=K_ALL, log=_log):
    cdir, adir = os.path.join(outdir, "cands"), os.path.join(outdir, "aligned")
    _W["ks"] = tuple(ks)
    n_b = json.load(open(os.path.join(cdir, "DONE.json"), encoding="utf-8"))["buckets"]
    os.makedirs(adir, exist_ok=True)
    todo = [i for i in range(n_b) if not os.path.exists(os.path.join(adir, f"b{i:04d}.ok"))]
    log(f"[r04c] aligning {len(todo)} of {n_b} buckets in {workers} processes")
    t0 = time.time()
    _W["corpus"] = r2c.Corpus.load(snap, mmap=True)
    _W["alts"] = np.load(os.path.join(outdir, "alts.npy"))
    _W["outdir"] = outdir
    with Pool(min(workers, max(1, len(todo)))) as pool:
        for n, (i, info) in enumerate(pool.imap_unordered(_align_bucket, todo)):
            if info and (n % 5 == 0 or n + 1 == len(todo)):
                log(f"[r04c]   bucket {i}: {info['candidates']:,} candidates, {info['raw']:,} reach the keep score, kept "
                    f"{info['kept']}; {info['seconds']}s ({n + 1}/{len(todo)} done, {time.time() - t0:.0f}s)")
    infos = [json.load(open(os.path.join(adir, f"b{i:04d}.ok"), encoding="utf-8")) for i in range(n_b)]
    summary = {"buckets": n_b, "candidates": sum(x["candidates"] for x in infos), "raw": sum(x["raw"] for x in infos),
               "kept": {str(k): sum(x["kept"][str(k)] for x in infos) for k in ks},
               "seconds": round(time.time() - t0, 1), "generated": time.strftime("%Y-%m-%d %H:%M")}
    json.dump(summary, open(os.path.join(adir, "DONE.json"), "w", encoding="utf-8"), indent=1)
    log(f"[r04c] aligned: {summary['candidates']:,} candidates, {summary['raw']:,} alignments at the keep score, kept per K {summary['kept']}")
    return summary


# ---------------------------------------------------------------- 6. the report

def _kept_dicts(R):
    ds = _raw_dicts(R)
    for d in ds:
        d["identity"] = d["matches"] / d["cols"] if d["cols"] else 0.0
        d["len"] = d["a1"] - d["a0"]
    return ds


def _pair_extras(K, pairs, corpus):
    """Per story pair of the kept alignments: the longest alignment in columns and the best identity (r05 reads them),
    in the order of pairs['a'], pairs['b']."""
    if not K.size:
        return {"max_cols": np.zeros(0, np.int64), "best_identity": np.zeros(0)}
    key = K["a"].astype(np.int64) * corpus.n_stories + K["b"].astype(np.int64)
    o = np.argsort(key, kind="stable")
    ks = key[o]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    mc = np.maximum.reduceat(K["cols"][o].astype(np.int64), starts)
    bi = np.maximum.reduceat((K["matches"][o] / np.maximum(K["cols"][o], 1)).astype(np.float64), starts)
    want = pairs["a"].astype(np.int64) * corpus.n_stories + pairs["b"].astype(np.int64)
    pos = np.searchsorted(ks[starts], want)
    return {"max_cols": mc[pos], "best_identity": bi[pos]}


def step_report(corpus, outdir=OUTDIR, ks=K_ALL, write_floor=WRITE_FLOOR_COLS, log=_log):
    adir = os.path.join(outdir, "aligned")
    n_b = json.load(open(os.path.join(adir, "DONE.json"), encoding="utf-8"))["buckets"]
    cmeta = json.load(open(os.path.join(outdir, "cands", "DONE.json"), encoding="utf-8"))
    kmeta = json.load(open(os.path.join(outdir, "knn_meta.json"), encoding="utf-8")) if os.path.exists(os.path.join(outdir, "knn_meta.json")) else {}
    emeta = json.load(open(os.path.join(outdir, "emb_meta.json"), encoding="utf-8")) if os.path.exists(os.path.join(outdir, "emb_meta.json")) else {}
    P = np.load(os.path.join(outdir, "passages.npy"), mmap_mode="r")
    units = corpus.units
    tag = f"{SET_NAME}_w{WINDOW}s{STRIDE}"
    out_stats = {}
    for k in ks:
        t0 = time.time()
        parts = [np.load(os.path.join(adir, f"b{i:04d}_kept_k{k}.npy")) for i in range(n_b)]
        K = np.concatenate(parts) if parts else np.zeros(0, dtype=RAW)
        o = np.lexsort((K["b0"], K["a0"], K["b"], K["a"]))
        K = K[o]
        same_mask = corpus.issue_of[K["a"]] == corpus.issue_of[K["b"]]
        cross, same = K[~same_mask], K[same_mask]
        pre = os.path.join(outdir, f"{tag}_k{k}")
        np.save(pre + "_alignments.npy", cross)
        np.save(pre + "_sameissue.npy", same)
        six = np.stack([cross["a"], cross["b"], cross["a0"], cross["a1"], cross["b0"], cross["b1"]], axis=1).astype(np.int32)
        pairs = r2c.pair_table(six, corpus)
        pairs.update(_pair_extras(cross, pairs, corpus))
        shares = r2c.story_share_table(six, corpus)
        long_cross = cross[cross["cols"] >= write_floor]
        long_same = same[same["cols"] >= write_floor]
        cross_d = _kept_dicts(long_cross)
        same_d = _kept_dicts(long_same)
        clusters = r2c.cluster(cross_d, units)
        ident = cross["matches"] / np.maximum(cross["cols"], 1)
        ident_hist = defaultdict(int)
        for v in ident:
            ident_hist[f"{int(v * 10) * 10}-{int(v * 10) * 10 + 9}%" if v < 1 else "100%"] += 1
        by_source = {SRC_NAME.get(int(s), str(s)): int(n) for s, n in zip(*np.unique(cross["src"], return_counts=True))}
        hist = {name: int(((cross["cols"] >= lo) & (cross["cols"] < hi)).sum()) for lo, hi, name in r2.LEN_BINS}
        stats = {
            "set": SET_NAME, "window": WINDOW, "stride": STRIDE, "k": k, "model": emeta.get("model", MODEL),
            "collection": os.path.basename(SNAP), "label": LABEL, "engine": "arrays",
            "embedding_backend": emeta.get("backend"), "neighbours": {kk: kmeta.get(kk) for kk in ("backend", "approximate", "k")},
            "min_cols": r4.MIN_COLS, "min_identity": r4.MIN_IDENT,
            "scores": {"match": r4.MATCH, "mismatch": r4.MISMATCH, "gap": r4.GAP},
            "fuzzy": {"min_len": r4.FUZZY_MIN_LEN, "alternates": r4.FUZZY_ALTS},
            "stories": len(units), "passages": int(P.shape[0]),
            "candidate_regions": cmeta["candidates"], "exact_seeds": cmeta.get("exact_seeds"),
            "alignments": int(cross.size), "same_issue_alignments": int(same.size),
            "same_issue_from_shared_regions": int(sum(1 for r in same if units[int(r["a"])]["fragments"] & units[int(r["b"])]["fragments"])),
            "clusters": len(clusters),
            "clusters_3plus_witnesses": sum(1 for c in clusters if c["witnesses"] >= 3),
            "length_hist": hist,
            "identity_hist": dict(sorted(ident_hist.items())),
            "by_source": by_source,
            "longest_alignment": int(cross["cols"].max()) if cross.size else 0,
            "stories_with_paraphrase": len(shares),
            "pairs_with_paraphrase": int(pairs["a"].size),
            "write_floor_cols": write_floor, "alignments_written": int(long_cross.size), "same_issue_written": int(long_same.size),
            "alignments_under_floor": int(cross.size - long_cross.size),
            "seconds_total": round(time.time() - t0, 1),
            "generated": time.strftime("%Y-%m-%d %H:%M"),
        }
        with open(pre + "_alignments.jsonl", "w", encoding="utf-8") as f:
            for a in sorted(cross_d, key=lambda a: (-a["cols"], -a["identity"])):
                f.write(json.dumps(r4.serialize(a, units), ensure_ascii=False) + "\n")
        with open(pre + "_sameissue.jsonl", "w", encoding="utf-8") as f:
            for a in sorted(same_d, key=lambda a: -a["cols"]):
                f.write(json.dumps(r4.serialize(a, units), ensure_ascii=False) + "\n")
        r2c.write_clusters(clusters, pre, "cols")
        np.savez(pre + "_pairs.npz", **pairs)
        top = [{"a": units[int(pairs["a"][i])]["story_id"], "b": units[int(pairs["b"][i])]["story_id"], "n_matches": int(pairs["n_matches"][i]),
                "max_len": int(pairs["max_len"][i]), "cover_a": float(pairs["cover_a"][i]), "cover_b": float(pairs["cover_b"][i])}
               for i in range(min(PAIRS_JSON, pairs["a"].size))]
        json.dump(top, open(pre + "_pairs.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(shares, open(pre + "_story_share.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        stats["seconds_total"] = round(time.time() - t0, 1)
        json.dump(stats, open(pre + "_stats.json", "w", encoding="utf-8"), indent=1)
        out_stats[k] = stats
        for u in units:
            if "canon" in u:
                u.drop_text()
        log(f"[r04c] K={k}: {cross.size:,} cross-issue alignments (longest {stats['longest_alignment']} cols), {same.size:,} same-issue, "
            f"{len(clusters):,} clusters of {write_floor}+ cols, by source {by_source}; {stats['seconds_total']}s")
    return out_stats


# ---------------------------------------------------------------- tests

def _plain_units_corpus(units):
    """The pilot's units as a Corpus (r2c.Corpus.from_units); set_corpus_ids puts the snapshot's ids on the units."""
    corpus = r2c.Corpus.from_units(units)
    for i, u in enumerate(units):
        u["index"] = i
    return corpus


def set_corpus_ids(units, corpus):
    for i, u in enumerate(units):
        u["ids"] = corpus.tok[corpus.bounds[i]:corpus.bounds[i + 1]].astype(np.int32)


def _pilot_from_neighbours(units, passages_list, neighbours, exact_dicts, tid, alts, ks):
    """The pilot's pipeline from the candidate step on (encode_units, gather_candidates, align_candidates, join, keep)
    — the reference for the array pipeline on the same neighbours. Leaves the pilot's ids on the units."""
    r4.encode_units(units, tid)
    cands = r4.gather_candidates(units, passages_list, neighbours, exact_dicts, WINDOW)
    raw = r4.align_candidates(units, cands, alts, log=lambda *a: None, window=WINDOW)
    out = {}
    for k in ks:
        sel = [a for a in raw if a["rank"] <= k]
        out[k] = r4.keep(r4.join_alignments(units, sel, alts))
    return out, cands, raw


def _sig(ds):
    return sorted((d["a"], d["b"], d["a0"], d["a1"], d["b0"], d["b1"], d["cols"], d["matches"], d["score"]) for d in ds)


def compare_engines(units, ks=K_ALL, log=print, tmp=None, k_retrieve=K_RETRIEVE):
    """The array pipeline against the pilot's on the same units with the fake embedding: the passages, the
    alternates, the neighbour similarities, the candidates after de-duplication, the alignments at the keep score and
    the kept alignments per K must agree. Returns True when they do."""
    import tempfile
    ok = True
    corpus = _plain_units_corpus(units)
    # alternates: the pilot's ids are frequency-sorted; compare by name
    tid, alts_p = r4.build_vocab(units)
    freq = np.bincount(corpus.tok, minlength=len(corpus.vocab))
    alts_c = build_alts(corpus.vocab, freq, log=None)
    names = {t: i for i, t in enumerate(corpus.vocab)}
    inv = {i: t for t, i in tid.items()}
    a_ok = True
    for t, i in tid.items():
        ref = [None if x < 0 else inv[int(x)] for x in alts_p[i]]
        got = [None if x < 0 else corpus.vocab[int(x)] for x in alts_c[names[t]]]
        if ref != got:
            a_ok = False
            break
    # passages
    pl = r4.make_passages(units, WINDOW, STRIDE)
    P = make_passages(corpus.bounds)
    p_ok = [tuple(int(x) for x in row) for row in P] == pl
    # embeddings and neighbours
    texts = [r4.passage_text(units, p) for p in pl]
    vec = fake_embed(texts)
    nb_pilot = r4.knn(vec, pl, k_retrieve)
    pstory = P[:, 0].astype(np.int64)
    idx, sim = knn_exact(vec.astype(np.float16), pstory, k_retrieve, backend="numpy", log=None)
    # the pilot's neighbour list on float32; ours from float16 vectors: compare the sorted similarity values per
    # query at float16 precision, and the neighbour sets where no tie straddles the boundary
    sims_p = defaultdict(list)
    for i, j, rank, s in nb_pilot:
        sims_p[i].append(s)
    n_ok = True
    for i in range(P.shape[0]):
        a = np.sort(np.array(sims_p.get(i, []), dtype=np.float32))[::-1]
        b = np.sort(sim[i][sim[i] > -1.5])[::-1]
        if a.size != b.size or (a.size and np.abs(a - b).max() > 0.01):
            n_ok = False
            break
    # from the SAME neighbours (ours) through both pipelines
    nb_ours = [(int(i), int(idx[i, r]), r + 1, float(sim[i, r])) for i in range(P.shape[0]) for r in range(k_retrieve) if idx[i, r] >= 0]
    exact, exact_same, _ = r2.find_matches(units, r4.EXACT_SEED_K)
    kept_p, cands_p, raw_p = _pilot_from_neighbours(units, pl, nb_ours, exact + exact_same, tid, alts_p, ks)
    set_corpus_ids(units, corpus)
    M = np.array([[m["a"], m["b"], m["a0"], m["a1"], m["b0"], m["b1"]] for m in exact + exact_same], dtype=np.int32).reshape(-1, 6)
    n_tok = np.diff(corpus.bounds)
    C = np.concatenate([_regions_from_neighbours(P, n_tok, idx, sim, 0, P.shape[0]), _regions_from_exact(M, n_tok)])
    C = dedup_candidates(C)
    n_cands_p = sum(len(v) for v in cands_p.values())
    c_ok = int(C.size) == n_cands_p
    C2, raw = score_and_trace(corpus, C, alts_c)
    r_ok = _sig(_raw_dicts(raw)) == _sig(raw_p)
    k_ok = True
    for k in ks:
        kept = join_and_keep(corpus, raw, alts_c, k)
        if _sig(_raw_dicts(kept)) != _sig(kept_p[k]):
            k_ok = False
    good = a_ok and p_ok and n_ok and c_ok and r_ok and k_ok
    log(f"{len(units)} stories, {P.shape[0]} passages: alternates {'same' if a_ok else 'DIFFER'}, passages {'same' if p_ok else 'DIFFER'}, "
        f"neighbour similarities {'same' if n_ok else 'DIFFER'}, candidates {C.size} vs {n_cands_p}, alignments at the keep score "
        f"{raw.size} vs {len(raw_p)} {'same' if r_ok else 'DIFFER'}, kept per K " + ", ".join(f"{k}: {int((join_and_keep(corpus, raw, alts_c, k)).size)}" for k in ks)
        + f" {'same' if k_ok else 'DIFFER'} -> {'OK' if good else 'FAIL'}")
    return ok and good


def selftest():
    import random
    import tempfile
    import r03_synthetic as r3
    ok = True
    # 1. the pilot's six fabricated stories with planted damaged copies (r03), through both pipelines
    recs = r2._fake_corpus()
    for r in recs:
        words = r["text"].split()
        r["text"] = ". ".join(" ".join(words[i:i + 12]) for i in range(0, len(words), 12))
    synth, plants = r3.make_synthetic(recs, seed=2, n_per_kind=2)
    print("the pilot's fabricated corpus with planted copies:")
    ok = compare_engines(r2.build_units(synth), k_retrieve=5) and ok
    # 2. random corpora with planted reuse (r02's generator), real-looking stride effects, several K
    for seed in (1, 2, 3):
        rng = random.Random(seed)
        recs = r2c._random_corpus(rng, n_stories=rng.choice([12, 20]), vocab=300, planted=8)
        for r in recs:
            words = r["text"].split()
            r["text"] = ". ".join(" ".join(words[i:i + 10]) for i in range(0, len(words), 10))
        print(f"random corpus, seed {seed}:")
        ok = compare_engines(r2.build_units(recs), k_retrieve=8) and ok
    # 3. the file path end to end with the fake embedding: alts, passages, embed (2 processes), knn, candidates (2 buckets), align, report
    rng = random.Random(9)
    recs = r2c._random_corpus(rng, n_stories=16, vocab=300, planted=8)
    by_issue = defaultdict(list)
    for r in recs:
        by_issue[r["issue"]].append(r)
    with tempfile.TemporaryDirectory() as td:
        exp = os.path.join(td, "export")
        os.makedirs(exp)
        for iid, rs in by_issue.items():
            with open(os.path.join(exp, f"{iid}.jsonl"), "w", encoding="utf-8") as f:
                for r in rs:
                    f.write(json.dumps(dict(r, magazine="M", cover_date="1930-01")) + "\n")
        snap = os.path.join(td, "corpus_dryrun")
        r2c.tokenize_corpus(exp, snap, workers=2, log=lambda *a: None)
        corpus = r2c.Corpus.load(snap)
        r2c.run(corpus, r4.EXACT_SEED_K, SET_NAME, snap, log=lambda *a: None)      # the exact seeds
        outdir = os.path.join(snap, "para")
        global BUCKET_CANDS, EMB_CHUNK
        saved = (BUCKET_CANDS, EMB_CHUNK)
        BUCKET_CANDS, EMB_CHUNK = 300, 50
        try:
            alts = step_alts(corpus, outdir, log=lambda *a: None)
            P = step_passages(corpus, outdir, log=lambda *a: None)
            step_embed(snap, outdir, backend="fake", workers=2, log=lambda *a: None)
            idx, sim, _ = step_knn(outdir, backend="numpy", k=8, log=lambda *a: None)
            n_b = step_candidates(corpus, outdir, snap, log=lambda *a: None)
            step_align(snap, outdir, workers=2, ks=(5, 8), log=lambda *a: None)
            st = step_report(corpus, outdir, ks=(5, 8), write_floor=20, log=lambda *a: None)
        finally:
            BUCKET_CANDS, EMB_CHUNK = saved
        # the reference: the pilot's pipeline on the same units and the same neighbours
        units = r2.build_units([r for iid in sorted(by_issue) for r in by_issue[iid]])
        ref = _plain_units_corpus(units)
        pl = r4.make_passages(units, WINDOW, STRIDE)
        tid, alts_p = r4.build_vocab(units)
        nb = [(int(i), int(idx[i, r]), r + 1, float(sim[i, r])) for i in range(P.shape[0]) for r in range(idx.shape[1]) if idx[i, r] >= 0]
        exact, exact_same, _ = r2.find_matches(units, r4.EXACT_SEED_K)
        kept_p, _, _ = _pilot_from_neighbours(units, pl, nb, exact + exact_same, tid, alts_p, (5, 8))
        good = True
        for k in (5, 8):
            A = np.load(os.path.join(outdir, f"{SET_NAME}_w{WINDOW}s{STRIDE}_k{k}_alignments.npy"))
            S = np.load(os.path.join(outdir, f"{SET_NAME}_w{WINDOW}s{STRIDE}_k{k}_sameissue.npy"))
            got = _sig(_raw_dicts(np.concatenate([A, S])))
            good = good and got == _sig(kept_p[k]) and st[k]["alignments"] == A.size
        # the per-pair extras (longest columns, best identity) against the pilot's kept alignments
        pz = np.load(os.path.join(outdir, f"{SET_NAME}_w{WINDOW}s{STRIDE}_k8_pairs.npz"))
        ref = defaultdict(lambda: [0, 0.0])
        for a in kept_p[8]:
            if units[a["a"]]["issue"] != units[a["b"]]["issue"]:
                r = ref[(units[a["a"]]["story_id"], units[a["b"]]["story_id"])]
                r[0] = max(r[0], a["cols"]); r[1] = max(r[1], a["matches"] / a["cols"])
        sid = [u["story_id"] for u in corpus.units]
        got = {(sid[int(pz["a"][i])], sid[int(pz["b"][i])]): (int(pz["max_cols"][i]), round(float(pz["best_identity"][i]), 9)) for i in range(pz["a"].size)}
        extras_ok = got == {k: (v[0], round(v[1], 9)) for k, v in ref.items()}
        good = good and extras_ok
        files = all(os.path.exists(os.path.join(outdir, f"{SET_NAME}_w{WINDOW}s{STRIDE}_k5_{x}")) for x in
                    ("alignments.jsonl", "sameissue.jsonl", "clusters.json", "pairs.json", "pairs.npz", "story_share.json", "stats.json"))
        lines = [json.loads(l) for l in open(os.path.join(outdir, f"{SET_NAME}_w{WINDOW}s{STRIDE}_k8_alignments.jsonl"), encoding="utf-8")]
        text_ok = all(l["text_a"] and l["text_b"] and l["cols"] >= 20 for l in lines) and len(lines) == st[8]["alignments_written"]
        print(f"file path: {P.shape[0]} passages, {n_b} candidate buckets, kept K=5 {st[5]['alignments']} / K=8 {st[8]['alignments']} cross-issue "
              f"({st[8]['same_issue_alignments']} same-issue), {st[8]['clusters']} clusters; the pilot's pipeline on the same neighbours "
              f"{'agrees' if good else 'DIFFERS'} (pair extras {'same' if extras_ok else 'DIFFER'}); files {'written' if files else 'MISSING'}; texts {'ok' if text_ok else 'BAD'} "
              f"-> {'OK' if good and files and text_ok else 'FAIL'}")
        ok = ok and good and files and text_ok
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


def pilot_check(stories_path=r2.STORIES, backend="fastembed", ks=K_ALL):
    """The pilot's stories through both pipelines with the real model: the array pipeline's kept alignments per K
    against the pilot's pipeline fed the same neighbours (the neighbour search itself is checked by its similarities)."""
    units = r2.build_units(r2.load_stories(stories_path, "machine"))
    corpus = _plain_units_corpus(units)
    pl = r4.make_passages(units, WINDOW, STRIDE)
    P = make_passages(corpus.bounds)
    print(f"[r04c] pilot check: {len(units)} stories, {P.shape[0]} passages; passages {'same' if [tuple(int(x) for x in r) for r in P] == pl else 'DIFFER'}")
    t0 = time.time()
    texts = [r4.passage_text(units, p) for p in pl]
    model = None
    if backend == "fastembed":
        from fastembed import TextEmbedding
        model = TextEmbedding(MODEL)
    vec = embed_texts(texts, backend, model)
    print(f"[r04c] {len(texts)} passages embedded ({backend}) in {time.time() - t0:.0f}s")
    pstory = P[:, 0].astype(np.int64)
    idx, sim = knn_exact(vec.astype(np.float16), pstory, K_RETRIEVE, backend="numpy", log=None)
    nb_p = r4.knn(vec, pl, K_RETRIEVE)
    sims_p = defaultdict(list)
    for i, j, rank, s in nb_p:
        sims_p[i].append(s)
    worst = 0.0
    for i in range(P.shape[0]):
        a = np.sort(np.array(sims_p.get(i, []), dtype=np.float32))[::-1]
        b = np.sort(sim[i][sim[i] > -1.5])[::-1]
        if a.size != b.size:
            worst = 9
            break
        if a.size:
            worst = max(worst, float(np.abs(a - b).max()))
    print(f"[r04c] neighbour similarities: largest difference between the pilot's float32 search and the float16 arrays {worst:.4f}")
    tid, alts_p = r4.build_vocab(units)
    freq = np.bincount(corpus.tok, minlength=len(corpus.vocab))
    alts_c = build_alts(corpus.vocab, freq, log=None)
    nb_ours = [(int(i), int(idx[i, r]), r + 1, float(sim[i, r])) for i in range(P.shape[0]) for r in range(K_RETRIEVE) if idx[i, r] >= 0]
    exact, exact_same, _ = r2.find_matches(units, r4.EXACT_SEED_K)
    kept_p, cands_p, raw_p = _pilot_from_neighbours(units, pl, nb_ours, exact + exact_same, tid, alts_p, ks)
    set_corpus_ids(units, corpus)
    M = np.array([[m["a"], m["b"], m["a0"], m["a1"], m["b0"], m["b1"]] for m in exact + exact_same], dtype=np.int32).reshape(-1, 6)
    n_tok = np.diff(corpus.bounds)
    C = dedup_candidates(np.concatenate([_regions_from_neighbours(P, n_tok, idx, sim, 0, P.shape[0]), _regions_from_exact(M, n_tok)]))
    C2, raw = score_and_trace(corpus, C, alts_c)
    ok = _sig(_raw_dicts(raw)) == _sig(raw_p) and worst < 0.01
    print(f"[r04c] candidates {C.size} vs {sum(len(v) for v in cands_p.values())}; alignments at the keep score {raw.size} vs {len(raw_p)} "
          f"{'same' if _sig(_raw_dicts(raw)) == _sig(raw_p) else 'DIFFER'}")
    for k in ks:
        kept = join_and_keep(corpus, raw, alts_c, k)
        same = _sig(_raw_dicts(kept)) == _sig(kept_p[k])
        ok = ok and same
        print(f"[r04c] K={k}: kept {kept.size} vs {len(kept_p[k])} -> {'OK' if same else 'FAIL'}")
    print("PILOT CHECK", "PASSED" if ok else "FAILED")
    return ok


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", nargs="?", choices=["alts", "passages", "embed", "knn", "candidates", "align", "report", "all"])
    ap.add_argument("--snap", default=SNAP, help="the r02 snapshot and results (data/reuse/corpus_dryrun)")
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--backend", default=None, help="embed: fastembed | st | fake; knn: torch | numpy | faiss")
    ap.add_argument("--device", default="cpu", help="st embeddings or torch neighbours: cpu, cuda:0, cuda:2 …")
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--threads", type=int, default=2, help="onnxruntime threads per fastembed process")
    ap.add_argument("--k", type=int, nargs="+", default=list(K_ALL))
    ap.add_argument("--write-floor", type=int, default=WRITE_FLOOR_COLS)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pilot-check", action="store_true")
    ap.add_argument("--stories", default=r2.STORIES)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    if args.pilot_check:
        sys.exit(0 if pilot_check(args.stories, args.backend or "fastembed", tuple(args.k)) else 1)
    if not args.step:
        ap.print_help()
        return
    steps = ["alts", "passages", "embed", "knn", "candidates", "align", "report"] if args.step == "all" else [args.step]
    corpus = None
    for s in steps:
        if s in ("alts", "passages", "candidates", "report") and corpus is None:
            corpus = r2c.Corpus.load(args.snap)
            _log(f"[r04c] {corpus.n_stories:,} stories, {int(corpus.bounds[-1]):,} tokens ({LABEL})")
        if s == "alts":
            step_alts(corpus, args.out)
        elif s == "passages":
            step_passages(corpus, args.out)
        elif s == "embed":
            step_embed(args.snap, args.out, args.backend or "fastembed", args.workers, args.device, args.threads)
        elif s == "knn":
            step_knn(args.out, args.backend or "torch", args.device)
        elif s == "candidates":
            step_candidates(corpus, args.out, args.snap)
        elif s == "align":
            step_align(args.snap, args.out, args.workers, tuple(args.k))
        elif s == "report":
            step_report(corpus, args.out, tuple(args.k), args.write_floor)


if __name__ == "__main__":
    main()
