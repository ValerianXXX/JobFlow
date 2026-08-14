from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.continuous_intake import ContinuousIntakeDescriptorStore, run_continuous_intake_tick
from jobops.document_builder import inspect_docx_text_blocks, template_fingerprint
from jobops.errors import JobOpsError
from jobops.external_claims import build_external_claim_set, claim_review_hash
from jobops.orchestrator import JobOpsOrchestrator
from jobops.onboarding_center import OnboardingCenterService
from jobops.private_onboarding import PrivateOnboarding
from jobops.queue_manager import QueueManager
from jobops.resume_tailoring import build_resume_tailoring_manifest, build_tailoring_proposal
from jobops.secure_store import WindowsDPAPIStore
from jobops.util import canonical_json, iso_utc, sha256_bytes, sha256_file


class RealProfileOfflineApplicationTests(unittest.TestCase):
    def build(self, temp: Path):
        database = JobOpsDB(temp / "jobops.db")
        database.initialize()
        private_temp = Path(tempfile.mkdtemp(prefix="jobflow-private-real-profile-test-"))
        self.addCleanup(shutil.rmtree, private_temp, True)
        onboarding = PrivateOnboarding(
            database,
            WindowsDPAPIStore(
                PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1",
                local_app_data=private_temp,
            ),
        )
        return database, onboarding, JobOpsOrchestrator(PROJECT, database, onboarding)

    def seed_completed_context(self, onboarding: PrivateOnboarding) -> dict[str, str]:
        fixtures = PROJECT / "tests" / "fixtures"
        portfolio = onboarding.import_file("onboarding_source_document", fixtures / "synthetic-forward-jd.pdf", synthetic=False)
        profile = json.loads((fixtures / "synthetic-forward-profile.json").read_text(encoding="utf-8"))
        profile.update({
            "github_url": "https://github.com/synthetic-candidate",
            "portfolio_url": "https://portfolio.example.test/synthetic-candidate",
            "portfolio_file_ref": portfolio["secure_ref"],
            "portfolio_file_sha256": portfolio["content_sha256"],
            "portfolio_file_display_name": "portfolio-fixture.pdf",
        })
        profile_record = onboarding.import_bytes("candidate_profile", canonical_json(profile), synthetic=False)
        profile_ref = str(profile_record["secure_ref"])
        profile["profile_ref"] = profile_ref
        onboarding.rotate(profile_ref, canonical_json(profile))

        answers = {
            "schema_version": 2, "status": "ONBOARDING_COMPLETE", "locale": "en", "answers": {},
            "completion": {"total": 25, "resolved": 25, "remaining": 0, "remaining_fields": [], "percent": 100},
            "updated_at": iso_utc(),
        }
        answer_record = onboarding.import_bytes("answer_bank", canonical_json(answers), synthetic=False)
        approval_record = onboarding.import_bytes("claim_approvals", canonical_json({"status": "ONBOARDING_COMPLETE"}), synthetic=False)
        master_source = Path(tempfile.mkdtemp(prefix="jobflow-real-master-source-"))
        self.addCleanup(shutil.rmtree, master_source, True)
        master_path = master_source / "reviewed-master.docx"
        document = Document()
        document.add_heading("Professional Summary", level=1)
        document.add_paragraph("Operations analyst focused on evidence-based application review.")
        document.add_heading("Projects", level=1)
        document.add_paragraph("Analyzed synthetic datasets with reproducible methods for application review.")
        document.add_heading("Education", level=1)
        document.add_paragraph("Synthetic University, Bachelor of Science.")
        document.save(master_path)
        master_record = onboarding.import_file("master_resume_docx", master_path, synthetic=False)
        master_ref = str(master_record["secure_ref"])
        master_hash = str(master_record["content_sha256"])
        fingerprint_hash = sha256_bytes(canonical_json(template_fingerprint(master_path).as_dict()))
        state_record = onboarding.import_bytes("onboarding_center_state", canonical_json({"status": "ONBOARDING_COMPLETE"}), synthetic=False)
        state_ref = str(state_record["secure_ref"])

        block = next(item for item in inspect_docx_text_blocks(master_path) if item["text"].startswith("Analyzed synthetic"))
        claim = {
            "claim_id": "CLM-REAL-OFFLINE-01", "category": "project", "claim_kind": "achievement",
            "statement": "Analyzes synthetic datasets with reproducible methods for reviewed applications.",
            "decision": "CONFIRMED", "deleted": False, "source_id": "SRC-MASTER-01",
            "provenance": {"line_start": block["line_number"], "line_end": block["line_number"]},
            "source_bindings": [{"kind": "MASTER_RESUME", "secure_ref": master_ref, "content_sha256": master_hash}],
        }
        master = {
            "secure_ref": master_ref, "sha256": master_hash, "editable_docx": True,
            "template_fingerprint": fingerprint_hash, "source_id": "SRC-MASTER-01",
        }
        external = build_external_claim_set(
            onboarding_state_ref=state_ref, profile_ref=profile_ref, master_resume=master,
            claims=[claim], allowed_uses=["resume", "cover_letter", "application_narrative"],
            expected_review_hash=claim_review_hash([claim], master_hash),
        )
        external_record = onboarding.import_bytes("external_claim_set", canonical_json(external), synthetic=False)
        proposal = build_tailoring_proposal(
            onboarding_state_ref=state_ref, master_resume=master,
            blocks=inspect_docx_text_blocks(master_path), claims=[claim],
        )
        candidate = proposal["candidates"][0]
        manifest = build_resume_tailoring_manifest(
            onboarding_state_ref=state_ref, master_resume=master, proposal=proposal,
            selections=[{"block_ref": candidate["block_ref"], "category": "project"}],
            expected_proposal_hash=proposal["proposal_hash"], user_confirmed=True,
        )
        manifest_record = onboarding.import_bytes("resume_tailoring_manifest", canonical_json(manifest), synthetic=False)
        completion = {
            "schema_version": 1, "status": "ONBOARDING_COMPLETE", "profile_ref": profile_ref,
            "answer_bank_ref": answer_record["secure_ref"], "claim_approvals_ref": approval_record["secure_ref"],
            "counts": {"answers_resolved": 25, "answers_total": 25, "claims_reviewed": 1, "claims_total": 1, "conflicts_resolved": 0, "conflicts_total": 0},
            "sources": {"resume_or_material": 1, "ai": 0, "direct_answers": 25},
            "locale": "en", "completed_at": iso_utc(), "real_external_actions": 0,
            "knowledge_write_operations": 0,
        }
        onboarding.import_bytes("onboarding_completion_packet", canonical_json(completion), synthetic=False)
        return {
            "profile_ref": profile_ref, "answer_bank_ref": str(answer_record["secure_ref"]),
            "master_resume_ref": master_ref, "external_claim_set_ref": str(external_record["secure_ref"]),
            "tailoring_manifest_ref": str(manifest_record["secure_ref"]),
            "tailoring_manifest_hash": str(manifest["content_hash"]),
        }

    def test_completed_user_context_generates_encrypted_review_materials_without_external_actions(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            refs = self.seed_completed_context(onboarding)
            fixtures = PROJECT / "tests" / "fixtures"
            route = json.loads((fixtures / "synthetic-forward-route.json").read_text(encoding="utf-8"))
            route["research"] = {
                "title": "Example Analytics Lab update", "url": "https://example.com/news/synthetic-update",
                "source_type": "official_company", "published_at": "2026-08-12T00:00:00Z",
                "accessed_at": iso_utc(), "official": True,
                "evidence_excerpt": "Example Analytics Lab uses documented checks for synthetic dataset analysis.",
            }
            route_path = temp / "saved-route.json"
            route_path.write_text(json.dumps(route), encoding="utf-8")
            master_before = onboarding.read_bytes(refs["master_resume_ref"])

            try:
                result = orchestrator.run_to_awaiting(
                    fixtures / "synthetic-forward-jd.txt",
                    profile_ref=None, master_resume_ref=None, answer_bank_ref=None,
                    route_fixture=route_path, form_fixture=fixtures / "synthetic-material-form.html",
                    research_fixture=fixtures / "synthetic-research.html", synthetic=False,
                )
            except JobOpsError as exc:
                details = exc.as_dict()
                if isinstance(details.get("stderr"), str):
                    details["stderr"] = details["stderr"][-800:]
                if isinstance(details.get("stdout"), str):
                    details["stdout"] = details["stdout"][-800:]
                self.fail(json.dumps(details, ensure_ascii=False))

            self.assertEqual(result["status"], "AWAITING_APPROVAL")
            self.assertEqual(result["document_qa"]["status"], "PASS")
            self.assertEqual(result["cover_letter_qa"]["status"], "PASS")
            self.assertEqual(result["real_external_actions"], 0)
            self.assertEqual(onboarding.read_bytes(refs["master_resume_ref"]), master_before)
            packet_meta = onboarding.reference_metadata(str(result["review_packet_ref"]))
            self.assertFalse(packet_meta["synthetic"])
            packet = json.loads(onboarding.read_bytes(str(result["review_packet_ref"])).decode("utf-8"))
            self.assertEqual(packet["master_resume_diff"]["manifest_content_hash"], refs["tailoring_manifest_hash"])
            self.assertTrue(packet["material_plan"]["all_uploads_and_submission_blocked"])
            self.assertFalse(packet["execution_plan"]["live_transport_registered"])
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0], 0)
                generated = connection.execute(
                    "SELECT COUNT(*) FROM private_refs WHERE kind LIKE 'generated_%' AND synthetic=0 AND status='ACTIVE'"
                ).fetchone()[0]
            self.assertGreaterEqual(generated, 4)
            self.assertEqual(list((onboarding.store.private_root / "staging").iterdir()), [])

    def test_local_ui_bundle_builds_route_and_review_packet_without_retaining_input_files(self) -> None:
        with project_temp() as temp:
            database, onboarding, _ = self.build(temp)
            self.seed_completed_context(onboarding)
            fixtures = PROJECT / "tests" / "fixtures"
            service = OnboardingCenterService(PROJECT, database, onboarding)
            result = service.prepare_offline_application_bundle(
                metadata={
                    "official_url": "https://example.com/careers/synthetic-data-analyst",
                    "application_url": "https://boards.greenhouse.io/example/jobs/987654",
                    "guest_available": True,
                    "research_title": "Synthetic company role page",
                    "evidence_excerpt": "Synthetic Data Analyst",
                },
                files={
                    "jd": (".txt", (fixtures / "synthetic-forward-jd.txt").read_bytes()),
                    "official": (".html", (fixtures / "synthetic-greenhouse-careers.html").read_bytes()),
                    "form": (".html", (fixtures / "synthetic-greenhouse-form.html").read_bytes()),
                },
            )
            self.assertEqual(result["status"], "AWAITING_APPROVAL")
            self.assertFalse(result["deferred_evidence_retained"])
            self.assertEqual(result["real_external_actions"], 0)
            self.assertEqual(result["network_actions"], 0)
            displayed = service.review_packet(str(result["application_id"]))
            self.assertEqual(displayed["packet"]["source_route"]["provider"], "greenhouse")
            with database.connect() as connection:
                kinds = {str(row[0]) for row in connection.execute("SELECT DISTINCT kind FROM private_refs")}
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0], 0)
            self.assertFalse(any(kind.startswith("application_input_") for kind in kinds))
            self.assertEqual(list((onboarding.store.private_root / "staging").iterdir()), [])

    def test_ui_deferred_bundle_is_encrypted_then_automatically_consumed_after_review(self) -> None:
        with project_temp() as temp:
            database, onboarding, _ = self.build(temp)
            self.seed_completed_context(onboarding)
            database.set_pending_limit(1)
            fixtures = PROJECT / "tests" / "fixtures"
            service = OnboardingCenterService(PROJECT, database, onboarding)
            metadata = {
                "official_url": "https://example.com/careers/synthetic-data-analyst",
                "application_url": "https://boards.greenhouse.io/example/jobs/987654",
                "guest_available": True,
                "research_title": "Synthetic company role page",
                "evidence_excerpt": "Synthetic Data Analyst",
            }

            def local_bundle(jd_name: str) -> dict[str, tuple[str, bytes]]:
                return {
                    "jd": (".txt", (fixtures / jd_name).read_bytes()),
                    "official": (".html", (fixtures / "synthetic-greenhouse-careers.html").read_bytes()),
                    "form": (".html", (fixtures / "synthetic-material-form.html").read_bytes()),
                }

            first = service.prepare_offline_application_bundle(
                metadata=metadata, files=local_bundle("synthetic-forward-jd.txt"),
            )
            second = service.prepare_offline_application_bundle(
                metadata=metadata, files=local_bundle("synthetic-forward-jd-two.txt"),
            )
            self.assertEqual(first["status"], "AWAITING_APPROVAL")
            self.assertEqual(second["status"], "DEFERRED")
            self.assertTrue(second["deferred_evidence_retained"])
            self.assertEqual(len(list((temp / "continuous-intake").glob("*.json"))), 1)
            with database.connect() as connection:
                active_before = connection.execute(
                    "SELECT COUNT(*) FROM private_refs WHERE kind='continuous_evidence_bundle' AND status='ACTIVE'"
                ).fetchone()[0]
            self.assertEqual(active_before, 1)

            application_id = str(first["application_id"])
            packet = service.review_packet(application_id)["packet"]
            decision = service.decide_review_packet({
                "application_id": application_id, "decision": "APPROVE",
                "expected_packet_hash": packet["content_hash"], "user_confirmed": True,
            })
            continued = decision["continued_intake"]
            self.assertEqual(continued["status"], "LOCAL_CONTINUATION_PROCESSED")
            self.assertEqual(continued["prepared_count"], 1)
            self.assertEqual(decision["queue"]["awaiting_approval"], 1)
            self.assertEqual(decision["queue"]["deferred_intake"], 0)
            self.assertEqual(list((temp / "continuous-intake").glob("*.json")), [])
            self.assertEqual(list((onboarding.store.private_root / "staging").iterdir()), [])
            with database.connect() as connection:
                evidence_rows = connection.execute(
                    "SELECT status FROM private_refs WHERE kind='continuous_evidence_bundle'"
                ).fetchall()
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0], 0)
            self.assertEqual([str(row["status"]) for row in evidence_rows], ["DELETED"])
            safe_output = json.dumps({"second": second, "decision": decision})
            self.assertNotIn("secure-ref:", safe_output)
            self.assertNotIn("synthetic-forward-jd-two", safe_output)

    def test_ui_deferred_retention_failure_rolls_back_the_queue_admission(self) -> None:
        with project_temp() as temp:
            database, onboarding, _ = self.build(temp)
            self.seed_completed_context(onboarding)
            database.set_pending_limit(1)
            fixtures = PROJECT / "tests" / "fixtures"
            service = OnboardingCenterService(PROJECT, database, onboarding)
            metadata = {
                "official_url": "https://example.com/careers/synthetic-data-analyst",
                "application_url": "https://boards.greenhouse.io/example/jobs/987654",
                "guest_available": True,
                "research_title": "Synthetic company role page",
                "evidence_excerpt": "Synthetic Data Analyst",
            }

            def local_bundle(jd_name: str) -> dict[str, tuple[str, bytes]]:
                return {
                    "jd": (".txt", (fixtures / jd_name).read_bytes()),
                    "official": (".html", (fixtures / "synthetic-greenhouse-careers.html").read_bytes()),
                    "form": (".html", (fixtures / "synthetic-material-form.html").read_bytes()),
                }

            first = service.prepare_offline_application_bundle(
                metadata=metadata, files=local_bundle("synthetic-forward-jd.txt"),
            )
            self.assertEqual(first["status"], "AWAITING_APPROVAL")
            original_import = onboarding.import_bytes

            def fail_deferred_import(kind: str, value: bytes, *, synthetic: bool = False):
                if kind == "continuous_evidence_bundle":
                    raise JobOpsError("LOCAL_RETENTION_FAILED", "Synthetic retention failure.")
                return original_import(kind, value, synthetic=synthetic)

            with mock.patch.object(onboarding, "import_bytes", side_effect=fail_deferred_import):
                with self.assertRaises(JobOpsError) as failed:
                    service.prepare_offline_application_bundle(
                        metadata=metadata, files=local_bundle("synthetic-forward-jd-two.txt"),
                    )
            self.assertEqual(failed.exception.code, "LOCAL_RETENTION_FAILED")
            status = QueueManager(database).status()
            self.assertEqual(status["awaiting_approval"], 1)
            self.assertEqual(status["deferred_intake"], 0)
            self.assertEqual(status["reserved_slots"], 0)
            self.assertEqual(list((temp / "continuous-intake").glob("*.json")), [])
            self.assertEqual(list((onboarding.store.private_root / "staging").iterdir()), [])
            with database.connect() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM private_refs WHERE kind='continuous_evidence_bundle' AND status='ACTIVE'"
                ).fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0], 0)

    def test_failed_preparation_releases_slot_and_deletes_new_encrypted_materials(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            self.seed_completed_context(onboarding)
            fixtures = PROJECT / "tests" / "fixtures"
            route = json.loads((fixtures / "synthetic-forward-route.json").read_text(encoding="utf-8"))
            route["research"] = {
                "title": "Example Analytics Lab update", "url": "https://example.com/news/synthetic-update",
                "source_type": "official_company", "published_at": "2026-08-12T00:00:00Z",
                "accessed_at": iso_utc(), "official": True,
                "evidence_excerpt": "Example Analytics Lab uses documented checks for synthetic dataset analysis.",
            }
            route_path = temp / "saved-route-failure.json"
            route_path.write_text(json.dumps(route), encoding="utf-8")
            original_import = onboarding.import_bytes

            def fail_after_two_generated(kind: str, value: bytes, *, synthetic: bool = False):
                if kind == "visual_evidence":
                    raise JobOpsError("LOCAL_TEST_FAILURE", "Stop after encrypted output for rollback testing.")
                return original_import(kind, value, synthetic=synthetic)

            with mock.patch.object(onboarding, "import_bytes", side_effect=fail_after_two_generated):
                with self.assertRaisesRegex(JobOpsError, "Stop after encrypted output"):
                    orchestrator.run_to_awaiting(
                        fixtures / "synthetic-forward-jd.txt",
                        profile_ref=None, master_resume_ref=None, answer_bank_ref=None,
                        route_fixture=route_path, form_fixture=fixtures / "synthetic-material-form.html",
                        research_fixture=fixtures / "synthetic-research.html", synthetic=False,
                    )
            with database.connect() as connection:
                active_generated = connection.execute(
                    "SELECT COUNT(*) FROM private_refs WHERE kind LIKE 'generated_%' AND synthetic=0 AND status='ACTIVE'"
                ).fetchone()[0]
                active_visual_or_packet = connection.execute(
                    "SELECT COUNT(*) FROM private_refs WHERE kind IN ('visual_evidence','review_packet') AND synthetic=0 AND status='ACTIVE'"
                ).fetchone()[0]
                reservation = connection.execute("SELECT status FROM queue_reservations ORDER BY created_at DESC LIMIT 1").fetchone()
            self.assertEqual(active_generated, 0)
            self.assertEqual(active_visual_or_packet, 0)
            self.assertEqual(str(reservation["status"]), "RELEASED")

    def test_manual_real_profile_tick_prepares_saved_local_evidence_and_is_idempotent(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            refs = self.seed_completed_context(onboarding)
            manager = QueueManager(database)
            manifest = {
                "schema_version": 1,
                "mode": "MANUAL_TICK_ONLY",
                "jobs": [{
                    "input": "tests/fixtures/synthetic-forward-jd.txt",
                    "profile_ref": refs["profile_ref"],
                    "master_resume_ref": refs["master_resume_ref"],
                    "answer_bank_ref": refs["answer_bank_ref"],
                    "external_claim_set_ref": refs["external_claim_set_ref"],
                    "tailoring_manifest_ref": refs["tailoring_manifest_ref"],
                    "route": "tests/fixtures/synthetic-real-offline-route.json",
                    "form": "tests/fixtures/synthetic-material-form.html",
                    "research": "tests/fixtures/synthetic-research.html",
                    "source_type": "txt",
                    "synthetic": False,
                }],
            }

            def prepare(item: dict) -> dict:
                return orchestrator.run_to_awaiting(
                    PROJECT / item["input"],
                    profile_ref=item["profile_ref"], master_resume_ref=item["master_resume_ref"],
                    answer_bank_ref=item["answer_bank_ref"],
                    external_claim_set_ref=item["external_claim_set_ref"],
                    tailoring_manifest_ref=item["tailoring_manifest_ref"],
                    route_fixture=PROJECT / item["route"], form_fixture=PROJECT / item["form"],
                    research_fixture=PROJECT / item["research"], source_type=item["source_type"],
                    synthetic=False,
                )

            first = run_continuous_intake_tick(manifest, queue_status=manager.status, prepare_job=prepare)
            self.assertEqual(first["status"], "MANUAL_TICK_COMPLETE")
            self.assertEqual(first["prepared_count"], 1)
            self.assertEqual(first["results"][0]["source_mode"], "SAVED_LOCAL_EVIDENCE")
            self.assertEqual(first["results"][0]["status"], "PREPARED")
            self.assertEqual(first["real_external_actions"], 0)
            safe_json = json.dumps(first)
            self.assertNotIn("secure-ref:", safe_json)
            self.assertNotIn("synthetic-forward-jd", safe_json)

            second = run_continuous_intake_tick(manifest, queue_status=manager.status, prepare_job=prepare)
            self.assertEqual(second["deduplicated_count"], 1)
            self.assertEqual(second["results"][0]["status"], "ALREADY_TRACKED")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0], 0)

    def test_review_decision_automatically_fills_freed_slot_from_recorded_local_batch(self) -> None:
        with project_temp() as temp:
            database, onboarding, orchestrator = self.build(temp)
            refs = self.seed_completed_context(onboarding)
            database.set_pending_limit(1)
            manager = QueueManager(database)
            store = ContinuousIntakeDescriptorStore(database, PROJECT / "schemas")

            def job(input_name: str) -> dict:
                return {
                    "input": f"tests/fixtures/{input_name}",
                    "profile_ref": refs["profile_ref"], "master_resume_ref": refs["master_resume_ref"],
                    "answer_bank_ref": refs["answer_bank_ref"],
                    "external_claim_set_ref": refs["external_claim_set_ref"],
                    "tailoring_manifest_ref": refs["tailoring_manifest_ref"],
                    "route": "tests/fixtures/synthetic-real-offline-route.json",
                    "form": "tests/fixtures/synthetic-material-form.html",
                    "research": "tests/fixtures/synthetic-research.html",
                    "source_type": "txt", "synthetic": False,
                }

            manifest = {
                "schema_version": 1, "mode": "MANUAL_TICK_ONLY",
                "jobs": [job("synthetic-forward-jd.txt"), job("synthetic-forward-jd-two.txt")],
            }

            def prepare(item: dict) -> dict:
                result = orchestrator.run_to_awaiting(
                    PROJECT / item["input"], profile_ref=item["profile_ref"],
                    master_resume_ref=item["master_resume_ref"], answer_bank_ref=item["answer_bank_ref"],
                    external_claim_set_ref=item["external_claim_set_ref"],
                    tailoring_manifest_ref=item["tailoring_manifest_ref"],
                    route_fixture=PROJECT / item["route"], form_fixture=PROJECT / item["form"],
                    research_fixture=PROJECT / item["research"], source_type="txt", synthetic=False,
                )
                if result.get("status") == "DEFERRED":
                    store.remember(str(result["intake_key"]), item)
                return result

            initial = run_continuous_intake_tick(manifest, queue_status=manager.status, prepare_job=prepare)
            self.assertEqual(initial["prepared_count"], 1)
            self.assertEqual(initial["deferred_count"], 1)
            first_application = str(initial["results"][0]["application_id"])
            service = OnboardingCenterService(PROJECT, database, onboarding)
            packet = service.review_packet(first_application)["packet"]
            decision = service.decide_review_packet({
                "application_id": first_application, "decision": "APPROVE",
                "expected_packet_hash": packet["content_hash"], "user_confirmed": True,
            })

            continued = decision["continued_intake"]
            self.assertEqual(continued["status"], "LOCAL_CONTINUATION_PROCESSED")
            self.assertEqual(continued["processed_count"], 1)
            self.assertEqual(continued["prepared_count"], 1)
            self.assertEqual(continued["results"][0]["source_mode"], "SAVED_LOCAL_EVIDENCE")
            self.assertEqual(decision["queue"]["awaiting_approval"], 1)
            self.assertEqual(decision["queue"]["deferred_intake"], 0)
            safe_json = json.dumps(continued)
            self.assertNotIn("secure-ref:", safe_json)
            self.assertNotIn("synthetic-forward-jd-two", safe_json)
            self.assertEqual(list((temp / "continuous-intake").glob("*.json")), [])
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
