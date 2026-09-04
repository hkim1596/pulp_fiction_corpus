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

## Stage 0 — the survey of the collection

Before any page is downloaded, the archive's own metadata is read for
every item of its pulp collection: the search index for all of them
(28,286 items on 3 September 2026: language, sub-collections, dates,
page counts) and each item's own record for who uploaded it and when,
which curator admitted it, which OCR engine produced the archive's
text, and what language that OCR detected. No page image and no text is
fetched. This is the history of the collection's transmission and the
frame the corpus is measured against: the working corpus is the items
in English or with no language given, and the study's frame the fiction
magazines among them (not the dime novels, comics, film and general
magazines filed in the same collection). The collection page shows the
sample by decade, genre, magazine, publisher and language; the datasheet
documents the corpus in the datasheet form.

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

## Stage 7 — assembling the article database

The final form of the corpus is not pages but articles: every separately
printed unit — story, serial installment, poem, feature, letters page, and
advertisement — becomes its own addressable record, findable by title or
author. A language model receives each page's segments (their layout labels,
positions, and text) and groups them into units: an ornamental display title
is joined to the body text it introduces and recorded as that article's
title, exactly as printed; a by-line becomes the author, exactly as printed;
page numbers and running heads are recorded as page furniture; anything the
model cannot place goes to an unsorted list for human review — nothing is
silently dropped. A second pass stitches units across pages, since stories
and serials rarely end on the page where they begin. Each finished article
carries its type, title, author, page range, assembled and rule-cleaned
text, and the exact scan regions it was built from, so every sentence can be
traced back to its place on the page.

Since 4 September 2026 the rules engine (assembly v2.2) also reads the
units the way the annotators sort them. A record is, at the top level,
the contents page, an article the contents page lists, or a non-article
it does not; its type is story, poem, feature, letters, house (the
publisher's own matter — next-issue announcements, story excerpts,
coupons and ballots, subscription appeals, the masthead), ad (an outside
advertiser), toc or other. A serial instalment is a story with serial
fields (the part label as printed, the part number and total when known,
the work's title without the marker), and instalments of one work are
linked across issues when both are in the corpus. Inside a story every
box carries a role: title, subtitle, author, body text, chapter
information, or paratext — the teaser (the blurb), the synopsis (the
recap printed on a later instalment) and notes (credits, "Illustrated
by", a tail line) — of which only the body text and the chapter
information are reading text; the synopsis is kept but left out of the
reading text and of the text-reuse inventory, as the house and
advertising records are. Standing departments are recognised from a
per-magazine list, and a letters page must contain signed letters.

## The two corpora

The export writes what the protocol names: a story-level corpus — the
story records of fifty words or more, each linked to its author and its
issue, its reading text the body and the chapter apparatus — and a
parallel corpus of everything else: advertisements, house matter,
contents pages, features, letters pages, poems, and story records too
short to be stories. The reuse stages read the story-level corpus only;
the parallel corpus is kept for the study of the magazines and for the
checks (an announcement that quotes a story is linked to it and stays
out of the reuse inventory).

## Human verification: the annotation layer

The machine's assembly is treated as a first draft. Every article page on
this site is also an annotation tool: a named annotator sees the separately
recognized pieces an article was built from, in their current order, each
linked to its scan page — and can reorder them, expel a piece that is not
story text (a "Continued from page" notice, a stray page number), detach a
wrongly joined piece into its own article, move a piece to the story it
belongs to, merge articles, and correct the title, author, type, or the text
itself. When an article is right, the annotator marks it verified.

Two more hand-review tools follow the protocol's own steps. The
paraphrase review shows readers pairs of passages the paraphrase
detector drew from its candidates across the whole range it sees, and
records each reader's judgment (paraphrase or copy, not, unsure) in an
append-only log; the settings of the detector are calibrated against
those judgments and frozen. The cases page keeps the clusters and pairs
marked as cases for the literary genealogies, with their notes.

Three rules make this trustworthy. First, the machine output is never
edited in place: every human action is one line in an append-only log, and
what the site shows is the machine draft with those actions replayed on
top — the original is always recoverable. Second, every action carries the
name of the account that made it, and each article shows its status:
automatic, modified (with the names of who changed it), or verified (with
the name and time). Third, the whole record is public to the team on the
activity page. Guest access (the shared passcode) can read everything but
change nothing; annotator accounts are created by the project lead.

## What the website shows

Every page of every pilot issue, as a side-by-side view: the scan with the
detected layout drawn on it, next to the text at any chosen stage, with the
changes between stages highlighted. Plus this method page, the timing table,
per-stage downloads, and a feedback log. The site is the project's working
surface for collaborators; after development it can be closed or archived.
