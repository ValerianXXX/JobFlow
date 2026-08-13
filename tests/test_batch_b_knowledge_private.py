from __future__ import annotations

import json
import os
import secrets
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _support import fixture_manifest, make_knowledge_root, project_temp, write_json
from jobops.claim_registry import ClaimRegistry
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError, SecurityBoundaryError
from jobops.knowledge import KnowledgeGateway
from jobops.locator import locate_knowledge_root
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.security import assert_project_io_path, path_has_hard_excluded_name
from jobops.util import iso_utc, sha256_bytes, sha256_file


SENTINEL = "JOBOPS_SYNTH_PRIVATE_SENTINEL_" + secrets.token_hex(16).upper()


def evidenced_claim(gateway: KnowledgeGateway, relative: str = "case.md") -> dict[str, object]:
    path = gateway.safe_path("personal_redacted", relative)
    text = path.read_text(encoding="utf-8")
    excerpt = "Built one synthetic evidence map."
    now = datetime.now(timezone.utc)
    return {
        "claim_id": "CLM-SYNTH-EVIDENCE",
        "raw_fact": "Built one synthetic evidence map.",
        "allowed_wording": ["Built one synthetic evidence map"],
        "forbidden_wording": ["Built a real client evidence map"],
        "responsibility_boundary": {"candidate": "built fixture", "team": "reviewed fixture", "ai": "formatted fixture"},
        "evidence": [{"kind": "count", "value": 1, "scope": "synthetic fixture only"}],
        "source_refs": [{
            "source_id": "personal_redacted",
            "relative_path": relative,
            "heading": "Verified fixture",
            "excerpt": excerpt,
            "excerpt_fingerprint": sha256_bytes(excerpt.encode("utf-8")),
            "fingerprint": sha256_file(path),
        }],
        "approved_for_external": False,
        "lifecycle_status": "proposed",
        "sensitivity": "personal-redacted",
        "last_verified_at": iso_utc(now),
        "expires_at": iso_utc(now + timedelta(days=30)),
        "allowed_uses": ["synthetic_material"],
    }


class KnowledgeEvidenceLifecycleTests(unittest.TestCase):
    def gateway(self, temp):
        manifest_path = fixture_manifest(temp / "manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"][0]["question_only_prefixes"] = ["profiles"]
        write_json(manifest_path, manifest)
        root = make_knowledge_root(temp / "AI计划")
        (root / "vault" / "case.md").write_text("# Verified fixture\n\nBuilt one synthetic evidence map.\n", encoding="utf-8")
        (root / "vault" / "profiles").mkdir()
        (root / "vault" / "profiles" / "usage.md").write_text("# Usage\n\nBuilt one synthetic evidence map.\n", encoding="utf-8")
        location = locate_knowledge_root(temp, manifest_path, environment={}, local_config_path=temp / "absent.json")
        return KnowledgeGateway(location), root

    def test_claim_approval_requires_real_current_gateway_evidence(self) -> None:
        with project_temp() as temp:
            gateway, _ = self.gateway(temp)
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            registry = ClaimRegistry(database, gateway)
            claim = evidenced_claim(gateway)
            registry.propose(claim)
            approved = registry.approve("CLM-SYNTH-EVIDENCE", allowed_uses=("synthetic_material",))
            self.assertEqual(approved["lifecycle_status"], "approved")
            self.assertTrue(approved["approved_for_external"])
            with database.connect() as connection:
                events = connection.execute("SELECT event_type FROM claim_events WHERE claim_id=? ORDER BY event_id", (claim["claim_id"],)).fetchall()
            self.assertEqual([row[0] for row in events], ["PROPOSED", "APPROVED"])

    def test_missing_heading_forged_hash_question_only_and_changed_file_are_blocked(self) -> None:
        with project_temp() as temp:
            gateway, root = self.gateway(temp)
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            registry = ClaimRegistry(database, gateway)
            scenarios = []
            missing = evidenced_claim(gateway)
            missing["source_refs"][0]["relative_path"] = "missing.md"
            scenarios.append((missing, "EVIDENCE_PATH_INVALID"))
            heading = evidenced_claim(gateway)
            heading["source_refs"][0]["heading"] = "Missing heading"
            scenarios.append((heading, "EVIDENCE_ANCHOR_MISSING"))
            forged = evidenced_claim(gateway)
            forged["source_refs"][0]["fingerprint"] = "sha256:" + "0" * 64
            scenarios.append((forged, "EVIDENCE_FILE_CHANGED"))
            question = evidenced_claim(gateway, "profiles/usage.md")
            scenarios.append((question, "QUESTION_ONLY_SOURCE"))
            for index, (claim, code) in enumerate(scenarios):
                claim["claim_id"] = f"CLM-SCENARIO-{index:02d}"
                registry.propose(claim)
                with self.assertRaises(JobOpsError) as caught:
                    registry.approve(claim["claim_id"], allowed_uses=("synthetic_material",))
                self.assertEqual(caught.exception.code, code)

            changed = evidenced_claim(gateway)
            changed["claim_id"] = "CLM-CHANGED-EVIDENCE"
            registry.propose(changed)
            (root / "vault" / "case.md").write_text("# Verified fixture\n\nChanged after proposal.\n", encoding="utf-8")
            with self.assertRaises(JobOpsError) as caught:
                registry.approve(changed["claim_id"], allowed_uses=("synthetic_material",))
            self.assertEqual(caught.exception.code, "EVIDENCE_FILE_CHANGED")

    def test_revoke_is_append_only_and_disables_external_use(self) -> None:
        with project_temp() as temp:
            gateway, _ = self.gateway(temp)
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            registry = ClaimRegistry(database, gateway)
            claim = evidenced_claim(gateway)
            registry.propose(claim)
            registry.approve(claim["claim_id"], allowed_uses=("synthetic_material",))
            revoked = registry.revoke(claim["claim_id"])
            self.assertEqual(revoked["lifecycle_status"], "revoked")
            self.assertFalse(revoked["approved_for_external"])
            with database.connect() as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM claim_events")


class PathAndPrivateOnboardingTests(unittest.TestCase):
    def test_hard_excluded_patterns_cover_variants_and_project_io_is_bounded(self) -> None:
        excluded = ["数据导入区", "原始附件", "cookies", "credentials", "tokens"]
        filenames = [".env", "credentials.json"]
        blocked = [
            "tokens.md", "Credentials Backup.txt", "cookies-backup", "API-Key.txt", "OAuth token.json",
            "原始附件-2026-08-12", "原始 ChatGPT 导出", "Hermes-原始备份", "数据 导入 区", "browser-profile", "private key.pem", "会话日志原文",
            "ＴＯＫＥＮＳ.md",
        ]
        for name in blocked:
            with self.subTest(name=name):
                self.assertTrue(path_has_hard_excluded_name(Path(name), excluded, filenames))
        with project_temp() as temp:
            project = temp / "project"
            (project / "workspace").mkdir(parents=True)
            allowed = project / "workspace" / "jd.txt"
            allowed.write_text("fixture", encoding="utf-8")
            self.assertEqual(assert_project_io_path(allowed, project, operation="read"), allowed.resolve())
            outside = temp / "outside.txt"
            outside.write_text("fixture", encoding="utf-8")
            with self.assertRaises(SecurityBoundaryError):
                assert_project_io_path(outside, project, operation="read")
            with self.assertRaises(SecurityBoundaryError):
                assert_project_io_path(project / "workspace" / "tokens.md", project, operation="write")

    def test_dpapi_onboarding_uses_private_root_and_leaks_no_sentinel(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            local_app_data = temp / "localappdata"
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=local_app_data)
            onboarding = PrivateOnboarding(database, store)
            record = onboarding.import_bytes("candidate_profile", SENTINEL.encode("utf-8"), synthetic=True)
            self.assertRegex(record["secure_ref"], r"^secure-ref:")
            self.assertTrue((local_app_data / "JobOps" / "private").is_dir())
            self.assertEqual(onboarding.read_bytes(record["secure_ref"]), SENTINEL.encode("utf-8"))
            rotated = onboarding.rotate(record["secure_ref"], (SENTINEL + "-V2").encode("utf-8"))
            self.assertEqual(rotated["version"], 2)
            onboarding.revoke(record["secure_ref"])
            with self.assertRaises(JobOpsError) as caught:
                onboarding.read_bytes(record["secure_ref"])
            self.assertEqual(caught.exception.code, "SECURE_REFERENCE_REVOKED")
            deleted = onboarding.delete(record["secure_ref"], user_confirmed=True)
            self.assertEqual(deleted["status"], "DELETED")
            self.assertFalse(any((local_app_data / "JobOps" / "private").glob("*.dpapi")))
            for path in temp.rglob("*"):
                if path.is_file() and path.suffix != ".db":
                    self.assertNotIn(SENTINEL.encode("utf-8"), path.read_bytes())
            connection = sqlite3.connect(temp / "jobops.db")
            for row in connection.iterdump():
                self.assertNotIn(SENTINEL, row)
            connection.close()

    def test_ciphertext_corruption_is_detected_without_plaintext_error(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            local_app_data = temp / "localappdata"
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=local_app_data)
            onboarding = PrivateOnboarding(database, store)
            record = onboarding.import_bytes("answer_bank", SENTINEL.encode("utf-8"), synthetic=True)
            store.cipher_path(record["secure_ref"]).write_bytes(b"corrupt")
            with self.assertRaises(JobOpsError) as caught:
                onboarding.read_bytes(record["secure_ref"])
            self.assertEqual(caught.exception.code, "SECURE_STORE_FAILED")
            self.assertNotIn(SENTINEL, json.dumps(caught.exception.as_dict()))
            onboarding.delete(record["secure_ref"], user_confirmed=True)


if __name__ == "__main__":
    unittest.main()
