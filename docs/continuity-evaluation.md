# Evaluating story continuity

`scripts/evaluate_continuity.py` compares assembly output with a frozen copy of the
human annotation replay. It measures whether a story's prose remains together and
in order, including across pages. It does not run OCR, call a model, apply human
corrections to candidates, or write to live data.

First freeze the labels from a local copy of the existing live articles,
annotations and layout. Do this before rebuilding any assembly. Every input file
used for the labels is identified by SHA-256. Malformed annotations and missing
layout fail the snapshot rather than silently reducing the gold set.

```sh
python3 scripts/evaluate_continuity.py snapshot --out /path/to/human-gold.json
python3 scripts/evaluate_continuity.py compare \
  --gold /path/to/human-gold.json \
  --baseline /path/to/original-rules \
  --candidate /path/to/candidate-rules \
  --out /path/to/continuity-comparison.json
python3 scripts/evaluate_continuity.py selftest
```

Both assembly directories contain `<issue>/articles.json`. The JSON report keeps
per-record missing boxes, missing runs, other story owners, order errors and extra
reading boxes, plus before/after summaries and per-record regressions. Missing
candidate issues are explicitly reported and score as missing, not omitted.

## What the measurements mean

- **Body-word recall:** the share of verified prose words retained in one candidate
  story record. Words are weighted by the human-corrected box text. An advertisement
  record does not count as a recovered story. Title, author, teaser, synopsis,
  note, caption and chapter-apparatus roles are excluded from this prose measure.
- **Internal missing runs:** consecutive missing gold prose boxes that still have
  recovered story boxes before and after them. The report gives missing words and
  the longest run, separately from missing beginnings and endings.
- **False splits:** gold prose is owned by multiple candidate story records. The
  other owners and their covered words are listed; duplicated ownership can also
  produce this diagnostic and should be checked before calling it a split.
- **Order errors:** inversions among retained prose boxes relative to the human
  ordering. It reports this separately from omitted boxes.
- **Intact body links:** consecutive gold prose boxes that still follow each other
  in the candidate reading stream. Legitimate gold chapter apparatus may intervene;
  unexpected advertisements, page numbers or other story text break the link.
- **Extra reading words:** material read as part of the matched story but absent
  from its verified reading record. This is a contamination diagnostic, not an
  independent text precision estimate. A candidate matching multiple verified
  stories is separately listed as a possible false merge.

Verified stories are the primary results. Modified but unverified stories have a
separate diagnostic summary. Manually inserted text without a scan-region identity
is counted separately, and records replaced by a whole-text override are excluded
explicitly. No combined score trades away continuity to remove a few extra words.

## Available pilot benchmark, 7 October 2026

The read-only server snapshot in the Dropbox root is
`pilot_export/continuity-2026-10-07/`. It contains `pilot-inputs.tar.gz`,
`baseline-rules/`, `human-gold.json` and `baseline-continuity.json`. The local
repository's ignored `data/` tree has the ten pilot issues' layout and articles,
nine annotation logs and the original rules output. The frozen labels contain
42 verified stories (349,689 words under their existing body roles) and four
modified stories. The rules were developed on these issues: this is a regression
benchmark, not a representative corpus accuracy estimate.

The original rules retain 349,329 of those words in one story record (99.897%),
with all body words present for 39 of 42 stories. Of the 360 apparent missing
words, 195 reflect inconsistent label conventions: a 30-word epigraph, a 159-word
serial synopsis, a two-word end mark and a four-word subtitle. Do not promote
these into narrative prose merely to improve the score. The labels remain
unchanged; the ambiguities are recorded in `benchmark-review-notes.json`.

The substantive missing ending is 165 words of *Mustang Mesa*, scan page 88,
regions 2–6, assigned to *The Flag of the Alamo*, whose heading starts at region
7 on the same page. This is a useful continuation regression case.

The original rules' 29 order inversions all concern one paragraph, `160:30`, in
*The Demolished Man*. A same-record human move placed it after scan pages 159 and
158, despite the printed folios ordering those leaves 156, 157, 158 and the prose
continuing naturally from 160:29 through 160:30 to 159:0. Preserve the human record
and report this benchmark disagreement; do not rearrange automatic output to
imitate an apparently mistaken annotation. Geometry improvements may reveal
additional inherited ordering errors, which need scan-based review before a
numeric regression is accepted as a real loss.
