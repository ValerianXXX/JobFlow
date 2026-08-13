from __future__ import annotations

import copy
import unittest

from _support import PROJECT, project_temp
from jobops.approvals import ApprovalContext, UploadBinding
from jobops.continuous_intake import build_continuous_intake_plan, validate_continuous_manifest
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.queue_manager import QueueManager
from jobops.util import load_json, sha256_bytes


H = "sha256:" + "a" * 64


def context(index: int) -> ApprovalContext:
    token = f"{index:012X}"
    return ApprovalContext(
        application_id=f"APP-{token}", job_id=f"JOB-{token}", jd_snapshot_hash=sha256_bytes(f"jd-{index}".encode()),
        jd_freshness_hash=H, source_route_hash=H, canonical_url=f"https://example.com/careers/{index}",
        ats_tenant="example", ats_board="official", ats_job_identity=str(index), profile_version="1",
        claim_set_hash=H, form_snapshot_hash=H, answers_hash=H, review_packet_hash=H,
        uploads=(UploadBinding(f"resume-{index}.pdf", "resume", H),),
        external_actions=("upload_material", "submit_application"), site_policy_version="1",
    )


class ContinuousIntakeTests(unittest.TestCase):
    def test_manual_plan_accounts_for_capacity_without_starting_a_scheduler(self) -> None:
        manifest = load_json(PROJECT / "tests" / "fixtures" / "synthetic-continuous-manifest.json")
        manifest["jobs"] = [
            {**manifest["jobs"][0], "input": f"tests/fixtures/job-{index}.txt"}
            for index in range(12)
        ]
        plan = build_continuous_intake_plan(
            manifest,
            {"pending_limit": 5, "awaiting_approval": 2, "reserved_slots": 1, "deferred_intake": 4, "slots_available": 2},
        )
        self.assertEqual(plan["status"], "MANUAL_TICK_READY")
        self.assertEqual(plan["job_count"], 12)
        self.assertEqual(plan["jobs_eligible_this_tick"], 2)
        self.assertEqual(plan["jobs_expected_to_defer"], 10)
        self.assertTrue(plan["requires_explicit_invocation"])
        self.assertFalse(plan["background_service_started"])
        self.assertEqual(plan["system_tasks_registered"], 0)
        self.assertEqual(plan["browser_actions"], 0)
        self.assertEqual(plan["network_actions"], 0)
        self.assertEqual(plan["real_external_actions"], 0)

        paused = build_continuous_intake_plan(
            manifest,
            {"pending_limit": 3, "awaiting_approval": 3, "reserved_slots": 0, "deferred_intake": 9, "slots_available": 0},
        )
        self.assertEqual(paused["status"], "PAUSED_AT_PENDING_LIMIT")
        self.assertEqual(paused["jobs_eligible_this_tick"], 0)

    def test_manifest_rejects_private_values_extra_fields_duplicates_and_real_mode(self) -> None:
        manifest = load_json(PROJECT / "tests" / "fixtures" / "synthetic-continuous-manifest.json")
        leaked = copy.deepcopy(manifest); leaked["jobs"][0]["email"] = "FORBIDDEN_PRIVATE_FIELD"
        with self.assertRaises(JobOpsError) as extra:
            validate_continuous_manifest(leaked)
        self.assertEqual(extra.exception.code, "CONTINUOUS_JOB_INVALID")
        plaintext = copy.deepcopy(manifest); plaintext["jobs"][0]["profile_ref"] = "Plain Person"
        with self.assertRaises(Exception):
            validate_continuous_manifest(plaintext)
        real = copy.deepcopy(manifest); real["jobs"][0]["synthetic"] = False
        with self.assertRaises(JobOpsError) as real_error:
            validate_continuous_manifest(real)
        self.assertEqual(real_error.exception.code, "CONTINUOUS_REAL_INTAKE_NOT_AUTHORIZED")
        duplicate = copy.deepcopy(manifest); duplicate["jobs"].append(copy.deepcopy(duplicate["jobs"][0]))
        with self.assertRaises(JobOpsError) as duplicate_error:
            validate_continuous_manifest(duplicate)
        self.assertEqual(duplicate_error.exception.code, "CONTINUOUS_JOB_DUPLICATE")

        for unsafe_path in ("../outside.txt", "jobs/../../outside.txt", "resume.txt:secret", "C:\\outside.txt", "\\\\server\\share\\job.txt"):
            unsafe = copy.deepcopy(manifest)
            unsafe["jobs"][0]["input"] = unsafe_path
            with self.subTest(path=unsafe_path), self.assertRaises(JobOpsError) as path_error:
                validate_continuous_manifest(unsafe)
            self.assertEqual(path_error.exception.code, "CONTINUOUS_JOB_INVALID")

    def test_released_capacity_promotes_oldest_deferred_before_new_intake(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            database.set_pending_limit(3)
            manager = QueueManager(database)
            admissions = [manager.enqueue(f"INTAKE-{index}", source_type="txt", source_locator=f"job-{index}.txt") for index in range(5)]
            self.assertEqual([item.status for item in admissions], ["RESERVED", "RESERVED", "RESERVED", "DEFERRED", "DEFERRED"])
            for index in range(3):
                manager.admit_awaiting(admissions[index].reservation_id, context(index), snapshot_relative_path=f"workspace/jobs/{index}.txt")
            manager.release_application(context(0).application_id, reason="SYNTHETIC_REVIEW")
            manager.release_application(context(1).application_id, reason="SYNTHETIC_REVIEW")

            closed_retry = manager.enqueue("INTAKE-0", source_type="txt", source_locator="job-0.txt")
            self.assertEqual(closed_retry.status, "CLOSED")
            self.assertIsNone(closed_retry.reservation_id)
            self.assertEqual(closed_retry.next_safe_action, "CREATE_NEW_INTAKE_ID")

            newcomer = manager.enqueue("INTAKE-5", source_type="txt", source_locator="job-5.txt")
            self.assertEqual(newcomer.status, "DEFERRED")
            self.assertEqual(newcomer.next_safe_action, "WAIT_FOR_OLDER_DEFERRED_INTAKE")
            promoted = manager.promote_available()
            self.assertEqual([item.intake_key for item in promoted], ["INTAKE-3", "INTAKE-4"])
            status = manager.status()
            self.assertEqual(status["awaiting_approval"], 1)
            self.assertEqual(status["reserved_slots"], 2)
            self.assertEqual(status["deferred_intake"], 1)
            self.assertEqual(status["slots_available"], 0)
            with database.connect() as connection:
                deferred = [row[0] for row in connection.execute("SELECT intake_key FROM intake_queue WHERE status='DEFERRED'")]
            self.assertEqual(deferred, ["INTAKE-5"])

    def test_deferred_reprocess_invalidates_old_approval_immediately(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            database.set_pending_limit(1)
            now = "2026-08-13T00:00:00Z"
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("JOB-BLOCKER", "synthetic", "blocker", None, "Synthetic", "Blocker", None, "FORM_VALIDATED", now, now),
                )
                connection.execute(
                    "INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("APP-BLOCKER", "JOB-BLOCKER", "example.test", "AWAITING_APPROVAL", H, H, 1, None, "AWAITING_APPROVAL", now),
                )
                connection.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("JOB-REPROCESS", "synthetic", "reprocess", None, "Synthetic", "Changed", None, "SITE_CHANGED", now, now),
                )
                connection.execute(
                    "INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("APP-REPROCESS", "JOB-REPROCESS", "example.test", "SITE_CHANGED", H, H, 1, None, "APPROVED", now),
                )
                connection.execute(
                    "INSERT INTO intake_queue VALUES(?,?,?,?,?,?,?)",
                    ("INTAKE-REPROCESS", "txt", "synthetic.txt", "ACCEPTED", None, now, now),
                )
                connection.execute(
                    """INSERT INTO approvals(
                    approval_id,application_id,job_id,site,resume_hash,answers_hash,bound_at,expires_at,status,external_actions_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    ("APR-OLD", "APP-REPROCESS", "JOB-REPROCESS", "example.test", H, H, now, "2026-08-14T00:00:00Z", "APPROVED", "[]"),
                )

            admission = QueueManager(database).reserve_reprocess("INTAKE-REPROCESS", "APP-REPROCESS")

            self.assertEqual(admission.status, "DEFERRED")
            self.assertEqual(admission.next_safe_action, "WAIT_FOR_APPROVAL_SLOT")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT status FROM approvals WHERE approval_id='APR-OLD'").fetchone()[0], "INVALIDATED")
                event = connection.execute(
                    "SELECT event_type,payload_json FROM events WHERE application_id='APP-REPROCESS' ORDER BY event_id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(event["event_type"], "REPROCESS_DEFERRED")
                self.assertIn("PENDING_APPROVAL_LIMIT", event["payload_json"])


if __name__ == "__main__":
    unittest.main()
