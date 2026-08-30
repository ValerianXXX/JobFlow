[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonArtifactPath,
    [Parameter(Mandatory = $true)][string]$SigstoreBundlePath,
    [Parameter(Mandatory = $true)][string]$WheelhousePath,
    [Parameter(Mandatory = $true)][string]$GitPath,
    [Parameter(Mandatory = $true)][string]$SourceCommit,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$ProjectRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Windows PowerShell 5.1 evaluates parameter default expressions before it
# initializes $PSScriptRoot.  Resolve the repository root only after binding so
# direct `-File` invocation has the same behavior as the explicit wrapper.
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

$runningOnWindows = $PSVersionTable.PSEdition -eq "Desktop" -or
    (Test-Path -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion")
if (-not $runningOnWindows) { throw "JOBFLOW_RUNTIME_BUILD_WINDOWS_REQUIRED" }
if ($SourceCommit -notmatch '^[0-9a-f]{40}$') { throw "JOBFLOW_RUNTIME_SOURCE_COMMIT_INVALID" }
$script:DeterministicSourceEpoch = "0"

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

# Keep these immutable complete-runtime archive limits byte-for-byte aligned
# with scripts/verify-windows-runtime-closure.ps1 and the installed bootstrap.
# The builder applies them before writing and again against the finished ZIP so
# a release artifact cannot be created that the independent verifier refuses.
$script:RuntimeArchiveMaximumEntries = 65535
$script:RuntimeArchiveMaximumEntryBytes = [long]536870912
$script:RuntimeArchiveMaximumUncompressedBytes = [long]1610612736
$script:RuntimeArchiveCompressionRatioMinimumBytes = [long]1048576
$script:RuntimeArchiveMaximumCompressionRatio = [double]200.0
$script:PinnedReleaseToolchainPolicySha256 = "sha256:146f0946644a50ec541c8f8f08a84755d852ed019053f6f9156b2c974c4c5598"
$script:PinnedProtectedBuilderPythonSha256 = "sha256:85b71d8c6ec1905935f74be0c9869aae198d00e98f39df699ec66f9c5a84cecd"
$script:PinnedProtectedBuilderSignerSubject = "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US"
$script:PinnedProtectedBuilderSignerThumbprint = "847785B686B2D3879731FA9AA3F1F5D48E85D99E"

function Initialize-AuthenticodeApi {
    if ("JobFlowAuthenticodeApi" -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;

public static class JobFlowAuthenticodeApi {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustFileInfo {
        internal uint cbStruct;
        internal IntPtr pcwszFilePath;
        internal IntPtr hFile;
        internal IntPtr pgKnownSubject;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustData {
        internal uint cbStruct;
        internal IntPtr pPolicyCallbackData;
        internal IntPtr pSIPClientData;
        internal uint dwUIChoice;
        internal uint fdwRevocationChecks;
        internal uint dwUnionChoice;
        internal IntPtr pFile;
        internal uint dwStateAction;
        internal IntPtr hWVTStateData;
        internal IntPtr pwszURLReference;
        internal uint dwProvFlags;
        internal uint dwUIContext;
        internal IntPtr pSignatureSettings;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct CryptProviderCertHeader {
        internal uint cbStruct;
        internal IntPtr pCert;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct CertContext {
        internal uint dwCertEncodingType;
        internal IntPtr pbCertEncoded;
        internal uint cbCertEncoded;
        internal IntPtr pCertInfo;
        internal IntPtr hCertStore;
    }

    [DllImport("wintrust.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern int WinVerifyTrust(IntPtr hwnd, [MarshalAs(UnmanagedType.LPStruct)] Guid action, IntPtr data);

    [DllImport("wintrust.dll", ExactSpelling = true)]
    private static extern IntPtr WTHelperProvDataFromStateData(IntPtr stateData);

    [DllImport("wintrust.dll", ExactSpelling = true)]
    private static extern IntPtr WTHelperGetProvSignerFromChain(
        IntPtr providerData,
        uint signerIndex,
        [MarshalAs(UnmanagedType.Bool)] bool counterSigner,
        uint counterSignerIndex);

    [DllImport("wintrust.dll", ExactSpelling = true)]
    private static extern IntPtr WTHelperGetProvCertFromChain(IntPtr signer, uint certificateIndex);

    private static X509Certificate2 CertificateFromTrustState(IntPtr stateData) {
        if (stateData == IntPtr.Zero) {
            throw new InvalidOperationException("AUTHENTICODE_STATE_INVALID");
        }
        IntPtr providerData = WTHelperProvDataFromStateData(stateData);
        if (providerData == IntPtr.Zero) {
            throw new InvalidOperationException("AUTHENTICODE_PROVIDER_DATA_INVALID");
        }
        IntPtr signer = WTHelperGetProvSignerFromChain(providerData, 0, false, 0);
        if (signer == IntPtr.Zero) {
            throw new InvalidOperationException("AUTHENTICODE_SIGNER_INVALID");
        }
        IntPtr providerCertificatePointer = WTHelperGetProvCertFromChain(signer, 0);
        if (providerCertificatePointer == IntPtr.Zero) {
            throw new InvalidOperationException("AUTHENTICODE_CERTIFICATE_INVALID");
        }
        var providerCertificate = (CryptProviderCertHeader)Marshal.PtrToStructure(
            providerCertificatePointer,
            typeof(CryptProviderCertHeader));
        if (
            providerCertificate.cbStruct < (uint)Marshal.SizeOf(typeof(CryptProviderCertHeader)) ||
            providerCertificate.pCert == IntPtr.Zero
        ) {
            throw new InvalidOperationException("AUTHENTICODE_CERTIFICATE_INVALID");
        }
        var certificateContext = (CertContext)Marshal.PtrToStructure(
            providerCertificate.pCert,
            typeof(CertContext));
        if (
            certificateContext.pbCertEncoded == IntPtr.Zero ||
            certificateContext.cbCertEncoded == 0 ||
            certificateContext.cbCertEncoded > 1048576
        ) {
            throw new InvalidOperationException("AUTHENTICODE_CERTIFICATE_INVALID");
        }
        var encodedCertificate = new byte[(int)certificateContext.cbCertEncoded];
        Marshal.Copy(certificateContext.pbCertEncoded, encodedCertificate, 0, encodedCertificate.Length);

        // Activating the long-standing byte[] constructor by reflection keeps
        // this helper source-compatible with both Windows PowerShell 5.1 and
        // PowerShell 7 without reopening the executable by path.
        var constructor = typeof(X509Certificate2).GetConstructor(new Type[] { typeof(byte[]) });
        if (constructor == null) {
            throw new InvalidOperationException("AUTHENTICODE_CERTIFICATE_LOADER_UNAVAILABLE");
        }
        var certificate = constructor.Invoke(new object[] { encodedCertificate }) as X509Certificate2;
        if (certificate == null) {
            throw new InvalidOperationException("AUTHENTICODE_CERTIFICATE_INVALID");
        }
        return certificate;
    }

    public static string[] VerifyEmbeddedSignature(string path) {
        var action = new Guid("00AAC56B-CD44-11D0-8CC2-00C04FC295EE");
        IntPtr pathPointer = IntPtr.Zero;
        IntPtr filePointer = IntPtr.Zero;
        IntPtr dataPointer = IntPtr.Zero;
        try {
            pathPointer = Marshal.StringToCoTaskMemUni(path);
            var file = new WinTrustFileInfo {
                cbStruct = (uint)Marshal.SizeOf(typeof(WinTrustFileInfo)),
                pcwszFilePath = pathPointer,
                hFile = IntPtr.Zero,
                pgKnownSubject = IntPtr.Zero
            };
            filePointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(WinTrustFileInfo)));
            Marshal.StructureToPtr(file, filePointer, false);
            var data = new WinTrustData {
                cbStruct = (uint)Marshal.SizeOf(typeof(WinTrustData)),
                pPolicyCallbackData = IntPtr.Zero,
                pSIPClientData = IntPtr.Zero,
                dwUIChoice = 2,
                fdwRevocationChecks = 0,
                dwUnionChoice = 1,
                pFile = filePointer,
                dwStateAction = 1,
                hWVTStateData = IntPtr.Zero,
                pwszURLReference = IntPtr.Zero,
                dwProvFlags = 0x00001000,
                dwUIContext = 0,
                pSignatureSettings = IntPtr.Zero
            };
            dataPointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(WinTrustData)));
            Marshal.StructureToPtr(data, dataPointer, false);
            int result = WinVerifyTrust(IntPtr.Zero, action, dataPointer);
            if (result != 0) { return new string[0]; }
            var verifiedData = (WinTrustData)Marshal.PtrToStructure(dataPointer, typeof(WinTrustData));
            using (var certificate2 = CertificateFromTrustState(verifiedData.hWVTStateData)) {
                if (String.IsNullOrWhiteSpace(certificate2.Subject) || String.IsNullOrWhiteSpace(certificate2.Thumbprint)) {
                    throw new InvalidOperationException("AUTHENTICODE_SIGNER_INVALID");
                }
                return new [] { certificate2.Subject, certificate2.Thumbprint.Replace(" ", "").ToUpperInvariant() };
            }
        }
        finally {
            if (dataPointer != IntPtr.Zero) {
                try {
                    var closeData = (WinTrustData)Marshal.PtrToStructure(dataPointer, typeof(WinTrustData));
                    closeData.dwStateAction = 2;
                    Marshal.StructureToPtr(closeData, dataPointer, false);
                    WinVerifyTrust(IntPtr.Zero, action, dataPointer);
                }
                catch { }
                Marshal.FreeHGlobal(dataPointer);
            }
            if (filePointer != IntPtr.Zero) { Marshal.FreeHGlobal(filePointer); }
            if (pathPointer != IntPtr.Zero) { Marshal.FreeCoTaskMem(pathPointer); }
        }
    }
}
'@
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return "sha256:" + (ConvertTo-LowerHex $sha.ComputeHash($stream)) }
        finally { $sha.Dispose() }
    }
    finally { $stream.Dispose() }
}

function ConvertTo-LowerHex([byte[]]$Bytes) {
    if ($null -eq $Bytes) { throw "JOBFLOW_RUNTIME_HASH_INVALID" }
    return ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant()
}

function Get-BytesSha256([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return "sha256:" + (ConvertTo-LowerHex $sha.ComputeHash($Bytes)) }
    finally { $sha.Dispose() }
}

function ConvertTo-UInt32Bits([int]$Value) {
    return [BitConverter]::ToUInt32([BitConverter]::GetBytes($Value), 0)
}

function Get-PortableJsonSha256([byte[]]$Bytes) {
    # The repository permits Git's Windows LF/CRLF checkout conversion for
    # *.lock.  Bind the exact UTF-8 document while treating only those two
    # transport encodings as equivalent; all other bytes/whitespace remain
    # integrity significant.
    try { $text = [Text.UTF8Encoding]::new($false, $true).GetString($Bytes) }
    catch { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
    $normalized = $text.Replace("`r`n", "`n")
    if ($normalized.Contains("`r")) { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
    return Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes($normalized))
}

function ConvertTo-NativeArgument([string]$Value) {
    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]'\') { $slashes++; continue }
        if ($character -eq [char]'"') {
            if ($slashes -gt 0) { [void]$builder.Append(('\' * ($slashes * 2))) }
            [void]$builder.Append('\"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) { [void]$builder.Append(('\' * $slashes)); $slashes = 0 }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) { [void]$builder.Append(('\' * ($slashes * 2))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Join-NativeArguments([string[]]$Values) {
    return [string]::Join(' ', @($Values | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }))
}

function Get-OrdinalSortedObjects(
    [object[]]$Values,
    [AllowEmptyString()][string]$PropertyName = "",
    [switch]$Descending,
    [switch]$IgnoreCase
) {
    $comparer = if ($IgnoreCase) { [StringComparer]::OrdinalIgnoreCase } else { [StringComparer]::Ordinal }
    $map = New-Object "System.Collections.Generic.Dictionary[string,object]" ($comparer)
    foreach ($value in @($Values)) {
        $key = if ([string]::IsNullOrEmpty($PropertyName)) {
            [string]$value
        }
        else {
            $property = $value.PSObject.Properties[$PropertyName]
            if ($null -eq $property) { throw "JOBFLOW_RUNTIME_ORDINAL_SORT_KEY_INVALID" }
            [string]$property.Value
        }
        if ([string]::IsNullOrEmpty($key) -or $map.ContainsKey($key)) {
            throw "JOBFLOW_RUNTIME_ORDINAL_SORT_KEY_INVALID"
        }
        $map.Add($key, $value)
    }
    $keys = [string[]]@($map.Keys)
    [Array]::Sort($keys, $comparer)
    if ($Descending) { [Array]::Reverse($keys) }
    foreach ($key in $keys) { $map[$key] }
}

$script:ReservedWindowsNames = @{
    "CON" = $true; "PRN" = $true; "AUX" = $true; "NUL" = $true
    "CONIN$" = $true; "CONOUT$" = $true; "CLOCK$" = $true
}
foreach ($index in 1..9) {
    $script:ReservedWindowsNames["COM$index"] = $true
    $script:ReservedWindowsNames["LPT$index"] = $true
}

function Get-NormalizedRuntimePath([string]$Value) {
    if (
        [string]::IsNullOrEmpty($Value) -or
        $Value.Length -gt 768 -or
        $Value -cne $Value.Normalize([Text.NormalizationForm]::FormC) -or
        $Value.Contains("\") -or $Value.Contains(":") -or
        $Value.StartsWith("/", [StringComparison]::Ordinal) -or
        $Value.EndsWith("/", [StringComparison]::Ordinal)
    ) { throw "JOBFLOW_RUNTIME_PAYLOAD_PATH_INVALID" }
    $parts = $Value.Split([char]'/', [StringSplitOptions]::None)
    foreach ($part in $parts) {
        if (
            [string]::IsNullOrEmpty($part) -or $part.Length -gt 255 -or
            $part -ceq "." -or $part -ceq ".." -or
            $part.EndsWith(" ", [StringComparison]::Ordinal) -or
            $part.EndsWith(".", [StringComparison]::Ordinal) -or
            $script:ReservedWindowsNames.ContainsKey($part.Split([char]'.', 2)[0].ToUpperInvariant())
        ) { throw "JOBFLOW_RUNTIME_PAYLOAD_PATH_INVALID" }
        foreach ($character in $part.ToCharArray()) {
            $code = [int]$character
            if ($code -lt 32 -or $code -gt 126 -or '"<>|?*'.Contains([string]$character)) {
                throw "JOBFLOW_RUNTIME_PAYLOAD_PATH_INVALID"
            }
        }
    }
    $canonical = [string]::Join('/', $parts)
    if ($canonical -cne $Value) { throw "JOBFLOW_RUNTIME_PAYLOAD_PATH_INVALID" }
    return $canonical
}

function Assert-OrdinaryInput([string]$Path, [switch]$Directory) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $leaf = [IO.Path]::GetFileName($absolute.TrimEnd([char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)))
    if (
        [string]::IsNullOrWhiteSpace($leaf) -or
        $leaf.EndsWith(" ", [StringComparison]::Ordinal) -or
        $leaf.EndsWith(".", [StringComparison]::Ordinal) -or
        $script:ReservedWindowsNames.ContainsKey($leaf.Split([char]'.', 2)[0].ToUpperInvariant())
    ) { throw "JOBFLOW_RUNTIME_BUILD_INPUT_INVALID" }
    $item = Get-Item -LiteralPath $absolute -Force -ErrorAction Stop
    if ([bool]$item.PSIsContainer -ne [bool]$Directory) { throw "JOBFLOW_RUNTIME_BUILD_INPUT_INVALID" }
    $cursor = $item
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_RUNTIME_BUILD_REPARSE_REJECTED"
        }
        $parent = [IO.Path]::GetDirectoryName($cursor.FullName)
        if ([string]::IsNullOrEmpty($parent) -or $parent -ceq $cursor.FullName) { break }
        $cursor = Get-Item -LiteralPath $parent -Force
    }
    if (-not $Directory) {
        try { $streams = @(Get-Item -LiteralPath $absolute -Stream * -ErrorAction Stop) }
        catch { throw "JOBFLOW_RUNTIME_BUILD_ADS_INSPECTION_FAILED" }
        if ($streams.Count -ne 1 -or [string]$streams[0].Stream -notin @("`$DATA", ":`$DATA")) {
            throw "JOBFLOW_RUNTIME_BUILD_ADS_REJECTED"
        }
    }
    return $absolute
}

function Initialize-RuntimeInputHandleApi {
    if ($null -ne ("JobFlowRuntimeInputHandleApi" -as [type])) { return }
    $null = Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

[StructLayout(LayoutKind.Sequential)]
internal struct JobFlowByHandleFileInformation
{
    internal uint FileAttributes;
    internal System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
    internal System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
    internal System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
    internal uint VolumeSerialNumber;
    internal uint FileSizeHigh;
    internal uint FileSizeLow;
    internal uint NumberOfLinks;
    internal uint FileIndexHigh;
    internal uint FileIndexLow;
}

public sealed class JobFlowRuntimeInputHandleIdentity
{
    public uint Attributes { get; internal set; }
    public uint Links { get; internal set; }
    public uint Volume { get; internal set; }
    public ulong FileIndex { get; internal set; }
    public long Size { get; internal set; }
    public string FinalPath { get; internal set; }
}

public static class JobFlowRuntimeInputHandleApi
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string path,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle,
        out JobFlowByHandleFileInformation information);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandleW(
        SafeFileHandle handle,
        StringBuilder path,
        uint pathLength,
        uint flags);

    public static SafeFileHandle OpenReadLockedDirectory(string path)
    {
        const uint FILE_LIST_DIRECTORY = 0x00000001;
        const uint FILE_READ_ATTRIBUTES = 0x00000080;
        const uint SYNCHRONIZE = 0x00100000;
        const uint FILE_SHARE_READ = 0x00000001;
        const uint OPEN_EXISTING = 3;
        const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
        SafeFileHandle handle = CreateFileW(
            path,
            FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            FILE_SHARE_READ,
            IntPtr.Zero,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);
        if (handle == null || handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            if (handle != null) handle.Dispose();
            throw new Win32Exception(error);
        }
        return handle;
    }

    public static JobFlowRuntimeInputHandleIdentity Inspect(SafeFileHandle handle)
    {
        if (handle == null || handle.IsInvalid || handle.IsClosed)
            throw new InvalidOperationException("JOBFLOW_RUNTIME_BUILD_INPUT_HANDLE_INVALID");

        JobFlowByHandleFileInformation information;
        if (!GetFileInformationByHandle(handle, out information))
            throw new Win32Exception(Marshal.GetLastWin32Error());

        int capacity = 512;
        string finalPath;
        while (true)
        {
            StringBuilder buffer = new StringBuilder(capacity);
            uint result = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
            if (result == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
            if (result < buffer.Capacity)
            {
                finalPath = buffer.ToString();
                break;
            }
            if (result > 32767) throw new InvalidOperationException("JOBFLOW_RUNTIME_BUILD_INPUT_PATH_INVALID");
            capacity = checked((int)result + 1);
        }

        ulong unsignedSize = ((ulong)information.FileSizeHigh << 32) | information.FileSizeLow;
        if (unsignedSize > long.MaxValue)
            throw new InvalidOperationException("JOBFLOW_RUNTIME_BUILD_INPUT_SIZE_INVALID");

        return new JobFlowRuntimeInputHandleIdentity {
            Attributes = information.FileAttributes,
            Links = information.NumberOfLinks,
            Volume = information.VolumeSerialNumber,
            FileIndex = ((ulong)information.FileIndexHigh << 32) | information.FileIndexLow,
            Size = (long)unsignedSize,
            FinalPath = finalPath
        };
    }
}
'@
}

function Get-HandleBoundRuntimePath([string]$Value) {
    if ($Value.StartsWith("\\?\UNC\", [StringComparison]::OrdinalIgnoreCase)) {
        return "\\" + $Value.Substring(8)
    }
    if ($Value.StartsWith("\\?\", [StringComparison]::OrdinalIgnoreCase)) {
        return $Value.Substring(4)
    }
    return $Value
}

function Enter-ProtectedRuntimeDirectoryLock([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    Initialize-RuntimeInputHandleApi
    $handle = $null
    try {
        $handle = [JobFlowRuntimeInputHandleApi]::OpenReadLockedDirectory($absolute)
        $identity = [JobFlowRuntimeInputHandleApi]::Inspect($handle)
        $final = [IO.Path]::GetFullPath((Get-HandleBoundRuntimePath ([string]$identity.FinalPath)))
        if (
            ([uint32]$identity.Attributes -band [uint32]0x10) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32]0x400) -ne 0 -or
            $final -cne $absolute
        ) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_DIRECTORY_INVALID" }
        $result = [pscustomobject]@{
            path = $absolute
            handle = $handle
            volume = [uint32]$identity.Volume
            file_index = [uint64]$identity.FileIndex
        }
        $script:ProtectedRuntimeDirectoryLocks.Add($result)
        $handle = $null
        return $result
    }
    catch { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_DIRECTORY_INVALID" }
    finally { if ($null -ne $handle) { $handle.Dispose() } }
}

function Assert-ProtectedRuntimeDirectoryLock([object]$Lock) {
    try {
        $identity = [JobFlowRuntimeInputHandleApi]::Inspect($Lock.handle)
        $final = [IO.Path]::GetFullPath((Get-HandleBoundRuntimePath ([string]$identity.FinalPath)))
    }
    catch { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_DIRECTORY_CHANGED" }
    if (
        [uint32]$identity.Volume -ne [uint32]$Lock.volume -or
        [uint64]$identity.FileIndex -ne [uint64]$Lock.file_index -or
        ([uint32]$identity.Attributes -band [uint32]0x10) -eq 0 -or
        ([uint32]$identity.Attributes -band [uint32]0x400) -ne 0 -or
        $final -cne [string]$Lock.path
    ) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_DIRECTORY_CHANGED" }
}

function Close-ProtectedRuntimeDirectoryLocks {
    for ($index = $script:ProtectedRuntimeDirectoryLocks.Count - 1; $index -ge 0; $index--) {
        try { $script:ProtectedRuntimeDirectoryLocks[$index].handle.Dispose() } catch { }
    }
    $script:ProtectedRuntimeDirectoryLocks.Clear()
    $script:ProtectedBuilderRuntime = $null
}

function Get-RetainedRuntimeInputHash([IO.FileStream]$Stream) {
    if ($null -eq $Stream -or -not $Stream.CanRead -or -not $Stream.CanSeek) {
        throw "JOBFLOW_RUNTIME_BUILD_INPUT_HANDLE_INVALID"
    }
    $position = $Stream.Position
    try {
        $Stream.Position = 0
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return "sha256:" + (ConvertTo-LowerHex $sha.ComputeHash($Stream)) }
        finally { $sha.Dispose() }
    }
    finally { $Stream.Position = $position }
}

function Assert-RetainedRuntimeInputIdentity([object]$RetainedInput, [switch]$VerifyHash) {
    if ($null -eq $RetainedInput -or $null -eq $RetainedInput.stream) {
        throw "JOBFLOW_RUNTIME_BUILD_INPUT_HANDLE_INVALID"
    }
    try { $identity = [JobFlowRuntimeInputHandleApi]::Inspect($RetainedInput.stream.SafeFileHandle) }
    catch { throw "JOBFLOW_RUNTIME_BUILD_INPUT_HANDLE_INVALID" }
    $attributes = [IO.FileAttributes]$identity.Attributes
    $finalPath = [IO.Path]::GetFullPath((Get-HandleBoundRuntimePath ([string]$identity.FinalPath)))
    if (
        ($attributes -band [IO.FileAttributes]::Directory) -ne 0 -or
        ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [uint32]$identity.Links -ne 1 -or
        [long]$identity.Size -ne [long]$RetainedInput.size -or
        [uint32]$identity.Volume -ne [uint32]$RetainedInput.volume -or
        [uint64]$identity.FileIndex -ne [uint64]$RetainedInput.file_index -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals($finalPath, [string]$RetainedInput.path)
    ) { throw "JOBFLOW_RUNTIME_BUILD_INPUT_CHANGED" }
    if ($VerifyHash -and (Get-RetainedRuntimeInputHash $RetainedInput.stream) -cne [string]$RetainedInput.sha256) {
        throw "JOBFLOW_RUNTIME_BUILD_INPUT_CHANGED"
    }
    $RetainedInput.stream.Position = 0
}

function Enter-RetainedRuntimeInput(
    [string]$Path,
    [long]$MinimumBytes = 1,
    [long]$MaximumBytes = 1073741824,
    [AllowNull()][string]$ExpectedSha256 = $null,
    [long]$ExpectedBytes = -1
) {
    $absolute = Assert-OrdinaryInput $Path
    if ($script:RetainedRuntimeInputsByPath.ContainsKey($absolute)) {
        $existing = $script:RetainedRuntimeInputsByPath[$absolute]
        Assert-RetainedRuntimeInputIdentity $existing -VerifyHash
        if (
            ($ExpectedBytes -ge 0 -and [long]$existing.size -ne $ExpectedBytes) -or
            (-not [string]::IsNullOrEmpty($ExpectedSha256) -and [string]$existing.sha256 -cne $ExpectedSha256)
        ) { throw "JOBFLOW_RUNTIME_BUILD_INPUT_CHANGED" }
        return $existing
    }
    $stream = $null
    try {
        # FileShare.Read deliberately withholds Write and Delete sharing.  The
        # exact file that is inspected remains immutable and name-bound until
        # every builder, pip and ZIP operation has finished.
        $stream = [IO.File]::Open($absolute, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        Initialize-RuntimeInputHandleApi
        $identity = [JobFlowRuntimeInputHandleApi]::Inspect($stream.SafeFileHandle)
        $attributes = [IO.FileAttributes]$identity.Attributes
        $finalPath = [IO.Path]::GetFullPath((Get-HandleBoundRuntimePath ([string]$identity.FinalPath)))
        if (
            ($attributes -band [IO.FileAttributes]::Directory) -ne 0 -or
            ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$identity.Links -ne 1 -or
            -not [StringComparer]::OrdinalIgnoreCase.Equals($finalPath, $absolute) -or
            [long]$identity.Size -lt $MinimumBytes -or [long]$identity.Size -gt $MaximumBytes -or
            ($ExpectedBytes -ge 0 -and [long]$identity.Size -ne $ExpectedBytes)
        ) { throw "JOBFLOW_RUNTIME_BUILD_INPUT_INVALID" }
        $sha256 = Get-RetainedRuntimeInputHash $stream
        if (-not [string]::IsNullOrEmpty($ExpectedSha256) -and $sha256 -cne $ExpectedSha256) {
            throw "JOBFLOW_RUNTIME_BUILD_INPUT_CHANGED"
        }
        $result = [pscustomobject]@{
            path = $absolute
            stream = $stream
            size = [long]$identity.Size
            sha256 = $sha256
            volume = [uint32]$identity.Volume
            file_index = [uint64]$identity.FileIndex
        }
        $script:RetainedRuntimeInputs.Add($result)
        $script:RetainedRuntimeInputsByPath[$absolute] = $result
        $stream = $null
        return $result
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
}

function Read-RetainedRuntimeInputBytes([object]$RetainedInput, [long]$MaximumBytes = 1048576) {
    Assert-RetainedRuntimeInputIdentity $RetainedInput
    if ([long]$RetainedInput.size -lt 1 -or [long]$RetainedInput.size -gt $MaximumBytes -or [long]$RetainedInput.size -gt [int]::MaxValue) {
        throw "JOBFLOW_RUNTIME_BUILD_JSON_INVALID"
    }
    $bytes = New-Object byte[] ([int]$RetainedInput.size)
    $offset = 0
    $RetainedInput.stream.Position = 0
    while ($offset -lt $bytes.Length) {
        $count = $RetainedInput.stream.Read($bytes, $offset, $bytes.Length - $offset)
        if ($count -le 0) { throw "JOBFLOW_RUNTIME_BUILD_INPUT_CHANGED" }
        $offset += $count
    }
    $RetainedInput.stream.Position = 0
    return ,$bytes
}

function Copy-RetainedRuntimeInput([object]$RetainedInput, [string]$Destination) {
    Assert-RetainedRuntimeInputIdentity $RetainedInput
    $parent = [IO.Path]::GetDirectoryName($Destination)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $output = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
    try {
        $RetainedInput.stream.Position = 0
        $RetainedInput.stream.CopyTo($output)
        $output.Flush($true)
    }
    finally {
        $output.Dispose()
        $RetainedInput.stream.Position = 0
    }
    if ((Get-Sha256 $Destination) -cne [string]$RetainedInput.sha256) { throw "JOBFLOW_RUNTIME_BUILD_INPUT_CHANGED" }
}

function Close-RetainedRuntimeInputs {
    for ($index = $script:RetainedRuntimeInputs.Count - 1; $index -ge 0; $index--) {
        try { $script:RetainedRuntimeInputs[$index].stream.Dispose() } catch { }
    }
    $script:RetainedRuntimeInputs.Clear()
    $script:RetainedRuntimeInputsByPath.Clear()
}

function Read-JsonObject([object]$RetainedInput, [long]$MaximumBytes = 1048576) {
    $bytes = Read-RetainedRuntimeInputBytes $RetainedInput $MaximumBytes
    if ($bytes.Length -lt 2 -or $bytes.Length -gt $MaximumBytes) { throw "JOBFLOW_RUNTIME_BUILD_JSON_INVALID" }
    try {
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes)
        $value = $text | ConvertFrom-Json
    }
    catch { throw "JOBFLOW_RUNTIME_BUILD_JSON_INVALID" }
    if ($null -eq $value) { throw "JOBFLOW_RUNTIME_BUILD_JSON_INVALID" }
    return [pscustomobject]@{ value = $value; bytes = $bytes; sha256 = Get-BytesSha256 $bytes }
}

function Read-BuilderPythonTrustPolicy([object]$RetainedInput, [string]$ToolName = "python") {
    $document = Read-JsonObject $RetainedInput
    $value = $document.value
    Assert-ExactProperties $value @(
        "schema_version", "tools", "python_execution_runtime", "javascript_dependencies"
    ) "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID"
    Assert-ExactProperties $value.tools @("node", "git", "python") "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID"
    if ($ToolName -notin @("python", "git")) { throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID" }
    $tool = $value.tools.$ToolName
    $toolProperties = @("file_names", "allowed_signers", "allowed_unsigned_sha256")
    if ($ToolName -ceq "git") { $toolProperties += "runtime_tree_sha256" }
    Assert-ExactProperties $tool $toolProperties "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID"
    if ([int]$value.schema_version -ne 1) { throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID" }

    $names = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($nameValue in @($tool.file_names)) {
        $name = [string]$nameValue
        if (
            [string]::IsNullOrWhiteSpace($name) -or
            [IO.Path]::GetFileName($name) -cne $name -or
            $name.Contains(":") -or $name.Contains("\") -or $name.Contains("/") -or
            -not $names.Add($name)
        ) { throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID" }
    }
    if ($names.Count -lt 1) { throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID" }

    $signers = New-Object System.Collections.Generic.List[object]
    $signerKeys = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::Ordinal)
    foreach ($signer in @($tool.allowed_signers)) {
        Assert-ExactProperties $signer @("subject", "thumbprint") "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID"
        $subject = [string]$signer.subject
        $thumbprint = [string]$signer.thumbprint
        $key = "$subject|$thumbprint"
        if (
            [string]::IsNullOrWhiteSpace($subject) -or $subject.Length -gt 512 -or
            $thumbprint -cnotmatch '^[0-9A-F]{40}$' -or
            -not $signerKeys.Add($key)
        ) { throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID" }
        $signers.Add([pscustomobject]@{ subject=$subject; thumbprint=$thumbprint })
    }

    $hashes = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::Ordinal)
    foreach ($hashValue in @($tool.allowed_unsigned_sha256)) {
        $hash = [string]$hashValue
        if ($hash -cnotmatch '^sha256:[0-9a-f]{64}$' -or -not $hashes.Add($hash)) {
            throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID"
        }
    }
    if ($signers.Count -eq 0 -and $hashes.Count -eq 0) {
        throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID"
    }
    $runtimeTreeSha256 = $null
    if ($ToolName -ceq "git") {
        $runtimeTreeSha256 = [string]$tool.runtime_tree_sha256
        if ($runtimeTreeSha256 -cnotmatch '^sha256:[0-9a-f]{64}$') {
            throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_INVALID"
        }
    }
    return [pscustomobject]@{
        document_sha256 = [string]$document.sha256
        file_names = $names
        allowed_signers = $signers
        allowed_unsigned_sha256 = $hashes
        runtime_tree_sha256 = $runtimeTreeSha256
    }
}

function Assert-RetainedToolTrust([object]$RetainedInput, [object]$Policy, [string]$UntrustedCode, [string]$CheckFailedCode) {
    # `$input` is a PowerShell automatic variable.  Do not use it as a
    # parameter name here: doing so silently substitutes the pipeline
    # enumerator and loses the retained SafeFileHandle.
    Assert-RetainedRuntimeInputIdentity $RetainedInput -VerifyHash
    $name = [IO.Path]::GetFileName([string]$RetainedInput.path)
    if (-not $Policy.file_names.Contains($name)) { throw $UntrustedCode }

    $trusted = $false
    $trustKind = $null
    $subject = $null
    $thumbprint = $null
    try {
        Initialize-AuthenticodeApi
        $signature = [JobFlowAuthenticodeApi]::VerifyEmbeddedSignature([string]$RetainedInput.path)
    }
    catch { throw $CheckFailedCode }
    Assert-RetainedRuntimeInputIdentity $RetainedInput -VerifyHash
    if ($null -ne $signature -and @($signature).Count -eq 2) {
        $subject = [string]$signature[0]
        $thumbprint = [string]$signature[1]
        foreach ($signer in $Policy.allowed_signers) {
            if (
                [StringComparer]::Ordinal.Equals([string]$signer.subject, $subject) -and
                [StringComparer]::Ordinal.Equals([string]$signer.thumbprint, $thumbprint)
            ) {
                $trusted = $true
                $trustKind = "AUTHENTICODE"
                break
            }
        }
    }
    if (-not $trusted -and $Policy.allowed_unsigned_sha256.Contains([string]$RetainedInput.sha256)) {
        $trusted = $true
        $trustKind = "PINNED_SHA256"
    }
    if (-not $trusted) { throw $UntrustedCode }
    Assert-RetainedRuntimeInputIdentity $RetainedInput -VerifyHash
    return [pscustomobject]@{
        trust = $trustKind
        sha256 = [string]$RetainedInput.sha256
        signer_subject = $subject
        signer_thumbprint = $thumbprint
    }
}

function Assert-BuilderPythonTrust([object]$Policy) {
    return Assert-RetainedToolTrust $script:BuilderPythonInput $Policy `
        "JOBFLOW_RUNTIME_BUILDER_PYTHON_UNTRUSTED" "JOBFLOW_RUNTIME_BUILDER_PYTHON_TRUST_CHECK_FAILED"
}

function Assert-ProtectedBuilderPythonTrust {
    Assert-ProtectedBuilderRuntime
    if ([string]$script:BuilderPythonInput.sha256 -cne $script:PinnedProtectedBuilderPythonSha256) {
        throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_UNTRUSTED"
    }
    Initialize-AuthenticodeApi
    try { $signature = [JobFlowAuthenticodeApi]::VerifyEmbeddedSignature([string]$script:BuilderPythonInput.path) }
    catch { throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_TRUST_CHECK_FAILED" }
    if (
        $signature.Count -ne 2 -or
        [string]$signature[0] -cne $script:PinnedProtectedBuilderSignerSubject -or
        [string]$signature[1] -cne $script:PinnedProtectedBuilderSignerThumbprint
    ) { throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_UNTRUSTED" }
    Assert-ProtectedBuilderRuntime
    return [pscustomobject]@{
        trust = "PINNED_OFFICIAL_ARCHIVE_AND_AUTHENTICODE"
        sha256 = [string]$script:BuilderPythonInput.sha256
        signer_subject = [string]$signature[0]
        signer_thumbprint = [string]$signature[1]
    }
}

function Get-RetainedGitRuntimeClosure([object]$GitInput) {
    Assert-RetainedRuntimeInputIdentity $GitInput -VerifyHash
    $gitPath = [IO.Path]::GetFullPath([string]$GitInput.path)
    $binRoot = [IO.Path]::GetDirectoryName($gitPath)
    $mingwRoot = [IO.Path]::GetDirectoryName($binRoot)
    if (
        [IO.Path]::GetFileName($gitPath) -cne "git.exe" -or
        [IO.Path]::GetFileName($binRoot) -cne "bin" -or
        [IO.Path]::GetFileName($mingwRoot) -cne "mingw64"
    ) { throw "JOBFLOW_RUNTIME_SOURCE_GIT_LAUNCHER_REJECTED" }
    [void](Assert-OrdinaryInput $binRoot -Directory)
    $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $records = New-Object System.Collections.Generic.List[object]
    $dllCount = 0
    $items = @(Get-OrdinalSortedObjects @(Get-ChildItem -LiteralPath $binRoot -Force) "Name")
    if ($items.Count -lt 2 -or $items.Count -gt 512) { throw "JOBFLOW_RUNTIME_SOURCE_GIT_CLOSURE_INVALID" }
    foreach ($item in $items) {
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.LinkType) {
            throw "JOBFLOW_RUNTIME_SOURCE_GIT_CLOSURE_INVALID"
        }
        if (-not $aliases.Add([string]$item.Name)) { throw "JOBFLOW_RUNTIME_SOURCE_GIT_CLOSURE_INVALID" }
        $retained = Enter-RetainedRuntimeInput $item.FullName 1 536870912
        if ($item.Extension -ceq ".dll") { $dllCount++ }
        $records.Add([ordered]@{ name=[string]$item.Name; size=[long]$retained.size; sha256=[string]$retained.sha256 })
    }
    if ($dllCount -lt 1 -or -not $aliases.Contains("git.exe")) {
        throw "JOBFLOW_RUNTIME_SOURCE_GIT_CLOSURE_INVALID"
    }
    $material = [ordered]@{
        digest_format = "JOBFLOW_GIT_RUNTIME_CLOSURE_DIGEST_V1"
        files = $records.ToArray()
    }
    return [pscustomobject]@{
        sha256 = Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $material)))
        file_count = $records.Count
    }
}

function Assert-ExactProperties([object]$Value, [string[]]$Expected, [string]$Code) {
    $actual = @(Get-OrdinalSortedObjects @($Value.PSObject.Properties.Name))
    $wanted = @(Get-OrdinalSortedObjects @($Expected))
    if ($actual.Count -ne $wanted.Count) { throw $Code }
    for ($index = 0; $index -lt $wanted.Count; $index++) {
        if ([string]$actual[$index] -cne [string]$wanted[$index]) { throw $Code }
    }
}

function Read-Lock([object]$RetainedInput, [string]$ExpectedType) {
    $document = Read-JsonObject $RetainedInput
    $value = $document.value
    $expectedProperties = @("schema_version", "lock_type", "python_tag", "platform", "only_binary", "packages")
    if ($ExpectedType -ceq "runtime-wheelhouse") { $expectedProperties += "abi" }
    Assert-ExactProperties $value $expectedProperties "JOBFLOW_RUNTIME_LOCK_INVALID"
    if (
        [int]$value.schema_version -ne 1 -or
        [string]$value.lock_type -cne $ExpectedType -or
        -not ($value.only_binary -is [bool]) -or -not [bool]$value.only_binary
    ) { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
    if ($ExpectedType -ceq "runtime-wheelhouse") {
        if (
            [string]$value.python_tag -cne "cp313" -or
            [string]$value.abi -cne "cp313-or-abi3" -or
            [string]$value.platform -cne "win_amd64"
        ) { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
    }
    elseif ([string]$value.python_tag -cne "py3" -or [string]$value.platform -cne "any") {
        throw "JOBFLOW_RUNTIME_LOCK_INVALID"
    }
    $names = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $files = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($package in @($value.packages)) {
        Assert-ExactProperties $package @("name", "version", "filename", "size", "sha256") "JOBFLOW_RUNTIME_LOCK_INVALID"
        if (
            [string]$package.name -notmatch '^[A-Za-z0-9_.-]+$' -or
            [string]$package.version -notmatch '^[A-Za-z0-9_.+-]+$' -or
            [string]$package.filename -notmatch '^[A-Za-z0-9_.+-]+\.whl$' -or
            [long]$package.size -lt 1 -or
            [string]$package.sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            -not $names.Add([string]$package.name) -or
            -not $files.Add([string]$package.filename)
        ) { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
    }
    if ($names.Count -eq 0) { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
    return [pscustomobject]@{ value = $value; sha256 = Get-PortableJsonSha256 $document.bytes }
}

function Assert-ExactWheelhouse([string]$Root, [object[]]$Packages) {
    $absolute = Assert-OrdinaryInput $Root -Directory
    $expected = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $inputs = New-Object "System.Collections.Generic.Dictionary[string,object]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($package in $Packages) {
        $name = [string]$package.filename
        $expected.Add($name) | Out-Null
        $path = Join-Path $absolute $name
        $retainedInput = Enter-RetainedRuntimeInput $path 1 1073741824 ([string]$package.sha256) ([long]$package.size)
        if ([long]$retainedInput.size -ne [long]$package.size -or [string]$retainedInput.sha256 -cne [string]$package.sha256) {
            throw "JOBFLOW_RUNTIME_WHEELHOUSE_HASH_MISMATCH"
        }
        $inputs[$name] = $retainedInput
    }
    $actual = @(Get-ChildItem -LiteralPath $absolute -Force)
    if ($actual.Count -ne $expected.Count) { throw "JOBFLOW_RUNTIME_WHEELHOUSE_INVENTORY_MISMATCH" }
    foreach ($item in $actual) {
        if ($item.PSIsContainer -or -not $expected.Contains($item.Name)) {
            throw "JOBFLOW_RUNTIME_WHEELHOUSE_INVENTORY_MISMATCH"
        }
    }
    $packageRecords = @(Get-OrdinalSortedObjects @($Packages) "filename" | ForEach-Object {
        [ordered]@{ filename = [string]$_.filename; sha256 = [string]$_.sha256; size = [long]$_.size }
    })
    return [pscustomobject]@{
        root = $absolute
        package_records = $packageRecords
        inputs = $inputs
    }
}

function Complete-ExactWheelhouseIdentity([object]$Identity) {
    $canonical = ConvertTo-CanonicalJson ([ordered]@{
        digest_format = "JOBFLOW_WHEELHOUSE_TREE_DIGEST_V1"
        packages = @($Identity.package_records)
    })
    return [pscustomobject]@{
        root = [string]$Identity.root
        tree_sha256 = Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes($canonical))
        inputs = $Identity.inputs
    }
}

function Get-ProtectedBuilderTreeIdentity([object]$Snapshot) {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("JOBFLOW_PROTECTED_BUILDER_TREE_DIGEST_V1")
    $fileCount = 0
    $directoryCount = 0
    foreach ($record in @(Get-OrdinalSortedObjects @($Snapshot.records) "relative")) {
        $pathBytes = [Text.UTF8Encoding]::new($false).GetBytes([string]$record.relative)
        $encodedPath = [Convert]::ToBase64String($pathBytes)
        if ([string]$record.kind -ceq "directory") {
            $directoryCount++
            $lines.Add("D|$encodedPath")
            continue
        }
        Assert-RetainedRuntimeInputIdentity $record.input -VerifyHash
        $fileCount++
        $lines.Add("F|$encodedPath|$([long]$record.input.size)|$([string]$record.input.sha256)")
    }
    $material = [Text.UTF8Encoding]::new($false).GetBytes([string]::Join("`n", $lines) + "`n")
    return [pscustomobject]@{
        tree_sha256 = Get-BytesSha256 $material
        file_count = [long]$fileCount
        directory_count = [long]$directoryCount
    }
}

function Get-ProtectedBuilderArchiveInventory([object]$ArchiveInput) {
    Assert-RetainedRuntimeInputIdentity $ArchiveInput -VerifyHash
    $files = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $stream = $ArchiveInput.stream
    $stream.Position = 0
    try {
        $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try {
            foreach ($entry in $zip.Entries) {
                if ($entry.FullName.EndsWith("/", [StringComparison]::Ordinal)) { continue }
                $relative = Get-NormalizedRuntimePath $entry.FullName
                $unixMode = ((ConvertTo-UInt32Bits ([int]$entry.ExternalAttributes)) -shr 16) -band 0xF000
                if ($unixMode -ne 0 -and $unixMode -ne 0x8000 -or -not $files.Add($relative)) {
                    throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_ARCHIVE_INVALID"
                }
            }
        }
        finally { $zip.Dispose() }
    }
    finally { $stream.Position = 0 }
    if ($files.Count -lt 10 -or $files.Count -gt 128 -or -not $files.Contains("python313._pth")) {
        throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_ARCHIVE_INVALID"
    }
    Assert-RetainedRuntimeInputIdentity $ArchiveInput -VerifyHash
    return ,$files
}

function Initialize-ProtectedBuilderRuntime(
    [string]$BuildRoot,
    [object]$Source,
    [object]$BuildLock,
    [object]$WheelhouseIdentity
) {
    if ($null -ne $script:ProtectedBuilderRuntime) {
        throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_ALREADY_INITIALIZED"
    }
    $root = Join-Path $BuildRoot "protected-builder"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    $expectedFiles = Get-ProtectedBuilderArchiveInventory $script:PythonArtifactInput
    Expand-SafeZip $script:PythonArtifactInput $root

    $pipPackages = @($BuildLock.value.packages | Where-Object { [string]$_.name -ceq "pip" })
    if (
        $pipPackages.Count -ne 1 -or
        [string]$pipPackages[0].version -cne [string]$Source.builder.pip_version
    ) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_PIP_INVALID" }
    $pipPackage = $pipPackages[0]
    $pipInput = $WheelhouseIdentity.inputs[[string]$pipPackage.filename]
    if ($null -eq $pipInput) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_PIP_INVALID" }

    $pthPath = Join-Path $root "python313._pth"
    if (-not [IO.File]::Exists($pthPath)) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_ARCHIVE_INVALID" }
    [IO.File]::Delete($pthPath)
    $pipRelative = "wheelhouse/$([string]$pipPackage.filename)"
    $pthText = "python313.zip`n.`n$($pipRelative.Replace('/', '\'))`n"
    Write-Utf8NoBom $pthPath $pthText
    $pipDestination = Join-Path $root ($pipRelative.Replace('/', [IO.Path]::DirectorySeparatorChar))
    Copy-RetainedRuntimeInput $pipInput $pipDestination
    [void]$expectedFiles.Add($pipRelative)

    $expectedDirectories = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($relative in $expectedFiles) {
        $parts = ([string]$relative).Split([char]'/', [StringSplitOptions]::None)
        $parent = ""
        for ($index = 0; $index -lt ($parts.Count - 1); $index++) {
            $parent = if ([string]::IsNullOrEmpty($parent)) { $parts[$index] } else { "$parent/$($parts[$index])" }
            [void]$expectedDirectories.Add($parent)
        }
    }

    $snapshot = Get-RetainedTreeSnapshot $root
    $actualFiles = New-Object "System.Collections.Generic.Dictionary[string,object]" ([StringComparer]::OrdinalIgnoreCase)
    $actualDirectories = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in $snapshot.records) {
        if ([string]$record.kind -ceq "directory") {
            [void]$actualDirectories.Add([string]$record.relative)
        }
        else {
            $actualFiles[[string]$record.relative] = $record.input
        }
    }
    if ($actualFiles.Count -ne $expectedFiles.Count -or $actualDirectories.Count -ne $expectedDirectories.Count) {
        throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_INVENTORY_MISMATCH"
    }
    foreach ($relative in $expectedFiles) {
        if (-not $actualFiles.ContainsKey([string]$relative)) {
            throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_INVENTORY_MISMATCH"
        }
    }
    foreach ($relative in $expectedDirectories) {
        if (-not $actualDirectories.Contains([string]$relative)) {
            throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_INVENTORY_MISMATCH"
        }
    }

    foreach ($required in @(
        "python.exe", "python3.dll", "python313.dll", "python313.zip", "python313._pth",
        "vcruntime140.dll", "vcruntime140_1.dll", "_hashlib.pyd", "unicodedata.pyd", "select.pyd", $pipRelative
    )) {
        if (-not $actualFiles.ContainsKey($required)) {
            throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_INVENTORY_MISMATCH"
        }
    }
    $pthInput = $actualFiles["python313._pth"]
    $actualPth = [Text.UTF8Encoding]::new($false, $true).GetString((Read-RetainedRuntimeInputBytes $pthInput 4096))
    if ($actualPth -cne $pthText) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_PTH_INVALID" }
    $retainedPip = $actualFiles[$pipRelative]
    if (
        [long]$retainedPip.size -ne [long]$pipInput.size -or
        [string]$retainedPip.sha256 -cne [string]$pipInput.sha256
    ) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_PIP_INVALID" }

    [void](Enter-ProtectedRuntimeDirectoryLock $root)
    foreach ($relative in @(Get-OrdinalSortedObjects @($actualDirectories))) {
        [void](Enter-ProtectedRuntimeDirectoryLock (Join-Path $root ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))))
    }
    $treeIdentity = Get-ProtectedBuilderTreeIdentity $snapshot
    $script:BuilderPythonInput = $actualFiles["python.exe"]
    $script:BuilderPython = [string]$script:BuilderPythonInput.path
    $script:ProtectedBuilderRuntime = [pscustomobject]@{
        root = [IO.Path]::GetFullPath($root)
        snapshot = $snapshot
        tree_sha256 = [string]$treeIdentity.tree_sha256
        file_count = [long]$treeIdentity.file_count
        directory_count = [long]$treeIdentity.directory_count
        artifact_sha256 = [string]$script:PythonArtifactInput.sha256
        pip_wheel_sha256 = [string]$pipInput.sha256
        pth_sha256 = [string]$pthInput.sha256
    }
    Assert-ProtectedBuilderRuntime
    $script:BuilderPythonTrust = Assert-ProtectedBuilderPythonTrust
    return $script:ProtectedBuilderRuntime
}

function Assert-ProtectedBuilderRuntime {
    $runtime = $script:ProtectedBuilderRuntime
    if ($null -eq $runtime -or $null -eq $runtime.snapshot -or $null -eq $script:BuilderPythonInput) {
        throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_NOT_INITIALIZED"
    }
    $expectedPython = [IO.Path]::GetFullPath((Join-Path $runtime.root "python.exe"))
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals([string]$script:BuilderPythonInput.path, $expectedPython)) {
        throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_CHANGED"
    }
    foreach ($lock in $script:ProtectedRuntimeDirectoryLocks) {
        Assert-ProtectedRuntimeDirectoryLock $lock
    }
    foreach ($record in $runtime.snapshot.records) {
        if ([string]$record.kind -ceq "file") {
            Assert-RetainedRuntimeInputIdentity $record.input -VerifyHash
        }
    }
    $expected = New-Object "System.Collections.Generic.Dictionary[string,string]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in $runtime.snapshot.records) { $expected[[string]$record.relative] = [string]$record.kind }
    $actualItems = @(Get-OrdinalSortedObjects @(Get-ChildItem -LiteralPath $runtime.root -Force -Recurse) "FullName")
    if ($actualItems.Count -ne $expected.Count) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_CHANGED" }
    foreach ($item in $actualItems) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.LinkType) {
            throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_CHANGED"
        }
        $relative = Get-NormalizedRuntimePath $item.FullName.Substring($runtime.root.Length).TrimStart([IO.Path]::DirectorySeparatorChar).Replace('\', '/')
        $kind = if ($item.PSIsContainer) { "directory" } else { "file" }
        if (-not $expected.ContainsKey($relative) -or [string]$expected[$relative] -cne $kind) {
            throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_CHANGED"
        }
    }
    $identity = Get-ProtectedBuilderTreeIdentity $runtime.snapshot
    if (
        [string]$identity.tree_sha256 -cne [string]$runtime.tree_sha256 -or
        [long]$identity.file_count -ne [long]$runtime.file_count -or
        [long]$identity.directory_count -ne [long]$runtime.directory_count
    ) { throw "JOBFLOW_RUNTIME_PROTECTED_BUILDER_CHANGED" }
    Assert-RetainedRuntimeInputIdentity $script:BuilderPythonInput -VerifyHash
}

function Invoke-BuilderPython(
    [string[]]$Arguments,
    [string]$WorkingDirectory,
    [AllowNull()][string]$StandardInput = $null,
    [AllowNull()][string]$TemporaryRoot = $null
) {
    Assert-ProtectedBuilderRuntime
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $script:BuilderPython
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardInput = $null -ne $StandardInput
    $utf8NoBom = [Text.UTF8Encoding]::new($false)
    $start.StandardOutputEncoding = $utf8NoBom
    $start.StandardErrorEncoding = $utf8NoBom
    $start.Arguments = Join-NativeArguments $Arguments
    $start.EnvironmentVariables.Clear()
    foreach ($name in @("SystemRoot", "WINDIR", "COMSPEC")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { $start.EnvironmentVariables[$name] = $value }
    }
    if ([string]::IsNullOrWhiteSpace($TemporaryRoot)) {
        if ([string]::IsNullOrWhiteSpace([string]$script:SourceBuildRoot)) {
            throw "JOBFLOW_RUNTIME_BUILD_TEMP_ROOT_UNAVAILABLE"
        }
        $TemporaryRoot = Join-Path $script:SourceBuildRoot "builder-tmp"
    }
    $temporary = [IO.Path]::GetFullPath($TemporaryRoot)
    [IO.Directory]::CreateDirectory($temporary) | Out-Null
    $start.EnvironmentVariables["TEMP"] = $temporary
    $start.EnvironmentVariables["TMP"] = $temporary
    $start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1"
    $start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
    $start.EnvironmentVariables["PIP_CONFIG_FILE"] = "NUL"
    $start.EnvironmentVariables["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    $start.EnvironmentVariables["PIP_NO_INDEX"] = "1"
    $start.EnvironmentVariables["NO_PROXY"] = "*"
    $start.EnvironmentVariables["SOURCE_DATE_EPOCH"] = [string]$script:DeterministicSourceEpoch
    $start.EnvironmentVariables["PYTHONHASHSEED"] = "0"
    $start.EnvironmentVariables["TZ"] = "UTC"
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_FAILED" }
        if ($null -ne $StandardInput) {
            $inputBytes = $utf8NoBom.GetBytes($StandardInput)
            try {
                $process.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
                $process.StandardInput.BaseStream.Flush()
            }
            finally {
                $process.StandardInput.Close()
                [Array]::Clear($inputBytes, 0, $inputBytes.Length)
            }
        }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_FAILED" }
        return [pscustomobject]@{ stdout = $stdout.Trim(); stderr = $stderr.Trim() }
    }
    finally {
        $process.Dispose()
        Assert-ProtectedBuilderRuntime
    }
}

function ConvertTo-CanonicalJson([object]$Value) {
    $transport = $Value | ConvertTo-Json -Depth 100 -Compress
    $transportBytes = [Text.UTF8Encoding]::new($false).GetBytes($transport)
    $standardInput = [Convert]::ToBase64String($transportBytes)
    $program = @'
import base64,json,sys
payload=sys.stdin.buffer.read()
# Windows PowerShell 5.1's redirected StreamWriter emits exactly one UTF-8
# preamble and exposes no StandardInputEncoding property.  Accept only that
# platform preamble; strict base64 validation still rejects any other prefix,
# suffix, whitespace, or injected data.
if payload.startswith(b"\xef\xbb\xbf"): payload=payload[3:]
raw=base64.b64decode(payload,validate=True)
value=json.loads(raw.decode("utf-8"),parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
sys.stdout.write(json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":")))
'@
    try {
        $result = Invoke-BuilderPython @("-I", "-c", $program) $script:Project $standardInput
    }
    catch {
        if ($_.Exception.Message -ceq "JOBFLOW_RUNTIME_BUILDER_PYTHON_FAILED") {
            throw "JOBFLOW_RUNTIME_CANONICAL_JSON_PROCESS_FAILED"
        }
        throw
    }
    if ([string]::IsNullOrWhiteSpace($result.stdout) -or -not [string]::IsNullOrWhiteSpace($result.stderr)) {
        throw "JOBFLOW_RUNTIME_CANONICAL_JSON_FAILED"
    }
    return [string]$result.stdout
}

function Assert-BuilderPython([object]$Source) {
    try {
        $probe = Invoke-BuilderPython @("-I", "-c", "import platform,sys;print(sys.version.split()[0]+'|'+platform.machine())") $script:Project
    }
    catch {
        if ($_.Exception.Message -ceq "JOBFLOW_RUNTIME_BUILDER_PYTHON_FAILED") {
            throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_IDENTITY_PROBE_FAILED"
        }
        throw
    }
    if ($probe.stdout -cne "$($Source.builder.python_version)|$($Source.builder.python_architecture)" -or -not [string]::IsNullOrWhiteSpace($probe.stderr)) {
        throw "JOBFLOW_RUNTIME_BUILDER_PYTHON_IDENTITY_INVALID"
    }
    try {
        $pip = Invoke-BuilderPython @("-I", "-m", "pip", "--version") $script:Project
    }
    catch {
        if ($_.Exception.Message -ceq "JOBFLOW_RUNTIME_BUILDER_PYTHON_FAILED") {
            throw "JOBFLOW_RUNTIME_BUILDER_PIP_PROBE_FAILED"
        }
        throw
    }
    if ($pip.stdout -notmatch ('^pip ' + [Regex]::Escape([string]$Source.builder.pip_version) + ' ')) {
        throw "JOBFLOW_RUNTIME_BUILDER_PIP_IDENTITY_INVALID"
    }
    return [pscustomobject]@{ probe = $probe.stdout; pip = $pip.stdout }
}

function Invoke-TrustedGit([string[]]$Arguments) {
    Assert-RetainedRuntimeInputIdentity $script:GitInput -VerifyHash
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = [string]$script:GitInput.path
    $start.WorkingDirectory = $script:Project
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $gitArguments = @(
        "--no-pager", "--no-replace-objects", "--no-optional-locks",
        "-c", "core.hooksPath=NUL", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "core.preloadindex=false", "-c", "core.autocrlf=false", "-c", "core.safecrlf=true",
        "-C", $script:Project
    ) + $Arguments
    $start.Arguments = Join-NativeArguments $gitArguments
    $start.EnvironmentVariables.Clear()
    foreach ($name in @("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { $start.EnvironmentVariables[$name] = $value }
    }
    $start.EnvironmentVariables["GIT_CONFIG_NOSYSTEM"] = "1"
    $start.EnvironmentVariables["GIT_CONFIG_GLOBAL"] = "NUL"
    $start.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0"
    $start.EnvironmentVariables["GIT_OPTIONAL_LOCKS"] = "0"
    $start.EnvironmentVariables["GIT_NO_REPLACE_OBJECTS"] = "1"
    $start.EnvironmentVariables["GIT_ATTR_NOSYSTEM"] = "1"
    $start.EnvironmentVariables["GIT_LITERAL_PATHSPECS"] = "1"
    $start.EnvironmentVariables["GIT_PAGER"] = "cat"
    $start.EnvironmentVariables["PAGER"] = "cat"
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_RUNTIME_SOURCE_GIT_FAILED" }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($stderr)) {
            throw "JOBFLOW_RUNTIME_SOURCE_GIT_FAILED"
        }
        return [pscustomobject]@{ stdout=$stdout; stderr=$stderr }
    }
    finally {
        $process.Dispose()
        Assert-RetainedRuntimeInputIdentity $script:GitInput -VerifyHash
    }
}

function Get-TrustedSourceIdentity {
    $top = (Invoke-TrustedGit @("rev-parse", "--show-toplevel")).stdout.Trim()
    if ([IO.Path]::GetFullPath($top).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $script:Project.TrimEnd([IO.Path]::DirectorySeparatorChar)) {
        throw "JOBFLOW_RUNTIME_SOURCE_REPOSITORY_MISMATCH"
    }
    if (-not [string]::IsNullOrWhiteSpace((Invoke-TrustedGit @("status", "--porcelain=v1", "--untracked-files=all")).stdout)) {
        throw "JOBFLOW_RUNTIME_SOURCE_WORKTREE_DIRTY"
    }
    $head = (Invoke-TrustedGit @("rev-parse", "--verify", "HEAD^{commit}")).stdout.Trim()
    if ($head -cne $SourceCommit) { throw "JOBFLOW_RUNTIME_SOURCE_COMMIT_MISMATCH" }
    $tree = (Invoke-TrustedGit @("rev-parse", "--verify", "$SourceCommit^{tree}")).stdout.Trim()
    if ($tree -notmatch '^[0-9a-f]{40}$') { throw "JOBFLOW_RUNTIME_SOURCE_TREE_INVALID" }
    $epoch = (Invoke-TrustedGit @("show", "-s", "--format=%ct", $SourceCommit)).stdout.Trim()
    if ($epoch -notmatch '^[1-9][0-9]{8,11}$') { throw "JOBFLOW_RUNTIME_SOURCE_COMMIT_TIME_INVALID" }

    $fileAliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $directoryAliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $entries = New-Object System.Collections.Generic.List[object]
    $raw = (Invoke-TrustedGit @("ls-tree", "-rz", "--full-tree", $SourceCommit)).stdout
    # Windows PowerShell 5.1 can bind the scalar-char overload ambiguously and
    # retain the terminal empty record produced by `git ls-tree -z`. Pin the
    # separator-array overload so a valid NUL-terminated tree is parsed
    # identically on every supported PowerShell runtime.
    foreach ($record in @($raw.Split([char[]]@([char]0), [StringSplitOptions]::RemoveEmptyEntries))) {
        if ($record -notmatch '^(?<mode>[0-9]{6}) (?<type>[a-z]+) (?<oid>[0-9a-f]{40})\t(?<path>.+)$') {
            throw "JOBFLOW_RUNTIME_SOURCE_TREE_UNSAFE"
        }
        $mode = [string]$Matches.mode
        $type = [string]$Matches.type
        $path = Get-NormalizedRuntimePath ([string]$Matches.path)
        if ($type -cne "blob" -or $mode -notin @("100644", "100755")) {
            throw "JOBFLOW_RUNTIME_SOURCE_TREE_UNSAFE"
        }
        $parts = $path.Split([char]'/')
        for ($index = 1; $index -lt $parts.Count; $index++) {
            $directory = [string]::Join('/', $parts[0..($index - 1)])
            if ($fileAliases.Contains($directory)) { throw "JOBFLOW_RUNTIME_SOURCE_TREE_UNSAFE" }
            [void]$directoryAliases.Add($directory)
        }
        if ($directoryAliases.Contains($path) -or -not $fileAliases.Add($path)) {
            throw "JOBFLOW_RUNTIME_SOURCE_TREE_UNSAFE"
        }
        $entries.Add([pscustomobject]@{ path=$path; mode=$mode; oid=[string]$Matches.oid })
    }
    if ($entries.Count -lt 1) { throw "JOBFLOW_RUNTIME_SOURCE_TREE_UNSAFE" }
    return [pscustomobject]@{
        commit=$head
        git_tree_oid=$tree
        source_date_epoch=$epoch
        entries=@(Get-OrdinalSortedObjects @($entries.ToArray()) "path")
    }
}

function Get-RetainedGitBlobOid([object]$RetainedInput) {
    Assert-RetainedRuntimeInputIdentity $RetainedInput -VerifyHash
    $sha1 = [Security.Cryptography.SHA1]::Create()
    $buffer = New-Object byte[] 65536
    try {
        $header = [Text.Encoding]::ASCII.GetBytes("blob $([long]$RetainedInput.size)`0")
        [void]$sha1.TransformBlock($header, 0, $header.Length, $header, 0)
        $RetainedInput.stream.Position = 0
        while (($count = $RetainedInput.stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            [void]$sha1.TransformBlock($buffer, 0, $count, $buffer, 0)
        }
        [void]$sha1.TransformFinalBlock((New-Object byte[] 0), 0, 0)
        return ConvertTo-LowerHex $sha1.Hash
    }
    finally {
        $RetainedInput.stream.Position = 0
        [Array]::Clear($buffer, 0, $buffer.Length)
        $sha1.Dispose()
    }
}

function Get-SourceSnapshotIdentity([string]$SourceRoot, [object[]]$GitEntries) {
    $root = Assert-OrdinaryInput $SourceRoot -Directory
    $actual = New-Object "System.Collections.Generic.Dictionary[string,object]" ([StringComparer]::Ordinal)
    $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($item in @(Get-OrdinalSortedObjects @(Get-ChildItem -LiteralPath $root -Force -Recurse) "FullName")) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.LinkType) {
            throw "JOBFLOW_RUNTIME_SOURCE_TREE_UNSAFE"
        }
        if ($item.PSIsContainer) { continue }
        $relative = Get-NormalizedRuntimePath $item.FullName.Substring($root.Length).TrimStart([IO.Path]::DirectorySeparatorChar).Replace('\', '/')
        if (-not $aliases.Add($relative)) { throw "JOBFLOW_RUNTIME_SOURCE_TREE_UNSAFE" }
        $retained = Enter-RetainedRuntimeInput $item.FullName
        $actual[$relative] = [pscustomobject]@{
            size=[long]$retained.size
            sha256=[string]$retained.sha256
            # `git archive` intentionally applies committed export attributes.
            # On Windows that can turn the canonical LF Git blob into a CRLF
            # archive entry.  Keep the archive-entry identity separate from
            # the canonical source blob OID instead of treating that documented
            # export conversion as source corruption.
            archive_blob_oid=Get-RetainedGitBlobOid $retained
        }
    }
    if ($actual.Count -ne $GitEntries.Count) { throw "JOBFLOW_RUNTIME_SOURCE_BUILD_TREE_MISMATCH" }
    $records = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @(Get-OrdinalSortedObjects @($GitEntries) "path")) {
        if (-not $actual.ContainsKey([string]$entry.path)) { throw "JOBFLOW_RUNTIME_SOURCE_BUILD_TREE_MISMATCH" }
        $identity = $actual[[string]$entry.path]
        $records.Add([ordered]@{
            path=[string]$entry.path
            mode=[string]$entry.mode
            git_blob_oid=[string]$entry.oid
            archive_blob_oid=[string]$identity.archive_blob_oid
            size=[long]$identity.size
            sha256=[string]$identity.sha256
        })
    }
    $material = [ordered]@{ digest_format="JOBFLOW_SOURCE_BUILD_TREE_DIGEST_V1"; entries=$records.ToArray() }
    return [pscustomobject]@{
        sha256=Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $material)))
        records=$records.ToArray()
    }
}

function New-TrustedSourceSnapshots([string]$Root, [object]$Identity) {
    $archiveA = Join-Path $Root "source-a.zip"
    $archiveB = Join-Path $Root "source-b.zip"
    [void](Invoke-TrustedGit @("archive", "--format=zip", "--output=$archiveA", $SourceCommit))
    [void](Invoke-TrustedGit @("archive", "--format=zip", "--output=$archiveB", $SourceCommit))
    $inputA = Enter-RetainedRuntimeInput $archiveA 1 536870912
    $inputB = Enter-RetainedRuntimeInput $archiveB 1 536870912
    if ([string]$inputA.sha256 -cne [string]$inputB.sha256) {
        throw "JOBFLOW_RUNTIME_SOURCE_ARCHIVE_NONDETERMINISTIC"
    }
    $snapshotA = Join-Path $Root "source-a"
    $snapshotB = Join-Path $Root "source-b"
    Expand-SafeZip $inputA $snapshotA
    Expand-SafeZip $inputB $snapshotB
    $treeA = Get-SourceSnapshotIdentity $snapshotA @($Identity.entries)
    $treeB = Get-SourceSnapshotIdentity $snapshotB @($Identity.entries)
    if ([string]$treeA.sha256 -cne [string]$treeB.sha256) {
        throw "JOBFLOW_RUNTIME_SOURCE_BUILD_TREE_MISMATCH"
    }
    # Retain every committed snapshot file with no write/delete sharing before
    # either build starts.  A post-build exact inventory check below rejects
    # added paths, while these handles prevent replacement of existing paths.
    $retainedA = Get-RetainedTreeSnapshot $snapshotA
    $retainedB = Get-RetainedTreeSnapshot $snapshotB
    return [pscustomobject]@{
        archive_sha256=[string]$inputA.sha256
        source_build_tree_sha256=[string]$treeA.sha256
        snapshot_a=$snapshotA
        snapshot_b=$snapshotB
        retained_a=$retainedA
        retained_b=$retainedB
    }
}

function Initialize-PinnedBuildTools([string]$Root, [object]$BuildLock) {
    $wheelhouse = Join-Path $Root "build-wheelhouse"
    $target = Join-Path $Root "build-tools"
    [IO.Directory]::CreateDirectory($wheelhouse) | Out-Null
    $requirements = New-Object System.Collections.Generic.List[string]
    foreach ($package in @($BuildLock.value.packages)) {
        $input = $script:WheelhouseInputs[[string]$package.filename]
        if ($null -eq $input) { throw "JOBFLOW_RUNTIME_WHEELHOUSE_INVENTORY_MISMATCH" }
        Copy-RetainedRuntimeInput $input (Join-Path $wheelhouse ([string]$package.filename))
        $requirements.Add("$([string]$package.name)==$([string]$package.version) --hash=$([string]$package.sha256)")
    }
    $requirementsPath = Join-Path $Root "build-requirements.lock"
    Write-Utf8NoBom $requirementsPath ([string]::Join("`n", $requirements) + "`n")
    $result = Invoke-BuilderPython @(
        "-I", "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "--no-index", "--no-deps",
        "--no-compile", "--disable-pip-version-check", "--find-links", $wheelhouse, "--target", $target,
        "--requirement", $requirementsPath
    ) $Root $null (Join-Path $Root "tmp")
    if (-not [string]::IsNullOrWhiteSpace($result.stderr)) { throw "JOBFLOW_RUNTIME_BUILD_TOOL_INSTALL_FAILED" }
    $snapshot = Get-RetainedTreeSnapshot $target
    $records = @(Get-OrdinalSortedObjects @($snapshot.records) "relative" | ForEach-Object {
        if ([string]$_.kind -ceq "directory") {
            [ordered]@{ kind="directory"; path=[string]$_.relative }
        }
        else {
            [ordered]@{
                kind="file"; path=[string]$_.relative; size=[long]$_.input.size; sha256=[string]$_.input.sha256
            }
        }
    })
    $material = [ordered]@{ digest_format="JOBFLOW_BUILD_TOOLS_TREE_DIGEST_V1"; entries=$records }
    return [pscustomobject]@{
        root=$target
        snapshot=$snapshot
        tree_sha256=Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $material)))
    }
}

function Get-SourceApplicationVersion([string]$SourceRoot) {
    $program = "import pathlib,sys,tomllib;print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))['project']['version'])"
    $result = Invoke-BuilderPython @("-I", "-c", $program, (Join-Path $SourceRoot "pyproject.toml")) $SourceRoot
    if ($result.stdout -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or -not [string]::IsNullOrWhiteSpace($result.stderr)) {
        throw "JOBFLOW_RUNTIME_APPLICATION_VERSION_MISMATCH"
    }
    return [string]$result.stdout
}

function Build-ApplicationWheel([string]$SourceRoot, [string]$OutputRoot, [string]$BuildToolsRoot, [string]$TemporaryRoot) {
    [IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
    $program = @'
import pathlib,site,sys
tools,out=sys.argv[1:3]
site.addsitedir(tools)
import setuptools.build_meta as backend
name=backend.build_wheel(out,config_settings={})
if pathlib.Path(name).name != name:
    raise RuntimeError("unsafe wheel name")
print(name)
'@
    try { [void](Invoke-BuilderPython @("-I", "-c", $program, $BuildToolsRoot, $OutputRoot) $SourceRoot $null $TemporaryRoot) }
    catch { throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_BUILD_FAILED" }
    $wheels = @(Get-ChildItem -LiteralPath $OutputRoot -File -Force)
    if ($wheels.Count -ne 1 -or $wheels[0].Extension -cne ".whl") {
        throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_BUILD_FAILED"
    }
    return Enter-RetainedRuntimeInput $wheels[0].FullName 1 536870912
}

function Get-ApplicationWheelIdentity([object]$RetainedInput) {
    Assert-RetainedRuntimeInputIdentity $RetainedInput -VerifyHash
    $name = [IO.Path]::GetFileName([string]$RetainedInput.path)
    if ($name -notmatch '^jobflow_local-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl$') {
        throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_INVALID"
    }
    $version = $Matches[1]
    $program = @'
import base64,csv,email.parser,hashlib,io,json,pathlib,re,sys,zipfile
p=pathlib.Path(sys.argv[1]); expected_version=sys.argv[2]
dist=f"jobflow_local-{expected_version}.dist-info"
reserved={"CON","PRN","AUX","NUL","CONIN$","CONOUT$","CLOCK$",*[f"COM{i}" for i in range(1,10)],*[f"LPT{i}" for i in range(1,10)]}
def fail(): raise RuntimeError("invalid wheel")
def safe(name):
    if not name or len(name)>768 or "\\" in name or ":" in name or name.startswith("/") or name.endswith("/"): fail()
    parts=name.split("/")
    for part in parts:
        if not part or len(part)>255 or part in {".",".."} or part.endswith((" ",".")) or part.split(".",1)[0].upper() in reserved: fail()
        if any(ord(c)<32 or ord(c)>126 or c in '\"<>|?*' for c in part): fail()
    return name
with zipfile.ZipFile(p,"r") as z:
    infos=z.infolist()
    if not 4<=len(infos)<=4096: fail()
    names={}; files=set(); directories=set(); total=0
    for info in infos:
        name=safe(info.filename)
        key=name.casefold()
        parts=name.split("/")
        prefixes={"/".join(parts[:i]).casefold() for i in range(1,len(parts))}
        # Windows resolves paths case-insensitively.  Reject both duplicate
        # names and file/directory aliasing (for example, ``a`` plus
        # ``a/b.py``) before extraction can reinterpret the archive.
        if key in names or key in directories or any(prefix in files for prefix in prefixes): fail()
        names[key]=name
        files.add(key)
        directories.update(prefixes)
        mode=(info.external_attr>>16)&0xF000
        if mode not in (0,0x8000): fail()
        if info.file_size<0 or info.file_size>64*1024*1024: fail()
        total+=info.file_size
        if total>256*1024*1024: fail()
        if info.file_size>=1024*1024 and info.file_size/max(1,info.compress_size)>200: fail()
        low=name.casefold()
        if not (low.startswith("jobops/") or low.startswith(dist.casefold()+"/")): fail()
        if low.endswith(".pth") or low.endswith("/direct_url.json") or "/scripts/" in low or "/bin/" in low: fail()
    meta=f"{dist}/METADATA"; wheel=f"{dist}/WHEEL"; record=f"{dist}/RECORD"
    dist_infos={name.split("/",1)[0].casefold() for name in names.values() if name.split("/",1)[0].casefold().endswith(".dist-info")}
    if dist_infos != {dist.casefold()}: fail()
    for required in (meta,wheel,record):
        if required.casefold() not in names or names[required.casefold()]!=required: fail()
    for required in ("jobops/__init__.py","jobops/cli.py","jobops/runtime_health.py"):
        if required.casefold() not in names or names[required.casefold()]!=required: fail()
    metadata=email.parser.Parser().parsestr(z.read(meta).decode("utf-8","strict"),headersonly=True)
    def singleton(message,key):
        values=message.get_all(key,[])
        if len(values)!=1: fail()
        return values[0].strip()
    if singleton(metadata,"Name")!="jobflow-local" or singleton(metadata,"Version")!=expected_version: fail()
    requires_python={part.strip() for part in singleton(metadata,"Requires-Python").split(",")}
    if requires_python != {">=3.11","<3.14"}: fail()
    wheel_headers=email.parser.Parser().parsestr(z.read(wheel).decode("utf-8","strict"),headersonly=True)
    if singleton(wheel_headers,"Wheel-Version")!="1.0" or singleton(wheel_headers,"Root-Is-Purelib").lower()!="true": fail()
    if wheel_headers.get_all("Tag",[]) != ["py3-none-any"]: fail()
    rows=list(csv.reader(io.StringIO(z.read(record).decode("utf-8","strict"),newline="")))
    if len(rows)!=len(infos): fail()
    seen=set()
    for row in rows:
        if len(row)!=3: fail()
        name=safe(row[0])
        if name in seen or name.casefold() not in names or names[name.casefold()]!=name: fail()
        seen.add(name)
        if name==record:
            if row[1] or row[2]: fail()
            continue
        data=z.read(name)
        digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        if row[1] != "sha256="+digest or row[2] != str(len(data)): fail()
    if seen != set(names.values()): fail()
print(json.dumps({"version":expected_version,"tag":"py3-none-any"},sort_keys=True,separators=(",",":")))
'@
    try {
        $result = Invoke-BuilderPython @("-I", "-c", $program, [string]$RetainedInput.path, $version) $script:Project
        $validated = $result.stdout | ConvertFrom-Json
        if ([string]$validated.version -cne $version -or [string]$validated.tag -cne "py3-none-any" -or -not [string]::IsNullOrWhiteSpace($result.stderr)) {
            throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_RECORD_INVALID"
        }
    }
    catch { throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_RECORD_INVALID" }
    Assert-RetainedRuntimeInputIdentity $RetainedInput -VerifyHash
    return [pscustomobject]@{
        path = [string]$RetainedInput.path
        input = $RetainedInput
        filename = $name
        version = $version
        size = [long]$RetainedInput.size
        sha256 = [string]$RetainedInput.sha256
    }
}

function Get-RetainedTreeSnapshot([string]$Source) {
    $sourceRoot = Assert-OrdinaryInput $Source -Directory
    $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $records = New-Object System.Collections.Generic.List[object]
    foreach ($item in @(Get-OrdinalSortedObjects @(Get-ChildItem -LiteralPath $sourceRoot -Force -Recurse) "FullName")) {
        $relative = $item.FullName.Substring($sourceRoot.Length).TrimStart([IO.Path]::DirectorySeparatorChar).Replace('\', '/')
        $relative = Get-NormalizedRuntimePath $relative
        if (-not $aliases.Add($relative)) { throw "JOBFLOW_RUNTIME_PAYLOAD_PATH_INVALID" }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_RUNTIME_BUILD_REPARSE_REJECTED"
        }
        if ($item.PSIsContainer) {
            $records.Add([pscustomobject]@{ kind = "directory"; relative = $relative; input = $null })
            continue
        }
        # Empty package marker files (for example `py.typed`) are valid tree
        # members.  Artifact entry points remain non-empty by default, while a
        # recursively retained tree explicitly allows zero-byte ordinary files.
        $retainedInput = Enter-RetainedRuntimeInput $item.FullName 0
        $records.Add([pscustomobject]@{ kind = "file"; relative = $relative; input = $retainedInput })
    }
    return [pscustomobject]@{ root = $sourceRoot; records = $records.ToArray() }
}

function Copy-SafeTree([object]$Snapshot, [string]$Destination) {
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($record in @($Snapshot.records)) {
        $relativePath = ([string]$record.relative).Replace('/', [IO.Path]::DirectorySeparatorChar)
        $target = Join-Path $Destination $relativePath
        if ([string]$record.kind -ceq "directory") {
            [IO.Directory]::CreateDirectory($target) | Out-Null
            continue
        }
        Copy-RetainedRuntimeInput $record.input $target
    }
}

function Expand-SafeZip([object]$ArchiveInput, [string]$Destination) {
    Assert-RetainedRuntimeInputIdentity $ArchiveInput -VerifyHash
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $stream = $ArchiveInput.stream
    $stream.Position = 0
    try {
        $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try {
            foreach ($entry in $zip.Entries) {
                if ($entry.FullName.EndsWith("/", [StringComparison]::Ordinal)) { continue }
                $relative = Get-NormalizedRuntimePath $entry.FullName
                if (
                    -not $aliases.Add($relative)
                ) { throw "JOBFLOW_RUNTIME_SOURCE_ARCHIVE_INVALID" }
                $unixMode = ((ConvertTo-UInt32Bits ([int]$entry.ExternalAttributes)) -shr 16) -band 0xF000
                if ($unixMode -ne 0 -and $unixMode -ne 0x8000) { throw "JOBFLOW_RUNTIME_SOURCE_ARCHIVE_INVALID" }
                $target = Join-Path $Destination ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
                $parent = [IO.Path]::GetDirectoryName($target)
                [IO.Directory]::CreateDirectory($parent) | Out-Null
                $source = $entry.Open()
                $output = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
                try { $source.CopyTo($output); $output.Flush($true) }
                finally { $output.Dispose(); $source.Dispose() }
            }
        }
        finally { $zip.Dispose() }
    }
    finally { $stream.Position = 0 }
    Assert-RetainedRuntimeInputIdentity $ArchiveInput -VerifyHash
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    $parent = [IO.Path]::GetDirectoryName($Path)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function Install-OfflineApplication([string]$BuildRoot, [string]$AppRoot, [object[]]$RuntimePackages, [object]$Application) {
    $localWheelhouse = Join-Path $BuildRoot "wheelhouse"
    [IO.Directory]::CreateDirectory($localWheelhouse) | Out-Null
    foreach ($package in $RuntimePackages) {
        $retainedInput = $script:WheelhouseInputs[[string]$package.filename]
        if ($null -eq $retainedInput) { throw "JOBFLOW_RUNTIME_WHEELHOUSE_INVENTORY_MISMATCH" }
        Copy-RetainedRuntimeInput $retainedInput (Join-Path $localWheelhouse ([string]$package.filename))
    }
    Copy-RetainedRuntimeInput $Application.input (Join-Path $localWheelhouse $Application.filename)
    $requirements = New-Object System.Collections.Generic.List[string]
    foreach ($package in $RuntimePackages) {
        $requirements.Add("$([string]$package.name)==$([string]$package.version) --hash=$([string]$package.sha256)")
    }
    $requirements.Add("jobflow-local==$($Application.version) --hash=$($Application.sha256)")
    Write-Utf8NoBom (Join-Path $BuildRoot "requirements.lock") ([string]::Join("`n", $requirements) + "`n")
    [IO.Directory]::CreateDirectory($AppRoot) | Out-Null
    $arguments = @(
        "-I", "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "--no-index", "--no-deps",
        "--no-compile", "--disable-pip-version-check", "--no-warn-script-location", "--find-links", ".\wheelhouse",
        "--target", ".\closure\app", "--requirement", ".\requirements.lock"
    )
    $result = Invoke-BuilderPython $arguments $BuildRoot
    if (-not [string]::IsNullOrWhiteSpace($result.stderr) -and $result.stderr -notmatch '^WARNING: Target directory') {
        throw "JOBFLOW_RUNTIME_OFFLINE_INSTALL_FAILED"
    }
    foreach ($path in @(Get-OrdinalSortedObjects @(Get-ChildItem -LiteralPath $AppRoot -Recurse -Force | Where-Object {
        $_.Name -in @("direct_url.json", "REQUESTED") -or $_.FullName -match '[\\/](Scripts|bin)([\\/]|$)'
    }) "FullName" -Descending)) {
        if ($path.PSIsContainer) { Remove-Item -LiteralPath $path.FullName -Recurse -Force }
        elseif (Test-Path -LiteralPath $path.FullName) { [IO.File]::Delete($path.FullName) }
    }
}

function Assert-NoPathLeakage([string]$Root, [string[]]$Forbidden) {
    $needles = New-Object System.Collections.Generic.List[byte[]]
    foreach ($value in $Forbidden) {
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        $absolute = [IO.Path]::GetFullPath($value)
        $needles.Add([Text.Encoding]::UTF8.GetBytes($absolute.ToLowerInvariant()))
        $needles.Add([Text.Encoding]::Unicode.GetBytes($absolute.ToLowerInvariant()))
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -File -Force -Recurse)) {
        if ($file.Length -gt 67108864) { continue }
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        $utf8Text = [Text.Encoding]::UTF8.GetString($bytes).ToLowerInvariant()
        $utf16Text = [Text.Encoding]::Unicode.GetString($bytes).ToLowerInvariant()
        foreach ($value in $Forbidden) {
            if ([string]::IsNullOrWhiteSpace($value)) { continue }
            $needle = [IO.Path]::GetFullPath($value).ToLowerInvariant()
            if ($utf8Text.Contains($needle) -or $utf16Text.Contains($needle)) {
                throw "JOBFLOW_RUNTIME_ABSOLUTE_PATH_LEAK"
            }
        }
    }
}

function Get-Inventory([string]$Root) {
    $records = New-Object System.Collections.Generic.List[object]
    function Visit([string]$Directory, [string]$Prefix) {
        foreach ($item in @(Get-OrdinalSortedObjects @(Get-ChildItem -LiteralPath $Directory -Force) "Name" -IgnoreCase)) {
            $relative = Get-NormalizedRuntimePath $(if ([string]::IsNullOrEmpty($Prefix)) { $item.Name } else { "$Prefix/$($item.Name)" })
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_RUNTIME_INVENTORY_UNSAFE"
            }
            if ($item.PSIsContainer) { Visit $item.FullName $relative; continue }
            if ($relative -ceq "runtime-closure.json") { continue }
            $records.Add([pscustomobject][ordered]@{ path=$relative; size=[long]$item.Length; sha256=Get-Sha256 $item.FullName })
        }
    }
    Visit $Root ""
    return $records.ToArray()
}

function Get-TreeHash([object[]]$Records) {
    $jsonRecords = foreach ($record in $Records) {
        $pathJson = ConvertTo-Json ([string]$record.path) -Compress
        $shaJson = ConvertTo-Json ([string]$record.sha256) -Compress
        "{`"path`":$pathJson,`"sha256`":$shaJson,`"size`":$([long]$record.size)}"
    }
    return Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes("[" + [string]::Join(",", $jsonRecords) + "]"))
}

function Write-ClosureManifest(
    [string]$ClosureRoot,
    [string]$Version,
    [bool]$SmokePassed,
    [string]$EvidenceSha,
    [object]$RuntimeLock,
    [object]$Application,
    [string]$WheelhouseTreeSha,
    [string]$BuilderToolchainSha
) {
    $records = @(Get-Inventory $ClosureRoot)
    $wheelRecords = @($RuntimeLock.value.packages | ForEach-Object {
        if ([string]$_.filename -notmatch '-([^-]+-[^-]+-[^-]+)\.whl$') { throw "JOBFLOW_RUNTIME_LOCK_INVALID" }
        [ordered]@{ name=[string]$_.name; version=[string]$_.version; tag=$Matches[1]; size=[long]$_.size; sha256=[string]$_.sha256 }
    })
    $manifest = [ordered]@{
        schema_version = 1
        status = "BUILT_UNATTESTED"
        artifact_type = "complete-runtime"
        platform = "windows-x64"
        application_version = $Version
        source_commit = $SourceCommit
        python = [ordered]@{
            version = "3.13.15"
            artifact_name = "python-3.13.15-embed-amd64.zip"
            artifact_sha256 = "sha256:d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
            sigstore_identity = "thomas@python.org"
            sigstore_verified = $false
        }
        build_inputs = [ordered]@{
            wheel_lock_sha256 = $RuntimeLock.sha256
            wheelhouse_tree_sha256 = $WheelhouseTreeSha
            application_wheel_sha256 = $Application.sha256
            application_wheel_provenance = $script:ApplicationWheelProvenance
            builder_toolchain_sha256 = $BuilderToolchainSha
            wheels = $wheelRecords
        }
        layout = [ordered]@{
            python = "runtime/python.exe"
            python_pth = "runtime/python313._pth"
            application_root = "app"
            module = "jobops.cli"
        }
        file_count = $records.Count
        total_bytes = [long](($records | Measure-Object -Property size -Sum).Sum)
        tree_sha256 = Get-TreeHash $records
        files = $records
        offline_smoke_tests = [ordered]@{ import_passed=$SmokePassed; schema_passed=$SmokePassed; external_actions=0 }
        protected_builder = [ordered]@{
            evidence_sha256 = $EvidenceSha
            deterministic_rebuild_match = $true
            outer_signature_ready = $false
        }
    }
    $path = Join-Path $ClosureRoot "runtime-closure.json"
    if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) }
    Write-Utf8NoBom $path (ConvertTo-CanonicalJson $manifest)
    return $manifest
}

function Write-SafeIndependentVerifierFailure([string]$Prefix, [string]$Text) {
    $match = [Text.RegularExpressions.Regex]::Match(
        [string]$Text,
        'JOBFLOW_RUNTIME_[A-Z0-9_]+'
    )
    $code = if ($match.Success) { [string]$match.Value } else { "UNKNOWN" }
    [Console]::Error.WriteLine($Prefix + "=" + $code)
}

function Invoke-IndependentVerifier(
    [string]$ClosureRoot,
    [bool]$Attested,
    [bool]$PendingSmoke
) {
    Assert-RetainedRuntimeInputIdentity $script:ClosureVerifierInput -VerifyHash
    $verifier = [string]$script:ClosureVerifierInput.path
    $arguments = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $verifier, "-RuntimeRoot", $ClosureRoot)
    if (-not $Attested) { $arguments += "-AllowUnattested" }
    if ($PendingSmoke) { $arguments += "-AllowPendingSmoke" }
    $expectedStatus = if ($PendingSmoke) {
        "RUNTIME_CLOSURE_STRUCTURE_VERIFIED"
    }
    else { "RUNTIME_CLOSURE_VERIFIED" }
    $failurePrefix = if ($PendingSmoke) {
        "JOBFLOW_RUNTIME_STRUCTURAL_PRE_SMOKE_VERIFY_DETAIL"
    }
    else {
        "JOBFLOW_RUNTIME_STRUCTURAL_FINAL_VERIFY_DETAIL"
    }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = (Get-Process -Id $PID).Path
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.Arguments = Join-NativeArguments $arguments
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_RUNTIME_STRUCTURAL_VERIFY_FAILED" }
        $output = $process.StandardOutput.ReadToEnd()
        $errorText = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0 -or $output -notmatch $expectedStatus) {
            Write-SafeIndependentVerifierFailure $failurePrefix $errorText
            throw "JOBFLOW_RUNTIME_STRUCTURAL_VERIFY_FAILED"
        }
    }
    finally {
        $process.Dispose()
        Assert-RetainedRuntimeInputIdentity $script:ClosureVerifierInput -VerifyHash
    }
}

function Invoke-IndependentArchiveVerifier([object]$ArchiveInput, [bool]$Attested) {
    Assert-RetainedRuntimeInputIdentity $ArchiveInput -VerifyHash
    Assert-RetainedRuntimeInputIdentity $script:ClosureVerifierInput -VerifyHash
    $arguments = @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", [string]$script:ClosureVerifierInput.path,
        "-ArchivePath", [string]$ArchiveInput.path
    )
    if (-not $Attested) { $arguments += "-AllowUnattested" }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = (Get-Process -Id $PID).Path
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.Arguments = Join-NativeArguments $arguments
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_RUNTIME_ARCHIVE_VERIFY_FAILED" }
        $output = $process.StandardOutput.ReadToEnd()
        $errorText = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if (
            $process.ExitCode -ne 0 -or
            $output -notmatch 'RUNTIME_CLOSURE_VERIFIED' -or
            -not [string]::IsNullOrWhiteSpace($errorText)
        ) {
            Write-SafeIndependentVerifierFailure "JOBFLOW_RUNTIME_ARCHIVE_VERIFY_DETAIL" $errorText
            throw "JOBFLOW_RUNTIME_ARCHIVE_VERIFY_FAILED"
        }
    }
    finally {
        $process.Dispose()
        Assert-RetainedRuntimeInputIdentity $ArchiveInput -VerifyHash
        Assert-RetainedRuntimeInputIdentity $script:ClosureVerifierInput -VerifyHash
    }
}

function Invoke-RuntimeBuildEvidenceVerifier(
    [string]$ClosureRoot,
    [object]$EvidenceInput
) {
    Assert-RetainedRuntimeInputIdentity $EvidenceInput -VerifyHash
    $runtimePython = Join-Path $ClosureRoot "runtime\python.exe"
    if (-not [IO.File]::Exists($runtimePython)) {
        throw "JOBFLOW_RUNTIME_BUILD_EVIDENCE_VERIFY_FAILED"
    }
    $scriptPath = Join-Path $script:RuntimeBuildRoot (
        ".runtime-build-evidence-verify-" + [Guid]::NewGuid().ToString("N") + ".py"
    )
    $program = @'
from pathlib import Path
import re
import sys

from jobops.publisher_attestation import validate_runtime_build_evidence

try:
    document = validate_runtime_build_evidence(Path(sys.argv[1]).read_bytes())
    if not document.sha256.startswith("sha256:"):
        raise RuntimeError("RUNTIME_BUILD_EVIDENCE_DIGEST_INVALID")
except Exception as error:
    code = getattr(error, "code", "RUNTIME_BUILD_EVIDENCE_UNKNOWN")
    if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) is None:
        code = "RUNTIME_BUILD_EVIDENCE_UNKNOWN"
    sys.stderr.write(code)
    raise SystemExit(2)
else:
    sys.stdout.write("JOBFLOW_RUNTIME_BUILD_EVIDENCE_OK")
'@
    Write-Utf8NoBom $scriptPath $program
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $runtimePython
    $start.WorkingDirectory = $ClosureRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.Arguments = Join-NativeArguments @(
        "-I", "-B", $scriptPath, [string]$EvidenceInput.path
    )
    $start.EnvironmentVariables.Clear()
    foreach ($name in @("SystemRoot", "WINDIR", "COMSPEC")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $start.EnvironmentVariables[$name] = $value
        }
    }
    $temporary = Join-Path $script:RuntimeBuildRoot "runtime-evidence-verify-tmp"
    [IO.Directory]::CreateDirectory($temporary) | Out-Null
    $start.EnvironmentVariables["TEMP"] = $temporary
    $start.EnvironmentVariables["TMP"] = $temporary
    $start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1"
    $start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
    $start.EnvironmentVariables["NO_PROXY"] = "*"
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) {
            throw "JOBFLOW_RUNTIME_BUILD_EVIDENCE_VERIFY_FAILED"
        }
        $output = $process.StandardOutput.ReadToEnd()
        $errorText = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if (
            $process.ExitCode -ne 0 -or
            $output -cne "JOBFLOW_RUNTIME_BUILD_EVIDENCE_OK" -or
            -not [string]::IsNullOrWhiteSpace($errorText)
        ) {
            $failure = [Text.RegularExpressions.Regex]::Match(
                [string]$errorText,
                '^[A-Z][A-Z0-9_]{0,95}$'
            )
            $failureCode = if ($failure.Success) {
                [string]$failure.Value
            }
            else { "RUNTIME_BUILD_EVIDENCE_UNKNOWN" }
            [Console]::Error.WriteLine(
                "JOBFLOW_RUNTIME_BUILD_EVIDENCE_VERIFY_DETAIL=" + $failureCode
            )
            throw "JOBFLOW_RUNTIME_BUILD_EVIDENCE_VERIFY_FAILED"
        }
    }
    finally {
        $process.Dispose()
        if ([IO.File]::Exists($scriptPath)) { [IO.File]::Delete($scriptPath) }
        Assert-RetainedRuntimeInputIdentity $EvidenceInput -VerifyHash
    }
}

function Invoke-OfflineSmoke([string]$ClosureRoot) {
    $scriptPath = Join-Path $ClosureRoot ".offline-smoke.py"
    $code = @'
import json
import os
import sys

def audit(event, args):
    if event.startswith(("socket.", "subprocess.", "os.system", "winreg.")):
        raise RuntimeError("external action blocked")

sys.addaudithook(audit)
import jobops
from jobops.runtime_closure import normalize_runtime_path
assert normalize_runtime_path("runtime/python.exe") == "runtime/python.exe"
with open("schemas/runtime-closure.schema.json", "r", encoding="utf-8") as handle:
    schema = json.load(handle)
assert schema["title"] == "JobFlow complete Windows runtime closure"
print("JOBFLOW_OFFLINE_SMOKE_OK|external_actions=0")
'@
    Write-Utf8NoBom $scriptPath $code
    try {
        $python = Join-Path $ClosureRoot "runtime\python.exe"
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $python
        $start.WorkingDirectory = $ClosureRoot
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        # The smoke test must never leave path-bearing .pyc files in the
        # closure.  Both the interpreter flag and environment variable are
        # deliberate defense in depth for every supported Python build.
        $start.Arguments = Join-NativeArguments @("-I", "-B", ".offline-smoke.py")
        $start.EnvironmentVariables.Clear()
        foreach ($name in @("SystemRoot", "WINDIR", "TEMP", "TMP")) {
            $value = [Environment]::GetEnvironmentVariable($name)
            if (-not [string]::IsNullOrWhiteSpace($value)) { $start.EnvironmentVariables[$name] = $value }
        }
        $start.EnvironmentVariables["JOBFLOW_OFFLINE_SMOKE"] = "1"
        $start.EnvironmentVariables["NO_PROXY"] = "*"
        $start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $start
        try {
            if (-not $process.Start()) { throw "JOBFLOW_RUNTIME_OFFLINE_SMOKE_FAILED" }
            $output = $process.StandardOutput.ReadToEnd().Trim()
            $errorText = $process.StandardError.ReadToEnd().Trim()
            $process.WaitForExit()
            if ($process.ExitCode -ne 0 -or $output -cne "JOBFLOW_OFFLINE_SMOKE_OK|external_actions=0" -or -not [string]::IsNullOrWhiteSpace($errorText)) {
                throw "JOBFLOW_RUNTIME_OFFLINE_SMOKE_FAILED"
            }
        }
        finally { $process.Dispose() }
    }
    finally { if ([IO.File]::Exists($scriptPath)) { [IO.File]::Delete($scriptPath) } }
}

function Assert-NoGeneratedBytecodeArtifacts([string]$ClosureRoot) {
    foreach ($item in @(Get-ChildItem -LiteralPath $ClosureRoot -Force -Recurse)) {
        if (
            ($item.PSIsContainer -and $item.Name -ieq "__pycache__") -or
            (-not $item.PSIsContainer -and $item.Extension -in @(".pyc", ".pyo"))
        ) { throw "JOBFLOW_RUNTIME_GENERATED_BYTECODE_REJECTED" }
    }
}

function Assert-CompleteRuntimeArchiveBounds([IO.Stream]$Stream, [string]$Prefix) {
    if ($null -eq $Stream -or -not $Stream.CanRead -or -not $Stream.CanSeek) {
        throw "JOBFLOW_RUNTIME_ARCHIVE_INVALID"
    }
    $Stream.Position = 0
    $zip = [IO.Compression.ZipArchive]::new($Stream, [IO.Compression.ZipArchiveMode]::Read, $true)
    try {
        $entryCount = [long]$zip.Entries.Count
        if ($entryCount -lt 1 -or $entryCount -gt $script:RuntimeArchiveMaximumEntries) {
            throw "JOBFLOW_RUNTIME_ARCHIVE_ENTRY_COUNT_INVALID"
        }
        $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
        $files = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
        $directories = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
        [long]$total = 0
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName.EndsWith("/", [StringComparison]::Ordinal)) {
                throw "JOBFLOW_RUNTIME_ARCHIVE_DIRECTORY_ENTRY_REJECTED"
            }
            if (-not $entry.FullName.StartsWith($Prefix, [StringComparison]::Ordinal)) {
                throw "JOBFLOW_RUNTIME_ARCHIVE_PREFIX_INVALID"
            }
            if ($entry.FullName.Length -gt 1024) { throw "JOBFLOW_RUNTIME_PAYLOAD_PATH_INVALID" }
            $relative = Get-NormalizedRuntimePath $entry.FullName.Substring($Prefix.Length)
            if (-not $aliases.Add($relative)) { throw "JOBFLOW_RUNTIME_PATH_COLLISION" }
            $parts = $relative.Split([char]'/', [StringSplitOptions]::None)
            $parent = ""
            for ($index = 0; $index -lt ($parts.Count - 1); $index++) {
                $parent = if ([string]::IsNullOrEmpty($parent)) { $parts[$index] } else { "$parent/$($parts[$index])" }
                if ($files.Contains($parent)) { throw "JOBFLOW_RUNTIME_PATH_COLLISION" }
                [void]$directories.Add($parent)
            }
            if ($directories.Contains($relative) -or -not $files.Add($relative)) {
                throw "JOBFLOW_RUNTIME_PATH_COLLISION"
            }
            [long]$length = $entry.Length
            [long]$compressed = $entry.CompressedLength
            if (
                $length -lt 0 -or $length -gt $script:RuntimeArchiveMaximumEntryBytes -or
                ($length -gt 0 -and $compressed -le 0)
            ) { throw "JOBFLOW_RUNTIME_ARCHIVE_SIZE_INVALID" }
            if ($length -gt ($script:RuntimeArchiveMaximumUncompressedBytes - $total)) {
                throw "JOBFLOW_RUNTIME_ARCHIVE_SIZE_INVALID"
            }
            $total += $length
            if (
                $length -gt $script:RuntimeArchiveCompressionRatioMinimumBytes -and
                ([double]$length / [double]$compressed) -gt $script:RuntimeArchiveMaximumCompressionRatio
            ) { throw "JOBFLOW_RUNTIME_ARCHIVE_COMPRESSION_RATIO_INVALID" }
        }
    }
    finally {
        $zip.Dispose()
        $Stream.Position = $Stream.Length
    }
}

function New-DeterministicZip([object]$ClosureSnapshot, [string]$ZipPath, [string]$Prefix) {
    $files = @(Get-OrdinalSortedObjects @($ClosureSnapshot.records | Where-Object { [string]$_.kind -ceq "file" }) "relative")
    if ($files.Count -lt 1 -or $files.Count -gt $script:RuntimeArchiveMaximumEntries) {
        throw "JOBFLOW_RUNTIME_ARCHIVE_ENTRY_COUNT_INVALID"
    }
    $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $sourceFiles = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $sourceDirectories = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    [long]$total = 0
    foreach ($file in $files) {
        $relative = Get-NormalizedRuntimePath ([string]$file.relative)
        if (-not $aliases.Add($relative)) { throw "JOBFLOW_RUNTIME_PATH_COLLISION" }
        $parts = $relative.Split([char]'/', [StringSplitOptions]::None)
        $parent = ""
        for ($index = 0; $index -lt ($parts.Count - 1); $index++) {
            $parent = if ([string]::IsNullOrEmpty($parent)) { $parts[$index] } else { "$parent/$($parts[$index])" }
            if ($sourceFiles.Contains($parent)) { throw "JOBFLOW_RUNTIME_PATH_COLLISION" }
            [void]$sourceDirectories.Add($parent)
        }
        if ($sourceDirectories.Contains($relative) -or -not $sourceFiles.Add($relative)) {
            throw "JOBFLOW_RUNTIME_PATH_COLLISION"
        }
        [long]$length = [long]$file.input.size
        if ($length -lt 0 -or $length -gt $script:RuntimeArchiveMaximumEntryBytes) {
            throw "JOBFLOW_RUNTIME_ARCHIVE_SIZE_INVALID"
        }
        if ($length -gt ($script:RuntimeArchiveMaximumUncompressedBytes - $total)) {
            throw "JOBFLOW_RUNTIME_ARCHIVE_SIZE_INVALID"
        }
        $total += $length
    }
    $stream = [IO.File]::Open($ZipPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
    try {
        $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            foreach ($file in $files) {
                $relative = Get-NormalizedRuntimePath ([string]$file.relative)
                $entry = $zip.CreateEntry($Prefix + $relative, [IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = [DateTimeOffset]::new(1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
                $target = $entry.Open()
                try {
                    Assert-RetainedRuntimeInputIdentity $file.input -VerifyHash
                    $file.input.stream.Position = 0
                    $file.input.stream.CopyTo($target)
                }
                finally {
                    $target.Dispose()
                    $file.input.stream.Position = 0
                }
            }
        }
        finally { $zip.Dispose() }
        $stream.Flush($true)
        Assert-CompleteRuntimeArchiveBounds $stream $Prefix
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
}

function New-OneBuild([string]$PassRoot, [string]$EvidenceSha, [string]$ToolchainSha) {
    $closure = Join-Path $PassRoot "closure"
    [IO.Directory]::CreateDirectory($closure) | Out-Null
    $runtime = Join-Path $closure "runtime"
    Expand-SafeZip $script:PythonArtifactInput $runtime
    $pth = Join-Path $runtime "python313._pth"
    if ([IO.File]::Exists($pth)) { [IO.File]::Delete($pth) }
    Write-Utf8NoBom $pth "python313.zip`n.`n../app`n"

    Install-OfflineApplication $PassRoot (Join-Path $closure "app") @($script:RuntimeLock.value.packages) $script:Application
    foreach ($directoryName in @("schemas", "config", "browser-companion", "scripts")) {
        Copy-SafeTree $script:ProjectTreeSnapshots[$directoryName] (Join-Path $closure $directoryName)
    }
    foreach ($fileName in @(".jobops-root", "LICENSE", "PRIVACY.md", "SECURITY.md")) {
        Copy-RetainedRuntimeInput $script:ProjectRootInputs[$fileName] (Join-Path $closure $fileName)
    }
    Write-Utf8NoBom (Join-Path $closure "Start JobFlow.ps1") @'
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $root "runtime\python.exe") -I -m jobops.cli onboarding-center
exit $LASTEXITCODE
'@
    Write-Utf8NoBom (Join-Path $closure "Start JobFlow.cmd") @'
@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start JobFlow.ps1"
exit /b %errorlevel%
'@
    Copy-RetainedRuntimeInput $script:ClosureVerifierInput (Join-Path $closure "Verify JobFlow Runtime.ps1")
    Assert-NoPathLeakage $closure @(
        $PassRoot, $script:Project, $script:Wheelhouse, $script:Application.path, $script:BuilderPython,
        $script:SourceBuildRoot, $script:SourceSnapshots.snapshot_a, $script:SourceSnapshots.snapshot_b
    )

    Write-ClosureManifest $closure $script:Application.version $false $EvidenceSha `
        $script:RuntimeLock $script:Application $script:WheelhouseTreeSha $ToolchainSha | Out-Null
    Assert-NoGeneratedBytecodeArtifacts $closure
    Invoke-IndependentVerifier $closure $false $true
    Invoke-OfflineSmoke $closure
    Assert-NoGeneratedBytecodeArtifacts $closure
    $finalManifest = Write-ClosureManifest $closure $script:Application.version $true $EvidenceSha `
        $script:RuntimeLock $script:Application $script:WheelhouseTreeSha $ToolchainSha
    # Hold every final closure file with no write/delete sharing before the
    # independent verifier runs.  The ZIP writer consumes these same streams,
    # so no post-verification path re-enumeration can change the payload.
    $closureSnapshot = Get-RetainedTreeSnapshot $closure
    Invoke-IndependentVerifier $closure $false $false
    $zipPath = Join-Path $PassRoot "complete.zip"
    New-DeterministicZip $closureSnapshot $zipPath "JobFlow-v$($script:Application.version)-windows-x64/"
    $manifestRecords = @($closureSnapshot.records | Where-Object {
        [string]$_.relative -ceq "runtime-closure.json"
    })
    if ($manifestRecords.Count -ne 1) {
        throw "JOBFLOW_RUNTIME_BUILD_MANIFEST_IDENTITY_INVALID"
    }
    return [pscustomobject]@{
        closure = $closure
        zip = $zipPath
        sha256 = Get-Sha256 $zipPath
        manifest_sha256 = [string]$manifestRecords[0].input.sha256
        tree_sha256 = [string]$finalManifest.tree_sha256
        file_count = [long]$finalManifest.file_count
        total_bytes = [long]$finalManifest.total_bytes
    }
}

function New-RuntimeBuildEvidence(
    [object]$FirstBuild,
    [object]$SecondBuild,
    [object]$ArchiveInput,
    [string]$ArchiveName,
    [object]$SourcePolicy,
    [object]$RuntimeLock,
    [object]$BuildLock,
    [object]$Application,
    [object]$ApplicationWheelProvenance,
    [string]$WheelhouseTreeSha,
    [string]$BuilderToolchainSha,
    [string]$VerifierSha256,
    [string]$Commit
) {
    $issued = [DateTime]::UtcNow
    $expires = $issued.AddHours(23)
    $buildInputs = [ordered]@{
        runtime_wheel_lock_sha256 = [string]$RuntimeLock.sha256
        build_wheel_lock_sha256 = [string]$BuildLock.sha256
        wheelhouse_tree_sha256 = $WheelhouseTreeSha
        application_wheel_sha256 = [string]$Application.sha256
        application_wheel_provenance = $ApplicationWheelProvenance
        builder_toolchain_sha256 = $BuilderToolchainSha
        runtime_wheel_count = [long]@($RuntimeLock.value.packages).Count
        build_wheel_count = [long]@($BuildLock.value.packages).Count
    }
    $buildInputsSha = Get-BytesSha256 (
        [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-CanonicalJson $buildInputs))
    )
    return [ordered]@{
        schema_version = 1
        format = "JOBFLOW_RUNTIME_BUILD_EVIDENCE_V1"
        evidence_kind = "SANITIZED_BUILD_OBSERVATION"
        issued_at_utc = $issued.ToString(
            "yyyy-MM-ddTHH:mm:ssZ", [Globalization.CultureInfo]::InvariantCulture
        )
        expires_at_utc = $expires.ToString(
            "yyyy-MM-ddTHH:mm:ssZ", [Globalization.CultureInfo]::InvariantCulture
        )
        application_version = [string]$Application.version
        source_commit = $Commit
        platform = "windows-x64"
        structural_status = "BUILT_UNATTESTED"
        archive = [ordered]@{
            name = $ArchiveName
            bytes = [long]$ArchiveInput.size
            sha256 = [string]$ArchiveInput.sha256
            archive_prefix = "JobFlow-v$([string]$Application.version)-windows-x64/"
        }
        runtime_closure = [ordered]@{
            manifest_sha256 = [string]$FirstBuild.manifest_sha256
            tree_sha256 = [string]$FirstBuild.tree_sha256
            source_payload_sha256 = [string]$ArchiveInput.sha256
            file_count = [long]$FirstBuild.file_count
            total_bytes = [long]$FirstBuild.total_bytes
            python_version = [string]$SourcePolicy.python.version
            platform = "windows-x64"
        }
        python_source = [ordered]@{
            version = [string]$SourcePolicy.python.version
            artifact_name = [string]$SourcePolicy.python.artifact_name
            artifact_bytes = [long]$SourcePolicy.python.artifact_bytes
            artifact_sha256 = [string]$SourcePolicy.python.artifact_sha256
            sigstore_bundle_name = [string]$SourcePolicy.python.artifact_name + ".sigstore"
            sigstore_bundle_bytes = [long]$SourcePolicy.python.sigstore_bundle_bytes
            sigstore_bundle_sha256 = [string]$SourcePolicy.python.sigstore_bundle_sha256
        }
        build_inputs = $buildInputs
        build_inputs_sha256 = $buildInputsSha
        deterministic_build = [ordered]@{
            pass_a_archive_sha256 = [string]$FirstBuild.sha256
            pass_b_archive_sha256 = [string]$SecondBuild.sha256
            pass_a_tree_sha256 = [string]$FirstBuild.tree_sha256
            pass_b_tree_sha256 = [string]$SecondBuild.tree_sha256
            match = $true
        }
        independent_verification = [ordered]@{
            status = "PASS"
            verifier_sha256 = $VerifierSha256
            archive_sha256 = [string]$ArchiveInput.sha256
            closure_manifest_sha256 = [string]$FirstBuild.manifest_sha256
            tree_sha256 = [string]$FirstBuild.tree_sha256
        }
        offline_smoke = [ordered]@{
            status = "PASS"
            result_token = "JOBFLOW_OFFLINE_SMOKE_OK"
            archive_sha256 = [string]$ArchiveInput.sha256
            closure_manifest_sha256 = [string]$FirstBuild.manifest_sha256
            tree_sha256 = [string]$FirstBuild.tree_sha256
            external_actions = 0
        }
        closure_self_claims = [ordered]@{
            sigstore_verified = $false
            outer_signature_ready = $false
        }
        external_actions = 0
    }
}

function Remove-SafeBuildRoot([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (
        [IO.Path]::GetDirectoryName($absolute).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $temp -or
        -not [IO.Path]::GetFileName($absolute).StartsWith("jobflow-runtime-build-", [StringComparison]::Ordinal)
    ) { throw "JOBFLOW_RUNTIME_BUILD_CLEANUP_REFUSED" }
    if ([IO.Directory]::Exists($absolute)) { Remove-Item -LiteralPath $absolute -Recurse -Force }
}

$script:RetainedRuntimeInputs = New-Object System.Collections.Generic.List[object]
$script:RetainedRuntimeInputsByPath = New-Object "System.Collections.Generic.Dictionary[string,object]" ([StringComparer]::OrdinalIgnoreCase)
$script:ProtectedRuntimeDirectoryLocks = New-Object System.Collections.Generic.List[object]
$script:ProtectedBuilderRuntime = $null
$script:SourceBuildRoot = $null
$script:RuntimeBuildRoot = $null
$script:RuntimeOutputRoot = $null
$script:CreatedRuntimeOutputs = New-Object System.Collections.Generic.List[string]
$script:RuntimeBuildSucceeded = $false
try {
$script:Project = Assert-OrdinaryInput $ProjectRoot -Directory
$script:PythonArtifactInput = Enter-RetainedRuntimeInput $PythonArtifactPath 1 268435456
$script:PythonArtifact = [string]$script:PythonArtifactInput.path
$script:SigstoreBundleInput = Enter-RetainedRuntimeInput $SigstoreBundlePath 1 16777216
$script:SigstoreBundle = [string]$script:SigstoreBundleInput.path
$script:Wheelhouse = Assert-OrdinaryInput $WheelhousePath -Directory
$script:GitInput = Enter-RetainedRuntimeInput $GitPath 1 536870912
$script:VerifierInput = Enter-RetainedRuntimeInput (Join-Path $script:Project "scripts\verify-windows-runtime-closure.ps1") 1 16777216
$script:Verifier = [string]$script:VerifierInput.path
$script:BuildScriptInput = Enter-RetainedRuntimeInput $MyInvocation.MyCommand.Path 1 16777216
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
if (-not [IO.Directory]::Exists($outputRoot)) { [IO.Directory]::CreateDirectory($outputRoot) | Out-Null }
$outputRoot = Assert-OrdinaryInput $outputRoot -Directory
$script:RuntimeOutputRoot = $outputRoot

$script:SourcePolicyInput = Enter-RetainedRuntimeInput (Join-Path $script:Project "config\windows-runtime-source.json") 2 1048576
$sourceDocument = Read-JsonObject $script:SourcePolicyInput
$source = $sourceDocument.value
Assert-ExactProperties $source @("schema_version", "status", "platform", "architecture", "python", "builder", "isolation", "attestation_policy") "JOBFLOW_RUNTIME_SOURCE_POLICY_INVALID"
Assert-ExactProperties $source.python @(
    "version", "artifact_name", "artifact_url", "artifact_bytes", "artifact_sha256", "release_page_url",
    "sigstore_bundle_url", "sigstore_bundle_bytes", "sigstore_bundle_sha256", "sigstore_transport_media_types", "sigstore_media_type",
    "sigstore_certificate_identity", "sigstore_certificate_oidc_issuer"
) "JOBFLOW_RUNTIME_SOURCE_POLICY_INVALID"
Assert-ExactProperties $source.builder @(
    "python_version", "python_architecture", "pip_version", "runtime_lock", "runtime_lock_sha256",
    "build_lock", "build_lock_sha256", "runtime_schema", "verification_script"
) "JOBFLOW_RUNTIME_SOURCE_POLICY_INVALID"
Assert-ExactProperties $source.isolation @(
    "python_pth", "import_site", "end_user_pip", "network_during_assembly", "network_during_smoke_test"
) "JOBFLOW_RUNTIME_SOURCE_POLICY_INVALID"
Assert-ExactProperties $source.attestation_policy @(
    "required_for_attested_status", "default_status", "public_release_allowed_status"
) "JOBFLOW_RUNTIME_SOURCE_POLICY_INVALID"
if (
    [int]$source.schema_version -ne 1 -or [string]$source.status -cne "PINNED_OFFICIAL_SOURCE" -or
    [string]$source.platform -cne "windows-x64" -or [string]$source.architecture -cne "AMD64" -or
    [string]$source.python.version -cne "3.13.15" -or
    [string]$source.python.artifact_name -cne "python-3.13.15-embed-amd64.zip" -or
    [string]$source.python.artifact_url -cne "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip" -or
    [long]$source.python.artifact_bytes -ne 11009825 -or
    [string]$source.python.artifact_sha256 -cne "sha256:d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf" -or
    [string]$source.python.release_page_url -cne "https://www.python.org/downloads/release/python-31315/" -or
    [string]$source.python.sigstore_bundle_url -cne "https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip.sigstore" -or
    [long]$source.python.sigstore_bundle_bytes -ne 7164 -or
    [string]$source.python.sigstore_bundle_sha256 -cne "sha256:3e487c064a40d94a59476eb05e2d6225c325665590797e0a03cd33592b617137" -or
    @($source.python.sigstore_transport_media_types).Count -ne 1 -or
    [string]@($source.python.sigstore_transport_media_types)[0] -cne "application/octet-stream" -or
    [string]$source.python.sigstore_media_type -cne "application/vnd.dev.sigstore.bundle.v0.3+json" -or
    [string]$source.python.sigstore_certificate_identity -cne "thomas@python.org" -or
    [string]$source.python.sigstore_certificate_oidc_issuer -cne "https://accounts.google.com" -or
    [string]$source.builder.python_version -cne "3.13.15" -or
    [string]$source.builder.python_architecture -cne "AMD64" -or
    [string]$source.builder.pip_version -cne "26.2.1" -or
    [string]$source.builder.runtime_lock -cne "config/windows-cp313-runtime.lock" -or
    [string]$source.builder.runtime_lock_sha256 -cne "sha256:fcff92c6cdc59601df54761c44314ec258d9dda39534abe0e5711eb0ab70bc9b" -or
    [string]$source.builder.build_lock -cne "config/windows-cp313-build.lock" -or
    [string]$source.builder.build_lock_sha256 -cne "sha256:b2deb4864339c5942842758843942472613290da464179a1f1b8a7b0a92d4453" -or
    [string]$source.builder.runtime_schema -cne "schemas/runtime-closure.schema.json" -or
    [string]$source.builder.verification_script -cne "scripts/verify-windows-runtime-closure.ps1" -or
    @($source.isolation.python_pth).Count -ne 3 -or
    [string]$source.isolation.python_pth[0] -cne "python313.zip" -or
    [string]$source.isolation.python_pth[1] -cne "." -or
    [string]$source.isolation.python_pth[2] -cne "../app" -or
    -not ($source.isolation.import_site -is [bool]) -or [bool]$source.isolation.import_site -or
    -not ($source.isolation.end_user_pip -is [bool]) -or [bool]$source.isolation.end_user_pip -or
    -not ($source.isolation.network_during_assembly -is [bool]) -or [bool]$source.isolation.network_during_assembly -or
    -not ($source.isolation.network_during_smoke_test -is [bool]) -or [bool]$source.isolation.network_during_smoke_test -or
    @($source.attestation_policy.required_for_attested_status).Count -ne 5 -or
    [string]$source.attestation_policy.required_for_attested_status[0] -cne "verified_psf_sigstore_evidence" -or
    [string]$source.attestation_policy.required_for_attested_status[1] -cne "deterministic_double_build_match" -or
    [string]$source.attestation_policy.required_for_attested_status[2] -cne "offline_smoke_passed" -or
    [string]$source.attestation_policy.required_for_attested_status[3] -cne "outer_signing_readiness_evidence" -or
    [string]$source.attestation_policy.required_for_attested_status[4] -cne "detached_signature_verified_with_pinned_trust" -or
    [string]$source.attestation_policy.default_status -cne "BUILT_UNATTESTED" -or
    [string]$source.attestation_policy.public_release_allowed_status -cne "ATTESTED"
) { throw "JOBFLOW_RUNTIME_SOURCE_POLICY_INVALID" }
if (
    [long]$script:PythonArtifactInput.size -ne [long]$source.python.artifact_bytes -or
    [string]$script:PythonArtifactInput.sha256 -cne [string]$source.python.artifact_sha256 -or
    [long]$script:SigstoreBundleInput.size -ne [long]$source.python.sigstore_bundle_bytes -or
    [string]$script:SigstoreBundleInput.sha256 -cne [string]$source.python.sigstore_bundle_sha256
) { throw "JOBFLOW_RUNTIME_OFFICIAL_SOURCE_MISMATCH" }

$script:ReleaseToolchainInput = Enter-RetainedRuntimeInput (Join-Path $script:Project "config\release-toolchain.json") 2 1048576
if ([string]$script:ReleaseToolchainInput.sha256 -cne $script:PinnedReleaseToolchainPolicySha256) {
    throw "JOBFLOW_RUNTIME_TOOLCHAIN_POLICY_UNTRUSTED"
}
$script:ReleaseToolchain = Read-BuilderPythonTrustPolicy $script:ReleaseToolchainInput
$script:GitPolicy = Read-BuilderPythonTrustPolicy $script:ReleaseToolchainInput "git"
$script:GitTrust = Assert-RetainedToolTrust $script:GitInput $script:GitPolicy `
    "JOBFLOW_RUNTIME_SOURCE_GIT_UNTRUSTED" "JOBFLOW_RUNTIME_SOURCE_GIT_TRUST_CHECK_FAILED"

$script:RuntimeLockInput = Enter-RetainedRuntimeInput (Join-Path $script:Project ([string]$source.builder.runtime_lock).Replace('/', '\')) 2 1048576
$script:BuildLockInput = Enter-RetainedRuntimeInput (Join-Path $script:Project ([string]$source.builder.build_lock).Replace('/', '\')) 2 1048576
$script:RuntimeLock = Read-Lock $script:RuntimeLockInput "runtime-wheelhouse"
$buildLock = Read-Lock $script:BuildLockInput "protected-builder-wheelhouse"
if (
    [string]$script:RuntimeLock.sha256 -cne [string]$source.builder.runtime_lock_sha256 -or
    [string]$buildLock.sha256 -cne [string]$source.builder.build_lock_sha256
) { throw "JOBFLOW_RUNTIME_LOCK_HASH_MISMATCH" }
$allPackages = @($script:RuntimeLock.value.packages) + @($buildLock.value.packages)
$wheelhouseIdentity = Assert-ExactWheelhouse $script:Wheelhouse $allPackages
$script:WheelhouseInputs = $wheelhouseIdentity.inputs
$script:SourceBuildRoot = Join-Path ([IO.Path]::GetTempPath()) ("jobflow-runtime-build-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($script:SourceBuildRoot) | Out-Null
try {
$script:ProtectedBuilder = Initialize-ProtectedBuilderRuntime `
    $script:SourceBuildRoot $source $buildLock $wheelhouseIdentity
$builderIdentity = Assert-BuilderPython $source
$completedWheelhouseIdentity = Complete-ExactWheelhouseIdentity $wheelhouseIdentity
$script:WheelhouseTreeSha = [string]$completedWheelhouseIdentity.tree_sha256
$script:GitRuntimeClosure = Get-RetainedGitRuntimeClosure $script:GitInput
if ([string]$script:GitRuntimeClosure.sha256 -cne [string]$script:GitPolicy.runtime_tree_sha256) {
    throw "JOBFLOW_RUNTIME_SOURCE_GIT_CLOSURE_UNTRUSTED"
}
$script:SourceIdentity = Get-TrustedSourceIdentity
$script:DeterministicSourceEpoch = [string]$script:SourceIdentity.source_date_epoch
$script:SourceSnapshots = New-TrustedSourceSnapshots $script:SourceBuildRoot $script:SourceIdentity
$script:ClosureVerifierInput = Enter-RetainedRuntimeInput (Join-Path $script:SourceSnapshots.snapshot_a "scripts\verify-windows-runtime-closure.ps1") 1 16777216

$toolchainMaterial = [ordered]@{
    digest_format = "JOBFLOW_BUILDER_TOOLCHAIN_DIGEST_V1"
    source_policy = $sourceDocument.sha256
    release_toolchain_policy = $script:ReleaseToolchain.document_sha256
    runtime_lock = $script:RuntimeLock.sha256
    build_lock = $buildLock.sha256
    builder_python = $builderIdentity.probe
    builder_python_sha256 = $script:BuilderPythonTrust.sha256
    builder_python_trust = $script:BuilderPythonTrust.trust
    builder_python_signer_subject = $script:BuilderPythonTrust.signer_subject
    builder_python_signer_thumbprint = $script:BuilderPythonTrust.signer_thumbprint
    protected_builder_tree_sha256 = [string]$script:ProtectedBuilder.tree_sha256
    protected_builder_file_count = [long]$script:ProtectedBuilder.file_count
    protected_builder_directory_count = [long]$script:ProtectedBuilder.directory_count
    protected_builder_source_artifact_sha256 = [string]$script:ProtectedBuilder.artifact_sha256
    protected_builder_pip_wheel_sha256 = [string]$script:ProtectedBuilder.pip_wheel_sha256
    protected_builder_pth_sha256 = [string]$script:ProtectedBuilder.pth_sha256
    source_git_sha256 = $script:GitTrust.sha256
    source_git_trust = $script:GitTrust.trust
    source_git_signer_subject = $script:GitTrust.signer_subject
    source_git_signer_thumbprint = $script:GitTrust.signer_thumbprint
    source_git_runtime_tree_sha256 = $script:GitRuntimeClosure.sha256
    source_git_runtime_file_count = [long]$script:GitRuntimeClosure.file_count
    builder_pip = [string]$source.builder.pip_version
    build_script = [string]$script:BuildScriptInput.sha256
    verify_script = [string]$script:ClosureVerifierInput.sha256
}
$toolchainSha = Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $toolchainMaterial)))
$recipeMaterial = [ordered]@{
    digest_format = "JOBFLOW_APPLICATION_WHEEL_BUILD_RECIPE_V1"
    source_commit = [string]$script:SourceIdentity.commit
    source_git_tree_oid = [string]$script:SourceIdentity.git_tree_oid
    source_build_tree_sha256 = [string]$script:SourceSnapshots.source_build_tree_sha256
    source_archive_sha256 = [string]$script:SourceSnapshots.archive_sha256
    build_lock_sha256 = [string]$buildLock.sha256
    builder_toolchain_sha256 = $toolchainSha
    backend = "setuptools.build_meta.build_wheel"
    config_settings = [ordered]@{}
    environment = [ordered]@{
        PIP_CONFIG_FILE = "NUL"
        PIP_DISABLE_PIP_VERSION_CHECK = "1"
        PIP_NO_INDEX = "1"
        PYTHONDONTWRITEBYTECODE = "1"
        PYTHONHASHSEED = "0"
        PYTHONNOUSERSITE = "1"
        SOURCE_DATE_EPOCH = [string]$script:DeterministicSourceEpoch
        TZ = "UTC"
    }
}
$buildToolsA = Initialize-PinnedBuildTools (Join-Path $script:SourceBuildRoot "pass-a-tools") $buildLock
$buildToolsB = Initialize-PinnedBuildTools (Join-Path $script:SourceBuildRoot "pass-b-tools") $buildLock
if ([string]$buildToolsA.tree_sha256 -cne [string]$buildToolsB.tree_sha256) {
    throw "JOBFLOW_RUNTIME_BUILD_TOOL_REPRODUCIBILITY_MISMATCH"
}
$recipeMaterial["build_tools_tree_sha256"] = [string]$buildToolsA.tree_sha256
$recipeSha = Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $recipeMaterial)))
$sourceWorkA = Join-Path $script:SourceBuildRoot "pass-a-source-work"
$sourceWorkB = Join-Path $script:SourceBuildRoot "pass-b-source-work"
Copy-SafeTree $script:SourceSnapshots.retained_a $sourceWorkA
Copy-SafeTree $script:SourceSnapshots.retained_b $sourceWorkB
$sourceWorkTreeA = Get-SourceSnapshotIdentity $sourceWorkA @($script:SourceIdentity.entries)
$sourceWorkTreeB = Get-SourceSnapshotIdentity $sourceWorkB @($script:SourceIdentity.entries)
if (
    [string]$sourceWorkTreeA.sha256 -cne [string]$script:SourceSnapshots.source_build_tree_sha256 -or
    [string]$sourceWorkTreeB.sha256 -cne [string]$script:SourceSnapshots.source_build_tree_sha256
) { throw "JOBFLOW_RUNTIME_SOURCE_BUILD_TREE_MISMATCH" }
# Setuptools may create standard metadata beside the source it builds.  Build
# only inside disposable copies while the two retained commit snapshots stay
# immutable and are revalidated after each pass.
$wheelA = Build-ApplicationWheel `
    $sourceWorkA `
    (Join-Path $script:SourceBuildRoot "wheel-a") `
    $buildToolsA.root `
    (Join-Path $script:SourceBuildRoot "pass-a-wheel-tmp")
$postBuildTreeA = Get-SourceSnapshotIdentity $script:SourceSnapshots.snapshot_a @($script:SourceIdentity.entries)
if ([string]$postBuildTreeA.sha256 -cne [string]$script:SourceSnapshots.source_build_tree_sha256) {
    throw "JOBFLOW_RUNTIME_SOURCE_BUILD_TREE_CHANGED"
}
$wheelB = Build-ApplicationWheel `
    $sourceWorkB `
    (Join-Path $script:SourceBuildRoot "wheel-b") `
    $buildToolsB.root `
    (Join-Path $script:SourceBuildRoot "pass-b-wheel-tmp")
$postBuildTreeB = Get-SourceSnapshotIdentity $script:SourceSnapshots.snapshot_b @($script:SourceIdentity.entries)
if ([string]$postBuildTreeB.sha256 -cne [string]$script:SourceSnapshots.source_build_tree_sha256) {
    throw "JOBFLOW_RUNTIME_SOURCE_BUILD_TREE_CHANGED"
}
$applicationA = Get-ApplicationWheelIdentity $wheelA
$applicationB = Get-ApplicationWheelIdentity $wheelB
$sourceVersionA = Get-SourceApplicationVersion $script:SourceSnapshots.snapshot_a
$sourceVersionB = Get-SourceApplicationVersion $script:SourceSnapshots.snapshot_b
if (
    [string]$applicationA.version -cne $sourceVersionA -or
    [string]$applicationB.version -cne $sourceVersionB -or
    [string]$applicationA.filename -cne [string]$applicationB.filename -or
    [string]$applicationA.sha256 -cne [string]$applicationB.sha256
) { throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_REPRODUCIBILITY_MISMATCH" }
$script:Application = $applicationA
$script:ApplicationWheelProvenance = [ordered]@{
    format = "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1"
    source_commit = [string]$script:SourceIdentity.commit
    source_git_tree_oid = [string]$script:SourceIdentity.git_tree_oid
    source_build_tree_sha256 = [string]$script:SourceSnapshots.source_build_tree_sha256
    source_archive_sha256 = [string]$script:SourceSnapshots.archive_sha256
    build_lock_sha256 = [string]$buildLock.sha256
    build_recipe_sha256 = $recipeSha
    pass_a_wheel_sha256 = [string]$applicationA.sha256
    pass_b_wheel_sha256 = [string]$applicationB.sha256
    reproducible = $true
}

$script:ProjectTreeSnapshots = New-Object "System.Collections.Generic.Dictionary[string,object]" ([StringComparer]::Ordinal)
foreach ($directoryName in @("schemas", "config", "browser-companion", "scripts")) {
    $script:ProjectTreeSnapshots[$directoryName] = Get-RetainedTreeSnapshot (Join-Path $script:SourceSnapshots.snapshot_a $directoryName)
}
$script:ProjectRootInputs = New-Object "System.Collections.Generic.Dictionary[string,object]" ([StringComparer]::Ordinal)
foreach ($fileName in @(".jobops-root", "LICENSE", "PRIVACY.md", "SECURITY.md")) {
    $script:ProjectRootInputs[$fileName] = Enter-RetainedRuntimeInput (Join-Path $script:SourceSnapshots.snapshot_a $fileName) 1 67108864
}
$status = "BUILT_UNATTESTED"

$evidenceMaterial = [ordered]@{
    digest_format = "JOBFLOW_PROTECTED_BUILDER_EVIDENCE_DIGEST_V1"
    attestation_stage = "POST_BUILD_PINNED_TRUST_REQUIRED"
    official_source = $sourceDocument.sha256
    runtime_lock = $script:RuntimeLock.sha256
    build_lock = $buildLock.sha256
    wheelhouse = $script:WheelhouseTreeSha
    application_wheel = $script:Application.sha256
    application_wheel_provenance = $script:ApplicationWheelProvenance
    builder_toolchain = $toolchainSha
}
$evidenceSha = Get-BytesSha256 ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $evidenceMaterial)))

$buildRoot = Join-Path ([IO.Path]::GetTempPath()) ("jobflow-runtime-build-" + [Guid]::NewGuid().ToString("N"))
$script:RuntimeBuildRoot = $buildRoot
[IO.Directory]::CreateDirectory($buildRoot) | Out-Null
try {
    $firstRoot = Join-Path $buildRoot "pass-a"
    $secondRoot = Join-Path $buildRoot "pass-b"
    [IO.Directory]::CreateDirectory($firstRoot) | Out-Null
    [IO.Directory]::CreateDirectory($secondRoot) | Out-Null
    $first = New-OneBuild $firstRoot $evidenceSha $toolchainSha
    $second = New-OneBuild $secondRoot $evidenceSha $toolchainSha
    if (
        $first.sha256 -cne $second.sha256 -or
        $first.manifest_sha256 -cne $second.manifest_sha256 -or
        $first.tree_sha256 -cne $second.tree_sha256 -or
        $first.file_count -ne $second.file_count -or
        $first.total_bytes -ne $second.total_bytes
    ) { throw "JOBFLOW_RUNTIME_DETERMINISTIC_REBUILD_MISMATCH" }

    $outputName = "JobFlow-v$($script:Application.version)-windows-x64-complete.zip"
    $outputPath = Join-Path $outputRoot $outputName
    $evidenceOutputName = "JobFlow-runtime-build-evidence.json"
    $evidenceOutputPath = Join-Path $outputRoot $evidenceOutputName
    if (
        [IO.File]::Exists($outputPath) -or [IO.Directory]::Exists($outputPath) -or
        [IO.File]::Exists($evidenceOutputPath) -or [IO.Directory]::Exists($evidenceOutputPath)
    ) { throw "JOBFLOW_RUNTIME_OUTPUT_EXISTS" }
    $sourceStream = [IO.File]::Open($first.zip, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $targetStream = [IO.File]::Open($outputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
    try { $sourceStream.CopyTo($targetStream); $targetStream.Flush($true) }
    finally { $targetStream.Dispose(); $sourceStream.Dispose() }
    $script:CreatedRuntimeOutputs.Add($outputPath)
    if ((Get-Sha256 $outputPath) -cne $first.sha256) { throw "JOBFLOW_RUNTIME_OUTPUT_COMMIT_MISMATCH" }
    # Re-open the exact committed output with a deny-write/delete retained
    # handle and run the verifier shipped by SourceCommit against the archive,
    # not merely the pre-ZIP staging tree.
    $outputInput = Enter-RetainedRuntimeInput $outputPath 1 $script:RuntimeArchiveMaximumUncompressedBytes $first.sha256
    Invoke-IndependentArchiveVerifier $outputInput $false
    $runtimeEvidence = New-RuntimeBuildEvidence `
        $first $second $outputInput $outputName $source $script:RuntimeLock $buildLock `
        $script:Application $script:ApplicationWheelProvenance $script:WheelhouseTreeSha `
        $toolchainSha ([string]$script:ClosureVerifierInput.sha256) $SourceCommit
    Write-Utf8NoBom $evidenceOutputPath (ConvertTo-CanonicalJson $runtimeEvidence)
    $script:CreatedRuntimeOutputs.Add($evidenceOutputPath)
    $evidenceInput = Enter-RetainedRuntimeInput $evidenceOutputPath 2 262144
    Invoke-RuntimeBuildEvidenceVerifier $first.closure $evidenceInput
    Invoke-IndependentVerifier $first.closure $false $false
    $result = [ordered]@{
        status = "COMPLETE_RUNTIME_BUILT"
        closure_status = $status
        artifact_name = $outputName
        artifact_sha256 = $first.sha256
        runtime_build_evidence_name = $evidenceOutputName
        runtime_build_evidence_sha256 = [string]$evidenceInput.sha256
        deterministic_rebuild_match = $true
        psf_sigstore_verified = $false
        outer_signing_ready = $false
        attestation_required = "POST_BUILD_DETACHED_SIGNATURE_WITH_PINNED_TRUST"
        offline_smoke_external_actions = 0
        public_release_blocked = ($status -cne "ATTESTED")
    } | ConvertTo-Json -Compress
    $script:RuntimeBuildSucceeded = $true
    [Console]::Out.Write($result)
}
finally { }
}
finally { }
}
finally {
    # Retained handles intentionally deny write/delete throughout every build,
    # archive, and verification phase.  Release them before deleting staging.
    Close-ProtectedRuntimeDirectoryLocks
    Close-RetainedRuntimeInputs
    if (-not $script:RuntimeBuildSucceeded -and $null -ne $script:RuntimeOutputRoot) {
        foreach ($createdPath in @($script:CreatedRuntimeOutputs)) {
            $absoluteCreated = [IO.Path]::GetFullPath([string]$createdPath)
            if (
                [IO.Path]::GetDirectoryName($absoluteCreated) -cne [string]$script:RuntimeOutputRoot -or
                [IO.Path]::GetFileName($absoluteCreated) -notin @(
                    "JobFlow-runtime-build-evidence.json",
                    "JobFlow-v$($script:Application.version)-windows-x64-complete.zip"
                )
            ) { throw "JOBFLOW_RUNTIME_OUTPUT_CLEANUP_REFUSED" }
            if ([IO.File]::Exists($absoluteCreated)) { [IO.File]::Delete($absoluteCreated) }
        }
    }
    if ($null -ne $script:RuntimeBuildRoot -and [IO.Directory]::Exists([string]$script:RuntimeBuildRoot)) {
        Remove-SafeBuildRoot ([string]$script:RuntimeBuildRoot)
    }
    if ($null -ne $script:SourceBuildRoot -and [IO.Directory]::Exists([string]$script:SourceBuildRoot)) {
        Remove-SafeBuildRoot ([string]$script:SourceBuildRoot)
    }
}
