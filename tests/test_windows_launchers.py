from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import DEVNULL, PIPE, CompletedProcess, Popen as RealPopen, TimeoutExpired

from _support import PROJECT
from jobops import __version__
from jobops.runtime_schema import validate_named


ISOLATED_ENVIRONMENT = os.environ.copy()
ISOLATED_ENVIRONMENT["PYTHONPATH"] = str(PROJECT / "src")
_WINDOWS_POWERSHELL = shutil.which("powershell.exe", path=ISOLATED_ENVIRONMENT.get("PATH"))
if not _WINDOWS_POWERSHELL:
    raise RuntimeError("Windows PowerShell is required for launcher tests.")
WINDOWS_POWERSHELL = Path(_WINDOWS_POWERSHELL).resolve(strict=True)
_PROJECT_VENV_PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
HEALTH_PYTHON = _PROJECT_VENV_PYTHON if _PROJECT_VENV_PYTHON.is_file() else Path(sys.executable)


def synthetic_v2_pointer(*, directory: str, version: str, digest: str) -> dict[str, object]:
    """Return a complete v2 pointer for isolated rollback launcher tests."""
    return {
        "schema_version": 2,
        "product": "JobFlow",
        "version_directory": directory,
        "version": version,
        "source_commit": "1" * 40,
        "source_payload_sha256": f"sha256:{digest}",
        "runtime_closure_manifest_sha256": "sha256:" + "2" * 64,
        "runtime_tree_sha256": "sha256:" + "3" * 64,
        "release_key_id": (
            "sha256:1037057f8578a60ac5b3dc030cb2d70a"
            "d945ec3b5fb51fa3944fcafa77146339"
        ),
        "bootstrap_version": "0.5.0",
        "platform": "windows-x64",
    }


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


def build_unified_installer_fixture(
    raw: str,
    *,
    companion_script: str,
    installer_mutator=None,
) -> dict[str, object]:
    """Create an isolated, dependency-free copy of the real unified installer."""
    fixture_root = Path(raw) / "source"
    local_app_data = Path(raw) / "LocalAppData"
    roaming_app_data = Path(raw) / "RoamingAppData"
    fixture_root.mkdir()
    local_app_data.mkdir()
    roaming_app_data.mkdir()
    for name in (".agents", "browser-companion", "config", "docs", "schemas", "scripts", "src", "tests"):
        (fixture_root / name).mkdir(parents=True, exist_ok=True)
    runtime_root = fixture_root / "scripts" / "windows-runtime"
    runtime_root.mkdir()
    shutil.copy2(
        PROJECT / "scripts" / "windows-runtime" / "jobflow-runtime-locks.ps1",
        runtime_root / "jobflow-runtime-locks.ps1",
    )
    shutil.copy2(
        PROJECT / "scripts" / "windows-runtime" / "jobflow-bootstrap.ps1",
        runtime_root / "jobflow-bootstrap.ps1",
    )
    for name in (
        "start-installed-jobflow.ps1", "check-installed-jobflow.ps1",
        "update-installed-jobflow.ps1", "rollback-installed-jobflow.ps1",
        "uninstall-installed-jobflow.ps1", "manage-authorized-discovery-task.ps1",
        "run-authorized-discovery-task.ps1", "Start JobFlow.cmd", "Check JobFlow.cmd",
        "Update JobFlow.cmd", "Rollback JobFlow.cmd", "Uninstall JobFlow.cmd",
    ):
        (runtime_root / name).write_text("exit 0\n", encoding="utf-8-sig")
    (fixture_root / ".jobops-root").write_text("JOBOPS_PROJECT_ROOT_V1\n", encoding="ascii")
    (fixture_root / "pyproject.toml").write_text(
        '[project]\nname = "jobflow-transaction-fixture"\nversion = "0.0.1"\n',
        encoding="utf-8",
    )
    for name in (
        "windows-cp311-requirements.lock",
        "windows-cp312-requirements.lock",
    ):
        shutil.copy2(PROJECT / "config" / name, fixture_root / "config" / name)
    (fixture_root / "scripts" / "check-jobflow.ps1").write_text(
        "param([switch]$Json, [string]$PythonPath = '')\nexit 0\n",
        encoding="utf-8-sig",
    )

    installer = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
    dependency_start = installer.index(
        '            $stagedPython = Join-Path $stagingRoot ".venv\\Scripts\\python.exe"'
    )
    dependency_end = installer.index(
        "            if (-not (Test-VersionHealth $stagingRoot)) {",
        dependency_start,
    )
    installer = (
        installer[:dependency_start]
        + '            $stagedPython = Join-Path $stagingRoot ".venv\\Scripts\\python.exe"\n'
        + '            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($stagedPython)) -Force | Out-Null\n'
        + '            [IO.File]::WriteAllText($stagedPython, "acceptance-only", (New-Object Text.UTF8Encoding($false)))\n\n'
        + installer[dependency_end:]
    )
    if installer_mutator is not None:
        installer = installer_mutator(installer)
    installer_path = fixture_root / "scripts" / "install-jobflow.ps1"
    installer_path.write_text(installer, encoding="utf-8-sig")
    (fixture_root / "scripts" / "install-jobflow-browser-companion.ps1").write_text(
        companion_script,
        encoding="utf-8-sig",
    )
    environment = ISOLATED_ENVIRONMENT.copy()
    environment.update({
        "LOCALAPPDATA": str(local_app_data),
        "APPDATA": str(roaming_app_data),
        "TEMP": str(Path(tempfile.gettempdir()).resolve(strict=True)),
        "TMP": str(Path(tempfile.gettempdir()).resolve(strict=True)),
    })
    command = [
        str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(installer_path), "-NoLaunch",
    ]
    return {
        "fixture_root": fixture_root,
        "local_app_data": local_app_data,
        "roaming_app_data": roaming_app_data,
        "runtime_root": runtime_root,
        "installer_path": installer_path,
        "environment": environment,
        "command": command,
    }


def create_directory_reparse(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    environment = ISOLATED_ENVIRONMENT.copy()
    environment["JOBFLOW_QA_REPARSE_LINK"] = str(link)
    environment["JOBFLOW_QA_REPARSE_TARGET"] = str(target)
    completed = __import__("subprocess").run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-Command",
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
            "-Path $env:JOBFLOW_QA_REPARSE_LINK "
            "-Target $env:JOBFLOW_QA_REPARSE_TARGET | Out-Null",
        ],
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)


def unlink_directory_reparse(link: Path) -> None:
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        os.rmdir(link)


def build_installed_rollback_fixture(raw: str, *, rollback_mutator=None) -> dict[str, object]:
    """Create a fixed-install runtime with two healthy synthetic versions."""
    local_app_data = Path(raw) / "LocalAppData"
    install_root = local_app_data / "JobOps"
    bin_root = install_root / "bin"
    data_root = install_root / "Data"
    versions_root = install_root / "Application" / "versions"
    bin_root.mkdir(parents=True)
    (data_root / "state").mkdir(parents=True)
    (data_root / ".jobflow-data-root").write_text(
        '{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}', encoding="utf-8"
    )
    pointers: dict[str, dict[str, object]] = {}
    for name, directory, version, digest in (
        ("current", "v0.4.1-aaaaaaaaaaaa", "0.4.1", "a" * 64),
        ("previous", "v0.4.0-bbbbbbbbbbbb", "0.4.0", "b" * 64),
    ):
        version_root = versions_root / directory
        (version_root / ".jobops-root").parent.mkdir(parents=True, exist_ok=True)
        (version_root / ".jobops-root").write_text("JOBOPS_PROJECT_ROOT_V1\n", encoding="ascii")
        (version_root / "runtime").mkdir(parents=True)
        (version_root / "runtime" / "python.exe").write_bytes(b"placeholder")
        (version_root / "scripts").mkdir()
        (version_root / "scripts" / "check-jobflow.ps1").write_text(
            "param([switch]$Json,[string]$PythonPath='')\n"
            "if ($Json) { '{\"status\":\"JOBFLOW_READY\"}' }\n"
            "exit 0\n",
            encoding="utf-8-sig",
        )
        pointer = synthetic_v2_pointer(directory=directory, version=version, digest=digest)
        pointers[name] = pointer
        (install_root / f"{name}.json").write_text(json.dumps(pointer), encoding="utf-8")
    for name in (
        "jobflow-runtime-locks.ps1",
        "rollback-installed-jobflow.ps1",
        "start-installed-jobflow.ps1",
        "check-installed-jobflow.ps1",
        "run-authorized-discovery-task.ps1",
    ):
        source = PROJECT / "scripts" / "windows-runtime" / name
        text = source.read_text(encoding="utf-8-sig")
        if name == "rollback-installed-jobflow.ps1" and rollback_mutator is not None:
            text = rollback_mutator(text)
        (bin_root / name).write_text(text, encoding="utf-8-sig")
    canonical_pointer = json.dumps(
        pointers["current"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    verifier_output = {
        "schema_version": 1,
        "status": "JOBFLOW_INSTALLED_RUNTIME_VERIFIED",
        "version": str(pointers["current"]["version"]),
        "manifest_sha256": "sha256:" + "4" * 64,
        "signature_envelope_sha256": "sha256:" + "5" * 64,
        "runtime_closure_manifest_sha256": pointers["current"]["runtime_closure_manifest_sha256"],
        "runtime_tree_sha256": pointers["current"]["runtime_tree_sha256"],
        "release_key_id": pointers["current"]["release_key_id"],
        "source_payload_sha256": pointers["current"]["source_payload_sha256"],
        "pointer_sha256": "sha256:" + hashlib.sha256(canonical_pointer).hexdigest(),
        "signed_activation_evidence_verified": True,
        "recovery_performed": False,
        "activation_committed_during_recovery": False,
        "paths_disclosed": False,
        "real_external_actions": 0,
    }
    (bin_root / "jobflow-bootstrap.ps1").write_text(
        "[CmdletBinding()] param([switch]$VerifyInstalled)\n"
        "if (-not $VerifyInstalled) { exit 2 }\n"
        + "'" + json.dumps(verifier_output, separators=(",", ":")) + "'\n"
        + "exit 0\n",
        encoding="utf-8-sig",
    )
    environment = ISOLATED_ENVIRONMENT.copy()
    environment.update({
        "LOCALAPPDATA": str(local_app_data),
        "TEMP": str(Path(tempfile.gettempdir()).resolve(strict=True)),
        "TMP": str(Path(tempfile.gettempdir()).resolve(strict=True)),
    })
    rollback_command = [
        str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(bin_root / "rollback-installed-jobflow.ps1"),
    ]
    return {
        "local_app_data": local_app_data,
        "install_root": install_root,
        "bin_root": bin_root,
        "data_root": data_root,
        "pointers": pointers,
        "environment": environment,
        "rollback_command": rollback_command,
    }


class WindowsLauncherTests(unittest.TestCase):
    @staticmethod
    def _rollback_contract_sources() -> tuple[str, str, str, str]:
        runtime = PROJECT / "scripts" / "windows-runtime"
        return (
            (runtime / "rollback-installed-jobflow.ps1").read_text(encoding="utf-8-sig"),
            (runtime / "jobflow-bootstrap.ps1").read_text(encoding="utf-8-sig"),
            (PROJECT / "tests" / "test_jobflow_bootstrap_rollback_v2.py").read_text(
                encoding="utf-8"
            ),
            (PROJECT / "tests" / "test_rollback_installed_jobflow_wrapper.py").read_text(
                encoding="utf-8"
            ),
        )

    def test_installer_wrapper_keeps_the_result_visible(self) -> None:
        wrapper = (PROJECT / "Install JobFlow.cmd").read_text(encoding="utf-8")
        self.assertIn(
            r'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"',
            wrapper,
        )
        self.assertNotIn("\npowershell.exe ", wrapper.lower())
        self.assertIn(r'-File "%~dp0scripts\install-jobflow-v2.ps1"', wrapper)
        self.assertNotIn(r'scripts\install-jobflow.ps1', wrapper)
        self.assertIn('set "JOBFLOW_INSTALL_EXIT=%ERRORLEVEL%"', wrapper)
        self.assertIn("JobFlow installation is ready.", wrapper)
        self.assertIn(
            r'%LOCALAPPDATA%\JobOps\bin\start-installed-jobflow.ps1',
            wrapper,
        )
        self.assertIn("pause", wrapper)
        self.assertIn("exit /b %JOBFLOW_INSTALL_EXIT%", wrapper)

    def test_localized_powershell_scripts_have_windows_utf8_bom(self) -> None:
        localized_scripts = (
            "check-jobflow.ps1",
            "check-release-readiness.ps1",
            "install-jobflow.ps1",
            "install-jobflow-v2.ps1",
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

    def test_installer_discovers_only_canonical_signed_python_without_path_lookup(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("function Get-CanonicalPythonCandidates", script)
        self.assertIn('[Environment]::GetFolderPath("LocalApplicationData")', script)
        self.assertIn('[Environment]::GetFolderPath("ProgramFiles")', script)
        self.assertIn('"Python Software Foundation"', script)
        self.assertIn("Microsoft.PowerShell.Security\\Get-AuthenticodeSignature", script)
        self.assertIn('Join-Path ([Environment]::SystemDirectory) "icacls.exe"', script)
        self.assertIn("JOBFLOW_TRUSTED_ICACLS_REQUIRED", script)
        self.assertIn(
            '(Get-OpenInstallerFileLinkCount $lock '
            '"JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED") -lt 1',
            script,
        )
        self.assertIn(
            '(Get-OpenInstallerFileLinkCount $icaclsExecutableLock '
            '"JOBFLOW_TRUSTED_ICACLS_REQUIRED") -lt 1',
            script,
        )
        self.assertNotIn('$env:SystemRoot\\System32\\icacls.exe', script)
        self.assertIn('[IO.FileShare]::Read', script)
        self.assertNotIn("Get-Command", script)
        self.assertNotIn('@{ Name = "python"', script)
        self.assertNotIn('@{ Name = "py"', script)
        self.assertIn("^CPython\\|(3\\.(11|12))\\|64\\|win32$", script)

    def test_installer_uses_versioned_fixed_target_and_checks_dependencies(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Push-Location $buildRoot', script)
        self.assertIn('(".b-" + $installId)', script)
        self.assertIn("Copy-VerifiedSourceSnapshot $stagingRoot $installFiles", script)
        self.assertIn("Copy-VerifiedSourceSnapshot $buildRoot $installFiles", script)
        self.assertNotIn("--editable", script)
        self.assertIn('"--no-build-isolation", "."', script)
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
        self.assertIn("function Enter-JobFlowFileLock", script)
        self.assertIn("function Exit-JobFlowFileLock", script)
        self.assertNotIn(". $lockHelpers", script)
        self.assertIn("Write-JsonAtomic", script)
        self.assertGreaterEqual(script.count("--quiet"), 2)
        self.assertIn('"-I", "-P", "-B", "-X", "utf8", "-m", "pip"', script)
        self.assertIn('"--require-virtualenv", "check"', script)
        self.assertIn('"config/windows-cp3$($python.Minor)-requirements.lock"', script)
        for name in (
            "windows-cp311-requirements.lock",
            "windows-cp312-requirements.lock",
        ):
            lock = (PROJECT / "config" / name).read_text(encoding="utf-8")
            self.assertIn("setuptools==80.10.2 --hash=sha256:", lock)
            self.assertIn("wheel==0.48.0 --hash=sha256:", lock)
            self.assertIn("pdfplumber==0.11.9 --hash=sha256:", lock)
            self.assertIn("python-docx==1.2.0 --hash=sha256:", lock)
        self.assertIn('"--no-deps"', script)
        self.assertIn('"--only-binary", ":all:"', script)
        self.assertIn('"--require-hashes"', script)
        self.assertIn('$start.EnvironmentVariables.Clear()', script)
        self.assertIn('$start.EnvironmentVariables["PIP_CONFIG_FILE"] = "NUL"', script)
        self.assertIn("JOBFLOW_RUNTIME_CLOSURE_UNATTESTED", script)
        self.assertIn("complete installed runtime closure is not yet independently attested", script)
        self.assertIn('install-jobflow-browser-companion.ps1', script)
        self.assertIn('-File $companionInstaller -NoLaunch', script)
        self.assertIn('-File $companionInstaller -OpenStoreOnly', script)
        self.assertIn('JOBFLOW_BROWSER_COMPANION_STORE_LAUNCH_FAILED', script)
        self.assertNotIn('$storeConfig.edge_addons_url', script)
        self.assertIn('"jobflow-runtime-locks.ps1"', script)
        self.assertIn('Enter-JobFlowFileLock $runtimeLockPath "JOBFLOW_INSTALL_RUNNING_INSTANCE_ACTIVE"', script)
        self.assertIn('Enter-JobFlowFileLock $discoveryLockPath "JOBFLOW_INSTALL_DISCOVERY_RUN_ACTIVE"', script)
        self.assertIn('JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY', script)
        self.assertIn('jobflow-fixed-install-qa-*', script)
        self.assertIn('JOBFLOW_INSTALL_ACCEPTANCE_BYPASS_FORBIDDEN', script)
        self.assertIn('Substring(0, 12)', script)
        self.assertIn('(".i-" + $installId)', script)
        self.assertIn('(".r-" + $installId)', script)
        self.assertIn("Install-StableLaunchers", script)
        self.assertIn("New-StableLauncherSnapshot", script)
        self.assertIn("Restore-StableLauncherSnapshot", script)
        self.assertIn("JOBFLOW_STABLE_LAUNCHER_ROLLBACK_FAILED", script)
        self.assertIn("JOBFLOW_STABLE_LAUNCHER_BACKUP_PRESERVED", script)
        self.assertIn("JOBFLOW_INSTALLED_POINTER_BACKUP_CLEANUP_FAILED", script)

        self.assertIn("JOBFLOW_INSTALLED_POINTER_BACKUP_PRESERVED", script)
        self.assertIn("JOBFLOW_INSTALLED_POINTER_COMMIT_UNKNOWN", script)
        self.assertIn("JOBFLOW_INSTALLED_POINTER_COMMIT_RECOVERED", script)
        self.assertIn("JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED", script)
        self.assertIn("JOBFLOW_INSTALL_REPAIR_BACKUP_LINKED", script)
        self.assertIn("Assert-StableStartMenuPath", script)
        self.assertIn("JOBFLOW_START_MENU_PATH_FORBIDDEN_OR_LINKED", script)
        self.assertIn("Assert-SourceRecord $record", script)
        self.assertGreaterEqual(script.count("Assert-StagedSourceSnapshot $stagingRoot $installFiles"), 1)
        self.assertIn("Assert-StagedSourceSnapshot $targetVersionRoot $installFiles", script)
        self.assertIn("JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL", script)
        self.assertIn("JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH", script)
        self.assertIn("function Get-FileSha256", script)
        self.assertIn("[IO.FileStream]::new", script)
        self.assertIn("[IO.FileShare]::Read", script)
        self.assertIn("$hasher.Dispose()", script)
        self.assertIn("$stream.Dispose()", script)
        self.assertNotIn("Get-FileHash", script)
        self.assertLess(
            script.index(
                '$discoveryLockStream = Enter-JobFlowFileLock $discoveryLockPath '
                '"JOBFLOW_INSTALL_DISCOVERY_RUN_ACTIVE"'
            ),
            script.index("$targetExistedBefore = Test-Path"),
        )
        self.assertIn('"update-installed-jobflow.ps1"', script)
        self.assertIn('"manage-authorized-discovery-task.ps1"', script)
        self.assertIn('"run-authorized-discovery-task.ps1"', script)
        self.assertIn('"Update JobFlow.cmd"', script)
        self.assertIn('@{ Name = "Update JobFlow.lnk"; Target = "Update JobFlow.cmd" }', script)
        self.assertIn('"/inheritance:r" "/grant:r" $grant', script)
        self.assertIn('(Join-Path $Path "*") "/reset" "/T" "/C"', script)
        self.assertIn("JOBFLOW_INSTALL_CHILD_ACL_FAILED", script)
        companion = (PROJECT / "scripts" / "install-jobflow-browser-companion.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('Join-Path $localRoot ".browser-companion-install.lock"', companion)
        self.assertIn("JOBFLOW_BROWSER_COMPANION_INSTALL_ALREADY_RUNNING", companion)
        self.assertLess(
            companion.index("$installLockStream = Enter-CompanionInstallLock"),
            companion.index("Get-Content -LiteralPath $sourceManifestPath"),
        )

    def test_unified_installer_ignores_path_python_and_powershell_shims(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-fixed-install-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw, companion_script="param([switch]$NoLaunch)\nexit 0\n"
            )
            fake_bin = Path(raw) / "caller-path"
            fake_bin.mkdir()
            marker = Path(raw) / "caller-path-used.txt"
            poison = (
                "@echo off\r\n"
                "echo caller-controlled executable used>\"%JOBFLOW_PROVENANCE_MARKER%\"\r\n"
                "exit /b 73\r\n"
            )
            (fake_bin / "python.cmd").write_text(poison, encoding="ascii")
            (fake_bin / "py.cmd").write_text(poison, encoding="ascii")
            (fake_bin / "powershell.cmd").write_text(poison, encoding="ascii")
            environment = dict(fixture["environment"])
            environment.update({
                "PATH": str(fake_bin),
                "JOBFLOW_PROVENANCE_MARKER": str(marker),
                "JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY": "1",
            })
            completed = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=environment,
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertFalse(marker.exists())

    def test_store_only_mode_validates_urls_before_any_binding_write(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-store-launch-qa-", dir=system_temp) as raw:
            fixture_root = Path(raw) / "source"
            scripts_root = fixture_root / "scripts"
            config_root = fixture_root / "config"
            companion_root = fixture_root / "browser-companion"
            local_app_data = Path(raw) / "LocalAppData"
            scripts_root.mkdir(parents=True)
            config_root.mkdir()
            companion_root.mkdir()
            local_app_data.mkdir()
            shutil.copy2(
                PROJECT / "scripts" / "install-jobflow-browser-companion.ps1",
                scripts_root / "install-jobflow-browser-companion.ps1",
            )
            shutil.copy2(PROJECT / ".jobops-root", fixture_root / ".jobops-root")
            shutil.copy2(
                PROJECT / "browser-companion" / "manifest.json",
                companion_root / "manifest.json",
            )
            store_config = json.loads(
                (PROJECT / "config" / "browser-companion-stores.json").read_text(encoding="utf-8")
            )
            environment = ISOLATED_ENVIRONMENT.copy()
            environment["LOCALAPPDATA"] = str(local_app_data)
            command = [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(scripts_root / "install-jobflow-browser-companion.ps1"),
                "-OpenStoreOnly", "-NoLaunch",
            ]

            for invalid_url in (
                "https://chromewebstore.google.com/detail/pgcnlkfakkacphkdojdbphccjnbbefic?unexpected=1",
                "https://user" + chr(64) + "chromewebstore.google.com/detail/pgcnlkfakkacphkdojdbphccjnbbefic",
                "https://chromewebstore.google.com:444/detail/pgcnlkfakkacphkdojdbphccjnbbefic",
            ):
                with self.subTest(url=invalid_url):
                    store_config["chrome_web_store_url"] = invalid_url
                    (config_root / "browser-companion-stores.json").write_text(
                        json.dumps(store_config), encoding="utf-8"
                    )
                    invalid = __import__("subprocess").run(
                        command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                        errors="replace", capture_output=True, timeout=30, check=False,
                    )
                    self.assertNotEqual(invalid.returncode, 0)
                    self.assertIn(
                        "JOBFLOW_BROWSER_COMPANION_STORE_CONFIG_INVALID",
                        invalid.stdout + invalid.stderr,
                    )
                    self.assertFalse((local_app_data / "JobOps").exists())

            shutil.copy2(
                PROJECT / "config" / "browser-companion-stores.json",
                config_root / "browser-companion-stores.json",
            )
            valid = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=30, check=False,
            )
            self.assertNotEqual(valid.returncode, 0)
            self.assertIn(
                "JOBFLOW_BROWSER_COMPANION_LAUNCH_MODE_INVALID",
                valid.stdout + valid.stderr,
            )
            self.assertFalse((local_app_data / "JobOps").exists())

    def test_companion_reinstall_preserves_binding_and_native_failure_rolls_back(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-binding-transaction-qa-", dir=system_temp) as raw:
            fixture_root = Path(raw) / "source"
            scripts_root = fixture_root / "scripts"
            config_root = fixture_root / "config"
            companion_root = fixture_root / "browser-companion"
            local_app_data = Path(raw) / "LocalAppData"
            scripts_root.mkdir(parents=True)
            config_root.mkdir()
            companion_root.mkdir()
            local_app_data.mkdir()
            shutil.copy2(
                PROJECT / "scripts" / "install-jobflow-browser-companion.ps1",
                scripts_root / "install-jobflow-browser-companion.ps1",
            )
            shutil.copy2(PROJECT / ".jobops-root", fixture_root / ".jobops-root")
            shutil.copy2(
                PROJECT / "browser-companion" / "manifest.json",
                companion_root / "manifest.json",
            )
            shutil.copy2(
                PROJECT / "config" / "browser-companion-stores.json",
                config_root / "browser-companion-stores.json",
            )
            marker = companion_root / "runtime-marker.txt"
            marker.write_text("first", encoding="utf-8")
            native_stub = scripts_root / "install-jobflow-native-host.ps1"
            native_stub.write_text("exit 0\n", encoding="ascii")
            environment = ISOLATED_ENVIRONMENT.copy()
            environment["LOCALAPPDATA"] = str(local_app_data)
            command = [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(scripts_root / "install-jobflow-browser-companion.ps1"), "-NoLaunch",
            ]

            first = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            jobops_root = local_app_data / "JobOps"
            binding_path = jobops_root / "browser-companion-binding.json"
            runtime_marker = jobops_root / "BrowserCompanion" / "runtime-marker.txt"
            first_binding = binding_path.read_bytes()
            self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "first")

            marker.write_text("failed-update", encoding="utf-8")
            native_stub.write_text("exit 1\n", encoding="ascii")
            failed = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=30, check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("JOBFLOW_NATIVE_HOST_INSTALL_FAILED", failed.stdout + failed.stderr)
            self.assertEqual(binding_path.read_bytes(), first_binding)
            self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "first")
            self.assertFalse(any(jobops_root.glob(".BrowserCompanion.*-*")))
            self.assertFalse(any(jobops_root.glob(".browser-companion-binding-*")))

            marker.write_text("second", encoding="utf-8")
            native_stub.write_text("exit 0\n", encoding="ascii")
            second = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(binding_path.read_bytes(), first_binding)
            self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "second")

    def test_unified_installer_rolls_back_core_when_companion_activation_fails(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-unified-transaction-qa-", dir=system_temp) as raw:
            fixture_root = Path(raw) / "source"
            local_app_data = Path(raw) / "LocalAppData"
            roaming_app_data = Path(raw) / "RoamingAppData"
            fixture_root.mkdir()
            local_app_data.mkdir()
            roaming_app_data.mkdir()
            for name in (".agents", "browser-companion", "config", "docs", "schemas", "scripts", "src", "tests"):
                (fixture_root / name).mkdir(parents=True, exist_ok=True)
            runtime_root = fixture_root / "scripts" / "windows-runtime"
            runtime_root.mkdir()
            shutil.copy2(
                PROJECT / "scripts" / "windows-runtime" / "jobflow-runtime-locks.ps1",
                runtime_root / "jobflow-runtime-locks.ps1",
            )
            shutil.copy2(
                PROJECT / "scripts" / "windows-runtime" / "jobflow-bootstrap.ps1",
                runtime_root / "jobflow-bootstrap.ps1",
            )
            for name in (
                "start-installed-jobflow.ps1", "check-installed-jobflow.ps1",
                "update-installed-jobflow.ps1", "rollback-installed-jobflow.ps1",
                "uninstall-installed-jobflow.ps1", "manage-authorized-discovery-task.ps1",
                "run-authorized-discovery-task.ps1", "Start JobFlow.cmd", "Check JobFlow.cmd",
                "Update JobFlow.cmd", "Rollback JobFlow.cmd", "Uninstall JobFlow.cmd",
            ):
                (runtime_root / name).write_text("exit 0\n", encoding="utf-8-sig")
            (fixture_root / ".jobops-root").write_text("JOBOPS_PROJECT_ROOT_V1\n", encoding="ascii")
            (fixture_root / "pyproject.toml").write_text(
                '[project]\nname = "jobflow-transaction-fixture"\nversion = "0.0.1"\n',
                encoding="utf-8",
            )
            for name in (
                "windows-cp311-requirements.lock",
                "windows-cp312-requirements.lock",
            ):
                shutil.copy2(PROJECT / "config" / name, fixture_root / "config" / name)
            (fixture_root / "scripts" / "check-jobflow.ps1").write_text(
                "param([switch]$Json, [string]$PythonPath = '')\nexit 0\n",
                encoding="utf-8-sig",
            )

            installer = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
            dependency_start = installer.index(
                '            $stagedPython = Join-Path $stagingRoot ".venv\\Scripts\\python.exe"'
            )
            dependency_end = installer.index(
                "            if (-not (Test-VersionHealth $stagingRoot)) {",
                dependency_start,
            )
            installer = (
                installer[:dependency_start]
                + '            $stagedPython = Join-Path $stagingRoot ".venv\\Scripts\\python.exe"\n'
                + '            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($stagedPython)) -Force | Out-Null\n'
                + '            [IO.File]::WriteAllText($stagedPython, "acceptance-only", (New-Object Text.UTF8Encoding($false)))\n\n'
                + installer[dependency_end:]
            )
            installer_path = fixture_root / "scripts" / "install-jobflow.ps1"
            installer_path.write_text(installer, encoding="utf-8-sig")
            companion_installer = fixture_root / "scripts" / "install-jobflow-browser-companion.ps1"
            companion_installer.write_text(
                "param([switch]$NoLaunch)\n"
                "$target = Join-Path $env:LOCALAPPDATA 'JobOps\\BrowserCompanion'\n"
                "New-Item -ItemType Directory -Path $target -Force | Out-Null\n"
                "[IO.File]::WriteAllText((Join-Path $target 'runtime-marker.txt'), 'first', "
                "(New-Object Text.UTF8Encoding($false)))\nexit 0\n",
                encoding="utf-8-sig",
            )
            environment = ISOLATED_ENVIRONMENT.copy()
            environment.update({
                "LOCALAPPDATA": str(local_app_data),
                "APPDATA": str(roaming_app_data),
                "TEMP": str(system_temp),
                "TMP": str(system_temp),
            })
            command = [
                str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(installer_path), "-NoLaunch",
            ]
            first = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=60, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            install_root = local_app_data / "JobOps"
            current_path = install_root / "current.json"
            first_pointer = json.loads(current_path.read_text(encoding="utf-8"))
            first_version_root = install_root / "Application" / "versions" / first_pointer["version_directory"]
            self.assertTrue(first_version_root.is_dir())
            self.assertEqual(
                (install_root / "BrowserCompanion" / "runtime-marker.txt").read_text(encoding="utf-8"),
                "first",
            )

            launcher_targets = [
                *(install_root / "bin").glob("*.ps1"),
                *install_root.glob("*.cmd"),
                *(roaming_app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "JobFlow").glob("*.lnk"),
            ]
            self.assertEqual(len(launcher_targets), 19)
            launcher_snapshot = {
                str(path.relative_to(Path(raw))): path.read_bytes()
                for path in launcher_targets
            }

            (runtime_root / "start-installed-jobflow.ps1").write_text(
                "throw 'new launcher must roll back'\n", encoding="utf-8-sig"
            )
            companion_installer.write_text("param([switch]$NoLaunch)\nexit 1\n", encoding="utf-8-sig")
            failed = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=60, check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("JOBFLOW_BROWSER_COMPANION_INSTALL_FAILED", failed.stdout + failed.stderr)
            self.assertEqual(json.loads(current_path.read_text(encoding="utf-8")), first_pointer)
            self.assertFalse((install_root / "previous.json").exists())
            self.assertEqual(
                sorted(path.name for path in (install_root / "Application" / "versions").iterdir()),
                [first_pointer["version_directory"]],
            )
            self.assertEqual(
                (install_root / "BrowserCompanion" / "runtime-marker.txt").read_text(encoding="utf-8"),
                "first",
            )
            for relative, expected in launcher_snapshot.items():
                with self.subTest(launcher=relative):
                    self.assertEqual((Path(raw) / relative).read_bytes(), expected)
            self.assertFalse(any(install_root.glob(".l-*")))

    def test_unified_installer_preserves_new_target_when_launcher_rollback_fails(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-launcher-rollback-failure-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw,
                companion_script="param([switch]$NoLaunch)\nexit 0\n",
            )
            command = fixture["command"]
            fixture_root = fixture["fixture_root"]
            environment = fixture["environment"]
            local_app_data = fixture["local_app_data"]
            runtime_root = fixture["runtime_root"]
            installer_path = fixture["installer_path"]
            self.assertIsInstance(command, list)
            self.assertIsInstance(fixture_root, Path)
            self.assertIsInstance(environment, dict)
            self.assertIsInstance(local_app_data, Path)
            self.assertIsInstance(runtime_root, Path)
            self.assertIsInstance(installer_path, Path)

            first = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=60, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            install_root = local_app_data / "JobOps"
            current_path = install_root / "current.json"
            first_pointer = json.loads(current_path.read_text(encoding="utf-8"))

            (runtime_root / "start-installed-jobflow.ps1").write_text(
                "throw 'new launcher target must be retained'\n",
                encoding="utf-8-sig",
            )
            companion_installer = fixture_root / "scripts" / "install-jobflow-browser-companion.ps1"
            companion_installer.write_text("param([switch]$NoLaunch)\nexit 1\n", encoding="utf-8-sig")
            installer = installer_path.read_text(encoding="utf-8-sig")
            restore_anchor = "function Restore-StableLauncherSnapshot([object]$Snapshot) {\n"
            self.assertEqual(installer.count(restore_anchor), 1)
            installer = installer.replace(
                restore_anchor,
                restore_anchor + '    throw "INJECTED_LAUNCHER_ROLLBACK_FAILURE"\n',
                1,
            )
            installer_path.write_text(installer, encoding="utf-8-sig")

            failed = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=60, check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("JOBFLOW_STABLE_LAUNCHER_ROLLBACK_FAILED", failed.stdout + failed.stderr)
            self.assertEqual(json.loads(current_path.read_text(encoding="utf-8")), first_pointer)

            version_roots = sorted(
                path for path in (install_root / "Application" / "versions").iterdir()
                if path.is_dir()
            )
            self.assertEqual(len(version_roots), 2)
            retained_targets = [
                path for path in version_roots
                if path.name != first_pointer["version_directory"]
            ]
            self.assertEqual(len(retained_targets), 1)
            self.assertTrue((retained_targets[0] / ".jobops-root").is_file())
            self.assertTrue(any(install_root.glob(".l-*")))

    def test_atomic_pointer_cleanup_failure_does_not_undo_committed_activation(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-pointer-cleanup-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw,
                companion_script="param([switch]$NoLaunch)\nexit 0\n",
            )
            command = fixture["command"]
            fixture_root = fixture["fixture_root"]
            environment = fixture["environment"]
            local_app_data = fixture["local_app_data"]
            self.assertIsInstance(command, list)
            self.assertIsInstance(fixture_root, Path)
            self.assertIsInstance(environment, dict)
            self.assertIsInstance(local_app_data, Path)
            first = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=60, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            install_root = local_app_data / "JobOps"
            current_path = install_root / "current.json"
            first_pointer = json.loads(current_path.read_text(encoding="utf-8"))

            installer_path = fixture["installer_path"]
            self.assertIsInstance(installer_path, Path)
            installer = installer_path.read_text(encoding="utf-8-sig")
            cleanup = '        try { Remove-Item -LiteralPath $backup -Force }'
            self.assertEqual(installer.count(cleanup), 1)
            installer = installer.replace(
                cleanup,
                '        try { throw "INJECTED_POINTER_BACKUP_CLEANUP_FAILURE" }',
                1,
            )
            installer_path.write_text(installer, encoding="utf-8-sig")

            second = __import__("subprocess").run(
                command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=60, check=False,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn(
                "JOBFLOW_INSTALLED_POINTER_BACKUP_CLEANUP_FAILED",
                second.stdout + second.stderr,
            )
            second_pointer = json.loads(current_path.read_text(encoding="utf-8"))
            self.assertNotEqual(second_pointer["version_directory"], first_pointer["version_directory"])
            active_root = install_root / "Application" / "versions" / second_pointer["version_directory"]
            self.assertTrue(active_root.is_dir())
            self.assertTrue((active_root / ".jobops-root").is_file())
            self.assertTrue(any(install_root.glob("current.json.*.backup")))

    def test_competing_same_version_failure_never_deletes_the_winner(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-same-version-race-qa-", dir=system_temp) as raw:
            signal_path = Path(raw) / "first-lock-acquired.signal"

            def inject_lock_signal(installer: str) -> str:
                anchor = (
                    '$discoveryLockStream = Enter-JobFlowFileLock $discoveryLockPath '
                    '"JOBFLOW_INSTALL_DISCOVERY_RUN_ACTIVE"'
                )
                self.assertEqual(installer.count(anchor), 1)
                return installer.replace(
                    anchor,
                    anchor + "\n"
                    '    if (-not [string]::IsNullOrWhiteSpace($env:JOBFLOW_QA_LOCK_SIGNAL)) {\n'
                    '        [IO.File]::WriteAllText($env:JOBFLOW_QA_LOCK_SIGNAL, "locked", '
                    '(New-Object Text.UTF8Encoding($false)))\n'
                    '        Start-Sleep -Seconds 3\n'
                    '    }',
                    1,
                )

            fixture = build_unified_installer_fixture(
                raw,
                companion_script=(
                    "param([switch]$NoLaunch)\n"
                    "$countPath = Join-Path $env:LOCALAPPDATA 'companion-call-count.txt'\n"
                    "$count = 0\n"
                    "if (Test-Path -LiteralPath $countPath -PathType Leaf) { "
                    "$count = [int](Get-Content -LiteralPath $countPath -Raw) }\n"
                    "$count += 1\n"
                    "[IO.File]::WriteAllText($countPath, [string]$count, "
                    "(New-Object Text.UTF8Encoding($false)))\n"
                    "if ($count -gt 1) { exit 1 }\n"
                    "exit 0\n"
                ),
                installer_mutator=inject_lock_signal,
            )
            command = fixture["command"]
            fixture_root = fixture["fixture_root"]
            environment = fixture["environment"]
            local_app_data = fixture["local_app_data"]
            self.assertIsInstance(command, list)
            self.assertIsInstance(fixture_root, Path)
            self.assertIsInstance(environment, dict)
            self.assertIsInstance(local_app_data, Path)
            environment["JOBFLOW_QA_LOCK_SIGNAL"] = str(signal_path)

            first = RealPopen(
                command, cwd=fixture_root, env=environment, stdin=DEVNULL,
                stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="replace",
            )
            second = None
            try:
                deadline = time.monotonic() + 15
                while not signal_path.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(signal_path.is_file(), "the first installer never acquired the lock")
                second = RealPopen(
                    command, cwd=fixture_root, env=environment, stdin=DEVNULL,
                    stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="replace",
                )
                first_stdout, first_stderr = first.communicate(timeout=60)
                second_stdout, second_stderr = second.communicate(timeout=60)
                self.assertEqual(first.returncode, 0, first_stdout + first_stderr)
                self.assertNotEqual(second.returncode, 0)
                self.assertIn(
                    "JOBFLOW_BROWSER_COMPANION_INSTALL_FAILED",
                    second_stdout + second_stderr,
                )
            finally:
                if first.poll() is None:
                    first.kill()
                    first.wait(timeout=5)
                if second is not None and second.poll() is None:
                    second.kill()
                    second.wait(timeout=5)

            install_root = local_app_data / "JobOps"
            pointer = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
            winner_root = install_root / "Application" / "versions" / pointer["version_directory"]
            self.assertTrue(winner_root.is_dir())
            self.assertTrue((winner_root / ".jobops-root").is_file())
            self.assertEqual(
                sorted(path.name for path in (install_root / "Application" / "versions").iterdir()),
                [pointer["version_directory"]],
            )

    def test_start_menu_programs_junction_is_rejected_before_shortcut_write(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-start-menu-link-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw,
                companion_script="param([switch]$NoLaunch)\nexit 0\n",
            )
            command = fixture["command"]
            fixture_root = fixture["fixture_root"]
            environment = fixture["environment"]
            roaming_app_data = fixture["roaming_app_data"]
            self.assertIsInstance(command, list)
            self.assertIsInstance(fixture_root, Path)
            self.assertIsInstance(environment, dict)
            self.assertIsInstance(roaming_app_data, Path)
            programs_link = roaming_app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            programs_link.parent.mkdir(parents=True)
            outside = Path(raw) / "outside-start-menu"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            create_directory_reparse(programs_link, outside)
            try:
                completed = __import__("subprocess").run(
                    command, cwd=fixture_root, env=environment, text=True, encoding="utf-8",
                    errors="replace", capture_output=True, timeout=60, check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(
                    "JOBFLOW_START_MENU_PATH_FORBIDDEN_OR_LINKED",
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
                self.assertFalse((outside / "JobFlow").exists())
                self.assertFalse(any(outside.glob("*.lnk")))
            finally:
                unlink_directory_reparse(programs_link)

    def test_installed_pointer_directory_is_rejected_without_false_success(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-pointer-directory-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw, companion_script="param([switch]$NoLaunch)\nexit 0\n"
            )
            install_root = fixture["local_app_data"] / "JobOps"
            current_path = install_root / "current.json"
            current_path.mkdir(parents=True)

            completed = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_INSTALLED_POINTER_INVALID", completed.stdout + completed.stderr)
            self.assertTrue(current_path.is_dir())
            self.assertEqual(list(current_path.iterdir()), [])
            versions_root = install_root / "Application" / "versions"
            if versions_root.exists():
                self.assertEqual(list(versions_root.iterdir()), [])

    def test_runtime_data_descendant_junction_is_rejected_before_acl_recursion(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-data-junction-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw, companion_script="param([switch]$NoLaunch)\nexit 0\n"
            )
            install_root = fixture["local_app_data"] / "JobOps"
            data_root = install_root / "Data"
            data_root.mkdir(parents=True)
            outside = Path(raw) / "outside-data"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            workspace_link = data_root / "workspace"
            create_directory_reparse(workspace_link, outside)
            try:
                completed = __import__("subprocess").run(
                    fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                    text=True, encoding="utf-8", errors="replace", capture_output=True,
                    timeout=60, check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(
                    "JOBFLOW_RUNTIME_DATA_REPARSE_FORBIDDEN",
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
                self.assertFalse((outside / ".jobflow-data-root").exists())
                self.assertFalse((outside / "state").exists())
                self.assertFalse((outside / "reports").exists())
                self.assertFalse((install_root / "current.json").exists())
            finally:
                unlink_directory_reparse(workspace_link)

    def test_unified_installer_uses_immutable_inlined_lock_code(self) -> None:
        installer = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("function Enter-JobFlowFileLock", installer)
        self.assertIn("function Exit-JobFlowFileLock", installer)
        self.assertNotIn(". $lockHelpers", installer)
        self.assertNotIn("Assert-SourcePath $lockHelpers", installer)

    def test_source_snapshot_rejects_synchronized_change_and_reparse_before_copy(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)

        def inject_source_pause(installer: str) -> str:
            anchor = (
                "        try {\n"
                "            $stagingDirectoryContext = "
                "Copy-VerifiedSourceSnapshot $stagingRoot $installFiles"
            )
            self.assertEqual(installer.count(anchor), 1)
            return installer.replace(
                anchor,
                "        try {\n"
                '            [IO.File]::WriteAllText($env:JOBFLOW_QA_SOURCE_SIGNAL, "ready", '
                '(New-Object Text.UTF8Encoding($false)))\n'
                "            Start-Sleep -Seconds 3\n"
                "            $stagingDirectoryContext = "
                "Copy-VerifiedSourceSnapshot $stagingRoot $installFiles",
                1,
            )

        for scenario in ("content-change", "directory-reparse"):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory(prefix=f"jobflow-source-{scenario}-qa-", dir=system_temp) as raw:
                    signal_path = Path(raw) / "source-snapshot-ready.signal"
                    fixture = build_unified_installer_fixture(
                        raw,
                        companion_script="param([switch]$NoLaunch)\nexit 0\n",
                        installer_mutator=inject_source_pause,
                    )
                    command = fixture["command"]
                    fixture_root = fixture["fixture_root"]
                    environment = fixture["environment"]
                    local_app_data = fixture["local_app_data"]
                    runtime_root = fixture["runtime_root"]
                    self.assertIsInstance(command, list)
                    self.assertIsInstance(fixture_root, Path)
                    self.assertIsInstance(environment, dict)
                    self.assertIsInstance(local_app_data, Path)
                    self.assertIsInstance(runtime_root, Path)
                    environment["JOBFLOW_QA_SOURCE_SIGNAL"] = str(signal_path)
                    process = RealPopen(
                        command, cwd=fixture_root, env=environment, stdin=DEVNULL,
                        stdout=PIPE, stderr=PIPE, text=True, encoding="utf-8", errors="replace",
                    )
                    runtime_backup = None
                    runtime_linked = False
                    try:
                        deadline = time.monotonic() + 15
                        while not signal_path.is_file() and time.monotonic() < deadline:
                            time.sleep(0.05)
                        self.assertTrue(signal_path.is_file(), "installer did not reach the synchronized copy gate")
                        if scenario == "content-change":
                            (runtime_root / "start-installed-jobflow.ps1").write_text(
                                "throw 'source changed after inventory'\n",
                                encoding="utf-8-sig",
                            )
                        else:
                            outside = Path(raw) / "outside-runtime"
                            shutil.copytree(runtime_root, outside)
                            runtime_backup = runtime_root.with_name("windows-runtime-original")
                            runtime_root.rename(runtime_backup)
                            create_directory_reparse(runtime_root, outside)
                            runtime_linked = True
                        stdout, stderr = process.communicate(timeout=30)
                        self.assertNotEqual(process.returncode, 0)
                        expected = (
                            "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL"
                            if scenario == "content-change"
                            else "JOBFLOW_INSTALL_SOURCE_LINK_FORBIDDEN"
                        )
                        self.assertIn(expected, stdout + stderr)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        if runtime_linked:
                            unlink_directory_reparse(runtime_root)
                        if runtime_backup is not None and runtime_backup.exists() and not runtime_root.exists():
                            runtime_backup.rename(runtime_root)

                    install_root = local_app_data / "JobOps"
                    self.assertFalse((install_root / "current.json").exists())
                    versions_root = install_root / "Application" / "versions"
                    self.assertTrue(versions_root.is_dir())
                    self.assertEqual(list(versions_root.iterdir()), [])
                    self.assertFalse(any(install_root.glob(".i-*")))

    def test_build_artifacts_are_isolated_but_unknown_staging_files_are_rejected(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)

        def add_artifact(root_variable: str):
            def mutate(installer: str) -> str:
                anchor = "            if (-not (Test-VersionHealth $stagingRoot)) {"
                self.assertEqual(installer.count(anchor), 1)
                injected = (
                    f'            [IO.File]::WriteAllText((Join-Path ${root_variable} "generated.tmp"), '
                    '"generated", (New-Object Text.UTF8Encoding($false)))\n'
                )
                return installer.replace(anchor, injected + anchor, 1)

            return mutate

        with tempfile.TemporaryDirectory(prefix="jobflow-build-root-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw,
                companion_script="param([switch]$NoLaunch)\nexit 0\n",
                installer_mutator=add_artifact("buildRoot"),
            )
            completed = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            install_root = fixture["local_app_data"] / "JobOps"
            pointer = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
            target = install_root / "Application" / "versions" / pointer["version_directory"]
            self.assertFalse((target / "generated.tmp").exists())
            self.assertFalse(any(install_root.glob(".b-*")))

        with tempfile.TemporaryDirectory(prefix="jobflow-staging-artifact-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw,
                companion_script="param([switch]$NoLaunch)\nexit 0\n",
                installer_mutator=add_artifact("stagingRoot"),
            )
            completed = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH", completed.stdout + completed.stderr)
            self.assertFalse((fixture["local_app_data"] / "JobOps" / "current.json").exists())

    def test_late_project_launcher_mutation_cannot_change_committed_launcher_bytes(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-late-launcher-qa-", dir=system_temp) as raw:
            signal = Path(raw) / "target-committed.signal"

            def pause_before_launchers(installer: str) -> str:
                anchor = "        Install-StableLaunchers $targetVersionRoot $installFiles"
                self.assertEqual(installer.count(anchor), 1)
                return installer.replace(
                    anchor,
                    '        [IO.File]::WriteAllText($env:JOBFLOW_QA_LATE_LAUNCHER_SIGNAL, "ready", '
                    '(New-Object Text.UTF8Encoding($false)))\n'
                    "        Start-Sleep -Seconds 3\n" + anchor,
                    1,
                )

            fixture = build_unified_installer_fixture(
                raw,
                companion_script="param([switch]$NoLaunch)\nexit 0\n",
                installer_mutator=pause_before_launchers,
            )
            environment = fixture["environment"]
            environment["JOBFLOW_QA_LATE_LAUNCHER_SIGNAL"] = str(signal)
            process = RealPopen(
                fixture["command"], cwd=fixture["fixture_root"], env=environment,
                stdin=DEVNULL, stdout=PIPE, stderr=PIPE, text=True,
                encoding="utf-8", errors="replace",
            )
            try:
                deadline = time.monotonic() + 15
                while not signal.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(signal.is_file(), "installer did not reach the launcher gate")
                (fixture["runtime_root"] / "start-installed-jobflow.ps1").write_text(
                    "throw 'late unverified source mutation'\n", encoding="utf-8-sig"
                )
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stdout + stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            installed = fixture["local_app_data"] / "JobOps" / "bin" / "start-installed-jobflow.ps1"
            self.assertEqual(installed.read_text(encoding="utf-8-sig"), "exit 0\n")

    def test_existing_version_descendant_reparse_is_rejected_before_execution(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-existing-reparse-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw, companion_script="param([switch]$NoLaunch)\nexit 0\n"
            )
            first = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            install_root = fixture["local_app_data"] / "JobOps"
            current_path = install_root / "current.json"
            original_pointer = json.loads(current_path.read_text(encoding="utf-8"))
            target = install_root / "Application" / "versions" / original_pointer["version_directory"]
            scripts = target / "scripts"
            scripts_backup = target / "scripts-original"
            scripts.rename(scripts_backup)
            outside = Path(raw) / "outside-version-scripts"
            outside.mkdir()
            sentinel = Path(raw) / "external-health-executed.txt"
            (outside / "check-jobflow.ps1").write_text(
                'param([switch]$Json, [string]$PythonPath = "")\n'
                f'[IO.File]::WriteAllText("{str(sentinel).replace(chr(34), chr(34) * 2)}", "executed")\n'
                "exit 0\n",
                encoding="utf-8-sig",
            )
            create_directory_reparse(scripts, outside)
            try:
                failed = __import__("subprocess").run(
                    fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                    text=True, encoding="utf-8", errors="replace", capture_output=True,
                    timeout=60, check=False,
                )
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn("JOBFLOW_INSTALL_EXISTING_VERSION_LINKED", failed.stdout + failed.stderr)
                self.assertFalse(sentinel.exists())
                self.assertEqual(json.loads(current_path.read_text(encoding="utf-8")), original_pointer)
            finally:
                unlink_directory_reparse(scripts)
                scripts_backup.rename(scripts)

    def test_installed_pointer_rejects_coercible_schema_types(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-pointer-types-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw, companion_script="param([switch]$NoLaunch)\nexit 0\n"
            )
            first = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            current_path = fixture["local_app_data"] / "JobOps" / "current.json"
            valid = json.loads(current_path.read_text(encoding="utf-8"))
            for bad_schema in ("1", True, 1.0, [1], {"value": 1}):
                with self.subTest(schema_version=bad_schema):
                    malformed = dict(valid)
                    malformed["schema_version"] = bad_schema
                    current_path.write_text(json.dumps(malformed), encoding="utf-8")
                    failed = __import__("subprocess").run(
                        fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                        text=True, encoding="utf-8", errors="replace", capture_output=True,
                        timeout=60, check=False,
                    )
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn("JOBFLOW_INSTALLED_POINTER_INVALID", failed.stdout + failed.stderr)
                    current_path.write_text(json.dumps(valid), encoding="utf-8")

    def test_pointer_commit_unknown_preserves_candidate_and_atomic_backup(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-pointer-unknown-qa-", dir=system_temp) as raw:
            fixture = build_unified_installer_fixture(
                raw, companion_script="param([switch]$NoLaunch)\nexit 0\n"
            )
            first = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            install_root = fixture["local_app_data"] / "JobOps"
            original = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
            (fixture["runtime_root"] / "start-installed-jobflow.ps1").write_text(
                "exit 0 # next version\n", encoding="utf-8-sig"
            )
            installer_path = fixture["installer_path"]
            installer = installer_path.read_text(encoding="utf-8-sig")
            anchor = "            [IO.File]::Replace($temporary, $Path, $backup, $true)"
            self.assertEqual(installer.count(anchor), 1)
            installer = installer.replace(
                anchor,
                anchor + "\n"
                "            [IO.File]::WriteAllText($Path, '{\"unknown\":true}', "
                "(New-Object Text.UTF8Encoding($false)))\n"
                '            throw "INJECTED_AFTER_POINTER_REPLACE"',
                1,
            )
            installer_path.write_text(installer, encoding="utf-8-sig")
            failed = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("JOBFLOW_INSTALLED_POINTER_COMMIT_UNKNOWN", failed.stdout + failed.stderr)
            self.assertEqual(json.loads((install_root / "current.json").read_text(encoding="utf-8")), {"unknown": True})
            versions = [path for path in (install_root / "Application" / "versions").iterdir() if path.is_dir()]
            self.assertEqual(len(versions), 2)
            self.assertTrue(any(path.name != original["version_directory"] for path in versions))
            self.assertTrue(any(install_root.glob("current.json.*.backup")))

    def test_launcher_backup_cleanup_failure_preserves_original_activation_error(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-launcher-cleanup-qa-", dir=system_temp) as raw:
            companion = Path(raw) / "source" / "scripts" / "install-jobflow-browser-companion.ps1"
            fixture = build_unified_installer_fixture(
                raw, companion_script="param([switch]$NoLaunch)\nexit 0\n"
            )
            first = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            install_root = fixture["local_app_data"] / "JobOps"
            current_path = install_root / "current.json"
            original_pointer = json.loads(current_path.read_text(encoding="utf-8"))
            installed_launcher = install_root / "bin" / "start-installed-jobflow.ps1"
            original_launcher = installed_launcher.read_bytes()

            (fixture["runtime_root"] / "start-installed-jobflow.ps1").write_text(
                "throw 'next launcher'\n", encoding="utf-8-sig"
            )
            companion.write_text("param([switch]$NoLaunch)\nexit 1\n", encoding="utf-8-sig")
            installer_path = fixture["installer_path"]
            installer = installer_path.read_text(encoding="utf-8-sig")
            cleanup = (
                '            Assert-LocalTreeNoReparse $launcherRollbackRoot "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"\n'
                '            Remove-SafeInstallerTree $launcherRollbackRoot '
                '"JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"'
            )
            restore_start = installer.index("function Restore-StableLauncherSnapshot")
            restore_end = installer.index("function Install-StableLaunchers", restore_start)
            restore_function = installer[restore_start:restore_end]
            self.assertEqual(restore_function.count(cleanup), 1)
            restore_function = restore_function.replace(
                cleanup,
                '            Assert-LocalTreeNoReparse $launcherRollbackRoot "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"\n'
                '            throw "INJECTED_LAUNCHER_BACKUP_CLEANUP_FAILURE"',
                1,
            )
            installer = installer[:restore_start] + restore_function + installer[restore_end:]
            installer_path.write_text(installer, encoding="utf-8-sig")

            failed = __import__("subprocess").run(
                fixture["command"], cwd=fixture["fixture_root"], env=fixture["environment"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=60, check=False,
            )
            output = failed.stdout + failed.stderr
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("JOBFLOW_BROWSER_COMPANION_INSTALL_FAILED", output)
            self.assertIn("JOBFLOW_STABLE_LAUNCHER_BACKUP_CLEANUP_FAILED", output)
            self.assertNotIn("JOBFLOW_STABLE_LAUNCHER_ROLLBACK_FAILED", output)
            self.assertEqual(json.loads(current_path.read_text(encoding="utf-8")), original_pointer)
            self.assertEqual(installed_launcher.read_bytes(), original_launcher)
            self.assertTrue(any(install_root.glob(".l-*")))

    def test_store_only_mode_selects_explicit_browsers_without_path_lookup(self) -> None:
        installer = (
            PROJECT / "scripts" / "install-jobflow-browser-companion.ps1"
        ).read_text(encoding="utf-8-sig")

        self.assertIn('[ValidateSet("auto", "chrome", "edge")]', installer)
        self.assertNotIn('Get-Command "chrome.exe"', installer)
        self.assertNotIn('Get-Command "msedge.exe"', installer)
        self.assertIn('if ($RequestedBrowser -eq "chrome")', installer)
        self.assertIn('if ($RequestedBrowser -eq "edge")', installer)
        self.assertIn('throw "JOBFLOW_TRUSTED_CHROME_REQUIRED"', installer)
        self.assertIn('throw "JOBFLOW_TRUSTED_EDGE_REQUIRED"', installer)
        self.assertIn("[Environment]::GetFolderPath($Folder)", installer)
        self.assertIn("Microsoft.PowerShell.Security\\Get-AuthenticodeSignature", installer)
        self.assertIn("Microsoft.PowerShell.Management\\Start-Process", installer)

    def test_installed_launchers_and_nested_shells_do_not_use_path_powershell(self) -> None:
        runtime = PROJECT / "scripts" / "windows-runtime"
        for name in (
            "Start JobFlow.cmd", "Check JobFlow.cmd", "Update JobFlow.cmd",
            "Rollback JobFlow.cmd", "Uninstall JobFlow.cmd",
        ):
            with self.subTest(launcher=name):
                text = (runtime / name).read_text(encoding="utf-8")
                self.assertIn(
                    r'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"',
                    text,
                )
                self.assertNotIn("\npowershell.exe ", text.lower())
        for name in (
            "check-installed-jobflow.ps1", "update-installed-jobflow.ps1",
            "uninstall-installed-jobflow.ps1", "manage-authorized-discovery-task.ps1",
        ):
            with self.subTest(script=name):
                text = (runtime / name).read_text(encoding="utf-8-sig")
                self.assertIn("[Environment]::SystemDirectory", text)
                self.assertIn("Microsoft.PowerShell.Security\\Get-AuthenticodeSignature", text)
                self.assertNotIn("& powershell.exe", text)
        rollback, _bootstrap, _contract, _wrapper_contract = self._rollback_contract_sources()
        self.assertIn("[Environment]::SystemDirectory", rollback)
        self.assertIn(
            "[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)",
            rollback,
        )
        self.assertIn('[IO.Path]::Combine($localData, "JobOps")', rollback)
        self.assertIn(
            '[IO.Path]::Combine($PSScriptRoot, "jobflow-bootstrap.ps1")', rollback
        )
        self.assertIn('"-File", $bootstrap, "-Rollback"', rollback)
        self.assertNotIn("& powershell.exe", rollback)
        uninstall = (runtime / "Uninstall JobFlow.cmd").read_text(encoding="utf-8")
        self.assertNotIn("%TEMP%", uninstall)
        self.assertNotIn("copy /y", uninstall.lower())

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
        rollback, bootstrap, rollback_contract, wrapper_contract = self._rollback_contract_sources()
        uninstall = (PROJECT / "scripts" / "windows-runtime" / "uninstall-installed-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"-File", $bootstrap, "-Rollback"', rollback)
        self.assertIn('if ($StartNewRollback.IsPresent)', rollback)
        for forbidden_implementation in (
            "current.json", "previous.json", "Invoke-CandidateRuntimeHealth",
            "rollback-transaction", "Write-Pointer",
        ):
            with self.subTest(forbidden_implementation=forbidden_implementation):
                self.assertNotIn(forbidden_implementation, rollback)
        for owned_by_bootstrap in (
            "function Invoke-RollbackManagement", "function Recover-PendingRollback",
            "Assert-RollbackPointerPairTrusted", "Assert-ActivationTrustEvidenceForPointer",
            "Enter-RollbackDiscoveryLock", "Publish-PointerPair",
        ):
            with self.subTest(owned_by_bootstrap=owned_by_bootstrap):
                self.assertIn(owned_by_bootstrap, bootstrap)
        self.assertNotIn("Disable-AuthorizedDiscoveryTask", bootstrap)
        self.assertIn(
            "def test_success_swaps_only_two_signed_v2_pointers_and_is_redacted",
            rollback_contract,
        )
        self.assertIn(
            "def test_default_delegation_passes_only_rollback", wrapper_contract
        )
        self.assertIn("-RemoveUserData -UserConfirmed", uninstall)
        self.assertIn('if ($RemoveUserData) { $targets += @("Data", "private") }', uninstall)
        self.assertNotIn('"Data", "private"\n)', uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_ROOT_FORBIDDEN", uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_REPARSE_FORBIDDEN", uninstall)
        self.assertIn("JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY", uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_ACCEPTANCE_BYPASS_FORBIDDEN", uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_RUNNING_INSTANCE_ACTIVE", uninstall)
        self.assertIn("JOBFLOW_UNINSTALL_DISCOVERY_RUN_ACTIVE", uninstall)
        self.assertIn("if (-not $skipBrowserIntegrationForAcceptance)", uninstall)
        self.assertIn("function Remove-SafeTarget", uninstall)
        self.assertIn("$cursor = $absolute", uninstall)
        self.assertIn("$cursorItem.Attributes -band [IO.FileAttributes]::ReparsePoint", uninstall)
        self.assertIn("function Enter-JobFlowFileLock", uninstall)
        self.assertIn("function Exit-JobFlowFileLock", uninstall)
        self.assertNotIn(". $lockHelpers", uninstall)
        self.assertIn('"\\\\?\\" + $absolute', uninstall)
        self.assertIn("[IO.Directory]::Delete($extended, $true)", uninstall)
        self.assertIn("[IO.File]::SetAttributes($file, [IO.FileAttributes]::Normal)", uninstall)
        self.assertIn('"Update JobFlow.cmd"', uninstall)
        self.assertIn('manage-authorized-discovery-task.ps1', uninstall)
        self.assertIn('-Action Remove', uninstall)

    def test_data_preserving_uninstall_removes_only_fixed_runtime_targets(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-fixed-install-qa-", dir=system_temp) as raw:
            acceptance_root = Path(raw)
            local_app_data = acceptance_root / "LocalAppData"
            install_root = local_app_data / "JobOps"
            bin_root = install_root / "bin"
            state_root = install_root / "Data" / "state"
            app_root = install_root / "Application" / "versions" / "v-test"
            private_root = install_root / "private"
            bin_root.mkdir(parents=True)
            state_root.mkdir(parents=True)
            app_root.mkdir(parents=True)
            private_root.mkdir(parents=True)
            shutil.copy2(
                PROJECT / "scripts" / "windows-runtime" / "jobflow-runtime-locks.ps1",
                bin_root / "jobflow-runtime-locks.ps1",
            )
            (install_root / "Data" / ".jobflow-data-root").write_text(
                '{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}', encoding="utf-8"
            )
            (state_root / "keep.db").write_bytes(b"preserved-user-state")
            (private_root / "keep.bin").write_bytes(b"preserved-private-state")
            (app_root / "runtime.bin").write_bytes(b"application-runtime")
            (install_root / "current.json").write_text("{}", encoding="utf-8")
            environment = ISOLATED_ENVIRONMENT.copy()
            environment.update({
                "LOCALAPPDATA": str(local_app_data),
                "APPDATA": str(acceptance_root / "Roaming"),
                "TEMP": str(system_temp),
                "TMP": str(system_temp),
                "JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY": "1",
            })
            completed = __import__("subprocess").run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(PROJECT / "scripts" / "windows-runtime" / "uninstall-installed-jobflow.ps1"),
                    "-InstallRoot", str(install_root),
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
            self.assertFalse((install_root / "Application").exists())
            self.assertFalse(bin_root.exists())
            self.assertFalse((install_root / "current.json").exists())
            self.assertEqual((state_root / "keep.db").read_bytes(), b"preserved-user-state")
            self.assertEqual((private_root / "keep.bin").read_bytes(), b"preserved-private-state")
            self.assertIn("local profile, queue, and private data were preserved", completed.stdout)

    def test_uninstaller_rejects_runtime_hardlink_without_touching_outside_file(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-fixed-install-qa-", dir=system_temp) as raw:
            acceptance_root = Path(raw)
            local_app_data = acceptance_root / "LocalAppData"
            install_root = local_app_data / "JobOps"
            application_root = install_root / "Application"
            application_root.mkdir(parents=True)
            outside = acceptance_root / "outside-runtime-sentinel.bin"
            outside.write_bytes(b"outside-must-not-change")
            os.link(outside, application_root / "linked-runtime.bin")
            environment = ISOLATED_ENVIRONMENT.copy()
            environment.update({
                "LOCALAPPDATA": str(local_app_data),
                "APPDATA": str(acceptance_root / "Roaming"),
                "TEMP": str(system_temp),
                "TMP": str(system_temp),
                "JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY": "1",
            })
            completed = __import__("subprocess").run(
                [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(PROJECT / "scripts" / "windows-runtime" / "uninstall-installed-jobflow.ps1"),
                    "-InstallRoot", str(install_root),
                ],
                cwd=PROJECT, env=environment, text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=30, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_UNINSTALL_HARDLINK_FORBIDDEN", completed.stdout + completed.stderr)
            self.assertEqual(outside.read_bytes(), b"outside-must-not-change")
            self.assertTrue((application_root / "linked-runtime.bin").exists())

    def test_uninstaller_rejects_start_menu_ancestor_junction_without_touching_outside_tree(self) -> None:
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="jobflow-fixed-install-qa-", dir=system_temp) as raw:
            acceptance_root = Path(raw)
            local_app_data = acceptance_root / "LocalAppData"
            install_root = local_app_data / "JobOps"
            application_root = install_root / "Application"
            application_root.mkdir(parents=True)
            runtime_sentinel = application_root / "runtime.bin"
            runtime_sentinel.write_bytes(b"installed-runtime-must-remain")
            roaming = acceptance_root / "Roaming"
            programs_link = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            programs_link.parent.mkdir(parents=True)
            outside = acceptance_root / "outside-start-menu"
            (outside / "JobFlow").mkdir(parents=True)
            outside_sentinel = outside / "JobFlow" / "outside.lnk"
            outside_sentinel.write_bytes(b"outside-must-not-change")
            create_directory_reparse(programs_link, outside)
            environment = ISOLATED_ENVIRONMENT.copy()
            environment.update({
                "LOCALAPPDATA": str(local_app_data),
                "APPDATA": str(roaming),
                "TEMP": str(system_temp),
                "TMP": str(system_temp),
                "JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY": "1",
            })
            try:
                completed = __import__("subprocess").run(
                    [
                        str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(PROJECT / "scripts" / "windows-runtime" / "uninstall-installed-jobflow.ps1"),
                        "-InstallRoot", str(install_root),
                    ],
                    cwd=PROJECT, env=environment, text=True, encoding="utf-8", errors="replace",
                    capture_output=True, timeout=30, check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(
                    "JOBFLOW_UNINSTALL_START_MENU_REPARSE_FORBIDDEN",
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(outside_sentinel.read_bytes(), b"outside-must-not-change")
                self.assertEqual(runtime_sentinel.read_bytes(), b"installed-runtime-must-remain")
            finally:
                unlink_directory_reparse(programs_link)

    def test_stable_launcher_atomic_copy_rejects_hardlink_target(self) -> None:
        installer = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        start = installer.index("function Initialize-JobFlowInstallerFileIdentityApi")
        end = installer.index("function Enter-JobFlowFileLock", start)
        production_helpers = installer[start:end]
        with tempfile.TemporaryDirectory(prefix="jobflow-launcher-hardlink-") as raw:
            temporary = Path(raw)
            source = temporary / "source.cmd"
            outside = temporary / "outside-launcher-sentinel.cmd"
            destination = temporary / "Start JobFlow.cmd"
            source.write_bytes(b"new-launcher")
            outside.write_bytes(b"outside-must-not-change")
            os.link(outside, destination)
            harness = temporary / "atomic-launcher-harness.ps1"
            harness.write_text(
                '$ErrorActionPreference = "Stop"\n'
                + production_helpers
                + '\nCopy-InstallerFileAtomic $args[0] $args[1] "JOBFLOW_STABLE_LAUNCHER_TARGET_INVALID"\n',
                encoding="utf-8-sig",
            )
            completed = __import__("subprocess").run(
                [str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), str(source), str(destination)],
                cwd=PROJECT, env=ISOLATED_ENVIRONMENT, text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=30, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("JOBFLOW_STABLE_LAUNCHER_TARGET_INVALID", completed.stdout + completed.stderr)
            self.assertEqual(outside.read_bytes(), b"outside-must-not-change")
            self.assertEqual(destination.read_bytes(), b"outside-must-not-change")

    def test_uninstaller_coordinates_with_the_global_native_host_install_mutex(self) -> None:
        temporary_root = PROJECT / "tests" / ".tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-uninstall-mutex-", dir=temporary_root) as raw:
            temporary = Path(raw)
            local_app_data = temporary / "LocalAppData"
            install_root = local_app_data / "JobOps"
            bin_root = install_root / "bin"
            application_root = install_root / "Application"
            bin_root.mkdir(parents=True)
            application_root.mkdir()
            sentinel = application_root / "preserved.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            shutil.copy2(
                PROJECT / "scripts" / "windows-runtime" / "jobflow-runtime-locks.ps1",
                bin_root / "jobflow-runtime-locks.ps1",
            )
            ready = temporary / "mutex-ready.signal"
            holder_script = (
                "$ErrorActionPreference='Stop'; "
                "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                "$name='Global\\JobFlow.NativeHostInstaller.'+($sid -replace '[^A-Za-z0-9_.-]','_'); "
                "$mutex=New-Object Threading.Mutex($false,$name); "
                "$held=$mutex.WaitOne(0); if(-not $held){throw 'mutex unavailable'}; "
                "[IO.File]::WriteAllText($env:JOBFLOW_QA_MUTEX_READY,'ready'); "
                "try{Start-Sleep -Seconds 30}finally{$mutex.ReleaseMutex();$mutex.Dispose()}"
            )
            environment = ISOLATED_ENVIRONMENT.copy()
            environment.update({
                "LOCALAPPDATA": str(local_app_data),
                "APPDATA": str(temporary / "Roaming"),
                "TEMP": str(temporary),
                "TMP": str(temporary),
                "JOBFLOW_QA_MUTEX_READY": str(ready),
            })
            holder = RealPopen(
                [str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-Command", holder_script],
                cwd=PROJECT, env=environment, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL,
                text=True, encoding="utf-8", errors="replace",
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready.is_file(), "native-host mutex holder did not start")
                completed = __import__("subprocess").run(
                    [
                        str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(PROJECT / "scripts" / "windows-runtime" / "uninstall-installed-jobflow.ps1"),
                        "-InstallRoot", str(install_root),
                    ],
                    cwd=PROJECT, env=environment, text=True, encoding="utf-8", errors="replace",
                    capture_output=True, timeout=30, check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(
                    "JOBFLOW_UNINSTALL_NATIVE_HOST_INSTALL_ACTIVE",
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.wait(timeout=5)

    def test_signed_update_launcher_is_user_initiated_pinned_and_fail_closed(self) -> None:
        script = (PROJECT / "scripts" / "windows-runtime" / "update-installed-jobflow.ps1").read_text(
            encoding="utf-8-sig"
        )
        wrapper = (PROJECT / "scripts" / "windows-runtime" / "Update JobFlow.cmd").read_text(
            encoding="utf-8"
        )
        self.assertIn("https://api.github.com/repos/ValerianXXX/JobFlow/releases/latest", script)
        self.assertIn("sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339", script)
        self.assertIn('$bootstrapPath = Join-Path $localRoot "bin\\jobflow-bootstrap.ps1"', script)
        self.assertIn('Invoke-StableBootstrap "RecoverOnly"', script)
        self.assertIn('Invoke-StableBootstrap "DescribeManifest"', script)
        self.assertIn('Invoke-StableBootstrap "Activate"', script)
        self.assertIn("Read-AndValidateV2CurrentPointer", script)
        self.assertIn("JOBFLOW_UPDATE_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED", script)
        self.assertIn("$archiveIdentityLock", script)
        self.assertIn("[IO.FileShare]::Read", script)
        self.assertNotIn("jobops.update_manifest", script)
        self.assertNotIn(".venv", script)
        self.assertNotIn("python.exe", script)
        self.assertNotIn("install-jobflow.ps1", script)
        self.assertIn("AllowAutoRedirect = $false", script)
        self.assertIn("Assert-AllowedHttpsUri", script)
        self.assertNotIn("Expand-Archive", script)
        self.assertNotIn("Expand-LockedVerifiedArchive", script)
        self.assertNotIn("IO.Compression.ZipArchive", script)
        self.assertNotIn("Register-ScheduledTask", script)
        self.assertNotIn("schtasks", script.casefold())
        self.assertNotIn("Start-BitsTransfer", script)
        self.assertIn("pause", wrapper.casefold())

    def test_installer_binds_trusted_update_payload_and_hash_locked_python_runtime(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("TrustedUpdatePayloadManifest", script)
        self.assertIn("TrustedUpdatePayloadManifestSha256", script)
        self.assertIn("UPDATE_EXTRACTED_PAYLOAD_ATTESTED", script)
        self.assertIn("Get-OpenInstallerFileLinkCount", script)
        self.assertIn("$trustedUpdateSourceLocks.Add($lock)", script)
        self.assertIn("^CPython\\|(3\\.(11|12))\\|64\\|win32$", script)
        self.assertIn("windows-cp3$($python.Minor)-requirements.lock", script)
        self.assertIn('"--require-hashes"', script)
        self.assertIn('"--only-binary", ":all:"', script)
        self.assertIn('"--no-deps"', script)
        self.assertIn("JOBFLOW_RUNTIME_CLOSURE_UNATTESTED", script)
        self.assertTrue((PROJECT / "config" / "windows-cp311-requirements.lock").is_file())
        self.assertTrue((PROJECT / "config" / "windows-cp312-requirements.lock").is_file())

    def test_installer_and_updater_fail_closed_for_pending_rollback_recovery(self) -> None:
        installer = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        updater = (PROJECT / "scripts" / "windows-runtime" / "update-installed-jobflow.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn(".rollback-pointer-transaction.json", installer)
        self.assertIn(".rollback-pointer-transaction.backup.json", installer)
        self.assertIn("JOBFLOW_ROLLBACK_RECOVERY_REQUIRED", installer)
        gate = installer.index('throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"')
        runtime_lock = installer.index('Enter-JobFlowFileLock $runtimeLockPath')
        discovery_lock = installer.index('Enter-JobFlowFileLock $discoveryLockPath')
        pointer_read = installer.index('$existingPointer = Read-InstalledPointer')
        self.assertLess(runtime_lock, gate)
        self.assertLess(discovery_lock, gate)
        self.assertLess(gate, pointer_read)
        self.assertNotIn(".rollback-pointer-transaction.json", updater)
        self.assertIn('Invoke-StableBootstrap "RecoverOnly"', updater)
        self.assertLess(
            updater.index('Invoke-StableBootstrap "RecoverOnly"'),
            updater.index("Receive-AllowedHttpsFile ("),
        )

    def test_installed_rollback_delegates_validated_pointer_swap_to_bootstrap(self) -> None:
        rollback, bootstrap, rollback_contract, wrapper_contract = (
            self._rollback_contract_sources()
        )
        self.assertIn('"-File", $bootstrap, "-Rollback"', rollback)
        for implementation_detail in (
            "current.json", "previous.json", "Publish-PointerPair",
            "Assert-RollbackPointerPairTrusted", "Invoke-CandidateRuntimeHealth",
        ):
            with self.subTest(implementation_detail=implementation_detail):
                self.assertNotIn(implementation_detail, rollback)
        for owned_by_bootstrap in (
            "function Start-RollbackTransaction",
            "function Recover-PendingRollback",
            "Assert-RollbackPointerPairTrusted",
            "Assert-ActivationTrustEvidenceForPointer",
            "Publish-PointerPair",
        ):
            with self.subTest(owned_by_bootstrap=owned_by_bootstrap):
                self.assertIn(owned_by_bootstrap, bootstrap)
        self.assertIn(
            "def test_success_swaps_only_two_signed_v2_pointers_and_is_redacted",
            rollback_contract,
        )
        self.assertIn(
            "def test_default_delegation_passes_only_rollback", wrapper_contract
        )

    def test_rollback_crash_boundaries_are_owned_by_bootstrap_v2_contract(self) -> None:
        rollback, bootstrap, rollback_contract, _wrapper_contract = (
            self._rollback_contract_sources()
        )
        boundaries = (
            "# JOBFLOW_ROLLBACK_PREPARED_BOUNDARY",
            "# JOBFLOW_ROLLBACK_PRE_HEALTH_OK_STATE_BOUNDARY",
            "# JOBFLOW_ROLLBACK_POINTER_SWITCHED_STATE_BOUNDARY",
            "# JOBFLOW_ROLLBACK_POST_HEALTH_OK_STATE_BOUNDARY",
            "# JOBFLOW_ROLLBACK_COMPLETION_RECEIPT_BOUNDARY",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                self.assertEqual(bootstrap.count(boundary), 1)
                self.assertNotIn(boundary, rollback)
        self.assertIn(
            "def test_each_crash_state_forward_completes_once_then_requires_new_intent",
            rollback_contract,
        )

    def test_rollback_journal_publish_is_atomic_before_pointer_switch(self) -> None:
        rollback, bootstrap, rollback_contract, _wrapper_contract = (
            self._rollback_contract_sources()
        )
        self.assertNotIn("Write-AtomicCanonicalActivationStateFile", rollback)
        atomic_writer = bootstrap.split(
            "function Write-AtomicCanonicalActivationStateFile", 1
        )[1].split("function ", 1)[0]
        self.assertIn("[IO.FileOptions]::WriteThrough", atomic_writer)
        self.assertIn("$stream.Flush($true)", atomic_writer)
        self.assertIn("Read-CanonicalActivationStateFile $Temporary $Kind", atomic_writer)
        self.assertIn("[IO.File]::Replace", atomic_writer)

        journal_writer = bootstrap.split(
            "function Write-RollbackJournalPair", 1
        )[1].split("function ", 1)[0]
        self.assertGreaterEqual(
            journal_writer.count("Write-AtomicCanonicalActivationStateFile"), 2
        )
        recovery = bootstrap.split(
            "function Recover-PendingRollback", 1
        )[1].split("function ", 1)[0]
        self.assertLess(
            recovery.index('Set-RollbackJournalState $Layout $journal "PRE_HEALTH_OK"'),
            recovery.index("Publish-PointerPair"),
        )
        self.assertIn(
            "def test_static_contract_is_pathless_v2_only_and_orders_locks_and_states",
            rollback_contract,
        )

    def test_rollback_journal_pair_recovery_is_owned_by_bootstrap_v2(self) -> None:
        rollback, bootstrap, rollback_contract, _wrapper_contract = (
            self._rollback_contract_sources()
        )
        self.assertNotIn("rollback-transaction", rollback)
        for function_name in (
            "function Read-RollbackJournalPair",
            "function Write-RollbackJournalPair",
            "function Assert-RollbackJournalShape",
            "function Get-RollbackJournalImmutableSha256",
            "function Get-RollbackLivePointerState",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(function_name, bootstrap)
        journal_reader = bootstrap.split(
            "function Read-RollbackJournalPair", 1
        )[1].split("function ", 1)[0]
        self.assertIn("main", journal_reader)
        self.assertIn("backup", journal_reader)
        self.assertIn("semantic_sha256", bootstrap)
        activation_journal_contract = (
            PROJECT / "tests" / "test_jobflow_bootstrap_activation_journal.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "def test_backup_anchor_partial_and_malformed_copy_truth_table",
            activation_journal_contract,
        )
        self.assertIn(
            "def test_each_crash_state_forward_completes_once_then_requires_new_intent",
            rollback_contract,
        )

    def test_rollback_completion_receipt_and_new_intent_are_bootstrap_owned(self) -> None:
        rollback, bootstrap, rollback_contract, wrapper_contract = (
            self._rollback_contract_sources()
        )
        self.assertNotIn("rollback-completion", rollback)
        for function_name in (
            "function New-RollbackCompletionReceipt",
            "function Assert-RollbackCompletionReceiptShape",
            "function Read-RollbackCompletionReceipt",
            "function Write-RollbackCompletionReceipt",
            "function Assert-RollbackCompletionMatchesPointers",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(function_name, bootstrap)
        self.assertIn(
            'if ($StartNewRollback.IsPresent) { $arguments += "-StartNewRollback" }',
            rollback,
        )
        self.assertIn(
            "def test_each_crash_state_forward_completes_once_then_requires_new_intent",
            rollback_contract,
        )
        self.assertIn(
            "def test_explicit_new_transaction_switch_is_forwarded_after_rollback",
            wrapper_contract,
        )

    def test_rollback_invalid_state_is_fail_closed_by_bootstrap_v2(self) -> None:
        rollback, bootstrap, rollback_contract, wrapper_contract = (
            self._rollback_contract_sources()
        )
        self.assertNotIn("JOBFLOW_ROLLBACK_RECOVERY_REQUIRED", rollback)
        self.assertIn(
            '[Console]::Error.WriteLine("JOBFLOW_ROLLBACK_WRAPPER_FAILED")', rollback
        )
        for validator in (
            "function Assert-RollbackJournalShape",
            "function Assert-RollbackCompletionReceiptShape",
            "function Assert-RollbackPointerPairTrusted",
            "function Get-RollbackLivePointerState",
        ):
            with self.subTest(validator=validator):
                self.assertIn(validator, bootstrap)
        self.assertIn("JOBFLOW_ROLLBACK_RECOVERY_REQUIRED", bootstrap)
        self.assertIn(
            "def test_paths_missing_previous_v1_and_tampered_evidence_fail_before_health",
            rollback_contract,
        )
        self.assertIn(
            "def test_wrong_installed_root_fails_redacted_before_bootstrap_invocation",
            wrapper_contract,
        )

    def test_regular_runtime_consumers_fail_closed_while_rollback_recovery_is_required(self) -> None:
        temporary_root = PROJECT / "tests" / ".tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rollback-consumer-gate-", dir=temporary_root) as raw:
            fixture = build_installed_rollback_fixture(raw)
            install_root = fixture["install_root"]
            bin_root = fixture["bin_root"]
            (install_root / ".rollback-pointer-transaction.json").write_text(
                "recovery-only", encoding="utf-8"
            )
            commands = {
                "start": [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy",
                    "Bypass", "-File", str(bin_root / "start-installed-jobflow.ps1"), "-NoBrowser",
                ],
                "check": [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy",
                    "Bypass", "-File", str(bin_root / "check-installed-jobflow.ps1"), "-Json",
                ],
                "discovery": [
                    str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy",
                    "Bypass", "-File", str(bin_root / "run-authorized-discovery-task.ps1"),
                ],
            }
            for name, command in commands.items():
                with self.subTest(consumer=name):
                    failed = __import__("subprocess").run(
                        command, cwd=PROJECT, env=fixture["environment"], text=True,
                        encoding="utf-8", errors="replace", capture_output=True,
                        timeout=30, check=False,
                    )
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn(
                        "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED",
                        failed.stdout + failed.stderr,
                    )
            (install_root / ".rollback-pointer-transaction.json").replace(
                install_root / ".rollback-pointer-transaction.backup.json"
            )
            for name, command in commands.items():
                with self.subTest(consumer=name, journal="backup-only"):
                    failed = __import__("subprocess").run(
                        command, cwd=PROJECT, env=fixture["environment"], text=True,
                        encoding="utf-8", errors="replace", capture_output=True,
                        timeout=30, check=False,
                    )
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn(
                        "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED",
                        failed.stdout + failed.stderr,
                    )

    def test_installed_runtime_serializes_pointer_resolution_and_maintenance(self) -> None:
        helper = (PROJECT / "scripts" / "windows-runtime" / "jobflow-runtime-locks.ps1").read_text(
            encoding="utf-8-sig"
        )
        start = (PROJECT / "scripts" / "windows-runtime" / "start-installed-jobflow.ps1").read_text(
            encoding="utf-8-sig"
        )
        runner = (PROJECT / "scripts" / "windows-runtime" / "run-authorized-discovery-task.ps1").read_text(
            encoding="utf-8-sig"
        )
        health = (PROJECT / "scripts" / "windows-runtime" / "check-installed-jobflow.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("[IO.File]::Open", helper)
        self.assertIn("$stream.Lock(0, 1)", helper)
        self.assertIn("JOBFLOW_ALREADY_RUNNING_OR_MAINTENANCE_ACTIVE", start)
        self.assertLess(start.index("Enter-JobFlowFileLock"), start.index("Read-JobFlowPointer $pointerPath"))
        self.assertIn("JOBFLOW_DISCOVERY_TASK_LOCK_TIMEOUT", runner)
        self.assertLess(runner.index("Enter-JobFlowFileLock"), runner.index("Read-DiscoveryPointer $pointerPath"))
        self.assertIn('$env:JOBFLOW_DISCOVERY_TASK_LOCK_HELD = "1"', runner)
        self.assertIn("JOBFLOW_ALREADY_RUNNING_OR_MAINTENANCE_ACTIVE", health)
        self.assertIn("JOBFLOW_DISCOVERY_TASK_LOCK_TIMEOUT", health)
        self.assertIn("JOBFLOW_ROLLBACK_RECOVERY_REQUIRED", start)
        self.assertIn("JOBFLOW_ROLLBACK_RECOVERY_REQUIRED", runner)
        self.assertIn("JOBFLOW_ROLLBACK_RECOVERY_REQUIRED", health)
        self.assertIn("$lockCursorItem.Attributes -band [IO.FileAttributes]::ReparsePoint", health)
        self.assertLess(
            health.index("$lockCursorItem.Attributes -band [IO.FileAttributes]::ReparsePoint"),
            health.index(". $lockHelpers"),
        )
        self.assertLess(health.index("Enter-JobFlowFileLock"), health.index("Read-InstalledPointer $pointerPath"))
        self.assertLess(
            health.index(".jobflow-runtime-maintenance.lock"),
            health.index(".authorized-discovery-task.lock"),
        )
        self.assertLess(
            health.index("Exit-JobFlowFileLock $discoveryLock"),
            health.index("Exit-JobFlowFileLock $runtimeLock"),
        )

    def test_runtime_file_lock_blocks_a_competing_process_and_then_releases(self) -> None:
        temporary_root = PROJECT / "tests" / ".tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="runtime-lock-", dir=temporary_root) as raw:
            lock_path = Path(raw) / "maintenance.lock"
            helper_path = PROJECT / "scripts" / "windows-runtime" / "jobflow-runtime-locks.ps1"
            quoted_helper = str(helper_path).replace("'", "''")
            quoted_lock = str(lock_path).replace("'", "''")
            holder_command = (
                f". '{quoted_helper}'; "
                f"$stream=Enter-JobFlowFileLock '{quoted_lock}' 'HOLDER_TIMEOUT' 2; "
                "[Console]::Out.WriteLine('LOCKED'); Start-Sleep -Seconds 3; "
                "Exit-JobFlowFileLock $stream"
            )
            contender_command = (
                f". '{quoted_helper}'; "
                f"$stream=Enter-JobFlowFileLock '{quoted_lock}' 'EXPECTED_LOCK_TIMEOUT' 1; "
                "Exit-JobFlowFileLock $stream"
            )
            holder = RealPopen(
                [str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", holder_command],
                cwd=PROJECT,
                env=ISOLATED_ENVIRONMENT,
                stdin=DEVNULL,
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                self.assertIsNotNone(holder.stdout)
                self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
                blocked = __import__("subprocess").run(
                    [str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", contender_command],
                    cwd=PROJECT,
                    env=ISOLATED_ENVIRONMENT,
                    stdin=DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    check=False,
                )
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("EXPECTED_LOCK_TIMEOUT", blocked.stderr)
                holder_code = holder.wait(timeout=5)
                holder_error = "" if holder.stderr is None else holder.stderr.read()
                self.assertEqual(holder_code, 0, holder_error)
                released = __import__("subprocess").run(
                    [str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", contender_command],
                    cwd=PROJECT,
                    env=ISOLATED_ENVIRONMENT,
                    stdin=DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    check=False,
                )
                self.assertEqual(released.returncode, 0, released.stderr)
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.wait(timeout=5)
                if holder.stdout is not None:
                    holder.stdout.close()
                if holder.stderr is not None:
                    holder.stderr.close()

    def test_launcher_messages_are_bilingual_and_external_actions_are_absent(self) -> None:
        install = (PROJECT / "scripts" / "install-jobflow.ps1").read_text(encoding="utf-8-sig")
        start = (PROJECT / "scripts" / "start-jobflow.ps1").read_text(encoding="utf-8-sig")
        check = (PROJECT / "scripts" / "check-jobflow.ps1").read_text(encoding="utf-8-sig")
        release = (PROJECT / "scripts" / "check-release-readiness.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(" / ", install)
        self.assertIn(" / ", start)
        self.assertIn(" / ", check)
        self.assertIn(" / ", release)
        for blocker in (
            "RELEASE_ATTESTATION_MISSING",
            "RELEASE_ATTESTATION_INVALID",
            "RELEASE_RUNTIME_CLOSURE_UNATTESTED",
            "CLEAN_WINDOWS_EVIDENCE_MISSING",
            "CLEAN_WINDOWS_EVIDENCE_INVALID",
            "BROWSER_COMPANION_STORES_PENDING",
            "BROWSER_COMPANION_STORES_OUTDATED",
            "BROWSER_COMPANION_STORES_INVALID",
        ):
            self.assertIn(blocker, release)
        self.assertIn('"PUBLIC_RELEASE_READY"', release)
        self.assertIn("exit 0", release)
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
        self.assertFalse(result["public_release_ready"])
        self.assertEqual(result["runtime_closure_status"], "UNATTESTED")
        self.assertIn(result["release_attestation_status"], {"MISSING", "INVALID"})
        self.assertIn(
            result["clean_windows_evidence_status"],
            {"NOT_CHECKED", "MISSING", "INVALID"},
        )
        self.assertIsInstance(result["release_attestation_failure_code"], str)
        self.assertIn("RELEASE_RUNTIME_CLOSURE_UNATTESTED", result["blockers"])
        self.assertFalse(result["upload_performed"])
        self.assertEqual(result["network_actions"], 0)
        self.assertEqual(result["real_external_actions"], 0)
        self.assertTrue(result["blockers"])
        self.assertNotIn("PYTHON_RUNTIME_MISSING", result["blockers"])
        serialized = json.dumps(result)
        self.assertNotIn(str(PROJECT), serialized)
        self.assertNotIn("secure-ref:", serialized)
        validate_named("release-readiness", result, PROJECT / "schemas")


if __name__ == "__main__":
    unittest.main()
