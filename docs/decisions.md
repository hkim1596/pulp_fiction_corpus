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
- 2026-09-02 — Feedback of 2 September (site): design for the whole
  corpus, not the pilot ("Can this overview design hold hundreds and
  thousands of stories, authors, magazines? Redesign it."): the explorer
  reads from a database, lists are paged, the overview is sliced by
  decade and genre, and drawings of individual entities are made only
  for slices small enough to read.
- 2026-09-02 — Story dates: "unless we have other evidence to conjecture
  composition dates of stories, default dates are associated with issue
  dates" — every record carries date and date_source ("issue").
- 2026-09-02 — A complete issue is one assembled into records by the
  machine; only complete issues appear on the explorer side; the
  workroom progress board shows every issue at every step of the
  process (download, layout OCR, text stages, assembly, export,
  annotation, verification).
- 2026-09-02 — Feedback entries: admins see and edit all and mark them
  done; each member sees and edits only their own; edits keep history.
  The feedback box is prefilled with the member's name and sending
  keeps the reader on the page.
- 2026-09-02 — Author names are shown in title case; the forms as
  printed live in fine print on the author's page and in the raw record.
- 2026-09-02 — The printed teaser on a story's first page is metadata,
  never story text (teaser role on the workbench; exported by r00).
- 2026-09-02 — Assembly v2: "Archive corrections and start clean. Keep
  comparing human-touched correction and automated correction so
  automation can reach the accuracy of human correction." Methods are
  compared against the human-corrected records: "I want to see which
  method is most accurate. Compare them."
- 2026-09-02 — The main server's new disks (/mnt/sda, /mnt/sdb): decide
  their use after seeing sizes; the site and tunnel go back to the main
  server now, the Studio stays on standby with its ssh path open.
- 2026-09-02 — Assembly: the rules engine (s08) replaces the model
  assembly in the corpus path on the pilot's evidence (docs/assembly-
  v2.md); the human-corrected records stay the yardstick in
  data/assembly_archive; the model-with-rules variant is tried later
  on the main server for pages the rules cannot anchor.
- 2026-09-02 — Storage on the main server: no mirror; project data on
  /mnt/sda (data/pages and data/thumbs moved there, symbolic links
  keep the paths); /mnt/sdb unassigned; Docker left untouched ("Don't
  remove") — other members' containers and images stay.
- 2026-09-02 — The site went back to the main server the same day
  (hand-back through the MacBook; docs/backup-server.md); the Studio
  keeps its tunnel for ssh and returns to the nightly pull.
- 2026-09-03 — The metadata survey of the archive's collection may run
  before protocol acceptance ("Yes, metadata only, now"): one record per
  item through the search API, no page images, no text; the
  downloader's gate is untouched. The site's boards show the whole
  collection, English and not, and the project's progress against the
  working corpus (English or unmarked, fiction magazines).
- 2026-09-03 — Advertisements carry an ad_class rather than new top-level
  types, as Sujin Kang proposed (house_next_issue, house_self,
  house_sibling, trade, classified with n_items) plus house_form for the
  magazine's own ballots and coupons, and an advertiser field; a house
  announcement that quotes a story verbatim is marked contains_excerpt
  / excerpt_of and stays out of the reuse inventory, with the author
  field empty (the announced works are in `announces`).
- 2026-09-03 — Author values are stored as printed and shown in title
  case everywhere; the workbench shows the printed form in fine print.
- 2026-09-03 — The contents page has authority over a story's title and
  author ("Titles and authors on the table of contents should have more
  authority than on each story's own page"); the page's own forms are
  kept as title_as_printed / author_as_printed, and a strong
  disagreement is flagged for a person. The same words in a better
  type case (the page's mixed case against the contents page's
  capitals) are not a disagreement.
- 2026-09-03 — /issues becomes the explorer's list, built for the whole
  corpus; the workroom's table of the ten pilot issues moves to
  /workroom/issues.
- 2026-09-03 — A new run of the rules goes live through
  scripts/switch_assembly.py --refresh, which keeps record ids and the
  annotation logs (an annotated record whose regions changed refuses
  the issue unless --force); the plain switch, which archives the
  logs, is for a change of method, not of version.
- 2026-09-04 — A record a person has verified is never changed by a
  machine run. scripts/switch_assembly.py --refresh carries a verified
  record (a verify event with no unverify after it) into the new file
  as it is and takes its regions away from the candidate; there is no
  flag to override this — to let the machine at it, unverify on the
  site first. Every other annotated record whose regions would change
  is reported, forced or not. Made after the forced refresh of 4
  September changed two verified records (A Loaf of Bifield, The
  Stolen Body) and the last page of The Demolished Man.
- 2026-09-04 — The credit line under a by-line ("Author of 'Men Like
  Gods,' etc.") carries the teaser role on the workbench (Heejin: as he
  marked it on The Stolen Body); it is never reading text nor page
  furniture, the record's teaser field keeps the blurb, and the credit
  text is exported as author_credit.
- 2026-09-04 — Page furniture stays outside the record: running heads,
  page numbers, printer's marks and illustration captions (the machine's
  and the verified records' convention). Sujin, who had been keeping
  captions inside the story record, is told in the guide; her records
  are not changed.
- 2026-09-04 — The roles above a story head follow Sujin's records: a
  second setting of the title is title, the blurb is teaser, a kicker
  or type label is subtitle, a synopsis heading ("The Story Thus Far")
  is heading. Inside a story a display line that is not a chapter head
  has no role (the author's typography stays in the text); section is
  for the headings of departments (The Eyrie's letters, a feature's
  topics). "PART ONE" and "I.—THE MURDER CLUB" are chapter heads.
- 2026-09-04 — The Stolen Body keeps its verified state including the
  two Next Month lines on p. 132 that belong to the announcement of
  Tenants of Broussac; Heejin takes them out on the workbench and
  verifies again ("I will, on the workbench").
- 2026-09-04 (round 3) — The types gain house and lose serial_part, as
  Sujin Kang proposed on 3 September: house is the publisher's own
  matter (next-issue announcements, story excerpts, coupons and
  ballots, subscription appeals, the masthead), told from ad, an
  outside advertiser's copy; the six advertisement classes stay as a
  field derived by rule. A serial instalment is a story with serial
  fields (part_label, part_n, part_total, source, the work's title
  without the marker) — a relation between issues, not a kind of unit;
  the machine links instalments across issues when both are in the
  corpus (Heejin: "combine these two parts into one once both stories
  are processed"). The old dropdown item replays as story with the
  annotator as the serial's source. Text reuse leaves out house and ad
  by type.
- 2026-09-04 (round 3) — The workbench follows Heejin's hierarchy: at
  the top, the contents page, an article (listed on the contents page)
  or a non-article; then the type; then, for a story, Title, Subtitle,
  Author, Body text, Chapter info (chapter no., chapter title, section)
  and Paratext. Paratext is one group with three kinds, as Sujin needs
  them told apart: teaser (editorial text about the story, kept as
  metadata), synopsis (the recap of earlier instalments — the story's
  own earlier text, left out of the reading text and of the reuse
  inventory; the machine marks it, a person corrects it) and note
  (credits, "Illustrated by", a tail line). The credit line's role is
  therefore note from now on (the teaser role of the morning's decision
  is kept on the records people had already marked). Any role button
  replaces the current role in one step; several chosen cards take one
  role at once; the title, subtitle, author and teaser fields hold the
  words of every box with that role, in reading order.
- 2026-09-04 (round 3) — Chapter titles: a display line inside a story
  is a chapter title when it belongs to a series of the same form on
  different pages (Wild West Weekly's "STAMPEDE OF THE Z BAR L."), never
  when several sit together on one page (Bester's "Consternation /
  Alarm / Conviction", a quoted headline). The local language model may
  later judge the leftover cases from the surrounding boxes as an
  optional stage (Heejin: "LLM should read context from the surrounding
  text boxes"); planned, not built.
- 2026-09-04 (round 3) — "Text in picture" is a kind of page furniture:
  words the scan read inside a drawing (an illustration page's garbled
  lines, a text box inside a picture box). The machine marks it; the
  workbench has a button for it.
- 2026-09-04 (round 3) — Departments: config/departments.json lists each
  magazine's standing departments with a type and the usual conductor;
  a record whose title names one gets the department field and that
  type. A letters page must hold signed letters (name, city, state);
  an editor-voiced column with readers quoted inline is a feature
  (Sujin: Headquarters, The Round-up).
- 2026-09-04 (round 3) — Sentence case everywhere on the site (menus,
  buttons, table headers); log-in times on the activity page; the
  articles list shows stories first, the verified and the modified ones
  first among them, with a distinct colour for "modified"; the overview
  charts stories by year and by genre separately; the progress pages
  draw one bar for the whole collection (28,286 items) with the working
  corpus, its fiction magazines, and the issues downloaded, processed
  and fully verified in their own colours.
- 2026-09-04 (evening, "Read the revised protocol. Upgrade our website
  and tool according to the protocol"; Heejin: the Google Doc as it
  stands, no additions) — The protocol was checked step by step against
  the site and the pipeline, and six places where it commits to
  something the tools did not yet do were built (all six chosen by
  Heejin): the paraphrase review set and its calibration (3.2), the
  collection's history of transmission and the datasheet (2), the two
  corpora as two files (2), the extensive cases and the exact-versus-
  paraphrase comparison on the reuse page (3.1, 3.2), every match placed
  among comparable pairs (4.1), and the cases for the genealogies (4.2).
  Working settings written into the tools, to be confirmed or changed:
  the review set's strata (source × score band × cosine band), 15 items
  per stratum, the grid K {5, 10, 20} × six keep rules, the precision
  floor 0.90, the choice rule (highest weighted recall at the floor,
  ties to the smaller pool); whole-story = 80% and high-coverage = 20%
  of the shorter story; a case is any cluster or pair a named member
  marks. Readers do not see the machine's numbers until they have
  judged. The provenance enrichment reads each item's own metadata
  record (metadata only, within the decision of 3 September); uploader
  addresses stay in the data file, the site shows the part before the @.
  The publisher table (config/publishers_magazines.json) is reference
  knowledge to be confirmed against the mastheads.
