# Decisions that shape the numbers

Dated, in Heejin Kim's words where they were his; the journal in the
Dropbox share carries the full context of each.

- 2026-08-20 — The ten pilot issues are the declared development set of
  the Registered Report: no other Internet Archive data is downloaded
  or analysed before protocol acceptance (approval gate in the
  downloader).
- 2026-08-22 — Corpus text = layout-route OCR plus deterministic rules;
  model-based cleanup dropped from the corpus path (pilot measurement:
  it adds nothing and occasionally harms). The weak point is assembly,
  so human verification on the workbench is the present focus.
- 2026-08-22 — Every human action on an article is an append-only event
  under the annotator's name; the machine output is never edited.
- 2026-08-24 — The shared protocol document is THE document the project
  follows; read-only from this side. The corpus may be its own paper.
- 2026-08-27 — Text-reuse pipeline: compare both story sets (machine-
  assembled and human-verified); seed lengths 6, 7, 8 all; planted
  synthetic reuse for validation, kept separate and marked; results go
  on the site.
- 2026-08-31 — Paraphrase stage: 50-word passages, step 25 (100/50 once
  as sensitivity); BAAI/bge-small-en-v1.5; 10 neighbours (5, 20 as
  sensitivity); word-level local alignment with one-edit tolerance for
  words of five or more letters; keep alignments of 20+ columns at 60%+
  identity until the hand-reviewed set says otherwise.
- 2026-08-31 — Background stage: all pairs at pilot scale plus the
  stratified sampler run as a check; topic similarity = TF-IDF cosine on
  reuse-masked text (embedding cosine as a second column); a full first
  version of the two-part hierarchical model as a proposal for the
  statistical design; pair facts: later date, years apart, topic
  quartile, same author / magazine / publisher / genre / format.
- 2026-08-31 — Site: overview → lists → entity pages → raw records;
  server-drawn SVG charts, no libraries; the development side (workroom)
  and the service side (explorer) separated in the navigation; raw JSON
  views for members and the read-only data door for automated reading.
- 2026-08-31 — Only one story was human-verified at the time of the
  first run, so the second story set is "corrected" (verified or
  modified) until the correction sprint delivers more.
