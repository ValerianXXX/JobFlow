from __future__ import annotations

import json
import unittest

import _support  # noqa: F401
from jobops.ai_runtime import LocalSubprocessAIEngine, _bounded_statement_grounding, _statement_grounding_report


class AIQuantityBindingTests(unittest.TestCase):
    def reject(self, source: str, candidate: str) -> None:
        report = _statement_grounding_report(candidate, source)
        self.assertEqual(report['unsupported_number_count'], 0)
        self.assertFalse(report['valid'])

    def test_currency_identity_cannot_change_with_the_same_coefficient(self):
        for original, changed in [('EUR', 'USD'), ('GBP', 'CAD'), ('CNY', 'JPY')]:
            with self.subTest(original=original, changed=changed):
                self.reject(f'The team managed a {original} 5000 budget for the synthetic pilot.',
                            f'The team managed a {changed} 5000 budget for the synthetic pilot.')
        self.reject('团队管理了合成项目的5000欧元预算。', '团队管理了合成项目的5000美元预算。')

    def test_monetary_scale_cannot_be_dropped_or_inflated(self):
        for original, changed in [('million', ''), ('thousand', 'million'), ('billion', 'million')]:
            with self.subTest(original=original, changed=changed):
                self.reject(f'The team managed USD 3 {original} in synthetic annual revenue.',
                            f'The team managed USD 3 {changed} in synthetic annual revenue.')
        self.reject('团队管理了合成项目的3万元预算。', '团队管理了合成项目的3元预算。')

    def test_nonmonetary_explicit_scale_remains_part_of_the_quantity(self):
        self.reject('The applicant analyzed 3 million records for the synthetic research project.',
                    'The applicant analyzed 3 records for the synthetic research project.')
        self.reject('申请人分析了合成研究项目中的3万条记录并保存结果。',
                    '申请人分析了合成研究项目中的3条记录并保存结果。')

    def test_time_units_cannot_change_with_the_same_digits(self):
        for original, changed in [('milliseconds', 'seconds'), ('minutes', 'hours'), ('hours', 'days')]:
            with self.subTest(original=original, changed=changed):
                self.reject(f'The applicant reduced latency by 20 {original} in the synthetic benchmark.',
                            f'The applicant reduced latency by 20 {changed} in the synthetic benchmark.')
        self.reject('申请人在合成测试中将处理延迟减少了20毫秒并保存结果。',
                    '申请人在合成测试中将处理延迟减少了20秒并保存结果。')

    def test_percentage_points_percent_and_basis_points_are_distinct(self):
        for original, changed in [('percentage points', 'percent'), ('basis points', 'percent'), ('percent', '')]:
            with self.subTest(original=original, changed=changed):
                self.reject(f'The applicant recorded a 12 {original} change in the synthetic measure.',
                            f'The applicant recorded a 12 {changed} change in the synthetic measure.')
        self.reject('申请人在合成项目中记录了12个百分点的变化。', '申请人在合成项目中记录了12%的变化。')

    def test_matching_literal_metrics_cannot_exchange_their_values(self):
        self.reject('The applicant recorded 25% growth and 40% retention in the synthetic report.',
                    'The applicant recorded 40% growth and 25% retention in the synthetic report.')
        self.reject('申请人在合成报告中记录了25%的增长率和40%的留存率。',
                    '申请人在合成报告中记录了40%的增长率和25%的留存率。')

    def test_explicit_negative_quantities_cannot_lose_their_sign(self):
        self.reject('The applicant recorded a -12% change in the synthetic measure.',
                    'The applicant recorded a 12% change in the synthetic measure.')
        self.reject('The applicant recorded a 12% change in the synthetic measure.',
                    'The applicant recorded a -12% change in the synthetic measure.')

    def test_literal_positive_formats_and_aliases_remain_usable(self):
        pairs = [
            ('The team managed USD 5,000.00 for the synthetic pilot.', 'The team managed USD 5000 for the synthetic pilot.'),
            ('The applicant measured 20 milliseconds of synthetic latency.', 'The applicant measured 20 ms of synthetic latency.'),
            ('The applicant recorded 12 percent growth in the synthetic report.', 'The applicant recorded 12% growth in the synthetic report.'),
            ('The team managed USD 3 million for the synthetic pilot.', 'The team managed USD 3 million for the synthetic pilot.'),
            ('团队管理了合成项目的３万元预算并保留核验记录。', '团队管理了合成项目的3万元预算并保留核验记录。'),
            ('The applicant worked from 2023-02 through 2025-06 on the synthetic project.',
             'The applicant worked from 2023-02 through 2025-06 on the synthetic project.'),
        ]
        for source, candidate in pairs:
            with self.subTest(candidate=candidate):
                self.assertTrue(_statement_grounding_report(candidate, source)['valid'])

    def test_reordered_plain_lists_are_not_mistaken_for_changed_metrics(self):
        source = 'The applicant reviewed 12 and 24 records in the synthetic evaluation batches.'
        candidate = 'The applicant reviewed 24 and 12 records in the synthetic evaluation batches.'
        self.assertTrue(_statement_grounding_report(candidate, source)['valid'])

    def test_multiple_source_sentences_keep_each_supported_literal_metric_order(self):
        first = 'The applicant recorded 25% growth and 40% retention in the synthetic report.'
        second = 'The applicant recorded 40% growth and 25% retention in the synthetic report.'
        self.assertTrue(_statement_grounding_report(second, first + ' ' + second)['valid'])

    def test_wrapped_unit_can_complete_the_original_citation(self):
        candidate = 'The applicant reduced synthetic benchmark latency by 20 milliseconds.'
        report = _bounded_statement_grounding(candidate,
            ['The applicant reduced synthetic benchmark latency by 20', 'milliseconds.'],
            line_start=1, line_end=1, line_number_start=1)
        self.assertTrue(report['valid'])
        self.assertEqual((report['line_start'], report['line_end']), (1, 2))
        self.assertEqual(report['citation_adjustment'], 'ADJACENT_WRAPPED_LINES')

    def test_explicit_unit_conflict_cannot_borrow_a_neighboring_quantity(self):
        report = _bounded_statement_grounding('The applicant measured 20 seconds in the synthetic benchmark.',
            ['The applicant measured 20 milliseconds in the synthetic benchmark', 'and another worker measured 20 seconds.'],
            line_start=1, line_end=1, line_number_start=1)
        self.assertFalse(report['valid'])
        self.assertFalse(report['adjacent_line_expansion_attempted'])

    def test_whole_preview_filters_bad_units_without_rewriting_or_approving_other_candidates(self):
        source = 'The team managed EUR 5000 for the synthetic pilot.'
        bad = 'The team managed USD 5000 for the synthetic pilot.'
        value = {'schema_version': 2,
            'entities': [{'entity_key':'synthetic', 'entity_type':'project', 'organization':'Synthetic Market Lab',
                          'role':'Project Analyst', 'start_date':'', 'end_date':'', 'line_start':1, 'line_end':1}],
            'candidates': [{'entity_key':'synthetic', 'category':'project', 'claim_kind':'responsibility',
                            'confidence':'HIGH', 'statement':text, 'line_start':2, 'line_end':2} for text in [bad, source]]}
        original = json.dumps(value, sort_keys=True)
        diagnostics = {}
        result = LocalSubprocessAIEngine._validated_candidates(value, source_id='SRC-SYNTHETIC-UNITS',
            source_lines=['Synthetic Market Lab Project Analyst', source], quality_diagnostics=diagnostics)
        self.assertEqual([item['statement'] for item in result], [source])
        self.assertFalse(result[0]['selected'])
        self.assertEqual(diagnostics, {'filtered_candidate_reasons': {'UNSUPPORTED_NUMERIC_CONTEXT': 1}})
        self.assertNotIn(bad, json.dumps(diagnostics))
        self.assertEqual(json.dumps(value, sort_keys=True), original)


if __name__ == '__main__':
    unittest.main()
