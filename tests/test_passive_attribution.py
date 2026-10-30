import unittest
import _support
from jobops.ai_runtime import (AI_PROTOCOL_VERSION, LocalSubprocessAIEngine,
                              _bounded_statement_grounding, _statement_grounding_report)


class PassiveAttributionTests(unittest.TestCase):
    def test_observed_unassigned_action_cannot_become_applicant_work(self):
        source = "An ambiguous observation was marked unresolved instead of being assigned a favorable label."
        statement = "The applicant marked an ambiguous observation as unresolved instead of assigning it a favorable label."
        report = _statement_grounding_report(statement, source)
        self.assertFalse(report["valid"])
        self.assertEqual(report.get("unsupported_actor_inference_count"), 1)

    def test_bounded_passive_actions_require_an_explicit_matching_actor(self):
        for source, statement in (
            ("The report was prepared from the approved records.", "The applicant prepared the report from the approved records."),
            ("The 24 records were reviewed using a written codebook.", "The team reviewed the 24 records using a written codebook."),
            ("The report was reviewed by the team.", "The applicant reviewed the report."),
            ("一条有歧义的观察被标为未解决，没有被改写成有利标签。", "申请人将一条有歧义的观察标为未解决，没有改写成有利标签。"),
            ("这条观察被团队标注为未解决并保留记录。", "申请人将这条观察标注为未解决并保留记录。"),
        ):
            with self.subTest(statement=statement):
                report = _statement_grounding_report(statement, source)
                self.assertFalse(report["valid"])
                self.assertGreater(report.get("unsupported_actor_inference_count", 0), 0)

    def test_passive_wording_explicit_agents_and_unrelated_actions_remain_valid(self):
        for source, statement in (
            ("An ambiguous observation was marked unresolved instead of being assigned a favorable label.",
             "An ambiguous observation was marked unresolved instead of being assigned a favorable label."),
            ("The report was reviewed by the applicant using the approved record checklist.", "The applicant reviewed the report using the approved record checklist."),
            ("The report was prepared by the candidate from the approved store records.", "The applicant prepared the report from the approved store records."),
            ("The applicant reviewed the synthetic report using approved records; a second report was reviewed.", "The applicant reviewed the synthetic report using approved records."),
            ("The applicant reviewed the synthetic report using approved records; its status was recorded.", "The applicant reviewed the synthetic report using approved records."),
            ("申请人将这条有歧义的观察标为未解决并保留记录。", "申请人将这条有歧义的观察标为未解决并保留记录。"),
            ("这条有歧义的观察被申请人标注为未解决并保留记录。", "申请人将这条有歧义的观察标注为未解决并保留记录。"),
        ):
            with self.subTest(statement=statement):
                self.assertTrue(_statement_grounding_report(statement, source)["valid"])

    def test_another_object_with_the_same_verb_does_not_assign_the_passive_action(self):
        source = "The report was prepared from approved store records; the applicant prepared charts from survey responses."
        report = _statement_grounding_report("The applicant prepared the report from approved store records.", source)
        self.assertFalse(report["valid"])
        self.assertEqual(report.get("unsupported_actor_inference_count"), 1)

    def test_adjacent_lines_cannot_supply_an_inferred_actor(self):
        report = _bounded_statement_grounding("The applicant prepared the report from the approved records.",
            ["The report was prepared from the approved records", "the applicant prepared the charts."],
            line_start=1, line_end=1, line_number_start=1)
        self.assertFalse(report["valid"])
        self.assertFalse(report["adjacent_line_expansion_attempted"])

    def test_filter_keeps_supported_candidate_and_records_only_a_reason_count(self):
        source = "An ambiguous observation was marked unresolved instead of being assigned a favorable label."
        value = {"schema_version": AI_PROTOCOL_VERSION, "entities": [], "candidates": [
            {"category": "summary", "claim_kind": "summary", "confidence": "HIGH", "statement": statement,
             "line_start": 1, "line_end": 1} for statement in (
                "The applicant marked an ambiguous observation as unresolved instead of assigning it a favorable label.", source)]}
        diagnostics = {}
        result = LocalSubprocessAIEngine._validated_candidates(value, source_id="SRC-SYNTHETIC-PASSIVE",
                                                              source_lines=[source], quality_diagnostics=diagnostics)
        self.assertEqual([row["statement"] for row in result], [source])
        self.assertFalse(result[0]["selected"])
        self.assertEqual(diagnostics, {"filtered_candidate_reasons": {"UNSUPPORTED_ACTOR": 1}})


if __name__ == "__main__":
    unittest.main()
