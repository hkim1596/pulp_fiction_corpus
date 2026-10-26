#!/usr/bin/env python3
"""Stage 8 — assembly v2: the rules engine.

Assembles the article records of an issue from its layout pages (s02, route
A) with printed conventions instead of a model: the contents page, the
printed page numbers, the running heads, the display title with its by-line,
the chapter apparatus, the teaser, the fillers at the end of a story. Every
rule comes from what the annotators reported on the workbench (feedback of
22–31 August 2026; docs/assembly-notes.md):

  1. A story starts where a display-size title stands above a by-line on a
     page inside the printed page range (or where the contents page says a
     piece starts, confirmed by a by-line). Its title and author come from
     those regions, cross-checked with the contents page — never from the
     ornamental lettering the OCR garbles.
  2. Continuation stays with the open record until the next start: a section
     header with no by-line while a story is open is a chapter head, tagged,
     never a new record. The running head confirms membership.
  3. The regions above the title on a start page (illustration caption,
     artist's signature, display garble) are page furniture, as the verified
     records do it; the printed blurb below the by-line is the teaser —
     metadata, kept with the record but never story text.
  4. A display-size header inside a story page that is not a chapter head
     starts a filler (house advertisement, "in the next issue", a letter to
     the readers): its own record to the end of the page.
  5. The contents page is a record of type toc; pages before the first and
     after the last printed page number are advertisement pages, one record
     per display header; a page inside the range with no open story and no
     by-line is a feature.
  6. Every text region belongs to exactly one record or to furniture; a
     coverage check reports anything left over.

Two variants are written, both in the articles.json shape so the site can
show them: rules-only (everything from the rules) and rules-on-model (the
rules inside the printed page range, the model's records — s07 — for the
advertisement pages outside it). Output: data/assembly_v2/<variant>/<issue>/
articles.json plus analysis.json (what the rules saw on every page). The
live assembly under data/articles is never touched; s09 compares them.

    python3 pipeline/s08_assemble_rules.py --all
    python3 pipeline/s08_assemble_rules.py --issue ast_1930_01
    python3 pipeline/s08_assemble_rules.py --selftest
"""
import argparse
import difflib
import glob
import json
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from layout_order import order_region_keys
from corpus_lib import write_json_atomic

VERSION = "2.12"
# 2.12 (2026-10-26, Claude in Sujin Kang's session): the cover. An advertisement or house block that stands on the
# first scan when that scan looks like the cover (the masthead, cover lines, a price, a date; no running prose:
# cover_page_score) is the cover's record — type other, department "cover", ad_class "cover" — as Sujin's reading rule of
# 17-21 October says (a cover is `other`, not house); until now it was house_self front matter. first_scan_kind() says
# what an issue's first scan is (cover / contents / body / blank / other) for the survey of the files that have no
# cover (scripts/cover_survey.py). The cover is never read as the contents page (Sujin, 26 October: "the cover
# highlights particular pieces; it is unfit for extracting the contents; a file without a contents page goes by the
# rules"): toc_folio and the 2.6 reading skip a first scan that looks like a cover unless a reader names that scan.
# The grouping of boxes is unchanged.
# 2.11 (2026-10-26, Claude in Sujin Kang's session): 2.10 typed a 'true story' and the unsigned pieces of an unsigned
# contents page as stories at the contents stage; assembled as stories, they swallowed the advertisement or house
# block at their feet, which the feature path cuts (True Detective Mysteries 1929-02 p.104, Short Stories 1925-06,
# Galaxy 1954-02, Fantastic Universe 1954-12: 35 issues the guard refused for a flagged block, and the same fault in
# issues it installed). 2.11 assembles both as features and retypes the records story once assembled (7a, 7b); an
# unsigned piece is retyped only when the contents page names no authors at all or the page itself gives the author
# (an unsigned department of Adventure stays a feature). The kinds are 2.10's; the grouping of boxes is 2.9's.
# 2.10 (2026-10-26, Claude in Sujin Kang's session): from the 100 kind clicks and the notes of the completed level-1
# check (150 issues, 21-25 October). A contents page that names no authors makes its unsigned entries stories, not
# features (True Romances, 12 Sports Aces, the Illustrated Detective: 52 clicks); a 'true story' is a story (Sujin,
# 26 October: "even with true in the section name, story"); a record the contents or the departments list typed
# letters stays letters (the signed-letters test of 2.3 applies only to a record the page alone made one: 13
# clicks); the departments list matches a magazine by its first distinctive word ('Galaxy' had never matched
# 'Galaxy Science Fiction'); a 'Verse' label that Weird Tales (1927-1932) prints under the entry it belongs to is
# corrected by the text itself — a contents poem whose text is prose is a story, a short story whose text is verse
# is a poem (20 clicks); a month, a volume line or 'CONTENTS JUNE' is not a piece (5 clicks); a reader's word on
# where the contents page is (config/toc_page_overrides.json) forces the reading of that scan (toc_folio 1.4).
# Kind "volume" (a bound volume of several issues) joins book and magazine in config/corpus_kinds.json: kept in
# the data, assembled as a magazine, not counted as an issue, not in the sample.
# 2.9 (2026-10-21, Claude in Sujin Kang's session): the kinds of contents entries, from Sujin's level-1 contents
# checks of 31 sample issues on 21 October. An ARTICLES section, a 'true story', fact, hints, a quiz are features
# (non-fiction), not stories; a FEATURED NOVELET is a story; a VERSE / POEMS / SONGS section or title is a poem; the
# readers' and the editor's departments (letters, the Eyrie, the Camp-Fire, Our Air Mail, the Log-Book, Discussions,
# an editorial or editor's page, 'Ask …', a department described as the readers' meeting place) are letters, kept
# apart from features; a part marker, a serial label or 'In Five Parts' makes a serial part (the total is kept);
# the publisher's club and next-issue notices are features that the departments list (config/departments.json,
# extended) makes house. The entries are repaired before typing (repair_toc_entries): a name glued to the title
# ('The Albino CanaryHal Moore'), an initial glued to the title's end ('Battle of WizardsL' / 'Ron Hubbard'), a
# section word with its brace before the title ('VERSE { HALLOWE'EN CANDLE'), the previous entry's chapter range
# before the title ('CHAPTERS IX-XIII LUCK'), a description in the author's place, a title that went into the
# previous entry's blurb and left its own entry empty (Amazing Stories 1927-06, The Moon Pool). On the 31 checked
# issues the kinds of 33 entries change, every one to Sujin's reading; the grouping of boxes is unchanged.
# 2.8 (2026-10-18, Claude in Sujin Kang's session; toc_folio 1.3): four contents layouts that 2.7 read with the
# author's name as the title and no author, or with the title line dropped — a title line ending in its leader
# with the author and the number on the next printed line ('The Voyage of the Neutralia (part 2) . . . . .' /
# 'B. Wallis 726': Weird Tales, 1937-12, 12 of 14 entries wrong); 'TITLE.....AUTHOR..... 689' in a cell list,
# broken after the leader (Argosy, 1909-11); two-line heading boxes 'Title .....' over 'AUTHOR 102' and a
# department's description carrying the number (Adventure, 1947-09); a name with its own leader under the title
# (Western Story, 1935-07); a year in a title is not a page number ('In The Year 2000 . . . . .'). Found through
# the 2.7 upgrade's refusals (p52, 10d): 634 issues kept 2.6 because their cross-issue metadata could not be
# carried over, most of them because the 2.7 record no longer had the title. On 150 of those issues the fixes
# change 30 entries in 4 issues, every one to the printed reading, and nothing else; against 2.6, entries read
# with the author as the title fall from 29 to 6. The upgrade guard gains one case for the excerpt inference
# (scripts/upgrade_continuity.py, absorbed_excerpt_inference). The assembly itself is unchanged.
# 2.7 (2026-10-15, Claude): the contents page and the printed page numbers are read together by
# pipeline/toc_folio.py — the folio map is a path of offsets fitted through the whole issue (inserted
# and missing leaves, volume pagination, numbers in running heads, leaves bound out of order) instead of
# the most common difference; the contents page is read in every layout of the 2026-10-14/15 test bed
# (page numbers in their own boxes, 'by Author 94' under the title, two columns, rows of two entries,
# numbers the OCR lost, Surya's flattened tables); every entry is placed on the scan page that shows its
# title or by-line, within two leaves of the page its number gives, or found by title and by-line when the
# number is unreadable; a book's chapter list is not read as pieces. Measured on 36 corpus issues checked
# by hand (424 contents pieces; docs/toc-folio.md): entries on the right scan page 57% (2.6) -> 98% (2.7;
# 87% on the 20 issues drawn fresh before the last round of fixes); records starting on the right page
# 87% -> 98%. Switch: settings assembly.toc_folio (default on) or PULP_TOC_FOLIO=0; the 2.6 reading is
# kept in analysis.json (toc_v26, offset_v26, folio_range_v26) for comparison.
# 2.5: require evidence for continuation ownership and contents identity;
# resolve duplicate folios; preserve small headings, embedded bylines, verse,
# and story endings before departments. Full corpus reassembly follows 2.4.
# 2.4: retain mislabelled body paragraphs, resume single-box continuations,
# follow explicit forward page references, and order fragments geometrically
# without changing the persistent region indices used by human annotations.
# 2.3 (2026-09-06, from the corrections audit s11 over the 558 annotation events of 3–6 September,
# read against a fresh build of 2.2): (a) advertising set beside the story on its continuation
# pages is cut by COLUMN — a filler or advertisement block keeps to the column it stands in, and a
# paragraph in the other column goes back to the piece (Thrilling Detective 1948-12, pp. 60, 100,
# 104, 110); an advertisement column is found even when it comes first in reading order or has no
# section-header label (pp. 102, 104, 106), one record per headline that follows a paragraph;
# a block of short display lines at the foot of a continuation page with nothing after it is an
# advertisement; a full advertisement page with one advertiser (one coupon, one Dept. code) is one
# record; the tail of a picture advertisement above the column's first headline (a copyright line,
# a company, a slogan in capitals) is its own record (p. 109); (b) the head zone of a piece —
# everything between the head and the first body paragraph, across a spread — is paratext: the
# illustration's caption and the pull-quote on the start page are teaser (the annotators' rule
# since 4 September, five records), a blurb set in several boxes is teaser box by box, a blurb set
# across the foot of the start page or its facing page below the columns is teaser (a display
# line, or a header in capitals with its paragraph: the series introduction of The Great Pearl
# Theft), an epigraph with its attribution is teaser, a type label is subtitle, a
# department's tagline is subtitle and its standing notice teaser, the illustrator's signature read
# beside the drawing is a note (config/illustrators.json), an empty title box is the title; a
# by-line may carry its type label ("a novelet by …") and a by-line of initials takes the surname
# in the next box; a title far above its by-line is found when the contents page names it; (c) the
# end of a piece: a "COMING—" / "FEATURED IN THE NEXT ISSUE" block with a title, a type label and a
# by-line is a house announcement, a next-month blurb or a "Watch for" line is a note whatever its
# punctuation, the end-signature and tail rules now run after the head is joined from the facing
# page (they never ran for those records); a piece's column above a title stops at a "continued on"
# notice, and a new start on the page ends the notice's tail; (d) a chapter head the layout
# labelled as a running head is a chapter head; a page outside the printed range with text is an
# advertisement (the cigarette pages), titled by its display lines; a record whose scan has leaves
# out of order is given in printed-page order.
# 2.2 (2026-09-04, the third round of feedback and the annotations of 3–4 September): the types
# gain house (the publisher's own matter: next-issue announcements, excerpts, coupons and ballots,
# subscription appeals, the masthead — ad_class house_*) and lose serial_part (a serial instalment
# is a story with serial fields part_n / part_total / part_label / work_title, linked across issues
# by link_serials); "(Poem)" in a title makes a poem; titles set in capitals go to title case (the
# printed form kept); a title split over two boxes or two pages is joined from the contents page;
# the roles note (credits, "Illustrated by", tail notes) and synopsis (the recap on a later
# instalment) join teaser as the paratext of a story; a chapter title is a display line that
# belongs to a series of the same form on different pages (Wild West Weekly), never one of several
# on one page (Bester's typography); an author's name repeated at the end is furniture (or the
# author of an unsigned column), "The End" and "To be continued" lines are furniture; "Conducted
# by" is a by-line; departments (config/departments.json) give a department field and the type,
# and a letters page needs signed letters; an illustration page's garbled text and text inside a
# picture are furniture; an advertisement column beside the story text is cut out by its column.
# 2.1.2 (2026-09-04): from the refresh that changed annotated records: the roles above a story
# head follow the annotators' convention (a second setting of the title is title, the blurb is
# teaser, a short display line is subtitle, a synopsis heading is heading); the credit line under
# a by-line ("Author of 'Men Like Gods,' etc.") is never reading text nor furniture — it carries
# the teaser role (Heejin's choice of 2026-09-04) and is kept as author_credit; the issue's page
# range takes in a trailing leaf the scan has out of order (Galaxy 1952-03 prints page 156 after
# 158), and the record that holds it is flagged.
# 2.1.1 (2026-09-03, evening): from the audit against the human corrections (s10): the end of
# a piece's column above the next piece's title stays with the piece (evidence: a paragraph that
# starts in lower case or a long one; the rest of the Text follows); blurbs and synopsis headings
# above the title go to the new piece (teaser, heading); a repeated title over a "continued from"
# notice is furniture; a plain subheading inside a department with selling words under it is an
# advertisement; a filler exits only at narrative prose; a headline inside advertising matter
# starts the next advertisement (stacked headlines joined); an announcement header runs on to a
# later notice on the page; a by-line followed by selling words on its page is a book or magazine
# announcement, not a story.
# 2.1 (2026-09-03): the contents page has authority over title and author (the page's
# forms are kept as title_as_printed / author_as_printed); chapters listed with number
# and title split; advertisements classified (ad_class, advertiser) with the works a
# house announcement names (announces) and the verbatim excerpt it may carry
# (contains_excerpt, excerpt_of); ballots and coupons are their own record (house_form).
DISPLAY_FRAC = 0.025          # a header at least this share of the page height is display size
TITLE_GAP = 520               # px: how far above a by-line the display title may stand
NUM_WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10}
CHAPTER_RE = re.compile(r"^\s*(?:CHAPTER|Chapter|CHAP\.|PART|Part)\s+(?:[IVXLC\d]+|" + "|".join(NUM_WORDS) + "|" +
                        "|".join(w.capitalize() for w in NUM_WORDS) + r")\b|^\s*[IVXL]{1,6}\.?\s*$|^\s*\d{1,2}\.?\s*$|"
                        r"^\s*(?:[IVXL]{1,6}|\d{1,2})(?:\.\s+|\.?\s*[—–-]\s*)[\"“]?(?![A-Z]\.\s)[A-Z][^\n]{2,60}$")     # 'IV. The Coming of the Beast', 'I.—THE MURDER CLUB' on one line
BYLINE_RE = re.compile(r"^\s*(?:(?i:(?:a |an )?(?:complete |thrilling |gripping |long |short |true |new |exciting |novelette |novelet |novel |story )*"
                       r"(?:novel|novelette|novelet|story|serial|mystery|romance)\s+))?"
                       r"(?:[Bb][Yy]|Conducted by|Edited by|Compiled by|Arranged by|As told to)\s+(.+)$", re.S)
NOT_BYLINE_RE = re.compile(r"^\s*(?:Illustrated|Illustrations?|Painted|Drawn|Drawings?|Cover|Decorations?|Photo)", re.I)
# the illustrator's credit on a story's first page ("Illustrated by WILLER"): a note, never story text
ILLUS_RE = re.compile(r"^\s*(?:Illustrated|Illustrations?|Drawings?|Decorations?|Pictures?|Painted|Photographs?)\s+by\s+(.{2,60}?)\s*\.?\s*$", re.I | re.S)
# the author's name repeated at the end of a piece ("—ROBERT A. HEINLEIN"), and the end mark
END_SIG_RE = re.compile(r"^\s*[—–\-]{1,2}\s*([A-Z][A-Za-z.'\- ]{2,40}?)\s*[.,]?\s*$")
THE_END_RE = re.compile(r"^\W*(?:THE END|The End|FINIS|Finis|END|\(?The end\)?)\W*$")
# an editorial line at the end of a piece: a note (paratext), never story text
TAIL_NOTE_RE = re.compile(r"^\s*(?:Watch for|Don't miss|Look for|Read|Another|Next (?:month|week|issue)|In (?:the )?next|Coming|"
                          r"Be sure|More (?:about|of)|See (?:the )?next|The next)\b", re.I)
# a paragraph at the end of an instalment that points to the next one ("… will be revealed in next month's
# WEIRD TALES. Don't miss it."): a note, never story text
NEXT_BLURB_RE = re.compile(r"next (?:month|week|issue)|in (?:the )?(?:next|following|forthcoming|coming) (?:issue|number|instal)|"
                           r"don't miss|will be (?:told|revealed|concluded|continued|found)|to be concluded", re.I)
# the instalment marker in a title: "[Part I]", "(Part One)", "(Conclusion)", "— Part Two of Three"
PART_MARK_RE = re.compile(r"(?<!\w)\s*[\[\(\-—–:,]*\s*(?:(?:a |the )?(?:(?:two|three|four|five|six|2|3|4|5|6)[\- ]part )?(?:serial|novel|story|novelette)?\s*[—–\-:,]?\s*)?"
                          r"(?P<label>(?:part|pt\.?|instalment|installment|chapters?)\s*(?P<n>[ivxl]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
                          r"(?:\s*(?:of|in)\s*(?P<tot>[ivxl]+|\d+|two|three|four|five|six|seven|eight))?|conclusion|concluded|the end|ending|beginning|final(?:e| instal?lment)?)"
                          r"[^\]\)]{0,40}?[\]\)]?\s*$", re.I)
# a type label printed with the head: "A Complete Book-Length Novel", "Novelette", "A Short Story"
TYPE_LABEL_RE = re.compile(r"^(?:a |an )?(?:complete |thrilling |gripping |long |short |true |weird |new |exciting |amazing |startling |"
                           r"(?:(?!(?:the|this|that|his|her|our|your|my|a|an|in|of|as)\b)[\"\u201c]?[A-Z][A-Za-z'\-]*[\"\u201d]? ){1,3})*(?:book-length |full-length |short |long )?"
                           r"(?:novel|novelette|novelet|story|stories|serial|mystery|romance|fact story|feature)s?\b[^\n]{0,30}$", re.I)
POEM_MARK_RE = re.compile(r"\s*[\(\[]\s*(?:a )?(?:poem|verse|verses|sonnet|ballad)\s*[\)\]]\s*$", re.I)
# a signed letter's signature: "— John Smith, Chicago, Ill." / "Name, City, State."
SIGNATURE_RE = re.compile(r"^\s*[—–\-]?\s*(?:[A-Z][A-Za-z.'\-]+\s){1,4}[A-Z][A-Za-z.'\-]+,\s*[A-Z][A-Za-z.\- ]{2,30},\s*[A-Z][A-Za-z.\- ]{2,30}\.?\s*$|"
                          r"^\s*[—–\-]\s*(?:[A-Z][A-Za-z.'\-]+\s){1,4}[A-Z][A-Za-z.'\-]+\s*[.,]?\s*$", re.M)
# a postal address as advertisements print them: "Box 204", "Dept. 37-B", "843 Octavia Street", "3815 Diversey Ave."
POSTAL_RE = re.compile(r"\bBox\s+\d|\bDept\.?\s*[A-Z0-9]|\bDesk\s+[A-Z0-9]|\d{2,5}\s+[A-Z][A-Za-z.]+\s+(?:St\.|Street|Ave\.?|Avenue|Blvd\.?|Broadway|Bldg\.?)|"
                       r"\bStation\s+[A-Z]\b,?\s+[A-Z]{3,}|\b[A-Z][A-Z. &]{2,30}(?:CO\.|COMPANY|INC\.|CORP\.|LABORATORIES|PUB\. CO\.),\s*[A-Z]")
PRICE_RE = re.compile(r"\b\d{1,2}c\b|\$\s?\d|\bcents\b|\bpostpaid\b|\bstamps\b", re.I)
SMALL_WORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "by", "with", "from", "into", "as", "but", "nor", "vs", "via"}
FILLER_RE = re.compile(r"NEXT ISSUE|NEXT MONTH|NEXT WEEK|WRITE IN|A LETTER TO|ON SALE|NEWSSTAND|APPEARS ON|COMING UP|COMING NEXT|COMING SOON|"
                       r"COMIN' NEXT|WATCH FOR|DON'T MISS|DON'T FAIL TO READ|IN THE (?:\w+ )?(?:ISSUE|NUMBER)|IN NEXT (?:MONTH|WEEK)'S|"
                       r"NEXT (?:MONTH|WEEK)'S|NOW ON SALE|OUT NOW|^\W*COMING\W*$|FEATURED IN", re.I)
AD_WORDS = re.compile(r"\$\d|\bFREE\b|coupon|Dept\.|Send (?:no|for|me)|money back|guarantee|\bWrite (?:for|to)\b|postpaid|Address\b|catalog|Agents wanted|Learn at home|\bmail\b", re.I)
MENTION_RE = re.compile(r"Please mention|when answering advertisements", re.I)
_CONT = r"\b(?:continued|cont(?:['’]d|d)?\.?)\s+"
CONT_FROM_RE = re.compile(_CONT + r"from\s+(?:p(?:age|p)?\.?\s*)?(\d{1,4})\b", re.I)
CONT_TO_PAGE_RE = re.compile(_CONT + r"on\s+(?:p(?:age|p)?\.?\s*)?(\d{1,4})\b", re.I)
CONT_ON_RE = re.compile(CONT_TO_PAGE_RE.pattern + r"|continued\s+on\s+(?:the\s+)?next\s+page|\(continued\)|to be continued|to be concluded|concluded in|"
                        r"\[?\s*turn (?:the )?page\s*\]?|turn to page \d+", re.I)
VERSE_RE = re.compile(r"\bverse\b|\bpoem\b", re.I)
SERIAL_RE = re.compile(r"serial|part (?:one|two|three|four|five|six|i{1,3}|iv|v)\b|(?:two|three|four|five|six)-part|conclusion|to be continued|continued from|concluded in", re.I)
STOP_WORDS = {"the", "a", "an", "of", "with", "for", "and", "to", "in", "on", "at", "by", "your", "our", "this", "that"}
LETTERS_RE = re.compile(r"\beyrie\b|letters|round-?up|readers|reader's|chat with|air ?mail|mail ?bag|mailbag|camp-?fire|argonotes|brass tacks|"
                        r"discussions|\bforum\b|log-?book", re.I)
FEATURE_SECTIONS = re.compile(r"department|feature|introducing|miscellaneous|science department|editorial", re.I)
STORY_SECTIONS = re.compile(r"novel|story|stories|novelet|serial", re.I)
# advertisement classes (feedback of 2026-09-02, Sujin Kang; decision of 2026-09-03):
# house_next_issue, house_self, house_sibling, house_form, trade, classified
HOUSE_NEXT_RE = re.compile(r"next (?:issue|month|week)|coming (?:next|soon|in)|in the (?:\w+ )?issue|on sale|newsstand|"
                           r"appears? in|watch for|don't miss|will appear|forthcoming|out (?:next|on)\b|in our next|^\W*coming\W*$|featured in", re.I | re.M)
HOUSE_SELF_RE = re.compile(r"subscri|back (?:number|issue|cop)|anniversary number|numbers? combined|binder|renew|a year|per year|"
                           r"\b(?:12|twelve|six) issues|enclosed? find|use (?:the |this )?coupon|(?:copy|copies) of|"
                           r"every (?:month|week)|write (?:us|to us|in|the editor)|the editor|your favou?rite|ballot|vote|"
                           r"tell us|let us know|readers?['’]? (?:opinion|vote|choice)", re.I)
FORM_RE = re.compile(r"ballot|my favou?rite stor|coupon|fill (?:in|out)|tear (?:out|off)|clip (?:this|and|the)|"
                     r"check (?:the|one|here)|mark (?:the|your)|please send me|enclosed? (?:find|is)|i enclose|"
                     r"(?:reader'?s? )?name and address|^\s*name\b|^\s*address\b|^\s*city\b|^\s*street\b", re.I | re.M)
SIBLING_RE = re.compile(r"\b((?:[A-Z][A-Za-z'\-]+ ){1,3}(?:Magazine|Stories|Quarterly|Detective|Western|Romances|Adventures|"
                        r"Novels|Mysteries|Tales|Weekly|Monthly))\b")
SIBLING_STOP = {"write", "have", "big", "famous", "easy", "your", "concerning", "spiritual", "these", "how", "to", "short",
                "the", "a", "all", "new", "true", "real", "great", "best", "complete", "other", "many", "more", "love", "our"}
SIBLING_SALE_RE = re.compile(r"\bcents?\b|a copy|on sale|newsstand|every (?:month|week)|ask your (?:news|dealer)|now on|"
                             r"the (?:new|big|great|popular) (?:magazine|monthly|weekly)|in the current issue|companion magazine|"
                             r"publishers? of|by the same publishers|sister magazine", re.I)
COMPANY_RE = re.compile(r"\b((?:[A-Z][A-Za-z&'\.\-]*,? ){1,4}(?:Co\.|Company|Inc\.|Corp\.|Corporation|Institute|Laboratories|"
                        r"Laboratory|Bureau|School(?:s)?|Studios?|Products|Bros\.|Mfg\.? Co\.|Manufacturing Co\.|Publications|"
                        r"Publishing Co\.|Press|Supply Co\.|Foundation|Society|Association|System|Service|Exchange|Sales Co\.|"
                        r"Distributors|Importers|Novelty Co\.))(?=[ ,.\n]|$)")
COMPANY_STOP = re.compile(r"^(?:by |the |free |use |mail |send |write |this |your |our |at |to |for |of |in |and |a )|"
                          r"\b(?:civil|secret|public|employment|mail|high|night|day|home study|correspondence) (?:service|school)s?$|"
                          r"^(?:free |write |mail |send |use )?(?:coupon|today|now)", re.I)
# the signature line of an advertisement set in capitals ("HOLYOKE GAME CO. 4 OLIVER ST., HOLYOKE, MASS.")
CLOSING_RE = re.compile(r"\b(?:CO\.|INC\.|CORP\.|COMPANY|LTD\.|BROS\.|MFG\.)(?=\s|,|$)|\b(?:ST\.|STREET|AVE\.|AVENUE|BLVD\.?|DEPT\.?|BOX \d)\b|"
                        r"\b[A-Z]{3,}\s*\d{0,2},\s*(?:[A-Z]{2,5}\.?|[A-Z]\. ?[A-Z]\.)\s*$")
ADDRESS_RE = re.compile(r"\b(?:Dept\.?|Desk|Box|Bldg\.?|Building|Ave\.?|Avenue|St\.?|Street|Broadway|Blvd\.?|Station)\b\s*[A-Z0-9\-]*|"
                        r"\b[A-Z][a-z]+, (?:[A-Z][a-z]+\.?|[A-Z]{2})\s*$", re.M)
# the credit line under a by-line: "Author of 'Men Like Gods,' 'Outline of History,' etc."
CREDIT_RE = re.compile(r"^\s*[\(\[]?\s*(?:Co-)?Authors? of\b", re.I)
CREDIT_ROLE = "note"            # credits are notes since the paratext group of 2026-09-04 (teaser · synopsis · note)
# a synopsis heading printed above a serial part's text
SYNOPSIS_RE = re.compile(r"^\W*(?:the )?(?:story (?:thus|so) far|synopsis|what has (?:gone before|happened)|preceding chapters|"
                         r"the story to date|in the (?:previous|preceding|last) (?:instal|install|chapters)|what came before)", re.I)
SALE_RE = re.compile(r"\b\d{2}c\b|\bcents\b|a copy|postage|handling charge|Publishing Corp|Publishing Co|Corp\.|\bInc\.|send \$|"
                     r"only \$|price is|order (?:now|today|from)|at your (?:news|book)", re.I)
CATEGORY_HEAD_RE = re.compile(r"(?m)^\s*[A-Z][A-Z&' \-]{3,30}\s*$")
CHAPTER_SPLIT_RE = re.compile(r"^\s*(?:(?:CHAPTER|Chapter|CHAP\.|PART|Part)\s+([A-Za-z\d]+)|([IVXL]{1,6})|(\d{1,2}))\.?\s*[:\-—–]?\s*(.*)$", re.S)
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
EXCERPT_MIN_WORDS = 120       # a house announcement carrying this much narrative prose quotes the work


# ---------------------------------------------------------------- helpers

def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


ORDINALS = {"1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth", "5th": "fifth", "6th": "sixth", "7th": "seventh",
            "8th": "eighth", "9th": "ninth", "10th": "tenth", "11th": "eleventh", "12th": "twelfth"}


def norm_words(s):
    """norm() with numerals spelled out and spacing collapsed, for comparing a
    title the page sets as '7th ORDER' with the contents page's 'THE SEVENTH ORDER'."""
    return " ".join(ORDINALS.get(w, w) for w in norm(s).split())


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def words(s):
    return len((s or "").split())


def region_text(r):
    return (r.get("text") or "").strip()


def load_pages(iid):
    pages = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "layout", iid, "page_*.json"))):
        p = json.load(open(f, encoding="utf-8"))
        p["regions"] = sorted(p["regions"], key=lambda r: r.get("order", 0))
        pages[p["page"]] = p
    return pages


def key(pno, idx):
    return f"{pno}:{idx}"


# ---------------------------------------------------------------- page analysis

def folio_of(p):
    """Printed page number on the page, from header/footer regions or a bare
    number at the top or bottom edge."""
    H = p["height"]
    cands = []
    for i, r in enumerate(p["regions"]):
        t = region_text(r)
        m = re.fullmatch(r"\D{0,6}?(\d{1,4})\D{0,4}", t) if t else None
        if not m:
            continue
        y0, y1 = r["bbox"][1], r["bbox"][3]
        edge = y1 < 0.09 * H or y0 > 0.91 * H
        if r["label"] in ("PageHeader", "PageFooter") or (edge and words(t) <= 3):
            cands.append(int(m.group(1)))
    return cands[0] if cands else None


def running_heads(p):
    """Running heads: the magazine's name or the piece's title at the top or
    the bottom edge (Galaxy prints them at the foot)."""
    H = p["height"]
    out = []
    for i, r in enumerate(p["regions"]):
        t = region_text(r)
        if not t or re.fullmatch(r"\D{0,6}?\d{1,4}\D{0,4}", t) or MENTION_RE.search(t) or re.match(r"^\s*(?:CHAPTER|Chapter|CHAP\.)\s+\S", t):
            continue
        y0, y1 = r["bbox"][1], r["bbox"][3]
        edge = y1 < 0.07 * H or y0 > 0.93 * H
        if (r["label"] in ("PageHeader", "PageFooter") and words(t) <= 10) or (edge and words(t) <= 8 and t.upper() == t):
            out.append((i, t))
    return out


def is_furniture(p, i, r):
    t = region_text(r)
    H = p["height"]
    if r["label"] in ("PageHeader", "PageFooter"):
        # Layout labels are predictions. A whole narrative paragraph mistakenly
        # labelled as a running head must not silently vanish from the story.
        if words(t) >= 25 and len(t) >= 120 and re.search(r"[a-z]", t):
            return False
        # "CHAPTER III" at the top of a column is a chapter head the layout took for a running head
        return not (t and re.match(r"^\s*(?:CHAPTER|Chapter|CHAP\.|PART|Part)\s+\S", t) and chapter_head(t))
    if not t:
        return True
    if r["label"] in ("Picture", "Figure"):
        return True
    y0, y1 = r["bbox"][1], r["bbox"][3]
    if (y1 < 0.07 * H or y0 > 0.93 * H):
        if MENTION_RE.search(t) or re.fullmatch(r"\D{0,6}?\d{1,4}\D{0,4}", t):
            return True
        if words(t) <= 3 and not re.search(r"[a-z]", t) and not FILLER_RE.search(t):     # signature marks: "WS—7C", "□ □ □"
            return True
        if words(t) <= 2 and all(len(w) <= 5 and w.endswith(".") for w in t.split()):      # "Ast. St."
            return True
    if re.fullmatch(r"[\W_]{1,12}", t):            # "□ □ □", rules, ornaments
        return True
    return False


def byline_author(t):
    m = BYLINE_RE.match(t.replace("\n", " "))
    if not m or NOT_BYLINE_RE.match(t) or re.match(r"^\s*by\s+way\s+of\b", t, re.I):
        return None
    a = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
    a = re.sub(r"\s*\(.*?\)\s*$", "", a)            # "(age 15)"
    if re.match(r"^(?:(?:famous|popular|leading|well-known|well known)\s+)?"
                r"(?:authors|writers)\s+(?:read|buy|choose)\b", a, re.I):
        return None                                  # "by Famous Authors Read a POPULAR PUBLICATION"
    if not a or words(a) > 7 or len(a) > 60:
        return None
    if re.search(r"\d", a) and not re.search(r"[A-Za-z]{3}", a):
        return None
    ws = a.split()
    if sum(1 for w in ws if w.lower() in STOP_WORDS) > 1 or not any(w[:1].isupper() and len(w) >= 2 for w in ws):
        return None                                   # "By Relieving the Cause with" is not a person
    return a


def is_display(p, r):
    if r["label"] in ("Caption", "PageHeader", "PageFooter", "Picture", "Figure"):
        return False
    h = r["bbox"][3] - r["bbox"][1]
    return h >= DISPLAY_FRAC * p["height"] and words(region_text(r)) <= 14


def column_of(p, r):
    """Which column of the page a box stands in: 0 (left), 1 (right), or "span"
    for a box wider than six tenths of the page."""
    x0, _, x1, _ = r["bbox"]
    W = p["width"]
    if x1 - x0 > 0.6 * W:
        return "span"
    return 0 if (x0 + x1) / 2 < W / 2 else 1


def chapter_title_like(r, head):
    """The short line after a chapter number that is the chapter's title."""
    t = region_text(r)
    if not t or words(t) > 8 or byline_author(t):
        return False
    if r["label"] == "SectionHeader":
        return True
    if '"' in t or "\u201c" in t or (t.endswith(".") and not (t.upper() == t and words(t) <= 8)):
        return False
    hh = head["bbox"][3] - head["bbox"][1]
    return (r["bbox"][3] - r["bbox"][1]) <= 1.8 * hh


# words of narrative prose; "you", "I", "my", "we" are left out because advertisement copy is written in them
NARRATIVE = {"he", "she", "his", "her", "said", "asked", "was", "were", "had", "they", "him", "hers", "himself", "herself"}


def prose_like(r):
    """A paragraph of narrative rather than advertisement copy."""
    t = region_text(r)
    if r["label"] != "Text" or words(t) < 25 or AD_WORDS.search(t):
        return False
    ws = re.findall(r"[a-z']+", t.lower())
    return sum(1 for w in ws if w in NARRATIVE) >= 2


def continued_source_credit(pages, pno, byline):
    """A manuscript credit cannot divide a sentence across facing pages."""
    title = byline.get("embedded_title") or ""
    if (pno - 1 not in pages or not re.fullmatch(
            r"from\s+(?:a|an|the)\s+(?:[a-z]+\s+){0,6}manuscript", title, re.I)):
        return []
    prev, p = pages[pno - 1], pages[pno]
    previous = order_region_keys(pages, [(pno - 1, j) for j, r in enumerate(prev["regions"])
                                        if region_text(r) and not is_furniture(prev, j, r)])
    if not previous:
        return []
    last = prev["regions"][previous[-1][1]]
    if not prose_like(last) or re.search(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$", region_text(last)):
        return []
    credits = [byline["idx"]]
    for j in range(byline["idx"] + 1, len(p["regions"])):
        r, t = p["regions"][j], region_text(p["regions"][j])
        if not t or is_furniture(p, j, r):
            continue
        if words(t) <= 20 and re.match(r"^(?:translated|adapted|augmented)\b.{0,80}\bby\b", t, re.I | re.S):
            credits.append(j)
            continue
        return credits if prose_like(r) and t[:1].islower() else []
    return []


def signed_product_endorsement(p, start):
    """Require a signed endorsement tied to the same product's direct offer."""
    bi = start.get("byline_idx")
    if not start.get("embedded_title") or start.get("toc") or bi is None:
        return False
    regs = p["regions"]
    after = [(j, region_text(r)) for j, r in enumerate(regs) if j > bi and r["label"] == "Text"]
    if not after:
        return False
    product = re.match(r"^([A-Z]{5,})\b", after[0][1])
    if not product:
        return False
    product = product.group(1).lower()
    author_words = set(re.findall(r"[a-z]+", (start.get("author") or "").lower()))
    for j, t in after:
        signed = re.fullmatch(r"\(signed\)\s+(.+?)\.?", t, re.I)
        if not signed:
            continue
        name_words = set(re.findall(r"[a-z]+", signed.group(1).lower()))
        if len(name_words) < 2 or not name_words <= author_words:
            continue
        endorsement = " ".join(t_ for j_, t_ in after if j_ < j)
        offer = " ".join(region_text(r) for r in regs[j + 1:])
        if (len(re.findall(r"\b" + re.escape(product) + r"\b", endorsement, re.I)) >= 3
                and re.search(r"\bI\b", endorsement)
                and re.search(r"\b(?:enroll|recommend)\w*\b", endorsement, re.I)
                and re.search(r"\b" + re.escape(product) + r"\b", offer, re.I)
                and re.search(r"\bfree\b", offer, re.I) and re.search(r"\bcoupon\b", offer, re.I)
                and COMPANY_RE.search(offer)):
            return True
    return False


def self_declared_commercial_head(p, start):
    """A full-page product advertisement can quote an author in its headline."""
    if not start.get("embedded_title") or start.get("toc"):
        return False
    content = [(j, r) for j, r in enumerate(p["regions"]) if region_text(r) and not is_furniture(p, j, r)]
    if (not content or content[0][0] not in start["title_idx"]
            or any(prose_like(r) for _, r in content)):
        return False
    return any(r["bbox"][2] - r["bbox"][0] >= .6 * p["width"]
               and re.search(r"\bthis\s+advertisement\b.{0,200}\b(?:product|goods)\b.{0,80}\bfor\s+sale\b",
                             region_text(r), re.I | re.S) for _, r in content)


def demote_embedded_documents(pages, analysis):
    """Repeated witness statements inside a named story are narrative headings."""
    groups = {}
    all_starts = [(pn, s) for pn, info in analysis["pages"].items() for s in info["starts"]]
    for pn, s in all_starts:
        if (s.get("embedded_title") and not s.get("toc")
                and re.match(r"^(?:statement|testimony|deposition)\b", s.get("title") or "", re.I)):
            groups.setdefault(norm_words(s["title"]), []).append((pn, s))
    for group in groups.values():
        if len({norm_words(s.get("author")) for _, s in group}) < 2:
            continue
        grouped = {id(s) for _, s in group}
        proven = []
        for pn, s in group:
            if pn - 1 not in pages or pn + 1 not in pages:
                continue
            heads = [{norm_words(t) for t in analysis["pages"][q]["running"] if t}
                     for q in (pn - 1, pn + 1)]
            shared = (heads[0] & heads[1]) - {norm_words(analysis.get("magazine_head"))}
            previous = [(q, t) for q, t in all_starts if q < pn and id(t) not in grouped]
            if not shared or not previous:
                continue
            _, enclosing = max(previous, key=lambda pair: (pair[0], start_first(pair[1])))
            toc = enclosing.get("toc") or {}
            if (toc.get("type") not in ("story", "serial_part")
                    or norm_words(toc.get("title")) not in shared):
                continue
            before = [r for j, r in enumerate(pages[pn]["regions"]) if j < start_first(s)
                      and not is_furniture(pages[pn], j, r)]
            if (not any(prose_like(r) for r in before)
                    or any(THE_END_RE.fullmatch(region_text(r)) for r in before)):
                continue
            proven.append((pn, s, norm_words(toc["title"])))
        # Both independently confirmed statements must belong to the same work.
        for title in {title for _, _, title in proven}:
            matching = [(pn, s) for pn, s, owner in proven if owner == title]
            if len({norm_words(s.get("author")) for _, s in matching}) < 2:
                continue
            for pn, s in matching:
                info = analysis["pages"][pn]
                info["starts"].remove(s)
                info.setdefault("embedded_document_headings", []).extend(s["title_idx"])
                s["rejected"] = "repeated witness statement inside the story named by both adjacent running heads"
                info.setdefault("rejected_starts", []).append(s)


def generic_resume_blocker(p, info, magazine=""):
    """Reject positive evidence of a different opening in the narrative column.

    A pronoun-count heuristic cannot establish that a paragraph is non-fiction:
    short dialogue, first-person speech and descriptions commonly fail it.
    Advertising in the other column cannot block the story column either.
    """
    regs = p["regions"]
    first = min((start_first(s) for s in info.get("starts", [])), default=len(regs))
    candidates = [i for i in range(first) if i not in info.get("furniture", []) and prose_like(regs[i])]
    blockers = []
    for i in range(first):
        r, t = regs[i], region_text(regs[i])
        if not t or r["label"] == "Caption" or i in info.get("chapters", []) or chapter_head(t):
            continue
        if department_of(magazine, t):
            blockers.append((i, "new department"))
            continue
        service = re.search(r"(?is)^(?:a\s+)?(?:free\s+)?question\s+and\s+answer\s+service\b|"
                            r"^questions\b.{0,120}\b(?:sent|expert|department)\b|"
                            r"^instructions\b.{0,160}\bquestionnaire\b", t)
        if service:
            blockers.append((i, "service opening"))
            continue
        if i in info.get("furniture", []):
            continue
        announcement = words(t) <= 12 and r["label"] == "SectionHeader" and FILLER_RE.search(t)
        commercial = re.search(r"(?is)\b(?:mail|send|clip)\b.{0,35}\bcoupon\b|"
                               r"\bmoney\s+(?:back|refunded)\b|\bsend\s+no\s+money\b|"
                               r"\bfree\s+(?:trial|catalog|catalogue|booklet)\b", t)
        if announcement or commercial:
            blockers.append((i, "advertising or announcement opening"))
    reasons = []
    for candidate in candidates:
        box = regs[candidate]["bbox"]
        blocked = []
        for i, reason in blockers:
            other = regs[i]["bbox"]
            overlap = max(0, min(box[2], other[2]) - max(box[0], other[0]))
            same_column = overlap >= .35 * max(1, min(box[2] - box[0], other[2] - other[0]))
            if i <= candidate and other[1] < box[3] and same_column:
                blocked.append(f"{reason} before narrative at region {i}")
        if not blocked:
            return None
        reasons.extend(blocked)
    return reasons[0] if reasons else "no narrative paragraph before a new printed start"


def start_first(s):
    """Index of the first region a start owns on its page."""
    idx = list(s["title_idx"]) + list(s["subtitle_idx"]) + list(s.get("teaser_idx") or [])
    if s.get("byline_idx") is not None:
        idx.append(s["byline_idx"])
    if s.get("at") is not None:
        idx.append(s["at"])
    return min(idx) if idx else 0


def chapter_head(t):
    t1 = t.strip().split("\n")[0]
    return bool(CHAPTER_RE.match(t1)) and words(t) <= 8


def roman_int(s):
    s = (s or "").upper()
    if s in NUM_WORDS:
        return NUM_WORDS[s]
    if not s or any(c not in ROMAN for c in s):
        return int(s) if s.isdigit() else None
    total = 0
    for i, c in enumerate(s):
        v = ROMAN[c]
        if i + 1 < len(s) and ROMAN[s[i + 1]] > v:
            total -= v
        else:
            total += v
    return total


def title_case(t):
    """A title set in capitals (or all in lower case) in title case: small words
    lower except the first, the rest capitalised; a mixed-case title is kept."""
    if not t:
        return t
    letters = [c for c in t if c.isalpha()]
    if not letters or not (all(c.isupper() for c in letters) or all(c.islower() for c in letters)):
        return t
    out = []
    for n, w in enumerate(t.split()):
        core = re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", w)
        lw = core.lower()
        if n and lw in SMALL_WORDS:
            out.append(w.lower())
        elif "'" in core and len(core) > 2 and core.upper() == core and lw.endswith("'s"):
            out.append(w[:1].upper() + w[1:].lower())          # GALAXY'S → Galaxy's
        else:
            out.append(re.sub(r"[A-Za-z][A-Za-z']*", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), w, count=1)
                       if re.match(r"[^A-Za-z]*[A-Za-z]", w) else w)
    return " ".join(out)


def title_prefix(t, title):
    """The text t is the first words of `title` set in a box of its own
    ("THE YEAR" of "The Year of the Jackpot", "Ace Hart" of "Ace Hart Loses
    His Man", "the" of "The Seventh Order"): a prefix by whole words, shorter
    than the title, and at least a word of it."""
    a, b = norm_words(t), norm_words(title or "")
    if not a or not b or len(a) >= len(b) or words(t) > 8:
        return False
    return b.startswith(a) and (len(a) == len(b) or b[len(a)] == " ")


def title_suffix(t, title):
    """The text t is the last words of `title` ("TALK" of "Dead Men Talk")."""
    a, b = norm_words(t), norm_words(title or "")
    if not a or not b or len(a) >= len(b) or words(t) > 8:
        return False
    return b.endswith(a) and b[-len(a) - 1] == " "


def split_part(title):
    """'The Waning of a World (Part One)' -> ('The Waning of a World', 'Part One', 1, None);
    'The Murder Monster [Part I]' -> (…, 'Part I', 1, None); 'Satan's Garden (Conclusion)' ->
    (…, 'Conclusion', None, None) with the label telling it is the last; a plain title -> (title, None, None, None)."""
    t = (title or "").strip()
    m = PART_MARK_RE.search(t)
    if not m or m.start() == 0:
        return t, None, None, None
    label = re.sub(r"\s+", " ", m.group("label")).strip(" .")
    n = roman_int(m.group("n")) if m.group("n") else None
    tot = roman_int(m.group("tot")) if m.group("tot") else None
    work = t[:m.start()].strip(" -—–:,[(")
    return (work or t), label, n, tot


def looks_like_verse(text):
    """True for a text set as verse (short lines, most beginning with a capital, many without sentence
    punctuation at the end), False for prose, None when there is too little text to say (rules 2.10)."""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    if len(lines) < 6:
        return None
    lens = sorted(len(l) for l in lines)
    median = lens[len(lens) // 2]
    caps = sum(1 for l in lines if l[0].isupper()) / len(lines)
    open_end = sum(1 for l in lines if not l.endswith((".", "!", "?", "\"", "\u201d", ":"))) / len(lines)
    if median <= 48 and caps >= 0.7 and open_end >= 0.4:
        return True
    if median >= 60 or caps < 0.4:
        return False
    return None


COVER_PRICE_RE = re.compile(r"\b\d{1,2}\s?(?:c|¢|cents)\b|\$\s?\d|\b(?:price|per copy)\b|\b(?:1[89]|20)\d\d\b|"
                            r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b|\bvol(?:ume|\.)\s*[\divxlc]+", re.I)


def cover_page_score(p, magazine=None):
    """How much a scan looks like a magazine's cover (rules 2.12): the masthead (the magazine's name in a display
    line), a price, a date or a volume line, cover lines (short display lines), little or no running prose. Returns
    (score, evidence). A score of 3 or more is a cover; a page with a paragraph of prose of 80 words or more, or
    more than two such paragraphs, is not."""
    regs = p.get("regions", [])
    texts = [(r, " ".join(region_text(r).split())) for r in regs]
    texts = [(r, t) for r, t in texts if t]
    if not texts:
        return 0, ["no text"]
    ev, score = [], 0
    prose = [t for r, t in texts if words(t) >= 80 and prose_like(r)]
    if len(prose) >= 1:
        return 0, [f"{len(prose)} paragraph(s) of prose"]
    short = sum(1 for r, t in texts if words(t) <= 12)
    if short >= max(3, 0.6 * len(texts)):
        score += 1; ev.append("short display lines")
    if magazine and any(sim(t, magazine) > 0.6 or norm(magazine) in norm(t) for r, t in texts if words(t) <= 10):
        score += 2; ev.append("the magazine's name")
    joined = "\n".join(t for _, t in texts)
    if COVER_PRICE_RE.search(joined):
        score += 1; ev.append("a price, a date or a volume line")
    if sum(words(t) for _, t in texts) <= 120:
        score += 1; ev.append("little text")
    if any(r.get("label") in ("Picture", "Figure") for r in regs):
        score += 1; ev.append("a picture")
    return score, ev


def first_scan_kind(pages, magazine=None):
    """What the first scan of an issue is (the survey of 26 October 2026): 'cover', 'contents' (the contents list
    on the first page, as The Black Cat printed it), 'body' (running text: the cover and the front matter are
    missing), 'blank', or 'other' (an advertisement, a title page)."""
    if not pages:
        return "blank", []
    pno = min(pages)
    p = pages[pno]
    regs = [r for r in p.get("regions", []) if region_text(r).strip()]
    if not regs:
        return "blank", ["no text on the first scan"]
    score, ev = cover_page_score(p, magazine)
    joined = "\n".join(region_text(r) for r in regs)
    n_num_lines = len(re.findall(r"(?m)(?:\.\s*){3,}\s*\d{1,4}\s*$|^\s*\d{1,4}\s*$", joined))
    if re.search(r"\bCONTENTS\b", joined, re.I) or n_num_lines >= 4:
        return "contents", ev + ["a contents list on the first scan"]
    if score >= 3:
        return "cover", ev
    prose = [r for r in regs if words(region_text(r)) >= 60 and prose_like(r)]
    if len(prose) >= 2 or (prose and sum(words(region_text(r)) for r in regs) >= 200):
        return "body", ev + [f"{len(prose)} paragraph(s) of running text on the first scan"]
    return "other", ev


def strip_poem_mark(title):
    m = POEM_MARK_RE.search(title or "")
    return (title[:m.start()].strip(), True) if m else (title, False)


_DEPARTMENTS = None


def departments():
    """config/departments.json: per magazine, the standing departments — name, type
    (feature · letters · house), the usual author — so a record whose title is a
    department's name gets the department field and the type the list gives."""
    global _DEPARTMENTS
    if _DEPARTMENTS is None:
        p = os.path.join(ROOT, "config", "departments.json")
        try:
            _DEPARTMENTS = json.load(open(p, encoding="utf-8"))
        except Exception:
            _DEPARTMENTS = {}
    return _DEPARTMENTS


_ILLUS = {}


def illustrators_of(magazine):
    """The known illustrators of a magazine (config/illustrators.json: a list of
    names per magazine-name pattern, "*" for all), for reading a signature
    the layout took for text."""
    if not _ILLUS:
        p = os.path.join(ROOT, "config", "illustrators.json")
        try:
            _ILLUS.update(json.load(open(p, encoding="utf-8")))
        except Exception:
            _ILLUS["*"] = []
    out = list(_ILLUS.get("*", []))
    m = (magazine or "").lower()
    for pat, names in _ILLUS.items():
        if pat != "*" and re.search(pat, m):
            out += names
    return out


GENERIC_FIRST_WORDS = {"the", "thrilling", "popular", "new", "all", "short", "famous", "true", "complete", "best", "super", "ten",
                       "big", "real", "startling", "exciting", "spicy", "dime", "double", "triple", "two", "three", "five", "detective",
                       "western", "love", "romance", "romances", "science", "fiction", "sea", "air", "war", "sport", "sports", "story",
                       "stories", "magazine", "weird", "wonder", "amazing", "astonishing", "fantastic", "strange", "mystery"}


def magazine_matches(key, magazine):
    """Whether a departments-list key names the magazine: the names are alike, or they begin with the same
    distinctive word ('Galaxy' / 'Galaxy Science Fiction', 'Astounding Stories' / 'Astounding Stories of
    Super-Science'; rules 2.10)."""
    nk, nm = norm(key), norm(magazine)
    if not nk or not nm:
        return False
    if nk == nm or nk in nm or nm in nk or sim(key, magazine) > 0.85:
        return True
    a = [w for w in nk.split() if w != "the"]
    b = [w for w in nm.split() if w != "the"]
    return bool(a and b and a[0] == b[0] and a[0] not in GENERIC_FIRST_WORDS)


def department_of(magazine, title):
    """(name, type, author) of the department a title names, else None."""
    if not title:
        return None
    for key, deps in departments().items():
        if key != "*" and not (magazine and magazine_matches(key, magazine)):
            continue
        for d in deps:
            names = [d["name"]] + list(d.get("also") or [])
            if any(sim(title, nm) > 0.75 or norm(title) == norm(nm) for nm in names):
                return d["name"], d.get("type") or "feature", d.get("author")
    return None


def split_chapter(text):
    """'CHAPTER II\\nThe Thing in the Vault' -> ('II', 2, 'The Thing in the Vault');
    'IV. The Coming of the Beast' -> ('IV', 4, 'The Coming of the Beast'); a bare
    title -> (None, None, title). The number is kept as printed; n is its value."""
    t = (text or "").strip()
    m = CHAPTER_SPLIT_RE.match(t)
    if not m or not chapter_head(t.split("\n")[0]):
        return None, None, re.sub(r"\s+", " ", t)
    num = m.group(1) or m.group(2) or m.group(3)
    rest = re.sub(r"\s+", " ", (m.group(4) or "")).strip(" .:-—–")
    return num, roman_int(num), (rest or None)


def form_block(p, i, info):
    """A ballot or coupon starting at region i: a form line ('My favorite
    stories in the May WEIRD TALES are:', 'Please send me…', 'Name…') followed
    by Table/Form regions or more form lines. Returns the block's indices, or
    None."""
    regs = p["regions"]
    t = region_text(regs[i])
    if words(t) > 40 or not FORM_RE.search(t):
        return None
    block = [i]
    j = i + 1
    while j < len(regs):
        if j in info["furniture"]:
            j += 1
            continue
        r = regs[j]
        tj = region_text(r)
        if r["label"] in ("Table", "Form") or (words(tj) <= 40 and (FORM_RE.search(tj) or re.fullmatch(r"[\W_\d]+", tj)
                                                                   or re.search(r"[-_.…]{3,}", tj)
                                                                   or (words(tj) <= 20 and tj.rstrip().endswith(":")))):
            block.append(j)
        elif words(tj) <= 60 and re.search(r"fill (?:in|out)|help us|it will help|mail (?:this|it|to)|send (?:this|it|to)|address(?:ed)? to", tj, re.I):
            block.append(j)
        else:
            break
        j += 1
    if len(block) < 2 or not any(regs[j]["label"] in ("Table", "Form") or re.search(r"[-_.…]{3,}", region_text(regs[j])) for j in block):
        return None
    return block


def parse_toc(pages):
    """Entries (title, author, printed page, blurb, section) from the contents
    page, whatever its layout; empty when the issue has none we can read."""
    best = None
    for pno in sorted(pages):
        if pno > 14:
            break
        p = pages[pno]
        if pno == min(pages) and cover_page_score(p)[0] >= 3:
            continue                                           # the cover is never the contents page (26 October 2026)
        texts = [region_text(r) for r in p["regions"]]
        joined = "\n".join(texts)
        has_word = bool(re.search(r"\bCONTENTS\b", joined, re.I)) or any(r["label"] == "TableOfContents" for r in p["regions"])
        n_pageno = (len(re.findall(r"(?:\.\s*){3,}\s*[^\n]{0,60}?(\d{1,4})\s*(?=\n|$)", joined))
                    + len(re.findall(r"\n\s*(\d{1,4})\s*(?=\n|$)", joined)))
        score = (3 if has_word else 0) + min(n_pageno, 12)
        if score >= 4 and (best is None or score > best[0]):
            best = (score, pno)
    if not best:
        return [], None
    pno = best[1]
    p = pages[pno]
    entries = []
    section = ""
    # walk the regions; a section label is a short header without a page number
    seq = []
    for r in p["regions"]:
        t = region_text(r)
        if not t:
            continue
        seq.append((r["label"], t))
    text = "\n".join(t for _, t in seq)
    # normalise dot leaders and glue
    text = text.replace("\xa0", " ")
    text = re.sub(r"(?:\s*\.){3,}", " ….. ", text)
    text = re.sub(r"[ \t]+", " ", text)
    # candidate entries: TITLE [….. AUTHOR] [….. ] PAGE  — in several printed layouts
    pats = [
        # Title ….. Author ….. 12   /   Title ….. Author 12   /   Title..... Author 12
        re.compile(r"(?P<title>[^\n…]{3,80}?)\s*…\.\.\s*(?:(?P<author>[A-Z][^\n…\d]{2,50}?)\s*(?:…\.\.\s*)?)?(?P<page>\d{1,4})\b"),
        # TITLE \n AUTHOR \n 12      (Astounding 1930-01)
        re.compile(r"(?m)^(?P<title>[A-Z][A-Z0-9' ,\-!?.]{2,60})\n(?P<author>[A-Z][A-Za-z.' \-]{2,50})\n(?P<page>\d{1,4})\s*$"),
        # TITLE ….. \n by Author 12  (Galaxy)
        re.compile(r"(?P<title>[^\n…]{3,80}?)\s*…\.\.\s*\n?\s*by (?P<author>[^\n\d]{2,50}?)\s*(?P<page>\d{1,4})\b", re.I),
        # TITLE ….. 12 \n by Author  (Galaxy 1952-03)
        re.compile(r"(?P<title>[^\n…]{3,80}?)\s*…\.\.\s*(?P<page>\d{1,4})\s*\n\s*by (?P<author>[^\n]{2,50})", re.I),
        # TITLE \n blurb line(s) \n AUTHOR \n 12   (Astounding 1930-03)
        re.compile(r"(?m)^(?P<title>[A-Z][A-Z0-9' ,\-!?.]{2,60})\n(?:[^\n]{20,}\n){0,3}(?P<author>[A-Z][A-Z. '\-]{2,50})\n(?P<page>\d{1,4})\s*$"),
    ]
    found = {}
    for pat in pats:
        for m in pat.finditer(text):
            title = re.sub(r"\s+", " ", m.group("title")).strip(" .:-")
            title = re.sub(r"^\d{1,2}\s+(?=[A-Z])", "", title)      # OCR noise like "5 Empty Holsters"
            # the previous blurb run together with the title ("Verse ReprintThe Eyrie"): cut at the last
            # lower-to-upper join that is not a name particle (McHenry, MacDonald, DeVille)
            cuts = []
            for x in re.finditer(r"([A-Za-z']+)(?=[A-Z][a-z])", title):
                left = x.group(1)
                if len(left) >= 3 and left not in ("Mac", "Van", "Von", "Des", "Del", "Della") and left[-1].islower():
                    cuts.append(x.end())
            if cuts and words(title[cuts[-1]:]) >= 1:
                title = title[cuts[-1]:].strip()
            author = (m.group("author") or "").strip(" .:-") if "author" in m.groupdict() else ""
            author = re.sub(r"\s+", " ", author)
            try:
                page = int(m.group("page"))
            except ValueError:
                continue
            if words(title) > 12 or page > 1200:
                continue
            if re.fullmatch(r"(?:cover design|cover)\b.*", title, re.I):
                continue
            k = norm(title)
            e = {"title": title, "author": author or None, "page": page, "start": m.start(), "end": m.end()}
            if k not in found or (not found[k]["author"] and author):     # a later pattern may add the author
                if k in found:
                    e["start"], e["end"] = found[k]["start"], max(found[k]["end"], m.end())
                found[k] = e
    entries = list(found.values())
    entries.sort(key=lambda e: e["start"])
    for n, e in enumerate(entries):
        nxt = entries[n + 1]["start"] if n + 1 < len(entries) else len(text)
        tail = text[e["end"]:nxt]
        tail = re.sub(r"^\s*(?:by [^\n]*\n)?", "", tail, flags=re.I)
        blurb = " ".join(l.strip() for l in tail.split("\n") if l.strip() and not re.fullmatch(r"[A-Z][A-Z \-—&']{4,40}", l.strip()))
        e["blurb"] = re.sub(r"\s+", " ", blurb)[:400]
    # section labels (NOVEL, SHORT STORIES, DEPARTMENTS…) and blurbs: the header right before each entry
    labels = [(m.start(), m.group(0).strip()) for m in re.finditer(r"(?m)^[A-Z][A-Z \-—&']{4,40}(?:[—\-][A-Za-z]+)?$", text)]
    for e in entries:
        sec = ""
        for pos, lab in labels:
            if pos < e["start"] and not sim(lab, e["title"]) > 0.8 and (not e["author"] or sim(lab, e["author"]) < 0.8):
                sec = lab
        e["section"] = sec
        e.pop("start", None)
        e.pop("end", None)
    entries.sort(key=lambda e: e["page"])
    type_toc_entries(entries)
    # a second contents page (Weird Tales prints two): the same parse on the next page
    return entries, pno


# the kinds of contents entries (rules 2.9; Sujin Kang's level-1 feedback of 21 October 2026, 31 issues checked)
VERSE_SECTION_RE = re.compile(r"\bverses?\b|\bpoems?\b|\bpoetry\b|\bsongs\b|\bballads?\b|\brhymes?\b", re.I)
NONFICTION_SECTIONS = re.compile(r"\barticles?\b|\btrue\b|\bfacts?\b|non-?fiction|\bhints?\b|how-?to|\bquiz\b|\bpictorial\b|\bpictures?\b", re.I)
# a 'true story' is a story for the readers (Sujin Kang, 26 October 2026) but is assembled as a feature: typed story as a
# story, True Detective's pieces swallowed the advertisement at their foot (the feature path cuts it); the record is
# retyped story once assembled (rules 2.10)
TRUE_STORY_RE = re.compile(r"\btrue\b", re.I)
# an unsigned entry that names a department rather than a piece, on a contents page that names no authors
DEPARTMENT_TITLE_RE = re.compile(r"\b(?:circle|services?|department|club|readers?|free to|mail|forum|notes?|news|talk|chat|corner|page|editorial|"
                                 r"editor|letters?|contest|prize|answers?|questions?|review|reviews|calendar|index|cover)\b", re.I)
# not a piece: a month, a volume line, the word CONTENTS with a month (rules 2.10)
NOT_A_PIECE_RE = re.compile(r"^(?:contents\s+)?(?:january|february|march|april|may|june|july|august|september|october|november|december)"
                            r"(?:\s+\d{1,2})?(?:,?\s+\d{4})?$|^contents\b|^vol(?:ume|\.)?\s*[\divxlc]+[,.]?\s*(?:no\.?\s*[\d]+)?\b|^no\.\s*\d+$", re.I)
STORY_WORDS = re.compile(r"novel|story|stories|novelet|serial|\btales?\b|fiction", re.I)
# a line of the contents page that is not a name in the author's place: a department's description
# ("Where readers, writers and adventurers meet", "Information you can't get elsewhere")
NAME_PARTICLES = {"Mac", "Mc", "Van", "Von", "De", "Del", "Della", "Des", "La", "Le", "Di", "Du", "St", "O"}
SECTION_WORD_RE = re.compile(r"^(?P<sec>VERSE|POEMS?|POETRY|NOVELETTES?|NOVELS?|SHORT STORIES|SERIALS?|FEATURES?|DEPARTMENTS?|ARTICLES?)\s*[{(\[|:]\s*(?=\S)", re.I)
CHAPTERS_PREFIX_RE = re.compile(r"^CHAPTERS?\s+[IVXLC\d]+(?:\s*[-–—]\s*[IVXLC\d]+)?\.?\s*(?=[A-Z])")
ROMAN_RE = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")
IN_PARTS_RE = re.compile(r"[\s,.(\[]*\(?\s*(?:a\s+)?(?:serial\s+)?in\s+(?P<tot>two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+parts?\s*\)?\s*$", re.I)
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def is_subtitle_line(author):
    """True when the author field holds a description, not a name: four or more words with at least two
    lower-case words that are not name particles."""
    if not author:
        return False
    ws = author.split()
    if len(ws) < 4:
        return False
    lower = [w for w in ws if w[:1].islower() and w.strip(".,'") not in ("de", "la", "le", "van", "von", "di", "du", "y")]
    return len(lower) >= 2


def split_glued_name(title):
    """'The Albino CanaryHal Moore' -> ('The Albino Canary', 'Hal Moore'); a title with no name glued on -> (title, None).
    The join is a lower-case letter followed by a capital that begins a name of two to four capitalised words."""
    if not title:
        return title, None
    for m in re.finditer(r"([a-z'’])(?=[A-Z][a-z])", title):
        left, right = title[:m.end()], title[m.end():]
        rw = right.split()
        if 2 <= len(rw) <= 4 and all(re.fullmatch(r"[A-Z][A-Za-z'’.\-]*\.?", w) for w in rw) and words(left) >= 1:
            last_left = left.split()[-1]
            if last_left in NAME_PARTICLES or len(last_left) < 3:
                continue
            return left.strip(), right.strip()
    return title, None


def repair_toc_entries(entries):
    """Faults of the printed contents that the readers leave in the entries (rules 2.9): a title with the
    author's name glued on and no author; a stray capital glued to the title that is the author's initial
    ('Battle of WizardsL' / 'Ron Hubbard'); a section word with its brace in front of the title ('VERSE { HALLOWE'EN
    CANDLE'); the chapter range of the previous entry in front of the title ('CHAPTERS I-VI EVERYTHING BUT THE TRUTH');
    'In Five Parts' after the title (the serial's total); a description in the author's place; a title that went
    into the previous entry's blurb and left its own entry without one. In place; returns the entries."""
    for n, e in enumerate(entries):
        t = (e.get("title") or "").strip()
        a = (e.get("author") or "").strip() or None
        m = SECTION_WORD_RE.match(t)
        if m:
            t = t[m.end():].strip()
            e["section_word"] = m.group("sec").upper()
        m = CHAPTERS_PREFIX_RE.match(t)
        if m:
            rest = t[m.end():]
            # the range's last numeral may have swallowed the title's first letters ('CHAPTERS IX-XIIILUCK'):
            # the numeral is the longest valid one, the rest is the title
            back = re.match(r"^(.*?[\s\-–—])?([IVXLC]+)$", t[:m.end()].rstrip(". "))
            if back:
                num = back.group(2)
                for k in range(len(num), 0, -1):
                    if ROMAN_RE.fullmatch(num[:k]):
                        rest = num[k:] + rest
                        break
            if words(rest) >= 1:
                t = rest.strip()
        m = IN_PARTS_RE.search(t)
        if m and words(t[:m.start()]) >= 1:
            tot = m.group("tot").lower()
            e["part_total"] = NUMBER_WORDS.get(tot) or (int(tot) if tot.isdigit() else None)
            t = t[:m.start()].strip(" .,:-")
        if a and is_subtitle_line(a):
            e["subtitle"] = a
            a = None
        if not a:
            t2, a2 = split_glued_name(t)
            if a2:
                t, a = t2, a2
        if a and re.search(r"[a-z][A-Z]$", t) and re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][A-Za-z'’.\-]+){1,3}", a):
            a = t[-1] + ". " + a                           # the initial of the name went to the title's end
            t = t[:-1].strip()
        if not t and n > 0:
            prev = entries[n - 1]
            bl = (prev.get("blurb") or "").strip()
            m = IN_PARTS_RE.search(bl)
            core = bl[:m.start()].strip(" .,:-(") if m else bl
            if core and words(core) <= 12 and core[:1].isupper():
                t = core
                if m:
                    tot = m.group("tot").lower()
                    e["part_total"] = NUMBER_WORDS.get(tot) or (int(tot) if tot.isdigit() else None)
                    e["serial_mark"] = True
                prev["blurb"] = ""
        e["title"], e["author"] = t, a
    return entries


def drop_non_pieces(entries):
    """Entries that are not pieces (rules 2.10, from the contents checks: 'March', 'JULY', 'CONTENTS JUNE',
    'Vol. 27, No. 1 A THRILLING PUBLICATION'). In place; returns the entries."""
    keep = []
    for e in entries:
        t = (e.get("title") or "").strip()
        if t and NOT_A_PIECE_RE.match(t) and words(t) <= 8:
            continue
        keep.append(e)
    entries[:] = keep
    return entries


def type_toc_entries(entries):
    """The kind of every contents entry from its section label, title, by-line and blurb (2.1 onwards; the kinds of
    2.9 follow Sujin Kang's level-1 feedback of 21 October 2026): poem — a verse section or marker (verse, poems,
    songs, ballads); letters — the readers' and the editor's departments (letters, the Eyrie, the Camp-Fire, Our Air
    Mail, the Log-Book, Discussions, Brass Tacks, an editorial or editor's page, a department described as the
    readers' meeting place); serial_part — a part marker, a serial section or label, 'In Five Parts'; feature — a
    non-fiction section (articles, true stories, fact, helpful hints, a quiz, pictures), a departments or features
    section without a story word, an unsigned entry outside a story section; story — a story section (novel,
    novelette, short stories, serials, tales, fiction — 'FEATURED NOVELET' is a story section, not a feature) or a
    signed entry."""
    repair_toc_entries(entries)
    drop_non_pieces(entries)
    # a contents page that names no authors at all (True Romances, 12 Sports Aces, the Illustrated Detective):
    # an unsigned entry is not a filler there, it is the magazine's ordinary piece (rules 2.10)
    signed = sum(1 for e in entries if e.get("author"))
    unsigned_contents = len(entries) >= 4 and signed <= 0.34 * len(entries)
    for e in entries:
        s = e.get("section") or ""
        if e.get("section_word"):
            s = (e["section_word"] + " " + s).strip()
        bl = e.get("blurb") or ""
        sub = e.get("subtitle") or ""
        lab = e.get("type_label") or ""
        prose_blurb = bl and sum(1 for c in bl if c.isupper()) < 0.5 * max(1, sum(1 for c in bl if c.isalpha()))
        if e["author"] and re.fullmatch(r"(?:a |the )?(?:department|editor|editors|readers|staff)s?", e["author"].strip(), re.I):
            s = (s + " " + e["author"]).strip()
            e["author"] = None
        title = e.get("title") or ""
        nonfiction = NONFICTION_SECTIONS.search(s) or NONFICTION_SECTIONS.search(lab)
        story_section = STORY_WORDS.search(s) and not nonfiction
        if TRUE_STORY_RE.search(s) or TRUE_STORY_RE.search(lab) or re.search(r"\(true (?:story|tale)\)", title, re.I):
            e["true_story"] = True
        if re.search(r"\bclub\b|\bnext (?:issue|month|week)\b|coming next", title, re.I):
            e["type"] = "feature"                     # the publisher's own notice: the departments list makes it house
        elif VERSE_SECTION_RE.search(s) or VERSE_SECTION_RE.search(lab) or VERSE_RE.search(bl[:60]) or POEM_MARK_RE.search(title) \
                or re.search(r"\bsongs\b", title, re.I):
            e["type"] = "poem"
        elif LETTERS_RE.search(title) or LETTERS_RE.search(sub) or LETTERS_RE.search(bl[:80]) or re.search(r"\bletters\b", s, re.I) \
                or re.search(r"editorial|editor'?s? (?:page|notes?|desk|corner|chat)|^the editor\b|^ask\b", title, re.I):
            e["type"] = "letters"
        elif e.get("part") or e.get("serial_mark") or e.get("part_total") or SERIAL_RE.search(s) or SERIAL_RE.search(lab) \
                or (prose_blurb and SERIAL_RE.search(bl[:80])) or SERIAL_RE.search(title):
            e["type"] = "serial_part"
        elif nonfiction:
            e["type"] = "feature"
        elif story_section and (e["author"] or STORY_WORDS.search(lab)):
            e["type"] = "story"
        elif FEATURE_SECTIONS.search(s) or FEATURE_SECTIONS.search(lab):
            e["type"] = "feature"
        elif not e["author"] and not story_section:
            e["type"] = "feature"                     # an unsigned short piece: a filler, a department
            if unsigned_contents and not DEPARTMENT_TITLE_RE.search(title) and not FEATURE_SECTIONS.search(s):
                e["unsigned_story"] = "all" if signed == 0 else True   # ... but on a contents page that names no authors, a story: retyped once assembled
        else:
            e["type"] = "story"
    return entries


def nearby_serial_contents(pages, pno, byline, entries, offset):
    """Recover a missing display title from independent serial-start evidence.

    A bad folio must not put a contents-backed byline on the preceding page.
    Require an exact author, a printed installment descriptor, nearby expected
    page, and the contents title in a following running head.
    """
    if offset is None:
        return None
    regs = pages[pno]["regions"]
    bi = byline["idx"]
    if any(region_text(r) and not is_furniture(pages[pno], j, r)
           for j, r in enumerate(regs[:bi])):
        return None
    descriptor = " ".join(region_text(r) for r in regs[bi + 1:bi + 3])
    if not re.match(r"(?i)^(?:first|second|third|fourth|fifth|sixth|seventh|eighth|last|final|\d+(?:st|nd|rd|th)?)\s+of\s+(?:\w+|\d+)\s+parts?\b", descriptor):
        return None
    matches = []
    for e in entries:
        if (e.get("type") != "serial_part" or not e.get("author")
                or norm(e["author"]) != norm(byline["author"])
                or abs(e["page"] + offset - pno) > 2):
            continue
        if any(norm(t) == norm(e["title"]) for q in range(pno + 1, pno + 5)
               if q in pages for _, t in running_heads(pages[q])):
            matches.append(e)
    return matches[0] if len(matches) == 1 else None


def toc_folio_enabled():
    if os.environ.get("PULP_TOC_FOLIO") is not None:
        return os.environ.get("PULP_TOC_FOLIO") not in ("0", "", "false", "no")
    try:
        cfg = json.load(open(os.path.join(ROOT, "config", "corpus_settings.json"), encoding="utf-8"))
        return bool(cfg.get("assembly", {}).get("toc_folio", True))
    except Exception:
        return True


def toc_from_reader(TF):
    """toc_folio's entries in the shape the rules use (title, author, page, blurb, section, type, scan)."""
    out = []
    for e in TF["entries"]:
        page = e.get("page")
        if page is None and e.get("scan_page") is not None:
            page = TF["folio"]["printed"].get(e["scan_page"])
        if page is None:
            continue
        x = {"title": e["title"], "author": e.get("author") or None, "page": int(page), "blurb": e.get("blurb", ""),
             "section": e.get("section", ""), "scan_page": e.get("scan_page"), "placed_by": e.get("placed_by"),
             "confidence": e.get("confidence"), "toc_scan": e.get("toc_scan")}
        for k in ("part", "type_label", "page_read"):
            if e.get(k):
                x[k] = e[k]
        out.append(x)
    out.sort(key=lambda e: e["page"])
    return type_toc_entries(out)


_TOC_OVERRIDES = {}


def toc_page_override(iid):
    """The contents page a reader named for an issue (config/toc_page_overrides.json, written by
    scripts/apply_contents_scans.py from the check page's answers), or None."""
    if not _TOC_OVERRIDES:
        try:
            _TOC_OVERRIDES.update(json.load(open(os.path.join(ROOT, "config", "toc_page_overrides.json"), encoding="utf-8")).get("scans", {}))
        except Exception:
            pass
        _TOC_OVERRIDES.setdefault("_loaded", True)
    v = _TOC_OVERRIDES.get(iid)
    return int(v) if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()) else None


def analyse(pages, force_scan=None, magazine=None):
    """Everything the rules need to know about every page."""
    A = {"pages": {}, "toc": [], "toc_page": None, "offset": None, "folio_range": None}
    TF = None
    if toc_folio_enabled():
        try:
            import toc_folio
            TF = toc_folio.analyse_issue(pages, force_scan=force_scan, magazine=magazine)
            if force_scan is not None:
                A["toc_forced_scan"] = force_scan
        except Exception as ex:                       # the 2.6 reading stands; the failure is recorded
            A["toc_folio_error"] = repr(ex)[:300]
    A["toc"], A["toc_page"] = parse_toc(pages)
    # a second contents page right after the first (Weird Tales)
    if A["toc_page"] and (A["toc_page"] + 1) in pages:
        more, _ = parse_toc({A["toc_page"] + 1: pages[A["toc_page"] + 1]})
        known = {norm(e["title"]) for e in A["toc"]}
        A["toc"] += [e for e in more if norm(e["title"]) not in known]
        A["toc"].sort(key=lambda e: e["page"])
    # Some contents layouts print "TITLE, by Author" in one OCR box. Keep
    # the author out of title matching so a real printed start can claim it.
    for entry in A["toc"]:
        inline = re.match(r"^(.+?)\s*[,—–]\s*(by\s+.+)$", entry["title"], re.I)
        author = byline_author(inline.group(2)) if inline else None
        if author and not entry.get("author"):
            entry["title_as_read"] = entry["title"]
            entry["title"], entry["author"] = inline.group(1).strip(), author
    if TF is not None:
        A["toc_v26"] = [{k: e.get(k) for k in ("title", "author", "page", "type")} for e in A["toc"]]
        A["toc_page_v26"] = A["toc_page"]
        A["toc_kind"] = TF["toc_kind"]
        if TF["entries"] or TF["toc_kind"] == "chapters":
            A["toc"] = toc_from_reader(TF)
            A["toc_page"] = TF["toc_pages"][0] if TF["toc_pages"] else None
            A["toc_pages"] = TF["toc_pages"]
            A["toc_chapters"] = [{k: e.get(k) for k in ("title", "page")} for e in TF.get("chapters", [])]
    offsets = Counter()
    for pno, p in pages.items():
        f = folio_of(p)
        if f:
            offsets[pno - f] += 1
    A["offset"] = offsets.most_common(1)[0][0] if offsets else None
    fol_pages = [pno for pno, p in pages.items() if folio_of(p) and A["offset"] is not None and pno - folio_of(p) == A["offset"]]
    if fol_pages:
        A["folio_range"] = [min(fol_pages), max(fol_pages)]
    # printed page -> scan page: the page that carries that number, else the nearest numbered page's offset
    # (a scan with an inserted leaf shifts the offset part-way through the issue)
    fol_index = {}
    candidates = {}
    for pno, p in sorted(pages.items()):
        f = folio_of(p)
        if f and abs((pno - f) - (A["offset"] or 0)) <= 6:
            candidates.setdefault(f, []).append(pno)
    folios = {pn: folio_of(p) for pn, p in pages.items()}
    A["folio_conflicts"] = {}
    for printed, scans in candidates.items():
        if len(scans) == 1:
            fol_index[printed] = scans[0]
            continue
        # A single OCR digit can duplicate a later folio. Prefer the candidate
        # supported by the numbering on its neighbours, not the first scan.
        def support(pn):
            errors = [abs((folios[q] - printed) - (q - pn)) for q in range(pn - 3, pn + 4)
                      if q != pn and folios.get(q) is not None and folios[q] != printed]
            return (sum(e == 0 for e in errors), -sum(min(e, 10) for e in errors))
        scores = {pn: support(pn) for pn in scans}
        ranked = sorted(scans, key=lambda pn: scores[pn], reverse=True)
        chosen = ranked[0] if scores[ranked[0]][0] >= 2 and scores[ranked[0]] != scores[ranked[1]] else None
        if chosen is not None:
            fol_index[printed] = chosen
        # Unresolved duplicates are omitted; scan_of estimates from neighbouring
        # reliable folios instead of treating a conflicting OCR number as fact.
        A["folio_conflicts"][printed] = {"scans": scans, "chosen": chosen}
    if TF is not None and TF["folio"]["support"] >= max(5, len(fol_pages) if fol_pages else 0):
        # the fitted folio map: printed page -> scan page, the dominant offset and the numbered range
        F = TF["folio"]
        segs = sorted(F["segments"], key=lambda g: -g["support"])
        A["offset_v26"], A["folio_range_v26"] = A["offset"], A["folio_range"]
        A["offset"] = segs[0]["offset"] if segs else A["offset"]
        agree = [s for s, v in F["printed"].items() if v is not None and F["read"].get(s) == v]
        if agree:
            A["folio_range"] = [min(agree), max(agree)]
        fol_index = {int(k): v for k, v in TF["scan_index"].items()}
        A["folio_segments"] = F["segments"]
        A["folio_misplaced"] = F["misplaced"]
    A["folio_index"] = fol_index

    def scan_of(printed):
        if printed in fol_index:
            return fol_index[printed]
        near = sorted(fol_index, key=lambda f: abs(f - printed))
        if near:
            f = near[0]
            return fol_index[f] + (printed - f)
        return printed + A["offset"] if A["offset"] is not None else None
    A["scan_of"] = scan_of
    toc_by_scan = {}
    if A["offset"] is not None:
        for e in A["toc"]:
            sp = e.get("scan_page") or scan_of(e["page"])     # the page that shows the entry (2.7), else the number
            if sp is not None:
                toc_by_scan.setdefault(sp, []).append(e)
    for pno, p in pages.items():
        H = p["height"]
        regs = p["regions"]
        info = {"folio": folio_of(p), "running": [t for _, t in running_heads(p)], "furniture": [], "bylines": [],
                "starts": [], "chapters": [], "fillers": [], "ad_score": 0, "toc_entries": toc_by_scan.get(pno, [])}
        body_chars = 0
        for i, r in enumerate(regs):
            t = region_text(r)
            if is_furniture(p, i, r):
                info["furniture"].append(i)
                continue
            body_chars += len(t) if len(t) > 200 else 0
            info["ad_score"] += len(AD_WORDS.findall(t))
            a = byline_author(t)
            if a and re.fullmatch(r"(?:[A-Z]\.?\s*){1,3}", a) and i + 1 < len(regs) and (i + 1) not in info["furniture"]:
                nt = " ".join(region_text(regs[i + 1]).split())
                if 0 < words(nt) <= 3 and re.fullmatch(r"[A-Z][A-Za-z'\-]+(?: [A-Z][A-Za-z'\-]+){0,2}\.?", nt) and not chapter_head(nt):
                    info["bylines"].append({"idx": i, "author": (a.rstrip(".") + ". " + nt.strip(" .")).replace("..", "."), "name_idx": i + 1})
                    continue                              # "By J. A." with "THOMPSON" in the next box
            if a:
                info["bylines"].append({"idx": i, "author": a})
            elif re.fullmatch(r"(?i)(?:by|conducted by|edited by|compiled by|arranged by)", t.strip()) and i + 1 < len(regs):
                nt = region_text(regs[i + 1]).replace("\n", " ")
                if 0 < words(nt) <= 5 and nt[:1].isupper() and not chapter_head(nt):
                    info["bylines"].append({"idx": i, "author": nt.strip(" .,"), "name_idx": i + 1})
            elif r["label"] == "SectionHeader" and words(t) <= 24:
                combined = re.match(r"^(.+?)(?:\n+|,\s+)([Bb][Yy]\s+.+)$", t, re.S)
                ca = byline_author(combined.group(2)) if combined else None
                if ca and words(combined.group(1)) <= 14:
                    info["bylines"].append({"idx": i, "author": ca,
                                             "embedded_title": " ".join(combined.group(1).split())})
        info["body_chars"] = body_chars
        # text inside an illustration (Heejin's category of 2026-09-04): a page whose only content is a few
        # short lines, none of them prose, most of them not words ("LOVE MALES DATER ME & MY CLAN" over a
        # drawing); and any text box that lies inside a picture box
        content = [i for i in range(len(regs)) if i not in info["furniture"]]
        pics = [r["bbox"] for r in regs if r["label"] in ("Picture", "Figure")]
        illus = []
        titles_here = [e["title"] for e in info["toc_entries"]] + [e["title"] for e in toc_by_scan.get(pno + 1, [])]
        if content and not info["bylines"] and not any(prose_like(regs[i]) for i in content) \
                and sum(words(region_text(regs[i])) for i in content) < 40 \
                and all(words(region_text(regs[i])) <= 12 for i in content) and not any(FILLER_RE.search(region_text(regs[i])) for i in content) \
                and not any(sim(region_text(regs[i]), tt) > 0.6 or title_prefix(region_text(regs[i]), tt) for i in content for tt in titles_here):
            toks = [w for i in content for w in re.findall(r"[A-Za-z']+", region_text(regs[i]))]
            wordy = sum(1 for w in toks if len(w) >= 2 and looks_like_word(w))
            odd = sum(1 for w in toks if len(w) == 1 and w not in ("a", "A", "I"))
            if toks and (wordy / len(toks) < 0.6 or odd >= 2):
                illus = list(content)
        for i in content:
            if i in illus:
                continue
            b = regs[i]["bbox"]
            area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
            for pb in pics:
                inter = max(0, min(b[2], pb[2]) - max(b[0], pb[0])) * max(0, min(b[3], pb[3]) - max(b[1], pb[1]))
                if inter / area > 0.8 and words(region_text(regs[i])) <= 12:
                    illus.append(i)
                    break
        for i in illus:
            info["furniture"].append(i)
        info["illustration_text"] = illus
        info["cont_from"] = []          # (idx, printed page) — the text after it continues an earlier piece
        info["cont_on"] = []            # idx — the piece pauses here
        for i, r in enumerate(regs):
            t = region_text(r)
            # OCR can combine the printed continuation notice with its first
            # paragraph. Route that box without making its prose furniture.
            first_line, sep, remainder = t.partition("\n")
            inline_from = CONT_FROM_RE.search(first_line)
            if (sep and inline_from and not re.search(r"\w", first_line[:inline_from.start()])
                    and not re.search(r"\w", first_line[inline_from.end():])
                    and words(remainder) >= 3):
                info["cont_from"].append((i, int(inline_from.group(1))))
                if i in info["furniture"]:
                    info["furniture"].remove(i)
                continue
            if words(t) > 12:
                continue
            m = CONT_FROM_RE.search(t)
            if m:
                info["cont_from"].append((i, int(m.group(1))))
                if i not in info["furniture"]:
                    info["furniture"].append(i)
            elif CONT_ON_RE.search(t):
                info["cont_on"].append(i)
                if i not in info["furniture"]:
                    info["furniture"].append(i)
        # starts: a by-line with its title — the section-header lines right above it
        for b in info["bylines"]:
            bi = b["idx"]
            source_credits = continued_source_credit(pages, pno, b)
            if source_credits:
                info.setdefault("source_credits", []).extend(source_credits)
                continue
            by0 = regs[bi]["bbox"][1]
            lines, subs, teaser_between = ([bi] if b.get("embedded_title") else []), [], []
            j = -1 if b.get("embedded_title") else bi - 1
            while j >= 0:
                r = regs[j]
                if j in info["furniture"]:
                    j -= 1
                    continue
                t = region_text(r)
                if by0 - r["bbox"][3] > TITLE_GAP:
                    break
                if words(t) <= 12 and FILLER_RE.search(t):
                    break                                  # "NEXT MONTH" above a title: the announcement's own header, not the title
                if r["label"] == "SectionHeader" and words(t) <= 14 and not chapter_head(t):
                    lines.insert(0, j)
                elif r["label"] == "Caption" and not lines:
                    pass                                   # the illustration's caption sits between title and by-line
                elif not lines and len(subs) < 3 and words(t) <= 8 and (not chapter_head(t) or PART_MARK_RE.fullmatch(t)):
                    subs.insert(0, j)                     # a subtitle line between the title and the by-line ("PART II." too)
                elif not lines and not teaser_between and 8 < words(t) <= 80 and r["label"] == "Text":
                    teaser_between.append(j)              # the blurb printed between the title and the by-line
                else:
                    break
                j -= 1
            if not lines:
                # no header above: a display line right below the by-line (some layouts), else the contents page
                for j in range(bi + 1, min(bi + 4, len(regs))):
                    if j == b.get("name_idx"):
                        continue
                    r = regs[j]
                    if j not in info["furniture"] and is_display(p, r) and r["label"] == "SectionHeader" and not chapter_head(region_text(r)):
                        lines = [j]
                        break
            toc_hit = None
            page_title_guess = b.get("embedded_title") or " ".join(region_text(regs[j]).replace("\n", " ") for j in lines)
            hits = [e for e in info["toc_entries"] if e["author"] and sim(e["author"], b["author"]) > 0.6]
            if hits:
                # two entries by one author on one page (a story and a poem): the one whose title the page prints
                toc_hit = max(hits, key=lambda e: (sim(page_title_guess, e["title"]) if page_title_guess else 0, sim(e["author"], b["author"])))
            if not toc_hit and info["toc_entries"] and not lines:
                toc_hit = max(info["toc_entries"], key=lambda e: sim(e["author"] or "", b["author"]))
            if not lines and not toc_hit:
                toc_hit = nearby_serial_contents(pages, pno, b, A["toc"], A["offset"])
            if not lines and toc_hit:
                # the title far above the by-line (a drawing between them: "Serenade WITH" a page-height above
                # "a novelet by …"): the display line the contents page names, anywhere above on the page
                for j in range(bi - 1, -1, -1):
                    r = regs[j]
                    tj = " ".join(region_text(r).split())
                    if j in info["furniture"] or r["label"] != "SectionHeader" or not tj or words(tj) > 14:
                        continue
                    if sim(tj, toc_hit["title"]) > 0.6 or title_prefix(tj, toc_hit["title"]):
                        lines = [j]
                        break
            facing_title = None
            if not lines and not toc_hit and pno - 1 in pages:
                prev = pages[pno - 1]
                # A title-only facing page plus an explicit byline and body is
                # independent start evidence. A previous story/ad page is not.
                prev_text = [(j, rr) for j, rr in enumerate(prev["regions"])
                             if region_text(rr) and not is_furniture(prev, j, rr)]
                following = " ".join(region_text(rr) for rr in regs[bi + 1:])
                facing_toc = toc_by_scan.get(pno - 1, [])
                facing_match = [e for e in facing_toc if len(prev_text) == 1
                                and sim(region_text(prev_text[0][1]), e["title"]) > .8
                                and (not e.get("author") or sim(e["author"], b["author"]) > .8)]
                if (len(prev_text) == 1 and prev_text[0][1]["label"] == "SectionHeader"
                        and is_display(prev, prev_text[0][1])
                        and 2 <= words(region_text(prev_text[0][1])) <= 14
                        and not chapter_head(region_text(prev_text[0][1]))
                        and not (AD_WORDS.search(region_text(prev_text[0][1]))
                                 or FILLER_RE.search(region_text(prev_text[0][1])))
                        and len(following) >= 500 and any(prose_like(rr) for rr in regs[bi + 1:])
                        and (not facing_toc or len(facing_match) == 1)
                        and not any(AD_WORDS.search(region_text(rr)) or FILLER_RE.search(region_text(rr))
                                    for rr in regs[:bi])):
                    facing_title = {"page": pno - 1, "idx": prev_text[0][0],
                                    "text": " ".join(region_text(prev_text[0][1]).split())}
                    if facing_match:
                        toc_hit = facing_match[0]
            if not lines and not toc_hit and not facing_title:
                continue
            # the title is the tallest line; other lines join it when the contents page agrees, else they are subtitles
            title_idx, sub_idx = [], list(subs)
            if lines:
                hts = {j: regs[j]["bbox"][3] - regs[j]["bbox"][1] for j in lines}
                main = max(lines, key=lambda j: hts[j])
                title_idx = [main]
                for j in lines:
                    if j == main:
                        continue
                    joined = " ".join(region_text(regs[x]).replace("\n", " ") for x in sorted(title_idx + [j]))
                    alone = " ".join(region_text(regs[x]).replace("\n", " ") for x in sorted(title_idx))
                    if toc_hit and sim(joined, toc_hit["title"]) > sim(alone, toc_hit["title"]):
                        title_idx.append(j)
                    elif not toc_hit and hts[j] >= 0.6 * hts[main]:
                        title_idx.append(j)
                    else:
                        sub_idx.append(j)
                title_idx.sort()
            title = (facing_title or {}).get("text") or b.get("embedded_title") or re.sub(r"\s+", " ", " ".join(region_text(regs[j]).replace("\n", " ") for j in title_idx)).strip()
            info["starts"].append({"title_idx": title_idx, "subtitle_idx": sorted(sub_idx), "byline_idx": bi, "title": title,
                                   "author": b["author"], "toc": toc_hit, "teaser_idx": (teaser_between if lines else []),
                                   "name_idx": b.get("name_idx"), "facing_title": facing_title,
                                   "embedded_title": bool(b.get("embedded_title"))})
        # chapter heads and fillers
        head_idx = {j for s_ in info["starts"] for j in s_["title_idx"] + s_["subtitle_idx"]}
        for i, r in enumerate(regs):
            if i in info["furniture"] or i in head_idx:
                continue
            t = region_text(r)
            if chapter_head(t):
                info["chapters"].append(i)
                # the chapter's own title on the next short line
                if i + 1 < len(regs) and (i + 1) not in info["furniture"] and chapter_title_like(regs[i + 1], r):
                    info["chapters"].append(i + 1)
            elif (is_display(p, r) and r["label"] == "SectionHeader" and not any(i in s["title_idx"] for s in info["starts"])) or \
                    (words(t) <= 6 and FILLER_RE.search(t) and not any(i in s["title_idx"] + s["subtitle_idx"] for s in info["starts"])):
                info["fillers"].append(i)
        # ballots and coupons: a form block is its own record (house_form), never text of the piece around it
        info["forms"] = {}
        taken = set()
        for i, r in enumerate(regs):
            if i in info["furniture"] or i in taken or any(i in s["title_idx"] + s["subtitle_idx"] for s in info["starts"]):
                continue
            blk = form_block(p, i, info)
            if blk:
                info["forms"][i] = blk
                taken.update(blk)
        A["pages"][pno] = info
    # nothing starts on the contents page or before it (the cover strip and the index list pieces, they do not begin them)
    if A["toc_page"]:
        for pno, info in A["pages"].items():
            if pno <= A["toc_page"] and info["starts"]:
                for st in info["starts"]:
                    st["rejected"] = "on or before the contents page"
                info.setdefault("rejected_starts", []).extend(info["starts"])
                info["starts"] = []
    # the magazine's own running head: the most common one
    heads = Counter(h for info in A["pages"].values() for h in info["running"] if words(h) <= 8)
    A["magazine_head"] = heads.most_common(1)[0][0] if heads else ""
    vocab = Counter()
    for p in pages.values():
        for r in p["regions"]:
            for w in re.findall(r"[A-Za-z']{2,}", region_text(r)):
                vocab[w.lower().strip("'")] += 1
    A["vocab"] = vocab
    # announcements are not starts: a title and by-line with almost no text after them
    # ("in the next issue", book reviews, the cover strip)
    order = sorted(pages)
    for n, pno in enumerate(order):
        info = A["pages"][pno]
        if not info["starts"]:
            continue
        regs = pages[pno]["regions"]
        keep = []
        starts = sorted(info["starts"], key=lambda s: s["byline_idx"])
        for k, s in enumerate(starts):
            if self_declared_commercial_head(pages[pno], s):
                s["rejected"] = "product offer explicitly identifies the page as an advertisement"
                info.setdefault("rejected_starts", []).append(s)
                continue
            if (s.get("embedded_title") and not s.get("toc")
                    and re.match(r"^(?:in\s+(?:the\s+)?)?next\s+(?:issue|month|week)(?:['\u2019]s)?\s+"
                                 r"(?:novel|story|novelette|serial|feature|poem)\s*:",
                                 s.get("title") or "", re.I)):
                s["rejected"] = "future-issue title and byline inside a filler block (an announcement, not a start)"
                info.setdefault("rejected_starts", []).append(s)
                continue
            if signed_product_endorsement(pages[pno], s):
                s["rejected"] = "signed product endorsement with the same product's free offer and coupon"
                info.setdefault("rejected_starts", []).append(s)
                continue
            first = start_first(s)
            stop = start_first(starts[k + 1]) if k + 1 < len(starts) else len(regs)
            following = sum(len(region_text(regs[j])) for j in range(s["byline_idx"] + 1, stop) if j not in info["furniture"])
            # inside a house announcement the text that belongs to the by-line ends at the next notice
            # ("in the June issue of"), display header or coupon; what follows is the department's own text
            stop_f = next((j for j in range(s["byline_idx"] + 1, stop) if j not in info["furniture"] and
                           ((words(region_text(regs[j])) <= 12 and FILLER_RE.search(region_text(regs[j])))
                            or (is_display(pages[pno], regs[j]) and regs[j]["label"] == "SectionHeader")
                            or j in (info.get("forms") or {}))), stop)
            following_f = sum(len(region_text(regs[j])) for j in range(s["byline_idx"] + 1, stop_f) if j not in info["furniture"])
            if following < 800 and k + 1 == len(starts) and n + 1 < len(order):
                nxt = A["pages"][order[n + 1]]
                nregs = pages[order[n + 1]]["regions"]
                content = [j for j in range(len(nregs)) if j not in nxt["furniture"]]
                near_top = set(content[:3])
                starts_next = any(start_first(t) in near_top for t in nxt["starts"])
                if not starts_next and nxt["ad_score"] < 3:
                    following += sum(len(region_text(nregs[j])) for j in content)
            in_filler = any(j < first and words(region_text(regs[j])) <= 12
                            and FILLER_RE.search(region_text(regs[j])) for j in range(len(regs)))
            unlisted = len(A["toc"]) >= 3 and not s.get("toc")
            chaptered = any(c > s["byline_idx"] for c in info["chapters"])          # "CHAPTER I" right after the by-line
            # inside a department (the running head names a letters or feature piece of the contents page)
            in_department = unlisted and any(sim(h, e["title"]) > 0.6 for h in info["running"]
                                             for e in A["toc"] if e.get("type") in ("letters", "feature"))
            # a listing: several by-lines on the page, none opening a chapter, all with little text
            listing = (len(starts) >= 2 and not info["chapters"] and unlisted
                       and all(sum(len(region_text(regs[j])) for j in range(x["byline_idx"] + 1, (start_first(starts[m + 1]) if m + 1 < len(starts) else len(regs)))
                                   if j not in info["furniture"]) < 800 for m, x in enumerate(starts)))
            # a book or magazine announcement with a by-line (Galaxy's "The Current GALAXY Science Fiction
            # Novel … By Olaf Stapledon … 35c a copy"): selling words follow on the same page
            page_after = " ".join(region_text(regs[j]) for j in range(s["byline_idx"] + 1, len(regs)) if j not in info["furniture"])
            selling = unlisted and following < 2500 and bool(SALE_RE.search(page_after))
            if ((following < 250 or ((in_filler or unlisted) and following < 800) or (in_filler and following_f < 1500)
                 or (in_department and following < 1500) or listing or selling) and not (chaptered and following >= 250)):
                s["rejected"] = (f"only {following} characters follow the by-line" + (" inside a filler block" if in_filler else "")
                                 + (" with selling words (a book or magazine announcement)" if selling else "")
                                 + " (an announcement, not a start)")
                info.setdefault("rejected_starts", []).append(s)
            else:
                keep.append(s)
        info["starts"] = keep
    # pieces the contents page lists but no by-line announced (title and by-line drawn as
    # lettering the OCR could not read, or a department with no author): the start is
    # placed on the page the contents page gives, at the title if one is readable, else
    # at the first paragraph (the one that opens with a word in capitals)
    if A["offset"] is not None:
        # A contents parser can read only "by Author" when the title is drawn
        # separately. Bind that entry to a unique actual title+byline instead
        # of inventing another work deep inside a story at a misread folio.
        for e in A["toc"]:
            author_only = byline_author(e["title"]) if not e.get("author") else None
            if not author_only:
                continue
            named = [(pno, s) for pno, info in A["pages"].items() for s in info["starts"]
                     if s.get("byline_idx") is not None and s.get("title_idx") and s.get("title")
                     and not s.get("toc") and norm(s.get("author") or "") == norm(author_only)]
            if len(named) == 1:
                pno, start = named[0]
                e["scan_linked"] = pno
                e["author_only_printed_match"] = {"title": start["title"], "author": start["author"]}
                # The missing contents title also makes its section/serial
                # attribution unreliable. Keep the independently printed
                # start's identity and type; only suppress the phantom start.
        claimed = {norm(e["title"]) for e in A["toc"] if e.get("author_only_printed_match")}
        for info in A["pages"].values():
            for s in info["starts"]:
                if s.get("toc"):
                    claimed.add(norm(s["toc"]["title"]))
        # Entries with a garbled page number can match a printed start by name.
        # Rank all pairs first: a cover credit sharing an author must not claim
        # that author's story before its exact title is considered.
        name_pairs = []
        for e in A["toc"]:
            if norm(e["title"]) in claimed:
                continue
            for pno, info in A["pages"].items():
                for s in info["starts"]:
                    if s.get("toc"):
                        continue
                    title_score = sim(s["title"], e["title"])
                    # Shared articles/letter shapes alone are not title
                    # evidence (THE CHRONICLER versus THE ATOMIC PILE).
                    left = {w for w in norm_words(s["title"]).split() if len(w) > 3 and w not in STOP_WORDS}
                    right = {w for w in norm_words(e["title"]).split() if len(w) > 3 and w not in STOP_WORDS}
                    title_match = title_score > 0.6 and (norm(s["title"]) == norm(e["title"]) or
                                                        any(sim(a, b) >= 0.85 for a in left for b in right))
                    author_score = sim(s["author"] or "", e["author"] or "") if e["author"] else 0
                    if title_match or author_score > 0.6:
                        name_pairs.append(((bool(title_match), title_score, author_score), e, s, pno))
        for _, e, s, pno in sorted(name_pairs, key=lambda pair: pair[0], reverse=True):
            if norm(e["title"]) in claimed or s.get("toc"):
                continue
            s["toc"] = e
            s["toc_linked_by_name"] = True
            e["scan_linked"] = pno
            claimed.add(norm(e["title"]))
        lo_hi = A["folio_range"] or [min(pages), max(pages)]
        for e in A["toc"]:
            if norm(e["title"]) in claimed:
                continue
            P = A["scan_of"](e["page"])
            if P is None or P not in pages or (A["toc_page"] and P <= A["toc_page"]) or P > lo_hi[1] + 4:
                continue                                          # a page number the OCR garbled
            cands = [P, P - 1, P + 1]
            placed = None
            for q in cands:
                if q not in pages:
                    continue
                info = A["pages"][q]
                regs = pages[q]["regions"]
                # a readable display title that matches
                for j, r in enumerate(regs):
                    # Small factual pieces often have an ordinary-size heading
                    # half way down the page, after the preceding story's end.
                    # Falling back to the first paragraph would steal that end.
                    small_title = (r["label"] == "SectionHeader" and words(region_text(r)) <= 14
                                   and norm_words(region_text(r)) == norm_words(e["title"]))
                    if j in info["furniture"] or not (is_display(pages[q], r) or small_title):
                        continue
                    if sim(region_text(r), e["title"]) > 0.6 or title_prefix(region_text(r), e["title"]):
                        placed = (q, [j], None)
                        break
                if placed:
                    break
                # a title drawn in pieces ("GALAXY'S", "STAR", "SHELF"): the display fragments near the top, joined
                frags = [j for j, r in enumerate(regs) if j not in info["furniture"] and is_display(pages[q], r) and words(region_text(r)) <= 3]
                frags = [j for j in frags if j <= (frags[0] + 5)] if frags else []
                if len(frags) >= 2 and sim(" ".join(region_text(regs[j]) for j in frags), e["title"]) > 0.6:
                    placed = (q, frags, None)
                    break
            if not placed and not e.get("author") and e.get("type") in ("feature", "letters"):
                # A department's page number can also be garbled. Its exact
                # printed heading plus matching running head is stronger than
                # a guessed paragraph boundary at that number.
                exact = [(q, j) for q, page in pages.items()
                         if not A["toc_page"] or q > A["toc_page"]
                         for j, r in enumerate(page["regions"])
                         if r["label"] == "SectionHeader" and norm(region_text(r)) == norm(e["title"])
                         and any(norm(t) == norm(e["title"]) for n in range(q, q + 3)
                                 if n in pages for _, t in running_heads(pages[n]))]
                if len(exact) == 1:
                    q, j = exact[0]
                    placed = (q, [j], None)
                    e["scan_linked"] = q
            if not placed and P in pages:
                info = A["pages"][P]
                regs = pages[P]["regions"]
                if any(sim(e["author"] or "", s["author"]) > 0.6 for s in info["starts"]):
                    continue                                  # a by-line start already covers it
                at = None
                for j, r in enumerate(regs):
                    if j in info["furniture"] or r["label"] == "Caption":
                        continue
                    t = region_text(r)
                    fw = t.split()[0] if t.split() else ""
                    if words(t) >= 8 and len(re.sub(r"[^A-Za-z]", "", fw)) >= 2 and fw.upper() == fw:
                        at = j                                # "MR. BESSEL was…": a first paragraph
                        break
                if at is None:
                    at = next((j for j, r in enumerate(regs) if j not in info["furniture"] and r["label"] != "Caption"), None)
                if at is not None:
                    placed = (P, [], at)
            if not placed:
                continue
            q, title_idx, at = placed
            info = A["pages"][q]
            st = {"title_idx": title_idx, "subtitle_idx": [], "byline_idx": None, "at": at, "title": e["title"],
                  "author": e["author"], "toc": e, "from_contents": True}
            info["starts"].append(st)
            claimed.add(norm(e["title"]))
    demote_embedded_documents(pages, A)
    for info in A["pages"].values():
        info["starts"].sort(key=start_first)
    # the range of pages that carry the magazine's own matter: the printed page numbers, widened by the
    # first and last piece the contents page or a by-line places (Western Story numbers its first story
    # pages without a readable folio)
    lo, hi = A["folio_range"] or [min(pages), max(pages)]
    start_pages = [pno for pno, info in A["pages"].items() if info["starts"]]
    toc_pages = [A["scan_of"](e["page"]) for e in A["toc"] if A["offset"] is not None and A["scan_of"](e["page"]) in pages]
    lo = min([lo] + start_pages + toc_pages)
    hi = max([hi] + start_pages + toc_pages)
    # a leaf the scan has out of order at the end of the numbered pages (Galaxy 1952-03 prints page 156
    # after 158): a page just past the range whose printed number belongs a few pages before its end
    A["misplaced"] = []
    if A["folio_range"] and A["offset"] is not None:
        f_hi = A["folio_range"][1] - A["offset"]
        for pno in range(hi + 1, min(hi + 4, max(pages) + 1)):
            f = folio_of(pages[pno]) if pno in pages else None
            if f is not None and f_hi - 4 <= f <= f_hi + 1 and pno - f != A["offset"]:
                A["misplaced"].append((pno, f))
                hi = pno
            else:
                break
    A["content_range"] = [lo, hi]
    return A


# ---------------------------------------------------------------- assembly

def clean_text(text):
    try:
        from s07_articles import clean_text as ct
        return ct(text)
    except Exception:
        return text


def join_boxes(texts):
    try:
        from s07_articles import join_boxes as jb
        return jb(texts)
    except Exception:
        return "\n".join(texts)


def looks_like_word(w):
    try:
        from s04_rules import looks_like_word as llw
        return llw(w)
    except Exception:
        w = w.lower()
        return bool(re.fullmatch(r"[a-z]{2,20}", w)) and bool(re.search(r"[aeiouy]", w))


class Assembler:
    def __init__(self, iid, pages, A):
        self.iid, self.pages, self.A = iid, pages, A
        self.records = []
        self.furniture = []
        self.owner = {}
        self.open = None            # the open record (a dict)
        self.last_text = ""         # the last text added to the open piece (continuation test)
        self.suspended = None       # the piece a full advertisement page interrupted
        self.continuations = {}     # target scan page -> record named by a forward notice
        self.n = 0

    def new(self, typ, title=None, author=None, page=None, toc=None):
        if typ == "ad" and title and words(title) > 12:
            title = " ".join(title.split()[:12]) + " …"          # an advertisement titled by a paragraph: its first words
        self.n += 1
        rec = {"article_id": f"{self.iid}_a{self.n:03d}", "type": typ, "title": title, "author": author,
               "pages": [], "fragments": [], "roles": {}, "flags": [], "toc": toc, "keys": []}
        self.records.append(rec)
        return rec

    def add(self, rec, pno, i, role=None):
        k = key(pno, i)
        if k in self.owner:
            return
        self.owner[k] = rec["article_id"]
        rec["keys"].append((pno, i))
        if role:
            rec["roles"][k] = role
        elif rec is self.open:
            self.last_text = region_text(self.pages[pno]["regions"][i])

    def furn(self, pno, i):
        k = key(pno, i)
        if k not in self.owner:
            self.owner[k] = "furniture"
            self.furniture.append({"page": pno, "idx": i})

    def run(self):
        A, pages = self.A, self.pages
        for info in A["pages"].values():          # the analysis is shared between variants
            for s in info["starts"]:
                s.pop("_rec", None)
        lo, hi = A.get("content_range") or A["folio_range"] or [min(pages), max(pages)]
        toc_page = A["toc_page"]
        for pno in sorted(pages):
            p = pages[pno]
            info = A["pages"][pno]
            regs = p["regions"]
            for i in info["furniture"]:
                self.furn(pno, i)
                if i in (info.get("illustration_text") or []):
                    self.furniture[-1]["why"] = "text inside an illustration"
            if pno == toc_page or (toc_page and pno == toc_page + 1 and info["toc_entries"] == [] and
                                    sum(1 for r in regs if r["label"] == "TableOfContents")):
                rec = self.new("toc", title="Contents", page=pno)
                for i in range(len(regs)):
                    if i not in info["furniture"]:
                        self.add(rec, pno, i)
                self.open = None
                continue
            inside = lo <= pno <= hi
            if not inside or self.is_ad_page(pno, p, info):
                if self.open is not None and self.open.get("type") in ("story", "letters", "feature") and inside:
                    self.suspended = self.open            # a full advertisement page inside a piece
                self.open = None
                self.ad_page(pno, p, info)
                continue
            self.story_page(pno, p, info)
        # the second look at every record (v2.2): the piece's ends, chapter series, synopsis, departments
        for rec in list(self.records):
            if rec["keys"]:
                self.finish(rec)
        for rec in list(self.records):
            if rec["keys"] and rec["type"] in ("story", "poem"):
                self.trailing_display_block(rec)
            if rec["keys"] and rec["type"] in ("story", "poem", "feature", "letters"):
                self.empty_title_box(rec)
        self.merge_page_ads()
        # texts, pages
        misplaced = dict(A.get("misplaced") or [])
        for rec in self.records:
            mism = rec.pop("_head_mismatch", None)
            if mism:
                rec["flags"].append(f"running head does not name this piece on pages {mism[:8]}")
            rec["keys"].sort()
            rec["pages"] = sorted({pn for pn, _ in rec["keys"]})
            held = [pn for pn in rec["pages"] if pn in misplaced]
            if held:
                # the record's pages in printed order (Sujin's note of 2026-09-04: "the page footer
                # information should be used to verify and correct the page sequence")
                folio_by_scan = {v: k for k, v in (A.get("folio_index") or {}).items()}
                folio_by_scan.update(misplaced)

                def printed(pn):
                    return folio_by_scan.get(pn, pn - (A["offset"] or 0))
                rec["keys"].sort(key=lambda x: (printed(x[0]), x[1]))
                rec["flags"].append("the scan has a leaf out of order: scan page " + ", ".join(f"{pn} carries printed page {misplaced[pn]}" for pn in held)
                                    + " — the text of this record follows the printed page numbers, not the scan order")
            if rec["type"] in ("story", "feature", "letters", "poem"):
                ordered = order_region_keys(pages, rec["keys"])
                if ordered != rec["keys"]:
                    rec["flags"].append("reading order follows column geometry (region identities preserved)")
                rec["keys"] = ordered
            frags = {}
            for pn, i in rec["keys"]:
                frags.setdefault(pn, []).append(i)
            page_order = [pn for pn, _ in rec["keys"]]
            page_order = list(dict.fromkeys(page_order))
            rec["fragments"] = [{"page": pn, "region_ids": frags[pn]} for pn in page_order]
            body = []
            syn = []
            for pn, i in rec["keys"]:
                role = rec["roles"].get(key(pn, i))
                if role in ("title", "subtitle", "author", "teaser", "caption", "note"):
                    continue
                if role == "synopsis":
                    syn.append(region_text(pages[pn]["regions"][i]))
                    continue
                body.append(region_text(pages[pn]["regions"][i]))
            rec["text"] = clean_text(join_boxes(body))       # one paragraph per box; a split paragraph rejoined (v2.3)
            if syn:
                rec["synopsis"] = clean_text(join_boxes(syn))
            rec["n_regions"] = len(rec["keys"])
            chs = self.chapters_of(rec) if rec["type"] not in ("ad", "house", "toc", "other") else []
            if chs:
                rec["chapters"] = chs
            if rec["type"] in ("ad", "house"):
                was_house = rec["type"] == "house"
                self.classify_ad(rec)
                if rec.get("ad_class") == "cover":
                    rec["type"] = "other"                     # the cover (Sujin Kang, 17-21 October 2026: a cover is `other`, not house)
                    rec["department"] = "cover"
                elif (rec.get("ad_class") or "").startswith("house"):
                    rec["type"] = "house"                     # the publisher's own matter (decision of 2026-09-04)
                elif was_house:
                    rec["ad_class"] = "house_next_issue"      # named as a department of the magazine: house by the list
                if rec["type"] == "house" and rec.get("title") and rec["title"].upper() == rec["title"]:
                    rec["title_as_printed"] = rec.get("title_as_printed") or rec["title"]
                    rec["title"] = title_case(rec["title"])
            del rec["keys"]
            rec.pop("_column_ad", None)
        self.records = [r for r in self.records if r["n_regions"]]
        # the cover is one record (rules 2.12): the masthead, the cover lines and the price, read as several blocks,
        # are joined under the magazine's name
        covers = [r for r in self.records if r.get("ad_class") == "cover"]
        if len(covers) > 1:
            head, rest = covers[0], covers[1:]
            for r in rest:
                for fr in r["fragments"]:
                    mine = next((f for f in head["fragments"] if f["page"] == fr["page"]), None)
                    if mine:
                        mine["region_ids"] = sorted(set(mine["region_ids"]) | set(fr["region_ids"]))
                    else:
                        head["fragments"].append(fr)
                head["roles"].update(r.get("roles") or {})
                head["text"] = (head.get("text") or "") + ("\n" if head.get("text") and r.get("text") else "") + (r.get("text") or "")
                head["n_regions"] += r["n_regions"]
            head["fragments"].sort(key=lambda f: f["page"])
            head["flags"].append(f"the cover's {len(covers)} blocks joined")
            own = self.magazine_name()
            if own:
                head["title_as_printed"] = head.get("title_as_printed") or head.get("title")
                head["title"] = own
            self.records = [r for r in self.records if r is head or r.get("ad_class") != "cover"]
        return self

    def demote(self, rec, pn, i, why):
        """Take a region out of a record and make it page furniture."""
        k = key(pn, i)
        rec["keys"] = [x for x in rec["keys"] if x != (pn, i)]
        rec["roles"].pop(k, None)
        self.owner[k] = "furniture"
        self.furniture.append({"page": pn, "idx": i, "why": why})

    def steal(self, rec, pn, i, role=None):
        """Move a region into rec from whatever holds it (another record, the
        furniture, nobody); the region keeps no old role."""
        k = key(pn, i)
        own = self.owner.get(k)
        if own == rec["article_id"]:
            if role:
                rec["roles"][k] = role
            return
        if own == "furniture":
            self.furniture = [f for f in self.furniture if not (f["page"] == pn and f.get("idx") == i)]
            del self.owner[k]
        elif own:
            other = next((r_ for r_ in self.records if r_["article_id"] == own), None)
            if other is not None:
                other["keys"] = [x for x in other["keys"] if x != (pn, i)]
                other["roles"].pop(k, None)
            del self.owner[k]
        self.add(rec, pn, i, role)

    def take(self, rec, pn, i, role=None):
        """Give a region to a record even when the page analysis made it
        furniture (a title box the layout split off)."""
        k = key(pn, i)
        if self.owner.get(k) == rec["article_id"]:
            if role:
                rec["roles"][k] = role
            return
        if self.owner.get(k) == "furniture":
            self.furniture = [f for f in self.furniture if not (f["page"] == pn and f.get("idx") == i)]
            del self.owner[k]
        self.add(rec, pn, i, role)

    def finish(self, rec):
        """The second look at a record once the whole issue is read."""
        pages = self.pages
        typ = rec["type"]
        if typ in ("toc",):
            return
        rec["keys"].sort()
        keys = rec["keys"]
        first_pn = keys[0][0]
        # 1. the head continued on the next page: the last words of the title in a box of their own
        #    ("DEAD MEN" on p. 6, "TALK" on p. 7) and a type label beside them ("A Complete Book-Length Novel")
        if typ in ("story", "feature", "poem") and rec.get("title"):
            full = (rec.get("toc") or {}).get("title") or rec["title"]
            printed = " ".join(region_text(pages[pn]["regions"][i]) for pn, i in keys if rec["roles"].get(key(pn, i)) == "title")
            if printed and norm(printed) != norm(full) and title_suffix(printed, full) and first_pn - 1 in pages:
                # the head began on the facing page: its first words in a box of their own ("THE YEAR" /
                # "OF THE JACKPOT"; "the" / "7th ORDER"), the illustrator's credit, and sometimes the
                # story's first paragraphs under them (The Seventh Order opens before its title page)
                prev = first_pn - 1
                regs = pages[prev]["regions"]
                at = next((j for j, r in enumerate(regs)
                           if title_prefix(" ".join(region_text(r).split()), full)
                           and norm_words(" ".join(region_text(r).split()) + " " + printed) == norm_words(full)), None)
                if at is not None:
                    self.steal(rec, prev, at, "title")
                    body_started = False
                    for j in range(at + 1, len(regs)):
                        r = regs[j]
                        t = " ".join(region_text(r).split())
                        k = key(prev, j)
                        if self.owner.get(k) == "furniture" and not (ILLUS_RE.match(t) or r["label"] == "Caption"):
                            continue
                        fw = t.split()[0] if t.split() else ""
                        dropcap = len(re.sub(r"[^A-Za-z]", "", fw)) >= 2 and fw.upper() == fw and words(t) >= 8
                        if ILLUS_RE.match(t) and words(t) <= 30:
                            self.steal(rec, prev, j, None)
                            self.illustrator(rec, prev, j)
                        elif CREDIT_RE.match(t) and words(t) <= 30:
                            self.steal(rec, prev, j, None)
                            self.credit(rec, prev, j)
                        elif body_started or dropcap or (r["label"] == "Text" and words(t) >= 60):
                            if r["label"] == "Text" and words(t) >= 8 and not AD_WORDS.search(t):
                                self.steal(rec, prev, j, None)    # the story's first paragraphs on the facing page
                                body_started = True
                        elif (r["label"] in ("Text", "Caption") and 3 <= words(t) <= 80) or t[:1] in "\"\u201c'\u2018":
                            self.steal(rec, prev, j, "teaser")    # the blurb, box by box, and the pull-quote
                            if not rec.get("teaser"):
                                rec["teaser"] = t
                        elif r["label"] == "SectionHeader" and words(t) <= 12 and not chapter_head(t):
                            self.steal(rec, prev, j, "subtitle")
                    rec["flags"].append(f"the head begins on p.{prev}: title words and text there joined")
                    keys = rec["keys"]
                    first_pn = prev
            if printed and norm(printed) != norm(full) and title_prefix(printed, full):
                nxt = first_pn + 1
                if nxt in pages:
                    regs = pages[nxt]["regions"]
                    for j, r in enumerate(regs[:4]):
                        t = " ".join(region_text(r).split())
                        k = key(nxt, j)
                        if self.owner.get(k) not in (None, "furniture", rec["article_id"]):
                            continue
                        if title_suffix(t, full) or norm_words(printed + " " + t) == norm_words(full):
                            self.take(rec, nxt, j, "title")
                        elif words(t) <= 8 and TYPE_LABEL_RE.match(t):
                            self.take(rec, nxt, j, "subtitle")
                            rec["subtitle"] = (rec.get("subtitle") + " " + t if rec.get("subtitle") else t)
        # 1b. the instalment marker printed above the head ("Conclusion", "Part Two") — often read as a running
        #     head by the layout: the subtitle, and the serial's part label when it has none
        if typ == "story":
            regs0 = pages[first_pn]["regions"]
            top = min((i for pn, i in keys if pn == first_pn), default=None)
            if top is not None:
                for j in range(0, top):
                    t = " ".join(region_text(regs0[j]).split())
                    if 1 <= words(t) <= 4 and re.fullmatch(r"(?i)(?:conclusion|concluded|beginning|part\s+\w+\.?|(?:first|second|third|fourth|last|final) (?:instal?lment|part))", t) \
                            and self.owner.get(key(first_pn, j)) in (None, "furniture"):
                        self.steal(rec, first_pn, j, "subtitle")
                        rec["subtitle"] = (t if not rec.get("subtitle") else rec["subtitle"] + " " + t)
                        ser = rec.get("serial") or {"part_label": None, "part_n": None, "part_total": None, "source": "head"}
                        if not ser.get("part_label"):
                            _w, lab, n, tot = split_part("X (" + t + ")")
                            ser["part_label"] = lab or t
                            ser["part_n"] = n or (1 if t.lower().startswith("beginning") else ser.get("part_n"))
                            ser["part_total"] = tot or ser.get("part_total")
                        rec["serial"] = ser
                        rec["work_title"] = rec.get("work_title") or rec.get("title")
                        break
        rec["keys"].sort()
        keys = rec["keys"]
        # 1c. the head zone (v2.3): everything between the head and the first body paragraph, across a spread,
        #     is paratext — the blurb box by box, the pull-quote, the epigraph with its attribution, the type
        #     label, a department's tagline and its standing notice, the illustrator's signature
        if typ in ("story", "poem", "feature", "letters"):
            self.head_zone(rec)
        if typ in ("story", "feature", "letters"):
            self.foot_blurb(rec)
        # 2a. the end mark or the author's signature with boxes of the same page after it (v2.3): what follows the
        #     end of a piece on its last page is advertising or an announcement, never the piece's text
        if typ in ("story", "poem") and len(keys) >= 6:
            last_pn = keys[-1][0]
            on_last = [(pn, i) for pn, i in keys if pn == last_pn]
            end_at = None
            for pn, i in on_last:
                t = region_text(pages[pn]["regions"][i]).strip()
                m = END_SIG_RE.match(t)
                if THE_END_RE.match(t) or (m and words(t) <= 6 and rec.get("author") and
                                           (sim(m.group(1).strip(), rec["author"]) > 0.6 or sim(m.group(1).strip(), rec.get("author_as_printed") or "") > 0.6)):
                    end_at = (pn, i)
            if end_at is not None:
                after = [(pn, i) for pn, i in on_last if (pn, i) > end_at and rec["roles"].get(key(pn, i)) in (None, "heading")]
                texts_after = [region_text(pages[pn]["regions"][i]) for pn, i in after]
                story_like = any(prose_like(pages[pn]["regions"][i]) for pn, i in after) or \
                    sum(1 for t_ in texts_after if t_.lstrip()[:1] in "\"\u201c'\u2018") >= 2
                if after and not story_like and len(after) <= 30 and \
                        not any(TAIL_NOTE_RE.match(t_.strip()) or NEXT_BLURB_RE.search(t_) for t_ in texts_after):
                    # one record per advertisement: by column, a display headline after a closing line (an address,
                    # a price, a paragraph of copy) opens the next
                    p_ = pages[last_pn]
                    by_col = {}
                    for pn, i in after:
                        by_col.setdefault(column_of(p_, p_["regions"][i]), []).append((pn, i))
                    for col, items in by_col.items():
                        ad, closed = None, False
                        for pn, i in items:
                            r_ = p_["regions"][i]
                            t_ = region_text(r_)
                            closing = bool(POSTAL_RE.search(t_) or COMPANY_RE.search(t_) or PRICE_RE.search(t_) or ADDRESS_RE.search(t_) or CLOSING_RE.search(t_))
                            headline = (r_["label"] == "SectionHeader" or is_display(p_, r_)) and words(t_) <= 10 and not closing
                            if ad is None or (headline and closed):
                                ad = self.new("ad", title=" ".join(t_.split())[:120], page=last_pn)
                                ad["flags"].append(f"matter after the end of the piece on p.{last_pn}: not the piece's")
                                self.steal(ad, pn, i, "title")
                                closed = False
                                continue
                            self.steal(ad, pn, i)
                            closed = bool(words(t_) >= 25 or closing)
                    rec["keys"].sort()
                    keys = rec["keys"]
        # 2. the end of the piece: the author's name repeated, "The End", the continuation notice, a tail note
        if typ in ("story", "feature", "poem", "letters") and len(keys) >= 3:
            tail = [(pn, i) for pn, i in keys[-4:] if rec["roles"].get(key(pn, i)) in (None, "heading")]
            for pn, i in reversed(tail):
                t = region_text(pages[pn]["regions"][i]).strip()
                if words(t) > 25:
                    break
                lines = [line.strip() for line in t.splitlines() if line.strip()]
                if (typ == "poem" and rec.get("author") and len(lines) == 2
                        and norm_words(lines[0]) == norm_words(rec["author"])
                        and re.fullmatch(r"\d{1,3}\s*[A-Z]?", lines[1])):
                    self.demote(rec, pn, i, "the author's name and printer's signature at the end")
                    continue
                m = END_SIG_RE.match(t)
                if m:
                    name = m.group(1).strip()
                    if rec.get("author") and (sim(name, rec["author"]) > 0.6 or sim(name, rec.get("author_as_printed") or "") > 0.6):
                        self.demote(rec, pn, i, "the author's name repeated at the end")
                    elif not rec.get("author") and words(name) <= 5 and not re.search(r"\d", name):
                        rec["roles"][key(pn, i)] = "author"
                        rec["author"] = name
                        rec["author_source"] = "end signature"
                    else:
                        rec["roles"][key(pn, i)] = "note"        # someone else's name: an editorial note
                    continue
                if THE_END_RE.match(t):
                    self.demote(rec, pn, i, "end mark")
                    continue
                if words(t) <= 14 and re.search(r"to be continued|to be concluded|concluded in|continued next|continued in", t, re.I):
                    rec.setdefault("serial", {"part_label": None, "part_n": None, "part_total": None, "source": "tail"})
                    rec["serial"].setdefault("tail", t)
                    rec["work_title"] = rec.get("work_title") or rec.get("title")
                    self.demote(rec, pn, i, "continuation notice")
                    continue
                if words(t) <= 25 and TAIL_NOTE_RE.match(t):
                    rec["roles"][key(pn, i)] = "note"
                    continue
                break
            # the last paragraph of an instalment that points to the next one ("… will be revealed in next
            # month's WEIRD TALES. Don't miss it."; 'A Complete Novel, "…," by …, in Next Week's Issue.')
            for pn, i in reversed([(pn, i) for pn, i in keys[-3:] if rec["roles"].get(key(pn, i)) in (None, "heading")]):
                t = region_text(pages[pn]["regions"][i]).strip()
                if words(t) <= 60 and NEXT_BLURB_RE.search(t) and (re.search(re.escape(self.magazine_name()[:12]), t, re.I) if self.magazine_name() else True
                                                                    or re.search(r"(?i)\bissue\b|instal|chapters|story|novel", t)):
                    rec["roles"][key(pn, i)] = "note"
                    continue
                break
        # 3. chapter titles as a series (decision of 2026-09-04): display lines of one form on different
        #    pages of a story are its chapter titles; several on one page are the author's typography
        if typ in ("story",) and len(rec["pages"] if rec.get("pages") else {pn for pn, _ in keys}) >= 2:
            cands = []
            for pn, i in keys:
                k = key(pn, i)
                if rec["roles"].get(k) not in (None,) or (pn, i) == keys[0]:
                    continue
                r = pages[pn]["regions"][i]
                t = region_text(r).strip()
                caps_line = t.upper() == t and t.endswith(".") and r["label"] == "Text" and not THE_END_RE.match(t)
                if (r["label"] != "SectionHeader" and not caps_line) or not (1 <= words(t) <= 8) or t.endswith(":"):
                    continue
                if SYNOPSIS_RE.match(t) or CREDIT_RE.match(t) or ILLUS_RE.match(t) or END_SIG_RE.match(t) or byline_author(t):
                    continue
                form = ("caps" if t.upper() == t else "mixed", t.endswith("."))
                cands.append((pn, i, form))
            by_form = {}
            for pn, i, form in cands:
                by_form.setdefault(form, []).append((pn, i))
            for form, members in by_form.items():
                pgs = [pn for pn, _ in members]
                if len(members) >= 2 and len(set(pgs)) == len(pgs):
                    for pn, i in members:
                        rec["roles"][key(pn, i)] = "chapter"
        # 4. the synopsis of a later instalment: the recap between the head and the first chapter head,
        #    or under a "Synopsis" / "The Story So Far" heading (Sujin's note of 2026-09-03)
        ser = rec.get("serial") or {}
        later = bool(ser) and ((ser.get("part_n") or 0) > 1 or re.search(r"(?i)conclu|final|end", ser.get("part_label") or ""))
        head_syn = next(((pn, i) for pn, i in keys if rec["roles"].get(key(pn, i)) == "heading" and SYNOPSIS_RE.match(region_text(pages[pn]["regions"][i]))), None)
        if typ == "story" and (later or head_syn):
            started = head_syn is None                    # without a heading the synopsis begins at the first body paragraph
            n = 0
            drop = 0
            for pn, i in keys:
                k = key(pn, i)
                role = rec["roles"].get(k)
                if (pn, i) == head_syn:
                    started = True
                    continue
                if not started or role in ("title", "subtitle", "author", "teaser", "note", "caption", "heading"):
                    continue
                if role == "chapter":
                    break
                t = region_text(pages[pn]["regions"][i])
                fw = t.split()[0] if t.split() else ""
                dropcap = len(re.sub(r"[^A-Za-z]", "", fw)) >= 3 and fw.upper() == fw
                if dropcap:
                    drop += 1
                    if drop >= 2 and head_syn is None:
                        break                              # the second drop-cap paragraph opens the story proper
                if pn > first_pn + 2 or n >= 40:
                    break
                rec["roles"][k] = "synopsis"
                n += 1
        # 5. departments (config/departments.json): the department field, the type, the usual author
        if typ in ("story", "feature", "letters", "house", "ad", "other") and rec.get("title"):
            d = department_of(self.magazine_name(), rec["title"])
            head_at = keys[0] if d else None
            if not d and typ in ("ad", "house", "other"):
                for pn, i in keys[:3]:
                    d = department_of(self.magazine_name(), region_text(pages[pn]["regions"][i]))
                    if d:
                        head_at = (pn, i)
                        break
            if d:
                name, dtype, dauthor = d
                rec["department"] = name
                bylines_after = sum(1 for pn2, i2 in keys if head_at is not None and (pn2, i2) > head_at and byline_author(region_text(pages[pn2]["regions"][i2])))
                if dtype == "house" and head_at is not None and bylines_after <= 1:
                    # a house block whose header is the department's name ("Coming Up . . ." / "IN THE APRIL
                    # GALAXY"; "NEXT MONTH" / "—The—" / "Tenants of Broussac"): the display lines under the
                    # header, joined, are the title — unless the block announces several works, when the
                    # header itself stays the title
                    parts = []
                    for pn2, i2 in keys:
                        if (pn2, i2) <= head_at:
                            continue
                        t2 = " ".join(region_text(pages[pn2]["regions"][i2]).split())
                        if not t2 or words(t2) > 8 or byline_author(t2) or FILLER_RE.search(t2) or TYPE_LABEL_RE.match(t2) or rec["roles"].get(key(pn2, i2)) not in (None, "title"):
                            break
                        parts.append((pn2, i2, re.sub(r"^[—–\-\s]+|[—–\-\s]+$", "", t2)))
                        if len(parts) == 3:
                            break
                    parts = [x for x in parts if x[2]]
                    if parts:
                        rec["title"] = title_case(" ".join(x[2] for x in parts))
                        for pn2, i2, _ in parts:
                            rec["roles"][key(pn2, i2)] = "title"
                elif norm(rec["title"]).startswith(norm(name)) and len(norm(rec["title"])) > len(norm(name)) + 2:
                    rec["title_as_printed"] = rec.get("title_as_printed") or rec["title"]
                    rec["title"] = name                        # the contents page glued the conductor's name to it
                if dtype == "house":
                    rec["type"] = "house"
                    rec.setdefault("ad_class", "house_next_issue")
                elif typ in ("story", "feature", "letters", "other"):
                    rec["type"] = dtype
                if dauthor and not rec.get("author"):
                    rec["author"] = dauthor
                    rec["author_source"] = "department list"
        # 6. a letters page needs signed letters (Sujin's note of 2026-09-03): else it is a feature
        if rec["type"] == "letters" and not rec.get("department") and (rec.get("toc") or {}).get("type") != "letters":
            # (rules 2.10: the editor's and the readers' departments are letters by Sujin Kang's decision of
            # 21 October; the test below keeps only for a record the page alone made a letters record)
            txt = "\n".join(region_text(pages[pn]["regions"][i]) for pn, i in keys)
            if len(SIGNATURE_RE.findall(txt)) < 2:
                rec["type"] = "feature"
                rec["flags"].append("typed feature: an editor's column with no signed letters")
        # 7a. a 'true story' or '(True Tale)' is a story for the readers (Sujin Kang, 26 October 2026), assembled as a feature
        if rec["type"] == "feature" and (rec.get("toc") or {}).get("true_story"):
            rec["type"] = "story"
            rec["flags"].append("typed story: a true story (assembled as a feature)")
        # 7b. an unsigned piece of a contents page that names no authors is the magazine's ordinary piece, a story
        # (True Romances, 12 Sports Aces, the Illustrated Detective: 52 of Sujin Kang's kind clicks), assembled as a
        # feature — typed story at the contents stage, the stories swallowed the advertisements at their feet
        # (when the contents page names some authors, only a piece whose author the page itself gives is retyped: an
        # unsigned department of Adventure stays a feature)
        if rec["type"] == "feature" and (rec.get("toc") or {}).get("unsigned_story") and not rec.get("department") \
                and ((rec.get("toc") or {}).get("unsigned_story") == "all" or rec.get("author")):
            rec["type"] = "story"
            rec["flags"].append("typed story: an unsigned piece of a contents page without authors (assembled as a feature)")
        # 7. the contents page's 'Verse' label can sit under the entry it belongs to (Weird Tales, 1927-1932):
        # a poem by the contents whose text is prose is a story; a short story whose text is verse is a poem
        # (rules 2.10; 20 of Sujin Kang's 100 kind clicks of 24-25 October)
        if rec["type"] in ("poem", "story") and (rec.get("toc") or {}).get("type") in ("poem", "story", "serial_part") and keys:
            body = "\n".join(region_text(pages[pn]["regions"][i]) for pn, i in keys if rec["roles"].get(key(pn, i)) in (None, "", "body"))
            verse = looks_like_verse(body)
            if rec["type"] == "poem" and verse is False and words(body) >= 150:
                rec["type"] = "story"
                rec["flags"].append("typed story: the contents said verse, the text is prose")
            elif rec["type"] == "story" and verse is True and words(body) <= 400 and not rec.get("serial"):
                rec["type"] = "poem"
                rec["flags"].append("typed poem: the text is verse")

    def merge_page_ads(self):
        """A full advertisement page cut at its display headlines into several
        records, all of one advertiser — one coupon, one Dept. code, one company
        name among them and no other address (Thrilling Detective 1948-12, p. 5:
        the National Radio Institute's page in five headlines): one record
        (Sujin's correction of 4 September)."""
        pages = self.pages
        by_page = {}
        for rec in self.records:
            if rec["type"] != "ad" or not rec["keys"]:
                continue
            pgs = {pn for pn, _ in rec["keys"]}
            if len(pgs) != 1:
                continue
            by_page.setdefault(next(iter(pgs)), []).append(rec)
        for pn, recs in by_page.items():
            if len(recs) < 2:
                continue
            # only a page nobody else is on (a full advertisement page)
            others = {self.owner.get(key(pn, j)) for j in range(len(pages[pn]["regions"]))} - {None, "furniture"} - {r["article_id"] for r in recs}
            if others:
                continue
            depts, signed, unsigned_named, n_addr, with_code = set(), 0, False, 0, []
            for rec in recs:
                txt = "\n".join(region_text(pages[p2]["regions"][i]) for p2, i in rec["keys"])
                d0 = {m.group(1).upper().strip("-") for m in re.finditer(r"\bDept\.?\s*([A-Z0-9\-]{1,8})", txt)}
                c0 = {norm(m.group(1).strip(" ,.")) for m in COMPANY_RE.finditer(txt)
                      if words(m.group(1)) >= 2 and not COMPANY_STOP.search(m.group(1))}
                a0 = len(POSTAL_RE.findall(txt)) + len(re.findall(r"\b[A-Z][a-z]+, (?:[A-Z][a-z]+\.?|[A-Z]{2}|[A-Z]\. ?[A-Z]\.)\s*$", txt, re.M))
                n_addr += a0
                if d0 or c0 or a0:
                    signed += 1
                if (c0 or a0) and not d0:
                    unsigned_named = True                   # a company or an address in a record with no Dept. code: another advertiser
                if d0:
                    with_code.append(rec)
                depts |= d0
            # one advertiser: one Dept. code on the page, in at most two records (the copy and the coupon), the
            # last of them the coupon, no company or address named apart from it, and not a classified page
            if len(recs) <= 6 and signed <= 2 and len(depts) == 1 and not unsigned_named and n_addr <= 3 and with_code and with_code[-1] is recs[-1]:
                first = recs[0]
                for rec in recs[1:]:
                    for p2, i in rec["keys"]:
                        self.owner[key(p2, i)] = first["article_id"]
                        first["keys"].append((p2, i))
                        role = rec["roles"].get(key(p2, i))
                        if role and role != "title":
                            first["roles"][key(p2, i)] = role
                    rec["keys"] = []
                    rec["roles"] = {}
                first["flags"].append(f"the advertisements of p.{pn} joined: one advertiser (one coupon or Dept. code on the page)")

    def head_zone(self, rec):
        """The zone between a piece's head and its first body paragraph (v2.3),
        on the start page and across a spread: every box there that has no
        role yet is paratext. The zone begins after the title or by-line box
        and ends at the first paragraph that opens with a word in capitals (the
        drop cap, split or not), at a chapter head, at a column-width paragraph
        of fifteen words or more, at a second long paragraph in a row, at a
        run of verse, or at forty words. Poems have no zone."""
        pages = self.pages
        keys = rec["keys"]
        if not keys or rec["type"] == "poem":
            return
        head_at = next((n for n, (pn, i) in enumerate(keys) if rec["roles"].get(key(pn, i)) in ("title", "author")), None)
        if head_at is None:
            return
        first_pn = keys[head_at][0]
        n = 0
        long_run = 0
        title_words = set(norm_words(rec.get("title") or "").split()) | set(norm_words((rec.get("toc") or {}).get("title") or "").split())
        for pos in range(head_at, len(keys)):
            pn, i = keys[pos]
            if pn > first_pn + 1 or n > 14:
                break
            k = key(pn, i)
            role = rec["roles"].get(k)
            r = pages[pn]["regions"][i]
            t = " ".join(region_text(r).split())
            if role in ("title", "subtitle", "author", "teaser", "note", "caption", "synopsis"):
                continue
            if role == "chapter":
                break
            if not t:
                continue
            W = pages[pn]["width"]
            x0, y0, x1, y1 = r["bbox"]
            toks = t.split()
            fw = toks[0]
            caps2 = len(toks) >= 3 and len(re.sub(r"[^A-Za-z]", "", fw)) == 1 and fw.isupper() and toks[1].isupper() and len(toks[1]) <= 3 and toks[2][:1].islower()   # "A T first": a split drop cap
            dropcap = (len(re.sub(r"[^A-Za-z]", "", fw)) >= 2 and fw.upper() == fw or caps2) and words(t) >= 8 and not (t.upper() == t)
            column = (x1 - x0) < 0.5 * W and abs((x0 + x1) / 2 - W / 2) > 0.12 * W
            centred_narrow = (x1 - x0) < 0.55 * W and abs((x0 + x1) / 2 - W / 2) <= 0.12 * W
            nxt_key = keys[pos + 1] if pos + 1 < len(keys) else None
            nxt_role = rec["roles"].get(key(*nxt_key)) if nxt_key else None
            before_chapter = nxt_role == "chapter"                 # the box right before "CHAPTER I": the blurb, wherever it is set
            verse = False
            if centred_narrow and nxt_key and nxt_key[0] == pn:
                r2 = pages[pn]["regions"][nxt_key[1]]
                w2 = r2["bbox"][2] - r2["bbox"][0]
                verse = abs(w2 - (x1 - x0)) < 0.15 * (x1 - x0) and abs((r2["bbox"][0] + r2["bbox"][2]) / 2 - W / 2) <= 0.12 * W and words(region_text(r2)) >= 12
            caps_block = t.upper() == t and (words(t) >= 12 or (rec["type"] in ("feature", "letters") and t.endswith(".")))   # a picture feature's captions; a facts column's items
            quoted = t[:1] in "\"\u201c'\u2018" or r["label"] == "Caption"
            long = words(t) >= 15 and not quoted
            if caps_block or verse or (long_run >= 1 and long) or \
                    ((dropcap or words(t) > 40 or (column and words(t) >= 15 and r["label"] == "Text" and not quoted)) and not before_chapter):
                break                                          # the body
            if role == "heading" and re.match(r"^\W*(?:foreword|prologue|introduction)\b", t, re.I):
                continue                                       # the heading stays; what follows it is decided box by box
            if role is not None:
                continue
            n += 1
            long_run = long_run + 1 if long else 0
            tw = set(norm_words(t).split())
            if CREDIT_RE.match(t) and words(t) <= 30:
                self.credit(rec, pn, i)
            elif ILLUS_RE.match(t) and words(t) <= 30:
                self.illustrator(rec, pn, i)
            elif byline_author(t) and words(t) <= 8:
                rec["roles"][k] = "author"                     # a by-line the start did not use (the head on the facing page)
                if not rec.get("author"):
                    rec["author"] = byline_author(t)
                    rec["author_source"] = "page"
            elif tw and title_words and tw <= title_words and words(t) <= 6:
                rec["roles"][k] = "title"                      # a piece of the title drawn in parts ("STAR", "SHELF")
            elif words(t) <= 8 and TYPE_LABEL_RE.match(t):
                rec["roles"][k] = "subtitle"
                rec["subtitle"] = (rec.get("subtitle") + " " + t if rec.get("subtitle") else t)
            elif self.signature_of(t) and words(t) <= 4:
                rec["roles"][k] = "note"                       # the illustrator's signature read beside the drawing
                rec["illustrator"] = rec.get("illustrator") or self.signature_of(t)
                rec["flags"].append(f"the illustrator's signature on p.{pn} read as text ({t!r}): a note")
            elif rec["type"] in ("feature", "letters") and t.upper() == t and words(t) <= 8 and not quoted and not t.endswith((".", "!", "?")):
                rec["roles"][k] = "subtitle"                   # a department's tagline
                rec["subtitle"] = (rec.get("subtitle") + " " + t if rec.get("subtitle") else t)
            elif words(t) <= 40 or quoted:
                rec["roles"][k] = "teaser"                     # the blurb (box by box), the pull-quote, the epigraph, the notice
                if not rec.get("teaser"):
                    rec["teaser"] = t
            else:
                break

    def foot_blurb(self, rec):
        """A blurb set across the foot of the start page or its facing page,
        below the columns (v2.3; Thrilling Detective 1948-12: "Kip Morgan
        Wades Through a Battle of Fists" at the foot of p.78 and its second
        half "and Knives for a Hard-Hitting K. O. of Crime!" at the foot of
        p.79; the boxed introduction "FROM THE ARCHIVES OF SCOTLAND YARD" with
        its paragraph at the foot of p.71, which runs on into the first column
        paragraph under the by-line). A display line of at most twenty words
        there, or a header in capitals with one paragraph (at most a hundred
        words) under it, is teaser;
        when the paragraph ends without a full stop and the first column
        paragraph after the head begins in lower case, that paragraph is the
        introduction's continuation. Pages without column text are left alone
        (the digests set everything full width)."""
        pages = self.pages
        keys = rec["keys"]
        head_at = next((n for n, (pn, i) in enumerate(keys) if rec["roles"].get(key(pn, i)) in ("title", "author")), None)
        if head_at is None:
            return
        first_pn = keys[head_at][0]
        for pn in (first_pn, first_pn + 1):
            p = pages.get(pn)
            if not p:
                continue
            H = p["height"]
            mine = [(pos, i) for pos, (pn_, i) in enumerate(keys) if pn_ == pn]
            cols = [p["regions"][i] for _, i in mine
                    if column_of(p, p["regions"][i]) != "span" and p["regions"][i]["label"] == "Text"
                    and rec["roles"].get(key(pn, i)) is None]
            if not cols:
                continue
            col_bottom = max(r["bbox"][3] for r in cols)
            foot = []
            for pos, i in mine:
                r = p["regions"][i]
                k = key(pn, i)
                if rec["roles"].get(k) is not None or column_of(p, r) != "span" or r["label"] not in ("Text", "SectionHeader", "Caption"):
                    continue
                if r["bbox"][1] < col_bottom - 0.02 * H or r["bbox"][1] < 0.6 * H:
                    continue
                t = " ".join(region_text(r).split())
                if not t or chapter_head(t) or words(t) > 100 or t[:1] in "\"\u201c'\u2018":
                    continue                                   # dialogue is the body
                fw = t.split()[0]
                if len(re.sub(r"[^A-Za-z]", "", fw)) >= 3 and fw.upper() == fw and words(t) > 20:
                    continue                                   # a paragraph opening with a drop cap: the body
                prev = p["regions"][keys[pos - 1][1]] if pos > 0 and keys[pos - 1][0] == pn else None
                if prev is not None and prev["label"] == "Text" and abs(prev["bbox"][0] - r["bbox"][0]) < 0.03 * p["width"] \
                        and column_of(p, prev) != "span":
                    continue                                   # the same flow of text as the line above it (a page cut round a drawing)
                foot.append((pos, i, t))
            if not foot:
                continue
            ok = all(words(t) <= 20 for _, _, t in foot) or \
                (len(foot) == 2 and foot[0][2].upper() == foot[0][2] and words(foot[0][2]) <= 10 and words(foot[1][2]) <= 100)
            if not ok:
                continue
            for pos, i, t in foot:
                rec["roles"][key(pn, i)] = "teaser"
                rec["teaser"] = (rec["teaser"] + " " + t) if rec.get("teaser") else t
            rec["flags"].append(f"blurb across the foot of p.{pn} ({len(foot)} box(es)): teaser")
            last_t = foot[-1][2]
            if len(foot) == 2 and not re.search(r"[.!?\"”']$", last_t):
                for pos, (pn_, i) in enumerate(keys):
                    if pos <= head_at or pn_ != pn or rec["roles"].get(key(pn_, i)) is not None:
                        continue
                    r = p["regions"][i]
                    if r["label"] != "Text":
                        break
                    t = " ".join(region_text(r).split())
                    if t and t[:1].islower() and words(t) <= 60:
                        rec["roles"][key(pn_, i)] = "teaser"
                        rec["flags"].append(f"the introduction at the foot of p.{pn} runs on into the first paragraph under the head: teaser")
                    break

    def signature_of(self, t):
        """An illustrator's name in a short garbled box ("ADROSNATCH", "A. BROSNAT
        & Co.") against the magazine's known illustrators (config/illustrators.json)
        and the issue's own "Illustrated by" credits; the canonical name, or None."""
        t2 = re.sub(r"[^A-Za-z ]+", " ", t).strip()
        if not t2 or words(t2) > 4 or byline_author(t) or CREDIT_RE.match(t) or chapter_head(t):
            return None
        names = list(illustrators_of(self.magazine_name()))
        for rec in self.records:
            if rec.get("illustrator"):
                names.append(rec["illustrator"])
        cand = t2.replace(" ", "").lower()
        for name in names:
            last = name.split()[-1].lower()
            if len(last) >= 5 and (difflib.SequenceMatcher(None, cand, last).ratio() >= 0.7 or last in cand):
                return name
        return None

    def trailing_display_block(self, rec):
        """A block of short display lines at the foot of a continuation page,
        with nothing of the piece after it on that page and the piece going on
        on a later page, is advertising the layout gave no headline to
        ("Everybody Gives— Everybody Benefits! / Contribute Generously to the
        RED FEATHER SERVICES / COMMUNITY CHEST"): its own advertisement record."""
        pages = self.pages
        keys = sorted(rec["keys"])
        by_page = {}
        for pn, i in keys:
            by_page.setdefault(pn, []).append(i)
        pgs = sorted(by_page)
        for pn in pgs:
            idx = by_page[pn]
            regs = pages[pn]["regions"]
            # the piece's last boxes on the page, from the end back to the last paragraph of prose
            block = []
            for i in reversed(idx):
                r = regs[i]
                t = " ".join(region_text(r).split())
                if rec["roles"].get(key(pn, i)) not in (None,) or not t:
                    break
                if words(t) <= 12 and t[:1] not in "\"\u201c'\u2018" and not t.endswith((".", ",", ";", ":")) and not re.search(r"[a-z]{2,}\s[a-z]{2,}\s[a-z]{2,}\s[a-z]{2,}", t):
                    block.insert(0, i)
                else:
                    break
            if len(block) < 3 or not any(region_text(regs[i]).upper() == region_text(regs[i]) for i in block):
                continue
            if not any(is_display(pages[pn], regs[i]) for i in block):
                continue
            # nothing of anybody else after the block on the page (the foot of the page)
            after = [j for j in range(block[-1] + 1, len(regs)) if key(pn, j) in self.owner and self.owner[key(pn, j)] != "furniture"]
            if after:
                continue
            ad = self.new("ad", title=" ".join(region_text(regs[block[0]]).split())[:120], page=pn)
            ad["flags"].append(f"display lines at the foot of p.{pn} with nothing of the piece after them: an advertisement")
            for i in block:
                self.steal(ad, pn, i, "title" if i == block[0] else None)

    def empty_title_box(self, rec):
        """The box of a title drawn as lettering the OCR could not read (an empty
        SectionHeader on the start page, before the first box of the record):
        the record's, with the role title, so the scan shows the head as the
        record's (Sujin's records of 4 September)."""
        keys = sorted(rec["keys"])
        if not keys or rec.get("title_source") != "contents":
            return
        if any(rec["roles"].get(key(pn, i)) == "title" for pn, i in keys):
            return
        pn, first = keys[0]
        regs = self.pages[pn]["regions"]
        for j in range(first - 1, -1, -1):
            r = regs[j]
            if r["label"] == "SectionHeader" and not region_text(r) and self.owner.get(key(pn, j)) in (None, "furniture"):
                self.take(rec, pn, j, "title")
                rec["flags"].append(f"the title box on p.{pn} carries no readable text; the title is the contents page's")
                return
            if region_text(r) and self.owner.get(key(pn, j)) not in (None, "furniture"):
                return

    def chapters_of(self, rec):
        """The chapter heads of a record, in order, number and title apart:
        'CHAPTER II' + 'The Thing in the Vault' on two lines or one."""
        out = []
        prev = None
        for pn, i in rec["keys"]:
            k = key(pn, i)
            if rec["roles"].get(k) != "chapter":
                prev = None
                continue
            t = region_text(self.pages[pn]["regions"][i])
            num, n, title = split_chapter(t)
            if num is None and prev is not None and not prev.get("title") and prev["page"] == pn:
                prev["title"] = title                      # the chapter's title on the line after its number
                prev["region_ids"].append(i)
                prev = None
                continue
            ch = {"number": num, "n": n, "title": title, "page": pn, "region_ids": [i]}
            out.append(ch)
            prev = ch if num is not None else None
        return out

    def classify_ad(self, rec):
        """ad_class and advertiser for an advertisement record, plus the works a
        house announcement names (announces) and whether it quotes one of them
        (contains_excerpt, excerpt_of). The classes: house_next_issue,
        house_self, house_sibling, house_form, trade, classified."""
        pages = self.pages
        texts = [region_text(pages[pn]["regions"][i]) for pn, i in rec["keys"]]
        full = "\n".join(texts)
        title = rec.get("title") or ""
        own = [n for n in (self.magazine_name(), self.A.get("magazine_head") or "") if n]
        own_re = re.compile("|".join(re.escape(n) for n in own), re.I) if own else None
        body_only = "\n".join(t_ for t_ in texts if not MENTION_RE.search(t_))     # not "please mention Weird Tales"
        mentions_self = bool(own_re and own_re.search(body_only))
        # announced works: the by-lines the analysis rejected as announcements inside this record
        mine = {(pn, i) for pn, i in rec["keys"]}
        announces = []
        for pn in sorted({pn for pn, _ in rec["keys"]}):
            for s in self.A["pages"][pn].get("rejected_starts", []):
                bi = s.get("byline_idx")
                if bi is not None and (pn, bi) in mine and s.get("title"):
                    announces.append({"title": s["title"], "author": s.get("author"), "page": pn, "idx": start_first(s)})
        # a verbatim excerpt: narrative prose of some length inside house copy
        prose_words = sum(words(region_text(pages[pn]["regions"][i])) for pn, i in rec["keys"] if prose_like(pages[pn]["regions"][i]))
        n_ad = len(AD_WORDS.findall(full))
        company = COMPANY_RE.search(full)
        siblings = [m.group(1).strip() for m in SIBLING_RE.finditer(full)
                    if not (own_re and own_re.search(m.group(1))) and words(m.group(1)) <= 4
                    and not re.fullmatch(r"(?:The |A )?(?:Magazine|Stories|Weekly|Monthly|Tales|Love|Wonder)", m.group(1).strip())]
        # works named as "TITLE, by Author" / "TITLE\nBy Author" inside house copy (no by-line region of their own)
        for m in re.finditer(r"(?m)^([A-Z][A-Z0-9' ,\-!?.]{3,60}?),?\s*(?:\n|\s)[Bb]y ([A-Z][A-Za-z.' \-]{2,50}?)\s*$", full):
            t_, a_ = m.group(1).strip(" ,"), m.group(2).strip(" .,")
            if words(t_) <= 10 and not any(norm(t_) == norm(x["title"]) for x in announces):
                announces.append({"title": t_, "author": a_, "page": rec["keys"][0][0], "idx": 10 ** 6})
        siblings = [x for x in siblings if x.split()[0].lower() not in SIBLING_STOP]
        cls, advertiser = None, None
        next_issue = bool(HOUSE_NEXT_RE.search(full) or FILLER_RE.search(title) or announces)
        self_copy = bool(mentions_self and HOUSE_SELF_RE.search(full))
        toc_page = self.A.get("toc_page")
        first = min(pages) if pages else None
        if rec.get("ad_class") == "house_form":
            cls, advertiser = "house_form", (own[0] if own else None)
        elif first is not None and all(pn == first for pn, _ in rec["keys"]) and cover_page_score(pages[first], self.magazine_name())[0] >= 3:
            cls, advertiser = "cover", (own[0] if own else None)             # the cover (rules 2.12): type other, not house
            rec["flags"].append("the cover: the masthead, the cover lines, the price")
        elif toc_page and all(pn <= toc_page for pn, _ in rec["keys"]) and (mentions_self or announces):
            cls, advertiser = "house_self", (own[0] if own else None)     # front matter: the cover strip, the title page
            rec["flags"].append("front matter (on or before the contents page): the issue's own cover strip or title page")
        elif next_issue and (mentions_self or FILLER_RE.search(title) or n_ad == 0 or announces) and not self_copy \
                and not (company and n_ad >= 2 and not mentions_self and not FILLER_RE.search(title)):
            cls, advertiser = "house_next_issue", (own[0] if own else None)
        elif siblings and SIBLING_SALE_RE.search(full) and not company and \
                (not mentions_self or re.search(r"publishers? of|companion|sister magazine|by the same", full, re.I)):
            cls, advertiser = "house_sibling", siblings[0]
        elif mentions_self and (self_copy or n_ad == 0 or next_issue):
            cls, advertiser = "house_self", (own[0] if own else None)
        elif FORM_RE.search(full) and re.search(r"ballot|favou?rite stor", full, re.I):
            cls, advertiser = "house_form", (own[0] if own else None)
        else:
            heads = len(CATEGORY_HEAD_RE.findall(full))
            addrs = len({norm(m.group(0)) for m in ADDRESS_RE.finditer(full)})      # distinct: one advertiser repeats its address
            depts = len({m.group(1).upper() for m in re.finditer(r"\bDept\.?\s*([A-Z0-9\-]{1,8})", full)})
            if heads >= 4 and addrs >= 4 and len(rec["keys"]) >= 4 and depts != 1:
                cls = "classified"
                rec["n_items"] = max(heads, addrs)
            else:
                cls = "trade"
                cands = [m.group(1).strip(" ,.") for m in COMPANY_RE.finditer(full)]
                cands = [c for c in cands if words(c) >= 2 and not COMPANY_STOP.search(c)]
                advertiser = cands[-1] if cands else None      # the signature line at the end of the copy
                if not advertiser:
                    m = re.search(r"(?m)^([A-Z][A-Za-z&'\.\-]+(?: [A-Z][A-Za-z&'\.\-]+){0,4}),\s*(?:Dept|Desk|Box|Studio)", full)
                    advertiser = m.group(1) if m else None
        rec["ad_class"] = cls
        rec["advertiser"] = advertiser
        if announces:
            rec["announces"] = [{"title": a["title"], "author": a["author"], "page": a["page"]} for a in announces]
            if cls in ("trade", "classified"):
                rec["flags"].append("a house announcement shares this block with the advertisement (" +
                                    "; ".join(a["title"][:40] for a in announces[:3]) + ")")
        rec["contains_excerpt"] = bool(cls == "house_next_issue" and prose_words >= EXCERPT_MIN_WORDS)
        if rec["contains_excerpt"]:
            # the quoted work: the announced piece whose title follows the prose (its blurb precedes the title)
            first_prose = next(((pn, i) for pn, i in rec["keys"] if prose_like(pages[pn]["regions"][i])), None)
            after = [a for a in announces if first_prose and (a["page"], a["idx"]) > first_prose]
            pick = (after or announces or [None])[0]
            rec["excerpt_of"] = ({"title": pick["title"], "author": pick["author"]} if pick else None)
            rec["flags"].append("house announcement quoting a story (contains_excerpt); left out of the reuse inventory")
        rec["author"] = None                          # an advertisement has no author; the works it names are in announces

    def is_ad_page(self, pno, p, info):
        """An advertisement page inside the printed range (Weird Tales numbers
        its advertisement pages and gives them running heads): advertisement
        words, no start, and no sign that the open piece continues here."""
        if info["starts"] or info["ad_score"] < 3 or info["chapters"] or info["cont_from"]:
            return False
        regs = p["regions"]
        content = [i for i in range(len(regs)) if i not in info["furniture"]]
        if not content:
            return False
        if sum(1 for i in content if prose_like(regs[i])) >= 3:
            return False                                  # story columns beside advertisements: a mixed page
        first = region_text(regs[content[0]])
        if first[:1].islower():
            return False
        if self.open is not None and self.open.get("type") not in ("ad", "other") and \
                any(region_text(regs[i])[:1].islower() and words(region_text(regs[i])) >= 25 for i in content):
            return False                                  # a paragraph that starts mid-sentence: the open piece continues here
        if self.open is not None and self.open.get("type") not in ("ad", "other"):
            title = self.open.get("title") or ""
            if any(sim(h, title) > 0.5 for h in info["running"]):
                return False
            if self.last_text and not re.search(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$", self.last_text):
                # the piece paused mid-sentence: it continues here unless this page is plainly an
                # advertisement page (no narrative prose, many selling words) — then the piece is suspended
                if sum(1 for i in content if prose_like(regs[i])) >= 2 or info["ad_score"] < 8 or first[:1].islower():
                    return False
        long = sum(1 for i in content if words(region_text(regs[i])) >= 40)
        return not (long >= 3 and info["ad_score"] < 6)

    def ad_page(self, pno, p, info):
        """A page outside the printed range: one advertisement per display
        header; the first regions join the first header's advertisement."""
        regs = p["regions"]
        rec = None
        pending = []
        for i, r in enumerate(regs):
            if i in info["furniture"]:
                continue
            if is_display(p, r) and r["label"] == "SectionHeader":
                if rec is not None and pending:
                    for j in pending:
                        self.add(rec, pno, j)
                    pending = []
                t = region_text(r).replace("\n", " ")
                if rec is not None and len(rec["keys"]) == 1 and not re.search(r"\d", rec["title"] or "") and words(rec["title"]) <= 6:
                    # two display lines with nothing between them: one headline in two sizes ("FREE FREE FREE" /
                    # "A $1.50 Self-filling Stylographic Pen")
                    rec["title"] = (rec["title"] + " " + t)[:120]
                    self.add(rec, pno, i, "title")
                    continue
                rec = self.new("ad", title=t[:120], page=pno)
                self.add(rec, pno, i, "title")
            elif rec is None:
                pending.append(i)
            else:
                self.add(rec, pno, i)
        if pending:
            if rec is None:
                n_words = sum(words(region_text(regs[j])) for j in pending)
                rec = self.new("ad" if (info["ad_score"] or n_words >= 8) else "other", title=None, page=pno)
                # the title: the tallest display line with the short display lines around it ("OLD GOLD" /
                # "changed the nation's smoking" / "HABITS"; "I do prefer" / "LUCKY STRIKE CIGARETTE")
                disp = [j for j in pending if is_display(p, regs[j]) and region_text(regs[j])]
                title_idx = []
                if disp:
                    tallest = max(disp, key=lambda j: regs[j]["bbox"][3] - regs[j]["bbox"][1])
                    title_idx = [tallest]
                    for j in reversed([x for x in disp if x < tallest]):
                        if j == title_idx[0] - 1 and words(region_text(regs[j])) <= 6:
                            title_idx.insert(0, j)
                        else:
                            break
                    for j in [x for x in disp if x > tallest]:
                        tj = region_text(regs[j])
                        if j == title_idx[-1] + 1 and words(tj) <= 5 and tj.upper() == tj:
                            title_idx.append(j)
                        else:
                            break
                if title_idx:
                    rec["title"] = " ".join(" ".join(region_text(regs[j]).split()) for j in title_idx)[:120]
                else:
                    t = next((region_text(regs[j]) for j in pending if region_text(regs[j])), "")
                    rec["title"] = t.split("\n")[0][:120]
                for j in pending:
                    self.add(rec, pno, j, "title" if j in title_idx else None)
                return
            for j in pending:
                self.add(rec, pno, j)

    def continued_prefix_columns(self, pno, p, info, loose, notice, target, catalogue_pages=None):
        """Keep the preceding story's run-on columns above another continuation.

        A later named continuation can share a three-column page with the
        preceding story and a narrow advertising column. OCR often lists the
        entire advertising column first; that does not make the two narrative
        columns an advertisement. Require an adjacent open story, a lowercase
        run-on, multiple narrative paragraphs, and separate commercial boxes.
        """
        if (self.open is None or self.open is target
                or self.open.get("type") not in ("story", "feature", "letters", "poem")
                or pno - 1 not in self.pages):
            return []
        def overlaps_box(a, b):
            return max(0, min(a[2], b[2]) - max(a[0], b[0])) >= .75 * min(a[2] - a[0], b[2] - b[0])

        def catalogue(text):
            return len(re.findall(r"(?im)\bfrom\s*\$\s*\d", text)) >= 3

        def direct_offer(region):
            text = region_text(region)
            return (region["label"] == "Form" or catalogue(text)
                    or bool(re.search(r"(?i)\b(?:send no money|money[- ]back|mail (?:the )?coupon|free trial|refunded|"
                                      r"buy from your local dealer|kits? only\s+\d)\b", text))
                    or (bool(re.search(r"(?i)\b(?:send|mail|order|buy|postpaid)\b", text))
                        and bool(POSTAL_RE.search(text) or re.search(r"\$\s*\d|\d+\s*(?:cents?|[¢c])\b", text)))
                    or (region["label"] in ("Caption", "SectionHeader")
                        and bool(re.search(r"(?i)\b(?:kit|bottle|booklet|copy)\b", text))
                        and bool(re.search(r"\$\s*\d", text))))

        def whole_catalogue_page(page):
            # A full intervening product advertisement can have been wrongly
            # owned by the open story already. Skip its identity evidence only
            # when every substantive text box names the same printed product
            # or is an explicit offer/catalogue. A narrative column vetoes it.
            regs = page["regions"]
            if any(THE_END_RE.fullmatch(region_text(r)) or CONT_ON_RE.search(region_text(r)) for r in regs):
                return False
            if not any(catalogue(region_text(r)) for r in regs):
                return False
            if any(r["label"] == "Text" and 0 < words(region_text(r)) < 8
                   and (region_text(r).lstrip().startswith(('"', '\u201c', '\u2018'))
                        or re.search(r"(?i)\b(?:I|we|he|she|they|his|her|their)\b", region_text(r)))
                   for r in regs):
                return False  # short dialogue can be genuine narrative too
            bodies = [r for r in regs if r["label"] == "Text" and words(region_text(r)) >= 8]
            brands = [region_text(r) for r in regs if r["label"] == "SectionHeader"
                      and re.fullmatch(r"[A-Z]{5,}", region_text(r))]
            return next((brand for brand in brands
                       if sum(bool(re.search(r"(?i)\b" + re.escape(brand) + r"s?\b", region_text(r))) for r in bodies) >= 2
                       and all(direct_offer(r) or re.search(r"(?i)\b" + re.escape(brand) + r"s?\b", region_text(r))
                               for r in bodies)), None)

        previous_page = pno - 1
        catalogue_title = whole_catalogue_page(self.pages[previous_page])
        if catalogue_title and pno - 2 in self.pages:
            previous_page = pno - 2
        previous_owned = [(pn, j) for pn, j in self.open["keys"] if pn == previous_page]
        if any(THE_END_RE.fullmatch(region_text(self.pages[pn]["regions"][j]))
               or CONT_ON_RE.search(region_text(self.pages[pn]["regions"][j])) for pn, j in previous_owned):
            return []
        prev_regs = self.pages[previous_page]["regions"]
        prior_offers = [r["bbox"] for r in prev_regs if direct_offer(r)]
        previous = [(pn, j) for pn, j in previous_owned
                    if self.open["roles"].get(key(pn, j)) not in
                    ("title", "author", "subtitle", "teaser", "caption", "note")
                    and not any(overlaps_box(prev_regs[j]["bbox"], box) for box in prior_offers)]
        if not previous:
            return []
        # Derive this from the actual owned reading boxes, not last_text: that
        # mutable convenience field can still describe an earlier open record.
        previous = order_region_keys(self.pages, previous)
        previous_text = [region_text(self.pages[pn]["regions"][j]) for pn, j in previous]
        last_region = self.pages[previous[-1][0]]["regions"][previous[-1][1]]
        if (not previous_text[-1]
                or last_region["label"] != "Text" or words(previous_text[-1]) < 8
                or not (prose_like(last_region) or re.search(r"\b(?:I|he|she|his|her|they|their|we|our)\b", previous_text[-1], re.I))
                or re.search(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$", previous_text[-1])):
            return []
        regs = p["regions"]
        top = regs[notice]["bbox"][1]
        text = [j for j in loose if regs[j]["label"] == "Text"
                and regs[j]["bbox"][1] < top]

        def overlaps(j, k):
            return overlaps_box(regs[j]["bbox"], regs[k]["bbox"])

        offers = {j for j in loose if direct_offer(regs[j]) or j in info.get("forms", {})}
        # Narrow coupon lines and inset headlines may not overlap one another
        # directly. Connect them through the other boxes of that same column;
        # never use a spanning box to bridge into a neighbouring story column.
        while offers:
            expanded = offers | {j for j in loose
                                 if regs[j]["bbox"][2] - regs[j]["bbox"][0] <= .4 * p["width"]
                                 and any(overlaps(j, k) for k in offers)}
            if expanded == offers:
                break
            offers = expanded
        anchors = [j for j in text if prose_like(regs[j])
                   and .15 * p["width"] <= regs[j]["bbox"][2] - regs[j]["bbox"][0] <= .4 * p["width"]
                   and not any(overlaps(j, k) for k in offers)]
        if len(anchors) < 2:
            return []
        narrative = [j for j in text if not any(overlaps(j, k) for k in offers)]
        if not narrative:
            return []
        first = min(narrative, key=lambda j: (regs[j]["bbox"][1], regs[j]["bbox"][0]))
        if not region_text(regs[first])[:1].islower() or words(region_text(regs[first])) < 4:
            return []
        # A genuine separate commercial column is required. No source box is
        # discarded merely for containing an ordinary advertising vocabulary word.
        commercial = [j for j in offers if not any(overlaps(j, k) for k in narrative)]
        if not commercial or any(regs[j]["bbox"][1] >= top and not any(overlaps(j, k) for k in offers)
                                 for j in loose if regs[j]["label"] == "Text"):
            return []
        if catalogue_pages is not None and catalogue_title and previous_page == pno - 2:
            catalogue_pages.append((pno - 1, catalogue_title))
        return narrative

    def separate_continuation_catalogues(self, catalogue_pages):
        """Remove a proven intervening advertisement from the reading stream.

        Called only after the complete continuation predicate succeeds. Keep
        every box and its identity in a separate advertiser record; never move
        boxes owned by another piece or merely suspected commercial prose.
        """
        for pn, title in catalogue_pages:
            owned = [(p, j) for p, j in self.open["keys"] if p == pn]
            if not owned:
                continue
            advertisement = self.new("ad", title=title, page=pn)
            for _, j in owned:
                self.steal(advertisement, pn, j)
            advertisement["flags"].append(f"whole product catalogue on p.{pn} separated from a continuous story")
            self.open["flags"].append(f"reading resumes across the separate product catalogue on p.{pn}")

    def story_page(self, pno, p, info):
        regs = p["regions"]
        pages_ = self.pages
        # Restore before processing a caption or an ornament at the top: making
        # a new feature for that box used to strand the single paragraph below.
        target = self.continuations.pop(pno, None)
        first_start = min((start_first(s) for s in info["starts"]), default=len(regs))
        body_before_start = [j for j in range(first_start) if j not in info["furniture"]
                             and prose_like(regs[j])]
        if target is not None:
            body_before_start = [j for j in range(first_start) if j not in info["furniture"]
                                 and words(region_text(regs[j])) >= 4
                                 and not AD_WORDS.search(region_text(regs[j]))]
        resume_allowed = (self.suspended is not None
                          and generic_resume_blocker(p, info, self.magazine_name()) is None
                          and not any(dest > pno and rec is self.suspended
                                      for dest, rec in self.continuations.items()))
        if body_before_start and (target is not None or (self.open is None and resume_allowed)):
            self.open = target if target is not None else self.suspended
            self.open["flags"].append(f"resumes on p.{pno} after interrupted story pages")
            if self.open is self.suspended:
                self.suspended = None
        starts = {s["byline_idx"]: s for s in info["starts"] if s["byline_idx"] is not None}
        at_of = {s["at"]: s for s in info["starts"] if s.get("at") is not None}
        title_of = {}
        for s in info["starts"]:
            for j in s["title_idx"] + s["subtitle_idx"] + list(s.get("teaser_idx") or []):
                title_of[j] = s
        chapters = set(info["chapters"])
        fillers = set(info["fillers"])
        forms = info.get("forms") or {}
        # announcement by-lines the analysis rejected inside a filler block: where one begins with no
        # filler open, the announcement (title, by-line, blurb, a quoted excerpt) is house advertising
        announce_at = {}
        for s in info.get("rejected_starts", []):
            rej = s.get("rejected") or ""
            after = [j for j in range(s["byline_idx"] + 1, len(regs)) if j not in info["furniture"]][:3] if s.get("byline_idx") is not None else []
            notice_after = any(words(region_text(regs[j])) <= 12 and FILLER_RE.search(region_text(regs[j])) for j in after)
            if "inside a filler block" in rej or ("an announcement" in rej and notice_after):
                announce_at.setdefault(start_first(s), s)
                if notice_after:
                    s["_notice_after"] = True
        n = len(regs)
        filler = None
        filler_hard = False        # the filler runs to the end of the page (a house announcement with its excerpt)
        cont_on = set(info.get("cont_on") or [])
        tail = False               # below a "continued on page N" / "[turn page]" notice: the rest of the page is not the piece
        cont_from = dict(info["cont_from"])
        if cont_from:
            # regions above the first "continued from" notice: the piece's repeated title joins the
            # piece; anything else belongs to nobody we know and gets its own record, flagged
            first_notice = min(cont_from)
            loose = [j for j in range(first_notice) if j not in info["furniture"] and key(pno, j) not in self.owner]
            if loose and not any(start_first(s) < first_notice for s in info["starts"]):
                target = self.continued_piece(cont_from[first_notice], pno)
                ttl = (target or {}).get("title") or ""
                # The repeated title/author can follow advertisements in the
                # other column. It is still furniture above this continuation.
                repeated = [j for j in loose if target is not None
                            and column_of(p, regs[j]) == column_of(p, regs[first_notice])
                            and words(region_text(regs[j])) <= 12
                            and norm_words(region_text(regs[j])) in
                            {norm_words(name) for name in (ttl, target.get("author")) if name}]
                for j in repeated:
                    self.furn(pno, j)
                    self.furniture[-1]["why"] = "repeated title or author above a 'continued from' notice"
                loose = [j for j in loose if j not in repeated]
                catalogue_pages = []
                continued_prefix = self.continued_prefix_columns(pno, p, info, loose, first_notice, target, catalogue_pages)
                if continued_prefix:
                    self.separate_continuation_catalogues(catalogue_pages)
                for j in continued_prefix:
                    text = region_text(regs[j])
                    role = "note" if words(text) <= 40 and FILLER_RE.search(text) else None
                    self.add(self.open, pno, j, role)
                if continued_prefix:
                    self.open["flags"].append(f"preceding story columns retained above another continuation on p.{pno}")
                    loose = [j for j in loose if j not in continued_prefix]
                if not loose:
                    pass
                elif target is not None and len(loose) <= 2 and all(words(region_text(regs[j])) <= 12 for j in loose) and \
                        any(sim(region_text(regs[j]), ttl) > 0.5 for j in loose):
                    for j in loose:
                        self.furn(pno, j)                          # the piece's title repeated over the continuation
                        self.furniture[-1]["why"] = "repeated title above a 'continued from' notice"
                else:
                    ltxt = " ".join(region_text(regs[j]) for j in loose)
                    kind = "ad" if (len(AD_WORDS.findall(ltxt)) >= 1 or info["ad_score"] >= 3) else "other"
                    if kind == "ad":
                        # Preserve the printed boundaries of separate offers;
                        # a later continuation does not make them one advertiser.
                        self.ad_page(pno, p, dict(info, furniture=[j for j in range(n) if j not in loose]))
                    else:
                        rec = self.new(kind, title=self.page_title(p, info, loose[0]) or region_text(regs[loose[0]])[:60], page=pno)
                        rec["flags"].append("text above a 'continued from' notice with no known owner")
                        self.open = rec
                        for j in loose:
                            self.add(rec, pno, j)
        pre_new = []               # regions above the title that belong to the piece starting on this page
        if info["starts"]:
            # regions above the first title of the page: the end of the open piece's column when there is
            # evidence it runs on here; else the illustration's caption (furniture, as the verified records
            # have it), the blurb, a "The Story Thus Far" heading or a second setting of the title (the new
            # piece's: teaser, heading), signature marks and display garble (furniture)
            first = min(start_first(s) for s in info["starts"])
            end_re = re.compile(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$")
            # a "continued on" / "to be continued" notice above the title ends the open piece's column there:
            # what stands between the notice and the title is announcement matter for the tail rule below
            notice_above = min((c for c in cont_on if c < first), default=None)
            above = [j for j in range(0, first) if j not in info["furniture"] and key(pno, j) not in self.owner]
            open_ok = self.open is not None and self.open.get("type") not in ("ad", "other")
            # evidence that the open piece's column runs on at the top of this page: a paragraph that starts
            # in lower case (a sentence carried over) or a long one; every Text region down to the last such
            # paragraph is the column, and so is the Text that follows it — except a short quoted line right
            # before the title, which is the illustration's caption
            evidence = [j for j in above if regs[j]["label"] == "Text" and
                        (region_text(regs[j])[:1].islower() or words(region_text(regs[j])) > 40
                         or (target is not None and self.open is target and not AD_WORDS.search(region_text(regs[j]))))
                        and (notice_above is None or j < notice_above)
                        and not (words(region_text(regs[j])) <= 8 and TYPE_LABEL_RE.match(region_text(regs[j])))    # "a novel" is a label
                        and words(region_text(regs[j])) >= 4]
            col_last = max(evidence) if (evidence and open_ok) else -1
            new_titles = [s_["title"] for s_ in info["starts"] if s_.get("title")] + \
                         [s_["toc"]["title"] for s_ in info["starts"] if s_.get("toc") and s_["toc"].get("title")]
            joined = False
            for j in above:
                r_ = regs[j]
                t = region_text(r_)
                last_above = (j == above[-1])
                after_notice = notice_above is not None and j > notice_above
                run_on = joined and r_["label"] == "Text" and not (last_above and t[:1] in "\"\u201c'\u2018" and words(t) <= 15) and not after_notice
                if open_ok and r_["label"] == "Text" and (j <= col_last or run_on):
                    self.add(self.open, pno, j, "chapter" if j in chapters else None)     # the end of the open piece
                    joined = True
                    continue
                joined = False
                if after_notice and not ((CREDIT_RE.match(t) or ILLUS_RE.match(t)) and words(t) <= 30
                                         or any(title_prefix(t, nt) or sim(t, nt) > 0.6 for nt in new_titles)
                                         or (words(t) <= 8 and TYPE_LABEL_RE.match(t)) or r_["label"] == "Caption"):
                    continue                                   # between the notice and the title: the tail rule's (announcements)
                if words(t) > 40 and self.open is None:
                    rec = self.new("feature", title=t.split("\n")[0][:80], page=pno)
                    rec["flags"].append("text above the first title of the page with no record open")
                    self.add(rec, pno, j)
                elif (CREDIT_RE.match(t) or ILLUS_RE.match(t)) and words(t) <= 30:
                    pre_new.append(j)                          # the credit line of a head set inside the illustration
                elif any(title_prefix(t, nt) for nt in new_titles):
                    pre_new.append(j)                          # the first words of a title set in two boxes ("THE YEAR" / "OF THE JACKPOT")
                elif words(t) <= 8 and TYPE_LABEL_RE.match(t):
                    pre_new.append(j)                          # "a novel" above the title: the new piece's type label
                elif r_["label"] == "Caption" or t[:1] in "\"\u201c'\u2018":
                    pre_new.append(j)                          # the illustration's caption or pull-quote on the start page: the new
                                                               # piece's teaser (the annotators' rule since 2026-09-04)
                elif any(sim(t, nt) > 0.6 for nt in new_titles) or \
                        (words(t) >= 4 and re.search(r"[a-z]", t) and (end_re.search(t) or r_["label"] == "SectionHeader" or
                                                                        (8 <= words(t) <= 80 and not (len(t.split()[0]) >= 2 and t.split()[0].isupper())))):
                    pre_new.append(j)                          # the blurb, the synopsis heading or a second setting of the title
                else:
                    self.furn(pno, j)
                    self.furniture[-1]["why"] = "above the title on a start page"
        pre_set = set(pre_new)
        ended_piece = None          # a following department can own its own continuation notice
        i = 0
        while i < n:
            if i in pre_set:
                i += 1                                    # attached to the new piece when its record opens
                continue
            if i in cont_from:
                target = self.continued_piece(cont_from[i], pno)
                if target is not None:
                    self.open = target
                    filler, filler_hard = None, False
                    target["flags"].append(f"continues on p.{pno} (notice: continued from page {cont_from[i]})") if f"continues on p.{pno}" not in " ".join(target["flags"]) else None
                else:
                    rec = self.new("other", page=pno, title=f"continued from page {cont_from[i]}")
                    rec["flags"].append(f"'continued from page {cont_from[i]}' but no record is open there")
                    self.open = rec
                if i not in info["furniture"]:
                    self.add(self.open, pno, i)  # inline notice plus narrative: retain the complete body box
                i += 1
                continue
            if i in cont_on and self.open is not None and self.open.get("type") not in ("ad", "other"):
                notice = region_text(regs[i])
                dest = CONT_TO_PAGE_RE.search(notice)
                next_page = re.search(r"(?i)continued\s+on\s+(?:the\s+)?next\s+page", notice)
                marker = dest or next_page
                prefix = norm_words(notice[:marker.start()]) if marker else ""
                own_titles = [self.open.get("title"), (self.open.get("toc") or {}).get("title")]
                own_notice = not prefix or any(prefix == norm_words(title) for title in own_titles if title)
                scan = self.A["scan_of"](int(dest.group(1))) if dest and own_notice and self.A.get("scan_of") else None
                if scan is None and own_notice and next_page:
                    scan = pno + 1
                if scan is not None and scan > pno:
                    self.continuations[scan] = self.open
                tail = True
            if i in info["furniture"]:
                i += 1
                continue
            r = regs[i]
            t = region_text(r)
            if (i in info.get("source_credits", []) and self.open is not None
                    and self.open.get("type") in ("story", "poem", "feature", "letters")):
                self.add(self.open, pno, i, "note")
                i += 1
                continue
            if (i in info.get("embedded_document_headings", []) and self.open is not None
                    and self.open.get("type") in ("story", "poem", "feature", "letters")):
                self.add(self.open, pno, i, "heading")
                i += 1
                continue
            if (self.open is not None and filler is None and not tail
                    and self.open.get("type") in ("story", "poem", "feature", "letters")
                    and THE_END_RE.fullmatch(t)):
                ended_piece = self.open
            editorial_intro = (r["label"] == "Text" and
                               re.search(r"(?is)^\s*in this department\b.{0,240}\bcorrespondence\b", t))
            if (ended_piece is self.open and ended_piece is not None
                    and filler is None and not tail and i not in title_of
                    and i not in starts and i not in at_of and i not in chapters
                    and ((r["label"] == "SectionHeader" and words(t) <= 8) or editorial_intro)):
                department = department_of(self.magazine_name(), t)
                # Some magazines have no department list yet. A Discussions
                # heading plus its own editorial introduction/salutation is
                # independent evidence of letters after the finished story.
                following = "\n".join(region_text(regs[j]) for j in range(i + 1, min(i + 5, n)))
                if (department is None and norm_words(t) == "discussions"
                        and re.search(r"(?im)^\s*(?:in this department\b|editor\s*,)", following)):
                    department = ("Discussions", "letters", None)
                if (department is None and not t
                        and re.search(r"(?is)^\s*in this department\b.{0,240}\bcorrespondence\b", following)):
                    department = (None, "letters", None)  # the drawn heading has no readable OCR
                if department is None and editorial_intro:
                    department = (None, "letters", None)
                if department is not None:
                    if self.suspended is ended_piece:
                        self.suspended = None
                    name, kind, author = department
                    self.open = self.new(kind, title=(t or None) if not editorial_intro else None, author=author, page=pno)
                    if name:
                        self.open["department"] = name
                    self.open["flags"].append(f"department after the preceding piece's end on p.{pno}")
                    self.add(self.open, pno, i, None if editorial_intro else "title")
                    ended_piece = None
                    i += 1
                    continue
            if self.open is not None and self.open.get("type") in ("story", "poem", "feature", "letters") and filler is None and not tail \
                    and i not in title_of and i not in starts and i not in at_of and i not in chapters and i not in cont_from:
                nxt_i = self.column_ad(pno, p, info, i)
                if nxt_i is not None:
                    i = nxt_i                                 # an advertisement column beside the piece's text: cut out
                    continue
            if tail and i not in title_of and i not in starts and i not in at_of and \
                    (filler is None or (is_display(p, r) and r["label"] == "SectionHeader")):
                # advertisement matter printed under the piece's "continued on" notice: one record per display header
                filler = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                filler["flags"].append(f"below the 'continued on' notice on p.{pno}")
                filler_hard = True
                self.add(filler, pno, i, "title")
                i += 1
                continue
            if i in title_of:
                s = title_of[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                    self.attach_pre(pno, p, s["_rec"], pre_new)
                    pre_new = []
                role = "title" if i in s["title_idx"] else ("teaser" if i in (s.get("teaser_idx") or []) else "subtitle")
                self.add(s["_rec"], pno, i, role)
                if role == "teaser":
                    s["_rec"]["teaser"] = t.replace("\n", " ")
                self.open = s["_rec"]
                filler, filler_hard, tail = None, False, False
                if s["byline_idx"] is None and i == max(s["title_idx"] + s["subtitle_idx"]):
                    self.teaser(pno, p, info, s, i + 1)
                i += 1
                continue
            if i in at_of:
                s = at_of[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                    self.attach_pre(pno, p, s["_rec"], pre_new)
                    pre_new = []
                self.open = s["_rec"]
                filler, filler_hard, tail = None, False, False
                # fall through: this region is the first paragraph of the piece
            if i in starts:
                s = starts[i]
                if s.get("_rec") is None:
                    self.begin(pno, p, info, s)
                    self.attach_pre(pno, p, s["_rec"], pre_new)
                    pre_new = []
                self.add(s["_rec"], pno, i, "author")
                if s.get("name_idx") is not None:
                    self.add(s["_rec"], pno, s["name_idx"], "author")
                self.open = s["_rec"]
                self.suspended = None
                filler, filler_hard, tail = None, False, False
                self.teaser(pno, p, info, s, i + 1)
                i += 1
                continue
            if self.open is None and self.suspended is not None and resume_allowed and \
                    any(j < min((start_first(s) for s in info["starts"]), default=n) and prose_like(regs[j])
                        for j in range(i, n) if j not in info["furniture"]):
                self.open = self.suspended                # the piece resumes after a full advertisement page
                self.open["flags"].append(f"resumes on p.{pno} after advertisement pages")
                self.suspended = None
            if self.open is None:
                # no record open: a feature page (editorial, a letter to the readers) or an advertisement
                rec = self.new("ad" if info["ad_score"] >= 3 and not info["running"] else "feature", page=pno)
                rec["title"] = self.page_title(p, info, i)
                rec["flags"].append("no by-line")
                self.open = rec
            if filler is not None and i in announce_at and not filler_hard:
                # a house announcement after a trade advertisement on the same page: its own record
                s = announce_at[i]
                filler = self.new("ad", title=(s.get("title") or "announcement").replace("\n", " ")[:120], page=pno)
                filler["flags"].append(f"announcement block on p.{pno} after another advertisement")
                filler_hard = True
                self.add(filler, pno, i, "title" if i in (s.get("title_idx") or []) else None)
                i += 1
                continue
            if filler is not None and i in forms:
                # the coupon closes the advertisement; the piece may resume after it
                for j in forms[i]:
                    self.add(filler, pno, j)
                i = forms[i][-1] + 1
                filler_hard = False
                continue
            if filler is not None and r["label"] == "SectionHeader" and words(t) <= 12 and \
                    (filler_hard or (i in fillers and is_display(p, r))) and \
                    not any(i in (s_.get("title_idx") or []) for s_ in announce_at.values()) and \
                    not any(k_ in (s_.get("title_idx") or []) for s_ in announce_at.values() for k_ in range(i + 1, min(i + 3, n))):
                # a headline inside advertising matter: the next advertisement on the page — unless the block so
                # far is only a stacked headline ("NEXT MONTH" / "—The—" / "Tenants of Broussac")
                stacked = (len(filler["keys"]) <= 3 and all(words(region_text(pages_[pn_]["regions"][j_])) <= 3 for pn_, j_ in filler["keys"])
                           and not re.search(r"\d", filler["title"] or ""))
                if stacked:
                    filler["title"] = (filler["title"] + " " + t.replace("\n", " "))[:120]
                    self.add(filler, pno, i, "title")
                else:
                    filler = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                    filler["flags"].append(f"advertisement after another on p.{pno}")
                    self.add(filler, pno, i, "title")
                    filler_hard = False
                i += 1
                continue
            if filler is not None:
                prose = (words(t) >= 40 and not AD_WORDS.search(t) and r["label"] == "Text"
                         and (t[:1].islower() or (words(t) >= 60 and prose_like(r))))
                piece_open = self.open is not None and self.open.get("type") not in ("ad", "other")
                # the filler keeps to the column it stands in (v2.3): a paragraph in the other column is the piece's
                fcols = {column_of(p, regs[j]) for pn_, j in filler["keys"] if pn_ == pno}
                c = column_of(p, r)
                other_col = (c != "span" and fcols and c not in fcols and "span" not in fcols and r["label"] == "Text"
                             and words(t) >= 8 and not (AD_WORDS.search(t) or PRICE_RE.search(t) or POSTAL_RE.search(t)))
                if piece_open and (other_col or (prose and (not filler_hard or t[:1].islower()))):
                    filler, filler_hard = None, False              # the piece's column resumes beside the advertisement
                else:
                    self.add(filler, pno, i)
                    i += 1
                    continue
            if i in forms and self.open["type"] not in ("ad", "other"):
                # a ballot or coupon inside a piece, never text of the piece: the magazine's own ballot is its
                # own record (house_form); a trade coupon joins the advertisement printed above it
                blk = forms[i]
                btxt = " ".join(region_text(regs[j]) for j in blk)
                own_names = [x for x in (self.magazine_name(), self.A.get("magazine_head") or "") if x]
                house = bool(re.search(r"ballot|favou?rite stor|the editor|readers?['’]? (?:vote|choice|opinion)", btxt, re.I)
                             or any(re.search(re.escape(x), btxt, re.I) for x in own_names))
                prev = next((self.owner.get(key(pno, j)) for j in range(i - 1, -1, -1) if j not in info["furniture"]), None)
                prev_rec = next((r_ for r_ in self.records if r_["article_id"] == prev), None) if prev else None
                if house:
                    rec = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                    rec["ad_class"] = "house_form"
                    rec["flags"].append(f"ballot or coupon on p.{pno}, kept apart from the piece around it")
                elif prev_rec is not None and prev_rec["type"] == "ad":
                    rec = prev_rec                                 # the coupon of the advertisement above it
                else:
                    rec = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)
                    rec["flags"].append(f"coupon on p.{pno} with no advertisement read above it")
                for j in blk:
                    self.add(rec, pno, j, "title" if (j == i and rec is not prev_rec) else None)
                i = blk[-1] + 1
                continue
            if i in announce_at and self.open["type"] in ("story", "poem", "letters", "feature"):
                # an announcement block ("in the next issue": title, by-line, blurb, often a quoted excerpt)
                # with no filler header on this page: house advertising to the end of the page
                s = announce_at[i]
                filler = self.new("ad", title=(s.get("title") or "announcement").replace("\n", " ")[:120], page=pno)
                filler["flags"].append(f"announcement block on p.{pno} (a rejected start)")
                filler_hard = True
                # the short display lines right above the title belong to the block ("AN ACE INVESTIGATOR PLUNGES
                # INTO …" / "IN" / "THE WRONG CORPSE"), back to the piece's last paragraph
                for j in range(i - 1, -1, -1):
                    if j in info["furniture"]:
                        continue
                    tj = region_text(regs[j])
                    if self.owner.get(key(pno, j)) == self.open["article_id"] and words(tj) <= 20 and \
                            (regs[j]["label"] == "SectionHeader" or (tj.upper() == tj and not tj[:1] in "\"\u201c'\u2018")) and \
                            self.open["roles"].get(key(pno, j)) is None:
                        self.steal(filler, pno, j)
                    else:
                        break
                self.add(filler, pno, i, "title" if i in (s.get("title_idx") or []) else None)
                i += 1
                continue
            house_head = words(t) <= 6 and (department_of(self.magazine_name(), t) or (None, None, None))[1] == "house"
            if (i in fillers or house_head) and self.open["type"] in ("story", "poem", "letters", "feature"):
                kind = None if t[:1] in "\"\u201c'\u2018" else ("ad" if house_head else self.filler_kind(pno, p, info, i))
                if kind is None and t[:1] in "\"\u201c'\u2018":
                    self.add(self.open, pno, i, "caption")           # a pull quote set in display type
                    i += 1
                    continue
                if kind:
                    filler = self.new(kind, title=t.replace("\n", " ")[:120], page=pno)
                    filler["flags"].append(f"filler after the story text on p.{pno}")
                    self.add(filler, pno, i, "title")
                    # a house announcement followed by announcement by-lines, or by another notice ("in the
                    # December issue of"), on this page: the block, with the blurb or excerpt it carries, runs on
                    filler_hard = bool(FILLER_RE.search(t)) and (any(j > i for j in announce_at) or
                                                                 any(j > i and j not in info["furniture"] and words(region_text(regs[j])) <= 12
                                                                     and FILLER_RE.search(region_text(regs[j])) for j in range(i + 1, n)))
                    i += 1
                    continue
                self.add(self.open, pno, i, self.heading_role(t))    # a display line inside the piece
                i += 1
                continue
            if i in chapters:
                self.add(self.open, pno, i, "chapter")
            elif CREDIT_RE.match(t) and words(t) <= 30 and self.open.get("type") not in ("ad", "other") and \
                    (not self.open["keys"] or self.open["keys"][0][0] == pno) and not self.open.get("author_credit"):
                self.credit(self.open, pno, i)                # the credit line on the piece's first page
            elif ILLUS_RE.match(t) and words(t) <= 30 and self.open.get("type") not in ("ad", "other"):
                self.illustrator(self.open, pno, i)           # "Illustrated by …" wherever the layout put it
            elif r["label"] == "Caption":
                self.add(self.open, pno, i, "caption")
            elif r["label"] == "SectionHeader" and words(t) <= 12 and self.open.get("type") in ("letters", "feature") and \
                    self.filler_kind(pno, p, info, i, strict=True) == "ad":
                filler = self.new("ad", title=t.replace("\n", " ")[:120], page=pno)   # an advertisement set in the department's type
                filler["flags"].append(f"advertisement inside the department on p.{pno} (selling words under a plain heading)")
                self.add(filler, pno, i, "title")
            elif r["label"] == "SectionHeader" and words(t) <= 12 and self.open.get("type") not in ("ad", "other"):
                self.add(self.open, pno, i, self.heading_role(t))    # a subheading inside the piece (a letter's title in The Eyrie)
            else:
                self.add(self.open, pno, i)
            i += 1
        for j in pre_new:
            if key(pno, j) not in self.owner:
                self.furn(pno, j)
                self.furniture[-1]["why"] = "above the title on a start page"
        if self.open is not None and any(dest > pno and rec is self.open for dest, rec in self.continuations.items()):
            self.suspended = self.open
            self.open = None
        # the running head names the piece the page belongs to; a mismatch is worth a flag
        if self.open and self.open.get("type") in ("story",) and info["running"]:
            title = self.open.get("title") or ""
            toc_title = (self.open.get("toc") or {}).get("title") or ""
            names = [h for h in info["running"] if words(h) <= 10 and sim(h, self.magazine_name()) < 0.6]
            if names and title and max(max(sim(h, title), sim(h, toc_title)) for h in names) < 0.35:
                self.open.setdefault("_head_mismatch", []).append(pno)

    def attach_pre(self, pno, p, rec, pre_new):
        """Regions printed above the title on a piece's first page, with the
        roles the annotators use (Sujin's records of 2026-09-03): a second
        setting of the title is title; the blurb is teaser (the record's
        teaser field keeps the first); a short display line ("A Complete
        Novelette", a kicker) is subtitle; a synopsis heading such as "The
        Story Thus Far" is heading (it stays in the reading text); the credit
        line is CREDIT_ROLE and author_credit; the illustration's caption is
        caption. None of these but the heading is reading text."""
        regs = p["regions"]
        end_re = re.compile(r"[.!?\"\u201d\u2019']\s*[)\]]?\s*$")
        names = [x for x in (rec.get("title"), (rec.get("toc") or {}).get("title"), rec.get("title_as_printed")) if x]
        for j in pre_new:
            if key(pno, j) in self.owner:
                continue
            r = regs[j]
            t = region_text(r)
            w = words(t)
            if CREDIT_RE.match(t) and w <= 30:
                self.credit(rec, pno, j)
            elif ILLUS_RE.match(t) and w <= 30:
                self.illustrator(rec, pno, j)
            elif r["label"] == "Caption" or (t[:1] in "\"\u201c'\u2018" and w <= 40):
                self.add(rec, pno, j, "teaser")               # the caption or pull-quote on the start page
                if not rec.get("teaser"):
                    rec["teaser"] = t.replace("\n", " ")
            elif w <= 8 and TYPE_LABEL_RE.match(t):
                self.add(rec, pno, j, "subtitle")
                rec["subtitle"] = (rec.get("subtitle") + " " + t if rec.get("subtitle") else t)
            elif (any(sim(t, nt) > 0.6 for nt in names) or any(title_prefix(t, nt) for nt in names)) and w <= 12:
                self.add(rec, pno, j, "title")                # the title set a second time, or its first words in their own box
            elif SYNOPSIS_RE.match(t) and w <= 12:
                self.add(rec, pno, j, "heading")              # "The Story Thus Far": a heading of the synopsis
            elif w < 8 and (r["label"] == "SectionHeader" or is_display(p, r) or not end_re.search(t)):
                self.add(rec, pno, j, "subtitle")             # a kicker or a type label in display type
            elif w < 8:
                self.add(rec, pno, j, "caption")              # a short sentence with a full stop: the caption
            elif w <= 80:
                self.add(rec, pno, j, "teaser")               # the blurb
                if not rec.get("teaser"):
                    rec["teaser"] = t.replace("\n", " ")
            else:
                self.add(rec, pno, j)

    def heading_role(self, t):
        """The role of a display line inside a piece that is not a chapter
        head: heading (the site's "section") inside a department — a letter's
        title in The Eyrie, a topic in a feature — and for a synopsis heading;
        inside a story it is reading text (Sujin's corrections of 2026-09-04 on
        The Demolished Man: "Consternation", "ABOLISH:" are the author's
        typography, not sections)."""
        if SYNOPSIS_RE.match(t) or (self.open or {}).get("type") in ("letters", "feature") or \
                re.match(r"^\W*(?:foreword|prologue|epilogue|afterword|interlude|postscript|introduction)\b", t, re.I):
            return "heading"
        return None

    def illustrator(self, rec, pno, j):
        """"Illustrated by WILLER": a note (paratext), never story text; the name
        is kept as illustrator."""
        self.take(rec, pno, j, "note")
        t = " ".join(region_text(self.pages[pno]["regions"][j]).split())
        m = ILLUS_RE.match(t)
        if m and not rec.get("illustrator"):
            rec["illustrator"] = m.group(1).strip(" .")

    def credit(self, rec, pno, j):
        """The credit line under a by-line ("Author of 'The Jailer of Souls,'
        etc."): CREDIT_ROLE on the workbench, never reading text; the text is
        kept as author_credit."""
        self.take(rec, pno, j, CREDIT_ROLE)
        t = " ".join(region_text(self.pages[pno]["regions"][j]).split())
        rec["author_credit"] = (rec["author_credit"] + " " + t) if rec.get("author_credit") else t

    def continued_piece(self, printed, pno):
        """The record that paused at printed page `printed` ('continued on page
        N' there, or simply the last piece owning regions on that page)."""
        if self.A["offset"] is None:
            return None
        scan = self.A["scan_of"](printed)
        cands = [r for r in self.records if r["type"] not in ("ad", "toc", "other") and any(pn == scan for pn, _ in r["keys"])]
        if not cands:
            cands = [r for r in self.records if r["type"] not in ("ad", "toc", "other") and any(pn in (scan - 1, scan + 1) for pn, _ in r["keys"])]
        if not cands:
            return None
        # the piece that paused there: the one whose last region on that page is last in reading order
        return max(cands, key=lambda r: max((i for pn, i in r["keys"] if pn == scan), default=-1))

    def column_ad(self, pno, p, info, i):
        """An advertisement column beside the piece's text (Weird Tales 1925-11,
        pp. 139–141: the Rosicrucian and birth-control advertisements in the
        right column while The Return of the Undead runs in the left; Thrilling
        Detective 1948-12, pp. 100–110: a column of small advertisements beside
        every continuation page). v2.3: the column is the page half the box
        stands in (column_of), the block runs from region i down that column
        to the next display headline, a paragraph of the piece's prose, or the
        end of the page — whether or not the piece has prose on the page yet
        (the advertisements often come first in reading order) — and it is an
        advertisement when it carries selling signs (prices, addresses, selling
        words, a company) and opens with a headline or a priced line; the piece
        must have prose in the OTHER column of the page (or on the page before,
        when this column has none). Inside the block a headline that follows a
        paragraph starts the next advertisement. Returns the index after the
        block, or None."""
        regs = p["regions"]
        r = regs[i]
        x0, _, x1, _ = r["bbox"]
        t0 = region_text(r)
        if x1 - x0 < 0.15 * p["width"] and not (COMPANY_RE.search(t0) or re.search(r"(?i)copyright,? \d{4}", t0)):
            return None
        c = column_of(p, r)
        if c == "span":
            return None
        # where the piece's prose stands on this page: a column with narrative paragraphs is the piece's
        prose_cols = {column_of(p, rr) for j, rr in enumerate(regs) if j not in info["furniture"] and prose_like(rr)}
        prose_cols |= {column_of(p, regs[j]) for pn_, j in self.open["keys"] if pn_ == pno and words(region_text(regs[j])) >= 6}
        if c in prose_cols:
            return None                                       # the piece's own column: the filler rules decide
        if not prose_cols:
            return None                                       # the piece has no prose on this page: not a shared page
        block = [i]
        sig_re = re.compile(r"(?i)copyright,? \d{4}")
        for j in range(i + 1, len(regs)):
            if j in info["furniture"] or key(pno, j) in self.owner:
                continue
            rj = regs[j]
            if column_of(p, rj) != c:
                break
            tj = region_text(rj)
            if prose_like(rj) or (rj["label"] == "Text" and tj[:1].islower() and words(tj) >= 20
                                  and not (AD_WORDS.search(tj) or PRICE_RE.search(tj) or POSTAL_RE.search(tj))):
                break                                         # the piece's prose: the column is shared
            # the tail of a picture advertisement above the column's first headline (a copyright line, a
            # company, a slogan in capitals — Thrilling Detective 1948-12 p.109: "Copyright 1948 Billerie
            # Rubber Co." / "FOREVER FIRST WITH FIRST QUALITY" over "HOME-STUDY BRINGS BIGGER PAY"): its
            # own block, so that it does not stay with the story
            lead = [region_text(regs[b]) for b in block]
            if len(block) <= 3 and all(words(t_) <= 8 for t_ in lead) and \
                    any(COMPANY_RE.search(t_) or sig_re.search(t_) for t_ in lead) and \
                    (rj["label"] == "SectionHeader" or is_display(p, rj)) and words(tj) <= 10:
                break
            block.append(j)
        texts = [region_text(regs[j]) for j in block]
        txt = "\n".join(texts)
        quoted = sum(1 for t_ in texts if t_.lstrip()[:1] in "\"\u201c'\u2018")
        if quoted >= max(2, 0.4 * len(texts)):
            return None                                       # dialogue: the story's own second column
        for t_ in texts:
            if words(t_) >= 40 and sum(1 for w in re.findall(r"[a-z']+", t_.lower()) if w in NARRATIVE) >= 3 \
                    and not (PRICE_RE.search(t_) or ADDRESS_RE.search(t_)):
                return None                                   # narrative prose in that column
        addr = len(POSTAL_RE.findall(txt))
        prices = len(PRICE_RE.findall(txt))
        hard = prices + 2 * addr + (1 if COMPANY_RE.search(txt) else 0)
        soft = len([w for w in AD_WORDS.findall(txt) if not w.startswith("$")]) + len(re.findall(r"(?i)copyright,? \d{4}|\btrade.?mark", txt))
        head_t = region_text(r).strip()
        display_head = (r["label"] == "SectionHeader" and is_display(p, r)) or (is_display(p, r) and head_t.upper() == head_t and words(head_t) <= 8)
        first_signed = bool(PRICE_RE.search(texts[0]) or POSTAL_RE.search(texts[0]) or
                            (words(texts[0]) <= 60 and len(AD_WORDS.findall(texts[0])) >= 2))
        # a signature block: a few short lines with a company or a copyright line (the tail of a picture
        # advertisement whose headline is drawn in the picture)
        signature = (len(block) <= 3 and all(words(t_) <= 8 for t_ in texts) and hard + soft >= 2 and
                     (COMPANY_RE.search(txt) or re.search(r"(?i)copyright,? \d{4}", txt)))
        if not (display_head or first_signed or signature):
            return None                                       # an advertisement block opens with a headline or a priced line
        if not ((hard >= 1 and hard + soft >= 3) or (addr >= 1 and hard + soft >= 2) or signature):
            return None
        own_names = [x for x in (self.magazine_name(), self.A.get("magazine_head") or "") if x]
        if any(re.search(re.escape(x[:16]), txt, re.I) for x in own_names):
            return None                                       # the department's own notice names the magazine
        if self.open.get("type") in ("feature", "letters") and not prices:
            return None                                       # inside a department only a priced block is trade advertising
        # one record per advertisement: a headline (display size, at most eight words) that follows a
        # paragraph of copy opens the next one; the previous column advertisement on this page continues
        # when the block has no headline of its own
        prev = next((rec for rec in reversed(self.records) if rec["type"] == "ad" and rec.get("_column_ad") == pno), None)
        rec = None
        closed = False                # the previous line closed an advertisement: an address, a price, a paragraph of copy
        # a signature block is titled by its slogan (the line in capitals), not by the copyright line
        sig_title = next((t_ for t_ in texts if t_.upper() == t_ and words(t_) >= 2 and not sig_re.search(t_)), None) if signature else None
        for j in block:
            rj = regs[j]
            tj = region_text(rj)
            closing = bool(POSTAL_RE.search(tj) or COMPANY_RE.search(tj) or PRICE_RE.search(tj) or ADDRESS_RE.search(tj) or CLOSING_RE.search(tj))
            headline = (rj["label"] == "SectionHeader" or is_display(p, rj)) and words(tj) <= 10 and not closing
            if rec is None:
                if prev is not None and not headline and not signature:
                    rec = prev
                else:
                    rec = self.new("ad", title=(sig_title or tj).replace("\n", " ")[:120], page=pno)
                    rec["flags"].append(f"advertisement column beside the text on p.{pno}" +
                                        (" (the tail of a picture advertisement: a copyright line, a company, a slogan)" if signature else ""))
                    rec["_column_ad"] = pno
                    self.add(rec, pno, j, "title" if (not sig_title or tj == sig_title) else None)
                    closed = False
                    continue
            elif headline and closed:
                rec = self.new("ad", title=tj.replace("\n", " ")[:120], page=pno)
                rec["flags"].append(f"advertisement column beside the text on p.{pno} (the next headline)")
                rec["_column_ad"] = pno
                self.add(rec, pno, j, "title")
                closed = False
                continue
            self.add(rec, pno, j, "title" if (sig_title and tj == sig_title) else None)
            closed = bool(words(tj) >= 25 or closing)
        return block[-1] + 1

    def filler_kind(self, pno, p, info, i, strict=False):
        """A display header inside a story page: a filler (its own record) when
        it reads like one — the magazine's name, 'in the next issue', an
        advertisement — else None (a headline inside the story). strict: only
        the two-selling-word test (for plain subheadings inside departments)."""
        regs = p["regions"]
        t = region_text(regs[i])
        rest = [j for j in range(i, len(regs)) if j not in info["furniture"]]
        near = " ".join(region_text(regs[j]) for j in rest[:4])
        # the block: from this header to where story prose resumes (a paragraph that starts mid-sentence,
        # or a long one without advertisement words)
        block_idx = []
        for j in rest:
            tj = region_text(regs[j])
            if j != i and words(tj) >= 40 and not AD_WORDS.search(tj) and regs[j]["label"] == "Text" and (tj[:1].islower() or words(tj) >= 60):
                break
            if strict and j != i and (j in (info.get("forms") or {}) or j in info["fillers"]):
                break                                     # a ballot/coupon or the next display header is its own block
            block_idx.append(j)
        block = " ".join(region_text(regs[j]) for j in block_idx)
        n_ad = len(AD_WORDS.findall(block))
        if strict:
            return "ad" if (n_ad >= 2 and not any(prose_like(regs[j]) for j in block_idx if j != i)) else None
        if sim(t, self.magazine_name()) > 0.6 or sim(t, self.A.get("magazine_head", "")) > 0.6:
            return "ad"
        if FILLER_RE.search(t) or FILLER_RE.search(near[:200]):
            return "ad"                                   # house copy: an ad of class house_next_issue / house_self
        if n_ad >= 2:
            return "ad"
        if n_ad >= 1 and len(block_idx) >= 2 and not any(prose_like(regs[j]) for j in block_idx if j != i) \
                and re.search(r"free (?:offer|trial|book|sample|booklet|details|information|catalog)|\boffer\b|\bsend\b|"
                              r"get yours|order now|yours now|\$|only \d|price|postpaid|c\. ?o\. ?d\.", block, re.I):
            return "ad"                                   # a small trade advertisement with one selling word
        return None

    def magazine_name(self):
        return self.A.get("magazine") or self.A.get("magazine_head") or ""

    def page_title(self, p, info, start_i):
        regs = p["regions"]
        parts = []
        for j in range(start_i, len(regs)):
            if j in info["furniture"]:
                continue
            if is_display(p, regs[j]):
                parts.append(region_text(regs[j]).replace("\n", " "))
                if len(parts) == 2:
                    break
            elif parts:
                break
        return " ".join(parts)[:120] if parts else None

    def begin(self, pno, p, info, s):
        """Open a new record at a title/by-line. Regions before the title on
        this page that nobody owns yet: short ones are furniture (the
        illustration's caption, the artist's signature, display garble), as
        the verified records treat them; long ones are the end of the open
        record."""
        regs = p["regions"]
        toc = s.get("toc")
        page_title = s["title"] or None
        page_author = s["author"] or None
        # the contents page has authority over title and author (decision of 2026-09-03); the page's own
        # forms are kept beside them, and a strong disagreement is flagged for a person to look at
        title = (toc["title"] if toc and toc.get("title") else page_title)
        author = (toc["author"] if toc and toc.get("author") else page_author)

        def same_words_better_case(chosen, other):
            # the same words in both places: keep the form set in upper and lower case (the contents
            # page is often all capitals) — authority is about the words, not the type case
            if chosen and other and norm(chosen) == norm(other) and chosen.upper() == chosen and other.upper() != other:
                return other
            return chosen
        title = same_words_better_case(title, page_title)
        author = same_words_better_case(author, page_author)
        sub = " ".join(region_text(regs[j]) for j in s["subtitle_idx"])
        typ = (toc or {}).get("type") or "story"
        if VERSE_RE.search(sub):
            typ = "poem"
        # a serial instalment is a story with serial fields (decision of 2026-09-04, from Sujin's note of
        # 3 September): the instalment marker leaves the title for part_label / part_n / part_total, the
        # title without it is the work's; "(Poem)" in a title makes a poem
        raw_title = title
        serial = None
        title, poem_mark = strip_poem_mark(title or "")
        if poem_mark:
            typ = "poem"
        work, label, part_n, part_total = split_part(title)
        if label:
            serial = {"part_label": label, "part_n": part_n, "part_total": part_total, "source": "contents" if toc and toc.get("title") else "head"}
            title = work
        elif typ == "serial_part" or SERIAL_RE.search(sub) or (typ == "story" and toc and SERIAL_RE.search((toc.get("blurb") or "")[:80])):
            m_lab = re.search(r"(?i)\b(part (?:one|two|three|four|five|six|i{1,3}|iv|v)|conclusion|concluded|beginning)\b", sub + " " + ((toc or {}).get("blurb") or "")[:80])
            lab = m_lab.group(1) if m_lab else None
            n = roman_int(re.sub(r"(?i)^part\s+", "", lab)) if lab and lab.lower().startswith("part") else (1 if lab and lab.lower() == "beginning" else None)
            serial = {"part_label": lab, "part_n": n, "part_total": None, "source": "contents" if (toc and toc.get("type") == "serial_part") else "head"}
        if serial and toc and toc.get("part_total") and not serial.get("part_total"):
            serial["part_total"] = toc["part_total"]        # 'In Five Parts' on the contents page (rules 2.9)
            serial["part_total_source"] = "the contents page"
        if typ == "serial_part":
            typ = "story"
        if title and title.upper() == title:
            title = title_case(title)                      # the printed capitals are kept as title_as_printed
        rec = self.new(typ, title=title, author=author, page=pno,
                       toc=({k: toc.get(k) for k in ("title", "author", "page", "type", "blurb", "true_story", "unsigned_story") if k in toc} if toc else None))
        if s.get("facing_title"):
            facing = s["facing_title"]
            self.steal(rec, facing["page"], facing["idx"], "title")
            rec["flags"].append(f"title-only facing page {facing['page']} joined to its printed by-line")
        if serial:
            rec["serial"] = serial
            rec["work_title"] = title
        if sub:
            rec["subtitle"] = re.sub(r"\s+", " ", sub)
        if page_title and norm(page_title) != norm(title or ""):
            rec["title_as_printed"] = page_title
        elif raw_title and norm(raw_title) != norm(title or ""):
            rec["title_as_printed"] = raw_title
        if page_author and norm(page_author) != norm(author or ""):
            rec["author_as_printed"] = page_author
        if toc and toc.get("title"):
            rec["title_source"] = "contents"
            if page_title and sim(toc["title"], page_title) < 0.55:
                rec["flags"].append(f"title from the contents page; the page prints '{page_title}'")
        elif page_title:
            rec["title_source"] = "page"
        if toc and toc.get("author"):
            rec["author_source"] = "contents"
            if page_author and sim(toc["author"], page_author) < 0.6:
                rec["flags"].append(f"author from the contents page; the page prints '{page_author}'")
        elif page_author:
            rec["author_source"] = "page"
        if not page_title and toc:
            rec["flags"].append("title taken from the contents page (no readable title on the page)")
        if not toc and self.A["toc"]:
            rec["flags"].append("not on the contents page")
        if s.get("from_contents"):
            rec["flags"].append("start placed from the contents page (no by-line read on the page)")
        s["_rec"] = rec
        self.open = rec

    def teaser(self, pno, p, info, s, from_i):
        """The printed blurb between the by-line and the body: short, centred
        or full width, in sentence case (the body's first word is set in
        capitals). Tagged, never story text; confirmed against the contents
        page's blurb when there is one."""
        regs = p["regions"]
        W = p["width"]
        # the credit line under the by-line ("Author of …"), before anything else: the next two
        # content regions, whether or not a blurb was already taken above the title
        seen = 0
        for j in range(from_i, len(regs)):
            if j in info["furniture"] or key(pno, j) in self.owner:
                continue
            t = region_text(regs[j])
            if CREDIT_RE.match(t) and words(t) <= 30:
                self.credit(s["_rec"], pno, j)
                continue
            if ILLUS_RE.match(t) and words(t) <= 30:
                self.illustrator(s["_rec"], pno, j)
                continue
            seen += 1
            if seen >= 2:
                break
        if s["_rec"].get("type") == "poem":
            return                                          # a short centred verse is reading text, not a guessed blurb
        if s["_rec"].get("teaser"):
            return                                          # the blurb above the title was already taken
        blurb = ((s.get("toc") or {}).get("blurb") or "")
        for j in s.get("teaser_idx") or []:
            if key(pno, j) not in self.owner:
                self.add(s["_rec"], pno, j, "teaser")
                s["_rec"]["teaser"] = region_text(regs[j]).replace("\n", " ")
                s["_rec"]["teaser_matches_contents"] = round(sim(region_text(regs[j]), blurb), 2) if blurb else None
                return
        best = None
        for j in range(from_i, len(regs)):
            if j in info["furniture"] or j in info["chapters"] or key(pno, j) in self.owner:
                continue
            r = regs[j]
            t = region_text(r)
            x0, y0, x1, y1 = r["bbox"]
            w = words(t)
            score = sim(t, blurb) if blurb else 0
            narrative = sum(w_ in NARRATIVE for w_ in re.findall(r"[a-z']+", t.lower())) >= 2
            if r["label"] != "Caption" and score < .6 and \
                    ((w > 40 and narrative) or (w >= 25 and y1 - y0 > .24 * p["height"])):
                break                                        # body prose need not start with a drop cap
            if w > 80 or w < 6 or r["label"] == "SectionHeader":
                continue
            fw = t.split()[0]
            if len(re.sub(r"[^A-Za-z]", "", fw)) >= 3 and fw.upper() == fw:
                continue                                     # "WHAT caused you…": the body's first paragraph
            centred = abs((x0 + x1) / 2 - W / 2) < 0.15 * W and (x1 - x0) < 0.62 * W
            wide = (x1 - x0) > 0.5 * W
            if not (centred or wide):
                continue
            if best is None or score > best[0]:
                best = (score, j)
            if not blurb:
                break
        if best is not None:
            j = best[1]
            self.add(s["_rec"], pno, j, "teaser")
            s["_rec"]["teaser"] = region_text(regs[j]).replace("\n", " ")
            s["_rec"]["teaser_matches_contents"] = round(best[0], 2) if blurb else None


def assemble(iid, pages, A, variant="rules", model=None, kind="magazine", book=None):
    asm = Assembler(iid, pages, A).run()
    records = asm.records
    book_summary = None
    if kind == "book":                                 # 21 Oct 2026: an item of kind "book" is read as a book (s08_book)
        import s08_book
        records, book_summary = s08_book.book_mode(iid, records, A, (book or {}).get("title") or A.get("magazine"), (book or {}).get("author"), pages)
    if variant == "rules_on_model" and model:
        lo, hi = A.get("content_range") or A["folio_range"] or [min(pages), max(pages)]
        keep = [r for r in records if any(lo <= pn <= hi for pn in r["pages"]) or r["type"] == "toc"]
        owned = {key(f["page"], i) for r in keep for f in r["fragments"] for i in f["region_ids"]}
        n = len(keep)
        for a in model.get("articles", []):
            if all((pn < lo or pn > hi) for pn in a.get("pages", [])) and a.get("pages"):
                ks = [key(f["page"], i) for f in a.get("fragments", []) for i in f["region_ids"]]
                if any(k in owned for k in ks):
                    continue
                n += 1
                b = dict(a)
                b["article_id"] = f"{iid}_a{n:03d}"
                b["roles"] = {}
                b["flags"] = ["from the model (page outside the printed range)"]
                b["n_regions"] = len(ks)
                keep.append(b)
                owned.update(ks)
        keep.sort(key=lambda r: (r["pages"][0] if r["pages"] else 0, r["article_id"]))
        for k, r in enumerate(keep, 1):
            r["article_id"] = f"{iid}_a{k:03d}"
        records = keep
    # coverage check
    owned = Counter()
    for r in records:
        for f in r["fragments"]:
            for i in f["region_ids"]:
                owned[key(f["page"], i)] += 1
    furn = {key(f["page"], f["idx"]) for f in asm.furniture}
    unassigned = []
    for pno, p in pages.items():
        for i, r in enumerate(p["regions"]):
            k = key(pno, i)
            if k not in owned and k not in furn and region_text(r):
                unassigned.append(k)
    double = [k for k, c in owned.items() if c > 1]
    return {"issue": iid, "backend": f"rules_v{VERSION}" if variant == "rules" else f"rules_v{VERSION}+model",
            "variant": variant, "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "articles": records,
            **({"kind": "book", "book": dict(book or {}, **(book_summary or {}))} if kind == "book" else {}),
            "furniture": asm.furniture, "unsorted": [{"key": k} for k in unassigned],
            "checks": {"unassigned_regions": len(unassigned), "double_owned_regions": len(double),
                       "records": len(records), "story_records": sum(1 for r in records if r["type"] == "story"),
                       "records_starting_with_chapter_head": sum(1 for r in records if r["type"] == "story" and r["fragments"] and
                                                                 r["roles"].get(key(r["fragments"][0]["page"], r["fragments"][0]["region_ids"][0])) == "chapter"),
                       "flags": sum(len(r["flags"]) for r in records),
                       "ad_classes": dict(Counter(r.get("ad_class") or "?" for r in records if r["type"] == "ad")),
                       "house_excerpts": sum(1 for r in records if r.get("contains_excerpt"))}}


def issue_kind(iid, cfg_issue=None):
    """('magazine', None) or ('book', {title, author}) for an item: the kind from the issue dict (a trial) or
    config/corpus_kinds.json; a book's title and author from the archive's record and the selection's name."""
    kind = (cfg_issue or {}).get("kind")
    if not kind:
        try:
            from corpus_lib import corpus_kinds
            kind = corpus_kinds().get(iid, "magazine")
        except Exception:
            kind = "magazine"
    if kind != "book":
        return "magazine", None
    import s08_book
    meta = {}
    try:
        meta = json.load(open(os.path.join(ROOT, "data", "raw", iid, "meta.json"), encoding="utf-8")).get("metadata") or {}
    except (OSError, ValueError):
        pass
    creator = meta.get("creator")
    creator = creator[0] if isinstance(creator, list) and creator else (creator if isinstance(creator, str) else None)
    title, author = s08_book.book_title_author((cfg_issue or {}).get("magazine"), meta.get("title") if isinstance(meta.get("title"), str) else None, creator)
    return "book", {"title": title, "author": author}


def run_issue(iid, cfg_issue=None, log=print):
    pages = load_pages(iid)
    if not pages:
        log(f"[s08] {iid}: no layout pages")
        return None
    A = analyse(pages, force_scan=toc_page_override(iid), magazine=(cfg_issue or {}).get("magazine", ""))
    A["magazine"] = (cfg_issue or {}).get("magazine", "")
    model_path = os.path.join(ROOT, "data", "articles", iid, "articles.json")
    model = json.load(open(model_path, encoding="utf-8")) if os.path.exists(model_path) else None
    kind, book = issue_kind(iid, cfg_issue)
    outs = {}
    for variant in ("rules", "rules_on_model"):
        if variant == "rules_on_model" and not model:
            continue
        out = assemble(iid, pages, A, variant, model, kind=kind, book=book)
        d = os.path.join(ROOT, "data", "assembly_v2", variant, iid)
        os.makedirs(d, exist_ok=True)
        write_json_atomic(os.path.join(d, "articles.json"), out)
        outs[variant] = out
    for e in A["toc"]:
        e["scan"] = e.get("scan_linked") or e.get("scan_page") or (A["scan_of"](e["page"]) if A.get("scan_of") and A["offset"] is not None else None)
        if e["scan"] not in pages:
            e["scan"] = None                      # a page number the OCR garbled and no start to link it to
    an = {"issue": iid, "offset": A["offset"], "folio_range": A["folio_range"], "content_range": A.get("content_range"),
          "folio_index": {str(k): v for k, v in A.get("folio_index", {}).items()},
          "folio_conflicts": A.get("folio_conflicts", {}),
          "magazine_head": A.get("magazine_head"), "toc_page": A["toc_page"], "toc": A["toc"],
          "toc_pages": A.get("toc_pages"), "toc_kind": A.get("toc_kind"), "toc_v26": A.get("toc_v26"),
          "toc_page_v26": A.get("toc_page_v26"), "offset_v26": A.get("offset_v26"), "folio_range_v26": A.get("folio_range_v26"),
          "folio_segments": A.get("folio_segments"), "folio_misplaced": {str(k): v for k, v in (A.get("folio_misplaced") or {}).items()},
          "toc_chapters": A.get("toc_chapters"), "toc_folio_error": A.get("toc_folio_error"),
          "pages": {str(k): {kk: vv for kk, vv in v.items() if kk != "toc_entries"} for k, v in A["pages"].items()}}
    for v in an["pages"].values():
        v["starts"] = [{k: s[k] for k in ("title", "author", "title_idx", "byline_idx")} for s in v["starts"]]
    write_json_atomic(os.path.join(ROOT, "data", "assembly_v2", "rules", iid, "analysis.json"), an)
    o = outs["rules"]
    log(f"[s08] {iid}: offset {A['offset']}, printed range {A['folio_range']}, contents entries {len(A['toc'])}, "
        f"{o['checks']['story_records']} story records of {o['checks']['records']}, unassigned {o['checks']['unassigned_regions']}, "
        f"chapter-head starts {o['checks']['records_starting_with_chapter_head']}")
    return outs


def selftest():
    assert chapter_head("CHAPTER II") and chapter_head("Chapter 3") and chapter_head("IV") and not chapter_head("The Beetle Horde")
    assert byline_author("By Victor Rousseau") == "Victor Rousseau"
    assert byline_author("by\nKatherine Yates") == "Katherine Yates"
    assert byline_author("Illustrated by Wesso") is None
    assert byline_author("By Relieving the Cause with this and that and more words") is None
    assert split_chapter("CHAPTER II\nThe Thing in the Vault") == ("II", 2, "The Thing in the Vault")
    assert split_chapter("IV. The Coming of the Beast") == ("IV", 4, "The Coming of the Beast")
    assert split_chapter("Chapter 3") == ("3", 3, None)
    assert split_chapter("The Thing in the Vault") == (None, None, "The Thing in the Vault")
    assert roman_int("XIV") == 14 and roman_int("IX") == 9
    fpage = {"width": 1400, "height": 2200, "regions": [
        {"label": "Text", "bbox": [0, 0, 100, 40], "order": 0, "text": "Bernard J. Kenton, of Cleveland, writes:"},
        {"label": "SectionHeader", "bbox": [0, 50, 100, 90], "order": 1, "text": "My favorite stories in the May WEIRD TALES are:"},
        {"label": "Table", "bbox": [0, 100, 100, 300], "order": 2, "text": "Story\nRemarks\n(1) -----\n(2) -----"},
        {"label": "Text", "bbox": [0, 310, 100, 340], "order": 3, "text": "I do not like the following stories:"},
        {"label": "Table", "bbox": [0, 350, 100, 500], "order": 4, "text": "(1) -----\nWhy? -----"},
        {"label": "Text", "bbox": [0, 510, 100, 560], "order": 5, "text": "It will help us to know what kind of stories you want if you will fill out this coupon and mail it."},
        {"label": "Text", "bbox": [0, 570, 100, 600], "order": 6, "text": "Reader's name and address:"},
        {"label": "Form", "bbox": [0, 610, 100, 700], "order": 7, "text": "-----\n-----"}]}
    assert form_block(fpage, 1, {"furniture": []}) == [1, 2, 3, 4, 5, 6, 7], form_block(fpage, 1, {"furniture": []})
    assert form_block(fpage, 0, {"furniture": []}) is None
    toc = "COVER DESIGN\nH. W. WESSOLOWSKI\n\nTHE BEETLE HORDE\nVICTOR ROUSSEAU\n8\n\nblurb\n\nTHE CAVE OF HORROR\nCAPTAIN S. P. MEEK\n32\n"
    fake = {4: {"page": 4, "width": 1400, "height": 2200, "regions": [{"label": "SectionHeader", "bbox": [0, 0, 100, 40], "order": 0, "text": "CONTENTS"},
                                                                     {"label": "TableOfContents", "bbox": [0, 50, 100, 900], "order": 1, "text": toc}]}}
    entries, pno = parse_toc(fake)
    assert pno == 4 and [e["title"] for e in entries] == ["THE BEETLE HORDE", "THE CAVE OF HORROR"], entries
    assert entries[0]["author"] == "VICTOR ROUSSEAU" and entries[1]["page"] == 32
    gal = {3: {"page": 3, "width": 1400, "height": 2200, "regions": [{"label": "SectionHeader", "bbox": [0, 0, 100, 40], "order": 0, "text": "CONTENTS"},
                                                                    {"label": "Text", "bbox": [0, 50, 100, 90], "order": 1, "text": "THE YEAR OF THE JACKPOT ..... 4\nby Robert A. Heinlein"},
                                                                    {"label": "Text", "bbox": [0, 100, 100, 140], "order": 2, "text": "MANNERS OF THE AGE ..... 38\nby H. B. Fyfe"},
                                                                    {"label": "Text", "bbox": [0, 150, 100, 190], "order": 3, "text": "THE DEMOLISHED MAN ..... 101\nby Alfred Bester"}]}}
    entries, _ = parse_toc(gal)
    assert [(e["title"], e["page"]) for e in entries] == [("THE YEAR OF THE JACKPOT", 4), ("MANNERS OF THE AGE", 38), ("THE DEMOLISHED MAN", 101)], entries
    assert entries[0]["author"] == "Robert A. Heinlein"
    td = {4: {"page": 4, "width": 1400, "height": 2200, "regions": [{"label": "SectionHeader", "bbox": [0, 0, 100, 40], "order": 0, "text": "CONTENTS"},
                                                                   {"label": "ListGroup", "bbox": [0, 50, 100, 90], "order": 1, "text": "Dead Men Talk..... Perley Poore Sheehan 6\n\nA Pulse-Stirring Story"},
                                                                   {"label": "ListGroup", "bbox": [0, 100, 100, 140], "order": 2, "text": "5 Empty Holsters . . . . . Don Alviso . . . . . 12\n\n blurb"}]}}
    entries, _ = parse_toc(td)
    assert [(e["title"], e["author"], e["page"]) for e in entries] == [("Dead Men Talk", "Perley Poore Sheehan", 6), ("Empty Holsters", "Don Alviso", 12)], entries
    # the cross-issue pass on two made-up issues: a two-part serial and a house block quoting the next issue's story
    import tempfile
    tmp = tempfile.mkdtemp()
    story = " ".join(f"word{i} the man said quietly and he went on his way again" for i in range(30))
    d1 = {"articles": [{"article_id": "x_1_a001", "type": "story", "title": "The Waning of a World", "author": "W. Elwyn Backus",
                        "serial": {"part_label": "Part One", "part_n": 1, "part_total": None, "source": "contents"}, "work_title": "The Waning of a World",
                        "text": "one " * 200, "pages": [1], "fragments": [], "roles": {}, "flags": []},
                       {"article_id": "x_1_a002", "type": "house", "title": "Next Month", "ad_class": "house_next_issue", "text": story[:900] + " Read it next month.",
                        "pages": [2], "fragments": [], "roles": {}, "flags": []}]}
    d2 = {"articles": [{"article_id": "x_2_a001", "type": "story", "title": "The Waning of a World", "author": "W. Elwyn Backus",
                        "serial": {"part_label": "Conclusion", "part_n": None, "part_total": None, "source": "contents"}, "work_title": "The Waning of a World",
                        "text": "two " * 200, "pages": [1], "fragments": [], "roles": {}, "flags": []},
                       {"article_id": "x_2_a002", "type": "story", "title": "The Quoted Story", "author": "A. Person", "text": story, "pages": [3], "fragments": [], "roles": {}, "flags": []}]}
    for iid, d in (("x_1", d1), ("x_2", d2)):
        os.makedirs(os.path.join(tmp, "data", "assembly_v2", "rules", iid), exist_ok=True)
        json.dump(d, open(os.path.join(tmp, "data", "assembly_v2", "rules", iid, "articles.json"), "w"))
    cfg = {"issues": [{"id": "x_1", "magazine": "Weird Tales", "cover_date": "1925-11"}, {"id": "x_2", "magazine": "Weird Tales", "cover_date": "1925-12"}]}
    cross_issue(cfg, variants=("rules",), log=lambda *a: None, root=tmp)
    r1 = json.load(open(os.path.join(tmp, "data", "assembly_v2", "rules", "x_1", "articles.json")))["articles"]
    r2 = json.load(open(os.path.join(tmp, "data", "assembly_v2", "rules", "x_2", "articles.json")))["articles"]
    assert r1[0]["work_id"] == r2[0]["work_id"] and r1[0]["serial"]["next"]["issue"] == "x_2" and r2[0]["serial"]["prev"]["issue"] == "x_1", (r1[0], r2[0])
    assert r2[0]["serial"]["part_n"] == 2 and r2[0]["serial"]["part_total"] == 2, r2[0]["serial"]
    assert r1[1]["contains_excerpt"] and r1[1]["excerpt_of"]["title"] == "The Quoted Story", r1[1]
    print("selftest OK")


def shingles(text, n=8):
    ws = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {" ".join(ws[i:i + n]) for i in range(0, max(0, len(ws) - n + 1))}


def cross_issue(cfg, variants=("rules", "rules_on_model"), log=print, root=None):
    """What one issue cannot know (2026-09-04, from Sujin's and Heejin's notes):

    1. Serial instalments are linked across issues of one magazine: records
       that share a work title and author (the title without its instalment
       marker) get a work_id, and each knows the instalment before and after
       it, by issue and title (record ids change with a refresh, titles do
       not). Part numbers missing on the page are filled from the order of
       the issues; a marker that disagrees with the order is flagged.
    2. A house record quoting a story verbatim (contains_excerpt) is linked
       to the story it quotes wherever the corpus holds it — the announced
       story usually appears in the next issue — by 8-word shingles: when a
       third of the announcement's shingles occur in one story record, that
       record is the excerpt_of. The flag is set by this match, never by
       hand (Sujin's note of 2026-09-03).
    Writes the fields back into every candidate file."""
    root = root or ROOT
    issues = sorted(cfg["issues"], key=lambda i: (i.get("magazine", ""), i.get("cover_date", "")))
    for variant in variants:
        docs = {}
        for it in issues:
            pth = os.path.join(root, "data", "assembly_v2", variant, it["id"], "articles.json")
            if os.path.exists(pth):
                docs[it["id"]] = json.load(open(pth, encoding="utf-8"))
        if not docs:
            continue
        # 1. serials
        groups = {}
        for it in issues:
            d = docs.get(it["id"])
            if not d:
                continue
            for a in d["articles"]:
                if a["type"] != "story":
                    continue
                if not (a.get("serial") or a.get("work_title")):
                    continue
                wt = a.get("work_title") or a.get("title") or ""
                k = (norm(it.get("magazine", "")), norm_words(wt), norm(a.get("author") or ""))
                groups.setdefault(k, []).append((it, a))
        n_linked = 0
        for (mag, wt, au), members in groups.items():
            members.sort(key=lambda x: x[0].get("cover_date", ""))
            work_id = f"{mag.replace(' ', '_')}:{wt.replace(' ', '_')}" if wt else None
            for n, (it, a) in enumerate(members):
                a["work_id"] = work_id
                ser = a.setdefault("serial", {"part_label": None, "part_n": None, "part_total": None, "source": "linked"})
                if len(members) > 1:
                    prev_ = members[n - 1] if n > 0 else None
                    next_ = members[n + 1] if n + 1 < len(members) else None
                    ser["prev"] = {"issue": prev_[0]["id"], "title": prev_[1].get("title"), "part_label": (prev_[1].get("serial") or {}).get("part_label")} if prev_ else None
                    ser["next"] = {"issue": next_[0]["id"], "title": next_[1].get("title"), "part_label": (next_[1].get("serial") or {}).get("part_label")} if next_ else None
                    n_linked += 1
                    # part numbers from the order of the issues when the page gave none; disagreement flagged
                    first_n = (members[0][1].get("serial") or {}).get("part_n")
                    guess = (first_n or 1) + n
                    if ser.get("part_n") is None:
                        ser["part_n"] = guess
                        ser["part_n_source"] = "issue order"
                    elif ser["part_n"] != guess and first_n:
                        a.setdefault("flags", []).append(f"instalment number {ser['part_n']} on the page, {guess} by the order of the issues")
                    if re.search(r"(?i)conclu|final|the end", (members[-1][1].get("serial") or {}).get("part_label") or "") and ser.get("part_total") is None:
                        ser["part_total"] = (first_n or 1) + len(members) - 1
                        ser["part_total_source"] = "the last instalment is marked as the conclusion"
        # 2. excerpts in house records, matched against every story of the same magazine
        stories = []
        for it in issues:
            d = docs.get(it["id"])
            if not d:
                continue
            for a in d["articles"]:
                if a["type"] == "story" and (a.get("text") or "").strip():
                    stories.append((it, a, shingles(a["text"])))
        n_exc = 0
        for it in issues:
            d = docs.get(it["id"])
            if not d:
                continue
            for a in d["articles"]:
                if a["type"] not in ("house", "ad") or words(a.get("text") or "") < 60:
                    continue
                sh = shingles(a["text"])
                if len(sh) < 20:
                    continue
                best = None
                for it2, st, ssh in stories:
                    if norm(it2.get("magazine", "")) != norm(it.get("magazine", "")):
                        continue
                    ov = len(sh & ssh) / len(sh)
                    if ov > 0.3 and (best is None or ov > best[0]):
                        best = (ov, it2, st)
                if best:
                    ov, it2, st = best
                    a["contains_excerpt"] = True
                    a["excerpt_of"] = {"title": st.get("title"), "author": st.get("author"), "issue": it2["id"], "share": round(ov, 2)}
                    if "matched by text" not in " ".join(a.get("flags") or []):
                        a.setdefault("flags", []).append(f"quotes {st.get('title')!r} ({it2['id']}) verbatim: {ov:.0%} of its 8-word runs occur there (matched by text)")
                    n_exc += 1
                elif a.get("contains_excerpt") and not (a.get("excerpt_of") or {}).get("issue"):
                    a.setdefault("flags", []).append("reads like an excerpt but no story in the corpus matches it (the quoted issue is not in the corpus)")
        for iid, d in docs.items():
            pth = os.path.join(root, "data", "assembly_v2", variant, iid, "articles.json")
            json.dump(d, open(pth, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"[s08] {variant}: {n_linked} serial instalments linked across issues, {n_exc} house records matched to the story they quote")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--issue")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--link-only", action="store_true", help="only the cross-issue pass (serials, excerpts) over the existing candidates")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    from corpus_lib import issues_config
    cfg = issues_config()           # the pilot list, or PULP_ISSUES=config/corpus_issues.json
    issues = {i["id"]: i for i in cfg["issues"]}
    if args.link_only:
        cross_issue(cfg)
        return
    ids = [args.issue] if args.issue else (list(issues) if args.all else [])
    if not ids:
        sys.exit("pass --all or --issue <id>")
    for iid in ids:
        run_issue(iid, issues.get(iid))
    cross_issue(cfg)


if __name__ == "__main__":
    main()
