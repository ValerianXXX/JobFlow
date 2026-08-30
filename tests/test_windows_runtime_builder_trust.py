from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import PROJECT


POWERSHELL = shutil.which("powershell.exe")
BUILDER = PROJECT / "scripts" / "build-windows-runtime-closure.ps1"
POLICY = PROJECT / "config" / "release-toolchain.json"
TEST_ROOT = PROJECT / "tests" / ".tmp"
TEST_ROOT.mkdir(parents=True, exist_ok=True)


class WindowsRuntimeBuilderTrustStaticTests(unittest.TestCase):
    @staticmethod
    def _text() -> str:
        return BUILDER.read_text(encoding="utf-8-sig")

    def test_trust_policy_is_retained_and_checked_before_python_executes(self) -> None:
        text = self._text()
        retained = text.index(
            "$script:ReleaseToolchainInput = Enter-RetainedRuntimeInput"
        )
        initialized = text.index("$script:ProtectedBuilder = Initialize-ProtectedBuilderRuntime")
        identity = text.index("$builderIdentity = Assert-BuilderPython $source")
        wheelhouse = text.index("$wheelhouseIdentity = Assert-ExactWheelhouse")
        completed_wheelhouse = text.index(
            "$completedWheelhouseIdentity = Complete-ExactWheelhouseIdentity"
        )
        self.assertLess(retained, wheelhouse)
        self.assertLess(wheelhouse, initialized)
        self.assertLess(initialized, identity)
        self.assertLess(identity, completed_wheelhouse)
        protected = text[
            text.index("function Initialize-ProtectedBuilderRuntime") :
            text.index("function Assert-ProtectedBuilderRuntime")
        ]
        self.assertIn("Expand-SafeZip $script:PythonArtifactInput $root", protected)
        self.assertIn('$script:BuilderPythonInput = $actualFiles["python.exe"]', protected)
        self.assertIn(
            "$script:BuilderPythonTrust = Assert-ProtectedBuilderPythonTrust",
            protected,
        )
        self.assertNotIn("BuilderPythonPath", text)
        self.assertIn(
            "release_toolchain_policy = $script:ReleaseToolchain.document_sha256",
            text,
        )
        self.assertIn(
            "builder_python_sha256 = $script:BuilderPythonTrust.sha256", text
        )
        self.assertIn(
            "protected_builder_tree_sha256 = [string]$script:ProtectedBuilder.tree_sha256",
            text,
        )

    def test_two_commit_archives_and_builds_are_physically_isolated(self) -> None:
        text = self._text()
        snapshots = text[text.index("function New-TrustedSourceSnapshots") : text.index("function Initialize-PinnedBuildTools")]
        self.assertIn('$archiveA = Join-Path $Root "source-a.zip"', snapshots)
        self.assertIn('$archiveB = Join-Path $Root "source-b.zip"', snapshots)
        self.assertEqual(snapshots.count('Invoke-TrustedGit @("archive", "--format=zip"'), 2)
        self.assertIn("$retainedA = Get-RetainedTreeSnapshot $snapshotA", snapshots)
        self.assertIn("$retainedB = Get-RetainedTreeSnapshot $snapshotB", snapshots)

        self.assertIn('Initialize-PinnedBuildTools (Join-Path $script:SourceBuildRoot "pass-a-tools")', text)
        self.assertIn('Initialize-PinnedBuildTools (Join-Path $script:SourceBuildRoot "pass-b-tools")', text)
        self.assertIn('(Join-Path $script:SourceBuildRoot "pass-a-wheel-tmp")', text)
        self.assertIn('(Join-Path $script:SourceBuildRoot "pass-b-wheel-tmp")', text)
        self.assertIn('$sourceWorkA = Join-Path $script:SourceBuildRoot "pass-a-source-work"', text)
        self.assertIn('$sourceWorkB = Join-Path $script:SourceBuildRoot "pass-b-source-work"', text)
        self.assertIn("Copy-SafeTree $script:SourceSnapshots.retained_a $sourceWorkA", text)
        self.assertIn("Copy-SafeTree $script:SourceSnapshots.retained_b $sourceWorkB", text)
        self.assertIn("$sourceWorkTreeA = Get-SourceSnapshotIdentity $sourceWorkA", text)
        self.assertIn("$sourceWorkTreeB = Get-SourceSnapshotIdentity $sourceWorkB", text)
        self.assertIn("Build-ApplicationWheel `\n    $sourceWorkA `", text)
        self.assertIn("Build-ApplicationWheel `\n    $sourceWorkB `", text)
        self.assertIn("$script:SourceSnapshots.snapshot_a", text)
        self.assertIn("$script:SourceSnapshots.snapshot_b", text)
        self.assertIn("[string]$applicationA.sha256 -cne [string]$applicationB.sha256", text)

    def test_final_archive_uses_retained_tree_and_is_independently_verified(self) -> None:
        text = self._text()
        new_build = text[text.index("function New-OneBuild") : text.index("function Remove-SafeBuildRoot")]
        retained = new_build.index("$closureSnapshot = Get-RetainedTreeSnapshot $closure")
        reverified = new_build.index("Invoke-IndependentVerifier $closure $false $false", retained)
        archived = new_build.index("New-DeterministicZip $closureSnapshot", reverified)
        self.assertLess(retained, reverified)
        self.assertLess(reverified, archived)

        zip_writer = text[text.index("function New-DeterministicZip") : text.index("function New-OneBuild")]
        self.assertIn("Assert-RetainedRuntimeInputIdentity $file.input -VerifyHash", zip_writer)
        self.assertIn("$file.input.stream.CopyTo($target)", zip_writer)
        committed = text.index("$outputInput = Enter-RetainedRuntimeInput $outputPath")
        final_verify = text.index("Invoke-IndependentArchiveVerifier $outputInput $false", committed)
        self.assertLess(committed, final_verify)

    def test_independent_verifier_failure_diagnostics_emit_only_fixed_codes(self) -> None:
        text = self._text()
        helper = text[
            text.index("function Write-SafeIndependentVerifierFailure") :
            text.index("function Invoke-IndependentVerifier")
        ]
        verifier = text[
            text.index("function Invoke-IndependentVerifier") :
            text.index("function Invoke-IndependentArchiveVerifier")
        ]
        archive = text[
            text.index("function Invoke-IndependentArchiveVerifier") :
            text.index("function Invoke-OfflineSmoke")
        ]
        self.assertIn("'JOBFLOW_RUNTIME_[A-Z0-9_]+'", helper)
        self.assertIn('$Prefix + "=" + $code', helper)
        self.assertNotIn("WriteLine($Text)", helper)
        self.assertIn(
            'Write-SafeIndependentVerifierFailure "JOBFLOW_RUNTIME_STRUCTURAL_VERIFY_DETAIL" $errorText',
            verifier,
        )
        self.assertIn(
            'Write-SafeIndependentVerifierFailure "JOBFLOW_RUNTIME_ARCHIVE_VERIFY_DETAIL" $errorText',
            archive,
        )

    def test_pending_smoke_and_final_verification_are_distinct_fail_closed_stages(self) -> None:
        text = self._text()
        verifier = text[
            text.index("function Invoke-IndependentVerifier") :
            text.index("function Invoke-IndependentArchiveVerifier")
        ]
        new_build = text[
            text.index("function New-OneBuild") :
            text.index("function Remove-SafeBuildRoot")
        ]
        self.assertIn('$arguments += "-AllowPendingSmoke"', verifier)
        self.assertIn('"RUNTIME_CLOSURE_STRUCTURE_VERIFIED"', verifier)
        self.assertIn('"RUNTIME_CLOSURE_VERIFIED"', verifier)
        self.assertIn("Invoke-IndependentVerifier $closure $false $true", new_build)
        self.assertIn("Invoke-IndependentVerifier $closure $false $false", new_build)
        self.assertLess(
            new_build.index("Invoke-IndependentVerifier $closure $false $true"),
            new_build.index("Invoke-OfflineSmoke $closure"),
        )
        self.assertGreater(
            new_build.index("Invoke-IndependentVerifier $closure $false $false"),
            new_build.index("Invoke-OfflineSmoke $closure"),
        )

    def test_deterministic_builder_uses_ordinal_sorting_only(self) -> None:
        text = self._text()
        self.assertNotIn("Sort-Object", text)
        self.assertIn("function Get-OrdinalSortedObjects", text)
        self.assertIn("[Array]::Sort($keys, $comparer)", text)
        self.assertIn("[StringComparer]::OrdinalIgnoreCase", text)

    def test_powershell_51_stdin_transport_accepts_only_one_known_bom(self) -> None:
        text = self._text()
        self.assertIn(
            'if payload.startswith(b"\\xef\\xbb\\xbf"): payload=payload[3:]',
            text,
        )
        self.assertIn("base64.b64decode(payload,validate=True)", text)
        self.assertIn(
            'if ($process.ExitCode -ne 0) { throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_FAILED" }',
            text,
        )
        self.assertNotIn("$diagnostic", text)
        for stage in (
            "JOBFLOW_RUNTIME_CANONICAL_JSON_PROCESS_FAILED",
            "JOBFLOW_RUNTIME_BUILDER_PYTHON_IDENTITY_PROBE_FAILED",
            "JOBFLOW_RUNTIME_BUILDER_PIP_PROBE_FAILED",
        ):
            self.assertIn(stage, text)

    def test_git_tree_nul_records_use_the_unambiguous_separator_array_overload(self) -> None:
        text = self._text()
        self.assertIn(
            ".Split([char[]]@([char]0), [StringSplitOptions]::RemoveEmptyEntries)",
            text,
        )
        self.assertNotIn(
            ".Split([char]0, [StringSplitOptions]::RemoveEmptyEntries)",
            text,
        )

    def test_exported_archive_identity_is_distinct_from_canonical_git_blob_identity(self) -> None:
        text = self._text()
        self.assertIn("archive_blob_oid=Get-RetainedGitBlobOid $retained", text)
        self.assertIn("git_blob_oid=[string]$entry.oid", text)
        self.assertIn("archive_blob_oid=[string]$identity.archive_blob_oid", text)
        self.assertNotIn("identity.git_blob_oid -cne", text)


@unittest.skipUnless(
    os.name == "nt" and POWERSHELL,
    "Windows PowerShell and Authenticode are required",
)
class WindowsRuntimeBuilderTrustRuntimeTests(unittest.TestCase):
    @classmethod
    def _prelude(cls) -> str:
        text = BUILDER.read_text(encoding="utf-8-sig")
        start = text.index("function Initialize-AuthenticodeApi")
        end = text.index("$script:RetainedRuntimeInputs =", start)
        return text[start:end]

    @classmethod
    def _ordinal_sort_helper(cls) -> str:
        text = BUILDER.read_text(encoding="utf-8-sig")
        start = text.index("function Get-OrdinalSortedObjects")
        end = text.index("$script:ReservedWindowsNames", start)
        return text[start:end]

    @staticmethod
    def _run(script: Path, *arguments: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                *(str(argument) for argument in arguments),
            ],
            cwd=script.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )

    def test_signed_python_is_accepted_and_untrusted_policy_fails_closed(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(
            prefix="jobflow-builder-trust-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            trusted = root / "trusted.json"
            trusted.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            policy["tools"]["python"]["allowed_signers"] = []
            policy["tools"]["python"]["allowed_unsigned_sha256"] = [
                "sha256:" + "0" * 64
            ]
            untrusted = root / "untrusted.json"
            untrusted.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            harness = root / "builder-trust.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "Add-Type -AssemblyName System.IO.Compression\n"
                "Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
                + self._prelude()
                + "\n$script:RetainedRuntimeInputs = New-Object System.Collections.Generic.List[object]\n"
                + "$script:RetainedRuntimeInputsByPath = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)\n"
                + "$script:Project = [IO.Path]::GetFullPath($args[3])\n"
                + "$failure = $null; $identity = $null\n"
                + "try {\n"
                + "  $script:BuilderPythonInput = Enter-RetainedRuntimeInput $args[0]\n"
                + "  $script:BuilderPython = [string]$script:BuilderPythonInput.path\n"
                + "  $trustedInput = Enter-RetainedRuntimeInput $args[1]\n"
                + "  $trustedPolicy = Read-BuilderPythonTrustPolicy $trustedInput\n"
                + "  $identity = Assert-BuilderPythonTrust $trustedPolicy\n"
                + "  $badInput = Enter-RetainedRuntimeInput $args[2]\n"
                + "  $badPolicy = Read-BuilderPythonTrustPolicy $badInput\n"
                + "  try { [void](Assert-BuilderPythonTrust $badPolicy) } catch { $failure = [string]$_.Exception.Message }\n"
                + "}\n"
                + "finally { Close-RetainedRuntimeInputs }\n"
                + "[Console]::Out.Write(([ordered]@{trust=$identity.trust;sha256=$identity.sha256;failure=$failure}|ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run(
                harness,
                Path(sys.executable).resolve(),
                trusted.resolve(),
                untrusted.resolve(),
                root.resolve(),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertIn(result["trust"], {"AUTHENTICODE", "PINNED_SHA256"})
            self.assertRegex(result["sha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(
                result["failure"], "JOBFLOW_RUNTIME_BUILDER_PYTHON_UNTRUSTED"
            )

    def test_direct_powershell_51_entry_resolves_default_project_root(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-builder-default-root-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            missing = root / "missing-input"
            completed = self._run(
                BUILDER,
                missing,
                missing,
                root,
                missing,
                Path("a" * 40),
                root,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn(
                "Cannot bind argument to parameter 'Path' because it is null",
                completed.stderr,
            )
            self.assertIn("missing-input", completed.stderr)

    def test_ordinal_sort_is_locale_independent_in_windows_powershell_51(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-builder-ordinal-sort-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            harness = root / "ordinal-sort.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + self._ordinal_sort_helper()
                + "\n$values = @('b','A','a','_','-')\n"
                + "$records = @([pscustomobject]@{path='z'},[pscustomobject]@{path='B'},[pscustomobject]@{path='a'})\n"
                + "$failure = $null\n"
                + "try { [void](Get-OrdinalSortedObjects @('A','a') '' -IgnoreCase) } catch { $failure = [string]$_.Exception.Message }\n"
                + "$result = [ordered]@{values=@(Get-OrdinalSortedObjects $values);paths=@(Get-OrdinalSortedObjects $records 'path'|ForEach-Object{$_.path});collision=$failure}\n"
                + "[Console]::Out.Write(($result|ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run(harness)
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertEqual(result["values"], ["-", "A", "_", "a", "b"])
            self.assertEqual(result["paths"], ["B", "a", "z"])
            self.assertEqual(
                result["collision"], "JOBFLOW_RUNTIME_ORDINAL_SORT_KEY_INVALID"
            )

    def test_powershell_51_drops_the_terminal_git_tree_nul_record(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-builder-tree-split-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            harness = root / "tree-split.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "$raw = 'first' + [char]0 + 'second' + [char]0\n"
                "$records = @($raw.Split([char[]]@([char]0), [StringSplitOptions]::RemoveEmptyEntries))\n"
                "[Console]::Out.Write(($records | ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run(harness)
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            self.assertEqual(
                json.loads(completed.stdout.strip().lstrip("\ufeff")),
                ["first", "second"],
            )


if __name__ == "__main__":
    unittest.main()
