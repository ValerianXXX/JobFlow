from pathlib import Path
import unittest

from jobops.orchestrator import _read_jd


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class JDInputFormatsTests(unittest.TestCase):
    def test_txt_html_pdf_and_saved_page_snapshot_are_local_and_readable(self):
        cases = (
            (FIXTURES / "synthetic-forward-jd.txt", "txt", "txt", "Entry Level Data Analyst"),
            (FIXTURES / "synthetic-official-careers.html", "html", "html", "Synthetic"),
            (FIXTURES / "synthetic-forward-jd.pdf", "pdf", "pdf", "Synthetic Data Analyst"),
            (FIXTURES / "synthetic-forward-page-snapshot.json", "snapshot", "page_snapshot", "Python and SQL"),
        )
        for path, requested, expected, needle in cases:
            with self.subTest(source_type=requested):
                text, source_type, source_url = _read_jd(path, requested)
                self.assertEqual(source_type, expected)
                self.assertIn(needle.casefold(), text.casefold())
                if requested == "snapshot":
                    self.assertEqual(source_url, "https://example.com/careers/entry-data-analyst")
                else:
                    self.assertIsNone(source_url)


if __name__ == "__main__":
    unittest.main()
