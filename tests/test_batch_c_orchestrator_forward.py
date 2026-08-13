from __future__ import annotations

import unittest

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore


class OrchestratorForwardTests(unittest.TestCase):
    def build(self, temp):
        database = JobOpsDB(temp / "jobops.db")
        database.initialize()
        store = WindowsDPAPIStore(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1", local_app_data=temp / "local")
        onboarding = PrivateOnboarding(database, store)
        return database, onboarding, JobOpsOrchestrator(PROJECT, database, onboarding)

    def run_chain(self, orchestrator, refs, *, crash=None):
        fixtures = PROJECT / "tests" / "fixtures"
        return orchestrator.run_to_awaiting(
            fixtures / "synthetic-forward-jd.txt", profile_ref=refs["profile_ref"], master_resume_ref=refs["master_resume_ref"],
            answer_bank_ref=refs["answer_bank_ref"], route_fixture=fixtures / "synthetic-forward-route.json",
            form_fixture=fixtures / "synthetic-forward-form.json", research_fixture=fixtures / "synthetic-research.html",
            synthetic=True, crash_after_step=crash,
        )

    def test_complete_local_chain_persists_one_packet_and_no_plaintext_paths(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            refs = orchestrator.secure_onboard_synthetic()
            result = self.run_chain(orchestrator, refs)
            self.assertEqual(result["status"], "AWAITING_APPROVAL")
            self.assertEqual(result["document_qa"]["status"], "PASS")
            self.assertEqual(result["real_external_actions"], 0)
            counts = database.table_counts()
            self.assertEqual(counts["applications"], 1)
            self.assertEqual(counts["review_packets"], 1)
            self.assertEqual(counts["job_analyses"], 1)
            self.assertEqual(counts["research_findings"], 1)
            with database.connect() as connection:
                packet_path = connection.execute("SELECT relative_path FROM review_packets").fetchone()[0]
                snapshot_path = connection.execute("SELECT snapshot_path FROM jd_snapshots").fetchone()[0]
            self.assertTrue(str(packet_path).startswith("secure-ref:"))
            self.assertFalse(":" in str(snapshot_path)[:3])
            packet = onboarding.read_bytes(packet_path)
            self.assertIn(b'"status":"AWAITING_APPROVAL"', packet)
            self.assertEqual(onboarding.purge_synthetic()["synthetic_refs_deleted"], counts["private_refs"])

    def test_local_review_packet_is_decrypted_validated_and_bound_to_queue_record(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            refs = orchestrator.secure_onboard_synthetic()
            result = self.run_chain(orchestrator, refs)
            service = OnboardingCenterService(PROJECT, database, onboarding)

            displayed = service.review_packet(result["application_id"])

            self.assertEqual(displayed["application_id"], result["application_id"])
            self.assertEqual(displayed["packet_id"], result["review_packet_id"])
            self.assertEqual(displayed["packet"]["status"], "AWAITING_APPROVAL")
            self.assertEqual(displayed["private_transport"], "LOCAL_SESSION_ONLY")
            self.assertEqual(displayed["private_values_persisted_to_project"], 0)
            self.assertEqual(displayed["real_external_actions"], 0)

            with database.connect() as connection:
                stored_hash = connection.execute(
                    "SELECT content_hash FROM review_packets WHERE application_id=?",
                    (result["application_id"],),
                ).fetchone()[0]
                self.assertEqual(displayed["packet"]["content_hash"], stored_hash)
                connection.execute(
                    "UPDATE review_packets SET content_hash=? WHERE application_id=?",
                    ("sha256:" + "0" * 64, result["application_id"]),
                )
            with self.assertRaises(JobOpsError) as caught:
                service.review_packet(result["application_id"])
            self.assertEqual(caught.exception.code, "REVIEW_PACKET_HASH_INVALID")

    def test_crash_before_admission_restarts_without_duplicate_job_packet_or_material(self) -> None:
        with project_temp() as temp:
            database, _, orchestrator = self.build(temp)
            refs = orchestrator.secure_onboard_synthetic()
            with self.assertRaises(JobOpsError) as caught:
                self.run_chain(orchestrator, refs, crash="before_admission")
            self.assertEqual(caught.exception.code, "SYNTHETIC_CRASH_INJECTED")
            result = self.run_chain(orchestrator, refs)
            self.assertEqual(result["status"], "AWAITING_APPROVAL")
            counts = database.table_counts()
            self.assertEqual(counts["jobs"], 1)
            self.assertEqual(counts["applications"], 1)
            self.assertEqual(counts["review_packets"], 1)
            self.assertEqual(counts["materials"], 3)
            repeated = self.run_chain(orchestrator, refs)
            self.assertTrue(repeated["deduplicated"])


if __name__ == "__main__":
    unittest.main()
