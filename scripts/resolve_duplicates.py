#!/usr/bin/env python3
"""Resolve the issues the archive holds twice (D-5; 4 November 2026) — one scan per issue, editions set aside.

Reads the issue pairs that scripts/duplicate_items.py found from the rehearsal's pair table
(data/reuse/corpus_dryrun/duplicates/issue_pairs.json: two issues joined by same-text stories) and sorts every pair
into one of these classes, from the two items' kinds, magazine names and dates:

  A  an edition (a British, Canadian or Australian edition) of an issue in the corpus — the edition is set aside,
     the original stays ("editions consolidated under the original": the registered design treats reprints under the
     earliest witness, main.tex 3.1; a whole-issue edition is the same issue again, not reuse);
  B  the same issue under two identifiers (the magazine's name spelt two ways, the date given two ways, or the same
     entry twice) — one scan is kept, the other set aside as a duplicate scan;
  C  the same contents under two dates that cannot both be right — listed for a reader to decide, not resolved here;
  D  a fragment of one or two stories cut out of a fuller issue — set aside as an excerpt of a magazine (the existing
     class) when its stories are in the fuller issue;
  E  a partial overlap between two issues of different dates — a reprint; nothing is done here (the reuse stage
     consolidates reprints under the earliest witness);
  K  a pair that involves a book, a bound volume or an item already set aside — nothing to do.

Which scan of a B pair is kept, in this order: the one in the frozen verification sample (config/verification_sample.json);
the one with records a person verified or modified; the one with more stories; the one with more pages read; the one
with a full cover date over a year or no date; the one whose first scan is the cover (data/corpus/.../cover_survey.json);
the lexicographically first id. The same rule orders a group of three.

    python3 scripts/resolve_duplicates.py --plan                 # write the plan and the report, change nothing
    python3 scripts/resolve_duplicates.py --apply --decided-by "Sujin Kang" --decided 2026-11-04
    python3 scripts/resolve_duplicates.py --selftest

--plan writes data/corpus/<run>/duplicates_plan.json and pilot_export/duplicates_plan.md (the C list for the reader).
--apply adds the A, B and D decisions to config/corpus_set_aside.json (classes "duplicate scan of an issue in the
corpus", "another edition of an issue in the corpus", "excerpt of a magazine"; each entry names the issue it stands
for in `duplicate_of`), skipping ids already set aside; the approved list is untouched; corpus_lib.set_aside() then
keeps every stage, the explorer and the datasheet off them. A C pair is applied only when the plan file carries a
`decision` for it ("keep_a", "keep_b" or "both"), written by hand. Safe to run again.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

PAIRS = os.path.join(ROOT, "data", "reuse", "corpus_dryrun", "duplicates", "issue_pairs.json")
RUN = os.path.join(ROOT, "data", "corpus", "p52-2026-10-16")
SET_ASIDE = os.path.join(ROOT, "config", "corpus_set_aside.json")
KINDS = os.path.join(ROOT, "config", "corpus_kinds.json")
SAMPLE = os.path.join(ROOT, "config", "verification_sample.json")
EXPLORER = os.path.join(ROOT, "data", "explorer.sqlite")
EDITION = re.compile(r"\b(british|canadian|australian)\b", re.I)
CLASS_DUP = "duplicate scan of an issue in the corpus"
CLASS_ED = "another edition of an issue in the corpus"
CLASS_EXCERPT = "excerpt of a magazine"


def _json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def date_relation(a, b):
    a = "" if (a or "").lower() in ("", "undated", "none") else a
    b = "" if (b or "").lower() in ("", "undated", "none") else b
    if a == b and a:
        return "same"
    if not a or not b:
        return "one undated"
    if a[:4] == b[:4] and (len(a) == 4 or len(b) == 4 or a[:7] == b[:7]):
        return "compatible"
    return "different"


def classify(r, kind_of, aside):
    """The class letter of one pair and its reason."""
    ia, ib = r["issue_a"], r["issue_b"]
    if kind_of.get(ia, "magazine") != "magazine" or kind_of.get(ib, "magazine") != "magazine" or ia in aside or ib in aside:
        return "K", "a book, a bound volume or an item already set aside"
    ea = bool(EDITION.search(r.get("magazine_a") or ""))
    eb = bool(EDITION.search(r.get("magazine_b") or ""))
    joined = min(r["stories_joined_a"], r["stories_joined_b"])
    small, large = min(r["stories_a"], r["stories_b"]), max(r["stories_a"], r["stories_b"])
    rel = date_relation(r.get("date_a"), r.get("date_b"))
    if ea != eb:
        return "A", "one item's magazine name says British, Canadian or Australian edition"
    if small <= 2 and large >= 4 and joined >= small:
        return "D", f"a fragment of {small} story(ies) whose text is in an issue of {large}"
    if rel != "different" and r["share_of_smaller"] >= 0.5:
        return "B", f"dates {rel}; {joined} of the smaller issue's {small} stories are the same text"
    if rel == "different" and r["share_of_smaller"] >= 0.8 and joined >= 4:
        return "C", f"the same {joined} stories under {r.get('date_a')} and {r.get('date_b')}"
    return "E", f"dates {rel}; {joined} of {small} stories shared — a reprint, for the reuse stage"


def issue_facts(ids):
    """Per issue: sample membership, verified/modified counts, stories, pages, date form, cover — from the explorer
    database, the sample file and the cover survey; zeros when a source is missing."""
    facts = {i: {"in_sample": 0, "verified": 0, "modified": 0, "stories": 0, "pages": 0, "date_form": 0, "cover": 0} for i in ids}
    sample = {s["id"] for s in _json(SAMPLE, {}).get("issues", [])}
    for i in ids:
        facts[i]["in_sample"] = int(i in sample)
    cover = _json(os.path.join(RUN, "cover_survey.json"), {})
    cov = cover.get("issues", cover) if isinstance(cover, dict) else {}
    for i in ids:
        c = cov.get(i) if isinstance(cov, dict) else None
        kind = c.get("first_scan") if isinstance(c, dict) else c
        facts[i]["cover"] = int(kind == "cover")
    if os.path.exists(EXPLORER):
        con = sqlite3.connect(f"file:{EXPLORER}?mode=ro", uri=True)
        for i in ids:
            row = con.execute("SELECT stories, pages, verified, modified, cover_date FROM issues WHERE id=?", (i,)).fetchone()
            if row:
                facts[i]["stories"] = row[0] or 0
                facts[i]["pages"] = row[1] or 0
                facts[i]["verified"] = row[2] or 0
                facts[i]["modified"] = row[3] or 0
                d = row[4] or ""
                facts[i]["date_form"] = 2 if len(d) >= 7 else (1 if d else 0)
        con.close()
    return facts


def keep_order(ids, facts):
    """The ids of a same-issue group, the one to keep first."""
    def key(i):
        f = facts[i]
        return (-f["in_sample"], -(f["verified"] + f["modified"]), -f["stories"], -f["pages"], -f["date_form"], -f["cover"], i)
    return sorted(ids, key=key)


def groups_of(pairs):
    adj = defaultdict(set)
    for r in pairs:
        adj[r["issue_a"]].add(r["issue_b"])
        adj[r["issue_b"]].add(r["issue_a"])
    seen, out = set(), []
    for n in sorted(adj):
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj[x])
        seen |= comp
        out.append(sorted(comp))
    return out


def plan(pairs_path=PAIRS):
    d = _json(pairs_path, {})
    pairs = d.get("issue_pairs", [])
    kinds = _json(KINDS, {}).get("kinds", {})
    kind_of = {k: (v.get("kind") if isinstance(v, dict) else v) for k, v in kinds.items()}
    aside = set(e["id"] for e in _json(SET_ASIDE, {}).get("issues", []))
    classed = []
    for r in pairs:
        c, why = classify(r, kind_of, aside)
        classed.append(dict(r, cls=c, why=why))
    ids = sorted({r["issue_a"] for r in classed} | {r["issue_b"] for r in classed})
    facts = issue_facts(ids)
    decisions = []
    # B: groups, one kept
    for g in groups_of([r for r in classed if r["cls"] == "B"]):
        order = keep_order(g, facts)
        for i in order[1:]:
            decisions.append({"id": i, "class": CLASS_DUP, "duplicate_of": order[0], "cls": "B",
                              "reason": f"the same issue as {order[0]} under another identifier; that scan kept ("
                                        + ", ".join(k for k in ("in_sample", "verified", "modified") if facts[order[0]][k]) + ")"})
    # A: the edition set aside; the original = the other item
    for r in classed:
        if r["cls"] == "A":
            ed, orig = (r["issue_a"], r["issue_b"]) if EDITION.search(r.get("magazine_a") or "") else (r["issue_b"], r["issue_a"])
            decisions.append({"id": ed, "class": CLASS_ED, "duplicate_of": orig, "cls": "A",
                              "reason": f"a whole-issue edition of {orig} ({r.get('magazine_a')} / {r.get('magazine_b')})"})
    # D: the fragment set aside as an excerpt
    for r in classed:
        if r["cls"] == "D":
            frag, full = (r["issue_a"], r["issue_b"]) if r["stories_a"] <= r["stories_b"] else (r["issue_b"], r["issue_a"])
            decisions.append({"id": frag, "class": CLASS_EXCERPT, "duplicate_of": full, "cls": "D",
                              "reason": f"{min(r['stories_a'], r['stories_b'])} story(ies) cut out of {full}, which is in the corpus"})
    # de-duplicate decisions (an id may appear in several pairs): the first class wins; an item of the frozen
    # verification sample is never set aside by this script (it would have to be redrawn): listed for attention
    seen, uniq, attention = set(), [], []
    for x in decisions:
        if x["id"] in seen or x["id"] == x["duplicate_of"]:
            continue
        seen.add(x["id"])
        if facts[x["id"]]["in_sample"]:
            attention.append(dict(x, note="in the frozen verification sample: not set aside; decide by hand"))
            continue
        uniq.append(x)
    counts = Counter(r["cls"] for r in classed)
    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "source": os.path.relpath(pairs_path, ROOT), "pairs": len(classed),
           "by_class": dict(sorted(counts.items())), "set_aside_planned": Counter(x["class"] for x in uniq),
           "decisions": uniq, "attention": attention, "to_decide": [r for r in classed if r["cls"] == "C"],
           "reprints_left_to_the_reuse_stage": [r for r in classed if r["cls"] == "E"], "pairs_classed": classed, "facts": facts}
    return out


def report(p):
    L = [f"# Duplicate items — the plan of {p['generated']}", "",
         f"{p['pairs']} issue pairs from {p['source']}. By class: " + ", ".join(f"{k} {v}" for k, v in p["by_class"].items()) + ".",
         "", "| set aside as | items |", "|---|---|"] + [f"| {k} | {v} |" for k, v in p["set_aside_planned"].items()]
    L += ["", "## C — the same contents under two dates: a reader decides (write `decision`: keep_a, keep_b or both)", "",
          "| issue A | issue B | date A | date B | stories | joined |", "|---|---|---|---|---|---|"]
    for r in p["to_decide"]:
        L.append(f"| {r['issue_a']} | {r['issue_b']} | {r['date_a']} | {r['date_b']} | {r['stories_a']}/{r['stories_b']} | "
                 f"{r['stories_joined_a']}/{r['stories_joined_b']} |")
    if p.get("attention"):
        L += ["", "## Needs attention — in the verification sample, not set aside", ""] + [f"- {x['id']} ({x['cls']}, stands for {x['duplicate_of']})" for x in p["attention"]]
    L += ["", "## E — reprints between issues of different dates (nothing done here)", "",
          "| issue A | issue B | date A | date B | shared |", "|---|---|---|---|---|"]
    for r in p["reprints_left_to_the_reuse_stage"]:
        L.append(f"| {r['issue_a']} | {r['issue_b']} | {r['date_a']} | {r['date_b']} | {min(r['stories_joined_a'], r['stories_joined_b'])} |")
    L += ["", "## Decisions", "", "| set aside | class | stands for | why |", "|---|---|---|---|"]
    for x in p["decisions"]:
        L.append(f"| {x['id']} | {x['cls']} | {x['duplicate_of']} | {x['reason']} |")
    return "\n".join(L) + "\n"


def apply(p, decided_by, decided, path=SET_ASIDE, dry_run=False):
    d = _json(path, {"issues": []})
    have = {e["id"] for e in d.get("issues", [])}
    added, skipped = [], []
    decisions = list(p["decisions"])
    for r in p.get("to_decide", []):
        dec = r.get("decision")
        if dec in ("keep_a", "keep_b"):
            gone, kept = (r["issue_b"], r["issue_a"]) if dec == "keep_a" else (r["issue_a"], r["issue_b"])
            decisions.append({"id": gone, "class": CLASS_DUP, "duplicate_of": kept, "cls": "C",
                              "reason": f"the same contents as {kept}; the date decided by hand ({dec})"})
    for x in decisions:
        if x["id"] in have:
            skipped.append(x["id"])
            continue
        have.add(x["id"])
        d["issues"].append({"id": x["id"], "class": x["class"], "reason": x["reason"], "duplicate_of": x["duplicate_of"],
                            "decided_by": decided_by, "decided": decided, "source": "scripts/resolve_duplicates.py"})
        added.append(x["id"])
    if not dry_run:
        d["written"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return added, skipped


def selftest():
    kind_of = {"book_x": "book"}
    aside = set()
    base = dict(stories_a=8, stories_b=8, stories_joined_a=8, stories_joined_b=8, share_of_smaller=1.0, magazine_a="Galaxy", magazine_b="Galaxy Magazine (May")
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b="1952-05"), kind_of, aside)[0] == "B"
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b="1952"), kind_of, aside)[0] == "B"
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b=None), kind_of, aside)[0] == "B"
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b="undated"), kind_of, aside)[0] == "B"
    assert date_relation("undated", "undated") == "one undated"
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b="1953-03"), kind_of, aside)[0] == "C"
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b="1953-03", share_of_smaller=0.6, stories_joined_a=5, stories_joined_b=5), kind_of, aside)[0] == "E"
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b="1952-05", magazine_b="Galaxy British Ed."), kind_of, aside)[0] == "A"
    assert classify(dict(base, issue_a="g1", issue_b="g2", date_a="1952-05", date_b="1952-05", stories_b=2, stories_joined_a=2, stories_joined_b=2), kind_of, aside)[0] == "D"
    assert classify(dict(base, issue_a="book_x", issue_b="g2", date_a="1952", date_b="1952"), kind_of, aside)[0] == "K"
    facts = {"a": {"in_sample": 0, "verified": 0, "modified": 0, "stories": 8, "pages": 130, "date_form": 2, "cover": 1},
             "b": {"in_sample": 1, "verified": 0, "modified": 0, "stories": 7, "pages": 128, "date_form": 2, "cover": 1},
             "c": {"in_sample": 0, "verified": 0, "modified": 0, "stories": 8, "pages": 131, "date_form": 1, "cover": 0}}
    assert keep_order(["a", "b", "c"], facts) == ["b", "c", "a"]       # the sample first, then more pages
    assert groups_of([{"issue_a": "a", "issue_b": "b"}, {"issue_a": "b", "issue_b": "c"}, {"issue_a": "x", "issue_b": "y"}]) == [["a", "b", "c"], ["x", "y"]]
    print("selftest ok")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=PAIRS)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan-file", default=os.path.join(RUN, "duplicates_plan.json"))
    ap.add_argument("--report", default=os.path.join(ROOT, "pilot_export", "duplicates_plan.md"))
    ap.add_argument("--decided-by", default="")
    ap.add_argument("--decided", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.plan:
        p = plan(a.pairs)
        os.makedirs(os.path.dirname(a.plan_file), exist_ok=True)
        json.dump(p, open(a.plan_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.makedirs(os.path.dirname(a.report), exist_ok=True)
        open(a.report, "w", encoding="utf-8").write(report(p))
        print(f"pairs {p['pairs']}; by class {p['by_class']}; to set aside {dict(p['set_aside_planned'])}; "
              f"to decide by hand {len(p['to_decide'])}; written {os.path.relpath(a.plan_file, ROOT)} and {os.path.relpath(a.report, ROOT)}")
    if a.apply:
        if not a.decided_by:
            sys.exit("--apply needs --decided-by")
        p = _json(a.plan_file, None)
        if not p:
            sys.exit(f"no plan at {a.plan_file}: run --plan first")
        added, skipped = apply(p, a.decided_by, a.decided, dry_run=a.dry_run)
        print(f"set aside: {len(added)} added, {len(skipped)} already set aside" + (" (dry run: nothing written)" if a.dry_run else ""))


if __name__ == "__main__":
    main()
