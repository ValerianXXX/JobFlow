"""Frozen source-scope counterexamples; no provider calls or inferred approval."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

import _support
from jobops.ai_runtime import AI_PROTOCOL_VERSION, LocalSubprocessAIEngine, _bounded_statement_grounding, _statement_grounding_report

FIXTURE = Path(__file__).parent / 'fixtures/assertion_scope/cases.json'
FIXTURE_SHA256 = '52cb85ff82c1350e60a4946e688127eb4a3a6174fc7453321c60bf784295fa6c'


class AssertionScopeTests(unittest.TestCase):
    def test_frozen_promotions_and_supported_controls(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes().replace(b'\r\n', b'\n')).hexdigest(), FIXTURE_SHA256)
        rows = json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']
        self.assertEqual(len(rows), 84)
        self.assertEqual(sum(not row['expected_valid'] for row in rows), 36)
        for row in rows:
            with self.subTest(case=row['id']):
                self.assertEqual(_statement_grounding_report(row['statement'], row['source'])['valid'], row['expected_valid'])

    def test_original_known_limit_observations_are_now_rejected_without_rewriting_them(self):
        path = _support.PROJECT / 'evaluations/2026-11-01/assertion-known-limits.json'
        prior = path.read_bytes()
        for case in json.loads(prior)['cases']:
            with self.subTest(case=case['id']):
                self.assertTrue(case['diagnostics']['valid'])
                self.assertFalse(_statement_grounding_report(case['statement'], case['source'])['valid'])
        self.assertEqual(path.read_bytes(), prior)

    def test_candidate_filter_preserves_proposal_and_original_source(self):
        source = 'The applicant proposed a deployment of the public service to production.'
        statement = 'The applicant deployed the public service to production.'
        lines = ['Synthetic Scope Review', 'Analyst 2023-2024', source]
        value = {'schema_version': AI_PROTOCOL_VERSION,
            'entities': [{'entity_key': 'synthetic', 'entity_type': 'project', 'organization': lines[0],
                'role': 'Analyst', 'start_date': '2023', 'end_date': '2024', 'line_start': 1, 'line_end': 2}],
            'candidates': [{'entity_key': 'synthetic', 'category': 'project', 'claim_kind': 'responsibility',
                'confidence': 'HIGH', 'statement': text, 'line_start': 3, 'line_end': 3} for text in [statement, source]]}
        original = deepcopy(value)
        diagnostics = {}
        candidates = LocalSubprocessAIEngine._validated_candidates(value, source_id='SRC-SYNTHETIC-SCOPE',
            source_lines=lines, quality_diagnostics=diagnostics)
        self.assertEqual([c['statement'] for c in candidates], [source])
        self.assertFalse(candidates[0]['selected'])
        self.assertEqual(value, original)
        self.assertEqual(lines[-1], source)
        self.assertEqual(diagnostics, {'filtered_candidate_reasons': {'SOURCE_ASSERTION_CONFLICT': 1}})
        self.assertNotIn('public service', json.dumps(diagnostics))

    def test_different_literal_actor_is_counted_without_exposing_it(self):
        report = _statement_grounding_report('The applicant completed the synthetic research report.',
            'The synthetic vendor completed the synthetic research report.')
        self.assertFalse(report['valid'])
        self.assertEqual(report['unsupported_actor_inference_count'], 1)
        self.assertNotIn('vendor', json.dumps(report))

    def test_a_narrow_citation_cannot_drop_modal_or_nominal_scope(self):
        for lines, statement in [
            (['The applicant was unable to', 'manage the synthetic project budget.'], 'The applicant managed the synthetic project budget.'),
            (['The applicant proposed a', 'deployment of the public service to production.'], 'The applicant deployed the public service to production.'),
            (['申请人无法', '处理这批合成项目的客户资料和来源记录。'], '申请人已处理这批合成项目的客户资料和来源记录。'),
        ]:
            with self.subTest(lines=lines):
                report = _bounded_statement_grounding(statement, lines, line_start=2, line_end=2, line_number_start=1)
                self.assertFalse(report['valid'])
                self.assertEqual(report['source_assertion_conflict_count'], 1)
                self.assertEqual(report['line_start'], 2)

    def test_a_narrow_citation_cannot_omit_a_different_performer(self):
        report = _bounded_statement_grounding('The applicant processed the public project records.',
            ['The synthetic manager', 'processed the public project records.'],
            line_start=2, line_end=2, line_number_start=1)
        self.assertFalse(report['valid'])
        self.assertEqual(report['unsupported_actor_inference_count'], 1)
        self.assertEqual(report['line_start'], 2)

    def test_actor_context_never_crosses_a_blank_or_bullet_boundary(self):
        for lines in [
            ['The synthetic manager', '', 'processed the public project records.'],
            ['The synthetic manager', '- The applicant processed the public project records.'],
        ]:
            report = _bounded_statement_grounding('The applicant processed the public project records.', lines,
                line_start=len(lines), line_end=len(lines), line_number_start=1)
            self.assertEqual(report['unsupported_actor_inference_count'], 0)
            self.assertFalse(report['adjacent_line_expansion_attempted'])


if __name__ == '__main__':
    unittest.main()
