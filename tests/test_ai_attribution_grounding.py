import unittest
import _support
from jobops.ai_runtime import AI_PROTOCOL_VERSION, LocalSubprocessAIEngine, _statement_grounding_report


class AttributionGroundingTests(unittest.TestCase):
    def test_direct_actor_replacement_is_rejected_even_with_identical_numbers(self):
        for source, statement in (
            ("The team managed a USD 12637 budget; the applicant had no spending authority.", "The applicant managed a USD 12637 budget."),
            ("The AI drafted the initial summary.", "The applicant drafted the initial summary."),
            ("团队核验了41家合成门店并记录检查结果。", "申请人核验了41家合成门店并记录检查结果。"),
            ("申请人独立编写了29份报告并记录检查结果。", "团队独立编写了29份报告并记录检查结果。"),
        ):
            with self.subTest(statement=statement):
                report = _statement_grounding_report(statement, source)
                self.assertFalse(report["valid"])

    def test_supported_actor_and_shared_responsibility_remain_reviewable(self):
        for source, statement in (
            ("The team managed a USD 12637 budget; the applicant had no spending authority.", "The team managed a USD 12637 budget."),
            ("The candidate coded 331 public records using a written codebook.", "The applicant coded 331 public records using a written codebook."),
            ("The team reviewed the synthetic report. The applicant reviewed the synthetic report.", "The applicant reviewed the synthetic report."),
            ("The applicant's team managed a USD 12637 budget.", "The applicant's team managed a USD 12637 budget."),
            ("团队核验了41家合成门店并记录检查结果。", "团队核验了41家合成门店并记录检查结果。"),
        ):
            with self.subTest(statement=statement):
                self.assertTrue(_statement_grounding_report(statement, source)["valid"])

    def test_whole_preview_retains_source_and_counts_rejected_candidate_without_wording(self):
        source = "The team managed a USD 12637 budget; the applicant had no spending authority."
        value = {"schema_version": AI_PROTOCOL_VERSION,
                 "entities": [{"entity_key": "synthetic", "entity_type": "project", "organization": "Synthetic Project",
                               "role": "Analyst", "start_date": "2023", "end_date": "2024", "line_start": 1, "line_end": 2}],
                 "candidates": [{"entity_key": "synthetic", "category": "project", "claim_kind": "responsibility", "confidence": "HIGH",
                                 "statement": statement, "line_start": 3, "line_end": 3} for statement in
                                ("The applicant managed a USD 12637 budget.", "The team managed a USD 12637 budget.")]}
        diagnostics = {}
        result = LocalSubprocessAIEngine._validated_candidates(value, source_id="SRC-SYNTHETIC-ATTRIBUTION",
            source_lines=["Synthetic Project", "Analyst 2023-2024", source], quality_diagnostics=diagnostics)
        self.assertEqual([c["statement"] for c in result], ["The team managed a USD 12637 budget."])
        self.assertFalse(result[0]["selected"])
        self.assertEqual(diagnostics, {"filtered_candidate_reasons": {"ATTRIBUTION_CONFLICT": 1}})


if __name__ == "__main__":
    unittest.main()
