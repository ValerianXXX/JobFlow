from __future__ import annotations

import copy
import unittest

from _support import PROJECT
from jobops.application_execution import build_application_execution_plan
from jobops.errors import JobOpsError
from jobops.runtime_schema import validate_named


H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64
H3 = "sha256:" + "3" * 64


def route(*, guest_mode: str = "GUEST_SELECTED", account_action: str = "NONE") -> dict:
    return {
        "provider": "greenhouse", "route_hash": H1,
        "guest_mode": guest_mode, "account_action": account_action,
    }


def materials(*, status: str = "READY_FOR_REVIEW") -> dict:
    return {
        "status": status,
        "cover_letter": {"generation_status": "GENERATED_ON_DEMAND"},
        "portfolio_file": {"binding_status": "BOUND_SECURE_FILE"},
        "all_uploads_and_submission_blocked": True,
        "real_external_actions": 0,
    }


FIELDS = [
    {"classification": "private_fixed", "action": "PREFILL_FROM_SECURE_STORE"},
    {"classification": "ordinary_fixed", "action": "PREFILL"},
    {"classification": "work_authorization_stop", "action": "STOP"},
    {"classification": "file_upload_stop", "action": "STOP"},
    {"classification": "final_submit_stop", "action": "STOP"},
]


class ApplicationExecutionPlanTests(unittest.TestCase):
    def test_ready_plan_is_bound_nonblocking_and_performs_nothing(self) -> None:
        plan = build_application_execution_plan(
            application_id="APP-ABCDEF123456", source_route=route(),
            form_snapshot_hash=H2, browser_plan_hash=H3, form_fields=FIELDS,
            material_plan=materials(), pending_limit=25,
        )
        self.assertEqual(plan["status"], "READY_FOR_REVIEW")
        self.assertEqual(plan["field_summary"]["fillable"], 2)
        self.assertEqual(plan["field_summary"]["work_authorization"], 1)
        self.assertEqual(plan["upload_purposes"], ["resume", "cover_letter", "portfolio"])
        self.assertTrue(plan["queue_behavior"]["continue_other_jobs"])
        self.assertEqual(plan["steps"][-1]["gate"], "FRESH_EXPLICIT_SUBMISSION_APPROVAL")
        self.assertTrue(plan["stop_before_final_submission"])
        self.assertFalse(plan["live_transport_registered"])
        self.assertEqual(plan["network_actions"], 0)
        self.assertEqual(plan["real_external_actions"], 0)
        validate_named("application-execution-plan", plan, PROJECT / "schemas")
        tampered = copy.deepcopy(plan)
        tampered["steps"][2]["item_count"] += 1
        with self.assertRaises(JobOpsError) as integrity:
            validate_named("application-execution-plan", tampered, PROJECT / "schemas")
        self.assertEqual(integrity.exception.code, "EXECUTION_PLAN_INTEGRITY_FAILED")

    def test_account_unknown_and_missing_materials_are_plain_blockers(self) -> None:
        account = build_application_execution_plan(
            application_id="APP-ABCDEF123456",
            source_route=route(guest_mode="GUEST_UNAVAILABLE", account_action="NEEDS_ACCOUNT_APPROVAL"),
            form_snapshot_hash=H2, browser_plan_hash=H3, form_fields=FIELDS,
            material_plan=materials(), pending_limit=10,
        )
        self.assertEqual(account["status"], "NEEDS_ACCOUNT_APPROVAL")
        self.assertIn("NEEDS_ACCOUNT_APPROVAL", account["blockers"])
        self.assertEqual(account["steps"][1]["state"], "BLOCKED")

        fields = [*FIELDS, {"classification": "unknown_stop", "action": "STOP"}]
        missing = build_application_execution_plan(
            application_id="APP-ABCDEF123456", source_route=route(),
            form_snapshot_hash=H2, browser_plan_hash=H3, form_fields=fields,
            material_plan=materials(status="NEEDS_USER_MATERIAL"), pending_limit=10,
        )
        self.assertEqual(missing["status"], "NEEDS_USER_INPUT")
        self.assertEqual(set(missing["blockers"]), {"REQUIRED_MATERIAL_MISSING", "UNKNOWN_FORM_FIELD"})

    def test_tampered_hash_or_open_stop_gate_fails_closed(self) -> None:
        with self.assertRaises(JobOpsError) as bad_hash:
            build_application_execution_plan(
                application_id="APP-ABCDEF123456", source_route=route(),
                form_snapshot_hash="not-a-hash", browser_plan_hash=H3,
                form_fields=FIELDS, material_plan=materials(), pending_limit=10,
            )
        self.assertEqual(bad_hash.exception.code, "EXECUTION_FORM_HASH_INVALID")

        unsafe = copy.deepcopy(materials())
        unsafe["all_uploads_and_submission_blocked"] = False
        with self.assertRaises(JobOpsError) as gates:
            build_application_execution_plan(
                application_id="APP-ABCDEF123456", source_route=route(),
                form_snapshot_hash=H2, browser_plan_hash=H3,
                form_fields=FIELDS, material_plan=unsafe, pending_limit=10,
            )
        self.assertEqual(gates.exception.code, "EXECUTION_STOP_GATES_MISSING")


if __name__ == "__main__":
    unittest.main()
