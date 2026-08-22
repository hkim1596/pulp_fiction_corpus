# Pilot results — ten issues, measured

Final numbers from the completed pilot (all ten issues through every
stage), measured 2026-08-22 with exact edit distances (rapidfuzz,
`pipeline/s06_metrics.py`). Raw numbers: `data/metrics.json` on the
server; a pulled copy sits next to this repository folder in Dropbox
(`pulp_metrics.json`, with `pulp_timings.jsonl` and
`pulp_articles_index.json`).

## The pilot set

Ten issues, five magazines, 1925–1952, three genres. Astounding
1930-01 and 1930-03 have complete human-proofread text on Project
Gutenberg (full-issue gold); three Galaxy 1952 stories have proofread
story-level gold; the remaining issues are scored by dictionary-word
rate only.

## Reading accuracy (the story-level gold — the clean measure)

Share of words differing from the proofread text, per stage:

| story                    | Archive text | our route + rules | + Claude | + qwen |
|--------------------------|-------------:|------------------:|---------:|-------:|
| Galaxy 1952-03 story     | 16.7%        | 6.6%              | 6.5%     | 6.6%   |
| Galaxy 1952-04 story 1   | 13.7%        | 5.3%              | 5.3%     | 5.3%   |
| Galaxy 1952-04 novella   | 14.8%        | 4.2%              | 6.0%     | 4.2%   |

The layout route (Surya layout detection + region reading + our rules)
cuts word errors to between a third and a half of the Archive's own
OCR. These story-level numbers compare exactly the same text spans, so
they are the ones to cite.

## Why the full-issue numbers look worse than they are

Against the two full-issue golds, every stage shows ~18–19% character
error. The error-composition measure explains it: at the layout-route
stages only about 1% of that error mass is misread characters — the
rest is content present on the scanned pages but absent from the
Gutenberg transcription (advertisements, departments, front matter;
the OCR text is ~481k characters against the gold's ~415k). The true
misreading rate of the layout route is roughly 0.2–0.3% of characters.
The Archive's own text on Astounding 1930-03 has ~2.4% true misreads —
about eight times ours. Lesson for the protocol: score aligned spans,
and always report error composition next to error rate.

## The model-cleanup question

The LLM cleanup stage (a model instructed to fix OCR errors only)
gains at most a tenth of a point on top of the rules-cleaned layout
text — and each backend hurt once. Qwen made Astounding 1930-03
slightly worse (word error 21.1% → 21.9%). Claude quietly shortened
the Galaxy novella by ~2,300 characters (many small per-page trims,
each inside the guard's tolerance), pushing its error from 4.2% to
6.0%. A corrector that deletes prose is worse than no corrector.
Position going into the protocol: the corpus text is the rules-cleaned
layout text; LLM cleanup is dropped or kept only as a flagged
experiment, pending the logged cost figures.

## Dictionary-word rate (all ten issues, no gold needed)

Archive text ~94.8% of tokens are dictionary words on average; layout
route ~97.5%; LLM cleanup adds ~+0.5 points. Worst scans:
Wonder/western titles of the mid-1930s (www_1936_02: 95.5% at best).

## Speed, and what the full corpus would cost in time

Measured per page on the pilot (the qwen and assembly lanes were
contended — three jobs shared one lane — so treat them as worst case):

    layout + reading (GPU)   1.51 s/page
    rules                    ~0
    Claude cleanup (API)     2.66 s/page
    qwen cleanup (local)     10.6 s/page
    article assembly (local) 5.61 s/page

Full corpus ≈ 28,075 issues × ~143 pages ≈ 4.0 million pages. That
means: layout+reading ≈ 70 days on one GPU (splits across GPUs);
downloading ≈ 55 days at our deliberately polite fetch rate; Claude
cleanup is parallel through the API so wall-clock is small and money is
the limit (real dollar figures are logged per page in the run's meta
files — see the journal for the extraction command); qwen cleanup and
assembly as configured are lane-bound (hundreds of lane-days) and would
need dedicated GPUs, batching, or a decision not to run them.

## The article database, first census

727 printed units across ten issues: 362 stories, 309 advertisements,
54 features, 1 poem, 1 letters page. 579 carry a printed title, 164 an
author. Assembly quality varies sharply by issue — Astounding 1930-03
came out as only 13 units where its sibling issue has 106, so it is
under-divided — and mastheads sometimes became "titles" verbatim
("A STOUNDING STORIES OF SUPER-SCIENCE"). This is the current weak
point of the pipeline, and the reason the project's present focus is
human verification through the website workbench: annotators repair
stories by adding and removing segments; once a set of stories is
human-corrected, those corrections become the reference for diagnosing
and fixing the automatic assembly.
