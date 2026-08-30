from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT, project_temp
from jobops.public_release import (
    validate_public_paths,
    validate_public_text,
    verify_author_identity,
    verify_public_history,
    verify_public_repository,
)
from jobops.release_toolchain import sanitized_command_environment


class PublicReleaseBoundaryTests(unittest.TestCase):
    def _author_result(self, root: Path, policy: dict[str, object], raw: bytes) -> dict[str, object]:
        config = root / "config"
        config.mkdir(exist_ok=True)
        (config / "public-release.json").write_text(json.dumps(policy), encoding="utf-8")
        with patch("jobops.public_release._git", return_value=raw):
            return verify_author_identity(root)

    def test_repository_boundary_never_claims_authoritative_release_readiness(self) -> None:
        trusted_git = Path("C:/synthetic-release-git/git.exe")
        with (
            patch(
                "jobops.public_release.locked_release_git",
                return_value=nullcontext(trusted_git),
            ),
            patch("jobops.public_release.verify_public_tree", return_value={"status": "PASS"}),
            patch("jobops.public_release.verify_public_history", return_value={"status": "PASS"}),
            patch(
                "jobops.public_release.verify_author_identity",
                return_value={"status": "PASS"},
            ),
        ):
            result = verify_public_repository(Path("synthetic-public-repository"))
        self.assertEqual(result["readiness_scope"], "PUBLIC_REPOSITORY_BOUNDARY_ONLY")
        self.assertTrue(result["public_repository_ready"])
        self.assertFalse(result["public_release_ready"])

    def test_runtime_and_private_files_cannot_be_tracked(self) -> None:
        findings = validate_public_paths(
            [
                "state/jobops.db",
                "state/onboarding-center-index.json",
                "reports/checkpoint-final.json",
                "workspace/jobs/JOB-1/raw/page.html",
                "private.dpapi",
                "export.zip",
                "src/jobops/__pycache__/cli.pyc",
            ]
        )
        kinds = {item["kind"] for item in findings}
        self.assertIn("runtime_state_tracked", kinds)
        self.assertIn("private_or_generated_file_tracked", kinds)
        self.assertIn("generated_path_tracked", kinds)

    def test_only_empty_runtime_sentinels_are_public(self) -> None:
        sentinels = [
            "state/.gitkeep",
            "reports/.gitkeep",
            "workspace/inbox/.gitkeep",
            "workspace/jobs/.gitkeep",
            "workspace/review-packets/.gitkeep",
        ]
        self.assertEqual(validate_public_paths(sentinels), [])

    def test_browser_companion_installation_bindings_are_never_public_paths(self) -> None:
        paths = [
            "browser-companion/binding.json",
            "BrowserCompanion/binding.json",
            "browser-companion-binding.json",
            "nested/.browser-companion-binding-synthetic.tmp",
        ]
        findings = validate_public_paths(paths)
        self.assertEqual(
            {(item["kind"], item["path"]) for item in findings},
            {("browser_companion_binding_tracked", path) for path in paths},
        )

    def test_project_has_no_checked_in_git_metadata_before_initialization_fixture(self) -> None:
        self.assertTrue((PROJECT / ".jobops-root").is_file())

    def test_public_text_rejects_identity_values_but_allows_reserved_examples(self) -> None:
        self.assertEqual(validate_public_text("safe.md", "contact@example.test"), [])
        self.assertEqual(validate_public_text("attestation.json", "thomas@python.org"), [])
        unsafe_email = "contact" + chr(64) + "personal.invalid"
        findings = validate_public_text("unsafe.md", unsafe_email)
        self.assertEqual(findings, [{"kind": "email", "path": "unsafe.md"}])
        binding = '{"secret_b64url":"' + "A" * 43 + '"}'
        self.assertEqual(
            validate_public_text("renamed.json", binding),
            [{"kind": "browser_companion_binding_secret", "path": "renamed.json"}],
        )

    def test_author_identity_requires_recognized_github_noreply_identity_and_valid_log(self) -> None:
        policy = {"schema_version": 1, "author_identity_policy": "NOREPLY_ONLY"}
        with project_temp() as root:
            valid = self._author_result(
                root,
                policy,
                b"Synthetic\x1f182967849+maintainer@users.noreply.github.com\x1fSynthetic\x1fmaintainer@users.noreply.github.com\x1e",
            )
            self.assertEqual(valid["status"], "PASS")
            service_noreply = b"noreply" + bytes([64]) + b"github.com"
            service = self._author_result(
                root,
                policy,
                b"Synthetic\x1f" + service_noreply + b"\x1fSynthetic\x1f" + service_noreply + b"\x1e",
            )
            self.assertEqual(service["status"], "PASS")
            impersonated = b"maintainer" + bytes([64]) + b"github.com"
            untrusted_service = self._author_result(
                root,
                policy,
                b"Synthetic\x1f" + impersonated + b"\x1fSynthetic\x1f" + impersonated + b"\x1e",
            )
            self.assertEqual(untrusted_service["status"], "REVIEW_REQUIRED")
            evil_email = b"maintainer" + bytes([64]) + b"noreply.evil"
            evil = self._author_result(
                root,
                policy,
                b"Synthetic\x1f" + evil_email + b"\x1fSynthetic\x1f" + evil_email + b"\x1e",
            )
            self.assertEqual(evil["status"], "REVIEW_REQUIRED")
            self.assertEqual(evil["non_noreply_identity_count"], 1)
            malformed = self._author_result(root, policy, b"not-a-four-field-record\x1e")
            self.assertEqual(malformed["status"], "REVIEW_REQUIRED")
            self.assertEqual(malformed["malformed_record_count"], 1)

    def test_public_email_policy_requires_an_explicit_exact_allowlist(self) -> None:
        raw = b"Synthetic\x1fmaintainer@example.test\x1fSynthetic\x1fmaintainer@example.test\x1e"
        with project_temp() as root:
            missing = self._author_result(
                root,
                {"schema_version": 1, "author_identity_policy": "PUBLIC_EMAIL_APPROVED"},
                raw,
            )
            self.assertEqual(missing["status"], "REVIEW_REQUIRED")
            approved = self._author_result(
                root,
                {
                    "schema_version": 1,
                    "author_identity_policy": "PUBLIC_EMAIL_APPROVED",
                    "approved_public_emails": ["maintainer@example.test"],
                },
                raw,
            )
            self.assertEqual(approved["status"], "PASS")
            unlisted = self._author_result(
                root,
                {
                    "schema_version": 1,
                    "author_identity_policy": "PUBLIC_EMAIL_APPROVED",
                    "approved_public_emails": ["other@example.test"],
                },
                raw,
            )
            self.assertEqual(unlisted["status"], "REVIEW_REQUIRED")
            self.assertEqual(unlisted["unapproved_identity_count"], 1)

    def test_deleted_secret_is_still_found_in_git_history(self) -> None:
        configured = os.environ.get("JOBFLOW_RELEASE_GIT_PATH")
        candidate = configured or shutil.which("git")
        self.assertIsNotNone(candidate)
        git_path = Path(str(candidate)).resolve(strict=True)
        self.assertTrue(git_path.is_file())
        with project_temp() as temp:
            environment = sanitized_command_environment(
                "git",
                executable=git_path,
                project=temp,
            )
            subprocess.run(
                [str(git_path), "init", "-b", "main"],
                cwd=temp,
                env=environment,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [str(git_path), "config", "user.name", "Synthetic Maintainer"],
                cwd=temp,
                env=environment,
                check=True,
            )
            subprocess.run(
                [str(git_path), "config", "user.email", "maintainer@users.noreply.github.com"],
                cwd=temp,
                env=environment,
                check=True,
            )
            historical = temp / "historical.md"
            historical.write_text("sk-" + "S" * 24, encoding="utf-8")
            subprocess.run(
                [str(git_path), "add", "historical.md"],
                cwd=temp,
                env=environment,
                check=True,
            )
            subprocess.run(
                [str(git_path), "commit", "-m", "synthetic unsafe history"],
                cwd=temp,
                env=environment,
                check=True,
                capture_output=True,
            )
            historical.write_text("synthetic safe replacement", encoding="utf-8")
            subprocess.run(
                [str(git_path), "add", "historical.md"],
                cwd=temp,
                env=environment,
                check=True,
            )
            subprocess.run(
                [str(git_path), "commit", "-m", "synthetic safe current tree"],
                cwd=temp,
                env=environment,
                check=True,
                capture_output=True,
            )
            result = verify_public_history(temp, git_path=git_path)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("openai_key", {item["kind"] for item in result["findings"]})
