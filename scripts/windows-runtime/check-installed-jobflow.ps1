[CmdletBinding()]
param([switch]$Json)

$ErrorActionPreference = "Stop"
$expectedRoot = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { $null } else {
    [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "JobOps"))
}
$localRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$pointerPath = Join-Path $localRoot "current.json"
if ($null -eq $expectedRoot -or -not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_INSTALLED_ROOT_INVALID"
}
$rootItem = Get-Item -LiteralPath $localRoot -Force
if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "JOBFLOW_INSTALLED_REPARSE_FORBIDDEN"
}
$lockHelpers = Join-Path $PSScriptRoot "jobflow-runtime-locks.ps1"
if (-not (Test-Path -LiteralPath $lockHelpers -PathType Leaf)) {
    throw "JOBFLOW_RUNTIME_LOCK_HELPER_MISSING"
}
$lockHelperItem = Get-Item -LiteralPath $lockHelpers -Force
if (($lockHelperItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "JOBFLOW_INSTALLED_REPARSE_FORBIDDEN"
}
$lockPrefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$lockCursor = [IO.Path]::GetFullPath($lockHelpers)
while ($lockCursor -and ($lockCursor -eq $localRoot -or $lockCursor.StartsWith($lockPrefix, [StringComparison]::OrdinalIgnoreCase))) {
    if (Test-Path -LiteralPath $lockCursor) {
        $lockCursorItem = Get-Item -LiteralPath $lockCursor -Force
        if (($lockCursorItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_INSTALLED_REPARSE_FORBIDDEN"
        }
    }
    if ($lockCursor -eq $localRoot) { break }
    $lockCursor = [IO.Path]::GetDirectoryName($lockCursor)
}
. $lockHelpers

$runtimeLock = $null
$discoveryLock = $null
$exitCode = 1
try {
    $runtimeLock = Enter-JobFlowFileLock `
        (Join-Path $localRoot "Data\state\.jobflow-runtime-maintenance.lock") `
        "JOBFLOW_ALREADY_RUNNING_OR_MAINTENANCE_ACTIVE" 30
    $discoveryLock = Enter-JobFlowFileLock `
        (Join-Path $localRoot "Data\state\.authorized-discovery-task.lock") `
        "JOBFLOW_DISCOVERY_TASK_LOCK_TIMEOUT" 30

    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        throw "JOBFLOW_INSTALLED_POINTER_MISSING"
    }
    $pointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
    $versionDirectory = [string]$pointer.version_directory
    if ($versionDirectory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$') {
        throw "JOBFLOW_INSTALLED_POINTER_INVALID"
    }
    $versionRoot = [IO.Path]::GetFullPath((Join-Path $localRoot "Application\versions\$versionDirectory"))
    $prefix = ([IO.Path]::GetFullPath((Join-Path $localRoot "Application\versions"))).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $versionRoot.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_INSTALLED_POINTER_FORBIDDEN"
    }
    $cursor = $versionRoot
    while ($cursor -and $cursor.StartsWith($localRoot, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_INSTALLED_REPARSE_FORBIDDEN"
            }
        }
        if ($cursor -eq $localRoot) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    $env:JOBFLOW_DATA_ROOT = Join-Path $localRoot "Data"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $python = Join-Path $versionRoot ".venv\Scripts\python.exe"
    $health = Join-Path $versionRoot "scripts\check-jobflow.ps1"
    $arguments = @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $health, "-PythonPath", $python)
    if ($Json) { $arguments += "-Json" }
    & powershell.exe @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Exit-JobFlowFileLock $discoveryLock
    Exit-JobFlowFileLock $runtimeLock
}
exit $exitCode
