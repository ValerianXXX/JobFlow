"""Frozen native stream contracts; all children are owned synthetic smoke work."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from _support import PROJECT
from jobops.release_smoke_process import run_release_python
from jobops.release_toolchain import windows_system_directory


@unittest.skipUnless(os.name == "nt", "Native Windows release command runner")
class ReleaseCommandStreamTests(unittest.TestCase):
    def run_streams(self, stdout: str, stderr: str, *, stderr_first: bool, child_exit: int):
        source = (PROJECT / "scripts/build-signed-update-bundle.ps1").read_text(encoding="utf-8-sig")
        entry = '\ntry {\n    try {\n        $projectRoot ='
        self.assertEqual(source.count(entry), 1)
        encoded_stdout = base64.b64encode(stdout.encode("utf-8")).decode("ascii")
        encoded_stderr = base64.b64encode(stderr.encode("utf-8")).decode("ascii")
        with tempfile.TemporaryDirectory(prefix="jobflow-stream-contract-") as raw:
            root = Path(raw)
            child = root / "child.ps1"
            child_source = "$out = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded_stdout + "'))\n"
            child_source += "$err = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded_stderr + "'))\n"
            writes = ("[Console]::Error.Write($err)", "[Console]::Out.Write($out)")
            if not stderr_first:
                writes = tuple(reversed(writes))
            child_source += "\n".join(writes) + f"\nexit {child_exit}\n"
            child.write_text(child_source, encoding="utf-8-sig")
            powershell = windows_system_directory() / "WindowsPowerShell/v1.0/powershell.exe"
            harness = source.split(entry)[0] + "\n$projectRoot = '" + str(PROJECT).replace("'", "''") + "'\n"
            harness += "$result = Invoke-SanitizedTextCommand 'powershell' '" + str(powershell).replace("'", "''")
            harness += "' @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File','" + str(child).replace("'", "''") + "') $projectRoot $null\n"
            harness += r'''
$hash = [Security.Cryptography.SHA256]::Create()
try {
    [pscustomobject]@{
        exit_code = $result.exit_code
        stdout_sha256 = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($result.stdout)))).Replace('-', '').ToLowerInvariant()
        stderr_sha256 = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($result.stderr)))).Replace('-', '').ToLowerInvariant()
    } | ConvertTo-Json -Compress
} finally { $hash.Dispose() }
'''
            script = root / "command.ps1"
            script.write_text(harness, encoding="utf-8-sig")
            launcher = root / "launch.py"
            launcher.write_text(
                "import subprocess,sys\n"
                "result=subprocess.run([sys.argv[1],'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',sys.argv[2]], creationflags=subprocess.CREATE_NO_WINDOW)\n"
                "raise SystemExit(result.returncode)\n", encoding="utf-8")
            # The existing smoke owner starts suspended, contains descendants,
            # then reclaims the entire owned job even if this contract times out.
            result = run_release_python(launcher, str(powershell), str(script), cwd=PROJECT,
                                        env=dict(os.environ), timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            observed = json.loads(result.stdout)
            self.assertEqual(observed["exit_code"], child_exit)
            self.assertEqual(observed["stdout_sha256"], hashlib.sha256(stdout.encode("utf-8")).hexdigest())
            self.assertEqual(observed["stderr_sha256"], hashlib.sha256(stderr.encode("utf-8")).hexdigest())

    def test_small_outputs_and_nonzero_status_remain_distinct(self):
        self.run_streams("synthetic result", "synthetic error", stderr_first=False, child_exit=7)

    def test_large_stderr_before_stdout_does_not_deadlock(self):
        self.run_streams("synthetic result", "E" * 100_000, stderr_first=True, child_exit=9)

    def test_large_both_streams_preserve_exact_bytes(self):
        self.run_streams("O" * 100_000, "E" * 100_000, stderr_first=False, child_exit=0)

    def test_empty_stdout_still_drains_large_stderr(self):
        self.run_streams("", "E" * 100_000, stderr_first=True, child_exit=11)
