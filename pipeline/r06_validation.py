#!/usr/bin/env python3
"""r06 — the hand-reviewed validation set for the paraphrase detector
(protocol section 3.2, "Parameter selection and validation": "we first
test it on a separate set of passages reviewed by hand. This allows us
to compare a small, prespecified range of settings and select those
that best recover known paraphrases without needlessly expanding the
candidate pool. The selected settings are then fixed and applied to the
full corpus.")

What it does. The paraphrase stage (r04) retrieves candidate passage
pairs — each passage's K nearest neighbours by embedding, plus every
exact match — and keeps an alignment when it is long and identical
enough (20 columns, identity 0.60 by default). Whether those settings
are right is a question only readers can answer, so this stage draws a
REVIEW SET of candidate pairs that spans the whole range the detector
sees: pairs the default rule keeps, pairs only a looser rule would keep,
pairs below every rule, and pairs the embedding search brought close
although the words share little — the hard negatives. Two readers judge
each pair on the site (/reuse/validate: paraphrase or copy / not / unsure,
with a note); every judgment is one line of an append-only log with the
reader's name and the time. The calibration then scores every setting in
a fixed grid (K 5/10/20 × six keep rules) against the judgments:
precision (kept pairs that readers called paraphrase), recall (paraphrase
pairs that the setting keeps), and the size of the pool it keeps. The
chosen setting is the one with the highest recall among those whose
precision is at least the floor (0.90); ties go to the smaller pool.

Strata. Candidates are stratified by source (embedding-only, or seeded by
an exact match) and by the alignment score band of the candidate region —
below any rule (score < 9), loose rule only (9–15), default rule (16–29),
strong (30 and more) — and, in the two lowest bands, by the embedding
cosine (closest 5%, next 20%, the rest). A fixed number of candidates is
drawn from every stratum with a fixed random seed, and each item carries
the inverse of its inclusion probability as its weight, so the precision
and recall of a setting can be estimated for the whole candidate pool,
not just for the reviewed set.

Run:
  python3 pipeline/r06_validation.py --build [--set machine] [--per-stratum 15]
  python3 pipeline/r06_validation.py --calibrate        (from the site's judgments)
  python3 pipeline/r06_validation.py --selftest
Outputs under data/reuse/validation/: review_set.jsonl, review_set_stats.json,
judgments.jsonl (written by the site), calibration.json.
"""
import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r02_verbatim as r2  # noqa: E402
import r04_paraphrase as r4  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "data", "reuse", "validation")
REVIEW_SET = os.path.join(OUTDIR, "review_set.jsonl")
REVIEW_STATS = os.path.join(OUTDIR, "review_set_stats.json")
JUDGMENTS = os.path.join(OUTDIR, "judgments.jsonl")
CALIBRATION = os.path.join(OUTDIR, "calibration.json")

SEED = 20260904
PER_STRATUM = 15
K_RETRIEVE = 20
SCORE_BANDS = [(-10 ** 9, 9, "below any rule"), (9, 16, "loose rule only"),
               (16, 30, "default rule"), (30, 10 ** 9, "strong")]
SIM_BANDS = [(0.95, "closest 5%"), (0.75, "next 20%"), (0.0, "the rest")]   # quantile cut-points
GRID_K = (5, 10, 20)
GRID_RULES = ((15, 0.50), (20, 0.55), (20, 0.60), (25, 0.60), (25, 0.65), (30, 0.70))
PRECISION_FLOOR = 0.90
JUDGMENTS_ALLOWED = ("paraphrase", "not", "unsure")


def _band(score):
    for lo, hi, name in SCORE_BANDS:
        if lo <= score < hi:
            return name
    return SCORE_BANDS[-1][2]


def _sim_band(sim, cuts):
    """cuts = (q95, q75) of the cosine among embedding candidates."""
    if sim is None:
        return None
    if sim >= cuts[0]:
        return SIM_BANDS[0][1]
    if sim >= cuts[1]:
        return SIM_BANDS[1][1]
    return SIM_BANDS[2][1]


def score_candidates(units, cands, alts, batch=1024, log=print, window=r4.WINDOW):
    """The batch alignment score of every candidate region (no trace-back).
    Returns a list of (key, cand, score)."""
    flat = [(key, c) for key, lst in cands.items() for c in lst]
    Lmax = window + 2 * r4.PAD
    out = []
    short = []
    for key, c in flat:
        if c.get("direct"):
            n = c["a1"] - c["a0"]
            out.append((key, c, n * r4.MATCH))
        elif c["a1"] - c["a0"] <= Lmax and c["b1"] - c["b0"] <= Lmax:
            short.append((key, c))
        else:
            out.append((key, c, 10 ** 6))         # a long region: treated as strong, traced if sampled
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
        scores, _, _ = r4.sw_batch_scores(IDA, IDB, alts)
        for r, (key, c) in enumerate(chunk):
            out.append((key, c, int(scores[r])))
        if (b0 // batch) % 50 == 0:
            log(f"[r06]   scored {min(b0 + batch, len(short))}/{len(short)} candidates, {time.time() - t0:.0f}s")
    return out


def build(set_name="machine", stories=r2.STORIES, per_stratum=PER_STRATUM, outdir=OUTDIR,
          use_embeddings=True, log=print, seed=SEED):
    os.makedirs(outdir, exist_ok=True)
    units = r2.build_units(r2.load_stories(stories, set_name))
    tid, alts = r4.build_vocab(units)
    r4.encode_units(units, tid)
    passages = r4.make_passages(units)
    log(f"[r06] set={set_name}: {len(units)} stories, {len(passages)} passages")
    neighbours = []
    if use_embeddings:
        key = r4.corpus_key(units, r4.WINDOW, r4.STRIDE)
        cache = os.path.join(r4.OUTDIR, f"emb_{set_name}_w{r4.WINDOW}s{r4.STRIDE}_{key}.npz")
        vec = r4.embed_passages(units, passages, cache, log)
        neighbours = r4.knn(vec, passages, K_RETRIEVE)
    exact, exact_same, _ = r2.find_matches(units, r4.EXACT_SEED_K)
    cands = r4.gather_candidates(units, passages, neighbours, exact + exact_same, r4.WINDOW)
    # cross-issue only: same-issue candidates are assembly diagnostics, never reviewed as reuse
    cands = {k: v for k, v in cands.items() if units[k[0]]["issue"] != units[k[1]]["issue"]}
    scored = score_candidates(units, cands, alts, log=log)
    sims = sorted(c["sim"] for _, c, _ in scored if c["sim"] is not None)
    cuts = ((sims[int(0.95 * (len(sims) - 1))], sims[int(0.75 * (len(sims) - 1))]) if sims else (1.0, 1.0))
    strata = defaultdict(list)
    for key, c, score in scored:
        src = "exact-seeded" if c["src"] != "embedding" else "embedding-only"
        band = _band(score)
        sb = _sim_band(c["sim"], cuts) if band in ("below any rule", "loose rule only") and src == "embedding-only" else None
        strata[(src, band, sb)].append((key, c, score))
    rng = random.Random(seed)
    items = []
    table = []
    for st in sorted(strata, key=lambda s: (s[0], [b[2] for b in SCORE_BANDS].index(s[1]), str(s[2]))):
        pool = strata[st]
        n = min(per_stratum, len(pool))
        draw = rng.sample(pool, n) if n else []
        w = len(pool) / n if n else 0.0
        table.append({"source": st[0], "score_band": st[1], "cosine_band": st[2],
                      "candidates": len(pool), "drawn": n, "weight": round(w, 3)})
        for key, c, score in draw:
            items.append(_item(units, key, c, score, st, w, alts))
    rng.shuffle(items)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for i, it in enumerate(items, 1):
        it["id"] = f"v{i:03d}"
        it["set_id"] = stamp
    with open(os.path.join(outdir, "review_set.jsonl"), "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    stats = {"set": set_name, "set_id": stamp, "generated": time.strftime("%Y-%m-%d %H:%M"),
             "stories": len(units), "passages": len(passages), "k_retrieve": K_RETRIEVE,
             "candidates_cross_issue": len(scored), "cosine_cuts": {"closest_5pct": round(cuts[0], 4), "next_20pct": round(cuts[1], 4)},
             "per_stratum": per_stratum, "seed": seed, "items": len(items), "strata": table,
             "grid": {"k": list(GRID_K), "rules": [list(r) for r in GRID_RULES]}, "precision_floor": PRECISION_FLOOR,
             "score_bands": [[lo if lo > -10 ** 8 else None, hi if hi < 10 ** 8 else None, name] for lo, hi, name in SCORE_BANDS]}
    json.dump(stats, open(os.path.join(outdir, "review_set_stats.json"), "w", encoding="utf-8"), indent=1)
    log(f"[r06] review set {stamp}: {len(items)} items from {len(scored)} cross-issue candidates in {len(table)} strata")
    return stats


def _item(units, key, c, score, st, w, alts):
    ua, ub = units[key[0]], units[key[1]]
    tr = None
    if score >= SCORE_BANDS[1][0]:                      # a region that reaches at least the loose band is traced
        tr = r4._trace(units, key, c, alts) if not c.get("direct") else {
            "a0": c["a0"], "a1": c["a1"], "b0": c["b0"], "b1": c["b1"],
            "cols": c["a1"] - c["a0"], "matches": c["a1"] - c["a0"], "score": (c["a1"] - c["a0"]) * r4.MATCH}

    def span(u, s, e):
        s, e = max(0, s), min(len(u["tokens"]), e)
        if e <= s:
            return ""
        return u["canon"][u["offsets"][s][0]:u["offsets"][e - 1][1]]
    it = {"a": ua["story_id"], "b": ub["story_id"],
          "a_meta": {k: ua["meta"].get(k) for k in ("title", "author", "issue", "magazine", "cover_date")},
          "b_meta": {k: ub["meta"].get(k) for k in ("title", "author", "issue", "magazine", "cover_date")},
          "source": c["src"], "rank": c["rank"], "cosine": None if c["sim"] is None else round(c["sim"], 4),
          "score": score if score < 10 ** 6 else None,
          "stratum": {"source": st[0], "score_band": st[1], "cosine_band": st[2]}, "weight": round(w, 3),
          "window_a": span(ua, c["a0"], c["a1"]), "window_b": span(ub, c["b0"], c["b1"]),
          "window_a_tok": [c["a0"], c["a1"]], "window_b_tok": [c["b0"], c["b1"]]}
    if tr:
        it["aligned"] = {"cols": tr["cols"], "matches": tr["matches"], "score": tr["score"],
                         "identity": round(tr["matches"] / tr["cols"], 4) if tr["cols"] else 0.0,
                         "a_tok": [tr["a0"], tr["a1"]], "b_tok": [tr["b0"], tr["b1"]],
                         "text_a": span(ua, tr["a0"], tr["a1"]), "text_b": span(ub, tr["b0"], tr["b1"])}
    else:
        it["aligned"] = None
    return it


# ---------------------------------------------------------------- judgments and calibration

def load_review_set(path=REVIEW_SET):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def load_judgments(path=JUDGMENTS, set_id=None):
    out = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                j = json.loads(line)
            except ValueError:
                continue
            if set_id and j.get("set_id") != set_id:
                continue
            if j.get("judgment") in JUDGMENTS_ALLOWED:
                out.append(j)
    return out


def latest_by_user(judgments):
    """{item_id: {user: judgment dict}} — a reader's later judgment of an item replaces the earlier one."""
    out = defaultdict(dict)
    for j in judgments:
        out[j["item"]][j["user"]] = j
    return out


def consensus(per_user):
    """Agreed judgment of one item: 'paraphrase' or 'not' when every reader who
    decided agrees (unsure does not count against), else 'disputed' or None."""
    votes = {u: j["judgment"] for u, j in per_user.items() if j["judgment"] != "unsure"}
    if not votes:
        return None
    vals = set(votes.values())
    if len(vals) == 1:
        return vals.pop()
    return "disputed"


def kept_by(item, k, min_cols, min_ident):
    if item["source"] == "embedding" and item["rank"] > k:
        return False
    al = item.get("aligned")
    return bool(al) and al["cols"] >= min_cols and al["identity"] >= min_ident


def kappa(a, b):
    """Cohen's kappa for two readers' labels on the same items (lists of equal length)."""
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    cats = set(a) | set(b)
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return None if pe == 1 else round((po - pe) / (1 - pe), 3)


def calibrate(items=None, judgments=None, write=True, outdir=OUTDIR):
    items = items if items is not None else load_review_set()
    if not items:
        return None
    set_id = items[0].get("set_id")
    judgments = judgments if judgments is not None else load_judgments(set_id=set_id)
    by_item = latest_by_user(judgments)
    labels = {it["id"]: consensus(by_item.get(it["id"], {})) for it in items}
    decided = [it for it in items if labels[it["id"]] in ("paraphrase", "not")]
    pos_w = sum(it["weight"] for it in decided if labels[it["id"]] == "paraphrase")
    grid = []
    for k in GRID_K:
        for min_cols, min_ident in GRID_RULES:
            kept = [it for it in decided if kept_by(it, k, min_cols, min_ident)]
            tp_w = sum(it["weight"] for it in kept if labels[it["id"]] == "paraphrase")
            kept_w = sum(it["weight"] for it in kept)
            tp = sum(1 for it in kept if labels[it["id"]] == "paraphrase")
            row = {"k": k, "min_cols": min_cols, "min_identity": min_ident,
                   "kept": len(kept), "true_positives": tp,
                   "precision": round(tp / len(kept), 3) if kept else None,
                   "recall": round(tp / sum(1 for it in decided if labels[it["id"]] == "paraphrase"), 3)
                   if any(labels[it["id"]] == "paraphrase" for it in decided) else None,
                   "precision_weighted": round(tp_w / kept_w, 3) if kept_w else None,
                   "recall_weighted": round(tp_w / pos_w, 3) if pos_w else None,
                   "pool_weighted": round(kept_w, 1)}
            grid.append(row)
    ok = [g for g in grid if g["precision_weighted"] is not None and g["precision_weighted"] >= PRECISION_FLOOR
          and g["recall_weighted"] is not None]
    chosen = max(ok, key=lambda g: (g["recall_weighted"], -g["pool_weighted"])) if ok else None
    users = Counter(j["user"] for j in judgments)
    top2 = [u for u, _ in users.most_common(2)]
    agreement = None
    if len(top2) == 2:
        both = [(by_item[i][top2[0]]["judgment"], by_item[i][top2[1]]["judgment"]) for i in by_item
                if top2[0] in by_item[i] and top2[1] in by_item[i]]
        dec = [(x, y) for x, y in both if x != "unsure" and y != "unsure"]
        agreement = {"readers": top2, "items_both_judged": len(both), "items_both_decided": len(dec),
                     "agree": sum(1 for x, y in dec if x == y),
                     "kappa": kappa([x for x, _ in dec], [y for _, y in dec]) if dec else None}
    out = {"set_id": set_id, "generated": time.strftime("%Y-%m-%d %H:%M"), "items": len(items),
           "judged_items": sum(1 for i in items if by_item.get(i["id"])),
           "decided_items": len(decided),
           "labels": dict(Counter(v for v in labels.values() if v)),
           "readers": dict(users), "agreement": agreement, "precision_floor": PRECISION_FLOOR,
           "grid": grid, "chosen": chosen}
    if write:
        os.makedirs(outdir, exist_ok=True)
        json.dump(out, open(os.path.join(outdir, "calibration.json"), "w", encoding="utf-8"), indent=1)
    return out


# ---------------------------------------------------------------- selftest

def selftest():
    import r03_synthetic as r3
    recs = r2._fake_corpus()
    for r in recs:
        words = r["text"].split()
        r["text"] = ". ".join(" ".join(words[i:i + 12]) for i in range(0, len(words), 12))
    synth, plants = r3.make_synthetic(recs, seed=2, n_per_kind=2)
    units = r2.build_units(synth)
    tid, alts = r4.build_vocab(units)
    r4.encode_units(units, tid)
    passages = r4.make_passages(units)
    exact, exact_same, _ = r2.find_matches(units, 6)
    cands = r4.gather_candidates(units, passages, [], exact + exact_same, r4.WINDOW)
    cands = {k: v for k, v in cands.items() if units[k[0]]["issue"] != units[k[1]]["issue"]}
    scored = score_candidates(units, cands, alts, log=lambda *a: None)
    assert scored and max(s for _, _, s in scored) >= 30, "a planted copy must score as strong"
    key, c, score = max(scored, key=lambda x: x[2])
    it = _item(units, key, c, score, ("exact-seeded", "strong", None), 1.0, alts)
    assert it["aligned"] and it["aligned"]["cols"] >= 20 and it["aligned"]["identity"] >= 0.6, it["aligned"]
    it["id"], it["set_id"] = "v001", "test"
    neg = dict(it, id="v002", aligned={"cols": 12, "matches": 7, "identity": 0.583, "score": 9, "a_tok": [0, 12], "b_tok": [0, 12], "text_a": "", "text_b": ""},
               source="embedding", rank=12, weight=40.0)
    js = [{"item": "v001", "user": "sujin", "judgment": "paraphrase", "set_id": "test"},
          {"item": "v001", "user": "heejin", "judgment": "paraphrase", "set_id": "test"},
          {"item": "v002", "user": "sujin", "judgment": "not", "set_id": "test"},
          {"item": "v002", "user": "heejin", "judgment": "not", "set_id": "test"}]
    cal = calibrate([it, neg], js, write=False)
    assert cal["chosen"] is not None and cal["chosen"]["recall_weighted"] == 1.0, cal["chosen"]
    assert cal["agreement"]["kappa"] == 1.0, cal["agreement"]
    assert not kept_by(neg, 10, 20, 0.60) and kept_by(it, 5, 20, 0.60)
    assert consensus({"a": {"judgment": "paraphrase"}, "b": {"judgment": "not"}}) == "disputed"
    assert consensus({"a": {"judgment": "paraphrase"}, "b": {"judgment": "unsure"}}) == "paraphrase"
    assert _band(8) == "below any rule" and _band(9) == "loose rule only" and _band(16) == "default rule" and _band(31) == "strong"
    print("selftest OK")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="draw the review set from the candidates")
    ap.add_argument("--calibrate", action="store_true", help="score the settings grid against the judgments")
    ap.add_argument("--set", default="machine", choices=["machine", "verified", "corrected"])
    ap.add_argument("--stories", default=r2.STORIES)
    ap.add_argument("--per-stratum", type=int, default=PER_STRATUM)
    ap.add_argument("--no-embed", action="store_true", help="exact seeds only (no embedding retrieval)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.build:
        build(a.set, a.stories, a.per_stratum, use_embeddings=not a.no_embed)
    if a.calibrate:
        cal = calibrate()
        if cal is None:
            print("[r06] no review set")
        else:
            ch = cal["chosen"]
            print(f"[r06] calibration: {cal['judged_items']}/{cal['items']} items judged, {cal['decided_items']} decided; "
                  + (f"chosen K={ch['k']}, {ch['min_cols']} columns, identity {ch['min_identity']} "
                     f"(precision {ch['precision_weighted']}, recall {ch['recall_weighted']})" if ch else "no setting reaches the precision floor yet"))
    if not (a.build or a.calibrate):
        sys.exit("pass --build, --calibrate or --selftest")


if __name__ == "__main__":
    main()
