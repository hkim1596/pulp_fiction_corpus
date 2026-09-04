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

## The text-reuse rehearsal (2026-08-31)

The analysis stages of the protocol (sections 3 and 4) ran end to end
on the pilot's own stories — 726 records, 360 typed as stories, 287
long enough to compare (50+ words), 679,941 words — as a rehearsal of
the method, not a study of pulp fiction. Full method in
docs/handbook.md; every number below is on the site under /reuse.

Exact reuse (r02). No genuine cross-issue reuse, as expected of ten
issues from five magazines: the longest passage shared by two stories
from different issues is ten words ("no more than a minute or two had
passed since", Astounding 1930-01 and 1930-03). Cross-issue matches:
599 at seed 6, 64 at seed 7, 5 at seed 8 — stock phrasing ("on the
other side of the", 102 times) and Street & Smith's address in
department columns. One real find: "Operative Carnes of the United
States Secret Service" in both S. P. Meek stories, a same-author
series formula. Same-issue matches (331 at seed 6) are diagnostics:
91 come from records that own the same scan regions; the rest are
real within-issue repeats — the Astounding contents page repeats each
story's teaser word for word, which assembly v2 can use to link
contents entries to first pages.

Two data problems the same-issue check exposed. The machine assembly
double-owns regions: td_1932_02_a001 (59,000 words) holds 2,235 of
that issue's 2,474 regions, including the full text of five stories
that also exist as their own records; gal_1952_03 has the Heinlein
story plus six single-page copies; wt_1925_11 31 shared regions. The
issue's annotation log holds one event, so this is the machine's
doing. And department columns and the contents page are typed as
stories ("Mines and Mining", "The Hollow Tree", "Contents"). Both go
to assembly v2 (docs/assembly-notes.md, rules 6 and 7). Only one
story was verified at run time, so the second story set was
"corrected" (verified or modified, 12 stories); same picture, longest
cross-issue match seven words.

Planted reuse (r03, separate copy; 178 stories, 60 plants). Exact
stage: verbatim plants 100% recovered at all seeds; near-verbatim
plants (8% of words damaged) all detected but only 85 / 81 / 75% of
their words recovered at seeds 6 / 7 / 8; heavy edits detected 100 /
75 / 45% with 26 / 15 / 9% of words recovered.

Paraphrase (r04; 27,044 passages of 50 words, K neighbours 5/10/20,
alignment kept at 20+ columns and 60%+ identity). Real corpus: two
cross-issue alignments, both publisher boilerplate with substitutions
— the second-class-mail notice (Astounding 1930-01 contents page and
Wild West Weekly 1936-02) and "Address all communications to …, care
of Street & Smith's …, 79 Seventh Avenue" (Western Story and Wild West
Weekly, identity 0.70). Same-issue alignments reproduce the region
duplicates at full length (10,212 columns, identity 1.0). Planted
copy: verbatim and near-verbatim plants 100% recovered at full extent
(the exact stage's 75–85% partial coverage of damaged copies becomes
99.9%); heavy edits 40% at the default keep rule, 95% (89% of words)
when the rule is loosened to 15 columns and 50% identity — the number
that tells us the hand-reviewed validation set will decide the
threshold. K made no difference at the default rule (every kept
alignment also had an exact seed); at the loose rule embedding-only
candidates begin to contribute. On the real corpus the loose rule
admits nine cross-issue alignments: the four publisher formulas and
five chance resemblances of ordinary prose ("on the other side of
the page … / … of the wharf …"), which is the false-positive side of
the same threshold question. The 100-word-window sensitivity run
found the same two alignments as the 50-word run.

Background (r05; 41,041 pairs, 35,442 across issues). 1.2% of
cross-issue pairs share a six-word passage; the share rises from 0.07%
in the lowest topic-similarity quartile to 4.3% in the highest, which
is the protocol's premise showing up in ten issues. The stratified
sampler (40 non-matching pairs per stratum, 20 draws) reproduces the
full-table probabilities exactly and the topic mean within 0.001,
where the unweighted sample is off by a factor of twenty. The two-part
model fits in under a minute (topic similarity is the dominant term
in the any-reuse part, +1.4 log-odds per standard deviation); its
coefficients are machinery output, not findings.

## The same rehearsal on the rules' records (2026-09-02, after the assembly switch)

Assembly v2 (docs/assembly-v2.md) replaced the model's 727 fragments
with 549 records of which 83 are stories or serial parts — whole
stories, not chapter pieces — and the text-reuse stages were rerun on
that export the same evening (r02–r05 on the main server, about
fifteen minutes; no corrected set yet, because the switch archived the
human corrections and the workbench starts clean).

Exact matching (82 stories of fifty words or more, 629,059 tokens):
415 cross-issue matches at seed 6 (209 clusters, 34 with three or more
witnesses), 35 at seed 7, 3 at seed 8; the longest is still 10 words;
64 of the 82 stories share at least one six-word phrase with a story
in another issue. Same-issue duplicates from shared scan regions: none
(there were 331 before — the double-owned regions went with the old
assembly). Planted copy: verbatim and near-verbatim plants recovered
100% at every seed; heavy edits 95% at seed 6 (65% at 7, 40% at 8) —
on whole stories the seed-6 net catches nearly every damaged copy.

Paraphrase (25,118 passages of 50 words, step 25): no alignment at all
at K=5, 10 or 20 with the default keep rule. The two alignments of the
first run were publisher boilerplate between fragments the model had
typed as stories; with real stories there is nothing to align, which
is the honest result for ten issues of six magazines. Planted copies:
verbatim and near-verbatim 100% recovered at full extent, heavy edits
60% (58% of their words) at the default rule.

Background: 3,321 story pairs, 2,973 across issues. 9.5% of cross-
issue pairs share a six-word phrase (up from 1.2%: whole stories of
7,700 words on average meet by chance far more often than 1,900-word
fragments did), and the share runs from 0.13% in the lowest topic-
similarity quartile to 25% in the highest — the same gradient, wider.
The sampler check again reproduces the full-table probabilities
exactly. The two-part model fits for seed 6 (282 matched pairs; topic
similarity +1.7 log-odds per standard deviation, same magazine +1.4)
and seed 7 (33 pairs); the paraphrase model is skipped for want of a
single matched pair. All of it is machinery output on ten issues; the
numbers change again when a corrected story set exists and when the
corpus arrives.

## The rehearsal on rules v2.2 (2026-09-04, 17:15–17:22 on the main server)

The records are now the rules v2.2 ones (docs/assembly-v2.md) with
the annotators' work replayed on top: 589 records exported by r00 (584
machine records and 5 made by hand on the workbench) — 79 stories
(instalments included), 25 features, 5 poems, 2 letters pages, 48
house records, 412 advertisements, 11 contents pages, 7 other. 18
records are verified (12 of them stories) and 28 more stories were
modified by hand, so a corrected story set of 40 exists for the first
time; this rerun is still the machine set only (the corrected set is
the next run). Against the 2 September run the story set is smaller,
83 → 79, one rule at a time: v2.1 (3 September) moved two records out
of the story type, v2.1.1 read the Odd John book announcement on the
last page of Galaxy 1952-03 as advertising (house matter under v2.2),
and v2.2 types "Braggin' Bill, Fighter" as the poem it is. One story
falls under the fifty-word floor — Whitman's "Whispers of Heavenly
Death", 44 words, printed as a filler in Weird Tales 1925-11 without a
poem mark. The stages ran in seven minutes (r02 17:15, r04 17:18, r05
17:22).

Exact matching (78 stories of fifty words or more, 625,975 tokens):
423 cross-issue matches at seed 6 (211 clusters, 34 with three or more
witnesses), 36 at seed 7 (30 clusters, 3), 3 at seed 8; the longest is
still 10 words; 64 of the 78 stories share at least one six-word
phrase with a story in another issue (40 at seed 7, 6 at seed 8).
Same-issue duplicates from shared scan regions: none. Planted copy
(r03, 75 stories, 60 plants): verbatim and near-verbatim plants
recovered 100% at every seed; heavy edits 90% at seed 6 (70% at 7, 65%
at 8) — the plants are drawn afresh each run, so the heavy-edit figure
moves a few points between runs.

Paraphrase (24,998 passages of 50 words, step 25): no alignment at
K=5, 10 or 20 with the default keep rule, as on 2 September. Planted
copies: verbatim and near-verbatim 100% recovered at full extent (mean
cover 1.00 and 0.997), heavy edits 55% (51% of their words) at the
default rule. The two extra settings from 31 August (the loose rule
c15i50 and the 100-word window) were not rerun; their files in
data/reuse/para still describe the first, fragment-based story set
(287 records) and are kept only as the record of that run.

Background: 3,003 story pairs, 2,681 across issues. 10.7% of cross-
issue pairs share a six-word phrase (9.5% on 2 September), running
from 0.6% in the lowest topic-similarity quartile through 3.9% and
11.9% to 26.6% in the highest; the tf-idf and embedding topic
measures agree more closely than before (correlation 0.81, was 0.77).
The sampler check again reproduces the full-table probabilities
exactly (the largest weighted error is 0.0004, on the mean topic
similarity of the unmatched pairs). The two-part model
fits for seed 6 (288 matched pairs; topic similarity +1.4 log-odds per
standard deviation, same magazine +1.5, same publisher −1.7, the
story-effect spread 0.67) and seed 7 (34 pairs; topic +1.0, same
author +2.5 on a wide interval); the paraphrase model is skipped for
want of a matched pair. The most unusual pairs are again the
same-author pairs between Astounding 1930-01 and 1930-03 — Ray
Cummings's "Phantoms of Reality" and "Brigands of the Moon" ("no more
than a minute or two had passed since", 10 words) and S. P. Meek's
"The Cave of Horror" and "Cold Light" ("Operative Carnes of the United
States Secret Service", the series character) — and after them the
stock phrases of the detective story ("the door at the end of the
hall": "The Seventh Devil" in Weird Tales 1925-11 against "The Murder
Monster" in Thrilling Detective 1932-02 and Louis L'Amour's "With
Death in His Corner" in 1948-12).

The corrected set (the same evening, 18:40 on the main server): the
40 stories that people had verified (12) or modified (28) by then,
362,706 tokens, 682 cross-issue pairs. 153 six-word matches across
issues (80 clusters, 31 stories), 13 at seven, none at eight; the
longest seven words. No paraphrase alignment at K = 10 (14,486
passages). 14.7% of the cross-issue pairs share a six-word phrase (none
in the lowest topic quartile, 7.1%, 19.4%, 32.2% in the others); the
sampler reproduces the full table exactly; the two-part model fits for
seed 6 (100 matched pairs; topic +1.1 log-odds per standard deviation,
same magazine +1.0, same publisher −1.0) and is skipped for seed 7 and
for paraphrase. Same picture as the machine set on fewer, cleaner
stories; the corrected set's use is the comparison, not the numbers.

Between the 2 September run and this one the main server ran the
stages once more (4 September 10:02–10:10, on the v2.1.1 records);
those results were overwritten by this run before they travelled, so
the repository's data/reuse goes from the 3 September files straight
to these. Nothing in the picture changed across the three runs: the
pilot's stories share stock phrases and nothing longer, the planted
copies are found, and the background machinery runs end to end. The
numbers change again with the corrected set and with the corpus.
