from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jobops.util import canonical_json


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-jobflow.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _inventory_sha256(directories: list[str], records: list[dict[str, object]]) -> str:
    material = canonical_json({"directories": directories, "records": records})
    return hashlib.sha256(material).hexdigest()


def _refresh_inventory_digest(payload: dict[str, object]) -> None:
    directories = payload["directories"]
    records = payload["records"]
    assert isinstance(directories, list)
    assert isinstance(records, list)
    digest = _inventory_sha256(directories, records)
    payload["directory_count"] = len(directories)
    payload["file_count"] = len(records)
    payload["inventory_sha256"] = digest
    payload["extracted_root_sha256"] = digest


def valid_attestation() -> dict[str, object]:
    version = "0.5.0"
    commit = "a" * 40
    archive_sha = "b" * 64
    directories = ["scripts"]
    records: list[dict[str, object]] = [
        {"length": 12, "relative": "scripts/example.ps1", "sha256": "f" * 64}
    ]
    inventory_sha = _inventory_sha256(directories, records)
    return {
        "schema_version": 2,
        "status": "UPDATE_EXTRACTED_PAYLOAD_ATTESTED",
        "transaction_nonce": "d" * 64,
        "release": {
            "version": version,
            "commit": commit,
            "archive_name": f"JobFlow-v{version}-{commit[:12]}-source.zip",
            "archive_size": 4096,
            "archive_sha256": archive_sha,
            "archive_prefix": f"JobFlow-v{version}/",
        },
        "expected_current": {
            "version_directory": "v0.4.1-123456789abc",
            "version": "0.4.1",
            "source_sha256": "e" * 64,
        },
        "archive_sha256": archive_sha,
        "archive_prefix": f"JobFlow-v{version}/",
        "directory_count": 1,
        "file_count": 1,
        "directories": directories,
        "records": records,
        "inventory_sha256": inventory_sha,
        "extracted_root_sha256": inventory_sha,
    }


class InstallerUpdateAttestationTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        if not POWERSHELL.exists():
            raise unittest.SkipTest("Windows PowerShell is required for installer contract tests")
        cls.source = INSTALLER.read_text(encoding="utf-8-sig")

    def _run_contract(
        self,
        payload: dict[str, object],
        *,
        current: dict[str, object] | None = None,
        project_version: str = "0.5.0",
    ) -> subprocess.CompletedProcess[str]:
        function_names = [
            "Test-JsonString",
            "Test-JsonIntegerInRange",
            "Test-ExactJsonProperties",
            "ConvertTo-StrictJobFlowVersionTuple",
            "Test-JobFlowVersionStrictlyGreater",
            "ConvertTo-JobFlowCanonicalJsonString",
            "Get-TrustedPayloadInventorySha256",
            "Assert-TrustedPayloadRelative",
            "Assert-TrustedUpdatePayloadContextContract",
            "Assert-TrustedUpdateHandoffContext",
        ]
        harness = r'''
$ErrorActionPreference = "Stop"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:JOBFLOW_INSTALLER, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw "INSTALLER_PARSE_FAILED" }
$wanted = @($env:JOBFLOW_FUNCTIONS -split '\|')
$functions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $wanted -contains $node.Name
}, $true))
foreach ($name in $wanted) {
    $matches = @($functions | Where-Object { $_.Name -ceq $name })
    if ($matches.Count -ne 1) { throw "FUNCTION_NOT_FOUND:$name" }
    Invoke-Expression $matches[0].Extent.Text
}
$payload = [IO.File]::ReadAllText($env:JOBFLOW_PAYLOAD_JSON) | ConvertFrom-Json
try {
    Assert-TrustedUpdatePayloadContextContract $payload
    if (-not [string]::IsNullOrWhiteSpace($env:JOBFLOW_CURRENT_JSON)) {
        $current = [IO.File]::ReadAllText($env:JOBFLOW_CURRENT_JSON) | ConvertFrom-Json
        Assert-TrustedUpdateHandoffContext $payload $current $env:JOBFLOW_PROJECT_VERSION
    }
    Write-Output "PASS"
    exit 0
}
catch {
    Write-Output ([string]$_.Exception.Message)
    exit 17
}
'''
        with tempfile.TemporaryDirectory(prefix="jobflow-installer-contract-") as raw:
            temp = Path(raw)
            harness_path = temp / "harness.ps1"
            payload_path = temp / "payload.json"
            current_path = temp / "current.json"
            harness_path.write_text(harness, encoding="utf-8")
            payload_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "JOBFLOW_INSTALLER": str(INSTALLER),
                    "JOBFLOW_FUNCTIONS": "|".join(function_names),
                    "JOBFLOW_PAYLOAD_JSON": str(payload_path),
                    "JOBFLOW_PROJECT_VERSION": project_version,
                    "JOBFLOW_CURRENT_JSON": "",
                }
            )
            if current is not None:
                current_path.write_text(json.dumps(current, separators=(",", ":")), encoding="utf-8")
                env["JOBFLOW_CURRENT_JSON"] = str(current_path)
            return subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness_path),
                ],
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
                check=False,
            )

    def assert_contract_rejected(self, payload: dict[str, object], code: str) -> None:
        result = self._run_contract(payload)
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertIn(code, result.stdout + result.stderr)

    def test_v2_contract_and_current_identity_are_accepted(self) -> None:
        payload = valid_attestation()
        current = {
            "schema_version": 1,
            **copy.deepcopy(payload["expected_current"]),
        }
        result = self._run_contract(payload, current=current)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_every_required_root_field_is_fail_closed(self) -> None:
        for field in valid_attestation():
            with self.subTest(field=field):
                payload = valid_attestation()
                del payload[field]
                self.assert_contract_rejected(payload, "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID")

    def test_mismatched_release_and_inventory_context_is_rejected(self) -> None:
        cases: list[tuple[str, object]] = [
            ("archive_sha256", "0" * 64),
            ("archive_prefix", "JobFlow-v9.9.9/"),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                payload = valid_attestation()
                payload[field] = value
                self.assert_contract_rejected(payload, "JOBFLOW_TRUSTED_UPDATE")

    def test_equal_forged_inventory_digests_are_rejected(self) -> None:
        payload = valid_attestation()
        payload["inventory_sha256"] = "1" * 64
        payload["extracted_root_sha256"] = "1" * 64
        self.assert_contract_rejected(payload, "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID")

    def test_directory_and_record_tampering_are_digest_bound(self) -> None:
        mutations = (
            ("directory", lambda payload: payload["directories"].__setitem__(0, "source")),
            ("record-relative", lambda payload: payload["records"][0].__setitem__("relative", "scripts/changed.ps1")),
            ("record-length", lambda payload: payload["records"][0].__setitem__("length", 13)),
        )
        for name, mutate in mutations:
            with self.subTest(mutation=name):
                payload = valid_attestation()
                mutate(payload)
                self.assert_contract_rejected(payload, "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID")

    def test_non_ascii_directory_and_record_paths_are_rejected(self) -> None:
        cases = (
            ("directory", lambda payload: payload["directories"].__setitem__(0, "scrípts")),
            ("record", lambda payload: payload["records"][0].__setitem__("relative", "scripts/café.ps1")),
        )
        for name, mutate in cases:
            with self.subTest(path_kind=name):
                payload = valid_attestation()
                mutate(payload)
                _refresh_inventory_digest(payload)
                self.assert_contract_rejected(payload, "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID")

    def test_windows_illegal_directory_and_record_characters_are_rejected(self) -> None:
        for character in '<>"|?*':
            for path_kind in ("directory", "record"):
                with self.subTest(character=character, path_kind=path_kind):
                    payload = valid_attestation()
                    if path_kind == "directory":
                        payload["directories"] = [f"bad{character}directory"]
                    else:
                        records = payload["records"]
                        assert isinstance(records, list)
                        record = records[0]
                        assert isinstance(record, dict)
                        record["relative"] = f"scripts/bad{character}name.ps1"
                    _refresh_inventory_digest(payload)
                    self.assert_contract_rejected(
                        payload,
                        "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID",
                    )

    def test_printable_ascii_json_escapes_match_python_canonical_digest(self) -> None:
        payload = valid_attestation()
        payload["directories"] = ["docs/R&D"]
        payload["records"] = [
            {"length": 12, "relative": "docs/R&D/O'Reilly.txt", "sha256": "f" * 64}
        ]
        _refresh_inventory_digest(payload)
        result = self._run_contract(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_equal_or_lower_release_is_rejected_as_replay_or_downgrade(self) -> None:
        for version in ("0.4.1", "0.4.0"):
            with self.subTest(version=version):
                payload = valid_attestation()
                release = payload["release"]
                assert isinstance(release, dict)
                commit = str(release["commit"])
                release["version"] = version
                release["archive_name"] = f"JobFlow-v{version}-{commit[:12]}-source.zip"
                release["archive_prefix"] = f"JobFlow-v{version}/"
                payload["archive_prefix"] = f"JobFlow-v{version}/"
                self.assert_contract_rejected(payload, "JOBFLOW_TRUSTED_UPDATE_CONTEXT_MISMATCH")

    def test_stale_current_pointer_identity_is_rejected(self) -> None:
        payload = valid_attestation()
        current = {
            "schema_version": 1,
            **copy.deepcopy(payload["expected_current"]),
        }
        current["source_sha256"] = "9" * 64
        result = self._run_contract(payload, current=current)
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertIn("JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_MISMATCH", result.stdout + result.stderr)

    def test_copy_path_is_create_new_streamed_and_reparse_fail_closed(self) -> None:
        copy_start = self.source.index("function Copy-VerifiedSourceSnapshot")
        copy_end = self.source.index("function Test-JsonIntegerOne", copy_start)
        copy_body = self.source[copy_start:copy_end]
        parent_start = self.source.index("function Initialize-SafeInstallerParentDirectory")
        parent_body = self.source[parent_start:copy_start]
        self.assertIn("[IO.FileMode]::CreateNew", copy_body)
        self.assertIn("[IO.FileShare]::None", copy_body)
        self.assertIn("$sourceStream.CopyTo($output)", copy_body)
        self.assertIn("$output.Flush($true)", copy_body)
        self.assertIn("Get-OpenInstallerFileLinkCount $output", copy_body)
        self.assertIn("Assert-NoInstallerAlternateDataStreams", copy_body)
        self.assertNotIn("Copy-Item", copy_body)
        self.assertIn("[IO.FileAttributes]::ReparsePoint", parent_body)
        self.assertIn("if (Test-Path -LiteralPath $absoluteDestination) { throw $Code }", parent_body)

    def test_context_gate_precedes_install_mutation_and_amd64_is_explicit(self) -> None:
        self.assertIn("[Runtime.InteropServices.Architecture]::X64", self.source)
        self.assertIn("JOBFLOW_WINDOWS_AMD64_REQUIRED", self.source)
        gate = self.source.index("Assert-TrustedUpdateHandoffContext $trustedUpdateContext $existingPointer $version")
        first_install_directory_mutation = self.source.index("New-Item -ItemType Directory -Path $localRoot -Force")
        self.assertLess(gate, first_install_directory_mutation)

    def test_trusted_executables_are_locked_and_identity_bound_before_signature_check(self) -> None:
        powershell_start = self.source.index("function Get-TrustedWindowsPowerShell")
        powershell_end = self.source.index("function Get-CanonicalPythonCandidates", powershell_start)
        powershell_body = self.source[powershell_start:powershell_end]
        self.assertLess(
            powershell_body.index("$lock = [IO.File]::Open"),
            powershell_body.index("Test-TrustedExecutableSignature"),
        )
        python_start = self.source.index("function Find-SupportedPython")
        python_end = self.source.index("function ConvertTo-WindowsProcessArgument", python_start)
        python_body = self.source[python_start:python_end]
        self.assertLess(
            python_body.index("$lock = [IO.File]::Open"),
            python_body.index("Test-TrustedExecutableSignature"),
        )
        icacls_open = self.source.index("$icaclsExecutableLock = [IO.File]::Open")
        icacls_signature = self.source.index(
            'Test-TrustedExecutableSignature $trustedIcaclsPath "Microsoft Corporation"'
        )
        self.assertLess(icacls_open, icacls_signature)
        self.assertGreaterEqual(self.source.count("Get-OpenInstallerFinalPath"), 4)
        self.assertIn("GetFinalPathNameByHandle", self.source)

    def test_python_child_clears_parent_secrets_and_round_trips_ps51_arguments(self) -> None:
        wanted = ("ConvertTo-WindowsProcessArgument", "Invoke-IsolatedInstallerPython")
        code = (
            "import json,os,sys;from pathlib import Path;"
            "Path(sys.argv[1]).write_text(json.dumps({'args':sys.argv[2:],'secret':os.environ.get('JOBFLOW_TEST_SECRET')}),encoding='utf-8')"
        )
        expected_arguments = ["space value", 'quote"value', "trailing\\", ""]
        harness = r'''
$ErrorActionPreference = "Stop"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:JOBFLOW_INSTALLER, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw "INSTALLER_PARSE_FAILED" }
$wanted = @($env:JOBFLOW_FUNCTIONS -split '\|')
$functions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $wanted -contains $node.Name
}, $true))
foreach ($name in $wanted) {
    $matches = @($functions | Where-Object { $_.Name -ceq $name })
    if ($matches.Count -ne 1) { throw "FUNCTION_NOT_FOUND:$name" }
    Invoke-Expression $matches[0].Extent.Text
}
$decodedArguments = [IO.File]::ReadAllText($env:JOBFLOW_ARGUMENTS_JSON) | ConvertFrom-Json
$arguments = @($decodedArguments | ForEach-Object { [string]$_ })
Invoke-IsolatedInstallerPython $env:JOBFLOW_PYTHON $arguments "JOBFLOW_TEST_CHILD_FAILED"
'''
        with tempfile.TemporaryDirectory(prefix="jobflow-installer-child-") as raw:
            temp = Path(raw)
            harness_path = temp / "harness.ps1"
            arguments_path = temp / "arguments.json"
            result_path = temp / "child-result.json"
            child_arguments = [
                "-I", "-P", "-B", "-X", "utf8", "-c", code, str(result_path), *expected_arguments
            ]
            harness_path.write_text(harness, encoding="utf-8")
            arguments_path.write_text(json.dumps(child_arguments), encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "JOBFLOW_INSTALLER": str(INSTALLER),
                    "JOBFLOW_FUNCTIONS": "|".join(wanted),
                    "JOBFLOW_ARGUMENTS_JSON": str(arguments_path),
                    "JOBFLOW_PYTHON": str(Path(sys.executable).resolve()),
                    "JOBFLOW_TEST_SECRET": "must-not-leak",
                }
            )
            completed = subprocess.run(
                [
                    str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(harness_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["args"], expected_arguments)
        self.assertIsNone(result["secret"])
        self.assertIn("$start.EnvironmentVariables.Clear()", self.source)
        self.assertNotIn("& $absolutePython @Arguments", self.source)
        self.assertNotIn("Write-Host $stdout", self.source)
        self.assertNotIn("Write-Host $stderr", self.source)


if __name__ == "__main__":
    unittest.main()
