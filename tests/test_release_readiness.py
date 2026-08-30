from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import Mock, patch

from _support import PROJECT, project_temp
from jobops.errors import JobOpsError
from jobops.release import (
    _external_action_verification_window,
    _independent_qa_matches_release,
    _latest_release_input_mtime,
    _release_test_report_matches_source,
    _skill_validation_passes,
    _source_commit,
    verify_release,
)
from jobops.release_readiness import (
    _local_verification_evidence,
    _source_candidate_status,
    github_release_gates,
    release_readiness,
)
from jobops.runtime_schema import validate_named
from jobops.util import sha256_bytes


def _write_release_candidate(root, *, version: str = "0.6.0", commit: str | None = None) -> dict[str, object]:
    commit = commit or "b" * 40
    artifact_name = f"JobFlow-v{version}-{commit[:12]}-source.zip"
    artifact_payload = b"synthetic deterministic release archive"
    dist = root / "dist"
    dist.mkdir()
    (dist / artifact_name).write_bytes(artifact_payload)
    candidate: dict[str, object] = {
        "schema_version": 1,
        "status": "RELEASE_CANDIDATE_BUILT",
        "version": version,
        "commit": commit,
        "artifact_name": artifact_name,
        "artifact_sha256": sha256_bytes(artifact_payload),
        "artifact_bytes": len(artifact_payload),
        "reproducible_builds": 2,
        "archive": {"status": "PASS", "file_count": 100, "finding_count": 0, "findings": []},
        "source_smoke": {
            "status": "PASS",
            "binding": "127.0.0.1",
            "supported_locales": ["zh", "en"],
            "offline_discovery": "PASS",
            "offline_candidates": 2,
            "snapshot_persisted": False,
            "candidate_queue_mutations": 0,
            "private_values_emitted": 0,
            "external_network_actions": 0,
            "real_external_actions": 0,
            "private_store_health": "PASS",
            "private_ciphertext_files": 0,
            "loopback_requests": 5,
            "security_headers": "PASS",
            "project_state_isolated": True,
            "local_app_data_isolated": True,
        },
        "repository_content_status": "PASS",
        "author_identity_status": "PASS",
        "uploaded": False,
        "external_network_actions": 0,
        "real_external_actions": 0,
    }
    reports = root / "reports"
    reports.mkdir()
    (reports / "release-candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
    return candidate


class ReleaseReadinessContractTests(unittest.TestCase):
    def test_release_external_action_gate_uses_the_current_verification_window(self) -> None:
        baseline = {"attempt_count": 3, "real_external_actions": 2}
        unchanged = _external_action_verification_window(
            {"attempt_count": 3, "real_external_actions": 2},
            baseline,
        )
        self.assertEqual(unchanged["status"], "PASS")
        self.assertEqual(unchanged["attempt_count"], 0)
        self.assertEqual(unchanged["real_external_actions"], 0)
        self.assertEqual(unchanged["lifetime_real_external_actions"], 2)

        new_read_only_attempt = _external_action_verification_window(
            {"attempt_count": 4, "real_external_actions": 2},
            baseline,
        )
        self.assertEqual(new_read_only_attempt["status"], "PASS")
        self.assertEqual(new_read_only_attempt["attempt_count"], 1)
        self.assertEqual(new_read_only_attempt["real_external_actions"], 0)

        new_real_action = _external_action_verification_window(
            {"attempt_count": 4, "real_external_actions": 3},
            baseline,
        )
        self.assertEqual(new_real_action["status"], "FAIL")
        self.assertEqual(new_real_action["real_external_actions"], 1)

    def test_release_external_action_gate_rejects_an_impossible_baseline(self) -> None:
        result = _external_action_verification_window(
            {"attempt_count": 2, "real_external_actions": 1},
            {"attempt_count": 3, "real_external_actions": 2},
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["baseline_valid"])

        for malformed in ("0", 0.0, False, True, None):
            with self.subTest(malformed_count=malformed):
                malformed_result = _external_action_verification_window(
                    {"attempt_count": 2, "real_external_actions": 1},
                    {"attempt_count": malformed, "real_external_actions": 1},
                )
                self.assertEqual(malformed_result["status"], "FAIL")
                self.assertFalse(malformed_result["baseline_valid"])

    def test_source_commit_is_exactly_bound_to_git_head(self) -> None:
        trusted_git = (PROJECT / "synthetic-release-git.exe").resolve()
        completed = Mock(returncode=0, stdout="A" * 40 + "\n")
        with patch(
            "jobops.release.locked_release_git",
            return_value=nullcontext(trusted_git),
        ), patch(
            "jobops.release.sanitized_command_environment",
            return_value={"GIT_CONFIG_GLOBAL": "NUL"},
        ), patch("jobops.release.subprocess.run", return_value=completed) as run:
            self.assertEqual(_source_commit(PROJECT, git_path=trusted_git), "a" * 40)
        run.assert_called_once_with(
            [str(trusted_git), "rev-parse", "HEAD"],
            cwd=PROJECT,
            env={"GIT_CONFIG_GLOBAL": "NUL"},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_source_commit_fails_closed_when_git_identity_is_unavailable(self) -> None:
        trusted_git = (PROJECT / "synthetic-release-git.exe").resolve()
        completed = Mock(returncode=1, stdout="")
        with patch(
            "jobops.release.locked_release_git",
            return_value=nullcontext(trusted_git),
        ), patch(
            "jobops.release.sanitized_command_environment",
            return_value={"GIT_CONFIG_GLOBAL": "NUL"},
        ), patch("jobops.release.subprocess.run", return_value=completed):
            with self.assertRaises(JobOpsError) as raised:
                _source_commit(PROJECT, git_path=trusted_git)
        self.assertEqual(raised.exception.code, "RELEASE_GIT_FAILED")

    def test_standalone_release_evidence_is_bound_and_structurally_valid(self) -> None:
        commit = "a" * 40
        self.assertTrue(
            _release_test_report_matches_source(
                {"status": "PASS", "failed": 0, "source_commit": commit},
                commit,
            )
        )
        for report in (
            {"status": "PASS", "failed": 0},
            {"status": "PASS", "failed": 0, "source_commit": "b" * 40},
            {"status": "PASS", "failed": False, "source_commit": commit},
        ):
            with self.subTest(report=report):
                self.assertFalse(_release_test_report_matches_source(report, commit))

        valid_skill = {
            "status": "PASS",
            "returncode": 0,
            "validator": "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py",
            "output": "Skill is valid!",
        }
        self.assertTrue(_skill_validation_passes(valid_skill))
        for mutation in (
            {**valid_skill, "returncode": 1},
            {**valid_skill, "returncode": False},
            {**valid_skill, "validator": "unknown"},
            {**valid_skill, "output": ""},
        ):
            with self.subTest(skill_report=mutation):
                self.assertFalse(_skill_validation_passes(mutation))

    def test_standalone_verify_release_rejects_a_report_from_another_commit(self) -> None:
        current_commit = "a" * 40
        with project_temp() as root:
            reports = root / "reports"
            reports.mkdir()
            (reports / "release-test-results.json").write_text(
                json.dumps(
                    {"status": "PASS", "failed": 0, "source_commit": "b" * 40}
                ),
                encoding="utf-8",
            )
            with patch("jobops.release._source_commit", return_value=current_commit):
                with self.assertRaises(JobOpsError) as raised:
                    verify_release(root, object())  # type: ignore[arg-type]
            self.assertEqual(raised.exception.code, "RELEASE_TEST_REPORT_SOURCE_MISMATCH")

    def test_release_freshness_covers_companion_scripts_workflows_and_root_launchers(self) -> None:
        with project_temp() as root:
            paths = [
                root / "src" / "baseline.py",
                root / "scripts" / "release.ps1",
                root / "browser-companion" / "background.js",
                root / ".github" / "workflows" / "ci.yml",
                root / "Start JobFlow.cmd",
            ]
            for index, path in enumerate(paths, start=1):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n", encoding="utf-8")
                path.touch()
                timestamp = 1_700_000_000 + index
                import os

                os.utime(path, (timestamp, timestamp))
                self.assertEqual(_latest_release_input_mtime(root), float(timestamp))

    def test_version_metadata_and_changelog_are_consistent(self) -> None:
        import tomllib
        from jobops import __version__

        metadata = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], __version__)
        changelog = (PROJECT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{__version__}]", changelog)
        self.assertIn("release candidate", changelog)

    def test_release_checklist_requires_identity_qa_tag_and_upload_authorization(self) -> None:
        checklist = (PROJECT / "docs" / "release-checklist.md").read_text(encoding="utf-8")
        for requirement in ("author identity", "independent QA", "annotated or signed", "explicit user authorization"):
            self.assertIn(requirement, checklist)
        self.assertIn("Real external actions must remain 0", checklist)

    def test_unconfirmed_github_release_decisions_remain_explicit_blockers(self) -> None:
        with project_temp() as root:
            config = root / "config"
            config.mkdir()
            companion = root / "browser-companion"
            companion.mkdir()
            (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_owner": "synthetic-owner",
                        "repository_name": "JobFlow",
                        "visibility": "PUBLIC",
                        "description": "Synthetic release test",
                        "topics": ["ai", "job-search", "privacy"],
                        "metadata_confirmed_by_user": False,
                        "private_vulnerability_reporting_confirmed": False,
                        "sanitized_screenshots_approved": False,
                        "clean_windows_profile_tested": False,
                        "browser_companion_store_versions_verified": False,
                    }
                ),
                encoding="utf-8",
            )
            gates = github_release_gates(root)
            self.assertEqual(
                gates,
                {
                    "repository_metadata": "PENDING",
                    "private_vulnerability_reporting": "PENDING",
                    "sanitized_screenshots": "PENDING",
                    "clean_windows_profile": "PENDING",
                    "browser_companion_stores": "PENDING",
                },
            )

    def test_release_decisions_pass_only_with_complete_metadata_and_explicit_evidence(self) -> None:
        today = date(2026, 8, 27)
        commit = "b" * 40
        with project_temp() as root:
            (root / "pyproject.toml").write_text(
                '[project]\nname = "synthetic-jobflow"\nversion = "0.6.0"\n',
                encoding="utf-8",
            )
            config = root / "config"
            config.mkdir()
            companion = root / "browser-companion"
            companion.mkdir()
            (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_owner": "synthetic-owner",
                        "repository_name": "JobFlow",
                        "visibility": "PUBLIC",
                        "description": "Synthetic release test",
                        "topics": ["ai", "job-search", "privacy"],
                        "metadata_confirmed_by_user": True,
                        "private_vulnerability_reporting_confirmed": True,
                        "sanitized_screenshots_approved": True,
                        "clean_windows_profile_tested": True,
                        "clean_windows_profile_tested_app_version": "0.6.0",
                        "clean_windows_profile_tested_companion_version": "0.9.2",
                        "clean_windows_profile_tested_commit": commit,
                        "clean_windows_profile_tested_at": today.isoformat(),
                        "browser_companion_store_versions_verified": True,
                        "browser_companion_store_versions_verified_at": "2026-08-27",
                        "browser_companion_chrome_published_version": "0.9.2",
                        "browser_companion_edge_published_version": "0.9.2",
                    }
                ),
                encoding="utf-8",
            )
            with patch("jobops.release_readiness._today", return_value=today):
                self.assertEqual(
                    github_release_gates(root, expected_commit=commit, expected_version="0.6.0"),
                    {
                        "repository_metadata": "CONFIRMED",
                        "private_vulnerability_reporting": "CONFIRMED",
                        "sanitized_screenshots": "APPROVED",
                        "clean_windows_profile": "PASS",
                        "browser_companion_stores": "PASS",
                    },
                )

    def test_clean_windows_evidence_is_commit_version_and_date_bound(self) -> None:
        today = date(2026, 8, 27)
        commit = "b" * 40
        base = {
            "clean_windows_profile_tested": True,
            "clean_windows_profile_tested_app_version": "0.6.0",
            "clean_windows_profile_tested_companion_version": "0.9.2",
            "clean_windows_profile_tested_commit": commit,
            "clean_windows_profile_tested_at": today.isoformat(),
        }
        cases = (
            ({}, None, "INVALID"),
            (base, None, "OUTDATED"),
            ({**base, "clean_windows_profile_tested_commit": "c" * 40}, commit, "OUTDATED"),
            ({**base, "clean_windows_profile_tested_app_version": "0.5.0"}, commit, "OUTDATED"),
            ({**base, "clean_windows_profile_tested_companion_version": "0.9.1"}, commit, "OUTDATED"),
            ({**base, "clean_windows_profile_tested_at": (today - timedelta(days=31)).isoformat()}, commit, "OUTDATED"),
            (base, commit, "PASS"),
        )
        for values, expected_commit, expected_status in cases:
            with self.subTest(expected=expected_status, values=values), project_temp() as root:
                config = root / "config"
                config.mkdir()
                companion = root / "browser-companion"
                companion.mkdir()
                (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
                payload = {"clean_windows_profile_tested": True}
                payload.update(values)
                (config / "github-release.json").write_text(json.dumps(payload), encoding="utf-8")
                with patch("jobops.release_readiness._today", return_value=today):
                    gates = github_release_gates(
                        root,
                        expected_commit=expected_commit,
                        expected_version="0.6.0",
                    )
                self.assertEqual(gates["clean_windows_profile"], expected_status)

    def test_public_repository_metadata_rejects_bilingual_or_non_english_description_fields(self) -> None:
        with project_temp() as root:
            config = root / "config"
            config.mkdir()
            companion = root / "browser-companion"
            companion.mkdir()
            (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_owner": "synthetic-owner",
                        "repository_name": "JobFlow",
                        "visibility": "PUBLIC",
                        "description": "Synthetic release test",
                        "description_zh": "不应进入公开元数据",
                        "topics": ["ai", "job-search", "privacy"],
                        "metadata_confirmed_by_user": True,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(github_release_gates(root)["repository_metadata"], "PENDING")

    def test_public_repository_metadata_requires_ascii_safe_approved_description_text(self) -> None:
        rejected_descriptions = (
            None,
            "",
            "2026-08-27",
            "Synthetic release ｔｅｓｔ",
            "Synthetic release тест",
            "Synthetic release テスト",
            "Synthetic release 테스트",
        )
        for description in rejected_descriptions:
            with self.subTest(description=description), project_temp() as root:
                config = root / "config"
                config.mkdir()
                companion = root / "browser-companion"
                companion.mkdir()
                (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
                (config / "github-release.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "repository_owner": "synthetic-owner",
                            "repository_name": "JobFlow",
                            "visibility": "PUBLIC",
                            "description": description,
                            "topics": ["ai", "job-search", "privacy"],
                            "metadata_confirmed_by_user": True,
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(github_release_gates(root)["repository_metadata"], "PENDING")

    def test_public_repository_metadata_requires_native_string_identity_and_integer_schema(self) -> None:
        invalid_overrides = (
            {"repository_owner": None},
            {"repository_name": None},
            {"visibility": None},
            {"schema_version": True},
            {"repository_owner": " synthetic-owner"},
            {"repository_name": "JobFlow "},
            {"visibility": "public"},
            {"description": " Synthetic release test"},
        )
        for override in invalid_overrides:
            with self.subTest(override=override), project_temp() as root:
                config = root / "config"
                config.mkdir()
                companion = root / "browser-companion"
                companion.mkdir()
                (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
                metadata = {
                    "schema_version": 1,
                    "repository_owner": "synthetic-owner",
                    "repository_name": "JobFlow",
                    "visibility": "PUBLIC",
                    "description": "Synthetic release test",
                    "topics": ["ai", "job-search", "privacy"],
                    "metadata_confirmed_by_user": True,
                }
                metadata.update(override)
                (config / "github-release.json").write_text(json.dumps(metadata), encoding="utf-8")
                self.assertEqual(github_release_gates(root)["repository_metadata"], "PENDING")

    def test_public_repository_topics_require_native_strings(self) -> None:
        with project_temp() as root:
            config = root / "config"
            config.mkdir()
            companion = root / "browser-companion"
            companion.mkdir()
            (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_owner": "synthetic-owner",
                        "repository_name": "JobFlow",
                        "visibility": "PUBLIC",
                        "description": "Synthetic release test",
                        "topics": ["ai", 123, "privacy"],
                        "metadata_confirmed_by_user": True,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(github_release_gates(root)["repository_metadata"], "PENDING")

    def test_private_repository_visibility_never_satisfies_public_release_metadata(self) -> None:
        with project_temp() as root:
            config = root / "config"
            config.mkdir()
            companion = root / "browser-companion"
            companion.mkdir()
            (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_owner": "synthetic-owner",
                        "repository_name": "JobFlow",
                        "visibility": "PRIVATE",
                        "description": "Synthetic release test",
                        "topics": ["ai", "job-search", "privacy"],
                        "metadata_confirmed_by_user": True,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(github_release_gates(root)["repository_metadata"], "PENDING")

    def test_source_candidate_is_bound_to_the_exact_archive_and_release_identity(self) -> None:
        commit = "b" * 40
        version = "0.6.0"
        with project_temp() as root:
            candidate = _write_release_candidate(root, version=version, commit=commit)
            with patch("jobops.release_readiness.os.fstat", wraps=__import__("os").fstat) as fstat_mock:
                self.assertEqual(_source_candidate_status(root, commit, version), "PASS")
            self.assertEqual(fstat_mock.call_count, 1)

            artifact = root / "dist" / str(candidate["artifact_name"])
            artifact.write_bytes(b"changed after candidate verification")
            self.assertEqual(_source_candidate_status(root, commit, version), "FAIL")

        with project_temp() as root:
            _write_release_candidate(root, version=version, commit=commit)
            self.assertEqual(_source_candidate_status(root, "c" * 40, version), "STALE")
            self.assertEqual(_source_candidate_status(root, commit, "0.6.1"), "FAIL")

        with project_temp() as root:
            _write_release_candidate(root, version=version, commit=commit)
            with patch("jobops.release_readiness.has_reparse_component", return_value=True):
                self.assertEqual(_source_candidate_status(root, commit, version), "FAIL")

    def test_source_candidate_rejects_every_required_safety_proof_when_malformed(self) -> None:
        commit = "b" * 40
        version = "0.6.0"
        mutations = {
            "nested_archive_not_object": lambda value: value.__setitem__("archive", "PASS"),
            "nested_smoke_not_object": lambda value: value.__setitem__("source_smoke", []),
            "archive_findings_nonzero": lambda value: value["archive"].update({"finding_count": 1}),
            "archive_findings_hidden": lambda value: value["archive"].update({"findings": [{"kind": "secret"}]}),
            "reproducible_builds_coerced": lambda value: value.__setitem__("reproducible_builds", "2"),
            "artifact_bytes_coerced": lambda value: value.__setitem__("artifact_bytes", True),
            "source_smoke_failed": lambda value: value["source_smoke"].update({"status": "FAIL"}),
            "source_smoke_network_action": lambda value: value["source_smoke"].update({"external_network_actions": 1}),
            "source_smoke_real_action": lambda value: value["source_smoke"].update({"real_external_actions": 1}),
            "top_level_network_action": lambda value: value.update({"external_network_actions": 1}),
            "top_level_real_action": lambda value: value.update({"real_external_actions": 1}),
            "uploaded": lambda value: value.update({"uploaded": True}),
            "unexpected_property": lambda value: value.update({"unreviewed": True}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), project_temp() as root:
                candidate = _write_release_candidate(root, version=version, commit=commit)
                mutate(candidate)
                (root / "reports" / "release-candidate.json").write_text(
                    json.dumps(candidate), encoding="utf-8"
                )
                self.assertEqual(_source_candidate_status(root, commit, version), "FAIL")

    def test_public_ready_requires_archive_clean_tree_and_all_detailed_gates(self) -> None:
        version = "0.6.0"
        commit = "b" * 40
        passing_manual = {
            "repository_metadata": "CONFIRMED",
            "private_vulnerability_reporting": "CONFIRMED",
            "sanitized_screenshots": "APPROVED",
            "clean_windows_profile": "PASS",
            "browser_companion_stores": "PASS",
        }
        passing_attestation = {
            "schema_version": 1,
            "status": "PASS",
            "release_attestation_status": "PASS",
            "clean_windows_evidence_status": "PASS",
            "runtime_closure_status": "ATTESTED",
            "version": version,
            "source_commit": commit,
            "failure_code": None,
            "signature_verified": True,
            "external_actions": 0,
            "real_external_actions": 0,
        }

        def fake_git(_project, *arguments):
            if arguments == ("rev-parse", "HEAD"):
                return commit
            if arguments == ("status", "--porcelain", "--untracked-files=all"):
                return ""
            if arguments == ("tag", "--points-at", "HEAD"):
                return f"v{version}"
            raise AssertionError(arguments)

        with project_temp() as root:
            (root / "pyproject.toml").write_text(
                f'[project]\nname = "synthetic-jobflow"\nversion = "{version}"\n', encoding="utf-8"
            )
            (root / "CHANGELOG.md").write_text(f"## [{version}]\n", encoding="utf-8")
            candidate = _write_release_candidate(root, version=version, commit=commit)
            repository = {
                "status": "PASS",
                "author_identity": {"status": "PASS"},
                "public_release_blockers": [],
            }
            with (
                patch("jobops.release_readiness.__version__", version),
                patch("jobops.release_readiness._git", side_effect=fake_git),
                patch("jobops.release_readiness.verify_public_repository", return_value=repository),
                patch("jobops.release_readiness._local_verification_evidence", return_value=("PASS", True)),
                patch("jobops.release_readiness.github_release_gates", return_value=passing_manual),
                patch(
                    "jobops.release_readiness.verify_public_release_attestation",
                    return_value=passing_attestation,
                ),
                patch("jobops.release_readiness.validate_named", side_effect=lambda _n, value, _s: value),
            ):
                result = release_readiness(root, object())  # type: ignore[arg-type]
                self.assertEqual(result["status"], "PUBLIC_RELEASE_READY")
                self.assertTrue(result["public_release_ready"])
                self.assertEqual(result["runtime_closure_status"], "ATTESTED")
                self.assertEqual(result["release_attestation_status"], "PASS")
                self.assertEqual(result["clean_windows_evidence_status"], "PASS")
                self.assertEqual(result["blockers"], [])

                artifact = root / "dist" / str(candidate["artifact_name"])
                artifact.unlink()
                missing_archive = release_readiness(root, object())  # type: ignore[arg-type]
                self.assertEqual(missing_archive["status"], "PUBLIC_RELEASE_BLOCKED")
                self.assertEqual(missing_archive["source_candidate_status"], "FAIL")
                self.assertIn("SOURCE_CANDIDATE_FAIL", missing_archive["blockers"])

    def test_readiness_schema_requires_runtime_closure_blocker(self) -> None:
        valid = {
            "schema_version": 1,
            "status": "PUBLIC_RELEASE_BLOCKED",
            "public_release_ready": False,
            "runtime_closure_status": "UNATTESTED",
            "release_attestation_status": "MISSING",
            "clean_windows_evidence_status": "NOT_CHECKED",
            "release_attestation_failure_code": "RELEASE_ATTESTATION_MISSING",
            "version": "0.6.0",
            "head_commit": "b" * 40,
            "worktree_clean": True,
            "version_consistent": True,
            "local_verification_status": "PASS",
            "public_repository_status": "PASS",
            "source_candidate_status": "PASS",
            "independent_qa_fresh": True,
            "author_identity_status": "PASS",
            "release_tag_status": "PRESENT",
            "manual_release_gates": {
                "repository_metadata": "CONFIRMED",
                "private_vulnerability_reporting": "CONFIRMED",
                "sanitized_screenshots": "APPROVED",
                "clean_windows_profile": "PENDING",
                "browser_companion_stores": "PASS",
            },
            "blockers": [
                "RELEASE_ATTESTATION_MISSING",
                "RELEASE_RUNTIME_CLOSURE_UNATTESTED",
            ],
            "upload_performed": False,
            "network_actions": 0,
            "real_external_actions": 0,
            "next_safe_action": "request explicit GitHub upload authorization",
        }
        validate_named("release-readiness", valid, PROJECT / "schemas")

        missing_runtime_gate = deepcopy(valid)
        missing_runtime_gate["blockers"] = ["RELEASE_ATTESTATION_MISSING"]
        with self.assertRaises(JobOpsError):
            validate_named("release-readiness", missing_runtime_gate, PROJECT / "schemas")

        ready = deepcopy(valid)
        ready.update(
            {
                "status": "PUBLIC_RELEASE_READY",
                "public_release_ready": True,
                "runtime_closure_status": "ATTESTED",
                "release_attestation_status": "PASS",
                "clean_windows_evidence_status": "PASS",
                "release_attestation_failure_code": None,
                "blockers": [],
            }
        )
        ready["manual_release_gates"]["clean_windows_profile"] = "PASS"
        validate_named("release-readiness", ready, PROJECT / "schemas")

    def test_release_is_blocked_when_a_store_version_lags_the_required_companion(self) -> None:
        with project_temp() as root:
            config = root / "config"
            config.mkdir()
            companion = root / "browser-companion"
            companion.mkdir()
            (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "browser_companion_store_versions_verified": True,
                        "browser_companion_store_versions_verified_at": "2026-08-27",
                        "browser_companion_chrome_published_version": "0.9.1",
                        "browser_companion_edge_published_version": "0.9.2",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(github_release_gates(root)["browser_companion_stores"], "OUTDATED")

    def test_store_verification_rejects_invalid_or_future_dates(self) -> None:
        for verified_at in (None, "2026-02-30", "9999-01-01", "20260827", " 2026-08-27"):
            with self.subTest(verified_at=verified_at), project_temp() as root:
                config = root / "config"
                config.mkdir()
                companion = root / "browser-companion"
                companion.mkdir()
                (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
                (config / "github-release.json").write_text(
                    json.dumps(
                        {
                            "browser_companion_store_versions_verified": True,
                            "browser_companion_store_versions_verified_at": verified_at,
                            "browser_companion_chrome_published_version": "0.9.2",
                            "browser_companion_edge_published_version": "0.9.2",
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(github_release_gates(root)["browser_companion_stores"], "INVALID")

    def test_store_verification_date_has_a_thirty_day_freshness_boundary(self) -> None:
        today = date(2026, 8, 27)
        for age_days, expected in ((30, "PASS"), (31, "OUTDATED")):
            with self.subTest(age_days=age_days), project_temp() as root:
                config = root / "config"
                config.mkdir()
                companion = root / "browser-companion"
                companion.mkdir()
                (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
                (config / "github-release.json").write_text(
                    json.dumps(
                        {
                            "browser_companion_store_versions_verified": True,
                            "browser_companion_store_versions_verified_at": (today - timedelta(days=age_days)).isoformat(),
                            "browser_companion_chrome_published_version": "0.9.2",
                            "browser_companion_edge_published_version": "0.9.2",
                        }
                    ),
                    encoding="utf-8",
                )
                with patch("jobops.release_readiness._today", return_value=today):
                    self.assertEqual(github_release_gates(root)["browser_companion_stores"], expected)

    def test_store_verification_rejects_malformed_published_semver(self) -> None:
        for chrome_version, edge_version in (
            (None, "0.9.2"),
            ("v0.9.2", "0.9.2"),
            ("0.9", "0.9.2"),
            ("0.09.2", "0.9.2"),
            (" 0.9.2", "0.9.2"),
            ("0.9.2", "0.9.2.1"),
        ):
            with self.subTest(chrome=chrome_version, edge=edge_version), project_temp() as root:
                config = root / "config"
                config.mkdir()
                companion = root / "browser-companion"
                companion.mkdir()
                (companion / "manifest.json").write_text(json.dumps({"version": "0.9.2"}), encoding="utf-8")
                (config / "github-release.json").write_text(
                    json.dumps(
                        {
                            "browser_companion_store_versions_verified": True,
                            "browser_companion_store_versions_verified_at": "2026-08-27",
                            "browser_companion_chrome_published_version": chrome_version,
                            "browser_companion_edge_published_version": edge_version,
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(github_release_gates(root)["browser_companion_stores"], "INVALID")

    def test_extracted_source_without_git_returns_a_safe_release_block(self) -> None:
        with patch(
            "jobops.release_readiness._git",
            side_effect=JobOpsError("RELEASE_GIT_FAILED", "synthetic missing Git metadata"),
        ):
            result = release_readiness(PROJECT, object())  # type: ignore[arg-type]
        self.assertEqual(result["status"], "PUBLIC_RELEASE_BLOCKED")
        self.assertEqual(result["head_commit"], "0" * 40)
        self.assertFalse(result["worktree_clean"])
        self.assertIn("GIT_REPOSITORY_REQUIRED", result["blockers"])
        self.assertEqual(result["manual_release_gates"], github_release_gates(PROJECT))
        self.assertFalse(result["upload_performed"])
        self.assertEqual(result["network_actions"], 0)
        self.assertEqual(result["real_external_actions"], 0)

    def test_local_release_evidence_is_commit_bound_and_ignores_operational_history(self) -> None:
        with project_temp() as root:
            (root / "src").mkdir()
            (root / "src" / "release-input.py").write_text("VALUE = 1\n", encoding="utf-8")
            reports = root / "reports"
            reports.mkdir()
            output_sha256 = "sha256:" + "a" * 64
            test_report = {
                "status": "PASS",
                "passed": 517,
                "failed": 0,
                "schema_count": 52,
                "output_sha256": output_sha256,
            }
            (reports / "release-test-results.json").write_text(json.dumps(test_report), encoding="utf-8")
            checks = {
                "tests": True,
                "skill": True,
                "knowledge": True,
                "security": True,
                "external_actions": True,
                "database": True,
                "synthetic_private_purged": True,
                "private_store_consistent": True,
                "public_repository": True,
                "independent_qa": False,
            }
            commit = "b" * 40
            checkpoint = {
                "status": "PASS",
                "verification_scope": "LOCAL_DEVELOPMENT",
                "source_commit": commit,
                "real_external_actions": 0,
                "checks": checks,
                "tests": test_report,
            }
            (reports / "checkpoint-final.json").write_text(json.dumps(checkpoint), encoding="utf-8")

            self.assertEqual(_local_verification_evidence(root, commit), ("PASS", False))
            self.assertEqual(_local_verification_evidence(root, "c" * 40), ("MISSING_OR_STALE", False))

            checkpoint["real_external_actions"] = 1
            (reports / "checkpoint-final.json").write_text(json.dumps(checkpoint), encoding="utf-8")
            self.assertEqual(_local_verification_evidence(root, commit), ("FAIL", False))

    def test_local_release_evidence_rejects_coercible_counts_and_malformed_reports(self) -> None:
        commit = "b" * 40
        output_sha256 = "sha256:" + "a" * 64
        for bad_failed, bad_actions in ((False, 0), ("0", 0), (0.0, 0), (0, False), (0, "0"), (0, 0.0)):
            with self.subTest(failed=bad_failed, actions=bad_actions), project_temp() as root:
                (root / "src").mkdir()
                (root / "src" / "release-input.py").write_text("VALUE = 1\n", encoding="utf-8")
                reports = root / "reports"
                reports.mkdir()
                test_report = {
                    "status": "PASS",
                    "passed": 1,
                    "failed": bad_failed,
                    "schema_count": 1,
                    "output_sha256": output_sha256,
                }
                checkpoint = {
                    "status": "PASS",
                    "verification_scope": "LOCAL_DEVELOPMENT",
                    "source_commit": commit,
                    "real_external_actions": bad_actions,
                    "checks": {
                        "tests": True,
                        "skill": True,
                        "knowledge": True,
                        "security": True,
                        "external_actions": True,
                        "database": True,
                        "synthetic_private_purged": True,
                        "private_store_consistent": True,
                        "public_repository": True,
                        "independent_qa": False,
                    },
                    "tests": test_report,
                }
                (reports / "release-test-results.json").write_text(json.dumps(test_report), encoding="utf-8")
                (reports / "checkpoint-final.json").write_text(json.dumps(checkpoint), encoding="utf-8")
                expected = "MISSING_OR_STALE" if bad_failed != 0 or type(bad_failed) is not int else "FAIL"
                self.assertEqual(_local_verification_evidence(root, commit), (expected, False))

    def test_local_release_evidence_accepts_only_literal_true_for_independent_qa(self) -> None:
        commit = "b" * 40
        output_sha256 = "sha256:" + "a" * 64
        malformed_values = ("true", "false", 1, 0, 1.0, [], [True], {}, {"passed": True})
        for independent_qa in (*malformed_values, False, True):
            with self.subTest(independent_qa=independent_qa), project_temp() as root:
                (root / "src").mkdir()
                (root / "src" / "release-input.py").write_text("VALUE = 1\n", encoding="utf-8")
                reports = root / "reports"
                reports.mkdir()
                test_report = {
                    "status": "PASS",
                    "passed": 1,
                    "failed": 0,
                    "schema_count": 1,
                    "output_sha256": output_sha256,
                }
                checkpoint = {
                    "status": "PASS",
                    "verification_scope": "LOCAL_DEVELOPMENT",
                    "source_commit": commit,
                    "real_external_actions": 0,
                    "checks": {
                        "tests": True,
                        "skill": True,
                        "knowledge": True,
                        "security": True,
                        "external_actions": True,
                        "database": True,
                        "synthetic_private_purged": True,
                        "private_store_consistent": True,
                        "public_repository": True,
                        "independent_qa": independent_qa,
                    },
                    "tests": test_report,
                }
                (reports / "release-test-results.json").write_text(json.dumps(test_report), encoding="utf-8")
                (reports / "checkpoint-final.json").write_text(json.dumps(checkpoint), encoding="utf-8")
                self.assertEqual(
                    _local_verification_evidence(root, commit),
                    ("PASS", independent_qa is True),
                )

    def test_independent_qa_is_bound_to_exact_commit_and_archive(self) -> None:
        commit = "b" * 40
        digest = "sha256:" + "c" * 64
        tests = {"status": "PASS", "passed": 518, "failed": 0, "schema_count": 52}
        candidate = {
            "status": "RELEASE_CANDIDATE_BUILT",
            "commit": commit,
            "artifact_sha256": digest,
            "reproducible_builds": 2,
            "archive": {"status": "PASS", "finding_count": 0},
            "external_network_actions": 0,
            "real_external_actions": 0,
        }
        independent = {
            "status": "PASS",
            "qa_mode": "PRIMARY_AGENT_ISOLATED_READ_ONLY_FROZEN_RELEASE_ARCHIVE",
            "source_tree_modified_by_qa": False,
            "archive": {
                "status": "PASS",
                "commit": commit,
                "sha256": digest.removeprefix("sha256:").upper(),
                "byte_reproducible_from_frozen_clone": True,
                "candidate_findings": 0,
            },
            "source_freeze": {
                "status": "UNCHANGED",
                "head": commit,
                "product_code_changed_since_full_isolated_regression": False,
            },
            "tests": {"status": "PASS", "passed": 518, "failed": 0},
            "schemas": {"status": "PASS", "valid": 52, "total": 52},
            "knowledge": {"status": "UNCHANGED", "write_operations": 0},
            "external_actions": {
                "status": "PASS",
                "qa_real_external_actions": 0,
                "isolated_candidate_real_external_actions": 0,
                "isolated_candidate_external_network_actions": 0,
                "real_recruiting_sites_visited": 0,
            },
            "security_scan": {
                "status": "PASS",
                "isolated_candidate_findings": 0,
                "public_tree_findings": 0,
                "public_history_findings": 0,
                "private_staging_files": 0,
                "private_ciphertext_integrity_failures": 0,
            },
            "p0_open": 0,
            "p1_open": 0,
            "must_fix_open": 0,
        }

        self.assertTrue(_independent_qa_matches_release(independent, candidate, tests))
        independent["archive"]["commit"] = "d" * 40
        self.assertFalse(_independent_qa_matches_release(independent, candidate, tests))
        independent["archive"]["commit"] = commit
        independent["archive"]["sha256"] = "e" * 64
        self.assertFalse(_independent_qa_matches_release(independent, candidate, tests))
        independent["archive"]["sha256"] = digest
        valid_actions = deepcopy(independent["external_actions"])
        independent["external_actions"]["qa_real_external_actions"] = 1
        self.assertFalse(_independent_qa_matches_release(independent, candidate, tests))
        independent["external_actions"] = "malformed"
        self.assertFalse(_independent_qa_matches_release(independent, candidate, tests))
        independent["external_actions"] = valid_actions

        for malformed in ("0", 0.0, False, True):
            with self.subTest(malformed_count=malformed):
                malformed_independent = deepcopy(independent)
                malformed_independent["external_actions"] = {
                    "status": "PASS",
                    "qa_real_external_actions": malformed,
                    "isolated_candidate_real_external_actions": 0,
                    "isolated_candidate_external_network_actions": 0,
                    "real_recruiting_sites_visited": 0,
                }
                self.assertFalse(_independent_qa_matches_release(malformed_independent, candidate, tests))

        malformed_candidate = deepcopy(candidate)
        malformed_candidate["reproducible_builds"] = "2"
        self.assertFalse(_independent_qa_matches_release(independent, malformed_candidate, tests))


if __name__ == "__main__":
    unittest.main()
