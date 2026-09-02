[CmdletBinding()]
param(
    [switch]$NoLaunch,
    [string]$ArchivePath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Net.Http

$expectedRepository = "ValerianXXX/JobFlow"
$expectedApiUrl = "https://api.github.com/repos/ValerianXXX/JobFlow/releases/latest"
$expectedKeyId = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
$manifestAssetName = "JobFlow-update-manifest.json"
$signatureAssetName = "JobFlow-update-manifest.sig.json"
$allowedDownloadHosts = @("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
$maxReleaseBytes = 2MB
$maxManifestBytes = 64KB
$maxSignatureBytes = 16KB
$maxArchiveBytes = 1536MB
$maxBootstrapBytes = 2MB
$maxBootstrapOutputBytes = 128KB
$maxInstallerJournalBytes = 256KB

$stableControlPlaneFiles = @(
    "jobflow-bootstrap.ps1",
    "start-installed-jobflow.ps1",
    "check-installed-jobflow.ps1",
    "update-installed-jobflow.ps1",
    "rollback-installed-jobflow.ps1",
    "uninstall-installed-jobflow.ps1",
    "jobflow-runtime-locks.ps1",
    "manage-authorized-discovery-task.ps1",
    "run-authorized-discovery-task.ps1",
    "Start JobFlow.cmd",
    "Check JobFlow.cmd",
    "Update JobFlow.cmd",
    "Rollback JobFlow.cmd",
    "Uninstall JobFlow.cmd"
)

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND" }
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$projectMarkerPath = Join-Path $projectRoot ".jobops-root"
$stableSourceRoot = Join-Path $projectRoot "scripts\windows-runtime"
$bootstrapPath = Join-Path $stableSourceRoot "jobflow-bootstrap.ps1"
if (-not (Test-Path -LiteralPath $projectMarkerPath -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
$localAppDataRoot = (Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path
# Helpers are rooted at Local AppData.  The installer owns only the explicit
# JobFlowInstaller state directory and JobOps\bin; it never enumerates or
# recursively removes any other pre-existing directory.
$localRoot = $localAppDataRoot
$jobOpsRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobOps"))
$currentPointerPath = Join-Path $jobOpsRoot "current.json"
$installerStateRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobFlowInstaller"))
$installerJournalPath = Join-Path $installerStateRoot "install-journal.json"
$updateCoordinatorPath = Join-Path $installerStateRoot ".install-v2.lock"
$updateId = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$stagingRoot = Join-Path $installerStateRoot (".jfi-" + $updateId)
$acceptanceMode = $env:JOBFLOW_INSTALL_V2_ACCEPTANCE_CORE_ONLY -eq "1"
$acceptanceFixtureRoot = $null
if ($acceptanceMode) {
    # Older .NET Framework builds may preserve the trailing separator when a
    # path ending in ".." is canonicalized.  Trim it before GetFileName so the
    # same exact QA-root check behaves consistently on hosted Windows runners.
    $qaRoot = ([IO.Path]::GetFullPath((Join-Path $projectRoot ".."))).TrimEnd(
        [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    )
    $expectedLocalAppData = [IO.Path]::GetFullPath((Join-Path $qaRoot "LocalAppData"))
    # Windows runners may expose the same temporary directory through a long
    # path in one environment variable and an 8.3 alias after Resolve-Path.
    # Resolve both sides before comparing so the acceptance-only guard still
    # requires the exact fixture directory without rejecting path aliases.
    $expectedLocalAppDataResolved = (Resolve-Path -LiteralPath $expectedLocalAppData).Path
    if (
        ([IO.Path]::GetFileName($qaRoot)) -notmatch '^jobflow-v2-install-qa-[0-9a-f]{8,32}$' -or
        -not $localAppDataRoot.Equals($expectedLocalAppDataResolved, [StringComparison]::OrdinalIgnoreCase)
    ) { throw "JOBFLOW_INSTALL_V2_ACCEPTANCE_BYPASS_FORBIDDEN" }
    $acceptanceFixtureRoot = [IO.Path]::GetFullPath((Join-Path $qaRoot "fixture"))
}
$archiveIdentityLock = $null
$powerShellExecutableLock = $null
$updateCoordinatorLock = $null
$releaseMetadataLock = $null
$manifestMetadataLock = $null
$signatureMetadataLock = $null
$stagingDirectoryContext = $null
$installerStateContext = $null
$bootstrapSource = $null
$scriptExitCode = 1
$scriptErrorCode = $null
$stableControlPlaneInstalled = $false
$localArchiveMode = -not [string]::IsNullOrWhiteSpace($ArchivePath)
$localArchiveInputPath = $ArchivePath
$localArchiveDirectoryContext = $null
$localManifestInputLock = $null
$localSignatureInputLock = $null
$localArchiveInputLock = $null

function Get-TrustedWindowsPowerShell {
    $systemDirectory = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
    $candidate = [IO.Path]::GetFullPath((Join-Path $systemDirectory "WindowsPowerShell\v1.0\powershell.exe"))
    $prefix = $systemDirectory.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $candidate -PathType Leaf)
    ) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
    $item = Get-Item -LiteralPath $candidate -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
    }
    Assert-NoAlternateDataStreams $candidate "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
    $lock = [IO.File]::Open($candidate, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $securityModule = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
    try {
        # Windows system binaries legitimately have WinSxS hard links.  Pin the
        # exact opened identity and require Microsoft's signature instead of
        # imposing the single-link rule used for updater-owned inputs.
        $identity = Get-OpenUpdaterFileIdentity $lock "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -ne 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [long]$identity.LinkCount -lt 1 -or
            -not (Get-UpdaterHandleFinalPath $lock.SafeFileHandle "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED").Equals(
                $candidate, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
        Microsoft.PowerShell.Core\Import-Module -Name $securityModule -Force -ErrorAction Stop
        $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $candidate
        if (
            [string]$signature.Status -cne "Valid" -or
            $null -eq $signature.SignerCertificate -or
            [string]$signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)'
        ) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
        return @{ Path = $candidate; Lock = $lock }
    }
    catch {
        $lock.Dispose()
        throw
    }
}

function Assert-JobFlowLocalPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -ne $localRoot -and -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_UPDATE_PATH_FORBIDDEN"
    }
    $cursor = $absolute
    while ($cursor -and ($cursor -eq $localRoot -or $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase))) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_UPDATE_REPARSE_FORBIDDEN"
            }
        }
        if ($cursor -eq $localRoot) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Initialize-JobFlowUpdaterFileIdentityApi {
    if ($null -ne ("JobFlowUpdaterNative.FileIdentity" -as [type])) { return }
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;
namespace JobFlowUpdaterNative {
    [StructLayout(LayoutKind.Sequential)] public struct FileTime { public uint Low; public uint High; }
    [StructLayout(LayoutKind.Sequential)] public struct FileIdentity {
        public uint Attributes; public FileTime CreationTime; public FileTime LastAccessTime;
        public FileTime LastWriteTime; public uint VolumeSerialNumber; public uint SizeHigh;
        public uint SizeLow; public uint LinkCount; public uint FileIndexHigh; public uint FileIndexLow;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct UnicodeString {
        public ushort Length; public ushort MaximumLength; public System.IntPtr Buffer;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct ObjectAttributes {
        public int Length; public System.IntPtr RootDirectory; public System.IntPtr ObjectName;
        public uint Attributes; public System.IntPtr SecurityDescriptor; public System.IntPtr SecurityQualityOfService;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct IoStatusBlock {
        public System.IntPtr Status; public System.IntPtr Information;
    }
    public static class FileIdentityApi {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern SafeFileHandle CreateFileW(
            string path, uint desiredAccess, uint shareMode, System.IntPtr securityAttributes,
            uint creationDisposition, uint flagsAndAttributes, System.IntPtr templateFile
        );
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern bool CreateDirectoryW(string path, System.IntPtr securityAttributes);
        [DllImport("kernel32.dll", SetLastError=true)]
        public static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileIdentity information);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern uint GetFinalPathNameByHandleW(
            SafeFileHandle handle, StringBuilder path, uint capacity, uint flags
        );
        [DllImport("ntdll.dll")]
        private static extern int NtCreateFile(
            out System.IntPtr fileHandle, uint desiredAccess, ref ObjectAttributes objectAttributes,
            out IoStatusBlock ioStatusBlock, System.IntPtr allocationSize, uint fileAttributes,
            uint shareAccess, uint createDisposition, uint createOptions,
            System.IntPtr eaBuffer, uint eaLength
        );
        private static SafeFileHandle CreateNewRelative(
            SafeFileHandle parent, string name, uint desiredAccess, uint shareAccess,
            uint fileAttributes, uint createOptions
        ) {
            if (parent == null || parent.IsInvalid || parent.IsClosed || string.IsNullOrEmpty(name) ||
                name.IndexOfAny(new char[] {'\\', '/', ':'}) >= 0) {
                return new SafeFileHandle(System.IntPtr.Zero, true);
            }
            System.IntPtr buffer = System.IntPtr.Zero;
            System.IntPtr unicodePointer = System.IntPtr.Zero;
            bool addedRef = false;
            try {
                byte[] nameBytes = Encoding.Unicode.GetBytes(name);
                if (nameBytes.Length < 2 || nameBytes.Length > 32766) return new SafeFileHandle(System.IntPtr.Zero, true);
                buffer = Marshal.StringToHGlobalUni(name);
                UnicodeString unicode = new UnicodeString {
                    Length = (ushort)nameBytes.Length, MaximumLength = (ushort)(nameBytes.Length + 2), Buffer = buffer
                };
                unicodePointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UnicodeString)));
                Marshal.StructureToPtr(unicode, unicodePointer, false);
                parent.DangerousAddRef(ref addedRef);
                ObjectAttributes attributes = new ObjectAttributes {
                    Length = Marshal.SizeOf(typeof(ObjectAttributes)), RootDirectory = parent.DangerousGetHandle(),
                    ObjectName = unicodePointer, Attributes = 0x40, SecurityDescriptor = System.IntPtr.Zero,
                    SecurityQualityOfService = System.IntPtr.Zero
                };
                IoStatusBlock io; System.IntPtr raw;
                int status = NtCreateFile(out raw, desiredAccess, ref attributes, out io, System.IntPtr.Zero,
                    fileAttributes, shareAccess, 2, createOptions, System.IntPtr.Zero, 0);
                if (status != 0 || raw == System.IntPtr.Zero || raw == new System.IntPtr(-1)) {
                    return new SafeFileHandle(System.IntPtr.Zero, true);
                }
                return new SafeFileHandle(raw, true);
            }
            finally {
                if (addedRef) parent.DangerousRelease();
                if (unicodePointer != System.IntPtr.Zero) Marshal.FreeHGlobal(unicodePointer);
                if (buffer != System.IntPtr.Zero) Marshal.FreeHGlobal(buffer);
            }
        }
        public static SafeFileHandle CreateNewFileRelative(SafeFileHandle parent, string name, uint shareAccess) {
            return CreateNewRelative(parent, name, 0x00100183, shareAccess, 0x80, 0x60);
        }
        public static SafeFileHandle CreateNewDirectoryRelative(SafeFileHandle parent, string name) {
            return CreateNewRelative(parent, name, 0x00100081, 0x3, 0x10, 0x21);
        }
    }
    public static class JsonApi {
        private sealed class Parser {
            private readonly string text;
            private int index;
            internal Parser(string value) { text = value; }
            private void Fail() { throw new InvalidDataException("JOBFLOW_UPDATE_JSON_INVALID"); }
            private void White() {
                while (index < text.Length && (text[index] == ' ' || text[index] == '\t' ||
                    text[index] == '\r' || text[index] == '\n')) index++;
            }
            private bool Take(char value) {
                White();
                if (index < text.Length && text[index] == value) { index++; return true; }
                return false;
            }
            private void Literal(string value) {
                if (index + value.Length > text.Length ||
                    String.CompareOrdinal(text, index, value, 0, value.Length) != 0) Fail();
                index += value.Length;
            }
            private int Hex(char value) {
                if (value >= '0' && value <= '9') return value - '0';
                if (value >= 'a' && value <= 'f') return value - 'a' + 10;
                if (value >= 'A' && value <= 'F') return value - 'A' + 10;
                Fail(); return 0;
            }
            private string StringValue() {
                White();
                if (index >= text.Length || text[index++] != '"') Fail();
                var result = new StringBuilder();
                while (index < text.Length) {
                    char value = text[index++];
                    if (value == '"') return result.ToString();
                    if (value < 0x20) Fail();
                    if (value != '\\') { result.Append(value); continue; }
                    if (index >= text.Length) Fail();
                    char escape = text[index++];
                    if (escape == '"' || escape == '\\' || escape == '/') result.Append(escape);
                    else if (escape == 'b') result.Append('\b');
                    else if (escape == 'f') result.Append('\f');
                    else if (escape == 'n') result.Append('\n');
                    else if (escape == 'r') result.Append('\r');
                    else if (escape == 't') result.Append('\t');
                    else if (escape == 'u') {
                        if (index + 4 > text.Length) Fail();
                        int code = 0;
                        for (int item = 0; item < 4; item++) code = (code << 4) | Hex(text[index++]);
                        char first = (char)code;
                        if (Char.IsHighSurrogate(first)) {
                            if (index + 6 > text.Length || text[index++] != '\\' || text[index++] != 'u') Fail();
                            int lowCode = 0;
                            for (int item = 0; item < 4; item++) lowCode = (lowCode << 4) | Hex(text[index++]);
                            char low = (char)lowCode;
                            if (!Char.IsLowSurrogate(low)) Fail();
                            result.Append(first); result.Append(low);
                        }
                        else {
                            if (Char.IsLowSurrogate(first)) Fail();
                            result.Append(first);
                        }
                    }
                    else Fail();
                }
                Fail(); return null;
            }
            private void Number() {
                if (index < text.Length && text[index] == '-') index++;
                if (index >= text.Length) Fail();
                if (text[index] == '0') index++;
                else {
                    if (text[index] < '1' || text[index] > '9') Fail();
                    while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
                }
                if (index < text.Length && text[index] == '.') {
                    index++; int start = index;
                    while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
                    if (index == start) Fail();
                }
                if (index < text.Length && (text[index] == 'e' || text[index] == 'E')) {
                    index++;
                    if (index < text.Length && (text[index] == '+' || text[index] == '-')) index++;
                    int start = index;
                    while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
                    if (index == start) Fail();
                }
            }
            private void Value(int depth) {
                if (depth > 64) Fail();
                White(); if (index >= text.Length) Fail();
                char value = text[index];
                if (value == '{') ObjectValue(depth + 1);
                else if (value == '[') ArrayValue(depth + 1);
                else if (value == '"') StringValue();
                else if (value == 't') Literal("true");
                else if (value == 'f') Literal("false");
                else if (value == 'n') Literal("null");
                else if (value == '-' || (value >= '0' && value <= '9')) Number();
                else Fail();
            }
            private void ObjectValue(int depth) {
                if (!Take('{')) Fail();
                var names = new HashSet<string>(StringComparer.Ordinal);
                White(); if (Take('}')) return;
                while (true) {
                    string name = StringValue();
                    if (!names.Add(name) || !Take(':')) Fail();
                    Value(depth);
                    if (Take('}')) return;
                    if (!Take(',')) Fail();
                }
            }
            private void ArrayValue(int depth) {
                if (!Take('[')) Fail();
                White(); if (Take(']')) return;
                while (true) {
                    Value(depth);
                    if (Take(']')) return;
                    if (!Take(',')) Fail();
                }
            }
            internal void Parse() { Value(0); White(); if (index != text.Length) Fail(); }
        }
        public static void AssertNoDuplicateProperties(string value) {
            if (value == null) throw new InvalidDataException("JOBFLOW_UPDATE_JSON_INVALID");
            new Parser(value).Parse();
        }
    }
}
"@ -ErrorAction Stop
}

function Get-OpenUpdaterFileIdentity([IO.FileStream]$Stream, [string]$Code) {
    Initialize-JobFlowUpdaterFileIdentityApi
    $information = New-Object JobFlowUpdaterNative.FileIdentity
    if (-not [JobFlowUpdaterNative.FileIdentityApi]::GetFileInformationByHandle(
        $Stream.SafeFileHandle, [ref]$information
    )) { throw $Code }
    return $information
}

function Assert-OpenUpdaterSingleLink([IO.FileStream]$Stream, [string]$Code) {
    $identity = Get-OpenUpdaterFileIdentity $Stream $Code
    if ([long]$identity.LinkCount -ne 1) { throw $Code }
}

function Get-UpdaterHandleFinalPath(
    [Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
    [string]$Code
) {
    Initialize-JobFlowUpdaterFileIdentityApi
    $builder = [Text.StringBuilder]::new(32768)
    $length = [JobFlowUpdaterNative.FileIdentityApi]::GetFinalPathNameByHandleW(
        $Handle, $builder, [uint32]$builder.Capacity, 0
    )
    if ($length -lt 1 -or $length -ge $builder.Capacity) { throw $Code }
    $value = $builder.ToString()
    if ($value.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        $value = '\\' + $value.Substring(8)
    }
    elseif ($value.StartsWith('\\?\', [StringComparison]::OrdinalIgnoreCase)) {
        $value = $value.Substring(4)
    }
    return [IO.Path]::GetFullPath($value)
}

function Open-StableUpdaterDirectoryHandle([string]$Path, [string]$Code) {
    Initialize-JobFlowUpdaterFileIdentityApi
    $absolute = [IO.Path]::GetFullPath($Path)
    # No FILE_SHARE_DELETE: the verified directory identity cannot be renamed,
    # deleted, or exchanged for a junction while the staging write is open.
    $handle = [JobFlowUpdaterNative.FileIdentityApi]::CreateFileW(
        $absolute, 0x80, 0x3, [IntPtr]::Zero, 3, (0x02000000 -bor 0x00200000), [IntPtr]::Zero
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $identity = New-Object JobFlowUpdaterNative.FileIdentity
        if (-not [JobFlowUpdaterNative.FileIdentityApi]::GetFileInformationByHandle($handle, [ref]$identity)) {
            throw $Code
        }
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not (Get-UpdaterHandleFinalPath $handle $Code).Equals(
                $absolute, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
        return [pscustomobject]@{
            Path = $absolute
            Handle = $handle
            Volume = [uint32]$identity.VolumeSerialNumber
            IndexHigh = [uint32]$identity.FileIndexHigh
            IndexLow = [uint32]$identity.FileIndexLow
        }
    }
    catch {
        $handle.Dispose()
        throw
    }
}

function Close-StableUpdaterDirectoryContext([object]$Context) {
    if ($null -eq $Context -or $null -eq $Context.Locks) { return }
    for ($index = @($Context.Locks).Count - 1; $index -ge 0; $index--) {
        $lock = @($Context.Locks)[$index]
        if ($null -ne $lock -and $null -ne $lock.Handle) { $lock.Handle.Dispose() }
    }
}

function Assert-StableUpdaterDirectoryContext([object]$Context, [string]$Code) {
    if ($null -eq $Context -or @($Context.Locks).Count -lt 1) { throw $Code }
    foreach ($lock in @($Context.Locks)) {
        if ($null -eq $lock.Handle -or $lock.Handle.IsInvalid -or $lock.Handle.IsClosed) { throw $Code }
        $identity = New-Object JobFlowUpdaterNative.FileIdentity
        if (-not [JobFlowUpdaterNative.FileIdentityApi]::GetFileInformationByHandle($lock.Handle, [ref]$identity)) {
            throw $Code
        }
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$identity.VolumeSerialNumber -ne [uint32]$lock.Volume -or
            [uint32]$identity.FileIndexHigh -ne [uint32]$lock.IndexHigh -or
            [uint32]$identity.FileIndexLow -ne [uint32]$lock.IndexLow -or
            -not (Get-UpdaterHandleFinalPath $lock.Handle $Code).Equals(
                [string]$lock.Path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
    }
}

function New-UpdaterDirectoryRelativeToLock([object]$ParentLock, [string]$Name, [string]$Code) {
    if ($null -eq $ParentLock -or $null -eq $ParentLock.Handle) { throw $Code }
    $handle = [JobFlowUpdaterNative.FileIdentityApi]::CreateNewDirectoryRelative($ParentLock.Handle, $Name)
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $identity = New-Object JobFlowUpdaterNative.FileIdentity
        if (-not [JobFlowUpdaterNative.FileIdentityApi]::GetFileInformationByHandle($handle, [ref]$identity)) { throw $Code }
        $expected = [IO.Path]::GetFullPath((Join-Path ([string]$ParentLock.Path) $Name))
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not (Get-UpdaterHandleFinalPath $handle $Code).Equals($expected, [StringComparison]::OrdinalIgnoreCase)
        ) { throw $Code }
    }
    finally { $handle.Dispose() }
}

function Open-NewUpdaterFileRelative([object]$ParentContext, [string]$Destination, [string]$Code, [uint32]$ShareAccess) {
    Assert-StableUpdaterDirectoryContext $ParentContext $Code
    $locks = @($ParentContext.Locks)
    $parentLock = $locks[$locks.Count - 1]
    $absolute = [IO.Path]::GetFullPath($Destination)
    if (-not [IO.Path]::GetDirectoryName($absolute).Equals([string]$parentLock.Path, [StringComparison]::OrdinalIgnoreCase)) {
        throw $Code
    }
    $handle = [JobFlowUpdaterNative.FileIdentityApi]::CreateNewFileRelative(
        $parentLock.Handle, [IO.Path]::GetFileName($absolute), $ShareAccess
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $stream = [IO.FileStream]::new($handle, [IO.FileAccess]::ReadWrite)
        $handle = $null
        Assert-OpenUpdaterFileAtPath $stream $absolute $Code
        Assert-StableUpdaterDirectoryContext $ParentContext $Code
        return $stream
    }
    catch {
        if ($null -ne $handle) { $handle.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        throw
    }
}

function Open-StableUpdaterDirectoryChain(
    [string]$Path,
    [string]$Code,
    [switch]$CreateMissing
) {
    Initialize-JobFlowUpdaterFileIdentityApi
    $absolute = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($absolute)
    if ([string]::IsNullOrWhiteSpace($root)) { throw $Code }
    $paths = [System.Collections.Generic.List[string]]::new()
    [void]$paths.Add($root)
    $cursor = $root
    $relative = $absolute.Substring($root.Length).TrimEnd('\')
    if (-not [string]::IsNullOrWhiteSpace($relative)) {
        foreach ($component in $relative.Split('\')) {
            if ([string]::IsNullOrWhiteSpace($component) -or $component -eq '.' -or $component -eq '..') { throw $Code }
            $cursor = Join-Path $cursor $component
            [void]$paths.Add([IO.Path]::GetFullPath($cursor))
        }
    }
    $locks = [System.Collections.Generic.List[object]]::new()
    try {
        foreach ($candidate in $paths) {
            if (-not [IO.Directory]::Exists($candidate)) {
                if (-not $CreateMissing) { throw $Code }
                if ($locks.Count -lt 1) { throw $Code }
                New-UpdaterDirectoryRelativeToLock $locks[$locks.Count - 1] ([IO.Path]::GetFileName($candidate)) $Code
            }
            [void]$locks.Add((Open-StableUpdaterDirectoryHandle $candidate $Code))
        }
        $context = [pscustomobject]@{ Path = $absolute; Locks = @($locks.ToArray()) }
        Assert-StableUpdaterDirectoryContext $context $Code
        return $context
    }
    catch {
        foreach ($lock in @($locks)) { if ($null -ne $lock.Handle) { $lock.Handle.Dispose() } }
        throw
    }
}

function New-StableUpdaterDirectoryRoot([string]$Path, [string]$Code) {
    Initialize-JobFlowUpdaterFileIdentityApi
    $absolute = [IO.Path]::GetFullPath($Path)
    $parentContext = Open-StableUpdaterDirectoryChain ([IO.Path]::GetDirectoryName($absolute)) $Code
    try {
        Assert-StableUpdaterDirectoryContext $parentContext $Code
        $parentLocks = @($parentContext.Locks)
        New-UpdaterDirectoryRelativeToLock $parentLocks[$parentLocks.Count - 1] ([IO.Path]::GetFileName($absolute)) $Code
        return (Open-StableUpdaterDirectoryChain $absolute $Code)
    }
    finally { Close-StableUpdaterDirectoryContext $parentContext }
}

function Assert-OpenUpdaterFileAtPath([IO.FileStream]$Stream, [string]$Path, [string]$Code) {
    $identity = Get-OpenUpdaterFileIdentity $Stream $Code
    if (
        ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -ne 0 -or
        ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [long]$identity.LinkCount -ne 1 -or
        -not (Get-UpdaterHandleFinalPath $Stream.SafeFileHandle $Code).Equals(
            [IO.Path]::GetFullPath($Path), [StringComparison]::OrdinalIgnoreCase
        )
    ) { throw $Code }
}

function Assert-NoAlternateDataStreams([string]$Path, [string]$Code) {
    try { $streams = @(Get-Item -LiteralPath $Path -Stream * -ErrorAction Stop) }
    catch { throw $Code }
    if ($streams.Count -ne 1 -or [string]$streams[0].Stream -cne ':$DATA') { throw $Code }
}

function Read-LockedBytes([IO.FileStream]$Stream, [long]$MaximumBytes, [string]$Code) {
    if ($null -eq $Stream -or $Stream.Length -lt 1 -or $Stream.Length -gt $MaximumBytes) { throw $Code }
    Assert-OpenUpdaterSingleLink $Stream $Code
    $expectedLength = [long]$Stream.Length
    $Stream.Position = 0
    $memory = [IO.MemoryStream]::new()
    try {
        $buffer = New-Object byte[] 65536
        [long]$total = 0
        while (($read = $Stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $total += $read
            if ($total -gt $MaximumBytes) { throw $Code }
            $memory.Write($buffer, 0, $read)
        }
        if ($total -ne $expectedLength -or $Stream.Length -ne $expectedLength) { throw $Code }
        $Stream.Position = 0
        return $memory.ToArray()
    }
    finally { $memory.Dispose() }
}

function Read-LockedJson(
    [IO.FileStream]$Stream,
    [long]$MaximumBytes,
    [string]$Code
) {
    $bytes = Read-LockedBytes $Stream $MaximumBytes $Code
    try {
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $text = $utf8.GetString($bytes).TrimStart([char]0xFEFF)
        [JobFlowUpdaterNative.JsonApi]::AssertNoDuplicateProperties($text)
        $value = $text | ConvertFrom-Json
        if ($null -eq $value) { throw $Code }
        return $value
    }
    catch { throw $Code }
}

function Open-LockedUpdaterFile([string]$Path, [long]$MaximumBytes, [string]$Code) {
    Assert-JobFlowLocalPath $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Code }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
    Assert-NoAlternateDataStreams $Path $Code
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        [void](Read-LockedBytes $stream $MaximumBytes $Code)
        return $stream
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Get-OpenStreamSha256([IO.FileStream]$Stream) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Stream.Position = 0
        $bytes = $hasher.ComputeHash($Stream)
        $Stream.Position = 0
        return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally { $hasher.Dispose() }
}

function Assert-InstallerSourcePath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($projectRoot)
    $prefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
        -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_INSTALL_SOURCE_PATH_FORBIDDEN"
    }
    $cursor = $absolute
    while ($true) {
        if (-not (Test-Path -LiteralPath $cursor)) { throw "JOBFLOW_INSTALL_SOURCE_MISSING" }
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_INSTALL_SOURCE_REPARSE_FORBIDDEN"
        }
        if ($cursor.Equals($root, [StringComparison]::OrdinalIgnoreCase)) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($cursor)) { throw "JOBFLOW_INSTALL_SOURCE_PATH_FORBIDDEN" }
    }
}

function Open-LockedInstallerSourceFile([string]$Path, [long]$MaximumBytes, [string]$Code) {
    Assert-InstallerSourcePath $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Code }
    Assert-NoAlternateDataStreams $Path $Code
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        Assert-OpenUpdaterFileAtPath $stream $Path $Code
        [void](Read-LockedBytes $stream $MaximumBytes $Code)
        return $stream
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Open-LockedLocalArchiveInput(
    [object]$DirectoryContext,
    [string]$Path,
    [long]$MaximumBytes,
    [string]$Code
) {
    if ($null -eq $DirectoryContext -or [string]::IsNullOrWhiteSpace($Path)) { throw $Code }
    Assert-StableUpdaterDirectoryContext $DirectoryContext $Code
    $absolute = [IO.Path]::GetFullPath($Path)
    if (-not [IO.Path]::GetDirectoryName($absolute).Equals(
        [string]$DirectoryContext.Path, [StringComparison]::OrdinalIgnoreCase
    )) { throw $Code }
    if (-not [IO.File]::Exists($absolute) -or [IO.Directory]::Exists($absolute)) { throw $Code }
    Assert-NoAlternateDataStreams $absolute $Code
    $stream = [IO.File]::Open(
        $absolute, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
    try {
        Assert-OpenUpdaterFileAtPath $stream $absolute $Code
        # Recheck streams after the primary handle is retained so an ADS
        # created in the path-check/open race cannot survive validation.
        Assert-NoAlternateDataStreams $absolute $Code
        if ($stream.Length -lt 1 -or $stream.Length -gt $MaximumBytes) { throw $Code }
        return $stream
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Assert-OfflineLocalArchivePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID" }
    $absolute = [IO.Path]::GetFullPath($Path)
    if ($absolute.StartsWith('\\', [StringComparison]::Ordinal) -or
        -not [IO.Path]::IsPathRooted($absolute)) {
        throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
    }
    try {
        $root = [IO.Path]::GetPathRoot($absolute)
        $drive = [IO.DriveInfo]::new($root)
        if ($drive.DriveType -notin @(
            [IO.DriveType]::Fixed,
            [IO.DriveType]::Removable,
            [IO.DriveType]::CDRom,
            [IO.DriveType]::Ram
        )) { throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID" }
    }
    catch { throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID" }
    return $absolute
}

function Copy-LockedLocalArchiveInput(
    [IO.FileStream]$Source,
    [string]$Destination,
    [long]$MaximumBytes,
    [string]$Code
) {
    if ($null -eq $Source -or $null -eq $stagingDirectoryContext) { throw $Code }
    Assert-StableUpdaterDirectoryContext $stagingDirectoryContext $Code
    Assert-OpenUpdaterSingleLink $Source $Code
    $expectedLength = [long]$Source.Length
    if ($expectedLength -lt 1 -or $expectedLength -gt $MaximumBytes) { throw $Code }
    $destinationStream = $null
    try {
        $destinationStream = Open-NewUpdaterFileRelative `
            $stagingDirectoryContext $Destination $Code ([uint32]1)
        $Source.Position = 0
        $buffer = New-Object byte[] 65536
        [long]$written = 0
        while (($read = $Source.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $written += $read
            if ($written -gt $expectedLength) { throw $Code }
            $destinationStream.Write($buffer, 0, $read)
        }
        if ($written -ne $expectedLength -or $Source.Length -ne $expectedLength) { throw $Code }
        $destinationStream.Flush($true)
        Assert-OpenUpdaterFileAtPath $destinationStream $Destination $Code
        Assert-StableUpdaterDirectoryContext $stagingDirectoryContext $Code
        $destinationStream.Dispose()
        $destinationStream = $null
        $retained = Open-LockedUpdaterFile $Destination $MaximumBytes $Code
        if ([long]$retained.Length -ne $expectedLength) {
            $retained.Dispose()
            throw $Code
        }
        $Source.Position = 0
        return $retained
    }
    finally {
        if ($null -ne $destinationStream) { $destinationStream.Dispose() }
    }
}

function Assert-LocalArchiveManifestBinding(
    [IO.FileStream]$ManifestStream,
    [IO.FileStream]$ArchiveStream,
    [string]$ArchiveLeaf
) {
    if ($ArchiveLeaf -cnotmatch '^JobFlow-v(?<version>[0-9]+\.[0-9]+\.[0-9]+)-windows-x64-complete\.zip$') {
        throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
    }
    $fileVersion = [string]$Matches["version"]
    $manifest = Read-LockedJson $ManifestStream $maxManifestBytes "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
    if ($acceptanceMode) {
        $manifestVersion = [string]$manifest.version
        $assetName = "JobFlow-v$manifestVersion-windows-x64-complete.zip"
        $assetBytes = [long]$ArchiveStream.Length
        $assetSha256 = [string]$manifest.archive_sha256
    }
    else {
        if ($manifest -isnot [PSCustomObject] -or $manifest.release -isnot [PSCustomObject] -or
            $manifest.asset -isnot [PSCustomObject]) {
            throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        }
        $manifestVersion = [string]$manifest.release.version
        $assetName = [string]$manifest.asset.name
        if (-not ($manifest.asset.bytes -is [int] -or $manifest.asset.bytes -is [long])) {
            throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        }
        $assetBytes = [long]$manifest.asset.bytes
        $assetSha256 = [string]$manifest.asset.sha256
    }
    if ($manifestVersion -cne $fileVersion -or $assetName -cne $ArchiveLeaf -or
        $assetBytes -ne [long]$ArchiveStream.Length -or
        $assetSha256 -cnotmatch '^sha256:[0-9a-f]{64}$') {
        throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
    }
    $actualSha256 = "sha256:" + (Get-OpenStreamSha256 $ArchiveStream)
    if ($actualSha256 -cne $assetSha256 -or [long]$ArchiveStream.Length -ne $assetBytes) {
        throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
    }
    $ArchiveStream.Position = 0
    return [pscustomobject]@{
        Tag = "v$manifestVersion"
        ArchiveName = $ArchiveLeaf
        ArchiveBytes = $assetBytes
        ArchiveSha256 = $assetSha256
    }
}

function Assert-AllowedHttpsUri([Uri]$Uri, [string[]]$AllowedHosts) {
    if ($null -eq $Uri -or -not $Uri.IsAbsoluteUri -or $Uri.Scheme -ne "https" -or $Uri.UserInfo) {
        throw "JOBFLOW_UPDATE_DOWNLOAD_URI_FORBIDDEN"
    }
    if ($AllowedHosts -notcontains $Uri.DnsSafeHost.ToLowerInvariant()) {
        throw "JOBFLOW_UPDATE_DOWNLOAD_HOST_FORBIDDEN"
    }
}

function Receive-AllowedHttpsFile(
    [Uri]$Uri,
    [string]$Destination,
    [long]$MaximumBytes,
    [string[]]$AllowedHosts,
    [string]$Accept
) {
    Assert-AllowedHttpsUri $Uri $AllowedHosts
    Assert-JobFlowLocalPath $Destination
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(45)
    $client.DefaultRequestHeaders.UserAgent.ParseAdd("JobFlow-Update/1")
    if (-not [string]::IsNullOrWhiteSpace($Accept)) {
        $client.DefaultRequestHeaders.Accept.ParseAdd($Accept)
    }
    $current = $Uri
    $retainedOutput = $null
    $completed = $false
    $parentContext = Open-StableUpdaterDirectoryChain ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Destination))) "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID"
    try {
        for ($redirects = 0; $redirects -le 5; $redirects++) {
            Assert-AllowedHttpsUri $current $AllowedHosts
            $response = $client.GetAsync($current, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            try {
                $status = [int]$response.StatusCode
                if ($status -ge 300 -and $status -lt 400) {
                    if ($redirects -eq 5 -or $null -eq $response.Headers.Location) {
                        throw "JOBFLOW_UPDATE_REDIRECT_INVALID"
                    }
                    $current = if ($response.Headers.Location.IsAbsoluteUri) {
                        $response.Headers.Location
                    }
                    else { New-Object Uri($current, $response.Headers.Location) }
                    continue
                }
                if (-not $response.IsSuccessStatusCode) { throw "JOBFLOW_UPDATE_DOWNLOAD_FAILED" }
                $contentLength = $response.Content.Headers.ContentLength
                if ($null -ne $contentLength -and ($contentLength -lt 1 -or $contentLength -gt $MaximumBytes)) {
                    throw "JOBFLOW_UPDATE_DOWNLOAD_SIZE_INVALID"
                }
                $input = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                try {
                    Assert-StableUpdaterDirectoryContext $parentContext "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID"
                    $output = Open-NewUpdaterFileRelative $parentContext $Destination "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID" 1
                    try {
                        Assert-OpenUpdaterFileAtPath $output $Destination "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID"
                        $buffer = New-Object byte[] 65536
                        [long]$total = 0
                        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                            $total += $read
                            if ($total -gt $MaximumBytes) { throw "JOBFLOW_UPDATE_DOWNLOAD_SIZE_INVALID" }
                            $output.Write($buffer, 0, $read)
                        }
                        $output.Flush($true)
                        Assert-StableUpdaterDirectoryContext $parentContext "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID"
                        Assert-OpenUpdaterFileAtPath $output $Destination "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID"
                        # A child bootstrap opens these inputs with a read-only
                        # sharing contract.  Close the writer first, then retain
                        # a freshly identity-checked read lock; otherwise the
                        # writer's desired access makes the child's open fail.
                        $output.Dispose()
                        $output = $null
                        $retainedOutput = Open-LockedUpdaterFile `
                            $Destination $MaximumBytes "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID"
                    }
                    finally { if ($null -ne $output) { $output.Dispose() } }
                }
                finally { $input.Dispose() }
                if ($null -eq $retainedOutput -or $retainedOutput.Length -lt 1) { throw "JOBFLOW_UPDATE_DOWNLOAD_EMPTY" }
                Assert-NoAlternateDataStreams $Destination "JOBFLOW_UPDATE_DOWNLOAD_IDENTITY_INVALID"
                $completed = $true
                return $retainedOutput
            }
            finally { $response.Dispose() }
        }
        throw "JOBFLOW_UPDATE_REDIRECT_INVALID"
    }
    finally {
        if (-not $completed -and $null -ne $retainedOutput) { $retainedOutput.Dispose() }
        Close-StableUpdaterDirectoryContext $parentContext
        $client.Dispose()
        $handler.Dispose()
    }
}

function Get-ReleaseAssetDescriptor(
    [object]$Release,
    [string]$Name,
    [long]$MaximumBytes
) {
    $matches = @($Release.assets | Where-Object { [string]$_.name -ieq $Name })
    if ($matches.Count -ne 1 -or [string]$matches[0].name -cne $Name) {
        throw "JOBFLOW_UPDATE_RELEASE_ASSET_INVALID"
    }
    $asset = $matches[0]
    if ($asset -isnot [PSCustomObject] -or
        -not ($asset.size -is [int] -or $asset.size -is [long]) -or
        [long]$asset.size -lt 1 -or
        [long]$asset.size -gt $MaximumBytes -or
        ($null -ne $asset.PSObject.Properties["state"] -and [string]$asset.state -cne "uploaded")) {
        throw "JOBFLOW_UPDATE_RELEASE_ASSET_INVALID"
    }
    $url = [Uri]([string]$asset.browser_download_url)
    $escapedTag = [Uri]::EscapeDataString([string]$Release.tag_name)
    $expectedPath = "/$expectedRepository/releases/download/$escapedTag/$Name"
    if ($url.Scheme -ne "https" -or $url.DnsSafeHost -ne "github.com" -or $url.AbsolutePath -cne $expectedPath) {
        throw "JOBFLOW_UPDATE_RELEASE_ASSET_URL_INVALID"
    }
    return [pscustomobject]@{
        Name = $Name
        Size = [long]$asset.size
        Url = $url
    }
}

function Assert-ExactObjectProperties(
    [object]$Value,
    [string[]]$ExpectedNames,
    [string]$Code
) {
    if ($Value -isnot [PSCustomObject]) { throw $Code }
    $actual = @($Value.PSObject.Properties | ForEach-Object { [string]$_.Name })
    if ($actual.Count -ne $ExpectedNames.Count) { throw $Code }
    $expected = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($name in $ExpectedNames) { [void]$expected.Add($name) }
    foreach ($name in $actual) {
        if (-not $expected.Remove($name)) { throw $Code }
    }
    if ($expected.Count -ne 0) { throw $Code }
}

function Read-StableBootstrapSource {
    $stream = Open-LockedInstallerSourceFile $bootstrapPath $maxBootstrapBytes "JOBFLOW_INSTALL_STABLE_BOOTSTRAP_INVALID"
    $bytes = $null
    try {
        $bytes = Read-LockedBytes $stream $maxBootstrapBytes "JOBFLOW_UPDATE_STABLE_BOOTSTRAP_INVALID"
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $source = $utf8.GetString($bytes).TrimStart([char]0xFEFF)
        if ([string]::IsNullOrWhiteSpace($source)) {
            throw "JOBFLOW_INSTALL_STABLE_BOOTSTRAP_INVALID"
        }
        return $source
    }
    catch { throw "JOBFLOW_INSTALL_STABLE_BOOTSTRAP_INVALID" }
    finally {
        if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
        $stream.Dispose()
    }
}

function Invoke-StableBootstrap(
    [ValidateSet("RecoverOnly", "DescribeManifest", "Activate", "VerifyInstalled")]
    [string]$Mode,
    [string]$ManifestPath,
    [string]$SignaturePath,
    [string]$ArchivePath
) {
    if ([string]::IsNullOrWhiteSpace($bootstrapSource) -or
        [string]::IsNullOrWhiteSpace($trustedWindowsPowerShell)) {
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }
    if ($Mode -in @("RecoverOnly", "VerifyInstalled")) {
        if ($ManifestPath -or $SignaturePath -or $ArchivePath) {
            throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
        }
    }
    elseif ($Mode -eq "DescribeManifest") {
        if (-not $ManifestPath -or -not $SignaturePath -or $ArchivePath) {
            throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
        }
    }
    elseif (-not $ManifestPath -or -not $SignaturePath -or -not $ArchivePath) {
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }

    $wrapper = @'
$source = [Console]::In.ReadToEnd()
$ProgressPreference = "SilentlyContinue"
$block = [ScriptBlock]::Create($source)
switch ($env:JOBFLOW_UPDATER_BOOTSTRAP_MODE) {
    "RecoverOnly" {
        & $block -RecoverOnly
        break
    }
    "VerifyInstalled" {
        & $block -VerifyInstalled
        break
    }
    "DescribeManifest" {
        & $block -DescribeManifest -ManifestPath $env:JOBFLOW_UPDATER_MANIFEST -SignaturePath $env:JOBFLOW_UPDATER_SIGNATURE
        break
    }
    "Activate" {
        & $block -Activate -ManifestPath $env:JOBFLOW_UPDATER_MANIFEST -SignaturePath $env:JOBFLOW_UPDATER_SIGNATURE -ArchivePath $env:JOBFLOW_UPDATER_ARCHIVE
        break
    }
    default {
        [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_MODE_INVALID")
        exit 1
    }
}
if ($null -ne $LASTEXITCODE) { exit [int]$LASTEXITCODE }
exit 0
'@
    $encodedWrapper = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($wrapper))
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $trustedWindowsPowerShell
    $start.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encodedWrapper"
    $start.WorkingDirectory = $localRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    $start.EnvironmentVariables.Clear()
    foreach ($name in @("SystemRoot", "WINDIR", "TEMP", "TMP")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $start.EnvironmentVariables[$name] = $value
        }
    }
    $start.EnvironmentVariables["JOBFLOW_UPDATER_BOOTSTRAP_MODE"] = $Mode
    if ($ManifestPath) { $start.EnvironmentVariables["JOBFLOW_UPDATER_MANIFEST"] = [IO.Path]::GetFullPath($ManifestPath) }
    if ($SignaturePath) { $start.EnvironmentVariables["JOBFLOW_UPDATER_SIGNATURE"] = [IO.Path]::GetFullPath($SignaturePath) }
    if ($ArchivePath) { $start.EnvironmentVariables["JOBFLOW_UPDATER_ARCHIVE"] = [IO.Path]::GetFullPath($ArchivePath) }
    if ($acceptanceMode) {
        $start.EnvironmentVariables["JOBFLOW_INSTALL_ACCEPTANCE_LOCALAPPDATA"] = $localAppDataRoot
    }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.Write($bootstrapSource)
        $process.StandardInput.Close()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if (
            [Text.Encoding]::UTF8.GetByteCount([string]$stdout) -gt $maxBootstrapOutputBytes -or
            [Text.Encoding]::UTF8.GetByteCount([string]$stderr) -gt $maxBootstrapOutputBytes
        ) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
        return [pscustomobject]@{
            ExitCode = [int]$process.ExitCode
            Stdout = ([string]$stdout).Trim()
            Stderr = ([string]$stderr).Trim()
        }
    }
    catch { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
    finally { $process.Dispose() }
}

function ConvertFrom-BootstrapJson([object]$Invocation, [string]$Code) {
    if ($null -eq $Invocation -or
        -not [string]::IsNullOrWhiteSpace([string]$Invocation.Stderr) -or
        [string]::IsNullOrWhiteSpace([string]$Invocation.Stdout)) {
        throw $Code
    }
    try {
        [JobFlowUpdaterNative.JsonApi]::AssertNoDuplicateProperties([string]$Invocation.Stdout)
        $value = [string]$Invocation.Stdout | ConvertFrom-Json
        if ($value -isnot [PSCustomObject]) { throw $Code }
        return $value
    }
    catch { throw $Code }
}

function Assert-RecoveryBootstrapResult([object]$Invocation) {
    $result = ConvertFrom-BootstrapJson $Invocation "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    Assert-ExactObjectProperties $result @(
        "schema_version", "status", "recovery_performed", "activation_committed",
        "retry_required", "real_external_actions"
    ) "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    if (
        [int]$Invocation.ExitCode -eq 6 -and
        [int]$result.schema_version -eq 1 -and
        [string]$result.status -ceq "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED" -and
        $result.recovery_performed -is [bool] -and $result.recovery_performed -eq $true -and
        $result.activation_committed -is [bool] -and
        $result.retry_required -is [bool] -and $result.retry_required -eq $true -and
        [int]$result.real_external_actions -eq 0
    ) {
        throw "JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED"
    }
    if (
        [int]$Invocation.ExitCode -ne 0 -or
        [int]$result.schema_version -ne 1 -or
        [string]$result.status -cne "JOBFLOW_ACTIVATION_RECOVERY_NOT_PENDING" -or
        $result.recovery_performed -isnot [bool] -or $result.recovery_performed -ne $false -or
        $result.activation_committed -isnot [bool] -or $result.activation_committed -ne $false -or
        $result.retry_required -isnot [bool] -or $result.retry_required -ne $false -or
        [int]$result.real_external_actions -ne 0
    ) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
}

function Assert-DescribeBootstrapResult(
    [object]$Invocation,
    [IO.FileStream]$ManifestStream
) {
    if ([int]$Invocation.ExitCode -ne 0) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
    $result = ConvertFrom-BootstrapJson $Invocation "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    Assert-ExactObjectProperties $result @(
        "schema_version", "status", "signature_verified", "key_id",
        "manifest_schema_version", "publisher_attestation_bound",
        "manifest_sha256", "manifest_bytes", "real_external_actions"
    ) "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    $expectedManifestSha = "sha256:" + (Get-OpenStreamSha256 $ManifestStream)
    if (
        [int]$result.schema_version -ne 1 -or
        [string]$result.status -cne "JOBFLOW_BOOTSTRAP_MANIFEST_VERIFIED" -or
        $result.signature_verified -isnot [bool] -or $result.signature_verified -ne $true -or
        [string]$result.key_id -cne $expectedKeyId -or
        [int]$result.manifest_schema_version -ne 2 -or
        $result.publisher_attestation_bound -isnot [bool] -or $result.publisher_attestation_bound -ne $true -or
        [string]$result.manifest_sha256 -cne $expectedManifestSha -or
        -not ($result.manifest_bytes -is [int] -or $result.manifest_bytes -is [long]) -or
        [long]$result.manifest_bytes -ne [long]$ManifestStream.Length -or
        [int]$result.real_external_actions -ne 0
    ) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
    return $result
}

function Assert-ActivationBootstrapResult([object]$Invocation) {
    $result = ConvertFrom-BootstrapJson $Invocation "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    if ([string]$result.status -ceq "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED") {
        Assert-ExactObjectProperties $result @(
            "status", "activation_committed", "retry_required", "real_external_actions"
        ) "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
        if (
            [int]$Invocation.ExitCode -eq 0 -and
            $result.activation_committed -is [bool] -and
            $result.retry_required -is [bool] -and $result.retry_required -eq $true -and
            [int]$result.real_external_actions -eq 0
        ) { throw "JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED" }
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }
    if ([int]$Invocation.ExitCode -ne 0) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
    $allowed = @(
        "status", "version", "source_payload_sha256", "runtime_tree_sha256",
        "activation_performed", "real_external_actions"
    )
    if ($null -ne $result.PSObject.Properties["legacy_migration_performed"]) {
        $allowed += "legacy_migration_performed"
    }
    Assert-ExactObjectProperties $result $allowed "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    if (
        [string]$result.status -cne "JOBFLOW_BOOTSTRAP_ACTIVATED" -or
        [string]$result.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$result.source_payload_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        [string]$result.runtime_tree_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        $result.activation_performed -isnot [bool] -or
        [int]$result.real_external_actions -ne 0 -or
        ($null -ne $result.PSObject.Properties["legacy_migration_performed"] -and
            ($result.legacy_migration_performed -isnot [bool] -or
             $result.legacy_migration_performed -ne $true))
    ) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
    return $result
}

function Assert-VerifyInstalledBootstrapResult([object]$Invocation) {
    if ([int]$Invocation.ExitCode -ne 0) { throw "JOBFLOW_INSTALL_VERIFY_REQUIRED" }
    $result = ConvertFrom-BootstrapJson $Invocation "JOBFLOW_INSTALL_VERIFY_REQUIRED"
    Assert-ExactObjectProperties $result @(
        "schema_version", "status", "version", "manifest_sha256",
        "signature_envelope_sha256", "runtime_closure_manifest_sha256",
        "runtime_tree_sha256", "release_key_id", "source_payload_sha256",
        "signed_activation_evidence_verified", "recovery_performed",
        "activation_committed_during_recovery", "paths_disclosed",
        "real_external_actions"
    ) "JOBFLOW_INSTALL_VERIFY_REQUIRED"
    if (
        [int]$result.schema_version -ne 1 -or
        [string]$result.status -cne "JOBFLOW_INSTALLED_RUNTIME_VERIFIED" -or
        [string]$result.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$result.manifest_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        [string]$result.signature_envelope_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        [string]$result.runtime_closure_manifest_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        [string]$result.runtime_tree_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        [string]$result.release_key_id -cne $expectedKeyId -or
        [string]$result.source_payload_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        $result.signed_activation_evidence_verified -isnot [bool] -or
        $result.signed_activation_evidence_verified -ne $true -or
        $result.recovery_performed -isnot [bool] -or
        $result.activation_committed_during_recovery -isnot [bool] -or
        $result.paths_disclosed -isnot [bool] -or $result.paths_disclosed -ne $false -or
        [int]$result.real_external_actions -ne 0
    ) { throw "JOBFLOW_INSTALL_VERIFY_REQUIRED" }
    return $result
}

function Read-AndValidateV2CurrentPointer(
    [object]$Activation,
    [string]$ExpectedTag
) {
    $stream = Open-LockedUpdaterFile $currentPointerPath 64KB "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    try {
        $pointer = Read-LockedJson $stream 64KB "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
        Assert-ExactObjectProperties $pointer @(
            "bootstrap_version", "platform", "product", "release_key_id",
            "runtime_closure_manifest_sha256", "runtime_tree_sha256", "schema_version",
            "source_commit", "source_payload_sha256", "version", "version_directory"
        ) "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
        $sourceHex = ([string]$pointer.source_payload_sha256).Substring(7)
        $expectedDirectory = "v$([string]$pointer.version)-$($sourceHex.Substring(0, 12))"
        if (
            [int]$pointer.schema_version -ne 2 -or
            [string]$pointer.product -cne "JobFlow" -or
            [string]$pointer.platform -cne "windows-x64" -or
            [string]$pointer.release_key_id -cne $expectedKeyId -or
            [string]$pointer.bootstrap_version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            [string]$pointer.source_commit -notmatch '^[0-9a-f]{40}$' -or
            [string]$pointer.source_payload_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            [string]$pointer.runtime_closure_manifest_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            [string]$pointer.runtime_tree_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            [string]$pointer.version -cne [string]$Activation.version -or
            $ExpectedTag -cne "v$([string]$pointer.version)" -or
            [string]$pointer.version_directory -cne $expectedDirectory -or
            [string]$pointer.source_payload_sha256 -cne [string]$Activation.source_payload_sha256 -or
            [string]$pointer.runtime_tree_sha256 -cne [string]$Activation.runtime_tree_sha256
        ) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
        return $pointer
    }
    finally { $stream.Dispose() }
}

function Set-InstallerCurrentUserOnly([string]$Path) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($null -eq $sid) { throw "JOBFLOW_INSTALL_ACL_FAILED" }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_INSTALL_ACL_FAILED"
    }
    if ($item.PSIsContainer) {
        $acl = [Security.AccessControl.DirectorySecurity]::new()
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
             [Security.AccessControl.InheritanceFlags]::ObjectInherit),
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
    }
    else {
        $acl = [Security.AccessControl.FileSecurity]::new()
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
    }
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($sid)
    [void]$acl.AddAccessRule($rule)
    if ($item.PSIsContainer) {
        [IO.Directory]::SetAccessControl([IO.Path]::GetFullPath($Path), $acl)
    }
    else {
        [IO.File]::SetAccessControl([IO.Path]::GetFullPath($Path), $acl)
    }
}

function Get-ControlPlaneSourceInventory {
    $records = [Collections.Generic.List[object]]::new()
    foreach ($name in $stableControlPlaneFiles) {
        $path = Join-Path $stableSourceRoot $name
        $stream = Open-LockedInstallerSourceFile $path 4MB "JOBFLOW_INSTALL_CONTROL_SOURCE_INVALID"
        try {
            [void]$records.Add([pscustomobject]@{
                name = $name
                bytes = [long]$stream.Length
                sha256 = "sha256:" + (Get-OpenStreamSha256 $stream)
            })
        }
        finally { $stream.Dispose() }
    }
    return $records.ToArray()
}

function Get-ControlPlaneInventoryMap([object[]]$Inventory, [string]$Code) {
    if (@($Inventory).Count -ne $stableControlPlaneFiles.Count) { throw $Code }
    $map = @{}
    foreach ($record in @($Inventory)) {
        Assert-ExactObjectProperties $record @("name", "bytes", "sha256") $Code
        $name = [string]$record.name
        if ($stableControlPlaneFiles -cnotcontains $name -or $map.ContainsKey($name) -or
            -not ($record.bytes -is [int] -or $record.bytes -is [long]) -or
            [long]$record.bytes -lt 1 -or [long]$record.bytes -gt 4MB -or
            [string]$record.sha256 -notmatch '^sha256:[0-9a-f]{64}$') { throw $Code }
        $map[$name] = $record
    }
    return $map
}

function Assert-ControlPlaneTree([string]$Root, [object[]]$Expected) {
    $absolute = [IO.Path]::GetFullPath($Root)
    $binExpected = [IO.Path]::GetFullPath((Join-Path $jobOpsRoot "bin"))
    $statePrefix = $installerStateRoot.TrimEnd('\') + '\'
    if (-not $absolute.Equals($binExpected, [StringComparison]::OrdinalIgnoreCase) -and
        -not $absolute.StartsWith($statePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_INSTALL_CONTROL_PLANE_PATH_FORBIDDEN"
    }
    if (-not (Test-Path -LiteralPath $absolute -PathType Container)) {
        throw "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
    }
    $rootItem = Get-Item -LiteralPath $absolute -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
    }
    $children = @(Get-ChildItem -LiteralPath $absolute -Force)
    if ($children.Count -ne $stableControlPlaneFiles.Count) {
        throw "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
    }
    $expectedMap = if ($null -eq $Expected) { $null } else {
        Get-ControlPlaneInventoryMap $Expected "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
    }
    foreach ($child in $children) {
        if ($child.PSIsContainer -or
            ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $stableControlPlaneFiles -cnotcontains [string]$child.Name) {
            throw "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
        }
        Assert-NoAlternateDataStreams $child.FullName "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
        $stream = [IO.File]::Open(
            $child.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
        )
        try {
            Assert-OpenUpdaterFileAtPath $stream $child.FullName "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
            if ($null -ne $expectedMap) {
                $record = $expectedMap[[string]$child.Name]
                if ([long]$stream.Length -ne [long]$record.bytes -or
                    ("sha256:" + (Get-OpenStreamSha256 $stream)) -cne [string]$record.sha256) {
                    throw "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID"
                }
            }
        }
        finally { $stream.Dispose() }
    }
}

function New-ControlPlaneStage([string]$TransactionId, [object[]]$Inventory) {
    $stage = Join-Path $installerStateRoot (".cp-" + $TransactionId)
    $context = New-StableUpdaterDirectoryRoot $stage "JOBFLOW_INSTALL_CONTROL_STAGE_INVALID"
    try {
        Set-InstallerCurrentUserOnly $stage
        $map = Get-ControlPlaneInventoryMap $Inventory "JOBFLOW_INSTALL_CONTROL_SOURCE_INVALID"
        foreach ($name in $stableControlPlaneFiles) {
            $sourcePath = Join-Path $stableSourceRoot $name
            $destinationPath = Join-Path $stage $name
            $source = Open-LockedInstallerSourceFile $sourcePath 4MB "JOBFLOW_INSTALL_CONTROL_SOURCE_INVALID"
            $destination = $null
            try {
                $destination = [IO.File]::Open(
                    $destinationPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
                )
                $buffer = New-Object byte[] 65536
                while (($read = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $destination.Write($buffer, 0, $read)
                }
                $destination.Flush($true)
            }
            finally {
                if ($null -ne $destination) { $destination.Dispose() }
                $source.Dispose()
            }
            Set-InstallerCurrentUserOnly $destinationPath
            Assert-NoAlternateDataStreams $destinationPath "JOBFLOW_INSTALL_CONTROL_STAGE_INVALID"
            $record = $map[$name]
            $verify = [IO.File]::Open(
                $destinationPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
            )
            try {
                Assert-OpenUpdaterFileAtPath $verify $destinationPath "JOBFLOW_INSTALL_CONTROL_STAGE_INVALID"
                if ([long]$verify.Length -ne [long]$record.bytes -or
                    ("sha256:" + (Get-OpenStreamSha256 $verify)) -cne [string]$record.sha256) {
                    throw "JOBFLOW_INSTALL_CONTROL_STAGE_INVALID"
                }
            }
            finally { $verify.Dispose() }
        }
        Assert-ControlPlaneTree $stage $Inventory
        return $stage
    }
    finally { Close-StableUpdaterDirectoryContext $context }
}

function Write-InstallerJournal([object]$Journal) {
    Assert-ExactObjectProperties $Journal @(
        "schema_version", "transaction_id", "state", "old_bin_present", "files"
    ) "JOBFLOW_INSTALL_JOURNAL_INVALID"
    if ([int]$Journal.schema_version -ne 1 -or
        [string]$Journal.transaction_id -notmatch '^[0-9a-f]{12}$' -or
        [string]$Journal.state -notin @("PREPARED", "OLD_MOVED", "NEW_MOVED", "COMMITTED") -or
        $Journal.old_bin_present -isnot [bool]) { throw "JOBFLOW_INSTALL_JOURNAL_INVALID" }
    [void](Get-ControlPlaneInventoryMap @($Journal.files) "JOBFLOW_INSTALL_JOURNAL_INVALID")
    $json = $Journal | ConvertTo-Json -Depth 5 -Compress
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    if ($bytes.Length -lt 1 -or $bytes.Length -gt $maxInstallerJournalBytes) {
        throw "JOBFLOW_INSTALL_JOURNAL_INVALID"
    }
    $temporary = Join-Path $installerStateRoot (".journal-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $backup = Join-Path $installerStateRoot (".journal-" + [Guid]::NewGuid().ToString("N") + ".bak")
    try {
        [IO.File]::WriteAllBytes($temporary, $bytes)
        Set-InstallerCurrentUserOnly $temporary
        Assert-NoAlternateDataStreams $temporary "JOBFLOW_INSTALL_JOURNAL_INVALID"
        if ([IO.File]::Exists($installerJournalPath)) {
            [IO.File]::Replace($temporary, $installerJournalPath, $backup, $true)
            if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) }
        }
        else { [IO.File]::Move($temporary, $installerJournalPath) }
        Set-InstallerCurrentUserOnly $installerJournalPath
    }
    finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) }
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function Read-InstallerJournal {
    if (-not [IO.File]::Exists($installerJournalPath)) { return $null }
    $stream = Open-LockedUpdaterFile $installerJournalPath $maxInstallerJournalBytes "JOBFLOW_INSTALL_JOURNAL_INVALID"
    try { $journal = Read-LockedJson $stream $maxInstallerJournalBytes "JOBFLOW_INSTALL_JOURNAL_INVALID" }
    finally { $stream.Dispose() }
    Assert-ExactObjectProperties $journal @(
        "schema_version", "transaction_id", "state", "old_bin_present", "files"
    ) "JOBFLOW_INSTALL_JOURNAL_INVALID"
    if ([int]$journal.schema_version -ne 1 -or
        [string]$journal.transaction_id -notmatch '^[0-9a-f]{12}$' -or
        [string]$journal.state -notin @("PREPARED", "OLD_MOVED", "NEW_MOVED", "COMMITTED") -or
        $journal.old_bin_present -isnot [bool]) { throw "JOBFLOW_INSTALL_JOURNAL_INVALID" }
    [void](Get-ControlPlaneInventoryMap @($journal.files) "JOBFLOW_INSTALL_JOURNAL_INVALID")
    return $journal
}

function Remove-OwnedInstallerTree([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $installerStateRoot.TrimEnd('\') + '\'
    $leaf = [IO.Path]::GetFileName($absolute)
    if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        $leaf -notmatch '^\.(?:jfi|cp|cpb)-[0-9a-f]{12}$') {
        throw "JOBFLOW_INSTALL_CLEANUP_PATH_FORBIDDEN"
    }
    if (-not [IO.Directory]::Exists($absolute)) { return }
    $rootItem = Get-Item -LiteralPath $absolute -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_INSTALL_CLEANUP_UNSAFE"
    }
    foreach ($child in @(Get-ChildItem -LiteralPath $absolute -Force)) {
        if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_INSTALL_CLEANUP_UNSAFE"
        }
        if ($child.PSIsContainer) {
            # Control-plane staging is flat.  Download staging may contain no
            # extracted tree because only the bootstrap may expand archives.
            throw "JOBFLOW_INSTALL_CLEANUP_UNSAFE"
        }
        Assert-NoAlternateDataStreams $child.FullName "JOBFLOW_INSTALL_CLEANUP_UNSAFE"
        $stream = [IO.File]::Open(
            $child.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite
        )
        try { Assert-OpenUpdaterFileAtPath $stream $child.FullName "JOBFLOW_INSTALL_CLEANUP_UNSAFE" }
        finally { $stream.Dispose() }
        [IO.File]::Delete($child.FullName)
    }
    [IO.Directory]::Delete($absolute, $false)
}

function Remove-InstallerJournalFile {
    if (-not [IO.File]::Exists($installerJournalPath)) { return }
    $stream = Open-LockedUpdaterFile $installerJournalPath $maxInstallerJournalBytes "JOBFLOW_INSTALL_JOURNAL_INVALID"
    $stream.Dispose()
    [IO.File]::Delete($installerJournalPath)
}

function Recover-InstallerControlPlane {
    $journal = Read-InstallerJournal
    if ($null -eq $journal) { return $false }
    $transactionId = [string]$journal.transaction_id
    $stage = Join-Path $installerStateRoot (".cp-" + $transactionId)
    $backup = Join-Path $installerStateRoot (".cpb-" + $transactionId)
    $bin = Join-Path $jobOpsRoot "bin"
    $expected = @($journal.files)
    $binMatches = $false
    if ([IO.Directory]::Exists($bin)) {
        try { Assert-ControlPlaneTree $bin $expected; $binMatches = $true }
        catch { $binMatches = $false }
    }
    if ($binMatches) {
        if ([IO.Directory]::Exists($stage)) { Remove-OwnedInstallerTree $stage }
        if ([IO.Directory]::Exists($backup)) { Remove-OwnedInstallerTree $backup }
        Remove-InstallerJournalFile
        return $true
    }
    if ([IO.Directory]::Exists($backup) -and -not (Test-Path -LiteralPath $bin)) {
        Assert-ControlPlaneTree $backup $null
        [IO.Directory]::Move($backup, $bin)
        if ([IO.Directory]::Exists($stage)) { Remove-OwnedInstallerTree $stage }
        Remove-InstallerJournalFile
        return $true
    }
    if ([string]$journal.state -eq "PREPARED" -and -not [IO.Directory]::Exists($backup)) {
        if ([IO.Directory]::Exists($bin)) { Assert-ControlPlaneTree $bin $null }
        if ([IO.Directory]::Exists($stage)) { Remove-OwnedInstallerTree $stage }
        Remove-InstallerJournalFile
        return $true
    }
    throw "JOBFLOW_INSTALL_RECOVERY_REQUIRED"
}

function Install-StableControlPlaneAtomic {
    $inventory = @(Get-ControlPlaneSourceInventory)
    $transactionId = [Guid]::NewGuid().ToString("N").Substring(0, 12)
    $stage = New-ControlPlaneStage $transactionId $inventory
    $backup = Join-Path $installerStateRoot (".cpb-" + $transactionId)
    $bin = Join-Path $jobOpsRoot "bin"
    $oldPresent = [IO.Directory]::Exists($bin)
    if (Test-Path -LiteralPath $bin -PathType Leaf) { throw "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID" }
    if ($oldPresent) { Assert-ControlPlaneTree $bin $null }
    $journal = [ordered]@{
        schema_version = 1
        transaction_id = $transactionId
        state = "PREPARED"
        old_bin_present = [bool]$oldPresent
        files = $inventory
    }
    Write-InstallerJournal ([pscustomobject]$journal)
    try {
        if ($oldPresent) {
            if (Test-Path -LiteralPath $backup) { throw "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID" }
            [IO.Directory]::Move($bin, $backup)
            $journal.state = "OLD_MOVED"
            Write-InstallerJournal ([pscustomobject]$journal)
        }
        [IO.Directory]::Move($stage, $bin)
        $journal.state = "NEW_MOVED"
        Write-InstallerJournal ([pscustomobject]$journal)
        Assert-ControlPlaneTree $bin $inventory
        $journal.state = "COMMITTED"
        Write-InstallerJournal ([pscustomobject]$journal)
        if ([IO.Directory]::Exists($backup)) { Remove-OwnedInstallerTree $backup }
        Remove-InstallerJournalFile
        $script:stableControlPlaneInstalled = $true
    }
    catch {
        try {
            if (Recover-InstallerControlPlane) {
                throw "JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED"
            }
        }
        catch {
            if ([string]$_.Exception.Message -eq "JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED") { throw }
            throw "JOBFLOW_INSTALL_RECOVERY_REQUIRED"
        }
        throw
    }
}

function Copy-AcceptanceFixtureAsset([string]$Name, [string]$Destination, [long]$MaximumBytes) {
    if (-not $acceptanceMode -or [string]::IsNullOrWhiteSpace($acceptanceFixtureRoot) -or
        $Name -notmatch '^[A-Za-z0-9._-]{1,180}$') {
        throw "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
    }
    $sourcePath = [IO.Path]::GetFullPath((Join-Path $acceptanceFixtureRoot $Name))
    $fixturePrefix = $acceptanceFixtureRoot.TrimEnd('\') + '\'
    if (-not $sourcePath.StartsWith($fixturePrefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
    }
    $sourceItem = Get-Item -LiteralPath $sourcePath -Force
    if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
    }
    Assert-NoAlternateDataStreams $sourcePath "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
    $source = [IO.File]::Open($sourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $destinationStream = $null
    try {
        Assert-OpenUpdaterFileAtPath $source $sourcePath "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
        if ($source.Length -lt 1 -or $source.Length -gt $MaximumBytes) {
            throw "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
        }
        $destinationStream = [IO.File]::Open(
            $Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read
        )
        $buffer = New-Object byte[] 65536
        while (($read = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $destinationStream.Write($buffer, 0, $read)
        }
        $destinationStream.Flush($true)
        Assert-OpenUpdaterFileAtPath $destinationStream $Destination "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
        $destinationStream.Dispose()
        $destinationStream = $null
        $retained = Open-LockedUpdaterFile `
            $Destination $MaximumBytes "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID"
        return $retained
    }
    finally {
        if ($null -ne $destinationStream) { $destinationStream.Dispose() }
        $source.Dispose()
    }
}

function Receive-InstallerAsset(
    [Uri]$Uri,
    [string]$Destination,
    [long]$MaximumBytes,
    [string[]]$AllowedHosts,
    [string]$Accept,
    [string]$AcceptanceName
) {
    if ($acceptanceMode) {
        return Copy-AcceptanceFixtureAsset $AcceptanceName $Destination $MaximumBytes
    }
    return Receive-AllowedHttpsFile $Uri $Destination $MaximumBytes $AllowedHosts $Accept
}

function Invoke-VerifiedCompanionInstaller([object]$Pointer) {
    if ($acceptanceMode) { return }
    $runtimeRoot = [IO.Path]::GetFullPath((Join-Path $jobOpsRoot ("Application\versions\" + [string]$Pointer.version_directory)))
    $versionsPrefix = [IO.Path]::GetFullPath((Join-Path $jobOpsRoot "Application\versions")).TrimEnd('\') + '\'
    if (-not $runtimeRoot.StartsWith($versionsPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_INSTALL_COMPANION_SOURCE_INVALID"
    }
    $installer = Join-Path $runtimeRoot "scripts\install-jobflow-browser-companion.ps1"
    $lock = Open-LockedUpdaterFile $installer 4MB "JOBFLOW_INSTALL_COMPANION_SOURCE_INVALID"
    $lock.Dispose()
    $wrapper = @'
$arguments = @("-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $env:JOBFLOW_COMPANION_INSTALLER)
if ($env:JOBFLOW_COMPANION_NO_LAUNCH -eq "1") { $arguments += "-NoLaunch" }
& $env:JOBFLOW_TRUSTED_POWERSHELL @arguments
if ($null -ne $LASTEXITCODE) { exit [int]$LASTEXITCODE }
exit 0
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($wrapper))
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $trustedWindowsPowerShell
    $start.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded"
    $start.WorkingDirectory = $runtimeRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.EnvironmentVariables.Clear()
    foreach ($name in @("SystemRoot", "WINDIR", "TEMP", "TMP")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $start.EnvironmentVariables[$name] = $value }
    }
    $start.EnvironmentVariables["JOBFLOW_COMPANION_INSTALLER"] = $installer
    $start.EnvironmentVariables["JOBFLOW_TRUSTED_POWERSHELL"] = $trustedWindowsPowerShell
    # An air-gapped archive install must never open an HTTPS store page even
    # when the caller omitted -NoLaunch.  Local registration still proceeds.
    $start.EnvironmentVariables["JOBFLOW_COMPANION_NO_LAUNCH"] = if ($NoLaunch -or $localArchiveMode) { "1" } else { "0" }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_INSTALL_COMPANION_FAILED" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ([Text.Encoding]::UTF8.GetByteCount($stdout) -gt 256KB -or
            [Text.Encoding]::UTF8.GetByteCount($stderr) -gt 256KB -or
            $process.ExitCode -ne 0) { throw "JOBFLOW_INSTALL_COMPANION_FAILED" }
    }
    finally { $process.Dispose() }
}

try {
    Assert-JobFlowLocalPath $stagingRoot
    Assert-JobFlowLocalPath $currentPointerPath
    Assert-JobFlowLocalPath $updateCoordinatorPath
    Assert-InstallerSourcePath $bootstrapPath
    $installerStateContext = Open-StableUpdaterDirectoryChain `
        $installerStateRoot "JOBFLOW_INSTALL_STATE_INVALID" -CreateMissing
    Set-InstallerCurrentUserOnly $installerStateRoot
    try {
        $updateCoordinatorLock = [IO.File]::Open(
            $updateCoordinatorPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::Read
        )
        Assert-OpenUpdaterSingleLink $updateCoordinatorLock "JOBFLOW_INSTALL_COORDINATOR_INVALID"
        Assert-NoAlternateDataStreams $updateCoordinatorPath "JOBFLOW_INSTALL_COORDINATOR_INVALID"
        Set-InstallerCurrentUserOnly $updateCoordinatorPath
    }
    catch { throw "JOBFLOW_INSTALL_ALREADY_RUNNING_OR_COORDINATOR_INVALID" }

    $bootstrapSource = Read-StableBootstrapSource
    $trustedPowerShellInfo = Get-TrustedWindowsPowerShell
    $trustedWindowsPowerShell = [string]$trustedPowerShellInfo.Path
    $powerShellExecutableLock = $trustedPowerShellInfo.Lock

    # The source-package bootstrap is the first component allowed to inspect an
    # existing JobOps root.  It never creates a fresh JobOps shell in
    # RecoverOnly mode.  Any recovered state ends this invocation without a
    # retry or download.
    $recoveryInvocation = Invoke-StableBootstrap "RecoverOnly" $null $null $null
    try { Assert-RecoveryBootstrapResult $recoveryInvocation }
    catch {
        if ([string]$_.Exception.Message -eq "JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED") {
            throw "JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED"
        }
        throw "JOBFLOW_INSTALL_RECOVERY_REQUIRED"
    }
    if ([IO.File]::Exists($installerJournalPath)) {
        try {
            [void](Assert-VerifyInstalledBootstrapResult (
                Invoke-StableBootstrap "VerifyInstalled" $null $null $null
            ))
            if (Recover-InstallerControlPlane) {
                throw "JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED"
            }
        }
        catch {
            if ([string]$_.Exception.Message -eq "JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED") { throw }
            throw "JOBFLOW_INSTALL_RECOVERY_REQUIRED"
        }
    }

    $expectedReleaseTag = $null
    $stagedArchivePath = $null
    if ($localArchiveMode) {
        # Air-gapped mode accepts one exact complete-runtime archive.  The
        # fixed-name signed manifest and signature envelope must be siblings.
        # Source identities remain locked without write/delete sharing while
        # signature, schema, asset name, byte count, and SHA-256 are verified.
        $localAbsoluteArchive = Assert-OfflineLocalArchivePath $localArchiveInputPath
        $localArchiveLeaf = [IO.Path]::GetFileName($localAbsoluteArchive)
        if ($localArchiveLeaf -cnotmatch '^JobFlow-v[0-9]+\.[0-9]+\.[0-9]+-windows-x64-complete\.zip$') {
            throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        }
        $localArchiveParent = [IO.Path]::GetDirectoryName($localAbsoluteArchive)
        $localManifestPath = Join-Path $localArchiveParent $manifestAssetName
        $localSignaturePath = Join-Path $localArchiveParent $signatureAssetName
        $localArchiveDirectoryContext = Open-StableUpdaterDirectoryChain `
            $localArchiveParent "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        $localManifestInputLock = Open-LockedLocalArchiveInput `
            $localArchiveDirectoryContext $localManifestPath $maxManifestBytes "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        $localSignatureInputLock = Open-LockedLocalArchiveInput `
            $localArchiveDirectoryContext $localSignaturePath $maxSignatureBytes "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        $localArchiveInputLock = Open-LockedLocalArchiveInput `
            $localArchiveDirectoryContext $localAbsoluteArchive $maxArchiveBytes "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"

        Write-Host "正在验证本地签名完整运行时…… / Verifying the local signed complete runtime..."
        $sourceDescribeInvocation = Invoke-StableBootstrap `
            "DescribeManifest" $localManifestPath $localSignaturePath $null
        try {
            $sourceDescribe = Assert-DescribeBootstrapResult `
                $sourceDescribeInvocation $localManifestInputLock
            $localBinding = Assert-LocalArchiveManifestBinding `
                $localManifestInputLock $localArchiveInputLock $localArchiveLeaf
        }
        catch { throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID" }

        # Only authenticated, identity-locked source bytes enter private
        # installer staging.  No network primitive is reachable in this branch.
        $stagingDirectoryContext = New-StableUpdaterDirectoryRoot `
            $stagingRoot "JOBFLOW_INSTALL_STAGING_COLLISION"
        Set-InstallerCurrentUserOnly $stagingRoot
        $manifestPath = Join-Path $stagingRoot $manifestAssetName
        $signaturePath = Join-Path $stagingRoot $signatureAssetName
        $stagedArchivePath = Join-Path $stagingRoot ([string]$localBinding.ArchiveName)
        $manifestMetadataLock = Copy-LockedLocalArchiveInput `
            $localManifestInputLock $manifestPath $maxManifestBytes "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        $signatureMetadataLock = Copy-LockedLocalArchiveInput `
            $localSignatureInputLock $signaturePath $maxSignatureBytes "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        $archiveIdentityLock = Copy-LockedLocalArchiveInput `
            $localArchiveInputLock $stagedArchivePath $maxArchiveBytes "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        $stagedDescribeInvocation = Invoke-StableBootstrap `
            "DescribeManifest" $manifestPath $signaturePath $null
        try {
            $stagedDescribe = Assert-DescribeBootstrapResult `
                $stagedDescribeInvocation $manifestMetadataLock
        }
        catch { throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID" }
        if ([string]$sourceDescribe.manifest_sha256 -cne [string]$stagedDescribe.manifest_sha256 -or
            [long]$archiveIdentityLock.Length -ne [long]$localBinding.ArchiveBytes -or
            ("sha256:" + (Get-OpenStreamSha256 $archiveIdentityLock)) -cne [string]$localBinding.ArchiveSha256) {
            throw "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID"
        }
        $expectedReleaseTag = [string]$localBinding.Tag
    }
    else {
        $stagingDirectoryContext = New-StableUpdaterDirectoryRoot `
            $stagingRoot "JOBFLOW_INSTALL_STAGING_COLLISION"
        Set-InstallerCurrentUserOnly $stagingRoot
        $releasePath = Join-Path $stagingRoot "release.json"
        $manifestPath = Join-Path $stagingRoot $manifestAssetName
        $signaturePath = Join-Path $stagingRoot $signatureAssetName

        Write-Host "正在获取 JobFlow 的签名完整运行时…… / Fetching the signed complete JobFlow runtime..."
        $releaseMetadataLock = Receive-InstallerAsset (
            [Uri]$expectedApiUrl
        ) $releasePath $maxReleaseBytes @("api.github.com") "application/vnd.github+json" "release.json"
        $release = Read-LockedJson $releaseMetadataLock $maxReleaseBytes "JOBFLOW_UPDATE_RELEASE_METADATA_INVALID"
        if (
            $release -isnot [PSCustomObject] -or
            $release.draft -ne $false -or
            $release.prerelease -ne $false -or
            ([string]$release.tag_name) -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$' -or
            @($release.assets).Count -lt 2 -or
            @($release.assets).Count -gt 64
        ) { throw "JOBFLOW_INSTALL_RELEASE_METADATA_INVALID" }

        $manifestDescriptor = Get-ReleaseAssetDescriptor $release $manifestAssetName $maxManifestBytes
        $signatureDescriptor = Get-ReleaseAssetDescriptor $release $signatureAssetName $maxSignatureBytes
        $archiveName = "JobFlow-$([string]$release.tag_name)-windows-x64-complete.zip"

        $manifestMetadataLock = Receive-InstallerAsset (
            [Uri]$manifestDescriptor.Url
        ) $manifestPath ([long]$manifestDescriptor.Size) $allowedDownloadHosts "application/octet-stream" $manifestAssetName
        $signatureMetadataLock = Receive-InstallerAsset (
            [Uri]$signatureDescriptor.Url
        ) $signaturePath ([long]$signatureDescriptor.Size) $allowedDownloadHosts "application/octet-stream" $signatureAssetName
        if (
            [long]$manifestMetadataLock.Length -ne [long]$manifestDescriptor.Size -or
            [long]$signatureMetadataLock.Length -ne [long]$signatureDescriptor.Size
        ) { throw "JOBFLOW_INSTALL_RELEASE_ASSET_INVALID" }

        $describeInvocation = Invoke-StableBootstrap "DescribeManifest" $manifestPath $signaturePath $null
        try { [void](Assert-DescribeBootstrapResult $describeInvocation $manifestMetadataLock) }
        catch {
            try { [void](Get-ReleaseAssetDescriptor $release $archiveName $maxArchiveBytes) }
            catch {
                # The current public schema-v1/source release fails here with a
                # precise producer-contract error; no metadata is synthesized.
                throw "JOBFLOW_INSTALL_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED"
            }
            throw "JOBFLOW_INSTALL_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED"
        }
        try { $archiveDescriptor = Get-ReleaseAssetDescriptor $release $archiveName $maxArchiveBytes }
        catch { throw "JOBFLOW_INSTALL_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED" }

        $stagedArchivePath = Join-Path $stagingRoot $archiveName
        Write-Host "正在下载并验证完整运行时；现有私人数据保持不变…… / Downloading and verifying the complete runtime; existing private data remains unchanged..."
        $archiveIdentityLock = Receive-InstallerAsset (
            [Uri]$archiveDescriptor.Url
        ) $stagedArchivePath ([long]$archiveDescriptor.Size) $allowedDownloadHosts "application/octet-stream" $archiveName
        if ([long]$archiveIdentityLock.Length -ne [long]$archiveDescriptor.Size) {
            throw "JOBFLOW_INSTALL_RELEASE_ASSET_INVALID"
        }
        $expectedReleaseTag = [string]$release.tag_name
    }

    $activationInvocation = Invoke-StableBootstrap "Activate" $manifestPath $signaturePath $stagedArchivePath
    try { $activation = Assert-ActivationBootstrapResult $activationInvocation }
    catch {
        if ([string]$_.Exception.Message -eq "JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED") {
            throw "JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED"
        }
        throw "JOBFLOW_INSTALL_RECOVERY_REQUIRED"
    }
    if ("v$([string]$activation.version)" -cne $expectedReleaseTag) {
        throw "JOBFLOW_INSTALL_RECOVERY_REQUIRED"
    }
    try { $updated = Read-AndValidateV2CurrentPointer $activation $expectedReleaseTag }
    catch { throw "JOBFLOW_INSTALL_RECOVERY_REQUIRED" }

    # Stable scripts are copied only after the runtime and its persistent
    # signed activation evidence pass the source bootstrap's no-path verifier.
    try {
        $verified = Assert-VerifyInstalledBootstrapResult (
            Invoke-StableBootstrap "VerifyInstalled" $null $null $null
        )
    }
    catch { throw "JOBFLOW_INSTALL_VERIFY_REQUIRED" }
    if ([string]$verified.version -cne [string]$updated.version) {
        throw "JOBFLOW_INSTALL_VERIFY_REQUIRED"
    }
    Install-StableControlPlaneAtomic
    try {
        $verifiedAfterControlPlane = Assert-VerifyInstalledBootstrapResult (
            Invoke-StableBootstrap "VerifyInstalled" $null $null $null
        )
    }
    catch { throw "JOBFLOW_INSTALL_VERIFY_REQUIRED" }
    if ([string]$verifiedAfterControlPlane.version -cne [string]$updated.version) {
        throw "JOBFLOW_INSTALL_VERIFY_REQUIRED"
    }
    Invoke-VerifiedCompanionInstaller $updated

    Write-Host "JobFlow $([string]$updated.version) 已安全安装。 / JobFlow $([string]$updated.version) was installed safely."
    $scriptExitCode = 0
}
catch {
    $message = [string]$_.Exception.Message
    if ($acceptanceMode) {
        $acceptanceCause = if ($message -match '^JOBFLOW_[A-Z0-9_]+$') {
            $message
        }
        else {
            "JOBFLOW_INSTALL_FAILED"
        }
        [Console]::Error.WriteLine("JOBFLOW_INSTALL_ACCEPTANCE_CAUSE:" + $acceptanceCause)
    }
    if ($message -eq "JOBFLOW_INSTALL_RECOVERED_RETRY_REQUIRED") {
        $scriptErrorCode = $message
        $scriptExitCode = 6
    }
    elseif ($message -eq "JOBFLOW_INSTALL_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED") {
        $scriptErrorCode = $message
        $scriptExitCode = 2
    }
    elseif ($message -in @("JOBFLOW_INSTALL_RECOVERY_REQUIRED", "JOBFLOW_INSTALL_VERIFY_REQUIRED")) {
        $scriptErrorCode = $message
        $scriptExitCode = 3
    }
    elseif ($message -in @(
        "JOBFLOW_INSTALL_ALREADY_RUNNING_OR_COORDINATOR_INVALID",
        "JOBFLOW_INSTALL_RELEASE_METADATA_INVALID",
        "JOBFLOW_INSTALL_RELEASE_ASSET_INVALID",
        "JOBFLOW_INSTALL_STATE_INVALID",
        "JOBFLOW_INSTALL_STAGING_COLLISION",
        "JOBFLOW_INSTALL_STABLE_BOOTSTRAP_INVALID",
        "JOBFLOW_INSTALL_CONTROL_SOURCE_INVALID",
        "JOBFLOW_INSTALL_CONTROL_STAGE_INVALID",
        "JOBFLOW_INSTALL_CONTROL_PLANE_INVALID",
        "JOBFLOW_INSTALL_COMPANION_FAILED",
        "JOBFLOW_INSTALL_COMPANION_SOURCE_INVALID",
        "JOBFLOW_INSTALL_ACCEPTANCE_BYPASS_FORBIDDEN",
        "JOBFLOW_INSTALL_ACCEPTANCE_FIXTURE_INVALID",
        "JOBFLOW_INSTALL_LOCAL_ARCHIVE_INVALID",
        "JOBFLOW_PROJECT_ROOT_NOT_FOUND",
        "JOBFLOW_UPDATE_RELEASE_ASSET_URL_INVALID",
        "JOBFLOW_UPDATE_DOWNLOAD_FAILED",
        "JOBFLOW_UPDATE_DOWNLOAD_SIZE_INVALID",
        "JOBFLOW_UPDATE_DOWNLOAD_EMPTY",
        "JOBFLOW_UPDATE_DOWNLOAD_URI_FORBIDDEN",
        "JOBFLOW_UPDATE_DOWNLOAD_HOST_FORBIDDEN",
        "JOBFLOW_UPDATE_REDIRECT_INVALID",
        "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
    )) {
        $scriptErrorCode = $message
        $scriptExitCode = 1
    }
    else {
        $scriptErrorCode = "JOBFLOW_INSTALL_FAILED"
        $scriptExitCode = 1
    }
}
finally {
    if ($null -ne $archiveIdentityLock) { $archiveIdentityLock.Dispose() }
    if ($null -ne $localArchiveInputLock) { $localArchiveInputLock.Dispose() }
    if ($null -ne $localSignatureInputLock) { $localSignatureInputLock.Dispose() }
    if ($null -ne $localManifestInputLock) { $localManifestInputLock.Dispose() }
    if ($null -ne $signatureMetadataLock) { $signatureMetadataLock.Dispose() }
    if ($null -ne $manifestMetadataLock) { $manifestMetadataLock.Dispose() }
    if ($null -ne $releaseMetadataLock) { $releaseMetadataLock.Dispose() }
    if ($null -ne $powerShellExecutableLock) { $powerShellExecutableLock.Dispose() }
    Close-StableUpdaterDirectoryContext $stagingDirectoryContext
    $stagingDirectoryContext = $null
    if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
        try { Remove-OwnedInstallerTree $stagingRoot }
        catch { Write-Warning "JOBFLOW_INSTALL_STAGING_RESIDUE:$([IO.Path]::GetFileName($stagingRoot))" }
    }
    if ($null -ne $updateCoordinatorLock) { $updateCoordinatorLock.Dispose() }
    Close-StableUpdaterDirectoryContext $localArchiveDirectoryContext
    Close-StableUpdaterDirectoryContext $installerStateContext
}
if ($null -ne $scriptErrorCode) { [Console]::Error.WriteLine($scriptErrorCode) }
exit $scriptExitCode
