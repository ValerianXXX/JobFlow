from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.forms import build_mock_ats_site, map_fields
from jobops.queueing import queue_decision, validate_pending_limit
from jobops.secure_store import WindowsDPAPIStore
from jobops.sourcing import assess_job_freshness, verify_source_route
from jobops.tracker import schedule_reminder, upsert_application
from jobops.util import iso_utc


ATS = ["myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com"]
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def binding() -> dict[str, str]:
    return {"provider": "workday", "company_registrable_domain": "example.com", "ats_host": "example.wd5.myworkdayjobs.com", "tenant": "example", "board": "careers", "job_identity": "123", "official_page_hash": HASH_A, "jd_snapshot_hash": HASH_B}


class SourcingQueueFormTests(unittest.TestCase):
    def test_official_to_ats_guest_route_is_allowed(self) -> None:
        route = verify_source_route(
            company_domain="example.com",
            official_entry_url="https://example.com/careers/analyst",
            current_url="https://example.wd5.myworkdayjobs.com/job/123",
            navigation_history=["https://example.com/careers/analyst", "https://example.wd5.myworkdayjobs.com/job/123"],
            approved_ats_hosts=ATS,
            guest_available=True,
            tenant_binding=binding(), official_page_hash=HASH_A, jd_snapshot_hash=HASH_B,
        )
        self.assertEqual(route.route_kind, "OFFICIAL_TO_APPROVED_ATS")
        self.assertEqual(route.guest_mode, "GUEST_SELECTED")
        self.assertEqual(route.account_action, "NONE")

    def test_direct_job_board_or_unapproved_redirect_is_blocked(self) -> None:
        with self.assertRaises(JobOpsError) as caught:
            verify_source_route(
                company_domain="example.com",
                official_entry_url="https://jobs-board.test/example",
                current_url="https://jobs-board.test/example",
                navigation_history=["https://jobs-board.test/example"],
                approved_ats_hosts=ATS,
                guest_available=True,
            )
        self.assertEqual(caught.exception.code, "COMPANY_DOMAIN_MISMATCH")

    def test_route_rejects_sensitive_query_fields_before_persistence(self) -> None:
        with self.assertRaises(JobOpsError) as entry:
            verify_source_route(
                company_domain="example.com",
                official_entry_url="https://example.com/careers?session_token=private-value",
                current_url="https://example.com/careers?session_token=private-value",
                navigation_history=["https://example.com/careers?session_token=private-value"],
                approved_ats_hosts=ATS,
                guest_available=True,
            )
        self.assertEqual(entry.exception.code, "ROUTE_URL_SENSITIVE_QUERY")

        with self.assertRaises(JobOpsError) as ats:
            verify_source_route(
                company_domain="example.com",
                official_entry_url="https://example.com/careers",
                current_url="https://jobs.lever.co/example/123?signature=private-value",
                navigation_history=[
                    "https://example.com/careers",
                    "https://jobs.lever.co/example/123?signature=private-value",
                ],
                approved_ats_hosts=ATS,
                guest_available=True,
                tenant_binding={
                    "provider": "lever", "company_registrable_domain": "example.com",
                    "ats_host": "jobs.lever.co", "tenant": "example", "board": "default",
                    "job_identity": "123", "official_page_hash": HASH_A, "jd_snapshot_hash": HASH_B,
                },
                official_page_hash=HASH_A,
                jd_snapshot_hash=HASH_B,
            )
        self.assertEqual(ats.exception.code, "ROUTE_URL_SENSITIVE_QUERY")

    def test_no_guest_requires_account_approval_not_auto_registration(self) -> None:
        route = verify_source_route(
            company_domain="example.com",
            official_entry_url="https://example.com/jobs/analyst",
            current_url="https://example.wd5.myworkdayjobs.com/job/123",
            navigation_history=["https://example.com/jobs/analyst", "https://example.wd5.myworkdayjobs.com/job/123"],
            approved_ats_hosts=ATS,
            guest_available=False,
            tenant_binding=binding(), official_page_hash=HASH_A, jd_snapshot_hash=HASH_B,
        )
        self.assertEqual(route.status, "NEEDS_ACCOUNT_APPROVAL")
        self.assertEqual(route.account_action, "NEEDS_ACCOUNT_APPROVAL")

    def test_pending_limit_continues_other_jobs_until_full(self) -> None:
        self.assertEqual(queue_decision(3, 5).decision, "CONTINUE_OTHER_JOBS")
        full = queue_decision(5, 5)
        self.assertFalse(full.continue_intake)
        self.assertEqual(full.decision, "PAUSE_NEW_INTAKE_AT_LIMIT")
        with self.assertRaises(JobOpsError):
            validate_pending_limit(0)

    def test_database_pending_limit_is_user_configurable_and_idempotent(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            database.set_pending_limit(2)
            now = iso_utc()
            with database.connect() as connection:
                for index in range(2):
                    job_id = f"JOB-{index}"
                    connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (job_id, "manual", "fixture", None, "Example", "Analyst", None, "DISCOVERED", now, now))
                    connection.execute("INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)", (f"APP-{index}", job_id, "example.test", "AWAITING_APPROVAL", None, None, 1, None, "AWAITING_APPROVAL", now))
            decision = database.pending_queue_decision()
            self.assertEqual(decision.pending_count, 2)
            self.assertFalse(decision.continue_intake)
            with self.assertRaises(JobOpsError) as blocked:
                database.set_pending_limit(1)
            self.assertEqual(blocked.exception.code, "PENDING_LIMIT_BELOW_ACTIVE")
            self.assertEqual(database.pending_queue_decision().pending_limit, 2)

    def test_all_sensitive_fields_and_submit_are_blocked(self) -> None:
        blocked = ["work_authorization", "signature", "disability", "salary"]
        fields = [
            {"id": "portfolio", "label": "Portfolio URL"},
            {"id": "auth", "label": "Work authorization"},
            {"id": "signature", "label": "Electronic signature"},
            {"id": "disability", "label": "Disability"},
        ]
        mapped = map_fields(fields, {"portfolio": "https://example.test/portfolio"}, blocked)
        self.assertTrue(mapped["submit_blocked"])
        self.assertEqual(len(mapped["sensitive_fields"]), 3)
        self.assertEqual([item["action"] for item in mapped["fields"]], ["PREFILL", "STOP", "STOP", "STOP"])

    def test_local_provider_sites_are_created_without_network(self) -> None:
        with project_temp() as temp:
            for provider in ("greenhouse", "lever", "workday", "ashby", "smartrecruiters"):
                manifest = build_mock_ats_site(temp, provider, [{"id": "name", "label": "Display name"}])
                self.assertEqual(manifest["network_actions"], 0)
                self.assertTrue(manifest["submit_blocked"])
                self.assertTrue((temp / f"{provider}.html").is_file())
                html = (temp / f"{provider}.html").read_text(encoding="utf-8")
                self.assertIn('data-local-simulation="true"', html)
                self.assertIn('id="submit" type="button" disabled', html)
                self.assertNotIn("<form action=", html)

    def test_official_freshness_must_be_current_before_prefill(self) -> None:
        now = datetime.now(timezone.utc)
        current = assess_job_freshness(official_listing_present=True, application_form_available=True, checked_at=iso_utc(now), now=now)
        self.assertTrue(current["may_apply"])
        stale = assess_job_freshness(official_listing_present=True, application_form_available=True, checked_at=iso_utc(now - timedelta(hours=1)), now=now)
        self.assertEqual(stale["status"], "NEEDS_REFRESH")
        removed = assess_job_freshness(official_listing_present=False, application_form_available=True, checked_at=iso_utc(now), now=now)
        self.assertEqual(removed["status"], "OFFICIAL_LISTING_REMOVED")

    def test_tracker_deduplicates_application_and_reminder(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            now = iso_utc()
            with database.connect() as connection:
                connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", ("JOB-1", "manual", "fixture", None, "Example", "Analyst", None, "DISCOVERED", now, now))
            for _ in range(2):
                upsert_application(database, application_id="APP-1", job_id="JOB-1", site="example.test", status="FORM_VALIDATED", resume_hash="sha256:" + "a" * 64, answers_hash="sha256:" + "b" * 64, secure_profile_ref="secure-ref:SYNTHETIC_PROFILE_001")
                schedule_reminder(database, reminder_id="REM-1", application_id="APP-1", kind="FOLLOW_UP", due_at="2026-08-20T00:00:00Z")
            counts = database.table_counts()
            self.assertEqual(counts["applications"], 1)
            self.assertEqual(counts["reminders"], 1)

    def test_windows_dpapi_roundtrip_does_not_log_plaintext(self) -> None:
        script = PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
        result = WindowsDPAPIStore(script).validate_roundtrip("SYNTHETIC_PRIVATE_FIXTURE_VALUE")
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["plaintext_logged"])


if __name__ == "__main__":
    unittest.main()
