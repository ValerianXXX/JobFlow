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
ATS = [
    "myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co",
    "ashbyhq.com", "smartrecruiters.com",
]


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


def provider_route(provider: str) -> dict:
    values = {
        "ashby": (
            "jobs.ashbyhq.com",
            "https://jobs.ashbyhq.com/example/11111111-1111-4111-8111-111111111111/application",
            "11111111-1111-4111-8111-111111111111",
        ),
        "smartrecruiters": (
            "jobs.smartrecruiters.com",
            "https://jobs.smartrecruiters.com/example/12345-synthetic-credit-analyst/apply",
            "12345-synthetic-credit-analyst",
        ),
    }
    host, current_url, identity = values[provider]
    binding = {
        "provider": provider, "company_registrable_domain": "example.com", "ats_host": host,
        "tenant": "example", "board": "default", "job_identity": identity,
        "official_page_hash": H1, "jd_snapshot_hash": H2,
    }
    official_url = f"https://example.com/careers/{provider}-analyst"
    return verify_source_route(
        company_domain="example.com", official_entry_url=official_url,
        current_url=current_url, navigation_history=[official_url, current_url],
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

    def test_ashby_and_smartrecruiters_snapshots_stop_upload_and_submit_without_transport(self) -> None:
        for provider in ("ashby", "smartrecruiters"):
            with self.subTest(provider=provider), patch(
                "socket.socket", side_effect=AssertionError("network forbidden")
            ), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                form = analyze_local_ats_form(
                    (PROJECT / "tests" / "fixtures" / f"synthetic-{provider}-form.html").read_bytes(),
                    route=provider_route(provider), blocked_categories=[],
                )
            self.assertEqual(form["provider"], provider)
            self.assertIn("FILE_UPLOAD_STOP", form["blockers"])
            self.assertIn("FINAL_SUBMIT_STOP", form["blockers"])
            self.assertEqual(form["browser_actions"], 0)
            self.assertEqual(form["network_actions"], 0)
            self.assertEqual(form["real_external_actions"], 0)

    def test_capability_report_is_hash_bound_and_never_claims_live_compatibility(self) -> None:
        report = offline_ats_capabilities()
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["provider_count"], 6)
        self.assertEqual(
            {item["provider"] for item in report["providers"]},
            {"company", "greenhouse", "lever", "workday", "ashby", "smartrecruiters"},
        )
        self.assertTrue(all(item["live_site_verified"] is False for item in report["providers"]))
        self.assertTrue(all(item["browser_actions"] == 0 and item["real_external_actions"] == 0 for item in report["providers"]))
        self.assertTrue(all(item["live_transport_registered"] is False for item in report["providers"]))
        self.assertTrue(all(item["automatic_retry"] is False for item in report["providers"]))
        self.assertTrue(all(item["final_submit"] == "USER_ONLY" for item in report["providers"]))
        self.assertTrue(all(item["evidence_refs"] for item in report["providers"]))
        self.assertTrue(all(str(item["evidence_bundle_hash"]).startswith("sha256:") for item in report["providers"]))
        for item in report["providers"]:
            self.assertFalse(set(item["verified_stages"]) & set(item["unverified_stages"]))
            self.assertEqual(len(set(item["verified_stages"]) | set(item["unverified_stages"])), 11)
        workday = next(item for item in report["providers"] if item["provider"] == "workday")
        self.assertIn("ordered_html_sequence", workday["saved_snapshot_modes"])
        self.assertIn("MULTI_PAGE_RESUME", workday["verified_stages"])
        ashby = next(item for item in report["providers"] if item["provider"] == "ashby")
        self.assertEqual(ashby["evidence_scope"], "DISCOVERY_TO_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT")
        self.assertIn("APPROVED_DOM_PREFILL", ashby["verified_stages"])
        self.assertIn("REVIEW_PACKET", ashby["verified_stages"])
        self.assertIn("RESULT_OBSERVATION", ashby["verified_stages"])
        self.assertEqual(
            ashby["user_present_prefill"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        company = next(item for item in report["providers"] if item["provider"] == "company")
        self.assertEqual(
            company["user_present_prefill"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        greenhouse = next(item for item in report["providers"] if item["provider"] == "greenhouse")
        self.assertEqual(greenhouse["evidence_scope"], "DISCOVERY_TO_PROVIDER_BROWSER_RUNTIME")
        self.assertEqual(
            {
                "APPROVED_DOM_PREFILL",
                "APPROVED_FILE_ATTACHMENT",
                "EXPLICIT_NONFINAL_NAVIGATION",
            } - set(greenhouse["verified_stages"]),
            set(),
        )
        self.assertEqual(
            greenhouse["user_present_prefill"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        self.assertEqual(
            greenhouse["approved_material_upload"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        self.assertEqual(
            greenhouse["nonfinal_navigation"],
            "PROVIDER_EVIDENCE_VERIFIED_EXPLICIT_CONTROLS_ONLY",
        )
        self.assertFalse(greenhouse["live_site_verified"])
        lever = next(item for item in report["providers"] if item["provider"] == "lever")
        self.assertEqual(
            lever["evidence_scope"],
            "DISCOVERY_TO_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT",
        )
        self.assertEqual(
            {"APPROVED_DOM_PREFILL", "APPROVED_FILE_ATTACHMENT", "RESULT_OBSERVATION"}
            - set(lever["verified_stages"]),
            set(),
        )
        self.assertEqual(
            lever["user_present_prefill"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        self.assertEqual(
            lever["approved_material_upload"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        self.assertEqual(
            lever["nonfinal_navigation"],
            "SHARED_RUNTIME_ONLY_PROVIDER_ACCEPTANCE_REQUIRED",
        )
        self.assertFalse(lever["live_site_verified"])
        self.assertEqual(
            workday["evidence_scope"],
            "MULTI_PAGE_SEQUENCE_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT",
        )
        self.assertEqual(
            {
                "APPROVED_DOM_PREFILL",
                "APPROVED_FILE_ATTACHMENT",
                "EXPLICIT_NONFINAL_NAVIGATION",
                "MULTI_PAGE_RESUME",
                "RESULT_OBSERVATION",
            } - set(workday["verified_stages"]),
            set(),
        )
        self.assertEqual(
            workday["user_present_prefill"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        self.assertEqual(
            workday["approved_material_upload"],
            "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
        )
        self.assertFalse(workday["live_site_verified"])
        runtime = report["browser_runtime_evidence"]
        self.assertEqual(runtime["status"], "SYNTHETIC_BROWSER_RUNTIME_PASS")
        self.assertEqual(
            set(runtime["verified_stages"]),
            {
                "APPROVED_DOM_PREFILL",
                "APPROVED_FILE_ATTACHMENT",
                "EXPLICIT_NONFINAL_NAVIGATION",
                "MODERN_COMPONENT_REBINDING",
            },
        )
        self.assertFalse(runtime["live_site_verified"])
        self.assertEqual(runtime["final_submit"], "USER_ONLY")
        tampered = copy.deepcopy(report)
        tampered["providers"][0]["offline_evidence_level"] = "SINGLE_SNAPSHOT_PASS"
        with self.assertRaises(JobOpsError) as integrity:
            validate_ats_capability_integrity(tampered)
        self.assertEqual(integrity.exception.code, "ATS_CAPABILITY_SCOPE_DRIFT")

        evidence_tampered = copy.deepcopy(report)
        evidence_tampered["providers"][0]["evidence_bundle_hash"] = H1
        with self.assertRaises(JobOpsError) as evidence:
            validate_ats_capability_integrity(evidence_tampered)
        self.assertEqual(evidence.exception.code, "ATS_CAPABILITY_EVIDENCE_INTEGRITY_FAILED")

        scope_tampered = copy.deepcopy(report)
        scope_tampered["providers"][0]["verified_stages"].append("MULTI_PAGE_RESUME")
        scope_tampered["providers"][0]["unverified_stages"].remove("MULTI_PAGE_RESUME")
        with self.assertRaises(JobOpsError) as scope:
            validate_ats_capability_integrity(scope_tampered)
        self.assertEqual(scope.exception.code, "ATS_CAPABILITY_SCOPE_DRIFT")

    def test_capability_scope_does_not_promote_generic_runtime_evidence_to_provider_acceptance(self) -> None:
        report = offline_ats_capabilities()
        by_provider = {item["provider"]: item for item in report["providers"]}
        self.assertEqual(
            by_provider["greenhouse"]["evidence_scope"],
            "DISCOVERY_TO_PROVIDER_BROWSER_RUNTIME",
        )
        self.assertEqual(
            by_provider["lever"]["evidence_scope"],
            "DISCOVERY_TO_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT",
        )
        self.assertEqual(
            by_provider["workday"]["evidence_scope"],
            "MULTI_PAGE_SEQUENCE_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT",
        )
        for provider in ("ashby", "smartrecruiters"):
            self.assertEqual(
                by_provider[provider]["evidence_scope"],
                "DISCOVERY_TO_PROVIDER_BROWSER_AND_SYNTHETIC_RESULT",
            )
            self.assertEqual(
                {
                    "PRIVATE_VALUE_FREE_PLAN",
                    "REVIEW_PACKET",
                    "APPROVED_DOM_PREFILL",
                    "APPROVED_FILE_ATTACHMENT",
                    "EXPLICIT_NONFINAL_NAVIGATION",
                    "RESULT_OBSERVATION",
                } - set(by_provider[provider]["verified_stages"]),
                set(),
            )
            self.assertIn("MULTI_PAGE_RESUME", by_provider[provider]["unverified_stages"])
            self.assertEqual(
                by_provider[provider]["approved_material_upload"],
                "PROVIDER_EVIDENCE_VERIFIED_WITH_RUNTIME_REVALIDATION",
            )
            self.assertEqual(
                by_provider[provider]["nonfinal_navigation"],
                "PROVIDER_EVIDENCE_VERIFIED_EXPLICIT_CONTROLS_ONLY",
            )
        self.assertTrue(all(item["live_site_verified"] is False for item in by_provider.values()))

    def test_every_provider_uses_one_hash_only_transport_contract(self) -> None:
        for provider in ("company", "greenhouse", "lever", "workday", "ashby", "smartrecruiters"):
            with self.subTest(provider=provider):
                contract = provider_transport_contract(provider)
                self.assertFalse(contract["live_transport_registered"])
                self.assertFalse(contract["automatic_retry"])
                self.assertEqual(contract["final_submit_gate"], "USER_ONLY_NO_TOOL_CAPABILITY")
                self.assertFalse(contract["submit_capability"])
                envelope = build_ats_transport_envelope(
                    provider=provider, action="prefill_application_form",
                    application_id="APP-ABCDEF123456", run_id="RUN-ABCDEF123456",
                    application_context_hash=H1, source_route_hash=H1,
                    form_snapshot_hash=H1, execution_plan_hash=H1,
                    request_payload_hash=H2,
                    authorization_kind="SCOPED_ACTION_SESSION_USE",
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
            "verify_receipt": "SUBMISSION_ATTEMPT",
        }
        self.assertEqual(
            provider_transport_contract("workday")["operation_sequence"],
            [*list(actions)[:-1], "await_user_submit", "verify_receipt"],
        )
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
        self.assertEqual(report["provider_count"], 6)
        self.assertFalse(report["live_site_accessed"])
        self.assertEqual(report["network_actions"], 0)
        self.assertEqual(report["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
