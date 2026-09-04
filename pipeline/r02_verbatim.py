#!/usr/bin/env python3
"""r02 — verbatim text reuse (protocol section 3.1), transparent and exact.

Steps, exactly as the protocol describes them:
  1. Minimal candidate seed retrieval: an inverted index of k-word
     shingles (k = 6, 7, 8 as separate passes); two stories that share a
     shingle become a candidate at that location.
  2. Extend the seed to its maximal span: walk left and right token by
     token while the folded tokens stay identical. All seeds inside one
     shared passage resolve to the same maximal span, so duplicates
     collapse by construction.
  3. Consolidate: overlapping occurrences of the same passage across
     stories are grouped into a reuse cluster with N witnesses.
  4. Report: length and frequency of repeated passages, share of each
     story involved, the most extensive cases.

Two design choices that are ours, recorded here because they shape the
numbers: (a) only pairs of stories from DIFFERENT issues enter the
inventory — a passage shared by two records of the same issue is almost
always one story the assembler cut in two, so those go to a separate
diagnostics file (they are useful for fixing assembly, not evidence of
reuse); (b) a shingle occurring in more than MAX_DF stories is skipped
as commonplace for candidate retrieval (the way Passim caps document
frequency) and every skipped shingle is written out for review, so the
choice is inspectable.

Run:  python3 pipeline/r02_verbatim.py --set machine --k 6 7 8
      python3 pipeline/r02_verbatim.py --selftest
Input:  data/pilot_stories.jsonl (from r00)      Output: data/reuse/
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r01_normalize import story_units  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORIES = os.path.join(ROOT, "data", "pilot_stories.jsonl")
OUTDIR = os.path.join(ROOT, "data", "reuse")
STORY_TYPES = ("story", "serial_part")     # serial_part: exports made before 2026-09-04, when instalments became stories with serial fields;
                                           # house and ad records (the publisher's own matter, advertising) are never in the inventory
MIN_TOKENS = 50          # shorter records are fragments, not stories
MAX_DF = 50              # commonplace-shingle cap (stories per shingle)
LEN_BINS = [(0, 10, "seed-9"), (10, 20, "10-19"), (20, 50, "20-49"),
            (50, 100, "50-99"), (100, 500, "100-499"), (500, 10 ** 9, "500+")]


# ---------------------------------------------------------------- corpus

def load_stories(path=STORIES, which="machine"):
    """Story records for one story set: 'machine' = every assembled
    story; 'verified' = only human-verified ones; 'corrected' = every
    story a human has touched (verified or modified)."""
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("type") not in STORY_TYPES:
                continue
            if which == "verified" and r.get("status") != "verified":
                continue
            if which == "corrected" and r.get("status") not in ("verified", "modified"):
                continue
            recs.append(r)
    return recs


def build_units(recs):
    units = []
    for r in recs:
        u = story_units(r)
        if len(u["tokens"]) < MIN_TOKENS:
            continue
        u["issue"] = r["issue"]
        u["fragments"] = set(r.get("fragments") or [])
        u["meta"] = {k: r.get(k) for k in ("issue", "magazine", "cover_date",
                                            "genre", "format", "type", "title",
                                            "author", "pages", "status")}
        units.append(u)
    _mark_region_families(units)
    return units


def _mark_region_families(units):
    """Two records of the same issue that own the same scan region are
    copies of the same printed text, not two witnesses of it. Records are
    grouped into 'families' (union-find over shared region keys, within an
    issue); every unit gets a family id. Witness counts are reported both
    raw and with families collapsed."""
    parent = list(range(len(units)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner = {}
    for i, u in enumerate(units):
        for fk in u["fragments"]:
            key = (u["issue"], fk)
            if key in owner:
                ra, rb = find(owner[key]), find(i)
                if ra != rb:
                    parent[rb] = ra
            else:
                owner[key] = i
    for i, u in enumerate(units):
        u["family"] = find(i)


def region_overlap_report(units):
    """Per issue: how many region keys are owned by more than one record,
    and which record pairs share the most. This is an assembly diagnostic
    (docs/assembly-notes.md rule 7, one region one record), written next
    to the reuse outputs because it explains most same-issue matches."""
    by_issue = defaultdict(list)
    for u in units:
        by_issue[u["issue"]].append(u)
    report = {}
    for iid, us in sorted(by_issue.items()):
        own = defaultdict(list)
        for u in us:
            for fk in u["fragments"]:
                own[fk].append(u["story_id"])
        multi = {fk: v for fk, v in own.items() if len(v) > 1}
        pairs = defaultdict(int)
        for v in multi.values():
            v = sorted(set(v))
            for i in range(len(v)):
                for j in range(i + 1, len(v)):
                    pairs[(v[i], v[j])] += 1
        worst = sorted(pairs.items(), key=lambda kv: -kv[1])[:10]
        report[iid] = {
            "stories": len(us),
            "region_keys": len(own),
            "keys_owned_by_2plus": len(multi),
            "stories_sharing": len({s for v in multi.values() for s in v}),
            "worst_pairs": [{"a": a, "b": b, "shared_keys": n} for (a, b), n in worst],
        }
    return report


# ---------------------------------------------------------------- engine

def find_matches(units, k, max_df=MAX_DF):
    """All maximal exact shared passages of length >= k between stories.

    Returns (matches, same_issue, skipped_shingles). A match is a dict
    with story indices a < b, token intervals, and length."""
    index = defaultdict(list)                     # shingle -> [(story, pos)]
    for si, u in enumerate(units):
        toks = u["tokens"]
        for p in range(len(toks) - k + 1):
            index[" ".join(toks[p:p + k])].append((si, p))

    skipped = []
    seen = set()
    matches, same_issue = [], []
    for sh, posts in index.items():
        if len(posts) < 2:
            continue
        n_stories = len({s for s, _ in posts})
        if n_stories < 2:
            continue
        if n_stories > max_df:
            skipped.append({"shingle": sh, "stories": n_stories,
                            "occurrences": len(posts)})
            continue
        for i in range(len(posts)):
            sa, pa = posts[i]
            for j in range(i + 1, len(posts)):
                sb, pb = posts[j]
                if sa == sb:
                    continue
                if sa > sb:
                    sa, pa, sb, pb = sb, pb, sa, pa
                ta, tb = units[sa]["tokens"], units[sb]["tokens"]
                a0, b0 = pa, pb
                while a0 > 0 and b0 > 0 and ta[a0 - 1] == tb[b0 - 1]:
                    a0 -= 1
                    b0 -= 1
                a1, b1 = pa + k, pb + k
                while a1 < len(ta) and b1 < len(tb) and ta[a1] == tb[b1]:
                    a1 += 1
                    b1 += 1
                key = (sa, sb, a0, b0, a1 - a0)
                if key in seen:
                    continue
                seen.add(key)
                m = {"a": sa, "b": sb, "a0": a0, "a1": a1, "b0": b0, "b1": b1,
                     "len": a1 - a0}
                if units[sa]["issue"] == units[sb]["issue"]:
                    same_issue.append(m)
                else:
                    matches.append(m)
    return matches, same_issue, skipped


def _merge_spans(members):
    by_story = defaultdict(list)
    for s, a0, a1 in members:
        by_story[s].append((a0, a1))
    out = []
    for s, spans in by_story.items():
        spans.sort()
        cur0, cur1 = spans[0]
        for a0, a1 in spans[1:]:
            if a0 <= cur1:
                cur1 = max(cur1, a1)
            else:
                out.append((s, cur0, cur1))
                cur0, cur1 = a0, a1
        out.append((s, cur0, cur1))
    return out


def cluster(matches, units, overlap=0.5):
    """Union-find over passage occurrences: the two sides of every match
    are joined, and occurrences in the same story that overlap by at
    least `overlap` of the shorter one are joined. A cluster is one
    passage with all its witnesses."""
    occ = {}          # (story, start, end) -> node id
    parent = []

    def node(s, a0, a1):
        kk = (s, a0, a1)
        if kk not in occ:
            occ[kk] = len(parent)
            parent.append(len(parent))
        return occ[kk]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for m in matches:
        union(node(m["a"], m["a0"], m["a1"]), node(m["b"], m["b0"], m["b1"]))
    by_story = defaultdict(list)
    for (s, a0, a1), nid in occ.items():
        by_story[s].append((a0, a1, nid))
    for s, lst in by_story.items():
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] >= lst[i][1]:
                    break
                ov = min(lst[i][1], lst[j][1]) - lst[j][0]
                shorter = min(lst[i][1] - lst[i][0], lst[j][1] - lst[j][0])
                if shorter and ov / shorter >= overlap:
                    union(lst[i][2], lst[j][2])
    groups = defaultdict(list)
    for (s, a0, a1), nid in occ.items():
        groups[find(nid)].append((s, a0, a1))
    out = []
    for members in groups.values():
        # one story can hold the passage at several places; occurrences
        # that overlap are one place, so merge them into their union
        members = _merge_spans(members)
        members.sort(key=lambda t: -(t[2] - t[1]))
        s, a0, a1 = members[0]
        u = units[s]
        c0, c1 = u["offsets"][a0][0], u["offsets"][a1 - 1][1]
        out.append({
            "witnesses": len({m[0] for m in members}),
            "witnesses_collapsed": len({units[m[0]]["family"] for m in members}),
            "issues": len({units[m[0]]["issue"] for m in members}),
            "occurrences": len(members),
            "max_len": a1 - a0,
            "representative": {"story_id": u["story_id"], "tok": [a0, a1],
                               "text": u["canon"][c0:c1]},
            "members": [{"story_id": units[m[0]]["story_id"],
                         "issue": units[m[0]]["issue"], "tok": [m[1], m[2]],
                         "len": m[2] - m[1],
                         "text": units[m[0]]["canon"][units[m[0]]["offsets"][m[1]][0]:
                                                      units[m[0]]["offsets"][m[2] - 1][1]][:1500]}
                        for m in members],
        })
    out.sort(key=lambda c: (-c["witnesses"], -c["max_len"]))
    return out


# ---------------------------------------------------------------- report

def length_bin(n):
    for lo, hi, name in LEN_BINS:
        if lo <= n < hi:
            return name
    return "500+"


def pair_stats(matches, units):
    pairs = defaultdict(lambda: {"n": 0, "max_len": 0, "ia": set(), "ib": set()})
    for m in matches:
        p = pairs[(m["a"], m["b"])]
        p["n"] += 1
        p["max_len"] = max(p["max_len"], m["len"])
        p["ia"].update(range(m["a0"], m["a1"]))
        p["ib"].update(range(m["b0"], m["b1"]))
    out = []
    for (a, b), p in pairs.items():
        out.append({"a": units[a]["story_id"], "b": units[b]["story_id"],
                    "n_matches": p["n"], "max_len": p["max_len"],
                    "cover_a": len(p["ia"]) / len(units[a]["tokens"]),
                    "cover_b": len(p["ib"]) / len(units[b]["tokens"])})
    out.sort(key=lambda r: -r["max_len"])
    return out


def story_share(matches, units):
    covered = defaultdict(set)
    for m in matches:
        covered[m["a"]].update(range(m["a0"], m["a1"]))
        covered[m["b"]].update(range(m["b0"], m["b1"]))
    return {units[s]["story_id"]: round(len(c) / len(units[s]["tokens"]), 4)
            for s, c in covered.items()}


def serialize_match(m, units):
    ua, ub = units[m["a"]], units[m["b"]]
    ca = (ua["offsets"][m["a0"]][0], ua["offsets"][m["a1"] - 1][1])
    cb = (ub["offsets"][m["b0"]][0], ub["offsets"][m["b1"] - 1][1])
    out = {"a": ua["story_id"], "b": ub["story_id"], "len": m["len"],
           "a_tok": [m["a0"], m["a1"]], "b_tok": [m["b0"], m["b1"]],
           "a_char": list(ca), "b_char": list(cb),
           "a_issue": ua["issue"], "b_issue": ub["issue"],
           "excerpt": ua["canon"][ca[0]:ca[1]][:300]}
    if ua["issue"] == ub["issue"]:
        shared = len(ua["fragments"] & ub["fragments"])
        out["shared_regions"] = shared
        out["cause"] = "shared-region duplicate" if shared else "same-issue repeat"
    return out


def run(units, k, set_name, outdir=OUTDIR, max_df=MAX_DF):
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    matches, same_issue, skipped = find_matches(units, k, max_df)
    t1 = time.time()
    clusters = cluster(matches, units)
    pairs = pair_stats(matches, units)
    shares = story_share(matches, units)
    hist = defaultdict(int)
    for m in matches:
        hist[length_bin(m["len"])] += 1
    n_tok = sum(len(u["tokens"]) for u in units)
    si_dup = sum(1 for m in same_issue
                 if units[m["a"]]["fragments"] & units[m["b"]]["fragments"])
    fam_with_reuse = {units[s]["family"] for s in
                      {m["a"] for m in matches} | {m["b"] for m in matches}}
    stats = {
        "set": set_name, "k": k, "max_df": max_df,
        "stories": len(units), "tokens": n_tok,
        "issues": len({u["issue"] for u in units}),
        "region_families": len({u["family"] for u in units}),
        "matches": len(matches), "same_issue_matches": len(same_issue),
        "same_issue_from_shared_regions": si_dup,
        "skipped_commonplace_shingles": len(skipped),
        "clusters": len(clusters),
        "clusters_3plus_witnesses": sum(1 for c in clusters if c["witnesses"] >= 3),
        "clusters_3plus_witnesses_collapsed": sum(
            1 for c in clusters if c["witnesses_collapsed"] >= 3),
        "length_hist": {name: hist.get(name, 0) for _, _, name in LEN_BINS},
        "longest_match": max((m["len"] for m in matches), default=0),
        "stories_with_reuse": len(shares),
        "stories_with_reuse_collapsed": len(fam_with_reuse),
        "seconds_index_and_extend": round(t1 - t0, 2),
        "seconds_total": round(time.time() - t0, 2),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }
    json.dump(region_overlap_report(units),
              open(os.path.join(outdir, f"{set_name}_region_overlap.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=1)
    pre = os.path.join(outdir, f"{set_name}_k{k}")
    with open(pre + "_matches.jsonl", "w", encoding="utf-8") as f:
        for m in sorted(matches, key=lambda m: -m["len"]):
            f.write(json.dumps(serialize_match(m, units), ensure_ascii=False) + "\n")
    with open(pre + "_sameissue.jsonl", "w", encoding="utf-8") as f:
        for m in sorted(same_issue, key=lambda m: -m["len"]):
            f.write(json.dumps(serialize_match(m, units), ensure_ascii=False) + "\n")
    json.dump(clusters, open(pre + "_clusters.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(pairs, open(pre + "_pairs.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(shares, open(pre + "_story_share.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(skipped, open(pre + "_skipped_shingles.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(stats, open(pre + "_stats.json", "w", encoding="utf-8"), indent=1)
    return stats


# ---------------------------------------------------------------- selftest

def _fake_corpus():
    """Six fabricated stories; a 40-token passage planted in stories 0 and
    3 (different issues) and a 25-token passage in 1 and 2 (same issue)."""
    import random
    rng = random.Random(3)
    vocab = [f"w{i}" for i in range(400)]

    def prose(n):
        return " ".join(rng.choice(vocab) for _ in range(n))
    shared40 = " ".join(f"p{i}" for i in range(40))
    shared25 = " ".join(f"q{i}" for i in range(25))
    texts = [prose(300) + " " + shared40 + " " + prose(200),
             prose(150) + " " + shared25 + " " + prose(300),
             prose(400) + " " + shared25 + " " + prose(100),
             prose(250) + " " + shared40 + " " + prose(250),
             prose(500), prose(500)]
    issues = ["i1", "i2", "i2", "i3", "i3", "i4"]
    # stories 1 and 2 (same issue) share a scan region, so their common
    # passage is a shared-region duplicate, not a within-issue repeat
    frags = [["1:0"], ["5:0", "5:1"], ["5:1", "9:0"], ["2:0"], ["3:0"], ["1:0"]]
    return [{"story_id": f"s{i}", "issue": issues[i], "type": "story",
             "status": "auto", "text": t, "fragments": frags[i]}
            for i, t in enumerate(texts)]


def selftest():
    units = build_units(_fake_corpus())
    ok = True
    fam_ok = (units[1]["family"] == units[2]["family"]
              and len({u["family"] for u in units}) == 5)
    print(f"region families: {[u['family'] for u in units]} -> "
          f"{'OK' if fam_ok else 'FAIL'}")
    ok = ok and fam_ok
    for k in (6, 7, 8):
        matches, same_issue, skipped = find_matches(units, k)
        lens = sorted(m["len"] for m in matches)
        si = sorted(m["len"] for m in same_issue)
        good = lens == [40] and si == [25] and not skipped
        cl = cluster(matches, units)
        good = good and len(cl) == 1 and cl[0]["witnesses"] == 2 \
            and cl[0]["witnesses_collapsed"] == 2
        sm = serialize_match(same_issue[0], units)
        good = good and sm["cause"] == "shared-region duplicate" \
            and sm["shared_regions"] == 1
        print(f"k={k}: cross-issue {lens} same-issue {si} ({sm['cause']}) "
              f"clusters {len(cl)} -> {'OK' if good else 'FAIL'}")
        ok = ok and good
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="machine", choices=["machine", "verified", "corrected"])
    ap.add_argument("--k", type=int, nargs="+", default=[6, 7, 8])
    ap.add_argument("--stories", default=STORIES)
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--max-df", type=int, default=MAX_DF)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    units = build_units(load_stories(args.stories, args.set))
    print(f"[r02] set={args.set}: {len(units)} stories, "
          f"{sum(len(u['tokens']) for u in units)} tokens")
    for k in args.k:
        st = run(units, k, args.set, args.out, args.max_df)
        print(f"[r02] k={k}: {st['matches']} cross-issue matches, "
              f"{st['same_issue_matches']} same-issue, {st['clusters']} clusters, "
              f"longest {st['longest_match']} tokens, "
              f"{st['skipped_commonplace_shingles']} commonplace shingles skipped, "
              f"{st['seconds_total']}s")


if __name__ == "__main__":
    main()
