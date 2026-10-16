# Reading the verification sample — a guide for the readers

For Sujin and Heejin (15 October 2026; plan section 6.1; site page /verify/sample, v0.22.0 onward).

## What this is for

The machine cut every issue of the corpus into records (a story, a serial part, a poem, a feature, an advertisement …)
from the page scans. How often it got that right is a number the data paper has to report. We measure it on 150
issues drawn at random from the corpus (at least two from every decade-and-genre cell), read by people against the
scans. The accuracy figure is the agreement between the machine's records and the records you verified, by decade and
genre. Every fifth issue in the reading order is read by a second reader too, so that we can also report how far two
readers agree.

## Where

Log in at https://pulp.digihumeng.org and open WORKROOM → Verification. The table lists the 150 issues in reading
order (the order mixes the genres, so the first weeks are not all one genre). Take the first issue marked "to do";
when you are done it shows "done" with your name. Open the issue; the workbench shows the records on the left and the
scan on the right, as for the pilot issues.

## What to do on each issue

1. Start from the contents page. Every piece it lists — stories, serial parts, poems, features, departments — must have
   a record that starts on the right page with the right title and author. A piece without a record: make one from its
   boxes. A record for something the contents page does not list (a filler, a house advertisement) is fine if it is
   typed as what it is.
2. Every record holds its own boxes and nothing else: no box of another piece, no advertisement, no page furniture
   (running heads, page numbers). A story that continues after an advertisement or a filler is one record, not two.
3. The roles are right: title, by-line, teaser, chapter headings, captions, notices. The reading text of a record is the
   piece's own text.
4. A serial part carries its part number and its work's title; advertisements and the publisher's own matter are typed
   as such.
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

The Verification page shows progress by decade and genre and, once the audit has run (pipeline/s11_corrections_audit.py
--corpus --sample), the assembly's accuracy by stratum: a record is "exact" when you left it as the machine made it,
"same boxes" when only a role or a field was corrected. Every action is recorded under your name with its time
(data/annotations/<issue>.jsonl); nothing is overwritten.
