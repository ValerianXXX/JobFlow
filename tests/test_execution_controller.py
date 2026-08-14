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
from jobops.external_action_sessions import ExternalActionSessionManager, ExternalActionSessionPolicy
from jobops.external_actions import ExternalActionGateway, ExternalActionPolicy
from jobops.final_submission import issue_final_submission_authorization
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


def browser_plan(*, prefill: bool = False) -> dict:
    actions = [{
        "control_ref": "CTL-ABCDEF123456",
        "classification": "ordinary_fixed" if prefill else "final_submit_stop",
        "action": "PROPOSE_PREFILL" if prefill else "STOP",
        "binding_kind": "PUBLIC_VALUE_HASH" if prefill else "NONE",
        "binding_ref": H if prefill else None,
        "reason_code": "KNOWN_PUBLIC_BINDING_REQUIRED" if prefill else "FINAL_SUBMIT_EXTERNAL_ACTION",
    }]
    material = {
        "form_snapshot_hash": H, "source_route_hash": H,
        "canonical_url": "https://example.com/careers/a", "actions": actions,
    }
    return {
        "schema_version": 1, "status": "LOCAL_PLAN_READY" if prefill else "NO_FIELDS_BOUND",
        "form_snapshot_hash": H, "source_route_hash": H,
        "canonical_url": "https://example.com/careers/a",
        "plan_hash": sha256_bytes(canonical_json(material)),
        "fillable_count": 1 if prefill else 0, "stopped_count": 0 if prefill else 1, "actions": actions,
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


def action_session(database: JobOpsDB, ctx: ApprovalContext, *, prefill: bool = False) -> str:
    manager = ExternalActionSessionManager(database, ExternalActionSessionPolicy.isolated_fake())
    manager.enable(user_confirmed=True)
    actions = ["read_official_job", "inspect_application_form", "upload_materials"]
    if prefill:
        actions.append("prefill_application_form")
    session = manager.issue(context=ctx, allowed_actions=actions, user_confirmed=True)
    manager.persist(session, context=ctx)
    return session.session_id


class IsolatedExecutionControllerTests(unittest.TestCase):
    def _prepared(self, database: JobOpsDB):
        ctx = context()
        seed_approved(database, ctx)
        browser = browser_plan()
        plan = execution_plan(browser["plan_hash"])
        controller = IsolatedApplicationExecutionController(database)
        prepared = controller.prepare_until_final_authorization(
            context=ctx, execution_plan=plan, browser_plan=browser,
            current_form_snapshot_hash=H, freshness_evidence_hash=H,
            action_session_id=action_session(database, ctx),
        )
        return ctx, browser, plan, controller, prepared

    def test_complete_fake_lifecycle_requires_two_approvals_and_never_uses_transport(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("external transport or process attempted")

        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_approved(database, ctx)
            browser = browser_plan(prefill=True)
            plan = execution_plan(browser["plan_hash"])
            controller = IsolatedApplicationExecutionController(database)
            session_id = action_session(database, ctx, prefill=True)

            with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(socket, "create_connection", forbidden), patch.object(urllib.request, "urlopen", forbidden), patch.object(smtplib, "SMTP", forbidden), patch.object(subprocess, "Popen", forbidden):
                prepared = controller.prepare_until_final_authorization(
                    context=ctx, execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash=H, freshness_evidence_hash=H,
                    action_session_id=session_id,
                )
                self.assertEqual(prepared["status"], "AWAITING_FINAL_AUTHORIZATION")
                self.assertEqual(prepared["checkpoint_count"], 5)
                self.assertEqual(prepared["scoped_action_count"], 4)
                self.assertEqual(prepared["transport_envelope_count"], 4)
                self.assertEqual(prepared["proposed_field_count"], 1)
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
            self.assertEqual(completed["checkpoint_count"], 8)
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
            self.assertEqual(tuple(run), ("CONFIRMED", 8))
            self.assertEqual(phases[-5:], [
                "SCOPED_ACTIONS_VALIDATED", "AWAITING_FINAL_AUTHORIZATION",
                "FINAL_AUTHORIZATION_CONSUMED", "FAKE_SUBMISSION_RECORDED", "RECEIPT_VERIFIED",
            ])
            self.assertEqual(approval_status, "CONSUMED")
            self.assertEqual(final_status, "CONSUMED")
            with database.connect() as connection:
                used_actions = [
                    row[0] for row in connection.execute(
                        "SELECT action FROM external_action_session_uses WHERE session_id=? ORDER BY rowid",
                        (session_id,),
                    )
                ]
            self.assertEqual(used_actions, [
                "read_official_job", "inspect_application_form",
                "prefill_application_form", "upload_materials",
            ])
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
            session_id = action_session(database, ctx)
            prepared = controller.prepare_until_final_authorization(
                context=ctx, execution_plan=plan, browser_plan=browser,
                current_form_snapshot_hash=H, freshness_evidence_hash=H,
                action_session_id=session_id,
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
            session_id = action_session(database, ctx)
            with self.assertRaises(JobOpsError) as changed:
                controller.prepare_until_final_authorization(
                    context=ctx, execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash="sha256:" + "b" * 64,
                    freshness_evidence_hash=H, action_session_id=session_id,
                )
            self.assertEqual(changed.exception.code, "SITE_CHANGED")
            prepared = controller.prepare_until_final_authorization(
                context=ctx, execution_plan=plan, browser_plan=browser,
                current_form_snapshot_hash=H, freshness_evidence_hash=H,
                action_session_id=session_id,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                with database.connect() as connection:
                    connection.execute(
                        "UPDATE application_execution_checkpoints SET status='PASS' WHERE run_id=?",
                        (prepared["run_id"],),
                    )

    def test_missing_complete_action_scope_fails_before_any_session_use(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_approved(database, ctx)
            browser = browser_plan()
            plan = execution_plan(browser["plan_hash"])
            manager = ExternalActionSessionManager(database, ExternalActionSessionPolicy.isolated_fake())
            manager.enable(user_confirmed=True)
            incomplete = manager.issue(
                context=ctx,
                allowed_actions=["inspect_application_form", "upload_materials"],
                user_confirmed=True,
            )
            manager.persist(incomplete, context=ctx)
            controller = IsolatedApplicationExecutionController(database)
            with self.assertRaises(JobOpsError) as blocked:
                controller.prepare_until_final_authorization(
                    context=ctx,
                    execution_plan=plan,
                    browser_plan=browser,
                    current_form_snapshot_hash=H,
                    freshness_evidence_hash=H,
                    action_session_id=incomplete.session_id,
                )
            self.assertEqual(blocked.exception.code, "EXTERNAL_ACTION_NOT_AUTHORIZED")
            with database.connect() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM external_action_session_uses WHERE session_id=?",
                    (incomplete.session_id,),
                ).fetchone()[0], 0)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM application_execution_runs WHERE application_id=?",
                    (ctx.application_id,),
                ).fetchone()[0], 0)

    def test_checkpoint_failure_after_authorization_becomes_unknown_without_retry(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx, browser, plan, controller, prepared = self._prepared(database)
            original = controller._append_checkpoint

            def fail_sequence_six(**kwargs):
                if kwargs["sequence"] == 6:
                    raise RuntimeError("synthetic crash after authorization consumption")
                return original(**kwargs)

            with patch.object(controller, "_append_checkpoint", side_effect=fail_sequence_six):
                with self.assertRaises(RuntimeError):
                    controller.complete_with_fresh_authorization(
                        run_id=prepared["run_id"], context=ctx,
                        execution_plan=plan, browser_plan=browser,
                        current_form_snapshot_hash=H, freshness_evidence_hash=H,
                        user_confirmed=True, fake_confirmation_number="SYNTHETIC-CRASH",
                    )
            with database.connect() as connection:
                run = connection.execute(
                    "SELECT status,checkpoint_sequence FROM application_execution_runs WHERE run_id=?",
                    (prepared["run_id"],),
                ).fetchone()
                phase = connection.execute(
                    "SELECT phase FROM application_execution_checkpoints WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                    (prepared["run_id"],),
                ).fetchone()[0]
                application = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?", (ctx.application_id,),
                ).fetchone()[0]
            self.assertEqual(tuple(run), ("SUBMISSION_UNKNOWN", 6))
            self.assertEqual(phase, "INTERRUPTION_RECONCILED")
            self.assertEqual(application, "SUBMISSION_UNKNOWN")
            with self.assertRaises(JobOpsError) as retried:
                controller.complete_with_fresh_authorization(
                    run_id=prepared["run_id"], context=ctx,
                    execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash=H, freshness_evidence_hash=H,
                    user_confirmed=True, fake_confirmation_number="NEVER-RETRY",
                )
            self.assertEqual(retried.exception.code, "SUBMISSION_UNKNOWN_NO_RETRY")

    def test_checkpoint_failure_after_fake_transport_becomes_unknown_without_retry(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx, browser, plan, controller, prepared = self._prepared(database)
            original = controller._append_checkpoint

            def fail_sequence_seven(**kwargs):
                if kwargs["sequence"] == 7:
                    raise RuntimeError("synthetic crash after fake transport")
                return original(**kwargs)

            with patch.object(controller, "_append_checkpoint", side_effect=fail_sequence_seven):
                with self.assertRaises(RuntimeError):
                    controller.complete_with_fresh_authorization(
                        run_id=prepared["run_id"], context=ctx,
                        execution_plan=plan, browser_plan=browser,
                        current_form_snapshot_hash=H, freshness_evidence_hash=H,
                        user_confirmed=True, fake_confirmation_number="SYNTHETIC-CRASH",
                    )
            with database.connect() as connection:
                run = connection.execute(
                    "SELECT status,checkpoint_sequence FROM application_execution_runs WHERE run_id=?",
                    (prepared["run_id"],),
                ).fetchone()
            self.assertEqual(tuple(run), ("SUBMISSION_UNKNOWN", 7))
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)

    def test_checkpoint_failure_after_verified_receipt_recovers_confirmed(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx, browser, plan, controller, prepared = self._prepared(database)
            original = controller._append_checkpoint

            def fail_sequence_eight(**kwargs):
                if kwargs["sequence"] == 8:
                    raise RuntimeError("synthetic crash after verified receipt")
                return original(**kwargs)

            with patch.object(controller, "_append_checkpoint", side_effect=fail_sequence_eight):
                outcome = controller.complete_with_fresh_authorization(
                    run_id=prepared["run_id"], context=ctx,
                    execution_plan=plan, browser_plan=browser,
                    current_form_snapshot_hash=H, freshness_evidence_hash=H,
                    user_confirmed=True, fake_confirmation_number="SYNTHETIC-RECOVERED",
                )
            self.assertEqual(outcome["status"], "CONFIRMED")
            self.assertTrue(outcome["recovered_after_interruption"])
            with database.connect() as connection:
                run = connection.execute(
                    "SELECT status,checkpoint_sequence FROM application_execution_runs WHERE run_id=?",
                    (prepared["run_id"],),
                ).fetchone()
                phase = connection.execute(
                    "SELECT phase FROM application_execution_checkpoints WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                    (prepared["run_id"],),
                ).fetchone()[0]
            self.assertEqual(tuple(run), ("CONFIRMED", 8))
            self.assertEqual(phase, "INTERRUPTION_RECONCILED")

    def test_restart_reconciliation_marks_consumed_run_unknown_without_replay(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx, browser, plan, controller, prepared = self._prepared(database)
            authorization = issue_final_submission_authorization(
                context=ctx, execution_plan=plan, freshness_evidence_hash=H,
                user_confirmed=True,
            )
            controller.gateway.persist_final_submission_authorization(
                authorization, context=ctx, execution_plan=plan, freshness_evidence_hash=H,
            )
            controller.gateway.begin_submission(
                ctx, execution_plan=plan, freshness_evidence_hash=H,
            )

            restarted = IsolatedApplicationExecutionController(database)
            result = restarted.reconcile_interrupted_runs()
            self.assertEqual(result["runs_examined"], 1)
            self.assertEqual(result["submission_unknown"], 1)
            self.assertEqual(result["automatic_retries"], 0)
            with database.connect() as connection:
                run = connection.execute(
                    "SELECT status,checkpoint_sequence FROM application_execution_runs WHERE run_id=?",
                    (prepared["run_id"],),
                ).fetchone()
            self.assertEqual(tuple(run), ("SUBMISSION_UNKNOWN", 6))


if __name__ == "__main__":
    unittest.main()
