from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


PROJECT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT / "scripts" / "install-jobflow.ps1"
UPDATER = PROJECT / "scripts" / "windows-runtime" / "update-installed-jobflow.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class StagingRaceHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = INSTALLER.read_text(encoding="utf-8-sig")
        cls.updater = UPDATER.read_text(encoding="utf-8-sig")

    def test_both_scripts_use_handle_anchored_staging_and_nonrecursive_cleanup(self) -> None:
        for source in (self.installer, self.updater):
            self.assertIn("CreateFileW", source)
            self.assertIn("CreateDirectoryW", source)
            self.assertIn("GetFinalPathNameByHandle", source)
            self.assertIn("0x02000000 -bor 0x00200000", source)
            self.assertNotIn("Remove-Item -LiteralPath $stagingRoot -Recurse", source)
            self.assertIn("[IO.Directory]::Delete", source)
            self.assertIn("$false", source)
        self.assertIn("New-StableInstallerDirectoryRoot $DestinationRoot", self.installer)
        self.assertIn("Assert-StableInstallerDirectoryContext $parentContext", self.installer)
        self.assertIn("Assert-OpenInstallerFileAtPath $output $destination", self.installer)
        self.assertIn("New-StableUpdaterDirectoryRoot $stagingRoot", self.updater)
        self.assertIn("Assert-StableUpdaterDirectoryContext $parentContext", self.updater)
        self.assertIn("Assert-OpenUpdaterFileAtPath $output $Destination", self.updater)
        self.assertNotIn("Expand-LockedVerifiedArchive", self.updater)

    def test_windows_directory_handle_blocks_swap_and_cleanup_refuses_junction(self) -> None:
        if os.name != "nt" or not POWERSHELL.exists():
            self.skipTest("requires Windows PowerShell and Windows directory handles")
        with tempfile.TemporaryDirectory(prefix="jobflow-staging-race-") as temp:
            harness = Path(temp) / "harness.ps1"
            harness.write_text(
                textwrap.dedent(
                    f"""
                    $ErrorActionPreference = 'Stop'
                    $sourcePath = {ps_quote(str(INSTALLER))}
                    $wanted = @(
                        'Assert-JobFlowLocalPath',
                        'Assert-NoInstallerAlternateDataStreams',
                        'Initialize-JobFlowInstallerFileIdentityApi',
                        'Get-InstallerHandleIdentity',
                        'Get-InstallerFinalPathFromHandle',
                        'Open-StableInstallerDirectoryHandle',
                        'Close-StableInstallerDirectoryContext',
                        'Assert-StableInstallerDirectoryContext',
                        'New-InstallerDirectoryRelativeToLock',
                        'Open-NewInstallerFileRelative',
                        'Open-StableInstallerDirectoryChain',
                        'New-StableInstallerDirectoryRoot',
                        'Assert-OpenInstallerFileAtPath',
                        'Remove-SafeInstallerTree'
                    )
                    $tokens = $null; $errors = $null
                    $ast = [Management.Automation.Language.Parser]::ParseFile(
                        $sourcePath, [ref]$tokens, [ref]$errors
                    )
                    if ($errors.Count -ne 0) {{ throw 'SOURCE_PARSE_FAILED' }}
                    $functions = @($ast.FindAll({{
                        param($node)
                        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                        $wanted -contains $node.Name
                    }}, $true))
                    foreach ($name in $wanted) {{
                        $match = @($functions | Where-Object Name -CEQ $name)
                        if ($match.Count -ne 1) {{ throw "FUNCTION_MISSING:$name" }}
                        Invoke-Expression $match[0].Extent.Text
                    }}

                    $sandbox = Join-Path ([IO.Path]::GetTempPath()) ('jf-native-' + [Guid]::NewGuid().ToString('N'))
                    $script:localRoot = Join-Path $sandbox 'local'
                    $script:versionsRoot = Join-Path $localRoot 'Application\\versions'
                    [IO.Directory]::CreateDirectory($versionsRoot) | Out-Null
                    try {{
                        $locked = Join-Path $localRoot 'locked'
                        [IO.Directory]::CreateDirectory($locked) | Out-Null
                        $moved = Join-Path $localRoot 'moved'
                        $renameOutside = Join-Path $sandbox 'rename-outside'
                        [IO.Directory]::CreateDirectory($renameOutside) | Out-Null
                        $context = Open-StableInstallerDirectoryChain $locked 'LOCK_FAILED'
                        try {{
                            $renamed = $false
                            try {{ [IO.Directory]::Move($locked, $moved); $renamed = $true }} catch {{}}
                            if ($renamed) {{
                                $swap = Start-Process -FilePath $env:ComSpec -ArgumentList @(
                                    '/d', '/c', 'mklink', '/J', $locked, $renameOutside
                                ) -Wait -PassThru -WindowStyle Hidden
                                if ($swap.ExitCode -ne 0) {{ throw 'SWAP_SETUP_FAILED' }}
                                $writeRejected = $false
                                try {{
                                    $probe = Open-NewInstallerFileRelative $context (Join-Path $locked 'probe.txt') 'LOCK_CHANGED'
                                    $probe.Dispose()
                                }}
                                catch {{ $writeRejected = $_.Exception.Message -eq 'LOCK_CHANGED' }}
                                if (-not $writeRejected) {{ throw 'RENAMED_PARENT_WRITE_ACCEPTED' }}
                                if ([IO.File]::Exists((Join-Path $renameOutside 'probe.txt'))) {{ throw 'EXTERNAL_FILE_CREATED' }}
                                & $env:ComSpec /d /c rmdir $locked | Out-Null
                            }}
                            else {{ Assert-StableInstallerDirectoryContext $context 'LOCK_CHANGED' }}
                        }}
                        finally {{ Close-StableInstallerDirectoryContext $context }}
                        if (-not $renamed) {{ [IO.Directory]::Move($locked, $moved) }}

                        $collision = Join-Path $localRoot '.i-111111111111'
                        [IO.Directory]::CreateDirectory($collision) | Out-Null
                        $collisionRejected = $false
                        try {{ $unused = New-StableInstallerDirectoryRoot $collision 'COLLISION' }}
                        catch {{ $collisionRejected = $_.Exception.Message -eq 'COLLISION' }}
                        if (-not $collisionRejected) {{ throw 'PREEXISTING_ROOT_ACCEPTED' }}

                        $stage = Join-Path $localRoot '.i-abcdef123456'
                        $outside = Join-Path $sandbox 'outside'
                        [IO.Directory]::CreateDirectory($stage) | Out-Null
                        [IO.Directory]::CreateDirectory($outside) | Out-Null
                        $sentinel = Join-Path $outside 'sentinel.txt'
                        [IO.File]::WriteAllText($sentinel, 'KEEP')
                        $junction = Join-Path $stage 'escape'
                        $mklink = Start-Process -FilePath $env:ComSpec -ArgumentList @(
                            '/d', '/c', 'mklink', '/J', $junction, $outside
                        ) -Wait -PassThru -WindowStyle Hidden
                        if ($mklink.ExitCode -ne 0) {{ throw 'JUNCTION_SETUP_FAILED' }}
                        $cleanupRejected = $false
                        try {{ Remove-SafeInstallerTree $stage 'UNSAFE_TREE' }}
                        catch {{
                            # The native identity layer may surface either the
                            # caller's fail-closed code or the underlying
                            # reparse/open error.  Both are acceptable only if
                            # the suspicious root and external sentinel remain.
                            $cleanupRejected = $true
                        }}
                        if (-not $cleanupRejected) {{ throw 'JUNCTION_CLEANUP_NOT_REJECTED' }}
                        if (-not [IO.File]::Exists($sentinel)) {{ throw 'EXTERNAL_SENTINEL_DELETED' }}
                        if (-not [IO.Directory]::Exists($stage)) {{ throw 'SUSPICIOUS_RESIDUE_NOT_RETAINED' }}
                        Write-Output 'PASS'
                    }}
                    finally {{
                        # Test-only cleanup stays inside the unique OS temp directory.
                        if ([IO.Directory]::Exists((Join-Path $localRoot '.i-abcdef123456\\escape'))) {{
                            & $env:ComSpec /d /c rmdir (Join-Path $localRoot '.i-abcdef123456\\escape') | Out-Null
                        }}
                        if ([IO.Directory]::Exists($sandbox)) {{ Remove-Item -LiteralPath $sandbox -Recurse -Force }}
                    }}
                    """
                ),
                encoding="utf-8-sig",
            )
            result = subprocess.run(
                [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                cwd=PROJECT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
