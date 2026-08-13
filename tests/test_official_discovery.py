from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.official_discovery import discover_official_jobs
from jobops.runtime_schema import validate_named


APPROVED_ATS = ["myworkdayjobs.com", "greenhouse.io", "lever.co"]


class OfflineOfficialDiscoveryTests(unittest.TestCase):
    def test_local_fixture_discovers_company_and_workday_jobs_without_network(self) -> None:
        snapshot = (PROJECT / "tests" / "fixtures" / "synthetic-official-job-list.html").read_bytes()
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch(
            "urllib.request.urlopen", side_effect=AssertionError("network forbidden")
        ):
            report = discover_official_jobs(
                snapshot,
                official_entry_url="https://example.com/careers",
                company_domain="example.com",
                approved_ats_hosts=APPROVED_ATS,
            )
        self.assertEqual(report["candidate_count"], 2)
        self.assertEqual(report["deduplicated_link_count"], 1)
        self.assertEqual(report["ignored_link_count"], 1)
        self.assertEqual({item["provider"] for item in report["candidates"]}, {"company", "workday"})
        self.assertEqual({item["title"] for item in report["candidates"]}, {"Synthetic Data Analyst", "Synthetic Engineer"})
        self.assertTrue(all(item["requires_live_freshness_check"] for item in report["candidates"]))
        self.assertFalse(report["untrusted_page_content_executed"])
        self.assertEqual(report["network_actions"], 0)
        self.assertEqual(report["real_external_actions"], 0)
        validate_named("official-discovery", report, PROJECT / "schemas")

    def test_saved_page_plain_url_uses_single_heading_but_never_infers_location(self) -> None:
        snapshot = json.dumps(
            {
                "source_url": "https://example.com/careers/analyst",
                "html": "<h1>Synthetic Analyst</h1><p>https://jobs.lever.co/example/abc-123</p>",
            }
        ).encode()
        report = discover_official_jobs(
            snapshot,
            official_entry_url="https://example.com/careers/analyst",
            company_domain="example.com",
            approved_ats_hosts=APPROVED_ATS,
            source_format="page_snapshot",
        )
        self.assertEqual(report["candidate_count"], 1)
        candidate = report["candidates"][0]
        self.assertEqual(candidate["provider"], "lever")
        self.assertEqual(candidate["title"], "Synthetic Analyst")
        self.assertEqual(candidate["location"], "UNKNOWN")
        self.assertEqual(candidate["location_status"], "UNKNOWN")

    def test_source_mismatch_and_non_official_entry_fail_closed(self) -> None:
        mismatched = json.dumps({"source_url": "https://example.com/careers/b", "html": "<p>safe</p>"}).encode()
        with self.assertRaises(JobOpsError) as source_error:
            discover_official_jobs(
                mismatched,
                official_entry_url="https://example.com/careers/a",
                company_domain="example.com",
                approved_ats_hosts=APPROVED_ATS,
                source_format="page_snapshot",
            )
        self.assertEqual(source_error.exception.code, "OFFICIAL_PAGE_SOURCE_MISMATCH")
        with self.assertRaises(JobOpsError) as domain_error:
            discover_official_jobs(
                b"<p>safe</p>",
                official_entry_url="https://unrelated.example.net/careers",
                company_domain="example.com",
                approved_ats_hosts=APPROVED_ATS,
            )
        self.assertEqual(domain_error.exception.code, "COMPANY_DOMAIN_MISMATCH")

    def test_cli_emits_path_free_offline_report(self) -> None:
        command = [
            sys.executable,
            str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"),
            "discover-official-jobs",
            "--input", "tests/fixtures/synthetic-official-job-list.html",
            "--company-domain", "example.com",
            "--official-url", "https://example.com/careers",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "LOCAL_SNAPSHOT_PARSED")
        self.assertEqual(report["candidate_count"], 2)
        self.assertNotIn("input_path", report)
        self.assertNotIn(str(PROJECT), completed.stdout)
        self.assertEqual(report["network_actions"], 0)
        self.assertEqual(report["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
