from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path

from _support import PROJECT


WINDOWS_POWERSHELL = shutil.which("powershell.exe")
IS_WINDOWS = os.name == "nt" and WINDOWS_POWERSHELL is not None


def _wait_for(path: Path, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {path.name}")


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _open_without_delete_share(path: Path) -> tuple[object, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(path),
        0x80000000,
        0x00000001 | 0x00000002,
        None,
        3,
        0x00000080,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), f"Unable to lock {path.name}")
    return kernel32, handle


@unittest.skipUnless(IS_WINDOWS, "Direct Browser Companion installation is Windows-only.")
class CompanionInstallerHardeningTests(unittest.TestCase):
    def test_public_cmd_wrapper_uses_system_powershell_and_ignores_caller_shims(self) -> None:
        wrapper_path = PROJECT / "Install JobFlow Browser Companion.cmd"
        wrapper = wrapper_path.read_text(encoding="utf-8")
        trusted_entry = (
            r'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"'
        )
        self.assertIn(trusted_entry, wrapper)
        self.assertNotIn("\npowershell.exe ", wrapper.lower())

        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
        with tempfile.TemporaryDirectory(
            prefix="jobflow-companion-wrapper-", dir=system_temp
        ) as raw:
            source = Path(raw) / "Unicode 路径 with spaces"
            scripts = source / "scripts"
            scripts.mkdir(parents=True)
            copied_wrapper = source / wrapper_path.name
            shutil.copy2(wrapper_path, copied_wrapper)
            marker = Path(raw) / "trusted-system-powershell-used.txt"
            (scripts / "install-jobflow-browser-companion.ps1").write_text(
                "param()\n"
                "[IO.File]::WriteAllText($env:JOBFLOW_WRAPPER_MARKER, 'trusted', "
                "(New-Object Text.UTF8Encoding($false)))\n"
                "exit 0\n",
                encoding="ascii",
            )

            comspec = Path(os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"))
            self.assertTrue(comspec.is_file(), f"Missing canonical cmd.exe: {comspec}")
            # A caller-controlled executable in both the working directory and PATH
            # would win if the public wrapper used an unqualified powershell.exe.
            shutil.copy2(comspec, source / "powershell.exe")
            environment = os.environ.copy()
            environment["PATH"] = str(source)
            environment["JOBFLOW_WRAPPER_MARKER"] = str(marker)
            completed = subprocess.run(
                [str(comspec), "/d", "/c", "call", str(copied_wrapper)],
                cwd=source,
                env=environment,
                input="\n",
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "trusted")

    def _fixture(self, raw: str) -> tuple[Path, Path, Path, Path, dict[str, str], list[str]]:
        temporary = Path(raw)
        source = temporary / "source"
        scripts = source / "scripts"
        config = source / "config"
        companion = source / "browser-companion"
        local_app_data = temporary / "LocalAppData"
        for path in (scripts, config, companion, local_app_data):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            PROJECT / "scripts" / "install-jobflow-browser-companion.ps1",
            scripts / "install-jobflow-browser-companion.ps1",
        )
        shutil.copy2(PROJECT / ".jobops-root", source / ".jobops-root")
        shutil.copy2(
            PROJECT / "browser-companion" / "manifest.json",
            companion / "manifest.json",
        )
        shutil.copy2(
            PROJECT / "config" / "browser-companion-stores.json",
            config / "browser-companion-stores.json",
        )
        native_installer = scripts / "install-jobflow-native-host.ps1"
        native_installer.write_text("exit 0\n", encoding="ascii")
        environment = os.environ.copy()
        environment.update(
            {
                "LOCALAPPDATA": str(local_app_data),
                "APPDATA": str(temporary / "Roaming"),
                "TEMP": str(temporary / "Temp"),
                "TMP": str(temporary / "Temp"),
            }
        )
        (temporary / "Roaming").mkdir()
        (temporary / "Temp").mkdir()
        command = [
            str(WINDOWS_POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "install-jobflow-browser-companion.ps1"),
            "-NoLaunch",
        ]
        return source, companion, native_installer, local_app_data, environment, command

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )

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

    def test_browser_and_child_executables_are_provenanced_before_launch(self) -> None:
        installer = (
            PROJECT / "scripts" / "install-jobflow-browser-companion.ps1"
        ).read_text(encoding="utf-8-sig")

        self.assertNotIn('Get-Command "msedge.exe"', installer)
        self.assertNotIn('Get-Command "chrome.exe"', installer)
        self.assertNotIn("& powershell.exe", installer)
        self.assertNotIn('Start-Process -FilePath "explorer.exe"', installer)
        self.assertIn("[Environment]::GetFolderPath($Folder)", installer)
        self.assertIn("[Environment+SpecialFolder]::ProgramFiles", installer)
        self.assertIn("[Environment+SpecialFolder]::ProgramFilesX86", installer)
        self.assertIn("[Environment+SpecialFolder]::LocalApplicationData", installer)
        self.assertIn("Microsoft.PowerShell.Security\\Get-AuthenticodeSignature", installer)
        self.assertIn("[Management.Automation.SignatureStatus]::Valid", installer)
        self.assertIn('@("Microsoft Corporation")', installer)
        self.assertIn('@("Google LLC")', installer)
        self.assertIn("[IO.FileAttributes]::ReparsePoint", installer)
        self.assertIn("-not ($leaf -is [IO.FileInfo])", installer)
        self.assertIn("[IO.FileShare]::Read", installer)
        self.assertIn("Microsoft.PowerShell.Management\\Start-Process", installer)
        self.assertIn("Get-TrustedWindowsPowerShellExecutable", installer)
        self.assertIn("Get-TrustedWindowsIcaclsExecutable", installer)
        self.assertNotIn('$env:SystemRoot\\System32\\icacls.exe', installer)
        self.assertIn('throw "JOBFLOW_TRUSTED_CHROME_REQUIRED"', installer)
        self.assertIn('throw "JOBFLOW_TRUSTED_EDGE_REQUIRED"', installer)

    def test_path_poisoned_powershell_is_not_used_for_native_host_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-path-powershell-") as raw:
            source, _companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            malicious = Path(raw) / "malicious-path"
            malicious.mkdir()
            (malicious / "powershell.exe").write_bytes(b"not a Windows executable")
            environment["PATH"] = str(malicious) + os.pathsep + environment.get("PATH", "")
            marker = local_app_data / "canonical-powershell-used.txt"
            native_installer.write_text(
                "$path = Join-Path $env:LOCALAPPDATA 'canonical-powershell-used.txt'\n"
                "[IO.File]::WriteAllText($path, 'yes', (New-Object Text.UTF8Encoding($false)))\n"
                "exit 0\n",
                encoding="ascii",
            )

            result = self._run(command, cwd=source, environment=environment)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "yes")

    def test_path_poisoned_edge_is_not_selected_or_launched(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-path-edge-") as raw:
            source, _companion, _native_installer, local_app_data, environment, _command = self._fixture(raw)
            malicious = Path(raw) / "malicious-path"
            malicious.mkdir()
            fake_edge = malicious / "msedge.exe"
            fake_edge.write_bytes(b"not Microsoft Edge")
            environment["PATH"] = str(malicious) + os.pathsep + environment.get("PATH", "")
            capture = local_app_data / "trusted-edge-capture.txt"
            environment["JOBFLOW_TRUSTED_EDGE_CAPTURE"] = str(capture)

            installer = source / "scripts" / "install-jobflow-browser-companion.ps1"
            installer_text = installer.read_text(encoding="utf-8-sig")
            launch = (
                "Microsoft.PowerShell.Management\\Start-Process "
                "-FilePath $Target.Path -ArgumentList $ArgumentList"
            )
            self.assertEqual(installer_text.count(launch), 1)
            capture_only = (
                "[IO.File]::WriteAllText($env:JOBFLOW_TRUSTED_EDGE_CAPTURE, $Target.Path, "
                "(New-Object Text.UTF8Encoding($false)))"
            )
            installer.write_text(installer_text.replace(launch, capture_only), encoding="utf-8-sig")
            command = [
                str(WINDOWS_POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
                "-OpenStoreOnly",
                "-PreferredBrowser",
                "edge",
            ]

            result = self._run(command, cwd=source, environment=environment)
            combined = result.stdout + result.stderr
            if "JOBFLOW_TRUSTED_EDGE_REQUIRED" in combined:
                self.skipTest("No trusted canonical Microsoft Edge installation is present.")
            self.assertEqual(result.returncode, 0, combined)
            selected = Path(capture.read_text(encoding="utf-8"))
            self.assertNotEqual(selected.resolve(), fake_edge.resolve())
            self.assertEqual(selected.name.casefold(), "msedge.exe")
            self.assertIn("microsoft", [part.casefold() for part in selected.parts])
            self.assertFalse((local_app_data / "JobOps").exists())

    def test_rollback_failure_retains_runtime_and_binding_backups(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-rollback-hardening-") as raw:
            source, companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            source_marker = companion / "runtime-marker.txt"
            source_marker.write_text("first", encoding="utf-8")
            first = self._run(command, cwd=source, environment=environment)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            jobops = local_app_data / "JobOps"
            binding = jobops / "browser-companion-binding.json"
            runtime_marker = jobops / "BrowserCompanion" / "runtime-marker.txt"
            original_binding = binding.read_bytes()
            self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "first")

            source_marker.write_text("failed-update", encoding="utf-8")
            signal = local_app_data / "rollback-native-started.txt"
            native_installer.write_text(
                "$signal = Join-Path $env:LOCALAPPDATA 'rollback-native-started.txt'\n"
                "[IO.File]::WriteAllText($signal, 'ready', (New-Object Text.UTF8Encoding($false)))\n"
                "Start-Sleep -Seconds 5\n"
                "exit 1\n",
                encoding="ascii",
            )
            process = subprocess.Popen(
                command,
                cwd=source,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            handle_owner = None
            handle = None
            try:
                _wait_for(signal)
                self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "failed-update")
                handle_owner, handle = _open_without_delete_share(runtime_marker)
                stdout, stderr = process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("JOBFLOW_BROWSER_COMPANION_ROLLBACK_FAILED", stdout + stderr)

                runtime_backups = list(jobops.glob(".BrowserCompanion.backup-*"))
                binding_backups = list(jobops.glob(".browser-companion-binding-*.backup"))
                self.assertEqual(len(runtime_backups), 1)
                self.assertEqual(len(binding_backups), 1)
                self.assertEqual(
                    (runtime_backups[0] / "runtime-marker.txt").read_text(encoding="utf-8"),
                    "first",
                )
                self.assertEqual(binding_backups[0].read_bytes(), original_binding)
            finally:
                if handle_owner is not None and handle is not None:
                    handle_owner.CloseHandle(handle)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_post_commit_backup_cleanup_failure_is_warning_and_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-post-commit-cleanup-") as raw:
            source, companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            source_marker = companion / "runtime-marker.txt"
            source_marker.write_text("first", encoding="utf-8")
            first = self._run(command, cwd=source, environment=environment)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            jobops = local_app_data / "JobOps"
            binding = jobops / "browser-companion-binding.json"
            runtime_marker = jobops / "BrowserCompanion" / "runtime-marker.txt"
            original_binding = binding.read_bytes()
            source_marker.write_text("second", encoding="utf-8")
            installer = source / "scripts" / "install-jobflow-browser-companion.ps1"
            installer_text = installer.read_text(encoding="utf-8-sig")
            preference_anchor = '$ErrorActionPreference = "Stop"'
            self.assertEqual(installer_text.count(preference_anchor), 1)
            installer.write_text(
                installer_text.replace(
                    preference_anchor,
                    preference_anchor + '\n$WarningPreference = "Stop"',
                ),
                encoding="utf-8",
            )
            signal = local_app_data / "post-commit-native-started.txt"
            native_installer.write_text(
                "$signal = Join-Path $env:LOCALAPPDATA 'post-commit-native-started.txt'\n"
                "[IO.File]::WriteAllText($signal, 'ready', (New-Object Text.UTF8Encoding($false)))\n"
                "Start-Sleep -Seconds 4\n"
                "exit 0\n",
                encoding="ascii",
            )
            process = subprocess.Popen(
                command,
                cwd=source,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            handle_owner = None
            handle = None
            try:
                _wait_for(signal)
                runtime_backups = list(jobops.glob(".BrowserCompanion.backup-*"))
                self.assertEqual(len(runtime_backups), 1)
                backup_marker = runtime_backups[0] / "runtime-marker.txt"
                handle_owner, handle = _open_without_delete_share(backup_marker)
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                self.assertIn(
                    "JOBFLOW_BROWSER_COMPANION_POST_COMMIT_CLEANUP_FAILED",
                    stdout + stderr,
                )
                self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "second")
                self.assertEqual(binding.read_bytes(), original_binding)
                self.assertTrue(runtime_backups[0].exists())
            finally:
                if handle_owner is not None and handle is not None:
                    handle_owner.CloseHandle(handle)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_failure_cleanup_does_not_mask_original_install_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-failure-cleanup-") as raw:
            source, companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            source_marker = companion / "runtime-marker.txt"
            source_marker.write_text("first", encoding="utf-8")
            first = self._run(command, cwd=source, environment=environment)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            jobops = local_app_data / "JobOps"
            binding = jobops / "browser-companion-binding.json"
            runtime_marker = jobops / "BrowserCompanion" / "runtime-marker.txt"
            original_binding = binding.read_bytes()
            source_marker.write_text("failed-update", encoding="utf-8")
            signal = local_app_data / "failure-cleanup-native-started.txt"
            native_installer.write_text(
                "$signal = Join-Path $env:LOCALAPPDATA 'failure-cleanup-native-started.txt'\n"
                "[IO.File]::WriteAllText($signal, 'ready', (New-Object Text.UTF8Encoding($false)))\n"
                "Start-Sleep -Seconds 4\n"
                "exit 1\n",
                encoding="ascii",
            )
            process = subprocess.Popen(
                command,
                cwd=source,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            handle_owner = None
            handle = None
            try:
                _wait_for(signal)
                runtime_backups = list(jobops.glob(".BrowserCompanion.backup-*"))
                self.assertEqual(len(runtime_backups), 1)
                backup_marker = runtime_backups[0] / "runtime-marker.txt"
                handle_owner, handle = _open_without_delete_share(backup_marker)
                stdout, stderr = process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("JOBFLOW_NATIVE_HOST_INSTALL_FAILED", stdout + stderr)
                self.assertIn(
                    "JOBFLOW_BROWSER_COMPANION_FAILURE_CLEANUP_FAILED",
                    stdout + stderr,
                )
                self.assertNotIn("JOBFLOW_BROWSER_COMPANION_ROLLBACK_FAILED", stdout + stderr)
                self.assertEqual(runtime_marker.read_text(encoding="utf-8"), "first")
                self.assertEqual(binding.read_bytes(), original_binding)
                self.assertTrue(runtime_backups[0].exists())
            finally:
                if handle_owner is not None and handle is not None:
                    handle_owner.CloseHandle(handle)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_synchronized_source_change_aborts_before_active_swap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-source-change-") as raw:
            source, companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            source_marker = companion / "runtime-marker.txt"
            source_marker.write_text("first-active", encoding="utf-8")
            first = self._run(command, cwd=source, environment=environment)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            jobops = local_app_data / "JobOps"
            binding = jobops / "browser-companion-binding.json"
            runtime = jobops / "BrowserCompanion"
            binding_before = binding.read_bytes()
            runtime_before = _tree_snapshot(runtime)
            source_marker.write_text("stable-source", encoding="utf-8")
            executed = local_app_data / "source-change-native-executed.txt"
            native_installer.write_text(
                "$path = Join-Path $env:LOCALAPPDATA 'source-change-native-executed.txt'\n"
                "[IO.File]::WriteAllText($path, 'unsafe', (New-Object Text.UTF8Encoding($false)))\n"
                "exit 0\n",
                encoding="ascii",
            )

            installer = source / "scripts" / "install-jobflow-browser-companion.ps1"
            installer_text = installer.read_text(encoding="utf-8-sig")
            anchor = "$sourceSnapshot = @(Get-JobFlowSourceSnapshot $sourceExtensionRoot)"
            self.assertEqual(installer_text.count(anchor), 1)
            injection = (
                anchor
                + "\n$sourceSnapshotSignal = Join-Path $env:LOCALAPPDATA 'source-snapshot-ready.txt'"
                + "\n$sourceSnapshotRelease = Join-Path $env:LOCALAPPDATA 'source-snapshot-release.txt'"
                + "\n[IO.File]::WriteAllText($sourceSnapshotSignal, 'ready', "
                + "(New-Object Text.UTF8Encoding($false)))"
                + "\n$sourceSnapshotDeadline = [DateTime]::UtcNow.AddSeconds(10)"
                + "\nwhile (-not (Test-Path -LiteralPath $sourceSnapshotRelease -PathType Leaf)) {"
                + "\n    if ([DateTime]::UtcNow -ge $sourceSnapshotDeadline) { throw 'SOURCE_SNAPSHOT_TEST_TIMEOUT' }"
                + "\n    Start-Sleep -Milliseconds 25"
                + "\n}"
            )
            installer.write_text(installer_text.replace(anchor, injection), encoding="utf-8")
            signal = local_app_data / "source-snapshot-ready.txt"
            release = local_app_data / "source-snapshot-release.txt"
            process = subprocess.Popen(
                command,
                cwd=source,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _wait_for(signal)
                source_marker.write_text("mutated-data!", encoding="utf-8")
                release.write_text("continue", encoding="ascii")
                stdout, stderr = process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("JOBFLOW_BROWSER_COMPANION_SOURCE_CHANGED", stdout + stderr)
                self.assertFalse(executed.exists())
                self.assertEqual(binding.read_bytes(), binding_before)
                self.assertEqual(_tree_snapshot(runtime), runtime_before)
                self.assertFalse(any(jobops.glob(".BrowserCompanion.install-*")))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_synchronized_staging_change_aborts_before_active_swap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-staging-change-") as raw:
            source, companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            source_marker = companion / "runtime-marker.txt"
            source_marker.write_text("first-active", encoding="utf-8")
            first = self._run(command, cwd=source, environment=environment)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            jobops = local_app_data / "JobOps"
            binding = jobops / "browser-companion-binding.json"
            runtime = jobops / "BrowserCompanion"
            binding_before = binding.read_bytes()
            runtime_before = _tree_snapshot(runtime)
            source_marker.write_text("stable-source", encoding="utf-8")
            executed = local_app_data / "staging-change-native-executed.txt"
            native_installer.write_text(
                "$path = Join-Path $env:LOCALAPPDATA 'staging-change-native-executed.txt'\n"
                "[IO.File]::WriteAllText($path, 'unsafe', (New-Object Text.UTF8Encoding($false)))\n"
                "exit 0\n",
                encoding="ascii",
            )

            installer = source / "scripts" / "install-jobflow-browser-companion.ps1"
            installer_text = installer.read_text(encoding="utf-8-sig")
            anchor = "Copy-JobFlowSourceSnapshot $sourceExtensionRoot $stagingRoot $sourceSnapshot"
            self.assertEqual(installer_text.count(anchor), 1)
            injection = (
                anchor
                + "\n$stagingSnapshotSignal = Join-Path $env:LOCALAPPDATA 'staging-snapshot-ready.txt'"
                + "\n$stagingSnapshotRelease = Join-Path $env:LOCALAPPDATA 'staging-snapshot-release.txt'"
                + "\n[IO.File]::WriteAllText($stagingSnapshotSignal, 'ready', "
                + "(New-Object Text.UTF8Encoding($false)))"
                + "\n$stagingSnapshotDeadline = [DateTime]::UtcNow.AddSeconds(10)"
                + "\nwhile (-not (Test-Path -LiteralPath $stagingSnapshotRelease -PathType Leaf)) {"
                + "\n    if ([DateTime]::UtcNow -ge $stagingSnapshotDeadline) { throw 'STAGING_SNAPSHOT_TEST_TIMEOUT' }"
                + "\n    Start-Sleep -Milliseconds 25"
                + "\n}"
            )
            installer.write_text(installer_text.replace(anchor, injection), encoding="utf-8")
            signal = local_app_data / "staging-snapshot-ready.txt"
            release = local_app_data / "staging-snapshot-release.txt"
            process = subprocess.Popen(
                command,
                cwd=source,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _wait_for(signal)
                staging_roots = list(jobops.glob(".BrowserCompanion.install-*"))
                self.assertEqual(len(staging_roots), 1)
                staged_marker = staging_roots[0] / "runtime-marker.txt"
                self.assertEqual(staged_marker.read_text(encoding="utf-8"), "stable-source")
                staged_marker.write_text("mutated-data!", encoding="utf-8")
                release.write_text("continue", encoding="ascii")
                stdout, stderr = process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("JOBFLOW_BROWSER_COMPANION_STAGING_MISMATCH", stdout + stderr)
                self.assertFalse(executed.exists())
                self.assertEqual(binding.read_bytes(), binding_before)
                self.assertEqual(_tree_snapshot(runtime), runtime_before)
                self.assertFalse(any(jobops.glob(".BrowserCompanion.install-*")))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_recursive_source_reparse_is_rejected_before_local_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-source-reparse-") as raw:
            source, companion, _native_installer, local_app_data, environment, command = self._fixture(raw)
            outside = Path(raw) / "outside-extension-content"
            outside.mkdir()
            (outside / "payload.txt").write_text("must not be copied", encoding="utf-8")
            link = companion / "linked-assets"
            self._create_directory_reparse(link, outside)
            try:
                result = self._run(command, cwd=source, environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "JOBFLOW_BROWSER_COMPANION_SOURCE_REPARSE_FORBIDDEN",
                    result.stdout + result.stderr,
                )
                self.assertFalse((local_app_data / "JobOps").exists())
            finally:
                if link.is_symlink():
                    link.unlink()
                elif link.exists():
                    os.rmdir(link)

    def test_native_installer_reparse_chain_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-native-reparse-") as raw:
            source, _companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            installer = source / "scripts" / "install-jobflow-browser-companion.ps1"
            installer_text = installer.read_text(encoding="utf-8-sig")
            original_assignment = 'Join-Path $PSScriptRoot "install-jobflow-native-host.ps1"'
            linked_assignment = 'Join-Path $PSScriptRoot "native-link\\install-jobflow-native-host.ps1"'
            self.assertEqual(installer_text.count(original_assignment), 1)
            installer.write_text(
                installer_text.replace(original_assignment, linked_assignment),
                encoding="utf-8",
            )
            native_installer.unlink()
            outside = Path(raw) / "outside-native-installer"
            outside.mkdir()
            executed = local_app_data / "native-installer-executed.txt"
            (outside / "install-jobflow-native-host.ps1").write_text(
                "$path = Join-Path $env:LOCALAPPDATA 'native-installer-executed.txt'\n"
                "[IO.File]::WriteAllText($path, 'unsafe', (New-Object Text.UTF8Encoding($false)))\n"
                "exit 0\n",
                encoding="ascii",
            )
            link = source / "scripts" / "native-link"
            self._create_directory_reparse(link, outside)
            try:
                result = self._run(command, cwd=source, environment=environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "JOBFLOW_BROWSER_COMPANION_SOURCE_REPARSE_FORBIDDEN",
                    result.stdout + result.stderr,
                )
                self.assertFalse(executed.exists())
                self.assertFalse((local_app_data / "JobOps" / "BrowserCompanion").exists())
                self.assertFalse(
                    (local_app_data / "JobOps" / "browser-companion-binding.json").exists()
                )
            finally:
                if link.is_symlink():
                    link.unlink()
                elif link.exists():
                    os.rmdir(link)

    def test_install_mutex_times_out_without_changing_active_installation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-mutex-") as raw:
            source, companion, native_installer, local_app_data, environment, command = self._fixture(raw)
            (companion / "runtime-marker.txt").write_text("locked-first", encoding="utf-8")
            signal = local_app_data / "mutex-native-started.txt"
            native_installer.write_text(
                "$signal = Join-Path $env:LOCALAPPDATA 'mutex-native-started.txt'\n"
                "[IO.File]::WriteAllText($signal, 'ready', (New-Object Text.UTF8Encoding($false)))\n"
                "Start-Sleep -Seconds 35\n"
                "exit 0\n",
                encoding="ascii",
            )
            first = subprocess.Popen(
                command,
                cwd=source,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _wait_for(signal)
                jobops = local_app_data / "JobOps"
                binding = jobops / "browser-companion-binding.json"
                runtime = jobops / "BrowserCompanion"
                binding_before = binding.read_bytes()
                runtime_before = _tree_snapshot(runtime)

                second = self._run(command, cwd=source, environment=environment, timeout=40)
                self.assertNotEqual(second.returncode, 0)
                self.assertIn(
                    "JOBFLOW_BROWSER_COMPANION_INSTALL_ALREADY_RUNNING",
                    second.stdout + second.stderr,
                )
                self.assertEqual(binding.read_bytes(), binding_before)
                self.assertEqual(_tree_snapshot(runtime), runtime_before)

                first_stdout, first_stderr = first.communicate(timeout=15)
                self.assertEqual(first.returncode, 0, first_stdout + first_stderr)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.wait(timeout=10)

    def test_install_lock_hardlink_is_rejected_before_growth_or_acl(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-companion-lock-hardlink-") as raw:
            source, _companion, _native_installer, local_app_data, environment, command = self._fixture(raw)
            jobops = local_app_data / "JobOps"
            jobops.mkdir()
            outside = Path(raw) / "outside-lock-sentinel.bin"
            outside.write_bytes(b"")
            os.link(outside, jobops / ".browser-companion-install.lock")

            result = self._run(command, cwd=source, environment=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "JOBFLOW_BROWSER_COMPANION_INSTALL_LOCK_LINKED",
                result.stdout + result.stderr,
            )
            self.assertEqual(outside.read_bytes(), b"")
            self.assertFalse((jobops / "BrowserCompanion").exists())


if __name__ == "__main__":
    unittest.main()
