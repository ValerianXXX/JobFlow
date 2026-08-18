[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$localRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$versionsRoot = Join-Path $localRoot "Application\versions"
$dataRoot = Join-Path $localRoot "Data"
$currentPath = Join-Path $localRoot "current.json"
$previousPath = Join-Path $localRoot "previous.json"

function Assert-JobFlowLocalPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -ne $localRoot -and -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_ROLLBACK_PATH_FORBIDDEN"
    }
    $cursor = $absolute
    while ($cursor -and ($cursor -eq $localRoot -or $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase))) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_ROLLBACK_REPARSE_FORBIDDEN"
            }
        }
        if ($cursor -eq $localRoot) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Read-Pointer([string]$Path) {
    Assert-JobFlowLocalPath $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "JOBFLOW_ROLLBACK_VERSION_MISSING" }
    try { $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
    catch { throw "JOBFLOW_ROLLBACK_POINTER_INVALID" }
    $directory = [string]$value.version_directory
    if (
        $value.schema_version -ne 1 -or
        $directory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' -or
        ([string]$value.source_sha256) -notmatch '^[0-9a-f]{64}$'
    ) { throw "JOBFLOW_ROLLBACK_POINTER_INVALID" }
    $target = [IO.Path]::GetFullPath((Join-Path $versionsRoot $directory))
    Assert-JobFlowLocalPath $target
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "JOBFLOW_ROLLBACK_VERSION_MISSING" }
    return @{ Value = $value; Root = $target }
}

function Write-Pointer([string]$Path, [object]$Value) {
    Assert-JobFlowLocalPath $Path
    $temporary = Join-Path $localRoot (([IO.Path]::GetFileName($Path)) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    $backup = Join-Path $localRoot (([IO.Path]::GetFileName($Path)) + "." + [Guid]::NewGuid().ToString("N") + ".backup")
    try {
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 6 -Compress), (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporary, $Path, $backup, $true)
        }
        else { Move-Item -LiteralPath $temporary -Destination $Path }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
        if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force }
    }
}

function Test-Version([string]$VersionRoot) {
    $python = Join-Path $VersionRoot ".venv\Scripts\python.exe"
    $health = Join-Path $VersionRoot "scripts\check-jobflow.ps1"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $health -PathType Leaf)) {
        return $false
    }
    $saved = $env:JOBFLOW_DATA_ROOT
    try {
        $env:JOBFLOW_DATA_ROOT = $dataRoot
        $null = & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $health -Json -PythonPath $python 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch { return $false }
    finally {
        if ($null -eq $saved) { Remove-Item Env:JOBFLOW_DATA_ROOT -ErrorAction SilentlyContinue }
        else { $env:JOBFLOW_DATA_ROOT = $saved }
    }
}

Assert-JobFlowLocalPath $versionsRoot
Assert-JobFlowLocalPath $dataRoot
$current = Read-Pointer $currentPath
$previous = Read-Pointer $previousPath
if ([string]$current.Value.version_directory -eq [string]$previous.Value.version_directory) {
    throw "JOBFLOW_ROLLBACK_VERSION_NOT_DIFFERENT"
}
if (-not (Test-Version $previous.Root)) {
    throw "要恢复的版本未通过本机健康检查；当前版本保持不变。 / The rollback target failed its local health check; the current version was not changed."
}

Write-Pointer $previousPath $current.Value
try {
    Write-Pointer $currentPath $previous.Value
    if (-not (Test-Version $previous.Root)) { throw "JOBFLOW_ROLLBACK_POST_SWITCH_CHECK_FAILED" }
}
catch {
    Write-Pointer $currentPath $current.Value
    Write-Pointer $previousPath $previous.Value
    throw
}

Write-Host "JobFlow 已恢复到版本 $($previous.Value.version)。请关闭正在运行的 JobFlow 后重新打开。 / JobFlow rolled back to $($previous.Value.version). Close any running JobFlow window and start it again."
exit 0
