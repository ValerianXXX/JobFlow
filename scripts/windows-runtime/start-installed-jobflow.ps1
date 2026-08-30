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
$rollbackTransactionPath = Join-Path $localRoot ".rollback-pointer-transaction.json"
$rollbackTransactionBackupPath = Join-Path $localRoot ".rollback-pointer-transaction.backup.json"
$runtimeLockStream = $null
$pointerFileLock = $null
$pythonFileLock = $null

if ($null -eq $expectedRoot -or -not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_INSTALLED_ROOT_INVALID"
}

function Get-TrustedWindowsPowerShell {
    $candidate = [IO.Path]::GetFullPath((Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\powershell.exe"))
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
    $securityModule = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
    Microsoft.PowerShell.Core\Import-Module -Name $securityModule -Force -ErrorAction Stop
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $candidate
    if (
        [string]$signature.Status -cne "Valid" -or
        $null -eq $signature.SignerCertificate -or
        [string]$signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)'
    ) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
    return $candidate
}

$trustedWindowsPowerShell = Get-TrustedWindowsPowerShell

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

if (-not ("JobFlowInstalledStartFiles" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class JobFlowInstalledStartFiles {
    [StructLayout(LayoutKind.Sequential)] public struct Info {
        public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Creation;
        public System.Runtime.InteropServices.ComTypes.FILETIME Access; public System.Runtime.InteropServices.ComTypes.FILETIME Write;
        public uint Volume; public uint SizeHigh; public uint SizeLow; public uint LinkCount; public uint IndexHigh; public uint IndexLow;
    }
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
}
'@
}

function Open-JobFlowOrdinarySingleLinkFile([string]$Path, [string]$Code) {
    Assert-JobFlowLocalPath $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Code }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::Device -bor [IO.FileAttributes]::ReparsePoint)) -ne 0) { throw $Code }
    $stream = $null
    try {
        $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $information = New-Object JobFlowInstalledStartFiles+Info
        if (-not [JobFlowInstalledStartFiles]::GetFileInformationByHandle($stream.SafeFileHandle, [ref]$information) -or
            [long]$information.LinkCount -ne 1 -or
            ([IO.FileAttributes]$information.Attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::Device -bor [IO.FileAttributes]::ReparsePoint)) -ne 0) { throw $Code }
        return $stream
    }
    catch {
        if ($null -ne $stream) { $stream.Dispose() }
        throw $Code
    }
}

function Read-JobFlowPointer([string]$Path) {
    $stream = Open-JobFlowOrdinarySingleLinkFile $Path "JOBFLOW_INSTALLED_POINTER_INVALID"
    try {
        if ($stream.Length -le 0 -or $stream.Length -gt 65536) { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
            $offset += $read
        }
        $text = [Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF)
        $value = $text | ConvertFrom-Json
        $properties = @($value.PSObject.Properties.Name | Sort-Object)
        if (($value.schema_version -isnot [int] -and $value.schema_version -isnot [long])) { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
        $schema = [int64]$value.schema_version
        $expected = @(
            "bootstrap_version", "platform", "product", "release_key_id", "runtime_closure_manifest_sha256",
            "runtime_tree_sha256", "schema_version", "source_commit", "source_payload_sha256", "version", "version_directory"
        ) | Sort-Object
        if ($schema -ne 2 -or @(Compare-Object $expected $properties -SyncWindow 0).Count -ne 0 -or
            $value.product -isnot [string] -or [string]$value.product -cne "JobFlow" -or
            $value.version_directory -isnot [string] -or [string]$value.version_directory -cnotmatch '^v[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}$' -or
            $value.version -isnot [string] -or [string]$value.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            $value.source_commit -isnot [string] -or [string]$value.source_commit -cnotmatch '^[0-9a-f]{40}$' -or
            $value.source_payload_sha256 -isnot [string] -or [string]$value.source_payload_sha256 -cnotmatch '^sha256:[0-9a-f]{64}$' -or
            $value.runtime_closure_manifest_sha256 -isnot [string] -or [string]$value.runtime_closure_manifest_sha256 -cnotmatch '^sha256:[0-9a-f]{64}$' -or
            $value.runtime_tree_sha256 -isnot [string] -or [string]$value.runtime_tree_sha256 -cnotmatch '^sha256:[0-9a-f]{64}$' -or
            $value.release_key_id -isnot [string] -or [string]$value.release_key_id -cne 'sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339' -or
            $value.bootstrap_version -isnot [string] -or [string]$value.bootstrap_version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            $value.platform -isnot [string] -or [string]$value.platform -cne "windows-x64") { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
        $payloadHex = ([string]$value.source_payload_sha256).Substring(7)
        if ([string]$value.version_directory -cne ("v" + [string]$value.version + "-" + $payloadHex.Substring(0, 12))) { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
        return @{ Value = $value; Lock = $stream }
    }
    catch {
        $stream.Dispose()
        throw "JOBFLOW_INSTALLED_POINTER_INVALID"
    }
}

function ConvertTo-JobFlowCanonicalJson([object]$Value) {
    if ($null -eq $Value) { return "null" }
    if ($Value -is [bool]) { if ($Value) { return "true" } else { return "false" } }
    if ($Value -is [string]) { return (ConvertTo-Json ([string]$Value) -Compress) }
    if ($Value -is [int] -or $Value -is [long]) {
        return ([long]$Value).ToString([Globalization.CultureInfo]::InvariantCulture)
    }
    if ($Value -is [PSCustomObject]) {
        $properties = @($Value.PSObject.Properties | Sort-Object -Property Name -CaseSensitive)
        $members = foreach ($property in $properties) {
            (ConvertTo-Json ([string]$property.Name) -Compress) + ":" + (ConvertTo-JobFlowCanonicalJson $property.Value)
        }
        return "{" + [string]::Join(",", [string[]]$members) + "}"
    }
    if ($Value -is [Collections.IEnumerable]) {
        $items = foreach ($item in $Value) { ConvertTo-JobFlowCanonicalJson $item }
        return "[" + [string]::Join(",", [string[]]$items) + "]"
    }
    throw "JOBFLOW_INSTALLED_POINTER_INVALID"
}

function Get-JobFlowCanonicalSha256([object]$Value) {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-JobFlowCanonicalJson $Value))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $raw = $hasher.ComputeHash($bytes) }
    finally { $hasher.Dispose() }
    return "sha256:" + (-join ($raw | ForEach-Object { $_.ToString("x2") }))
}

function Invoke-JobFlowInstalledTrustVerification {
    $bootstrapPath = Join-Path $PSScriptRoot "jobflow-bootstrap.ps1"
    Assert-JobFlowLocalPath $bootstrapPath
    try { (Open-JobFlowOrdinarySingleLinkFile $bootstrapPath "JOBFLOW_BOOTSTRAP_TRUST_ROOT_INVALID").Dispose() }
    catch { throw "JOBFLOW_BOOTSTRAP_TRUST_ROOT_INVALID" }
    $output = @(& $trustedWindowsPowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $bootstrapPath -VerifyInstalled 2>$null)
    if ($LASTEXITCODE -ne 0 -or $output.Count -ne 1) { throw "JOBFLOW_INSTALLED_TRUST_VERIFICATION_FAILED" }
    try { $verified = ([string]$output[0]) | ConvertFrom-Json }
    catch { throw "JOBFLOW_INSTALLED_TRUST_VERIFICATION_FAILED" }
    $expected = @(
        "activation_committed_during_recovery", "manifest_sha256", "paths_disclosed", "pointer_sha256",
        "real_external_actions", "recovery_performed", "release_key_id", "runtime_closure_manifest_sha256",
        "runtime_tree_sha256", "schema_version", "signed_activation_evidence_verified",
        "signature_envelope_sha256", "source_payload_sha256", "status", "version"
    ) | Sort-Object
    $actual = @($verified.PSObject.Properties.Name | Sort-Object)
    if (
        @((Compare-Object $expected $actual -SyncWindow 0)).Count -ne 0 -or
        ($verified.schema_version -isnot [int] -and $verified.schema_version -isnot [long]) -or
        [int64]$verified.schema_version -ne 1 -or
        [string]$verified.status -cne "JOBFLOW_INSTALLED_RUNTIME_VERIFIED" -or
        $verified.pointer_sha256 -isnot [string] -or [string]$verified.pointer_sha256 -cnotmatch '^sha256:[0-9a-f]{64}$' -or
        $verified.signed_activation_evidence_verified -isnot [bool] -or -not [bool]$verified.signed_activation_evidence_verified -or
        $verified.paths_disclosed -isnot [bool] -or [bool]$verified.paths_disclosed -or
        ($verified.real_external_actions -isnot [int] -and $verified.real_external_actions -isnot [long]) -or
        [int64]$verified.real_external_actions -ne 0
    ) { throw "JOBFLOW_INSTALLED_TRUST_VERIFICATION_FAILED" }
    return $verified
}

$lockHelpers = Join-Path $PSScriptRoot "jobflow-runtime-locks.ps1"
Assert-JobFlowLocalPath $lockHelpers
if (-not (Test-Path -LiteralPath $lockHelpers -PathType Leaf)) { throw "JOBFLOW_RUNTIME_LOCK_HELPERS_MISSING" }
. $lockHelpers

# Verify the signed activation evidence before taking the long-lived runtime
# lock.  The pointer is re-read and hash-bound under that lock below so an
# activation in this narrow gap cannot change which runtime is executed.
$verifiedInstallation = Invoke-JobFlowInstalledTrustVerification

$runtimeLockPath = Join-Path $dataRoot "state\.jobflow-runtime-maintenance.lock"
Assert-JobFlowLocalPath $runtimeLockPath
$runtimeLockStream = Enter-JobFlowFileLock $runtimeLockPath "JOBFLOW_ALREADY_RUNNING_OR_MAINTENANCE_ACTIVE"

try {
    Assert-JobFlowLocalPath $pointerPath
    Assert-JobFlowLocalPath $rollbackTransactionPath
    Assert-JobFlowLocalPath $rollbackTransactionBackupPath
    Assert-JobFlowLocalPath $dataRoot
    if (
        (Test-Path -LiteralPath $rollbackTransactionPath) -or
        (Test-Path -LiteralPath $rollbackTransactionBackupPath)
    ) {
        throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
    }
    if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
        throw "JobFlow 尚未完成固定目录安装。请重新运行 Install JobFlow.cmd。 / JobFlow is not installed in its fixed directory; run Install JobFlow.cmd again."
    }
    $pointerRecord = Read-JobFlowPointer $pointerPath
    $pointerFileLock = $pointerRecord.Lock
    $pointer = $pointerRecord.Value
    if ((Get-JobFlowCanonicalSha256 $pointer) -cne [string]$verifiedInstallation.pointer_sha256) {
        throw "JOBFLOW_INSTALLED_TRUST_CHANGED"
    }
    $versionDirectory = [string]$pointer.version_directory
    $versionRoot = [IO.Path]::GetFullPath((Join-Path $versionsRoot $versionDirectory))
    Assert-JobFlowLocalPath $versionRoot
    if (-not (Test-Path -LiteralPath $versionRoot -PathType Container)) { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
    $runtimePython = Join-Path $versionRoot "runtime\python.exe"
    Assert-JobFlowLocalPath $runtimePython
    $pythonFileLock = Open-JobFlowOrdinarySingleLinkFile $runtimePython "JOBFLOW_INSTALLED_RUNTIME_INVALID"
    try { (Open-JobFlowOrdinarySingleLinkFile (Join-Path $versionRoot ".jobops-root") "JOBFLOW_INSTALLED_MARKER_INVALID").Dispose() }
    catch { throw "JOBFLOW_INSTALLED_MARKER_INVALID" }
    try { (Open-JobFlowOrdinarySingleLinkFile (Join-Path $dataRoot ".jobflow-data-root") "JOBFLOW_INSTALLED_DATA_MARKER_INVALID").Dispose() }
    catch { throw "JOBFLOW_INSTALLED_DATA_MARKER_INVALID" }
    if (-not (Test-Path -LiteralPath $versionRoot -PathType Container)) {
        throw "JobFlow 固定安装不完整。请重新运行安装程序或回滚。 / The fixed JobFlow installation is incomplete; reinstall or roll back."
    }

    $env:JOBFLOW_DATA_ROOT = $dataRoot
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Push-Location $versionRoot
    try {
        $arguments = @("-m", "jobops.cli", "onboarding-center")
        if ($NoBrowser) { $arguments += "--no-browser" }
        & $runtimePython @arguments
        $jobflowExitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
    if ($jobflowExitCode -ne 0) {
        throw "JobFlow 未能正常启动（代码 $jobflowExitCode）。 / JobFlow stopped with exit code $jobflowExitCode."
    }
}
finally {
    if ($null -ne $pythonFileLock) { $pythonFileLock.Dispose() }
    if ($null -ne $pointerFileLock) { $pointerFileLock.Dispose() }
    Exit-JobFlowFileLock $runtimeLockStream
}
