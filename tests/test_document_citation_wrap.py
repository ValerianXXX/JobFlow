"""Native synthetic citation wrap; order/ownership and numeric guards remain."""
from __future__ import annotations
import unittest
from _support import PROJECT
from jobops.document_qa import automated_visual_probe, reading_order_issues, structural_qa


class DocumentCitationWrapTests(unittest.TestCase):
    paragraph = "Example Analytics Lab builds documented research workflows. (https://example.com/careers/analyst-first-use, accessed 2026-11-01)."

    def test_actual_native_cover_letter_retains_reading_order(self):
        root = PROJECT / "tests/fixtures/native-citation-wrap-v1"
        pages = [root / "page-1.png"]
        result = structural_qa(root / "cover-letter.docx", root / "cover-letter.pdf", pages,
                               visual_record=automated_visual_probe(pages), page_limit=2)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.reading_order_issue_count, 0)
        self.assertEqual(result.text_difference_ratio, 0.0168)

    def test_layout_wraps_at_retained_url_punctuation_are_equivalent(self):
        for split in ("analyst-", "https://", "example.", "careers/"):
            for newline in ("\n", "\r\n"):
                with self.subTest(split=split, newline=repr(newline)):
                    wrapped = self.paragraph.replace(split, split + newline)
                    self.assertEqual(reading_order_issues(self.paragraph, wrapped), 0)

    def test_hyphenated_text_wrap_keeps_its_separator(self):
        original = "The applicant documented an evidence-based process for twelve stores without claiming team savings."
        self.assertEqual(reading_order_issues(original, original.replace("evidence-based", "evidence-\nbased")), 0)
        self.assertEqual(reading_order_issues(original, original.replace("evidence-based", "evidencebased")), 1)

    def test_changed_or_reordered_citation_atoms_remain_rejected(self):
        for changed in (
            self.paragraph.replace("example.com/careers", "careers.com/example"),
            self.paragraph.replace("analyst-first", "analyst/first"),
            self.paragraph.replace("analyst-first", "analystfirst"),
            self.paragraph.replace("2026-11-01", "2026-01-11"),
        ):
            with self.subTest(changed=changed):
                self.assertEqual(reading_order_issues(self.paragraph, changed), 1)

    def test_digits_and_ordinary_words_are_never_glued_across_whitespace(self):
        original = "The applicant reviewed 12502 dollars with the team and checked twelve stores."
        for changed in (original.replace("12502", "12\n502"), original.replace("the team", "theteam"),
                        original.replace("twelve stores", "twelvestores")):
            with self.subTest(changed=changed):
                self.assertEqual(reading_order_issues(original, changed), 1)


if __name__ == "__main__":
    unittest.main()
