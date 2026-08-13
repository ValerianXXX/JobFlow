from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from _support import PROJECT
from jobops.release_candidate import run_source_candidate_smoke, verify_candidate_archive


class ReleaseCandidateTests(unittest.TestCase):
    def test_archive_requires_complete_source_app_and_rejects_private_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-test-") as raw_temp:
            path = Path(raw_temp) / "candidate.zip"
            prefix = "JobFlow-v0.1.0/"
            with zipfile.ZipFile(path, "w") as archive:
                for required in (
                    ".github/workflows/ci.yml", ".gitignore", ".jobops-root", "AGENTS.md",
                    "CONTRIBUTING.md", "Check JobFlow.cmd", "Install JobFlow.cmd", "LICENSE", "MANIFEST.in", "README.md",
                    "SECURITY.md", "Start JobFlow.cmd", "Start JobFlow Demo.cmd",
                    "scripts/check-jobflow.ps1", "scripts/start-jobflow-demo.ps1", "pyproject.toml",
                ):
                    archive.writestr(prefix + required, "synthetic safe text")
                archive.writestr(prefix + "state/jobops.db", b"synthetic")
            result = verify_candidate_archive(PROJECT, path, prefix=prefix)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("runtime_state_tracked", {item["kind"] for item in result["findings"]})

    def test_archive_rejects_traversal_and_secret_text(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-test-") as raw_temp:
            path = Path(raw_temp) / "candidate.zip"
            prefix = "JobFlow-v0.1.0/"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(prefix + "../escape.txt", "safe")
                archive.writestr(prefix + "unsafe.md", "sk-" + "S" * 24)
            result = verify_candidate_archive(PROJECT, path, prefix=prefix)
            kinds = {item["kind"] for item in result["findings"]}
            self.assertIn("unsafe_archive_path", kinds)
            self.assertIn("openai_key", kinds)

    def test_source_smoke_refuses_candidate_without_smoke_entry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-test-") as raw_temp:
            temporary = Path(raw_temp)
            path = temporary / "candidate.zip"
            prefix = "JobFlow-v0.1.0/"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(prefix + ".jobops-root", "jobflow-root-v1")
            with self.assertRaises(Exception):
                run_source_candidate_smoke(path, prefix=prefix, temporary=temporary / "smoke")


if __name__ == "__main__":
    unittest.main()
