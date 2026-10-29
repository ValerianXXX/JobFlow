from copy import deepcopy
import importlib.util
import unittest

import _support
from _support import PROJECT

spec = importlib.util.spec_from_file_location('complete_material_eval', PROJECT / 'scripts/evaluate-complete-materials.py')
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


class CompleteMaterialCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = corpus.load_manifest()

    def test_complete_population_and_family_holdout_are_frozen(self):
        cases = self.manifest['cases']
        self.assertEqual(len(cases), 36)
        for language in ('en', 'zh', 'mixed'):
            selected = [c for c in cases if c['language'] == language]
            self.assertEqual(len(selected), 12)
            self.assertEqual(sum(c['split'] == 'held_out' for c in selected), 4)
        dev = {c['family'] for c in cases if c['split'] == 'development'}
        held = {c['family'] for c in cases if c['split'] == 'held_out'}
        self.assertFalse(dev & held)
        for case in cases:
            self.assertGreaterEqual(len(case['facts']), 47)
            self.assertEqual(len({f['id'] for f in case['facts']}), len(case['facts']))
            self.assertEqual({s['id'] for s in case['sections']}, {'profile', 'role_a', 'role_b', 'project', 'education', 'skills', 'notes'})
            self.assertTrue(any(f['kind'] == 'negative_evidence' for f in case['facts']))
            self.assertTrue(any(f['owner'] == 'team' for f in case['facts']))

    def test_author_geometry_is_complete_but_not_invented_for_docx(self):
        for case in self.manifest['cases']:
            if case['encoding'] == 'docx':
                self.assertIsNone(case['page_count'])
                self.assertEqual(case['source_regions'], [])
                continue
            self.assertEqual({f['id'] for f in case['facts']}, {r['fact_id'] for r in case['source_regions']})
            for region in case['source_regions']:
                x0, y0, x1, y1 = region['bbox']
                self.assertTrue(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)
                self.assertTrue(1 <= region['page'] <= case['page_count'])

    def test_duplicates_require_distinct_text_occurrences(self):
        case = next(c for c in self.manifest['cases'] if c['id'] == 'en-same_organization_roles')
        facts = [f for f in case['facts'] if f['text'].startswith('Organization:')]
        self.assertEqual(facts[0]['text'], facts[1]['text'])
        measured = corpus.measure({'facts': facts}, facts[0]['text'])
        self.assertEqual(measured['verbatim_retained'], 1)
        self.assertIsNone(measured['misattributions'])

    def test_numbers_and_ownership_are_not_enough_to_claim_full_accuracy(self):
        case = self.manifest['cases'][0]
        text = '\n'.join(f['text'] for f in case['facts'])
        expected = len(case['facts'])
        self.assertEqual(corpus.measure(case, text)['verbatim_retained'], expected)
        changed = text.replace('The team reviewed 21 stores', 'The applicant reviewed 99 stores')
        result = corpus.measure(case, changed)
        self.assertEqual(result['verbatim_retained'], expected-1)
        self.assertIsNone(result['unflagged_critical_claim_errors'])
        self.assertIsNone(result['semantic_claim_recall'])

    def test_absent_input_facts_are_separate_and_cannot_be_reconstructed(self):
        case = next(c for c in self.manifest['challenges'] if c['id'] == 'challenge-en-missing_middle')
        full_text = '\n'.join(f['text'] for f in case['facts'])
        result = corpus.measure(case, full_text)
        self.assertEqual(set(result['absent_source_facts_reappeared']), set(case['unavailable_fact_ids']))
        self.assertEqual(result['expected_available_facts'], len(case['facts'])-len(case['unavailable_fact_ids']))

    def test_challenges_are_separate_development_derivatives(self):
        rows = self.manifest['challenges']
        self.assertEqual(len(rows), 18)
        self.assertEqual(len({r['id'] for r in rows}), 18)
        self.assertEqual({r['split'] for r in rows}, {'development_challenge'})
        self.assertEqual(len({r['kind'] for r in rows}), 6)
        self.assertTrue(all(r['required_behavior'] for r in rows))


if __name__ == '__main__':
    unittest.main()
