from __future__ import annotations

import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from _support import fixture_manifest, make_knowledge_root, project_temp, write_json
from jobops.claim_registry import ClaimRegistry
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError, SecurityBoundaryError
from jobops.knowledge import KnowledgeGateway
from jobops.locator import locate_knowledge_root
from jobops.private_onboarding import PrivateOnboarding
from jobops.release import security_scan
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

    def test_private_file_import_is_bounded_and_rejects_reparse_paths(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            local_app_data = temp / "localappdata"
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            onboarding = PrivateOnboarding(database, WindowsDPAPIStore(script, local_app_data=local_app_data))
            selected = temp / "selected.json"
            selected.write_bytes(b"123456789")

            with patch("jobops.private_onboarding.MAX_PRIVATE_IMPORT_FILE_BYTES", 8):
                with self.assertRaises(JobOpsError) as oversized:
                    onboarding.import_file("answer_bank", selected, synthetic=True)
            self.assertEqual(oversized.exception.code, "PRIVATE_IMPORT_FILE_TOO_LARGE")

            with patch("jobops.private_onboarding.has_reparse_component", return_value=True):
                with self.assertRaises(JobOpsError) as reparse:
                    onboarding.import_file("answer_bank", selected, synthetic=True)
            self.assertEqual(reparse.exception.code, "PRIVATE_IMPORT_REPARSE_FORBIDDEN")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM private_refs").fetchone()[0], 0)
            self.assertFalse(any(onboarding.store.private_root.glob("*.dpapi")))

    def test_private_import_and_rotation_roll_back_metadata_failures(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            local_app_data = temp / "localappdata"
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=local_app_data)
            onboarding = PrivateOnboarding(database, store)

            original_connect = database.connect
            import_calls = 0

            def fail_import_metadata():
                nonlocal import_calls
                import_calls += 1
                if import_calls == 2:
                    raise sqlite3.OperationalError("synthetic metadata failure")
                return original_connect()

            with patch.object(database, "connect", side_effect=fail_import_metadata):
                with self.assertRaises(JobOpsError) as failed_import:
                    onboarding.import_bytes("candidate_profile", SENTINEL.encode("utf-8"), synthetic=True)
            self.assertEqual(failed_import.exception.code, "PRIVATE_IMPORT_DATABASE_FAILED")
            self.assertFalse(any(store.private_root.glob("*.dpapi")))
            self.assertFalse(any(store.private_root.glob(".jobflow-write-*")))

            original = (SENTINEL + "-ORIGINAL").encode("utf-8")
            record = onboarding.import_bytes("answer_bank", original, synthetic=True)
            rotate_calls = 0

            def fail_rotation_metadata():
                nonlocal rotate_calls
                rotate_calls += 1
                if rotate_calls == 3:
                    raise sqlite3.OperationalError("synthetic rotation failure")
                return original_connect()

            with patch.object(database, "connect", side_effect=fail_rotation_metadata):
                with self.assertRaises(JobOpsError) as failed_rotation:
                    onboarding.rotate(record["secure_ref"], (SENTINEL + "-NEW").encode("utf-8"))
            self.assertEqual(failed_rotation.exception.code, "PRIVATE_ROTATION_DATABASE_FAILED")
            self.assertEqual(onboarding.read_bytes(record["secure_ref"]), original)
            with original_connect() as connection:
                row = connection.execute("SELECT version,status FROM private_refs WHERE secure_ref=?", (record["secure_ref"],)).fetchone()
            self.assertEqual((row["version"], row["status"]), (1, "ACTIVE"))
            self.assertFalse(any(store.private_root.glob(".jobflow-write-*")))
            onboarding.delete(record["secure_ref"], user_confirmed=True)

    def test_private_delete_rolls_back_storage_and_metadata_failures(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=temp / "localappdata")
            onboarding = PrivateOnboarding(database, store)
            record = onboarding.import_bytes("answer_bank", SENTINEL.encode("utf-8"), synthetic=True)
            reference = str(record["secure_ref"])

            with patch.object(store, "delete", side_effect=JobOpsError("SECURE_STORE_FAILED", "Synthetic delete failure.")):
                with self.assertRaises(JobOpsError) as storage_failure:
                    onboarding.delete(reference, user_confirmed=True)
            self.assertEqual(storage_failure.exception.code, "PRIVATE_DELETE_STORAGE_FAILED")
            self.assertTrue(store.test(reference))
            with database.connect() as connection:
                status = connection.execute("SELECT status FROM private_refs WHERE secure_ref=?", (reference,)).fetchone()[0]
            self.assertEqual(status, "ACTIVE")

            original_connect = database.connect
            connect_calls = 0

            def fail_delete_metadata():
                nonlocal connect_calls
                connect_calls += 1
                if connect_calls == 2:
                    raise sqlite3.OperationalError("synthetic delete metadata failure")
                return original_connect()

            with patch.object(database, "connect", side_effect=fail_delete_metadata):
                with self.assertRaises(JobOpsError) as database_failure:
                    onboarding.delete(reference, user_confirmed=True)
            self.assertEqual(database_failure.exception.code, "PRIVATE_DELETE_DATABASE_FAILED")
            self.assertTrue(store.test(reference))
            with original_connect() as connection:
                status = connection.execute("SELECT status FROM private_refs WHERE secure_ref=?", (reference,)).fetchone()[0]
            self.assertEqual(status, "ACTIVE")
            onboarding.delete(reference, user_confirmed=True)
            self.assertFalse(store.test(reference))

    def test_private_delete_accepts_helper_failure_after_completed_delete(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=temp / "localappdata")
            onboarding = PrivateOnboarding(database, store)
            record = onboarding.import_bytes("answer_bank", SENTINEL.encode("utf-8"), synthetic=True)
            reference = str(record["secure_ref"])
            original_delete = store.delete

            def complete_then_fail(value: str) -> None:
                original_delete(value)
                raise JobOpsError("SECURE_STORE_FAILED", "Synthetic lost helper reply.")

            with patch.object(store, "delete", side_effect=complete_then_fail):
                deleted = onboarding.delete(reference, user_confirmed=True)
            self.assertEqual(deleted["status"], "DELETED")
            self.assertFalse(store.test(reference))
            with database.connect() as connection:
                status = connection.execute("SELECT status FROM private_refs WHERE secure_ref=?", (reference,)).fetchone()[0]
            self.assertEqual(status, "DELETED")

    def test_interrupted_new_dpapi_write_has_a_known_cleanup_reference(self) -> None:
        with project_temp() as temp:
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=temp / "localappdata")
            original_run = store._run

            def interrupted(operation, reference=None, payload=None, **kwargs):
                if operation == "Put":
                    path = store.cipher_path(reference)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"synthetic interrupted ciphertext")
                    raise JobOpsError("SECURE_STORE_FAILED", "Synthetic interrupted helper.")
                return original_run(operation, reference=reference, payload=payload, **kwargs)

            with patch.object(store, "_run", side_effect=interrupted):
                with self.assertRaises(JobOpsError) as interrupted_write:
                    store.put_bytes(SENTINEL.encode("utf-8"))
            self.assertEqual(interrupted_write.exception.code, "SECURE_STORE_FAILED")
            self.assertFalse(any(store.private_root.glob("*.dpapi")))

    def test_interrupted_rotation_restores_old_private_content(self) -> None:
        with project_temp() as temp:
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=temp / "localappdata")
            onboarding = PrivateOnboarding(database, store)
            original = (SENTINEL + "-ORIGINAL").encode("utf-8")
            record = onboarding.import_bytes("answer_bank", original, synthetic=True)
            original_run = store._run
            failed_once = False

            def interrupted(operation, reference=None, payload=None, **kwargs):
                nonlocal failed_once
                if operation == "Put" and not failed_once:
                    failed_once = True
                    # Simulate a helper that replaced the destination and then lost its reply.
                    completed = original_run(operation, reference=reference, payload=payload, **kwargs)
                    self.assertEqual(completed.returncode, 0)
                    raise JobOpsError("SECURE_STORE_FAILED", "Synthetic interrupted helper.")
                return original_run(operation, reference=reference, payload=payload, **kwargs)

            with patch.object(store, "_run", side_effect=interrupted):
                with self.assertRaises(JobOpsError) as interrupted_rotation:
                    onboarding.rotate(record["secure_ref"], (SENTINEL + "-NEW").encode("utf-8"))
            self.assertEqual(interrupted_rotation.exception.code, "PRIVATE_ROTATION_WRITE_FAILED")
            self.assertEqual(onboarding.read_bytes(record["secure_ref"]), original)
            with database.connect() as connection:
                row = connection.execute("SELECT version,status FROM private_refs WHERE secure_ref=?", (record["secure_ref"],)).fetchone()
            self.assertEqual((row["version"], row["status"]), (1, "ACTIVE"))
            self.assertFalse(any(store.private_root.glob(".jobflow-write-*")))
            onboarding.delete(record["secure_ref"], user_confirmed=True)

    def test_release_scan_rejects_atomic_write_residue(self) -> None:
        with project_temp() as temp:
            project = temp / "project"
            project.mkdir()
            database = JobOpsDB(project / "jobops.db")
            database.initialize()
            local_app_data = temp / "localappdata"
            private_root = local_app_data / "JobOps" / "private"
            private_root.mkdir(parents=True)
            (private_root / ".jobflow-write-synthetic.tmp").write_bytes(b"synthetic encrypted residue")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}):
                report = security_scan(project, database)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["private_temporary_file_count"], 1)
            self.assertIn("private_atomic_write_residue", {item["kind"] for item in report["findings"]})

    def test_release_scan_binds_each_active_reference_to_its_ciphertext_hash(self) -> None:
        with project_temp() as temp, tempfile.TemporaryDirectory(prefix="jobflow-private-integrity-") as private_base:
            project = temp / "project"
            project.mkdir()
            database = JobOpsDB(project / "state" / "jobops.db")
            database.initialize()
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=Path(private_base))
            onboarding = PrivateOnboarding(database, store)
            record = onboarding.import_bytes("answer_bank", SENTINEL.encode("utf-8"), synthetic=True)
            cipher = store.cipher_path(record["secure_ref"])
            original_ciphertext = cipher.read_bytes()

            with patch.dict(os.environ, {"LOCALAPPDATA": private_base}):
                clean = security_scan(project, database)
                self.assertEqual(clean["status"], "PASS", clean)
                self.assertEqual(clean["private_ciphertext_integrity_failure_count"], 0)

                cipher.write_bytes(b"synthetic-corruption")
                mismatch = security_scan(project, database)
                self.assertEqual(mismatch["private_ciphertext_integrity_failure_count"], 1)
                self.assertIn("private_ciphertext_hash_mismatch", {item["kind"] for item in mismatch["findings"]})

                cipher.write_bytes(original_ciphertext)
                cipher.unlink()
                missing = security_scan(project, database)
                self.assertEqual(missing["private_ciphertext_integrity_failure_count"], 1)
                self.assertIn("missing_private_ciphertext", {item["kind"] for item in missing["findings"]})

                cipher.write_bytes(original_ciphertext)
                orphan = store.private_root / "orphan-ciphertext.dpapi"
                orphan.write_bytes(b"synthetic-orphan")
                extra = security_scan(project, database)
                self.assertEqual(extra["private_ciphertext_integrity_failure_count"], 1)
                self.assertIn("orphan_private_ciphertext", {item["kind"] for item in extra["findings"]})
                orphan.unlink()

                restored = security_scan(project, database)
                self.assertEqual(restored["status"], "PASS")
                self.assertEqual(restored["private_ciphertext_integrity_failure_count"], 0)

                onboarding.revoke(record["secure_ref"])
                revoked = security_scan(project, database)
                self.assertEqual(revoked["status"], "PASS", revoked)
                self.assertEqual(revoked["private_expected_ciphertext_file_count"], 1)
                self.assertEqual(revoked["private_ciphertext_file_count"], 1)

                with database.connect() as connection:
                    connection.execute(
                        "UPDATE private_refs SET status='CORRUPT' WHERE secure_ref=?",
                        (record["secure_ref"],),
                    )
                corrupt = security_scan(project, database)
                self.assertEqual(corrupt["status"], "FAIL")
                self.assertIn("corrupt_private_reference", {item["kind"] for item in corrupt["findings"]})
            onboarding.delete(record["secure_ref"], user_confirmed=True)

    def test_private_staging_rejects_project_overlap_and_unsafe_suffixes(self) -> None:
        with project_temp() as temp:
            project = temp / "project"
            project.mkdir()
            database = JobOpsDB(temp / "jobops.db")
            database.initialize()
            script = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
            overlapping = PrivateOnboarding(
                database,
                WindowsDPAPIStore(script, local_app_data=project / "localappdata"),
            )
            with self.assertRaises(JobOpsError) as overlap:
                overlapping.assert_outside_project(project)
            self.assertEqual(overlap.exception.code, "PRIVATE_STORE_PROJECT_OVERLAP")

            safe = PrivateOnboarding(
                database,
                WindowsDPAPIStore(script, local_app_data=temp / "localappdata"),
            )
            safe.assert_outside_project(project)
            record = safe.import_bytes("answer_bank", SENTINEL.encode("utf-8"), synthetic=True)
            with self.assertRaises(JobOpsError) as suffix:
                with safe.staged_file(record["secure_ref"], "../../escape.txt"):
                    pass
            self.assertEqual(suffix.exception.code, "PRIVATE_STAGING_SUFFIX_INVALID")
            with safe.staged_file(record["secure_ref"], ".txt") as staged:
                self.assertEqual(staged.read_bytes(), SENTINEL.encode("utf-8"))
                staged.with_suffix(".sidecar").write_text("synthetic", encoding="utf-8")
                staging_directory = staged.parent
            self.assertFalse(staging_directory.exists())

            with self.assertRaises(JobOpsError) as cleanup_failed:
                with patch("jobops.private_onboarding.shutil.rmtree", side_effect=PermissionError("synthetic lock")):
                    with safe.staging_directory() as locked_staging:
                        (locked_staging / "private.txt").write_text(SENTINEL, encoding="utf-8")
            self.assertEqual(cleanup_failed.exception.code, "PRIVATE_STAGING_CLEANUP_FAILED")
            self.assertTrue(locked_staging.exists())
            safe.clear_staging_residue()
            self.assertFalse(locked_staging.exists())

            residue = safe.store.private_root / "staging" / "crashed-session" / "nested"
            residue.mkdir(parents=True)
            (residue / "material.txt").write_text(SENTINEL, encoding="utf-8")
            cleaned = safe.clear_staging_residue()
            self.assertEqual(cleaned["status"], "PRIVATE_STAGING_CLEAN")
            self.assertGreaterEqual(cleaned["staging_items_deleted"], 3)
            self.assertFalse(any((safe.store.private_root / "staging").iterdir()))

            unsafe = safe.store.private_root / "staging" / "linked-session"
            unsafe.mkdir()
            (unsafe / "material.txt").write_text(SENTINEL, encoding="utf-8")
            with patch("jobops.private_onboarding.has_reparse_component", side_effect=[False, True]):
                with self.assertRaises(JobOpsError) as reparse:
                    safe.clear_staging_residue()
            self.assertEqual(reparse.exception.code, "PRIVATE_STAGING_REPARSE_FORBIDDEN")
            self.assertTrue(unsafe.exists())
            safe.clear_staging_residue()
            safe.delete(record["secure_ref"], user_confirmed=True)


if __name__ == "__main__":
    unittest.main()
