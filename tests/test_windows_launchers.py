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
ISOLATED_ENVIRONMENT["PYTHONPATH"] = str(PROJECT / "src")
_WINDOWS_POWERSHELL = shutil.which("powershell.exe", path=ISOLATED_ENVIRONMENT.get("PATH"))
if not _WINDOWS_POWERSHELL:
    raise RuntimeError("Windows PowerShell is required for launcher tests.")
WINDOWS_POWERSHELL = Path(_WINDOWS_POWERSHELL).resolve(strict=True)
_PROJECT_VENV_PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
HEALTH_PYTHON = _PROJECT_VENV_PYTHON if _PROJECT_VENV_PYTHON.is_file() else Path(sys.executable)


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
        self.assertIn('start "JobFlow" "%LOCALAPPDATA%\\JobOps\\Start JobFlow.cmd"', wrapper)
        self.assertIn("pause", wrapper)
        self.assertIn("exit /b %JOBFLOW_INSTALL_EXIT%", wrapper)

    def test_localized_powershell_scripts_have_windows_utf8_bom(self) -> None:
        localized_scripts = (
            "check-jobflow.ps1",
            "check-release-readiness.ps1",
            "install-jobflow.ps1",
            "start-jobflow-demo.ps1",
            "start-jobflow.ps1",
            "windows-runtime/start-installed-jobflow.ps1",
            "windows-runtime/update-installed-jobflow.ps1",
            "windows-runtime/rollback-installed-jobflow.ps1",
            "windows-runtime/uninstall-installed-jobflow.ps1",
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

    def test_installer_uses_versioned_fixed_target_and_checks_dependencies(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Push-Location $stagingRoot', script)
        self.assertNotIn("--editable", script)
        self.assertIn('".[build]"', script)
        self.assertIn('Join-Path $localRoot "Application"', script)
        self.assertIn('Join-Path $applicationRoot "versions"', script)
        self.assertIn('Join-Path $localRoot "Data"', script)
        self.assertIn('Join-Path $localRoot "current.json"', script)
        self.assertIn('Join-Path $localRoot "previous.json"', script)
        self.assertIn('"src", "tests"', script)
        self.assertIn('"Install JobFlow Browser Companion.cmd"', script)
        self.assertIn('|\\.tmp|\\.git)', script)
        self.assertIn('"v$version-$($sourceHash.Substring(0, 12))"', script)
        self.assertIn("Test-VersionHealth", script)
        self.assertIn("Write-JsonAtomic", script)
        self.assertGreaterEqual(script.count("--quiet"), 2)
        self.assertIn("-m pip check", script)
        self.assertIn("setuptools>=77,<81", script)
        self.assertIn("wheel>=0.43,<1", script)
        self.assertIn('install-jobflow-browser-companion.ps1', script)
        self.assertIn('-File $companionInstaller -NoLaunch', script)
        self.assertIn('JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY', script)
        self.assertIn('jobflow-fixed-install-qa-*', script)
        self.assertIn('JOBFLOW_INSTALL_ACCEPTANCE_BYPASS_FORBIDDEN', script)
        self.assertIn('Substring(0, 12)', script)
        self.assertIn('(".i-" + $installId)', script)
        self.assertIn('(".r-" + $installId)', script)
        self.assertIn("Install-StableLaunchers", script)
        self.assertIn('"update-installed-jobflow.ps1"', script)
        self.assertIn('"Update JobFlow.cmd"', script)
        self.assertIn('@{ Name = "Update JobFlow.lnk"; Target = "Update JobFlow.cmd" }', script)
        self.assertIn('"/inheritance:r" "/grant:r" $grant', script)
        self.assertIn('(Join-Path $Path "*") "/reset" "/T" "/C"', script)
        self.assertIn("JOBFLOW_INSTALL_CHILD_ACL_FAILED", script)

    def test_source_launchers_delegate_to_the_fixed_install(self) -> None:
        start = (PROJECT / "Start JobFlow.cmd").read_text(encoding="utf-8")
        check = (PROJECT / "Check JobFlow.cmd").read_text(encoding="utf-8")
        rollback = (PROJECT / "Rollback JobFlow.cmd").read_text(encoding="utf-8")
        update = (PROJECT / "Update JobFlow.cmd").read_text(encoding="utf-8")
        uninstall = (PROJECT / "Uninstall JobFlow.cmd").read_text(encoding="utf-8")
        self.assertIn(r"%LOCALAPPDATA%\JobOps\Start JobFlow.cmd", start)
        self.assertIn(r"%LOCALAPPDATA%\JobOps\Check JobFlow.cmd", check)
        self.assertIn(r"%LOCALAPPDATA%\JobOps\Rollback JobFlow.cmd", rollback)
        self.assertIn(r"%LOCALAPPDATA%\JobOps\Update JobFlow.cmd", update)
        self.assertIn(r"%LOCALAPPDATA%\JobOps\Uninstall JobFlow.cmd", uninstall)

    def test_installed_runtime_has_rollback_and_data_preserving_uninstall(self) -> None:
        rollback = (PROJECT / "scripts" / "windows-runtime" / "rollback-installed-jobflow.ps1").read_text(encoding="utf-8-sig")
        uninstall = (PROJECT / "scripts" / "windows-runtime" / "uninstall-installed-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Test-Version", rollback)
        self.assertIn("Write-Pointer $previousPath $current.Value", rollback)
        self.assertIn("Write-Pointer $currentPath $previous.Value", rollback)
        self.assertIn("-RemoveUserData -UserConfirmed", uninstall)
        self.assertIn('if ($RemoveUserData) { $targets += @("Data", "private") }', uninstall)
        self.assertNotIn('"Data", "private"\n)', uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_ROOT_FORBIDDEN", uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_REPARSE_FORBIDDEN", uninstall)
        self.assertIn("JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY", uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_ACCEPTANCE_BYPASS_FORBIDDEN", uninstall)
        self.assertIn("if (-not $skipBrowserIntegrationForAcceptance)", uninstall)
        self.assertIn("function Remove-SafeTarget", uninstall)
        self.assertIn('"\\\\?\\" + $absolute', uninstall)
        self.assertIn("[IO.Directory]::Delete($extended, $true)", uninstall)
        self.assertIn("[IO.File]::SetAttributes($file, [IO.FileAttributes]::Normal)", uninstall)
        self.assertIn('"Update JobFlow.cmd"', uninstall)

    def test_signed_update_launcher_is_user_initiated_pinned_and_fail_closed(self) -> None:
        script = (PROJECT / "scripts" / "windows-runtime" / "update-installed-jobflow.ps1").read_text(
            encoding="utf-8-sig"
        )
        wrapper = (PROJECT / "scripts" / "windows-runtime" / "Update JobFlow.cmd").read_text(
            encoding="utf-8"
        )
        self.assertIn("https://api.github.com/repos/ValerianXXX/JobFlow/releases/latest", script)
        self.assertIn("sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339", script)
        self.assertIn("jobops.update_manifest inspect", script)
        self.assertIn("jobops.update_manifest verify", script)
        self.assertIn("JOBFLOW_UPDATE_POST_SWITCH_HEALTH_FAILED_ROLLED_BACK", script)
        self.assertIn("rollback-installed-jobflow.ps1", script)
        self.assertIn("AllowAutoRedirect = $false", script)
        self.assertIn("Assert-AllowedHttpsUri", script)
        self.assertIn("Expand-Archive", script)
        self.assertIn("-NoLaunch", script)
        self.assertNotIn("Register-ScheduledTask", script)
        self.assertNotIn("schtasks", script.casefold())
        self.assertNotIn("Start-BitsTransfer", script)
        self.assertIn("pause", wrapper.casefold())

    def test_installed_rollback_swaps_only_validated_version_pointers(self) -> None:
        temporary_root = PROJECT / "tests" / ".tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rollback-layout-", dir=temporary_root) as raw:
            local_app_data = Path(raw) / "LocalAppData"
            install_root = local_app_data / "JobOps"
            bin_root = install_root / "bin"
            data_root = install_root / "Data"
            versions_root = install_root / "Application" / "versions"
            bin_root.mkdir(parents=True)
            data_root.mkdir(parents=True)
            (data_root / ".jobflow-data-root").write_text(
                '{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}', encoding="utf-8"
            )
            for directory, version, digest in (
                ("v0.4.1-aaaaaaaaaaaa", "0.4.1", "a" * 64),
                ("v0.4.0-bbbbbbbbbbbb", "0.4.0", "b" * 64),
            ):
                version_root = versions_root / directory
                (version_root / ".venv" / "Scripts").mkdir(parents=True)
                (version_root / ".venv" / "Scripts" / "python.exe").write_bytes(b"placeholder")
                (version_root / "scripts").mkdir()
                (version_root / "scripts" / "check-jobflow.ps1").write_text(
                    "param([switch]$Json,[string]$PythonPath='')\n"
                    "if ($Json) { '{\"status\":\"JOBFLOW_READY\"}' }\n"
                    "exit 0\n",
                    encoding="utf-8-sig",
                )
                pointer = {
                    "schema_version": 1,
                    "version_directory": directory,
                    "version": version,
                    "source_sha256": digest,
                }
                name = "current.json" if version == "0.4.1" else "previous.json"
                (install_root / name).write_text(json.dumps(pointer), encoding="utf-8")
            shutil.copy2(
                PROJECT / "scripts" / "windows-runtime" / "rollback-installed-jobflow.ps1",
                bin_root / "rollback-installed-jobflow.ps1",
            )
            environment = ISOLATED_ENVIRONMENT.copy()
            environment["LOCALAPPDATA"] = str(local_app_data)
            completed = __import__("subprocess").run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(bin_root / "rollback-installed-jobflow.ps1"),
                ],
                cwd=PROJECT,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            current = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
            previous = json.loads((install_root / "previous.json").read_text(encoding="utf-8"))
            self.assertEqual(current["version"], "0.4.0")
            self.assertEqual(previous["version"], "0.4.1")
            self.assertTrue((data_root / ".jobflow-data-root").is_file())

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
                "-PythonPath", str(HEALTH_PYTHON),
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

    def test_source_health_check_ignores_unrelated_python_distributions(self) -> None:
        script = (PROJECT / "scripts" / "check-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("import docx, lxml.etree, pdfplumber, pypdf; from PIL import Image", script)
        self.assertNotIn("$venvPython -m pip check", script)

    def test_public_cli_reports_a_safe_version(self) -> None:
        completed = run_process(
            [str(HEALTH_PYTHON), "-m", "jobops.cli", "--version"],
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
                "-PythonPath", str(HEALTH_PYTHON),
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
