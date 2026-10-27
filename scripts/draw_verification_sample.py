#!/usr/bin/env python3
"""The verification sample (corpus-build-plan-2026-10-04, section 6.1; Heejin's choice of 14 October 2026: "150 issues,
stratified, two readers"): a stratified random sample of assembled issues, drawn once with a fixed seed, for people to
read through on the workbench against the scans and mark verified record by record. The assembly's accuracy reported
in Stage 2 is the agreement of the machine's records with the verified ones (pipeline/s11_corrections_audit.py
--sample), by stratum.

Strata: decade of the cover date (1890s … 1950s, and "undated") × genre in the plan's eight classes (science fiction,
detective, western, adventure, weird, general, romance, other — the archive's rarer filings, fantasy, humor, youth,
sports, unsorted and the like, are "other"; the archive's own filing is kept on every issue). The plan also names the
format, pulp or digest, but the archive's metadata does not carry it (every issue is "unknown"). Allocation: at
least MIN_PER_CELL issues from every non-empty cell (all of them when the cell is smaller), the rest in proportion to
the cells' sizes (largest remainders), to N in all. Within a cell the issues are chosen at random (random.Random(SEED)).
The reading order interleaves the genres so that the first weeks' reading is not all one genre. Every fifth issue of
that order is read by a second reader too (so that reader agreement can be reported).

    python3 scripts/draw_verification_sample.py                       # on the server: from the assembled issues
    python3 scripts/draw_verification_sample.py --list <issues.json> --any-state   # a dry run anywhere
    python3 scripts/draw_verification_sample.py --replace             # replace the sampled issues that were set aside since
Writes config/verification_sample.json (tracked in git: the sample is part of the study's record) and prints the cells.
Safe to run again: the same seed and the same population give the same sample; a changed population changes it, so the
file is drawn once and committed.

--replace (decision 1 of 19 October 2026, revised 21 October: items that are not magazine issues are sorted by kind):
every sampled issue that is now in config/corpus_set_aside.json or of a kind other than magazine in
config/corpus_kinds.json is replaced by an assembled issue of the same decade-and-genre cell, chosen
at random (seeded by the slot's order number, so a rerun chooses the same) among the issues not yet in the sample. The
replacement takes the slot's order number and second-reader mark; the issue it replaces is kept in the file under
"replaced" with the reason. A cell the exclusion empties (every 1910s western was a Wild West Weekly number, say) gives
its slot to its decade — the replacement is drawn from the decade's other genres — and failing that to its genre, so
the sample keeps its 150 and its spread over the decades; the entry says so ("cell_fallback"). The verification done on
a replaced issue stays in the annotation logs; it does not count toward the sample.
"""
import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

SEED = 20261014
N = 150
MIN_PER_CELL = 2
SECOND_READER_EVERY = 5


def decade_of(issue):
    y = issue.get("year")
    try:
        y = int(y)
    except (TypeError, ValueError):
        return "undated"
    if issue.get("undated") or not (1880 <= y <= 1960):
        return "undated"
    return f"{(y // 10) * 10}s"


GENRES = ("science fiction", "detective", "western", "adventure", "weird", "general", "romance")      # the plan's classes; the rest is "other"


def genre_class(issue):
    g = (issue.get("genre") or "").strip().lower()
    return g if g in GENRES else "other"


def draw(issues, n=N, seed=SEED, min_per_cell=MIN_PER_CELL):
    cells = defaultdict(list)
    for i in issues:
        cells[(decade_of(i), genre_class(i))].append(i)
    keys = sorted(cells)
    sizes = {k: len(cells[k]) for k in keys}
    alloc = {k: min(min_per_cell, sizes[k]) for k in keys}
    left = n - sum(alloc.values())
    if left < 0:
        raise SystemExit(f"{len(keys)} non-empty cells × {min_per_cell} is more than {n}; raise N or lower MIN_PER_CELL")
    total = sum(sizes.values())
    room = {k: sizes[k] - alloc[k] for k in keys}
    # proportional shares of the rest, by largest remainder, never beyond a cell's size
    shares = {k: left * sizes[k] / total for k in keys}
    whole = {k: min(int(shares[k]), room[k]) for k in keys}
    left2 = left - sum(whole.values())
    by_rem = sorted(keys, key=lambda k: (-(shares[k] - int(shares[k])), k))
    for k in by_rem:
        if left2 <= 0:
            break
        if whole[k] < room[k]:
            whole[k] += 1; left2 -= 1
    for k in keys:
        alloc[k] += whole[k]
    rnd = random.Random(seed)
    chosen = []
    for k in keys:
        pool = sorted(cells[k], key=lambda i: i["id"])
        chosen += [dict(i, decade=k[0], genre_stratum=k[1]) for i in rnd.sample(pool, alloc[k])]
    # the reading order: round-robin over the genres, each genre's issues shuffled
    by_genre = defaultdict(list)
    for i in chosen:
        by_genre[i["genre_stratum"]].append(i)
    for g in by_genre:
        rnd.shuffle(by_genre[g])
    genres = sorted(by_genre, key=lambda g: -len(by_genre[g]))
    order = []
    while any(by_genre.values()):
        for g in genres:
            if by_genre[g]:
                order.append(by_genre[g].pop())
    out = []
    for pos, i in enumerate(order, 1):
        out.append({"order": pos, "id": i["id"], "ia_identifier": i.get("ia_identifier"), "magazine": i.get("magazine"),
                    "cover_date": i.get("cover_date"), "decade": i["decade"], "genre": i["genre_stratum"],
                    "genre_as_filed": i.get("genre"), "second_reader": pos % SECOND_READER_EVERY == 0})
    cell_rows = [{"decade": k[0], "genre": k[1], "population": sizes[k], "drawn": alloc[k]} for k in keys]
    return out, cell_rows


def replace_set_aside(sample, population, aside, seed=SEED, now=None):
    """The sampled issues now set aside, each replaced within its cell (see --replace above). Pure: returns the new
    sample dict and the list of (old id, new id or None, cell, fallback or None). population: the assembled issues to
    draw from (the set-aside ones already left out)."""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    in_sample = {i["id"] for i in sample["issues"]}
    cells = defaultdict(list)
    for i in population:
        if i["id"] not in in_sample and i["id"] not in aside:
            cells[(decade_of(i), genre_class(i))].append(i)
    out = dict(sample)
    out["issues"] = []
    replaced = list(sample.get("replaced") or [])
    changes = []
    for i in sample["issues"]:
        if i["id"] not in aside:
            out["issues"].append(i)
            continue
        key = (i["decade"], i["genre"])
        reason = (aside[i["id"]] or {}).get("reason") or (aside[i["id"]] or {}).get("class") or "set aside"
        old = dict(i, replaced_on=now, replaced_reason=reason)
        pool, fallback = sorted(cells.get(key, []), key=lambda x: x["id"]), None
        if not pool:                      # the cell is empty now: the slot goes to the decade, then to the genre
            pool = sorted((x for k, v in cells.items() if k[0] == key[0] for x in v), key=lambda x: x["id"])
            fallback = "decade"
        if not pool:
            pool = sorted((x for k, v in cells.items() if k[1] == key[1] for x in v), key=lambda x: x["id"])
            fallback = "genre"
        if not pool:
            replaced.append(dict(old, replaced_by=None, note="no assembled issue left in the cell, the decade or the genre: the slot is dropped"))
            changes.append((i["id"], None, key, None))
            continue
        rnd = random.Random(f"{seed}:{i['order']}")
        pick = rnd.choice(pool)
        pkey = (decade_of(pick), genre_class(pick))
        cells[pkey].remove(pick)
        new = {"order": i["order"], "id": pick["id"], "ia_identifier": pick.get("ia_identifier"), "magazine": pick.get("magazine"),
               "cover_date": pick.get("cover_date"), "decade": pkey[0], "genre": pkey[1], "genre_as_filed": pick.get("genre"),
               "second_reader": i["second_reader"], "replaces": i["id"], "replaced_on": now}
        if fallback:
            new["cell_fallback"] = f"the {key[0]} / {key[1]} cell is empty after the exclusion: drawn from the {fallback}"
        out["issues"].append(new)
        replaced.append(dict(old, replaced_by=pick["id"], **({"cell_fallback": new["cell_fallback"]} if fallback else {})))
        changes.append((i["id"], pick["id"], key, fallback))
    out["replaced"] = replaced
    out["n"] = len(out["issues"])
    out["replacements_on"] = sorted(set((out.get("replacements_on") or []) + ([now] if changes else [])))
    return out, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", help="the issue list (default: config/corpus_issues.json)")
    ap.add_argument("--any-state", action="store_true", help="draw from the whole list, not only the assembled issues (a dry run)")
    ap.add_argument("--n", type=int, default=N)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default=os.path.join(ROOT, "config", "verification_sample.json"))
    ap.add_argument("--replace", action="store_true", help="replace the sampled issues that are now set aside, within their cells")
    ap.add_argument("--freeze", action="store_true", help="freeze the sample: its slots are not replaced any more (Sujin Kang, 27 October 2026)")
    ap.add_argument("--unfreeze", action="store_true", help="lift the freeze (say why in the build log)")
    args = ap.parse_args()
    from corpus_lib import corpus_config, all_states, set_aside, corpus_kinds
    cfg = json.load(open(args.list, encoding="utf-8")) if args.list else corpus_config()
    issues = cfg["issues"]
    aside = set_aside()
    kinds = corpus_kinds()                       # 21 Oct 2026: the sample is drawn from the magazine issues; books leave it too
    issues = [i for i in issues if i["id"] not in aside and kinds.get(i["id"], "magazine") == "magazine"]
    if not args.any_state:
        states = all_states()
        issues = [i for i in issues if "assembled" in states.get(i["id"], {}).get("stages", {})]
    if args.freeze or args.unfreeze:
        S = json.load(open(args.out, encoding="utf-8"))
        if args.freeze:
            S["frozen"] = time.strftime("%Y-%m-%d")
            S["frozen_note"] = ("The sample's 150 slots are final (Sujin Kang, 27 October 2026, after the level-1 check of every slot that was "
                                "replaced): no slot is replaced any more; an issue found later not to be a magazine issue leaves the corpus "
                                "but its slot stays, counted as checked at level 1 for nothing.")
        else:
            S.pop("frozen", None); S.pop("frozen_note", None)
        json.dump(S, open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(("frozen on " + S["frozen"]) if args.freeze else "unfrozen", "—", os.path.relpath(args.out, ROOT))
        return
    if args.replace:
        S = json.load(open(args.out, encoding="utf-8"))
        if S.get("frozen"):
            print(f"the sample is frozen (since {S['frozen']}): no slot replaced; --unfreeze lifts it"); return
        leaving = dict(aside)
        for i in S["issues"]:
            if i["id"] not in leaving and kinds.get(i["id"], "magazine") != "magazine":
                leaving[i["id"]] = {"id": i["id"], "class": f"kind: {kinds[i['id']]}", "reason": f"kind: {kinds[i['id']]} (not a magazine issue; it stays in the data)"}
        new, changes = replace_set_aside(S, issues, leaving, S.get("seed", args.seed))
        if not changes:
            print("nothing to replace: no sampled issue is set aside"); return
        json.dump(new, open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        for old, nid, key, fb in changes:
            print(f"  {key[0]} / {key[1]}: {old} -> {nid or '(slot dropped: nothing left in the cell, the decade or the genre)'}" + (f"  [cell empty: drawn from the {fb}]" if fb else ""))
        print(f"{sum(1 for c in changes if c[1])} slots replaced ({sum(1 for c in changes if c[3])} from outside an emptied cell), {sum(1 for c in changes if not c[1])} dropped; "
              f"{new['n']} issues in the sample; written {os.path.relpath(args.out, ROOT)}")
        return
    sample, cells = draw(issues, args.n, args.seed)
    rec = {"_comment": "The verification sample (plan section 6.1; Heejin, 14 October 2026): 150 assembled issues, stratified by decade "
                       "and genre, drawn once with a fixed seed by scripts/draw_verification_sample.py; read on the workbench and marked "
                       "verified record by record; every fifth issue in the reading order is read by a second reader too. The "
                       "assembly's accuracy is s11_corrections_audit.py --sample over these issues.",
           "drawn": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": args.seed, "n": len(sample), "population": len(issues),
           "strata": "decade x genre class (the plan's eight; format unknown in the archive's metadata)", "min_per_cell": MIN_PER_CELL,
           "second_reader_every": SECOND_READER_EVERY, "cells": cells, "issues": sample}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(rec, open(args.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    dec = Counter(i["decade"] for i in sample); gen = Counter(i["genre"] for i in sample)
    print(f"{len(sample)} issues drawn from {len(issues)} (seed {args.seed}); {len(cells)} cells; second reader on {sum(i['second_reader'] for i in sample)}")
    print("by decade:", ", ".join(f"{k} {v}" for k, v in sorted(dec.items())))
    print("by genre:", ", ".join(f"{k} {v}" for k, v in gen.most_common()))
    print("first ten in reading order:", ", ".join(f"{i['id']}" for i in sample[:10]))
    print("written", os.path.relpath(args.out, ROOT))


if __name__ == "__main__":
    main()
