from __future__ import annotations

import copy
import json
import socket
import subprocess
import sys
import unittest
import urllib.request
from unittest.mock import patch

from _support import PROJECT
from jobops.ats_browser import (
    analyze_local_ats_form,
    analyze_local_ats_form_sequence,
    validate_ats_form_sequence_integrity,
)
from jobops.errors import JobOpsError
from jobops.sourcing import verify_source_route


H1 = "sha256:" + "a" * 64
H2 = "sha256:" + "b" * 64
ATS = ["myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co"]


def workday_route() -> dict:
    binding = {
        "provider": "workday", "company_registrable_domain": "example.com",
        "ats_host": "example.wd5.myworkdayjobs.com", "tenant": "example", "board": "careers",
        "job_identity": "123", "official_page_hash": H1, "jd_snapshot_hash": H2,
    }
    return verify_source_route(
        company_domain="example.com", official_entry_url="https://example.com/careers/strategy-analyst",
        current_url="https://example.wd5.myworkdayjobs.com/en-US/Careers/job/123",
        navigation_history=[
            "https://example.com/careers/strategy-analyst",
            "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/123",
        ],
        approved_ats_hosts=ATS, guest_available=True, tenant_binding=binding,
        official_page_hash=H1, jd_snapshot_hash=H2,
    ).as_dict()


class WorkdaySavedSequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_root = PROJECT / "tests" / "fixtures"
        self.pages = [(fixture_root / f"synthetic-workday-step-{index}.html").read_bytes() for index in (1, 2, 3)]

    def test_three_saved_steps_are_bound_deduplicated_and_never_navigated(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("network or browser transport attempted")

        with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(
            urllib.request, "urlopen", forbidden
        ):
            sequence = analyze_local_ats_form_sequence(self.pages, route=workday_route(), blocked_categories=[])
        serialized = json.dumps(sequence, sort_keys=True)
        self.assertNotIn("DO_NOT_RETAIN_WORKDAY_VALUE", serialized)
        self.assertEqual(sequence["provider"], "workday")
        self.assertEqual(sequence["step_count"], 3)
        self.assertEqual(
            [item["step_kind"] for item in sequence["steps"]],
            ["MY_INFORMATION", "APPLICATION_QUESTIONS", "VOLUNTARY_DISCLOSURE"],
        )
        self.assertEqual(sum(item["field_count"] for item in sequence["steps"]), 10)
        self.assertEqual(sequence["duplicate_field_count"], 1)
        self.assertEqual(sequence["unique_field_count"], 9)
        self.assertEqual(set(sequence["blockers"]), {"NAVIGATION_ACTION_STOP", "FILE_UPLOAD_STOP", "FINAL_SUBMIT_STOP"})
        self.assertFalse(sequence["navigation_performed"])
        self.assertFalse(sequence["entered_values_retained"])
        self.assertEqual(sequence["browser_actions"], 0)
        self.assertEqual(sequence["network_actions"], 0)
        self.assertEqual(sequence["real_external_actions"], 0)

        first = analyze_local_ats_form(self.pages[0], route=workday_route(), blocked_categories=[])
        second = analyze_local_ats_form(self.pages[1], route=workday_route(), blocked_categories=[])
        first_email = next(item for item in first["fields"] if item["answer_key"] == "email")
        second_email = next(item for item in second["fields"] if item["answer_key"] == "email")
        self.assertNotEqual(first_email["control_ref"], second_email["control_ref"])
        self.assertEqual(first_email["logical_field_hash"], second_email["logical_field_hash"])

    def test_duplicate_page_hash_tamper_and_account_gate_fail_closed(self) -> None:
        with self.assertRaises(JobOpsError) as duplicate:
            analyze_local_ats_form_sequence([self.pages[0], self.pages[0]], route=workday_route(), blocked_categories=[])
        self.assertEqual(duplicate.exception.code, "FORM_SEQUENCE_DUPLICATE_PAGE")

        sequence = analyze_local_ats_form_sequence(self.pages, route=workday_route(), blocked_categories=[])
        tampered = copy.deepcopy(sequence)
        tampered["steps"][0]["field_count"] += 1
        with self.assertRaises(JobOpsError) as integrity:
            validate_ats_form_sequence_integrity(tampered)
        self.assertEqual(integrity.exception.code, "FORM_SEQUENCE_INTEGRITY_FAILED")

        account = analyze_local_ats_form(
            b"<h1>Create account or Sign in</h1><label for='p'>Password</label><input id='p' type='password'>",
            route=workday_route(), blocked_categories=[],
        )
        self.assertEqual(account["step_kind"], "ACCOUNT_OR_LOGIN")
        self.assertIn("LOGIN_STOP", account["blockers"])
        self.assertIn("ACCOUNT_CREATION_STOP", account["blockers"])
        self.assertTrue(all(item["action"] == "STOP" for item in account["fields"]))

    def test_cli_sequence_report_has_no_paths_or_values(self) -> None:
        command = [
            sys.executable,
            str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"),
            "analyze-ats-sequence",
            "--manifest", "tests/fixtures/synthetic-workday-sequence.json",
            "--route", "tests/fixtures/mock-official-route.json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "LOCAL_FORM_SEQUENCE_ANALYZED")
        self.assertEqual(report["step_count"], 3)
        self.assertNotIn("DO_NOT_RETAIN", completed.stdout)
        self.assertNotIn(str(PROJECT), completed.stdout)
        self.assertNotIn("synthetic-workday-step", completed.stdout)
        self.assertEqual(report["browser_actions"], 0)
        self.assertEqual(report["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
