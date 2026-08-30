[CmdletBinding(DefaultParameterSetName = "Acquire")]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(ParameterSetName = "Acquire")][switch]$AllowNetwork,
    [Parameter(ParameterSetName = "Verify")][switch]$VerifyOnly,
    [string]$PythonPath = "",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
trap {
    $code = [string]$_.Exception.Message
    if ($code -notmatch '^JOBFLOW_RUNTIME_INPUT_[A-Z0-9_]+$') {
        $code = "JOBFLOW_RUNTIME_INPUT_OPERATION_FAILED"
    }
    [Console]::Error.WriteLine($code)
    exit 1
}

$runningOnWindows = $PSVersionTable.PSEdition -eq "Desktop" -or
    [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
if (-not $runningOnWindows) { throw "JOBFLOW_RUNTIME_INPUT_WINDOWS_REQUIRED" }
if (-not $VerifyOnly -and -not $AllowNetwork) {
    throw "JOBFLOW_RUNTIME_INPUT_NETWORK_OPT_IN_REQUIRED"
}

$trustedSystemDirectory = [Environment]::SystemDirectory
$trustedWindowsRoot = [IO.Directory]::GetParent($trustedSystemDirectory).FullName
$env:SystemRoot = $trustedWindowsRoot
$securityModuleRoot = if ($PSVersionTable.PSEdition -eq "Desktop") {
    Join-Path $trustedSystemDirectory "WindowsPowerShell\v1.0\Modules"
}
else {
    Join-Path $PSHOME "Modules"
}
$securityModule = Join-Path $securityModuleRoot "Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
if (-not [IO.File]::Exists($securityModule)) {
    throw "JOBFLOW_RUNTIME_INPUT_POWERSHELL_MODULE_MISSING"
}
Import-Module -Name $securityModule -ErrorAction Stop

try {
    $expectedProject = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    $project = if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $expectedProject
    }
    else { [IO.Path]::GetFullPath($ProjectRoot) }
    $expectedPython = [IO.Path]::GetFullPath((Join-Path $expectedProject ".venv\Scripts\python.exe"))
    $python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        $expectedPython
    }
    else { [IO.Path]::GetFullPath($PythonPath) }
    $destinationPath = [IO.Path]::GetFullPath($Destination)
}
catch { throw "JOBFLOW_RUNTIME_INPUT_PATH_INVALID" }

if ($project -cne $expectedProject) {
    throw "JOBFLOW_RUNTIME_INPUT_PROJECT_INVALID"
}
if ($python -cne $expectedPython) {
    throw "JOBFLOW_RUNTIME_INPUT_PYTHON_UNBOUNDED"
}

function Assert-NotReparsePoint([string]$Path, [string]$FailureCode) {
    try { $attributes = [IO.File]::GetAttributes($Path) }
    catch { throw $FailureCode }
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw $FailureCode
    }
}

foreach ($directory in @(
    $project,
    (Join-Path $project ".venv"),
    (Join-Path $project ".venv\Scripts"),
    (Join-Path $project "config")
)) {
    if (-not [IO.Directory]::Exists($directory)) {
        throw "JOBFLOW_RUNTIME_INPUT_PROJECT_INVALID"
    }
    Assert-NotReparsePoint $directory "JOBFLOW_RUNTIME_INPUT_PROJECT_UNSAFE"
}
$markerPath = Join-Path $project ".jobops-root"
if (-not [IO.File]::Exists($markerPath)) {
    throw "JOBFLOW_RUNTIME_INPUT_PROJECT_INVALID"
}
Assert-NotReparsePoint $markerPath "JOBFLOW_RUNTIME_INPUT_PROJECT_UNSAFE"
if (-not [IO.File]::Exists($python) -or [IO.Path]::GetFileName($python) -cne "python.exe") {
    throw "JOBFLOW_RUNTIME_INPUT_PYTHON_INVALID"
}
Assert-NotReparsePoint $python "JOBFLOW_RUNTIME_INPUT_PYTHON_UNSAFE"

if ($null -eq ("JobFlowRuntimeInputFileApi" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public sealed class JobFlowRuntimeInputFileIdentity
{
    public UInt32 VolumeSerialNumber { get; set; }
    public UInt32 FileIndexHigh { get; set; }
    public UInt32 FileIndexLow { get; set; }
    public UInt32 NumberOfLinks { get; set; }
}

public static class JobFlowRuntimeInputFileApi
{
    [StructLayout(LayoutKind.Sequential)]
    private struct FILETIME { public UInt32 Low; public UInt32 High; }

    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION
    {
        public UInt32 FileAttributes;
        public FILETIME CreationTime;
        public FILETIME LastAccessTime;
        public FILETIME LastWriteTime;
        public UInt32 VolumeSerialNumber;
        public UInt32 FileSizeHigh;
        public UInt32 FileSizeLow;
        public UInt32 NumberOfLinks;
        public UInt32 FileIndexHigh;
        public UInt32 FileIndexLow;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle,
        out BY_HANDLE_FILE_INFORMATION information
    );

    public static JobFlowRuntimeInputFileIdentity Inspect(SafeFileHandle handle)
    {
        BY_HANDLE_FILE_INFORMATION information;
        if (!GetFileInformationByHandle(handle, out information))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return new JobFlowRuntimeInputFileIdentity {
            VolumeSerialNumber = information.VolumeSerialNumber,
            FileIndexHigh = information.FileIndexHigh,
            FileIndexLow = information.FileIndexLow,
            NumberOfLinks = information.NumberOfLinks
        };
    }
}
'@
}

function Get-StreamIdentity([IO.FileStream]$Stream) {
    try { return [JobFlowRuntimeInputFileApi]::Inspect($Stream.SafeFileHandle) }
    catch { throw "JOBFLOW_RUNTIME_INPUT_FILE_IDENTITY_INVALID" }
}

function Get-StreamSha256([IO.FileStream]$Stream) {
    $originalPosition = $Stream.Position
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $Stream.Position = 0
        $digest = $sha.ComputeHash($Stream)
        return "sha256:" + [BitConverter]::ToString($digest).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $Stream.Position = $originalPosition
        $sha.Dispose()
    }
}

function Assert-RetainedFile([object]$Retained, [switch]$VerifyHash) {
    $identity = Get-StreamIdentity $Retained.Stream
    if (
        [uint32]$identity.VolumeSerialNumber -ne [uint32]$Retained.Identity.VolumeSerialNumber -or
        [uint32]$identity.FileIndexHigh -ne [uint32]$Retained.Identity.FileIndexHigh -or
        [uint32]$identity.FileIndexLow -ne [uint32]$Retained.Identity.FileIndexLow -or
        [uint32]$identity.NumberOfLinks -ne 1
    ) { throw "JOBFLOW_RUNTIME_INPUT_FILE_IDENTITY_CHANGED" }
    if ($VerifyHash -and (Get-StreamSha256 $Retained.Stream) -cne [string]$Retained.Sha256) {
        throw "JOBFLOW_RUNTIME_INPUT_FILE_CHANGED"
    }
}

function Open-RetainedFile([string]$Path, [long]$MaximumBytes, [string]$FailureCode) {
    if (-not [IO.File]::Exists($Path)) { throw $FailureCode }
    Assert-NotReparsePoint $Path $FailureCode
    try {
        $stream = [IO.File]::Open(
            $Path,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
    }
    catch { throw $FailureCode }
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $MaximumBytes) { throw $FailureCode }
        $identity = Get-StreamIdentity $stream
        if ([uint32]$identity.NumberOfLinks -ne 1) { throw $FailureCode }
        return [pscustomobject]@{
            Stream = $stream
            Identity = $identity
            Sha256 = Get-StreamSha256 $stream
        }
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Read-RetainedUtf8Json([object]$Retained, [string]$FailureCode) {
    Assert-RetainedFile $Retained -VerifyHash
    $Retained.Stream.Position = 0
    $reader = New-Object IO.StreamReader(
        $Retained.Stream,
        (New-Object Text.UTF8Encoding($false, $true)),
        $true,
        4096,
        $true
    )
    try {
        $text = $reader.ReadToEnd()
        return $text | ConvertFrom-Json
    }
    catch { throw $FailureCode }
    finally { $reader.Dispose() }
}

function Assert-ExactProperties([object]$Value, [string[]]$Expected, [string]$FailureCode) {
    if ($null -eq $Value) { throw $FailureCode }
    $actual = @($Value.PSObject.Properties | ForEach-Object { [string]$_.Name })
    if ($actual.Count -ne $Expected.Count) { throw $FailureCode }
    foreach ($name in $Expected) {
        if ($actual -cnotcontains $name) { throw $FailureCode }
    }
}

$policyPath = Join-Path $project "config\release-toolchain.json"
$retainedPython = $null
$retainedPolicy = $null
try {
    $retainedPython = Open-RetainedFile $python 536870912 "JOBFLOW_RUNTIME_INPUT_PYTHON_INVALID"
    $retainedPolicy = Open-RetainedFile $policyPath 1048576 "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
    $trust = Read-RetainedUtf8Json $retainedPolicy "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
    Assert-ExactProperties $trust @("schema_version", "tools", "python_execution_runtime", "javascript_dependencies") "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
    Assert-ExactProperties $trust.tools @("node", "git", "python") "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
    $pythonPolicy = $trust.tools.python
    Assert-ExactProperties $pythonPolicy @("file_names", "allowed_signers", "allowed_unsigned_sha256") "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
    if ([int]$trust.schema_version -ne 1 -or @($pythonPolicy.file_names) -cnotcontains "python.exe") {
        throw "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
    }
    foreach ($signer in @($pythonPolicy.allowed_signers)) {
        Assert-ExactProperties $signer @("subject", "thumbprint") "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
        if (
            [string]::IsNullOrWhiteSpace([string]$signer.subject) -or
            [string]$signer.thumbprint -cnotmatch '^[0-9A-F]{40}$'
        ) { throw "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID" }
    }
    foreach ($digest in @($pythonPolicy.allowed_unsigned_sha256)) {
        if ([string]$digest -cnotmatch '^sha256:[0-9a-f]{64}$') {
            throw "JOBFLOW_RUNTIME_INPUT_TRUST_CONFIG_INVALID"
        }
    }

    Assert-RetainedFile $retainedPython -VerifyHash
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $python
    $trusted = $false
    if ([string]$signature.Status -eq "Valid" -and $null -ne $signature.SignerCertificate) {
        $subject = [string]$signature.SignerCertificate.Subject
        $thumbprint = ([string]$signature.SignerCertificate.Thumbprint).ToUpperInvariant()
        foreach ($signer in @($pythonPolicy.allowed_signers)) {
            if (
                [string]$signer.subject -ceq $subject -and
                ([string]$signer.thumbprint).ToUpperInvariant() -ceq $thumbprint
            ) { $trusted = $true; break }
        }
    }
    if (-not $trusted -and @($pythonPolicy.allowed_unsigned_sha256) -ccontains [string]$retainedPython.Sha256) {
        $trusted = $true
    }
    if (-not $trusted) { throw "JOBFLOW_RUNTIME_INPUT_PYTHON_TRUST_INVALID" }

    $arguments = @("-I", "-m", "jobops.runtime_inputs", "--project", $project)
    if ($VerifyOnly) {
        $arguments += @("verify", "--bundle", $destinationPath)
    }
    else {
        $arguments += @("acquire", "--destination", $destinationPath, "--allow-network")
    }

    & $python @arguments
    $operationExitCode = [int]$LASTEXITCODE
    Assert-RetainedFile $retainedPython -VerifyHash
    Assert-RetainedFile $retainedPolicy -VerifyHash
    if ($operationExitCode -ne 0) {
        throw "JOBFLOW_RUNTIME_INPUT_OPERATION_FAILED"
    }
}
finally {
    if ($null -ne $retainedPolicy) { $retainedPolicy.Stream.Dispose() }
    if ($null -ne $retainedPython) { $retainedPython.Stream.Dispose() }
}
