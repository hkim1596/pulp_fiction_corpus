# Text reuse — the protocol, and how this site carries it out

The paragraphs in quotation blocks are copied word for word from the
Stage 1 Registered Report protocol draft ("Planning", Dennis Yi Tenen
and Heejin Kim, shared Google Doc, read 2026-08-31). That document
governs; when it changes, this page follows. After each quoted step
comes what the code in this repository does for it, with the exact
settings in force and the file that produces the numbers shown on the
site.

## The corpus the analysis runs on (protocol section 2)

> Our initial tests indicate that existing OCR is also uneven across a collection assembled from scans produced at different times and by different contributors. We therefore rerun OCR using a modern document-layout and OCR recognition pipeline. Individual stories across page and issue breaks are then reconstructed with appropriate metadata. Advertisements, tables of contents, editorials, and other paratexts are separated into a parallel corpus.

> These efforts ultimately yield a story-level corpus, ready for analysis, in which individual works are linked to authors and to their original issue-level metadata, which we plan to release publicly.

Here: the corpus-building stages s01–s08 (the section above this one)
produce one record per printed unit — story (a serial instalment is a
story with serial fields), poem, feature, letters page, house matter,
advertisement, contents page — with title and by-line as printed, page
range, and the scan regions it came from. The reuse stages read those
records through the site's own replay (machine assembly plus every
human correction) via `pipeline/r00_export_stories.py`, which writes
`data/pilot_stories.jsonl` and, since 4 September 2026, the two corpora
the protocol names as two files: `data/export/stories.jsonl`, the
story-level corpus (story records of fifty words or more), and
`data/export/paratext.jsonl`, the parallel corpus (everything else,
including story records too short to be stories). The
[corpus page](/corpus) shows both side by side with downloads. Ten
issues are the declared development set; nothing else is downloaded or
analysed before the protocol is accepted.

> Because the archive was assembled from a variety of contributors rather than according to a systematic collection design or a library collection policy, we treat it as a repository from which to construct our analytical corpus rather than as a representative corpus in itself. Following longstanding work on corpus design and representativeness, as well as more recent calls to document the provenance and construction of digital collections, we reconstruct the collection's history of transmission and assess the resulting sample by year, title, author, and publisher, alongside other available metadata.

Here (`pipeline/s00_survey.py`, metadata only, allowed before
acceptance by the decision of 3 September): `--run` reads the search
index for every item of the collection (28,286 on 3 September 2026;
language, sub-collections, dates, page counts), and `--enrich` reads
each item's own metadata record for what the index does not carry —
the uploading account and date, the curator who admitted it and the
sub-collection it was filed under, the OCR engine that produced the
archive's text, the language that OCR detected (a check on the items
with no language given), the rights fields. The
[collection page](/collection) is the history of transmission and the
sample assessed by decade, genre, magazine title, publisher (from
`config/publishers_magazines.json`, a reference table of magazine and
period to be confirmed against the mastheads) and language, with the
authors of the issues processed so far; the [datasheet](/datasheet)
is the same material in the form of Gebru et al.'s datasheets and
Bender and Friedman's data statements (the protocol's references 12
and 13), generated from the survey and the corpus counts, with what
the protocol leaves for the release marked pending.

## Study design (protocol section 3)

> Initially, to accommodate length variation, we find exact matches without assuming fixed passage size, allowing repeated sequences to extend as long as the textual evidence supports them. After, we relax matching parameters to capture near-verbatim reuse, now including passages altered through substitution, omission, insertion, or paraphrase. Further, both kinds of results must be interpreted against an empirical background of textual similarity. What reuse may mean depends on how often comparable patterns occur among works of the same period and theme.

Here: three stages in that order — exact (r02), paraphrase (r04),
background (r05) — each writing files under `data/reuse/` that the
pages of this site read in place.

## Verbatim reuse (protocol section 3.1)

> Conservative preprocessing. From the reconstructed story text, we lowercase and normalize for encoding errors, whitespace, hyphenation, punctuation, and typography. No stemming, lemmatization, or stopword removal is performed at this stage. Verbatim reuse can be defined in terms of lexical identity at this stage.

Here (`pipeline/r01_normalize.py`): Unicode NFKC, soft hyphens
removed, words broken at line ends rejoined ("moth-" + newline +
"er"), typographic quotes and dashes straightened, whitespace
collapsed. A token is a run of letters or digits with internal
apostrophes allowed ("don't"), folded to lowercase; punctuation is not
a token, so identity ignores it. Every token keeps its character
offsets into the canonical text, so a match can always be shown as it
stands.

> Minimal candidate seed retrieval. We then create an inverted index of fixed-length word shingles, initially 6–8 tokens, using shared shingles to identify candidate locations in different stories. This avoids comparing every story against every other story. The approach broadly follows the retrieval logic used in Viral Texts and Passim projects, where an n-gram index identifies promising regions before more expensive passage comparison. The minimum token length of the candidate seed is treated as a computational parameter rather than an evidentiary threshold. We test several seed lengths and report the resulting sensitivity as a robustness check.

Here (`pipeline/r02_verbatim.py`): an inverted index of word shingles
at seed lengths 6, 7, and 8, run as three separate passes and reported
side by side (the sensitivity table on the reuse page). A shingle that
occurs in more than 50 stories is skipped as commonplace for candidate
retrieval, and every skipped shingle is written out
(`*_skipped_shingles.json`); in the pilot none was skipped.

> Extend the seed to its maximal span. When two texts share a seed, we extend the match left and right token by token until lexical identity ends. Thus, an eight-word seed might resolve into an 11-word match, a 73-word match, or an entire story. Overlapping seeds from the same continuous sequence are consolidated into a single maximal match. A resulting unit of analysis becomes the maximal exact shared passage of variable length occurring in two or more works.

Here: exactly that; seeds inside one shared passage collapse to the
same maximal span by construction (`find_matches`). Two design choices
that are ours: only pairs of stories from different issues enter the
inventory — a passage shared by two records of one issue is nearly
always the assembler listing a scan region under two records, so those
go to a diagnostics file with the cause labelled; and records of one
issue that share scan regions are grouped into a "family", so witness
counts are reported raw and with families collapsed.

> Consolidate. We further cluster individual matches to represent patterns of reuse across the corpus. For example, if the same 80-word passage appears in six stories, it can ultimately be represented as one reuse cluster with six witnesses, rather than as the fifteen pairwise comparisons among them.

Here: union-find over passage occurrences — the two sides of every
match are joined, and occurrences in one story that overlap by at
least half are joined (`cluster`). Each cluster carries its witnesses,
its longest span, and the text of every member; the cluster browser
shows them side by side.

> Report. This phase concludes with a descriptive analysis of the resulting reuse inventory. We report the length and frequency of repeated passages and the proportion of each story involved in reuse (along with other statistics and visualizations that may become apparent at this time). We also identify especially extensive cases, including whole-story and high-coverage reprints.

Here: `*_stats.json` (counts, length histogram, clusters, stories
involved), `*_story_share.json` (share of each story inside matches),
`*_pairs.json` (per story pair: matches, longest, coverage). Since 4
September the reuse page reports the extensive cases as the protocol
asks: every cross-issue story pair ranked by the share of the shorter
story that shared passages cover, with two named bands — whole-story
(80% or more) and high-coverage (20% or more) — and the proportion of
each story involved in reuse (how many stories have half, a tenth, one
percent of their words in shared passages, and the largest shares by
name). A whole-story case is classified on its pair page by the
printed record (a reprint the magazine acknowledges, or not). Whole-
story duplicates surfaced immediately in the first run — as same-issue
shared-region duplicates, which is the assembly finding recorded in
the pilot results.

## Paraphrase detection (protocol section 3.2)

> Candidate retrieval. Because paraphrased passages may share few exact words, the shingle-based retrieval used for verbatim reuse will miss many potential matches. As a consequence, in the second phase of the study, we broaden candidate retrieval to identify passage pairs with similar meanings despite differences in wording. Short overlapping passages are represented as semantic embeddings, and for each passage, we retrieve a fixed number of its nearest neighbors. The number retrieved is calibrated in the validation procedure described below.

Here (`pipeline/r04_paraphrase.py`): passages of 50 words, step 25
(so each word sits in two passages); embedded with the open-weights
model BAAI/bge-small-en-v1.5; for each passage the K most similar
passages in other stories are retrieved, K = 10 in the main run and 5
and 20 as sensitivity settings, all taken from one K = 20 retrieval
by rank. In addition every exact match is a candidate, so that an
OCR-damaged copy is aligned even where the embedding search misses it;
each kept alignment records which route found it. A 100-word window
was run once as a further sensitivity check.

> Local alignment. As before, candidate passages are then compared locally, but this time allowing for substitutions, insertions, deletions, and related forms of reformulation. Adjacent correspondences are joined and extended where the evidence supports a longer relationship between the texts. This second stage helps distinguish sustained passage-level correspondence from the much more common case of two stories simply describing similar things. The resulting unit of analysis becomes a maximally aligned passage of variable length occurring in two or more works.

Here: each candidate pair, widened by 25 words of context on both
sides, is aligned word by word by local alignment (Smith–Waterman;
match +2, mismatch −1, gap −1). Two words count as equal when
identical or, for words of five letters or more, one edit apart (OCR
tolerance). Alignments in the same story pair that touch or overlap
are joined and the union is re-aligned, so a long relationship is
reported once at full length. Each alignment stores its length in
columns, its matching columns, identity (matching over all columns),
score, and both texts.

> Parameter selection and validation. To calibrate the paraphrase detector, we first test it on a separate set of passages reviewed by hand. This allows us to compare a small, prespecified range of settings and select those that best recover known paraphrases without needlessly expanding the candidate pool. The selected settings are then fixed and applied to the full corpus.

Here (`pipeline/r06_validation.py`, the [paraphrase review](/reuse/validate)
page): the hand-reviewed set is drawn from the detector's own candidate
pairs across the whole range it sees. Candidates are stratified by
source (embedding-only, or seeded by an exact match) and by the
alignment score of the candidate region — below any rule (score under
9), loose rule only (9–15), default rule (16–29), strong (30 and more)
— and, in the two lowest bands, by the embedding cosine (the closest
5%, the next 20%, the rest), so that the set holds pairs the default
rule keeps, pairs only a looser rule would keep, pairs below every
rule, and the hard negatives the embedding search brought close
although the words share little. Fifteen candidates are drawn from
every stratum with a fixed seed, each with the inverse of its
inclusion probability as its weight. Readers judge each pair on the
site — paraphrase or copy, not a paraphrase, unsure, with a note —
without seeing the machine's numbers; each judgment is one line of an
append-only log (`data/reuse/validation/judgments.jsonl`: time,
reader, set, item, judgment, note), and two readers judge the same
items so that their agreement (Cohen's kappa) is reported. The
calibration scores every setting in a fixed grid — K 5, 10, 20 by six
keep rules from 15 columns at identity 0.50 to 30 columns at 0.70 —
against the agreed judgments: precision (kept pairs the readers called
paraphrase), recall (paraphrase pairs the setting keeps), both weighted
by the strata, and the size of the pool kept; the chosen setting is
the highest recall among those with precision of at least 0.90, ties
to the smaller pool. Until the readers have judged, the default keep
rule stands (20 columns, identity 0.60), and the planted-reuse copy
stands in as the positive control: 20 verbatim, 20 lightly damaged and
20 heavily edited plants scored for recall at the default rule and at
a looser one (15 columns, 0.50).

> Consolidate and report. As in the verbatim analysis, overlapping and related alignments are grouped into reuse clusters with multiple witnesses. Exact and paraphrastic matches are retained separately, allowing us later to compare how much recurrence is captured by each form of reuse.

Here: the same clustering as the exact stage, in separate files
(`data/reuse/para/`), separate pages, and separate columns of the
pair table.

## Interpretation — estimating background confluence (protocol section 4.1)

> Construct comparison sets. Our basic unit of comparison has been a pair of pulp stories throughout. For each pair, we record the extent of exact and paraphrastic reuse identified in Sections 3.1 and 3.2. We then characterize the relationship between the two works using publication date and temporal distance, topic similarity, publication venue, author, and other available metadata.

> Topic similarity gives us a continuous measure of how closely two works concern the same subject matter. This matters because some amount of textual recurrence may follow from that similarity alone. To keep the measure independent of the reuse we are trying to explain, we calculate topical similarity using the surrounding text after masking detected reused passages.

Here (`pipeline/r05_background.py`): one row per pair of stories —
at pilot scale every pair, 41,041 rows — with: longest exact match
and covered words at each seed; longest paraphrase alignment, count,
best identity at each K; the later publication date and the years
between the two; topic similarity as the TF-IDF cosine over words and
word pairs computed on the text left after masking every detected
reused passage, plus the embedding cosine as a second column; same
author (printed by-lines normalized, pseudonyms not yet resolved, with
an explicit "unknown" state), same magazine, same publisher (from
`pipeline/publishers.json`), same genre, same format; same issue and
shared scan regions as flags. The pair page shows this row for any
two stories; the pairs page lists and filters the table; the whole
table is downloadable.

> Sample comparison pairs. While detecting reuse tells us where matches occur, estimating a background rate also requires cases where they do not. Consider also that we cannot practically model every possible pair in a corpus of this size. And a purely random sample would likely be dominated by very distant or topically unrelated stories.

> Because of these limitations, we retain detected matches and draw a stratified sample from the much larger pool of nonmatching pairs, preserving variation in publication date, temporal distance, and topical similarity. Sampled nonmatches provide the denominator against which the frequency of reuse can be estimated. Because matching and nonmatching pairs enter the analysis at different sampling rates, their known sampling probabilities are retained and used to weight corpus-level estimates.

Here: the sampler exists (`stratified_sample`: strata = later
decade × years-apart band × topic quartile, every matched pair kept,
non-matched pairs sampled per stratum with the inclusion probability
stored and the weight 1/probability), and because the full table
exists at pilot scale it is run against it as a check: the weighted
estimates must reproduce the full-table values. They do, exactly for
the probabilities and within 0.001 for the topic mean; the unweighted
estimates are off by a factor of twenty, which is what the weights
correct.

> Estimate the background distributions. We estimate the distribution of observed recurrence rather than imposing a single threshold for meaningful reuse. For exact reuse, of particular interest is the probability that two comparable works share a passage at least as long as the observed match. For a match of length L, we estimate P(Mexact ≥ L | X) where Mexact represents the length of the longest exact passage shared by a pair of works, and X represents the relevant characteristics of that pair.

Here: empirical curves P(longest ≥ L) over cross-issue pairs, overall
and by topic quartile, years-apart band, later decade, and same-author
flag, for each seed; the same for paraphrase alignments by length with
the identity distribution; and, since 4 September, the curve of every
full stratum of the sampler (later decade × years-apart band × topic
quartile) in `summary_<set>.json` under `background.by_stratum`. Every
matched pair is placed among comparable pairs in
`background/placement_<set>.json` — at two levels, the full stratum
and the wider one (topic quartile × years band), the pair itself left
out of the count — and the [pair page](/pairs) shows the placement for
exact and for paraphrastic reuse ("none of the other 157 comparable
pairs share at least 10 words"), the story page for each of a story's
matches, and the reuse page the most unusual pairs overall.

> Model historical variation. Once these background distributions can be established, we model recurrence at the level of story pairs. … To do this, we use a two-part hierarchical model, first estimating the probability that a pair contains any detected reuse and then, where reuse occurs, its extent. The models account for topic similarity, temporal distance, shared authorship, and publication venue. Story-level effects account for the fact that the same work may appear in many comparisons.

Here: a first full version, as a proposal for the statistical design
(which Dennis owns): part one, any reuse, logistic; part two, extent
given reuse, Poisson on covered words beyond the threshold; both with
fixed effects for topic similarity, log years apart, later year, same
author, author-unknown, same magazine, same publisher, same genre, and
one random intercept per story that enters for both members of a pair
(a design matrix with two ones per row); fit by variational Bayes
(statsmodels BayesMixedGLM). Coefficients at pilot scale are machinery
output, shown on the reuse page with that label.

## Literary genealogies (protocol section 4.2)

> From the larger set of reuse clusters, we select a small number of cases that appear especially extensive, unexpected, or historically suggestive, returning to the texts themselves by qualitative means.

Here: the layered explorer is the working surface for this — cluster
→ witnesses → story → pair → the passage on the scan — so that every
quoted passage in a case study can be checked against the printed
page; and, since 4 September, the [cases](/reuse/cases) page: a
cluster or a pair marked as a case from its page with a name and a
note, the notes kept in order, the case closed when written up or
dropped — an append-only log (`data/reuse/cases.jsonl`) with the name
of who did what, which is the working list of the qualitative step.
