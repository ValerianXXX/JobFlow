from __future__ import annotations

import unittest

from _support import PROJECT
from jobops.application_readiness import build_application_readiness
from jobops.document_builder import build_cover_letter_narrative
from jobops.errors import JobOpsError
from jobops.external_claims import build_external_claim_set, claim_review_hash, validate_external_claim_set_integrity
from jobops.runtime_schema import validate_named


H1 = "sha256:" + "1" * 64


def reviewed_claim() -> dict[str, object]:
    return {
        "claim_id": "CLM-SYNTHETIC01", "category": "project", "claim_kind": "achievement",
        "statement": "The applicant completed a synthetic, evidence-bound project.",
        "decision": "CONFIRMED", "deleted": False,
        "source_bindings": [{
            "kind": "MASTER_RESUME", "secure_ref": "secure-ref:SYNTHETIC_MASTER",
            "content_sha256": H1,
        }],
    }


class ExternalClaimReadinessTests(unittest.TestCase):
    def test_explicit_external_claim_set_is_hash_bound_and_schema_valid(self) -> None:
        claims = [reviewed_claim()]
        review_hash = claim_review_hash(claims, H1)
        value = build_external_claim_set(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE",
            profile_ref="secure-ref:SYNTHETIC_PROFILE",
            master_resume={"secure_ref": "secure-ref:SYNTHETIC_MASTER", "sha256": H1, "editable_docx": True},
            claims=claims,
            allowed_uses=["resume", "cover_letter", "application_narrative"],
            expected_review_hash=review_hash,
        )
        validate_named("external-claim-set", value, PROJECT / "schemas")
        validate_external_claim_set_integrity(value)
        self.assertEqual(value["claim_count"], 1)
        self.assertTrue(value["claims"][0]["applicant_confirmed"])
        self.assertEqual(value["real_external_actions"], 0)

        value["claims"][0]["allowed_wording"][0] = "Tampered wording."
        with self.assertRaises(JobOpsError) as tampered:
            validate_external_claim_set_integrity(value)
        self.assertEqual(tampered.exception.code, "EXTERNAL_CLAIM_SET_HASH_INVALID")

    def test_changed_review_cannot_reuse_old_approval_hash(self) -> None:
        claims = [reviewed_claim()]
        old_hash = claim_review_hash(claims, H1)
        claims[0]["statement"] = "The applicant completed a changed synthetic project."
        with self.assertRaises(JobOpsError) as stale:
            build_external_claim_set(
                onboarding_state_ref="secure-ref:SYNTHETIC_STATE",
                profile_ref="secure-ref:SYNTHETIC_PROFILE",
                master_resume={"secure_ref": "secure-ref:SYNTHETIC_MASTER", "sha256": H1, "editable_docx": True},
                claims=claims, allowed_uses=["resume"], expected_review_hash=old_hash,
            )
        self.assertEqual(stale.exception.code, "EXTERNAL_CLAIM_REVIEW_STALE")

    def test_application_narrative_requires_its_own_external_use_approval(self) -> None:
        claims = [reviewed_claim()]
        review_hash = claim_review_hash(claims, H1)
        cover_only = build_external_claim_set(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE",
            profile_ref="secure-ref:SYNTHETIC_PROFILE",
            master_resume={"secure_ref": "secure-ref:SYNTHETIC_MASTER", "sha256": H1, "editable_docx": True},
            claims=claims,
            allowed_uses=["cover_letter"],
            expected_review_hash=review_hash,
        )
        with self.assertRaises(JobOpsError) as blocked:
            build_cover_letter_narrative(
                candidate_display_name="Synthetic Candidate",
                company="Example",
                target_role="Analyst",
                why_company="Example published the role (https://example.com/role, accessed 2026-08-21).",
                why_role=str(claims[0]["statement"]),
                external_claim_set=cover_only,
            )
        self.assertEqual(blocked.exception.code, "EXTERNAL_CLAIM_USE_NOT_APPROVED")

        dual_use = build_external_claim_set(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE",
            profile_ref="secure-ref:SYNTHETIC_PROFILE",
            master_resume={"secure_ref": "secure-ref:SYNTHETIC_MASTER", "sha256": H1, "editable_docx": True},
            claims=claims,
            allowed_uses=["cover_letter", "application_narrative"],
            expected_review_hash=review_hash,
        )
        narrative = build_cover_letter_narrative(
            candidate_display_name="Synthetic Candidate",
            company="Example",
            target_role="Analyst",
            why_company="Example published the role (https://example.com/role, accessed 2026-08-21).",
            why_role=str(claims[0]["statement"]),
            external_claim_set=dual_use,
        )
        self.assertEqual(narrative.claim_ids, ("CLM-SYNTHETIC01",))

    def test_readiness_is_true_only_when_every_local_material_gate_is_ready(self) -> None:
        queue = {"pending_limit": 10, "awaiting_approval": 0, "slots_available": 10}
        blocked = build_application_readiness(
            onboarding_status="ONBOARDING_COMPLETE", ai_ready=True,
            master_resume={
                "secure_ref": "secure-ref:SYNTHETIC_MASTER", "sha256": H1,
                "editable_docx": True, "template_fingerprint": H1, "template_slots": [],
            },
            confirmed_claim_count=1, claim_review_hash=H1,
            external_claim_status={"current": True, "claim_count": 1}, queue=queue,
        )
        self.assertEqual(blocked["status"], "NEEDS_TEMPLATE_PREPARATION")
        self.assertFalse(blocked["capabilities"]["offline_application_preparation"])

        ready = build_application_readiness(
            onboarding_status="ONBOARDING_COMPLETE", ai_ready=True,
            master_resume={
                "secure_ref": "secure-ref:SYNTHETIC_MASTER", "sha256": H1,
                "editable_docx": True, "template_fingerprint": H1, "template_slots": ["SUMMARY"],
            },
            confirmed_claim_count=1, claim_review_hash=H1,
            external_claim_status={"current": True, "claim_count": 1}, queue=queue,
        )
        validate_named("application-readiness", ready, PROJECT / "schemas")
        self.assertEqual(ready["status"], "READY_FOR_OFFLINE_APPLICATION_PREPARATION")
        self.assertEqual(ready["blockers"], [])
        self.assertFalse(ready["capabilities"]["live_site_access"])
        self.assertEqual(ready["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
