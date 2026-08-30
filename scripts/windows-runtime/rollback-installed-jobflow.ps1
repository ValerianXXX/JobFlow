[CmdletBinding()]
param([switch]$StartNewRollback)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Assert-OrdinaryLeaf([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or
        ($item.Attributes -band (
            [IO.FileAttributes]::Directory -bor
            [IO.FileAttributes]::Device -bor
            [IO.FileAttributes]::ReparsePoint
        )) -ne 0) {
        throw "JOBFLOW_ROLLBACK_WRAPPER_FAILED"
    }
    return [IO.Path]::GetFullPath([string]$item.FullName)
}

try {
    $localData = [IO.Path]::GetFullPath(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    )
    if ([string]::IsNullOrWhiteSpace($localData) -or -not [IO.Directory]::Exists($localData)) {
        throw "JOBFLOW_ROLLBACK_WRAPPER_FAILED"
    }
    $expectedRoot = [IO.Path]::GetFullPath([IO.Path]::Combine($localData, "JobOps"))
    $actualRoot = [IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath ([IO.Path]::Combine($PSScriptRoot, ".."))).Path
    )
    if (-not $actualRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_ROLLBACK_WRAPPER_FAILED"
    }
    foreach ($directory in @($actualRoot, $PSScriptRoot)) {
        $item = Get-Item -LiteralPath $directory -Force -ErrorAction Stop
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band (
                [IO.FileAttributes]::Device -bor [IO.FileAttributes]::ReparsePoint
            )) -ne 0) {
            throw "JOBFLOW_ROLLBACK_WRAPPER_FAILED"
        }
    }

    $bootstrap = Assert-OrdinaryLeaf ([IO.Path]::Combine($PSScriptRoot, "jobflow-bootstrap.ps1"))
    $trustedPowerShell = Assert-OrdinaryLeaf (
        [IO.Path]::Combine(
            [Environment]::SystemDirectory,
            "WindowsPowerShell\v1.0\powershell.exe"
        )
    )
    $arguments = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", $bootstrap, "-Rollback"
    )
    if ($StartNewRollback.IsPresent) { $arguments += "-StartNewRollback" }
    & $trustedPowerShell @arguments
    exit $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("JOBFLOW_ROLLBACK_WRAPPER_FAILED")
    exit 1
}
