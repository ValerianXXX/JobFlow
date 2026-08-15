from __future__ import annotations

import subprocess
import unittest

from _support import PROJECT, project_temp
from jobops.public_release import validate_public_paths, validate_public_text, verify_public_history


class PublicReleaseBoundaryTests(unittest.TestCase):
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
        unsafe_email = "contact" + chr(64) + "personal.invalid"
        findings = validate_public_text("unsafe.md", unsafe_email)
        self.assertEqual(findings, [{"kind": "email", "path": "unsafe.md"}])
        binding = '{"secret_b64url":"' + "A" * 43 + '"}'
        self.assertEqual(
            validate_public_text("renamed.json", binding),
            [{"kind": "browser_companion_binding_secret", "path": "renamed.json"}],
        )

    def test_deleted_secret_is_still_found_in_git_history(self) -> None:
        with project_temp() as temp:
            subprocess.run(["git", "init", "-b", "main"], cwd=temp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Synthetic Maintainer"], cwd=temp, check=True)
            subprocess.run(["git", "config", "user.email", "maintainer@users.noreply.github.com"], cwd=temp, check=True)
            historical = temp / "historical.md"
            historical.write_text("sk-" + "S" * 24, encoding="utf-8")
            subprocess.run(["git", "add", "historical.md"], cwd=temp, check=True)
            subprocess.run(["git", "commit", "-m", "synthetic unsafe history"], cwd=temp, check=True, capture_output=True)
            historical.write_text("synthetic safe replacement", encoding="utf-8")
            subprocess.run(["git", "add", "historical.md"], cwd=temp, check=True)
            subprocess.run(["git", "commit", "-m", "synthetic safe current tree"], cwd=temp, check=True, capture_output=True)
            result = verify_public_history(temp)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("openai_key", {item["kind"] for item in result["findings"]})
