# The contents page and the printed page numbers: toc_folio (rules 2.7)

Written 15 October 2026 (Claude, Sujin Kang's session). Code: `pipeline/toc_folio.py`; used by
`pipeline/s08_assemble_rules.py` from version 2.7; read-only corpus report: `scripts/toc_folio_report.py`;
tests: `tests/test_toc_folio.py`. The evaluation kit and the hand-checked answers are in the Dropbox folder,
`pilot_export/p51_toc_folio/`.

## Why

The rules engine places a story where the contents page says it begins: the printed page number, turned into a
scan page through the issue's printed page numbers (the folio). Both steps were weak outside the ten pilot issues
the rules were written on:

- The folio map took the most common difference between scan page and printed number. Volume numbers, years and
  prices read at the page edge pulled it astray (offsets of −1,054, −518 and −431 on archive scans), numbers inside
  running heads ("ASTOUNDING STORIES 153") were not read, and an inserted plate shifted every page after it.
- The contents parser knew five printed layouts and needed dot leaders or one fixed line pattern. It missed page
  numbers set in their own box, "by Author 94" printed under the title, two-column contents pages, two entries in
  one row, entries whose number the OCR lost, Surya's flattened tables, and it took an advertisement page full of
  prices for the contents page (A. Merritt's Fantasy Magazine, December 1949: 35 entries, all book prices).
- An entry was placed on the page its number gave, unchecked.

## How

1. **Folio map.** Every page's candidate numbers in the top and bottom bands (the number alone, or at either end of
   a running head; years, "Vol./No." and dates refused). A path of offsets is fitted through the whole issue
   (dynamic programming: each page that agrees with its offset scores, every change of offset costs), so an
   inserted or missing leaf starts a new segment and misreadings do not move the map. Pages without a readable
   number take their segment's offset; a leaf bound out of order keeps the number it carries.
2. **Contents.** The best page among the first sixteen scans (and the last three, if they say so), plus a
   neighbouring page that continues it (and is not an advertisement with prices). Two readers, on the text as Surya
   gives it: a cell reader for contents blocks (title / author / number cells, entries apart by blank lines, blurbs
   recognised as prose, numbers after a blank line), and a row reader on positions (title box, author box and
   number box side by side; a number below the last line of its entry; a by-line row taking its title from the row
   above; a blurb ending in a number taking the title and by-line above it). Section labels, type labels
   ("A Complete Novel", "(Verse)"), parts ("Part Two", "(Conclusion)"), staff names, prices, coupons and
   cross-references ("Coupon on Page 112") are recognised and kept out of titles. A book's list of chapters is
   reported as chapters, never as pieces.
3. **Placement.** Each entry is looked for on the page its number gives and the pages around it: the title among
   the page's display lines (a title set across a spread counts on the numbered page), the author among its
   by-lines. One leaf away needs the title or a clear by-line; two leaves away needs both (or the title over
   "CHAPTER I"). An entry with no usable number is found anywhere in the issue, but only by title and author
   together. Every entry records how it was placed (`placed_by`) and a confidence.

In s08 the new reading replaces the 2.6 contents entries, contents page and folio index when it reads anything;
the 2.6 reading is kept in `analysis.json` (`toc_v26`, `toc_page_v26`, `offset_v26`, `folio_range_v26`) for
comparison. Switch: `assembly.toc_folio` in `config/corpus_settings.json` (default on) or `PULP_TOC_FOLIO=0`.
`pipeline/s02_layout_ocr.py` now keeps Surya's table cells and rows as lines, for issues read from now on.

## Measured

On corpus issues (Surya's layout pages from the server through the data door), against answers checked by hand:
the contents page read in full, the display titles and by-lines of every page looked at, the start of every listed
piece recorded (two scan pages accepted where a title is set across a spread).

| Set | Issues (with a contents page) | Listed pieces | 2.6: placed right | 2.7: placed right | Placed entries that are real pieces, 2.6 / 2.7 |
|---|---|---|---|---|---|
| A: 30 random issues, used while fixing | 30 (22) | 242 | 148 (61.2%) | 238 (98.3%) | 78.3% / 98.8% |
| B: 20 fresh random issues, before the second round of fixes | 20 (14) | 182 | 95 (52.2%) | 159 (87.4%) | 78.5% / 90.9% |
| B after the second round | | 182 | | 177 (97.3%) | 98.3% |

Set B's first line is the fair estimate for issues the code has not seen; the second round of fixes was made from
set B's errors. Three magazines outside the study that the selection let in (a horror magazine of the 2010s, a 1972
film magazine, a 1915 Punch) and one contents page the OCR interleaved beyond reading were left out of the counts.

The whole assembly, both sets (34 issues assembled with 2.6 and 2.7, 417 listed pieces): a record starts on the
listed piece's first page for 364 (87.3%) with 2.6 and 410 (98.3%) with 2.7; with the right title 341 (81.8%) and
405 (97.1%). No issue did worse; no region was left unassigned or owned twice; story records not at a listed start
28 (2.6: 32).

The folio map against the Internet Archive's own page numbering (its `_page_numbers.json`, an independent
reading), 45 issues and 6,359 pages it numbers: 2.6 agrees on 95.7%, 2.7 on 99.2%; pages given a number that
disagrees with the archive's, 7 and 15.

On the eight pilot issues in the sample (Astounding 1930-01 and Galaxy 1952-04 were not drawn) the contents
entries and their pages are the same as 2.6's except three titles set across a spread, now placed on the left-hand page where the title begins (Galaxy 1952-03: The Year of the Jackpot, The
Seventh Order; Thrilling Detective 1948-12: The Great Pearl Theft).

## Known limits

Contents pages whose two columns the OCR read across (The Popular Magazine, 20 March 1927); entries printed as a
cross-reference ("Also See 'Abide Here, Stranger' … on Page 110"); a title glued to the next entry by the OCR
("N. K. Hemming79● Guest Editorial"); titles drawn as lettering with no by-line and no readable number. These are
placed by their number only, or not at all, and say so in `placed_by`.
