#!/usr/bin/env python3
"""s04b — part-of-speech tags and lemmas for the cleaned text (protocol section 3.2: the lexical tier compares
lemmas; the tokens stay aligned to the printed text so every match can be shown on the page).

Input : data/text/<id>/rules_routeA/page_NNNN.txt      (s04's cleaned pages)
Output: data/text/<id>/lemma_routeA.jsonl.gz            one line per page:
          {"page": 5, "n": 412, "tokens": [[text, lemma, pos, start_char], ...]}
        the first line is a header: {"header": true, "model": "en_core_web_sm", "version": "3.8.0", "spacy": ...}

spaCy's small English model, pinned in config/corpus_settings.json (lemma.model, lemma.model_version); the parser
and the named-entity recogniser are switched off (the lemmatizer needs only the tagger). The pinned version is
checked at start: a different model version is refused, because lemmas would then differ between issues. The
lemmatizer is rule-based and consistent rather than always right ("riding" becomes "rid"); as both sides of every
comparison are lemmatized the same way, a wrong but consistent lemma does not lose a match.

    python3 pipeline/s04b_lemma.py --issue <id>
    python3 pipeline/s04b_lemma.py --all              # every issue of the list (PULP_ISSUES=... for the corpus)
    python3 pipeline/s04b_lemma.py --selftest
"""
import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_lib import ROOT, settings, issues_config, log  # noqa: E402
from timing_util import stage_timer  # noqa: E402

_NLP = None


def nlp():
    """The pipeline, loaded once per process and checked against the pin."""
    global _NLP
    if _NLP is None:
        import spacy
        cfg = settings()["lemma"]
        _NLP = spacy.load(cfg["model"], disable=cfg.get("disable", []))
        got = _NLP.meta.get("version")
        if cfg.get("model_version") and got != cfg["model_version"]:
            sys.exit(f"[s04b] {cfg['model']} is version {got}, the settings pin {cfg['model_version']}: "
                     f"install the pinned version (pip install {cfg['model']}=={cfg['model_version']} from spaCy's release URL) "
                     f"or change the pin on purpose and record it.")
        _NLP.max_length = 2_000_000
    return _NLP


def tag_pages(pages):
    """pages: list of (page_no, text) -> list of page records."""
    out = []
    texts = [t for _n, t in pages]
    for (pno, _t), doc in zip(pages, nlp().pipe(texts, batch_size=16)):
        toks = [[t.text, t.lemma_, t.pos_, t.idx] for t in doc if not t.is_space]
        out.append({"page": pno, "n": len(toks), "tokens": toks})
    return out


def load_pages(iid, src="routeA"):
    d = os.path.join(ROOT, "data", "text", iid, f"rules_{src}")
    if not os.path.isdir(d):
        return None
    pages = []
    for f in sorted(os.listdir(d)):
        if f.startswith("page_") and f.endswith(".txt"):
            pages.append((int(f[5:9]), open(os.path.join(d, f), encoding="utf-8").read()))
    return pages


def run_issue(iid, src="routeA"):
    pages = load_pages(iid, src)
    if pages is None:
        log("s04b", f"{iid}: no rules_{src} pages, skipped")
        return None
    out = os.path.join(ROOT, "data", "text", iid, f"lemma_{src}.jsonl.gz")
    cfg = settings()["lemma"]
    import spacy
    with stage_timer("s04b_lemma", iid, pages=len(pages), extra={"src": src}):
        recs = tag_pages(pages)
        tmp = out + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(json.dumps({"header": True, "model": cfg["model"], "version": nlp().meta.get("version"),
                                "spacy": spacy.__version__, "disabled": cfg.get("disable", []), "src": src}) + "\n")
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, out)
    n = sum(r["n"] for r in recs)
    log("s04b", f"{iid}: {len(pages)} pages, {n:,} tokens -> {os.path.relpath(out, ROOT)}")
    return {"pages": len(pages), "tokens": n}


def read_lemmas(iid, src="routeA"):
    """Reader for the analysis stages: yields the page records (the header line is skipped)."""
    p = os.path.join(ROOT, "data", "text", iid, f"lemma_{src}.jsonl.gz")
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not r.get("header"):
                yield r


def selftest():
    recs = tag_pages([(1, "The cowboys were riding faster than the wolves ran.\n\nShe had seen better days."), (2, "")])
    assert recs[0]["page"] == 1 and recs[0]["n"] > 10 and recs[1]["n"] == 0
    lem = {t[0]: t[1] for t in recs[0]["tokens"]}
    # the small model's lemmatizer is rule-based: consistent rather than always right ("riding" comes out as "rid").
    # Both sides of every comparison are lemmatized the same way, so matching is unaffected; it is recorded here.
    assert lem["cowboys"] == "cowboy" and lem["were"] == "be" and lem["ran"] == "run" and lem["wolves"] == "wolf", lem
    pos = {t[0]: t[2] for t in recs[0]["tokens"]}
    assert pos["riding"] == "VERB" and pos["cowboys"] == "NOUN", pos
    assert all(isinstance(t[3], int) for t in recs[0]["tokens"])
    print("s04b selftest ok:", nlp().meta.get("name"), nlp().meta.get("version"), "pipes", nlp().pipe_names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--src", default="routeA")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest(); return
    ids = [i["id"] for i in issues_config()["issues"]]
    if args.issue:
        ids = [args.issue]
    elif not args.all:
        sys.exit("pass --all, --issue <id> or --selftest")
    for iid in ids:
        run_issue(iid, args.src)


if __name__ == "__main__":
    main()
