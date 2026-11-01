"""Behavioral checks for assigning distinct approved facts to resume positions."""
from __future__ import annotations
from copy import deepcopy
import unittest
import test_resume_tailoring_manifest as fixtures
from jobops.external_claims import build_external_claim_set, claim_review_hash
from jobops.resume_tailoring import build_resume_tailoring_manifest, choose_tailoring_replacements
from jobops.util import canonical_json, sha256_bytes


class DistinctTailoringClaimsTests(unittest.TestCase):
    def scenario(self, wordings=None, limits=(200, 200)):
        fixture = fixtures.ResumeTailoringManifestTests()
        fixture.setUp()
        proposal, base_claim = fixture._proposal()
        candidate = proposal["candidates"][0]
        manifest = build_resume_tailoring_manifest(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE", master_resume=fixture.master_descriptor,
            proposal=proposal, selections=[{"block_ref": candidate["block_ref"], "category": "project"}],
            expected_proposal_hash=proposal["proposal_hash"], user_confirmed=True,
        )
        prototype = deepcopy(manifest["blocks"][0])
        manifest["blocks"] = [
            {**prototype, "block_ref": f"BLOCK-{i}", "paragraph_index": i, "maximum_characters": limit}
            for i, limit in enumerate(limits)
        ]
        manifest["block_count"] = len(limits)
        manifest["content_hash"] = sha256_bytes(canonical_json({key: value for key, value in manifest.items() if key != "content_hash"}))
        wordings = wordings or [
            "The applicant reviewed twelve stores with the project team.",
            "The applicant documented eight reports for the project team.",
            "The applicant tested twenty-six scenarios for the project team.",
        ]
        claims = [{**base_claim, "claim_id": f"CLAIM-{i}", "statement": wording, "claim_kind": "achievement",
                   "source_bindings": [{"kind": "MASTER_RESUME", "secure_ref": "secure-ref:SYNTHETIC_MASTER", "content_sha256": fixture.master_hash}]}
                  for i, wording in enumerate(wordings)]
        external = build_external_claim_set(
            onboarding_state_ref="secure-ref:SYNTHETIC_STATE", profile_ref="secure-ref:SYNTHETIC_PROFILE",
            master_resume=fixture.master_descriptor, claims=claims, allowed_uses=["resume"],
            expected_review_hash=claim_review_hash(claims, fixture.master_hash),
        )
        return manifest, external

    def select(self, manifest, external, preferred=None):
        return choose_tailoring_replacements(manifest=manifest, external_claim_set=external,
                                             job_text="Review project evidence and reports with the team.", preferred_claim_ids=preferred)

    def test_two_positions_receive_distinct_claims_in_ai_preference_order(self):
        manifest, external = self.scenario()
        result = self.select(manifest, external, ["CLAIM-2", "CLAIM-0", "CLAIM-1"])
        self.assertEqual([item["claim_id"] for item in result], ["CLAIM-2", "CLAIM-0"])

    def test_lexical_route_also_uses_each_claim_at_most_once(self):
        manifest, external = self.scenario()
        result = self.select(manifest, external)
        self.assertEqual(len(result), 2)
        self.assertEqual(len({item["claim_id"] for item in result}), 2)

    def test_exhausted_candidates_do_not_repeat_a_fact_to_fill_another_position(self):
        manifest, external = self.scenario(["The applicant reviewed twelve stores with the project team."])
        self.assertEqual(len(self.select(manifest, external, ["CLAIM-0"])), 1)

    def test_long_preferred_fact_does_not_hide_a_shorter_approved_candidate(self):
        manifest, external = self.scenario([
            "The applicant reviewed project evidence with the team and documented every store outcome in the original reviewed report.",
            "The applicant reviewed project evidence with the team.",
        ], limits=(60,))
        self.assertEqual(self.select(manifest, external, ["CLAIM-0", "CLAIM-1"])[0]["claim_id"], "CLAIM-1")

    def test_identical_wording_under_two_ids_is_not_duplicated(self):
        wording = "The applicant reviewed twelve stores with the project team."
        manifest, external = self.scenario([wording, wording, "The applicant documented eight reports for the project team."])
        result = self.select(manifest, external, ["CLAIM-0", "CLAIM-1", "CLAIM-2"])
        self.assertEqual([item["claim_id"] for item in result], ["CLAIM-0", "CLAIM-2"])


if __name__ == "__main__":
    unittest.main()
