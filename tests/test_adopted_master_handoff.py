"""Application admission after real service transitions (synthetic store/model).

Native Windows/browser/PDF acceptance is recorded by the separate continuous
journey. These contract tests never assign a completed state or approval to make
the successful setup pass; mutations below are deliberate rejection probes.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import _support
from _support import PROJECT
from pdf_regions_ui_server import RecordedSyntheticEngine
import test_onboarding_center as onboarding_fixtures
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.orchestrator import JobOpsOrchestrator
from jobops.private_onboarding import PrivateOnboarding
from jobops.util import canonical_json, sha256_bytes


class AdoptedMasterHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jobflow-adopted-master-test-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        project = root / "project"
        project.mkdir()
        shutil.copy2(PROJECT / ".jobops-root", project / ".jobops-root")
        shutil.copytree(PROJECT / "schemas", project / "schemas")
        (project / "config").mkdir()
        shutil.copy2(PROJECT / "config/policy.json", project / "config/policy.json")
        database = JobOpsDB(project / "state/jobops.db")
        database.initialize()
        self.onboarding = PrivateOnboarding(database, onboarding_fixtures.MemorySecureStore(root / "private"))
        self.service = OnboardingCenterService(project, database, self.onboarding, ai_engine=RecordedSyntheticEngine())
        self.addCleanup(self.service.close)
        self.orchestrator = JobOpsOrchestrator(project, database, self.onboarding)
        self.assertEqual(len(self.service.bootstrap()["claims"]), 0)
        with patch.dict("os.environ", {"JOBFLOW_DOCUMENT_PARSER": "pdf_regions", "JOBFLOW_VISUAL_ENABLE": "false"}):
            preview = self.service.preview_source("resume", ".pdf", (PROJECT / "tests/fixtures/material-layout-probes-v1/zh-columns.pdf").read_bytes())
        pending = self.service.bootstrap()["pending_sources"][0]
        self.service.commit_source(preview["source_id"], [
            {"candidate_id": item["candidate_id"], "selected": True, "visual_source_verified": True}
            for item in pending["candidates"]
        ])
        onboarding_fixtures.OnboardingCenterTests.prepare_completion(self.service)
        self.service.complete(user_confirmed=True)
        self.approve_materials()
        old_ref, _ = self.service.ensure_state()
        self.old_ref, self.old_bytes = old_ref, self.onboarding.read_bytes(old_ref)
        self.draft = self.service.create_standard_resume({"expected_review_hash": self.service.bootstrap()["standard_resume"]["review_hash"], "locale": "en"})
        self.service.adopt_standard_resume({"draft_ref": self.draft["draft_ref"], "expected_docx_sha256": self.draft["docx_sha256"], "user_confirmed": True})
        onboarding_fixtures.OnboardingCenterTests.prepare_completion(self.service)
        self.service.complete(user_confirmed=True)
        self.approve_materials()

    def approve_materials(self):
        approval = self.service.bootstrap()["external_claim_approval"]
        self.service.approve_external_claims({"user_confirmed": True, "expected_review_hash": approval["review_hash"], "allowed_uses": approval["allowed_uses"]})

    def assert_admitted(self):
        result = self.orchestrator.current_real_application_references()
        _, state = self.service.ensure_state()
        self.assertEqual(result["master_resume_ref"], state["master_resume"]["secure_ref"])
        self.assertEqual(self.onboarding.read_bytes(self.old_ref), self.old_bytes)
        return result

    def assert_rejected(self):
        with self.assertRaises(JobOpsError):
            self.orchestrator.current_real_application_references()

    def test_adopted_generated_master_is_accepted_without_changing_original_evidence(self):
        _, state = self.service.ensure_state()
        original = state["base_claim_source"]["secure_ref"]
        original_bytes = self.onboarding.read_bytes(original)
        self.assert_admitted()
        self.assertEqual(self.onboarding.read_bytes(original), original_bytes)
        with self.service.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM external_action_attempts").fetchone()[0], 0)

    def test_later_confirmed_revision_keeps_the_same_adoption_lineage(self):
        self.service.start_revision()
        onboarding_fixtures.OnboardingCenterTests.prepare_completion(self.service)
        self.service.complete(user_confirmed=True)
        self.approve_materials()
        self.assert_admitted()

    def test_generated_file_without_explicit_adoption_is_rejected(self):
        reference, state = self.service.ensure_state()
        del state["adopted_standard_resume_ref"]
        self.onboarding.rotate(reference, canonical_json(state))
        self.assert_rejected()

    def test_adoption_requires_current_completed_state_and_original_binding(self):
        reference, state = self.service.ensure_state()
        for mutation in ("editable", "derived_from", "base_source"):
            with self.subTest(mutation=mutation):
                changed = deepcopy(state)
                if mutation == "editable": changed["status"] = "IN_PROGRESS"
                if mutation == "derived_from": changed["master_resume"]["derived_from"] = changed["master_resume"]["secure_ref"]
                if mutation == "base_source": changed["base_claim_source"]["sha256"] = "sha256:" + "0" * 64
                self.onboarding.rotate(reference, canonical_json(changed))
                self.assert_rejected()
        self.onboarding.rotate(reference, canonical_json(state))

    def test_draft_file_hash_and_original_approval_must_all_agree(self):
        reference = self.draft["draft_ref"]
        draft = json.loads(self.onboarding.read_bytes(reference))
        for field, value in (
            ("docx_ref", draft["original_master"]["secure_ref"]),
            ("docx_sha256", "sha256:" + "0" * 64),
            ("template_fingerprint", "sha256:" + "0" * 64),
            ("claim_set_hash", "sha256:" + "0" * 64),
            ("onboarding_state_ref", "secure-ref:UNKNOWN0001"),
            ("profile_hash", "sha256:" + "0" * 64),
        ):
            with self.subTest(field=field):
                self.onboarding.rotate(reference, canonical_json({**draft, field: value}))
                self.assert_rejected()
        self.onboarding.rotate(reference, canonical_json(draft))

    def test_wrong_kind_adoption_receipt_is_rejected(self):
        reference, state = self.service.ensure_state()
        state["adopted_standard_resume_ref"] = state["master_resume"]["secure_ref"]
        self.onboarding.rotate(reference, canonical_json(state))
        self.assert_rejected()

    def test_changed_original_pdf_is_rejected(self):
        _, state = self.service.ensure_state()
        self.onboarding.rotate(state["base_claim_source"]["secure_ref"], b"changed original synthetic source")
        self.assert_rejected()

    def test_current_material_permission_is_still_required(self):
        self.service.start_revision()
        onboarding_fixtures.OnboardingCenterTests.prepare_completion(self.service)
        self.service.complete(user_confirmed=True)
        self.assert_rejected()

    def test_legacy_master_citations_still_bind_only_to_the_original_pdf(self):
        # A legacy-source contract mutation only; the native journey uses normal
        # UPLOADED_MATERIAL bindings and never rewrites an approval fixture.
        refs = self.assert_admitted()
        reference = refs["external_claim_set_ref"]
        claims = json.loads(self.onboarding.read_bytes(reference))
        for claim in claims["claims"]:
            for binding in claim["source_bindings"]:
                binding["kind"] = "MASTER_RESUME"
        claims["content_hash"] = sha256_bytes(canonical_json({key: value for key, value in claims.items() if key != "content_hash"}))
        self.onboarding.rotate(reference, canonical_json(claims))
        self.assert_admitted()
        unrelated = self.onboarding.import_bytes("onboarding_source_document", b"unrelated synthetic source", synthetic=False)
        claims["claims"][0]["source_bindings"][0].update({"secure_ref": unrelated["secure_ref"], "content_sha256": unrelated["content_sha256"]})
        claims["content_hash"] = sha256_bytes(canonical_json({key: value for key, value in claims.items() if key != "content_hash"}))
        self.onboarding.rotate(reference, canonical_json(claims))
        with self.assertRaises(JobOpsError) as rejected:
            self.orchestrator.current_real_application_references()
        self.assertEqual(rejected.exception.code, "APPLICATION_CLAIM_MASTER_BINDING_MISMATCH")

    def test_synthetic_or_revoked_draft_cannot_be_promoted_by_real_state(self):
        reference = self.draft["draft_ref"]
        for column, value in (("synthetic", 1), ("status", "REVOKED")):
            with self.subTest(boundary=column):
                with self.service.database.connect() as connection:
                    connection.execute("UPDATE private_refs SET " + column + "=? WHERE secure_ref=?", (value, reference))
                self.assert_rejected()
                with self.service.database.connect() as connection:
                    connection.execute("UPDATE private_refs SET synthetic=0,status='ACTIVE' WHERE secure_ref=?", (reference,))

    def test_expired_generation_receipt_is_history_but_current_permission_must_be_live(self):
        draft_ref = self.draft["draft_ref"]
        draft = json.loads(self.onboarding.read_bytes(draft_ref))
        prior = json.loads(self.onboarding.read_bytes(draft["claim_set_ref"]))
        prior["expires_at"] = "2020-01-01T00:00:00Z"
        prior["content_hash"] = sha256_bytes(canonical_json({key: value for key, value in prior.items() if key != "content_hash"}))
        self.onboarding.rotate(draft["claim_set_ref"], canonical_json(prior))
        draft["claim_set_hash"] = prior["content_hash"]
        self.onboarding.rotate(draft_ref, canonical_json(draft))
        refs = self.assert_admitted()
        current = json.loads(self.onboarding.read_bytes(refs["external_claim_set_ref"]))
        current["expires_at"] = "2020-01-01T00:00:00Z"
        current["content_hash"] = sha256_bytes(canonical_json({key: value for key, value in current.items() if key != "content_hash"}))
        self.onboarding.rotate(refs["external_claim_set_ref"], canonical_json(current))
        with self.assertRaises(JobOpsError) as rejected:
            self.orchestrator.current_real_application_references()
        self.assertEqual(rejected.exception.code, "EXTERNAL_CLAIM_SET_EXPIRED")


if __name__ == "__main__":
    unittest.main()
