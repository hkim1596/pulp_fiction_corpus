#!/usr/bin/env python3
"""r05 at corpus scale — the background confluence (protocol section 4.1) over the whole corpus.

The unit is a pair of stories. The pilot (r05_background.py) wrote one row for every pair of its 287 stories (41,041
pairs) and ran the protocol's stratified sampler against that full table as a check. The corpus has 63,000 stories and
2 billion pairs; the table cannot be written, and the protocol's own answer is the sampler:

  * every pair with a match gets its row — every pair with an exact match at seed 6 (r02) or a near-verbatim
    alignment at K = 20 (r04), in the dry run about 40 million rows;
  * the pairs without a match are represented by a stratified random sample — n pairs drawn at random from every
    cell of later decade x years band (the decade of the later of the two dates, the years between them), each row
    weighted by the cell's number of unmatched pairs over n; the cell sizes are exact (counted from the stories' dates,
    not sampled), so the share of pairs with any match in a cell is exact and only the finer splits (topic quartiles,
    same author) carry sampling error; a second independent draw is made and the two draws' estimates are compared
    (the pilot checked its sampler against the full table; that check is the warrant);
  * the same-issue pairs with a match are in the table, flagged, and out of the background, as in the pilot; pairs of
    undated stories are out of the table (the protocol keeps undated issues out of the dated analyses).

Every column is the pilot's (r05_background.build_pair_table): the dates and bands, the shared author / magazine /
publisher / genre / format flags, the exact and near-verbatim extent per k and K, topic similarity by TF-IDF cosine
on the reuse-masked text (the pilot's TfidfVectorizer settings, computed here from the token ids: unigrams and
bigrams, min_df 2, max_df 0.9, sublinear tf, smoothed idf, L2 rows) and by the cosine of the mean passage vectors
(r04's embeddings, passages mostly inside detected reuse left out), the topic quartile (weighted quartiles of the
cross-issue rows), and the weight. The report (summary_machine.json) has the pilot's keys — the background curves
overall and by stratum (weighted), the time table, the most unusual pairs — plus the sampler's design and the two
draws' agreement; the two-part model is fit on a capped, unweighted subsample as a machinery check only, as in the
pilot (statsmodels, when present; --model, off by default). The pilot's placement table is not written: the by-stratum curves place any pair.

    python3 pipeline/r05_background_corpus.py [--per-cell 20000] [--model]
    python3 pipeline/r05_background_corpus.py --selftest

Inputs: data/reuse/corpus_dryrun/ (the r02 snapshot and pair tables, r04's para/ tables and embeddings; pipeline/
publishers.json for the pilot's issues, else the archive's publisher field in data/raw/<issue>/meta.json). Outputs:
data/reuse/corpus_dryrun/background/pairs_machine.npy (structured), pairs_machine_top.csv.gz (the rows with the
most reuse), summary_machine.json. Everything is the engineering dry run of 14 October 2026 onward, not the study's
numbers.
"""
import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r02_verbatim_corpus as r2c  # noqa: E402
import r05_background as r5  # noqa: E402       the pilot's definitions: decimal_year, year_band, YEAR_BANDS, survival
from r01_normalize import author_key  # noqa: E402

ROOT = r2c.ROOT
SNAP = r2c.OUTDIR
OUTDIR = os.path.join(SNAP, "background")
LABEL = r2c.LABEL
EXACT_KS = r5.EXACT_KS
PARA_KS = r5.PARA_KS
PARA_MAIN = r5.PARA_MAIN
PARA_TAG = r5.PARA_TAG
SET_NAME = "machine"
PER_CELL = 20_000
SEEDS = (20261015, 20261016)
BANDS = [name for _, _, name in r5.YEAR_BANDS]
EXACT_GRID = list(range(6, 201))
PARA_GRID = list(range(20, 301, 10))

ROW = np.dtype([("a", "<i4"), ("b", "<i4"), ("same_issue", "u1"), ("same_family", "u1"), ("same_magazine", "u1"), ("same_publisher", "u1"),
                ("same_genre", "u1"), ("same_format", "u1"), ("author_known", "u1"), ("same_author", "u1"),
                ("year_a", "<f4"), ("year_b", "<f4"), ("later_year", "<f4"), ("years_apart", "<f4"), ("years_band", "u1"), ("later_decade", "<i2"),
                ("topic_tfidf", "<f4"), ("topic_emb", "<f4"), ("topic_q", "u1"), ("matched", "u1"), ("draw", "u1"), ("weight", "<f8")]
               + [(f"exact_k{k}_{c}", t) for k in EXACT_KS for c, t in (("longest", "<i4"), ("n", "<i4"), ("cover_a", "<i4"), ("cover_b", "<i4"), ("share_max", "<f4"))]
               + [(f"para_k{k}_{c}", t) for k in PARA_KS for c, t in (("longest", "<i4"), ("n", "<i4"), ("best_identity", "<f4"), ("cover_a", "<i4"), ("cover_b", "<i4"), ("share_max", "<f4"))])


def _log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- the stories

def story_table(corpus, raw_dir=None):
    """Per story: year (nan when undated), decade, codes for magazine, publisher group, genre, format, author key."""
    n = corpus.n_stories
    year = np.full(n, np.nan, dtype=np.float64)
    mags, pubs, genres, formats, authors = {}, {}, {}, {}, {}
    mag = np.zeros(n, np.int64); pub = np.full(n, -1, np.int64); gen = np.zeros(n, np.int64); fmt = np.zeros(n, np.int64); aut = np.full(n, -1, np.int64)
    pilot_pubs = r5.load_publishers()
    issue_pub = {}
    for i, u in enumerate(corpus.units):
        m = u["meta"]
        y = r5.decimal_year(m.get("cover_date"))
        if y is not None:
            year[i] = y
        mag[i] = mags.setdefault(m.get("magazine") or "", len(mags))
        gen[i] = genres.setdefault(m.get("genre") or "", len(genres))
        fmt[i] = formats.setdefault(m.get("format") or "", len(formats))
        k = author_key(m.get("author"))
        if k:
            aut[i] = authors.setdefault(k, len(authors))
        iid = u["issue"]
        if iid not in issue_pub:
            p = pilot_pubs.get(iid, {}).get("publisher_group")
            src = "reference" if p else None
            if not p and raw_dir:
                try:
                    md = json.load(open(os.path.join(raw_dir, iid, "meta.json"), encoding="utf-8")).get("metadata", {})
                    p0 = md.get("publisher")
                    if isinstance(p0, list):
                        p0 = p0[0] if p0 else None
                    if p0:
                        p = " ".join("".join(ch for ch in str(p0).lower() if ch.isalnum() or ch == " ").split())[:60] or None
                        src = "archive" if p else None
                except Exception:
                    p = None
            issue_pub[iid] = (p, src)
        p = issue_pub[iid][0]
        if p:
            pub[i] = pubs.setdefault(p, len(pubs))
    n_pub_src = Counter(src for _, src in issue_pub.values())
    return {"year": year, "mag": mag, "pub": pub, "gen": gen, "fmt": fmt, "aut": aut, "n_tokens": np.diff(corpus.bounds),
            "issue": corpus.issue_of, "family": np.array([u["family"] for u in corpus.units], dtype=np.int64),
            "publisher_sources": {k or "none": v for k, v in n_pub_src.items()}, "authors": len(authors), "magazines": len(mags)}


def band_code(years_apart):
    out = np.full(years_apart.shape, len(BANDS) - 1, dtype=np.int64)
    for i, (lo, hi, _) in enumerate(r5.YEAR_BANDS):
        out[(years_apart >= lo) & (years_apart < hi)] = i
    return out


# ---------------------------------------------------------------- the reuse inputs

def load_pair_table(path):
    if not os.path.exists(path):
        return None
    z = np.load(path)
    return {k: z[k] for k in z.files}


def pair_keys(a, b, n):
    return a.astype(np.int64) * n + b.astype(np.int64)


def exact_inputs(snap, corpus, log=_log):
    """{k: table with keys, longest, n, cover_a, cover_b (tokens), share_max} over cross-issue AND same-issue pairs."""
    n_tok = np.diff(corpus.bounds)
    out = {}
    for k in EXACT_KS:
        cross = load_pair_table(os.path.join(snap, f"{SET_NAME}_k{k}_pairs.npz"))
        same_p = os.path.join(snap, f"{SET_NAME}_k{k}_sameissue.npy")
        same = r2c.pair_table(np.load(same_p), corpus) if os.path.exists(same_p) else None
        parts = [t for t in (cross, same) if t is not None and t["a"].size]
        if not parts:
            out[k] = None
            continue
        a = np.concatenate([t["a"] for t in parts]); b = np.concatenate([t["b"] for t in parts])
        key = pair_keys(a, b, corpus.n_stories)
        cov_a = np.concatenate([np.rint(t["cover_a"] * n_tok[t["a"]]).astype(np.int64) for t in parts])
        cov_b = np.concatenate([np.rint(t["cover_b"] * n_tok[t["b"]]).astype(np.int64) for t in parts])
        o = np.argsort(key)
        out[k] = {"key": key[o], "longest": np.concatenate([t["max_len"] for t in parts])[o].astype(np.int64),
                  "n": np.concatenate([t["n_matches"] for t in parts])[o].astype(np.int64), "cover_a": cov_a[o], "cover_b": cov_b[o],
                  "share_max": np.concatenate([np.maximum(t["cover_a"], t["cover_b"]) for t in parts])[o].astype(np.float64)}
        log(f"[r05c] exact k={k}: {key.size:,} pairs with a match")
    return out


def para_inputs(snap, corpus, log=_log):
    n_tok = np.diff(corpus.bounds)
    out = {}
    for k in PARA_KS:
        cross = load_pair_table(os.path.join(snap, "para", f"{PARA_TAG}_k{k}_pairs.npz"))
        same_p = os.path.join(snap, "para", f"{PARA_TAG}_k{k}_sameissue.npy")
        same = None
        if os.path.exists(same_p):
            S = np.load(same_p)
            if S.size:
                six = np.stack([S["a"], S["b"], S["a0"], S["a1"], S["b0"], S["b1"]], axis=1).astype(np.int32)
                same = r2c.pair_table(six, corpus)
                import r04_paraphrase_corpus as r4c
                same.update(r4c._pair_extras(S, same, corpus))
        parts = [t for t in (cross, same) if t is not None and t["a"].size]
        if not parts:
            out[k] = None
            continue
        a = np.concatenate([t["a"] for t in parts]); b = np.concatenate([t["b"] for t in parts])
        key = pair_keys(a, b, corpus.n_stories)
        o = np.argsort(key)
        out[k] = {"key": key[o], "longest": np.concatenate([t["max_cols"] for t in parts])[o].astype(np.int64),
                  "n": np.concatenate([t["n_matches"] for t in parts])[o].astype(np.int64),
                  "best_identity": np.concatenate([t["best_identity"] for t in parts])[o].astype(np.float64),
                  "cover_a": np.concatenate([np.rint(t["cover_a"] * n_tok[t["a"]]).astype(np.int64) for t in parts])[o],
                  "cover_b": np.concatenate([np.rint(t["cover_b"] * n_tok[t["b"]]).astype(np.int64) for t in parts])[o],
                  "share_max": np.concatenate([np.maximum(t["cover_a"], t["cover_b"]) for t in parts])[o].astype(np.float64)}
        log(f"[r05c] near-verbatim K={k}: {key.size:,} pairs with an alignment")
    return out


def lookup(table, keys):
    """Rows of a sorted pair table for the given keys (-1 where absent)."""
    if table is None:
        return np.full(keys.shape, -1, dtype=np.int64)
    pos = np.searchsorted(table["key"], keys)
    pos = np.minimum(pos, table["key"].size - 1)
    hit = table["key"][pos] == keys
    return np.where(hit, pos, -1)


# ---------------------------------------------------------------- the reuse masks and topic similarity

def reuse_masks(corpus, snap):
    """Per story a boolean array over its tokens: inside a cross-issue exact match (seed 6) or a cross-issue
    near-verbatim alignment (the main K) — the pilot's reuse_masks. Returns one flat boolean array over all tokens."""
    mask = np.zeros(int(corpus.bounds[-1]), dtype=bool)
    spans = []
    p = os.path.join(snap, f"{SET_NAME}_k{EXACT_KS[0]}_matches.npy")
    if os.path.exists(p):
        M = np.load(p)
        if M.size:
            spans.append((M[:, 0], M[:, 2], M[:, 3])); spans.append((M[:, 1], M[:, 4], M[:, 5]))
    p = os.path.join(snap, "para", f"{PARA_TAG}_k{PARA_MAIN}_alignments.npy")
    if os.path.exists(p):
        A = np.load(p)
        if A.size:
            spans.append((A["a"], A["a0"], A["a1"])); spans.append((A["b"], A["b0"], A["b1"]))
    for s, st, en in spans:
        g0 = corpus.bounds[s.astype(np.int64)] + st.astype(np.int64)
        g1 = corpus.bounds[s.astype(np.int64)] + en.astype(np.int64)
        # mark [g0, g1) with a difference array
        d = np.zeros(mask.size + 1, dtype=np.int64)
        np.add.at(d, g0, 1)
        np.add.at(d, g1, -1)
        mask |= np.cumsum(d[:-1]) > 0
    return mask


def tfidf_matrix(corpus, mask, log=_log):
    """The pilot's TfidfVectorizer(analyzer=unigrams+bigrams, min_df=2, max_df=0.9, sublinear_tf=True) on the
    reuse-masked token sequences, from the ids: a CSR matrix with L2-normalised rows."""
    import scipy.sparse as sp
    t0 = time.time()
    tok, bounds = corpus.tok, corpus.bounds
    n_doc = corpus.n_stories
    V = np.int64(len(corpus.vocab))
    keep = ~mask
    ids = np.asarray(tok, dtype=np.int64)[keep]
    doc = np.repeat(np.arange(n_doc, dtype=np.int64), np.diff(bounds))[keep]
    # bigrams of consecutive remaining tokens of the same story
    same_doc = doc[1:] == doc[:-1]
    bi_key = V + ids[:-1] * V + ids[1:]                      # unigram keys 0..V-1; bigram keys V + a*V + b
    bi_doc = doc[:-1]
    keys = np.concatenate([ids, bi_key[same_doc]])
    docs = np.concatenate([doc, bi_doc[same_doc]])
    del ids, bi_key, bi_doc, same_doc
    # term counts per (doc, key)
    order = np.lexsort((keys, docs))
    keys, docs = keys[order], docs[order]
    del order
    new = np.r_[True, (keys[1:] != keys[:-1]) | (docs[1:] != docs[:-1])]
    starts = np.flatnonzero(new)
    counts = np.diff(np.r_[starts, keys.size]).astype(np.float64)
    keys, docs = keys[starts], docs[starts]
    # document frequency per key; min_df 2, max_df 0.9
    ko = np.argsort(keys, kind="stable")
    ks = keys[ko]
    kstart = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    df = np.diff(np.r_[kstart, ks.size])
    ukeys = ks[kstart]
    good = (df >= 2) & (df <= 0.9 * n_doc)
    ukeys, df = ukeys[good], df[good]
    pos = np.searchsorted(ukeys, keys)
    pos = np.minimum(pos, max(ukeys.size - 1, 0))
    hit = (ukeys.size > 0) & (ukeys[pos] == keys) if ukeys.size else np.zeros(keys.size, bool)
    keys, docs, counts, pos = keys[hit], docs[hit], counts[hit], pos[hit]
    idf = np.log((1 + n_doc) / (1 + df)) + 1.0
    vals = (1.0 + np.log(counts)) * idf[pos]
    X = sp.csr_matrix((vals, (docs, pos)), shape=(n_doc, ukeys.size))
    norms = np.sqrt(np.asarray(X.multiply(X).sum(axis=1)).ravel())
    X = sp.diags(1.0 / np.maximum(norms, 1e-12)) @ X
    X = X.tocsr()
    log(f"[r05c] topic: TF-IDF over {ukeys.size:,} word and word-pair features on the masked text, {X.nnz:,} entries, {time.time() - t0:.0f}s")
    return X


def pair_cosines(X, a, b, log=None):
    """Cosine (rows are unit vectors) for the pairs (a[i], b[i]), by story: X[a] against its partners."""
    out = np.zeros(a.size, dtype=np.float64)
    if not a.size:
        return out
    order = np.argsort(a, kind="stable")
    a_s, b_s = a[order], b[order]
    starts = np.flatnonzero(np.r_[True, a_s[1:] != a_s[:-1]])
    ends = np.r_[starts[1:], a_s.size]
    t0 = time.time()
    for i, (s, e) in enumerate(zip(starts, ends)):
        row = X[int(a_s[s])]
        sims = (X[b_s[s:e]] @ row.T).toarray().ravel()
        out[order[s:e]] = sims
        if log and i and i % 10000 == 0:
            log(f"[r05c]   cosines for {e:,}/{a.size:,} pairs, {time.time() - t0:.0f}s")
    return out


def embedding_topic_vectors(corpus, mask, snap):
    """Mean passage vector per story (passages mostly inside masked reuse left out), unit length; nan rows where no
    passage remains; None when r04's embeddings are not there."""
    p_meta = os.path.join(snap, "para", "emb_meta.json")
    p_pass = os.path.join(snap, "para", "passages.npy")
    if not (os.path.exists(p_meta) and os.path.exists(p_pass)):
        return None
    meta = json.load(open(p_meta, encoding="utf-8"))
    if not meta.get("done"):
        return None
    P = np.load(p_pass)
    emb = np.memmap(os.path.join(snap, "para", "emb.f16"), dtype=np.float16, mode="r", shape=(meta["n"], meta["dim"]))
    if P.shape[0] != meta["n"]:
        return None
    cs = np.concatenate([[0], np.cumsum(mask.astype(np.int64))])
    g0 = corpus.bounds[P[:, 0].astype(np.int64)] + P[:, 1]
    g1 = corpus.bounds[P[:, 0].astype(np.int64)] + P[:, 2]
    inside = cs[g1] - cs[g0]
    use = inside * 2 <= (P[:, 2] - P[:, 1])
    sums = np.zeros((corpus.n_stories, meta["dim"]), dtype=np.float64)
    counts = np.zeros(corpus.n_stories, dtype=np.int64)
    rows = np.flatnonzero(use)
    for s in range(0, rows.size, 500_000):
        r = rows[s:s + 500_000]
        np.add.at(sums, P[r, 0].astype(np.int64), np.asarray(emb[r], dtype=np.float64))
    np.add.at(counts, P[use, 0].astype(np.int64), 1)
    mean = sums / np.maximum(counts, 1)[:, None]
    mean /= np.maximum(np.linalg.norm(mean, axis=1, keepdims=True), 1e-9)
    mean[counts == 0] = np.nan
    return mean.astype(np.float32)


# ---------------------------------------------------------------- the pairs: matched, and the sample of the unmatched

def exact_cell_counts(year, issue=None):
    """The exact number of unordered CROSS-ISSUE pairs of dated stories in every (later decade, years band) cell, from
    the dates alone: for every story as the later one, the earlier stories at a distance inside the band (sorted dates
    and searchsorted); pairs with the same date (band 0-2) counted once; the pairs inside one issue (same date, band
    0-2) taken out when `issue` is given."""
    dated = ~np.isnan(year)
    ys = np.sort(year[dated])
    dec = (ys // 10 * 10).astype(np.int64)
    counts = Counter()
    for lo, hi, name in r5.YEAR_BANDS:
        lower = np.searchsorted(ys, ys - hi, side="right")            # i with ys[i] <= ys[j] - hi: too far back
        if lo == 0:
            upper = np.searchsorted(ys, ys, side="left")               # i with ys[i] < ys[j]: strictly earlier
        else:
            upper = np.searchsorted(ys, ys - lo, side="right")        # i with ys[i] <= ys[j] - lo
        per_j = upper - lower
        for d in np.unique(dec):
            counts[(int(d), name)] += int(per_j[dec == d].sum())
        if lo == 0:                                                   # pairs with the same date, once each
            vals, first, g = np.unique(ys, return_index=True, return_counts=True)
            for v, f, c in zip(vals, first, g):
                if c > 1:
                    counts[(int(dec[f]), name)] += int(c * (c - 1) // 2)
    if issue is not None:                                             # the pairs inside one issue are not cross-issue pairs
        iss = issue[dated]
        yd = year[dated]
        for i in np.unique(iss):
            sel = iss == i
            g = int(sel.sum())
            if g > 1:
                counts[(int(yd[sel][0] // 10 * 10), BANDS[0])] -= g * (g - 1) // 2
    return counts


def draw_unmatched(year, same_issue_of, matched_keys, n_stories, per_cell, seed, cell_total, matched_cell, log=_log):
    """A stratified random sample of unmatched cross-issue pairs of dated stories: per (later decade, band) cell up
    to per_cell pairs, drawn uniformly by rejection from random story pairs; returns (a, b) and the cells' weights."""
    rng = np.random.default_rng(seed)
    dated = np.flatnonzero(~np.isnan(year))
    want = {c: min(per_cell, cell_total[c] - matched_cell.get(c, 0)) for c in cell_total}
    got = {c: [] for c in cell_total}
    need = sum(max(0, v) for v in want.values())
    t0 = time.time()
    tries = 0
    while need > 0 and tries < 400:
        tries += 1
        m = 2_000_000
        i = dated[rng.integers(0, dated.size, m)]
        j = dated[rng.integers(0, dated.size, m)]
        ok = i != j
        i, j = i[ok], j[ok]
        a, b = np.minimum(i, j), np.maximum(i, j)
        ok = same_issue_of[a] != same_issue_of[b]
        a, b = a[ok], b[ok]
        key = pair_keys(a, b, n_stories)
        pos = np.searchsorted(matched_keys, key)
        pos = np.minimum(pos, max(matched_keys.size - 1, 0))
        if matched_keys.size:
            ok = matched_keys[pos] != key
            a, b, key = a[ok], b[ok], key[ok]
        # unique within this batch
        key, idx = np.unique(key, return_index=True)
        a, b = a[idx], b[idx]
        ya, yb = year[a], year[b]
        later = np.maximum(ya, yb)
        apart = later - np.minimum(ya, yb)
        dec = (later // 10 * 10).astype(np.int64)
        bc = band_code(apart)
        for c in list(got):
            if want[c] - len(got[c]) <= 0:
                continue
            sel = np.flatnonzero((dec == c[0]) & (bc == BANDS.index(c[1])))
            if not sel.size:
                continue
            take = sel[:want[c] - len(got[c])]
            got[c].extend(zip(a[take].tolist(), b[take].tolist()))
        need = sum(max(0, want[c] - len(got[c])) for c in got)
    # de-duplicate across batches
    a_all, b_all, w_all = [], [], []
    for c, lst in got.items():
        if not lst:
            continue
        arr = np.array(sorted(set(lst)), dtype=np.int64)
        n_unm = cell_total[c] - matched_cell.get(c, 0)
        w = n_unm / arr.shape[0]
        a_all.append(arr[:, 0]); b_all.append(arr[:, 1]); w_all.append(np.full(arr.shape[0], w))
    if not a_all:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0)
    a = np.concatenate(a_all); b = np.concatenate(b_all); w = np.concatenate(w_all)
    log(f"[r05c] sample {seed}: {a.size:,} unmatched pairs from {sum(1 for v in got.values() if v)} cells in {tries} batches, {time.time() - t0:.0f}s")
    return a, b, w


def build_rows(corpus, st, exact, para, a, b, matched, draw, weight, X, E, log=_log):
    """The table rows for the pairs (a, b) — the pilot's columns."""
    n = corpus.n_stories
    R = np.zeros(a.size, dtype=ROW)
    R["a"], R["b"] = a, b
    R["same_issue"] = st["issue"][a] == st["issue"][b]
    R["same_family"] = st["family"][a] == st["family"][b]
    R["same_magazine"] = st["mag"][a] == st["mag"][b]
    R["same_publisher"] = (st["pub"][a] >= 0) & (st["pub"][a] == st["pub"][b])
    R["same_genre"] = st["gen"][a] == st["gen"][b]
    R["same_format"] = st["fmt"][a] == st["fmt"][b]
    known = (st["aut"][a] >= 0) & (st["aut"][b] >= 0)
    R["author_known"] = known
    R["same_author"] = known & (st["aut"][a] == st["aut"][b])
    ya, yb = st["year"][a], st["year"][b]
    R["year_a"], R["year_b"] = ya, yb
    later = np.maximum(ya, yb); earlier = np.minimum(ya, yb)
    R["later_year"] = later
    apart = np.round(later - earlier, 3)
    R["years_apart"] = apart
    R["years_band"] = band_code(apart)
    R["later_decade"] = (later // 10 * 10).astype(np.int16)
    R["matched"], R["draw"], R["weight"] = matched, draw, weight
    key = pair_keys(a, b, n)
    for k in EXACT_KS:
        t = exact.get(k)
        pos = lookup(t, key)
        hit = pos >= 0
        for c in ("longest", "n", "cover_a", "cover_b", "share_max"):
            col = np.zeros(a.size, dtype=np.float64)
            if t is not None:
                col[hit] = t[c][pos[hit]]
            R[f"exact_k{k}_{c}"] = col
    for k in PARA_KS:
        t = para.get(k)
        pos = lookup(t, key)
        hit = pos >= 0
        for c in ("longest", "n", "best_identity", "cover_a", "cover_b", "share_max"):
            col = np.zeros(a.size, dtype=np.float64)
            if t is not None:
                col[hit] = t[c][pos[hit]]
            R[f"para_k{k}_{c}"] = col
    R["topic_tfidf"] = np.round(pair_cosines(X, a, b, log=log), 5)            # five decimals, as the pilot's rows
    if E is not None:
        ea, eb = E[a].astype(np.float64), E[b].astype(np.float64)
        R["topic_emb"] = np.round(np.where(np.isnan(ea).any(axis=1) | np.isnan(eb).any(axis=1), np.nan, (ea * eb).sum(axis=1)), 5)
    else:
        R["topic_emb"] = np.nan
    return R


def weighted_quantiles(values, weights, qs):
    """Weighted quantiles that reduce to the pilot's (pandas, linear interpolation) when the weights are equal."""
    o = np.argsort(values, kind="stable")
    v, w = values[o], weights[o]
    if v.size == 0:
        return [float("nan") for _ in qs]
    if v.size == 1:
        return [float(v[0]) for _ in qs]
    cw = np.cumsum(w) - w
    denom = w.sum() - w[-1]
    pos = cw / denom if denom > 0 else np.linspace(0, 1, v.size)
    return [float(np.interp(q, pos, v)) for q in qs]


# ---------------------------------------------------------------- the report

def wsurvival(values, weights, grid):
    tot = weights.sum()
    return {int(L): (round(float(weights[values >= L].sum() / tot), 6) if tot > 0 else None) for L in grid}


def background_curves(R):
    bg = R[R["same_issue"] == 0]
    w = bg["weight"]
    out = {"n_rows": int(bg.size), "n_pairs_represented": round(float(w.sum())), "exact": {}, "paraphrase": {}}
    strata = {"by_topic_q": bg["topic_q"], "by_years_band": bg["years_band"], "by_later_decade": bg["later_decade"]}
    names = {"by_topic_q": lambda v: str(int(v)), "by_years_band": lambda v: BANDS[int(v)], "by_later_decade": lambda v: str(int(v))}
    for k in EXACT_KS:
        col = bg[f"exact_k{k}_longest"]
        grid = [L for L in EXACT_GRID if L >= k]
        cur = {"overall": wsurvival(col, w, grid), "p_any": round(float(w[col >= k].sum() / w.sum()), 6) if w.sum() else None}
        for sname, arr in strata.items():
            cur[sname] = {names[sname](v): wsurvival(col[arr == v], w[arr == v], grid) for v in np.unique(arr)}
        known = bg["author_known"] == 1
        cur["by_same_author"] = {str(int(v)): {"n": int((known & (bg["same_author"] == v)).sum()),
                                               **wsurvival(col[known & (bg["same_author"] == v)], w[known & (bg["same_author"] == v)], grid)}
                                 for v in (0, 1)}
        out["exact"][f"k{k}"] = cur
    for k in PARA_KS:
        col = bg[f"para_k{k}_longest"]
        cur = {"overall": wsurvival(col, w, PARA_GRID), "p_any": round(float(w[col >= 20].sum() / w.sum()), 6) if w.sum() else None}
        for sname in ("by_topic_q", "by_years_band"):
            arr = strata[sname]
            cur[sname] = {names[sname](v): wsurvival(col[arr == v], w[arr == v], PARA_GRID) for v in np.unique(arr)}
        ident = bg[f"para_k{k}_best_identity"][col >= 20]
        cur["identity_hist"] = {f"{lo/100:.2f}-{(lo+10)/100:.2f}": int(((ident >= lo / 100) & (ident < (lo + 10) / 100)).sum()) for lo in range(60, 100, 10)}
        cur["identity_hist"]["1.00"] = int((ident >= 1.0).sum())
        out["paraphrase"][f"k{k}"] = cur
    out["by_stratum"] = {}
    for name, col, grid in (("exact_k6", "exact_k6_longest", [L for L in EXACT_GRID if L >= 6]), ("exact_k7", "exact_k7_longest", [L for L in EXACT_GRID if L >= 7]),
                            ("exact_k8", "exact_k8_longest", [L for L in EXACT_GRID if L >= 8]), ("para_k10", "para_k10_longest", PARA_GRID)):
        t = {}
        codes = bg["later_decade"].astype(np.int64) * 100 + bg["years_band"].astype(np.int64) * 10 + bg["topic_q"].astype(np.int64)
        for c in np.unique(codes):
            sel = codes == c
            dec, band, q = int(c // 100), BANDS[int(c // 10 % 10)], int(c % 10)
            t[f"{dec}s|{band}|q{q}"] = {"n_rows": int(sel.sum()), "n_pairs": round(float(w[sel].sum())), **wsurvival(bg[col][sel], w[sel], grid)}
        out["by_stratum"][name] = t
    out["time_table"] = {}
    for name, col, thr in (("exact_k6", "exact_k6_longest", 6), ("exact_k8", "exact_k8_longest", 8), ("para_k10", "para_k10_longest", 20)):
        t = {}
        codes = bg["later_decade"].astype(np.int64) * 10 + bg["years_band"].astype(np.int64)
        for c in np.unique(codes):
            sel = codes == c
            v = bg[col][sel]; ww = w[sel]
            anym = v >= thr
            t[f"{int(c // 10)}s|{BANDS[int(c % 10)]}"] = {"n_pairs": round(float(ww.sum())), "p_any": round(float(ww[anym].sum() / ww.sum()), 6) if ww.sum() else None,
                                                        "mean_longest_given_any": round(float(v[anym].mean()), 2) if anym.any() else None}
        out["time_table"][name] = t
    return out


def unusual(R, corpus, top=25):
    """The pilot's surprise table: for matched cross-issue pairs, the weighted share of comparable pairs (same topic
    quartile and years band) that share at least as much; small = unusual. Vectorized per stratum."""
    bg = R[R["same_issue"] == 0]
    out = {}
    for name, col, thr in (("exact_k6", "exact_k6_longest", 6), ("para_k10", "para_k10_longest", 20)):
        best = []
        n_matched = 0
        codes = bg["topic_q"].astype(np.int64) * 10 + bg["years_band"].astype(np.int64)
        for c in np.unique(codes):
            sel = np.flatnonzero(codes == c)
            vals = bg[col][sel].astype(np.int64); ww = bg["weight"][sel]
            tot = float(ww.sum())
            m = np.flatnonzero(vals >= thr)
            if not m.size or tot <= 0:
                continue
            n_matched += int(m.size)
            order = np.argsort(-vals, kind="stable")
            sv, cum = vals[order], np.cumsum(ww[order])
            pos = np.searchsorted(-sv, -vals[m], side="right") - 1          # the last row with value >= v
            p = cum[pos] / tot
            keep = np.argsort(p, kind="stable")[:top]
            for i in keep:
                r = sel[m[i]]
                best.append({"a": corpus.units[int(bg["a"][r])]["story_id"], "b": corpus.units[int(bg["b"][r])]["story_id"], "longest": int(vals[m[i]]),
                             "topic_q": int(c // 10), "years_band": BANDS[int(c % 10)], "stratum_pairs": round(tot), "p_at_least": round(float(p[i]), 8),
                             "same_author": int(bg["same_author"][r])})
        best.sort(key=lambda r: (r["p_at_least"], -r["longest"]))
        out[name] = {"n_matched_pairs": n_matched, "most_unusual": best[:top]}
    return out


def fit_models(R, corpus, cap=100_000, log=_log):
    """The pilot's two-part model (fit_two_part) on a capped, unweighted subsample: a machinery check, not a finding."""
    import pandas as pd
    bg = R[R["same_issue"] == 0]
    rng = np.random.default_rng(0)
    m = np.flatnonzero(bg["matched"] == 1); u = np.flatnonzero(bg["matched"] == 0)
    take = np.concatenate([rng.choice(m, min(m.size, cap // 2), replace=False), rng.choice(u, min(u.size, cap // 2), replace=False)])
    sub = bg[np.sort(take)]
    sid = [corpus.units[int(i)]["story_id"] for i in range(corpus.n_stories)]
    df = pd.DataFrame({"a": [sid[int(x)] for x in sub["a"]], "b": [sid[int(x)] for x in sub["b"]], "same_issue": 0,
                       "topic_tfidf": sub["topic_tfidf"].astype(float), "years_apart": sub["years_apart"].astype(float),
                       "later_year": sub["later_year"].astype(float), "same_author": sub["same_author"].astype(int),
                       "author_known": sub["author_known"].astype(int), "same_magazine": sub["same_magazine"].astype(int),
                       "same_publisher": sub["same_publisher"].astype(int), "same_genre": sub["same_genre"].astype(int)})
    for k in EXACT_KS:
        for c in ("longest", "cover_a", "cover_b"):
            df[f"exact_k{k}_{c}"] = sub[f"exact_k{k}_{c}"].astype(float)
    for k in PARA_KS:
        for c in ("longest", "cover_a", "cover_b"):
            df[f"para_k{k}_{c}"] = sub[f"para_k{k}_{c}"].astype(float)
    units = [{"story_id": s, "meta": corpus.units[i]["meta"]} for i, s in enumerate(sid)]
    out = {"note": f"fit on an unweighted subsample of {len(df):,} cross-issue rows (half matched, half unmatched): a machinery check, not a finding"}
    try:
        import statsmodels  # noqa: F401
    except ImportError:
        out["skipped"] = "statsmodels is not installed"
        return out
    for name, thr in (("exact_k6", 6), ("exact_k7", 7), ("para_k10", 20)):
        try:
            out[name] = r5.fit_two_part(df, units, name, thr, log)
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": repr(e)[:200]}
    return out


# ---------------------------------------------------------------- run

def run(snap=SNAP, outdir=OUTDIR, per_cell=PER_CELL, seeds=SEEDS, model=False, log=_log, all_pairs=False):
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    corpus = r2c.Corpus.load(snap)
    st = story_table(corpus, raw_dir=os.path.join(ROOT, "data", "raw"))
    log(f"[r05c] {corpus.n_stories:,} stories; dated {int((~np.isnan(st['year'])).sum()):,}; {st['magazines']} magazines; "
        f"{st['authors']} author keys; publishers by source {st['publisher_sources']}")
    exact = exact_inputs(snap, corpus, log)
    para = para_inputs(snap, corpus, log)
    n = corpus.n_stories
    # the matched pairs: exact k6 or near-verbatim K=20 (the widest), dated stories only
    keys = []
    for t in (exact.get(EXACT_KS[0]), para.get(max(PARA_KS))):
        if t is not None:
            keys.append(t["key"])
    mkeys = np.unique(np.concatenate(keys)) if keys else np.zeros(0, np.int64)
    ma, mb = mkeys // n, mkeys % n
    dated = ~np.isnan(st["year"])
    keep = dated[ma] & dated[mb]
    ma, mb, mkeys = ma[keep], mb[keep], mkeys[keep]
    log(f"[r05c] matched pairs (exact k{EXACT_KS[0]} or near-verbatim K{max(PARA_KS)}), both dated: {mkeys.size:,}")
    # masks and topic
    mask = reuse_masks(corpus, snap)
    log(f"[r05c] reuse masks cover {int(mask.sum()):,} of {mask.size:,} tokens")
    X = tfidf_matrix(corpus, mask, log)
    E = embedding_topic_vectors(corpus, mask, snap)
    log(f"[r05c] embedding topic column: {'yes' if E is not None else 'no (r04 embeddings not there yet)'}")
    # cell sizes and the matched pairs' cells
    cell_total = exact_cell_counts(st["year"], st["issue"])
    later = np.maximum(st["year"][ma], st["year"][mb]); apart = later - np.minimum(st["year"][ma], st["year"][mb])
    cross_m = st["issue"][ma] != st["issue"][mb]
    mc = Counter()
    for d, bcode in zip((later[cross_m] // 10 * 10).astype(np.int64).tolist(), band_code(apart[cross_m]).tolist()):
        mc[(d, BANDS[bcode])] += 1
    # the rows: matched (weight 1), then the sample of the unmatched (two draws)
    parts = [build_rows(corpus, st, exact, para, ma, mb, np.ones(ma.size, np.uint8), np.zeros(ma.size, np.uint8), np.ones(ma.size), X, E, log)]
    if all_pairs:
        # every unmatched cross-issue pair of dated stories (tests only): weight 1
        d_idx = np.flatnonzero(dated)
        ii, jj = np.triu_indices(d_idx.size, 1)
        a, b = d_idx[ii], d_idx[jj]
        key = pair_keys(a, b, n)
        pos = np.searchsorted(mkeys, key); pos = np.minimum(pos, max(mkeys.size - 1, 0))
        un = ~(mkeys[pos] == key) if mkeys.size else np.ones(key.size, bool)
        a, b = a[un], b[un]
        parts.append(build_rows(corpus, st, exact, para, a, b, np.zeros(a.size, np.uint8), np.ones(a.size, np.uint8), np.ones(a.size), X, E, log))
        draws_info = {"all_pairs": True}
    else:
        draws_info = {"per_cell": per_cell, "seeds": list(seeds), "cells": {f"{d}s|{b}": {"pairs": cnt, "matched": mc.get((d, b), 0)} for (d, b), cnt in sorted(cell_total.items())}}
        for di, seed in enumerate(seeds, 1):
            a, b, w = draw_unmatched(st["year"], st["issue"], mkeys, n, per_cell, seed, cell_total, mc, log)
            parts.append(build_rows(corpus, st, exact, para, a, b, np.zeros(a.size, np.uint8), np.full(a.size, di, np.uint8), w, X, E, log))
    R = np.concatenate(parts)
    # topic quartiles: weighted over the cross-issue rows of the first draw plus the matched
    base = R[(R["same_issue"] == 0) & (R["draw"] <= 1)]
    qs = weighted_quantiles(base["topic_tfidf"].astype(np.float64), base["weight"].astype(np.float64), [0.25, 0.5, 0.75])
    R["topic_q"] = 1 + (R["topic_tfidf"][:, None] > np.array(qs, dtype=np.float32)[None, :]).sum(axis=1)
    np.save(os.path.join(outdir, f"pairs_{SET_NAME}.npy"), R)
    # the report over the matched rows and the first draw (the second draw is the agreement check)
    R1 = R[R["draw"] <= 1]
    summary = {"set": SET_NAME, "label": LABEL, "collection": os.path.basename(snap), "stories": int(n), "dated_stories": int(dated.sum()),
               "rows": int(R.size), "matched_rows": int((R["matched"] == 1).sum()), "sampled_rows_draw1": int((R["draw"] == 1).sum()),
               "sampled_rows_draw2": int((R["draw"] == 2).sum()), "same_issue_rows": int((R["same_issue"] == 1).sum()),
               "cross_issue_pairs_represented": round(float(R1[R1["same_issue"] == 0]["weight"].sum())),
               "topic_quartile_cuts": [round(x, 5) for x in qs], "publisher_sources": st["publisher_sources"],
               "author_known_rows": int(R1["author_known"].sum()), "same_author_rows": int(R1["same_author"].sum()),
               "sampler": draws_info, "columns": list(R.dtype.names), "generated": time.strftime("%Y-%m-%d %H:%M")}
    cross = R1[R1["same_issue"] == 0]
    if not np.isnan(cross["topic_emb"]).all():
        ok = ~np.isnan(cross["topic_emb"])
        summary["topic_tfidf_vs_embedding_corr"] = round(float(np.corrcoef(cross["topic_tfidf"][ok], cross["topic_emb"][ok])[0, 1]), 4)
    summary["background"] = background_curves(R1)
    summary["unusual"] = unusual(R1, corpus)
    if not all_pairs and len(seeds) > 1:
        R2 = R[(R["matched"] == 1) | (R["draw"] == 2)]
        chk = {}
        for name, col, thr in (("exact_k6", "exact_k6_longest", 6), ("exact_k8", "exact_k8_longest", 8), ("para_k10", "para_k10_longest", 20)):
            est = []
            for RR in (R1, R2):
                bg = RR[RR["same_issue"] == 0]; w = bg["weight"]
                q4 = bg["topic_q"] == 4
                est.append({"p_any": float(w[bg[col] >= thr].sum() / w.sum()) if w.sum() else None,
                            "p_any_topic_q4": float(w[q4 & (bg[col] >= thr)].sum() / w[q4].sum()) if w[q4].sum() else None,
                            "mean_topic_unmatched": float(np.average(bg["topic_tfidf"][bg[col] < thr], weights=w[bg[col] < thr])) if (bg[col] < thr).any() else None})
            chk[name] = {"draw1": est[0], "draw2": est[1],
                         "abs_diff": {k: (round(abs(est[0][k] - est[1][k]), 6) if est[0][k] is not None and est[1][k] is not None else None) for k in est[0]}}
        summary["sampler_check"] = {"note": "two independent stratified draws of the unmatched pairs; p_any per cell is exact by design (every matched pair is in the table and the cell sizes are counted), so the differences show the sampling error of the finer splits", "checks": chk}
    if model:
        summary["models"] = fit_models(R1, corpus, log=log)
    json.dump(summary, open(os.path.join(outdir, f"summary_{SET_NAME}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # the rows with the most reuse, as CSV for people
    top = R1[R1["same_issue"] == 0]
    order = np.argsort(-(top["exact_k6_longest"].astype(np.int64) * 1000 + top["para_k10_longest"].astype(np.int64)))[:50_000]
    sid = [u["story_id"] for u in corpus.units]
    with gzip.open(os.path.join(outdir, f"pairs_{SET_NAME}_top.csv.gz"), "wt", encoding="utf-8") as f:
        cols = [c for c in R.dtype.names if c not in ("a", "b")]
        f.write("a,b," + ",".join(cols) + "\n")
        for r in top[order]:
            f.write(f"{sid[int(r['a'])]},{sid[int(r['b'])]}," + ",".join(str(r[c]) for c in cols) + "\n")
    summary["seconds"] = round(time.time() - t0, 1)
    json.dump(summary, open(os.path.join(outdir, f"summary_{SET_NAME}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"[r05c] {R.size:,} rows ({summary['matched_rows']:,} matched; {summary['sampled_rows_draw1']:,} + {summary['sampled_rows_draw2']:,} sampled) "
        f"representing {summary['cross_issue_pairs_represented']:,} cross-issue pairs; {summary['seconds']}s")
    return R, summary


# ---------------------------------------------------------------- tests

def selftest():
    """A fabricated corpus through r02c, r04c (fake embeddings) and this stage in all-pairs mode, against the pilot's
    r05 fed the pilot's own r02/r04 outputs on the same stories: every column of the pair table and the background
    curves must agree; then the sampler on the same data: exact cell sizes, weights that sum to the cells, and the two
    draws' estimates close to the full table's."""
    import random
    import tempfile
    import r02_verbatim as r2
    import r04_paraphrase as r4
    import r04_paraphrase_corpus as r4c
    ok = True
    rng = random.Random(21)
    recs = r2c._random_corpus(rng, n_stories=30, n_issues=10, vocab=4000, planted=8, commonplace=0)
    dates = ["1925-03", "1931-07", "1936-01", "1942-11", "1948-05", "1952-09", "1929-12", "1934-04", "1939-08", "1950-02"]
    mags = ["Argosy", "Weird Tales", "Argosy", "Astounding", "Weird Tales", "Argosy", "Astounding", "Blue Book", "Argosy", "Weird Tales"]
    for r in recs:
        words = r["text"].split()
        r["text"] = ". ".join(" ".join(words[i:i + 10]) for i in range(0, len(words), 10))
        k = int(r["issue"][1:]) % 10
        r["cover_date"] = dates[k]; r["magazine"] = mags[k]; r["genre"] = ["weird", "adventure"][k % 2]; r["format"] = "pulp"
        r["author"] = ["Robert E. Howard", "Max Brand", None, "Lester Dent"][int(r["story_id"][1:]) % 4]
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
        for k in EXACT_KS:
            r2c.run(corpus, k, SET_NAME, snap, log=lambda *a: None)
        para = os.path.join(snap, "para")
        saved = (r4c.BUCKET_CANDS, r4c.EMB_CHUNK)
        r4c.BUCKET_CANDS, r4c.EMB_CHUNK = 500, 50
        try:
            r4c.step_alts(corpus, para, log=lambda *a: None); r4c.step_passages(corpus, para, log=lambda *a: None)
            r4c.step_embed(snap, para, backend="fake", workers=2, log=lambda *a: None)
            r4c.step_knn(para, backend="numpy", k=r4c.K_RETRIEVE, log=lambda *a: None)
            r4c.step_candidates(corpus, para, snap, log=lambda *a: None)
            r4c.step_align(snap, para, workers=2, ks=PARA_KS, log=lambda *a: None)
            r4c.step_report(corpus, para, ks=PARA_KS, write_floor=20, log=lambda *a: None)
        finally:
            r4c.BUCKET_CANDS, r4c.EMB_CHUNK = saved
        # the pilot's r02/r04/r05 on the same stories (the same neighbours: r04 fed the array pipeline's knn)
        units = r2.build_units([r for iid in sorted(by_issue) for r in by_issue[iid]])
        reuse = os.path.join(td, "reuse"); os.makedirs(os.path.join(reuse, "para"))
        for k in EXACT_KS:
            r2.run(units, k, SET_NAME, reuse)
        idx = np.load(os.path.join(para, "knn_idx.npy")); sim = np.load(os.path.join(para, "knn_sim.npy"))
        P = np.load(os.path.join(para, "passages.npy"))
        pl = r4.make_passages(units, r4.WINDOW, r4.STRIDE)
        nb = [(int(i), int(idx[i, r]), r + 1, float(sim[i, r])) for i in range(P.shape[0]) for r in range(idx.shape[1]) if idx[i, r] >= 0]
        orig_knn, orig_embed = r4.knn, r4.embed_passages
        r4.knn = lambda vec, passages, k, block=1024: nb
        r4.embed_passages = lambda units_, passages, cache_path, log=print: r4c.fake_embed([r4.passage_text(units_, p) for p in passages])
        try:
            r4.run_set(units, SET_NAME, outdir=os.path.join(reuse, "para"), log=lambda *a: None)
        finally:
            r4.knn, r4.embed_passages = orig_knn, orig_embed
        # the embedding topic of the pilot reads emb_*.npz from the para dir: write the fake vectors there in its shape
        vec = r4c.fake_embed([r4.passage_text(units, p) for p in pl])
        np.savez(os.path.join(reuse, "para", f"emb_{SET_NAME}_w{r4.WINDOW}s{r4.STRIDE}_test.npz"), vec=vec, n=len(pl))
        saved_reuse = r5.REUSE
        r5.REUSE = reuse
        try:
            df = r5.build_pair_table(units, SET_NAME, log=lambda *a: None)
        finally:
            r5.REUSE = saved_reuse
        # ours, in all-pairs mode
        R, summary = run(snap, os.path.join(snap, "background"), model=False, log=lambda *a: None, all_pairs=True)
        sid = [u["story_id"] for u in corpus.units]
        ours = {(sid[int(r["a"])], sid[int(r["b"])]): r for r in R}
        theirs = {(r["a"], r["b"]): r for _, r in df.iterrows()}
        same_keys = set(ours) == set(theirs)
        bad = []
        for key, r in theirs.items():
            o = ours.get(key)
            if o is None:
                continue
            checks = {"same_issue": (int(r["same_issue"]), int(o["same_issue"])), "same_magazine": (int(r["same_magazine"]), int(o["same_magazine"])),
                      "same_genre": (int(r["same_genre"]), int(o["same_genre"])), "same_author": (int(r["same_author"]), int(o["same_author"])),
                      "author_known": (int(r["author_known"]), int(o["author_known"])), "years_band": (r["years_band"], BANDS[int(o["years_band"])]),
                      "later_decade": (int(r["later_decade"]), int(o["later_decade"])), "years_apart": (round(float(r["years_apart"]), 2), round(float(o["years_apart"]), 2)),
                      "topic_tfidf": (round(float(r["topic_tfidf"]), 3), round(float(o["topic_tfidf"]), 3)),
                      "topic_emb": (round(float(r["topic_emb"]), 3), round(float(o["topic_emb"]), 3)), "topic_q": (int(r["topic_q"]), int(o["topic_q"]))}
            for k in EXACT_KS:
                for c in ("longest", "n", "cover_a", "cover_b"):
                    checks[f"exact_k{k}_{c}"] = (int(r[f"exact_k{k}_{c}"]), int(o[f"exact_k{k}_{c}"]))
                checks[f"exact_k{k}_share_max"] = (round(float(r[f"exact_k{k}_share_max"]), 4), round(float(o[f"exact_k{k}_share_max"]), 4))
            for k in PARA_KS:
                for c in ("longest", "n", "cover_a", "cover_b"):
                    checks[f"para_k{k}_{c}"] = (int(r[f"para_k{k}_{c}"]), int(o[f"para_k{k}_{c}"]))
                checks[f"para_k{k}_best_identity"] = (round(float(r[f"para_k{k}_best_identity"]), 3), round(float(o[f"para_k{k}_best_identity"]), 3))
            for name, (x, y) in checks.items():
                if x != y and not (isinstance(x, float) and abs(x - y) <= 0.0011):
                    bad.append((key, name, x, y))
        n_matched = int((R["matched"] == 1).sum())
        print(f"all-pairs mode: {R.size} rows ({n_matched} matched) vs the pilot's {len(df)}; keys {'same' if same_keys else 'DIFFER'}; "
              f"column differences {len(bad)}" + (f" e.g. {bad[:3]}" if bad else "") + f" -> {'OK' if same_keys and not bad else 'FAIL'}")
        ok = ok and same_keys and not bad
        # the background curves against the pilot's
        bgp = r5.background_curves(df)
        bgo = summary["background"]
        c_ok = all(abs(bgp["exact"][f"k{k}"]["p_any"] - bgo["exact"][f"k{k}"]["p_any"]) < 1e-6 for k in EXACT_KS) and \
            all(abs(bgp["paraphrase"][f"k{k}"]["p_any"] - bgo["paraphrase"][f"k{k}"]["p_any"]) < 1e-6 for k in PARA_KS) and \
            all(abs(bgp["exact"]["k6"]["overall"][L] - bgo["exact"]["k6"]["overall"][L]) < 1e-6 for L in range(6, 41))
        print(f"background curves: p_any exact {[bgo['exact'][f'k{k}']['p_any'] for k in EXACT_KS]} vs {[bgp['exact'][f'k{k}']['p_any'] for k in EXACT_KS]} -> {'OK' if c_ok else 'FAIL'}")
        ok = ok and c_ok
        # the sampler on the same data
        st = story_table(corpus)
        cells = exact_cell_counts(st["year"], st["issue"])
        # brute-force cell counts
        bf = Counter()
        dated = np.flatnonzero(~np.isnan(st["year"]))
        for ii in range(dated.size):
            for jj in range(ii + 1, dated.size):
                if st["issue"][dated[ii]] == st["issue"][dated[jj]]:
                    continue
                ya, yb = st["year"][dated[ii]], st["year"][dated[jj]]
                bf[(int(max(ya, yb) // 10 * 10), r5.year_band(max(ya, yb) - min(ya, yb)))] += 1
        cells_ok = {k: v for k, v in cells.items() if v} == dict(bf)
        if not cells_ok:
            print("  cells:", sorted(cells.items()), "\n  brute:", sorted(bf.items()))
        Rs, sm = run(snap, os.path.join(snap, "background_sampled"), per_cell=6, model=False, log=lambda *a: None)
        bg1 = Rs[(Rs["draw"] <= 1) & (Rs["same_issue"] == 0)]
        w_ok = abs(bg1["weight"].sum() - sum(bf.values())) < 1e-6 and abs(float(bg1["weight"].sum()) - sm["cross_issue_pairs_represented"]) < 1
        full_p = bgp["exact"]["k6"]["p_any"]
        est_p = sm["background"]["exact"]["k6"]["p_any"]
        p_ok = abs(full_p - est_p) < 1e-9          # exact by design
        print(f"sampler: cell sizes {'exact' if cells_ok else 'WRONG'}; weights sum to {bg1['weight'].sum():.0f} of {sum(bf.values())} cross-issue pairs "
              f"{'OK' if w_ok else 'FAIL'}; p_any k6 {est_p} vs full {full_p} {'OK' if p_ok else 'FAIL'}; two draws differ by "
              f"{sm['sampler_check']['checks']['exact_k6']['abs_diff']}")
        ok = ok and cells_ok and w_ok and p_ok
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=SNAP)
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--per-cell", type=int, default=PER_CELL)
    ap.add_argument("--model", action="store_true", help="also fit the pilot's two-part model on a capped subsample (statsmodels; slow)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    run(args.snap, args.out, args.per_cell, model=args.model)


if __name__ == "__main__":
    main()
