from pathlib import Path
import unittest
from unittest import mock

from _support import project_temp
from jobops.cli import _read_bounded_local_bytes
from jobops.errors import JobOpsError
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

    def test_local_jd_inputs_are_bounded_before_parsing(self):
        with project_temp() as temp:
            oversized = temp / "oversized.txt"
            oversized.write_bytes(b"12345")
            with mock.patch("jobops.orchestrator.MAX_JD_SOURCE_BYTES", 4), self.assertRaises(JobOpsError) as caught:
                _read_jd(oversized, "txt")
            self.assertEqual(caught.exception.code, "JD_INPUT_TOO_LARGE")

            invalid = temp / "invalid.json"
            invalid.write_text("[]", encoding="utf-8")
            with self.assertRaises(JobOpsError) as caught:
                _read_jd(invalid, "snapshot")
            self.assertEqual(caught.exception.code, "JD_SNAPSHOT_INVALID")

            many_events = temp / "many-events.html"
            many_events.write_text("<p>one</p><p>two</p>", encoding="utf-8")
            with mock.patch("jobops.orchestrator.MAX_JD_HTML_EVENTS", 2), self.assertRaises(JobOpsError) as caught:
                _read_jd(many_events, "html")
            self.assertEqual(caught.exception.code, "JD_HTML_EVENT_LIMIT_EXCEEDED")

            with self.assertRaises(JobOpsError) as caught:
                _read_bounded_local_bytes(oversized, 4, "SYNTHETIC_INPUT_TOO_LARGE")
            self.assertEqual(caught.exception.code, "SYNTHETIC_INPUT_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
