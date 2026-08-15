from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import DEVNULL, CompletedProcess, Popen as RealPopen, TimeoutExpired

from _support import PROJECT
from jobops import __version__


ISOLATED_ENVIRONMENT = os.environ.copy()
_WINDOWS_POWERSHELL = shutil.which("powershell.exe", path=ISOLATED_ENVIRONMENT.get("PATH"))
if not _WINDOWS_POWERSHELL:
    raise RuntimeError("Windows PowerShell is required for launcher tests.")
WINDOWS_POWERSHELL = Path(_WINDOWS_POWERSHELL).resolve(strict=True)


def run_process(command: list[str], *, timeout: int) -> CompletedProcess[str]:
    """Run a launcher with clean process state and file-backed output capture."""
    helper = (
        "import json, subprocess, sys\n"
        "from pathlib import Path\n"
        "result_path = Path(sys.argv[1])\n"
        "stdout_path = result_path.with_suffix('.stdout')\n"
        "stderr_path = result_path.with_suffix('.stderr')\n"
        "with stdout_path.open('wb') as stdout_file, stderr_path.open('wb') as stderr_file:\n"
        "    completed = subprocess.run(sys.argv[2:], stdout=stdout_file, stderr=stderr_file, check=False)\n"
        "result_path.write_text(json.dumps({"
        "'returncode': completed.returncode, "
        "'stdout': stdout_path.read_bytes().decode('utf-8-sig'), "
        "'stderr': stderr_path.read_bytes().decode('utf-8-sig')}), encoding='utf-8')\n"
    )
    temporary_root = PROJECT / "tests" / ".tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="launcher-", dir=temporary_root) as raw:
        result_path = Path(raw) / "result.json"
        process = RealPopen(
            [sys.executable, "-I", "-c", helper, str(result_path), *command],
            cwd=PROJECT,
            env=ISOLATED_ENVIRONMENT,
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        try:
            process.wait(timeout=timeout)
        except TimeoutExpired:
            process.kill()
            process.wait()
            raise
        if process.returncode != 0 or not result_path.is_file():
            raise AssertionError("The isolated launcher test helper did not return a result.")
        value = json.loads(result_path.read_text(encoding="utf-8"))
    return CompletedProcess(
        command,
        int(value["returncode"]),
        str(value.get("stdout") or ""),
        str(value.get("stderr") or ""),
    )


class WindowsLauncherTests(unittest.TestCase):
    def test_installer_wrapper_keeps_the_result_visible(self) -> None:
        wrapper = (PROJECT / "Install JobFlow.cmd").read_text(encoding="utf-8")
        self.assertIn('set "JOBFLOW_INSTALL_EXIT=%ERRORLEVEL%"', wrapper)
        self.assertIn("JobFlow installation is ready.", wrapper)
        self.assertIn("pause", wrapper)
        self.assertIn("exit /b %JOBFLOW_INSTALL_EXIT%", wrapper)

    def test_localized_powershell_scripts_have_windows_utf8_bom(self) -> None:
        localized_scripts = (
            "check-jobflow.ps1",
            "check-release-readiness.ps1",
            "install-jobflow.ps1",
            "start-jobflow-demo.ps1",
            "start-jobflow.ps1",
        )
        for name in localized_scripts:
            with self.subTest(script=name):
                payload = (PROJECT / "scripts" / name).read_bytes()
                self.assertTrue(
                    payload.startswith(b"\xef\xbb\xbf"),
                    f"{name} must include a UTF-8 BOM for Windows PowerShell 5.1",
                )

    def test_installer_discovers_python_without_hardcoding_one_minor(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('@{ Name = "python"; Prefix = @() }', script)
        self.assertIn('@{ Name = "py"; Prefix = @("-3") }', script)
        self.assertNotIn('Prefix = @("-3.11")', script)
        self.assertIn("-ge 11", script)

    def test_installer_uses_relative_editable_target_and_checks_dependencies(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Push-Location $projectRoot', script)
        self.assertIn('--editable ".[build]"', script)
        self.assertNotIn('--editable $projectRoot', script)
        self.assertGreaterEqual(script.count("--quiet"), 2)
        self.assertIn("-m pip check", script)
        self.assertIn("setuptools>=77,<81", script)
        self.assertIn("wheel>=0.43,<1", script)

    def test_launcher_messages_are_bilingual_and_external_actions_are_absent(self) -> None:
        install = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        start = (PROJECT / "scripts" / "start-jobflow.ps1").read_text(encoding="utf-8-sig")
        check = (PROJECT / "scripts" / "check-jobflow.ps1").read_text(encoding="utf-8-sig")
        release = (PROJECT / "scripts" / "check-release-readiness.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(" / ", install)
        self.assertIn(" / ", start)
        self.assertIn(" / ", check)
        self.assertIn(" / ", release)
        combined = (install + start + check + release).casefold()
        for forbidden in ("invoke-webrequest", "start-bitstransfer", "git clone", "git push"):
            self.assertNotIn(forbidden, combined)

    def test_one_click_health_check_is_redacted_local_only_and_passing(self) -> None:
        completed = run_process(
            [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PROJECT / "scripts" / "check-jobflow.ps1"), "-Json",
                "-PythonPath", sys.executable,
            ],
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(result["status"], "JOBFLOW_READY")
        self.assertEqual(result["version"], __version__)
        self.assertEqual(result["checks_passed"], result["checks_total"])
        self.assertEqual(result["private_values_read"], 0)
        self.assertEqual(result["private_values_emitted"], 0)
        self.assertEqual(result["network_actions"], 0)
        self.assertEqual(result["real_external_actions"], 0)
        private_check = next(item for item in result["checks"] if item["id"] == "PRIVATE_STORE_INTEGRITY")
        self.assertEqual(private_check["status"], "PASS")
        serialized = json.dumps(result)
        self.assertNotIn(str(PROJECT), serialized)
        self.assertNotIn("secure-ref:", serialized)

    def test_public_cli_reports_a_safe_version(self) -> None:
        completed = run_process(
            [sys.executable, "-m", "jobops.cli", "--version"],
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), f"JobFlow {__version__}")
        self.assertNotIn(str(PROJECT), completed.stdout)

    def test_one_click_release_check_is_redacted_local_only_and_truthfully_blocked(self) -> None:
        completed = run_process(
            [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PROJECT / "scripts" / "check-release-readiness.ps1"), "-Json",
                "-PythonPath", sys.executable,
            ],
            timeout=120,
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = json.loads(completed.stdout.lstrip("\ufeff"))
        self.assertEqual(result["status"], "PUBLIC_RELEASE_BLOCKED")
        self.assertFalse(result["upload_performed"])
        self.assertEqual(result["network_actions"], 0)
        self.assertEqual(result["real_external_actions"], 0)
        self.assertTrue(result["blockers"])
        self.assertNotIn("PYTHON_RUNTIME_MISSING", result["blockers"])
        serialized = json.dumps(result)
        self.assertNotIn(str(PROJECT), serialized)
        self.assertNotIn("secure-ref:", serialized)


if __name__ == "__main__":
    unittest.main()
