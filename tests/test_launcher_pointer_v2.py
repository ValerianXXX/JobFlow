from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _support import PROJECT
from jobops.desktop_update import update_availability
from jobops.errors import JobOpsError


POWERSHELL = Path(shutil.which("powershell.exe") or "")
PRODUCTION_KEY = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"


def pointer_v2(*, version: str = "0.5.0", payload: str = "a" * 64) -> dict[str, object]:
    return {
        "schema_version": 2,
        "product": "JobFlow",
        "version_directory": f"v{version}-{payload[:12]}",
        "version": version,
        "source_commit": "1" * 40,
        "source_payload_sha256": f"sha256:{payload}",
        "runtime_closure_manifest_sha256": "sha256:" + "2" * 64,
        "runtime_tree_sha256": "sha256:" + "3" * 64,
        "release_key_id": PRODUCTION_KEY,
        "bootstrap_version": "0.5.0",
        "platform": "windows-x64",
    }


def pointer_v1() -> dict[str, object]:
    return {
        "schema_version": 1,
        "version_directory": "v0.4.1-bbbbbbbbbbbb",
        "version": "0.4.1",
        "source_sha256": "b" * 64,
    }


class LauncherPointerV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        if not POWERSHELL.is_file():
            self.skipTest("Windows PowerShell is unavailable")
        base = PROJECT / "tests" / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="pointer-v2-launcher-", dir=base)
        self.root = Path(self.temporary.name)
        self.local_app_data = self.root / "LocalAppData"
        self.install = self.local_app_data / "JobOps"
        self.bin = self.install / "bin"
        self.versions = self.install / "Application" / "versions"
        self.data = self.install / "Data"
        self.bin.mkdir(parents=True)
        (self.data / "state").mkdir(parents=True)
        (self.data / ".jobflow-data-root").write_text(
            '{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}', encoding="utf-8"
        )
        for name in (
            "jobflow-runtime-locks.ps1",
            "check-installed-jobflow.ps1",
            "rollback-installed-jobflow.ps1",
            "start-installed-jobflow.ps1",
            "run-authorized-discovery-task.ps1",
        ):
            source = PROJECT / "scripts" / "windows-runtime" / name
            if name == "start-installed-jobflow.ps1":
                original = source.read_text(encoding="utf-8-sig")
                text = original.replace(
                    "        & $runtimePython @arguments\n        $jobflowExitCode = $LASTEXITCODE",
                    "        [IO.File]::WriteAllText($env:JOBFLOW_QA_LAUNCH_SENTINEL, $runtimePython)\n        $jobflowExitCode = 0",
                    1,
                )
                self.assertNotEqual(text, original, "start launcher execution anchor changed")
                (self.bin / name).write_text(text, encoding="utf-8-sig")
            elif name == "run-authorized-discovery-task.ps1":
                original = source.read_text(encoding="utf-8-sig")
                text = original.replace(
                    "        & $pythonPath -m jobops.cli authorized-discovery-run\n        $jobflowExitCode = $LASTEXITCODE",
                    "        [IO.File]::WriteAllText($env:JOBFLOW_QA_LAUNCH_SENTINEL, $pythonPath)\n        $jobflowExitCode = 0",
                    1,
                )
                self.assertNotEqual(text, original, "discovery launcher execution anchor changed")
                (self.bin / name).write_text(text, encoding="utf-8-sig")
            else:
                shutil.copy2(source, self.bin / name)
        self._write_verifier_stub()
        (self.install / "Update JobFlow.cmd").write_text("@echo off\r\n", encoding="ascii")
        self.sentinel = self.root / "health-ran.txt"
        self.launch_sentinel = self.root / "runtime-selected.txt"
        self.v2 = pointer_v2()
        self.v1 = pointer_v1()
        self.previous_v2 = pointer_v2(version="0.4.0", payload="c" * 64)
        self.v2_root = self._make_version(self.v2, complete_runtime=True)
        self.v1_root = self._make_version(self.v1, complete_runtime=False)
        self.previous_v2_root = self._make_version(self.previous_v2, complete_runtime=True)
        self.environment = os.environ.copy()
        self.environment.update({
            "LOCALAPPDATA": str(self.local_app_data),
            "JOBFLOW_QA_HEALTH_SENTINEL": str(self.sentinel),
            "JOBFLOW_QA_LAUNCH_SENTINEL": str(self.launch_sentinel),
            "PYTHONPATH": str(PROJECT / "src"),
        })

    def _write_verifier_stub(self) -> None:
        """Install a deterministic trust-root double for launcher-only tests.

        The real bootstrap/evidence integration has its own suites.  These
        tests isolate the second half of the launcher contract: the bootstrap
        returns a canonical pointer token, then each launcher must re-read and
        bind the exact pointer under its execution lock.
        """
        (self.bin / "jobflow-bootstrap.ps1").write_text(
            "[CmdletBinding()]\n"
            "param([switch]$VerifyInstalled)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "if (-not $VerifyInstalled) { exit 2 }\n"
            "$root = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'JobOps'))\n"
            "$pointer = (Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw) | ConvertFrom-Json\n"
            "function Canon([object]$v) {\n"
            "  if ($null -eq $v) { return 'null' }\n"
            "  if ($v -is [bool]) { if ($v) { return 'true' } else { return 'false' } }\n"
            "  if ($v -is [string]) { return (ConvertTo-Json ([string]$v) -Compress) }\n"
            "  if ($v -is [int] -or $v -is [long]) { return ([long]$v).ToString([Globalization.CultureInfo]::InvariantCulture) }\n"
            "  if ($v -is [PSCustomObject]) {\n"
            "    $m = foreach($p in @($v.PSObject.Properties | Sort-Object -Property Name -CaseSensitive)) { (ConvertTo-Json ([string]$p.Name) -Compress) + ':' + (Canon $p.Value) }\n"
            "    return '{' + [string]::Join(',', [string[]]$m) + '}'\n"
            "  }\n"
            "  if ($v -is [Collections.IEnumerable]) { $i = foreach($x in $v) { Canon $x }; return '[' + [string]::Join(',', [string[]]$i) + ']' }\n"
            "  exit 2\n"
            "}\n"
            "$bytes = [Text.UTF8Encoding]::new($false).GetBytes((Canon $pointer))\n"
            "$sha = [Security.Cryptography.SHA256]::Create()\n"
            "try { $raw = $sha.ComputeHash($bytes) } finally { $sha.Dispose() }\n"
            "$pointerSha = 'sha256:' + (-join ($raw | ForEach-Object { $_.ToString('x2') }))\n"
            "[ordered]@{schema_version=1;status='JOBFLOW_INSTALLED_RUNTIME_VERIFIED';version='0.5.0';manifest_sha256=('sha256:'+'4'*64);signature_envelope_sha256=('sha256:'+'5'*64);runtime_closure_manifest_sha256=('sha256:'+'2'*64);runtime_tree_sha256=('sha256:'+'3'*64);release_key_id=('sha256:'+'1'*64);source_payload_sha256=('sha256:'+'a'*64);pointer_sha256=$pointerSha;signed_activation_evidence_verified=$true;recovery_performed=$false;activation_committed_during_recovery=$false;paths_disclosed=$false;real_external_actions=0} | ConvertTo-Json -Compress\n"
            "exit 0\n",
            encoding="utf-8-sig",
        )

    def tearDown(self) -> None:
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def _make_version(self, pointer: dict[str, object], *, complete_runtime: bool) -> Path:
        root = self.versions / str(pointer["version_directory"])
        (root / "scripts").mkdir(parents=True)
        (root / ".jobops-root").write_text("JOBOPS_PROJECT_ROOT_V1\n", encoding="ascii")
        runtime = root / ("runtime/python.exe" if complete_runtime else ".venv/Scripts/python.exe")
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"synthetic-runtime")
        (root / "scripts" / "check-jobflow.ps1").write_text(
            "param([switch]$Json,[string]$PythonPath='')\n"
            "[IO.File]::WriteAllText($env:JOBFLOW_QA_HEALTH_SENTINEL, $PythonPath)\n"
            "if ($Json) { '{\"status\":\"JOBFLOW_READY\"}' }\n"
            "exit 0\n",
            encoding="utf-8-sig",
        )
        return root

    def _write(self, name: str, value: dict[str, object]) -> None:
        (self.install / name).write_text(json.dumps(value), encoding="utf-8")

    def _run(self, script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.bin / script), *arguments,
            ],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_v2_health_selects_the_bound_runtime(self) -> None:
        self._write("current.json", self.v2)
        completed = self._run("check-installed-jobflow.ps1", "-Json")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(self.sentinel.is_file())
        self.assertTrue(
            self.sentinel.read_text(encoding="utf-8").endswith("runtime\\python.exe")
        )

    def test_v1_pointer_is_rejected_before_health_execution(self) -> None:
        self._write("current.json", self.v1)
        completed = self._run("check-installed-jobflow.ps1", "-Json")
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("JOBFLOW_INSTALLED_POINTER_INVALID", completed.stdout + completed.stderr)
        self.assertFalse(self.sentinel.exists())

    def test_v2_pointer_tampering_fails_before_health_execution(self) -> None:
        cases: dict[str, object] = {
            "schema_version": "2",
            "platform": "linux-x64",
            "product": "JobOps",
            "version": "0.5.0+mutable",
            "version_directory": "v0.5.0-bbbbbbbbbbbb",
            "source_commit": "1" * 39,
            "source_payload_sha256": "a" * 64,
            "runtime_closure_manifest_sha256": "sha256:" + "2" * 63,
            "runtime_tree_sha256": "sha256:" + "G" * 64,
            "release_key_id": "sha256:" + "4" * 64,
            "bootstrap_version": "0.5",
        }
        for field, replacement in cases.items():
            with self.subTest(field=field):
                self.sentinel.unlink(missing_ok=True)
                value = dict(self.v2)
                value[field] = replacement
                self._write("current.json", value)
                completed = self._run("check-installed-jobflow.ps1", "-Json")
                self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertIn("JOBFLOW_INSTALLED_POINTER_INVALID", completed.stdout + completed.stderr)
                self.assertFalse(self.sentinel.exists())

        self.sentinel.unlink(missing_ok=True)
        value = dict(self.v2)
        value["extra"] = True
        self._write("current.json", value)
        completed = self._run("check-installed-jobflow.ps1", "-Json")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.sentinel.exists())

    def test_v2_never_falls_back_to_the_legacy_runtime_path(self) -> None:
        self._write("current.json", self.v2)
        (self.v2_root / "runtime" / "python.exe").unlink()
        legacy = self.v2_root / ".venv" / "Scripts" / "python.exe"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"legacy-must-not-run")
        completed = self._run("check-installed-jobflow.ps1", "-Json")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("JOBFLOW_INSTALLED_RUNTIME_INVALID", completed.stdout + completed.stderr)
        self.assertFalse(self.sentinel.exists())

    def test_start_and_discovery_launchers_select_only_the_v2_runtime(self) -> None:
        self._write("current.json", self.v2)
        for script in ("start-installed-jobflow.ps1", "run-authorized-discovery-task.ps1"):
            with self.subTest(script=script):
                self.launch_sentinel.unlink(missing_ok=True)
                completed = self._run(
                    script, *(["-NoBrowser"] if script.startswith("start-") else [])
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertTrue(self.launch_sentinel.is_file())
                self.assertTrue(
                    self.launch_sentinel.read_text(encoding="utf-8").endswith(
                        "runtime\\python.exe"
                    )
                )

        self._write("current.json", self.v1)
        for script in ("start-installed-jobflow.ps1", "run-authorized-discovery-task.ps1"):
            with self.subTest(v1_rejected_by=script):
                self.launch_sentinel.unlink(missing_ok=True)
                completed = self._run(
                    script, *(["-NoBrowser"] if script.startswith("start-") else [])
                )
                self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertFalse(self.launch_sentinel.exists())

        malformed = dict(self.v2)
        malformed["platform"] = "windows-arm64"
        self._write("current.json", malformed)
        for script in ("start-installed-jobflow.ps1", "run-authorized-discovery-task.ps1"):
            with self.subTest(tampered_script=script):
                self.launch_sentinel.unlink(missing_ok=True)
                completed = self._run(script, *(["-NoBrowser"] if script.startswith("start-") else []))
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(self.launch_sentinel.exists())

    def test_stable_runtime_consumers_contain_no_legacy_runtime_fallback(self) -> None:
        for name in (
            "start-installed-jobflow.ps1",
            "check-installed-jobflow.ps1",
            "run-authorized-discovery-task.ps1",
        ):
            with self.subTest(script=name):
                text = (PROJECT / "scripts" / "windows-runtime" / name).read_text(
                    encoding="utf-8-sig"
                )
                self.assertNotIn(".venv", text.casefold())
                self.assertNotIn("RuntimeRelative", text)
                self.assertIn('"runtime\\python.exe"', text)

    def test_rollback_delegates_v2_semantics_to_the_stable_bootstrap(self) -> None:
        wrapper = (
            PROJECT / "scripts" / "windows-runtime" / "rollback-installed-jobflow.ps1"
        ).read_text(encoding="utf-8-sig")
        self.assertIn('"jobflow-bootstrap.ps1"', wrapper)
        self.assertIn('"-Rollback"', wrapper)
        self.assertIn('[switch]$StartNewRollback', wrapper)
        self.assertNotIn("Read-Pointer", wrapper)
        self.assertNotIn("Write-Pointer", wrapper)
        self.assertNotIn("Test-Version", wrapper)
        self.assertNotIn("Enter-JobFlowFileLock", wrapper)

    def test_desktop_update_recognizes_v2_and_rejects_static_identity_tampering(self) -> None:
        self._write("current.json", self.v2)
        result = update_availability(self.v2_root, environ={"LOCALAPPDATA": str(self.local_app_data)})
        self.assertEqual(result["status"], "AVAILABLE")
        bad = dict(self.v2)
        bad["version_directory"] = "v0.5.0-bbbbbbbbbbbb"
        self._write("current.json", bad)
        with self.assertRaises(JobOpsError) as raised:
            update_availability(self.v2_root, environ={"LOCALAPPDATA": str(self.local_app_data)})
        self.assertEqual(raised.exception.code, "JOBFLOW_UPDATE_POINTER_INVALID")

    def test_pointer_hardlink_is_rejected_when_supported(self) -> None:
        self._write("current.json", self.v2)
        second = self.install / "current-second-link.json"
        try:
            os.link(self.install / "current.json", second)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        completed = self._run("check-installed-jobflow.ps1", "-Json")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("JOBFLOW_INSTALLED_POINTER_INVALID", completed.stdout + completed.stderr)
        self.assertFalse(self.sentinel.exists())

    def test_v2_runtime_hardlink_is_rejected_by_every_runtime_consumer(self) -> None:
        self._write("current.json", self.v2)
        runtime = self.v2_root / "runtime" / "python.exe"
        second = self.v2_root / "runtime" / "python-second-link.exe"
        try:
            os.link(runtime, second)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        for script in (
            "check-installed-jobflow.ps1",
            "start-installed-jobflow.ps1",
            "run-authorized-discovery-task.ps1",
        ):
            with self.subTest(script=script):
                self.sentinel.unlink(missing_ok=True)
                self.launch_sentinel.unlink(missing_ok=True)
                arguments = ["-Json"] if script.startswith("check-") else (["-NoBrowser"] if script.startswith("start-") else [])
                completed = self._run(script, *arguments)
                self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertFalse(self.sentinel.exists())
                self.assertFalse(self.launch_sentinel.exists())


if __name__ == "__main__":
    unittest.main()
