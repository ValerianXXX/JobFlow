from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-jobflow-v2.ps1"
POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
KEY_ID = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
STABLE_FILES = (
    "jobflow-bootstrap.ps1",
    "start-installed-jobflow.ps1",
    "check-installed-jobflow.ps1",
    "update-installed-jobflow.ps1",
    "rollback-installed-jobflow.ps1",
    "uninstall-installed-jobflow.ps1",
    "jobflow-runtime-locks.ps1",
    "manage-authorized-discovery-task.ps1",
    "run-authorized-discovery-task.ps1",
    "Start JobFlow.cmd",
    "Check JobFlow.cmd",
    "Update JobFlow.cmd",
    "Rollback JobFlow.cmd",
    "Uninstall JobFlow.cmd",
)


BOOTSTRAP_FIXTURE = r'''[CmdletBinding()]
param(
    [string]$ManifestPath,
    [string]$SignaturePath,
    [string]$ArchivePath,
    [switch]$DescribeManifest,
    [switch]$RecoverOnly,
    [switch]$VerifyInstalled,
    [switch]$Activate
)
$ErrorActionPreference = "Stop"
$local = $env:JOBFLOW_INSTALL_ACCEPTANCE_LOCALAPPDATA
if ([string]::IsNullOrWhiteSpace($local)) { [Console]::Error.WriteLine("FIXTURE_LOCALAPPDATA_MISSING"); exit 9 }
$jobops = Join-Path $local "JobOps"
$log = Join-Path $local "bootstrap-modes.log"
function Log([string]$mode) { [IO.File]::AppendAllText($log, $mode + "`n", [Text.UTF8Encoding]::new($false)) }
function Sha([string]$path) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return "sha256:" + (-join ($hash.ComputeHash([IO.File]::ReadAllBytes($path)) | ForEach-Object { $_.ToString("x2") })) }
    finally { $hash.Dispose() }
}
function ReadManifest {
    $value = [IO.File]::ReadAllText($ManifestPath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json
    $signature = [IO.File]::ReadAllText($SignaturePath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json
    if ([int]$value.schema_version -ne 2 -or [string]$value.synthetic_signature -cne "synthetic-signed-v2" -or
        [string]$signature.key_id -cne "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339") {
        [Console]::Error.WriteLine("FIXTURE_MANIFEST_REJECTED"); exit 4
    }
    return $value
}
if ($RecoverOnly) {
    Log "RecoverOnly"
    $stream = if ([IO.File]::Exists((Join-Path $local "emit-error-clixml"))) { "Error" } else { "progress" }
    $progressDocument = '#< CLIXML' + "`r`n" + '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><S S="progress">Preparing modules for first use.</S></Objs>'
    $secondDocument = '#< CLIXML' + "`r`n" + '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><S S="' + $stream + '">Preparing modules for first use.</S></Objs>'
    # Hosted Windows PowerShell can emit a fresh BOM for each serialized
    # stream document.  Keep the fixture representative of that boundary.
    $separator = if ([IO.File]::Exists((Join-Path $local "include-second-clixml-header"))) {
        ([char]0xFEFF) + "`r`n#< CLIXML`r`n"
    } else {
        ([char]0xFEFF) + "`r`n"
    }
    $cliXml = $progressDocument + $separator + $secondDocument.Substring($secondDocument.IndexOf("<Objs"))
    [Console]::Error.Write($cliXml)
    if ([IO.Directory]::Exists($jobops) -and -not [IO.File]::Exists((Join-Path $jobops "current.json"))) {
        [Console]::Error.WriteLine("FIXTURE_EXISTING_ROOT_INVALID"); exit 3
    }
    @{schema_version=1;status="JOBFLOW_ACTIVATION_RECOVERY_NOT_PENDING";recovery_performed=$false;activation_committed=$false;retry_required=$false;real_external_actions=0} | ConvertTo-Json -Compress
    exit 0
}
if ($DescribeManifest) {
    Log "DescribeManifest"
    [void](ReadManifest)
    @{schema_version=1;status="JOBFLOW_BOOTSTRAP_MANIFEST_VERIFIED";signature_verified=$true;key_id="sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339";manifest_schema_version=2;publisher_attestation_bound=$true;manifest_sha256=(Sha $ManifestPath);manifest_bytes=([IO.FileInfo]$ManifestPath).Length;real_external_actions=0} | ConvertTo-Json -Compress
    exit 0
}
if ($Activate) {
    Log "Activate"
    $manifest = ReadManifest
    $archiveSha = Sha $ArchivePath
    if ($archiveSha -cne [string]$manifest.archive_sha256) { [Console]::Error.WriteLine("FIXTURE_ARCHIVE_REJECTED"); exit 5 }
    $version = [string]$manifest.version
    $sourceHex = $archiveSha.Substring(7)
    $directory = "v$version-" + $sourceHex.Substring(0, 12)
    $runtime = Join-Path $jobops ("Application\versions\" + $directory)
    [IO.Directory]::CreateDirectory($runtime) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $jobops "Data\state")) | Out-Null
    [IO.File]::WriteAllText((Join-Path $jobops "Data\state\preserved-private-sentinel.bin"), "PRESERVE", [Text.UTF8Encoding]::new($false))
    $pointer = [ordered]@{
        bootstrap_version="0.6.0"; platform="windows-x64"; product="JobFlow";
        release_key_id="sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339";
        runtime_closure_manifest_sha256=("sha256:" + ("3" * 64)); runtime_tree_sha256=("sha256:" + ("4" * 64));
        schema_version=2; source_commit=("a" * 40); source_payload_sha256=$archiveSha;
        version=$version; version_directory=$directory
    }
    [IO.File]::WriteAllText((Join-Path $jobops "current.json"), ($pointer | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    @{status="JOBFLOW_BOOTSTRAP_ACTIVATED";version=$version;source_payload_sha256=$archiveSha;runtime_tree_sha256=("sha256:" + ("4" * 64));activation_performed=$true;real_external_actions=0} | ConvertTo-Json -Compress
    exit 0
}
if ($VerifyInstalled) {
    Log "VerifyInstalled"
    $pointerPath = Join-Path $jobops "current.json"
    if (-not [IO.File]::Exists($pointerPath)) { [Console]::Error.WriteLine("FIXTURE_NOT_INSTALLED"); exit 7 }
    $pointer = [IO.File]::ReadAllText($pointerPath) | ConvertFrom-Json
    @{schema_version=1;status="JOBFLOW_INSTALLED_RUNTIME_VERIFIED";version=[string]$pointer.version;manifest_sha256=("sha256:" + ("1" * 64));signature_envelope_sha256=("sha256:" + ("2" * 64));runtime_closure_manifest_sha256=[string]$pointer.runtime_closure_manifest_sha256;runtime_tree_sha256=[string]$pointer.runtime_tree_sha256;release_key_id=[string]$pointer.release_key_id;source_payload_sha256=[string]$pointer.source_payload_sha256;signed_activation_evidence_verified=$true;recovery_performed=$false;activation_committed_during_recovery=$false;paths_disclosed=$false;real_external_actions=0} | ConvertTo-Json -Compress
    exit 0
}
[Console]::Error.WriteLine("FIXTURE_MODE_INVALID")
exit 8
'''


class ThinV2InstallerTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        if not POWERSHELL.exists():
            raise unittest.SkipTest("Windows PowerShell 5.1 is required")
        cls.source = INSTALLER.read_text(encoding="utf-8-sig")

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:12]
        self.qa_root = Path(tempfile.gettempdir()) / f"jobflow-v2-install-qa-{suffix}"
        self.project = self.qa_root / "project"
        self.local_app_data = self.qa_root / "LocalAppData"
        self.fixture = self.qa_root / "fixture"
        (self.project / "scripts" / "windows-runtime").mkdir(parents=True)
        self.local_app_data.mkdir(parents=True)
        self.fixture.mkdir(parents=True)
        (self.project / ".jobops-root").write_text("jobops-root-v1\n", encoding="utf-8")
        shutil.copy2(INSTALLER, self.project / "scripts" / "install-jobflow-v2.ps1")
        runtime = self.project / "scripts" / "windows-runtime"
        for name in STABLE_FILES:
            body = BOOTSTRAP_FIXTURE if name == "jobflow-bootstrap.ps1" else f"CONTROL::{name}\n"
            (runtime / name).write_text(body, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.qa_root, ignore_errors=True)

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _prepare_release(self, *, schema_v2: bool = True, include_archive: bool = True) -> None:
        version = "0.7.0"
        tag = f"v{version}"
        archive_name = f"JobFlow-{tag}-windows-x64-complete.zip"
        archive = b"SYNTHETIC_SIGNED_V2_COMPLETE_RUNTIME\x00"
        archive_sha = "sha256:" + hashlib.sha256(archive).hexdigest()
        manifest = {
            "schema_version": 2 if schema_v2 else 1,
            "version": version,
            "archive_sha256": archive_sha,
            "synthetic_signature": "synthetic-signed-v2" if schema_v2 else "legacy-v1",
        }
        signature = {
            "schema_version": 1,
            "algorithm": "RSA-PKCS1-v1_5-SHA256",
            "key_id": KEY_ID,
            "signature_b64url": "synthetic",
        }
        manifest_bytes = self._canonical(manifest)
        signature_bytes = self._canonical(signature)
        (self.fixture / "JobFlow-update-manifest.json").write_bytes(manifest_bytes)
        (self.fixture / "JobFlow-update-manifest.sig.json").write_bytes(signature_bytes)
        if include_archive:
            (self.fixture / archive_name).write_bytes(archive)

        def asset(name: str, size: int) -> dict[str, object]:
            return {
                "name": name,
                "size": size,
                "state": "uploaded",
                "browser_download_url": f"https://github.com/ValerianXXX/JobFlow/releases/download/{tag}/{name}",
            }

        assets = [
            asset("JobFlow-update-manifest.json", len(manifest_bytes)),
            asset("JobFlow-update-manifest.sig.json", len(signature_bytes)),
        ]
        if include_archive:
            assets.append(asset(archive_name, len(archive)))
        release = {"draft": False, "prerelease": False, "tag_name": tag, "assets": assets}
        (self.fixture / "release.json").write_bytes(self._canonical(release))

    def _run(self, *, local_app_data: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "LOCALAPPDATA": str(local_app_data or self.local_app_data),
                "JOBFLOW_INSTALL_V2_ACCEPTANCE_CORE_ONLY": "1",
            }
        )
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.project / "scripts" / "install-jobflow-v2.ps1"),
                "-NoLaunch",
            ],
            cwd=self.project,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )

    def test_acceptance_mode_rejects_a_different_local_app_data_root(self) -> None:
        wrong_local_app_data = self.qa_root / "OtherLocalAppData"
        wrong_local_app_data.mkdir()
        result = self._run(local_app_data=wrong_local_app_data)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("JOBFLOW_INSTALL_V2_ACCEPTANCE_BYPASS_FORBIDDEN", result.stderr)
        self.assertFalse((wrong_local_app_data / "JobOps").exists())

    def test_static_contract_is_thin_bootstrap_first(self) -> None:
        lower = self.source.casefold()
        for forbidden in (
            ".venv",
            "python.exe",
            "install-jobflow.ps1",
            "expand-archive",
            "installer_relative_path",
        ):
            self.assertNotIn(forbidden, lower)
        self.assertIn('Invoke-StableBootstrap "RecoverOnly"', self.source)
        self.assertIn('Invoke-StableBootstrap "DescribeManifest"', self.source)
        self.assertIn('Invoke-StableBootstrap "Activate"', self.source)
        self.assertIn('Invoke-StableBootstrap "VerifyInstalled"', self.source)
        self.assertIn("Initialize-StableBootstrapPowerShell", self.source)
        self.assertIn('["PSModuleAnalysisCachePath"] = $moduleAnalysisCachePath', self.source)
        self.assertIn('["PSModulePath"] = $trustedPowerShellModulePath', self.source)
        self.assertIn('["PSDisableModuleAnalysisCacheCleanup"] = "1"', self.source)
        self.assertNotIn("JOBFLOW_INSTALL_ACCEPTANCE_DEBUG", self.source)
        self.assertNotIn("$_.Exception.GetType().Name", self.source)
        self.assertIn(
            '"JOBFLOW_INSTALL_ACCEPTANCE_CAUSE:" + $acceptanceCause', self.source
        )
        activated_flow = self.source[self.source.index("$activationInvocation") :]
        self.assertLess(
            activated_flow.index('Invoke-StableBootstrap "VerifyInstalled"'),
            activated_flow.index("Install-StableControlPlaneAtomic"),
        )

    def test_synthetic_signed_v2_fresh_install_activates_then_copies_control_plane(self) -> None:
        self._prepare_release()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        jobops = self.local_app_data / "JobOps"
        pointer = json.loads((jobops / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(pointer["schema_version"], 2)
        self.assertEqual(pointer["version"], "0.7.0")
        self.assertEqual(
            (jobops / "Data" / "state" / "preserved-private-sentinel.bin").read_text(encoding="utf-8"),
            "PRESERVE",
        )
        installed = jobops / "bin"
        self.assertEqual(sorted(path.name for path in installed.iterdir()), sorted(STABLE_FILES))
        source = self.project / "scripts" / "windows-runtime"
        for name in STABLE_FILES:
            self.assertEqual((installed / name).read_bytes(), (source / name).read_bytes())
        modes = (self.local_app_data / "bootstrap-modes.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(modes, ["RecoverOnly", "DescribeManifest", "Activate", "VerifyInstalled", "VerifyInstalled"])
        state = self.local_app_data / "JobFlowInstaller"
        self.assertFalse((state / "install-journal.json").exists())
        self.assertEqual(list(state.glob(".jfi-*")), [])
        self.assertEqual(list(state.glob(".psmc-*")), [])
        self.assertEqual(list(state.glob(".cp-*")), [])
        self.assertEqual(list(state.glob(".cpb-*")), [])

    def test_progress_clixml_accepts_repeated_document_headers(self) -> None:
        self._prepare_release()
        (self.local_app_data / "include-second-clixml-header").write_text("1", encoding="utf-8")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.local_app_data / "JobOps" / "current.json").is_file())

    def test_preexisting_unverified_jobops_root_is_preserved_and_blocks_install(self) -> None:
        self._prepare_release()
        jobops = self.local_app_data / "JobOps"
        jobops.mkdir()
        sentinel = jobops / "do-not-delete.bin"
        sentinel.write_bytes(b"UNRELATED_PREEXISTING_ROOT")
        result = self._run()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("JOBFLOW_INSTALL_RECOVERY_REQUIRED", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"UNRELATED_PREEXISTING_ROOT")
        self.assertFalse((jobops / "bin").exists())
        self.assertFalse((jobops / "current.json").exists())

    def test_error_clixml_is_not_treated_as_benign_progress(self) -> None:
        self._prepare_release()
        (self.local_app_data / "emit-error-clixml").write_text("1", encoding="utf-8")
        result = self._run()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("JOBFLOW_INSTALL_RECOVERY_REQUIRED", result.stderr)
        self.assertFalse((self.local_app_data / "JobOps").exists())

    def test_interrupted_control_plane_move_is_recovered_then_stops_for_retry(self) -> None:
        self._prepare_release()
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

        transaction_id = "0123456789ab"
        jobops = self.local_app_data / "JobOps"
        state = self.local_app_data / "JobFlowInstaller"
        installed = jobops / "bin"
        backup = state / f".cpb-{transaction_id}"
        stage = state / f".cp-{transaction_id}"
        installed.rename(backup)
        stage.mkdir()
        source = self.project / "scripts" / "windows-runtime"
        inventory: list[dict[str, object]] = []
        for name in STABLE_FILES:
            payload = (source / name).read_bytes()
            (stage / name).write_bytes(payload)
            inventory.append(
                {
                    "name": name,
                    "bytes": len(payload),
                    "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                }
            )
        journal = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "state": "OLD_MOVED",
            "old_bin_present": True,
            "files": inventory,
        }
        (state / "install-journal.json").write_bytes(self._canonical(journal))

        recovered = self._run()
        self.assertEqual(recovered.returncode, 6, recovered.stdout + recovered.stderr)
        self.assertIn("JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED", recovered.stderr)
        self.assertTrue(installed.is_dir())
        self.assertFalse(backup.exists())
        self.assertFalse(stage.exists())
        self.assertFalse((state / "install-journal.json").exists())
        modes = (self.local_app_data / "bootstrap-modes.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(modes[-2:], ["RecoverOnly", "VerifyInstalled"])

    def test_schema_v1_public_shape_fails_closed_without_creating_jobops(self) -> None:
        self._prepare_release(schema_v2=False, include_archive=False)
        result = self._run()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("JOBFLOW_INSTALL_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED", result.stderr)
        self.assertFalse((self.local_app_data / "JobOps").exists())


if __name__ == "__main__":
    unittest.main()
