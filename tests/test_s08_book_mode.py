"""The book mode of the assembler (pipeline/s08_book.py; 21 October 2026): a novel read as one piece, a collection as
its listed pieces, the front matter and the publisher's catalogue set apart, the book's title and author."""
import sys
from pathlib import Path
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "pipeline")]
import s08_book as B


def rec(aid, type_, title, pages, author=None, text="", roles=None, chapters=None):
    return {"article_id": aid, "type": type_, "title": title, "author": author, "pages": list(pages),
            "fragments": [{"page": p, "region_ids": [0, 1]} for p in pages], "roles": roles or {}, "flags": [],
            "text": text, "chapters": chapters or [], "n_regions": 2 * len(pages)}


CATALOGUE = " ".join(["Crown 8vo. 3s. 6d. net. Second Edition. Messrs. Methuen's list."] * 12)


class BookMode(unittest.TestCase):
    def test_a_novel_becomes_one_story_with_its_chapters(self):
        A = {"toc_kind": "chapters", "toc": [{"title": "I. SUNSET", "page": 7}], "toc_page": 7}
        records = [rec("x_a001", "other", "Title page", [3]), rec("x_a002", "toc", "Contents", [7]),
                   rec("x_a003", "feature", "I. SUNSET", [10, 11, 12], text="Chapter one " * 100, roles={"10:0": "chapter"}, chapters=[{"n": 1}]),
                   rec("x_a004", "story", "II. THE HEIR-AT-LAW", [13, 14], text="Chapter two " * 100, roles={"13:0": "chapter"}, chapters=[{"n": 2}]),
                   rec("x_a005", "ad", "Buy Pears' Soap", [15]),
                   rec("x_a006", "feature", "III. MR. CORONER JUDD", [16, 17, 18], text="Chapter three " * 100, roles={"16:0": "chapter"}),
                   rec("x_a007", "feature", "Messrs. Gardner's List", [19, 20], text=CATALOGUE)]
        n_regions = sum(r["n_regions"] for r in records)
        out, summ = B.book_mode("x", records, A, "Silken Threads", "Harold William Gardner")
        self.assertEqual(summ["kind"], "novel")
        stories = [r for r in out if r["type"] == "story"]
        self.assertEqual(len(stories), 1)
        s = stories[0]
        self.assertEqual((s["title"], s["author"], s["pages"]), ("Silken Threads", "Harold William Gardner", [10, 11, 12, 13, 14, 16, 17, 18]))
        self.assertEqual(len(s["chapters"]), 2)
        self.assertEqual(s["roles"]["16:0"], "chapter")
        self.assertIn("Chapter three", s["text"])
        self.assertEqual([r["type"] for r in out if r["pages"][0] == 3], ["other"])          # the front matter
        self.assertEqual(next(r["type"] for r in out if r["pages"][0] == 19), "house")        # the catalogue
        self.assertEqual(next(r["ad_class"] for r in out if r["pages"][0] == 19), "house_publisher_list")
        self.assertEqual([r["article_id"] for r in out], [f"x_a{k:03d}" for k in range(1, len(out) + 1)])
        self.assertEqual(sum(r["n_regions"] for r in out), n_regions)

    def test_a_collection_becomes_its_listed_pieces(self):
        A = {"toc_kind": "pieces", "toc_page": 11, "toc": [{"title": "THE PLATTNER STORY", "page": 1, "scan": 13, "type": "feature"},
                                                            {"title": "IN THE ABYSS", "page": 71, "scan": 83, "type": "feature"}]}
        records = [rec("p_a001", "other", "TO MY FATHER", [9]), rec("p_a002", "toc", "CONTENTS", [11]),
                   rec("p_a003", "feature", "The Plattner Story", [13, 14], text="Whether the story " * 50),
                   rec("p_a004", "story", "II", [15, 16], text="part two " * 50, roles={"15:0": "chapter"}),
                   rec("p_a005", "feature", "In the Abyss", [83, 84], text="The lieutenant " * 50),
                   rec("p_a006", "feature", "English Classics", [329, 330], author="W. E. HENLEY", text=CATALOGUE)]
        out, summ = B.book_mode("p", records, A, "The Plattner Story And Others", "H. G. Wells")
        self.assertEqual((summ["kind"], summ["pieces"], summ["merged"], summ["front_matter"], summ["publisher_list"]), ("collection", 2, 1, 1, 1))
        stories = [r for r in out if r["type"] == "story"]
        self.assertEqual([(s["title"], s["author"], s["pages"]) for s in stories],
                         [("The Plattner Story", "H. G. Wells", [13, 14, 15, 16]), ("In the Abyss", "H. G. Wells", [83, 84])])
        self.assertEqual(stories[0]["toc"]["title"], "THE PLATTNER STORY")
        self.assertEqual(out[0]["title"], "Front matter")
        self.assertEqual(out[0]["type"], "other")

    def test_a_listed_piece_is_never_taken_for_a_catalogue(self):
        A = {"toc_kind": "pieces", "toc_page": 3, "toc": [{"title": "A SLIP UNDER THE MICROSCOPE", "page": 286, "scan": 286}]}
        records = [rec("q_a001", "feature", "A Slip Under the Microscope", [286, 287], text=CATALOGUE)]
        out, summ = B.book_mode("q", records, A, "Book", "H. G. Wells")
        self.assertEqual(out[0]["type"], "story")

    def test_titles_and_authors(self):
        self.assertEqual(B.book_title_author(archive_title="The Plattner Story And Others - H. G. Wells (Methuen, 1897)"), ("The Plattner Story And Others", "H. G. Wells"))
        self.assertEqual(B.book_title_author(archive_title="Wells, H. G. - The Island Of Dr. Moreau"), ("The Island Of Dr. Moreau", "H. G. Wells"))
        self.assertEqual(B.book_title_author(name="Death at Sea, by Richard Sale"), ("Death at Sea", "Richard Sale"))
        self.assertEqual(B.book_title_author(name="Galaxy Novel 028 - Destiny Times Three", creator="Fritz Leiber"), ("Galaxy Novel 028 - Destiny Times Three", "Fritz Leiber"))
        self.assertEqual(B.book_kind_of_contents({"toc": []}), "novel")
        self.assertEqual(B.book_kind_of_contents({"toc": [{"title": "CHAPTER I"}, {"title": "CHAPTER II"}, {"title": "Epilogue"}]}), "novel")


if __name__ == "__main__":
    unittest.main()
