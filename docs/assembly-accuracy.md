# How accurate is the automatic assembly? — the picture on 3 September 2026

Written for Heejin Kim's question of 3 September: "Analyze the accuracy
of automatic assembly. Give me some clear picture of how automatic
assembly is doing. What is still inaccurate compared to human assembly."
The numbers come from the harness (pipeline/s09_assembly_eval.py) and
from a new audit (pipeline/s10_assembly_audit.py) that looks at every
record a person corrected and says, region by region, where the machine
differs and why. Both run in seconds and can be rerun after every
change; the /assembly page of the site shows the harness.

## The short answer

On the ten pilot issues the rules engine (assembly v2.1.1) finds every
one of the 108 pieces the contents pages list, starts each on its page
with the contents page's title and author, and leaves no text region
unassigned and none shared between two records. Against the records
that people repaired to the end, the machine's records are the same
records: the one verified story is reproduced exactly; of the thirteen
stories, poems and departments Sujin Kang repaired in Weird Tales
1934-05, five are identical and the other eight differ only in page
furniture — running heads, page numbers and printer's marks she kept in
the story, a caption, chapter numbers she took out — never in story
text. The
things that are still wrong are of three kinds: a few real machine
mistakes that the audit found and today's version fixed; conventions
that people and the machine apply differently (what counts as story
text); and the limits of the layout reading underneath (text drawn
inside pictures, blocks the detector missed), which no assembly rule
can repair.

## Three yardsticks, and what each can and cannot say

The contents page. Every issue prints its own list of pieces with their
page numbers. The harness checks that a record of the right kind starts
on that page, that its title and author agree, that no story record
starts inside the piece (a chapter split), that the record does not run
past the next piece, and how many of the piece's pages the record
touches. Since v2.1 the machine takes title and author from the contents
page, so the title and author columns of this yardstick are no longer
an independent test; the start, the split, the overrun and the coverage
still are.

The people. Fifty-one records carry human actions: 34 in Weird Tales
1934-05 (Sujin, 1,340 actions), 12 in Astounding 1930-01, two each in
Weird Tales 1925-11 and Astounding 1930-03, one in Thrilling Detective
1932-02. These corrections were made on the model's assembly of August
and were archived on 2 September (data/assembly_archive/20260902_203349);
the audit replays them through the site's own engine and compares the
result with the rules' records. One record was verified to the end (The
Cave of Horror, 231 regions); the others are "modified", which means a
person worked on them and may or may not have finished. The audit
therefore sorts them: records the person evidently worked through
(their record and the machine's overlap by 90% or more), the small
advertisement records Sujin cut on the advertising pages, and records
that are unfinished repairs or fragments of the old model's assembly.

The structure, which needs no yardstick: regions owned by two records,
text regions owned by none, story records that begin with a chapter
head, story records without an author.

## Yardstick 1: the contents pages of the ten issues

    issue        pieces found clean cover xstart over | records  double unassigned
    wt_1925_11      19    19    18  0.92      1    0 |    68       0       0
    wt_1934_05      12    12    12  0.98      0    0 |    44       0       0
    ast_1930_01      8     8     8  1.00      0    0 |    92       0       0
    ast_1930_03      5     5     5  0.99      0    0 |    71       0       0
    gal_1952_03      8     8     8  0.96      0    0 |    36       0       0
    gal_1952_04      9     9     9  0.97      0    0 |    27       0       0
    td_1932_02      10    10    10  0.93      0    0 |    34       0       0
    td_1948_12       7     7     6  0.73      1    0 |    80       0       0
    ws_1937_02      18    18    18  1.00      0    0 |    64       0       0
    www_1936_02     12    12    12  0.98      0    0 |    69       0       0
    ALL            108   108   106  0.95      2    0 |   585       0       0

(found = a record of the piece's kind starts on the page the contents
page gives; clean = found, no story record starts inside the piece, the
record does not run past the next piece; cover = share of the piece's
pages the record touches; xstart = story records starting inside a
piece.) For comparison, the August model assembly scored 96 found, 50
clean, 213 starts inside pieces, 1,785 regions owned twice and 1,352
owned by nobody.

The two "story records inside a piece" that remain are not chapter
splits. One is a reader's poem printed inside The Eyrie of Weird Tales
1925-11 ("Pity Me!", Bertha Russell), which is a piece of its own that
the contents page does not list; the machine types it story where poem
would be right, because the page gives no "verse" label. The other is
the lead novel of Thrilling Detective 1948-12, which that issue's
contents page lost to the OCR, so the harness counts the novel as
starting "inside" the department before it; the record itself is right.
The low coverage of Thrilling Detective 1948-12 (0.73) is the same
issue's advertising pages inside the stories' spans: a story that jumps
over whole pages of advertisements now resumes correctly on the next
page with narrative prose, and the advertisement pages are no longer
counted as the story's, which the coverage figure treats as a loss.

## Yardstick 2: the records people corrected

Nineteen human-touched records are repairs worked through (the person's
record and the machine's overlap by 90% or more). Jaccard is the share
of regions the two records have in common:

    record                                 status    kind     J      regions  what differs
    The Cave of Horror (ast_1930_01)       verified  story    1.000  231      nothing
    The Stolen Body (wt_1925_11)           modified  story    1.000   77      nothing
    The Eyrie (wt_1934_05)                 modified  letters  1.000   56      nothing
    Incubus, Mementos, Atavism (wt_1934_05) modified poem     1.000   5–7     nothing
    the Eyrie ballot (wt_1934_05)          modified  form     1.000    7      nothing
    Astounding Stories cover strip          modified  ad       1.000    3      nothing
    Satan's Garden (wt_1934_05)            modified  serial   0.998  435      the illustration's caption she kept
    Vampires of the Moon (wt_1934_05)      modified  serial   0.996  226      1 printer's mark ("W. T.—2") she kept
    Brigands of the Moon (ast_1930_03)     modified  serial   0.990  957      the caption, 4 running heads and the "(To be continued)" line she kept; 4 lines of the next-issue announcement of the same serial she put in the story
    The Satanic Piano (wt_1934_05)         modified  story    0.987  237      2 printer's marks she kept; the teaser she marked not story text
    Cold Light (ast_1930_03)               modified  story    0.985  195      the caption and 2 running heads she kept
    Queen of the Black Coast (wt_1934_05)  modified  story    0.983  233      the caption and 2 printer's marks she kept; the verse epigraph she marked not story text
    Scarlet Dream (wt_1934_05)             modified  story    0.976  247      4 running heads and marks she kept; 2 chapter numbers she took out
    The Gray Death (wt_1934_05)            modified  story    0.975  120      2 running heads she kept; a footnote she took out
    The Tomb-Spawn (wt_1934_05)            modified  story    0.968   62      2 running heads she kept
    Bellowing Bamboo (wt_1934_05)          modified  story    0.967  152      5 running heads she kept
    Invisible Death (ast_1930_01)          modified  story    0.932  286      21 paragraphs of the story's last two pages the person split off into an untitled record

Every remaining difference in this group but one is a convention, not
a missed piece of text: 24 running heads, page numbers and printer's
marks ("W. T.—1") that the person kept inside the story and the machine
calls page furniture; four illustration captions the person kept and the
machine calls furniture (the verified record, by contrast, has its
caption as furniture — two annotators decided this differently); a
teaser and a verse epigraph the person marked as not story text where
the machine keeps them with the record (the teaser as metadata, outside
the reading text); two bare chapter numbers and a footnote the person
took out; a "(To be continued)" line; and the four lines of the "In the
Next Issue" announcement that name Brigands of the Moon, which the
person put in the serial and the machine, rightly, in the announcement.
The one exception is Invisible Death, where the person's own split
(the story's last two pages as a separate, untitled record) looks like
an unfinished repair rather than a reading of the page.

The twenty small advertisement records Sujin made on the advertising
pages of Weird Tales 1934-05 are a question of grain, not of accuracy:
she cut the novelty page into one record per item ("Real Live Pet
Turtles", "Novelty French Photo Ring", two regions each); the machine
cuts advertising pages at their display headlines, so its "Fun, Magic
and Mystery" holds three of her items and "Make Your Own Radio Receiving
Set" seven. Eight of her twenty are identical to the machine's. Which
grain the advertising study wants is a decision for the protocol; the
records can be split either way on the workbench.

The twelve remaining human-touched records are not evidence against the
machine. Ten are fragments of the August model's assembly that a person
touched (a title typed, a role marked) but never rejoined — The Beetle
Horde in three pieces, Phantoms of Reality in two, The Stolen Mind in
three, and the 2,235-region block of Thrilling Detective 1932-02 with
one role set; the machine's single records for those stories are what
the contents pages say. One, "Compensation" in Astounding 1930-01, is a
person's record that runs on into the next story ("Tanks", by Murray
Leinster, with its own title and by-line): the machine is right to keep
them apart. One, "A Mysterious Message from the Ether!" on the inside
cover of Weird Tales 1934-05, the person typed as a story; it is the
magazine's own free-book offer, and the machine types it as house
advertising.

Titles, authors and types. Where a person's record and the machine's
overlap, the machine's title differs in one case only ("Satan's Garden
(conclusion)", the contents page's form, against her "Satan's Garden");
the authors agree except for The Eyrie, where Sujin wrote "Editors" and
the machine leaves the field empty. Types differ on the serials: the
machine writes serial_part for the installments of Vampires of the Moon,
Satan's Garden and Brigands of the Moon, as the contents pages and the
"to be continued" notices say; the person wrote story. That is a
vocabulary to settle in the guide, not an error either way.

## What the audit found wrong in the machine, and what today's version does about it

Reading the disputed regions one by one showed a handful of real
mistakes, all in the rules' handling of the edges between pieces:

- The end of a story printed above the next piece's title on the same
  page was thrown away as furniture when its paragraphs were short
  (seven lines of dialogue at the end of Bellowing Bamboo; ten regions
  of Mountain Miracle in Western Story; the same in Empty Holsters,
  Desert Pirates, Pitfall, and two stories of Wild West Weekly). Now,
  when there is evidence that the column runs on (a paragraph that
  starts in lower case, or a long one), the text above the title stays
  with the piece.
- The blurb, the "The Story Thus Far" heading and a second setting of
  the title above a piece's own title went to furniture; now they go to
  the new piece as teaser and heading.
- Advertisements set in the department's own type inside The Eyrie —
  the Rosicrucian "Mind or Matter" advertisement, whose headline is
  drawn in a picture, and two paragraphs of a hair-tonic advertisement
  — stayed in the department; now a plain subheading with selling words
  under it, and copy without narrative prose, are advertising.
- An announcement that carried its blurb after a "Next Month" header
  lost the blurb to the story around it; the block now runs on to the
  next notice on the page.
- A repeated title over a "continued from" notice was tagged a heading;
  it is furniture.
- A book announcement with a by-line on the last page of Galaxy 1952-03
  ("The Current GALAXY Science Fiction Novel … Odd John, by Olaf
  Stapledon … 35c a copy") became a story record by Olaf Stapledon; a
  by-line followed by selling words on its page is an announcement now.

Before these changes 9 of the 51 human-touched records were identical
to the machine's; after them 16 are, and every worked-through story
differs from its human record only in furniture. The contents-page
yardstick moved from 105 clean pieces to 106 and the story records
inside pieces from 3 to 2.

## What is still inaccurate, and what would change it

1. Page furniture and captions are decided by convention, and the two
   annotators decided differently (running heads kept in the story;
   captions in or out). Until the guide fixes one rule, a human record
   and a machine record of the same story will keep differing by these
   few regions. The machine's rule is the verified record's: running
   heads, page numbers, printer's marks and illustration captions are
   furniture; the teaser and the chapter apparatus stay with the record
   (the teaser outside the reading text).
2. Serial installments are typed serial_part; a reader's poem inside a
   department is typed story. The type vocabulary — and whether a poem
   without a "verse" label can be recognised from its lines — is the
   next small rule.
3. Text drawn inside pictures is not read by the layout stage (an
   advertisement's headline, some story titles), and some blocks are
   not detected at all (Sujin's 131E–F, 131Q–R, 131Z26 and after). No
   assembly rule can recover text that was never read; that waits for
   the second reading route (a vision model on the page image).
4. Advertisements: the grain (one record per headline, or one per
   item), and the classifier's known misfires — an advertisement that
   quotes the magazine's name reads as house advertising; a
   multi-headline book advertisement is cut into several records;
   house_sibling has almost no examples in the pilot.
5. Thrilling Detective 1948-12 is the hardest issue: its contents page
   half lost to the OCR, its stories interleaved with advertising on
   every continuation page. Every story starts right and the
   advertisements are now apart, but a person should still read the
   continuation pages there.
6. The reading order inside a page comes from the layout stage; the
   assembler keeps it. Where two columns were read as two pages'
   worth (Sujin's 63C/63N remark), the workbench's order tools are the
   repair.
7. Only five issues have human work, and only one record is verified.
   The picture is sharp for Weird Tales 1934-05 and blurry elsewhere;
   the fastest way to sharpen it is to verify the rules' records — on
   Weird Tales 1934-05 that is now a matter of reading, since the
   records already match Sujin's.

## How to reproduce

    python3 pipeline/s08_assemble_rules.py --all
    python3 pipeline/s09_assembly_eval.py --all --yardstick data/assembly_archive/20260902_203349
    python3 pipeline/s10_assembly_audit.py --yardstick data/assembly_archive/20260902_203349 --show

The audit writes data/assembly_v2/audit.json (every disputed region
with the machine's and the person's reasons) and prints the report.

## Addendum, 4 September: the refresh that changed annotated records

The refresh that put v2.1.1 live on the main server on 4 September
(10:02) ran with `--force`, written the day before for one record of
Heejin's. By then Sujin and Heejin had annotated five issues on the
rules' records (335 events by 10:30 that day) and verified four
records; the refresh changed the machine regions under sixteen of the
records they had touched. Compared as effective records (machine record
plus replayed log), fourteen were identical or better afterwards — the
annotators' actions replay by region key — and two verified records
changed:

    td_1932_02_a021  A Loaf of Bifield   the credit line "Author of 'Three of a Kind,' …" was a teaser; v2.1.1 put it in the reading text
    wt_1925_11_a015  The Stolen Body     lost 131:2 (the title repeated over the continuation: now furniture) and 132:6–7 (the Next Month lines of Tenants of Broussac: now the announcement's)

The second change was the machine reading the page correctly — the
verified record contains two lines of an announcement — but a verified
record must not change under anyone; Heejin restores it and takes the
two lines out himself. Note that his own correction of 2 September on
the model assembly (the yardstick's record a007) did not contain the
three regions: the verified state of 3 September was made without
noticing them, so against the yardstick The Stolen Body now scores
J=0.96 instead of exact until he does.

The Demolished Man (gal_1952_03_a020, corrected by Sujin) lost its last
page for a different reason: the scan has its final leaves out of order
(printed 156 after 158), and once the Odd John announcement on the
last page was rightly read as advertising rather than a story start,
the issue's page range ended before that leaf. v2.1.2 takes such a leaf
in and flags the record.

Rules v2.1.2 (docs/assembly-v2.md) follow from the comparison and from
Sujin's newest corrections; the refresh now protects verified records
and reports every annotated one (docs/handbook.md, switch_assembly.py).
The counts above this addendum are as of 3 September.

The third round of the same day (rules v2.2, docs/assembly-v2.md) read
the 335 annotation events of 3–4 September as corrections and built
rules from them: the paratext roles, the end signatures, the split
titles, the chapter-title series, the synopsis, the departments, the
column advertisements, the text inside illustrations. Against the 2
September yardstick nothing moved (108/108, 88/88, 106 clean, the
verified Cave of Horror exact); against the newer annotations the
comparison is scripts/compare_effective.py, run after every refresh.

## 6 September: the second corrections audit, and what rules v2.3 changed

Heejin's request of 6 September: "Read the feedback and new
annotations. Improve layout assembly algorithm and annotation tools.
Closely examines what was wrong by automatic assembly. Give me updated
accuracy in plain English." The yardstick this time is the live
annotation logs themselves — 558 events by Sujin and Heejin between 3
and 6 September in nine issues, touching 82 records, 22 of them
verified — read by a new audit (pipeline/s11_corrections_audit.py). For
every record a person touched, the audit takes the record as the person
left it (the site's own replay of the log) and puts it beside the
machine's record, box by box: a box the person added (and where the
machine had it — which record, or furniture, and the machine's reason),
a box the person took out (and where it went), a role that differs, a
title, author or type that differs. `--candidate` measures a fresh
build of the rules against the same records, so a new version can be
judged before it goes live. The three grades: exact (boxes, roles,
title, author and type all as the machine made them), same boxes and
roles (only a title, author or type differs; teaser, note, synopsis and
caption count as one group here), same boxes.

The same 82 records, the machine of 4 September (v2.2) against the
machine of today (v2.3):

    records                       n    exact        same boxes+roles   same boxes
    all                          82    25 -> 38       33 -> 56           49 -> 68
    verified                     22     6 ->  7        8 -> 12           13 -> 16
    stories                      43    18 -> 25       20 -> 29           30 -> 35
    advertisements and house     24     3 ->  7        6 -> 14            9 -> 18
    features, letters, poems     15     4 ->  6        7 -> 13           10 -> 15

    by issue                      n    exact        same boxes+roles   same boxes
    Weird Tales 1925-11           7     3 ->  3        4 ->  4            5 ->  4
    Weird Tales 1934-05           2     0 ->  1        0 ->  1            2 ->  2
    Astounding 1930-03            8     2 ->  2        2 ->  5            5 ->  5
    Galaxy 1952-03               11     3 ->  5        5 ->  8            8 -> 10
    Thrilling Detective 1932-02  13     4 ->  5        6 ->  8           11 -> 12
    Thrilling Detective 1948-12  20     0 ->  7        0 -> 10            0 -> 14
    Western Story 1937-02        11     5 ->  7        7 -> 11            8 -> 11
    Wild West Weekly 1936-02     10     8 ->  8        9 ->  9           10 -> 10

Counted in boxes: the 82 records hold 13,610 boxes. The machine of 4
September lacked 106 boxes the people have in those records and held
68 they do not; today's lacks 28 and holds 5. Roles: 44 boxes had a
different role, now 25; inside the paratext group (teaser against note,
caption or subtitle) 14, now 18 — the machine now marks more of the
head matter, and the people's names for it differ from one another.

In plain words: before today, of every ten records a person had worked
on, three came back exactly as the machine had made them and six had
the right boxes; now about five of ten are exact and eight of ten have
the right boxes. On the verified records — the ones a person read to
the end — seven of 22 are exact and sixteen have the right boxes; the
rest differ by one to three boxes each. The issue that had been
hopeless, Thrilling Detective 1948-12 (advertising in a column beside
every page of the stories), goes from none of twenty records right to
fourteen of twenty with the right boxes.

What was wrong in the machine, as the audit showed it box by box:

1. Advertising beside the story. On the continuation pages of Thrilling
   Detective 1948-12 the small advertisements run in one column while
   the story runs in the other; the machine cut by reading order, so a
   story paragraph went into the advertisement and copy stayed in the
   story (most of that issue's twenty touched records). Now the block
   keeps to its column, a
   paragraph of the story's prose in the other column returns to the
   story, an advertisement column is found even when it comes first on
   the page or has no headline, and one record is made per headline
   that follows a paragraph of copy. The tail of a picture advertisement
   above the column ("Copyright 1948 Billerie Rubber Co." / "FOREVER
   FIRST WITH FIRST QUALITY") stayed in the story; it is its own record.
2. The head of a piece. The caption under the illustration and the
   pull-quote on the start page went to furniture; since 4 September
   the annotators put them in the record as teaser (five records). A
   blurb set in several boxes was taken only in part; a type label
   ("A Complete Novelet"), a department's tagline, an epigraph, the
   illustrator's signature read as text beside the drawing, an empty
   title box, a title set far above its by-line, a by-line of initials
   with the surname in the next box — each was left as body text or
   furniture. Now everything between the head and the first body
   paragraph is paratext, with the role the annotators use. A blurb set
   across the foot of the start page or its facing page, below the
   columns (Thrilling Detective's style), was body text; it is teaser.
3. The end of a piece. "COMING—" and "FEATURED IN THE NEXT ISSUE"
   blocks with a title and a by-line were story text (two verified
   records had to be carved by hand); they are house announcements. A
   next-month blurb or a "Watch for" line at the end was body text; it
   is a note. The end-signature and tail rules never ran for a record
   whose head had been joined from the facing page — a plain bug — so
   the matter after the end mark stayed in the story; fixed.
4. Chapter heads that the layout stage had labelled as running heads
   ("CHAPTER VII" in a header box) were furniture, so the chapter was
   missing; the cigarette pages outside the printed range were left
   unassigned; a scan with its leaves out of order gave the story in
   scan order; a whole advertisement page with one advertiser was cut
   into pieces at every headline. Each has its rule now.

What still differs, and why:

- Conventions the two annotators, or the annotators and the machine,
  apply differently: "a novelet by X" is a by-line to the machine
  (author) and a teaser to Sujin; the illustrator's credit, the "Author
  of …" line and the artist's signature are notes to the machine and
  teaser to her; the caption on the start page is teaser in her newer
  records and furniture in two older verified ones; the "Foreword" of
  Vampires of the Moon and the author's foreword of Brigands of the
  Moon (ten paragraphs) are body text to the machine and teaser to her;
  "[THE END]" was kept inside a verified record; an empty box where the
  title is drawn is the title box to the machine and "not story text"
  to her; the small advertisements on a classified page are one record
  per headline to the machine and one record per column to her
  ("INVENTORS", 24 boxes against the machine's 12); a house
  announcement is titled by the story it announces ("The Wrong Corpse")
  where she keeps the header ("FEATURED IN THE NEXT ISSUE!"). None of
  these is an error; the guide should fix one form, and the roles are
  a click on the workbench.
- The persons' own leftovers: a story line inside an advertisement
  (Tramp's Christmas Eve, p. 108) and a copyright line left in the
  story (p. 109) that the machine now places right; a body line marked
  as a chapter number; The Stolen Body's three boxes Heejin has still
  to take out of the verified record (the repeated title over the
  continuation and two lines of the next-month announcement).
- Known limits: a reader's poem inside The Eyrie typed story (no
  "verse" label on the page); text drawn inside pictures and blocks the
  layout stage never read; the reading order of a page cut round a
  drawing.

Against the 2 September yardstick (the corrections made on the model
assembly) nothing moved that matters: 108 of 108 pieces found, 88 of
88 titles and authors, 106 clean, cover 0.95; 14 of 51 human-touched
records identical (15 on 4 September — The Cave of Horror's start-page
caption, which the machine now keeps as teaser by the annotators'
newer convention).

The tools changed with the rules (site v0.16.0): the boxes on the scan
are coloured by role group and their ids stand in the margin beside
them, with the same colour on the card; an action that cannot be saved
says so instead of doing nothing; the reading text joins boxes that run
on; the story page shows the whole text; a move to a record that no
longer exists leaves the box in place. The refresh keeps the roles of
verified records people made and the ids the annotators used most.

How to reproduce:

    python3 pipeline/s08_assemble_rules.py --all
    python3 pipeline/s11_corrections_audit.py --candidate data/assembly_v2/rules
    python3 pipeline/s09_assembly_eval.py --all
    python3 pipeline/s10_assembly_audit.py --yardstick data/assembly_archive/20260902_203349 --candidate data/assembly_v2/rules

(s09 without --yardstick measures against the live logs' verified
records; with --yardstick data/assembly_archive/20260902_203349 against
the archived corrections of 2 September.)

The "before" column is the same audit run with `--candidate` pointing
at a build of v2.2 (kept on the sandbox as /tmp/v22/rules_v22; on the
server the archived live tree of the refresh,
data/assembly_archive/<stamp>_refresh/articles, is that build with the
annotators' ids).
