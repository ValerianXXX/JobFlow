from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from _support import PROJECT


WINDOWS_POWERSHELL = shutil.which("powershell.exe")
IS_WINDOWS = os.name == "nt" and WINDOWS_POWERSHELL is not None


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@unittest.skipUnless(IS_WINDOWS, "Native Host installation hardening is Windows-only.")
class NativeHostInstallerHardeningTests(unittest.TestCase):
    def _fixture(self, raw: str) -> tuple[Path, Path, dict[str, str], list[str]]:
        temporary = Path(raw)
        source = temporary / "source"
        scripts = source / "scripts"
        native_source = scripts / "native-messaging"
        config = source / "config"
        local_app_data = temporary / "LocalAppData"
        for path in (native_source, config, local_app_data):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            PROJECT / "scripts" / "install-jobflow-native-host.ps1",
            scripts / "install-jobflow-native-host.ps1",
        )
        shutil.copy2(PROJECT / ".jobops-root", source / ".jobops-root")
        (native_source / "JobFlowBrowserCompanionHost.cs").write_text(
            "// synthetic source; transaction test replaces compilation\n",
            encoding="ascii",
        )
        (config / "browser-companion-stores.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "native_host_name": "com.jobflow.browser_companion",
                    "extension_ids": ["a" * 32],
                }
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            {
                "LOCALAPPDATA": str(local_app_data),
                "TEMP": str(temporary / "Temp"),
                "TMP": str(temporary / "Temp"),
            }
        )
        (temporary / "Temp").mkdir()
        command = [
            str(WINDOWS_POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "install-jobflow-native-host.ps1"),
        ]
        return source, local_app_data, environment, command

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )

    def _stub_compilation(self, script: str, body: str | None = None) -> str:
        compile_line = (
            '    Add-Type -TypeDefinition $sourceText -ReferencedAssemblies '
            '@("System.Web.Extensions") -OutputAssembly $stagedExecutable '
            '-OutputType ConsoleApplication'
        )
        self.assertEqual(script.count(compile_line), 1)
        replacement = body or "    [IO.File]::WriteAllText($stagedExecutable, 'synthetic-new-host')"
        return script.replace(compile_line, replacement)

    def _replace_registry_functions(self, script: str, fake_functions: str) -> str:
        start = script.index("function Open-RegistryKey")
        end = script.index("foreach ($path in @($hostRoot", start)
        return script[:start] + fake_functions + "\n" + script[end:]

    def _create_directory_reparse(self, link: Path, target: Path) -> None:
        try:
            os.symlink(target, link, target_is_directory=True)
            return
        except OSError:
            pass
        completed = subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "& { param([string]$Link, [string]$Target) "
                "$ErrorActionPreference='Stop'; "
                "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null }",
                str(link),
                str(target),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def _unlink_directory_reparse(self, link: Path) -> None:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            os.rmdir(link)

    def test_script_has_source_boundary_and_explicit_rollback_failure_contract(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow-native-host.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("function Assert-JobFlowSourcePath", script)
        self.assertIn("JOBFLOW_NATIVE_HOST_SOURCE_PATH_FORBIDDEN", script)
        self.assertIn("JOBFLOW_NATIVE_HOST_SOURCE_REPARSE_FORBIDDEN", script)
        self.assertIn("JOBFLOW_NATIVE_HOST_ROLLBACK_FAILED", script)
        self.assertIn("JOBFLOW_NATIVE_HOST_BACKUP_PRESERVED", script)
        self.assertIn("JOBFLOW_NATIVE_HOST_SOURCE_CHANGED", script)
        self.assertIn("function Read-SourceFileCapture", script)
        self.assertIn("function Assert-InstalledHostSnapshot", script)
        self.assertIn("function Enter-NativeHostInstallMutex", script)
        self.assertIn('"Global\\JobFlow.NativeHostInstaller."', script)
        self.assertIn("JOBFLOW_NATIVE_HOST_INSTALL_BUSY", script)
        self.assertIn("$registryBackupCaptured", script)
        self.assertIn("$registryMutationStarted", script)
        self.assertLess(
            script.index("Assert-JobFlowSourcePath $path"),
            script.index("Read-SourceFileCapture $storeIdentityPath"),
        )
        self.assertIn("$storeIdentityText | ConvertFrom-Json", script)
        self.assertIn("Add-Type -TypeDefinition $sourceText", script)
        self.assertNotIn("Add-Type -Path $stagedSourcePath", script)
        self.assertNotIn("Get-Content -LiteralPath $stagedStoreIdentityPath", script)
        rollback = script[script.index("catch {", script.index("$hostInstalled = $false")) :]
        self.assertNotIn("catch { }", rollback)
        self.assertIn("foreach ($subkey in $registrySubkeys)", rollback)

    def test_incomplete_rollback_restores_host_copy_and_preserves_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-rollback-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            installer = source / "scripts" / "install-jobflow-native-host.ps1"
            script = installer.read_text(encoding="utf-8-sig")
            fake_functions = r'''$script:registryWriteCount = 0
function Read-RegistryDefault([string]$Subkey) {
    return @{
        KeyExists = $true
        DefaultExists = $true
        Value = "synthetic-previous"
        Kind = [Microsoft.Win32.RegistryValueKind]::String
    }
}
function Write-RegistryDefault([string]$Subkey, [string]$Value) {
    $script:registryWriteCount += 1
    if ($script:registryWriteCount -eq 2) { throw "SYNTHETIC_REGISTRY_WRITE_FAILED" }
}
function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) {
    $log = Join-Path $env:LOCALAPPDATA "native-restore-attempts.log"
    [IO.File]::AppendAllText($log, ($Subkey + [Environment]::NewLine))
    if ($Subkey -like "*Google*") { throw "SYNTHETIC_REGISTRY_RESTORE_FAILED" }
}

'''
            script = self._replace_registry_functions(script, fake_functions)
            script = self._stub_compilation(script)
            installer.write_text(script, encoding="utf-8")

            host_root = local_app_data / "JobOps" / "BrowserCompanionHost"
            host_root.mkdir(parents=True)
            (host_root / "JobFlowBrowserCompanionHost.exe").write_text("old-host", encoding="ascii")
            (host_root / "com.jobflow.browser_companion.json").write_text("old-manifest", encoding="ascii")
            (host_root / "old-marker.txt").write_text("preserve-me", encoding="ascii")
            original = _tree_snapshot(host_root)

            result = self._run(command, cwd=source, environment=environment)
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JOBFLOW_NATIVE_HOST_ROLLBACK_FAILED", combined)
            self.assertEqual(_tree_snapshot(host_root), original)
            backups = list((local_app_data / "JobOps").glob(".BrowserCompanionHost.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(_tree_snapshot(backups[0]), original)
            restore_log = (local_app_data / "native-restore-attempts.log").read_text(encoding="utf-8")
            self.assertIn("Google", restore_log)
            self.assertIn("Microsoft\\Edge", restore_log)

    def test_source_and_store_identity_reparse_are_rejected_before_registry_access(self) -> None:
        for linked_relative, outside_relative, expected in (
            (
                Path("scripts/native-messaging"),
                Path("outside-native-source"),
                "JOBFLOW_NATIVE_HOST_SOURCE_REPARSE_FORBIDDEN",
            ),
            (
                Path("config"),
                Path("outside-store-config"),
                "JOBFLOW_NATIVE_HOST_SOURCE_REPARSE_FORBIDDEN",
            ),
        ):
            with self.subTest(link=linked_relative), tempfile.TemporaryDirectory(
                prefix="jobflow-native-source-boundary-"
            ) as raw:
                source, local_app_data, environment, command = self._fixture(raw)
                link = source / linked_relative
                outside = Path(raw) / outside_relative
                outside.mkdir()
                if linked_relative.name == "native-messaging":
                    (outside / "JobFlowBrowserCompanionHost.cs").write_text(
                        "// outside source must not be compiled\n", encoding="ascii"
                    )
                else:
                    shutil.copy2(
                        source / "config" / "browser-companion-stores.json",
                        outside / "browser-companion-stores.json",
                    )
                shutil.rmtree(link)
                self._create_directory_reparse(link, outside)
                try:
                    result = self._run(command, cwd=source, environment=environment)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stdout + result.stderr)
                    self.assertFalse((local_app_data / "JobOps" / "BrowserCompanionHost").exists())
                    self.assertFalse((local_app_data / "native-restore-attempts.log").exists())
                finally:
                    self._unlink_directory_reparse(link)

    def test_local_app_data_ancestor_reparse_is_rejected_before_host_or_registry_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-localappdata-link-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            outside = Path(raw) / "outside-local-app-data"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            local_app_data.rmdir()
            self._create_directory_reparse(local_app_data, outside)
            try:
                completed = self._run(command, cwd=source, environment=environment)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(
                    "JOBFLOW_NATIVE_HOST_LOCAL_APP_DATA_REPARSE_FORBIDDEN",
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
                self.assertFalse((outside / "JobOps").exists())
            finally:
                self._unlink_directory_reparse(local_app_data)

    def test_captured_source_and_identity_are_used_after_original_paths_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-source-capture-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            installer = source / "scripts" / "install-jobflow-native-host.ps1"
            script = installer.read_text(encoding="utf-8-sig")
            needle = (
                "    $storeIdentityCapture = Read-SourceFileCapture $storeIdentityPath\n"
                "    $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)"
            )
            self.assertEqual(script.count(needle), 1)
            script = script.replace(
                needle,
                "    $storeIdentityCapture = Read-SourceFileCapture $storeIdentityPath\n"
                "    [IO.File]::WriteAllText($sourcePath, '// replaced after capture')\n"
                "    [IO.File]::WriteAllText($storeIdentityPath, '{not-json')\n"
                "    $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)",
            )
            script = self._stub_compilation(
                script,
                "    if ($sourceText -notlike '*synthetic source*') { throw 'CAPTURE_NOT_REUSED' }\n"
                "    [IO.File]::WriteAllText($stagedExecutable, 'synthetic-new-host')",
            )
            fake_registry = r'''function Read-RegistryDefault([string]$Subkey) {
    return @{ KeyExists = $false; DefaultExists = $false; Value = $null; Kind = $null }
}
function Write-RegistryDefault([string]$Subkey, [string]$Value) { }
function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) { }
'''
            script = self._replace_registry_functions(script, fake_registry)
            installer.write_text(script, encoding="utf-8")

            result = self._run(command, cwd=source, environment=environment)
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, combined)
            manifest = json.loads(
                (local_app_data / "JobOps" / "BrowserCompanionHost" / "com.jobflow.browser_companion.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["allowed_origins"], ["chrome-extension://" + "a" * 32 + "/"])

    def test_production_install_excludes_unpackaged_development_origin(self) -> None:
        development_id = "hhlliaaafegldkmcgmaoaelabipcaooj"
        store_ids = ["b" * 32, "c" * 32]
        for development in (False, True):
            with self.subTest(development=development), tempfile.TemporaryDirectory(
                prefix="jobflow-native-origin-mode-"
            ) as raw:
                source, local_app_data, environment, command = self._fixture(raw)
                (source / "config" / "browser-companion-stores.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "native_host_name": "com.jobflow.browser_companion",
                            "extension_ids": [development_id, *store_ids],
                        }
                    ),
                    encoding="utf-8",
                )
                installer = source / "scripts" / "install-jobflow-native-host.ps1"
                script = self._stub_compilation(installer.read_text(encoding="utf-8-sig"))
                fake_registry = r'''function Read-RegistryDefault([string]$Subkey) {
    return @{ KeyExists = $false; DefaultExists = $false; Value = $null; Kind = $null }
}
function Write-RegistryDefault([string]$Subkey, [string]$Value) { }
function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) { }
'''
                installer.write_text(
                    self._replace_registry_functions(script, fake_registry),
                    encoding="utf-8",
                )
                if development:
                    command.append("-Development")
                result = self._run(command, cwd=source, environment=environment)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                manifest = json.loads(
                    (
                        local_app_data
                        / "JobOps"
                        / "BrowserCompanionHost"
                        / "com.jobflow.browser_companion.json"
                    ).read_text(encoding="utf-8")
                )
                expected = [development_id, *store_ids] if development else store_ids
                self.assertEqual(
                    manifest["allowed_origins"],
                    [f"chrome-extension://{item}/" for item in expected],
                )

    def test_store_identity_rejects_coercive_json_types_and_duplicate_ids(self) -> None:
        invalid_values = (
            {
                "schema_version": "1",
                "native_host_name": "com.jobflow.browser_companion",
                "extension_ids": ["a" * 32],
            },
            {
                "schema_version": True,
                "native_host_name": "com.jobflow.browser_companion",
                "extension_ids": ["a" * 32],
            },
            {
                "schema_version": 1.0,
                "native_host_name": "com.jobflow.browser_companion",
                "extension_ids": ["a" * 32],
            },
            {
                "schema_version": 1,
                "native_host_name": 7,
                "extension_ids": ["a" * 32],
            },
            {
                "schema_version": 1,
                "native_host_name": "com.jobflow.browser_companion",
                "extension_ids": "a" * 32,
            },
            {
                "schema_version": 1,
                "native_host_name": "com.jobflow.browser_companion",
                "extension_ids": ["a" * 32, "a" * 32],
            },
        )
        for index, value in enumerate(invalid_values):
            with self.subTest(case=index), tempfile.TemporaryDirectory(
                prefix="jobflow-native-identity-types-"
            ) as raw:
                source, local_app_data, environment, command = self._fixture(raw)
                (source / "config" / "browser-companion-stores.json").write_text(
                    json.dumps(value), encoding="utf-8"
                )
                result = self._run(command, cwd=source, environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "JOBFLOW_BROWSER_STORE_IDENTITY_INVALID",
                    result.stdout + result.stderr,
                )
                self.assertFalse((local_app_data / "JobOps" / "BrowserCompanionHost").exists())

    def test_direct_installer_mutex_rejects_a_concurrent_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-mutex-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            ready = Path(raw) / "mutex-ready.txt"
            holder = Path(raw) / "hold-mutex.ps1"
            holder.write_text(
                r'''param([string]$Ready)
$ErrorActionPreference = "Stop"
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$name = "Global\JobFlow.NativeHostInstaller." + ($sid -replace '[^A-Za-z0-9_.-]', '_')
$mutex = New-Object Threading.Mutex($false, $name)
try {
    if (-not $mutex.WaitOne(0)) { throw "HOLDER_BUSY" }
    [IO.File]::WriteAllText($Ready, "ready")
    Start-Sleep -Seconds 15
    $mutex.ReleaseMutex()
}
finally { $mutex.Dispose() }
''',
                encoding="utf-8-sig",
            )
            process = subprocess.Popen(
                [
                    str(WINDOWS_POWERSHELL),
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(holder),
                    str(ready),
                ],
                cwd=source,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not ready.exists() and process.poll() is None:
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), "mutex holder did not become ready")
                result = self._run(command, cwd=source, environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("JOBFLOW_NATIVE_HOST_INSTALL_BUSY", result.stdout + result.stderr)
                self.assertFalse(
                    (local_app_data / "JobOps").exists(),
                    "a losing concurrent installer must not recreate the JobOps root",
                )
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)

    def test_existing_host_hardlink_is_rejected_without_touching_outside_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-host-hardlink-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            installer = source / "scripts" / "install-jobflow-native-host.ps1"
            script = self._stub_compilation(installer.read_text(encoding="utf-8-sig"))
            fake_registry = r'''function Read-RegistryDefault([string]$Subkey) {
    return @{ KeyExists = $false; DefaultExists = $false; Value = $null; Kind = $null }
}
function Write-RegistryDefault([string]$Subkey, [string]$Value) { }
function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) { }
'''
            installer.write_text(self._replace_registry_functions(script, fake_registry), encoding="utf-8")
            host_root = local_app_data / "JobOps" / "BrowserCompanionHost"
            host_root.mkdir(parents=True)
            outside = Path(raw) / "outside-host-sentinel.bin"
            outside.write_bytes(b"outside-must-not-change")
            os.link(outside, host_root / "linked-host.exe")

            result = self._run(command, cwd=source, environment=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JOBFLOW_NATIVE_HOST_HARDLINK_FORBIDDEN", result.stdout + result.stderr)
            self.assertEqual(outside.read_bytes(), b"outside-must-not-change")
            self.assertTrue((host_root / "linked-host.exe").exists())

    def test_staging_root_junction_is_rejected_before_build(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-stage-link-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            installer = source / "scripts" / "install-jobflow-native-host.ps1"
            script = installer.read_text(encoding="utf-8-sig")
            needle = "    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null"
            self.assertEqual(script.count(needle), 1)
            script = script.replace(
                needle,
                "    $outsideStage = Join-Path $localRoot 'outside-stage'\n"
                "    New-Item -ItemType Directory -Path $outsideStage -Force | Out-Null\n"
                "    [IO.File]::WriteAllText((Join-Path $outsideStage 'sentinel.txt'), 'safe')\n"
                "    New-Item -ItemType Junction -Path $stagingRoot -Target $outsideStage | Out-Null",
            )
            installer.write_text(script, encoding="utf-8")
            result = self._run(command, cwd=source, environment=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JOBFLOW_NATIVE_HOST_REPARSE_FORBIDDEN", result.stdout + result.stderr)
            self.assertEqual(
                (local_app_data / "JobOps" / "outside-stage" / "sentinel.txt").read_text(
                    encoding="utf-8"
                ),
                "safe",
            )
            self.assertFalse((local_app_data / "JobOps" / "BrowserCompanionHost").exists())

    def test_late_staging_mutation_is_rejected_by_precommit_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-late-stage-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            installer = source / "scripts" / "install-jobflow-native-host.ps1"
            script = self._stub_compilation(installer.read_text(encoding="utf-8-sig"))
            fake_registry = r'''function Read-RegistryDefault([string]$Subkey) {
    return @{ KeyExists = $false; DefaultExists = $false; Value = $null; Kind = $null }
}
function Write-RegistryDefault([string]$Subkey, [string]$Value) { }
function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) { }
'''
            script = self._replace_registry_functions(script, fake_registry)
            needle = "    $registryBackupCaptured = $true\n\n    if (Test-Path -LiteralPath $hostRoot)"
            self.assertEqual(script.count(needle), 1)
            script = script.replace(
                needle,
                "    $registryBackupCaptured = $true\n"
                "    New-Item -ItemType Directory -Path (Join-Path $stagingRoot 'unexpected') | Out-Null\n\n"
                "    if (Test-Path -LiteralPath $hostRoot)",
            )
            installer.write_text(script, encoding="utf-8")
            result = self._run(command, cwd=source, environment=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JOBFLOW_NATIVE_HOST_STAGED_SNAPSHOT_INVALID", result.stdout + result.stderr)
            self.assertFalse((local_app_data / "JobOps" / "BrowserCompanionHost").exists())

    def test_backup_cleanup_failure_after_successful_rollback_keeps_original_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-cleanup-warning-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            installer = source / "scripts" / "install-jobflow-native-host.ps1"
            script = self._stub_compilation(installer.read_text(encoding="utf-8-sig"))
            fake_registry = r'''function Read-RegistryDefault([string]$Subkey) {
    return @{ KeyExists = $false; DefaultExists = $false; Value = $null; Kind = $null }
}
function Write-RegistryDefault([string]$Subkey, [string]$Value) {
    throw "SYNTHETIC_REGISTRY_COMMIT_FAILED"
}
function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) { }
'''
            script = self._replace_registry_functions(script, fake_registry)
            cleanup = "                Remove-Item -LiteralPath $backupRoot -Recurse -Force"
            self.assertEqual(script.count(cleanup), 1)
            script = script.replace(cleanup, '                throw "SYNTHETIC_BACKUP_DELETE_FAILED"')
            installer.write_text(script, encoding="utf-8")

            host_root = local_app_data / "JobOps" / "BrowserCompanionHost"
            host_root.mkdir(parents=True)
            (host_root / "old-marker.txt").write_text("old-host", encoding="ascii")
            original = _tree_snapshot(host_root)
            result = self._run(command, cwd=source, environment=environment)
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SYNTHETIC_REGISTRY_COMMIT_FAILED", combined)
            self.assertIn("JOBFLOW_NATIVE_HOST_BACKUP_CLEANUP_FAILED", combined)
            self.assertNotIn("JOBFLOW_NATIVE_HOST_ROLLBACK_FAILED", combined)
            self.assertEqual(_tree_snapshot(host_root), original)
            self.assertEqual(
                len(list((local_app_data / "JobOps").glob(".BrowserCompanionHost.backup-*"))),
                1,
            )

    def test_real_captured_csharp_compiles_and_commits_only_expected_host_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-native-real-compile-") as raw:
            source, local_app_data, environment, command = self._fixture(raw)
            shutil.copy2(
                PROJECT / "scripts" / "native-messaging" / "JobFlowBrowserCompanionHost.cs",
                source / "scripts" / "native-messaging" / "JobFlowBrowserCompanionHost.cs",
            )
            installer = source / "scripts" / "install-jobflow-native-host.ps1"
            script = installer.read_text(encoding="utf-8-sig")
            fake_registry = r'''function Read-RegistryDefault([string]$Subkey) {
    return @{ KeyExists = $false; DefaultExists = $false; Value = $null; Kind = $null }
}
function Write-RegistryDefault([string]$Subkey, [string]$Value) { }
function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) { }
'''
            installer.write_text(
                self._replace_registry_functions(script, fake_registry), encoding="utf-8"
            )
            result = self._run(command, cwd=source, environment=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            host_root = local_app_data / "JobOps" / "BrowserCompanionHost"
            self.assertEqual(
                sorted(path.name for path in host_root.iterdir()),
                ["JobFlowBrowserCompanionHost.exe", "com.jobflow.browser_companion.json"],
            )
            self.assertGreater((host_root / "JobFlowBrowserCompanionHost.exe").stat().st_size, 0)

    def test_registry_snapshot_restores_missing_default_and_value_kind_exactly(self) -> None:
        script = (PROJECT / "scripts" / "install-jobflow-native-host.ps1").read_text(
            encoding="utf-8-sig"
        )
        read_start = script.index("function Read-RegistryDefault")
        transaction_start = script.index("foreach ($path in @($hostRoot", read_start)
        production_functions = script[read_start:transaction_start]
        harness = r'''
$ErrorActionPreference = "Stop"
$script:registry = @{}

function New-FakeKey([string]$Subkey) {
    $key = [pscustomobject]@{ Subkey = $Subkey }
    $key | Add-Member ScriptMethod GetValueNames {
        $entry = $script:registry[$this.Subkey]
        $names = @("named-value")
        if ($entry.DefaultExists) { $names += "" }
        return $names
    }
    $key | Add-Member ScriptMethod GetValue {
        param($Name, $Fallback, $Options)
        $entry = $script:registry[$this.Subkey]
        if ($Name -eq "" -and $entry.DefaultExists) { return $entry.Value }
        return $Fallback
    }
    $key | Add-Member ScriptMethod GetValueKind {
        param($Name)
        return [Microsoft.Win32.RegistryValueKind]$script:registry[$this.Subkey].Kind
    }
    $key | Add-Member ScriptMethod SetValue {
        param($Name, $Value, $Kind)
        $entry = $script:registry[$this.Subkey]
        $entry.DefaultExists = $true
        $entry.Value = $Value
        $entry.Kind = [int]$Kind
    }
    $key | Add-Member ScriptMethod DeleteValue {
        param($Name, $ThrowOnMissing)
        $entry = $script:registry[$this.Subkey]
        $entry.DefaultExists = $false
        $entry.Value = $null
        $entry.Kind = $null
    }
    $key | Add-Member ScriptMethod Dispose { }
    return $key
}

function Open-RegistryKey([string]$Subkey, [bool]$Writable) {
    if (-not $script:registry.ContainsKey($Subkey)) { return $null }
    return New-FakeKey $Subkey
}
function New-RegistryKey([string]$Subkey) {
    if (-not $script:registry.ContainsKey($Subkey)) {
        $script:registry[$Subkey] = @{ DefaultExists = $false; Value = $null; Kind = $null }
    }
    return New-FakeKey $Subkey
}
function Remove-RegistryKey([string]$Subkey) { [void]$script:registry.Remove($Subkey) }
''' + production_functions + r'''

$missing = Read-RegistryDefault "missing"
Write-RegistryDefault "missing" "new"
Restore-RegistryDefault "missing" $missing

$script:registry["no-default"] = @{ DefaultExists = $false; Value = $null; Kind = $null }
$noDefault = Read-RegistryDefault "no-default"
Write-RegistryDefault "no-default" "new"
Restore-RegistryDefault "no-default" $noDefault

$script:registry["expand"] = @{
    DefaultExists = $true
    Value = "%LOCALAPPDATA%\previous.json"
    Kind = [int][Microsoft.Win32.RegistryValueKind]::ExpandString
}
$expand = Read-RegistryDefault "expand"
Write-RegistryDefault "expand" "new"
Restore-RegistryDefault "expand" $expand

[ordered]@{
    MissingKeyExists = $script:registry.ContainsKey("missing")
    NoDefaultKeyExists = $script:registry.ContainsKey("no-default")
    NoDefaultExists = $script:registry["no-default"].DefaultExists
    ExpandValue = $script:registry["expand"].Value
    ExpandKind = $script:registry["expand"].Kind
} | ConvertTo-Json -Compress
'''
        with tempfile.TemporaryDirectory(prefix="jobflow-native-registry-model-") as raw:
            harness_path = Path(raw) / "registry-model.ps1"
            harness_path.write_text(harness, encoding="utf-8-sig")
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL),
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness_path),
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        output_lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(output_lines, completed.stdout + completed.stderr)
        state = json.loads(output_lines[-1])
        self.assertFalse(state["MissingKeyExists"])
        self.assertTrue(state["NoDefaultKeyExists"])
        self.assertFalse(state["NoDefaultExists"])
        self.assertEqual(state["ExpandValue"], "%LOCALAPPDATA%\\previous.json")
        self.assertEqual(
            state["ExpandKind"],
            2,  # RegistryValueKind.ExpandString
        )


if __name__ == "__main__":
    unittest.main()
