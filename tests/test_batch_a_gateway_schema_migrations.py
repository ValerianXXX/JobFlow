from __future__ import annotations

import json
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from _support import PROJECT, project_temp
from jobops.approvals import ApprovalContext, UploadBinding, issue_approval, validate_approval
from jobops.db import JobOpsDB, MIGRATION_001_SQL, MIGRATION_003_SQL
from jobops.errors import JobOpsError
from jobops.external_actions import ExternalActionGateway, ExternalActionPolicy
from jobops.runtime_schema import validate_named
from jobops.util import iso_utc


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def context(*, actions: tuple[str, ...] = ("upload_material", "submit_application")) -> ApprovalContext:
    return ApprovalContext(
        application_id="APP-ABCDEF123456",
        job_id="JOB-ABCDEF123456",
        jd_snapshot_hash=HASH_A,
        jd_freshness_hash=HASH_B,
        source_route_hash=HASH_C,
        canonical_url="https://example.wd5.myworkdayjobs.com/job/123",
        ats_tenant="example",
        ats_board="careers",
        ats_job_identity="123",
        profile_version="PROFILE-0001",
        claim_set_hash=HASH_A,
        form_snapshot_hash=HASH_B,
        answers_hash=HASH_C,
        review_packet_hash=HASH_A,
        uploads=(UploadBinding("resume.pdf", "resume", HASH_B),),
        external_actions=actions,
        site_policy_version="SITE-POLICY-1",
    )


def seed_awaiting(database: JobOpsDB, binding: ApprovalContext) -> None:
    now = iso_utc()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO jobs(job_id,source_type,source_locator,official_url,company,title,location,status,discovered_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (binding.job_id, "manual", "fixture", "https://example.com/careers/123", "Example", "Analyst", None, "FORM_VALIDATED", now, now),
        )
        connection.execute(
            """INSERT INTO applications(application_id,job_id,site,status,resume_hash,answers_hash,dry_run,secure_profile_ref,last_safe_state,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                binding.application_id,
                binding.job_id,
                binding.canonical_url,
                "AWAITING_APPROVAL",
                binding.uploads[0].sha256,
                binding.answers_hash,
                1,
                "secure-ref:SYNTHETIC_PROFILE_001",
                "AWAITING_APPROVAL",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO application_bindings(application_id,context_hash,context_json,updated_at) VALUES(?,?,?,?)",
            (binding.application_id, binding.context_hash, json.dumps(binding.as_dict(), sort_keys=True), now),
        )


class GatewayAndApprovalTests(unittest.TestCase):
    def test_raw_database_transition_cannot_enter_protected_states(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            seed_awaiting(database, context())
            with self.assertRaises(JobOpsError) as caught:
                database.transition_application("APP-ABCDEF123456", "APPROVED")
            self.assertEqual(caught.exception.code, "EXTERNAL_GATEWAY_REQUIRED")

    def test_approval_binds_actions_uploads_and_every_context_hash(self) -> None:
        binding = context()
        approval = issue_approval(context=binding, user_confirmed=True)
        self.assertEqual(validate_approval(approval, context=binding, required_actions=("submit_application",)), "APPROVAL_VALID")
        self.assertEqual(
            validate_approval(approval, context=replace(binding, external_actions=("upload_material",)), required_actions=("submit_application",)),
            "APPROVAL_INVALIDATED",
        )
        changed_upload = replace(binding, uploads=(UploadBinding("resume.pdf", "resume", HASH_C),))
        self.assertEqual(validate_approval(approval, context=changed_upload), "APPROVAL_INVALIDATED")

    def test_production_gateway_fails_closed_before_submit(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            binding = context()
            seed_awaiting(database, binding)
            approval = issue_approval(context=binding, user_confirmed=True)
            gateway = ExternalActionGateway(database, ExternalActionPolicy.production_disabled())
            gateway.persist_approval(approval, binding)
            with self.assertRaises(JobOpsError) as caught:
                gateway.begin_submission(binding)
            self.assertEqual(caught.exception.code, "PHASE_NOT_AUTHORIZED")
            with database.connect() as connection:
                row = connection.execute("SELECT status FROM applications WHERE application_id=?", (binding.application_id,)).fetchone()
                attempts = connection.execute("SELECT result_code FROM external_action_attempts").fetchall()
            self.assertEqual(row[0], "APPROVED")
            self.assertEqual([item[0] for item in attempts], ["PHASE_NOT_AUTHORIZED"])

    def test_fake_isolated_submission_consumes_approval_once_atomically(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            binding = context()
            seed_awaiting(database, binding)
            approval = issue_approval(context=binding, user_confirmed=True)
            gateway = ExternalActionGateway(database, ExternalActionPolicy.isolated_fake())
            gateway.persist_approval(approval, binding)
            result = gateway.begin_submission(binding)
            self.assertEqual(result["status"], "SUBMITTING")
            with self.assertRaises(JobOpsError) as caught:
                gateway.begin_submission(binding)
            self.assertEqual(caught.exception.code, "APPROVAL_REPLAYED")
            with database.connect() as connection:
                application = connection.execute("SELECT status FROM applications WHERE application_id=?", (binding.application_id,)).fetchone()[0]
                approval_status = connection.execute("SELECT status FROM approvals WHERE approval_id=?", (approval.approval_id,)).fetchone()[0]
                event = connection.execute("SELECT from_state,to_state FROM events WHERE to_state='SUBMITTING'").fetchone()
            self.assertEqual(application, "SUBMITTING")
            self.assertEqual(approval_status, "CONSUMED")
            self.assertEqual(tuple(event), ("APPROVED", "SUBMITTING"))


class RuntimeSchemaAndMigrationTests(unittest.TestCase):
    def test_approval_schema_rejects_extra_fields_and_bad_time_order(self) -> None:
        now = datetime.now(timezone.utc)
        value = issue_approval(context=context(), user_confirmed=True, now=now).as_dict()
        validate_named("approval", value, PROJECT / "schemas")
        value["unexpected"] = True
        with self.assertRaises(JobOpsError) as caught:
            validate_named("approval", value, PROJECT / "schemas")
        self.assertEqual(caught.exception.code, "SCHEMA_VALIDATION_FAILED")
        value.pop("unexpected")
        value["expires_at"] = iso_utc(now - timedelta(seconds=1))
        with self.assertRaises(JobOpsError) as caught:
            validate_named("approval", value, PROJECT / "schemas")
        self.assertEqual(caught.exception.code, "SCHEMA_SEMANTIC_CONFLICT")

    def test_versioned_migration_preserves_v1_rows_and_dry_run_constraint(self) -> None:
        with project_temp() as temp:
            path = temp / "legacy.db"
            connection = sqlite3.connect(path)
            connection.executescript(MIGRATION_001_SQL)
            now = iso_utc()
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','1')")
            connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", ("JOB-LEGACY", "manual", "fixture", None, "Example", "Analyst", None, "DISCOVERED", now, now))
            connection.execute("INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)", ("APP-LEGACY", "JOB-LEGACY", "example.test", "DISCOVERED", None, None, 1, None, "DISCOVERED", now))
            connection.commit()
            connection.close()

            database = JobOpsDB(path)
            applied = database.migrate()
            self.assertIn(2, applied)
            with database.connect() as current:
                self.assertEqual(current.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0], str(database.LATEST_SCHEMA_VERSION))
                self.assertEqual(current.execute("SELECT COUNT(*) FROM applications WHERE application_id='APP-LEGACY'").fetchone()[0], 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    current.execute("INSERT INTO applications(application_id,job_id,site,status,dry_run,last_safe_state,updated_at) VALUES(?,?,?,?,?,?,?)", ("APP-UNSAFE", "JOB-LEGACY", "example.test", "DISCOVERED", 0, "DISCOVERED", now))

    def test_v4_migration_preserves_packet_and_allows_versioned_history_only(self) -> None:
        with project_temp() as temp:
            path = temp / "review-history-v3.db"
            database = JobOpsDB(path)
            now = iso_utc()
            with database.connect() as connection:
                connection.executescript(MIGRATION_001_SQL)
                connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','1')")
                database._migrate_1_to_2(connection)
                connection.executescript(MIGRATION_003_SQL)
                connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version','3')")
                connection.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("JOB-PACKET", "synthetic", "fixture", None, "Example", "Analyst", None, "FORM_VALIDATED", now, now),
                )
                connection.execute(
                    "INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("APP-PACKET", "JOB-PACKET", "example.test", "AWAITING_APPROVAL", HASH_A, HASH_B, 1, None, "AWAITING_APPROVAL", now),
                )
                connection.execute(
                    "INSERT INTO review_packets(packet_id,application_id,content_hash,relative_path,status,created_at) VALUES(?,?,?,?,?,?)",
                    ("RPK-PACKET-1", "APP-PACKET", HASH_A, "secure-ref:SYNTHETIC_PACKET_1", "AWAITING_APPROVAL", now),
                )

            self.assertEqual(database.migrate(), [4])
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT packet_id,packet_version,supersedes_packet_id,status FROM review_packets"
                ).fetchone()
                self.assertEqual(dict(row), {
                    "packet_id": "RPK-PACKET-1", "packet_version": 1,
                    "supersedes_packet_id": None, "status": "AWAITING_APPROVAL",
                })
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT INTO review_packets(
                        packet_id,application_id,content_hash,relative_path,status,packet_version,created_at
                        ) VALUES(?,?,?,?,?,?,?)""",
                        ("RPK-PACKET-2", "APP-PACKET", HASH_B, "secure-ref:SYNTHETIC_PACKET_2", "AWAITING_APPROVAL", 2, now),
                    )


if __name__ == "__main__":
    unittest.main()
