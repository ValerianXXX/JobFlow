from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from _support import PROJECT, project_temp
from jobops.approvals import ApprovalContext, UploadBinding, issue_approval, submission_confirmation_status, validate_approval
from jobops.collector import JobCollector
from jobops.db import JobOpsDB
from jobops.errors import ApprovalError, JobOpsError, SecurityBoundaryError
from jobops.review import build_review_packet
from jobops.security import assert_no_plaintext_secret, classify_form_field, validate_secure_reference
from jobops.state_machine import assert_transition
from jobops.util import iso_utc


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def approval_context(*, resume_hash: str = HASH_A) -> ApprovalContext:
    return ApprovalContext(
        application_id="APP-ABCDEF123456", job_id="JOB-ABCDEF123456", jd_snapshot_hash=HASH_A,
        jd_freshness_hash=HASH_A, source_route_hash=HASH_A, canonical_url="https://example.test/job",
        ats_tenant="example", ats_board="official", ats_job_identity="job", profile_version="1",
        claim_set_hash=HASH_A, form_snapshot_hash=HASH_A, answers_hash=HASH_B, review_packet_hash=HASH_A,
        uploads=(UploadBinding("resume.pdf", "resume", resume_hash),), external_actions=("submit_application",),
        site_policy_version="1",
    )


class WorkflowGateTests(unittest.TestCase):
    def test_jd_collection_is_deduplicated(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            collector = JobCollector(database, temp / "jobs")
            first = collector.collect_text("A synthetic job description", company="Example", title="Analyst")
            second = collector.collect_text("A synthetic job description", company="Example", title="Analyst")
            self.assertEqual(first["status"], "SNAPSHOTTED")
            self.assertEqual(second["status"], "DUPLICATE")
            self.assertEqual(database.table_counts()["jobs"], 1)
            self.assertEqual(database.table_counts()["jd_snapshots"], 1)

            for locator in ("C:\\Users\\private\\job.txt", "../outside.txt", "job.txt:private"):
                with self.subTest(locator=locator), self.assertRaises(JobOpsError) as blocked:
                    collector.collect_text("Another synthetic description", source_locator=locator)
                self.assertEqual(blocked.exception.code, "JOB_SOURCE_LOCATOR_INVALID")

            for official_url, code in (
                ("http://example.test/jobs/1", "HTTPS_REQUIRED"),
                ("https://example.test/jobs/1?session_token=private", "JOB_SOURCE_URL_SENSITIVE_QUERY"),
            ):
                with self.subTest(official_url=official_url), self.assertRaises(JobOpsError) as blocked:
                    collector.collect_text("Another synthetic description", official_url=official_url)
                self.assertEqual(blocked.exception.code, code)
            self.assertEqual(database.table_counts()["jobs"], 1)

            for invalid_content, code in (("", "JOB_SNAPSHOT_CONTENT_INVALID"), ("12345", "JOB_SNAPSHOT_CONTENT_TOO_LARGE")):
                with mock.patch("jobops.collector.MAX_COLLECTED_JD_CHARACTERS", 4), self.assertRaises(JobOpsError) as blocked:
                    collector.collect_text(invalid_content)
                self.assertEqual(blocked.exception.code, code)
            with self.assertRaises(JobOpsError) as blocked:
                collector.collect_text("Another synthetic description", title="bad\nmetadata")
            self.assertEqual(blocked.exception.code, "JOB_METADATA_INVALID")
            self.assertEqual(database.table_counts()["jobs"], 1)

    def test_jd_snapshot_failure_rolls_back_database_and_files(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            with database.connect() as connection:
                connection.execute(
                    "CREATE TRIGGER reject_snapshot BEFORE INSERT ON jd_snapshots "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic snapshot failure'); END"
                )
            collector = JobCollector(database, temp / "jobs")

            with self.assertRaises(sqlite3.IntegrityError):
                collector.collect_text("A synthetic job description")

            self.assertEqual(database.table_counts()["jobs"], 0)
            self.assertEqual(database.table_counts()["jd_snapshots"], 0)
            self.assertEqual(list((temp / "jobs").rglob("*.txt")), [])
            self.assertEqual(list((temp / "jobs").rglob("*.tmp")), [])

    def test_plaintext_secrets_and_invalid_private_values_are_rejected(self) -> None:
        with self.assertRaises(SecurityBoundaryError):
            assert_no_plaintext_secret("pass" + "word = synthetic-fixture-value")
        with self.assertRaises(SecurityBoundaryError):
            validate_secure_reference("not-a-secure-reference")
        validate_secure_reference("secure-ref:AbCdEf123456")

    def test_sensitive_field_interception_is_complete_for_policy_list(self) -> None:
        policy = json.loads((PROJECT / "config" / "policy.json").read_text(encoding="utf-8"))
        categories = policy["blocked_form_categories"]
        stopped = [category for category in categories if classify_form_field(category, categories) == "STOP_REQUIRED"]
        self.assertEqual(stopped, categories)
        self.assertEqual(classify_form_field("portfolio URL", categories), "PREFILL_ALLOWED")

    def test_approval_requires_confirmation_and_exact_binding(self) -> None:
        with self.assertRaises(ApprovalError):
            issue_approval(context=approval_context(), user_confirmed=False)
        approval = issue_approval(context=approval_context(), user_confirmed=True)
        self.assertEqual(validate_approval(approval, context=approval_context(), required_actions=("submit_application",)), "APPROVAL_VALID")
        self.assertEqual(validate_approval(approval, context=approval_context(resume_hash=HASH_B)), "APPROVAL_INVALIDATED")

    def test_approval_expiry_and_submission_evidence(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        approval = issue_approval(context=approval_context(), user_confirmed=True, ttl_minutes=5, now=old)
        self.assertEqual(validate_approval(approval, context=approval_context()), "APPROVAL_EXPIRED")
        self.assertEqual(submission_confirmation_status(confirmation_page=True, confirmation_number=None, confirmation_email=False), "SUBMISSION_UNKNOWN")
        self.assertEqual(submission_confirmation_status(confirmation_page=True, confirmation_number="ABC123", confirmation_email=False), "CONFIRMED")

    def test_review_packet_requires_traceable_bullets_and_upload_hashes(self) -> None:
        base = {
            "job": {"job_id": "JOB-1", "company": "Example", "title": "Analyst", "official_url": "https://example.test/job"},
            "jd_captured_at": iso_utc(),
            "fit": {"overall": 80},
            "hard_gaps": [],
            "resume_bullets": [{"text": "Approved exact wording", "claim_id": "CLM-TEST", "evidence": ["case.md#evidence"]}],
            "master_resume_diff": [],
            "form_questions": [],
            "sensitive_fields": [],
            "uploads": [{"filename": "resume.pdf", "sha256": HASH_A}],
            "external_actions": ["upload resume", "submit application"],
            "source_route": {"route_kind": "OFFICIAL_TO_APPROVED_ATS"},
            "queue": {"pending_limit": 10, "pending_count": 1},
        }
        packet = build_review_packet(base)
        self.assertEqual(packet["status"], "AWAITING_APPROVAL")
        base["resume_bullets"] = [{"text": "Untraceable"}]
        with self.assertRaises(JobOpsError):
            build_review_packet(base)

    def test_state_machine_prevents_skips_and_unknown_retry(self) -> None:
        assert_transition("DISCOVERED", "SNAPSHOTTED")
        with self.assertRaises(JobOpsError):
            assert_transition("DISCOVERED", "APPROVED")
        with self.assertRaises(JobOpsError) as caught:
            assert_transition("SUBMISSION_UNKNOWN", "SUBMITTING")
        self.assertEqual(caught.exception.code, "SUBMISSION_UNKNOWN_NO_RETRY")

    def test_database_records_last_safe_state_on_block(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            now = iso_utc()
            with database.connect() as connection:
                connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", ("JOB-1", "manual", "fixture", None, "Example", "Analyst", None, "DISCOVERED", now, now))
                connection.execute("INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)", ("APP-1", "JOB-1", "example.test", "FORM_VALIDATED", HASH_A, HASH_B, 1, None, "FORM_VALIDATED", now))
            database.transition_application("APP-1", "BLOCKED_CAPTCHA")
            with database.connect() as connection:
                row = connection.execute("SELECT status,last_safe_state FROM applications WHERE application_id='APP-1'").fetchone()
                self.assertEqual(row["status"], "BLOCKED_CAPTCHA")
                self.assertEqual(row["last_safe_state"], "FORM_VALIDATED")

    def test_schema_files_are_valid_json(self) -> None:
        schemas = list((PROJECT / "schemas").glob("*.schema.json"))
        self.assertGreaterEqual(len(schemas), 19)
        for path in schemas:
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("$schema", value)
            self.assertEqual(value["type"], "object")


if __name__ == "__main__":
    unittest.main()
