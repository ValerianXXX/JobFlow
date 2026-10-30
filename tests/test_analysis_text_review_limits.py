from __future__ import annotations
import http.client
import threading
import unittest
from unittest import mock

from _support import project_temp
import test_analysis_text_review as contracts
from jobops.errors import JobOpsError
from jobops.onboarding_server import create_server
from jobops.util import sha256_bytes


class AnalysisTextReviewLimitTests(unittest.TestCase):
    def fixture(self, root, text):
        helper = contracts.AnalysisTextReviewTests()
        self.addCleanup(helper.doCleanups)
        return helper.prepare(root, text)

    def test_character_bound_preserves_every_whole_line_across_windows(self):
        text = "\n".join(f"Synthetic row {i}: " + "合" * 1980 for i in range(40))
        with project_temp() as root:
            service, _, _, payload = self.fixture(root, text)
            collected, start = [], 1
            while start:
                result = service.source_review_text({**payload, "line_start": start})
                self.assertLessEqual(sum(len(row["text"]) for row in result["lines"]), 32768)
                collected.extend(row["text"] for row in result["lines"])
                start = result["next_line"]
            self.assertEqual(collected, text.splitlines())

    def test_single_oversize_line_is_not_truncated_into_false_evidence(self):
        with project_temp() as root:
            service, _, _, payload = self.fixture(root, "Synthetic long line. " * 2000)
            with self.assertRaises(JobOpsError) as caught:
                service.source_review_text(payload)
            self.assertEqual(caught.exception.code, "SOURCE_TEXT_REVIEW_LIMIT")

    def test_chat_export_never_retains_an_analysis_transcript(self):
        with project_temp() as root:
            service, _, _, _ = self.fixture(root, contracts.TEXT)
            summary = {"analysis_mode": "AI_CORE_ENTITY_ANALYSIS", "quality_contract": contracts.AI_QUALITY_CONTRACT,
                       "ai_chunks": 1, "ai_input_characters": 100, "ai_covered_characters": 100, "ai_input_truncated": False}
            with mock.patch.object(service.ai_engine, "analyze_document", return_value=([], summary)):
                result = service._analyze_prepared_text(source_type="chatgpt_export", extension=".zip",
                    source_hash=sha256_bytes(b"synthetic-export"), text=contracts.TEXT,
                    excluded_secret_fragments=0, raw_data=None, intake_mode="STANDARD_MEMORY")
            self.assertIsNone(result["_analysis_transcript"])
            self.assertEqual(result["analysis_text_review"]["status"], "NOT_RETAINED")
            self.assertNotIn(b"Synthetic transcript marker", service.onboarding.read_bytes(result["secure_ref"]))

    def test_new_asset_is_served_only_inside_the_local_session(self):
        with project_temp() as root:
            service, _, _, _ = self.fixture(root, contracts.TEXT)
            server = create_server(service, token="synthetic-text-assets")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for url, status in (("/session/synthetic-text-assets/analysis-text.js", 200), ("/analysis-text.js", 403)):
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    conn.request("GET", url)
                    response = conn.getresponse()
                    self.assertEqual(response.status, status)
                    self.assertIn("no-store", response.getheader("Cache-Control", ""))
                    body = response.read()
                    conn.close()
                    if status == 200:
                        self.assertIn("text/javascript", response.getheader("Content-Type"))
                        self.assertIn(b"JobFlowAnalysisText", body)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
