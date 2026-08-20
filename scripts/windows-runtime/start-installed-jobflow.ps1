[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$expectedRoot = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { $null } else {
    [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "JobOps"))
}
$localRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$versionsRoot = Join-Path $localRoot "Application\versions"
$dataRoot = Join-Path $localRoot "Data"
$pointerPath = Join-Path $localRoot "current.json"
$runtimeLockStream = $null

if ($null -eq $expectedRoot -or -not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_INSTALLED_ROOT_INVALID"
}

function Assert-JobFlowLocalPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -ne $localRoot -and -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_INSTALLED_PATH_FORBIDDEN"
    }
    $cursor = $absolute
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
}

$lockHelpers = Join-Path $PSScriptRoot "jobflow-runtime-locks.ps1"
Assert-JobFlowLocalPath $lockHelpers
if (-not (Test-Path -LiteralPath $lockHelpers -PathType Leaf)) { throw "JOBFLOW_RUNTIME_LOCK_HELPERS_MISSING" }
. $lockHelpers

$runtimeLockPath = Join-Path $dataRoot "state\.jobflow-runtime-maintenance.lock"
Assert-JobFlowLocalPath $runtimeLockPath
$runtimeLockStream = Enter-JobFlowFileLock $runtimeLockPath "JOBFLOW_ALREADY_RUNNING_OR_MAINTENANCE_ACTIVE"

try {
    Assert-JobFlowLocalPath $pointerPath
    Assert-JobFlowLocalPath $dataRoot
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        throw "JobFlow 尚未完成固定目录安装。请重新运行 Install JobFlow.cmd。 / JobFlow is not installed in its fixed directory; run Install JobFlow.cmd again."
    }
    $pointer = Get-Content -LiteralPath $pointerPath -Raw | ConvertFrom-Json
    $versionDirectory = [string]$pointer.version_directory
    if ($versionDirectory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$') {
        throw "JOBFLOW_INSTALLED_POINTER_INVALID"
    }
    $versionRoot = [IO.Path]::GetFullPath((Join-Path $versionsRoot $versionDirectory))
    Assert-JobFlowLocalPath $versionRoot
    $venvPython = Join-Path $versionRoot ".venv\Scripts\python.exe"
    if (
        -not (Test-Path -LiteralPath (Join-Path $versionRoot ".jobops-root") -PathType Leaf) -or
        -not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $dataRoot ".jobflow-data-root") -PathType Leaf)
    ) {
        throw "JobFlow 固定安装不完整。请重新运行安装程序或回滚。 / The fixed JobFlow installation is incomplete; reinstall or roll back."
    }

    $env:JOBFLOW_DATA_ROOT = $dataRoot
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Push-Location $versionRoot
    try {
        $arguments = @("-m", "jobops.cli", "onboarding-center")
        if ($NoBrowser) { $arguments += "--no-browser" }
        & $venvPython @arguments
        $jobflowExitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
    if ($jobflowExitCode -ne 0) {
        throw "JobFlow 未能正常启动（代码 $jobflowExitCode）。 / JobFlow stopped with exit code $jobflowExitCode."
    }
}
finally {
    Exit-JobFlowFileLock $runtimeLockStream
}
