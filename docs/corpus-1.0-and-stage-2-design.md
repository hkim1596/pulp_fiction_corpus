# Corpus 1.0 and the road to Stage 2 — the consolidated design (4 November 2026)

Written in Sujin Kang's session on 4 November 2026; the record of every design decision taken since the takeover review of 29 October, with what is done, what is designed and what is still open. Sources: main.tex (the registered protocol), docs/corpus-build-log.md to 4 November, the rehearsal analysis of 2 November, the duplicate plan and the story-text diagnosis of 4 November (pilot_export/story_text_diagnosis.md), the work plan of 2 November. Decisions of 4 November (Sujin Kang): §3.4 (ii) — the guard stays and the paratext rules reach the 381 guarded items as a post-process; V2 at 70 pages; the co-authors' snapshot to be settled at the meeting; HK the second reader.

## 1. Where the corpus stands (4 November)

| | Count | Note |
|---|---|---|
| Selected (approved 4 October) | 7,440 | untouched |
| Set aside, with a reason each (config/corpus_set_aside.json) | 706 | not a scan 87, bound volume 6, no images 3, incomplete 2, restricted 1, excerpt of a magazine 103, outside the period 119, comic 43, not a text 9, **duplicate scan of an issue in the corpus 310, another edition of an issue in the corpus 23** (D-5, 4 November) |
| Books (config/corpus_kinds.json) | 633 | match partners only |
| Bound volumes | 5 | |
| **Magazine issues — the corpus** | **≈ 6,090** | the explorer's first tile after patch 0033 |
| Rules | 2.13 on 6,956 of 7,337 assembled items; 381 keep an earlier version (hand corrections the guard protects) | one version before the freeze: §3 |
| Verification | contents check 2,025 of 2,072 = 97.7 % on the 150-issue development sample; level 2 288 records | held-out sample after the freeze |

## 2. Decisions taken and applied

**D-5 duplicates (4 November).** The 384 issue pairs the rehearsal joined by same-text stories were re-read with the two items' kinds, magazine names and dates: 324 are the same issue under two identifiers (the magazine's name spelt two ways, the date given two ways), 25 are British, Canadian or Australian editions, 5 fragments, 10 pairs with two dates (decided from the scans), 5 reprints (left to the reuse stage), 15 involve books. One scan per issue is kept — the one in the frozen verification sample, then the one with records a person verified or modified, more stories, more pages, a full date, a cover; editions are set aside under the original. Nothing was deleted; each set-aside entry names the issue it stands for (`duplicate_of`). scripts/resolve_duplicates.py, patches 0032–0033.

**The explorer counts the corpus of the protocol** (patch 0033): magazine issues in the first tile, books apart; the schema version is in the database signature.

**The People page** (patches 0030–0037): the three co-principal investigators in the order Tenen, Kim, Kang, from config/people.json; public before the passcode.

## 3. Assembly rules 2.14 — design (v2.1, after the diagnosis of 4 November)

### 3.1 What the diagnosis found
469,379 cross-issue matches of ten tokens or more between items still in the corpus, sorted by where they sit in the two stories (scripts/diagnose_story_text.py):

| class | matches | stories | what it is |
|---|---|---|---|
| block | 100,726 | 4,491 | the same excerpt (20+ tokens) in 5+ stories of 2+ issues: 4,558 standing blocks — the postal statement of ownership (178 blocks, 259 stories), advertisements in prose (703 blocks, 1,950 stories), departments' standing text (Adventure's Camp-Fire 2,281 occurrences; Startling Stories' Hall of Fame notice 522), the rest variants of these |
| foot | 58,439 | 14,425 | a short match at the foot of a record; the random sample is advertisements, mastheads, publishers' addresses, department lists |
| head | 30,993 | 11,152 | the same at the head: coupons, subscription lines, "Entered as second-class matter" |
| middle | 276,702 | 29,857 | 10–19 tokens: stock phrases; longer: advertisements inside records and genuine reuse |
| synopsis | 1,244 | 620 | only 167 of 50 tokens or more are recaps; the rest are "This story began in…" notices |
| whole | 1,275 | 1,501 | reprints (the reuse stage's consolidation) |

So the matter in story records that is not story is, overwhelmingly, **advertising, masthead and standing department text in boxes that the assembly gave to a story** — about a third of the story records are touched. Synopses and by-lines are small matters.

### 3.2 The rules
Order of application: R-B, R-C (typographic and regex evidence, per issue) → R-A (recurrence evidence, corpus-wide) → R-D (by-lines).

**R-A, the recurring-box rule (the main rule; revised 5 November from the reading of the evidence).** Evidence: for every layout box of every record, the share of its 8-word shingles that also occur in boxes of **two or more other issues**, computed corpus-wide by the array engine of r02_verbatim_corpus (hash, sort, count; 443 M shingles, 198 seconds on the server, 4 November) and written to `data/corpus/p52-2026-10-16/box_recurrence.json` — and, issue by issue, to `box_recurrence/<issue>.json`, which the assembly reads — for boxes of 15 tokens or more with share ≥ 0.6, with each box's reach and up to eight partner records (issue, record, type, author, work title). The set-aside items are left out of the count (the run of 4 November counted them; it is redone before the upgrade). The file is assembly evidence (like config/toc_page_overrides.json), not an analysis: no pairs, dates or topics are computed.

What the reading found (scripts/box_recurrence_read.py, 5 November, 58,781 story records of the magazine issues): 9,986 records carry a recurring box, 6,311 of them by less than a tenth of their words; the boxes are advertisements, house notices and departments' standing matter. Nine in ten recurring boxes sit **inside** the record's box sequence, not at its head or tail (a story continued at the back of the issue runs through advertisement pages), so the position test of the first design would have left the rule inert (785 of 7,499 records). The records that recur by half or more (2,378) are **reprints** — the British editions of Astounding, the Avon readers, Famous Fantastic Mysteries, Fantastic Novels top the magazine table — i.e. the study's object; a partner of the same work title is a reprint or a serial's recap; a partner that is a story by the same author is, as often as not, a regular author's story carrying the same advertisement (Fox B. Holden's stories in IF, each with the same advertisement), so the same author alone is no protection. Boxes shared only with other stories and carrying no selling cue are quotations (Kipling's "over the hills and far away" in three stories) — commonplaces in the sense of §3.1 of the protocol, not paratext.

Rule (s08_assemble_rules.recurring_boxes, after the second look over the records and before the texts are built): in a story record, for each plain text box (no role) with an evidence entry of 15+ tokens, share ≥ 0.6, reach ≥ 2 other issues —
1. a partner **of the same work title** → the box stays (a reprint, a serial's recap); the record is flagged;
2. every partner **a story by the same author** → stays (the author's own recurring text); flagged;
3. a box found in **fewer than 6 other issues** is weak evidence, and three tests apply before it is split: (a) when **three or more** of the record's weakly recurring boxes share a partner issue, that issue reprints the story, and every box shared with it stays (flag `reprinted, by the recurrence evidence, in …`; `reprint?`) — a story reprinted in issue X recurs with X box after box, an advertisement's partners are scattered; (b) **narrative prose** (R-B's test: the third person past outweighs the second person, no strong selling cue) stays, flagged `reprint?`; (c) a box with **no selling cue** stays, flagged `weakly recurring`. Only a weakly recurring box with a selling cue is split. The first two regressions (5 November, 90 issues) showed why: reprinted stories ("Pygmalion's Spectacles" in Fantastic Story 1955, "In Amundsen's Tent" in Weird Tales 1928) lost their pages — the text is found in two other issues whose assembly had given it to a feature or an advertisement record, so the same-work test did not see it, and the grammar test alone let the dialogue and the diary pages through. Prose found in six or more other issues is standing matter whatever its grammar (the Psychiana advertisement, narrative in the first person, is found in 46);
4. no partner, or every partner a story, and the box carries **no selling cue** (a strong cue, or two advertising words with a selling phrase) → stays, flagged `commonplace?`; with a selling cue it is the advertisement every story absorbed → split;
5. otherwise → **split out** into its own record (`paratext: recurring box` — or `ownership statement` / `masthead` when the one-cue regex matches, so that the legal matter R-B's two-cue test missed is house_masthead — with `split_from` and `recurrence: {n_issues, share, partners}`), consecutive boxes on a page into one record; the record's type and class follow its content (classify_ad). **The box's position in the record is no test.**
Record guard, unchanged: when the boxes to split are **half or more** of the record's words, or **fewer than 200 words** would remain, nothing is split and the record is flagged `mostly recurring` for review (a reprint, or paratext read as a story). The reading puts the rule's reach at about 7,500 records and 1.9 M words; the thresholds (three issues, share 0.6, 15 tokens, 200 words) move the count little (sensitivity table in pilot_export/box-recurrence-read-2026-11-05/report.md) and stay as proposed.

**R-B, masthead, coupon and price regex (per issue, no corpus pass).** A box matching two or more cues — Statement of (the) Ownership; Entered as second-class matter; Sworn to and subscribed; Before me, a Notary Public; Subscription … single copies; All rights reserved / Names of all characters … fictitious; NAME ___ ADDRESS ___; Enclosed find … cents; Send no money; Dept. [A-Z0-9]; "…, Inc., <number> <street>, New York" — or one cue in a box that is not inside the prose flow — is split out as in R-A. Target: the 259 stories carrying an ownership statement → 0.

**R-C, serial notices and recaps.** "This story began in the All-Story Weekly for …" and its kin → role note. For instalments linked by work_id, a run of 40 tokens or more in the later instalment's first 600 tokens that matches the earlier instalment's text → role synopsis (text evidence added to 2.13's heading rule). Target: the 167 recaps → 0.

**R-D, the by-line guard (conservative).** Rejected as authors, kept in `author_as_printed`: editorial signatures (the Editor, THE EDITORS, THE READERS, Editor and Readers — the record is flagged for type review), advertising and price strings (THE $4.00 YEAR, Mail 30c, page, I). **House pseudonyms are kept** (An Old Scout, A Trainer): they are by-lines, and the author table resolves them.

### 3.3 Residuals, stated in advance
Advertising OCR'd into the same box as story text cannot be split by R-A; short advertising lines under 15 tokens remain (they fall under the commonplace cap or stay as 10–14-token matches); the recurrence evidence depends on the layout boxes, which are not redone.

### 3.4 One version over the corpus — two ways (a decision for the PI)
(i) **Force the 2.14 upgrade on every item** and replay the hand corrections; corrections that cannot be replayed are listed and redone by hand. Every item carries rules 2.14.
(ii) **Keep the guard**: the 381 items whose assembly it protects keep their 2.13 assembly, and the paratext splits of R-A/R-B are applied to them as a post-process (a record's identity does not change, so the hand corrections stay). Their rules field reads "2.13 + 2.14 paratext"; the datasheet says so.
Recommendation: (ii) — the corrections are the product of level-2 verification and are costly to lose; the paratext rules reach every item either way.

### 3.5 Steps
2a regex, notices, by-lines in s08 (+ docs/rules-2.14-design.md, this section in English) — done 4 November · 2b scripts/box_recurrence.py, its run, and the reading (scripts/box_recurrence_read.py) — done 4–5 November · 2c R-A in s08 — written 5 November (patch 0046) · 2d the evidence recounted without the set-aside items, then the regression on the 10 pilot issues and the 30 double-read issues (every changed record read), 50 random issues' new splits read — three regressions, read clean on 5 November (patches 0047, 0048) · 2e snapshot (patch 0049, P52bq) (tar of data/assembly_v2, data/annotations, data/assembly_archive, config), the upgrade over the corpus, the cross-issue pass, the report. About 7 working days and 9 machine hours.

## 4. Evidence files and their standing
| file | made by | used by | analysis? |
|---|---|---|---|
| config/corpus_set_aside.json | D-5 and earlier decisions | every stage | no |
| data/corpus/…/duplicates_plan.json | resolve_duplicates.py from the rehearsal's pair table | the apply | no (the pair table is read for duplicate detection only) |
| data/reuse/corpus_dryrun/story_text_diagnosis.json | diagnose_story_text.py from the rehearsal's matches | the design of 2.14 | no (it sets no parameter of the registered analysis) |
| data/corpus/…/box_recurrence.json and box_recurrence/<issue>.json | box_recurrence.py, corpus-wide shingle counts over boxes (set-aside items left out) | assembly 2.14 (R-A) | no (no pairs, dates or topics) |
| pilot_export/box-recurrence-read-2026-11-05/ | box_recurrence_read.py over the evidence and the candidates | the design of R-A | no (it sets the rule's guards, not a parameter of the registered analysis) |
All four are deposited with corpus 1.0 so that the Stage 2 Methods can say what the assembly read.

## 5. Verification sets (from the plan of 2 November)
V1 held-out contents check, 150 issues drawn after the freeze from the corpus minus the 150 development issues, stratified by decade and format, SK reads all, HK 30 (κ) · V2 hand-transcribed control set, 70 pages = 7 decades × 2 scan sources × 5, transcribed by SK and HK and cross-checked, CER/WER by decade and source · V3 the remaining level-2 records of the 30 double-read issues · V4 the near-verbatim review set of the registered run, 300 items in 12 strata, two readers blind · V5 200 verbatim clusters classified by two readers · V6 300 printed author strings checked against the author table · V7 the duplicate decisions (done, 10 by hand).

## 6. The author table (A5)
Columns: author_as_printed, author_norm (case, punctuation, initials, titles stripped), writer_id (ISFDB / FictionMags), pseudonym_of, house_name flag, confidence, source. Order: normalisation and OCR-variant merging first (the shared corpus gets author_norm), then the reference-work resolution; house names unresolved stay author-unknown, as main.tex 4.1 provides. V6 checks 300.

## 7. The reuse stage: what is added before the registered run
Reprints consolidated under the earliest witness and reported apart (3.1) · clusters defined on the identity of the passage with an overlap threshold (the rehearsal's chaining) · the lemma-based and passage-level tiers of 3.2 · masked and full topic similarity, the OCR-quality score per story, sampling weights in the two-part model (4.1) · a settings file (seeds 6/7/8; floor 10 with 15, 20; commonplace cap 50 with 20, 100; cluster overlap; K 5/10/20; the alignment keep rule; review-set and background sample sizes) deposited with the build record before the run.

## 8. The freeze and the release
Order: 2.14 → author_norm and the quality score → the release export (stories.jsonl, issues.csv, manifest.json) → manifest and datasheet (counts by decade, magazine, kind and set-aside class; the four evidence files; known limits: coverless files, issues without a contents page, OCR error rates) → **corpus 1.0** → Zenodo (restricted at first if needed) with the code tag and the settings file. A dated snapshot for the co-authors is the same export, labelled `1.0rc-<date>` and replaced by 1.0.

## 9. After acceptance
The registered run from the settings file on corpus 1.0; V4 and V5; the model with weights and the sensitivity grid; the case studies; the Stage 2 manuscript with the deviation note (the two rehearsals, their dates, the figures that were read).

## 10. Decisions of 4 November
1. §3.4 (ii): the guard stays; the paratext rules reach the 381 guarded items as a post-process.
2. V2 at 70 pages.
3. Whether the co-authors receive a `1.0rc` snapshot before the freeze or wait for 1.0: to be settled at the meeting.
4. HK is the second reader for V1 and V4.

## 10a. Decisions of 5 November (R-A, from the reading)
1. The position of a recurring box in the record is no test; the head/tail condition of the first design is dropped.
2. Protection by partners: the same work title; every partner a story by the same author; stories only without a selling cue (`commonplace?`). The same author alone does not protect a box.
3. The record guard stays: half or more recurring, or fewer than 200 words remaining → flagged, not split.
4. The recurrence is recounted without the set-aside items before the regression and the upgrade; the count of 4 November is kept as the first reading's evidence. (Done 5 November 15:19: 6,740 issues, 619,159 recurring boxes, of them 216,672 in story records — the count with the set-aside items had 458,333: the duplicate scans had doubled the evidence.)
5. From the first regression (5 November, 90 issues): narrative prose is split only when found in six or more other issues (patch 0047); the one-cue ownership and masthead regex names the family of a split recurring box.
6. From the second regression: below six other issues the evidence is weak — a reprint is recognised record by record (three or more boxes sharing a partner issue), and a weakly recurring box is split only with a selling cue (patch 0048).

## 11. Timeline from 4 November (working days)
2.14 (7) → author_norm (2) ‖ quality score (1) → release export (1) → manifest, datasheet, freeze (3) → Zenodo (0.5): **about 3 weeks** to corpus 1.0. Then V1 (7 + 1.5) ‖ V2 (5 + 5, can start now) ‖ author table (9) ‖ 3.2 tiers (8) ‖ reuse-stage additions (10) → settings file and analysis plan (3) → manuscript preparation (6): about 6–7 weeks more. Phase B after acceptance: about 9 weeks.
