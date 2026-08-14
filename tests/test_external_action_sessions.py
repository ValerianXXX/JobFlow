from __future__ import annotations

import json
import sqlite3
import unittest

from _support import project_temp
from jobops.approvals import ApprovalContext, UploadBinding, issue_approval
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.external_action_sessions import ExternalActionSessionManager, ExternalActionSessionPolicy
from jobops.external_actions import ExternalActionGateway, ExternalActionPolicy
from jobops.util import iso_utc


H = "sha256:" + "a" * 64
H2 = "sha256:" + "b" * 64


def context(*, form_hash: str = H) -> ApprovalContext:
    return ApprovalContext(
        application_id="APP-ABCDEF123456", job_id="JOB-ABCDEF123456",
        jd_snapshot_hash=H, jd_freshness_hash=H, source_route_hash=H,
        canonical_url="https://example.com/careers/a", ats_tenant="example",
        ats_board="careers", ats_job_identity="a", profile_version="1",
        claim_set_hash=H, form_snapshot_hash=form_hash, answers_hash=H,
        review_packet_hash=H,
        uploads=(UploadBinding("synthetic-resume.pdf", "resume", H),),
        external_actions=("upload_material", "submit_application"),
        site_policy_version="1",
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


class ExternalActionSessionTests(unittest.TestCase):
    def test_production_build_cannot_enable_or_issue_a_session(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            manager = ExternalActionSessionManager(database, ExternalActionSessionPolicy.production_disabled())
            self.assertFalse(manager.control_state()["enabled"])
            with self.assertRaises(JobOpsError) as blocked:
                manager.enable(user_confirmed=True)
            self.assertEqual(blocked.exception.code, "PHASE_NOT_AUTHORIZED")
            with self.assertRaises(JobOpsError) as stopped:
                manager.issue(
                    context=context(), allowed_actions=["read_official_job"], user_confirmed=True,
                )
            self.assertEqual(stopped.exception.code, "EXTERNAL_ACTION_KILL_SWITCH_ACTIVE")

    def test_isolated_session_is_scoped_one_use_per_action_and_kill_switch_revokes(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_approved(database, ctx)
            manager = ExternalActionSessionManager(database, ExternalActionSessionPolicy.isolated_fake())
            control = manager.enable(user_confirmed=True)
            session = manager.issue(
                context=ctx,
                allowed_actions=["upload_materials", "read_official_job", "inspect_application_form"],
                user_confirmed=True,
            )
            self.assertEqual(session.allowed_actions, tuple(sorted(session.allowed_actions)))
            persisted = manager.persist(session, context=ctx)
            self.assertEqual(persisted["mode"], "ISOLATED_FAKE")
            scope = manager.validate_scope(
                session_id=session.session_id,
                context=ctx,
                required_actions=["read_official_job", "inspect_application_form"],
            )
            self.assertEqual(scope["required_action_count"], 2)
            self.assertEqual(scope["real_external_actions"], 0)
            use = manager.record_isolated_use(
                session_id=session.session_id, context=ctx, action="read_official_job",
                request_hash=H, result_code="FAKE_FRESHNESS_PASS",
            )
            self.assertEqual(use["real_external_actions"], 0)
            with self.assertRaises(JobOpsError) as scope_replayed:
                manager.validate_scope(
                    session_id=session.session_id,
                    context=ctx,
                    required_actions=["read_official_job", "inspect_application_form"],
                )
            self.assertEqual(scope_replayed.exception.code, "EXTERNAL_ACTION_SESSION_REPLAYED")
            with self.assertRaises(JobOpsError) as replay:
                manager.record_isolated_use(
                    session_id=session.session_id, context=ctx, action="read_official_job",
                    request_hash=H, result_code="FAKE_FRESHNESS_PASS",
                )
            self.assertEqual(replay.exception.code, "EXTERNAL_ACTION_SESSION_REPLAYED")
            with self.assertRaises(JobOpsError) as out_of_scope:
                manager.record_isolated_use(
                    session_id=session.session_id, context=ctx, action="prefill_application_form",
                    request_hash=H, result_code="FAKE_PREFILL_PASS",
                )
            self.assertEqual(out_of_scope.exception.code, "EXTERNAL_ACTION_NOT_AUTHORIZED")
            with self.assertRaises(JobOpsError) as drifted:
                manager.record_isolated_use(
                    session_id=session.session_id, context=context(form_hash=H2),
                    action="inspect_application_form", request_hash=H,
                    result_code="FAKE_FORM_PASS",
                )
            self.assertEqual(drifted.exception.code, "EXTERNAL_ACTION_SESSION_INVALIDATED")
            stopped = manager.disable(reason="USER_KILL_SWITCH")
            self.assertEqual(stopped["status"], "EXTERNAL_ACTIONS_DISABLED")
            self.assertGreater(stopped["generation"], control["generation"])
            self.assertFalse(manager.control_state()["enabled"])
            with self.assertRaises(JobOpsError) as invalidated:
                manager.record_isolated_use(
                    session_id=session.session_id, context=ctx, action="inspect_application_form",
                    request_hash=H, result_code="FAKE_FORM_PASS",
                )
            self.assertEqual(invalidated.exception.code, "EXTERNAL_ACTION_SESSION_NOT_ACTIVE")
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT status FROM external_action_sessions WHERE session_id=?", (session.session_id,),
                ).fetchone()
                real = connection.execute(
                    "SELECT COALESCE(SUM(real_side_effect),0) FROM external_action_session_uses"
                ).fetchone()[0]
            self.assertEqual(row["status"], "INVALIDATED")
            self.assertEqual(real, 0)

    def test_final_submit_is_never_part_of_a_regular_session_and_use_log_is_append_only(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_approved(database, ctx)
            manager = ExternalActionSessionManager(database, ExternalActionSessionPolicy.isolated_fake())
            manager.enable(user_confirmed=True)
            with self.assertRaises(JobOpsError) as separate:
                manager.issue(
                    context=ctx, allowed_actions=["submit_application"], user_confirmed=True,
                )
            self.assertEqual(separate.exception.code, "SEPARATE_ACTION_AUTHORIZATION_REQUIRED")
            session = manager.issue(
                context=ctx, allowed_actions=["inspect_application_form"], user_confirmed=True,
            )
            manager.persist(session, context=ctx)
            manager.record_isolated_use(
                session_id=session.session_id, context=ctx, action="inspect_application_form",
                request_hash=H, result_code="FAKE_FORM_PASS",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                with database.connect() as connection:
                    connection.execute(
                        "DELETE FROM external_action_session_uses WHERE session_id=?", (session.session_id,),
                    )


if __name__ == "__main__":
    unittest.main()
