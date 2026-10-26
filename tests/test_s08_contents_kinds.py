"""The kinds of contents entries (rules 2.9): Sujin Kang's level-1 contents checks of 21 October 2026, 31 sample issues,
turned into rules — pipeline/s08_assemble_rules.py repair_toc_entries / type_toc_entries and config/departments.json.
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import s08_assemble_rules as R


def E(title, author=None, section="", blurb="", **kw):
    e = {"title": title, "author": author, "page": 1, "blurb": blurb, "section": section}
    e.update(kw)
    return e


def typed(*entries):
    R.type_toc_entries(list(entries))
    return entries


class Kinds(unittest.TestCase):
    def test_articles_hints_and_quizzes_are_features_true_stories_are_stories(self):
        a, b, c, d = typed(E("Let's Finish the Lindbergh Case!", "Cecil Carnes", "ARTICLES"), E("Winter of Hell", "Will Watson", "a True Story"),
                           E("Fencing with Singlesticks", "Prof. Fairfax", "HELPFUL HINTS"), E("Know Your West, a Quiz", "Rattlesnake Robert", "DEPARTMENTS"))
        self.assertEqual([x["type"] for x in (a, b, c, d)], ["feature", "feature", "feature", "feature"])
        self.assertTrue(b.get("true_story"))          # Sujin, 26 October: a true story is a story — retyped once assembled (the feature path cuts the advertisement at its foot)
        e, = typed(E("Seventy-Eight Years Ago (True Tale)", "William S. Lee", "SHORT STORIES"))
        self.assertTrue(e.get("true_story"))

    def test_an_unsigned_contents_page_lists_stories(self):
        es = typed(E("THE RED-LIGHT EXPRESS"), E("BASKET LARCENY"), E("KILL THE CHAMP!"), E("PIGSKIN PAY DIRT"), E("FIGHT FEVER"), E("SPORTS QUIZ PAGE"))
        self.assertEqual([x["type"] for x in es], ["feature"] * 6)               # assembled as features...
        self.assertEqual([x.get("unsigned_story") for x in es], ["all"] * 5 + [None])   # ...retyped story once assembled; a department is not
        es = typed(E("Pawn of the Unseen", "Lyon Mearson"), E("Spirit Tales"), E("The Flaming Curse", "Paul Jacobs"), E("Guided by a Phantom Hound", "Bob Swift"))
        self.assertEqual(es[1]["type"], "feature")                     # a signed contents page: an unsigned entry is a filler

    def test_months_and_volume_lines_are_not_pieces(self):
        es = [E("March"), E("JULY"), E("CONTENTS JUNE"), E("Vol. 27, No. 1 A THRILLING PUBLICATION", "A Complete Novel"), E("March Hare", "A. Writer")]
        R.type_toc_entries(es)
        self.assertEqual([e["title"] for e in es], ["March Hare"])

    def test_magazine_matching(self):
        self.assertTrue(R.magazine_matches("Galaxy Science Fiction", "Galaxy"))
        self.assertTrue(R.magazine_matches("Astounding Stories of Super-Science", "Astounding Stories"))
        self.assertFalse(R.magazine_matches("Galaxy Science Fiction", "Astounding Science Fiction"))
        self.assertFalse(R.magazine_matches("Thrilling Detective", "Thrilling Wonder Stories"))
        self.assertEqual(R.department_of("Galaxy", "Editor's Page")[1], "letters")

    def test_verse_or_prose(self):
        verse = "\n".join(["The night was dark and cold,", "A shadow crossed the moor,", "And no one heard the bell", "That tolled beside the shore,",
                           "Nor saw the rider pass", "Beneath the ancient door,", "Where once the king had stood", "In days of yore."])
        prose = "\n".join(["It was a strange old structure, and the strangest part of it was that green opaque window which looked out upon the street."] * 7)
        self.assertTrue(R.looks_like_verse(verse))
        self.assertFalse(R.looks_like_verse(prose))
        self.assertIsNone(R.looks_like_verse("one line"))

    def test_a_featured_novelet_is_a_story(self):
        a, b, c = typed(E("Colleen of the Wishing Hills", "Lee Floren", "FEATURED NOVELET"), E("Heart of the Range", None, "FEATURES"),
                        E("Once a Sucker", "Robert Bloch", "BLUEBOOK S COMPLETE BOOK LENGTH NOVEL"))
        self.assertEqual([x["type"] for x in (a, b, c)], ["story", "feature", "story"])

    def test_verse_sections_titles_and_songs_are_poems(self):
        a, b, c = typed(E("Demon Lure", "Harriet Bradfield", "VERSE"), E("SONGS OF THE SPACEWAYS", None, "FANTASY BOOK"), E("Arid-Zona", "Limerick Luke", "FEATURES", "verse"))
        self.assertEqual([x["type"] for x in (a, b, c)], ["poem", "poem", "poem"])

    def test_the_readers_and_the_editors_departments_are_letters(self):
        a, b, c, d, e, f = typed(E("OUR AIR MAIL", "Our Readers", "DEPARTMENTS"), E("The Camp-Fire", "Where readers, writers and adventurers meet"),
                                 E("Ask Adventure", "Information you can’t get elsewhere"), E("THE LOG-BOOK", None, "EIGHT SHORT STORIES"),
                                 E("The Editor's Page", None, "DEPARTMENTS"), E("Pro and Con", None, "SPECIAL FEATURES"))
        self.assertEqual([x["type"] for x in (a, b, c, d, e)], ["letters"] * 5)
        self.assertIsNone(b["author"])                                   # a description is not a name
        self.assertEqual(b["subtitle"], "Where readers, writers and adventurers meet")
        self.assertEqual(f["type"], "feature")                           # Pro and Con: the departments list makes it letters
        self.assertEqual(R.department_of("Blue Book", "Pro and Con")[1], "letters")
        self.assertEqual(R.department_of("Ranch Romances", "TRAIL DUST")[1], "letters")

    def test_serial_marks(self):
        a, b, c = typed(E("Rustlers’ Range", "Luke Short", type_label="a serial"), E("Black Goldfish", "John Taine", part="Part 2"),
                        E("CROSSES OF STEEL. In Five Parts", "REX PARSON", "FOUR SERIAL STORIES"))
        self.assertEqual([x["type"] for x in (a, b, c)], ["serial_part"] * 3)
        self.assertEqual((c["title"], c["part_total"]), ("CROSSES OF STEEL", 5))

    def test_club_notice_is_a_department(self):
        a, = typed(E("WEIRD TALES CLUB", None, "VERSE"))
        self.assertEqual(a["type"], "feature")
        self.assertEqual(R.department_of("Weird Tales", "WEIRD TALES CLUB")[1], "letters")     # Sujin, 26 October


class Repairs(unittest.TestCase):
    def test_glued_names(self):
        a, b, c, d = typed(E("The Albino CanaryHal Moore"), E("Tongue of the DragonDale Hart"), E("Battle of WizardsL", "Ron Hubbard"), E("Empire of DustBasil Wells"))
        self.assertEqual((a["title"], a["author"], a["type"]), ("The Albino Canary", "Hal Moore", "story"))
        self.assertEqual((b["title"], b["author"]), ("Tongue of the Dragon", "Dale Hart"))
        self.assertEqual((c["title"], c["author"]), ("Battle of Wizards", "L. Ron Hubbard"))
        self.assertEqual((d["title"], d["author"]), ("Empire of Dust", "Basil Wells"))
        self.assertEqual(R.split_glued_name("Joe Mulligan Dies Natural"), ("Joe Mulligan Dies Natural", None))
        self.assertEqual(R.split_glued_name("The Man from McHenry"), ("The Man from McHenry", None))

    def test_section_word_and_chapter_range_before_the_title(self):
        a, b, c = typed(E("VERSE { HALLOWE'EN CANDLE", "Yetza Gillespie", "SHORT STORIES"), E("CHAPTERS IX-XIIILUCK. In Six Parts", "JOHN FREDERICK", "FOUR SERIAL STORIES"),
                        E("CHAPTERS I-VIEVERYTHING BUT THE TRUTH. In Four Parts", "EDGAR FRANKLIN", "FOUR SERIAL STORIES"))
        self.assertEqual((a["title"], a["type"]), ("HALLOWE'EN CANDLE", "poem"))
        self.assertEqual((b["title"], b["part_total"], b["type"]), ("LUCK", 6, "serial_part"))
        self.assertEqual((c["title"], c["part_total"]), ("EVERYTHING BUT THE TRUTH", 4))

    def test_a_title_that_went_into_the_previous_blurb(self):
        a, b = typed(E("Solander's Radio Tomb", "Ellis Parker Butler", "AMAZING STORIES", "The Moon Pool (A Serial in 3 parts)"), E("", "A. Merritt", "AMAZING STORIES"))
        self.assertEqual((a["type"], a["blurb"]), ("story", ""))
        self.assertEqual((b["title"], b["type"], b["part_total"]), ("The Moon Pool", "serial_part", 3))

    def test_ordinary_entries_are_untouched(self):
        a, b = typed(E("The Underbody", "Allison V. Harding", "NOVELETTES", "The country fields of summer hid a terror"), E("Special Announcement", None, "Registered U S Patent Office"))
        self.assertEqual((a["title"], a["author"], a["type"]), ("The Underbody", "Allison V. Harding", "story"))
        self.assertEqual(b["type"], "feature")


if __name__ == "__main__":
    unittest.main()
