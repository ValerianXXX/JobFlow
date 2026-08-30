from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from _support import PROJECT, project_temp
from jobops.errors import JobOpsError
from jobops.public_release import _git as public_git
from jobops.public_release import verify_public_repository
from jobops.release import _source_commit
from jobops.release_readiness import _git as readiness_git
from jobops.release_toolchain import (
    ReleaseToolchainError,
    resolve_configured_release_git,
)


class ReleaseGitFailClosedTests(unittest.TestCase):
    def test_path_shadow_is_never_selected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-path-git-") as raw:
            shadow = Path(raw) / "git.exe"
            shadow.write_bytes(b"malicious path shadow")
            with patch.dict(os.environ, {"PATH": raw}, clear=False):
                os.environ.pop("JOBFLOW_RELEASE_GIT_PATH", None)
                with self.assertRaises(ReleaseToolchainError) as raised:
                    resolve_configured_release_git()
            self.assertEqual(str(raised.exception), "RELEASE_GIT_PATH_REQUIRED")

    def test_relative_environment_git_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {"JOBFLOW_RELEASE_GIT_PATH": "git.exe"},
            clear=False,
        ):
            with self.assertRaises(ReleaseToolchainError) as raised:
                resolve_configured_release_git()
        self.assertEqual(str(raised.exception), "RELEASE_GIT_PATH_INVALID")

    def test_public_git_uses_only_absolute_executable_and_sanitized_environment(self) -> None:
        with project_temp() as root:
            executable = (root / "git.exe").resolve()
            executable.write_bytes(b"synthetic git")
            completed = subprocess.CompletedProcess([], 0, stdout=b"clean", stderr=b"")
            with patch(
                "jobops.public_release.sanitized_command_environment",
                return_value={"GIT_CONFIG_GLOBAL": "NUL", "PATH": "bounded"},
            ), patch(
                "jobops.public_release.subprocess.run", return_value=completed
            ) as run:
                self.assertEqual(public_git(root, "status", git_path=executable), b"clean")
        command = run.call_args.args[0]
        keywords = run.call_args.kwargs
        self.assertEqual(command, [str(executable), "status"])
        self.assertEqual(keywords["env"]["PATH"], "bounded")

    def test_public_repository_authenticates_selected_git_before_scanning(self) -> None:
        with project_temp() as root:
            executable = (root / "git.exe").resolve()
            executable.write_bytes(b"synthetic git")
            with patch(
                "jobops.public_release.locked_release_git",
                return_value=nullcontext(executable),
            ) as locked, patch(
                "jobops.public_release.verify_public_tree",
                return_value={"status": "PASS"},
            ) as tree, patch(
                "jobops.public_release.verify_public_history",
                return_value={"status": "PASS"},
            ), patch(
                "jobops.public_release.verify_author_identity",
                return_value={"status": "PASS"},
            ):
                result = verify_public_repository(root, git_path=executable)
        locked.assert_called_once_with(root, executable)
        tree.assert_called_once_with(root, git_path=executable)
        self.assertTrue(result["public_repository_ready"])

    def test_source_commit_uses_locked_absolute_git_and_minimal_environment(self) -> None:
        executable = (PROJECT / "synthetic-git.exe").resolve()
        completed = Mock(returncode=0, stdout="A" * 40 + "\n")
        with patch(
            "jobops.release.locked_release_git",
            return_value=nullcontext(executable),
        ), patch(
            "jobops.release.sanitized_command_environment",
            return_value={"GIT_CONFIG_GLOBAL": "NUL", "PATH": "bounded"},
        ), patch("jobops.release.subprocess.run", return_value=completed) as run:
            self.assertEqual(_source_commit(PROJECT, git_path=executable), "a" * 40)
        self.assertEqual(run.call_args.args[0], [str(executable), "rev-parse", "HEAD"])
        self.assertEqual(run.call_args.kwargs["env"]["PATH"], "bounded")

    def test_readiness_git_does_not_execute_a_path_shadow(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-readiness-git-") as raw:
            (Path(raw) / "git.exe").write_bytes(b"malicious path shadow")
            with patch.dict(os.environ, {"PATH": raw}, clear=False), patch(
                "jobops.release_readiness.subprocess.run"
            ) as run:
                os.environ.pop("JOBFLOW_RELEASE_GIT_PATH", None)
                with self.assertRaises(JobOpsError) as raised:
                    readiness_git(PROJECT, "rev-parse", "HEAD")
            run.assert_not_called()
        self.assertEqual(raised.exception.code, "RELEASE_GIT_PATH_REQUIRED")

    def test_every_public_entry_point_is_free_of_path_git_fallbacks(self) -> None:
        files = {
            "public": PROJECT / "src" / "jobops" / "public_release.py",
            "release": PROJECT / "src" / "jobops" / "release.py",
            "readiness": PROJECT / "src" / "jobops" / "release_readiness.py",
            "runner": PROJECT
            / ".agents"
            / "skills"
            / "job-application-operator"
            / "scripts"
            / "run-release-verification.py",
        }
        for name, path in files.items():
            source = path.read_text(encoding="utf-8")
            with self.subTest(entrypoint=name):
                self.assertNotIn('["git",', source)
                self.assertNotIn("shutil.which", source)
        cli = (PROJECT / "src" / "jobops" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('verify_release_command.add_argument("--git-path", type=Path)', cli)
        self.assertIn("git_path=args.git_path", cli)
        wrapper = (PROJECT / "scripts" / "check-release-readiness.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertNotIn("Get-Command git", wrapper)
        self.assertIn("--git-path $resolvedGit", wrapper)

    def test_ci_uses_explicit_git_and_immutable_action_revisions(self) -> None:
        workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4",
            workflow,
        )
        self.assertIn(
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
            workflow,
        )
        self.assertIn(
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4",
            workflow,
        )
        self.assertIn(
            "python -m jobops.public_release --git-path $env:JOBFLOW_RELEASE_GIT_PATH",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
