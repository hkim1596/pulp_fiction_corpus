# Method — how the pilot processes a pulp magazine issue

This page describes exactly what the code in this repository does, stage by
stage. It is rendered live on the website at /method, so the description and
the implementation move together. Comment through the feedback box; the method
is revised from feedback and then frozen for the Registered Report protocol.

## What the pilot is for

Ten issues (five magazines, 1925-1952, three genres, pulp and digest formats)
are processed end to end. Four issues have human-proofread text on Project
Gutenberg, so for them every stage's output can be scored against a correct
answer. The pilot answers two questions before the full corpus is attempted:
which method reads pulp pages best, and what does each method cost in time and
money at the scale of the whole archive.

## Stage 1 — download

For each approved issue we fetch from the Internet Archive: the item metadata,
the Archive's own OCR text (our baseline), the positional OCR, and the page
images. Page images are converted to working PNGs (2,200 pixels tall). Fetches
are sequential and throttled, with a contact address in the user agent, and
every fetch is logged to a manifest. The downloader refuses to run until the
issue list is explicitly approved in the configuration file: the ten issues are
the project's declared development set under the Registered Report rules.

## Stage 2 — route A: layout detection, then region reading

Following the American Stories approach for historical newspapers: a layout
model finds the regions of each page (text columns, titles, pictures, page
furniture), a recognition model reads the text regions line by line, and
reading order follows column geometry. Every line keeps its page number and
box coordinates, so any sentence in the corpus can be traced back to the exact
place on the scan where it is printed. Picture and furniture regions are
excluded from the text but kept in the page record.

## Stage 3 — route B: whole-page reading by a vision language model

The same pages are also read by a vision language model served on our own GPU
machine: the model receives the page image and returns a transcription in
reading order. This route is simpler and often strong on two-column prose, but
it does not produce line coordinates. The pilot runs both routes on the same
pages so they can be compared on accuracy, speed, and cost.

## Stage 4 — rule-based cleanup

Deterministic rules, applied in a fixed order: normalize damaged characters
(ligatures, soft hyphens); drop lines that are scan noise; remove running
heads and page numbers (lines that repeat across many pages of the issue);
rejoin words split by line-break hyphens when the joined form is a real word;
unwrap hard line breaks into paragraphs. Every removal and every join is
written to a change log — nothing is silently deleted, and every change can be
reviewed on the website.

## Stage 5 — LLM cleanup, two models compared

The rule-cleaned text goes to a language model with one instruction: fix
optical character recognition errors only — never rephrase, never modernize,
never add or remove. Two backends run on the same input: a local Qwen model on
our own GPUs (free, private) and Claude through its API (paid). A guardrail
compares each corrected page against its input and rejects any correction that
changed too much, keeping the rule-cleaned page and flagging it for review.
The pilot reports, per backend: error-rate improvement, seconds per page, and
cost per page.

## Stage 6 — measurement

For the four gold issues, character error rate and word error rate are
computed against the proofread text at every stage, so the report shows
exactly how much each step helps, per decade and per format. For all ten
issues, a dictionary-word rate tracks quality where no gold exists. All
wall-clock timings are collected per stage and projected to full-corpus scale
on the timing page.

## What the website shows

Every page of every pilot issue, as a side-by-side view: the scan with the
detected layout drawn on it, next to the text at any chosen stage, with the
changes between stages highlighted. Plus this method page, the timing table,
per-stage downloads, and a feedback log. The site is the project's working
surface for collaborators; after development it can be closed or archived.
