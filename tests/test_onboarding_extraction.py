from __future__ import annotations

import unittest

from jobops.onboarding_extraction import reconstruct_blocks, structured_claim_candidates


class OnboardingExtractionTests(unittest.TestCase):
    def test_project_layout_noise_is_not_promoted_line_by_line(self) -> None:
        text = """PUBLIC-SAFE WORK SAMPLE PAGE 1
WORKSTREAM DECISION DELIVERABLE VALUE CREATED
I designed a governed workflow that connected research, approvals, and
implementation so the team could review every decision.
SIGNAL 01 SIGNAL 02 SIGNAL 03
The model deliberately separates verified public facts and estimates.
Built a synthetic evaluation set across 4,000 records and improved review accuracy by 20%.
"""
        candidates, summary = structured_claim_candidates(text, source_id="SRC-SYNTHETIC", source_type="project_case")
        statements = [item["statement"] for item in candidates]
        self.assertLess(len(candidates), len(text.splitlines()))
        self.assertTrue(any("connected research" in item for item in statements))
        self.assertTrue(any("4,000 records" in item for item in statements))
        self.assertFalse(any("PAGE 1" in item or "SIGNAL 01" in item or item.startswith("The model") for item in statements))
        self.assertGreater(summary["filtered_noise_lines"] + summary["non_claim_blocks"], 0)

    def test_resume_continuation_is_reconstructed_as_one_block(self) -> None:
        blocks, _ = reconstruct_blocks(
            "Led product strategy and translated research\n"
            "into a prioritized implementation roadmap.\n"
            "Built a second complete achievement."
        )
        statements = [item["text"] for item in blocks if item.get("kind") != "heading"]
        self.assertEqual(len(statements), 2)
        self.assertIn("translated research into a prioritized", statements[0])

    def test_resume_headers_dates_markup_and_urls_are_not_claims(self) -> None:
        text = """Founder December 2020 - Present
| variable | value |
https://example.test/profile
Built a 4,000 merchant database and improved conversion by 20%.
"""
        candidates, _ = structured_claim_candidates(text, source_id="SRC-SYNTHETIC", source_type="resume")
        statements = [item["statement"] for item in candidates]
        self.assertEqual(len(statements), 1)
        self.assertIn("4,000 merchant", statements[0])


if __name__ == "__main__":
    unittest.main()
