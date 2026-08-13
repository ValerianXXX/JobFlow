from __future__ import annotations

import json
import smtplib
import socket
import sqlite3
import subprocess
import unittest
import urllib.request
from unittest.mock import patch

from _support import project_temp
from jobops.adapters import audit_real_external_actions
from jobops.application_execution import build_application_execution_plan
from jobops.approvals import ApprovalContext, UploadBinding, issue_approval
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.execution_controller import IsolatedApplicationExecutionController
from jobops.external_actions import ExternalActionGateway, ExternalActionPolicy
from jobops.util import canonical_json, iso_utc, sha256_bytes


H = "sha256:" + "a" * 64


def context() -> ApprovalContext:
    return ApprovalContext(
        application_id="APP-ABCDEF123456", job_id="JOB-ABCDEF123456",
        jd_snapshot_hash=H, jd_freshness_hash=H, source_route_hash=H,
        canonical_url="https://example.com/careers/a", ats_tenant="example",
        ats_board="careers", ats_job_identity="a", profile_version="1",
        claim_set_hash=H, form_snapshot_hash=H, answers_hash=H,
        review_packet_hash=H,
        uploads=(UploadBinding("synthetic-resume.pdf", "resume", H),),
        external_actions=("upload_material", "submit_application"),
        site_policy_version="1",
    )


def browser_plan() -> dict:
    actions = [{
        "control_ref": "CTL-ABCDEF123456", "classification": "final_submit_stop",
        "action": "STOP", "binding_kind": "NONE", "binding_ref": None,
        "reason_code": "FINAL_SUBMIT_EXTERNAL_ACTION",
    }]
    material = {
        "form_snapshot_hash": H, "source_route_hash": H,
        "canonical_url": "https://example.com/careers/a", "actions": actions,
    }
    return {
        "schema_version": 1, "status": "NO_FIELDS_BOUND",
        "form_snapshot_hash": H, "source_route_hash": H,
        "canonical_url": "https://example.com/careers/a",
        "plan_hash": sha256_bytes(canonical_json(material)),
        "fillable_count": 0, "stopped_count": 1, "actions": actions,
        "submit_blocked": True, "upload_blocked": True,
        "account_creation_blocked": True, "browser_actions": 0,
        "network_actions": 0, "real_external_actions": 0,
    }


def execution_plan(plan_hash: str) -> dict:
    return build_application_execution_plan(
        application_id="APP-ABCDEF123456",
        source_route={
            "provider": "company", "route_hash": H,
            "guest_mode": "GUEST_SELECTED", "account_action": "NONE",
        },
        form_snapshot_hash=H, browser_plan_hash=plan_hash, form_fields=[],
        material_plan={
            "status": "READY_FOR_REVIEW",
            "cover_letter": {"generation_status": "NOT_GENERATED"},
            "portfolio_file": {"binding_status": "NOT_REQUESTED"},
            "all_uploads_and_submission_blocked": True,
            "real_external_actions": 0,
        },
        pending_limit=10,
    )


def seed_approved(database: JobOpsDB, ctx: ApprovalContext) -> None:
    now = iso_utc()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                ctx.job_id, "txt", "synthetic-fixture", ctx.canonical_url,
                "Example", "Analyst", "Remote", "FORM_VALIDATED", now, now,
            ),
        )
        connection.execute(
            "INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                ctx.application_id, ctx.job_id, "example", "AWAITING_APPROVAL",
                H, H, 1, "secure-ref:SYNTHETIC01", "AWAITING_APPROVAL", now,
            ),
        )
        connection.execute(
            "INSERT INTO application_bindings VALUES(?,?,?,?)",
            (ctx.application_id, ctx.context_hash, json.dumps(ctx.as_dict(), sort_keys=True), now),
        )
    ExternalActionGateway(database, ExternalActionPolicy.production_disabled()).persist_approval(
        issue_approval(context=ctx, user_confirmed=True), ctx,
    )


class IsolatedExecutionControllerTests(unittest.TestCase):
    def test_complete_fake_lifecycle_requires_two_approvals_and_never_uses_transport(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("external transport or process attempted")

        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_approved(database, ctx)
            browser = browser_plan()
            plan = execution_plan(browser["plan_hash"])
            controller = IsolatedApplicationExecutionController(database)

            with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(socket, "create_connection", forbidden), patch.object(urllib.request, "urlopen", forbidden), patch.object(smtplib, "SMTP", forbidden), patch.object(subprocess, "Popen", forbidden):
                prepared = controller.prepare_until_final_authorization(
                    context=ctx, execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash=H, freshness_evidence_hash=H,
                )
                self.assertEqual(prepared["status"], "AWAITING_FINAL_AUTHORIZATION")
                self.assertEqual(prepared["fields_modified"], 0)
                self.assertEqual(prepared["real_external_actions"], 0)
                with self.assertRaises(JobOpsError) as unconfirmed:
                    controller.complete_with_fresh_authorization(
                        run_id=prepared["run_id"], context=ctx,
                        execution_plan=plan, browser_plan=browser,
                        current_form_snapshot_hash=H, freshness_evidence_hash=H,
                        user_confirmed=False, fake_confirmation_number="SYNTHETIC-1",
                    )
                self.assertEqual(unconfirmed.exception.code, "FINAL_SUBMISSION_CONFIRMATION_REQUIRED")
                completed = controller.complete_with_fresh_authorization(
                    run_id=prepared["run_id"], context=ctx,
                    execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash=H, freshness_evidence_hash=H,
                    user_confirmed=True, fake_confirmation_number="SYNTHETIC-1",
                )

            self.assertEqual(completed["status"], "CONFIRMED")
            self.assertEqual(completed["checkpoint_count"], 7)
            self.assertEqual(completed["real_external_actions"], 0)
            audit = audit_real_external_actions(database)
            self.assertEqual(audit["real_external_actions"], 0)
            with database.connect() as connection:
                application = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?", (ctx.application_id,),
                ).fetchone()[0]
                run = connection.execute(
                    "SELECT status,checkpoint_sequence FROM application_execution_runs WHERE run_id=?",
                    (prepared["run_id"],),
                ).fetchone()
                phases = [row[0] for row in connection.execute(
                    "SELECT phase FROM application_execution_checkpoints WHERE run_id=? ORDER BY sequence",
                    (prepared["run_id"],),
                )]
                approval_status = connection.execute(
                    "SELECT status FROM approvals WHERE application_id=? ORDER BY issued_at DESC LIMIT 1",
                    (ctx.application_id,),
                ).fetchone()[0]
                final_status = connection.execute(
                    "SELECT status FROM final_submission_authorizations WHERE application_id=? ORDER BY issued_at DESC LIMIT 1",
                    (ctx.application_id,),
                ).fetchone()[0]
            self.assertEqual(application, "CONFIRMED")
            self.assertEqual(tuple(run), ("CONFIRMED", 7))
            self.assertEqual(phases[-3:], [
                "FINAL_AUTHORIZATION_CONSUMED", "FAKE_SUBMISSION_RECORDED", "RECEIPT_VERIFIED",
            ])
            self.assertEqual(approval_status, "CONSUMED")
            self.assertEqual(final_status, "CONSUMED")
            with self.assertRaises(JobOpsError) as replayed:
                controller.complete_with_fresh_authorization(
                    run_id=prepared["run_id"], context=ctx,
                    execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash=H, freshness_evidence_hash=H,
                    user_confirmed=True, fake_confirmation_number="SYNTHETIC-2",
                )
            self.assertEqual(replayed.exception.code, "EXECUTION_RUN_NOT_AWAITING_FINAL")

    def test_missing_receipt_enters_unknown_and_cannot_retry(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_approved(database, ctx)
            browser = browser_plan()
            plan = execution_plan(browser["plan_hash"])
            controller = IsolatedApplicationExecutionController(database)
            prepared = controller.prepare_until_final_authorization(
                context=ctx, execution_plan=plan, browser_plan=browser,
                current_form_snapshot_hash=H, freshness_evidence_hash=H,
            )
            outcome = controller.complete_with_fresh_authorization(
                run_id=prepared["run_id"], context=ctx,
                execution_plan=plan, browser_plan=browser,
                current_form_snapshot_hash=H, freshness_evidence_hash=H,
                user_confirmed=True, fake_confirmation_number=None,
            )
            self.assertEqual(outcome["status"], "SUBMISSION_UNKNOWN")
            self.assertFalse(outcome["automatic_retry"])
            with self.assertRaises(JobOpsError) as retried:
                controller.complete_with_fresh_authorization(
                    run_id=prepared["run_id"], context=ctx,
                    execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash=H, freshness_evidence_hash=H,
                    user_confirmed=True, fake_confirmation_number="LATE",
                )
            self.assertEqual(retried.exception.code, "SUBMISSION_UNKNOWN_NO_RETRY")
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)

    def test_changed_form_fails_before_run_and_checkpoints_are_append_only(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_approved(database, ctx)
            browser = browser_plan()
            plan = execution_plan(browser["plan_hash"])
            controller = IsolatedApplicationExecutionController(database)
            with self.assertRaises(JobOpsError) as changed:
                controller.prepare_until_final_authorization(
                    context=ctx, execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash="sha256:" + "b" * 64,
                    freshness_evidence_hash=H,
                )
            self.assertEqual(changed.exception.code, "SITE_CHANGED")
            prepared = controller.prepare_until_final_authorization(
                context=ctx, execution_plan=plan, browser_plan=browser,
                current_form_snapshot_hash=H, freshness_evidence_hash=H,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                with database.connect() as connection:
                    connection.execute(
                        "UPDATE application_execution_checkpoints SET status='PASS' WHERE run_id=?",
                        (prepared["run_id"],),
                    )


if __name__ == "__main__":
    unittest.main()
