from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from _support import project_temp
from jobops.claims import external_use_decision
from jobops.db import JobOpsDB
from jobops.util import iso_utc


def claim(source_id: str = "personal_redacted", approved: bool = True, expires_delta: int = 30):
    now = datetime.now(timezone.utc)
    return {
        "claim_id": "CLM-TEST0001",
        "raw_fact": "Synthetic test fact",
        "allowed_wording": ["Approved exact wording"],
        "forbidden_wording": ["solely delivered"],
        "responsibility_boundary": {"candidate": "designed", "team": "reviewed", "ai": "drafted alternatives"},
        "evidence": [{"kind": "count", "value": 1}],
        "source_refs": [{"source_id": source_id, "relative_path": "case.md", "fingerprint": "sha256:" + "a" * 64}],
        "approved_for_external": approved,
        "sensitivity": "personal-redacted",
        "last_verified_at": iso_utc(now),
        "expires_at": iso_utc(now + timedelta(days=expires_delta)),
    }


class ClaimTests(unittest.TestCase):
    def test_only_approved_personal_claim_can_be_external(self) -> None:
        decision = external_use_decision(claim(), wording="Approved exact wording")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, "APPROVED")

    def test_unapproved_claim_is_blocked(self) -> None:
        decision = external_use_decision(claim(approved=False), wording="Approved exact wording")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "CLAIM_NOT_APPROVED")

    def test_ai_or_business_knowledge_is_not_personal_evidence(self) -> None:
        for source in ("ai_public_core", "business_public_core", "joint_navigation"):
            with self.subTest(source=source):
                decision = external_use_decision(claim(source_id=source), wording="Approved exact wording")
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.code, "NON_PERSONAL_SOURCE")

    def test_redacted_usage_profile_is_question_only(self) -> None:
        value = claim()
        value["source_refs"][0]["relative_path"] = "个人AI应用实验室/01-使用画像与演变/profile.md"
        decision = external_use_decision(value, wording="Approved exact wording")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "QUESTION_ONLY_SOURCE")

    def test_expired_and_modified_wording_are_blocked(self) -> None:
        self.assertEqual(external_use_decision(claim(expires_delta=-1), wording="Approved exact wording").code, "CLAIM_EXPIRED")
        self.assertEqual(external_use_decision(claim(), wording="A stronger invented wording").code, "WORDING_NOT_ALLOWLISTED")

    def test_registry_upsert_is_idempotent(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            database.upsert_claim(claim())
            database.upsert_claim(claim())
            self.assertEqual(database.table_counts()["claims"], 1)


if __name__ == "__main__":
    unittest.main()
