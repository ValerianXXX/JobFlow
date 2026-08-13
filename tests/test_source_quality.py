from __future__ import annotations

import unittest

import _support  # noqa: F401  # Adds the project src directory to sys.path.
from jobops.source_quality import document_quality_rank, document_text_preflight, safe_ai_failure_category


class SourceQualityTests(unittest.TestCase):
    def test_empty_pdf_is_an_ocr_failure_without_document_content(self) -> None:
        report = document_text_preflight("", extension=".pdf", page_count=2)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("NO_EXTRACTABLE_TEXT", report["reason_codes"])
        self.assertIn("OCR_REQUIRED", report["reason_codes"])
        self.assertFalse(report["contains_document_text"])

    def test_clean_text_ranks_above_custom_font_glyph_output(self) -> None:
        clean = document_text_preflight(
            "At Synthetic Studio, a Project Analyst built a complete workflow.",
            extension=".pdf", page_count=1,
        )
        corrupt = document_text_preflight("\ue001\n\ue002\n\ue003\nA\nB\nC\n" * 20, extension=".pdf", page_count=1)
        self.assertGreater(document_quality_rank(clean), document_quality_rank(corrupt))

    def test_ai_failure_categories_are_stable_and_content_free(self) -> None:
        category = safe_ai_failure_category(
            "AI_RESPONSE_REPAIR_FAILED", {"failure_category": "CITED_LINE_GROUNDING"},
        )
        self.assertEqual(category, "CITED_LINE_GROUNDING")


if __name__ == "__main__":
    unittest.main()
