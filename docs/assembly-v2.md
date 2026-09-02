# Assembly v2: rules from the printed page, and how the methods compare

Written 2026-09-02. Companion to docs/assembly-notes.md (the problems the
annotators found) and the /assembly page of the site (the live comparison).

## Why

The first assembler (pipeline/s07_articles.py) asks a language model,
page by page, to group the layout regions into units and then to stitch
the units into articles. On the ten pilot issues it made the same
mistakes over and over, and Sujin Kang's reports of 22–31 August named
them precisely: it broke a story at every chapter head; it missed the
boundary where a new story began with its own title and by-line; it
took the ornamental lettering the OCR garbled ("Hemur Grail") as a
title although the real title and by-line stood on the same page; it
left boxes in the middle of a story assigned to nothing; it kept the
contents page as an article; it listed some scan regions under two
records. Her suggestion was structural: every issue prints its own
ground truth on the contents page, and the printed page carries its
conventions — running heads, page numbers, chapter heads, "continued
from" notices — that a reader uses without thinking. Assembly v2 is
those conventions written down.

## The rules (pipeline/s08_assemble_rules.py)

Every page is first read for what it is. The printed page number (the
folio) comes from the header or footer; the offset between scan page and
printed page comes from the most common difference, and a folio index
maps every printed number to its scan page, so a leaf the scanner
inserted does not shift the whole issue. The contents page is found by
its word and its list of page numbers and parsed in the layouts the
pilot magazines use: one entry per line with dot leaders (Weird Tales,
Thrilling Detective, Western Story), title / author / page on three
lines (Astounding), title with the page and "by Author" below (Galaxy).
Each entry gives a title, an author, a printed start page, a blurb and a
section label; the section label and the blurb decide the type (story,
serial part, verse, letters, feature). Running heads (the magazine's
name, or the piece's title) are read from the page edges.

A piece starts where a by-line ("By …", also "By" and the name in two
boxes) stands under a display-size title; the title is the section
header above the by-line, with subtitle lines ("A TWO-PART NOVEL") and a
blurb between them kept apart. A title the OCR damaged is replaced by
the contents page's reading when the two disagree and the contents
page's words are the ones the issue itself uses. Pieces the contents
page lists but no by-line announces — a title and by-line drawn as
lettering the OCR could not read, a department with no author — start
on the page the contents page gives, at the readable title if there is
one, else at the first paragraph that opens with a word in capitals, as
pulp first paragraphs do. A by-line that is only an announcement ("In
the next issue", a book review, the cover strip, the contents page) is
rejected: too little text follows it, or it stands inside a filler
block, or several such by-lines share a page.

Once a piece is open, everything on the following pages belongs to it
until the next start. A section header with no by-line is a chapter
head (tagged, with its chapter title, never a new record); a display
line inside the text that reads like the magazine's own notice ("in the
next issue", the magazine's name, advertisement words) is a filler and
gets its own record until story prose resumes; a display line that
reads like neither is a headline inside the story ("DOOM LAUNCH ADRIFT
ON LAKE") and stays. On a start page the regions above the title — the
illustration's caption, the artist's signature, the display garble —
are page furniture, exactly as the verified records treat them; the
blurb below the by-line is the teaser, kept with the record as metadata
and never as story text. "Continued from page N" sends the text that
follows to the piece that paused at printed page N; the notice itself
is furniture. A full advertisement page inside a piece suspends it; the
piece resumes on the next page that carries narrative prose. Pages
before the first piece and after the last, and advertisement pages that
Weird Tales numbers and gives running heads to, are cut into one record
per display header.

Every text region ends up in exactly one record or in furniture; the
output says so (checks: unassigned regions, double-owned regions). The
records carry, besides the fields the workbench already uses, the roles
of their regions (title, subtitle, author, teaser, chapter, caption,
heading), the teaser text, the contents-page entry they were matched
to, and flags for anything a person should look at ("start placed from
the contents page", "title differs from the contents page", "not on the
contents page", "resumes after advertisement pages").

## The harness (pipeline/s09_assembly_eval.py)

Three yardsticks. The strongest is the set of records a person verified
on the workbench: the candidate's best-overlapping record is scored on
its scan regions (precision, recall, Jaccard, exact match) and on
title, author and type; records a person modified without verifying
are listed but not counted, because they are partial repairs. The
widest is the contents page: every piece it lists, with the scan page
it starts on, should be started there by a record of the right type
with that title and author; no story record should start inside the
piece's span (a chapter split) and the record should not run past the
next piece (a missed boundary); the share of the span's pages the
record touches is its coverage. The third needs no yardstick: regions
owned by two records, text regions owned by none, story records that
begin with a chapter head, story records with no author.

Three candidates are compared: the live assembly (model, s07), the
rules alone (s08), and the rules inside the printed range with the
model's records for the advertisement pages outside it. A fourth — the
model prompted with the rules as constraints — waits for the main
server's GPU lane.

## Results on the ten pilot issues (2 September 2026)

    issue        variant         pieces found title author clean cover xstart over | verif exact  jacc | recs  dbl unas chap noauth
    ALL          model              108    96    73  64/76    50  0.58    213    2 |     1     0  0.98 |  727 1785 1352   34    231
    ALL          rules              108   108   108  87/88   103  0.95      5    0 |     1     1  1.00 |  549    0    0    0      4
    ALL          rules_on_model     108   108   108  87/88   103  0.95      5    0 |     1     1  1.00 |  447    0  512    0     12

Read: of the 108 pieces the ten contents pages list, the rules start
every one on its page with a title that agrees (the model: 96 and 73);
87 of the 88 signed pieces get the right author (the model: 64 of 76);
103 pieces are clean — started, not split, not run over (the model:
50); the records cover 95% of their pieces' pages (58%). Story records
starting inside a piece, which is what a chapter split looks like: 5
against 213; of the five, one is the lead novel of Thrilling Detective
1948-12, which that contents page does not list, and one a reader's
poem inside The Eyrie. No region is owned by two records (the model:
1,785) and no text region by none (1,352). Story records that begin
with a chapter head: none (34). The one record a person has verified
end to end — The Cave of Horror, 231 regions — the rules reproduce
exactly, region for region, title and author included; the model's
record for it had 230 of the 231. Sujin's two large hand repairs in
Astounding 1930-03 (Cold Light, Brigands of the Moon, 195 and 957
regions) agree with the rules' records at Jaccard 0.985 and 0.989. The
hybrid brings nothing the rules do not, and inherits the model's gaps
on the advertisement pages (512 unassigned regions), so the rules alone
are the candidate.

Per issue the picture is the same; the one weak spot is Thrilling
Detective 1948-12, whose stories jump across whole advertisement pages
with and without notices (coverage 0.73), and whose contents page the
OCR half lost. Everything the rules were unsure of is flagged on the
record, so the workbench can show a person where to look first.

## What this changes

Decision of 2026-09-02: the human corrections are archived and the
machine starts clean; the archive stays the yardstick so automation can
be measured against human correction from here on.
scripts/switch_assembly.py does the switch (it moves the live assembly
and the annotation logs into data/assembly_archive/<stamp>/ and copies
the rules' records into place; nothing is deleted; the harness reads
the archive with --yardstick). The site (v0.11.1) reads the machine's
roles, so a v2 record opens on the workbench with its title, author and
teaser already in their sections, and the chapter heads tagged. After
the switch r00 must run again so the export carries the new records
with their teasers.

What remains, in the order it matters: the paraphrase and reuse results
were computed on the model's records and will be recomputed on the
rules' records (r00 → r05); Thrilling Detective's jumps over
advertisement pages without notices need the running-head test
extended to footers and the "suspended piece" rule tightened; the
contents-page parser has seen five layouts and will meet more at
corpus scale — the flags will say when it fails; the model-with-rules
variant is worth running on the main server for the pages where the
rules have no anchor (no contents page, no by-line, no running head).
The rules will be judged again every time a person verifies a record:
the harness runs in seconds.

## How to run

    python3 pipeline/s08_assemble_rules.py --all          # data/assembly_v2/{rules,rules_on_model}/<issue>/
    python3 pipeline/s09_assembly_eval.py --all           # data/assembly_v2/eval.json and the table
    python3 scripts/switch_assembly.py --variant rules --dry-run
    python3 scripts/switch_assembly.py --variant rules    # archive and go live (decision of 2026-09-02)
    python3 pipeline/s09_assembly_eval.py --all --yardstick data/assembly_archive/<stamp>

Both stages have --selftest / --issue forms; the site's /assembly page
shows eval.json, per issue and per piece, with links to the scans.
