from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import sha256_bytes


class OrchestratorForwardTests(unittest.TestCase):
    def build(self, temp):
        database = JobOpsDB(temp / "jobops.db")
        database.initialize()
        private_temp = Path(tempfile.mkdtemp(prefix="jobflow-private-test-"))
        self.addCleanup(shutil.rmtree, private_temp, True)
        store = WindowsDPAPIStore(
            PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1",
            local_app_data=private_temp,
        )
        onboarding = PrivateOnboarding(database, store)
        return database, onboarding, JobOpsOrchestrator(PROJECT, database, onboarding)

    def run_chain(self, orchestrator, refs, *, crash=None, form_name="synthetic-forward-form.json"):
        fixtures = PROJECT / "tests" / "fixtures"
        return orchestrator.run_to_awaiting(
            fixtures / "synthetic-forward-jd.txt", profile_ref=refs["profile_ref"], master_resume_ref=refs["master_resume_ref"],
            answer_bank_ref=refs["answer_bank_ref"], route_fixture=fixtures / "synthetic-forward-route.json",
            form_fixture=fixtures / form_name, research_fixture=fixtures / "synthetic-research.html",
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

    def test_explicit_local_approval_is_hash_bound_and_performs_no_external_action(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            refs = orchestrator.secure_onboard_synthetic()
            result = self.run_chain(orchestrator, refs)
            service = OnboardingCenterService(PROJECT, database, onboarding)
            displayed = service.review_packet(result["application_id"])
            payload = {
                "application_id": result["application_id"],
                "decision": "APPROVE",
                "expected_packet_hash": displayed["packet"]["content_hash"],
            }

            with self.assertRaises(JobOpsError) as unconfirmed:
                service.decide_review_packet({**payload, "user_confirmed": False})
            self.assertEqual(unconfirmed.exception.code, "EXPLICIT_CONFIRMATION_REQUIRED")
            with self.assertRaises(JobOpsError) as stale:
                service.decide_review_packet({
                    **payload, "expected_packet_hash": "sha256:" + "0" * 64,
                    "user_confirmed": True,
                })
            self.assertEqual(stale.exception.code, "REVIEW_PACKET_STALE")

            decision = service.decide_review_packet({**payload, "user_confirmed": True})

            self.assertEqual(decision["status"], "APPROVED")
            self.assertEqual(decision["phase5_authorization"], "PER_APPLICATION_USER_PRESENT_REQUIRED")
            self.assertEqual(decision["real_external_actions"], 0)
            with database.connect() as connection:
                application_status = connection.execute(
                    "SELECT status FROM applications WHERE application_id=?", (result["application_id"],)
                ).fetchone()[0]
                packet_status = connection.execute(
                    "SELECT status FROM review_packets WHERE application_id=?", (result["application_id"],)
                ).fetchone()[0]
                approval_count = connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
                attempts = connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0]
            self.assertEqual((application_status, packet_status), ("APPROVED", "APPROVED"))
            self.assertEqual(approval_count, 1)
            self.assertEqual(attempts, 0)

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

    def test_job_materials_derive_from_one_master_and_generate_only_requested_extras(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            refs = orchestrator.secure_onboard_synthetic()
            master_before = onboarding.read_bytes(refs["master_resume_ref"])

            result = self.run_chain(
                orchestrator, refs, form_name="synthetic-material-form.html",
            )

            master_after = onboarding.read_bytes(refs["master_resume_ref"])
            plan = result["material_plan"]
            self.assertEqual(master_after, master_before)
            self.assertEqual(plan["resume"]["master_secure_ref"], refs["master_resume_ref"])
            self.assertEqual(plan["resume"]["master_sha256"], sha256_bytes(master_before))
            self.assertEqual(plan["resume"]["derivation"], "TAILORED_COPY_OF_SINGLE_APPROVED_MASTER")
            self.assertEqual(plan["cover_letter"]["request_status"], "REQUESTED_REQUIRED")
            self.assertEqual(plan["cover_letter"]["generation_status"], "GENERATED_ON_DEMAND")
            self.assertEqual(result["cover_letter_qa"]["status"], "PASS")
            self.assertEqual(plan["portfolio_file"]["binding_status"], "BOUND_SECURE_FILE")
            self.assertEqual({item["kind"] for item in plan["public_links"]}, {"github", "portfolio"})
            self.assertTrue(all(item["binding_status"] == "BOUND_CONFIRMED_PUBLIC_VALUE" for item in plan["public_links"]))
            self.assertEqual(result["ats_safe_prefill"]["fields_discovered"], 7)
            self.assertEqual(result["ats_safe_prefill"]["fields_proposed"], 3)
            self.assertEqual(result["ats_safe_prefill"]["fields_stopped"], 4)
            with database.connect() as connection:
                kinds = [row[0] for row in connection.execute(
                    "SELECT kind FROM materials WHERE application_id=? ORDER BY kind",
                    (result["application_id"],),
                ).fetchall()]
                binding = json.loads(connection.execute(
                    "SELECT context_json FROM application_bindings WHERE application_id=?",
                    (result["application_id"],),
                ).fetchone()[0])
            self.assertEqual(len(kinds), 8)
            self.assertIn("execution_bundle", kinds)
            self.assertIn("cover_letter_docx", kinds)
            self.assertIn("cover_letter_pdf", kinds)
            self.assertIn("portfolio_file", kinds)
            self.assertEqual({item["purpose"] for item in binding["uploads"]}, {"resume", "cover_letter", "portfolio"})
            self.assertTrue(plan["all_uploads_and_submission_blocked"])
            self.assertEqual(plan["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
