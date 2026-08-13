from __future__ import annotations

import json
import socket
import subprocess
import sys
import unittest
import urllib.request
from unittest.mock import patch

from _support import PROJECT
from jobops.ats_browser import analyze_local_ats_form, assert_form_snapshot_current, build_browser_action_plan
from jobops.errors import JobOpsError
from jobops.sourcing import source_route_hash, verify_source_route


H1 = "sha256:" + "a" * 64
H2 = "sha256:" + "b" * 64
ATS = ["myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co"]


def verified_route() -> dict:
    binding = {
        "provider": "workday", "company_registrable_domain": "example.com",
        "ats_host": "example.wd5.myworkdayjobs.com", "tenant": "example", "board": "careers",
        "job_identity": "123", "official_page_hash": H1, "jd_snapshot_hash": H2,
    }
    return verify_source_route(
        company_domain="example.com",
        official_entry_url="https://example.com/careers/strategy-analyst",
        current_url="https://example.wd5.myworkdayjobs.com/en-US/Careers/job/123",
        navigation_history=[
            "https://example.com/careers/strategy-analyst",
            "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/123",
        ],
        approved_ats_hosts=ATS,
        guest_available=True,
        tenant_binding=binding,
        official_page_hash=H1,
        jd_snapshot_hash=H2,
    ).as_dict()


class ATSBrowserSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = (PROJECT / "tests" / "fixtures" / "synthetic-workday-form.html").read_bytes()

    def test_snapshot_discards_values_classifies_all_controls_and_performs_no_transport(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("browser or network transport attempted")

        with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(
            urllib.request, "urlopen", forbidden
        ):
            report = analyze_local_ats_form(self.snapshot, route=verified_route(), blocked_categories=[])
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("DO_NOT_LEAK_PRIVATE_SENTINEL", serialized)
        self.assertNotIn("DO_NOT_LEAK_HIDDEN_SENTINEL", serialized)
        self.assertNotIn("Email address", serialized)
        self.assertEqual(report["field_count"], 7)
        self.assertEqual(report["ignored_hidden_control_count"], 1)
        self.assertEqual(
            report["classification_counts"],
            {
                "private_fixed": 1, "ordinary_fixed": 1, "work_authorization_stop": 1,
                "compensation_stop": 1, "voluntary_disclosure_stop": 1,
                "file_upload_stop": 1, "final_submit_stop": 1,
            },
        )
        self.assertEqual(
            set(report["blockers"]),
            {"CAPTCHA_STOP", "MFA_STOP", "LOGIN_STOP", "FILE_UPLOAD_STOP", "FINAL_SUBMIT_STOP", "CROSS_ORIGIN_IFRAME_STOP"},
        )
        self.assertTrue(any(field["existing_value_discarded"] for field in report["fields"]))
        self.assertFalse(report["entered_values_retained"])
        self.assertEqual(report["browser_actions"], 0)
        self.assertEqual(report["network_actions"], 0)
        self.assertEqual(report["real_external_actions"], 0)

    def test_form_parser_rejects_tag_floods_at_the_event_boundary(self) -> None:
        snapshot = ("<html><body>" + ("<div></div>" * 20) + "</body></html>").encode("utf-8")
        with patch("jobops.ats_browser.MAX_FORM_HTML_EVENTS", 20):
            with self.assertRaises(JobOpsError) as blocked:
                analyze_local_ats_form(snapshot, route=verified_route(), blocked_categories=[])
        self.assertEqual(blocked.exception.code, "ATS_FORM_COMPLEXITY_LIMIT")

    def test_action_plan_keeps_plaintext_out_and_never_plans_protected_actions(self) -> None:
        report = analyze_local_ats_form(self.snapshot, route=verified_route(), blocked_categories=[])
        private_control = next(item for item in report["fields"] if item["classification"] == "private_fixed")
        ordinary_control = next(item for item in report["fields"] if item["classification"] == "ordinary_fixed")
        plaintext = "https://synthetic.example.test/DO_NOT_LEAK_PUBLIC_VALUE"
        plan = build_browser_action_plan(
            report,
            {
                private_control["control_ref"]: {"kind": "secure_ref", "value": "secure-ref:SYNTHETIC_PRIVATE_01"},
                ordinary_control["control_ref"]: {"kind": "public_value", "value": plaintext},
            },
        )
        serialized = json.dumps(plan, sort_keys=True)
        self.assertNotIn(plaintext, serialized)
        self.assertEqual(plan["fillable_count"], 2)
        self.assertEqual(plan["stopped_count"], 5)
        proposed = [item for item in plan["actions"] if item["action"] == "PROPOSE_PREFILL"]
        self.assertEqual({item["binding_kind"] for item in proposed}, {"SECURE_REF", "PUBLIC_VALUE_HASH"})
        self.assertTrue(all(item["classification"] in {"private_fixed", "ordinary_fixed"} for item in proposed))
        self.assertTrue(plan["submit_blocked"] and plan["upload_blocked"] and plan["account_creation_blocked"])

    def test_unknown_binding_route_tamper_and_changed_form_fail_closed(self) -> None:
        report = analyze_local_ats_form(self.snapshot, route=verified_route(), blocked_categories=[])
        with self.assertRaises(JobOpsError) as unknown:
            build_browser_action_plan(report, {"CTL-000000000000": {"kind": "public_value", "value": "x"}})
        self.assertEqual(unknown.exception.code, "FORM_BINDING_UNKNOWN_CONTROL")

        tampered = verified_route()
        tampered["ats_job_identity"] = "different"
        with self.assertRaises(JobOpsError) as route_error:
            analyze_local_ats_form(self.snapshot, route=tampered, blocked_categories=[])
        self.assertEqual(route_error.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

        sensitive = verified_route()
        sensitive_url = sensitive["current_url"] + "?session_token=private-value"
        sensitive["current_url"] = sensitive_url
        sensitive["navigation_history"][-1] = sensitive_url
        sensitive["route_hash"] = source_route_hash(sensitive)
        with self.assertRaises(JobOpsError) as sensitive_error:
            analyze_local_ats_form(self.snapshot, route=sensitive, blocked_categories=[])
        self.assertEqual(sensitive_error.exception.code, "ATS_ROUTE_SENSITIVE_QUERY")

        changed = analyze_local_ats_form(self.snapshot.replace(b"Synthetic application", b"Changed application"), route=verified_route(), blocked_categories=[])
        with self.assertRaises(JobOpsError) as changed_error:
            assert_form_snapshot_current(report, changed)
        self.assertEqual(changed_error.exception.code, "SITE_CHANGED")

    def test_cli_report_is_structured_redacted_and_offline(self) -> None:
        command = [
            sys.executable,
            str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"),
            "analyze-ats-form",
            "--input", "tests/fixtures/synthetic-workday-form.html",
            "--route", "tests/fixtures/mock-official-route.json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "FORM_SNAPSHOT_ANALYZED")
        self.assertEqual(report["field_count"], 7)
        self.assertNotIn("DO_NOT_LEAK", completed.stdout)
        self.assertNotIn(str(PROJECT), completed.stdout)
        self.assertEqual(report["browser_actions"], 0)
        self.assertEqual(report["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
