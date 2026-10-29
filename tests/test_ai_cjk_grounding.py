from __future__ import annotations
import unittest
import _support
from jobops.ai_runtime import AI_PROTOCOL_VERSION, LocalSubprocessAIEngine, _statement_grounding_report


class CjkGroundingTests(unittest.TestCase):
    def test_uncased_chinese_sentence_is_not_an_all_caps_heading(self):
        statement = "申请人在合成青杉实验室担任分析员，团队核验了14家门店。"
        report = _statement_grounding_report(statement, statement)
        self.assertTrue(report["valid"])

    def test_cjk_with_short_latin_acronym_is_not_an_all_caps_heading(self):
        statement = "申请人在合成研究项目中检查了12份PDF文件并记录检查结果。"
        self.assertTrue(_statement_grounding_report(statement, statement)["valid"])

    def test_uppercase_latin_heading_still_fails_with_or_without_cjk(self):
        for statement in ("SYNTHETIC TEAM PROJECT DELIVERY SUMMARY.", "SYNTHETIC TEAM 项目交付汇总及能力说明。"):
            with self.subTest(statement=statement):
                self.assertFalse(_statement_grounding_report(statement, statement)["valid"])

    def test_chinese_still_requires_length_punctuation_and_exact_numbers(self):
        source = "申请人在合成青杉实验室担任分析员，团队核验了14家门店。"
        for statement in ("核验了14家门店。", source[:-1], source.replace("14", "15")):
            with self.subTest(statement=statement):
                self.assertFalse(_statement_grounding_report(statement, source)["valid"])

    def test_unrelated_chinese_evidence_is_rejected(self):
        statement = "申请人在合成青杉实验室担任分析员，团队核验了14家门店。"
        self.assertFalse(_statement_grounding_report(statement, "合成海港大学颁发研究学位并开设课程。")["valid"])

    def test_full_candidate_validator_keeps_chinese_fact_unselected(self):
        lines = ["合成青杉实验室", "分析员 | 2023-2024", "团队核验了14家门店。"]
        value = {"schema_version": AI_PROTOCOL_VERSION,
                 "entities": [{"entity_key": "cedar", "entity_type": "work", "organization": lines[0], "role": "分析员",
                               "start_date": "2023", "end_date": "2024", "line_start": 1, "line_end": 2}],
                 "candidates": [{"entity_key": "cedar", "category": "work", "claim_kind": "achievement", "confidence": "HIGH",
                                 "statement": "合成青杉实验室的分析员所属团队核验了14家门店。", "line_start": 1, "line_end": 3}]}
        result = LocalSubprocessAIEngine._validated_candidates(value, source_id="SRC-SYNTHETIC-CJK", source_lines=lines)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["selected"])
        self.assertTrue(result[0]["provenance"]["human_confirmation_required"])


if __name__ == "__main__":
    unittest.main()
