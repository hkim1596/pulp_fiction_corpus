# Assembly: known failures and the agreed rules for the next version

Written 2026-08-22 from annotator feedback (Sujin Kang, Heejin Kim)
gathered while hand-repairing Astounding 1930-01 on the workbench. This
is the specification for the next iteration of `pipeline/s07_articles.py`.
The human-corrected, verified articles are the test set for it: after
the rules below are implemented, re-assembly of the pilot issues must
reproduce the verified structure of these repaired stories.

## The observed failure, three times over

The same pattern broke all three long stories checked in Astounding
1930-01. The Beetle Horde (pp. 10–33) came out as six records; Phantoms
of Reality (pp. 48–76) as nine; The Stolen Mind (pp. 77–93) as three
plus a hole. In every case the extra records begin at a chapter heading
("Beetles and Humans", "The Challenge of the Unknown", …) or at a
running head, and the true story text continues straight through. The
Stolen Mind adds a worse variant: five mid-story boxes (79C–79G)
assigned to no record at all — text that would be silently lost if no
person noticed.

Also observed: the machine titled a story from the OCR of its
ornamental splash lettering ("Hemur Grail") even though the same page
carries a clean SectionHeader title, a subtitle ("A TWO-PART NOVEL"),
and a by-line ("By Victor Rousseau"); the contents page and cover strip
became story records; and the target picker in the workbench was
unusable because record titles were stray first lines of prose (that
part is fixed in site v0.8.0 — pickers now show id, page range, title).

## The rules (in priority order)

1. CONTINUATION RULE. A SectionHeader with no by-line, on a page where
   a story record is already open, never starts a new record. It is a
   chapter heading: keep it in the open story, tagged (see rule 5).
   A new story record starts only at a heading WITH a by-line, or at a
   page listed in the contents (rule 2).

2. TOC-ANCHORED PASS (top-down). Nearly every pulp issue prints its
   own ground truth: a contents page with each story's title, author,
   and printed start page. Parse it into a per-issue checklist; derive
   the printed-folio-to-scan-page offset automatically (constant per
   issue; +2 in Astounding 1930-01); then require story records to
   match the checklist — a story starts at a listed page with the
   listed title and author, and all fiction text between two listed
   start pages belongs to the earlier story unless a new by-line
   appears. Ads, features, and letters keep the current bottom-up
   path, since the contents page does not list them. The contents page
   itself gets type `toc`, never `story`.

3. SHARED-PAGE INVARIANT (hard check). Two story records that share a
   boundary page are one continuous text unless a by-line separates
   them on that page. Enforce at assembly time and flag any violation
   for review rather than fixing case by case.

4. COVERAGE CHECK. After assembly, every text box between a story's
   first and last page must belong to some record (that story, another
   article, furniture, or unsorted-by-decision). Any unassigned text
   box in a story's span is flagged loudly. Silent loss is the one
   unacceptable outcome.

5. CHAPTER APPARATUS — adopted convention. Chapter numbers and chapter
   titles stay in the reading text, tagged with dedicated roles
   (`chapter_number`, `chapter_title`) rather than stripped or
   promoted to record titles. This preserves both views: the story as
   printed, and plain prose for analysis (export can include or drop
   tagged headings). The workbench has one-click buttons for both
   roles as of v0.8.0. The assembler should emit these tags itself
   when rule 1 captures a chapter heading.

6. TITLE AND AUTHOR PREFERENCE. When filling a record's title and
   author, prefer SectionHeader text and by-line segments over
   ornamental display text; subtitle lines like "A TWO-PART NOVEL"
   set the subtitle and imply type `serial_part`. Only if no
   SectionHeader or by-line exists may display lettering be used, and
   then flagged as low confidence.

## Workbench support shipped for the interim (v0.8.0)

Until re-assembly lands, hand repair is made viable by: multi-select
on the scans (click several boxes, one confirm), range claim ("this
box and everything after it, through page N"), whole-record merge in
both directions with readable labels, chapter-role buttons, and
position memory after every action. A serial split across six records
is now roughly six clicks (pull each record in), not hundreds.

## How re-assembly will be validated

Re-run s07 with the new rules on the pilot issues (using `--force`,
which archives existing annotations first — coordinate before
running). Then compare: every human-verified article must come out
structurally identical (same pages, same segment set, same order);
the flags from rules 3 and 4 must be empty or reviewed. Only then do
the improved prompts/rules become the default for the full corpus.
