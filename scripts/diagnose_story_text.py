#!/usr/bin/env python3
"""What is in the story records that is not story — read from the rehearsal's matches (4 November 2026, for rules 2.14).

The rehearsal (data/reuse/corpus_dryrun/, 27–28 October) found 536,526 cross-issue matches of ten tokens or more at
seed 6. Most are not reuse in the study's sense: the same text present twice, and matter that recurs inside one
magazine. This script reads every written match (machine_k6_matches.jsonl: the two stories, the token spans, a
300-character excerpt), the stories' metadata (tokens/stories.jsonl) and the export records (data/export/corpus/
*.jsonl: work_id, serial, department) and sorts each match by where it sits in the two stories and what links them:

  whole      the match covers 80 % or more of the shorter story — a duplicate scan, an edition or a reprint
             (resolved by D-5 and by the reuse stage's consolidation; not an assembly matter)
  block      the same excerpt (its first 120 characters, 20 tokens or more) occurs in 5 or more distinct stories of
             2 or more issues — a standing block: a subscription form, a running line, a publisher's address
  synopsis   the match starts in the first 250 tokens of the later story and the two stories are instalments of one
             work (work_id), or of one magazine where either carries a serial part label — a recap the assembly did
             not set apart
  head       the match starts in the first 250 tokens of the later story, no serial link — a repeated blurb, an
             editor's introduction, a department head
  foot       the match ends in the last 250 tokens of either story and is under 400 tokens — a block at the foot
             of a record: an advertisement, a house notice, a department that followed the story
  middle     everything else — the candidates for reuse proper

and, apart from the class, records for every match where it sits in each story (head / inside / foot) and what links
the two (same work, same magazine, other), so that the report can show the cross-table the rules are written from.
Matches that involve an item set aside since (config/corpus_set_aside.json — the duplicate scans and editions of D-5) are
left out, so the counts describe the corpus as it now stands. Each standing block is tagged by family (the postal
statement of ownership, an advertisement, a department, other). It also lists the printed author strings that do not
look like a person's name (for the by-line guard). It writes
pilot_export/story_text_diagnosis.md and data/reuse/corpus_dryrun/story_text_diagnosis.json. Nothing else is changed.

    python3 scripts/diagnose_story_text.py [--snap data/reuse/corpus_dryrun] [--export data/export/corpus] [--selftest]
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "data", "reuse", "corpus_dryrun")
EXPORT = os.path.join(ROOT, "data", "export", "corpus")
SET_ASIDE = os.path.join(ROOT, "config", "corpus_set_aside.json")
OWNERSHIP_RE = re.compile(r"statement of (?:the )?ownership|postal laws and regulations|act of (?:august 24, 1912|march 3, 1933|october 23, 1962)|"
                          r"bondholders, mortgagees|stockholders and security holders|sworn to and subscribed|notary public", re.I)
AD_RE = re.compile(r"\b(?:send|mail|free|coupon|dept\.|department [a-z0-9]|address|write (?:to|for)|money back|guarantee|\$\d|cents?|"
                   r"postage|agents wanted|catalog|booklet|trial|no obligation|inc\.|co\.|corp\.|ltd\.)\b", re.I)
HEAD_TOK = 250
FOOT_TOK = 250
FOOT_MAX = 400
BLOCK_MIN_STORIES = 5
BLOCK_MIN_ISSUES = 2
BLOCK_MIN_TOKENS = 20
WHOLE = 0.8
NAME_WORDS_MAX = 5


def load_stories(snap):
    out = {}
    with open(os.path.join(snap, "tokens", "stories.jsonl"), encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            m = s.get("meta") or {}
            out[s["story_id"]] = {"issue": s["issue"], "n": s["n_tokens"], "magazine": m.get("magazine"), "date": m.get("cover_date") or "",
                                  "title": m.get("title"), "author": m.get("author")}
    return out


def load_links(export_dir, wanted):
    """story_id -> (work_id, serial part label, department) from the export files; only the stories wanted."""
    links = {}
    paths = sorted(glob.glob(os.path.join(export_dir, "*.jsonl")))
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if '"type": "story"' not in line and '"type":"story"' not in line:
                    continue
                i = line.find('"story_id"')
                if i < 0:
                    continue
                m = re.match(r'"story_id":\s*"([^"]+)"', line[i:])
                if not m or m.group(1) not in wanted:
                    continue
                r = json.loads(line)
                ser = r.get("serial") or {}
                links[r["story_id"]] = (r.get("work_id"), ser.get("part_label") if isinstance(ser, dict) else None, r.get("department"))
    return links


def norm_excerpt(t):
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (t or "").lower())).strip()[:120]


def side(tok, n):
    """Where a span sits in a story: head (starts in the first HEAD_TOK tokens), foot (ends in the last FOOT_TOK), inside."""
    if tok[0] < HEAD_TOK:
        return "head"
    if tok[1] > n - FOOT_TOK:
        return "foot"
    return "inside"


def relation(a, b, S, L):
    la, lb = L.get(a, (None, None, None)), L.get(b, (None, None, None))
    if la[0] and la[0] == lb[0]:
        return "same work"
    if S[a]["magazine"] == S[b]["magazine"]:
        return "same magazine, serial part" if (la[1] or lb[1]) else "same magazine"
    return "other"


def classify(m, S, L, block_keys):
    a, b = m["a"], m["b"]
    sa, sb = S[a], S[b]
    n_min = min(sa["n"], sb["n"])
    ln = m["len"]
    if n_min and ln >= WHOLE * n_min:
        return "whole"
    if norm_excerpt(m.get("excerpt")) in block_keys:
        return "block"
    # the later story: by date, then by id
    later_is_a = (sa["date"], a) > (sb["date"], b)
    lt = m["a_tok"] if later_is_a else m["b_tok"]
    if lt[0] < HEAD_TOK:
        rel = relation(a, b, S, L)
        return "synopsis" if rel in ("same work", "same magazine, serial part") else "head"
    if ln < FOOT_MAX and (m["a_tok"][1] > sa["n"] - FOOT_TOK or m["b_tok"][1] > sb["n"] - FOOT_TOK):
        return "foot"
    return "middle"


NAME_STOP = {"a", "an", "the", "of", "and", "novel", "novelette", "novelet", "story", "stories", "serial", "complete", "new", "old",
             "next", "month", "months", "issue", "department", "editor", "editors", "readers", "reader", "club", "letters", "news",
             "where", "when", "what", "who", "how", "why", "director", "physical", "scout", "lieutenant", "captain", "major", "sergeant",
             "dr", "mr", "mrs", "miss", "by", "from", "for", "with", "in", "on", "to", "at", "is", "are", "was", "this", "that", "our",
             "your", "all", "true", "famous", "popular", "author", "authors", "anonymous", "staff", "special", "feature", "thrilling"}


def looks_like_name(a):
    """A loose test for a person's name as printed: each part (split at 'and' / '&') has 1–5 words and at least one
    capitalised word that is not a stop word or a title word; no digits; an initial ("A.") is not the article "A"; no
    'of'/'where'/'next month' constructions."""
    if not a or re.search(r"\d", a):
        return False
    parts = [p for p in re.split(r"\s+(?:and|&)\s+", a.strip()) if p.strip()]
    if not parts:
        return False
    for part in parts:
        ws = re.sub(r"[^A-Za-z.'\- ]", " ", part).split()
        if not ws or len(ws) > NAME_WORDS_MAX:
            return False
        low = [w.strip(".'").lower() for w in ws]
        initial = bool(re.match(r"^[A-Z]\.$", ws[0]))
        if not initial and low[0] in ("a", "an", "the", "where", "when", "what", "how", "news", "next"):
            return False
        content = [w for w, l in zip(ws, low) if l not in NAME_STOP and len(l) >= 2]
        if not content or not any(w[:1].isupper() for w in content):
            return False
    return True


def block_family(excerpt):
    t = excerpt or ""
    if OWNERSHIP_RE.search(t):
        return "ownership statement"
    if AD_RE.search(t):
        return "advertisement"
    if re.search(r"camp-fire|the editor|readers|letters|our readers|department|next month|next issue|coming next|in this issue", t, re.I):
        return "department / house"
    return "other"


def diagnose(snap=SNAP, export_dir=EXPORT, log=print):
    t0 = time.time()
    S = load_stories(snap)
    path = os.path.join(snap, "machine_k6_matches.jsonl")
    matches = [json.loads(l) for l in open(path, encoding="utf-8")]
    n_all = len(matches)
    try:
        aside = {e["id"] for e in json.load(open(SET_ASIDE, encoding="utf-8")).get("issues", [])}
    except (OSError, ValueError):
        aside = set()
    matches = [m for m in matches if S[m["a"]]["issue"] not in aside and S[m["b"]]["issue"] not in aside]
    log(f"{n_all:,} matches, {len(matches):,} between items still in the corpus ({len(aside)} items set aside), {len(S):,} stories ({time.time() - t0:.0f}s)")
    wanted = {m["a"] for m in matches} | {m["b"] for m in matches}
    L = load_links(export_dir, wanted) if os.path.isdir(export_dir) else {}
    log(f"links for {len(L):,} of {len(wanted):,} stories ({time.time() - t0:.0f}s)")
    # standing blocks: the same excerpt in many distinct stories
    by_key, by_key_issues = defaultdict(set), defaultdict(set)
    key_example, key_mags, key_len = {}, defaultdict(Counter), {}
    for m in matches:
        k = norm_excerpt(m.get("excerpt"))
        if not k or m["len"] < BLOCK_MIN_TOKENS:
            continue
        by_key[k].add(m["a"])
        by_key[k].add(m["b"])
        by_key_issues[k].add(S[m["a"]]["issue"])
        by_key_issues[k].add(S[m["b"]]["issue"])
        key_example.setdefault(k, (m.get("excerpt") or "")[:200])
        key_len[k] = max(key_len.get(k, 0), m["len"])
        key_mags[k][S[m["a"]]["magazine"]] += 1
        key_mags[k][S[m["b"]]["magazine"]] += 1
    block_keys = {k for k, st in by_key.items() if len(st) >= BLOCK_MIN_STORIES and len(by_key_issues[k]) >= BLOCK_MIN_ISSUES}
    counts, lens, cases = Counter(), defaultdict(list), defaultdict(list)
    cross = Counter()           # (class, side of the earlier story, side of the later story, relation)
    rng = random.Random(20261104)
    classes = []
    for m in matches:
        c = classify(m, S, L, block_keys)
        classes.append(c)
        counts[c] += 1
        lens[c].append(m["len"])
        a, b = m["a"], m["b"]
        later_is_a = (S[a]["date"], a) > (S[b]["date"], b)
        e_tok, e_n, l_tok, l_n = (m["b_tok"], S[b]["n"], m["a_tok"], S[a]["n"]) if later_is_a else (m["a_tok"], S[a]["n"], m["b_tok"], S[b]["n"])
        cross[(c, side(e_tok, e_n), side(l_tok, l_n), relation(a, b, S, L))] += 1
    for c in counts:
        pool = [m for m, cc in zip(matches, classes) if cc == c]
        cases[c] = rng.sample(pool, min(40, len(pool)))           # a random sample, not the longest
    bins = ((10, 19), (20, 49), (50, 99), (100, 499), (500, 10 ** 9))
    len_bins = {c: {f"{lo}-{hi if hi < 10 ** 9 else ''}": sum(1 for x in v if lo <= x <= hi) for lo, hi in bins} for c, v in lens.items()}
    blocks = sorted(({"stories": len(by_key[k]), "issues": len(by_key_issues[k]), "longest": key_len[k], "family": block_family(key_example[k]),
                      "magazines": dict(key_mags[k].most_common(5)), "excerpt": key_example[k]}
                     for k in block_keys), key=lambda x: -x["stories"])
    fam = Counter()
    fam_stories = defaultdict(set)
    for b in blocks:
        fam[b["family"]] += 1
    for k in block_keys:
        fam_stories[block_family(key_example[k])] |= by_key[k]
    families = {f: {"blocks": n, "stories": len(fam_stories[f])} for f, n in fam.most_common()}
    # printed author strings that do not look like a name (over the whole story set, not only the matched stories)
    auth = Counter(s["author"] for s in S.values() if s.get("author"))
    odd_authors = [{"author": a_, "stories": n} for a_, n in auth.most_common() if not looks_like_name(a_)]
    # synopsis cases grouped by magazine; foot cases by magazine
    syn_mag = Counter(S[m["a"]]["magazine"] for m in matches if classify(m, S, L, block_keys) == "synopsis")
    foot_mag = Counter(S[m["a"]]["magazine"] for m in matches if classify(m, S, L, block_keys) == "foot")
    stories_hit = {c: len({x for m in matches if classify(m, S, L, block_keys) == c for x in (m["a"], m["b"])}) for c in counts}
    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "matches": len(matches), "matches_in_file": n_all, "items_set_aside": len(aside),
           "counts": dict(counts), "stories_involved": stories_hit, "length_bins": len_bins, "block_families": families,
           "median_len": {c: sorted(v)[len(v) // 2] for c, v in lens.items()},
           "blocks": blocks, "synopsis_by_magazine": dict(syn_mag.most_common(40)), "foot_by_magazine": dict(foot_mag.most_common(40)),
           "cross": [{"class": k[0], "earlier": k[1], "later": k[2], "relation": k[3], "matches": v} for k, v in cross.most_common()],
           "odd_authors": odd_authors[:400], "odd_authors_total": len(odd_authors), "odd_author_stories": sum(x["stories"] for x in odd_authors),
           "examples": {c: [{"a": m["a"], "b": m["b"], "len": m["len"], "a_tok": m["a_tok"], "b_tok": m["b_tok"], "excerpt": (m.get("excerpt") or "")[:160]}
                            for m in v] for c, v in cases.items()},
           "thresholds": {"head_tok": HEAD_TOK, "foot_tok": FOOT_TOK, "foot_max": FOOT_MAX, "block_min_stories": BLOCK_MIN_STORIES,
                          "block_min_issues": BLOCK_MIN_ISSUES, "block_min_tokens": BLOCK_MIN_TOKENS, "whole": WHOLE}}
    return out


def report(d):
    L = [f"# What is in the story records that is not story — {d['generated']}", "",
         f"{d['matches']:,} cross-issue matches of ten tokens or more (seed 6) from the rehearsal between items still in the corpus "
         f"({d['matches_in_file']:,} in the file; {d['items_set_aside']} items set aside since, D-5), sorted by where they sit.", "",
         "| class | matches | stories involved | median length |", "|---|---|---|---|"]
    for c in ("whole", "block", "synopsis", "head", "foot", "middle"):
        if c in d["counts"]:
            L.append(f"| {c} | {d['counts'][c]:,} | {d['stories_involved'].get(c, 0):,} | {d['median_len'].get(c, 0)} |")
    L += ["", "## Length of the matches, by class", "", "| class | 10–19 | 20–49 | 50–99 | 100–499 | 500+ |", "|---|---|---|---|---|---|"]
    for c in ("whole", "block", "synopsis", "head", "foot", "middle"):
        if c in d["length_bins"]:
            L.append(f"| {c} | " + " | ".join(f"{v:,}" for v in d["length_bins"][c].values()) + " |")
    L += ["", "## Standing-block families", "", "| family | blocks | stories |", "|---|---|---|"]
    for f, v in d["block_families"].items():
        L.append(f"| {f} | {v['blocks']} | {v['stories']:,} |")
    L += ["", "## Where the matches sit (earlier story × later story × what links them)", "",
          "| class | in the earlier story | in the later story | relation | matches |", "|---|---|---|---|---|"]
    for r in d["cross"][:60]:
        L.append(f"| {r['class']} | {r['earlier']} | {r['later']} | {r['relation']} | {r['matches']:,} |")
    L += ["", f"## Standing blocks — the same excerpt ({d['thresholds']['block_min_tokens']}+ tokens) in {d['thresholds']['block_min_stories']}+ stories of {d['thresholds']['block_min_issues']}+ issues ({len(d['blocks'])})", "",
          "| stories | issues | longest | family | magazines | excerpt |", "|---|---|---|---|---|---|"]
    for b in d["blocks"][:100]:
        mags = ", ".join(f"{k} ({v})" for k, v in b["magazines"].items())
        L.append(f"| {b['stories']} | {b['issues']} | {b['longest']} | {b['family']} | {mags} | {b['excerpt'][:140].replace('|', '/')} |")
    L += ["", f"## Printed author strings that do not look like a name ({d['odd_authors_total']} strings on {d['odd_author_stories']:,} stories; the 120 most frequent)", "",
          "| author as printed | stories |", "|---|---|"] + [f"| {x['author'].replace('|', '/')} | {x['stories']} |" for x in d["odd_authors"][:120]]
    L += ["", "## Synopsis cases by magazine", "", "| magazine | matches |", "|---|---|"] + [f"| {k} | {v} |" for k, v in d["synopsis_by_magazine"].items()]
    L += ["", "## Foot blocks by magazine", "", "| magazine | matches |", "|---|---|"] + [f"| {k} | {v} |" for k, v in d["foot_by_magazine"].items()]
    for c in ("synopsis", "head", "foot"):
        L += ["", f"## Examples — {c}", "", "| a | b | len | a tokens | b tokens | excerpt |", "|---|---|---|---|---|---|"]
        for m in d["examples"].get(c, [])[:25]:
            L.append(f"| {m['a']} | {m['b']} | {m['len']} | {m['a_tok']} | {m['b_tok']} | {m['excerpt'][:110].replace('|', '/')} |")
    return "\n".join(L) + "\n"


def selftest():
    S = {"s1": {"issue": "i1", "n": 5000, "magazine": "M", "date": "1930-01", "title": "T", "author": "A"},
         "s2": {"issue": "i2", "n": 5000, "magazine": "M", "date": "1930-02", "title": "T", "author": "A"},
         "s3": {"issue": "i3", "n": 300, "magazine": "N", "date": "1931-02", "title": "T", "author": "A"}}
    L = {"s1": ("w1", "Part 1", None), "s2": ("w1", "Part 2", None)}
    assert classify({"a": "s1", "b": "s2", "len": 150, "a_tok": [4000, 4150], "b_tok": [10, 160], "excerpt": "x"}, S, L, set()) == "synopsis"
    assert classify({"a": "s1", "b": "s2", "len": 150, "a_tok": [4000, 4150], "b_tok": [10, 160], "excerpt": "x"}, S, {}, set()) == "head"
    assert classify({"a": "s1", "b": "s2", "len": 60, "a_tok": [4900, 4960], "b_tok": [2000, 2060], "excerpt": "x"}, S, L, set()) == "foot"
    assert classify({"a": "s1", "b": "s3", "len": 280, "a_tok": [1000, 1280], "b_tok": [10, 290], "excerpt": "x"}, S, L, set()) == "whole"
    assert classify({"a": "s1", "b": "s2", "len": 60, "a_tok": [2000, 2060], "b_tok": [2000, 2060], "excerpt": "Buy now"}, S, L, {norm_excerpt("Buy now")}) == "block"
    assert classify({"a": "s1", "b": "s2", "len": 60, "a_tok": [2000, 2060], "b_tok": [2000, 2060], "excerpt": "x"}, S, L, set()) == "middle"
    assert side([10, 100], 5000) == "head" and side([4900, 4950], 5000) == "foot" and side([2000, 2100], 5000) == "inside"
    assert relation("s1", "s2", S, L) == "same work" and relation("s1", "s3", S, L) == "other"
    assert relation("s1", "s2", S, {"s1": (None, "Part 2", None)}) == "same magazine, serial part"
    for good in ("Robert E. Howard", "H. P. Lovecraft", "Seabury Quinn", "Eando Binder", "Vargo Statten", "Anthony M. Rud", "Zane Grey",
                 "A. E. van Vogt", "A. Merritt", "H. Bedford-Jones and Captain L. B. Williams", "L. SPRAGUE DE CAMP & FLETCHER PRATT"):
        assert looks_like_name(good), good
    for bad in ("A Novel", "An Old Scout", "Physical Director", "Where Old Paths Cross", "News of Next Month's Issue", "The Editor",
                "Famous Authors", "Lieutenant", "", "1936", "THE EDITORS", "I", "page", "THE $4.00 YEAR", "Editor and Readers"):
        assert not looks_like_name(bad), bad
    assert block_family("STATEMENT OF THE OWNERSHIP, MANAGEMENT, CIRCULATION") == "ownership statement"
    assert block_family("Send no money. Dept. 5, 24 Union Square") == "advertisement"
    assert block_family("Our Camp-Fire came into being May 5, 1912") == "department / house"
    assert block_family("he rode into the sunset") == "other"
    print("selftest ok")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=SNAP)
    ap.add_argument("--export", default=EXPORT)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=os.path.join(ROOT, "pilot_export", "story_text_diagnosis.md"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    d = diagnose(a.snap, a.export)
    oj = a.out_json or os.path.join(a.snap, "story_text_diagnosis.json")
    json.dump(d, open(oj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.makedirs(os.path.dirname(a.out_md), exist_ok=True)
    open(a.out_md, "w", encoding="utf-8").write(report(d))
    print(f"counts {d['counts']}; blocks {len(d['blocks'])}; written {os.path.relpath(oj, ROOT)} and {os.path.relpath(a.out_md, ROOT)}")


if __name__ == "__main__":
    main()
