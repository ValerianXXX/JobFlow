from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from docx import Document

from _support import PROJECT, project_temp
from jobops.db import JobOpsDB
from jobops.document_builder import inspect_docx_text_blocks, template_fingerprint
from jobops.errors import JobOpsError
from jobops.external_claims import build_external_claim_set, claim_review_hash
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
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


if __name__ == "__main__":
    unittest.main()
