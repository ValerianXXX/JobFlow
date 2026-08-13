from __future__ import annotations

import json
import unittest

from _support import PROJECT, project_temp
from jobops.document_builder import inspect_docx_text_blocks, tailor_master_resume_with_manifest
from jobops.document_qa import extract_docx_text
from jobops.errors import JobOpsError
from jobops.external_claims import build_external_claim_set, claim_review_hash
from jobops.resume_tailoring import (
    build_resume_tailoring_manifest,
    build_tailoring_proposal,
    validate_resume_tailoring_manifest_integrity,
)
from jobops.runtime_schema import validate_named
from jobops.util import sha256_file


class ResumeTailoringManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.master = PROJECT / "tests" / "fixtures" / "complex-master-resume.docx"
        self.master_hash = sha256_file(self.master)
        self.master_descriptor = {
            "secure_ref": "secure-ref:SYNTHETIC_MASTER", "sha256": self.master_hash,
            "template_fingerprint": "sha256:" + "8" * 64,
            "editable_docx": True, "source_id": "SRC-SYNTHETIC_MASTER",
        }

    def _proposal(self) -> tuple[dict, dict]:
        blocks = inspect_docx_text_blocks(self.master)
        source = next(item for item in blocks if item["text_length"] >= 20)
        claim = {
            "claim_id": "CLM-SYNTHETIC-MANIFEST", "category": "project",
            "statement": "Built a synthetic, evidence-bound workflow for a reviewed local application.",
            "decision": "CONFIRMED", "deleted": False,
            "source_id": "SRC-SYNTHETIC_MASTER",
            "provenance": {"line_start": source["line_number"], "line_end": source["line_number"]},
        }
        proposal = build_tailoring_proposal(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE",
            master_resume=self.master_descriptor, blocks=blocks, claims=[claim],
        )
        return proposal, claim

    def test_ai_reviewed_block_proposal_persists_only_hashes_and_needs_confirmation(self) -> None:
        proposal, _ = self._proposal()
        self.assertEqual(proposal["candidate_count"], 1)
        candidate = proposal["candidates"][0]
        self.assertIn("text", candidate)
        with self.assertRaises(JobOpsError) as unconfirmed:
            build_resume_tailoring_manifest(
                onboarding_state_ref="secure-ref:SYNTHETIC_STATE", master_resume=self.master_descriptor,
                proposal=proposal, selections=[{"block_ref": candidate["block_ref"], "category": "project"}],
                expected_proposal_hash=proposal["proposal_hash"], user_confirmed=False,
            )
        self.assertEqual(unconfirmed.exception.code, "TAILORING_CONFIRMATION_REQUIRED")
        manifest = build_resume_tailoring_manifest(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE", master_resume=self.master_descriptor,
            proposal=proposal, selections=[{"block_ref": candidate["block_ref"], "category": "project"}],
            expected_proposal_hash=proposal["proposal_hash"], user_confirmed=True,
        )
        validate_named("resume-tailoring-manifest", manifest, PROJECT / "schemas")
        validate_resume_tailoring_manifest_integrity(manifest)
        serialized = json.dumps(manifest)
        self.assertNotIn(candidate["text"], serialized)
        self.assertEqual(manifest["real_external_actions"], 0)

    def test_manifest_tailoring_uses_exact_approved_claim_and_preserves_master(self) -> None:
        proposal, claim = self._proposal()
        candidate = proposal["candidates"][0]
        manifest = build_resume_tailoring_manifest(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE", master_resume=self.master_descriptor,
            proposal=proposal, selections=[{"block_ref": candidate["block_ref"], "category": "project"}],
            expected_proposal_hash=proposal["proposal_hash"], user_confirmed=True,
        )
        external_input = [{
            **claim,
            "claim_kind": "achievement",
            "source_bindings": [{
                "kind": "MASTER_RESUME", "secure_ref": "secure-ref:SYNTHETIC_MASTER",
                "content_sha256": self.master_hash,
            }],
        }]
        external = build_external_claim_set(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE", profile_ref="secure-ref:SYNTHETIC_PROFILE",
            master_resume=self.master_descriptor, claims=external_input, allowed_uses=["resume"],
            expected_review_hash=claim_review_hash(external_input, self.master_hash),
        )
        original = self.master.read_bytes()
        with project_temp() as root:
            output = root / "tailored.docx"
            diff = tailor_master_resume_with_manifest(
                self.master, output, manifest=manifest,
                replacements=[{"block_ref": candidate["block_ref"], "claim_id": claim["claim_id"]}],
                external_claim_set=external, synthetic=True,
            )
            self.assertIn(claim["statement"], extract_docx_text(output))
            self.assertEqual(diff["block_changes"][0]["claim_id"], claim["claim_id"])
            self.assertNotIn(claim["statement"], json.dumps(diff))
        self.assertEqual(self.master.read_bytes(), original)

    def test_unrelated_claim_cannot_create_or_fill_a_manifest_position(self) -> None:
        blocks = inspect_docx_text_blocks(self.master)
        unrelated = {
            "claim_id": "CLM-SYNTHETIC-UNRELATED", "category": "project",
            "statement": "This unrelated synthetic sentence has no grounding in the selected document.",
            "decision": "CONFIRMED", "deleted": False, "source_id": "SRC-OTHER",
            "provenance": {"line_start": 1, "line_end": 1},
        }
        with self.assertRaises(JobOpsError) as empty:
            build_tailoring_proposal(
                onboarding_state_ref="secure-ref:SYNTHETIC_STATE",
                master_resume=self.master_descriptor, blocks=blocks, claims=[unrelated],
            )
        self.assertEqual(empty.exception.code, "TAILORING_PROPOSAL_EMPTY")


if __name__ == "__main__":
    unittest.main()
