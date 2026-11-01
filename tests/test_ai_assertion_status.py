"""Frozen synthetic source-status counterexamples, not a model benchmark."""
import hashlib
import json
from pathlib import Path
import unittest

import _support
from jobops.ai_runtime import AI_PROTOCOL_VERSION, LocalSubprocessAIEngine, _bounded_statement_grounding, _statement_grounding_report


FIXTURE = Path(__file__).parent / "fixtures/assertion_status/cases.json"


class AssertionStatusTests(unittest.TestCase):
    def test_frozen_contradictions_and_supported_controls(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                         "93d3076899fe29a0aea20908fe009793c0a638c4d5318480ee82597672861450")
        cases = json.loads(FIXTURE.read_text(encoding="utf8"))["cases"]
        self.assertEqual(len(cases), 60)
        for case in cases:
            with self.subTest(case=case["id"]):
                actual = _statement_grounding_report(case["statement"], case["source"])
                self.assertEqual(actual["valid"], case["expected_valid"])

    def test_candidate_filter_does_not_discard_valid_candidate_or_rewrite_source(self):
        source = "The applicant did not manage the project budget."
        lines = ["Synthetic Project", "Analyst 2023-2024", source]
        value = {"schema_version": AI_PROTOCOL_VERSION,
                 "entities": [{"entity_key": "synthetic", "entity_type": "project", "organization": "Synthetic Project",
                               "role": "Analyst", "start_date": "2023", "end_date": "2024", "line_start": 1, "line_end": 2}],
                 "candidates": [{"entity_key": "synthetic", "category": "project", "claim_kind": "responsibility", "confidence": "HIGH",
                                 "statement": statement, "line_start": 3, "line_end": 3} for statement in
                                ("The applicant managed the project budget.", source)]}
        diagnostics = {}
        candidates = LocalSubprocessAIEngine._validated_candidates(value, source_id="SRC-SYNTHETIC-STATUS",
            source_lines=lines, quality_diagnostics=diagnostics)
        self.assertEqual([c["statement"] for c in candidates], [source])
        self.assertFalse(candidates[0]["selected"])
        self.assertEqual(lines, ["Synthetic Project", "Analyst 2023-2024", source])
        self.assertEqual(diagnostics, {"filtered_candidate_reasons": {"SOURCE_ASSERTION_CONFLICT": 1}})
        self.assertNotIn("budget", json.dumps(diagnostics))

    def test_all_conflicting_candidates_can_be_filtered_without_rejecting_source_protocol(self):
        value = {"schema_version": AI_PROTOCOL_VERSION,
                 "entities": [{"entity_key": "synthetic", "entity_type": "project", "organization": "Synthetic Project",
                               "role": "Analyst", "start_date": "2023", "end_date": "2024", "line_start": 1, "line_end": 2}],
                 "candidates": [{"entity_key": "synthetic", "category": "project", "claim_kind": "responsibility", "confidence": "HIGH",
                                 "statement": "The applicant managed the project budget.", "line_start": 3, "line_end": 3}]}
        diagnostics = {}
        candidates = LocalSubprocessAIEngine._validated_candidates(value, source_id="SRC-SYNTHETIC-STATUS",
            source_lines=["Synthetic Project", "Analyst 2023-2024", "The applicant did not manage the project budget."],
            quality_diagnostics=diagnostics)
        self.assertEqual(candidates, [])
        self.assertEqual(diagnostics, {"filtered_candidate_reasons": {"SOURCE_ASSERTION_CONFLICT": 1}})

    def test_explicit_conflict_cannot_borrow_an_adjacent_positive_clause(self):
        report = _bounded_statement_grounding("The applicant managed the project budget.",
            ["The applicant did not manage the project budget", "and the applicant managed the project budget."],
            line_start=1, line_end=1, line_number_start=1)
        self.assertFalse(report["valid"])
        self.assertFalse(report["adjacent_line_expansion_attempted"])
        self.assertEqual(report["line_start"], 1)
        self.assertEqual(report["line_end"], 1)


if __name__ == "__main__":
    unittest.main()
