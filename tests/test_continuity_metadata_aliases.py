import copy
import sys
from pathlib import Path
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).resolve().parents[1] / 'pipeline')]
import upgrade_continuity as u


def record(title, author, ids=(0, 1, 2)):
    return {'article_id': 'x', 'type': 'story', 'title': title, 'author': author,
            'fragments': [{'page': 1, 'region_ids': list(ids)}], 'roles': {'1:0': 'title', '1:1': 'author'}}


class PrintedMetadataProofTest(unittest.TestCase):
    def mixed_excerpt_fixture(self):
        before = record('A Special Offer', None, (0, 1, 2))
        before.update(type='house', contains_excerpt=True, fragments=[{'page': 2, 'region_ids': [0, 1, 2]}])
        story = record('The Road', 'Jane Doe', (0,))
        story.update(fragments=[{'page': 1, 'region_ids': [0]}, {'page': 2, 'region_ids': [1, 2]}],
                     flags=['preceding story columns retained above another continuation on p.2'])
        ad = record('The Offer', None)
        ad.update(article_id='ad', type='ad', fragments=[{'page': 2, 'region_ids': [0]}])
        paragraph = 'he looked at her and said that she had come home. ' * 8
        pages = {2: {'regions': [
            {'label': 'Text', 'text': 'Mail coupon to 123 Main Street, Chicago for $1.', 'bbox': [0, 100, 90, 300]},
            {'label': 'Text', 'text': paragraph, 'bbox': [110, 100, 200, 300]},
            {'label': 'Text', 'text': paragraph, 'bbox': [110, 320, 200, 500]}]}}
        return before, {'articles': [story, ad]}, pages

    def test_mixed_excerpt_flag_retires_after_complete_separate_column_proof(self):
        before, candidate, pages = self.mixed_excerpt_fixture()
        proof = u.retired_mixed_column_excerpt(before, candidate, pages)
        self.assertIsNotNone(proof)
        self.assertEqual(proof['retained_story_regions'], 2)
        before['excerpt_of'] = {'title': 'Even a named local inference'}
        self.assertIsNone(u.retired_mixed_column_excerpt(before, candidate, pages))

    def test_mixed_excerpt_rejects_missing_box_unsupported_routing_and_overlap(self):
        before, candidate, pages = self.mixed_excerpt_fixture()
        candidate['articles'][0]['fragments'][1]['region_ids'] = [1]
        self.assertIsNone(u.retired_mixed_column_excerpt(before, candidate, pages))
        candidate['articles'][0]['fragments'][1]['region_ids'] = [1, 2]
        candidate['articles'][0]['flags'] = []
        self.assertIsNone(u.retired_mixed_column_excerpt(before, candidate, pages))
        candidate['articles'][0]['flags'] = ['preceding story columns retained above another continuation on p.2']
        pages[2]['regions'][0]['bbox'] = [100, 100, 170, 300]
        self.assertIsNone(u.retired_mixed_column_excerpt(before, candidate, pages))

    def alias_fixture(self):
        before = record('By Jane Doe', None, (2,))
        before.update(toc={'title': 'By Jane Doe', 'page': 17, 'blurb': 'The River (A Serial in Three Parts) Part II'},
                      serial={'part_label': 'Part II', 'part_n': 2, 'source': 'contents'})
        after = record('The Road', 'Jane Doe')
        other = record('The River', 'John Roe')
        other.update(article_id='other', fragments=[{'page': 2, 'region_ids': [0, 1, 2]}],
                     roles={'2:0': 'title', '2:1': 'author'})
        pages = {1: {'regions': [{'label': 'SectionHeader', 'text': 'The Road'}, {'text': 'By Jane Doe'}, {'text': 'Body'}]},
                 2: {'regions': [{'label': 'SectionHeader', 'text': 'The River'}, {'text': 'By John Roe'}, {'text': 'Other body'}]},
                 3: {'regions': [{'text': 'The Road\nBy Jane Doe ..... 17\nIllustrated by Wesso\nThe River\n(A Serial in Three Parts) Part II\nBy John Roe ..... 25'}]}}
        return before, after, {'articles': [after, other]}, pages

    def test_author_alias_retires_only_next_printed_work_ordinal(self):
        before, after, candidate, pages = self.alias_fixture()
        proof = u.author_only_contents_ordinal(before, after, candidate, pages)
        self.assertEqual(proof['action'], 'retire')
        self.assertEqual(proof['ordinal_owner'], 'other')
        audit = []
        u.preserve_links({'articles': [before]}, candidate, pages, audit)
        self.assertNotIn('serial', after)
        self.assertEqual(audit[0]['proof']['action'], 'retire')

    def test_author_alias_retains_real_ordinal_inside_own_entry(self):
        before, after, candidate, pages = self.alias_fixture()
        pages[3]['regions'][0]['text'] = 'The Road\nBy Jane Doe ..... 17\n(A Serial in Three Parts) Part II\nThe River\nBy John Roe ..... 25'
        u.preserve_links({'articles': [before]}, candidate, pages, [])
        self.assertEqual(after['serial'], before['serial'])
        self.assertEqual(after['work_title'], 'The Road')

    def test_author_alias_rejects_missing_body_identity_or_external_link(self):
        before, after, candidate, pages = self.alias_fixture()
        before['serial']['prev'] = {'issue': 'other'}
        self.assertIsNone(u.author_only_contents_ordinal(before, after, candidate, pages))
        before['serial'].pop('prev'); after['fragments'][0]['region_ids'] = [0, 1]
        self.assertIsNone(u.author_only_contents_ordinal(before, after, candidate, pages))
        after['fragments'][0]['region_ids'] = [0, 1, 2]; pages[2]['regions'][1]['text'] = 'By Somebody Else'
        self.assertIsNone(u.author_only_contents_ordinal(before, after, candidate, pages))

    def test_author_alias_rejects_wrong_contents_page_and_ordinal(self):
        before, after, candidate, pages = self.alias_fixture()
        before['toc']['page'] = 18
        self.assertIsNone(u.author_only_contents_ordinal(before, after, candidate, pages))
        before['toc']['page'] = 17; before['serial']['part_label'] = 'Part III'
        self.assertIsNone(u.author_only_contents_ordinal(before, after, candidate, pages))

    def test_printed_ordinal_allows_only_short_serial_descriptor(self):
        self.assertTrue(u.printed_ordinal_label('(A Serial in Two Parts) Part I', 'Part I'))
        self.assertTrue(u.printed_ordinal_label('A Serial in Three Parts—Part I', 'Part I'))
        self.assertFalse(u.printed_ordinal_label('He read Part I before falling asleep.', 'Part I'))
        self.assertFalse(u.printed_ordinal_label('(A Serial in Two Parts) Part II', 'Part I'))

    def test_actual_work_link_survives_exact_byline_title_correction(self):
        before = record('By Jane Doe', None, (1, 2))
        before.update(work_id='stable-work', work_title='By Jane Doe', roles={'1:1': 'title'},
                      serial={'source': 'contents', 'prev': {'issue': 'prior'}})
        after = record('The Road', 'Jane Doe')
        pages = {1: {'regions': [{'label': 'SectionHeader', 'text': 'The Road'}, {'text': 'By Jane Doe'}, {'text': 'Body'}]}}
        u.preserve_links({'articles': [before]}, {'articles': [after]}, pages, [])
        self.assertEqual(after['work_id'], 'stable-work')
        self.assertEqual(after['serial']['prev'], {'issue': 'prior'})
        self.assertEqual(after['work_title'], 'The Road')
        after['fragments'][0]['region_ids'] = [0, 1]
        self.assertFalse(u.printed_byline_alias(before, after, pages))
        after['fragments'][0]['region_ids'] = [0, 1, 2, 3]
        pages[1]['regions'].append({'text': 'Additional story body'})
        self.assertFalse(u.printed_byline_alias(before, after, pages))

    def test_inline_contents_alias_preserves_all_body_and_ordinal(self):
        before = record('The Road, by Jane Roe', None, (2,))
        before.update(toc={'title': before['title'], 'page': 1}, serial={'part_n': 2})
        after = record('The Road', 'Jane Roe')
        after.update(toc={'title': 'The Road', 'author': 'Jane Roe', 'page': 1},
                     author_as_printed='Jane Doe', serial={'part_n': 2})
        pages = {1: {'regions': [{'label': 'SectionHeader', 'text': 'THE ROAD'}, {'text': 'By Jane Doe'}, {'text': 'Body'}]}}
        self.assertTrue(u.unlinked_contents_alias(before, after, pages))
        before['work_id'] = 'external'
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))
        before.pop('work_id'); after['serial']['part_n'] = 3
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))
        after['serial']['part_n'] = 2; after['fragments'][0]['region_ids'] = [0, 1]
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))

    def test_missing_byline_requires_literal_title_and_printed_ordinal(self):
        before = record('The Road', 'Jane Roe', (2,))
        after = record('The Road', 'Jane Roe')
        for r in (before, after):
            r.update(toc={'title': 'The Road', 'author': 'Jane Roe', 'page': 1}, serial={'part_label': 'Conclusion'})
        after['roles'] = {'1:0': 'title'}
        pages = {1: {'regions': [{'label': 'SectionHeader', 'text': 'THE ROAD'}, {'text': 'Second of two parts. Summary follows.'}, {'text': 'Body'}]}}
        self.assertTrue(u.unlinked_contents_alias(before, after, pages))
        pages[1]['regions'][1]['text'] = 'First of two parts. Summary follows.'
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))
        pages[1]['regions'][1]['text'] = 'Second of two parts. Summary follows.'
        pages[1]['regions'][0]['text'] = 'ANOTHER ROAD'
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))
        pages[1]['regions'][0]['text'] = 'THE ROAD'
        after['fragments'][0]['region_ids'] = [1, 2]
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))

    def test_missing_title_requires_real_byline_number_and_matching_running_head(self):
        before = record('The Road, by Jane Doe', None, (2,))
        before.update(toc={'title': before['title'], 'page': 1}, serial={'part_n': 2})
        after = record('The Road', 'Jane Doe')
        after.update(toc={'title': 'The Road', 'author': 'Jane Doe', 'page': 1, 'type': 'serial_part'},
                     serial={'part_n': 2}, roles={'1:0': 'author'})
        pages = {1: {'height': 1000, 'regions': [
                    {'label': 'Text', 'text': 'By Jane Doe', 'bbox': [100, 100, 500, 140]},
                    {'label': 'Text', 'text': 'Second of three parts. Summary follows.', 'bbox': [100, 150, 500, 190]},
                    {'label': 'Text', 'text': 'Body', 'bbox': [100, 200, 500, 400]},
                    {'label': 'PageFooter', 'text': '1', 'bbox': [100, 960, 120, 990]}]},
                 2: {'height': 1000, 'regions': [
                    {'label': 'PageFooter', 'text': 'THE ROAD', 'bbox': [100, 960, 500, 990]},
                    {'label': 'PageFooter', 'text': '2', 'bbox': [600, 960, 620, 990]}]}}
        self.assertTrue(u.unlinked_contents_alias(before, after, pages))
        pages[2]['regions'][0]['text'] = 'OTHER STORY'
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))
        pages[2]['regions'][0]['text'] = 'THE ROAD'
        pages[1]['regions'][1]['text'] = 'First of three parts. Summary follows.'
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))
        pages[1]['regions'][1]['text'] = 'Second of three parts. Summary follows.'
        before['work_id'] = 'external'
        self.assertFalse(u.unlinked_contents_alias(before, after, pages))

    def test_chapter_excerpt_retirement_cannot_drop_body_or_external_relation(self):
        before = record('A Curious Journey', None, (2, 3, 4, 5))
        before.update(type='house', contains_excerpt=True)
        after = record('The Road', 'Jane Doe', (0, 1, 2, 3, 4))
        spare = record('Short Chapter', None, (5,)); spare['type'] = 'house'
        candidate = {'articles': [after, spare]}
        pages = {1: {'regions': [{'label': 'SectionHeader', 'text': 'THE ROAD'}, {'text': 'By Jane Doe'},
                 {'text': 'CHAPTER III'}, {'label': 'SectionHeader', 'text': 'A Curious Journey'},
                 {'text': 'Narrative body'}, {'label': 'SectionHeader', 'text': 'Next Chapter'}]}}
        self.assertTrue(u.retired_chapter_excerpt(before, after, candidate, pages))
        before['excerpt_of'] = {'issue': 'another'}
        self.assertFalse(u.retired_chapter_excerpt(before, after, candidate, pages))
        before.pop('excerpt_of'); pages[1]['regions'][5]['label'] = 'Text'
        self.assertFalse(u.retired_chapter_excerpt(before, after, candidate, pages))

    def test_conclusion_retained_only_with_printed_identity_and_last_part(self):
        before = record('Serial in 2 parts)', 'Jane Doe .. 1')
        before.update(title_as_printed='THE ROAD', author_as_printed='JANE DOE',
                      toc={'title': '(Serial in 2 parts) (Conclusion)'}, serial={'part_label': 'Conclusion'})
        after = record('The Road', 'Jane Doe')
        pages = {1: {'regions': [{'label': 'SectionHeader', 'text': 'THE ROAD'}, {'text': 'By Jane Doe'}, {'text': 'Part II'}]}}
        self.assertTrue(u.printed_conclusion_identity(before, after, pages))
        before['serial']['part_n'] = 1
        self.assertFalse(u.printed_conclusion_identity(before, after, pages))
        before['serial']['part_n'] = 2; before['serial']['part_total'] = 3
        self.assertFalse(u.printed_conclusion_identity(before, after, pages))
        before['serial'].pop('part_n'); before['serial'].pop('part_total')
        pages[1]['regions'][2]['text'] = 'Part I'
        self.assertFalse(u.printed_conclusion_identity(before, after, pages))
        pages[1]['regions'][2]['text'] = 'Part II'; before['serial']['prev'] = {'issue': 'external'}
        self.assertFalse(u.printed_conclusion_identity(before, after, pages))

    def test_printed_ordinal_stub_without_contents_retains_part_on_full_story(self):
        before = record('The Road', 'Jane Doe', (0, 1))
        before.update(serial={'part_label': 'Part I', 'part_n': 1, 'source': 'head'}, roles={'1:0': 'subtitle', '1:1': 'title'})
        after = record('The Road', 'Jane Doe', tuple(range(10)))
        after.update(serial=copy.deepcopy(before['serial']), roles={'1:0': 'subtitle', '1:1': 'title', '1:2': 'author'})
        pages = {1: {'regions': [{'text': 'Part I'}, {'label': 'SectionHeader', 'text': 'THE ROAD'},
                 {'text': 'By Jane Doe'}] + [{'text': 'Story body'}] * 7}}
        self.assertIsNotNone(u.recomputed_unlinked_identity_reason(before, after, pages))
        pages[1]['regions'][0]['text'] = 'Part II'
        self.assertIsNone(u.recomputed_unlinked_identity_reason(before, after, pages))
        pages[1]['regions'][0]['text'] = 'Part I'; before['work_id'] = 'external'
        self.assertIsNone(u.recomputed_unlinked_identity_reason(before, after, pages))

    def test_tiny_commercial_stub_can_relocate_true_link_but_narrative_cannot(self):
        before = record('The Road', 'Jane Doe', (0, 1, 2))
        before.update(work_id='real-work', toc={'title': 'The Road', 'author': 'Jane Doe', 'page': 10}, serial={'part_n': 2})
        after = record('The Road', 'Jane Doe', (0, 1, 2))
        after.update(toc=copy.deepcopy(before['toc']), serial={'part_n': 2},
                     fragments=[{'page': 4, 'region_ids': [0, 1, 2]}], roles={'4:0': 'title', '4:1': 'author'})
        ads = record('Package slogans', None, (0, 1, 2)); ads['type'] = 'ad'
        candidate = {'articles': [after, ads]}
        def folio(n): return {'height': 1000, 'regions': [{'label': 'PageFooter', 'text': str(n), 'bbox': [0, 950, 50, 990]}]}
        pages = {1: {'regions': [{'text': 'Ever tried pancakes?'}, {'text': 'The red box for waffles'}, {'text': 'The yellow box for buckwheats'}]},
                 3: folio(9), 4: {'height': 1000, 'regions': [{'label': 'SectionHeader', 'text': 'THE ROAD'}, {'text': 'By Jane Doe'}, {'text': 'II'}]}, 6: folio(12)}
        self.assertTrue(u.relocated_printed_serial(before, after, candidate, pages))
        pages[1]['regions'][0]['text'] = 'He remembered breakfast and the pancakes. ' * 8
        self.assertFalse(u.relocated_printed_serial(before, after, candidate, pages))
        pages[1]['regions'][0]['text'] = 'Ever tried pancakes?'; pages[4]['regions'][2]['text'] = 'III'
        self.assertFalse(u.relocated_printed_serial(before, after, candidate, pages))


if __name__ == '__main__': unittest.main()
