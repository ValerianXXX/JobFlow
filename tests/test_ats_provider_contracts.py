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
from jobops.adapters import FakeBrowserPrefillAdapter
from jobops.ats_browser import analyze_local_ats_form, build_browser_action_plan
from jobops.ats_capabilities import offline_ats_capabilities, provider_transport_contract, validate_ats_capability_integrity
from jobops.ats_transport import build_ats_transport_envelope, validate_ats_transport_envelope
from jobops.errors import JobOpsError
from jobops.sourcing import source_route_hash, verify_source_route


H1 = "sha256:" + "a" * 64
H2 = "sha256:" + "b" * 64
ATS = ["myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co"]


def lever_route() -> dict:
    binding = {
        "provider": "lever", "company_registrable_domain": "example.com", "ats_host": "jobs.lever.co",
        "tenant": "example", "board": "default", "job_identity": "abc-123",
        "official_page_hash": H1, "jd_snapshot_hash": H2,
    }
    return verify_source_route(
        company_domain="example.com", official_entry_url="https://example.com/careers/lever-analyst",
        current_url="https://jobs.lever.co/example/abc-123",
        navigation_history=["https://example.com/careers/lever-analyst", "https://jobs.lever.co/example/abc-123"],
        approved_ats_hosts=ATS, guest_available=True, tenant_binding=binding,
        official_page_hash=H1, jd_snapshot_hash=H2,
    ).as_dict()


class ATSProviderContractTests(unittest.TestCase):
    def test_lever_reuses_the_safe_snapshot_and_plan_protocol_without_transport(self) -> None:
        snapshot = (PROJECT / "tests" / "fixtures" / "synthetic-lever-form.html").read_bytes()

        def forbidden(*args, **kwargs):
            raise AssertionError("network or browser transport attempted")

        with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(
            urllib.request, "urlopen", forbidden
        ):
            form = analyze_local_ats_form(snapshot, route=lever_route(), blocked_categories=[])
            full_name = next(item for item in form["fields"] if item["answer_key"] == "full_name")
            portfolio = next(item for item in form["fields"] if item["answer_key"] == "portfolio")
            plan = build_browser_action_plan(form, {
                full_name["control_ref"]: {"kind": "secure_ref", "value": "secure-ref:SYNTHETIC_PROFILE_01"},
                portfolio["control_ref"]: {"kind": "public_value", "value": "https://portfolio.example.test"},
            })
            validated = FakeBrowserPrefillAdapter().prefill({
                "plan": plan, "current_form_snapshot_hash": form["form_snapshot_hash"],
                "isolation_policy": "ISOLATED_FAKE_ONLY",
            })
        self.assertEqual(form["provider"], "lever")
        self.assertEqual(form["field_count"], 5)
        self.assertEqual(plan["fillable_count"], 2)
        self.assertEqual(plan["stopped_count"], 3)
        self.assertEqual({item["answer_key"] for item in form["fields"]}, {"full_name", "portfolio", "linkedin", "resume", "UNKNOWN"})
        self.assertIn("FILE_UPLOAD_STOP", form["blockers"])
        self.assertIn("FINAL_SUBMIT_STOP", form["blockers"])
        self.assertEqual(validated["status"], "FAKE_PLAN_VALIDATED")
        self.assertEqual(validated["fields_modified"], 0)
        self.assertEqual(validated["browser_actions"], 0)
        self.assertEqual(validated["network_actions"], 0)
        self.assertEqual(validated["real_side_effects"], 0)

    def test_capability_report_is_hash_bound_and_never_claims_live_compatibility(self) -> None:
        report = offline_ats_capabilities()
        self.assertEqual(report["provider_count"], 4)
        self.assertEqual({item["provider"] for item in report["providers"]}, {"company", "greenhouse", "lever", "workday"})
        self.assertTrue(all(item["live_site_verified"] is False for item in report["providers"]))
        self.assertTrue(all(item["browser_actions"] == 0 and item["real_external_actions"] == 0 for item in report["providers"]))
        self.assertTrue(all(item["live_transport_registered"] is False for item in report["providers"]))
        self.assertTrue(all(item["automatic_retry"] is False for item in report["providers"]))
        workday = next(item for item in report["providers"] if item["provider"] == "workday")
        self.assertIn("ordered_html_sequence", workday["saved_snapshot_modes"])
        tampered = copy.deepcopy(report)
        tampered["providers"][0]["offline_evidence_level"] = "SINGLE_SNAPSHOT_PASS"
        with self.assertRaises(JobOpsError) as integrity:
            validate_ats_capability_integrity(tampered)
        self.assertEqual(integrity.exception.code, "ATS_CAPABILITY_INTEGRITY_FAILED")

    def test_every_provider_uses_one_hash_only_transport_contract(self) -> None:
        for provider in ("company", "greenhouse", "lever", "workday"):
            with self.subTest(provider=provider):
                contract = provider_transport_contract(provider)
                self.assertFalse(contract["live_transport_registered"])
                self.assertFalse(contract["automatic_retry"])
                self.assertEqual(contract["final_submit_gate"], "FRESH_ONE_TIME_AUTHORIZATION")
                envelope = build_ats_transport_envelope(
                    provider=provider, action="submit_application",
                    application_id="APP-ABCDEF123456", run_id="RUN-ABCDEF123456",
                    application_context_hash=H1, source_route_hash=H1,
                    form_snapshot_hash=H1, execution_plan_hash=H1,
                    request_payload_hash=H2,
                    authorization_kind="FINAL_SUBMISSION_AUTHORIZATION",
                    authorization_hash=H2,
                )
                validate_ats_transport_envelope(envelope)
                self.assertFalse(envelope["contains_private_values"])
                self.assertFalse(envelope["contains_file_content"])
                self.assertEqual(envelope["network_actions"], 0)
                tampered = copy.deepcopy(envelope)
                tampered["request_payload_hash"] = H1
                with self.assertRaises(JobOpsError) as invalid:
                    validate_ats_transport_envelope(tampered)
                self.assertEqual(invalid.exception.code, "ATS_TRANSPORT_ENVELOPE_TAMPERED")
        with self.assertRaises(JobOpsError) as wrong_gate:
            build_ats_transport_envelope(
                provider="greenhouse", action="submit_application",
                application_id="APP-ABCDEF123456", run_id="RUN-ABCDEF123456",
                application_context_hash=H1, source_route_hash=H1,
                form_snapshot_hash=H1, execution_plan_hash=H1,
                request_payload_hash=H2, authorization_kind="SCOPED_ACTION_SESSION_USE",
                authorization_hash=H2,
            )
        self.assertEqual(wrong_gate.exception.code, "ATS_TRANSPORT_AUTHORIZATION_MISMATCH")

    def test_transport_sequence_has_no_authorization_shortcut(self) -> None:
        actions = {
            "read_official_job": "SCOPED_ACTION_SESSION_USE",
            "inspect_application_form": "SCOPED_ACTION_SESSION_USE",
            "prefill_application_form": "SCOPED_ACTION_SESSION_USE",
            "upload_materials": "SCOPED_ACTION_SESSION_USE",
            "submit_application": "FINAL_SUBMISSION_AUTHORIZATION",
            "verify_receipt": "SUBMISSION_ATTEMPT",
        }
        self.assertEqual(provider_transport_contract("workday")["operation_sequence"], list(actions))
        for action, authorization_kind in actions.items():
            envelope = build_ats_transport_envelope(
                provider="workday", action=action,
                application_id="APP-ABCDEF123456", run_id="RUN-ABCDEF123456",
                application_context_hash=H1, source_route_hash=H1,
                form_snapshot_hash=H1, execution_plan_hash=H1,
                request_payload_hash=H2, authorization_kind=authorization_kind,
                authorization_hash=H2,
            )
            validate_ats_transport_envelope(envelope)

    def test_provider_host_mismatch_and_public_cli_fail_closed(self) -> None:
        route = lever_route()
        route["provider"] = "greenhouse"
        route["route_hash"] = source_route_hash(route)
        with self.assertRaises(JobOpsError) as mismatch:
            analyze_local_ats_form(
                (PROJECT / "tests" / "fixtures" / "synthetic-lever-form.html").read_bytes(),
                route=route, blocked_categories=[],
            )
        self.assertEqual(mismatch.exception.code, "ATS_PROVIDER_HOST_MISMATCH")

        command = [
            sys.executable,
            str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"),
            "ats-capabilities",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["provider_count"], 4)
        self.assertFalse(report["live_site_accessed"])
        self.assertEqual(report["network_actions"], 0)
        self.assertEqual(report["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
