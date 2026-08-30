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
from tests import test_signed_update_presign_v2 as presign_fixtures


WINDOWS_POWERSHELL = shutil.which("powershell.exe")
TEST_ROOT = PROJECT / "tests" / ".tmp"
TEST_ROOT.mkdir(parents=True, exist_ok=True)


@unittest.skipUnless(
    os.name == "nt" and WINDOWS_POWERSHELL,
    "Windows PowerShell and native Windows handles are required",
)
class SignedUpdateStagingHandleTests(unittest.TestCase):
    @staticmethod
    def _builder_text() -> str:
        return (PROJECT / "scripts" / "build-signed-update-bundle.ps1").read_text(
            encoding="utf-8-sig"
        )

    @classmethod
    def _builder_handle_function_block(cls) -> str:
        builder = cls._builder_text()
        start = builder.index("function Assert-NoReparsePath")
        end = builder.index("function Invoke-ProtectedSigningHandoff", start)
        return builder[start:end]

    @classmethod
    def _function_block(cls, name: str) -> str:
        builder = cls._builder_text()
        start = builder.index(f"function {name}")
        end = builder.find("\nfunction ", start + 1)
        if end < 0:
            end = len(builder)
        return builder[start:end]

    @staticmethod
    def _run_powershell(
        script: Path, *arguments: Path | str, timeout: int = 90
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
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
            timeout=timeout,
            check=False,
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls._runtime_directory = tempfile.TemporaryDirectory(
            prefix="jobflow-staging-handle-runtime-", dir=TEST_ROOT
        )
        cls.runtime_version = ".".join(str(part) for part in sys.version_info[:3])
        cls.runtime_tag = f"python{sys.version_info.major}{sys.version_info.minor}"
        cls.runtime_artifact_name = (
            f"python-{cls.runtime_version}-embed-amd64.zip"
        )
        cls.runtime_artifact = (
            Path(cls._runtime_directory.name) / cls.runtime_artifact_name
        )
        fixture_class = presign_fixtures.SignedUpdatePresignV2Tests
        fixture_class.runtime_version = cls.runtime_version
        fixture_class.runtime_tag = cls.runtime_tag
        fixture_class.runtime_artifact_name = cls.runtime_artifact_name
        fixture_class.runtime_artifact = cls.runtime_artifact
        cls.runtime_required_entries = fixture_class._build_embedded_python_fixture(
            cls.runtime_artifact
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._runtime_directory.cleanup()

    def test_native_disposition_layout_and_dos_leaf_rejection(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-native-handle-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            project = root / "project"
            dist = project / "dist"
            dist.mkdir(parents=True)
            harness = root / "native-handle-contract.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + self._builder_handle_function_block()
                + "\nInitialize-JobFlowReleaseFileIdentityApi\n"
                + "$type = [JobFlowReleaseNative.FileIdentityApi].Assembly.GetType('JobFlowReleaseNative.FileDispositionInfo', $true)\n"
                + "$instance = [Activator]::CreateInstance($type, $true)\n"
                + "$size = [Runtime.InteropServices.Marshal]::SizeOf($instance)\n"
                + "if($size -ne 1) { throw 'JOBFLOW_TEST_DISPOSITION_LAYOUT_INVALID' }\n"
                + "$parent = Open-StableReleaseDirectoryHandle $args[1] 'JOBFLOW_TEST_PARENT_INVALID'\n"
                + "$reserved = @('CON', 'prn.txt', 'AUX.json', 'NUL.bin', 'CLOCK$.txt', 'COM1.log', 'lpt9.data')\n"
                + "try {\n"
                + "  foreach($name in $reserved) {\n"
                + "    $handle = [JobFlowReleaseNative.FileIdentityApi]::CreateNewFileRelative($parent.Handle, $name, 1)\n"
                + "    try { if($null -ne $handle -and -not $handle.IsInvalid) { throw 'JOBFLOW_TEST_RESERVED_LEAF_ACCEPTED' } }\n"
                + "    finally { if($null -ne $handle) { $handle.Dispose() } }\n"
                + "    if([IO.File]::Exists((Join-Path $args[1] $name))) { throw 'JOBFLOW_TEST_RESERVED_LEAF_CREATED' }\n"
                + "  }\n"
                + "  $valid = [JobFlowReleaseNative.FileIdentityApi]::CreateNewFileRelative($parent.Handle, 'normal.bin', 1)\n"
                + "  if($null -eq $valid -or $valid.IsInvalid) { throw 'JOBFLOW_TEST_VALID_LEAF_REJECTED' }\n"
                + "  $stream = [IO.FileStream]::new($valid, [IO.FileAccess]::ReadWrite)\n"
                + "  $valid = $null; $stream.WriteByte(1); $stream.Flush($true); $stream.Dispose()\n"
                + "}\n"
                + "finally { if($null -ne $parent.Handle) { $parent.Handle.Dispose() } }\n"
                + "[IO.File]::Delete((Join-Path $args[1] 'normal.bin'))\n"
                + "[Console]::Out.Write(([ordered]@{disposition_size=$size; reserved_rejected=$reserved.Count; valid_created=$true}|ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, project.resolve(), dist.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            evidence = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertEqual(evidence["disposition_size"], 1)
            self.assertEqual(evidence["reserved_rejected"], 7)
            self.assertTrue(evidence["valid_created"])

    def test_real_python_executes_while_all_protected_runtime_handles_are_retained(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-runtime-handles-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            project = root / "project"
            dist = project / "dist"
            dist.mkdir(parents=True)
            artifact = root / self.runtime_artifact_name
            shutil.copy2(self.runtime_artifact, artifact)
            required_json = json.dumps(self.runtime_required_entries, separators=(",", ":"))
            harness = root / "retained-runtime-execution.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + self._builder_handle_function_block()
                + "\n$inputLocks = [Collections.Generic.List[object]]::new()\n"
                + "$stagingLocks = [Collections.Generic.List[object]]::new()\n"
                + "$stagingPaths = [Collections.Generic.List[string]]::new()\n"
                + "$context = $null; $failure = $null; $executionOutput = $null\n"
                + "try {\n"
                + "  Initialize-JobFlowReleaseFileIdentityApi\n"
                + "  $archiveLock = Enter-InputFileLock $args[2] 134217728 'JOBFLOW_TEST_RUNTIME_ARCHIVE_INVALID'\n"
                + "  [void]$inputLocks.Add($archiveLock)\n"
                + f"  $required = @((ConvertFrom-Json -InputObject '{required_json}'))\n"
                + f"  $tag = '{self.runtime_tag}'\n"
                + "  $runtimePolicy = [pscustomobject]@{\n"
                + "    artifact_name = [IO.Path]::GetFileName($args[2]); artifact_bytes = [long]$archiveLock.length; artifact_sha256 = [string]$archiveLock.sha256; python_tag = $tag;\n"
                + "    policy = [pscustomobject]@{ maximum_files=256; maximum_entry_bytes=134217728; maximum_uncompressed_bytes=268435456; maximum_compression_ratio=500; required_entries=$required; active_pth_entries=@(($tag + '.zip'), '.') }\n"
                + "  }\n"
                + "  $context = New-ProtectedInputStagingRoot $args[1]\n"
                + "  [void](New-ProtectedStagingDirectory $context 'python-runtime')\n"
                + "  $runtime = Expand-LockedReleasePythonRuntime $archiveLock $runtimePolicy $context $stagingLocks $stagingPaths\n"
                + "  Assert-ProtectedStagingContext $context 'JOBFLOW_TEST_STAGING_CONTEXT_CHANGED'\n"
                + "  Assert-AllInputFileLocksUnchanged $runtime.locks\n"
                + "  $executionOutput = @(& $runtime.python_path -I -S -E -c \"import _hashlib,json,select,unicodedata;print('JOBFLOW_STAGED_PYTHON_OK')\" 2>&1) -join \"`n\"\n"
                + "  if($LASTEXITCODE -ne 0 -or $executionOutput.Trim() -cne 'JOBFLOW_STAGED_PYTHON_OK') { throw 'JOBFLOW_TEST_STAGED_PYTHON_EXECUTION_FAILED' }\n"
                + "  Assert-ProtectedStagingContext $context 'JOBFLOW_TEST_STAGING_CONTEXT_CHANGED'\n"
                + "  Assert-AllInputFileLocksUnchanged $runtime.locks\n"
                + "}\n"
                + "catch { $failure = [string]$_.Exception.Message }\n"
                + "finally {\n"
                + "  foreach($lock in $stagingLocks) { try { Remove-ProtectedStagedFileLock $lock } catch { if($null -eq $failure){$failure='JOBFLOW_TEST_STAGING_CLEANUP_FAILED'} } }\n"
                + "  if($null -ne $context) { try { Remove-ProtectedInputStagingRoot $context } catch { if($null -eq $failure){$failure='JOBFLOW_TEST_STAGING_CLEANUP_FAILED'} } }\n"
                + "  foreach($lock in $inputLocks) { if($null -ne $lock.stream) { $lock.stream.Dispose() } }\n"
                + "}\n"
                + "if($null -ne $failure) { [Console]::Error.Write($failure); exit 2 }\n"
                + "[Console]::Out.Write($executionOutput.Trim())\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(
                harness, project.resolve(), dist.resolve(), artifact.resolve(), timeout=120
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            self.assertEqual(
                completed.stdout.strip().lstrip("\ufeff"), "JOBFLOW_STAGED_PYTHON_OK"
            )
            self.assertEqual(list(dist.iterdir()), [])

    def test_protected_cleanup_has_no_path_fallback(self) -> None:
        forbidden = (
            "Remove-Item",
            "Remove-TemporaryOutput",
            "[IO.File]::Delete",
            "[IO.Directory]::Delete",
            "[System.IO.File]::Delete",
            "[System.IO.Directory]::Delete",
        )
        for name in (
            "Remove-ProtectedStagedFileLock",
            "Remove-ProtectedStagedDirectoryLock",
            "Remove-ProtectedInputStagingRoot",
        ):
            with self.subTest(function=name):
                block = self._function_block(name)
                for token in forbidden:
                    self.assertNotIn(token, block)
        file_cleanup = self._function_block("Remove-ProtectedStagedFileLock")
        directory_cleanup = self._function_block("Remove-ProtectedStagedDirectoryLock")
        self.assertIn("OpenDeleteFileRelative", file_cleanup)
        self.assertIn("OpenDeleteDirectoryRelative", directory_cleanup)
        self.assertIn("Mark-ReleaseHandleDelete", file_cleanup)
        self.assertIn("Mark-ReleaseHandleDelete", directory_cleanup)
        self.assertNotIn("Assert-InputFileLockUnchanged", file_cleanup)
        creator_cleanup = self._function_block("Remove-NewReleaseFileOutput")
        for token in forbidden:
            self.assertNotIn(token, creator_cleanup)
        self.assertIn("OpenDeleteFileRelative", creator_cleanup)
        self.assertIn("Mark-ReleaseHandleDelete", creator_cleanup)

        builder = self._builder_text()
        self.assertRegex(
            builder,
            r"foreach \(\$lock in \$generatedLocks\) \{\s*try \{ Remove-ProtectedStagedFileLock \$lock \}",
        )
        self.assertRegex(
            builder,
            r"foreach \(\$lock in \$stagingLocks\) \{\s*try \{ Remove-ProtectedStagedFileLock \$lock \}",
        )

    def test_signer_requires_runtime_evidence_application_wheel_provenance_binding(
        self,
    ) -> None:
        function = self._function_block(
            "Assert-RuntimeEvidenceApplicationWheelProvenanceBinding"
        )
        self.assertIn("application_wheel_provenance", function)
        self.assertIn("application_wheel_sha256", function)
        self.assertIn("ConvertTo-CanonicalJsonText", function)
        builder = self._builder_text()
        self.assertRegex(
            builder,
            r"\$closureValue = Read-LockedCanonicalJsonObject\s+`\s*\$closureLock",
        )
        self.assertRegex(
            builder,
            r"Assert-RuntimeEvidenceApplicationWheelProvenanceBinding\s+`\s*\$closureValue \$runtimeEvidenceValue",
        )

        digest_a = "sha256:" + ("a" * 64)
        digest_b = "sha256:" + ("b" * 64)
        harness_text = (
            "$ErrorActionPreference = 'Stop'\n"
            + self._builder_handle_function_block()
            + f"\n$wheelA = '{digest_a}'; $wheelB = '{digest_b}'\n"
            + "$provenance = [pscustomobject]@{ format='JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1'; pass_a_wheel_sha256=$wheelA }\n"
            + "$closure = [pscustomobject]@{ build_inputs=[pscustomobject]@{ application_wheel_sha256=$wheelA; application_wheel_provenance=$provenance } }\n"
            + "$evidence = [pscustomobject]@{ build_inputs=[pscustomobject]@{ application_wheel_sha256=$wheelA; application_wheel_provenance=$provenance } }\n"
            + "Assert-RuntimeEvidenceApplicationWheelProvenanceBinding $closure $evidence 'JOBFLOW_TEST_PROVENANCE_INVALID'\n"
            + "$evidence.build_inputs.application_wheel_sha256 = $wheelB\n"
            + "$rejected = $false\n"
            + "try { Assert-RuntimeEvidenceApplicationWheelProvenanceBinding $closure $evidence 'JOBFLOW_TEST_PROVENANCE_INVALID' } catch { $rejected = ([string]$_.Exception.Message -ceq 'JOBFLOW_TEST_PROVENANCE_INVALID') }\n"
            + "if(-not $rejected) { throw 'JOBFLOW_TEST_PROVENANCE_MISMATCH_ACCEPTED' }\n"
            + "$evidence.build_inputs.application_wheel_sha256 = $wheelA\n"
            + "$evidence.build_inputs.application_wheel_provenance = [pscustomobject]@{ format='JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1'; pass_a_wheel_sha256=$wheelB }\n"
            + "$rejected = $false\n"
            + "try { Assert-RuntimeEvidenceApplicationWheelProvenanceBinding $closure $evidence 'JOBFLOW_TEST_PROVENANCE_INVALID' } catch { $rejected = ([string]$_.Exception.Message -ceq 'JOBFLOW_TEST_PROVENANCE_INVALID') }\n"
            + "if(-not $rejected) { throw 'JOBFLOW_TEST_PROVENANCE_MISMATCH_ACCEPTED' }\n"
            + "[Console]::Out.Write('JOBFLOW_PROVENANCE_BINDING_OK')\n"
        )
        with tempfile.TemporaryDirectory(
            prefix="jobflow-provenance-binding-", dir=TEST_ROOT
        ) as raw:
            harness = Path(raw) / "provenance-binding.ps1"
            harness.write_text(harness_text, encoding="utf-8-sig")
            completed = self._run_powershell(harness)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
        self.assertEqual(
            completed.stdout.strip().lstrip("\ufeff"),
            "JOBFLOW_PROVENANCE_BINDING_OK",
        )

    def test_zero_byte_creator_cleanup_is_parent_relative_and_complete(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-zero-byte-cleanup-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            project = root / "project"
            dist = project / "dist"
            dist.mkdir(parents=True)
            harness = root / "zero-byte-cleanup.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + self._builder_handle_function_block()
                + "\n$context = New-ProtectedInputStagingRoot $args[1]\n"
                + "[void](New-ProtectedStagingDirectory $context 'inputs')\n"
                + "$inputs = $context.Directories['inputs']\n"
                + "$output = Open-NewReleaseFileRelative $inputs 'partial.bin' 1 'JOBFLOW_TEST_CREATE_FAILED'\n"
                + "$path = [string]$output.path\n"
                + "Remove-NewReleaseFileOutput $output\n"
                + "if([IO.File]::Exists($path)) { throw 'JOBFLOW_TEST_PARTIAL_FILE_REMAINED' }\n"
                + "Remove-ProtectedInputStagingRoot $context\n"
                + "if([IO.Directory]::Exists([string]$context.Path)) { throw 'JOBFLOW_TEST_STAGING_ROOT_REMAINED' }\n"
                + "[Console]::Out.Write('JOBFLOW_ZERO_BYTE_CLEANUP_OK')\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, project.resolve(), dist.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            self.assertEqual(
                completed.stdout.strip().lstrip("\ufeff"),
                "JOBFLOW_ZERO_BYTE_CLEANUP_OK",
            )
            self.assertEqual(list(dist.iterdir()), [])

    def test_retained_root_blocks_or_detects_rename_and_reparse_replacement(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-staging-rename-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            project = root / "project"
            dist = project / "dist"
            dist.mkdir(parents=True)
            harness = root / "retained-root-rename.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + self._builder_handle_function_block()
                + "\n$context = New-ProtectedInputStagingRoot $args[1]\n"
                + "$original = [string]$context.Path\n"
                + "$moved = $original + '.moved'\n"
                + "$renameBlocked = $false\n"
                + "try { [IO.Directory]::Move($original, $moved) } catch { $renameBlocked = $true }\n"
                + "if($renameBlocked) {\n"
                + "  Assert-ProtectedStagingContext $context 'JOBFLOW_TEST_STAGING_CONTEXT_CHANGED'\n"
                + "}\n"
                + "else {\n"
                + "  if([IO.Directory]::Exists($original) -or -not [IO.Directory]::Exists($moved)) { throw 'JOBFLOW_TEST_STAGING_RENAME_STATE_INVALID' }\n"
                + "  $target = Join-Path $args[1] 'attacker-target'\n"
                + "  [IO.Directory]::CreateDirectory($target) | Out-Null\n"
                + "  $canary = Join-Path $target 'external-canary.txt'\n"
                + "  [IO.File]::WriteAllText($canary, 'preserve-me', [Text.Encoding]::UTF8)\n"
                + "  $junctionResult = & cmd.exe /d /c \"mklink /J `\"$original`\" `\"$target`\"\" 2>&1\n"
                + "  if($LASTEXITCODE -ne 0) { throw 'JOBFLOW_TEST_JUNCTION_SETUP_FAILED' }\n"
                + "  $reparseDetected = $false\n"
                + "  try { Assert-ProtectedStagingContext $context 'JOBFLOW_TEST_STAGING_CONTEXT_CHANGED' } catch { $reparseDetected = ([string]$_.Exception.Message -ceq 'JOBFLOW_TEST_STAGING_CONTEXT_CHANGED') }\n"
                + "  if(-not $reparseDetected -or -not [IO.File]::Exists($canary)) { throw 'JOBFLOW_TEST_REPARSE_REPLACEMENT_NOT_DETECTED' }\n"
                + "  [IO.Directory]::Delete($original)\n"
                + "  [IO.Directory]::Move($moved, $original)\n"
                + "  Assert-ProtectedStagingContext $context 'JOBFLOW_TEST_STAGING_CONTEXT_CHANGED'\n"
                + "  [IO.File]::Delete($canary); [IO.Directory]::Delete($target)\n"
                + "}\n"
                + "Remove-ProtectedInputStagingRoot $context\n"
                + "[Console]::Out.Write('JOBFLOW_RENAME_REPARSE_BLOCKED')\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, project.resolve(), dist.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            self.assertEqual(
                completed.stdout.strip().lstrip("\ufeff"),
                "JOBFLOW_RENAME_REPARSE_BLOCKED",
            )
            self.assertEqual(list(dist.iterdir()), [])

    def test_hardlink_canary_is_not_deleted_by_staging_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-staging-hardlink-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            project = root / "project"
            dist = project / "dist"
            dist.mkdir(parents=True)
            canary = root / "external-canary.bin"
            harness = root / "hardlink-canary.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + self._builder_handle_function_block()
                + "\n$context = New-ProtectedInputStagingRoot $args[1]\n"
                + "[void](New-ProtectedStagingDirectory $context 'inputs')\n"
                + "$inputs = $context.Directories['inputs']\n"
                + "$output = Open-NewReleaseFileRelative $inputs 'protected.bin' 1 'JOBFLOW_TEST_CREATE_FAILED'\n"
                + "$bytes = [Text.Encoding]::UTF8.GetBytes('protected-staging-bytes')\n"
                + "$output.stream.Write($bytes, 0, $bytes.Length); $output.stream.Flush($true)\n"
                + "$lock = Convert-NewReleaseFileToReadLock $output 4096 'JOBFLOW_TEST_CONVERT_FAILED'\n"
                + "New-Item -ItemType HardLink -Path $args[2] -Target ([string]$lock.path) -ErrorAction Stop | Out-Null\n"
                + "$failure = $null\n"
                + "try { Remove-ProtectedStagedFileLock $lock } catch { $failure = [string]$_.Exception.Message }\n"
                + "if($failure -cne 'JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED') { throw 'JOBFLOW_TEST_HARDLINK_CLEANUP_NOT_BLOCKED' }\n"
                + "if(-not [IO.File]::Exists($args[2]) -or -not [IO.File]::Exists([string]$lock.path)) { throw 'JOBFLOW_TEST_HARDLINK_CANARY_DELETED' }\n"
                + "$canaryBytes = [IO.File]::ReadAllBytes($args[2])\n"
                + "if([Convert]::ToBase64String($bytes) -cne [Convert]::ToBase64String($canaryBytes)) { throw 'JOBFLOW_TEST_HARDLINK_CANARY_CHANGED' }\n"
                + "[IO.File]::Delete([string]$lock.path); [IO.File]::Delete($args[2])\n"
                + "Remove-ProtectedInputStagingRoot $context\n"
                + "[Console]::Out.Write('JOBFLOW_HARDLINK_CANARY_PRESERVED')\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(
                harness, project.resolve(), dist.resolve(), canary.resolve()
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            self.assertEqual(
                completed.stdout.strip().lstrip("\ufeff"),
                "JOBFLOW_HARDLINK_CANARY_PRESERVED",
            )
            self.assertFalse(canary.exists())
            self.assertEqual(list(dist.iterdir()), [])

    def test_unknown_staging_child_is_preserved_and_cleanup_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-unknown-child-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            project = root / "project"
            dist = project / "dist"
            dist.mkdir(parents=True)
            harness = root / "unknown-child-cleanup.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                + "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + self._builder_handle_function_block()
                + "\n$context = New-ProtectedInputStagingRoot $args[1]\n"
                + "$stagingPath = [string]$context.Path\n"
                + "$unknown = Join-Path $stagingPath 'unexpected-third-party-child.txt'\n"
                + "[IO.File]::WriteAllText($unknown, 'do-not-delete-implicitly', [Text.Encoding]::UTF8)\n"
                + "$failure = $null\n"
                + "try { Remove-ProtectedInputStagingRoot $context } catch { $failure = [string]$_.Exception.Message }\n"
                + "$preserved = [IO.File]::Exists($unknown)\n"
                + "$rootPreserved = [IO.Directory]::Exists($stagingPath)\n"
                + "if($failure -cne 'JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED' -or -not $preserved -or -not $rootPreserved) { throw 'JOBFLOW_TEST_UNKNOWN_CHILD_NOT_PRESERVED' }\n"
                + "[IO.File]::Delete($unknown); [IO.Directory]::Delete($stagingPath, $false)\n"
                + "[Console]::Out.Write(([ordered]@{failure=$failure; child_preserved=$preserved; root_preserved=$rootPreserved}|ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run_powershell(harness, project.resolve(), dist.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            evidence = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertEqual(
                evidence["failure"], "JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED"
            )
            self.assertTrue(evidence["child_preserved"])
            self.assertTrue(evidence["root_preserved"])
            self.assertEqual(list(dist.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
