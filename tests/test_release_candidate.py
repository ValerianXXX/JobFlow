from __future__ import annotations

import tempfile
import unittest
import zipfile
from os import replace as real_replace
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT
from jobops.release_candidate import _commit_candidate_archive, run_source_candidate_smoke, verify_candidate_archive


class ReleaseCandidateTests(unittest.TestCase):
    def test_validated_archive_is_committed_from_the_destination_volume(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-source-") as raw_source:
            with tempfile.TemporaryDirectory(prefix="jobflow-candidate-destination-") as raw_destination:
                source = Path(raw_source) / "candidate.zip"
                destination = Path(raw_destination) / "dist" / "candidate.zip"
                destination.parent.mkdir()
                source.write_bytes(b"validated deterministic archive")
                replace_calls: list[tuple[Path, Path]] = []

                def checked_replace(staging: str | Path, target: str | Path) -> None:
                    staging_path, target_path = Path(staging), Path(target)
                    self.assertEqual(staging_path.parent, destination.parent)
                    self.assertEqual(target_path, destination)
                    replace_calls.append((staging_path, target_path))
                    real_replace(staging_path, target_path)

                with patch("jobops.release_candidate.os.replace", side_effect=checked_replace):
                    _commit_candidate_archive(source, destination)

                self.assertEqual(destination.read_bytes(), source.read_bytes())
                self.assertEqual(len(replace_calls), 1)
                self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_archive_rejects_localized_powershell_without_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-test-") as raw_temp:
            path = Path(raw_temp) / "candidate.zip"
            prefix = "JobFlow-v0.1.0/"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(prefix + "scripts/start-jobflow-demo.ps1", "Write-Host '中文'")
            result = verify_candidate_archive(PROJECT, path, prefix=prefix)
            self.assertIn(
                "windows_powershell_utf8_bom_missing",
                {item["kind"] for item in result["findings"]},
            )

    def test_archive_requires_complete_source_app_and_rejects_private_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-test-") as raw_temp:
            path = Path(raw_temp) / "candidate.zip"
            prefix = "JobFlow-v0.1.0/"
            with zipfile.ZipFile(path, "w") as archive:
                for required in (
                    ".github/workflows/ci.yml", ".gitignore", ".jobops-root", "AGENTS.md",
                    "CONTRIBUTING.md", "Check JobFlow.cmd", "Check Release Readiness.cmd", "Install JobFlow.cmd", "LICENSE", "MANIFEST.in", "README.md",
                    "SECURITY.md", "Start JobFlow.cmd", "Start JobFlow Demo.cmd",
                    "scripts/check-jobflow.ps1", "scripts/check-release-readiness.ps1", "scripts/start-jobflow-demo.ps1", "pyproject.toml",
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

    def test_archive_rejects_browser_companion_installation_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-test-") as raw_temp:
            path = Path(raw_temp) / "candidate.zip"
            prefix = "JobFlow-v0.2.0/"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    prefix + "browser-companion/binding.json",
                    '{"schema_version":1,"installation_id":"' + "a" * 32 + '","secret_b64url":"synthetic"}',
                )
            result = verify_candidate_archive(PROJECT, path, prefix=prefix)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn(
                "browser_companion_binding_tracked",
                {item["kind"] for item in result["findings"]},
            )

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
