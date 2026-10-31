"""Fixed path/trust contracts before explicit release Node selection is added."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from _support import PROJECT
from _release_node_fixture import PINNED_NODE_SHA256, pinned_node_path
from jobops.release_toolchain import windows_system_directory


@unittest.skipUnless(os.name == "nt", "Windows authenticated release tools")
class ExplicitReleaseNodeTests(unittest.TestCase):
    def run_selection(self, root, node):
        source = (PROJECT / "scripts/build-signed-update-bundle.ps1").read_text(encoding="utf-8-sig")
        entry = '\ntry {\n    try {\n        $projectRoot ='
        self.assertEqual(source.count(entry), 1)
        # Keep the real parameter parser, path validation, identity and locked
        # signer/hash validation. Only the final release transaction is omitted.
        harness = source.split(entry)[0] + "\n$projectRoot = '" + str(PROJECT).replace("'", "''") + "'\n" + r'''
$selectedLock = $null
try {
    $policy = Get-Content -LiteralPath (Join-Path $projectRoot 'config/release-toolchain.json') -Raw | ConvertFrom-Json
    $selected = Find-NodeApplication
    $selectedLock = Enter-AuthenticatedToolLock 'node' $selected $policy
    Assert-AuthenticatedToolUnchanged $selectedLock $policy
    [Console]::Out.Write([string]$selectedLock.identity.sha256)
} catch {
    $code = [string]$_.Exception.Message
    if ($code -notmatch '^JOBFLOW_[A-Z0-9_]+$') { $code = 'JOBFLOW_TEST_NODE_FAILED' }
    [Console]::Error.Write($code)
    exit 2
} finally {
    if ($null -ne $selectedLock) { $selectedLock.stream.Dispose() }
}
'''
        script = root / "node-selection.ps1"
        script.write_text(harness, encoding="utf-8-sig")
        powershell = windows_system_directory() / "WindowsPowerShell/v1.0/powershell.exe"
        return subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(script), "-NodePath", str(node)], cwd=PROJECT,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def assert_rejected(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "JOBFLOW_RELEASE_NODE_NOT_TRUSTED")

    def test_explicit_official_binary_retains_locked_signer_and_hash_checks(self):
        with tempfile.TemporaryDirectory(prefix="jobflow-explicit-node-") as raw:
            result = self.run_selection(Path(raw), pinned_node_path())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "sha256:" + PINNED_NODE_SHA256)

    def test_trusted_binary_in_a_path_with_spaces_is_supported(self):
        with tempfile.TemporaryDirectory(prefix="jobflow-explicit-node-") as raw:
            root = Path(raw)
            target = root / "isolated tools" / "node.exe"
            target.parent.mkdir()
            shutil.copyfile(pinned_node_path(), target)
            result = self.run_selection(root, target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "sha256:" + PINNED_NODE_SHA256)

    def test_bad_explicit_input_never_falls_back_to_a_default_runtime(self):
        with tempfile.TemporaryDirectory(prefix="jobflow-explicit-node-") as raw:
            root = Path(raw)
            for node in ("relative/node.exe", root / "missing.exe", " ", r"\\invalid.example\tools\node.exe",
                         str(pinned_node_path()) + ":stream"):
                with self.subTest(kind=str(node).split(":")[-1]):
                    self.assert_rejected(self.run_selection(root, node))

    def test_unsigned_executable_and_renamed_binary_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="jobflow-explicit-node-") as raw:
            root = Path(raw)
            fake = root / "node.exe"
            fake.write_bytes(b"synthetic untrusted executable; never execute")
            self.assert_rejected(self.run_selection(root, fake))
            renamed = root / "not-node.exe"
            shutil.copyfile(pinned_node_path(), renamed)
            self.assert_rejected(self.run_selection(root, renamed))

    def test_authenticated_hardlink_is_still_rejected(self):
        with tempfile.TemporaryDirectory(prefix="jobflow-explicit-node-") as raw:
            root = Path(raw)
            first = root / "first" / "node.exe"
            first.parent.mkdir()
            shutil.copyfile(pinned_node_path(), first)
            second = root / "node.exe"
            os.link(first, second)
            self.assert_rejected(self.run_selection(root, second))


if __name__ == "__main__":
    unittest.main()
