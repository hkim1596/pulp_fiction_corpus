# Implementation plan — carrying out the protocol

How the study described in the protocol document (the shared Google
Doc, which this plan follows section by section) gets carried out on
our actual infrastructure. The protocol says WHAT will be done and
why; this plan says what runs on which machine, what gets built, what
it costs in time and storage, who does what, and in what order. Where
the two ever disagree, the protocol wins and this plan gets revised.

Numbers marked "pilot-measured" come from the completed ten-issue
pilot (docs/pilot-results.md). Numbers marked "estimate" should be
re-measured on the pilot before being relied on.

## Phase 0 — before protocol acceptance (running now)

Registered Report discipline: until the Stage 1 protocol is accepted,
no study data is collected or analyzed beyond the ten declared
development-set issues. Everything in this phase either improves the
machinery on the development set or prepares documents.

0.1 Manual correction sprint (this month). Annotators repair and
verify stories on the workbench, priority on the known broken
serials. Every verified story is a test case for 0.2. Target: enough
verified stories to cover the failure patterns in
docs/assembly-notes.md — roughly 20–30 stories across several
magazines and decades, including at least the three documented
Astounding cases and one full issue verified end to end.

0.2 Assembly version 2 (early next month, after the main server
returns). Rewrite of the assembly stage implementing the seven rules
in docs/assembly-notes.md: the continuation rule (a section heading
without a by-line never starts a new record), the contents-page
top-down pass with automatic folio-offset detection, the shared-page
check, the coverage check (no text box unassigned inside a story's
span), tagged chapter apparatus, title and author from by-lines
rather than ornamental lettering, and one-region-one-record.
Acceptance test: re-assembly must reproduce the human-verified
structure of every corrected story, and the check flags must come
back empty or explained. This is the single highest-leverage piece of
work in the whole plan — every later phase consumes story boundaries.

0.3 Protocol review and freeze. Comments in the document; a brief
meeting; Dennis ports to LaTeX. Alignment items our pilot already
settled that the text should reflect before freezing: the OCR route
(layout detection plus region reading plus deterministic rules; the
pilot showed model-based cleanup adds nothing and occasionally
harms, so it is dropped from the corpus path); error reporting as
exact character and word error rates with error composition; the
archive item count stated with its date of counting; the ten-issue
development set named.

0.4 Author-name groundwork. The interpretation phase conditions on
shared authorship, and pulp authorship is full of pseudonyms and
house names. Prepare the normalization approach now: printed by-line
kept verbatim, a normalized author table built with help from the
FictionMags Index / ISFDB where coverage exists, and an explicit
"unresolved" state. No full-corpus data is touched; the design and
the linkage code are written and tested on the development set.

0.5 Capacity decisions (below, "Storage and compute"), because the
first one — where twenty terabytes of page images live and die —
changes how Phase 1 is built.

0.6 Development-set rehearsal of the analysis (done 2026-08-31, kept
current from here on). Phases 2–4 below already exist as code and
have run end to end on the ten pilot issues: exact matching at seeds
6, 7, 8 with clusters and sensitivity; the paraphrase stage
(embeddings, neighbour retrieval, local alignment, joined
alignments); planted-reuse validation on a separate copy; the pair
table with masked topic similarity, the stratified sampler checked
against the full table, background curves, and a first two-part
hierarchical model. Results live on the site (/reuse) and under
data/reuse in the repository; the method is in docs/handbook.md.
What the rehearsal changes in this plan: (a) Phase 2.2 no longer
needs a Passim trial first — our own engine implements the
protocol's definitions exactly, is validated on planted reuse, and
runs the pilot in half a minute per seed; Passim remains the
cross-check on a shared subset at corpus scale. (b) Phase 3.1 has
measured numbers: 27,044 passages for 680,000 words; embedding on
two CPU cores at about 30 passages a second (the GPUs will do the
corpus); brute-force neighbour search is fine at this size and an
approximate index is needed beyond about a million passages. (c)
The validation set (3.2) is now the blocking item for the paraphrase
settings: the rehearsal used development-set defaults (50-word
windows, K=10, keep at 20 columns and 60% identity) that the
hand-reviewed set must confirm or replace. (d) The pair table (4.1)
exists with every column the protocol names; Dennis receives it
with the model as a concrete proposal rather than a blank page. (e)
The same-issue check exposed double-owned regions in the machine
assembly (0.2's rule 7), which the reuse inventory neutralises by
collapsing such records, but which assembly v2 must fix at the
source.

## Phase 1 — corpus construction (protocol section 2)

Starts at acceptance. Everything is a scaled-up version of machinery
that already exists and was measured in the pilot.

1.1 Survey and sampling frame. Harvest the metadata of every item in
the archive's pulp collection (about 28,000 items; state the count
with its date). Reconstruct provenance: contributor, upload date,
scan source. Detect duplicates — the archive holds multiple scans of
some issues — by normalized title, volume, issue, and date; keep the
best scan per issue by resolution and completeness, recording the
choice. Produce the data-statement documentation the protocol
promises: coverage by year, title, genre, publisher, alongside the
known gaps.

1.2 Download, as a rolling pipeline, not a bulk copy. Pilot-measured
politeness rate projects to roughly 55 days of fetching for about
4.0 million pages; that is fine, because download feeds OCR in
batches (an issue is fetched, processed, and its bulk artifacts
released before the next batch lands). Working page images at pilot
resolution are 4–5 MB each — about 18 TB if kept, which we cannot —
so masters are deleted after OCR (they remain re-fetchable at the
archive), and what is kept per page is: the layout-and-text record,
a small preview image (pilot-measured ~120 KB, about 500 GB for the
full corpus — this is the piece that needs a storage purchase or a
trim decision), and the provenance pointer back to the archive item.

1.3 Reading at scale. The layout-and-recognition stage is
pilot-measured at 1.5 seconds per page on one GPU: about 70 GPU-days
for the corpus, halved per additional GPU lane. Runs in resumable
batches with the existing completeness guards; quality is watched by
the dictionary-word rate per issue (pilot baseline ~97.5%), with
outlier issues flagged for inspection rather than silently included.
Deterministic rules cleanup follows at negligible cost. No model
cleanup stage.

1.4 Assembly at scale, with assembly v2. Every issue becomes
articles; the coverage and shared-page checks run corpus-wide and
their flags become work queues, not warnings to scroll past.
Paratexts — advertisements, contents pages, editorials, letters —
are separated into the parallel corpus the protocol describes.

1.5 Verification at scale. Human verification cannot cover a corpus
of this size, so it becomes measurement and repair: a stratified
random sample of stories (by decade, genre, and flag status) is
verified on the workbench to estimate assembly accuracy with a
confidence interval for the dataset paper; everything the checks
flag is repaired or excluded with a recorded reason; and any story
that later enters a genealogy case study is verified before being
quoted. Annotator agreement is measured on a double-verified
subsample.

1.6 The story-level corpus. Output: one record per printed unit with
verbatim title and by-line, normalized author link, magazine, issue,
date, genre, page range, provenance to scan regions, text, and flags.
Versioned releases; restricted access until the dataset paper (Open
Humanities Data) is out, per the agreed publication plan. The corpus
may be its own paper; this plan only promises the dataset and its
documentation.

## Phase 2 — verbatim reuse (protocol section 3.1)

2.1 Normalization. The protocol's conservative preprocessing
(lowercase; normalize encoding, whitespace, hyphenation, punctuation,
typography; nothing else) — a small, heavily unit-tested module,
because every downstream number depends on it.

2.2 Matching engine. First candidate: Passim, which the protocol
cites and which implements exactly this retrieval logic (n-gram index
then alignment) and is attested in the literature. Trial it on the
development set; if its maximal-span and clustering behavior match
the protocol's definitions, adopt it and record versions and
settings; if not, the fallback is our own inverted shingle index with
left-right maximal extension, which is straightforward but must then
be validated against Passim on a shared subset. Corpus scale is
roughly 1.6 billion tokens (estimate: 4.0M pages at ~400 words);
either engine wants the full corpus on fast local disk and runs for
days, not hours — schedule accordingly.

2.3 Sensitivity runs. Seed lengths 6, 7, 8 as three full passes;
report match-inventory sensitivity, as the protocol commits.

2.4 Consolidation and inventory. Merge overlapping matches, cluster
passages with multiple witnesses, and store the inventory as its own
versioned dataset: cluster id, witnesses (story ids and offsets),
length, and type. Whole-story reprints fall out here and are
reported, not discarded. The site gets a reuse-cluster browser page
(same doctrine as the rest of the site: every claim traceable to the
scan), which becomes the shared surface for Phase 5.

## Phase 3 — paraphrase (protocol section 3.2)

3.1 Passage embeddings. Overlapping short passages (window and
stride fixed after development-set trials) embedded with an
open-weights model on our GPUs; vectors indexed for nearest-neighbor
retrieval. At corpus scale this is the largest new engineering piece:
on the order of 100 million passages, so the index needs compression
(quantization) and sharding; embedding throughput needs measuring on
the development set before promising a schedule.

3.2 Validation set first. Before any corpus-wide run, a
hand-reviewed set of known paraphrase pairs and hard negatives is
built — Sujin leads; the workbench grows a small judgment page for
this (pair on screen, judgment recorded append-only under the
judge's name, same as annotation). The protocol's calibration —
retrieval depth and alignment thresholds chosen against this set,
then frozen — is exactly a development-set activity and can begin
before acceptance.

3.3 Local alignment. Candidate pairs aligned allowing substitutions,
insertions, deletions; adjacent correspondences joined; results kept
separate from exact matches; consolidated into clusters as in 2.4.

## Phase 4 — background confluence (protocol section 4)

4.1 The pair table. For every detected match pair, and for a
stratified sample of non-matching pairs (strata: publication date,
temporal distance, topic similarity), one row: reuse extent for each
kind, later publication date, temporal distance, topic similarity
computed on reuse-masked text, same-author flag from the normalized
table, venue, genre. Sampling probabilities stored with the rows —
the estimates are reweighted by them.

4.2 Modeling. The two-part hierarchical model (any reuse? then how
much?) with story-level effects. Dennis owns the statistical design;
our side delivers the pair table, reproducible from raw inventory by
one script, and the compute to fit models at whatever sample size
the design settles on. Model code lives in the repository with the
rest.

4.3 Outputs. Background distributions for exact and paraphrastic
reuse; every match scored against the background expected for its
pair's characteristics; change over time in both the probability and
the extent of reuse.

## Phase 5 — genealogies (protocol section 4.2)

Case selection from the scored inventory (extensive, surprising, or
historically suggestive clusters); every quoted passage verified
against the scan on the workbench; the cluster-browser page is the
working surface for the close reading. Output: the case studies for
the findings paper(s), each with a machine-checkable evidence trail.

## Storage and compute (decide early)

Per pilot measurement: page images 4–5 MB (18 TB full corpus — not
kept; rolling delete after OCR), previews ~120 KB (~500 GB — keep if
storage allows; this needs a disk purchase or a policy decision),
text and layout records (measure exactly on the pilot before
estimating; likely low hundreds of GB compressed — kept, this IS the
corpus), vector index for Phase 3 (order of 100–300 GB — kept during
analysis). The shared server currently lives near disk-full; the
honest options are a dedicated storage volume for this project or
trimming previews to flagged-and-sampled issues only. GPU budget:
~70 GPU-days for reading (split across lanes), embedding time to be
measured, matching engine runs on CPU over days. The warm-standby
Studio mirrors the website's data, not the full corpus; corpus-scale
data needs its own backup plan (second copy on the storage volume,
or re-derivability from the archive accepted and documented as the
recovery path).

## Who does what

Heejin: infrastructure, corpus pipeline, website and workbench,
dataset releases. Sujin: annotation lead, validation sets, agreement
measurement, narratological side of the case studies. Dennis:
protocol and statistical design, modeling, LaTeX and submissions.
Everything lands in the shared repository and is documented in the
handbook and journal as it is built.

## Timeline sketch (to revise at the protocol meeting)

Now to acceptance: correction sprint, assembly v2, validation-set
tooling, protocol freeze, storage decision. After acceptance, months
1–3: survey, rolling download and reading, assembly, QA sample.
Months 3–4: verbatim engine trials and full runs with sensitivity
passes. Months 4–6: embeddings, paraphrase calibration and runs.
Months 6–8: pair table, modeling, genealogy selection; dataset paper
(Open Humanities Data) prepared in parallel once the corpus freezes.
Fetching and reading overlap by design, so the long poles are the
55-day polite download and the paraphrase engineering.

## Risks worth naming

Disk (the project has hit a full disk twice already; the rolling
pipeline exists because of it). GPU contention on the shared server
(lanes are borrowed; the schedule assumes one to two GPUs, not
four). Archive changes (items disappear or get restricted:
provenance records pin what was used; the survey snapshot is the
citable population). Author normalization quality (pseudonyms will
leave an "unresolved" remainder; the modeling treats it explicitly).
Assembly accuracy on the worst scans (the QA sample turns this from
an anxiety into a measured number with an exclusion rule).
