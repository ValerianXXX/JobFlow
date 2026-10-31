"""Bounded, pathless timing of the real locked release Node trust operations.

This diagnostic preserves signature, hash, file-lock and 30-second checks. It
instruments a temporary copy of the release functions, not the shipped source.
No Node program, signing transaction or release is executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "tests"), str(PROJECT / "src")]
from _release_node_fixture import PINNED_NODE_SHA256, pinned_node_path
from jobops.release_smoke_process import run_release_python
from jobops.release_toolchain import windows_system_directory

STAGES = frozenset({"STARTED", "NODE_SELECTED", "FILE_ID_BEGIN", "FILE_ID_END",
    "FIRST_IDENTITY_BEGIN", "FIRST_IDENTITY_END", "SIGNATURE_PROCESS_BEGIN",
    "SIGNATURE_PROCESS_END", "HASH_BEGIN", "HASH_END", "AUTH_LOCKED", "AUTH_RECHECKED"})


def instrument(source: str) -> str:
    entry = "\ntry {\n    try {\n        $projectRoot ="
    if source.count(entry) != 1:
        raise ValueError("TRUST_PROFILE_SOURCE_CHANGED")
    result = source.split(entry)[0]
    replacements = (
        ("$openIdentity = Get-OpenOutputFileIdentity $stream $code",
         "Set-TrustProbeStage 'FILE_ID_BEGIN'\n        $openIdentity = Get-OpenOutputFileIdentity $stream $code\n        Set-TrustProbeStage 'FILE_ID_END'"),
        ("$before = Get-AuthenticatedToolIdentity $Tool $absolute $Policy",
         "Set-TrustProbeStage 'FIRST_IDENTITY_BEGIN'\n        $before = Get-AuthenticatedToolIdentity $Tool $absolute $Policy\n        Set-TrustProbeStage 'FIRST_IDENTITY_END'"),
        ('$signatureCommand = Invoke-SanitizedTextCommand "powershell"',
         'Set-TrustProbeStage \'SIGNATURE_PROCESS_BEGIN\'\n    $signatureCommand = Invoke-SanitizedTextCommand "powershell"'),
        ("if ($signatureCommand.exit_code -ne 0",
         "Set-TrustProbeStage 'SIGNATURE_PROCESS_END'\n    if ($signatureCommand.exit_code -ne 0"),
        ("$digest = $algorithm.ComputeHash($Stream)",
         "Set-TrustProbeStage 'HASH_BEGIN'\n        $digest = $algorithm.ComputeHash($Stream)\n        Set-TrustProbeStage 'HASH_END'"),
    )
    for old, new in replacements:
        if result.count(old) != 1:
            raise ValueError("TRUST_PROFILE_SOURCE_CHANGED")
        result = result.replace(old, new)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New JSON path inside this checkout's reports directory")
    output = Path(parser.parse_args().output).absolute()
    reports = (PROJECT / "reports").resolve(strict=True)
    if not output.resolve().is_relative_to(reports) or output.suffix != ".json" or output.exists():
        raise ValueError("TRUST_PROFILE_OUTPUT_INVALID")
    node = pinned_node_path()
    source = (PROJECT / "scripts/build-signed-update-bundle.ps1").read_text(encoding="utf-8-sig")
    harness = instrument(source)
    harness += "\n$projectRoot = '" + str(PROJECT).replace("'", "''") + "'\n"
    harness += r'''
$probeClock = [Diagnostics.Stopwatch]::StartNew()
function Set-TrustProbeStage([string]$Stage) {
    [IO.File]::AppendAllText((Join-Path $PSScriptRoot 'timings.txt'), $Stage + ':' + $probeClock.ElapsedMilliseconds + [Environment]::NewLine)
}
$lock = $null
try {
    Set-TrustProbeStage 'STARTED'
    $policy = Get-Content -LiteralPath (Join-Path $projectRoot 'config/release-toolchain.json') -Raw | ConvertFrom-Json
    $selected = Find-NodeApplication
    Set-TrustProbeStage 'NODE_SELECTED'
    $lock = Enter-AuthenticatedToolLock 'node' $selected $policy
    Set-TrustProbeStage 'AUTH_LOCKED'
    Assert-AuthenticatedToolUnchanged $lock $policy
    Set-TrustProbeStage 'AUTH_RECHECKED'
    [Console]::Out.Write([string]$lock.identity.sha256)
} finally { if ($null -ne $lock) { $lock.stream.Dispose() } }
'''
    status, error_type, stages = "FAILED", None, []
    with tempfile.TemporaryDirectory(prefix="jobflow-trust-profile-", dir=reports) as raw:
        root = Path(raw).resolve(strict=True)
        if not root.is_relative_to(reports):
            raise ValueError("TRUST_PROFILE_ROOT_INVALID")
        script = root / "trace.ps1"
        script.write_text(harness, encoding="utf-8-sig")
        launcher = root / "launch.py"
        launcher.write_text(
            "import subprocess,sys\n"
            "r=subprocess.run([sys.argv[1],'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',sys.argv[2],'-NodePath',sys.argv[3]],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)\n"
            "sys.stdout.write(r.stdout);sys.stderr.write(r.stderr)\nraise SystemExit(r.returncode)\n", encoding="utf-8")
        start = time.monotonic()
        try:
            result = run_release_python(launcher,
                str(windows_system_directory() / "WindowsPowerShell/v1.0/powershell.exe"),
                str(script), str(node), cwd=PROJECT, env=dict(os.environ), timeout=30)
            if result.returncode or result.stdout != "sha256:" + PINNED_NODE_SHA256 or result.stderr:
                raise ValueError("TRUST_PROFILE_NATIVE_RESULT_INVALID")
            status = "PASS"
        except Exception as error:
            # Never log arbitrary child stderr, private paths or exception text.
            error_type = type(error).__name__
        elapsed = time.monotonic() - start
        trace = root / "timings.txt"
        if trace.exists():
            if trace.stat().st_size > 4096:
                raise ValueError("TRUST_PROFILE_TRACE_INVALID")
            for line in trace.read_text(encoding="utf-8-sig").splitlines():
                label, _, milliseconds = line.partition(":")
                if label not in STAGES or not milliseconds.isdecimal() or len(stages) >= 64:
                    raise ValueError("TRUST_PROFILE_TRACE_INVALID")
                stages.append({"stage": label, "elapsed_ms": int(milliseconds)})
    report = {"status": status, "error_type": error_type, "elapsed_seconds": elapsed,
        "stages": stages, "source_lf_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "node_sha256": PINNED_NODE_SHA256, "python": sys.version.split()[0],
        "deadline_seconds": 30, "trust_operations_replaced": False, "raw_paths_logged": False,
        "production_actions": 0, "note": "Real Windows trust operations; not signed-release or clean-machine acceptance"}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
