from __future__ import annotations

import copy
import unittest

from _support import PROJECT
from jobops.application_execution import build_application_execution_plan
from jobops.ats_browser import analyze_local_ats_form, build_browser_action_plan
from jobops.errors import JobOpsError
from jobops.execution_bundle import (
    build_application_execution_bundle,
    validate_application_execution_bundle,
)
from jobops.sourcing import verify_source_route
from jobops.util import canonical_json, sha256_bytes


H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64


class ApplicationExecutionBundleTests(unittest.TestCase):
    def build_bundle(self) -> dict:
        route = verify_source_route(
            company_domain="example.com",
            official_entry_url="https://example.com/careers/analyst",
            current_url="https://jobs.lever.co/example/abc-123",
            navigation_history=[
                "https://example.com/careers/analyst",
                "https://jobs.lever.co/example/abc-123",
            ],
            approved_ats_hosts=["lever.co"],
            guest_available=True,
            tenant_binding={
                "provider": "lever",
                "company_registrable_domain": "example.com",
                "ats_host": "jobs.lever.co",
                "tenant": "example",
                "board": "default",
                "job_identity": "abc-123",
                "official_page_hash": H1,
                "jd_snapshot_hash": H2,
            },
            official_page_hash=H1,
            jd_snapshot_hash=H2,
        ).as_dict()
        snapshot = analyze_local_ats_form(
            (PROJECT / "tests" / "fixtures" / "synthetic-lever-form.html").read_bytes(),
            route=route,
            blocked_categories=[],
        )
        full_name = next(item for item in snapshot["fields"] if item["answer_key"] == "full_name")
        portfolio = next(item for item in snapshot["fields"] if item["answer_key"] == "portfolio")
        portfolio_value = "https://portfolio.example.test/synthetic"
        browser_plan = build_browser_action_plan(snapshot, {
            full_name["control_ref"]: {"kind": "secure_ref", "value": "secure-ref:SYNTHETIC_PROFILE_01"},
            portfolio["control_ref"]: {"kind": "public_value", "value": portfolio_value},
        })
        material_plan = {
            "status": "READY_FOR_REVIEW",
            "cover_letter": {"generation_status": "NOT_GENERATED"},
            "portfolio_file": {"binding_status": "NOT_REQUESTED"},
            "all_uploads_and_submission_blocked": True,
            "real_external_actions": 0,
        }
        execution_plan = build_application_execution_plan(
            application_id="APP-ABCDEF123456",
            source_route=route,
            form_snapshot_hash=snapshot["form_snapshot_hash"],
            browser_plan_hash=browser_plan["plan_hash"],
            form_fields=snapshot["fields"],
            material_plan=material_plan,
            pending_limit=10,
        )
        return build_application_execution_bundle(
            application_id="APP-ABCDEF123456",
            form_snapshot=snapshot,
            browser_plan=browser_plan,
            execution_plan=execution_plan,
            public_values={portfolio["control_ref"]: portfolio_value},
            material_references=[{
                "purpose": "resume",
                "filename": "jobflow-resume.pdf",
                "sha256": H1,
                "secure_ref": "secure-ref:SYNTHETIC_RESUME_01",
            }],
        )

    def test_bundle_binds_exact_form_plan_public_value_and_material(self) -> None:
        bundle = self.build_bundle()
        validate_application_execution_bundle(bundle)
        self.assertEqual(bundle["real_external_actions"], 0)
        self.assertEqual(bundle["public_values"][0]["value_sha256"], sha256_bytes(
            bundle["public_values"][0]["value"].encode("utf-8"),
        ))

        tampered = copy.deepcopy(bundle)
        tampered["public_values"][0]["value"] = "https://changed.example.test"
        tampered["bundle_hash"] = sha256_bytes(canonical_json({
            key: value for key, value in tampered.items() if key != "bundle_hash"
        }))
        with self.assertRaises(JobOpsError) as changed:
            validate_application_execution_bundle(tampered)
        self.assertEqual(changed.exception.code, "EXECUTION_BUNDLE_PUBLIC_VALUE_CHANGED")

        missing = copy.deepcopy(bundle)
        missing["public_values"] = []
        missing["bundle_hash"] = sha256_bytes(canonical_json({
            key: value for key, value in missing.items() if key != "bundle_hash"
        }))
        with self.assertRaises(JobOpsError) as incomplete:
            validate_application_execution_bundle(missing)
        self.assertEqual(incomplete.exception.code, "EXECUTION_BUNDLE_PUBLIC_VALUE_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
