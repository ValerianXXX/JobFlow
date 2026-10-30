"""Frozen native staging contracts; synthetic bytes and existing handle APIs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import unittest

from _support import PROJECT
import test_signed_update_staging_handles as staging

TEST_ROOT = PROJECT / "tests" / ".tmp"


@unittest.skipUnless(os.name == "nt" and staging.WINDOWS_POWERSHELL, "Native Windows handles required")
class StagedRuntimeRevalidationTests(unittest.TestCase):
    def scenario(self, mode: str, *, blocked: bool, deep: bool = False):
        with tempfile.TemporaryDirectory(prefix="jf-revalidate-", dir=TEST_ROOT) as raw:
            root = Path(raw)
            project = root / "project"
            dist = project / "dist"
            dist.mkdir(parents=True)
            harness = root / "native-revalidation.ps1"
            script = (
                "$ErrorActionPreference = 'Stop'\n"
                "$projectRoot = [IO.Path]::GetFullPath($args[0])\n"
                + staging.SignedUpdateStagingHandleTests._builder_handle_function_block()
                + "\nInitialize-JobFlowReleaseFileIdentityApi\n"
                "$context=$null; $lock=$null; $sourceLock=$null; $blocked=$false; $failure=$null; $length=0\n"
                "try {\n"
                "  $context = New-ProtectedInputStagingRoot $args[1]\n"
                "  $leaf = 'input.bin'\n"
                "  if($args[3] -ceq 'deep') { $leaf = ('x' * [Math]::Max(1,280-$context.Path.Length-5)) + '.bin' }\n"
                "  $output = Open-NewReleaseFileRelative $context.RootLock $leaf 1 'JOBFLOW_TEST_CREATE_FAILED'\n"
                "  $bytes = [Text.Encoding]::UTF8.GetBytes('test')\n"
                "  $output.stream.Write($bytes,0,$bytes.Length); $output.stream.Flush($true)\n"
                "  $lock = Convert-NewReleaseFileToReadLock $output 1024 'JOBFLOW_TEST_REVALIDATION_FAILED'\n"
                "  $length = ([string]$lock.path).Length\n"
                "  $saved = @{ParentLock=$lock.ParentLock;Name=$lock.Name;path=$lock.path;sha256=$lock.sha256;maximum_bytes=$lock.maximum_bytes}\n"
                "  try {\n"
                "    switch -CaseSensitive ($args[2]) {\n"
                "      'name' { $lock.Name='different.bin' }\n"
                "      'parent' { [void](New-ProtectedStagingDirectory $context 'other'); $lock.ParentLock=$context.Directories['other'] }\n"
                "      'closed-parent' { $lock.ParentLock=[pscustomobject]@{Path=$context.RootLock.Path;Handle=$null} }\n"
                "      'hash' { $lock.sha256='sha256:' + ('0'*64) }\n"
                "      'size' { $lock.maximum_bytes=3 }\n"
                "      'path' { $lock.path=Join-Path $context.Path 'different.bin' }\n"
                "      'ordinary' {\n"
                "        $source=Join-Path $args[0] 'source.bin'; [IO.File]::WriteAllBytes($source,$bytes)\n"
                "        $sourceLock=Enter-InputFileLock $source 1024 'JOBFLOW_TEST_SOURCE_INVALID'\n"
                "      }\n"
                "    }\n"
                "    try {\n"
                "      if($null -ne $sourceLock) { Assert-InputFileLockUnchanged $sourceLock }\n"
                "      else { Assert-InputFileLockUnchanged $lock }\n"
                "    } catch { $blocked=$true }\n"
                "  } finally { foreach($key in $saved.Keys){ $lock.$key=$saved[$key] } }\n"
                "} catch { $failure=[string]$_.Exception.Message }\n"
                "finally {\n"
                "  if($null -ne $sourceLock){ $sourceLock.stream.Dispose() }\n"
                "  if($null -ne $lock){ try { Remove-ProtectedStagedFileLock $lock } catch { $failure='JOBFLOW_TEST_FILE_CLEANUP_FAILED' } }\n"
                "  if($null -ne $context){ try { Remove-ProtectedInputStagingRoot $context } catch { $failure='JOBFLOW_TEST_DIRECTORY_CLEANUP_FAILED' } }\n"
                "}\n"
                "if($null -ne $failure){ [Console]::Error.Write($failure); exit 2 }\n"
                "[Console]::Out.Write(([ordered]@{blocked=$blocked;path_length=$length}|ConvertTo-Json -Compress))\n"
            )
            harness.write_text(script, encoding="utf-8-sig")
            completed = staging.SignedUpdateStagingHandleTests._run_powershell(harness, project.resolve(), dist.resolve(), mode, "deep" if deep else "short")
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(completed.stderr.strip().lstrip("\ufeff"), "")
            observed = json.loads(completed.stdout.strip().lstrip("\ufeff"))
            if deep:
                self.assertGreater(observed["path_length"], 260)
            self.assertEqual(observed["blocked"], blocked)
            self.assertEqual(list(dist.iterdir()), [])

    def test_deep_staged_leaf_keeps_the_same_identity(self):
        self.scenario("valid", blocked=False, deep=True)

    def test_short_staged_leaf_keeps_the_same_identity(self):
        self.scenario("valid", blocked=False)

    def test_staged_leaf_name_must_match_its_parent_binding(self):
        self.scenario("name", blocked=True)

    def test_staged_parent_must_match_its_retained_file(self):
        self.scenario("parent", blocked=True)

    def test_staged_parent_must_still_be_open(self):
        self.scenario("closed-parent", blocked=True)

    def test_hash_mismatch_is_rejected(self):
        self.scenario("hash", blocked=True)

    def test_size_limit_is_not_lost_by_native_revalidation(self):
        self.scenario("size", blocked=True)

    def test_changed_path_is_rejected(self):
        self.scenario("path", blocked=True)

    def test_ordinary_source_file_keeps_existing_validation(self):
        self.scenario("ordinary", blocked=False)


if __name__ == "__main__":
    unittest.main()
