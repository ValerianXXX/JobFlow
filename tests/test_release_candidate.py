from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import nullcontext
from os import replace as real_replace
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.release_candidate import (
    _archive_identity,
    build_release_candidate,
    _commit_candidate_archive,
    _configured_git_path,
    _git,
    _python_smoke_environment,
    run_source_candidate_smoke,
    verify_candidate_archive,
)


class ReleaseCandidateTests(unittest.TestCase):
    def test_candidate_build_requires_repository_identity_approval(self) -> None:
        repository = {
            "status": "PASS",
            "public_repository_ready": False,
            "author_identity": {"status": "REVIEW_REQUIRED"},
        }
        trusted = PROJECT / "synthetic-trusted-git.exe"
        with patch(
            "jobops.release_candidate._locked_release_git",
            return_value=nullcontext(trusted),
        ), patch(
            "jobops.release_candidate._git_environment",
            return_value={"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "NUL"},
        ), patch("jobops.release_candidate.verify_public_repository", return_value=repository):
            with self.assertRaises(JobOpsError) as blocked:
                build_release_candidate(PROJECT, git_path=trusted)
        self.assertEqual(
            blocked.exception.code,
            "PUBLIC_REPOSITORY_IDENTITY_REVIEW_REQUIRED",
        )

    def test_candidate_refuses_path_git_even_when_path_contains_a_shim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-git-shim-") as raw:
            shim = Path(raw) / "git.exe"
            shim.write_bytes(b"malicious shim")
            with patch.dict(
                os.environ,
                {"PATH": raw},
                clear=False,
            ):
                os.environ.pop("JOBFLOW_RELEASE_GIT_PATH", None)
                with self.assertRaises(JobOpsError) as blocked:
                    _configured_git_path(None)
            self.assertEqual(blocked.exception.code, "RELEASE_GIT_PATH_REQUIRED")

    def test_candidate_rejects_an_untrusted_absolute_git_from_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-untrusted-git-") as raw:
            shim = (Path(raw) / "git.exe").resolve()
            shim.write_bytes(b"untrusted absolute shim")
            with patch.dict(
                os.environ,
                {"JOBFLOW_RELEASE_GIT_PATH": str(shim)},
                clear=False,
            ):
                with self.assertRaises(JobOpsError) as blocked:
                    build_release_candidate(PROJECT)
            self.assertEqual(blocked.exception.code, "RELEASE_GIT_UNTRUSTED")

    def test_git_subprocess_uses_absolute_git_and_minimal_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-trusted-git-") as raw:
            trusted = Path(raw) / "git.exe"
            trusted.write_bytes(b"synthetic executable")
            observed: dict[str, object] = {}

            def capture(*arguments: object, **keywords: object) -> subprocess.CompletedProcess[bytes]:
                observed["arguments"] = arguments
                observed["keywords"] = keywords
                return subprocess.CompletedProcess(arguments[0], 0, stdout=b"clean", stderr=b"")

            hostile = {
                "PATH": str(Path(raw) / "attacker"),
                "GIT_CONFIG_GLOBAL": str(Path(raw) / "attacker.gitconfig"),
                "GIT_CONFIG_SYSTEM": str(Path(raw) / "system.gitconfig"),
                "GIT_EXEC_PATH": str(Path(raw) / "git-core"),
                "GIT_SSH_COMMAND": "attacker.exe",
                "PYTHONPATH": str(Path(raw) / "python-shim"),
                "PYTHONHOME": str(Path(raw) / "python-home"),
            }
            with patch.dict(os.environ, hostile, clear=False), patch(
                "jobops.release_candidate.subprocess.run", side_effect=capture
            ):
                self.assertEqual(_git(PROJECT, trusted, "status"), b"clean")

            command = observed["arguments"][0]
            keywords = observed["keywords"]
            environment = keywords["env"]
            self.assertEqual(command[0], str(trusted))
            self.assertEqual(command[1:], ["status"])
            self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "NUL")
            self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertNotIn("GIT_CONFIG_SYSTEM", environment)
            self.assertNotIn("GIT_EXEC_PATH", environment)
            self.assertNotIn("GIT_SSH_COMMAND", environment)
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("PYTHONHOME", environment)
            self.assertNotIn(str(Path(raw) / "attacker"), environment["PATH"])

    def test_python_smoke_environment_drops_caller_injection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-python-env-") as raw:
            temporary = Path(raw)
            hostile = {
                "PYTHONPATH": str(temporary / "python-path"),
                "PYTHONHOME": str(temporary / "python-home"),
                "PYTHONSTARTUP": str(temporary / "startup.py"),
                "PYTHONWARNINGS": "error",
                "GIT_CONFIG_GLOBAL": str(temporary / "gitconfig"),
            }
            with patch.dict(os.environ, hostile, clear=False):
                environment = _python_smoke_environment(temporary)
            for key in hostile:
                self.assertNotIn(key, environment)
            self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
            self.assertEqual(environment["PYTHONSAFEPATH"], "1")
            self.assertEqual(
                environment["LOCALAPPDATA"],
                str((temporary / "local-app-data").resolve()),
            )

    def test_candidate_uses_one_git_for_scan_clean_head_and_archives(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-binding-") as raw:
            project = Path(raw).resolve()
            (project / "reports").mkdir()
            (project / "pyproject.toml").write_text(
                '[project]\nversion = "0.9.9"\n', encoding="utf-8"
            )
            trusted = project / "trusted" / "git.exe"
            trusted.parent.mkdir()
            trusted.write_bytes(b"synthetic executable")
            commit = "a" * 40
            git_calls: list[tuple[Path, tuple[str, ...]]] = []
            archive_calls: list[tuple[Path, tuple[str, ...]]] = []

            def fake_git(root: Path, executable: Path, *arguments: str) -> bytes:
                git_calls.append((executable, arguments))
                if arguments[0] == "status":
                    return b""
                if arguments[0] == "rev-parse":
                    return commit.encode("ascii") + b"\n"
                raise AssertionError(arguments)

            def fake_archive(
                root: Path, executable: Path, *arguments: str
            ) -> subprocess.CompletedProcess[bytes]:
                archive_calls.append((executable, arguments))
                output = next(value.split("=", 1)[1] for value in arguments if value.startswith("--output="))
                Path(output).write_bytes(b"deterministic archive")
                return subprocess.CompletedProcess([str(executable), *arguments], 0, stdout=b"", stderr=b"")

            repository = {
                "status": "PASS",
                "public_repository_ready": True,
                "author_identity": {"status": "PASS"},
            }
            scan_environments: list[dict[str, str]] = []

            def fake_scan(root: Path, *, git_path: Path) -> dict[str, object]:
                scan_environments.append(dict(os.environ))
                return repository

            hostile = {
                "GIT_CONFIG_SYSTEM": str(project / "attacker.gitconfig"),
                "GIT_EXEC_PATH": str(project / "attacker-git-core"),
                "GIT_SSH_COMMAND": "attacker.exe",
                "PYTHONPATH": str(project / "attacker-python"),
            }
            with patch(
                "jobops.release_candidate._locked_release_git",
                return_value=nullcontext(trusted),
            ), patch(
                "jobops.release_candidate.verify_public_repository",
                side_effect=fake_scan,
            ) as scan, patch(
                "jobops.release_candidate._git", side_effect=fake_git
            ), patch(
                "jobops.release_candidate._run_git", side_effect=fake_archive
            ), patch(
                "jobops.release_candidate.verify_candidate_archive",
                return_value={"status": "PASS", "file_count": 1, "finding_count": 0, "findings": []},
            ), patch(
                "jobops.release_candidate.run_source_candidate_smoke",
                return_value={"status": "PASS"},
            ), patch.dict(os.environ, hostile, clear=False):
                result = build_release_candidate(project, git_path=trusted)

            scan.assert_called_once_with(project, git_path=trusted)
            self.assertEqual(len(scan_environments), 1)
            for key in hostile:
                self.assertNotIn(key, scan_environments[0])
            self.assertEqual(scan_environments[0]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(scan_environments[0]["GIT_CONFIG_GLOBAL"], "NUL")
            self.assertTrue(git_calls)
            self.assertEqual({path for path, _ in git_calls}, {trusted})
            self.assertEqual(len(archive_calls), 2)
            self.assertEqual({path for path, _ in archive_calls}, {trusted})
            self.assertTrue(all(arguments[-1] == commit for _, arguments in archive_calls))
            self.assertEqual(result["commit"], commit)

    def test_archive_identity_reads_digest_and_size_from_one_handle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-identity-") as raw:
            archive = Path(raw) / "candidate.zip"
            payload = b"same-handle candidate identity"
            archive.write_bytes(payload)
            digest, size = _archive_identity(archive)
            self.assertEqual(digest, "sha256:" + hashlib.sha256(payload).hexdigest())
            self.assertEqual(size, len(payload))

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
                    "scripts/check-jobflow.ps1", "scripts/check-release-readiness.ps1", "scripts/install-jobflow-v2.ps1", "scripts/start-jobflow-demo.ps1", "pyproject.toml",
                ):
                    archive.writestr(prefix + required, "synthetic safe text")
                archive.writestr(prefix + "state/jobops.db", b"synthetic")
            result = verify_candidate_archive(PROJECT, path, prefix=prefix)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("runtime_state_tracked", {item["kind"] for item in result["findings"]})

    def test_archive_rejects_a_public_wrapper_that_routes_to_the_legacy_installer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-candidate-test-") as raw_temp:
            path = Path(raw_temp) / "candidate.zip"
            prefix = "JobFlow-v0.1.0/"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    prefix + "Install JobFlow.cmd",
                    '@echo off\r\npowershell.exe -File "%~dp0scripts\\install-jobflow.ps1"\r\n',
                )
                archive.writestr(
                    prefix + "scripts/install-jobflow-v2.ps1",
                    b"\xef\xbb\xbf[CmdletBinding()] param([switch]$NoLaunch)\r\n",
                )
            result = verify_candidate_archive(PROJECT, path, prefix=prefix)
            self.assertIn(
                "candidate_installer_path_invalid",
                {item["kind"] for item in result["findings"]},
            )

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
