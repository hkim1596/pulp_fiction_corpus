#!/usr/bin/env python3
"""r02 at corpus scale — verbatim text reuse (protocol section 3.1) over the whole corpus, with arrays.

The method is r02_verbatim.py's, step for step: k-word shingles (k = 6, 7, 8 as separate passes) shared by two stories
are the seeds; a shingle in more than MAX_DF stories is commonplace and skipped (and written out); every seed is
extended left and right while the folded tokens stay identical; the maximal spans are de-duplicated; matches between
two records of the same issue go to a separate diagnostics file; clusters, pair statistics, story shares and the
length histogram are the pilot's own functions. What changes is the engine (docs/reuse-at-scale-plan-2026-10-14.md,
section 2): the pilot kept a Python dictionary from every shingle to its places, which does not fit half a billion
shingles; here the folded tokens are integer ids in one array, every shingle gets a 64-bit hash computed in numpy,
the hashes are sorted, shingles that occur once are dropped, and the rest are grouped by their actual tokens (so a
hash collision cannot join two different shingles), paired, reduced to one run per diagonal (same two stories, same
offset), extended in vectorized batches, and verified token by token. The result is the same set of maximal spans the
pilot's engine finds — --selftest proves it on fabricated corpora, --pilot-check on the pilot's own stories.

Two steps on the server:

    python3 pipeline/r02_verbatim_corpus.py tokenize --workers 32        # data/export/corpus/*.jsonl -> tokens/ (a snapshot:
                                                                         # token ids, story bounds, the canonical texts, vocabulary)
    python3 pipeline/r02_verbatim_corpus.py match --k 6 7 8              # -> data/reuse/corpus_dryrun/machine_k<k>_*

Everything is written under data/reuse/corpus_dryrun/ (the plan's section 1: an engineering dry run of 14 October 2026
onward, not the study's numbers; the study's run repeats the same code on the frozen corpus, version 1.0). The output
files have the pilot's shapes (<set>_k<k>_{stats,clusters,pairs,story_share,skipped_shingles}.json, _matches.jsonl,
_sameissue.jsonl, <set>_region_overlap.json) with these differences of size, all recorded in the stats: every match is
counted and kept as arrays (_matches.npy, _sameissue.npy, _pairs.npz), but the files with text (_matches.jsonl,
_sameissue.jsonl, _clusters.json) hold the matches of WRITE_FLOOR (10) tokens or more — at corpus scale the six-to-
nine-word matches run to hundreds of millions (the journal's estimate of 22 September: 200 million story pairs with a
six-word match, 24 million with seven, 2 million with eight) and cannot be written with excerpts or consolidated; cluster
members carry 300 characters of text, not 1,500; _pairs.json holds the 20,000 longest pairs and _skipped_shingles.json
the 50,000 most widespread commonplace shingles (the whole list in a .tsv beside it).

    python3 pipeline/r02_verbatim_corpus.py --selftest
    python3 pipeline/r02_verbatim_corpus.py --pilot-check [--stories data/pilot_stories.jsonl]

Memory at corpus scale (half a billion tokens): the token array 2 GB; one pass of k hashes and their sort about 25 GB;
the candidate seeds go to disk in buckets of 50 million when there are more. Measured in the sandbox on fabricated
corpora: 50 million tokens with few shared shingles, 14 s per k and 2.2 GB; 23 million tokens with 3.5 million seeds,
13 s per k and 1.5 GB. Time at corpus scale: the tokenization minutes in 32 processes; a pass per k under an hour.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r01_normalize import prepare, tokenize  # noqa: E402
import r02_verbatim as R  # noqa: E402       the pilot's engine and report functions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(ROOT, "data", "export", "corpus")
OUTDIR = os.path.join(ROOT, "data", "reuse", "corpus_dryrun")
LABEL = "engineering dry run of 14 October 2026 onward; not the study's numbers"
STORY_TYPES = R.STORY_TYPES
MIN_TOKENS = R.MIN_TOKENS
MAX_DF = R.MAX_DF
HASH_BASE = np.uint64(0x9E3779B97F4A7C15)
MEMBER_TEXT = 300           # characters of text per cluster member (the pilot wrote 1,500)
PAIR_CHUNK = 20_000_000     # candidate pairs handled at once
EXT_BATCH = 500_000         # runs extended at once
EXT_CHUNK = 32              # tokens compared per extension round
BUCKET_PAIRS = 50_000_000   # candidate seeds per bucket (more than this: the seeds go to disk, bucketed by story pair)
WRITE_FLOOR = 10            # matches of this many tokens or more go to the text files and the clusters (see run())
PAIRS_JSON = 20_000         # the longest story pairs written as JSON (the whole table is the .npz)
SKIPPED_JSON = 50_000       # the most widespread commonplace shingles written as JSON (the whole list is the .tsv)


# ---------------------------------------------------------------- the stories, as arrays

def _select(rec, which):
    if rec.get("type") not in STORY_TYPES:
        return False
    if which == "verified" and rec.get("status") != "verified":
        return False
    if which == "corrected" and rec.get("status") not in ("verified", "modified"):
        return False
    return True


def _meta_of(rec):
    return {k: rec.get(k) for k in ("issue", "magazine", "cover_date", "genre", "format", "type", "title", "author", "pages", "status")}


def _tokenize_files(args):
    """One worker: the story records of a list of export files -> a shard on disk (the canonical texts as UTF-8 bytes,
    the token ids against the worker's own vocabulary) and, returned, the stories' metadata and the vocabulary."""
    paths, which, shard_path = args
    vocab, stories = {}, []
    with open(shard_path + ".canon", "wb") as fc, open(shard_path + ".ids", "wb") as fi:
        for path in paths:
            off = 0
            with open(path, "rb") as f:
                for raw in f:
                    start, off = off, off + len(raw)
                    if not raw.strip():
                        continue
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    if not _select(rec, which):
                        continue
                    canon = prepare(rec.get("text", ""))
                    toks = [t[0] for t in tokenize(canon)]
                    if len(toks) < MIN_TOKENS:
                        continue
                    fi.write(np.fromiter((vocab.setdefault(t, len(vocab)) for t in toks), dtype=np.uint32, count=len(toks)).tobytes())
                    cb = canon.encode("utf-8")
                    fc.write(cb)
                    stories.append({"story_id": rec["story_id"], "issue": rec["issue"], "src": [os.path.basename(path), start],
                                    "n_tokens": len(toks), "n_bytes": len(cb), "fragments": list(rec.get("fragments") or []), "meta": _meta_of(rec)})
    return stories, [t for t, _ in sorted(vocab.items(), key=lambda kv: kv[1])]


def tokenize_corpus(export_dir=EXPORT_DIR, outdir=OUTDIR, which="machine", workers=8, log=print):
    """data/export/corpus/*.jsonl -> outdir/tokens/{tokens.u32, bounds.npy, canon.bin, canon_bounds.npy, stories.jsonl,
    vocab.txt, meta.json}: a self-contained snapshot of the story set (the canonical texts are kept, so the later steps
    do not depend on the export files, which the site rewrites when an issue's records change). Story order: the export
    files in name order, the records in file order (as the pilot's build_units kept file order)."""
    import shutil
    t0 = time.time()
    files = sorted(f for f in os.listdir(export_dir) if f.endswith(".jsonl"))
    paths = [os.path.join(export_dir, f) for f in files]
    workers = max(1, min(workers, len(paths)))
    per = -(-len(paths) // workers)
    shards = [paths[i * per:(i + 1) * per] for i in range(workers) if paths[i * per:(i + 1) * per]]     # contiguous: shard order is file order
    tdir = os.path.join(outdir, "tokens")
    os.makedirs(tdir, exist_ok=True)
    shard_paths = [os.path.join(tdir, f"_shard{i:03d}") for i in range(len(shards))]
    with Pool(len(shards)) as pool:
        results = pool.map(_tokenize_files, [(s, which, sp) for s, sp in zip(shards, shard_paths)])
    gvocab = {}
    all_stories = []
    n_tok = sum(s["n_tokens"] for st, _ in results for s in st)
    tok = np.memmap(os.path.join(tdir, "tokens.u32"), dtype=np.uint32, mode="w+", shape=(max(n_tok, 1),))
    pos = 0
    with open(os.path.join(tdir, "canon.bin"), "wb") as fc:
        for (stories, vocab), sp in zip(results, shard_paths):
            lut = np.fromiter((gvocab.setdefault(t, len(gvocab)) for t in vocab), dtype=np.uint32, count=len(vocab))
            ids = np.fromfile(sp + ".ids", dtype=np.uint32)
            tok[pos:pos + ids.size] = lut[ids] if ids.size else ids
            pos += ids.size
            with open(sp + ".canon", "rb") as fs:
                shutil.copyfileobj(fs, fc, 16 * 1024 * 1024)
            all_stories += stories
            os.remove(sp + ".ids")
            os.remove(sp + ".canon")
    tok.flush()
    del tok
    bounds = np.zeros(len(all_stories) + 1, dtype=np.int64)
    bounds[1:] = np.cumsum([s["n_tokens"] for s in all_stories])
    np.save(os.path.join(tdir, "bounds.npy"), bounds)
    cb = np.zeros(len(all_stories) + 1, dtype=np.int64)
    cb[1:] = np.cumsum([s["n_bytes"] for s in all_stories])
    np.save(os.path.join(tdir, "canon_bounds.npy"), cb)
    with open(os.path.join(tdir, "stories.jsonl"), "w", encoding="utf-8") as f:
        for s in all_stories:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(os.path.join(tdir, "vocab.txt"), "w", encoding="utf-8") as f:
        for t, _ in sorted(gvocab.items(), key=lambda kv: kv[1]):
            f.write(t + "\n")
    meta = {"generated": time.strftime("%Y-%m-%d %H:%M"), "which": which, "export_dir": os.path.relpath(export_dir, ROOT),
            "files": len(files), "stories": len(all_stories), "tokens": int(n_tok), "vocabulary": len(gvocab),
            "canon_bytes": int(cb[-1]), "issues": len({s["issue"] for s in all_stories}), "min_tokens": MIN_TOKENS,
            "story_types": list(STORY_TYPES), "workers": len(shards), "seconds": round(time.time() - t0, 1), "label": LABEL}
    json.dump(meta, open(os.path.join(tdir, "meta.json"), "w", encoding="utf-8"), indent=1)
    log(f"[r02c] tokenized {meta['stories']} stories of {meta['issues']} issues from {meta['files']} files: "
        f"{n_tok:,} tokens, {len(gvocab):,} distinct, {cb[-1] / 1e6:.0f} MB of text; {meta['seconds']}s")
    return meta


class _Offsets:
    """Character offsets of a story's tokens as an int32 array (n x 2), read like the pilot's list of (start, end)."""

    def __init__(self, arr):
        self.arr = arr

    def __getitem__(self, i):
        return (int(self.arr[i, 0]), int(self.arr[i, 1]))

    def __len__(self):
        return self.arr.shape[0]


class LazyUnit(dict):
    """A story unit whose canonical text and token offsets are read from the snapshot on first use (the pilot's
    cluster() and serialize_match() read u['canon'] and u['offsets']; keeping those for every story would not fit)."""

    def __init__(self, corpus, **kw):
        super().__init__(**kw)
        self._corpus = corpus

    def __missing__(self, key):
        if key in ("canon", "offsets"):
            self._corpus.fill(self)
            return dict.__getitem__(self, key)
        if key == "tokens":
            toks = [t[0] for t in tokenize(self["canon"])]
            dict.__setitem__(self, "tokens", toks)
            return toks
        raise KeyError(key)


class Corpus:
    """The token array, the story bounds and the story units of one tokenized corpus (or of a list of the pilot's units)."""

    def __init__(self, tok, bounds, units, export_dir=None, vocab=None):
        self.tok = tok                      # uint32, all tokens of all stories
        self.bounds = bounds                # int64, story s holds tok[bounds[s]:bounds[s+1]]
        self.export_dir = export_dir
        self.vocab = vocab                  # list: id -> folded token
        self.meta = None
        self.canon = None                   # uint8 memmap of every story's canonical text (tokenize_corpus), by canon_bounds
        self.canon_bounds = None
        self.set_units(units)

    def set_units(self, units):
        self.units = units                  # list of dicts: story_id, issue, fragments (set), meta, family, n_tokens, src
        self.n_stories = len(units)
        issues = sorted({u["issue"] for u in units})
        idx = {iid: i for i, iid in enumerate(issues)}
        self.issue_of = np.array([idx[u["issue"]] for u in units], dtype=np.int64) if units else np.zeros(0, np.int64)

    @classmethod
    def load(cls, outdir=OUTDIR, export_dir=EXPORT_DIR):
        tdir = os.path.join(outdir, "tokens")
        meta = json.load(open(os.path.join(tdir, "meta.json"), encoding="utf-8"))
        tok = np.fromfile(os.path.join(tdir, "tokens.u32"), dtype=np.uint32)[:meta["tokens"]]
        bounds = np.load(os.path.join(tdir, "bounds.npy"))
        vocab = [l.rstrip("\n") for l in open(os.path.join(tdir, "vocab.txt"), encoding="utf-8")]
        c = cls(tok, bounds, [], export_dir, vocab)
        c.canon = np.memmap(os.path.join(tdir, "canon.bin"), dtype=np.uint8, mode="r")
        c.canon_bounds = np.load(os.path.join(tdir, "canon_bounds.npy"))
        units = []
        with open(os.path.join(tdir, "stories.jsonl"), encoding="utf-8") as f:
            for i, line in enumerate(f):
                s = json.loads(line)
                units.append(LazyUnit(c, story_id=s["story_id"], issue=s["issue"], fragments=set(s["fragments"]),
                                      meta=s["meta"], n_tokens=s["n_tokens"], src=tuple(s["src"]), index=i))
        R._mark_region_families(units)
        c.set_units(units)
        c.meta = meta
        return c

    @classmethod
    def from_units(cls, units):
        """The pilot's units (r02_verbatim.build_units) as arrays: for the tests and the pilot check."""
        vocab = {}
        chunks = []
        for u in units:
            chunks.append(np.fromiter((vocab.setdefault(t, len(vocab)) for t in u["tokens"]), dtype=np.uint32, count=len(u["tokens"])))
            u["n_tokens"] = len(u["tokens"])
        tok = np.concatenate(chunks) if chunks else np.zeros(0, np.uint32)
        bounds = np.zeros(len(units) + 1, dtype=np.int64)
        bounds[1:] = np.cumsum([len(u["tokens"]) for u in units])
        return cls(tok, bounds, units, None, [t for t, _ in sorted(vocab.items(), key=lambda kv: kv[1])])

    def fill(self, u):
        """Set the story's canon (from the snapshot's canon.bin) and offsets (int32 array n x 2)."""
        i = u["index"]
        canon = bytes(self.canon[self.canon_bounds[i]:self.canon_bounds[i + 1]]).decode("utf-8")
        toks = tokenize(canon)
        if len(toks) != u["n_tokens"]:
            raise RuntimeError(f"{u['story_id']}: {len(toks)} tokens now, {u['n_tokens']} when tokenized: the snapshot is damaged")
        dict.__setitem__(u, "canon", canon)
        dict.__setitem__(u, "offsets", _Offsets(np.array([(t[1], t[2]) for t in toks], dtype=np.int32).reshape(-1, 2)))

    def shingle_text(self, pos, k):
        return " ".join(self.vocab[int(t)] for t in self.tok[pos:pos + k])


# ---------------------------------------------------------------- the engine

def shingle_hashes(tok, k, block=50_000_000):
    """A 64-bit polynomial hash of every k-token window (positions 0 .. n-k), computed blockwise."""
    n = tok.shape[0]
    m = n - k + 1
    h = np.zeros(max(m, 0), dtype=np.uint64)
    for s in range(0, m, block):
        e = min(m, s + block)
        hb = np.zeros(e - s, dtype=np.uint64)
        for j in range(k):
            hb *= HASH_BASE
            hb += tok[s + j:e + j].astype(np.uint64)
            hb += np.uint64(1)
        h[s:e] = hb
    return h


def _valid_positions(bounds, k, m):
    """Mask over positions 0 .. m-1: a shingle must lie inside one story."""
    valid = np.ones(m, dtype=bool)
    if k > 1 and bounds.size > 1:
        ends = bounds[1:]
        bad = (ends[:, None] - np.arange(1, k)[None, :]).ravel()
        lo = np.repeat(bounds[:-1], k - 1)
        bad = bad[(bad >= lo) & (bad < m) & (bad >= 0)]
        valid[bad] = False
    return valid


def _shared_positions(corpus, k):
    """Positions of every shingle that occurs at least twice (by hash; verified by tokens later), sorted by hash."""
    tok, bounds = corpus.tok, corpus.bounds
    m = tok.shape[0] - k + 1
    if m <= 0:
        return np.zeros(0, np.int64)
    h = shingle_hashes(tok, k)
    valid = _valid_positions(bounds, k, m)
    P = np.flatnonzero(valid)
    H = h[P]
    del h, valid
    order = np.argsort(H)
    H, P = H[order], P[order]
    del order
    same_as_prev = np.r_[False, H[1:] == H[:-1]]
    same_as_next = np.r_[H[:-1] == H[1:], False]
    keep = same_as_prev | same_as_next
    return P[keep]


def _group_by_shingle(corpus, P, k):
    """Sort the shared positions by their actual k tokens, then story, then position; return (P, S, starts, sizes,
    n_stories) over the groups of identical shingles."""
    tok, bounds = corpus.tok, corpus.bounds
    if P.size == 0:
        z = np.zeros(0, np.int64)
        return z, z, z, z, z
    S = np.searchsorted(bounds, P, side="right") - 1
    keys = [P, S] + [tok[P + j] for j in range(k - 1, -1, -1)]     # lexsort: the last key is the primary
    order = np.lexsort(keys)
    P, S = P[order], S[order]
    diff = np.zeros(P.size, dtype=bool)
    for j in range(k):
        t = tok[P + j]
        diff[1:] |= t[1:] != t[:-1]
    diff[0] = True
    starts = np.flatnonzero(diff)
    sizes = np.diff(np.r_[starts, P.size])
    new_story = diff | np.r_[True, S[1:] != S[:-1]]
    n_stories = np.add.reduceat(new_story.astype(np.int64), starts)
    return P, S, starts, sizes, n_stories


def _pairs_of_groups(P, S, starts, sizes, chunk=PAIR_CHUNK):
    """All pairs of positions inside each group (i < j), in chunks: yields (pi, pj, si, sj)."""
    npair = sizes * (sizes - 1) // 2
    csum = np.cumsum(npair)
    g0 = 0
    while g0 < starts.size:
        base = csum[g0 - 1] if g0 else 0
        g1 = int(np.searchsorted(csum, base + chunk, side="right"))
        g1 = max(g1, g0 + 1)
        np_ = npair[g0:g1]
        tot = int(np_.sum())
        if tot:
            grp = np.repeat(np.arange(g0, g1), np_)
            first = np.repeat(np.cumsum(np_) - np_, np_)
            t = np.arange(tot, dtype=np.int64) - first
            j = ((1 + np.sqrt(1 + 8 * t.astype(np.float64))) / 2).astype(np.int64)
            j = np.where(j * (j - 1) // 2 > t, j - 1, j)
            j = np.where((j + 1) * j // 2 <= t, j + 1, j)
            i = t - j * (j - 1) // 2
            gi = starts[grp] + i
            gj = starts[grp] + j
            yield P[gi], P[gj], S[gi], S[gj]
        g0 = g1


def _extend(tok, a, b, lim_a, lim_b, direction, chunk=EXT_CHUNK, batch=EXT_BATCH):
    """Move the span ends outward while the tokens stay identical. direction +1: a, b are exclusive ends, lim_* the
    stories' exclusive ends; direction -1: a, b are inclusive starts, lim_* the stories' starts. Returns new a, b."""
    a, b = a.astype(np.int64), b.astype(np.int64)
    n = tok.shape[0]
    ar = np.arange(chunk, dtype=np.int64)
    for s in range(0, a.size, batch):
        active = np.arange(s, min(a.size, s + batch))
        while active.size:
            ra, rb = a[active], b[active]
            if direction > 0:
                lim = np.minimum(lim_a[active] - ra, lim_b[active] - rb)
                ia = ra[:, None] + ar[None, :]
                ib = rb[:, None] + ar[None, :]
            else:
                lim = np.minimum(ra - lim_a[active], rb - lim_b[active])
                ia = ra[:, None] - 1 - ar[None, :]
                ib = rb[:, None] - 1 - ar[None, :]
            lim = np.minimum(lim, chunk)
            ia = np.clip(ia, 0, n - 1)
            ib = np.clip(ib, 0, n - 1)
            eq = (tok[ia] == tok[ib]) & (ar[None, :] < lim[:, None])
            lead = np.where(eq.all(axis=1), chunk, np.argmin(eq, axis=1))
            a[active] += direction * lead
            b[active] += direction * lead
            active = active[lead == chunk]
    return a, b


def _runs_to_matches(corpus, k, seeds):
    """Seeds (int32 n x 4: sa, sb, pa, pb; sa < sb, local positions) of complete story pairs -> maximal spans, one
    per span: the seeds of one diagonal (same pair, same offset) with gaps of at most k tokens are one run; each run
    is extended both ways; runs that reached the same span are one match. Returns (int32 n x 6: sa, sb, a0, a1, b0,
    b1; the number of runs)."""
    tok, bounds = corpus.tok, corpus.bounds
    sa, sb, pa, pb = (seeds[:, i].astype(np.int64) for i in range(4))
    d = pa - pb
    order = np.lexsort((pa, d, sb, sa))
    sa, sb, pa, pb, d = sa[order], sb[order], pa[order], pb[order], d[order]
    del order
    same = (sa[1:] == sa[:-1]) & (sb[1:] == sb[:-1]) & (d[1:] == d[:-1]) & (pa[1:] - pa[:-1] <= k)
    rs = np.flatnonzero(np.r_[True, ~same])
    re_ = np.r_[rs[1:], sa.size] - 1
    r_sa, r_sb = sa[rs], sb[rs]
    a0, b0 = pa[rs], pb[rs]
    a1, b1 = pa[re_] + k, pb[re_] + k
    n_runs = int(rs.size)
    del sa, sb, pa, pb, d, same, rs, re_
    ga1, gb1 = _extend(tok, bounds[r_sa] + a1, bounds[r_sb] + b1, bounds[r_sa + 1], bounds[r_sb + 1], +1)
    ga0, gb0 = _extend(tok, bounds[r_sa] + a0, bounds[r_sb] + b0, bounds[r_sa], bounds[r_sb], -1)
    a0 = ga0 - bounds[r_sa]; a1 = ga1 - bounds[r_sa]
    b0 = gb0 - bounds[r_sb]; b1 = gb1 - bounds[r_sb]
    order = np.lexsort((b0, a0, r_sb, r_sa))
    r_sa, r_sb, a0, a1, b0, b1 = r_sa[order], r_sb[order], a0[order], a1[order], b0[order], b1[order]
    dup = np.r_[False, (r_sa[1:] == r_sa[:-1]) & (r_sb[1:] == r_sb[:-1]) & (a0[1:] == a0[:-1]) & (b0[1:] == b0[:-1]) & (a1[1:] == a1[:-1])]
    keep = ~dup
    out = np.stack([r_sa[keep], r_sb[keep], a0[keep], a1[keep], b0[keep], b1[keep]], axis=1).astype(np.int32)
    return out, n_runs


def find_matches_arrays(corpus, k, max_df=MAX_DF, log=None, workdir=None, bucket_pairs=BUCKET_PAIRS):
    """All maximal exact shared passages of length >= k between stories, as the pilot's find_matches, from arrays.
    Returns (M, SI, skipped, diag): M (cross-issue) and SI (same-issue) as int32 arrays n x 6 (a, b, a0, a1, b0, b1;
    story indices a < b, token positions within the story), skipped as the pilot's list of commonplace shingles, diag
    the engine's counts. The candidate seeds are split by story pair into buckets (on disk under workdir when there are
    more than bucket_pairs of them) so that every story pair is finished in one piece and memory stays bounded."""
    import shutil
    import tempfile
    t0 = time.time()
    bounds = corpus.bounds
    if bounds[-1] >= 2 ** 31 - 1:
        raise RuntimeError("more than 2^31 tokens: the int32 positions overflow")
    P = _shared_positions(corpus, k)
    P, S, starts, sizes, n_st = _group_by_shingle(corpus, P, k)
    diag = {"positions_shared_by_hash": int(P.size), "shingle_groups": int(starts.size)}
    skipped = []
    for g in np.flatnonzero(n_st > max_df):
        skipped.append({"shingle": corpus.shingle_text(int(P[starts[g]]), k), "stories": int(n_st[g]), "occurrences": int(sizes[g])})
    skipped.sort(key=lambda d: (-d["stories"], -d["occurrences"], d["shingle"]))
    keep = (n_st >= 2) & (n_st <= max_df)
    starts, sizes = starts[keep], sizes[keep]
    total_pairs = int((sizes * (sizes - 1) // 2).sum())
    diag["seed_groups"] = int(starts.size)
    diag["candidate_pairs"] = total_pairs
    n_buckets = max(1, -(-total_pairs // bucket_pairs))
    diag["buckets"] = n_buckets
    if log:
        log(f"[r02c] k={k}: {P.size:,} shared positions, {diag['shingle_groups']:,} shingle groups, {starts.size:,} seed groups "
            f"({total_pairs:,} candidate pairs, {n_buckets} bucket{'s' if n_buckets > 1 else ''}), {len(skipped):,} commonplace; {time.time() - t0:.0f}s")
    tmp = None
    if n_buckets > 1:
        tmp = tempfile.mkdtemp(prefix=f"r02c_k{k}_", dir=workdir)
        handles = [open(os.path.join(tmp, f"b{i}.i32"), "wb") for i in range(n_buckets)]
    mem = []
    n_seed = 0
    for pi, pj, si, sj in _pairs_of_groups(P, S, starts, sizes):
        m = si != sj
        pi, pj, si, sj = pi[m], pj[m], si[m], sj[m]
        if not pi.size:
            continue
        sw = si > sj
        sa = np.where(sw, sj, si); sb = np.where(sw, si, sj)
        pa = np.where(sw, pj, pi) - bounds[sa]
        pb = np.where(sw, pi, pj) - bounds[sb]
        seeds = np.stack([sa, sb, pa, pb], axis=1).astype(np.int32)
        n_seed += int(seeds.shape[0])
        if tmp is None:
            mem.append(seeds)
        else:
            bk = (sa * 2654435761 % n_buckets).astype(np.int64)      # a story pair always lands in one bucket (by sa)
            order = np.argsort(bk, kind="stable")
            bk, seeds = bk[order], seeds[order]
            cuts = np.r_[0, np.flatnonzero(bk[1:] != bk[:-1]) + 1, bk.size]
            for c0, c1 in zip(cuts[:-1], cuts[1:]):
                handles[int(bk[c0])].write(seeds[c0:c1].tobytes())
    del P, S, starts, sizes
    diag["seeds"] = n_seed
    if tmp is not None:
        for h in handles:
            h.close()
    if log:
        log(f"[r02c] k={k}: {n_seed:,} seeds in {n_buckets} bucket{'s' if n_buckets > 1 else ''}; extending; {time.time() - t0:.0f}s")
    parts, n_runs = [], 0
    try:
        for i in range(n_buckets):
            if tmp is None:
                seeds = np.concatenate(mem) if mem else np.zeros((0, 4), np.int32)
                mem = []
            else:
                seeds = np.fromfile(os.path.join(tmp, f"b{i}.i32"), dtype=np.int32).reshape(-1, 4)
            if not seeds.shape[0]:
                continue
            out, nr = _runs_to_matches(corpus, k, seeds)
            parts.append(out)
            n_runs += nr
            del seeds
            if log and n_buckets > 1:
                log(f"[r02c] k={k}: bucket {i + 1}/{n_buckets}: {nr:,} runs -> {out.shape[0]:,} spans; {time.time() - t0:.0f}s")
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    diag["runs"] = n_runs
    A = np.concatenate(parts) if parts else np.zeros((0, 6), np.int32)
    del parts
    if A.shape[0]:
        order = np.lexsort((A[:, 4], A[:, 2], A[:, 1], A[:, 0]))
        A = A[order]
    si_mask = corpus.issue_of[A[:, 0]] == corpus.issue_of[A[:, 1]]
    M, SI = A[~si_mask], A[si_mask]
    diag["seconds"] = round(time.time() - t0, 2)
    if log:
        log(f"[r02c] k={k}: {M.shape[0]:,} cross-issue matches, {SI.shape[0]:,} same-issue; {diag['seconds']}s")
    return M, SI, skipped, diag


def _dicts(A):
    return [{"a": int(r[0]), "b": int(r[1]), "a0": int(r[2]), "a1": int(r[3]), "b0": int(r[4]), "b1": int(r[5]), "len": int(r[3] - r[2])} for r in A]


def find_matches(corpus, k, max_df=MAX_DF, log=None):
    """The pilot's interface: (matches, same_issue, skipped) as lists of dicts, plus diag. For the tests and small sets."""
    M, SI, skipped, diag = find_matches_arrays(corpus, k, max_df, log)
    return _dicts(M), _dicts(SI), skipped, diag


# ---------------------------------------------------------------- the report, at scale

def _union_by_group(grp_sorted, start, end):
    """For intervals [start, end) sorted by (group, start): the union length per group (groups in order of first
    appearance) and the group start indices."""
    if start.size == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    new = np.r_[True, grp_sorted[1:] != grp_sorted[:-1]]
    gidx = np.cumsum(new) - 1
    big = np.int64(1) << np.int64(40)
    reach = np.maximum.accumulate(end + gidx * big)
    prev = np.empty_like(reach)
    prev[0] = -1
    prev[1:] = reach[:-1]
    prev = np.where(new, -1, prev - gidx * big)                  # the farthest end before this interval, in its group
    contrib = np.maximum(0, end - np.maximum(start, prev))
    starts = np.flatnonzero(new)
    return np.add.reduceat(contrib, starts), starts


def pair_table(M, corpus):
    """The pilot's pair_stats for every story pair with a match (n_matches, max_len, cover_a, cover_b), as arrays
    sorted by max_len descending: a, b (story indices), n_matches, max_len, cover_a, cover_b."""
    if M.shape[0] == 0:
        return {"a": np.zeros(0, np.int32), "b": np.zeros(0, np.int32), "n_matches": np.zeros(0, np.int64),
                "max_len": np.zeros(0, np.int64), "cover_a": np.zeros(0), "cover_b": np.zeros(0)}
    n_tok = np.diff(corpus.bounds)
    a, b = M[:, 0].astype(np.int64), M[:, 1].astype(np.int64)
    key = a * corpus.n_stories + b
    order = np.lexsort((M[:, 2], key))
    ks = key[order]
    ua, starts = _union_by_group(ks, M[order, 2].astype(np.int64), M[order, 3].astype(np.int64))
    order_b = np.lexsort((M[:, 4], key))
    ub, _ = _union_by_group(key[order_b], M[order_b, 4].astype(np.int64), M[order_b, 5].astype(np.int64))
    pa_, pb_ = a[order][starts], b[order][starts]
    n_matches = np.diff(np.r_[starts, ks.size])
    lens = (M[order, 3] - M[order, 2]).astype(np.int64)
    max_len = np.maximum.reduceat(lens, starts)
    out = {"a": pa_.astype(np.int32), "b": pb_.astype(np.int32), "n_matches": n_matches, "max_len": max_len,
           "cover_a": ua / n_tok[pa_], "cover_b": ub / n_tok[pb_]}
    o = np.lexsort((out["b"], out["a"], -out["max_len"]))
    return {kk: v[o] for kk, v in out.items()}


def story_share_table(M, corpus):
    """The pilot's story_share: the share of each story's tokens inside some cross-issue match. {story_id: share}."""
    if M.shape[0] == 0:
        return {}
    n_tok = np.diff(corpus.bounds)
    s = np.concatenate([M[:, 0], M[:, 1]]).astype(np.int64)
    st = np.concatenate([M[:, 2], M[:, 4]]).astype(np.int64)
    en = np.concatenate([M[:, 3], M[:, 5]]).astype(np.int64)
    order = np.lexsort((st, s))
    u, starts = _union_by_group(s[order], st[order], en[order])
    ids = s[order][starts]
    return {corpus.units[int(i)]["story_id"]: round(float(x) / float(n_tok[i]), 4) for i, x in zip(ids, u)}


def _union_len(spans):
    """Length of the union of [start, end) intervals (the pilot's sets of positions, as intervals)."""
    spans.sort()
    total, c0, c1 = 0, None, None
    for s0, s1 in spans:
        if c1 is None or s0 > c1:
            if c1 is not None:
                total += c1 - c0
            c0, c1 = s0, s1
        elif s1 > c1:
            c1 = s1
    return total + ((c1 - c0) if c1 is not None else 0)


def pair_stats(matches, units):
    """The pilot's pair_stats on a list of match dicts, with intervals instead of sets (the tests compare it with
    pair_table and with the pilot's own)."""
    pairs = defaultdict(lambda: {"n": 0, "max_len": 0, "ia": [], "ib": []})
    for m in matches:
        p = pairs[(m["a"], m["b"])]
        p["n"] += 1
        p["max_len"] = max(p["max_len"], m["len"])
        p["ia"].append((m["a0"], m["a1"]))
        p["ib"].append((m["b0"], m["b1"]))
    out = [{"a": units[a]["story_id"], "b": units[b]["story_id"], "n_matches": p["n"], "max_len": p["max_len"],
            "cover_a": _union_len(p["ia"]) / units[a]["n_tokens"], "cover_b": _union_len(p["ib"]) / units[b]["n_tokens"]}
           for (a, b), p in pairs.items()]
    out.sort(key=lambda r: -r["max_len"])
    return out


def story_share(matches, units):
    covered = defaultdict(list)
    for m in matches:
        covered[m["a"]].append((m["a0"], m["a1"]))
        covered[m["b"]].append((m["b0"], m["b1"]))
    return {units[s]["story_id"]: round(_union_len(c) / units[s]["n_tokens"], 4) for s, c in covered.items()}


def cluster(matches, units):
    """The pilot's cluster(), with members' text cut to MEMBER_TEXT characters."""
    out = R.cluster(matches, units)
    for c in out:
        for m in c["members"]:
            m["text"] = m["text"][:MEMBER_TEXT]
    return out


def run(corpus, k, set_name="machine", outdir=OUTDIR, max_df=MAX_DF, write_floor=WRITE_FLOOR, log=print):
    """One pass for one k: the array engine, then the pilot's report in the pilot's file shapes under outdir.

    Every match of length >= k is counted (stats, length histogram, pair table, story shares) and kept in
    <set>_k<k>_matches.npy (int32 n x 6: a, b, a0, a1, b0, b1 as story indices into tokens/stories.jsonl and token
    positions; _sameissue.npy likewise). The files with text — _matches.jsonl, _sameissue.jsonl, _clusters.json — hold
    the matches of write_floor tokens or more (the pilot's length bins start a new bin at 10): at corpus scale the
    six-to-nine-word matches run to hundreds of millions and cannot be written out with excerpts or consolidated into
    clusters; the stats record the floor and how many matches lie under it. _pairs.json holds the PAIRS_JSON longest
    pairs, the whole pair table is _pairs.npz; _skipped_shingles.json the SKIPPED_JSON most widespread commonplace
    shingles, the whole list _skipped_shingles.tsv when there are more."""
    os.makedirs(outdir, exist_ok=True)
    units = corpus.units
    t0 = time.time()
    M, SI, skipped, diag = find_matches_arrays(corpus, k, max_df, log=log, workdir=outdir)
    t1 = time.time()
    pre = os.path.join(outdir, f"{set_name}_k{k}")
    np.save(pre + "_matches.npy", M)
    np.save(pre + "_sameissue.npy", SI)
    lens = (M[:, 3] - M[:, 2]).astype(np.int64)
    matches = _dicts(M[lens >= write_floor])
    same_issue = _dicts(SI[(SI[:, 3] - SI[:, 2]) >= write_floor])
    if log:
        log(f"[r02c] k={k}: {len(matches):,} matches of {write_floor}+ tokens go to the text files and the clusters; {time.time() - t0:.0f}s")
    clusters = cluster(matches, units)
    pairs = pair_table(M, corpus)
    shares = story_share_table(M, corpus)
    hist = {name: int(((lens >= lo) & (lens < hi)).sum()) for lo, hi, name in R.LEN_BINS}
    si_dup = int(sum(1 for r in SI if units[int(r[0])]["fragments"] & units[int(r[1])]["fragments"]))
    fam_with_reuse = {units[int(s)]["family"] for s in np.unique(M[:, :2])}
    stats = {
        "set": set_name, "k": k, "max_df": max_df, "collection": os.path.basename(outdir), "label": LABEL, "engine": "arrays",
        "stories": len(units), "tokens": int(corpus.bounds[-1]),
        "issues": len({u["issue"] for u in units}),
        "region_families": len({u["family"] for u in units}),
        "matches": int(M.shape[0]), "same_issue_matches": int(SI.shape[0]),
        "same_issue_from_shared_regions": si_dup,
        "skipped_commonplace_shingles": len(skipped),
        "clusters": len(clusters),
        "clusters_3plus_witnesses": sum(1 for c in clusters if c["witnesses"] >= 3),
        "clusters_3plus_witnesses_collapsed": sum(1 for c in clusters if c["witnesses_collapsed"] >= 3),
        "length_hist": hist,
        "longest_match": int(lens.max()) if lens.size else 0,
        "stories_with_reuse": len(shares),
        "stories_with_reuse_collapsed": len(fam_with_reuse),
        "pairs_with_reuse": int(pairs["a"].size),
        "write_floor_tokens": write_floor,
        "matches_written": len(matches), "same_issue_written": len(same_issue),
        "matches_under_floor": int(M.shape[0]) - len(matches),
        "engine_counts": diag,
        "seconds_index_and_extend": round(t1 - t0, 2),
        "seconds_total": round(time.time() - t0, 2),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }
    json.dump(R.region_overlap_report(units), open(os.path.join(outdir, f"{set_name}_region_overlap.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(pre + "_matches.jsonl", "w", encoding="utf-8") as f:
        for m in sorted(matches, key=lambda m: -m["len"]):
            f.write(json.dumps(R.serialize_match(m, units), ensure_ascii=False) + "\n")
    with open(pre + "_sameissue.jsonl", "w", encoding="utf-8") as f:
        for m in sorted(same_issue, key=lambda m: -m["len"]):
            f.write(json.dumps(R.serialize_match(m, units), ensure_ascii=False) + "\n")
    json.dump(clusters, open(pre + "_clusters.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    np.savez(pre + "_pairs.npz", **pairs)
    top = [{"a": units[int(pairs["a"][i])]["story_id"], "b": units[int(pairs["b"][i])]["story_id"], "n_matches": int(pairs["n_matches"][i]),
            "max_len": int(pairs["max_len"][i]), "cover_a": float(pairs["cover_a"][i]), "cover_b": float(pairs["cover_b"][i])}
           for i in range(min(PAIRS_JSON, pairs["a"].size))]
    json.dump(top, open(pre + "_pairs.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(shares, open(pre + "_story_share.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(skipped[:SKIPPED_JSON], open(pre + "_skipped_shingles.json", "w", encoding="utf-8"), ensure_ascii=False)
    if len(skipped) > SKIPPED_JSON:
        with open(pre + "_skipped_shingles.tsv", "w", encoding="utf-8") as f:
            f.write("stories\toccurrences\tshingle\n")
            for d in skipped:
                f.write(f"{d['stories']}\t{d['occurrences']}\t{d['shingle']}\n")
    stats["seconds_total"] = round(time.time() - t0, 2)
    json.dump(stats, open(pre + "_stats.json", "w", encoding="utf-8"), indent=1)
    return stats


# ---------------------------------------------------------------- tests

def _key_set(matches):
    return sorted((m["a"], m["b"], m["a0"], m["a1"], m["b0"], m["b1"]) for m in matches)


def compare_engines(units, ks=(6, 7, 8), max_df=MAX_DF, log=print, bucket_pairs=None):
    """Both engines on the same units: the match sets, the clusters, the pair statistics and the story shares must be
    identical. bucket_pairs forces the on-disk bucket path with small buckets. Returns True when all agree."""
    corpus = Corpus.from_units(units)
    ok = True
    for k in ks:
        m1, s1, sk1 = R.find_matches(units, k, max_df)
        if bucket_pairs:
            M, SI, sk2, diag = find_matches_arrays(corpus, k, max_df, bucket_pairs=bucket_pairs)
        else:
            M, SI, sk2, diag = find_matches_arrays(corpus, k, max_df)
        m2, s2 = _dicts(M), _dicts(SI)
        same = (_key_set(m1) == _key_set(m2) and _key_set(s1) == _key_set(s2)
                and sorted((d["shingle"], d["stories"], d["occurrences"]) for d in sk1)
                == sorted((d["shingle"], d["stories"], d["occurrences"]) for d in sk2))
        c1, c2 = R.cluster(m1, units), R.cluster(m2, units)

        def _csig(cs):
            return sorted((c["witnesses"], c["witnesses_collapsed"], c["issues"], c["occurrences"], c["max_len"],
                           tuple(sorted((m["story_id"], m["tok"][0], m["tok"][1]) for m in c["members"]))) for c in cs)
        csame = _csig(c1) == _csig(c2)

        def _psig(rows):
            return sorted((p["a"], p["b"], p["n_matches"], p["max_len"], round(p["cover_a"], 9), round(p["cover_b"], 9)) for p in rows)
        p1 = R.pair_stats(m1, units)
        pt = pair_table(M, corpus)
        p3 = [{"a": units[int(pt["a"][i])]["story_id"], "b": units[int(pt["b"][i])]["story_id"], "n_matches": int(pt["n_matches"][i]),
               "max_len": int(pt["max_len"][i]), "cover_a": float(pt["cover_a"][i]), "cover_b": float(pt["cover_b"][i])} for i in range(pt["a"].size)]
        psame = _psig(p1) == _psig(pair_stats(m2, units)) == _psig(p3)
        sh1 = R.story_share(m1, units)
        ssame = sh1 == story_share(m2, units) == story_share_table(M, corpus)
        good = same and csame and psame and ssame
        log(f"k={k}: pilot {len(m1)} cross-issue / {len(s1)} same-issue / {len(sk1)} commonplace; arrays {len(m2)} / {len(s2)} / {len(sk2)} "
            f"(seeds {diag['seeds']}, runs {diag['runs']}, buckets {diag['buckets']}); clusters {len(c1)} vs {len(c2)}; "
            f"pairs and shares {'same' if psame and ssame else 'DIFFER'} -> {'OK' if good else 'FAIL'}")
        ok = ok and good
    return ok


def _random_corpus(rng, n_stories=40, n_issues=12, vocab=600, planted=12, commonplace=3, max_df=MAX_DF):
    """Fabricated stories with planted shared passages: pairs in different and in the same issue, passages shared by
    three to six stories, repeats inside one story, overlapping plantings, and a few 'commonplace' shingles planted in
    more than max_df stories (which must be skipped). Fragments: a shared region for one same-issue pair."""
    words = [f"w{i}" for i in range(vocab)]
    texts = [[rng.choice(words) for _ in range(rng.randint(80, 700))] for _ in range(n_stories)]
    issues = [f"i{rng.randrange(n_issues)}" for _ in range(n_stories)]
    for p in range(planted):
        L = rng.randint(4, 60)
        passage = [f"p{p}_{i}" for i in range(L)]
        n_w = rng.choice([2, 2, 2, 3, 4, 6])
        for s in rng.sample(range(n_stories), n_w):
            pos = rng.randint(0, len(texts[s]))
            texts[s][pos:pos] = passage
            if rng.random() < 0.3:                                 # a repeat inside the same story
                pos2 = rng.randint(0, len(texts[s]))
                texts[s][pos2:pos2] = passage[: rng.randint(3, L)]
    for c in range(commonplace):
        passage = [f"c{c}_{i}" for i in range(rng.randint(6, 9))]
        for s in rng.sample(range(n_stories), min(n_stories, max_df + 1 + rng.randint(0, 3))):
            pos = rng.randint(0, len(texts[s]))
            texts[s][pos:pos] = passage
    frags = [[f"{s}:0"] for s in range(n_stories)]
    if n_stories >= 2:
        frags[1] = ["1:0", "9:9"]; frags[2] = ["9:9", "2:0"]; issues[2] = issues[1]
    return [{"story_id": f"s{i}", "issue": issues[i], "type": "story", "status": "auto", "text": " ".join(t), "fragments": frags[i]}
            for i, t in enumerate(texts)]


def selftest():
    import random
    ok = True
    print("the pilot's six-story corpus:")
    ok = compare_engines(R.build_units(R._fake_corpus())) and ok
    for seed in (1, 2, 3, 4, 5):
        rng = random.Random(seed)
        n = rng.choice([30, 60, 90])
        print(f"random corpus, seed {seed}, {n} stories, max_df {8 if seed % 2 else MAX_DF}:")
        recs = _random_corpus(rng, n_stories=n, max_df=8 if seed % 2 else MAX_DF)
        ok = compare_engines(R.build_units(recs), max_df=8 if seed % 2 else MAX_DF) and ok
    # a weak hash must not change the result (the grouping is by the tokens themselves)
    global HASH_BASE
    saved = HASH_BASE
    try:
        HASH_BASE = np.uint64(3)
        rng = random.Random(7)
        print("random corpus with a weak hash (many collisions):")
        ok = compare_engines(R.build_units(_random_corpus(rng, n_stories=50, vocab=40)), ks=(6,)) and ok
    finally:
        HASH_BASE = saved
    # the on-disk bucket path (the seeds split by story pair into small buckets) must give the same result
    rng = random.Random(8)
    print("random corpus through 1,000-seed disk buckets:")
    ok = compare_engines(R.build_units(_random_corpus(rng, n_stories=60)), ks=(6, 8), bucket_pairs=1000) and ok
    # the file path: tokenize an export directory in a temporary folder, load it, match, and compare with the units
    import tempfile
    rng = random.Random(11)
    recs = _random_corpus(rng, n_stories=45)
    with tempfile.TemporaryDirectory() as td:
        exp = os.path.join(td, "export")
        os.makedirs(exp)
        by_issue = defaultdict(list)
        for r in recs:
            by_issue[r["issue"]].append(r)
        for iid, rs in by_issue.items():
            with open(os.path.join(exp, f"{iid}.jsonl"), "w", encoding="utf-8") as f:
                for r in rs:
                    f.write(json.dumps(dict(r, magazine="M", cover_date="1930-01")) + "\n")
                f.write(json.dumps({"story_id": "ad", "issue": iid, "type": "ad", "text": "buy " * 100}) + "\n")
        out = os.path.join(td, "corpus_dryrun")
        meta = tokenize_corpus(exp, out, workers=3, log=lambda *a: None)
        corpus = Corpus.load(out)
        # the same stories in the same order as the pilot's loader gives them from the same files
        units = R.build_units([r for iid in sorted(by_issue) for r in by_issue[iid]])
        ref = Corpus.from_units(units)
        same_order = [u["story_id"] for u in units] == [u["story_id"] for u in corpus.units]
        same_tokens = corpus.bounds.tolist() == ref.bounds.tolist() and all(
            corpus.vocab[int(x)] == ref.vocab[int(y)] for x, y in zip(corpus.tok[:5000], ref.tok[:5000]))
        print(f"tokenize/load: {meta['stories']} stories, {meta['tokens']} tokens, {meta['files']} files; order {'same' if same_order else 'DIFFERS'}, "
              f"tokens {'same' if same_tokens else 'DIFFER'}")
        ok = ok and same_order and same_tokens
        st = run(corpus, 6, "machine", out, write_floor=10, log=lambda *a: None)
        m1, s1, _ = R.find_matches(units, 6)
        long1 = [m for m in m1 if m["len"] >= 10]
        files_ok = all(os.path.exists(os.path.join(out, f)) for f in ("machine_k6_stats.json", "machine_k6_clusters.json", "machine_k6_matches.jsonl",
                                                                       "machine_k6_sameissue.jsonl", "machine_k6_pairs.json", "machine_k6_story_share.json",
                                                                       "machine_k6_skipped_shingles.json", "machine_region_overlap.json"))
        lines = [json.loads(l) for l in open(os.path.join(out, "machine_k6_matches.jsonl"), encoding="utf-8")]
        excerpt_ok = all(l["excerpt"] and len(l["excerpt"]) <= 300 for l in lines)
        # the lazily read text gives the same excerpt as the in-memory units
        ser_ref = {(d["a"], d["b"], tuple(d["a_tok"])): d["excerpt"] for d in (R.serialize_match(m, units) for m in m1)}
        ser_ok = all(ser_ref.get((l["a"], l["b"], tuple(l["a_tok"]))) == l["excerpt"] for l in lines)
        cl_ref = R.cluster(long1, units)
        npy = np.load(os.path.join(out, "machine_k6_matches.npy"))
        pz = np.load(os.path.join(out, "machine_k6_pairs.npz"))
        good = (files_ok and st["matches"] == len(m1) and st["same_issue_matches"] == len(s1) and excerpt_ok and ser_ok and st["label"] == LABEL
                and len(lines) == len(long1) == st["matches_written"] and st["clusters"] == len(cl_ref) and npy.shape[0] == len(m1)
                and int(pz["a"].size) == st["pairs_with_reuse"] == len(R.pair_stats(m1, units))
                and sum(st["length_hist"].values()) == len(m1) and st["matches_under_floor"] == len(m1) - len(long1))
        print(f"run(): files {'written' if files_ok else 'MISSING'}; {st['matches']} matches ({len(m1)} by the pilot), {st['same_issue_matches']} same-issue "
              f"({len(s1)}); {st['matches_written']} of 10+ tokens written ({len(long1)}), {st['clusters']} clusters ({len(cl_ref)}); "
              f"excerpts from the snapshot {'match' if ser_ok else 'DIFFER'} -> {'OK' if good else 'FAIL'}")
        ok = ok and good
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


def pilot_check(stories_path, ks=(6, 7, 8), which="machine"):
    """Both engines on the pilot's stories (data/pilot_stories.jsonl): the numbers must be the pilot's."""
    units = R.build_units(R.load_stories(stories_path, which))
    print(f"[r02c] pilot check: {len(units)} stories, {sum(len(u['tokens']) for u in units):,} tokens")
    return compare_engines(units, ks)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", nargs="?", choices=["tokenize", "match"])
    ap.add_argument("--export", default=EXPORT_DIR)
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--set", default="machine", choices=["machine", "verified", "corrected"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--k", type=int, nargs="+", default=[6, 7, 8])
    ap.add_argument("--max-df", type=int, default=MAX_DF)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pilot-check", action="store_true")
    ap.add_argument("--stories", default=R.STORIES)
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    if args.pilot_check:
        sys.exit(0 if pilot_check(args.stories, tuple(args.k)) else 1)
    if args.step == "tokenize":
        tokenize_corpus(args.export, args.out, args.set, args.workers)
    elif args.step == "match":
        corpus = Corpus.load(args.out, args.export)
        print(f"[r02c] {corpus.n_stories} stories, {int(corpus.bounds[-1]):,} tokens, {len(set(corpus.issue_of.tolist()))} issues ({LABEL})")
        for k in args.k:
            st = run(corpus, k, args.set, args.out, args.max_df)
            print(f"[r02c] k={k}: {st['matches']:,} cross-issue matches, {st['same_issue_matches']:,} same-issue, {st['clusters']:,} clusters, "
                  f"longest {st['longest_match']} tokens, {st['skipped_commonplace_shingles']:,} commonplace shingles skipped, {st['seconds_total']}s")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
