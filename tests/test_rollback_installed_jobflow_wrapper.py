from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _support import PROJECT


POWERSHELL = Path(shutil.which("powershell.exe") or "")
WRAPPER = (
    PROJECT
    / "scripts"
    / "windows-runtime"
    / "rollback-installed-jobflow.ps1"
)
KNOWN_FOLDER_EXPRESSION = (
    "[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)"
)


class RollbackInstalledJobFlowWrapperTests(unittest.TestCase):
    """Verify that the installed rollback entrypoint is only a safe delegator."""

    maxDiff = None

    def setUp(self) -> None:
        if not POWERSHELL.is_file():
            self.skipTest("Windows PowerShell is unavailable")
        base = PROJECT / "tests" / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="rollback-wrapper-", dir=base
        )
        self.root = Path(self.temporary.name)
        self.local_app_data = self.root / "LocalAppData"
        self.bin_root = self.local_app_data / "JobOps" / "bin"
        self.bin_root.mkdir(parents=True)
        self.capture = self.root / "bootstrap-argv.json"

    def tearDown(self) -> None:
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    @staticmethod
    def _source() -> str:
        return WRAPPER.read_text(encoding="utf-8-sig")

    def _write_testable_wrapper(self, destination: Path) -> Path:
        """Redirect only the OS Known Folder lookup into the isolated fixture.

        The production wrapper deliberately resolves the real Windows Known
        Folder and therefore cannot safely be pointed at a test install.  The
        static test below locks that production expression in place.  This
        one-anchor copy lets the delegation behavior run without reading or
        modifying the user's installed JobFlow tree.
        """

        source = self._source()
        self.assertEqual(source.count(KNOWN_FOLDER_EXPRESSION), 1)
        testable = source.replace(
            KNOWN_FOLDER_EXPRESSION,
            "$env:LOCALAPPDATA",
            1,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(testable, encoding="utf-8-sig")
        return destination

    def _write_bootstrap_stub(self, bin_root: Path) -> Path:
        stub = bin_root / "jobflow-bootstrap.ps1"
        stub.write_text(
            "[CmdletBinding()]\n"
            "param([switch]$Rollback, [switch]$StartNewRollback)\n"
            "$received = @()\n"
            "if ($Rollback.IsPresent) { $received += '-Rollback' }\n"
            "if ($StartNewRollback.IsPresent) { "
            "$received += '-StartNewRollback' }\n"
            "$json = ConvertTo-Json -Compress -InputObject ([object[]]$received)\n"
            "[IO.File]::WriteAllText($env:JOBFLOW_QA_WRAPPER_CAPTURE, $json, "
            "[Text.UTF8Encoding]::new($false))\n"
            "[Console]::Out.WriteLine('BOOTSTRAP_STDOUT_SENTINEL')\n"
            "[Console]::Error.WriteLine('BOOTSTRAP_STDERR_SENTINEL')\n"
            "exit [int]$env:JOBFLOW_QA_BOOTSTRAP_EXIT\n",
            encoding="utf-8-sig",
        )
        return stub

    def _environment(self, *, exit_code: int = 0) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "LOCALAPPDATA": str(self.local_app_data),
                "JOBFLOW_QA_WRAPPER_CAPTURE": str(self.capture),
                "JOBFLOW_QA_BOOTSTRAP_EXIT": str(exit_code),
            }
        )
        return environment

    def _run(
        self,
        wrapper: Path,
        *arguments: str,
        exit_code: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                *arguments,
            ],
            cwd=PROJECT,
            env=self._environment(exit_code=exit_code),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def test_static_contract_has_only_the_explicit_new_rollback_switch(self) -> None:
        source = self._source()
        parameter_block = source.split("param(", 1)[1].split(")", 1)[0]
        parameters = re.findall(r"\$([A-Za-z][A-Za-z0-9_]*)", parameter_block)
        self.assertEqual(parameters, ["StartNewRollback"])

        self.assertIn(KNOWN_FOLDER_EXPRESSION, source)
        self.assertIn('[IO.Path]::Combine($localData, "JobOps")', source)
        self.assertIn(
            '[IO.Path]::Combine($PSScriptRoot, "jobflow-bootstrap.ps1")',
            source,
        )
        self.assertIn('"-File", $bootstrap, "-Rollback"', source)
        self.assertIn(
            'if ($StartNewRollback.IsPresent) { '
            '$arguments += "-StartNewRollback" }',
            source,
        )

        for forbidden_input in (
            "ManifestPath",
            "SignaturePath",
            "ArchivePath",
            "PointerPath",
            "CurrentPath",
            "PreviousPath",
            "HealthPath",
            "LockPath",
            "InstallRoot",
            "DataRoot",
        ):
            with self.subTest(forbidden_input=forbidden_input):
                self.assertNotIn(forbidden_input, parameter_block)

        for forbidden_implementation in (
            "current.json",
            "previous.json",
            "Invoke-CandidateRuntimeHealth",
            "OpenExclusiveLockFile",
            "rollback-transaction",
        ):
            with self.subTest(forbidden_implementation=forbidden_implementation):
                self.assertNotIn(forbidden_implementation, source)

    def test_default_delegation_passes_only_rollback(self) -> None:
        wrapper = self._write_testable_wrapper(
            self.bin_root / "rollback-installed-jobflow.ps1"
        )
        self._write_bootstrap_stub(self.bin_root)

        completed = self._run(wrapper)

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(self.capture.read_text(encoding="utf-8")), ["-Rollback"])
        self.assertIn("BOOTSTRAP_STDOUT_SENTINEL", completed.stdout)
        self.assertIn("BOOTSTRAP_STDERR_SENTINEL", completed.stderr)

    def test_explicit_new_transaction_switch_is_forwarded_after_rollback(self) -> None:
        wrapper = self._write_testable_wrapper(
            self.bin_root / "rollback-installed-jobflow.ps1"
        )
        self._write_bootstrap_stub(self.bin_root)

        completed = self._run(wrapper, "-StartNewRollback")

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(
            json.loads(self.capture.read_text(encoding="utf-8")),
            ["-Rollback", "-StartNewRollback"],
        )

    def test_bootstrap_stdout_stderr_and_nonzero_exit_are_propagated(self) -> None:
        wrapper = self._write_testable_wrapper(
            self.bin_root / "rollback-installed-jobflow.ps1"
        )
        self._write_bootstrap_stub(self.bin_root)

        completed = self._run(wrapper, exit_code=23)

        self.assertEqual(completed.returncode, 23, completed.stdout + completed.stderr)
        self.assertIn("BOOTSTRAP_STDOUT_SENTINEL", completed.stdout)
        self.assertIn("BOOTSTRAP_STDERR_SENTINEL", completed.stderr)
        self.assertNotIn("JOBFLOW_ROLLBACK_WRAPPER_FAILED", completed.stderr)

    def test_wrong_installed_root_fails_redacted_before_bootstrap_invocation(self) -> None:
        wrong_bin = self.root / "WrongRoot" / "JobOps" / "bin"
        wrapper = self._write_testable_wrapper(
            wrong_bin / "rollback-installed-jobflow.ps1"
        )
        self._write_bootstrap_stub(wrong_bin)

        completed = self._run(wrapper)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_ROLLBACK_WRAPPER_FAILED")
        self.assertFalse(self.capture.exists())
        self.assertNotIn(str(self.root), completed.stdout + completed.stderr)

    def test_missing_bootstrap_fails_redacted_before_any_invocation(self) -> None:
        wrapper = self._write_testable_wrapper(
            self.bin_root / "rollback-installed-jobflow.ps1"
        )

        completed = self._run(wrapper)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr.strip(), "JOBFLOW_ROLLBACK_WRAPPER_FAILED")
        self.assertFalse(self.capture.exists())
        self.assertNotIn(str(self.root), completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
