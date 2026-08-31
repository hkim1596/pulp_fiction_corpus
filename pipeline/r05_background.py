#!/usr/bin/env python3
"""r05 — background confluence (protocol section 4.1) on the pilot set.

The unit is a PAIR of stories. For every pair this stage records how
much exact reuse (r02) and paraphrastic reuse (r04) was found, and the
facts about the pair the protocol conditions on: the later publication
date, the years between the two, topic similarity computed on the text
that remains after masking detected reuse, and whether the two share an
author, a magazine, a publisher, a genre, a format. Decisions of
2026-08-31 (Heejin):

  * At pilot scale every pair is computed (287 stories, 41,041 pairs).
    The stratified sampler the protocol requires at corpus scale is ALSO
    run, on this same table, to check that its weighted estimates
    reproduce the full-table numbers before it ever matters.
  * Topic similarity = TF-IDF cosine over words and word pairs on the
    reuse-masked text; the embedding cosine (mean passage vector, r04's
    model) is carried as a second column for comparison.
  * The two-part hierarchical model (any reuse? then how much?) is fit
    in a full first version — logistic part and count part, both with a
    story-level random effect that enters once for each member of the
    pair — as a concrete proposal for Dennis, who owns the statistical
    design. Nothing about the pilot fit is a finding.

Same-issue pairs stay in the table, flagged, and are excluded from the
background curves and the model by default, because the machine
assembly still double-owns regions inside an issue (see r02's region
families).

Run:  python3 pipeline/r05_background.py --set machine
Outputs: data/reuse/background/pairs_<set>.csv.gz, summary_<set>.json
"""
import argparse
import glob
import json
import math
import os
import re
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r02_verbatim as r2  # noqa: E402
import r04_paraphrase as r4  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REUSE = os.path.join(ROOT, "data", "reuse")
OUTDIR = os.path.join(REUSE, "background")
PUBLISHERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "publishers.json")
EXACT_KS = (6, 7, 8)
PARA_TAG = "machine_w50s25"
PARA_KS = (5, 10, 20)
PARA_MAIN = 10
YEAR_BANDS = [(0, 3, "0-2"), (3, 10, "3-9"), (10, 20, "10-19"), (20, 1000, "20+")]
HONORIFICS = {"captain", "capt", "dr", "prof", "professor", "mr", "mrs", "miss",
              "lieut", "lieutenant", "major", "col", "colonel", "sgt", "rev", "by"}


# ---------------------------------------------------------------- facts

def author_key(name):
    """Comparison key for a printed by-line, or None when unusable. Pulp
    pseudonyms are NOT resolved here (plan item 0.4); this is the
    verbatim-name state with an explicit unknown."""
    if not name:
        return None
    s = name.lower()
    if s.startswith("author of") or "the editor" in s:
        return None
    s = re.sub(r"[^a-z\s]", " ", s)
    parts = [p for p in s.split() if p not in HONORIFICS]
    if len(parts) < 2 or sum(len(p) for p in parts) < 5:
        return None
    return " ".join(parts)


def decimal_year(cover_date):
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", cover_date or "")
    if not m:
        return None
    y = int(m.group(1))
    mo = int(m.group(2) or 6)
    d = int(m.group(3) or 15)
    return round(y + (mo - 1) / 12 + (d - 1) / 365, 3)


def year_band(years):
    for lo, hi, name in YEAR_BANDS:
        if lo <= years < hi:
            return name
    return "20+"


def load_publishers():
    try:
        return json.load(open(PUBLISHERS, encoding="utf-8"))["issues"]
    except Exception:
        return {}


# ---------------------------------------------------------------- reuse inputs

def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_exact(set_name, k):
    pre = os.path.join(REUSE, f"{set_name}_k{k}")
    return load_jsonl(pre + "_matches.jsonl") + load_jsonl(pre + "_sameissue.jsonl")


def load_para(tag, k):
    pre = os.path.join(REUSE, "para", f"{tag}_k{k}")
    return load_jsonl(pre + "_alignments.jsonl") + load_jsonl(pre + "_sameissue.jsonl")


def per_pair_exact(matches):
    """{(a,b): {longest, n, cover_a, cover_b, excerpt}} token-interval unions."""
    acc = {}
    for m in matches:
        key = (m["a"], m["b"])
        d = acc.setdefault(key, {"longest": 0, "n": 0, "ia": set(), "ib": set(), "excerpt": ""})
        d["n"] += 1
        if m["len"] > d["longest"]:
            d["longest"] = m["len"]
            d["excerpt"] = m.get("excerpt", "")
        d["ia"].update(range(m["a_tok"][0], m["a_tok"][1]))
        d["ib"].update(range(m["b_tok"][0], m["b_tok"][1]))
    return acc


def per_pair_para(aligns):
    acc = {}
    for a in aligns:
        key = (a["a"], a["b"])
        d = acc.setdefault(key, {"longest": 0, "n": 0, "best_identity": 0.0,
                                 "ia": set(), "ib": set(), "excerpt": ""})
        d["n"] += 1
        if a["cols"] > d["longest"]:
            d["longest"] = a["cols"]
            d["excerpt"] = a.get("text_a", "")[:300]
        d["best_identity"] = max(d["best_identity"], a["identity"])
        d["ia"].update(range(a["a_tok"][0], a["a_tok"][1]))
        d["ib"].update(range(a["b_tok"][0], a["b_tok"][1]))
    return acc


# ---------------------------------------------------------------- topic

def reuse_masks(units, exact_pp, para_pp, idx):
    """Per story: set of token positions inside any CROSS-ISSUE detected
    reuse (exact seed 6 and paraphrase main run)."""
    masks = defaultdict(set)
    for pp in (exact_pp, para_pp):
        for (a, b), d in pp.items():
            if units[idx[a]]["issue"] == units[idx[b]]["issue"]:
                continue
            masks[a].update(d["ia"])
            masks[b].update(d["ib"])
    return masks


def tfidf_topic(units, masks):
    from sklearn.feature_extraction.text import TfidfVectorizer

    def grams(toks):
        return toks + [f"{x} {y}" for x, y in zip(toks, toks[1:])]
    docs = []
    for u in units:
        m = masks.get(u["story_id"], set())
        toks = [t for i, t in enumerate(u["tokens"]) if i not in m]
        docs.append(toks)
    vec = TfidfVectorizer(analyzer=grams, min_df=2, max_df=0.9, sublinear_tf=True)
    X = vec.fit_transform(docs)
    sims = (X @ X.T).toarray()
    return sims, X.shape[1]


def embedding_topic(units, masks, set_name, window=r4.WINDOW, stride=r4.STRIDE):
    """Cosine between mean passage vectors per story, passages that are
    mostly inside masked reuse excluded. Uses r04's cached embeddings."""
    files = glob.glob(os.path.join(REUSE, "para", f"emb_{set_name}_w{window}s{stride}_*.npz"))
    if not files:
        return None
    z = np.load(max(files, key=os.path.getmtime))
    vec = z["vec"]
    passages = r4.make_passages(units, window, stride)
    if len(passages) != vec.shape[0]:
        return None
    dim = vec.shape[1]
    sums = np.zeros((len(units), dim), dtype=np.float64)
    counts = np.zeros(len(units))
    for row, (si, s, e) in enumerate(passages):
        m = masks.get(units[si]["story_id"], set())
        inside = sum(1 for t in range(s, e) if t in m) if m else 0
        if inside * 2 > (e - s):
            continue
        sums[si] += vec[row]
        counts[si] += 1
    mean = sums / np.maximum(counts, 1)[:, None]
    norms = np.linalg.norm(mean, axis=1, keepdims=True)
    mean = mean / np.maximum(norms, 1e-9)
    sims = mean @ mean.T
    sims[counts == 0, :] = np.nan
    sims[:, counts == 0] = np.nan
    return sims


# ---------------------------------------------------------------- pair table

def build_pair_table(units, set_name, log=print):
    idx = {u["story_id"]: i for i, u in enumerate(units)}
    pubs = load_publishers()
    exact = {k: per_pair_exact(load_exact(set_name, k)) for k in EXACT_KS}
    para = {k: per_pair_para(load_para(PARA_TAG, k)) for k in PARA_KS}
    masks = reuse_masks(units, exact[EXACT_KS[0]], para[PARA_MAIN], idx)
    t0 = time.time()
    topic, n_features = tfidf_topic(units, masks)
    log(f"[r05] topic similarity: TF-IDF over {n_features} word and word-pair "
        f"features on masked text, {time.time() - t0:.1f}s")
    emb = embedding_topic(units, masks, set_name)
    log(f"[r05] embedding topic column: {'yes' if emb is not None else 'no cache found'}")
    rows = []
    n = len(units)
    for i in range(n):
        ua = units[i]
        for j in range(i + 1, n):
            ub = units[j]
            ma, mb = ua["meta"], ub["meta"]
            ya, yb = decimal_year(ma["cover_date"]), decimal_year(mb["cover_date"])
            later, earlier = max(ya, yb), min(ya, yb)
            apart = round(later - earlier, 3)
            ka, kb = author_key(ma.get("author")), author_key(mb.get("author"))
            pa = pubs.get(ua["issue"], {}).get("publisher_group")
            pb = pubs.get(ub["issue"], {}).get("publisher_group")
            row = {
                "a": ua["story_id"], "b": ub["story_id"],
                "issue_a": ua["issue"], "issue_b": ub["issue"],
                "same_issue": int(ua["issue"] == ub["issue"]),
                "shared_regions": len(ua["fragments"] & ub["fragments"]),
                "same_family": int(ua["family"] == ub["family"]),
                "tokens_a": len(ua["tokens"]), "tokens_b": len(ub["tokens"]),
                "magazine_a": ma["magazine"], "magazine_b": mb["magazine"],
                "same_magazine": int(ma["magazine"] == mb["magazine"]),
                "publisher_a": pa, "publisher_b": pb,
                "same_publisher": int(bool(pa) and pa == pb),
                "same_genre": int(ma["genre"] == mb["genre"]),
                "same_format": int(ma["format"] == mb["format"]),
                "author_a": ma.get("author"), "author_b": mb.get("author"),
                "author_known": int(bool(ka and kb)),
                "same_author": int(bool(ka and kb and ka == kb)),
                "year_a": ya, "year_b": yb, "later_year": later, "earlier_year": earlier,
                "years_apart": apart, "years_band": year_band(apart),
                "later_decade": int(later // 10 * 10),
                "topic_tfidf": round(float(topic[i, j]), 5),
                "topic_emb": (None if emb is None or np.isnan(emb[i, j])
                              else round(float(emb[i, j]), 5)),
            }
            key = (ua["story_id"], ub["story_id"])
            for k in EXACT_KS:
                d = exact[k].get(key)
                row[f"exact_k{k}_longest"] = d["longest"] if d else 0
                row[f"exact_k{k}_n"] = d["n"] if d else 0
                row[f"exact_k{k}_cover_a"] = len(d["ia"]) if d else 0
                row[f"exact_k{k}_cover_b"] = len(d["ib"]) if d else 0
                row[f"exact_k{k}_share_max"] = round(max(
                    len(d["ia"]) / len(ua["tokens"]), len(d["ib"]) / len(ub["tokens"])), 5) if d else 0.0
                if k == EXACT_KS[0]:
                    row["exact_excerpt"] = d["excerpt"] if d else ""
            for k in PARA_KS:
                d = para[k].get(key)
                row[f"para_k{k}_longest"] = d["longest"] if d else 0
                row[f"para_k{k}_n"] = d["n"] if d else 0
                row[f"para_k{k}_best_identity"] = round(d["best_identity"], 4) if d else 0.0
                row[f"para_k{k}_cover_a"] = len(d["ia"]) if d else 0
                row[f"para_k{k}_cover_b"] = len(d["ib"]) if d else 0
                row[f"para_k{k}_share_max"] = round(max(
                    len(d["ia"]) / len(ua["tokens"]), len(d["ib"]) / len(ub["tokens"])), 5) if d else 0.0
                if k == PARA_MAIN:
                    row["para_excerpt"] = d["excerpt"] if d else ""
            rows.append(row)
    df = pd.DataFrame(rows)
    cross = df["same_issue"] == 0
    qs = df.loc[cross, "topic_tfidf"].quantile([0.25, 0.5, 0.75]).tolist()
    df["topic_q"] = 1 + (df["topic_tfidf"].values[:, None] > np.array(qs)[None, :]).sum(axis=1)
    df.attrs["topic_quartile_cuts"] = qs
    log(f"[r05] pair table: {len(df)} pairs, {int(cross.sum())} cross-issue, "
        f"{time.time() - t0:.1f}s")
    return df


# ---------------------------------------------------------------- background

def survival(values, grid):
    v = np.asarray(values)
    n = len(v)
    return {int(L): (round(float((v >= L).mean()), 6) if n else None) for L in grid}


def background_curves(df):
    bg = df[df["same_issue"] == 0]
    out = {"n_pairs": int(len(bg)), "exact": {}, "paraphrase": {}}
    for k in EXACT_KS:
        col = f"exact_k{k}_longest"
        grid = list(range(k, 41))
        cur = {"overall": survival(bg[col], grid), "by_topic_q": {}, "by_years_band": {},
               "by_later_decade": {}, "by_same_author": {}}
        for q, g in bg.groupby("topic_q"):
            cur["by_topic_q"][int(q)] = survival(g[col], grid)
        for b, g in bg.groupby("years_band"):
            cur["by_years_band"][b] = survival(g[col], grid)
        for d, g in bg.groupby("later_decade"):
            cur["by_later_decade"][int(d)] = survival(g[col], grid)
        for s, g in bg[bg["author_known"] == 1].groupby("same_author"):
            cur["by_same_author"][int(s)] = {"n": int(len(g)), **survival(g[col], grid)}
        cur["p_any"] = round(float((bg[col] >= k).mean()), 6)
        out["exact"][f"k{k}"] = cur
    for k in PARA_KS:
        col = f"para_k{k}_longest"
        grid = list(range(20, 301, 10))
        cur = {"overall": survival(bg[col], grid), "by_topic_q": {}, "by_years_band": {}}
        for q, g in bg.groupby("topic_q"):
            cur["by_topic_q"][int(q)] = survival(g[col], grid)
        for b, g in bg.groupby("years_band"):
            cur["by_years_band"][b] = survival(g[col], grid)
        cur["p_any"] = round(float((bg[col] >= 20).mean()), 6)
        ident = bg.loc[bg[col] >= 20, f"para_k{k}_best_identity"]
        cur["identity_hist"] = {f"{lo/100:.2f}-{(lo+10)/100:.2f}": int(((ident >= lo / 100) & (ident < (lo + 10) / 100)).sum())
                                for lo in range(60, 100, 10)}
        cur["identity_hist"]["1.00"] = int((ident >= 1.0).sum())
        out["paraphrase"][f"k{k}"] = cur
    # the two historical variables, tabulated
    out["time_table"] = {}
    for name, col, thr in (("exact_k6", "exact_k6_longest", 6), ("exact_k8", "exact_k8_longest", 8),
                           ("para_k10", "para_k10_longest", 20)):
        t = {}
        for (dec, band), g in bg.groupby(["later_decade", "years_band"]):
            t[f"{int(dec)}s|{band}"] = {"n": int(len(g)), "p_any": round(float((g[col] >= thr).mean()), 5),
                                        "mean_longest_given_any": (round(float(g.loc[g[col] >= thr, col].mean()), 2)
                                                                   if (g[col] >= thr).any() else None)}
        out["time_table"][name] = t
    return out


def surprise_table(df, top=25):
    """For every matched cross-issue pair: how often comparable pairs
    (same topic quartile and years band, the pair itself left out) share
    a passage at least as long. Small = unusual."""
    bg = df[df["same_issue"] == 0]
    out = {}
    for name, col, thr in (("exact_k6", "exact_k6_longest", 6), ("para_k10", "para_k10_longest", 20)):
        rows = []
        for (q, band), g in bg.groupby(["topic_q", "years_band"]):
            vals = g[col].values
            for _, r in g[g[col] >= thr].iterrows():
                others = vals[vals != -1]
                p = (float(((others >= r[col]).sum() - 1) / max(1, len(others) - 1)))
                rows.append({"a": r["a"], "b": r["b"], "longest": int(r[col]),
                             "topic_q": int(q), "years_band": band, "stratum_n": int(len(g)),
                             "p_at_least": round(p, 6),
                             "same_author": int(r["same_author"]),
                             "excerpt": r["exact_excerpt" if name.startswith("exact") else "para_excerpt"][:200]})
        rows.sort(key=lambda r: (r["p_at_least"], -r["longest"]))
        out[name] = {"n_matched_pairs": len(rows), "most_unusual": rows[:top]}
    return out


# ---------------------------------------------------------------- sampler

def stratified_sample(bg, matched_col, n_per_stratum, rng):
    """Keep every matched pair; sample non-matched pairs per stratum
    (later decade x years band x topic quartile). Returns the sample with
    a 'weight' column = 1 / inclusion probability."""
    strata = ["later_decade", "years_band", "topic_q"]
    matched = bg[bg[matched_col]]
    non = bg[~bg[matched_col]]
    parts = [matched.assign(weight=1.0, p_incl=1.0)]
    for _, g in non.groupby(strata):
        take = min(len(g), n_per_stratum)
        p = take / len(g)
        s = g.sample(n=take, random_state=int(rng.integers(0, 2**31 - 1)))
        parts.append(s.assign(weight=1.0 / p, p_incl=p))
    return pd.concat(parts, ignore_index=True)


def sampler_check(df, n_per_stratum=40, seeds=20):
    """Does the weighted sample reproduce the full table? Reported per
    target quantity: full-table value, mean and worst weighted-sample
    error over `seeds` draws, and the naive (unweighted) error."""
    bg = df[df["same_issue"] == 0].copy()
    out = {"n_per_stratum": n_per_stratum, "seeds": seeds, "checks": {}}
    for name, longest_col, thr in (("exact_k6", "exact_k6_longest", 6),
                                   ("exact_k8", "exact_k8_longest", 8),
                                   ("para_k10", "para_k10_longest", 20)):
        bg["_m"] = bg[longest_col] >= thr
        targets = {
            "p_any": lambda d, w: float(np.average(d["_m"], weights=w)),
            "p_any_topic_q4": lambda d, w: float(np.average(d["_m"][d["topic_q"] == 4], weights=w[d["topic_q"] == 4]))
            if (d["topic_q"] == 4).any() else float("nan"),
            "mean_topic_nonmatched": lambda d, w: float(np.average(d["topic_tfidf"][~d["_m"]], weights=w[~d["_m"]]))
            if (~d["_m"]).any() else float("nan"),
            "p_longest_ge_thr_plus2": lambda d, w: float(np.average(d[longest_col] >= thr + 2, weights=w)),
        }
        full = {t: f(bg, np.ones(len(bg))) for t, f in targets.items()}
        errs = {t: [] for t in targets}
        naive = {t: [] for t in targets}
        sizes = []
        for s in range(seeds):
            rng = np.random.default_rng(1000 + s)
            smp = stratified_sample(bg, "_m", n_per_stratum, rng)
            sizes.append(len(smp))
            for t, f in targets.items():
                errs[t].append(f(smp, smp["weight"].values) - full[t])
                naive[t].append(f(smp, np.ones(len(smp))) - full[t])
        out["checks"][name] = {
            "matched_pairs": int(bg["_m"].sum()), "sample_size_mean": float(np.mean(sizes)),
            "full_table_pairs": int(len(bg)),
            "targets": {t: {"full": round(full[t], 6),
                            "weighted_mean_abs_err": round(float(np.nanmean(np.abs(errs[t]))), 6),
                            "weighted_max_abs_err": round(float(np.nanmax(np.abs(errs[t]))), 6),
                            "naive_mean_err": round(float(np.nanmean(naive[t])), 6)}
                        for t in targets}}
    return out


# ---------------------------------------------------------------- model

def _z(x):
    x = np.asarray(x, dtype=float)
    sd = x.std()
    return (x - x.mean()) / (sd if sd > 0 else 1.0)


def fit_two_part(df, units, response_prefix, thr, log=print):
    """Two-part hierarchical model on cross-issue pairs.
    Part 1: any reuse (longest >= thr) ~ fixed effects + story effects.
    Part 2: extent given reuse: covered tokens beyond the threshold, Poisson,
            same fixed effects + story effects.
    Story effects: one random intercept per story, entering once for each
    member of the pair (design matrix with two ones per row). Fit by the
    variational Bayes routine in statsmodels (BayesMixedGLM); a first
    version for Dennis to replace or refine."""
    from scipy import sparse
    from statsmodels.genmod.bayes_mixed_glm import (BinomialBayesMixedGLM,
                                                    PoissonBayesMixedGLM)
    bg = df[df["same_issue"] == 0].reset_index(drop=True)
    longest = bg[f"{response_prefix}_longest"].values
    any_ = (longest >= thr).astype(float)
    names = ["intercept", "topic_tfidf_z", "log_years_apart_z", "later_year_z",
             "same_author", "author_unknown", "same_magazine", "same_publisher", "same_genre"]
    X = np.column_stack([
        np.ones(len(bg)), _z(bg["topic_tfidf"]), _z(np.log1p(bg["years_apart"])),
        _z(bg["later_year"]), bg["same_author"].values, 1 - bg["author_known"].values,
        bg["same_magazine"].values, bg["same_publisher"].values, bg["same_genre"].values])
    sid = {u["story_id"]: i for i, u in enumerate(units)}
    ia = bg["a"].map(sid).values
    ib = bg["b"].map(sid).values
    rows = np.concatenate([np.arange(len(bg)), np.arange(len(bg))])
    cols = np.concatenate([ia, ib])
    Z = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(bg), len(units)))
    ident = np.zeros(len(units), dtype=int)
    out = {"response": response_prefix, "threshold": thr, "n_pairs": int(len(bg)),
           "n_any": int(any_.sum()), "fixed_effect_names": names,
           "note": ("First full version. Story random intercept shared by both members of a pair. "
                    "Variational Bayes fit (statsmodels BayesMixedGLM). Pilot-scale numbers are a "
                    "machinery check, not findings.")}
    t0 = time.time()
    try:
        m1 = BinomialBayesMixedGLM(any_, X, Z, ident, vcp_p=0.5, fe_p=2.0)
        r1 = m1.fit_vb()
        out["part1_any"] = {
            "fixed": {n: {"mean": round(float(mu), 4), "sd": round(float(sd), 4)}
                      for n, mu, sd in zip(names, r1.fe_mean, r1.fe_sd)},
            "story_effect_sd": round(float(np.exp(r1.vcp_mean[0])), 4),
            "seconds": round(time.time() - t0, 1)}
        re_means = np.asarray(r1.vc_mean)
        top = np.argsort(-np.abs(re_means))[:10]
        out["part1_any"]["largest_story_effects"] = [
            {"story_id": units[i]["story_id"], "effect": round(float(re_means[i]), 3),
             "title": units[i]["meta"].get("title")} for i in top]
    except Exception as e:  # keep the pipeline alive; report the failure
        out["part1_any"] = {"error": repr(e)}
    sel = any_ > 0
    if sel.sum() >= 30:
        cover = np.maximum(bg[f"{response_prefix}_cover_a"].values,
                           bg[f"{response_prefix}_cover_b"].values)[sel]
        y = np.maximum(cover - thr, 0).astype(float)
        t0 = time.time()
        try:
            m2 = PoissonBayesMixedGLM(y, X[sel], Z[sel], ident, vcp_p=0.5, fe_p=2.0)
            r2_ = m2.fit_vb()
            out["part2_extent"] = {
                "response": f"max covered tokens - {thr}, Poisson (overdispersion to be addressed)",
                "n": int(sel.sum()),
                "fixed": {n: {"mean": round(float(mu), 4), "sd": round(float(sd), 4)}
                          for n, mu, sd in zip(names, r2_.fe_mean, r2_.fe_sd)},
                "story_effect_sd": round(float(np.exp(r2_.vcp_mean[0])), 4),
                "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            out["part2_extent"] = {"error": repr(e)}
    else:
        out["part2_extent"] = {"skipped": f"only {int(sel.sum())} pairs with reuse (< 30)"}
    log(f"[r05] model {response_prefix} (thr {thr}): {out['n_any']} of {out['n_pairs']} pairs with reuse; "
        f"part1 {'ok' if 'fixed' in out['part1_any'] else 'failed'}, "
        f"part2 {'ok' if 'fixed' in out['part2_extent'] else out['part2_extent']}")
    return out


# ---------------------------------------------------------------- run

def run(set_name="machine", outdir=OUTDIR, log=print):
    os.makedirs(outdir, exist_ok=True)
    units = r2.build_units(r2.load_stories(r2.STORIES, set_name))
    df = build_pair_table(units, set_name, log)
    df.to_csv(os.path.join(outdir, f"pairs_{set_name}.csv.gz"), index=False, compression="gzip")
    summary = {
        "set": set_name, "stories": len(units), "pairs": int(len(df)),
        "cross_issue_pairs": int((df["same_issue"] == 0).sum()),
        "same_issue_pairs": int((df["same_issue"] == 1).sum()),
        "pairs_sharing_regions": int((df["shared_regions"] > 0).sum()),
        "topic_quartile_cuts": [round(x, 5) for x in df.attrs["topic_quartile_cuts"]],
        "author_known_pairs": int(df["author_known"].sum()),
        "same_author_pairs": int(df["same_author"].sum()),
        "same_author_cross_issue_pairs": int(((df["same_author"] == 1) & (df["same_issue"] == 0)).sum()),
        "columns": list(df.columns),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }
    cross = df[df["same_issue"] == 0]
    if df["topic_emb"].notna().any():
        c = cross[["topic_tfidf", "topic_emb"]].dropna().corr().iloc[0, 1]
        summary["topic_tfidf_vs_embedding_corr"] = round(float(c), 4)
    summary["background"] = background_curves(df)
    summary["unusual"] = surprise_table(df)
    summary["sampler_check"] = sampler_check(df)
    summary["models"] = {
        "exact_k6": fit_two_part(df, units, "exact_k6", 6, log),
        "exact_k7": fit_two_part(df, units, "exact_k7", 7, log),
        "para_k10": fit_two_part(df, units, "para_k10", 20, log),
    }
    json.dump(summary, open(os.path.join(outdir, f"summary_{set_name}.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"[r05] wrote pairs_{set_name}.csv.gz and summary_{set_name}.json")
    return df, summary


def selftest():
    ok = True
    ok &= author_key("Captain S. P. Meek") == author_key("Capt. S. P. Meek") == "s p meek"
    ok &= author_key("By CARL JACOBI") == "carl jacobi"
    ok &= author_key('Author of "Black Medicine"') is None
    ok &= author_key("THE EDITOR") is None
    ok &= author_key("w. t.") is None
    ok &= abs(decimal_year("1930-01") - 1930.0) < 0.05
    ok &= abs(decimal_year("1937-02-20") - 1937.135) < 0.01
    ok &= year_band(2.9) == "0-2" and year_band(10) == "10-19" and year_band(25) == "20+"
    # sampler: weights reproduce a known proportion exactly in expectation
    rng = np.random.default_rng(0)
    n = 4000
    fake = pd.DataFrame({
        "same_issue": 0,
        "later_decade": rng.choice([1920, 1930, 1950], n),
        "years_band": rng.choice(["0-2", "3-9", "20+"], n),
        "topic_q": rng.choice([1, 2, 3, 4], n),
        "topic_tfidf": rng.random(n),
    })
    fake["_m"] = rng.random(n) < 0.03
    p_full = fake["_m"].mean()
    ests = []
    for s in range(30):
        smp = stratified_sample(fake, "_m", 15, np.random.default_rng(s))
        ests.append(np.average(smp["_m"], weights=smp["weight"]))
    ok &= abs(np.mean(ests) - p_full) < 0.002 and all(abs(e - p_full) < 1e-9 for e in ests)
    print(f"author keys, dates, bands, sampler (full {p_full:.4f}, weighted {np.mean(ests):.4f}) -> "
          f"{'OK' if ok else 'FAIL'}")
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return bool(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="machine", choices=["machine", "verified", "corrected"])
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    run(args.set, args.out)


if __name__ == "__main__":
    main()
