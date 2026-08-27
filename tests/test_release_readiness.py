from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from _support import PROJECT, project_temp
from jobops.errors import JobOpsError
from jobops.release import _external_action_verification_window, _independent_qa_matches_release, _source_commit
from jobops.release_readiness import _local_verification_evidence, github_release_gates, release_readiness


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

    def test_source_commit_is_exactly_bound_to_git_head(self) -> None:
        completed = Mock(returncode=0, stdout="A" * 40 + "\n")
        with patch("jobops.release.subprocess.run", return_value=completed) as run:
            self.assertEqual(_source_commit(PROJECT), "a" * 40)
        run.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_source_commit_fails_closed_when_git_identity_is_unavailable(self) -> None:
        completed = Mock(returncode=1, stdout="")
        with patch("jobops.release.subprocess.run", return_value=completed):
            with self.assertRaises(JobOpsError) as raised:
                _source_commit(PROJECT)
        self.assertEqual(raised.exception.code, "RELEASE_GIT_FAILED")

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
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_owner": "synthetic-owner",
                        "repository_name": "JobFlow",
                        "visibility": "PUBLIC",
                        "description_zh": "合成发布测试",
                        "description_en": "Synthetic release test",
                        "topics": ["ai", "job-search", "privacy"],
                        "metadata_confirmed_by_user": False,
                        "private_vulnerability_reporting_confirmed": False,
                        "sanitized_screenshots_approved": False,
                        "clean_windows_profile_tested": False,
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
                },
            )

    def test_release_decisions_pass_only_with_complete_metadata_and_explicit_evidence(self) -> None:
        with project_temp() as root:
            config = root / "config"
            config.mkdir()
            (config / "github-release.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository_owner": "synthetic-owner",
                        "repository_name": "JobFlow",
                        "visibility": "PUBLIC",
                        "description_zh": "合成发布测试",
                        "description_en": "Synthetic release test",
                        "topics": ["ai", "job-search", "privacy"],
                        "metadata_confirmed_by_user": True,
                        "private_vulnerability_reporting_confirmed": True,
                        "sanitized_screenshots_approved": True,
                        "clean_windows_profile_tested": True,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                github_release_gates(root),
                {
                    "repository_metadata": "CONFIRMED",
                    "private_vulnerability_reporting": "CONFIRMED",
                    "sanitized_screenshots": "APPROVED",
                    "clean_windows_profile": "PASS",
                },
            )

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
        independent["external_actions"]["qa_real_external_actions"] = 1
        self.assertFalse(_independent_qa_matches_release(independent, candidate, tests))
        independent["external_actions"] = "malformed"
        self.assertFalse(_independent_qa_matches_release(independent, candidate, tests))


if __name__ == "__main__":
    unittest.main()
