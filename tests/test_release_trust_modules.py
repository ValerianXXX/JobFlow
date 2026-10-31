"""Real signature checks must not depend on ambient module discovery."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import unittest

from _support import PROJECT
from _release_node_fixture import PINNED_NODE_SHA256, pinned_node_path
from jobops.release_smoke_process import run_release_python
from jobops.release_toolchain import windows_system_directory


@unittest.skipUnless(os.name == "nt", "Windows native trust module boundary")
class ReleaseTrustModuleTests(unittest.TestCase):
    def probe(self, command: str, *, forged_policy: bool = False, unsigned: bool = False):
        source = (PROJECT / "scripts/build-signed-update-bundle.ps1").read_text(encoding="utf-8-sig")
        entry = "\ntry {\n    try {\n        $projectRoot ="
        self.assertEqual(source.count(entry), 1)
        source = source.split(entry)[0]
        self.assertEqual(source.count("function Invoke-SanitizedTextCommand("), 1)
        # Preserve the real child runner, environment, signatures and locks.
        # Inject only a synthetic module search path at child entry, after
        # process creation, to emulate ambient paths added by PowerShell itself.
        source = source.replace("function Invoke-SanitizedTextCommand(", "function Invoke-OriginalSanitizedTextCommand(")
        with tempfile.TemporaryDirectory(prefix="jobflow-trust-modules-", dir=PROJECT / "reports") as raw:
            root = Path(raw).resolve(strict=True)
            self.assertTrue(root.is_relative_to((PROJECT / "reports").resolve(strict=True)))
            module_name = "Microsoft.PowerShell.Management" if command == "Join-Path" else "Microsoft.PowerShell.Utility"
            modules = root / "modules"
            module = modules / module_name
            module.mkdir(parents=True)
            marker = root / "ambient-loaded.txt"
            fake_identity = {"status": "Valid", "subject": "CN=Synthetic Unapproved Publisher", "thumbprint": "0" * 40}
            output = "'" + json.dumps(fake_identity, separators=(",", ":")) + "'" if forged_policy else "throw 'SYNTHETIC_AMBIENT_MODULE_LOADED'"
            marker_literal = str(marker).replace("'", "''")
            (module / (module_name + ".psm1")).write_text(
                "function " + command + " { [IO.File]::WriteAllText('" + marker_literal + "','loaded'); " + output + " }\n"
                "Export-ModuleMember -Function " + command + "\n", encoding="utf-8")
            (module / (module_name + ".psd1")).write_text(
                "@{RootModule='" + module_name + ".psm1';ModuleVersion='1.0.0';FunctionsToExport=@('" + command
                + "');CmdletsToExport=@();AliasesToExport=@()}\n", encoding="utf-8")
            system_modules = windows_system_directory() / "WindowsPowerShell/v1.0/Modules"
            module_path = str(modules) + os.pathsep + str(system_modules)
            encoded_modules = base64.b64encode(module_path.encode("utf-16-le")).decode("ascii")
            prefix = "$env:PSModulePath=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + encoded_modules + "'));"
            encoded_prefix = base64.b64encode(prefix.encode("utf-16-le")).decode("ascii")
            harness = source + "\n$projectRoot = '" + str(PROJECT).replace("'", "''") + "'\n"
            harness += "$script:syntheticPrefix = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + encoded_prefix + "'))\n"
            harness += r'''
function Invoke-SanitizedTextCommand([string]$Tool,[string]$Executable,[string[]]$Arguments,[string]$WorkingDirectory,[string]$TrustedGitApplication,[string]$LocalAppDataOverride = $null) {
    if ($Tool -cne 'powershell' -or $Arguments[-2] -cne '-EncodedCommand') { throw 'SYNTHETIC_UNEXPECTED_COMMAND' }
    $program = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($Arguments[-1]))
    $Arguments[-1] = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script:syntheticPrefix + $program))
    return Invoke-OriginalSanitizedTextCommand $Tool $Executable $Arguments $WorkingDirectory $TrustedGitApplication $LocalAppDataOverride
}
$selectedLock = $null
try {
    $policy = Get-Content -LiteralPath (Join-Path $projectRoot 'config/release-toolchain.json') -Raw | ConvertFrom-Json
'''
            if forged_policy:
                harness += "$policy.tools.node.allowed_signers = @([pscustomobject]@{subject='CN=Synthetic Unapproved Publisher';thumbprint='" + "0" * 40 + "'})\n"
            harness += r'''
    $selected = Find-NodeApplication
    $selectedLock = Enter-AuthenticatedToolLock 'node' $selected $policy
    Assert-AuthenticatedToolUnchanged $selectedLock $policy
    [Console]::Out.Write([string]$selectedLock.identity.sha256)
} catch {
    $code = [string]$_.Exception.Message
    if ($code -notmatch '^JOBFLOW_[A-Z0-9_]+$') { $code = 'JOBFLOW_TEST_TRUST_MODULE_FAILED' }
    [Console]::Error.Write($code)
    exit 2
} finally { if ($null -ne $selectedLock) { $selectedLock.stream.Dispose() } }
'''
            script = root / "probe.ps1"
            script.write_text(harness, encoding="utf-8-sig")
            node = pinned_node_path()
            if unsigned:
                node = root / "node.exe"
                node.write_bytes(b"synthetic unsigned file; never execute")
            launcher = root / "launch.py"
            launcher.write_text(
                "import subprocess,sys\n"
                "r=subprocess.run([sys.argv[1],'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',sys.argv[2],'-NodePath',sys.argv[3]],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)\n"
                "sys.stdout.write(r.stdout);sys.stderr.write(r.stderr)\nraise SystemExit(r.returncode)\n", encoding="utf-8")
            result = run_release_python(launcher,
                str(windows_system_directory() / "WindowsPowerShell/v1.0/powershell.exe"),
                str(script), str(node), cwd=PROJECT, env=dict(os.environ), timeout=30)
            return result, marker.exists()

    def test_valid_tool_does_not_need_ambient_path_command(self):
        result, loaded = self.probe("Join-Path")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "sha256:" + PINNED_NODE_SHA256)
        self.assertFalse(loaded)

    def test_valid_tool_does_not_need_ambient_serializer(self):
        result, loaded = self.probe("ConvertTo-Json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "sha256:" + PINNED_NODE_SHA256)
        self.assertFalse(loaded)

    def test_ambient_serializer_cannot_replace_the_actual_publisher(self):
        result, loaded = self.probe("ConvertTo-Json", forged_policy=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "JOBFLOW_RELEASE_NODE_NOT_TRUSTED")
        self.assertFalse(loaded)

    def test_unsigned_file_stays_rejected_with_an_ambient_serializer(self):
        result, loaded = self.probe("ConvertTo-Json", unsigned=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "JOBFLOW_RELEASE_NODE_NOT_TRUSTED")
        self.assertFalse(loaded)
