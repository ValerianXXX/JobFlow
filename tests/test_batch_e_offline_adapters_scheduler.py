from __future__ import annotations

import smtplib
import socket
import subprocess
import unittest
import urllib.request
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from _support import project_temp
from jobops.adapters import AdapterRegistry, DisabledAdapter, FakeBrowserPrefillAdapter, FakeMaterialUploadAdapter, FakeOfficialSourceAdapter, FakeOutboxAdapter, FakeReceiptAdapter, FakeSubmissionAdapter, audit_real_external_actions
from jobops.application_execution import build_application_execution_plan
from jobops.ats_transport import build_ats_transport_envelope
from jobops.approvals import ApprovalContext, UploadBinding, issue_approval
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.external_actions import ExternalActionGateway, ExternalActionPolicy
from jobops.final_submission import issue_final_submission_authorization
from jobops.fake_scheduler import FakeClock, OfflineScheduler
from jobops.recovery import RecoveryManager
from jobops.util import canonical_json, iso_utc, sha256_bytes


H = "sha256:" + "a" * 64


def safe_browser_plan() -> dict:
    actions = [{
        "control_ref": "CTL-ABCDEF123456", "classification": "final_submit_stop", "action": "STOP",
        "binding_kind": "NONE", "binding_ref": None, "reason_code": "FINAL_SUBMIT_EXTERNAL_ACTION",
    }]
    material = {"form_snapshot_hash": H, "source_route_hash": H, "canonical_url": "https://example.com/careers/a", "actions": actions}
    return {
        "schema_version": 1, "status": "NO_FIELDS_BOUND", "form_snapshot_hash": H, "source_route_hash": H,
        "canonical_url": "https://example.com/careers/a", "plan_hash": sha256_bytes(canonical_json(material)),
        "fillable_count": 0, "stopped_count": 1, "actions": actions, "submit_blocked": True,
        "upload_blocked": True, "account_creation_blocked": True, "browser_actions": 0,
        "network_actions": 0, "real_external_actions": 0,
    }


def context() -> ApprovalContext:
    return ApprovalContext(
        application_id="APP-ABCDEF123456", job_id="JOB-ABCDEF123456", jd_snapshot_hash=H, jd_freshness_hash=H,
        source_route_hash=H, canonical_url="https://example.com/careers/a", ats_tenant="example", ats_board="careers",
        ats_job_identity="a", profile_version="1", claim_set_hash=H, form_snapshot_hash=H, answers_hash=H,
        review_packet_hash=H, uploads=(UploadBinding("resume.pdf", "resume", H),),
        external_actions=("upload_material", "submit_application"), site_policy_version="1",
    )


def seed_awaiting(database: JobOpsDB, ctx: ApprovalContext) -> None:
    now = iso_utc()
    with database.connect() as connection:
        connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (ctx.job_id, "txt", "fixture", ctx.canonical_url, "Example", "Analyst", "Remote", "FORM_VALIDATED", now, now))
        connection.execute("INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)", (ctx.application_id, ctx.job_id, ctx.canonical_url, "AWAITING_APPROVAL", H, H, 1, "secure-ref:SYNTHETIC01", "AWAITING_APPROVAL", now))
        connection.execute("INSERT INTO application_bindings VALUES(?,?,?,?)", (ctx.application_id, ctx.context_hash, json.dumps(ctx.as_dict(), sort_keys=True), now))


def execution_plan(ctx: ApprovalContext) -> dict:
    return build_application_execution_plan(
        application_id=ctx.application_id,
        source_route={"provider": "company", "route_hash": H, "guest_mode": "GUEST_SELECTED", "account_action": "NONE"},
        form_snapshot_hash=H, browser_plan_hash=H, form_fields=[],
        material_plan={
            "status": "READY_FOR_REVIEW", "cover_letter": {"generation_status": "NOT_GENERATED"},
            "portfolio_file": {"binding_status": "NOT_REQUESTED"},
            "all_uploads_and_submission_blocked": True, "real_external_actions": 0,
        }, pending_limit=10,
    )


class OfflineAdapterTests(unittest.TestCase):
    def test_fake_official_source_streams_only_bounded_local_fixtures(self) -> None:
        with project_temp() as temp:
            fixture = temp / "jobs.html"
            fixture.write_bytes(b"safe synthetic fixture")
            adapter = FakeOfficialSourceAdapter(temp)
            self.assertEqual(adapter.discover({"fixture": fixture.name})["network_actions"], 0)
            with patch("jobops.adapters.MAX_FAKE_FIXTURE_BYTES", 4), self.assertRaises(JobOpsError) as caught:
                adapter.discover({"fixture": fixture.name})
            self.assertEqual(caught.exception.code, "LOCAL_FIXTURE_TOO_LARGE")

    def test_registry_contains_only_fake_or_disabled_and_real_capabilities_fail_closed(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            registry = AdapterRegistry.offline_only(database=database, fixture_root=temp)
            self.assertTrue(set(registry.manifest().values()) <= {"fake", "mock", "dry-run", "disabled"})
            with self.assertRaises(JobOpsError) as caught:
                registry.resolve("submission").submit({"application_id": None})
            self.assertEqual(caught.exception.code, "PHASE_NOT_AUTHORIZED")
            audit = audit_real_external_actions(database)
            self.assertEqual(audit["attempt_count"], 1)
            self.assertEqual(audit["real_external_actions"], 0)

    def test_fake_operations_do_not_touch_socket_dns_http_smtp_browser_or_system_scheduler(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("external transport or system process attempted")

        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            ctx = context()
            seed_awaiting(database, ctx)
            envelope = build_ats_transport_envelope(
                provider="company", action="submit_application",
                application_id=ctx.application_id, run_id="RUN-ABCDEF123456",
                application_context_hash=ctx.context_hash, source_route_hash=H,
                form_snapshot_hash=H, execution_plan_hash=H, request_payload_hash=H,
                authorization_kind="FINAL_SUBMISSION_AUTHORIZATION", authorization_hash=H,
            )
            adapters = [
                lambda: FakeBrowserPrefillAdapter().prefill({
                    "plan": safe_browser_plan(), "current_form_snapshot_hash": H, "isolation_policy": "ISOLATED_FAKE_ONLY",
                }),
                lambda: FakeMaterialUploadAdapter().upload({
                    "application_id": "APP-ABCDEF123456",
                    "upload_bindings": [{"purpose": "resume", "sha256": H}],
                    "isolation_policy": "ISOLATED_FAKE_ONLY",
                }),
                lambda: FakeSubmissionAdapter(database).submit({"transport_envelope": envelope, "isolation_policy": "ISOLATED_FAKE_ONLY"}),
                lambda: FakeOutboxAdapter().send_email({"to": "synthetic@example.test", "body": "fixture"}),
                lambda: FakeReceiptAdapter().verify({"source": "fake-receipt", "confirmation_number": "SYN-1"}),
            ]
            with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(socket, "create_connection", forbidden), patch.object(urllib.request, "urlopen", forbidden), patch.object(smtplib, "SMTP", forbidden), patch.object(subprocess, "Popen", forbidden):
                results = [operation() for operation in adapters]
            self.assertTrue(all(result.get("real_side_effects", 0) == 0 for result in results))
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)

    def test_fake_browser_blocks_submit_and_fake_receipt_never_guesses(self) -> None:
        plan = safe_browser_plan()
        plan["actions"][0].update({"action": "PROPOSE_PREFILL", "binding_kind": "SECURE_REF", "binding_ref": "secure-ref:SYNTHETIC01"})
        plan["fillable_count"], plan["stopped_count"] = 1, 0
        plan["status"] = "LOCAL_PLAN_READY"
        material = {"form_snapshot_hash": H, "source_route_hash": H, "canonical_url": "https://example.com/careers/a", "actions": plan["actions"]}
        plan["plan_hash"] = sha256_bytes(canonical_json(material))
        with self.assertRaises(JobOpsError) as caught:
            FakeBrowserPrefillAdapter().prefill({"plan": plan, "current_form_snapshot_hash": H, "isolation_policy": "ISOLATED_FAKE_ONLY"})
        self.assertEqual(caught.exception.code, "BROWSER_PLAN_PROTECTED_FIELD")
        self.assertEqual(FakeReceiptAdapter().verify({"source": "fake-receipt"})["status"], "SUBMISSION_UNKNOWN")
        with self.assertRaises(JobOpsError) as plaintext_upload:
            FakeMaterialUploadAdapter().upload({
                "application_id": "APP-ABCDEF123456",
                "upload_bindings": [{"purpose": "resume", "sha256": H, "filename": "private.pdf"}],
                "isolation_policy": "ISOLATED_FAKE_ONLY",
            })
        self.assertEqual(plaintext_upload.exception.code, "FAKE_UPLOAD_BINDINGS_INVALID")

    def test_only_verified_fake_receipt_can_confirm_after_fake_submission(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db"); database.initialize(); ctx = context(); seed_awaiting(database, ctx)
            gateway = ExternalActionGateway(database, ExternalActionPolicy.isolated_fake())
            gateway.persist_approval(issue_approval(context=ctx, user_confirmed=True), ctx)
            plan = execution_plan(ctx)
            final_authorization = issue_final_submission_authorization(
                context=ctx, execution_plan=plan, freshness_evidence_hash=H, user_confirmed=True,
            )
            gateway.persist_final_submission_authorization(
                final_authorization, context=ctx, execution_plan=plan, freshness_evidence_hash=H,
            )
            gateway.begin_submission(ctx, execution_plan=plan, freshness_evidence_hash=H)
            gateway.mark_fake_submitted(ctx.application_id, fake_evidence={"transport": "memory-only"})
            receipt = {
                "receipt_id": "RCP-ABCDEF123456", "application_id": ctx.application_id, "source": "fake-receipt",
                "confirmation_type": "confirmation_number", "confirmation_hash": H, "verified": True, "verified_at": iso_utc(),
            }
            result = gateway.confirm_with_receipt(ctx.application_id, receipt)
            self.assertEqual(result["status"], "CONFIRMED")
            self.assertEqual(audit_real_external_actions(database)["real_external_actions"], 0)


class RecoveryTests(unittest.TestCase):
    def test_submission_unknown_never_retries_and_site_change_needs_reanalysis(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db"); database.initialize(); now = iso_utc()
            with database.connect() as connection:
                connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", ("JOB-ABCDEF123456", "txt", "fixture", None, "Example", "Analyst", None, "FORM_VALIDATED", now, now))
                connection.execute("INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)", ("APP-ABCDEF123456", "JOB-ABCDEF123456", "example", "SUBMISSION_UNKNOWN", H, H, 1, "secure-ref:SYNTHETIC01", "APPROVED", now))
            manager = RecoveryManager(database)
            with self.assertRaises(JobOpsError) as caught:
                manager.resume_safe_step("APP-ABCDEF123456", validation_material={"context_hash": H})
            self.assertEqual(caught.exception.code, "SUBMISSION_UNKNOWN_NO_RETRY")
            with database.connect() as connection:
                connection.execute("UPDATE applications SET status='SITE_CHANGED',last_safe_state='FORM_VALIDATED' WHERE application_id='APP-ABCDEF123456'")
                connection.execute("INSERT INTO application_bindings VALUES(?,?,?,?)", ("APP-ABCDEF123456", H, "{}", now))
            result = manager.resume_safe_step("APP-ABCDEF123456", validation_material={"context_hash": H})
            self.assertEqual(result["status"], "SITE_CHANGED")
            self.assertEqual(result["decision"], "REANALYZE_REQUIRED")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT status FROM applications WHERE application_id='APP-ABCDEF123456'").fetchone()[0], "SITE_CHANGED")


class FakeSchedulerTests(unittest.TestCase):
    def test_daily_dedupe_deadline_pause_resume_retry_and_capacity_recovery(self) -> None:
        clock = FakeClock(datetime(2026, 8, 12, tzinfo=timezone.utc))
        scheduler = OfflineScheduler(clock, retry_delay=timedelta(minutes=5), max_attempts=2)
        due = iso_utc(clock.now)
        first = scheduler.enqueue("daily", {"job": "one"}, due_at=due, daily=True)
        duplicate = scheduler.enqueue("daily", {"job": "changed"}, due_at=due, daily=True)
        self.assertFalse(first["deduplicated"])
        self.assertTrue(duplicate["deduplicated"])
        scheduler.enqueue("expired", {}, due_at=due, deadline=iso_utc(clock.now - timedelta(seconds=1)))
        scheduler.enqueue("capacity", {}, due_at=due)
        scheduler.enqueue("retry", {}, due_at=due)
        scheduler.pause()
        self.assertEqual(scheduler.tick(lambda _: "SUCCESS"), [])
        scheduler.resume()
        calls = {"retry": 0}

        def handler(payload):
            if payload == {}:
                # Dispatch based on call order: expired is filtered before handler.
                calls["retry"] += 1
                if calls["retry"] == 1:
                    return "DEFERRED_CAPACITY"
                raise JobOpsError("SYNTHETIC_CRASH", "fixture crash")
            return "SUCCESS"

        results = scheduler.tick(handler)
        statuses = {item["key"]: item["status"] for item in results}
        self.assertEqual(statuses["daily"], "SCHEDULED")
        self.assertEqual(statuses["expired"], "DEADLINE_PASSED")
        self.assertEqual(statuses["capacity"], "DEFERRED_CAPACITY")
        self.assertEqual(statuses["retry"], "RETRY_WAIT")
        clock.advance(minutes=5)
        second = scheduler.tick(lambda _: "SUCCESS")
        self.assertEqual({item["key"]: item["status"] for item in second}, {"capacity": "COMPLETED", "retry": "COMPLETED"})
        self.assertEqual(scheduler.snapshot()["system_tasks_registered"], 0)


if __name__ == "__main__":
    unittest.main()
