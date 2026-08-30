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


WINDOWS_POWERSHELL = shutil.which("powershell.exe")
BUILDER = PROJECT / "scripts" / "build-windows-runtime-closure.ps1"
TEST_ROOT = PROJECT / "tests" / ".tmp"
TEST_ROOT.mkdir(parents=True, exist_ok=True)


class WindowsRuntimeClosureInputHandleStaticTests(unittest.TestCase):
    @staticmethod
    def _text() -> str:
        return BUILDER.read_text(encoding="utf-8-sig")

    @classmethod
    def _function(cls, name: str) -> str:
        text = cls._text()
        start = text.index(f"function {name}")
        end = text.find("\nfunction ", start + 1)
        return text[start:] if end < 0 else text[start:end]

    def test_all_immutable_inputs_are_retained_and_stream_consumed(self) -> None:
        text = self._text()
        enter = self._function("Enter-RetainedRuntimeInput")
        identity = self._function("Assert-RetainedRuntimeInputIdentity")
        read_json = self._function("Read-JsonObject")
        expand_zip = self._function("Expand-SafeZip")
        install = self._function("Install-OfflineApplication")

        self.assertIn("[IO.FileShare]::Read", enter)
        self.assertNotIn("FileShare]::ReadWrite", enter)
        self.assertIn("[uint32]$identity.Links -ne 1", enter)
        self.assertIn("Get-RetainedRuntimeInputHash $stream", enter)
        self.assertIn("Get-HandleBoundRuntimePath", enter)
        self.assertIn("$RetainedInput.file_index", identity)
        self.assertIn("$RetainedInput.volume", identity)

        self.assertIn("Read-RetainedRuntimeInputBytes $RetainedInput", read_json)
        self.assertNotIn("ReadAllBytes", read_json)
        self.assertIn("return ,$bytes", self._function("Read-RetainedRuntimeInputBytes"))
        self.assertIn("$stream = $ArchiveInput.stream", expand_zip)
        self.assertNotIn("[IO.File]::Open($Archive", expand_zip)
        self.assertIn("Copy-RetainedRuntimeInput $retainedInput", install)
        self.assertIn("Copy-RetainedRuntimeInput $Application.input", install)
        self.assertNotIn("[IO.File]::Copy", install)

        for assignment in (
            "$script:PythonArtifactInput = Enter-RetainedRuntimeInput",
            "$script:SigstoreBundleInput = Enter-RetainedRuntimeInput",
            "$script:GitInput = Enter-RetainedRuntimeInput",
            "$script:VerifierInput = Enter-RetainedRuntimeInput",
            "$script:BuildScriptInput = Enter-RetainedRuntimeInput",
            "$script:SourcePolicyInput = Enter-RetainedRuntimeInput",
            "$script:RuntimeLockInput = Enter-RetainedRuntimeInput",
            "$script:BuildLockInput = Enter-RetainedRuntimeInput",
            "$script:ClosureVerifierInput = Enter-RetainedRuntimeInput",
        ):
            self.assertIn(assignment, text)

        self.assertIn('$script:BuilderPythonInput = $actualFiles["python.exe"]', text)
        self.assertNotIn("BuilderPythonPath", text)

        self.assertIn("Get-RetainedTreeSnapshot", text)
        self.assertIn("$script:WheelhouseInputs = $wheelhouseIdentity.inputs", text)
        cleanup = text[text.rindex("finally {") :]
        self.assertLess(
            cleanup.index("Close-ProtectedRuntimeDirectoryLocks"),
            cleanup.index("Close-RetainedRuntimeInputs"),
        )
        self.assertLess(
            cleanup.index("Close-RetainedRuntimeInputs"),
            cleanup.index("Remove-SafeBuildRoot ([string]$script:RuntimeBuildRoot)"),
        )
        self.assertLess(
            cleanup.index("Close-RetainedRuntimeInputs"),
            cleanup.index("Remove-SafeBuildRoot ([string]$script:SourceBuildRoot)"),
        )

    def test_external_consumers_revalidate_retained_executable_handles(self) -> None:
        builder_python = self._function("Invoke-BuilderPython")
        protected_builder = self._function("Assert-ProtectedBuilderRuntime")
        verifier = self._function("Invoke-IndependentVerifier")
        self.assertEqual(builder_python.count("Assert-ProtectedBuilderRuntime"), 2)
        self.assertIn("$script:BuilderPythonInput", protected_builder)
        self.assertIn("Assert-RetainedRuntimeInputIdentity", protected_builder)
        self.assertIn("-VerifyHash", protected_builder)
        self.assertGreaterEqual(verifier.count("Assert-RetainedRuntimeInputIdentity"), 2)
        self.assertGreaterEqual(verifier.count("$script:ClosureVerifierInput"), 2)
        self.assertIn("-VerifyHash", verifier)

    def test_dos_reserved_input_leaf_is_rejected_before_path_resolution(self) -> None:
        ordinary = self._function("Assert-OrdinaryInput")
        self.assertLess(
            ordinary.index("$script:ReservedWindowsNames.ContainsKey"),
            ordinary.index("Get-Item -LiteralPath"),
        )


@unittest.skipUnless(
    os.name == "nt" and WINDOWS_POWERSHELL,
    "Windows PowerShell and native Windows handles are required",
)
class WindowsRuntimeClosureInputHandleRuntimeTests(unittest.TestCase):
    @classmethod
    def _function_prelude(cls) -> str:
        text = BUILDER.read_text(encoding="utf-8-sig")
        start = text.index("function Get-Sha256")
        end = text.index("$script:RetainedRuntimeInputs =", start)
        return text[start:end]

    @staticmethod
    def _run(script: Path, *arguments: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_retained_read_lock_blocks_write_and_rename_but_real_python_executes(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-runtime-input-lock-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            protected = root / "immutable-input.bin"
            protected.write_bytes(b"immutable-runtime-input")
            json_input = root / "immutable-input.json"
            json_input.write_bytes(b'{"retained":true}')
            harness = root / "retained-input-runtime.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "Add-Type -AssemblyName System.IO.Compression\n"
                "Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
                + self._function_prelude()
                + "\n$script:RetainedRuntimeInputs = New-Object System.Collections.Generic.List[object]\n"
                + "$script:RetainedRuntimeInputsByPath = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)\n"
                + "function Assert-ProtectedBuilderRuntime { Assert-RetainedRuntimeInputIdentity $script:BuilderPythonInput -VerifyHash }\n"
                + "$script:Project = [IO.Path]::GetFullPath($args[2])\n"
                + "$script:SourceBuildRoot = $script:Project\n"
                + "$writeBlocked = $false; $renameBlocked = $false; $result = $null\n"
                + "try {\n"
                + "  $input = Enter-RetainedRuntimeInput $args[0]\n"
                + "  $script:BuilderPythonInput = Enter-RetainedRuntimeInput $args[1]\n"
                + "  $jsonInput = Enter-RetainedRuntimeInput $args[3]\n"
                + "  if($null -eq $input -or $null -eq $input.stream) { throw ('JOBFLOW_TEST_INPUT_OBJECT_INVALID|' + $input.GetType().FullName) }\n"
                + "  if($null -eq $script:BuilderPythonInput -or $null -eq $script:BuilderPythonInput.stream) { throw ('JOBFLOW_TEST_BUILDER_OBJECT_INVALID|' + $script:BuilderPythonInput.GetType().FullName) }\n"
                + "  $script:BuilderPython = [string]$script:BuilderPythonInput.path\n"
                + "  try { $other = [IO.File]::Open($args[0], [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite); $other.Dispose() } catch { $writeBlocked = $true }\n"
                + "  try { [IO.File]::Move($args[0], ($args[0] + '.moved')) } catch { $renameBlocked = $true }\n"
                + "  Assert-RetainedRuntimeInputIdentity $input -VerifyHash\n"
                + "  $document = Read-JsonObject $jsonInput\n"
                + "  if(-not [bool]$document.value.retained -or $document.bytes.GetType().FullName -cne 'System.Byte[]') { throw 'JOBFLOW_TEST_RETAINED_JSON_FAILED' }\n"
                + "  $probe = Invoke-BuilderPython @('-I','-c',\"print('JOBFLOW_RETAINED_PYTHON_OK')\") $script:Project\n"
                + "  if($probe.stdout -cne 'JOBFLOW_RETAINED_PYTHON_OK' -or -not [string]::IsNullOrWhiteSpace($probe.stderr)) { throw 'JOBFLOW_TEST_REAL_PYTHON_FAILED' }\n"
                + "  $result = [ordered]@{ write_blocked=$writeBlocked; rename_blocked=$renameBlocked; python=$probe.stdout; json_bytes=$document.bytes.GetType().FullName; size=[long]$input.size; sha256=[string]$input.sha256 }\n"
                + "}\n"
                + "finally { Close-RetainedRuntimeInputs }\n"
                + "[Console]::Out.Write(($result | ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run(
                harness,
                protected.resolve(),
                Path(sys.executable).resolve(),
                root.resolve(),
                json_input.resolve(),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertTrue(result["write_blocked"])
            self.assertTrue(result["rename_blocked"])
            self.assertEqual(result["python"], "JOBFLOW_RETAINED_PYTHON_OK")
            self.assertEqual(result["json_bytes"], "System.Byte[]")
            self.assertEqual(result["size"], len(b"immutable-runtime-input"))
            self.assertRegex(result["sha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertTrue(protected.is_file())

    def test_protected_directory_lock_pins_identity_until_released(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-runtime-directory-lock-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            protected = root / "protected-builder"
            protected.mkdir()
            (protected / "seed.bin").write_bytes(b"seed")
            harness = root / "directory-lock-runtime.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "Add-Type -AssemblyName System.IO.Compression\n"
                "Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
                + self._function_prelude()
                + "\n$script:RetainedRuntimeInputs = New-Object System.Collections.Generic.List[object]\n"
                + "$script:RetainedRuntimeInputsByPath = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)\n"
                + "$script:ProtectedRuntimeDirectoryLocks = New-Object System.Collections.Generic.List[object]\n"
                + "$script:ProtectedBuilderRuntime = $null\n"
                + "$createObserved=$false; $renameBlocked=$false; $createdAfter=$false\n"
                + "try {\n"
                + "  [void](Enter-ProtectedRuntimeDirectoryLock $args[0])\n"
                + "  [IO.File]::WriteAllText((Join-Path $args[0] 'added.bin'),'x'); $createObserved=[IO.File]::Exists((Join-Path $args[0] 'added.bin'))\n"
                + "  try { [IO.Directory]::Move($args[0],($args[0]+'.moved')) } catch { $renameBlocked=$true }\n"
                + "}\n"
                + "finally { Close-ProtectedRuntimeDirectoryLocks; Close-RetainedRuntimeInputs }\n"
                + "[IO.File]::WriteAllText((Join-Path $args[0] 'after.bin'),'x'); $createdAfter=[IO.File]::Exists((Join-Path $args[0] 'after.bin'))\n"
                + "[Console]::Out.Write(([ordered]@{create_observed=$createObserved;rename_blocked=$renameBlocked;created_after=$createdAfter}|ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run(harness, protected.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertTrue(result["create_observed"])
            self.assertTrue(result["rename_blocked"])
            self.assertTrue(result["created_after"])

    def test_protected_builder_exact_inventory_detects_added_path(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-runtime-inventory-lock-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            protected = root / "protected-builder"
            protected.mkdir()
            (protected / "python.exe").write_bytes(b"pinned-builder-placeholder")
            harness = root / "inventory-lock-runtime.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "Add-Type -AssemblyName System.IO.Compression\n"
                "Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
                + self._function_prelude()
                + "\n$script:RetainedRuntimeInputs = New-Object System.Collections.Generic.List[object]\n"
                + "$script:RetainedRuntimeInputsByPath = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)\n"
                + "$script:ProtectedRuntimeDirectoryLocks = New-Object System.Collections.Generic.List[object]\n"
                + "$script:ProtectedBuilderRuntime = $null; $failure=$null\n"
                + "try {\n"
                + "  $snapshot=Get-RetainedTreeSnapshot $args[0]\n"
                + "  [void](Enter-ProtectedRuntimeDirectoryLock $args[0])\n"
                + "  $tree=Get-ProtectedBuilderTreeIdentity $snapshot\n"
                + "  $python=@($snapshot.records|Where-Object{$_.relative -ceq 'python.exe'})[0].input\n"
                + "  $script:BuilderPythonInput=$python; $script:BuilderPython=[string]$python.path\n"
                + "  $script:ProtectedBuilderRuntime=[pscustomobject]@{root=[IO.Path]::GetFullPath($args[0]);snapshot=$snapshot;tree_sha256=$tree.tree_sha256;file_count=$tree.file_count;directory_count=$tree.directory_count}\n"
                + "  Assert-ProtectedBuilderRuntime\n"
                + "  [IO.File]::WriteAllText((Join-Path $args[0] 'injected.dll'),'x')\n"
                + "  try { Assert-ProtectedBuilderRuntime } catch { $failure=[string]$_.Exception.Message }\n"
                + "}\n"
                + "finally { Close-ProtectedRuntimeDirectoryLocks; Close-RetainedRuntimeInputs }\n"
                + "[Console]::Out.Write(([ordered]@{failure=$failure}|ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run(harness, protected.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertEqual(
                result["failure"], "JOBFLOW_RUNTIME_PROTECTED_BUILDER_CHANGED"
            )

    def test_retained_tree_snapshot_accepts_empty_package_marker(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-runtime-empty-marker-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            tree = root / "tree"
            tree.mkdir()
            (tree / "py.typed").write_bytes(b"")
            harness = root / "empty-marker-runtime.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "Add-Type -AssemblyName System.IO.Compression\n"
                "Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
                + self._function_prelude()
                + "\n$script:RetainedRuntimeInputs = New-Object System.Collections.Generic.List[object]\n"
                + "$script:RetainedRuntimeInputsByPath = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)\n"
                + "try {\n"
                + "  $snapshot=Get-RetainedTreeSnapshot $args[0]\n"
                + "  $marker=@($snapshot.records|Where-Object{$_.relative -ceq 'py.typed'})[0].input\n"
                + "  [Console]::Out.Write(([ordered]@{size=[long]$marker.size;sha256=[string]$marker.sha256}|ConvertTo-Json -Compress))\n"
                + "}\n"
                + "finally { Close-RetainedRuntimeInputs }\n",
                encoding="utf-8-sig",
            )
            completed = self._run(harness, tree.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertEqual(result["size"], 0)
            self.assertEqual(
                result["sha256"],
                "sha256:e3b0c44298fc1c149afbf4c8996fb924"
                "27ae41e4649b934ca495991b7852b855",
            )

    def test_hardlinked_file_and_reserved_leaf_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="jobflow-runtime-input-hardlink-", dir=TEST_ROOT
        ) as raw:
            root = Path(raw)
            source = root / "source.bin"
            alias = root / "alias.bin"
            source.write_bytes(b"same-file-two-names")
            os.link(source, alias)
            harness = root / "hardlink-runtime.ps1"
            harness.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "Add-Type -AssemblyName System.IO.Compression\n"
                "Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
                + self._function_prelude()
                + "\n$script:RetainedRuntimeInputs = New-Object System.Collections.Generic.List[object]\n"
                + "$script:RetainedRuntimeInputsByPath = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)\n"
                + "$hardlinkFailure = $null; $reservedFailure = $null\n"
                + "try {\n"
                + "  try { [void](Enter-RetainedRuntimeInput $args[0]) } catch { $hardlinkFailure = [string]$_.Exception.Message }\n"
                + "  try { [void](Assert-OrdinaryInput (Join-Path $args[1] 'CON.txt')) } catch { $reservedFailure = [string]$_.Exception.Message }\n"
                + "}\n"
                + "finally { Close-RetainedRuntimeInputs }\n"
                + "[Console]::Out.Write(([ordered]@{hardlink=$hardlinkFailure;reserved=$reservedFailure}|ConvertTo-Json -Compress))\n",
                encoding="utf-8-sig",
            )
            completed = self._run(harness, source.resolve(), root.resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            self.assertEqual(result["hardlink"], "JOBFLOW_RUNTIME_BUILD_INPUT_INVALID")
            self.assertEqual(result["reserved"], "JOBFLOW_RUNTIME_BUILD_INPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
