[CmdletBinding()]
param()

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

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND" }
$localAppDataRoot = (Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobOps"))
$localRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_UPDATE_INSTALLED_ROOT_INVALID"
}
$currentPointerPath = Join-Path $localRoot "current.json"
$bootstrapPath = Join-Path $localRoot "bin\jobflow-bootstrap.ps1"
$updateCoordinatorPath = Join-Path $localRoot ".jobflow-update.lock"
$updateId = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$stagingRoot = Join-Path $localRoot (".u-" + $updateId)
$archiveIdentityLock = $null
$powerShellExecutableLock = $null
$updateCoordinatorLock = $null
$releaseMetadataLock = $null
$manifestMetadataLock = $null
$signatureMetadataLock = $null
$stagingDirectoryContext = $null
$bootstrapSource = $null
$scriptExitCode = 1
$scriptErrorCode = $null

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
                        $output.Position = 0
                        $retainedOutput = $output
                        $output = $null
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

function Remove-SafeUpdateTree([string]$Path, [string]$Code) {
    $absolute = [IO.Path]::GetFullPath($Path)
    Assert-JobFlowLocalPath $absolute
    $leaf = [IO.Path]::GetFileName($absolute)
    if ($leaf -notmatch '^\.u-[0-9a-f]{12}$' -or -not (Test-Path -LiteralPath $absolute -PathType Container)) {
        throw $Code
    }
    function Remove-SafeUpdateDirectory([string]$Directory) {
        $directoryContext = Open-StableUpdaterDirectoryChain $Directory $Code
        try {
            Assert-StableUpdaterDirectoryContext $directoryContext $Code
            foreach ($child in @(Get-ChildItem -LiteralPath $Directory -Force -ErrorAction Stop)) {
                $childPath = [IO.Path]::GetFullPath($child.FullName)
                if (-not [IO.Path]::GetDirectoryName($childPath).Equals(
                    [IO.Path]::GetFullPath($Directory), [StringComparison]::OrdinalIgnoreCase
                )) { throw $Code }
                if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    # Preserve suspicious residue; never follow or recursively
                    # remove a junction supplied by another same-user process.
                    throw $Code
                }
                if ($child.PSIsContainer) {
                    Remove-SafeUpdateDirectory $childPath
                    continue
                }
                Assert-NoAlternateDataStreams $childPath $Code
                $stream = [IO.File]::Open(
                    $childPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite
                )
                try { Assert-OpenUpdaterFileAtPath $stream $childPath $Code }
                finally { $stream.Dispose() }
                [IO.File]::Delete($childPath)
            }
            Assert-StableUpdaterDirectoryContext $directoryContext $Code
            $last = @($directoryContext.Locks).Count - 1
            $leafLock = @($directoryContext.Locks)[$last]
            $leafLock.Handle.Dispose()
            $leafLock.Handle = $null
            # Non-recursive deletion cannot traverse a late replacement.  If
            # anything changed or a child appeared, deletion fails and residue
            # is intentionally retained for the next audited run.
            [IO.Directory]::Delete([IO.Path]::GetFullPath($Directory), $false)
        }
        finally { Close-StableUpdaterDirectoryContext $directoryContext }
    }
    Remove-SafeUpdateDirectory $absolute
}

function Remove-StaleUpdateTrees {
    foreach ($candidate in @(Get-ChildItem -LiteralPath $localRoot -Directory -Force -Filter ".u-*")) {
        if ($candidate.Name -notmatch '^\.u-[0-9a-f]{12}$') {
            throw "JOBFLOW_UPDATE_STAGING_RESIDUE_UNSAFE"
        }
        Remove-SafeUpdateTree $candidate.FullName "JOBFLOW_UPDATE_STAGING_RESIDUE_UNSAFE"
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
    $stream = Open-LockedUpdaterFile $bootstrapPath $maxBootstrapBytes "JOBFLOW_UPDATE_STABLE_BOOTSTRAP_INVALID"
    $bytes = $null
    try {
        $bytes = Read-LockedBytes $stream $maxBootstrapBytes "JOBFLOW_UPDATE_STABLE_BOOTSTRAP_INVALID"
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $source = $utf8.GetString($bytes).TrimStart([char]0xFEFF)
        if ([string]::IsNullOrWhiteSpace($source)) {
            throw "JOBFLOW_UPDATE_STABLE_BOOTSTRAP_INVALID"
        }
        return $source
    }
    catch { throw "JOBFLOW_UPDATE_STABLE_BOOTSTRAP_INVALID" }
    finally {
        if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
        $stream.Dispose()
    }
}

function Invoke-StableBootstrap(
    [ValidateSet("RecoverOnly", "DescribeManifest", "Activate")]
    [string]$Mode,
    [string]$ManifestPath,
    [string]$SignaturePath,
    [string]$ArchivePath
) {
    if ([string]::IsNullOrWhiteSpace($bootstrapSource) -or
        [string]::IsNullOrWhiteSpace($trustedWindowsPowerShell)) {
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }
    if ($Mode -eq "RecoverOnly") {
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
$encodedSource = [Console]::In.ReadToEnd().TrimStart([char]0xFEFF).Trim()
$sourceBytes = [Convert]::FromBase64String($encodedSource)
try {
    $source = [Text.UTF8Encoding]::new($false, $true).GetString($sourceBytes)
}
finally {
    if ($null -ne $sourceBytes) {
        [Array]::Clear($sourceBytes, 0, $sourceBytes.Length)
    }
}
$block = [ScriptBlock]::Create($source)
switch ($env:JOBFLOW_UPDATER_BOOTSTRAP_MODE) {
    "RecoverOnly" {
        & $block -RecoverOnly
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
    $bootstrapBytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($bootstrapSource)
    $encodedBootstrap = [Convert]::ToBase64String($bootstrapBytes)
    [Array]::Clear($bootstrapBytes, 0, $bootstrapBytes.Length)
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

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        # Transport the verified source as ASCII Base64.  This preserves the
        # exact UTF-8 bytes across Windows PowerShell/.NET stdin encodings.
        $process.StandardInput.Write($encodedBootstrap)
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
    finally {
        $encodedBootstrap = $null
        $process.Dispose()
    }
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
        $result.activation_performed -ne $true -or
        [int]$result.real_external_actions -ne 0 -or
        ($null -ne $result.PSObject.Properties["legacy_migration_performed"] -and
            ($result.legacy_migration_performed -isnot [bool] -or
             $result.legacy_migration_performed -ne $true))
    ) { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
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

try {
    Assert-JobFlowLocalPath $stagingRoot
    Assert-JobFlowLocalPath $bootstrapPath
    Assert-JobFlowLocalPath $currentPointerPath
    Assert-JobFlowLocalPath $updateCoordinatorPath
    try {
        $updateCoordinatorLock = [IO.File]::Open(
            $updateCoordinatorPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::Read
        )
        Assert-OpenUpdaterSingleLink $updateCoordinatorLock "JOBFLOW_UPDATE_COORDINATOR_INVALID"
        Assert-NoAlternateDataStreams $updateCoordinatorPath "JOBFLOW_UPDATE_COORDINATOR_INVALID"
    }
    catch { throw "JOBFLOW_UPDATE_ALREADY_RUNNING_OR_COORDINATOR_INVALID" }

    $bootstrapSource = Read-StableBootstrapSource
    $trustedPowerShellInfo = Get-TrustedWindowsPowerShell
    $trustedWindowsPowerShell = [string]$trustedPowerShellInfo.Path
    $powerShellExecutableLock = $trustedPowerShellInfo.Lock

    # Recovery is the first bootstrap action and precedes staging cleanup or
    # network access.  A recovered or ambiguous state never retries in-run.
    $recoveryInvocation = Invoke-StableBootstrap "RecoverOnly" $null $null $null
    try { Assert-RecoveryBootstrapResult $recoveryInvocation }
    catch {
        if ([string]$_.Exception.Message -eq "JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED") { throw }
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }

    Remove-StaleUpdateTrees
    $stagingDirectoryContext = New-StableUpdaterDirectoryRoot $stagingRoot "JOBFLOW_UPDATE_STAGING_COLLISION"
    $releasePath = Join-Path $stagingRoot "release.json"
    $manifestPath = Join-Path $stagingRoot $manifestAssetName
    $signaturePath = Join-Path $stagingRoot $signatureAssetName

    Write-Host "正在检查 JobFlow 的签名稳定版更新…… / Checking for a signed stable JobFlow update..."
    $releaseMetadataLock = Receive-AllowedHttpsFile (
        [Uri]$expectedApiUrl
    ) $releasePath $maxReleaseBytes @("api.github.com") "application/vnd.github+json"
    $release = Read-LockedJson $releaseMetadataLock $maxReleaseBytes "JOBFLOW_UPDATE_RELEASE_METADATA_INVALID"
    if (
        $release -isnot [PSCustomObject] -or
        $release.draft -ne $false -or
        $release.prerelease -ne $false -or
        ([string]$release.tag_name) -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$' -or
        @($release.assets).Count -lt 3 -or
        @($release.assets).Count -gt 64
    ) { throw "JOBFLOW_UPDATE_RELEASE_METADATA_INVALID" }

    $manifestDescriptor = Get-ReleaseAssetDescriptor $release $manifestAssetName $maxManifestBytes
    $signatureDescriptor = Get-ReleaseAssetDescriptor $release $signatureAssetName $maxSignatureBytes
    $archiveName = "JobFlow-$([string]$release.tag_name)-windows-x64-complete.zip"

    $manifestMetadataLock = Receive-AllowedHttpsFile (
        [Uri]$manifestDescriptor.Url
    ) $manifestPath ([long]$manifestDescriptor.Size) $allowedDownloadHosts "application/octet-stream"
    $signatureMetadataLock = Receive-AllowedHttpsFile (
        [Uri]$signatureDescriptor.Url
    ) $signaturePath ([long]$signatureDescriptor.Size) $allowedDownloadHosts "application/octet-stream"
    if (
        [long]$manifestMetadataLock.Length -ne [long]$manifestDescriptor.Size -or
        [long]$signatureMetadataLock.Length -ne [long]$signatureDescriptor.Size
    ) { throw "JOBFLOW_UPDATE_RELEASE_ASSET_INVALID" }

    $describeInvocation = Invoke-StableBootstrap "DescribeManifest" $manifestPath $signaturePath $null
    try { [void](Assert-DescribeBootstrapResult $describeInvocation $manifestMetadataLock) }
    catch {
        try { [void](Get-ReleaseAssetDescriptor $release $archiveName $maxArchiveBytes) }
        catch {
            # The current public schema-v1/source release fails here with a
            # precise producer-contract error; no metadata is synthesized.
            throw "JOBFLOW_UPDATE_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED"
        }
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }
    try { $archiveDescriptor = Get-ReleaseAssetDescriptor $release $archiveName $maxArchiveBytes }
    catch { throw "JOBFLOW_UPDATE_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED" }

    $archivePath = Join-Path $stagingRoot $archiveName
    Write-Host "正在下载并验证完整运行时；当前版本在激活完成前不会改变…… / Downloading and verifying the complete runtime; the current version remains unchanged until activation completes..."
    $archiveIdentityLock = Receive-AllowedHttpsFile (
        [Uri]$archiveDescriptor.Url
    ) $archivePath ([long]$archiveDescriptor.Size) $allowedDownloadHosts "application/octet-stream"
    if ([long]$archiveIdentityLock.Length -ne [long]$archiveDescriptor.Size) {
        throw "JOBFLOW_UPDATE_RELEASE_ASSET_INVALID"
    }

    $activationInvocation = Invoke-StableBootstrap "Activate" $manifestPath $signaturePath $archivePath
    try { $activation = Assert-ActivationBootstrapResult $activationInvocation }
    catch {
        if ([string]$_.Exception.Message -eq "JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED") { throw }
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }
    if ("v$([string]$activation.version)" -cne [string]$release.tag_name) {
        throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED"
    }
    try { $updated = Read-AndValidateV2CurrentPointer $activation ([string]$release.tag_name) }
    catch { throw "JOBFLOW_UPDATE_RECOVERY_REQUIRED" }
    Write-Host "JobFlow 已安全更新到 $([string]$updated.version)。请关闭旧窗口并重新打开 JobFlow。 / JobFlow was safely updated to $([string]$updated.version). Close the old window and start JobFlow again."
    $scriptExitCode = 0
}
catch {
    $message = [string]$_.Exception.Message
    if ($message -eq "JOBFLOW_UPDATE_RECOVERED_RETRY_REQUIRED") {
        $scriptErrorCode = $message
        $scriptExitCode = 6
    }
    elseif ($message -eq "JOBFLOW_UPDATE_SCHEMA_V2_COMPLETE_RUNTIME_REQUIRED") {
        $scriptErrorCode = $message
        $scriptExitCode = 2
    }
    elseif ($message -eq "JOBFLOW_UPDATE_RECOVERY_REQUIRED") {
        $scriptErrorCode = $message
        $scriptExitCode = 3
    }
    elseif ($message -in @(
        "JOBFLOW_UPDATE_ALREADY_RUNNING_OR_COORDINATOR_INVALID",
        "JOBFLOW_UPDATE_RELEASE_METADATA_INVALID",
        "JOBFLOW_UPDATE_RELEASE_ASSET_INVALID",
        "JOBFLOW_UPDATE_RELEASE_ASSET_URL_INVALID",
        "JOBFLOW_UPDATE_DOWNLOAD_FAILED",
        "JOBFLOW_UPDATE_DOWNLOAD_SIZE_INVALID",
        "JOBFLOW_UPDATE_DOWNLOAD_EMPTY",
        "JOBFLOW_UPDATE_DOWNLOAD_URI_FORBIDDEN",
        "JOBFLOW_UPDATE_DOWNLOAD_HOST_FORBIDDEN",
        "JOBFLOW_UPDATE_REDIRECT_INVALID",
        "JOBFLOW_UPDATE_STAGING_RESIDUE_UNSAFE",
        "JOBFLOW_UPDATE_STABLE_BOOTSTRAP_INVALID",
        "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
    )) {
        $scriptErrorCode = $message
        $scriptExitCode = 1
    }
    else {
        $scriptErrorCode = "JOBFLOW_UPDATE_FAILED"
        $scriptExitCode = 1
    }
}
finally {
    if ($null -ne $archiveIdentityLock) { $archiveIdentityLock.Dispose() }
    if ($null -ne $signatureMetadataLock) { $signatureMetadataLock.Dispose() }
    if ($null -ne $manifestMetadataLock) { $manifestMetadataLock.Dispose() }
    if ($null -ne $releaseMetadataLock) { $releaseMetadataLock.Dispose() }
    if ($null -ne $powerShellExecutableLock) { $powerShellExecutableLock.Dispose() }
    Close-StableUpdaterDirectoryContext $stagingDirectoryContext
    $stagingDirectoryContext = $null
    if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
        try { Remove-SafeUpdateTree $stagingRoot "JOBFLOW_UPDATE_STAGING_CLEANUP_FAILED" }
        catch { Write-Warning "JOBFLOW_UPDATE_STAGING_RESIDUE:$([IO.Path]::GetFileName($stagingRoot))" }
    }
    if ($null -ne $updateCoordinatorLock) { $updateCoordinatorLock.Dispose() }
}
if ($null -ne $scriptErrorCode) { [Console]::Error.WriteLine($scriptErrorCode) }
exit $scriptExitCode
