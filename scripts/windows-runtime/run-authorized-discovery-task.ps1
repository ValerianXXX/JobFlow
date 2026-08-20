[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { exit 2 }
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "JobOps"))
$localRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) { exit 2 }
$discoveryLockStream = $null

function Assert-LocalPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -ne $localRoot -and -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { exit 2 }
    $cursor = $absolute
    while ($cursor -and ($cursor -eq $localRoot -or $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase))) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { exit 2 }
        }
        if ($cursor -eq $localRoot) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

$lockHelpers = Join-Path $PSScriptRoot "jobflow-runtime-locks.ps1"
Assert-LocalPath $lockHelpers
if (-not (Test-Path -LiteralPath $lockHelpers -PathType Leaf)) { exit 2 }
. $lockHelpers

$discoveryLockPath = Join-Path $localRoot "Data\state\.authorized-discovery-task.lock"
Assert-LocalPath $discoveryLockPath
try {
    $discoveryLockStream = Enter-JobFlowFileLock $discoveryLockPath "JOBFLOW_DISCOVERY_TASK_LOCK_TIMEOUT"
}
catch { exit 2 }

try {
    $pointerPath = Join-Path $localRoot "current.json"
    $dataRoot = Join-Path $localRoot "Data"
    Assert-LocalPath $pointerPath
    Assert-LocalPath $dataRoot
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) { exit 2 }
    try { $pointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json }
    catch { exit 2 }
    $versionDirectory = [string]$pointer.version_directory
    if ($versionDirectory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$') { exit 2 }
    $versionRoot = [IO.Path]::GetFullPath((Join-Path $localRoot ("Application\versions\" + $versionDirectory)))
    Assert-LocalPath $versionRoot
    $pythonPath = Join-Path $versionRoot ".venv\Scripts\python.exe"
    $versionMarkerPath = Join-Path $versionRoot ".jobops-root"
    $dataMarkerPath = Join-Path $dataRoot ".jobflow-data-root"
    Assert-LocalPath $pythonPath
    Assert-LocalPath $versionMarkerPath
    Assert-LocalPath $dataMarkerPath
    if (
        -not (Test-Path -LiteralPath $versionMarkerPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $dataMarkerPath -PathType Leaf)
    ) { exit 2 }

    $env:JOBFLOW_DISCOVERY_TASK_LOCK_HELD = "1"
    $env:JOBFLOW_DATA_ROOT = $dataRoot
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Push-Location $versionRoot
    try {
        & $pythonPath -m jobops.cli authorized-discovery-run
        $jobflowExitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
    exit $jobflowExitCode
}
finally {
    Exit-JobFlowFileLock $discoveryLockStream
}
