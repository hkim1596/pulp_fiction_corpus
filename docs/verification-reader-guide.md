# Reading the verification sample — a guide for the readers

For Sujin and Heejin (15 October 2026; plan section 6.1; site page /verify/sample, v0.22.0 onward). Two levels of
reading since 19 October 2026 (Sujin's decision 2): the contents check on every issue first, the full verification as
time allows.

## What this is for

The machine cut every issue of the corpus into records (a story, a serial part, a poem, a feature, an advertisement …)
from the page scans. How often it got that right is a number the data paper has to report. We measure it on 150
issues drawn at random from the corpus (at least two from every decade-and-genre cell), read by people against the
scans. The accuracy figure is the agreement between the machine's records and the records you verified, by decade and
genre. Every fifth issue in the reading order is read by a second reader too, so that we can also report how far two
readers agree.

## Where

Log in at https://pulp.digihumeng.org and open WORKROOM → Verification. The table lists the 150 issues in reading
order (the order mixes the genres, so the first weeks are not all one genre). The *contents* column is level 1: CHECK
opens the contents check of the issue, *checked* means it is done. The *state* column is level 2: take the first issue
marked "to do"; when you are done it shows "done" with your name. Open the issue; the workbench shows the records on
the left and the scan on the right, as for the pilot issues.

## Level 1 — the contents check (about fifteen minutes an issue)

This is the check the protocol's Supplement A names: at least 95 percent of the entries on the contents pages must be
assembled with the correct start page, title and author. It is done on every issue of the sample before the full
verification, because the data paper's accuracy claim rests on it and it takes a quarter of an hour.

Open CHECK in the contents column. The page shows the scan of the contents page on the left and, on the right, the
entries the machine read from it — title, author, printed page — each with the record the machine made for it (its
title, author, type and the scan where it starts; the record and the page open in a new tab). For each entry decide,
against the scan and, where in doubt, the record's first page:

- **RIGHT AS MADE** — a record starts on the right page with the right title and author, as the machine left it.
- **FIXED** — you had to correct the record on the workbench (its start, its title or its author; a split or a join).
  Make the correction there first, then mark the entry.
- **MISSING** — the piece has no record (make one from its boxes on the workbench if you can, and mark MISSING either way:
  the figure measures what the machine did).
- **NOT A PIECE** — the entry is not a piece at all: a cover line, an illustration credit, a department heading with
  nothing under it, a misreading of the page. These leave the count.

Then, below the table: how many pieces the contents page lists that the machine's reading of the page *missed* (and how
many of those nevertheless have a right record — a piece can be assembled from its own title and by-line without the
contents page), and how many pieces the issue has that the contents page does not list (fillers, house matter; this
number is for information). A note on the issue is optional. The button MARK THE UNMARKED ENTRIES RIGHT AS MADE fills
the rest once you have marked the exceptions. Save: the Verification page shows the issue as *checked* with your name,
and the running figure — listed pieces assembled right, over all checked issues — below the table.

The check is saved under your name with its time (data/review/contents_check.jsonl); saving again replaces your earlier
check in the counts, and the earlier line stays in the log. A second reader's check of the same issue is kept beside the
first; the figure uses the first reader's, and the share of entries on which the two agree is reported.

## Level 2 — the full verification (about an hour an issue)

1. Start from the contents page. Every piece it lists — stories, serial parts, poems, features, departments — must have
   a record that starts on the right page with the right title and author. A piece without a record: make one from its
   boxes. A record for something the contents page does not list (a filler, a house advertisement) is fine if it is
   typed as what it is.
2. Every record holds its own boxes and nothing else: no box of another piece, no advertisement, no page furniture
   (running heads, page numbers). A story that continues after an advertisement or a filler is one record, not two.
3. The roles are right: title, by-line, teaser, chapter headings, captions, notices. The reading text of a record is the
   piece's own text.
4. A serial part carries its part number and its work's title; advertisements and the publisher's own matter are typed
   as such. Two rules from the first sample issues (Sujin, 17–18 October): a book cover is `other`, not `house`; a
   "What's Next" or next-issue section is a `teaser` box when it stands in front of a serial or a story and a `house`
   record when it fills its own page.
5. When a record is right as the machine made it, or once you have fixed it, mark it VERIFIED. When every record of the
   issue is verified, the issue is done.

## What not to spend time on

- OCR misreadings inside a box (a wrong letter, a broken word). The reading accuracy is measured by another sample (the
  control set); here the question is which boxes belong to which piece.
- The boxes' exact edges on the scan, unless a box plainly belongs to another piece.
- Dates, author spellings and other metadata on the issue page: note them in the feedback form if they are wrong, do
  not stop for them.

An issue of 130 pages takes about an hour the first time and less once you know the magazine's layout. Stop when you
are tired: a verified record is a record, and the issue keeps its "started" state until you come back.

## The second reader

Issues with "yes" in the second-reader column are read by both of you. The site holds one live state per issue, so the
second reader sees the issue as the first reader left it: go through it with the same checklist, change what you
disagree with, verify every record again, and write one line in the feedback form for each change. The reader
disagreement we report is the share of the first reader's verified records that the second reader changed; it is not a
fully independent second reading, and the data paper says so.

## Where the numbers appear

The Verification page shows progress by decade and genre at both levels: the contents check's figure (listed pieces
assembled right, by stratum and in all, against the 95 percent the protocol asks) as soon as issues are checked, and,
once the audit has run (pipeline/s11_corrections_audit.py --corpus --sample), the assembly's accuracy on the verified
records by stratum: a record is "exact" when you left it as the machine made it, "same boxes" when only a role or a
field was corrected. Every action is recorded under your name with its time (data/annotations/<issue>.jsonl,
data/review/contents_check.jsonl); nothing is overwritten.

## Issues that leave the sample

An issue found not to be a magazine (a book, a dime-novel number, a single story, a comic — decision 1 of 19 October
2026) is set aside from the corpus and its slot in the sample is redrawn in the same decade-and-genre cell
(scripts/draw_verification_sample.py --replace); the replacement shows "(replaces a set-aside issue)" in the table and
takes the slot's place in the reading order. Work done on the issue that left stays in the logs and does not count.
