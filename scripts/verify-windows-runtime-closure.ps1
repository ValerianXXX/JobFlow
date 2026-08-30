[CmdletBinding()]
param(
    [string]$RuntimeRoot,
    [string]$ArchivePath,
    [switch]$AllowUnattested
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Immutable complete-runtime archive limits.  These values must remain aligned
# with build-windows-runtime-closure.ps1 and the installed bootstrap preflight.
$script:RuntimeArchiveMaximumEntries = 65535
$script:RuntimeArchiveMaximumEntryBytes = [long]536870912
$script:RuntimeArchiveMaximumUncompressedBytes = [long]1610612736
$script:RuntimeArchiveCompressionRatioMinimumBytes = [long]1048576
$script:RuntimeArchiveMaximumCompressionRatio = [double]200.0

$runningOnWindows = $PSVersionTable.PSEdition -eq "Desktop" -or
    (Test-Path -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion")
if (-not $runningOnWindows) {
    throw "JOBFLOW_RUNTIME_VERIFY_WINDOWS_REQUIRED"
}
if ([string]::IsNullOrWhiteSpace($RuntimeRoot) -eq [string]::IsNullOrWhiteSpace($ArchivePath)) {
    throw "JOBFLOW_RUNTIME_VERIFY_ONE_INPUT_REQUIRED"
}

if (-not ("JobFlow.RuntimeNative" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace JobFlow {
    [StructLayout(LayoutKind.Sequential)]
    public struct RuntimeFileTime {
        public uint LowDateTime;
        public uint HighDateTime;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation {
        public uint FileAttributes;
        public RuntimeFileTime CreationTime;
        public RuntimeFileTime LastAccessTime;
        public RuntimeFileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    public static class RuntimeNative {
        public const uint FileReadAttributes = 0x80;
        public const uint FileShareRead = 0x1;
        public const uint OpenExisting = 3;
        public const uint FileFlagBackupSemantics = 0x02000000;
        public const uint FileFlagOpenReparsePoint = 0x00200000;

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetFileInformationByHandle(
            SafeFileHandle handle,
            out ByHandleFileInformation information
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern uint GetFinalPathNameByHandleW(
            SafeFileHandle handle,
            System.Text.StringBuilder path,
            uint pathLength,
            uint flags
        );
    }
}
"@
}

if (-not ("JobFlow.RuntimeZipPreflight" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace JobFlow {
    public sealed class RuntimeZipEntry {
        public string Name { get; internal set; }
        public long CompressedSize { get; internal set; }
        public long Length { get; internal set; }
        public long LocalOffset { get; internal set; }
    }

    public sealed class RuntimeZipResult {
        public string Prefix { get; internal set; }
        public RuntimeZipEntry[] Entries { get; internal set; }
        public long PayloadBytes { get; internal set; }
        public int PayloadFiles { get; internal set; }
    }

    public static class RuntimeZipPreflight {
        private const uint CentralSignature = 0x02014b50;
        private const uint LocalSignature = 0x04034b50;
        private const uint EndSignature = 0x06054b50;
        private const int MaximumEntries = 65535;

        private static void Fail() { throw new InvalidDataException("JOBFLOW_RUNTIME_ARCHIVE_INVALID"); }
        private static ushort U16(byte[] b, int o) { return (ushort)(b[o] | (b[o + 1] << 8)); }
        private static uint U32(byte[] b, int o) { return (uint)(b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)); }

        private static byte[] ReadExact(FileStream stream, int count) {
            byte[] value = new byte[count];
            int offset = 0;
            while (offset < count) {
                int read = stream.Read(value, offset, count - offset);
                if (read <= 0) Fail();
                offset += read;
            }
            return value;
        }

        private static string DecodeName(byte[] raw) {
            if (raw.Length == 0) Fail();
            for (int index = 0; index < raw.Length; index++)
                if (raw[index] < 0x20 || raw[index] > 0x7e) Fail();
            return Encoding.ASCII.GetString(raw);
        }

        private static bool ReservedSegment(string segment) {
            string stem = segment.Split('.')[0];
            if (stem.Equals("CON", StringComparison.OrdinalIgnoreCase) ||
                stem.Equals("PRN", StringComparison.OrdinalIgnoreCase) ||
                stem.Equals("AUX", StringComparison.OrdinalIgnoreCase) ||
                stem.Equals("NUL", StringComparison.OrdinalIgnoreCase) ||
                stem.Equals("CONIN$", StringComparison.OrdinalIgnoreCase) ||
                stem.Equals("CONOUT$", StringComparison.OrdinalIgnoreCase) ||
                stem.Equals("CLOCK$", StringComparison.OrdinalIgnoreCase)) return true;
            return stem.Length == 4 &&
                (stem.StartsWith("COM", StringComparison.OrdinalIgnoreCase) ||
                 stem.StartsWith("LPT", StringComparison.OrdinalIgnoreCase)) &&
                stem[3] >= '1' && stem[3] <= '9';
        }

        private static void ValidateName(string name, string prefix) {
            if (!name.StartsWith(prefix, StringComparison.Ordinal) || name.EndsWith("/", StringComparison.Ordinal) ||
                name.IndexOf('\\') >= 0 || name.StartsWith("/", StringComparison.Ordinal) || name.IndexOf(':') >= 0 ||
                name.IndexOfAny(new char[] { '<', '>', '"', '|', '?', '*' }) >= 0) Fail();
            string relative = name.Substring(prefix.Length);
            if (relative.Length == 0 || name.Length > 1024 || relative.Length > 768) Fail();
            foreach (string part in relative.Split('/')) {
                if (part.Length == 0 || part.Length > 255 || part == "." || part == ".." ||
                    part.EndsWith(".", StringComparison.Ordinal) || part.EndsWith(" ", StringComparison.Ordinal) ||
                    ReservedSegment(part)) Fail();
            }
        }

        public static RuntimeZipResult Preflight(FileStream stream, int maximumClosureBytes) {
            if (stream == null || !stream.CanRead || !stream.CanSeek || maximumClosureBytes < 1) Fail();
            long length = stream.Length;
            if (length < 22) Fail();
            int tailLength = checked((int)Math.Min(length, 65557L));
            stream.Position = length - tailLength;
            byte[] tail = ReadExact(stream, tailLength);
            int eocd = -1;
            for (int index = tail.Length - 22; index >= 0; index--) {
                if (U32(tail, index) == EndSignature && index + 22 + U16(tail, index + 20) == tail.Length) {
                    eocd = index; break;
                }
            }
            if (eocd < 0 || U16(tail, eocd + 20) != 0 || U16(tail, eocd + 4) != 0 ||
                U16(tail, eocd + 6) != 0 || U16(tail, eocd + 8) != U16(tail, eocd + 10)) Fail();
            int entryCount = U16(tail, eocd + 10);
            uint centralSize = U32(tail, eocd + 12);
            uint centralOffset = U32(tail, eocd + 16);
            long eocdAbsolute = length - tailLength + eocd;
            if (entryCount < 1 || entryCount > MaximumEntries || centralOffset == UInt32.MaxValue ||
                centralSize == UInt32.MaxValue || (long)centralOffset + centralSize != eocdAbsolute) Fail();

            var entries = new List<RuntimeZipEntry>(entryCount);
            var aliases = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var exact = new HashSet<string>(StringComparer.Ordinal);
            var localOffsets = new HashSet<long>();
            var localRanges = new List<long[]>();
            var files = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string prefix = null;
            long payloadBytes = 0;
            int payloadFiles = 0;
            int closureCount = 0;
            stream.Position = centralOffset;
            for (int item = 0; item < entryCount; item++) {
                byte[] header = ReadExact(stream, 46);
                if (U32(header, 0) != CentralSignature) Fail();
                ushort flags = U16(header, 8);
                ushort method = U16(header, 10);
                uint crc = U32(header, 16);
                uint compressed = U32(header, 20);
                uint uncompressed = U32(header, 24);
                ushort nameLength = U16(header, 28);
                ushort extraLength = U16(header, 30);
                ushort commentLength = U16(header, 32);
                ushort disk = U16(header, 34);
                uint external = U32(header, 38);
                uint localOffset = U32(header, 42);
                if ((flags & ~0x0800) != 0 || (method != 0 && method != 8) ||
                    compressed == UInt32.MaxValue || uncompressed == UInt32.MaxValue || localOffset == UInt32.MaxValue ||
                    extraLength != 0 || commentLength != 0 || disk != 0) Fail();
                string name = DecodeName(ReadExact(stream, nameLength));
                int slash = name.IndexOf('/');
                if (slash <= 0) Fail();
                string candidate = name.Substring(0, slash + 1);
                if (prefix == null) {
                    if (!Regex.IsMatch(candidate, @"^JobFlow-v[0-9]+\.[0-9]+\.[0-9]+-windows-x64/$")) Fail();
                    prefix = candidate;
                } else if (!String.Equals(prefix, candidate, StringComparison.Ordinal)) Fail();
                ValidateName(name, prefix);
                if (!exact.Add(name) || !aliases.Add(name) || !localOffsets.Add(localOffset)) Fail();
                string relative = name.Substring(prefix.Length);
                string[] treeParts = relative.Split('/');
                string parent = "";
                for (int part = 0; part < treeParts.Length - 1; part++) {
                    parent = parent.Length == 0 ? treeParts[part] : parent + "/" + treeParts[part];
                    if (files.Contains(parent)) Fail();
                    directories.Add(parent);
                }
                if (directories.Contains(relative) || !files.Add(relative)) Fail();
                uint unixType = (external >> 16) & 0xF000;
                uint dos = external & 0xFFFF;
                if ((dos & (0x10U | 0x40U | 0x400U | 0x4000U)) != 0 ||
                    (unixType != 0 && unixType != 0x8000) || uncompressed > 536870912U ||
                    (uncompressed > 0 && compressed == 0) || (method == 0 && compressed != uncompressed)) Fail();
                if (uncompressed > 1048576U && ((double)uncompressed / compressed) > 200.0) Fail();
                if (relative == "runtime-closure.json") {
                    if (++closureCount != 1 || uncompressed < 2 || uncompressed > maximumClosureBytes) Fail();
                } else {
                    payloadFiles++;
                    payloadBytes = checked(payloadBytes + uncompressed);
                    if (payloadBytes > 1610612736L) Fail();
                }
                entries.Add(new RuntimeZipEntry { Name=name, CompressedSize=compressed, Length=uncompressed, LocalOffset=localOffset });

                long saved = stream.Position;
                stream.Position = localOffset;
                byte[] local = ReadExact(stream, 30);
                if (U32(local, 0) != LocalSignature || U16(local, 6) != flags || U16(local, 8) != method ||
                    U32(local, 14) != crc || U32(local, 18) != compressed || U32(local, 22) != uncompressed ||
                    U16(local, 28) != 0) Fail();
                ushort localNameLength = U16(local, 26);
                string localName = DecodeName(ReadExact(stream, localNameLength));
                long localStart = localOffset;
                long localEnd = checked(stream.Position + compressed);
                if (!String.Equals(localName, name, StringComparison.Ordinal) || localEnd > centralOffset || localEnd <= localStart) Fail();
                foreach (long[] range in localRanges) if (localStart < range[1] && localEnd > range[0]) Fail();
                localRanges.Add(new long[] { localStart, localEnd });
                stream.Position = saved;
            }
            if (stream.Position != (long)centralOffset + centralSize || closureCount != 1) Fail();
            localRanges.Sort(delegate(long[] left, long[] right) { return left[0].CompareTo(right[0]); });
            long covered = 0;
            foreach (long[] range in localRanges) { if (range[0] != covered) Fail(); covered = range[1]; }
            if (covered != centralOffset) Fail();
            stream.Position = 0;
            return new RuntimeZipResult { Prefix=prefix, Entries=entries.ToArray(), PayloadBytes=payloadBytes, PayloadFiles=payloadFiles };
        }
    }
}
'@
}

$script:ReservedWindowsNames = @{
    "CON" = $true; "PRN" = $true; "AUX" = $true; "NUL" = $true
    "CONIN$" = $true; "CONOUT$" = $true; "CLOCK$" = $true
}
foreach ($index in 1..9) {
    $script:ReservedWindowsNames["COM$index"] = $true
    $script:ReservedWindowsNames["LPT$index"] = $true
}

function Assert-ExactProperties([object]$Value, [string[]]$Expected, [string]$Code) {
    if ($null -eq $Value) { throw $Code }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if ($actual.Count -ne $wanted.Count) { throw $Code }
    for ($index = 0; $index -lt $wanted.Count; $index++) {
        if ([string]$actual[$index] -cne [string]$wanted[$index]) { throw $Code }
    }
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

function Get-PortableJsonSha256([byte[]]$Bytes) {
    try { $text = [Text.UTF8Encoding]::new($false, $true).GetString($Bytes) }
    catch { throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID" }
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID" }
    $normalized = $text.Replace("`r`n", "`n")
    if ($normalized.Contains("`r")) { throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID" }
    return Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes($normalized))
}

function Test-JsonInteger([object]$Value, [long]$Minimum, [long]$Maximum) {
    return (($Value -is [int]) -or ($Value -is [long])) -and [long]$Value -ge $Minimum -and [long]$Value -le $Maximum
}

function Get-NormalizedRuntimePath([string]$Value) {
    if (
        [string]::IsNullOrEmpty($Value) -or
        $Value.Length -gt 768 -or
        $Value -cne $Value.Normalize([Text.NormalizationForm]::FormC) -or
        $Value.Contains("\") -or
        $Value.Contains(":") -or
        $Value.StartsWith("/", [StringComparison]::Ordinal) -or
        $Value.EndsWith("/", [StringComparison]::Ordinal)
    ) { throw "JOBFLOW_RUNTIME_PATH_INVALID" }
    $parts = $Value.Split([char]'/', [StringSplitOptions]::None)
    if ($parts.Count -eq 0) { throw "JOBFLOW_RUNTIME_PATH_INVALID" }
    foreach ($part in $parts) {
        if ([string]::IsNullOrEmpty($part) -or $part.Length -gt 255 -or $part -ceq "." -or $part -ceq "..") {
            throw "JOBFLOW_RUNTIME_PATH_INVALID"
        }
        if ($part.EndsWith(" ", [StringComparison]::Ordinal) -or $part.EndsWith(".", [StringComparison]::Ordinal)) {
            throw "JOBFLOW_RUNTIME_PATH_INVALID"
        }
        $base = $part.Split([char]'.', 2)[0].ToUpperInvariant()
        if ($script:ReservedWindowsNames.ContainsKey($base)) { throw "JOBFLOW_RUNTIME_PATH_INVALID" }
        foreach ($character in $part.ToCharArray()) {
            $code = [int]$character
            if ($code -lt 32 -or $code -gt 126 -or '"<>|?*'.Contains([string]$character)) {
                throw "JOBFLOW_RUNTIME_PATH_INVALID"
            }
        }
    }
    $canonical = [string]::Join("/", $parts)
    if ($canonical -cne $Value) { throw "JOBFLOW_RUNTIME_PATH_INVALID" }
    return $canonical
}

function Assert-NoReparsePath([string]$Path, [switch]$Directory) {
    $absolute = [IO.Path]::GetFullPath($Path)
    if ($Directory) {
        if (-not [IO.Directory]::Exists($absolute)) { throw "JOBFLOW_RUNTIME_ROOT_INVALID" }
    }
    elseif (-not [IO.File]::Exists($absolute)) { throw "JOBFLOW_RUNTIME_FILE_INVALID" }
    $cursor = Get-Item -LiteralPath $absolute -Force
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_RUNTIME_REPARSE_REJECTED"
        }
        if (($cursor.Attributes -band [IO.FileAttributes]::Encrypted) -ne 0) {
            throw "JOBFLOW_RUNTIME_ENCRYPTED_REJECTED"
        }
        $parentPath = [IO.Path]::GetDirectoryName($cursor.FullName)
        if ([string]::IsNullOrEmpty($parentPath) -or $parentPath -ceq $cursor.FullName) { break }
        $cursor = Get-Item -LiteralPath $parentPath -Force
    }
    return $absolute
}

function Get-OpenIdentity([IO.FileStream]$Stream, [string]$Code) {
    return Get-HandleIdentity $Stream.SafeFileHandle $Code
}

function Get-HandleIdentity([Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle, [string]$Code) {
    $information = New-Object JobFlow.ByHandleFileInformation
    if ($null -eq $Handle -or $Handle.IsInvalid -or -not [JobFlow.RuntimeNative]::GetFileInformationByHandle($Handle, [ref]$information)) {
        throw $Code
    }
    return [pscustomobject]@{
        attributes = [uint32]$information.FileAttributes
        links = [uint32]$information.NumberOfLinks
        volume = [uint32]$information.VolumeSerialNumber
        index_high = [uint32]$information.FileIndexHigh
        index_low = [uint32]$information.FileIndexLow
        size = (([uint64]$information.FileSizeHigh -shl 32) -bor [uint64]$information.FileSizeLow)
        write_high = [uint32]$information.LastWriteTime.HighDateTime
        write_low = [uint32]$information.LastWriteTime.LowDateTime
    }
}

function Get-FinalHandlePath([Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle, [string]$Code) {
    $capacity = 32768
    $builder = New-Object Text.StringBuilder $capacity
    $length = [JobFlow.RuntimeNative]::GetFinalPathNameByHandleW($Handle, $builder, [uint32]$capacity, 0)
    if ($length -eq 0 -or $length -ge $capacity) { throw $Code }
    $value = $builder.ToString()
    if ($value.StartsWith("\\?\UNC\", [StringComparison]::OrdinalIgnoreCase)) {
        return "\\" + $value.Substring(8)
    }
    if ($value.StartsWith("\\?\", [StringComparison]::Ordinal)) { return $value.Substring(4) }
    return $value
}

function Open-LockedDirectory([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $handle = [JobFlow.RuntimeNative]::CreateFileW(
        $absolute,
        [JobFlow.RuntimeNative]::FileReadAttributes,
        [JobFlow.RuntimeNative]::FileShareRead,
        [IntPtr]::Zero,
        [JobFlow.RuntimeNative]::OpenExisting,
        ([JobFlow.RuntimeNative]::FileFlagBackupSemantics -bor [JobFlow.RuntimeNative]::FileFlagOpenReparsePoint),
        [IntPtr]::Zero
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw "JOBFLOW_RUNTIME_DIRECTORY_LOCK_FAILED"
    }
    try {
        $identity = Get-HandleIdentity $handle "JOBFLOW_RUNTIME_DIRECTORY_LOCK_FAILED"
        $forbidden = [uint32]([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Encrypted -bor [IO.FileAttributes]::Device)
        if (($identity.attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or ($identity.attributes -band $forbidden) -ne 0) {
            throw "JOBFLOW_RUNTIME_DIRECTORY_LOCK_FAILED"
        }
        $finalPath = [IO.Path]::GetFullPath((Get-FinalHandlePath $handle "JOBFLOW_RUNTIME_DIRECTORY_LOCK_FAILED"))
        if (-not $finalPath.Equals($absolute, [StringComparison]::OrdinalIgnoreCase)) {
            throw "JOBFLOW_RUNTIME_DIRECTORY_LOCK_FAILED"
        }
        return [pscustomobject]@{ path = $absolute; handle = $handle; identity = $identity }
    }
    catch {
        $handle.Dispose()
        throw
    }
}

function Open-DirectoryLockSet([string]$Root) {
    $absoluteRoot = [IO.Path]::GetFullPath($Root)
    $paths = New-Object System.Collections.Generic.List[string]
    $paths.Add($absoluteRoot)
    foreach ($directory in @(Get-ChildItem -LiteralPath $absoluteRoot -Directory -Force -Recurse | Sort-Object { $_.FullName.Length }, FullName)) {
        $paths.Add([IO.Path]::GetFullPath($directory.FullName))
    }
    $locks = New-Object System.Collections.Generic.List[object]
    try {
        foreach ($path in $paths) { $locks.Add((Open-LockedDirectory $path)) }
        return $locks.ToArray()
    }
    catch {
        foreach ($lock in @($locks)) { $lock.handle.Dispose() }
        throw
    }
}

function Close-DirectoryLockSet([object[]]$Locks, [switch]$Verify) {
    $failure = $null
    foreach ($lock in @($Locks)) {
        try {
            if ($Verify) {
                $after = Get-HandleIdentity $lock.handle "JOBFLOW_RUNTIME_DIRECTORY_CHANGED"
                if (-not (Test-SameDirectoryIdentity $lock.identity $after)) { throw "JOBFLOW_RUNTIME_DIRECTORY_CHANGED" }
            }
        }
        catch { if ($null -eq $failure) { $failure = $_ } }
    }
    foreach ($lock in @($Locks)) { $lock.handle.Dispose() }
    if ($null -ne $failure) { throw $failure }
}

function Test-SameIdentity([object]$Before, [object]$After) {
    foreach ($name in @("attributes", "links", "volume", "index_high", "index_low", "size", "write_high", "write_low")) {
        if ($Before.$name -ne $After.$name) { return $false }
    }
    return $true
}

function Test-SameDirectoryIdentity([object]$Before, [object]$After) {
    foreach ($name in @("attributes", "volume", "index_high", "index_low")) {
        if ($Before.$name -ne $After.$name) { return $false }
    }
    return $true
}

function Assert-NoAlternateDataStreams([string]$Path) {
    try { $streams = @(Get-Item -LiteralPath $Path -Stream * -ErrorAction Stop) }
    catch { throw "JOBFLOW_RUNTIME_ADS_INSPECTION_FAILED" }
    if ($streams.Count -ne 1 -or ([string]$streams[0].Stream -notin @("`$DATA", ":`$DATA"))) {
        throw "JOBFLOW_RUNTIME_ADS_REJECTED"
    }
}

function Read-LockedFile([string]$Path, [switch]$ReturnBytes) {
    $absolute = Assert-NoReparsePath $Path
    Assert-NoAlternateDataStreams $absolute
    $stream = [IO.File]::Open($absolute, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $before = Get-OpenIdentity $stream "JOBFLOW_RUNTIME_FILE_IDENTITY_INVALID"
        $forbiddenAttributes = [uint32]([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Directory -bor [IO.FileAttributes]::Device -bor [IO.FileAttributes]::Encrypted)
        if ($before.links -ne 1 -or ($before.attributes -band $forbiddenAttributes) -ne 0) {
            throw "JOBFLOW_RUNTIME_FILE_IDENTITY_INVALID"
        }
        $sha = [Security.Cryptography.SHA256]::Create()
        $memory = $null
        try {
            $memory = if ($ReturnBytes) { New-Object IO.MemoryStream } else { $null }
            $buffer = New-Object byte[] (1024 * 1024)
            [uint64]$total = 0
            while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $sha.TransformBlock($buffer, 0, $count, $null, 0) | Out-Null
                if ($null -ne $memory) { $memory.Write($buffer, 0, $count) }
                $total += [uint64]$count
            }
            $empty = New-Object byte[] 0
            $sha.TransformFinalBlock($empty, 0, 0) | Out-Null
            $after = Get-OpenIdentity $stream "JOBFLOW_RUNTIME_FILE_IDENTITY_INVALID"
            if (-not (Test-SameIdentity $before $after) -or $total -ne $before.size) {
                throw "JOBFLOW_RUNTIME_FILE_CHANGED"
            }
            return [pscustomobject]@{
                size = [long]$total
                sha256 = "sha256:" + (ConvertTo-LowerHex $sha.Hash)
                bytes = if ($null -ne $memory) { $memory.ToArray() } else { $null }
            }
        }
        finally {
            if ($null -ne $memory) { $memory.Dispose() }
            $sha.Dispose()
        }
    }
    finally { $stream.Dispose() }
}

function Get-RuntimeInventory([string]$Root, [string]$ExcludedRelative) {
    $absoluteRoot = Assert-NoReparsePath $Root -Directory
    $excludedKey = (Get-NormalizedRuntimePath $ExcludedRelative).ToLowerInvariant()
    $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::Ordinal)
    $records = New-Object System.Collections.Generic.List[object]

    function Visit-Directory([string]$DirectoryPath, [string]$Prefix) {
        $directoryItem = Get-Item -LiteralPath $DirectoryPath -Force
        if (($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_RUNTIME_REPARSE_REJECTED"
        }
        $entries = @(Get-ChildItem -LiteralPath $DirectoryPath -Force | Sort-Object `
            @{Expression = { $_.Name.Normalize([Text.NormalizationForm]::FormC).ToLowerInvariant() }}, `
            @{Expression = { $_.Name }})
        foreach ($entry in $entries) {
            $relative = if ([string]::IsNullOrEmpty($Prefix)) { $entry.Name } else { "$Prefix/$($entry.Name)" }
            $relative = Get-NormalizedRuntimePath $relative
            $alias = $relative.ToLowerInvariant()
            if (-not $aliases.Add($alias)) { throw "JOBFLOW_RUNTIME_PATH_COLLISION" }
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_RUNTIME_REPARSE_REJECTED"
            }
            if ($entry.PSIsContainer) {
                Visit-Directory $entry.FullName $relative
                continue
            }
            if (($entry.Attributes -band [IO.FileAttributes]::Directory) -ne 0) {
                throw "JOBFLOW_RUNTIME_SPECIAL_FILE_REJECTED"
            }
            $locked = Read-LockedFile $entry.FullName
            if ($alias -cne $excludedKey) {
                $records.Add([pscustomobject][ordered]@{
                    path = $relative
                    size = [long]$locked.size
                    sha256 = [string]$locked.sha256
                })
            }
        }
    }

    Visit-Directory $absoluteRoot ""
    return $records.ToArray()
}

function Get-TreeSha256([object[]]$Records) {
    $jsonRecords = foreach ($record in $Records) {
        $pathJson = ConvertTo-Json ([string]$record.path) -Compress
        $shaJson = ConvertTo-Json ([string]$record.sha256) -Compress
        "{`"path`":$pathJson,`"sha256`":$shaJson,`"size`":$([long]$record.size)}"
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes("[" + [string]::Join(",", $jsonRecords) + "]")
    return Get-BytesSha256 $bytes
}

function Assert-ApplicationWheelProvenance([object]$Manifest) {
    $provenance = $Manifest.build_inputs.application_wheel_provenance
    Assert-ExactProperties $provenance @(
        "format", "source_commit", "source_git_tree_oid", "source_build_tree_sha256",
        "source_archive_sha256", "build_lock_sha256", "build_recipe_sha256",
        "pass_a_wheel_sha256", "pass_b_wheel_sha256", "reproducible"
    ) "JOBFLOW_RUNTIME_APPLICATION_WHEEL_PROVENANCE_INVALID"
    if (
        [string]$provenance.format -cne "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1" -or
        [string]$provenance.source_commit -cne [string]$Manifest.source_commit -or
        [string]$provenance.source_git_tree_oid -notmatch '^[0-9a-f]{40}$' -or
        -not ($provenance.reproducible -is [bool]) -or -not [bool]$provenance.reproducible
    ) { throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_PROVENANCE_INVALID" }
    foreach ($name in @(
        "source_build_tree_sha256", "source_archive_sha256", "build_lock_sha256",
        "build_recipe_sha256", "pass_a_wheel_sha256", "pass_b_wheel_sha256"
    )) {
        if ([string]$provenance.$name -notmatch '^sha256:[0-9a-f]{64}$') {
            throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_PROVENANCE_INVALID"
        }
    }
    if (
        [string]$provenance.pass_a_wheel_sha256 -cne [string]$Manifest.build_inputs.application_wheel_sha256 -or
        [string]$provenance.pass_b_wheel_sha256 -cne [string]$Manifest.build_inputs.application_wheel_sha256
    ) { throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_REPRODUCIBILITY_MISMATCH" }
}

function Assert-ManifestShape([object]$Manifest, [switch]$PermitUnattested) {
    Assert-ExactProperties $Manifest @(
        "schema_version", "status", "artifact_type", "platform", "application_version",
        "source_commit", "python", "build_inputs", "layout", "file_count", "total_bytes",
        "tree_sha256", "files", "offline_smoke_tests", "protected_builder"
    ) "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    if (-not (Test-JsonInteger $Manifest.schema_version 1 1)) { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }
    if ([string]$Manifest.artifact_type -cne "complete-runtime" -or [string]$Manifest.platform -cne "windows-x64") {
        throw "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    }
    if ([string]$Manifest.application_version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$') {
        throw "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    }
    if ([string]$Manifest.source_commit -notmatch '^[0-9a-f]{40}$') { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }
    if ([string]$Manifest.status -cne "BUILT_UNATTESTED") {
        # A closure manifest is deliberately not a trust root.  ATTESTED is a
        # later envelope status which requires a detached signature verified
        # against a pinned publisher key.  Until that verifier exists, no
        # local JSON value (including otherwise plausible evidence booleans)
        # can promote this structural closure.
        if ([string]$Manifest.status -ceq "ATTESTED") {
            throw "JOBFLOW_RUNTIME_ATTESTATION_UNVERIFIABLE"
        }
        throw "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    }
    if (-not $PermitUnattested) {
        throw "JOBFLOW_RUNTIME_CLOSURE_UNATTESTED"
    }

    Assert-ExactProperties $Manifest.python @(
        "version", "artifact_name", "artifact_sha256", "sigstore_identity", "sigstore_verified"
    ) "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    if (
        [string]$Manifest.python.version -cne "3.13.15" -or
        [string]$Manifest.python.artifact_name -cne "python-3.13.15-embed-amd64.zip" -or
        [string]$Manifest.python.artifact_sha256 -cne "sha256:d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf" -or
        [string]$Manifest.python.sigstore_identity -cne "thomas@python.org" -or
        -not ($Manifest.python.sigstore_verified -is [bool]) -or
        [bool]$Manifest.python.sigstore_verified
    ) { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }

    Assert-ExactProperties $Manifest.build_inputs @(
        "wheel_lock_sha256", "wheelhouse_tree_sha256", "application_wheel_sha256",
        "application_wheel_provenance", "builder_toolchain_sha256", "wheels"
    ) "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    if ($Manifest.build_inputs.wheels -isnot [Collections.IList]) {
        throw "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    }
    foreach ($name in @("wheel_lock_sha256", "wheelhouse_tree_sha256", "application_wheel_sha256", "builder_toolchain_sha256")) {
        if ([string]$Manifest.build_inputs.$name -notmatch '^sha256:[0-9a-f]{64}$') {
            throw "JOBFLOW_RUNTIME_MANIFEST_INVALID"
        }
    }
    $wheelAliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    foreach ($wheel in @($Manifest.build_inputs.wheels)) {
        Assert-ExactProperties $wheel @("name", "version", "tag", "size", "sha256") "JOBFLOW_RUNTIME_MANIFEST_INVALID"
        if (
            [string]$wheel.name -notmatch '^[A-Za-z0-9_.-]+$' -or
            [string]$wheel.version -notmatch '^[A-Za-z0-9_.+-]+$' -or
            [string]$wheel.tag -notmatch '^[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+$' -or
            -not (Test-JsonInteger $wheel.size 1 ([long]::MaxValue)) -or
            [string]$wheel.sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            -not $wheelAliases.Add([string]$wheel.name)
        ) { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }
    }
    Assert-ApplicationWheelProvenance $Manifest

    Assert-ExactProperties $Manifest.layout @("python", "python_pth", "application_root", "module") "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    if (
        [string]$Manifest.layout.python -cne "runtime/python.exe" -or
        [string]$Manifest.layout.python_pth -cne "runtime/python313._pth" -or
        [string]$Manifest.layout.application_root -cne "app" -or
        [string]$Manifest.layout.module -cne "jobops.cli"
    ) { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }

    Assert-ExactProperties $Manifest.offline_smoke_tests @("import_passed", "schema_passed", "external_actions") "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    if (
        -not ($Manifest.offline_smoke_tests.import_passed -is [bool]) -or
        -not [bool]$Manifest.offline_smoke_tests.import_passed -or
        -not ($Manifest.offline_smoke_tests.schema_passed -is [bool]) -or
        -not [bool]$Manifest.offline_smoke_tests.schema_passed -or
        -not (Test-JsonInteger $Manifest.offline_smoke_tests.external_actions 0 0)
    ) { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }

    Assert-ExactProperties $Manifest.protected_builder @(
        "evidence_sha256", "deterministic_rebuild_match", "outer_signature_ready"
    ) "JOBFLOW_RUNTIME_MANIFEST_INVALID"
    if (
        [string]$Manifest.protected_builder.evidence_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        -not ($Manifest.protected_builder.deterministic_rebuild_match -is [bool]) -or
        -not [bool]$Manifest.protected_builder.deterministic_rebuild_match -or
        -not ($Manifest.protected_builder.outer_signature_ready -is [bool]) -or
        [bool]$Manifest.protected_builder.outer_signature_ready
    ) { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }

}

function Assert-RuntimeLockBinding([string]$Root, [object]$Manifest) {
    $lockPath = Join-Path $Root "config\windows-cp313-runtime.lock"
    $locked = Read-LockedFile $lockPath -ReturnBytes
    $portableLockSha = Get-PortableJsonSha256 ([byte[]]$locked.bytes)
    if ([string]$Manifest.build_inputs.wheel_lock_sha256 -cne $portableLockSha) {
        throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID"
    }
    $buildLockPath = Join-Path $Root "config\windows-cp313-build.lock"
    $buildLocked = Read-LockedFile $buildLockPath -ReturnBytes
    $portableBuildLockSha = Get-PortableJsonSha256 ([byte[]]$buildLocked.bytes)
    if ([string]$Manifest.build_inputs.application_wheel_provenance.build_lock_sha256 -cne $portableBuildLockSha) {
        throw "JOBFLOW_RUNTIME_APPLICATION_WHEEL_BUILD_LOCK_MISMATCH"
    }
    try {
        $raw = [Text.UTF8Encoding]::new($false, $true).GetString([byte[]]$locked.bytes)
        $value = $raw | ConvertFrom-Json
    }
    catch { throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID" }
    Assert-ExactProperties $value @("schema_version", "lock_type", "python_tag", "abi", "platform", "only_binary", "packages") "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID"
    if (
        -not (Test-JsonInteger $value.schema_version 1 1) -or
        [string]$value.lock_type -cne "runtime-wheelhouse" -or
        [string]$value.python_tag -cne "cp313" -or
        [string]$value.abi -cne "cp313-or-abi3" -or
        [string]$value.platform -cne "win_amd64" -or
        -not ($value.only_binary -is [bool]) -or -not [bool]$value.only_binary -or
        $value.packages -isnot [Collections.IList]
    ) { throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID" }
    $packages = @($value.packages)
    $wheels = @($Manifest.build_inputs.wheels)
    if ($packages.Count -ne $wheels.Count -or $packages.Count -eq 0) {
        throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID"
    }
    $names = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    $files = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
    for ($index = 0; $index -lt $packages.Count; $index++) {
        $package = $packages[$index]
        $wheel = $wheels[$index]
        Assert-ExactProperties $package @("name", "version", "filename", "size", "sha256") "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID"
        $filename = [string]$package.filename
        if ($filename -notmatch '-([^-]+-[^-]+-[^-]+)\.whl$') { throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID" }
        $tag = $Matches[1]
        if (
            [string]$package.name -notmatch '^[A-Za-z0-9_.-]+$' -or
            [string]$package.version -notmatch '^[A-Za-z0-9_.+-]+$' -or
            $filename -notmatch '^[A-Za-z0-9_.+-]+\.whl$' -or
            -not (Test-JsonInteger $package.size 1 ([long]::MaxValue)) -or
            [string]$package.sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            -not $names.Add([string]$package.name) -or
            -not $files.Add($filename) -or
            [string]$wheel.name -cne [string]$package.name -or
            [string]$wheel.version -cne [string]$package.version -or
            [string]$wheel.tag -cne $tag -or
            [long]$wheel.size -ne [long]$package.size -or
            [string]$wheel.sha256 -cne [string]$package.sha256
        ) { throw "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID" }
    }
    foreach ($forbidden in @("pip", "setuptools", "wheel")) {
        if ($names.Contains($forbidden)) { throw "JOBFLOW_RUNTIME_END_USER_PIP_REJECTED" }
    }
}

function Test-RuntimeRoot([string]$Root, [switch]$PermitUnattested) {
    $absoluteRoot = Assert-NoReparsePath $Root -Directory
    $manifestPath = Join-Path $absoluteRoot "runtime-closure.json"
    $manifestRead = Read-LockedFile $manifestPath -ReturnBytes
    if ($manifestRead.size -lt 2 -or $manifestRead.size -gt 16777216) { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }
    $rawText = [Text.UTF8Encoding]::new($false, $true).GetString([byte[]]$manifestRead.bytes)
    if ($rawText -match '(?i)(?:[A-Z]:[\\/]|\\\\[^\\])') { throw "JOBFLOW_RUNTIME_ABSOLUTE_PATH_LEAK" }
    try { $manifest = $rawText | ConvertFrom-Json }
    catch { throw "JOBFLOW_RUNTIME_MANIFEST_INVALID" }
    Assert-ManifestShape $manifest -PermitUnattested:$PermitUnattested

    $actual = @(Get-RuntimeInventory $absoluteRoot "runtime-closure.json")
    $expected = @($manifest.files)
    if ($actual.Count -ne $expected.Count) { throw "JOBFLOW_RUNTIME_INVENTORY_MISMATCH" }
    $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::Ordinal)
    for ($index = 0; $index -lt $expected.Count; $index++) {
        $item = $expected[$index]
        Assert-ExactProperties $item @("path", "size", "sha256") "JOBFLOW_RUNTIME_MANIFEST_INVALID"
        $path = Get-NormalizedRuntimePath ([string]$item.path)
        if (-not $aliases.Add($path.ToLowerInvariant())) { throw "JOBFLOW_RUNTIME_PATH_COLLISION" }
        if (
            -not (Test-JsonInteger $item.size 0 ([long]::MaxValue)) -or
            [string]$item.sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            [string]$actual[$index].path -cne $path -or
            [long]$actual[$index].size -ne [long]$item.size -or
            [string]$actual[$index].sha256 -cne [string]$item.sha256
        ) { throw "JOBFLOW_RUNTIME_INVENTORY_MISMATCH" }
    }
    if (
        -not (Test-JsonInteger $manifest.file_count 1 ([long]::MaxValue)) -or
        [long]$manifest.file_count -ne $actual.Count -or
        -not (Test-JsonInteger $manifest.total_bytes 1 ([long]::MaxValue)) -or
        [long]$manifest.total_bytes -ne [long](($actual | Measure-Object -Property size -Sum).Sum) -or
        [string]$manifest.tree_sha256 -cne (Get-TreeSha256 $actual)
    ) { throw "JOBFLOW_RUNTIME_INVENTORY_MISMATCH" }

    $paths = @{}
    foreach ($record in $actual) { $paths[[string]$record.path] = $true }
    foreach ($required in @(
        ".jobops-root",
        "runtime/python.exe", "runtime/python313.dll", "runtime/python313._pth", "runtime/python313.zip",
        "app/jobops/__init__.py", "app/jobops/cli.py", "app/jobops/runtime_health.py",
        "config/windows-cp313-build.lock", "config/windows-cp313-runtime.lock"
    )) {
        if (-not $paths.ContainsKey($required)) { throw "JOBFLOW_RUNTIME_LAYOUT_MISSING" }
    }
    Assert-RuntimeLockBinding $absoluteRoot $manifest
    $pth = Read-LockedFile (Join-Path $absoluteRoot "runtime\python313._pth") -ReturnBytes
    $expectedPth = [Text.UTF8Encoding]::new($false).GetBytes("python313.zip`n.`n../app`n")
    if ($pth.bytes.Length -ne $expectedPth.Length) { throw "JOBFLOW_RUNTIME_PTH_INVALID" }
    for ($index = 0; $index -lt $expectedPth.Length; $index++) {
        if ($pth.bytes[$index] -ne $expectedPth[$index]) { throw "JOBFLOW_RUNTIME_PTH_INVALID" }
    }
    return [pscustomobject]@{
        status = "RUNTIME_CLOSURE_VERIFIED"
        closure_status = [string]$manifest.status
        application_version = [string]$manifest.application_version
        tree_sha256 = [string]$manifest.tree_sha256
        file_count = [long]$manifest.file_count
        total_bytes = [long]$manifest.total_bytes
        external_actions = 0
    }
}

function Remove-VerificationTemp([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (
        [IO.Path]::GetDirectoryName($absolute).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $tempRoot -or
        -not [IO.Path]::GetFileName($absolute).StartsWith("jobflow-runtime-verify-", [StringComparison]::Ordinal)
    ) { throw "JOBFLOW_RUNTIME_TEMP_CLEANUP_REFUSED" }
    if ([IO.Directory]::Exists($absolute)) { Remove-Item -LiteralPath $absolute -Recurse -Force }
}

function Test-RuntimeArchive([string]$Path, [switch]$PermitUnattested) {
    $absolute = Assert-NoReparsePath $Path
    Assert-NoAlternateDataStreams $absolute
    $archiveStream = [IO.File]::Open($absolute, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("jobflow-runtime-verify-" + [Guid]::NewGuid().ToString("N"))
    [IO.Directory]::CreateDirectory($tempRoot) | Out-Null
    $directoryLocks = @()
    $targetStreams = New-Object System.Collections.Generic.List[IO.FileStream]
    $locksVerified = $false
    try {
        $identity = Get-OpenIdentity $archiveStream "JOBFLOW_RUNTIME_ARCHIVE_IDENTITY_INVALID"
        if ($identity.links -ne 1 -or ($identity.attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_RUNTIME_ARCHIVE_IDENTITY_INVALID"
        }
        try { $preflight = [JobFlow.RuntimeZipPreflight]::Preflight($archiveStream, 16777216) }
        catch { throw "JOBFLOW_RUNTIME_ARCHIVE_INVALID" }
        $prefix = [string]$preflight.Prefix
        $archiveStream.Position = 0
        $zip = New-Object IO.Compression.ZipArchive($archiveStream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try {
            $entryCount = [long]$zip.Entries.Count
            if (
                $entryCount -lt 1 -or
                $entryCount -gt $script:RuntimeArchiveMaximumEntries -or
                $entryCount -ne @($preflight.Entries).Count
            ) {
                throw "JOBFLOW_RUNTIME_ARCHIVE_ENTRY_COUNT_INVALID"
            }
            $aliases = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
            $files = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
            $directories = New-Object "System.Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
            $validated = New-Object System.Collections.Generic.List[object]
            [long]$total = 0
            [int]$entryIndex = 0
            foreach ($entry in $zip.Entries) {
                if ($entry.FullName.EndsWith("/", [StringComparison]::Ordinal)) {
                    throw "JOBFLOW_RUNTIME_ARCHIVE_DIRECTORY_ENTRY_REJECTED"
                }
                $slash = $entry.FullName.IndexOf([char]'/')
                if ($slash -le 0) { throw "JOBFLOW_RUNTIME_ARCHIVE_PREFIX_INVALID" }
                $currentPrefix = $entry.FullName.Substring(0, $slash + 1)
                if ($currentPrefix -cne $prefix) { throw "JOBFLOW_RUNTIME_ARCHIVE_PREFIX_INVALID" }
                $rawEntry = @($preflight.Entries)[$entryIndex]
                if (
                    [string]$rawEntry.Name -cne [string]$entry.FullName -or
                    [long]$rawEntry.Length -ne [long]$entry.Length -or
                    [long]$rawEntry.CompressedSize -ne [long]$entry.CompressedLength
                ) { throw "JOBFLOW_RUNTIME_ARCHIVE_INVALID" }
                $entryIndex++
                if ($entry.FullName.Length -gt 1024) { throw "JOBFLOW_RUNTIME_PATH_INVALID" }
                $relative = Get-NormalizedRuntimePath $entry.FullName.Substring($prefix.Length)
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
                $unixMode = ([uint32]$entry.ExternalAttributes -shr 16) -band 0xF000
                if ($unixMode -ne 0 -and $unixMode -ne 0x8000) { throw "JOBFLOW_RUNTIME_SPECIAL_FILE_REJECTED" }
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
                $validated.Add([pscustomobject]@{ entry = $entry; relative = $relative })
            }
            if ($aliases.Count -eq 0) { throw "JOBFLOW_RUNTIME_ARCHIVE_EMPTY" }

            foreach ($item in $validated) {
                $destination = Join-Path $tempRoot (([string]$item.relative).Replace('/', [IO.Path]::DirectorySeparatorChar))
                $parent = [IO.Path]::GetDirectoryName($destination)
                [IO.Directory]::CreateDirectory($parent) | Out-Null
                Assert-NoReparsePath $parent -Directory | Out-Null
            }
            $directoryLocks = @(Open-DirectoryLockSet $tempRoot)
            foreach ($item in $validated) {
                $entry = $item.entry
                $relative = [string]$item.relative
                $destination = Join-Path $tempRoot ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
                $parent = [IO.Path]::GetDirectoryName($destination)
                Assert-NoReparsePath $parent -Directory | Out-Null
                $source = $entry.Open()
                $target = [IO.File]::Open($destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                try {
                    $source.CopyTo($target)
                    $target.Flush($true)
                    $fileIdentity = Get-OpenIdentity $target "JOBFLOW_RUNTIME_ARCHIVE_OUTPUT_INVALID"
                    $forbidden = [uint32]([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Directory -bor [IO.FileAttributes]::Device -bor [IO.FileAttributes]::Encrypted)
                    if ($fileIdentity.links -ne 1 -or ($fileIdentity.attributes -band $forbidden) -ne 0 -or $fileIdentity.size -ne [uint64]$entry.Length) {
                        throw "JOBFLOW_RUNTIME_ARCHIVE_OUTPUT_INVALID"
                    }
                }
                finally {
                    $target.Dispose()
                    $source.Dispose()
                }
                $readLock = [IO.File]::Open($destination, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
                try {
                    $readIdentity = Get-OpenIdentity $readLock "JOBFLOW_RUNTIME_ARCHIVE_OUTPUT_INVALID"
                    if ($readIdentity.links -ne 1 -or ($readIdentity.attributes -band $forbidden) -ne 0 -or $readIdentity.size -ne [uint64]$entry.Length) {
                        throw "JOBFLOW_RUNTIME_ARCHIVE_OUTPUT_INVALID"
                    }
                    $targetStreams.Add($readLock)
                    $readLock = $null
                }
                finally { if ($null -ne $readLock) { $readLock.Dispose() } }
            }

            $result = Test-RuntimeRoot $tempRoot -PermitUnattested:$PermitUnattested
            $expectedPrefix = "JobFlow-v$([string]$result.application_version)-windows-x64/"
            if ($prefix -cne $expectedPrefix) { throw "JOBFLOW_RUNTIME_ARCHIVE_PREFIX_INVALID" }
            if (
                [long]$preflight.PayloadFiles -ne [long]$result.file_count -or
                [long]$preflight.PayloadBytes -ne [long]$result.total_bytes
            ) { throw "JOBFLOW_RUNTIME_INVENTORY_MISMATCH" }
            Close-DirectoryLockSet $directoryLocks -Verify
            $directoryLocks = @()
            $locksVerified = $true
        }
        finally { $zip.Dispose() }
        $after = Get-OpenIdentity $archiveStream "JOBFLOW_RUNTIME_ARCHIVE_IDENTITY_INVALID"
        if (-not (Test-SameIdentity $identity $after)) { throw "JOBFLOW_RUNTIME_ARCHIVE_CHANGED" }
        return $result
    }
    finally {
        foreach ($target in @($targetStreams)) { $target.Dispose() }
        if ($directoryLocks.Count -gt 0) {
            if ($locksVerified) { Close-DirectoryLockSet $directoryLocks -Verify }
            else { Close-DirectoryLockSet $directoryLocks }
        }
        $archiveStream.Dispose()
        Remove-VerificationTemp $tempRoot
    }
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$result = if (-not [string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    Test-RuntimeRoot $RuntimeRoot -PermitUnattested:$AllowUnattested
}
else {
    Test-RuntimeArchive $ArchivePath -PermitUnattested:$AllowUnattested
}
$result | ConvertTo-Json -Depth 5 -Compress
