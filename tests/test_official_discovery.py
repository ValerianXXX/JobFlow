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
    def test_saved_greenhouse_and_lever_json_are_auto_detected_without_transport(self) -> None:
        fixtures = PROJECT / "tests" / "fixtures"
        for filename, expected_format, expected_provider, expected_titles in (
            (
                "synthetic-greenhouse-jobs.json", "greenhouse_json", "greenhouse",
                {"Synthetic Data Analyst", "Synthetic Operations Analyst"},
            ),
            (
                "synthetic-lever-postings.json", "lever_json", "lever",
                {"Synthetic Strategy Analyst", "Synthetic Product Analyst"},
            ),
        ):
            with self.subTest(filename=filename), patch(
                "socket.socket", side_effect=AssertionError("network forbidden")
            ), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                report = discover_official_jobs(
                    (fixtures / filename).read_bytes(),
                    official_entry_url="https://example.com/careers",
                    company_domain="example.com",
                    approved_ats_hosts=APPROVED_ATS,
                    source_format="auto",
                )
            self.assertEqual(report["source_format"], expected_format)
            self.assertEqual(report["candidate_count"], 2)
            self.assertEqual({item["provider"] for item in report["candidates"]}, {expected_provider})
            self.assertEqual({item["title"] for item in report["candidates"]}, expected_titles)
            self.assertTrue(all(item["evidence_kind"] == "provider_json" for item in report["candidates"]))
            self.assertTrue(all(item["ats_tenant"] == "example" for item in report["candidates"]))
            self.assertTrue(all(item["requires_live_freshness_check"] for item in report["candidates"]))
            self.assertEqual(report["network_actions"], 0)
            self.assertEqual(report["real_external_actions"], 0)
            validate_named("official-discovery", report, PROJECT / "schemas")

    def test_unrecognized_or_mislabeled_provider_json_fails_closed(self) -> None:
        with self.assertRaises(JobOpsError) as unrecognized:
            discover_official_jobs(
                b'{"results": []}', official_entry_url="https://example.com/careers",
                company_domain="example.com", approved_ats_hosts=APPROVED_ATS, source_format="auto",
            )
        self.assertEqual(unrecognized.exception.code, "OFFICIAL_PROVIDER_JSON_UNRECOGNIZED")
        with self.assertRaises(JobOpsError) as mislabeled:
            discover_official_jobs(
                b'[]', official_entry_url="https://example.com/careers",
                company_domain="example.com", approved_ats_hosts=APPROVED_ATS, source_format="greenhouse_json",
            )
        self.assertEqual(mislabeled.exception.code, "OFFICIAL_PROVIDER_JSON_INVALID")

    def test_provider_json_report_rejects_mismatched_semantics(self) -> None:
        report = discover_official_jobs(
            (PROJECT / "tests" / "fixtures" / "synthetic-greenhouse-jobs.json").read_bytes(),
            official_entry_url="https://example.com/careers",
            company_domain="example.com",
            approved_ats_hosts=APPROVED_ATS,
            source_format="auto",
        )
        report["candidates"][0]["provider"] = "lever"
        with self.assertRaises(JobOpsError) as mismatch:
            validate_named("official-discovery", report, PROJECT / "schemas")
        self.assertEqual(mismatch.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

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

    def test_scripts_external_links_and_credential_queries_never_escape(self) -> None:
        snapshot = b"""<!doctype html><body>
        <script><a href='https://example.com/careers/jobs/scripted'>Injected role</a></script>
        <a href='https://unapproved.example.net/jobs/external'>External role</a>
        <a href='https://example.com/careers/jobs/private?session_token=do-not-emit'>Private query</a>
        <a href='/careers/jobs/safe?source=official&amp;keywords=analyst&amp;jobcode=42'>Synthetic Safe Role</a>
        </body>"""
        report = discover_official_jobs(
            snapshot,
            official_entry_url="https://example.com/careers",
            company_domain="example.com",
            approved_ats_hosts=APPROVED_ATS,
        )
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["candidates"][0]["title"], "Synthetic Safe Role")
        self.assertNotIn("do-not-emit", json.dumps(report))
        self.assertFalse(report["untrusted_page_content_executed"])
        with self.assertRaises(JobOpsError) as sensitive_entry:
            discover_official_jobs(
                b"<a href='/careers/jobs/safe'>Safe</a>",
                official_entry_url="https://example.com/careers?auth_token=do-not-emit",
                company_domain="example.com",
                approved_ats_hosts=APPROVED_ATS,
            )
        self.assertEqual(sensitive_entry.exception.code, "OFFICIAL_URL_SENSITIVE_QUERY")
        self.assertNotIn("do-not-emit", str(sensitive_entry.exception))

    def test_snapshot_complexity_is_bounded_before_results_are_emitted(self) -> None:
        snapshot = b"<a href='/careers/jobs/one'>One</a><a href='/careers/jobs/two'>Two</a>"
        with patch("jobops.official_discovery.MAX_LINK_EVIDENCE", 1):
            with self.assertRaises(JobOpsError) as complexity:
                discover_official_jobs(
                    snapshot,
                    official_entry_url="https://example.com/careers",
                    company_domain="example.com",
                    approved_ats_hosts=APPROVED_ATS,
                )
        self.assertEqual(complexity.exception.code, "OFFICIAL_SNAPSHOT_COMPLEXITY_LIMIT")

        tag_flood = ("<html><body>" + ("<div></div>" * 20) + "</body></html>").encode()
        with patch("jobops.official_discovery.MAX_SNAPSHOT_HTML_EVENTS", 20):
            with self.assertRaises(JobOpsError) as event_limit:
                discover_official_jobs(
                    tag_flood,
                    official_entry_url="https://example.com/careers",
                    company_domain="example.com",
                    approved_ats_hosts=APPROVED_ATS,
                )
        self.assertEqual(event_limit.exception.code, "OFFICIAL_SNAPSHOT_COMPLEXITY_LIMIT")

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
