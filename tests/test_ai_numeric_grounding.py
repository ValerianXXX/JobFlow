from __future__ import annotations

import unittest

import _support  # noqa: F401  # Adds the project src directory to sys.path.
from jobops.ai_runtime import LocalSubprocessAIEngine, _numbers


def _payload(statement: str, *, line_start: int = 1, line_end: int = 1) -> dict[str, object]:
    return {
        "schema_version": 2,
        "entities": [{
            "entity_key": "synthetic-project",
            "entity_type": "project",
            "organization": "Synthetic Market Lab",
            "role": "Project Analyst",
            "start_date": "",
            "end_date": "",
            "line_start": 1,
            "line_end": 1,
        }],
        "candidates": [{
            "statement": statement,
            "category": "project",
            "claim_kind": "achievement",
            "entity_key": "synthetic-project",
            "confidence": "HIGH",
            "line_start": line_start,
            "line_end": line_end,
            "reason": "Synthetic numeric grounding case.",
        }],
    }


class AINumericGroundingTests(unittest.TestCase):
    def _validate(
        self,
        statement: str,
        source_lines: list[str],
        *,
        line_end: int = 1,
        diagnostics: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return LocalSubprocessAIEngine._validated_candidates(
            _payload(statement, line_end=line_end),
            source_id="SRC-NUMERIC-SYNTHETIC",
            source_lines=source_lines,
            quality_diagnostics=diagnostics,
        )

    def test_safe_grouping_and_full_width_formats_are_equivalent_but_flagged_for_review(self) -> None:
        statement = "At Synthetic Market Lab, a Project Analyst built a 4,000+ merchant database."
        for source_number in ("4 000", "４，０００", "4000"):
            with self.subTest(source_number=source_number):
                candidates = self._validate(
                    statement,
                    [f"At Synthetic Market Lab, a Project Analyst built a {source_number}+ merchant database."],
                )
                self.assertEqual(len(candidates), 1)
                provenance = candidates[0]["provenance"]
                self.assertGreaterEqual(provenance["numeric_format_normalizations"], 1)
                self.assertTrue(provenance["human_confirmation_required"])
                self.assertFalse(candidates[0]["selected"])

    def test_decimal_trailing_zero_is_format_equivalent(self) -> None:
        self.assertEqual(_numbers("Measured 1.20 seconds."), _numbers("Measured 1.2 seconds."))

    def test_pdf_numeric_wrap_expands_only_the_adjacent_sentence_lines(self) -> None:
        statement = (
            "At Synthetic Market Lab, a Project Analyst built a 4,000+ merchant database "
            "and converted fragmented data into screening criteria."
        )
        candidates = self._validate(statement, [
            "At Synthetic Market Lab, a Project Analyst built a 4,",
            "000+ merchant database and converted fragmented data into screening criteria.",
        ])
        provenance = candidates[0]["provenance"]
        self.assertEqual((provenance["line_start"], provenance["line_end"]), (1, 2))
        self.assertEqual(provenance["citation_adjustment"], "ADJACENT_WRAPPED_LINES")
        self.assertEqual(provenance["numeric_format_normalizations"], 1)

    def test_new_or_calculated_number_is_filtered_without_rejecting_the_source(self) -> None:
        cases = (
            (
                "At Synthetic Market Lab, a Project Analyst built a 5,000 merchant database.",
                ["At Synthetic Market Lab, a Project Analyst built a 4,000 merchant database."],
            ),
            (
                "At Synthetic Market Lab, a Project Analyst increased throughput by 100%.",
                ["At Synthetic Market Lab, a Project Analyst increased throughput from 10 to 20 cases."],
            ),
        )
        for statement, source_lines in cases:
            with self.subTest(statement=statement):
                diagnostics: dict[str, object] = {}
                self.assertEqual(self._validate(statement, source_lines, diagnostics=diagnostics), [])
                self.assertEqual(
                    diagnostics,
                    {"filtered_candidate_reasons": {"UNSUPPORTED_NUMBER": 1}},
                )

    def test_number_from_a_separate_sentence_is_filtered_not_borrowed(self) -> None:
        statement = "At Synthetic Market Lab, a Project Analyst built a 5,000 merchant database."
        diagnostics: dict[str, object] = {}
        self.assertEqual(self._validate(statement, [
            "At Synthetic Market Lab, a Project Analyst built a merchant database.",
            "A separate project processed 5,000 records.",
        ], diagnostics=diagnostics), [])
        self.assertEqual(
            diagnostics,
            {"filtered_candidate_reasons": {"UNSUPPORTED_NUMBER": 1}},
        )


if __name__ == "__main__":
    unittest.main()
