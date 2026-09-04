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
    python3 scripts/switch_assembly.py --variant rules --refresh --dry-run   # a new run of the same rules,
    python3 scripts/switch_assembly.py --variant rules --refresh             # ids and annotation logs kept

Both stages have --selftest / --issue forms; the site's /assembly page
shows eval.json, per issue and per piece, with links to the scans.

## v2.1 (3 September 2026): the contents page's authority, chapters, advertisements

The second round of feedback (Heejin Kim and Sujin Kang, 2–3 September)
asked for four things of the assembler, and v2.1 does them.

The contents page has authority over title and author. Where v2.0
weighed the page's display lettering against the contents page by the
issue's own vocabulary, v2.1 takes the contents page's words whenever a
record is matched to an entry, and keeps the page's forms beside them
(title_as_printed, author_as_printed; title_source and author_source
say "contents" or "page"). The same words set in a better type case —
"The Beetle Horde" on the page against THE BEETLE HORDE on the
contents page — are not a disagreement, and the page's case is kept.
A strong disagreement (similarity under 0.55 for a title, 0.6 for an
author) is flagged for a person. When two entries by one author fall on
one page (a story and a poem), the entry whose title the page prints
is the match.

Chapters are listed. A chapter head's number and title are split by a
text rule on the region — "CHAPTER II" with "The Thing in the Vault" on
the next line, or "IV. The Coming of the Beast" on one line — into
`chapters: [{number, n, title, page}]`; the regions keep the chapter
role and stay in the text. Subheadings inside a piece that are not
chapter heads (the letter titles in The Eyrie) carry the role heading,
which the workbench shows and sets as "section".

Advertisements carry a class and an advertiser, as Sujin proposed:
house_next_issue (the magazine announcing its next issue: "Coming Next
Month", "In the Next Issue"), house_self (subscriptions, back numbers,
the cover strip and title page, the magazine about itself),
house_sibling (the publisher's other magazines: a magazine name that is
not this one, with sale words, and no company), house_form (the
magazine's own ballot or coupon — "My favorite stories in the May WEIRD
TALES are:", the Eyrie ballot, cut apart from the department around it;
a trade coupon joins the advertisement above it), classified (four or
more category headers with four or more addresses; n_items), and trade
(everything else; the advertiser is the company's signature line —
"Co.", "Inc.", "Institute", "School", "Laboratories" … — read from the
copy, or the name before "Dept."). A house announcement's works are
listed in `announces` (the title and by-line pairs the analysis had
rejected as starts inside the block, and "TITLE, by Author" lines in
the copy); when the block carries 120 or more words of narrative prose
it quotes a story — `contains_excerpt`, with `excerpt_of` the announced
work whose title follows the prose — and the record stays out of the
reuse inventory. Advertisement records have no author; the announced
authors are in `announces`.

Four rules changed with these. An announcement block that begins on a
page with no filler header of its own (the title, the by-line, the blurb
and the quoted excerpt) is house advertising to the end of the page,
and so is the block under a "Coming Next Month" header that the
excerpt follows; before, the excerpt's prose sent the rest of the page
back to the department (Weird Tales 1934-05, page 123). A ballot or
coupon closes an advertisement block, and prose that starts
mid-sentence resumes the piece after it. A page of advertising inside a
piece that paused mid-sentence suspends the piece when the page has no
narrative prose and many selling words (the music school page inside
Headquarters in Thrilling Detective 1948-12, the subscription page
inside The Eyrie); the piece resumes on the next page with prose. What
is printed under a "continued on page N" or "[Turn page]" notice is
advertising, one record per display header (Thrilling Detective's
continuation pages); two display lines with nothing between them are
one headline. A filler header above a title ("NEXT MONTH") stops the
title walk-up, so the announcement's by-line is rejected as a start
rather than taken as a subtitle.

Results on the ten pilot issues, the live v2.0 records against v2.1
(the harness's "live" row is whatever data/articles holds):

    issue        variant         pieces found title author clean cover xstart over | recs  dbl unas chap noauth
    ALL          live (v2.0)        108   108   108  87/88   103  0.95      5    0 |  549    0    0    0      4
    ALL          rules (v2.1)       108   108   108  88/88   105  0.95      3    0 |  559    0    0    0      4

Two of the five "story starts inside a piece" were announcements typed
as stories (Wizard's Isle on the last page of The Eyrie; Let's Have
Some Murder under Next Issue's Headliners) and are advertisements now;
one signed piece more gets its author from the contents page. Across
the ten issues 559 records: 370 trade, 24 house_self, 21
house_next_issue, 17 classified, 1 house_form; one house announcement
quotes a story (The Colossus of Ylourgne, Weird Tales 1934-05). The
verified Cave of Horror is still reproduced exactly. Coverage moves a
little because advertisement pages inside a piece's span no longer
count as the piece's pages — the metric's limitation, not a loss.

Known limits of the classifier, for the next round: an advertisement
that quotes the magazine's name in a blurb ("Readers of this magazine
will want to buy this book" — a university press advertisement in
Galaxy) reads as house_self; a book advertisement with several display
lines is cut into several records; the pilot has few sibling-magazine
advertisements to test house_sibling on; small classified blocks after
an announcement can read as "classified" when they are three unrelated
advertisements. The workbench's record facts form corrects all of these
by hand, and the harness will say when the rules catch up.

Going live: the plain switch of 2 September archived the annotation
logs because the method changed; a new version of the same rules goes
live with `scripts/switch_assembly.py --refresh`, which keeps the record
ids (same regions, or the best overlap) and the logs, and refuses an
issue whose annotated records would change regions unless --force. On 3
September the one annotated record (The Stolen Body, Weird Tales
1925-11: three actions by Heejin) lost page 133's six regions to a
house advertisement that v2.1 reads correctly; the refresh was forced
and the replay re-placed the annotated region by its key.

## v2.1.1 (3 September, evening): what the audit against the corrections changed

docs/assembly-accuracy.md reads the human corrections record by record
(pipeline/s10_assembly_audit.py). Six rules changed from it: the end of
a piece's column above the next piece's title stays with the piece when
there is evidence the column runs on; blurbs, synopsis headings and a
second setting of the title above the title go to the new piece
(teaser, heading); a repeated title over a "continued from" notice is
furniture; a plain subheading inside a department with selling words
under it is an advertisement, and a filler exits only at narrative
prose; a headline inside advertising matter starts the next
advertisement (stacked headlines joined) and an announcement header runs
on to a later notice on the page; a by-line followed by selling words on
its page is a book or magazine announcement. Human-touched records
identical to the machine's: 9 → 16 of 51; contents-page pieces clean
105 → 106; story starts inside pieces 3 → 2 (a reader's poem inside The
Eyrie and an unlisted lead novel — neither a split).


## v2.1.2 (4 September): after the refresh changed annotated records

The forced refresh of 4 September (v2.1 → v2.1.1) ran while Sujin and
Heejin had been annotating the rules' records since 3 September, and it
changed sixteen records they had touched, two of them verified. The
effective records (machine record plus the replayed log) were compared
before and after: fourteen came out identical or better because the
annotators' actions replay by region key; two verified records changed
(A Loaf of Bifield: the credit line under the by-line fell into the
reading text; The Stolen Body: three regions the new rules read as
furniture and as the next-month announcement left it); and The
Demolished Man lost its last page because a book announcement on the
issue's last page, now rightly read as advertising, had been the page
that kept the issue's page range open — the scan prints page 156 after
158, and the narrowed range left that leaf outside.

What changed in the rules, with the annotators' own conventions as the
measure (Sujin's records of 3–4 September, Heejin's decisions of 4
September):

- Above a story head: a second setting of the title is `title` (Sujin
  marks both settings), the blurb `teaser` (the record's teaser field
  keeps the first), a kicker or type label `subtitle`, a synopsis
  heading such as "The Story Thus Far" `heading`; a short sentence with
  a full stop is the illustration's caption. Before, all of these were
  `heading`, which is reading text.
- The credit line under the by-line ("Author of 'Men Like Gods,' etc.")
  is never reading text nor furniture: it carries the `teaser` role
  (Heejin's choice; he had marked it so on The Stolen Body) and its
  text is kept as `author_credit`, which r00 exports. Before, it was the
  teaser when the story had no blurb, reading text when it had, and
  furniture when the title was set inside the illustration and the line
  was labelled a caption (Weird Tales 1925-11).
- Inside a story, a display line that is not a chapter head has no
  role — Sujin cleared the machine's `heading` on "Consternation",
  "ABOLISH:", "EMERGENCY BUSINESS" in The Demolished Man: the author's
  typography, not sections. `heading` stays for departments (a letter's
  title in The Eyrie, a topic in a feature) and for synopsis headings
  and forewords. Sixty-three roles changed on the ten issues.
- Chapter heads: "PART ONE" (a number word), "I.—THE MURDER CLUB" (a
  roman number, a dash, the title) and '2. "Smash It, You Fool!"' are
  chapter heads now; "L. L. COOKE Chief Engineer" no longer is (an
  initial before a full stop is not a chapter number). Advertisement
  records no longer carry a chapters list.
- The page range: a page just past the last numbered page whose printed
  number belongs a few pages before the end is a leaf the scan has out
  of order; it is inside the range, and the record holding it is
  flagged ("the scan has a leaf out of order … the text of this record
  is in scan order, not reading order").

Harness against the 2 September yardstick: unchanged (108/108 found,
88/88 authors, 106 clean, cover 0.95, 2 story starts inside pieces); 583
records (585 before: the two Cooke advertisements on p. 3 of Weird Tales
1925-11 are one record, and the loose last page of The Demolished Man is
back in the serial). Twenty-two records carry an author_credit.

The refresh itself changed (scripts/switch_assembly.py): a verified
record is never changed by a refresh — its live machine record is
carried over, its regions leave the candidate, leftovers go to
`unsorted` — and every annotated record is reported one line each,
forced or not. `--verified-from data/assembly_archive/20260904_100220_refresh`
restores, for the records verified before that refresh, the copy the
person saw. Checked in the sandbox on the main server's logs and
records of 4 September 10:30: every verified record (seven, including
the three verified after the refresh) comes out identical to what the
person saw; the annotated records differ only by the intended role
changes and the last page of The Demolished Man.

## v2.2 (4 September, the third round): what the annotations and the feedback asked for

Thirty-two feedback entries (Sujin's of 3 September, Heejin's of 4
September) and all 335 annotation events of 3–4 September, each read
against what the machine had done on that record, gave the rules their
next version. Decisions of 4 September (docs/decisions.md): the type
house and no serial_part; Heejin's hierarchy on the workbench with
Paratext as a group of teaser, synopsis and note; chapter titles by
series; "text in picture" as a kind of furniture; departments from a
list; sentence case, log-in times, one collection-wide progress bar.

What the annotations taught the rules, and what changed:

- "Illustrated by WILLER" (Sujin: teaser) → the note role and the
  illustrator field. "Author of …" → note and author_credit (it was
  teaser in the morning's v2.1.2; the records people had already marked
  keep their teaser).
- "—ROBERT A. HEINLEIN", "—H. B. FYFE" at the end of a story (Sujin:
  not story text) → furniture "the author's name repeated at the end";
  "—GROFF CONKLIN" at the end of an unsigned column → the author. "The
  End." → furniture "end mark". "To be continued in next week's issue."
  (Sujin: not story text) → furniture "continuation notice", the serial
  field noted. "Watch for the next story in this thrilling series" →
  note.
- "Conducted by / HELEN RIVERS" (Sujin: author, both boxes) → the
  by-line forms Conducted by, Edited by, Compiled by, Arranged by, As
  told to, in one box or two.
- Titles set in two boxes — "THE YEAR" / "OF THE JACKPOT", "DEAD MEN" /
  "TALK" (across two pages), "Ace Hart" / "Loses His Man", "the" / "7th
  ORDER" — are joined from the contents page (numerals spelled out for
  the comparison); on the facing page the illustrator's credit and the
  story's first paragraphs come along (The Seventh Order begins under a
  bare "the" a page before its title). "A Complete Book-Length Novel"
  beside the title's second box → subtitle. "Conclusion" printed as a
  running head over The Demolished Man → subtitle and the part label.
- "The Murder Monster [Part I]" → title The Murder Monster, part label
  Part I, part 1 (Sujin: no part number in the title string); "Braggin'
  Bill, Fighter (Poem)" → a poem titled Braggin' Bill, Fighter. Titles
  the contents page sets in capitals → title case (the printed form is
  kept): The Year of the Jackpot, Catch That Martian, Galaxy's Five Star
  Shelf.
- Wild West Weekly's "STAMPEDE OF THE Z BAR L." and its kind (Sujin:
  chapter title) → chapter titles, by the series rule: display lines of
  one form (capitals, a full stop) on different pages of a story;
  Bester's "Consternation / Alarm / Conviction" and the quoted newspaper
  headlines (Heejin: "Sensation") stay reading text without a role.
- Desert Pirates part II: the recap between the by-line and "CHAPTER
  V." (Sujin: not the story; she wants a synopsis role) → synopsis, 197
  words, out of the reading text; The Demolished Man's synopsis under
  its "SYNOPSIS" heading, 912 words over three pages, likewise.
- Headquarters and The Round-up (Sujin: feature, not letters) → feature
  by config/departments.json, which also names the conductor (The
  Editor, The Editors, J. A. Thompson …); "Coming Up . . ." / "IN THE
  APRIL GALAXY" → a house record titled In the April Galaxy in the
  department Coming Up; "NEXT MONTH / —The— / Tenants of Broussac" →
  The Tenants of Broussac; "COMIN' NEXT WEEK!", "DON'T FAIL TO READ
  THESE THRILLERS!" and "NEXT QUESTION" (Sujin: house) → house records
  cut out of the departments they were printed in.
- The Rosicrucian and birth-control advertisements in the right column
  beside The Return of the Undead (Heejin marked ten boxes not story
  text) → advertisement records by the column rule: a block in a column
  the piece's prose does not use, opening with a headline or a priced
  line, carrying a price or a postal address, with no dialogue and no
  narrative paragraph. Eleven such records on the ten issues, none of
  them story text; the first versions of the rule took story columns
  and were tightened on the Cave of Horror (the verified record stays
  exact).
- "LOVE MALES DATER ME & MY CLAN" over a drawing (Sujin: loose boxes;
  Heejin: a category, text in picture) → furniture "text inside an
  illustration": an illustration page (no prose, a few short lines,
  most of them not words) or a text box inside a picture box; title
  pages over a full-page drawing are protected (a by-line or a contents
  title on the page).

Cross-issue pass (cross_issue, after --all): instalments of one work
linked across issues by magazine, work title and author (work_id,
prev/next by issue and title, part numbers from the order of the issues
when the page gives none, the total when the last is marked as the
conclusion); house records matched by 8-word runs to the story they
quote wherever the corpus holds it (the excerpt flag is the machine's).
The pilot has no two instalments of one work and no quoted story in the
corpus, so both counts are zero here; the self-test exercises both on
made-up issues.

Harness against the 2 September yardstick: unchanged (108/108 found,
88/88 authors, 106 clean, cover 0.95, 2 story starts inside pieces, the
verified Cave of Horror exact) — the harness now lets a record begin on
the facing page of its own head. 588 records on the ten issues: 79
stories, 24 features, 5 poems, 3 letters, 53 house, 411 ad, 11 toc, 2
other; 38 notes, 32 synopsis regions in 4 records, 11 illustrators, 8
serial instalments with fields, 37 records with a department, 22 boxes
of text inside illustrations.
