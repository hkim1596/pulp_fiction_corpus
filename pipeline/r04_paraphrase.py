#!/usr/bin/env python3
"""r04 — paraphrase and near-verbatim reuse (protocol section 3.2).

The exact stage (r02) finds passages that are identical word for word.
This stage finds passages that were copied with damage or rewritten:
"substitutions, insertions, deletions, and related forms of
reformulation". It has three parts, each a decision Heejin made on
2026-08-31 (recorded in docs/handbook.md and the journal):

  1. Candidate retrieval. Every story is cut into overlapping passages of
     W=50 words, step S=25. Each passage is embedded with an open-weights
     model (BAAI/bge-small-en-v1.5) and its K nearest neighbours in OTHER
     stories are retrieved (K=10 main run; 5 and 20 as sensitivity, all
     derived from one K=20 retrieval by rank). In addition, every exact
     match from r02 (seed 6) is a candidate: the lexical near-match tier
     Sujin asked for, so that an OCR-damaged copy is aligned even if the
     embedding search misses it. Each candidate records where it came
     from ("embedding" with its rank, "exact", or both).
  2. Local alignment. Each candidate pair of passages, widened by PAD=25
     words of context on both sides, is aligned word by word
     (Smith-Waterman local alignment: match +2, mismatch -1, gap -1). Two
     words count as equal if identical, or, for words of five letters or
     more, one edit apart (OCR tolerance). Alignments that touch or
     overlap in the same story pair are joined and re-aligned over their
     union, so a long relationship is reported once, at full length.
  3. Keep rule. An alignment enters the inventory if it spans at least
     MIN_COLS=20 alignment columns with identity (matching columns over
     all columns) of at least MIN_IDENT=0.60. Every kept alignment stores
     its length, matches, identity, score, and both texts. The thresholds
     are to be re-set on Sujin's hand-reviewed paraphrase set (protocol:
     "parameter selection and validation"); until then they are the
     development-set defaults.

Same-issue pairs are kept out of the inventory and written to a
diagnostics file, exactly as in r02, because the machine assembly still
double-owns regions inside an issue.

Run:
  python3 pipeline/r04_paraphrase.py --set machine            (main + sensitivity)
  python3 pipeline/r04_paraphrase.py --set machine --window 100 --stride 50
  python3 pipeline/r04_paraphrase.py --synthetic               (planted-reuse recall)
  python3 pipeline/r04_paraphrase.py --selftest
Outputs under data/reuse/para/.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r02_verbatim as r2  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "data", "reuse", "para")
MODEL = "BAAI/bge-small-en-v1.5"
WINDOW, STRIDE, PAD = 50, 25, 25
K_MAIN, K_ALL = 10, (5, 10, 20)
MIN_COLS, MIN_IDENT = 20, 0.60
MATCH, MISMATCH, GAP = 2, -1, -1
FUZZY_MIN_LEN, FUZZY_ALTS = 5, 3
JOIN_GAP = 5             # alignments this close (tokens) in one pair are joined
LONG_SIDE = 3000         # cap on one side of a re-alignment region
LONG_EXACT = 100         # exact seeds this long are recorded without a DP pass
EXACT_SEED_K = 6


# ---------------------------------------------------------------- vocabulary

def _lev_le1(a, b):
    """True if Levenshtein distance between a and b is at most 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def build_vocab(units):
    """Token type -> id, plus up to FUZZY_ALTS alternates per type that are
    one edit away (words of FUZZY_MIN_LEN letters or more only), ranked by
    corpus frequency. Alternates are found through deletion neighbourhoods
    (two strings one edit apart share a one-deletion form), then verified."""
    freq = defaultdict(int)
    for u in units:
        for t in u["tokens"]:
            freq[t] += 1
    types = sorted(freq, key=lambda t: (-freq[t], t))
    tid = {t: i for i, t in enumerate(types)}
    buckets = defaultdict(list)
    for t in types:
        if len(t) < FUZZY_MIN_LEN:
            continue
        buckets[t].append(t)
        for i in range(len(t)):
            buckets[t[:i] + t[i + 1:]].append(t)
    alts = np.full((len(types), FUZZY_ALTS), -1, dtype=np.int32)
    for t in types:
        if len(t) < FUZZY_MIN_LEN:
            continue
        cands = set()
        cands.update(buckets.get(t, ()))
        for i in range(len(t)):
            cands.update(buckets.get(t[:i] + t[i + 1:], ()))
        cands.discard(t)
        good = [c for c in cands if _lev_le1(t, c)]
        good.sort(key=lambda c: (-freq[c], c))
        for j, c in enumerate(good[:FUZZY_ALTS]):
            alts[tid[t], j] = tid[c]
    return tid, alts


def encode_units(units, tid):
    for u in units:
        u["ids"] = np.fromiter((tid[t] for t in u["tokens"]), dtype=np.int32,
                               count=len(u["tokens"]))


# ---------------------------------------------------------------- passages

def make_passages(units, window=WINDOW, stride=STRIDE):
    """[(unit index, start, end)] covering every story; the last window is
    pulled back so the tail is covered."""
    out = []
    for si, u in enumerate(units):
        n = len(u["tokens"])
        if n <= window:
            out.append((si, 0, n))
            continue
        starts = list(range(0, n - window + 1, stride))
        if starts[-1] != n - window:
            starts.append(n - window)
        for s in starts:
            out.append((si, s, s + window))
    return out


def passage_text(units, p):
    si, s, e = p
    u = units[si]
    return u["canon"][u["offsets"][s][0]:u["offsets"][e - 1][1]]


def corpus_key(units, window, stride):
    h = hashlib.sha1(f"{MODEL}|{window}|{stride}".encode())
    for u in units:
        h.update(u["story_id"].encode())
        h.update(hashlib.sha1(u["canon"].encode("utf-8")).digest())
    return h.hexdigest()[:16]


def embed_passages(units, passages, cache_path, log=print):
    """L2-normalised passage vectors, cached on disk."""
    if os.path.exists(cache_path):
        z = np.load(cache_path)
        if z["n"] == len(passages):
            log(f"[r04] embeddings from cache {os.path.basename(cache_path)}")
            return z["vec"]
    from fastembed import TextEmbedding
    t0 = time.time()
    model = TextEmbedding(MODEL)
    texts = [passage_text(units, p) for p in passages]
    vecs = []
    done = 0
    for v in model.embed(texts, batch_size=64):
        vecs.append(np.asarray(v, dtype=np.float32))
        done += 1
        if done % 5000 == 0:
            log(f"[r04]   embedded {done}/{len(texts)} passages, {time.time() - t0:.0f}s")
    vec = np.vstack(vecs)
    vec /= np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-9)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    np.savez(cache_path, vec=vec, n=len(passages))
    log(f"[r04] embedded {len(texts)} passages in {time.time() - t0:.0f}s")
    return vec


def knn(vec, passages, k, block=1024):
    """For every passage, its k most similar passages in OTHER stories:
    list of (i, j, rank, cosine). Brute force in blocks (fine at pilot
    scale; corpus scale needs an approximate index)."""
    story = np.array([p[0] for p in passages], dtype=np.int32)
    n = len(passages)
    out = []
    for b0 in range(0, n, block):
        b1 = min(n, b0 + block)
        sims = vec[b0:b1] @ vec.T
        same = story[b0:b1, None] == story[None, :]
        sims[same] = -2.0
        kk = min(k, n - 1)
        top = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
        for r in range(b1 - b0):
            js = top[r]
            order = np.argsort(-sims[r, js])
            for rank, jj in enumerate(js[order], start=1):
                out.append((b0 + r, int(jj), rank, float(sims[r, jj])))
    return out


# ---------------------------------------------------------------- alignment

def _eq_matrix(ida, idb, alts):
    """Boolean equality matrix between two id arrays, with fuzzy alternates."""
    e = ida[:, None] == idb[None, :]
    aa, ab = alts[ida], alts[idb]
    for f in range(alts.shape[1]):
        e |= aa[:, f][:, None] == idb[None, :]
        e |= ida[:, None] == ab[:, f][None, :]
    return e


def sw_single(ida, idb, alts):
    """Smith-Waterman on one pair with traceback. Returns
    (score, a0, a1, b0, b1, cols, matches) for the best local alignment,
    or None if the best score is 0."""
    n, m = len(ida), len(idb)
    if n == 0 or m == 0:
        return None
    sub = np.where(_eq_matrix(ida, idb, alts), MATCH, MISMATCH).astype(np.int32)
    H = np.zeros((n + 1, m + 1), dtype=np.int32)
    # pointers: 0 stop, 1 diag, 2 up (gap in b), 3 left (gap in a)
    P = np.zeros((n + 1, m + 1), dtype=np.int8)
    jidx = np.arange(1, m + 1, dtype=np.int32)
    for i in range(1, n + 1):
        diag = H[i - 1, :-1] + sub[i - 1]
        up = H[i - 1, 1:] + GAP
        T = np.maximum(np.maximum(diag, up), 0)
        left_chain = np.maximum.accumulate(T - GAP * jidx) + GAP * jidx
        row = np.maximum(T, left_chain)
        H[i, 1:] = row
        p = np.where(row == 0, 0, np.where(row == diag, 1, np.where(row == up, 2, 3)))
        P[i, 1:] = p
    best = int(H.max())
    if best <= 0:
        return None
    i, j = np.unravel_index(int(H.argmax()), H.shape)
    a1, b1 = int(i), int(j)
    cols = matches = 0
    while i > 0 and j > 0 and H[i, j] > 0:
        p = P[i, j]
        if p == 1:
            if sub[i - 1, j - 1] == MATCH:
                matches += 1
            i, j = i - 1, j - 1
        elif p == 2:
            i -= 1
        elif p == 3:
            j -= 1
        else:
            break
        cols += 1
    return best, int(i), a1, int(j), b1, cols, matches


def sw_batch_scores(IDA, IDB, alts):
    """Best local-alignment score for a batch of equal-length problems.
    IDA: (B, n) ids padded with -1; IDB: (B, m) padded with -2 (pads never
    match anything). Returns (scores, end_i, end_j) arrays."""
    B, n = IDA.shape
    m = IDB.shape[1]
    safe_a = np.where(IDA < 0, 0, IDA)
    safe_b = np.where(IDB < 0, 0, IDB)
    aa = alts[safe_a]                                   # (B, n, F)
    ab = alts[safe_b]                                   # (B, m, F)
    aa[IDA < 0] = -1
    ab[IDB < 0] = -1
    E = IDA[:, :, None] == IDB[:, None, :]              # (B, n, m)
    E &= (IDA[:, :, None] >= 0)
    for f in range(alts.shape[1]):
        E |= (aa[:, :, f][:, :, None] == IDB[:, None, :]) & (aa[:, :, f][:, :, None] >= 0)
        E |= (IDA[:, :, None] == ab[:, :, f][:, None, :]) & (ab[:, :, f][:, None, :] >= 0)
    sub = np.where(E, MATCH, MISMATCH).astype(np.int16)
    jidx = np.arange(1, m + 1, dtype=np.int16)
    Hprev = np.zeros((B, m + 1), dtype=np.int16)
    best = np.zeros(B, dtype=np.int16)
    bi = np.zeros(B, dtype=np.int32)
    bj = np.zeros(B, dtype=np.int32)
    for i in range(1, n + 1):
        diag = Hprev[:, :-1] + sub[:, i - 1, :]
        up = Hprev[:, 1:] + GAP
        T = np.maximum(np.maximum(diag, up), 0)
        left_chain = np.maximum.accumulate(T - GAP * jidx, axis=1) + GAP * jidx
        row = np.maximum(T, left_chain)
        Hcur = np.zeros_like(Hprev)
        Hcur[:, 1:] = row
        rmax = row.max(axis=1)
        better = rmax > best
        if better.any():
            best[better] = rmax[better]
            bi[better] = i
            bj[better] = row.argmax(axis=1)[better] + 1
        Hprev = Hcur
    return best.astype(np.int32), bi, bj


def min_score_for_keep():
    """Lowest score any kept alignment can have: MIN_COLS columns at
    MIN_IDENT identity, the rest mismatches (gaps score the same)."""
    mt = int(np.ceil(MIN_COLS * MIN_IDENT))
    return mt * MATCH + (MIN_COLS - mt) * MISMATCH


# ---------------------------------------------------------------- pipeline

def gather_candidates(units, passages, neighbours, exact_matches, window):
    """Candidate regions: {(sa, sb): [dict(a0,a1,b0,b1,src,rank,sim)]} with
    sa < sb; regions are the passage windows widened by PAD tokens."""
    cands = defaultdict(list)

    def add(sa, a0, a1, sb, b0, b1, src, rank, sim):
        if sa == sb:
            return
        if sa > sb:
            sa, a0, a1, sb, b0, b1 = sb, b0, b1, sa, a0, a1
        na, nb = len(units[sa]["tokens"]), len(units[sb]["tokens"])
        cands[(sa, sb)].append({
            "a0": max(0, a0 - PAD), "a1": min(na, a1 + PAD),
            "b0": max(0, b0 - PAD), "b1": min(nb, b1 + PAD),
            "src": src, "rank": rank, "sim": sim})

    for i, j, rank, sim in neighbours:
        pa, pb = passages[i], passages[j]
        add(pa[0], pa[1], pa[2], pb[0], pb[1], pb[2], "embedding", rank, sim)
    for m in exact_matches:
        if m["a1"] - m["a0"] >= LONG_EXACT:
            # a long identical span needs no alignment: identity 1.0 by
            # construction (r02 already reports it); recorded directly
            cands[(m["a"], m["b"])].append({
                "a0": m["a0"], "a1": m["a1"], "b0": m["b0"], "b1": m["b1"],
                "src": "exact", "rank": 0, "sim": None, "direct": True})
            continue
        add(m["a"], m["a0"], m["a1"], m["b"], m["b0"], m["b1"], "exact", 0, None)
    # drop exact duplicates of the same region from mutual neighbours
    for key, lst in cands.items():
        seen = {}
        for c in lst:
            rk = (c["a0"], c["a1"], c["b0"], c["b1"])
            if rk in seen:
                s = seen[rk]
                s["rank"] = min(s["rank"], c["rank"]) if s["src"] == c["src"] else min(s["rank"], c["rank"])
                s["src"] = s["src"] if s["src"] == c["src"] else "embedding+exact"
                if c["sim"] is not None and (s["sim"] is None or c["sim"] > s["sim"]):
                    s["sim"] = c["sim"]
            else:
                seen[rk] = c
        cands[key] = list(seen.values())
    return cands


def align_candidates(units, cands, alts, batch=1024, log=print, window=WINDOW):
    """Run the batch scorer over every candidate region; trace back only
    those that can reach the keep rule. Returns list of raw alignments."""
    flat = [(key, c) for key, lst in cands.items() for c in lst]
    thr = min_score_for_keep()
    short, long_ = [], []
    Lmax = window + 2 * PAD
    raw = []
    for key, c in flat:
        if c.get("direct"):
            n = c["a1"] - c["a0"]
            raw.append({"a": key[0], "b": key[1], "a0": c["a0"], "a1": c["a1"],
                        "b0": c["b0"], "b1": c["b1"], "cols": n, "matches": n,
                        "score": n * MATCH, "srcs": {"exact"}, "rank": 0, "sim": None})
        elif c["a1"] - c["a0"] <= Lmax and c["b1"] - c["b0"] <= Lmax:
            short.append((key, c))
        else:
            long_.append((key, c))
    t0 = time.time()
    for b0 in range(0, len(short), batch):
        chunk = short[b0:b0 + batch]
        IDA = np.full((len(chunk), Lmax), -1, dtype=np.int32)
        IDB = np.full((len(chunk), Lmax), -2, dtype=np.int32)
        for r, (key, c) in enumerate(chunk):
            ua, ub = units[key[0]], units[key[1]]
            ia = ua["ids"][c["a0"]:c["a1"]]
            ib = ub["ids"][c["b0"]:c["b1"]]
            IDA[r, :len(ia)] = ia
            IDB[r, :len(ib)] = ib
        scores, _, _ = sw_batch_scores(IDA, IDB, alts)
        for r, (key, c) in enumerate(chunk):
            if scores[r] >= thr:
                raw.append(_trace(units, key, c, alts))
        if (b0 // batch) % 20 == 0:
            log(f"[r04]   aligned {min(b0 + batch, len(short))}/{len(short)} "
                f"short candidates, {time.time() - t0:.0f}s")
    for key, c in long_:
        raw.append(_trace(units, key, c, alts))
    return [a for a in raw if a is not None]


def _trace(units, key, c, alts):
    ua, ub = units[key[0]], units[key[1]]
    a0, a1 = c["a0"], min(c["a1"], c["a0"] + LONG_SIDE)
    b0, b1 = c["b0"], min(c["b1"], c["b0"] + LONG_SIDE)
    r = sw_single(ua["ids"][a0:a1], ub["ids"][b0:b1], alts)
    if r is None:
        return None
    score, x0, x1, y0, y1, cols, matches = r
    return {"a": key[0], "b": key[1], "a0": a0 + x0, "a1": a0 + x1,
            "b0": b0 + y0, "b1": b0 + y1, "cols": cols, "matches": matches,
            "score": score, "srcs": {c["src"]}, "rank": c["rank"],
            "sim": c["sim"]}


def join_alignments(units, raw, alts):
    """Alignments of one story pair that overlap or nearly touch on both
    sides are joined; the union region is re-aligned so length, identity,
    and score describe the whole relationship."""
    by_pair = defaultdict(list)
    for a in raw:
        by_pair[(a["a"], a["b"])].append(a)
    out = []
    for key, lst in by_pair.items():
        lst.sort(key=lambda a: (a["a0"], a["b0"]))
        groups = []
        for a in lst:
            placed = False
            for g in groups:
                if (a["a0"] <= g["a1"] + JOIN_GAP and a["a1"] >= g["a0"] - JOIN_GAP
                        and a["b0"] <= g["b1"] + JOIN_GAP and a["b1"] >= g["b0"] - JOIN_GAP):
                    g["a0"], g["a1"] = min(g["a0"], a["a0"]), max(g["a1"], a["a1"])
                    g["b0"], g["b1"] = min(g["b0"], a["b0"]), max(g["b1"], a["b1"])
                    g["members"].append(a)
                    placed = True
                    break
            if not placed:
                groups.append({"a0": a["a0"], "a1": a["a1"], "b0": a["b0"],
                               "b1": a["b1"], "members": [a]})
        for g in groups:
            if len(g["members"]) == 1:
                a = dict(g["members"][0])
            elif (g["a1"] - g["a0"] > LONG_SIDE or g["b1"] - g["b0"] > LONG_SIDE):
                # too long to re-align whole: report the union with the
                # members' columns and matches added up (all such cases in
                # the pilot are identical duplicates found by r02 anyway)
                ms = g["members"]
                a = {"a": key[0], "b": key[1], "a0": g["a0"], "a1": g["a1"],
                     "b0": g["b0"], "b1": g["b1"],
                     "cols": sum(m["cols"] for m in ms), "matches": sum(m["matches"] for m in ms),
                     "score": sum(m["score"] for m in ms),
                     "srcs": set().union(*(m["srcs"] for m in ms)),
                     "rank": min(m["rank"] for m in ms),
                     "sim": max((m["sim"] for m in ms if m["sim"] is not None), default=None)}
            else:
                ua, ub = units[key[0]], units[key[1]]
                pa0, pa1 = max(0, g["a0"] - PAD), min(len(ua["ids"]), g["a1"] + PAD)
                pb0, pb1 = max(0, g["b0"] - PAD), min(len(ub["ids"]), g["b1"] + PAD)
                r = sw_single(ua["ids"][pa0:pa1], ub["ids"][pb0:pb1], alts)
                if r is None:
                    continue
                score, x0, x1, y0, y1, cols, matches = r
                a = {"a": key[0], "b": key[1], "a0": pa0 + x0, "a1": pa0 + x1,
                     "b0": pb0 + y0, "b1": pb0 + y1, "cols": cols,
                     "matches": matches, "score": score,
                     "srcs": set().union(*(m["srcs"] for m in g["members"])),
                     "rank": min(m["rank"] for m in g["members"]),
                     "sim": max((m["sim"] for m in g["members"] if m["sim"] is not None),
                                default=None)}
            a["identity"] = a["matches"] / a["cols"] if a["cols"] else 0.0
            a["len"] = a["a1"] - a["a0"]
            out.append(a)
    return out


def keep(alignments):
    return [a for a in alignments if a["cols"] >= MIN_COLS and a["identity"] >= MIN_IDENT]


def serialize(a, units):
    ua, ub = units[a["a"]], units[a["b"]]
    ca = (ua["offsets"][a["a0"]][0], ua["offsets"][a["a1"] - 1][1])
    cb = (ub["offsets"][a["b0"]][0], ub["offsets"][a["b1"] - 1][1])
    out = {"a": ua["story_id"], "b": ub["story_id"],
           "a_tok": [a["a0"], a["a1"]], "b_tok": [a["b0"], a["b1"]],
           "a_char": list(ca), "b_char": list(cb),
           "a_issue": ua["issue"], "b_issue": ub["issue"],
           "cols": a["cols"], "matches": a["matches"],
           "identity": round(a["identity"], 4), "score": a["score"],
           "sources": sorted(a["srcs"]), "best_rank": a["rank"],
           "max_cosine": None if a["sim"] is None else round(a["sim"], 4),
           "text_a": ua["canon"][ca[0]:ca[1]][:2000],
           "text_b": ub["canon"][cb[0]:cb[1]][:2000]}
    if ua["issue"] == ub["issue"]:
        shared = len(ua["fragments"] & ub["fragments"])
        out["shared_regions"] = shared
        out["cause"] = "shared-region duplicate" if shared else "same-issue repeat"
    return out


def run_set(units, set_name, window=WINDOW, stride=STRIDE, ks=K_ALL,
            outdir=OUTDIR, use_embeddings=True, log=print, tag=None):
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    tid, alts = build_vocab(units)
    encode_units(units, tid)
    passages = make_passages(units, window, stride)
    log(f"[r04] set={set_name}: {len(units)} stories, {len(passages)} passages "
        f"(window {window}, stride {stride}), vocabulary {len(tid)}")
    neighbours = []
    if use_embeddings:
        key = corpus_key(units, window, stride)
        cache = os.path.join(outdir, f"emb_{set_name}_w{window}s{stride}_{key}.npz")
        vec = embed_passages(units, passages, cache, log)
        t1 = time.time()
        neighbours = knn(vec, passages, max(ks))
        log(f"[r04] retrieved {len(neighbours)} neighbour pairs (K={max(ks)}) "
            f"in {time.time() - t1:.0f}s")
    exact, exact_same, _ = r2.find_matches(units, EXACT_SEED_K)
    cands = gather_candidates(units, passages, neighbours, exact + exact_same, window)
    n_c = sum(len(v) for v in cands.values())
    log(f"[r04] {n_c} candidate regions in {len(cands)} story pairs "
        f"({len(exact) + len(exact_same)} exact seeds)")
    raw = align_candidates(units, cands, alts, log=log, window=window)
    log(f"[r04] {len(raw)} alignments reach the keep score; joining")
    stats_all = {}
    tagp = tag or f"{set_name}_w{window}s{stride}"
    for k in ks:
        sel = [a for a in raw if a["rank"] <= k]          # exact seeds have rank 0
        joined = join_alignments(units, sel, alts)
        kept = keep(joined)
        cross = [a for a in kept if units[a["a"]]["issue"] != units[a["b"]]["issue"]]
        same = [a for a in kept if units[a["a"]]["issue"] == units[a["b"]]["issue"]]
        clusters = r2.cluster(cross, units)
        pairs = r2.pair_stats(cross, units)
        shares = r2.story_share(cross, units)
        hist = defaultdict(int)
        for a in cross:
            hist[r2.length_bin(a["cols"])] += 1
        ident_hist = defaultdict(int)
        for a in cross:
            ident_hist[f"{int(a['identity'] * 10) * 10}-{int(a['identity'] * 10) * 10 + 9}%"
                       if a["identity"] < 1 else "100%"] += 1
        src_counts = defaultdict(int)
        for a in cross:
            src_counts["+".join(sorted(a["srcs"]))] += 1
        stats = {
            "set": set_name, "window": window, "stride": stride, "k": k,
            "model": MODEL if use_embeddings else None,
            "min_cols": MIN_COLS, "min_identity": MIN_IDENT,
            "scores": {"match": MATCH, "mismatch": MISMATCH, "gap": GAP},
            "fuzzy": {"min_len": FUZZY_MIN_LEN, "alternates": FUZZY_ALTS},
            "stories": len(units), "passages": len(passages),
            "candidate_regions": n_c,
            "alignments": len(cross), "same_issue_alignments": len(same),
            "same_issue_from_shared_regions": sum(
                1 for a in same if units[a["a"]]["fragments"] & units[a["b"]]["fragments"]),
            "clusters": len(clusters),
            "clusters_3plus_witnesses": sum(1 for c in clusters if c["witnesses"] >= 3),
            "length_hist": {name: hist.get(name, 0) for _, _, name in r2.LEN_BINS},
            "identity_hist": dict(sorted(ident_hist.items())),
            "by_source": dict(src_counts),
            "longest_alignment": max((a["cols"] for a in cross), default=0),
            "stories_with_paraphrase": len(shares),
            "seconds_total": round(time.time() - t0, 1),
            "generated": time.strftime("%Y-%m-%d %H:%M"),
        }
        pre = os.path.join(outdir, f"{tagp}_k{k}")
        with open(pre + "_alignments.jsonl", "w", encoding="utf-8") as f:
            for a in sorted(cross, key=lambda a: (-a["cols"], -a["identity"])):
                f.write(json.dumps(serialize(a, units), ensure_ascii=False) + "\n")
        with open(pre + "_sameissue.jsonl", "w", encoding="utf-8") as f:
            for a in sorted(same, key=lambda a: -a["cols"]):
                f.write(json.dumps(serialize(a, units), ensure_ascii=False) + "\n")
        json.dump(clusters, open(pre + "_clusters.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        json.dump(pairs, open(pre + "_pairs.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        json.dump(shares, open(pre + "_story_share.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        json.dump(stats, open(pre + "_stats.json", "w", encoding="utf-8"), indent=1)
        stats_all[k] = stats
        log(f"[r04] K={k}: {len(cross)} cross-issue alignments "
            f"(longest {stats['longest_alignment']} cols), {len(same)} same-issue, "
            f"{len(clusters)} clusters, by source {dict(src_counts)}")
    return stats_all, raw


# ---------------------------------------------------------------- synthetic

def plant_span(units_by_id, plant):
    """Token span of the planted passage inside the recipient unit."""
    u = units_by_id.get(plant["recipient"])
    if u is None:
        return None
    from r01_normalize import tokenize, prepare
    ptoks = [t[0] for t in tokenize(prepare(plant["planted_text"]))]
    toks = u["tokens"]
    L = len(ptoks)
    if L == 0:
        return None
    first = ptoks[0]
    for i in range(len(toks) - L + 1):
        if toks[i] == first and toks[i:i + L] == ptoks:
            return (i, i + L)
    # damaged plants may not tokenize identically at the seams: locate by
    # the longest common run
    import difflib
    sm = difflib.SequenceMatcher(None, toks, ptoks, autojunk=False)
    m = sm.find_longest_match(0, len(toks), 0, L)
    if m.size >= 5:
        return (m.a - m.b, m.a - m.b + L)
    return None


def score_synthetic(units, raw, plants, ks, min_cover=0.5):
    """Share of plants recovered by a kept alignment covering at least
    `min_cover` of the planted span, per kind, per K."""
    ub = {u["story_id"]: u for u in units}
    idx = {u["story_id"]: i for i, u in enumerate(units)}
    spans = {p["plant_id"]: plant_span(ub, p) for p in plants}
    tid, alts = build_vocab(units)
    encode_units(units, tid)
    report = {}
    for k in ks:
        kept = keep(join_alignments(units, [a for a in raw if a["rank"] <= k], alts))
        by_pair = defaultdict(list)
        for a in kept:
            by_pair[(a["a"], a["b"])].append(a)
        per_kind = {}
        for p in plants:
            sp = spans[p["plant_id"]]
            d = per_kind.setdefault(p["kind"], {"plants": 0, "recovered": 0,
                                                "locatable": 0, "cover": []})
            d["plants"] += 1
            if sp is None:
                continue
            d["locatable"] += 1
            ia, ib = idx[p["donor"]], idx[p["recipient"]]
            key = (min(ia, ib), max(ia, ib))
            covered = set()
            for a in by_pair.get(key, []):
                side = ("b0", "b1") if ib == key[1] else ("a0", "a1")
                covered.update(range(max(a[side[0]], sp[0]), min(a[side[1]], sp[1])))
            cov = len(covered) / (sp[1] - sp[0])
            d["cover"].append(cov)
            d["recovered"] += int(cov >= min_cover)
        for d in per_kind.values():
            d["recall"] = round(d["recovered"] / d["plants"], 3)
            d["mean_cover"] = round(sum(d["cover"]) / len(d["cover"]), 3) if d["cover"] else 0.0
            del d["cover"]
        report[f"k{k}"] = per_kind
    return report


# ---------------------------------------------------------------- selftest

def _rand_units(rng, n_units=6, n_tok=300):
    vocab = [f"w{i}" for i in range(500)]
    units = []
    for i in range(n_units):
        toks = [rng.choice(vocab) for _ in range(n_tok)]
        units.append(toks)
    return units


def selftest():
    import random
    rng = random.Random(5)
    ok = True
    # 1. batch scorer agrees with the single scorer
    vocab = [f"tok{i:03d}" for i in range(60)]
    ids_all = {t: i for i, t in enumerate(vocab)}
    alts = np.full((len(vocab), FUZZY_ALTS), -1, dtype=np.int32)
    probs = []
    for _ in range(40):
        a = [rng.choice(vocab) for _ in range(rng.randint(5, 40))]
        b = [rng.choice(vocab) for _ in range(rng.randint(5, 40))]
        if rng.random() < 0.5:                      # plant a shared run
            run = [rng.choice(vocab) for _ in range(rng.randint(3, 12))]
            pa, pb = rng.randint(0, len(a)), rng.randint(0, len(b))
            a = a[:pa] + run + a[pa:]
            b = b[:pb] + run + b[pb:]
        probs.append((np.array([ids_all[t] for t in a], dtype=np.int32),
                      np.array([ids_all[t] for t in b], dtype=np.int32)))
    Lmax = max(max(len(p[0]), len(p[1])) for p in probs)
    IDA = np.full((len(probs), Lmax), -1, dtype=np.int32)
    IDB = np.full((len(probs), Lmax), -2, dtype=np.int32)
    for r, (a, b) in enumerate(probs):
        IDA[r, :len(a)] = a
        IDB[r, :len(b)] = b
    bs, _, _ = sw_batch_scores(IDA, IDB, alts)
    agree = 0
    for r, (a, b) in enumerate(probs):
        s = sw_single(a, b, alts)
        single = s[0] if s else 0
        agree += int(single == bs[r])
    print(f"batch vs single scorer: {agree}/{len(probs)} agree")
    ok = ok and agree == len(probs)
    # 2. fuzzy equality and vocabulary alternates
    fake = [{"story_id": "x", "issue": "i", "tokens": ["mother", "motber", "mother", "father", "fathor", "the", "tbe"],
             "canon": "", "offsets": [], "fragments": set()}]
    tid, alts2 = build_vocab(fake)
    m_alt = alts2[tid["mother"]]
    fz = tid["motber"] in set(m_alt.tolist()) and tid["fathor"] in set(alts2[tid["father"]].tolist())
    short_not = tid["tbe"] not in set(alts2[tid["the"]].tolist())
    print(f"fuzzy alternates: mother~motber {tid['motber'] in set(m_alt.tolist())}, "
          f"father~fathor {tid['fathor'] in set(alts2[tid['father']].tolist())}, "
          f"the!~tbe (short words exact only) {short_not}")
    ok = ok and fz and short_not
    # 3. a damaged copy planted across issues is recovered through the
    #    lexical tier alone (no embeddings needed for the selftest)
    recs = r2._fake_corpus()
    import r03_synthetic as r3
    for r in recs:
        words = r["text"].split()
        r["text"] = ". ".join(" ".join(words[i:i + 12]) for i in range(0, len(words), 12))
    synth, plants = r3.make_synthetic(recs, seed=2, n_per_kind=2)
    units = r2.build_units(synth)
    stats, raw = run_set(units, "selftest", ks=(10,), outdir="/tmp/r04_selftest",
                         use_embeddings=False, log=lambda *a: None)
    rep = score_synthetic(units, raw, plants, ks=(10,))
    print("synthetic via lexical tier:", json.dumps(rep))
    v = rep["k10"].get("verbatim", {}).get("recall", 0)
    nv = rep["k10"].get("near-verbatim", {}).get("recall", 0)
    ok = ok and v == 1.0 and nv >= 0.5
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


def main():
    global MIN_COLS, MIN_IDENT
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="machine", choices=["machine", "verified", "corrected"])
    ap.add_argument("--stories", default=r2.STORIES)
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--window", type=int, default=WINDOW)
    ap.add_argument("--stride", type=int, default=STRIDE)
    ap.add_argument("--k", type=int, nargs="+", default=list(K_ALL))
    ap.add_argument("--no-embed", action="store_true",
                    help="lexical tier only (exact seeds), no embedding retrieval")
    ap.add_argument("--synthetic", action="store_true",
                    help="run on the planted-reuse copy and score recall")
    ap.add_argument("--min-cols", type=int, default=MIN_COLS,
                    help="keep rule: minimum alignment columns (default 20)")
    ap.add_argument("--min-identity", type=float, default=MIN_IDENT,
                    help="keep rule: minimum identity (default 0.60)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    MIN_COLS, MIN_IDENT = args.min_cols, args.min_identity
    tag_extra = ("" if (MIN_COLS, MIN_IDENT) == (20, 0.60)
                 else f"_c{MIN_COLS}i{int(round(MIN_IDENT * 100))}")
    if args.synthetic:
        import r03_synthetic as r3
        recs = r2.load_stories(args.stories, "machine")
        synth, plants = r3.make_synthetic(recs)
        units = r2.build_units(synth)
        outdir = os.path.join(args.out, "synthetic")
        stats, raw = run_set(units, "synthetic", args.window, args.stride, tuple(args.k),
                             outdir, not args.no_embed,
                             tag=f"synthetic_w{args.window}s{args.stride}{tag_extra}")
        rep = score_synthetic(units, raw, plants, tuple(args.k))
        rep["_generated"] = time.strftime("%Y-%m-%d %H:%M")
        rep["_keep_rule"] = {"min_cols": MIN_COLS, "min_identity": MIN_IDENT}
        json.dump(rep, open(os.path.join(outdir, f"recall_paraphrase_w{args.window}s{args.stride}{tag_extra}.json"),
                            "w"), indent=1)
        for k, v in rep.items():
            if k.startswith("_"):
                continue
            print(f"[r04] synthetic {k}: " + ", ".join(
                f"{kind} recall {d['recall']} (mean cover {d['mean_cover']})"
                for kind, d in v.items()))
        return
    units = r2.build_units(r2.load_stories(args.stories, args.set))
    run_set(units, args.set, args.window, args.stride, tuple(args.k), args.out,
            not args.no_embed, tag=f"{args.set}_w{args.window}s{args.stride}{tag_extra}")


if __name__ == "__main__":
    main()
