from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from _support import PROJECT, project_temp
from jobops.errors import JobOpsError
from jobops.release_readiness import github_release_gates, release_readiness


class ReleaseReadinessContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
