from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from dataclasses import replace

from _support import PROJECT, project_temp
from jobops.approvals import ApprovalContext, UploadBinding
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.queue_manager import QueueManager
from jobops.util import sha256_bytes


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


def binding(index: int) -> ApprovalContext:
    token = f"{index:012X}"
    return ApprovalContext(
        application_id=f"APP-{token}", job_id=f"JOB-{token}", jd_snapshot_hash=sha256_bytes(str(index).encode()),
        jd_freshness_hash=HASH_B, source_route_hash=HASH_C,
        canonical_url=f"https://tenant.example.test/job/{index}", ats_tenant="tenant",
        ats_board="careers", ats_job_identity=str(index), profile_version="PROFILE-1",
        claim_set_hash=HASH_A, form_snapshot_hash=HASH_B, answers_hash=HASH_C,
        review_packet_hash=HASH_A, uploads=(UploadBinding(f"resume-{index}.pdf", "resume", HASH_B),),
        external_actions=("upload_material", "submit_application"), site_policy_version="SITE-POLICY-1",
    )


class AtomicQueueTests(unittest.TestCase):
    def test_review_packet_hash_must_match_current_approval_context(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            manager = QueueManager(database)
            reservation = manager.enqueue("PACKET-MISMATCH", source_type="txt", source_locator="fixtures/mismatch.txt")
            with self.assertRaises(JobOpsError) as blocked:
                manager.admit_awaiting(
                    reservation.reservation_id, binding(99), snapshot_relative_path="workspace/jobs/mismatch/jd.txt",
                    review_packet={
                        "packet_id": "RPK-MISMATCH", "content_hash": HASH_B,
                        "secure_ref": "secure-ref:SYNTHETIC_PACKET_MISMATCH",
                        "status": "AWAITING_APPROVAL",
                    },
                )
            self.assertEqual(blocked.exception.code, "REVIEW_PACKET_CONTEXT_MISMATCH")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_packets").fetchone()[0], 0)

    def test_twelve_jobs_limit_three_release_promotes_exactly_one(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            database.set_pending_limit(3)
            manager = QueueManager(database)
            admissions = []
            for index in range(12):
                decision = manager.enqueue(f"INTAKE-{index:02d}", source_type="txt", source_locator=f"fixtures/jd-{index}.txt")
                admissions.append(decision)
                if decision.status == "RESERVED":
                    manager.admit_awaiting(
                        decision.reservation_id, binding(index),
                        snapshot_relative_path=f"workspace/jobs/{index}/jd.txt",
                        review_packet={
                            "packet_id": f"RPK-{index:012X}", "content_hash": HASH_A,
                            "secure_ref": f"secure-ref:SYNTHETIC_PACKET_{index:04d}",
                            "status": "AWAITING_APPROVAL",
                        },
                    )
            self.assertEqual(sum(item.status == "RESERVED" for item in admissions), 3)
            status = manager.status()
            self.assertEqual(status["awaiting_approval"], 3)
            self.assertEqual(status["reserved_slots"], 0)
            self.assertEqual(status["deferred_intake"], 9)
            self.assertLessEqual(status["awaiting_approval"] + status["reserved_slots"], 3)

            manager.release_application(binding(0).application_id, reason="USER_REJECTED")
            promoted = manager.promote_next_deferred()
            self.assertEqual(promoted.status, "RESERVED")
            next_index = int(promoted.intake_key.split("-")[-1])
            manager.admit_awaiting(promoted.reservation_id, binding(next_index), snapshot_relative_path=f"workspace/jobs/{next_index}/jd.txt")
            status = manager.status()
            self.assertEqual(status["awaiting_approval"], 3)
            self.assertEqual(status["deferred_intake"], 8)
            self.assertEqual(status["closed_applications"], 1)
            with database.connect() as connection:
                rejected_packet = connection.execute(
                    "SELECT status FROM review_packets WHERE application_id=?", (binding(0).application_id,)
                ).fetchone()[0]
            self.assertEqual(rejected_packet, "REJECTED")

    def test_duplicate_intake_is_idempotent(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            manager = QueueManager(database)
            first = manager.enqueue("SAME-HASH", source_type="txt", source_locator="fixtures/a.txt")
            second = manager.enqueue("SAME-HASH", source_type="txt", source_locator="fixtures/a.txt")
            self.assertEqual(first.reservation_id, second.reservation_id)
            self.assertEqual(second.status, "RESERVED")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM intake_queue").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM queue_reservations").fetchone()[0], 1)

    def test_two_workers_cannot_take_the_last_slot(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            database.set_pending_limit(1)
            manager = QueueManager(database)
            barrier = threading.Barrier(3)
            results = []
            errors = []

            def worker(key: str) -> None:
                try:
                    barrier.wait()
                    results.append(manager.enqueue(key, source_type="txt", source_locator=f"fixtures/{key}.txt"))
                except Exception as exc:  # pragma: no cover - recorded for assertion
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(f"RACE-{index}",)) for index in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(10)
            self.assertEqual(errors, [])
            self.assertEqual(sorted(item.status for item in results), ["DEFERRED", "RESERVED"])
            status = manager.status()
            self.assertLessEqual(status["awaiting_approval"] + status["reserved_slots"], 1)

    def test_explicit_revision_can_reenter_without_duplicate_application(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            manager = QueueManager(database)
            original = binding(7)
            intake_key = "REVISION-HASH"
            first = manager.enqueue(intake_key, source_type="txt", source_locator="fixtures/revision.txt")
            manager.admit_awaiting(
                first.reservation_id, original, snapshot_relative_path="workspace/jobs/revision/jd.txt",
                review_packet={
                    "packet_id": "RPK-REVISION-0001", "content_hash": HASH_A,
                    "secure_ref": "secure-ref:SYNTHETIC_PACKET_REVISION_0001",
                    "status": "AWAITING_APPROVAL",
                },
            )
            revision = manager.request_revision(original.application_id, reason="SYNTHETIC_TEST_REVISION")
            self.assertEqual(revision["status"], "MATERIALS_NEEDS_CORRECTION")
            self.assertTrue(revision["capacity_released"])
            accepted = manager.enqueue(intake_key, source_type="txt", source_locator="fixtures/revision.txt")
            self.assertEqual(accepted.status, "ACCEPTED")
            reopened = manager.reserve_reprocess(intake_key, original.application_id)
            changed = replace(
                original,
                uploads=(UploadBinding("resume-7-revised.pdf", "resume", HASH_C),),
                review_packet_hash=HASH_B,
            )
            manager.admit_awaiting(
                reopened.reservation_id, changed, snapshot_relative_path="workspace/jobs/revision/jd.txt",
                review_packet={
                    "packet_id": "RPK-REVISION-0002", "content_hash": HASH_B,
                    "secure_ref": "secure-ref:SYNTHETIC_PACKET_REVISION_0002",
                    "status": "AWAITING_APPROVAL",
                },
            )
            with database.connect() as connection:
                application = connection.execute(
                    "SELECT status,resume_hash FROM applications WHERE application_id=?", (original.application_id,)
                ).fetchone()
                stored_hash = connection.execute(
                    "SELECT context_hash FROM application_bindings WHERE application_id=?", (original.application_id,)
                ).fetchone()[0]
                count = connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
                packets = connection.execute(
                    """SELECT packet_id,status,packet_version,supersedes_packet_id
                    FROM review_packets WHERE application_id=? ORDER BY packet_version""",
                    (original.application_id,),
                ).fetchall()
            self.assertEqual(dict(application), {"status": "AWAITING_APPROVAL", "resume_hash": HASH_C})
            self.assertEqual(stored_hash, changed.context_hash)
            self.assertEqual(count, 1)
            self.assertEqual(
                [dict(item) for item in packets],
                [
                    {
                        "packet_id": "RPK-REVISION-0001", "status": "NEEDS_REVISION",
                        "packet_version": 1, "supersedes_packet_id": None,
                    },
                    {
                        "packet_id": "RPK-REVISION-0002", "status": "AWAITING_APPROVAL",
                        "packet_version": 2, "supersedes_packet_id": "RPK-REVISION-0001",
                    },
                ],
            )


class PublicCLIContractTests(unittest.TestCase):
    def run_cli(self, *args: str):
        command = [sys.executable, str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"), *args]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        return completed, json.loads(completed.stdout)

    def test_status_and_migrate_db_are_structured_and_path_safe(self) -> None:
        with project_temp() as temp:
            relative = temp.relative_to(PROJECT) / "cli.db"
            migrated, migration = self.run_cli("migrate-db", "--path", relative.as_posix())
            self.assertEqual(migrated.returncode, 0)
            self.assertEqual(migration["status"], "MIGRATED")
            self.assertEqual(migration["database"], "$PROJECT_ROOT/" + relative.as_posix())
            self.assertIn("next_safe_action", migration)
            status_run, status = self.run_cli("status", "--path", relative.as_posix())
            self.assertEqual(status_run.returncode, 0)
            self.assertEqual(status["schema_version"], JobOpsDB.LATEST_SCHEMA_VERSION)
            self.assertIn("next_safe_action", status)

    def test_cli_exposes_required_operator_commands(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"), "--help"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        for command in (
            "status", "audit", "locate", "init-db", "migrate-db", "secure-onboard",
            "secure-onboard-resume", "finalize-resume-onboarding", "review-onboarding",
            "onboarding-center", "onboarding-status",
            "secure-import-master-resume", "secure-import-answer-bank", "secure-store-status",
            "purge-synthetic-private-data", "propose-claims", "list-claim-proposals", "approve-claim",
            "reject-claim", "revoke-claim", "revalidate-claims", "import-jd", "analyze-job",
            "run-to-awaiting-approval", "run-queue", "list-pending", "show-review-packet",
            "revise-application", "approve-review-packet", "reject-review-packet", "resume-blocked",
            "retry-safe-step", "explain", "verify-release",
            "discover-official-jobs",
            "analyze-ats-form",
            "analyze-ats-sequence",
            "ats-capabilities",
            "plan-continuous-intake",
        ):
            self.assertIn(command, completed.stdout)


if __name__ == "__main__":
    unittest.main()
