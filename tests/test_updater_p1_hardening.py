from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.update_manifest import (
    _read_bounded_json,
    attest_extracted_payload,
    inventory_archive_payload,
)
from jobops.util import canonical_json


class UpdaterP1HardeningTests(unittest.TestCase):
    def test_updater_is_a_thin_stable_bootstrap_client(self) -> None:
        updater = (PROJECT / "scripts/windows-runtime/update-installed-jobflow.ps1").read_text(
            encoding="utf-8-sig"
        )
        for required in (
            '$bootstrapPath = Join-Path $localRoot "bin\\jobflow-bootstrap.ps1"',
            'Invoke-StableBootstrap "RecoverOnly"',
            'Invoke-StableBootstrap "DescribeManifest"',
            'Invoke-StableBootstrap "Activate"',
            "$sourceBytes = [Convert]::FromBase64String($encodedSource)",
            ".TrimStart([char]0xFEFF).Trim()",
            "[Console]::InputEncoding = [Text.UTF8Encoding]::new($false, $true)",
            "$process.StandardInput.BaseStream.Write($stdinBytes",
            'Read-AndValidateV2CurrentPointer',
            'JOBFLOW_UPDATE_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED',
            'JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED',
            'JOBFLOW_UPDATE_RECOVERY_REQUIRED',
            'AllowAutoRedirect = $false',
            'Assert-AllowedHttpsUri',
            "Open-NewUpdaterFileRelative",
            '$start.EnvironmentVariables.Clear()',
            '$result.activation_performed -ne $true',
            '[IO.Directory]::Delete',
        ):
            self.assertIn(required, updater)
        for forbidden in (
            ".venv",
            "python.exe",
            "jobops.update_manifest",
            "Expand-Archive",
            "Expand-LockedVerifiedArchive",
            "IO.Compression.ZipArchive",
            "install-jobflow.ps1",
            "TrustedUpdatePayloadManifest",
            "$process.StandardInput.Write($bootstrapSource)",
        ):
            self.assertNotIn(forbidden, updater)

        recover = updater.index('Invoke-StableBootstrap "RecoverOnly"')
        first_network = updater.index("Receive-AllowedHttpsFile (")
        describe = updater.index('Invoke-StableBootstrap "DescribeManifest"')
        archive_download = updater.index(
            '$archiveIdentityLock = Receive-AllowedHttpsFile ('
        )
        activate = updater.index('Invoke-StableBootstrap "Activate"')
        pointer = updater.index("Read-AndValidateV2CurrentPointer", activate)
        self.assertLess(recover, first_network)
        self.assertLess(describe, archive_download)
        self.assertLess(archive_download, activate)
        self.assertLess(activate, pointer)

    def _run_early_bootstrap(self, bootstrap: str) -> subprocess.CompletedProcess[str]:
        powershell = shutil.which("powershell.exe")
        if os.name != "nt" or powershell is None:
            self.skipTest("requires Windows PowerShell")
        with tempfile.TemporaryDirectory(prefix="jobflow-thin-updater-") as raw:
            local_app_data = Path(raw) / "LocalAppData"
            bin_root = local_app_data / "JobOps" / "bin"
            bin_root.mkdir(parents=True)
            shutil.copy2(
                PROJECT / "scripts/windows-runtime/update-installed-jobflow.ps1",
                bin_root / "update-installed-jobflow.ps1",
            )
            (bin_root / "jobflow-bootstrap.ps1").write_text(
                bootstrap, encoding="utf-8-sig"
            )
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(local_app_data)
            completed = subprocess.run(
                [
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(bin_root / "update-installed-jobflow.ps1"),
                ],
                cwd=bin_root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(list((local_app_data / "JobOps").glob(".u-*")), [])
            return completed

    def test_recovery_performed_stops_before_network_without_same_run_retry(self) -> None:
        payload = json.dumps(
            {
                "schema_version": 1,
                "status": "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED",
                "recovery_performed": True,
                "activation_committed": False,
                "retry_required": True,
                "real_external_actions": 0,
            },
            separators=(",", ":"),
        )
        completed = self._run_early_bootstrap(
            "[CmdletBinding()] param([switch]$RecoverOnly)\n"
            f"[Console]::Out.WriteLine('{payload}')\n"
            "exit 6\n"
        )
        self.assertEqual(completed.returncode, 6, completed.stdout + completed.stderr)
        self.assertIn("JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED", completed.stderr)
        self.assertNotIn("Checking for a signed stable JobFlow update", completed.stdout)

    def test_ambiguous_bootstrap_result_requires_recovery_without_network(self) -> None:
        completed = self._run_early_bootstrap(
            "[CmdletBinding()] param([switch]$RecoverOnly)\n"
            "[Console]::Out.WriteLine('not-json')\n"
            "exit 0\n"
        )
        self.assertEqual(completed.returncode, 3, completed.stdout + completed.stderr)
        self.assertIn("JOBFLOW_UPDATE_RECOVERY_REQUIRED", completed.stderr)
        self.assertNotIn("Checking for a signed stable JobFlow update", completed.stdout)

    def test_published_v060_manifest_is_not_a_v2_complete_runtime_release(self) -> None:
        manifest = json.loads(
            (
                PROJECT
                / "tests"
                / "fixtures"
                / "published-update-v0.6.0"
                / "JobFlow-update-manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertTrue(manifest["asset_name"].endswith("-source.zip"))
        self.assertEqual(manifest["version"], "0.6.0")

    def test_inventory_digest_is_canonical_and_matches_extracted_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-updater-p1-") as raw:
            root = Path(raw)
            archive = root / "payload.zip"
            prefix = "JobFlow-v1.2.3/"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(prefix, b"")
                bundle.writestr(prefix + "scripts/install-jobflow.ps1", b"installer")
            inventory = inventory_archive_payload(archive, prefix)
            expected = hashlib.sha256(
                canonical_json(
                    {"directories": inventory["directories"], "records": inventory["records"]}
                )
            ).hexdigest()
            self.assertEqual(inventory["inventory_sha256"], expected)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root / "extracted")
            attestation = attest_extracted_payload(
                archive, prefix, root / "extracted" / "JobFlow-v1.2.3"
            )
            self.assertEqual(attestation["inventory_sha256"], expected)
            self.assertEqual(attestation["extracted_root_sha256"], expected)

    def test_bounded_metadata_reader_rejects_multi_link_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-updater-link-") as raw:
            root = Path(raw)
            metadata = root / "channel.json"
            metadata.write_bytes(canonical_json({"schema_version": 1}))
            alias = root / "alias.json"
            try:
                os.link(metadata, alias)
            except OSError as error:
                self.skipTest(f"hard links are unavailable: {error}")
            with self.assertRaises(JobOpsError) as rejected:
                _read_bounded_json(metadata, maximum=4096, code="UPDATE_CHANNEL_INVALID")
            self.assertEqual(rejected.exception.code, "UPDATE_CHANNEL_INVALID")

    def test_updater_parses_in_windows_powershell_51(self) -> None:
        powershell = shutil.which("powershell.exe")
        self.assertIsNotNone(powershell)
        updater = PROJECT / "scripts/windows-runtime/update-installed-jobflow.ps1"
        escaped_updater = str(updater).replace("'", "''")
        command = (
            "$errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{escaped_updater}', [ref]$null, [ref]$errors); "
            "if($errors.Count){$errors | ForEach-Object {$_.Message}; exit 1}"
        )
        completed = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-Command", command],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
