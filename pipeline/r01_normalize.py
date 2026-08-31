#!/usr/bin/env python3
"""r01 — conservative normalization for text reuse (protocol section 3.1).

The protocol: "lowercase and normalize for encoding errors, whitespace,
hyphenation, punctuation, and typography. No stemming, lemmatization, or
stopword removal." Verbatim reuse is then lexical identity.

How that is realized here, so every later number is reproducible:

  prepare(text)   → the canonical display text: soft hyphens removed,
                    words split across line breaks rejoined, typographic
                    quotes and dashes straightened, whitespace collapsed.
  tokenize(text)  → word tokens with character offsets INTO that canonical
                    text, each carrying a folded form (NFKC + lowercase,
                    ligatures expanded, apostrophes unified). Punctuation
                    is not a token, so identity ignores it; digits and
                    apostrophe-internal words ("don't") survive.

Matching always compares folded forms; display always uses the offsets,
so a matched passage can be shown exactly as it stands in the story.
"""
import re
import unicodedata

_SOFT_HYPHEN = "­"
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'",
           "“": '"', "”": '"', "„": '"', "‟": '"',
           "′": "'", "″": '"'}
_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-",
           "—": "-", "―": "-", "−": "-"}
_TRANS = str.maketrans({**_QUOTES, **_DASHES})

# a word broken at a line end: "moth-\n er" → "mother"; kept conservative:
# both halves must be letters and the second half must start lowercase
_LINE_HYPHEN = re.compile(r"([A-Za-z])-[ \t]*\n[ \t]*([a-z])")
_WS = re.compile(r"\s+")

# a token is a run of letters/digits, allowing internal apostrophes
TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)


def prepare(text):
    """Canonical text: the story as it will be displayed and indexed."""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace(_SOFT_HYPHEN, "")
    t = t.translate(_TRANS)
    t = _LINE_HYPHEN.sub(r"\1\2", t)
    t = _WS.sub(" ", t).strip()
    return t


def fold(token):
    """The comparison form of one token."""
    return token.lower()


def tokenize(canon):
    """[(folded, start, end)] over an already-prepared canonical text."""
    out = []
    for m in TOKEN_RE.finditer(canon):
        out.append((fold(m.group(0)), m.start(), m.end()))
    return out


HONORIFICS = {"captain", "capt", "dr", "prof", "professor", "mr", "mrs", "miss",
              "lieut", "lieutenant", "major", "col", "colonel", "sgt", "rev", "by"}


def author_key(name):
    """Comparison key for a printed by-line, or None when unusable ("THE
    EDITOR", "Author of ...", initials only). Pulp pseudonyms are NOT
    resolved here (implementation plan 0.4): this is the verbatim-name
    state with an explicit unknown. Shared by r05 and the website."""
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


def story_units(record):
    """Canonical text and token arrays for one exported story record."""
    canon = prepare(record.get("text", ""))
    toks = tokenize(canon)
    return {
        "story_id": record["story_id"],
        "canon": canon,
        "tokens": [t[0] for t in toks],
        "offsets": [(t[1], t[2]) for t in toks],
    }


if __name__ == "__main__":
    demo = "The “iron door” swung—slow-\nly, and don’t you forget it.  ﬁne."
    c = prepare(demo)
    print(c)
    print(tokenize(c))
