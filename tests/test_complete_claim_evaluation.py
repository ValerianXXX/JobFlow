from copy import deepcopy
import importlib.util
import unittest
from unittest.mock import patch

from _support import PROJECT

spec = importlib.util.spec_from_file_location('complete_claim_evaluation', PROJECT / 'scripts/evaluate-complete-claims.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class CompleteClaimEvaluationTests(unittest.TestCase):
    def fixture(self):
        case = next(c for c in evaluation.complete.load_manifest()['cases'] if c['id'] == 'en-chronological')
        fact = case['facts'][9]
        candidate = {'candidate_id': 'SYNTHETIC-1', 'statement': fact['text'], 'selected': False,
                     'entity': {'organization': fact['organization'], 'role': fact['role'],
                                'start_date': fact['dates'][0], 'end_date': fact['dates'][1]},
                     'provenance': {'line_start': 1, 'line_end': 1}}
        return case, candidate, fact['text']

    def test_all_source_facts_remain_and_exact_match_requires_entity_and_evidence(self):
        case, candidate, text = self.fixture()
        result = evaluation.assess(case, [candidate], text)
        self.assertEqual(result['source_facts'], 47)
        self.assertEqual(result['exact_grounded_wording_matches'], 1)
        self.assertEqual(len(result['facts']), 47)
        self.assertTrue({'identity', 'contact', 'missing_information', 'negative_evidence'} <= {f['kind'] for f in result['facts']})
        for mutate in (lambda c: c['entity'].update(organization='Another organization'),
                       lambda c: c['entity'].update(end_date='2026-06'),
                       lambda c: c['entity'].update(role='Director'),
                       lambda c: c['provenance'].update(line_start=2),
                       lambda c: c.update(selected=True)):
            changed = deepcopy(candidate)
            mutate(changed)
            self.assertEqual(evaluation.assess(case, [changed], text)['exact_grounded_wording_matches'], 0)

    def test_extra_wording_and_paraphrases_do_not_become_semantic_passes(self):
        case, candidate, text = self.fixture()
        candidate['statement'] += ' The applicant personally approved the budget.'
        result = evaluation.assess(case, [candidate], text)
        self.assertEqual(result['exact_grounded_wording_matches'], 1)
        self.assertIsNone(result['unsupported_claims'])
        self.assertIsNone(result['semantic_recall'])
        self.assertIsNone(result['unflagged_critical_errors'])
        self.assertTrue(all(row['semantic_status'] == 'REQUIRES_SOURCE_ADJUDICATION' for row in result['facts']))
        self.assertEqual(evaluation.assess(case, [], text)['exact_grounded_wording_matches'], 0)

    def test_pilot_is_frozen_six_complete_development_inputs(self):
        cases = {c['id']: c for c in evaluation.complete.load_manifest()['cases']}
        self.assertEqual(len(evaluation.PILOT), 6)
        for identity, mode in evaluation.PILOT:
            self.assertEqual(cases[identity]['split'], 'development')
            self.assertGreaterEqual(len(cases[identity]['facts']), 47)
            self.assertIn(mode, {'existing_text', 'pdf_regions', 'visual'})

    def test_budget_stops_before_provider_call_and_failure_consumes_one(self):
        engine = evaluation.ObservedHermes({'model': 'synthetic', 'provider': 'synthetic'})
        with patch.object(evaluation.layout.WSLHermesCLIEngine, '_invoke', side_effect=evaluation.layout.JobOpsError('AI_ENGINE_FAILED', 'private text')):
            with self.assertRaises(evaluation.layout.JobOpsError):
                engine._invoke({'task': 'SYNTHETIC'})
        self.assertEqual(len(engine.calls), 1)
        self.assertNotIn('private text', str(engine.calls))
        engine.calls = [{}] * evaluation.CALL_LIMIT
        with patch.object(evaluation.layout.WSLHermesCLIEngine, '_invoke') as invoke:
            with self.assertRaises(evaluation.layout.JobOpsError):
                engine._invoke({'task': 'SYNTHETIC'})
            invoke.assert_not_called()

    def test_observer_retains_malformed_synthetic_response_for_real_validation(self):
        engine = evaluation.ObservedHermes({'model': 'synthetic', 'provider': 'synthetic'})
        envelope = {'result': {'content': 'not JSON'}, 'toolSummary': {'calls': 0}}
        with patch.object(evaluation.layout.WSLHermesCLIEngine, '_invoke', return_value=envelope):
            self.assertIs(engine._invoke({'task': 'SYNTHETIC', 'line_numbered_document': ['2\tSynthetic line']}), envelope)
        self.assertEqual(engine.responses[0]['result_content'], 'not JSON')
        self.assertEqual(engine.responses[0]['line_number_start'], 2)
        self.assertEqual(engine.responses[0]['source_lines'], ['Synthetic line'])

    def test_budget_configuration_cannot_expand_without_code_review(self):
        for limit in (0, 19, True, '18'):
            with self.assertRaises(ValueError):
                evaluation.layout.ObservedHermes({'model': 'synthetic', 'provider': 'synthetic'}, request_limit=limit)


if __name__ == '__main__':
    unittest.main()
