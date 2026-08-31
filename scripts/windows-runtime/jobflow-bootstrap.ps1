[CmdletBinding()]
param(
    [string]$ManifestPath,
    [string]$SignaturePath,
    [string]$ArchivePath,
    [switch]$DescribeManifest,
    [switch]$RecoverOnly,
    [switch]$VerifyInstalled,
    [switch]$Rollback,
    [switch]$StartNewRollback,
    [switch]$ExpandArchive,
    [switch]$Activate
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

# Resolve the public management contract before creating a compiler temporary
# directory, loading interop, opening any caller path, or touching the installed
# layout.  The no-switch compatibility form is intentionally only an alias for
# -DescribeManifest; every archive or mixed-mode ambiguity fails closed.
$selectedMode = $null
$modeSwitchCount = @(
    $DescribeManifest.IsPresent,
    $RecoverOnly.IsPresent,
    $VerifyInstalled.IsPresent,
    $Rollback.IsPresent,
    $ExpandArchive.IsPresent,
    $Activate.IsPresent
).Where({ $_ }).Count
$manifestBound = $PSBoundParameters.ContainsKey("ManifestPath")
$signatureBound = $PSBoundParameters.ContainsKey("SignaturePath")
$archiveBound = $PSBoundParameters.ContainsKey("ArchivePath")
$hasManifest = $manifestBound -and -not [string]::IsNullOrWhiteSpace($ManifestPath)
$hasSignature = $signatureBound -and -not [string]::IsNullOrWhiteSpace($SignaturePath)
$hasArchive = $archiveBound -and -not [string]::IsNullOrWhiteSpace($ArchivePath)
if ($modeSwitchCount -gt 1) {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
if ($StartNewRollback.IsPresent -and -not $Rollback.IsPresent) {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
if ($RecoverOnly.IsPresent) {
    if ($manifestBound -or $signatureBound -or $archiveBound) {
        [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
        exit 1
    }
    $selectedMode = "RecoverOnly"
}
elseif ($VerifyInstalled.IsPresent) {
    if ($manifestBound -or $signatureBound -or $archiveBound) {
        [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
        exit 1
    }
    $selectedMode = "VerifyInstalled"
}
elseif ($Rollback.IsPresent) {
    if ($manifestBound -or $signatureBound -or $archiveBound) {
        [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
        exit 1
    }
    $selectedMode = "Rollback"
}
elseif ($DescribeManifest.IsPresent -or $modeSwitchCount -eq 0) {
    if (-not $hasManifest -or -not $hasSignature -or $archiveBound) {
        [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
        exit 1
    }
    $selectedMode = "DescribeManifest"
}
elseif ($ExpandArchive.IsPresent) {
    if (-not $hasManifest -or -not $hasSignature -or -not $hasArchive) {
        [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
        exit 1
    }
    $selectedMode = "ExpandArchive"
}
elseif ($Activate.IsPresent) {
    if (-not $hasManifest -or -not $hasSignature -or -not $hasArchive) {
        [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
        exit 1
    }
    $selectedMode = "Activate"
}
else {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
trap {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}

# These values are the immutable JobFlow production release trust root.  They
# deliberately are not read from the installed tree, an update archive, or a
# caller-controlled configuration file.
$trustedAlgorithm = "RSA-PKCS1-v1_5-SHA256"
$trustedKeyId = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
$trustedModulusBase64Url = "4_GvTbc3dTuLSvzARhbG2Msy6mTvLnN5nINaBcSjAiEI986j44U1YxtmkAQ7ZQooPaA5s_xzJvFn5ZlYuExeaZy5L2om2LMfMljz7IOfFeEcz5wOcO8Rokd-zVK8fKFh4xAi4DkGoYxle1vpCiNdr09QeYH4o123GNCAKOfYjNW1WlHKh-9aRnlvrvt2JrsJni--JPLVmoThCeKUdH1ic1rojRR761L6U5AXRfYC46rp952HMr8xt7U_w_M0XukoJLuUtHa1UbGYZZIaU0lRstcpQiwIWtgub0K8Pnnf_l52kc02S2TlrFhGQko32pSOQPifMHiNy6Fg5n8I4F9IGl0MiHFh1fdiKCDzM_m5_bqhFUIIgMULF3BJTPYT41gqXZ_BRELH1g08Q41DAAIzpdDO2iOXvVVizPjvlqThNabz9enDt_uVoEPaTW1VfDV3rswbzfLaO0dTsbtlHxhLLe66u1XhOmnb0ELha6f9iOyijlgSNPwptc7YIpzN8G-d"
$trustedExponentBase64Url = "AQAB"
$bootstrapVersion = "0.7.0"
$supportedUpdaterVersion = "0.7.0"
$maximumManifestBytes = 64 * 1024
$maximumSignatureBytes = 16 * 1024
$maximumArchiveBytes = [long]1610612736
$maximumClosureManifestBytes = 16 * 1024 * 1024
$maximumRuntimeFileCount = 100000
$maximumExtractedTreeEntries = (2 * $maximumRuntimeFileCount) + 66
$maximumExtractedTreeBytes = [long]$maximumArchiveBytes + [long]$maximumClosureManifestBytes
$maximumLegacyV1Files = 4096
$maximumLegacyV1Directories = 1024
$maximumLegacyV1Entries = 8192
$maximumLegacyV1FileBytes = 67108864
$maximumLegacyV1TreeBytes = 268435456
$maximumLegacyV1RelativePathChars = 512
$maximumLegacyV1Depth = 32
$maximumActivationStateBytes = 256 * 1024
$maximumActivationEvidenceBytes = 64 * 1024
$maximumActivationTrustEntries = 8
$maximumActivationTrustBytes = 128 * 1024
$maximumRuntimeHealthTemporaryEntries = 64
$maximumRuntimeHealthTemporaryBytes = 16 * 1024 * 1024

# PowerShell 5.1 may start its framework compiler while loading the fixed
# interop helper below.  Remove caller-controlled process variables first so
# that no credentials, tokens, language-runtime hooks, or command overrides can
# cross that process boundary.  Only a bounded writable temporary directory and
# trusted Windows roots are retained.
$trustedSystemDirectory = [Environment]::SystemDirectory
$trustedWindowsRoot = [IO.Path]::GetDirectoryName($trustedSystemDirectory)
$trustedLocalDataRoot = [IO.Path]::GetFullPath(
    [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
)
if ([string]::IsNullOrWhiteSpace($trustedLocalDataRoot) -or
    -not [IO.Directory]::Exists($trustedLocalDataRoot)) {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
$trustedLocalDataDriveRoot = [IO.Path]::GetPathRoot($trustedLocalDataRoot)
try { $trustedLocalDataDrive = New-Object IO.DriveInfo($trustedLocalDataDriveRoot) }
catch {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
if ($trustedLocalDataDriveRoot -cnotmatch '^[A-Za-z]:\\$' -or
    $trustedLocalDataDrive.DriveType -notin @([IO.DriveType]::Fixed, [IO.DriveType]::Removable)) {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
function Assert-ExistingLocalDirectoryChain([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    if ($root -cnotmatch '^[A-Za-z]:\\$' -or -not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $current = $root
    foreach ($part in $full.Substring($root.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        if ([string]::IsNullOrEmpty($part)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        $current = [IO.Path]::Combine($current, $part)
        $attributes = [IO.File]::GetAttributes($current)
        if (($attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device)) -ne 0 -or
            ($attributes -band [IO.FileAttributes]::Directory) -eq 0) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
    return $full
}
try { Assert-ExistingLocalDirectoryChain $trustedLocalDataRoot | Out-Null }
catch {
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
$trustedTemporaryRoot = [IO.Path]::Combine(
    $trustedLocalDataRoot,
    "JobFlowBootstrap-" + [Guid]::NewGuid().ToString("N")
)
try {
    [IO.Directory]::CreateDirectory($trustedTemporaryRoot) | Out-Null
    Assert-ExistingLocalDirectoryChain $trustedTemporaryRoot | Out-Null
}
catch {
    if ([IO.Directory]::Exists($trustedTemporaryRoot)) {
        try { [IO.Directory]::Delete($trustedTemporaryRoot, $false) } catch { }
    }
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
$allowedEnvironment = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
foreach ($name in @("SystemRoot", "WinDir", "TEMP", "TMP")) {
    [void]$allowedEnvironment.Add($name)
}
foreach ($entry in @([Environment]::GetEnvironmentVariables().Keys)) {
    if (-not $allowedEnvironment.Contains([string]$entry)) {
        [Environment]::SetEnvironmentVariable([string]$entry, $null, "Process")
    }
}
[Environment]::SetEnvironmentVariable("SystemRoot", $trustedWindowsRoot, "Process")
[Environment]::SetEnvironmentVariable("WinDir", $trustedWindowsRoot, "Process")
[Environment]::SetEnvironmentVariable("TEMP", $trustedTemporaryRoot, "Process")
[Environment]::SetEnvironmentVariable("TMP", $trustedTemporaryRoot, "Process")

$interopLoaded = $false
try {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

public static class JobFlowBootstrapFiles
{
    private const uint FileTypeDisk = 0x0001;
    private const int ErrorHandleEof = 38;
    private static readonly IntPtr InvalidHandleValue = new IntPtr(-1);

    [StructLayout(LayoutKind.Sequential)]
    private struct ByHandleFileInformation
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

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct Win32FindStreamData
    {
        internal long StreamSize;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 296)]
        internal string StreamName;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle fileHandle,
        out ByHandleFileInformation fileInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint GetFileType(SafeFileHandle fileHandle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle fileHandle,
        System.Text.StringBuilder filePath,
        uint filePathLength,
        uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr FindFirstStreamW(
        string fileName,
        int informationLevel,
        out Win32FindStreamData findStreamData,
        uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool FindNextStreamW(
        IntPtr findStream,
        out Win32FindStreamData findStreamData);

    [DllImport("kernel32.dll")]
    private static extern bool FindClose(IntPtr findHandle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateDirectoryW(string path, IntPtr securityAttributes);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern uint GetDriveTypeW(string rootPathName);

    private static void Fail()
    {
        throw new InvalidDataException("JOBFLOW_BOOTSTRAP_INPUT_REJECTED");
    }

    private static string NormalizeHandlePath(string value)
    {
        if (value.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
            return @"\\" + value.Substring(8);
        if (value.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase))
            return value.Substring(4);
        return value;
    }

    private static ByHandleFileInformation InspectHandle(
        FileStream stream,
        string expectedPath,
        long maximumBytes,
        bool allowEmpty)
    {
        if (GetFileType(stream.SafeFileHandle) != FileTypeDisk)
            Fail();
        ByHandleFileInformation information;
        if (!GetFileInformationByHandle(stream.SafeFileHandle, out information))
            Fail();
        FileAttributes attributes = (FileAttributes)information.FileAttributes;
        if ((attributes & (FileAttributes.Directory | FileAttributes.Device | FileAttributes.ReparsePoint)) != 0)
            Fail();
        if (information.NumberOfLinks != 1)
            Fail();
        ulong length = ((ulong)information.FileSizeHigh << 32) | information.FileSizeLow;
        if ((!allowEmpty && length == 0) || length > (ulong)maximumBytes || length != (ulong)stream.Length)
            Fail();

        var finalPath = new System.Text.StringBuilder(32768);
        uint finalLength = GetFinalPathNameByHandle(
            stream.SafeFileHandle,
            finalPath,
            (uint)finalPath.Capacity,
            0);
        if (finalLength == 0 || finalLength >= finalPath.Capacity)
            Fail();
        string normalizedFinal = Path.GetFullPath(NormalizeHandlePath(finalPath.ToString()));
        if (!string.Equals(expectedPath, normalizedFinal, StringComparison.OrdinalIgnoreCase))
            Fail();
        return information;
    }

    private static void InspectStreams(string path)
    {
        Win32FindStreamData streamData;
        IntPtr streamHandle = FindFirstStreamW(path, 0, out streamData, 0);
        if (streamHandle == InvalidHandleValue)
            Fail();
        try
        {
            int count = 0;
            do
            {
                count++;
                if (count != 1 || !string.Equals(streamData.StreamName, "::$DATA", StringComparison.Ordinal))
                    Fail();
            }
            while (FindNextStreamW(streamHandle, out streamData));
            int error = Marshal.GetLastWin32Error();
            if (error != ErrorHandleEof || count != 1)
                Fail();
        }
        finally
        {
            FindClose(streamHandle);
        }
    }

    private static void AssertLocalPathWithoutReparse(string fullPath, bool finalDirectory)
    {
        if (String.IsNullOrEmpty(fullPath) || fullPath.StartsWith(@"\\", StringComparison.Ordinal))
            Fail();
        string root = Path.GetPathRoot(fullPath);
        if (root == null || root.Length != 3 || !Char.IsLetter(root[0]) ||
            root[1] != ':' || root[2] != Path.DirectorySeparatorChar)
            Fail();
        uint driveType = GetDriveTypeW(root);
        if (driveType != 2U && driveType != 3U)
            Fail();

        string remainder = fullPath.Substring(root.Length);
        if (remainder.Length == 0 || remainder.IndexOf(':') >= 0)
            Fail();
        string[] components = remainder.Split(Path.DirectorySeparatorChar);
        string current = root;
        for (int index = 0; index < components.Length; index++)
        {
            if (components[index].Length == 0)
                Fail();
            current = Path.Combine(current, components[index]);
            FileAttributes attributes = File.GetAttributes(current);
            if ((attributes & (FileAttributes.Device | FileAttributes.ReparsePoint)) != 0)
                Fail();
            bool directory = (attributes & FileAttributes.Directory) != 0;
            if (index < components.Length - 1 && !directory)
                Fail();
            if (index == components.Length - 1 && directory != finalDirectory)
                Fail();
        }
    }

    public static string AssertLocalDirectoryPathWithoutReparse(string path)
    {
        string full;
        try { full = Path.GetFullPath(path); }
        catch { Fail(); return null; }
        AssertLocalPathWithoutReparse(full, true);
        return full;
    }

    public sealed class LockedRegularFile : IDisposable
    {
        private readonly ByHandleFileInformation before;
        private readonly long maximumBytes;
        private readonly bool allowEmpty;
        private bool disposed;
        public FileStream Stream { get; private set; }
        public string FullPath { get; private set; }
        public long Length { get { return Stream.Length; } }

        internal LockedRegularFile(string path, long maximum, bool allowZeroLength)
        {
            if (String.IsNullOrWhiteSpace(path) || maximum < 1)
                Fail();
            try { FullPath = Path.GetFullPath(path); }
            catch { Fail(); }
            AssertLocalPathWithoutReparse(FullPath, false);
            try
            {
                Stream = new FileStream(
                    FullPath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.Read,
                    65536,
                    FileOptions.SequentialScan);
                maximumBytes = maximum;
                allowEmpty = allowZeroLength;
                before = InspectHandle(Stream, FullPath, maximumBytes, allowEmpty);
                InspectStreams(FullPath);
            }
            catch
            {
                if (Stream != null) Stream.Dispose();
                throw;
            }
        }

        public void VerifyUnchanged()
        {
            if (disposed) Fail();
            ByHandleFileInformation after = InspectHandle(Stream, FullPath, maximumBytes, allowEmpty);
            if (before.VolumeSerialNumber != after.VolumeSerialNumber ||
                before.FileIndexHigh != after.FileIndexHigh ||
                before.FileIndexLow != after.FileIndexLow ||
                before.FileSizeHigh != after.FileSizeHigh ||
                before.FileSizeLow != after.FileSizeLow)
                Fail();
            InspectStreams(FullPath);
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            if (Stream != null) Stream.Dispose();
        }
    }

    public static LockedRegularFile OpenLockedRegularFile(string path, long maximumBytes)
    {
        return new LockedRegularFile(path, maximumBytes, false);
    }

    public static LockedRegularFile OpenLockedRegularFile(string path, long maximumBytes, bool allowEmpty)
    {
        return new LockedRegularFile(path, maximumBytes, allowEmpty);
    }

    public static FileStream OpenExclusiveLockFile(string path)
    {
        string full;
        try { full = Path.GetFullPath(path); }
        catch { Fail(); return null; }
        string parent = Path.GetDirectoryName(full);
        if (String.IsNullOrEmpty(parent)) Fail();
        AssertLocalPathWithoutReparse(parent, true);
        FileStream stream = null;
        try
        {
            stream = new FileStream(
                full,
                FileMode.OpenOrCreate,
                FileAccess.ReadWrite,
                FileShare.None,
                1,
                FileOptions.WriteThrough);
            if (stream.Length == 0)
            {
                stream.SetLength(1);
                stream.Flush(true);
            }
            InspectHandle(stream, full, 1, false);
            InspectStreams(full);
            return stream;
        }
        catch
        {
            if (stream != null) stream.Dispose();
            throw;
        }
    }

    public static FileStream OpenExistingExclusiveLockFile(string path)
    {
        string full;
        try { full = Path.GetFullPath(path); }
        catch { Fail(); return null; }
        string parent = Path.GetDirectoryName(full);
        if (String.IsNullOrEmpty(parent)) Fail();
        AssertLocalPathWithoutReparse(parent, true);
        if (!File.Exists(full) || Directory.Exists(full)) Fail();
        FileStream stream = null;
        try
        {
            stream = new FileStream(
                full,
                FileMode.Open,
                FileAccess.ReadWrite,
                FileShare.None,
                1,
                FileOptions.WriteThrough);
            InspectHandle(stream, full, 1, false);
            InspectStreams(full);
            return stream;
        }
        catch
        {
            if (stream != null) stream.Dispose();
            throw;
        }
    }

    public static void ReplaceFileWithoutBackup(string source, string destination)
    {
        if (String.IsNullOrWhiteSpace(source) || String.IsNullOrWhiteSpace(destination))
            Fail();
        string sourceFull;
        string destinationFull;
        try
        {
            sourceFull = Path.GetFullPath(source);
            destinationFull = Path.GetFullPath(destination);
        }
        catch { Fail(); return; }
        AssertLocalPathWithoutReparse(sourceFull, false);
        AssertLocalPathWithoutReparse(destinationFull, false);
        File.Replace(sourceFull, destinationFull, null, true);
    }

    public static void CreateNewDirectory(string path)
    {
        string full;
        try { full = Path.GetFullPath(path); }
        catch { Fail(); return; }
        if (!CreateDirectoryW(full, IntPtr.Zero)) Fail();
        FileAttributes attributes = File.GetAttributes(full);
        if ((attributes & FileAttributes.Directory) == 0 ||
            (attributes & FileAttributes.ReparsePoint) != 0) Fail();
    }

    public static byte[] ReadBoundedRegularFile(string path, int maximumBytes)
    {
        using (var locked = OpenLockedRegularFile(path, maximumBytes))
        {
            FileStream stream = locked.Stream;
            int length = checked((int)locked.Length);
            byte[] value = new byte[length];
            int offset = 0;
            while (offset < value.Length)
            {
                int read = stream.Read(value, offset, value.Length - offset);
                if (read <= 0)
                    Fail();
                offset += read;
            }
            if (stream.ReadByte() != -1)
                Fail();
            locked.VerifyUnchanged();
            return value;
        }
    }

    private static void InspectDeleteTreeNoFollowCore(
        string path,
        ref int entries,
        ref long bytes,
        int maximumEntries,
        long maximumBytes)
    {
        entries = checked(entries + 1);
        if (entries > maximumEntries) Fail();
        FileAttributes attributes = File.GetAttributes(path);
        bool directory = (attributes & FileAttributes.Directory) != 0;
        bool reparse = (attributes & FileAttributes.ReparsePoint) != 0;
        if (reparse) return;
        if (!directory)
        {
            bytes = checked(bytes + new FileInfo(path).Length);
            if (bytes > maximumBytes) Fail();
            return;
        }
        foreach (string child in Directory.GetFileSystemEntries(path))
            InspectDeleteTreeNoFollowCore(child, ref entries, ref bytes, maximumEntries, maximumBytes);
    }

    private static void DeleteTreeNoFollowCore(string path)
    {
        FileAttributes attributes = File.GetAttributes(path);
        bool directory = (attributes & FileAttributes.Directory) != 0;
        bool reparse = (attributes & FileAttributes.ReparsePoint) != 0;
        if (reparse)
        {
            if (directory) Directory.Delete(path, false);
            else File.Delete(path);
            return;
        }
        if (!directory)
        {
            File.Delete(path);
            return;
        }
        foreach (string child in Directory.GetFileSystemEntries(path))
            DeleteTreeNoFollowCore(child);
        Directory.Delete(path, false);
    }

    public static void DeleteDirectoryTreeNoFollow(string path)
    {
        DeleteDirectoryTreeNoFollowBounded(path, 100000, 1610612736L);
    }

    public static void DeleteDirectoryTreeNoFollowBounded(string path, int maximumEntries, long maximumBytes)
    {
        string full;
        try { full = Path.GetFullPath(path); }
        catch { Fail(); return; }
        if (maximumEntries < 1 || maximumBytes < 0 || !Directory.Exists(full)) Fail();
        AssertLocalPathWithoutReparse(full, true);
        int entries = 0;
        long bytes = 0;
        InspectDeleteTreeNoFollowCore(full, ref entries, ref bytes, maximumEntries, maximumBytes);
        DeleteTreeNoFollowCore(full);
    }
}

public sealed class JobFlowBootstrapZipEntry
{
    public string Name { get; internal set; }
    public bool IsDirectory { get; internal set; }
    public long CompressedSize { get; internal set; }
    public long Length { get; internal set; }
    public long LocalOffset { get; internal set; }
}

public static class JobFlowBootstrapZip
{
    private const uint CentralSignature = 0x02014b50;
    private const uint LocalSignature = 0x04034b50;
    private const uint EndSignature = 0x06054b50;
    private const int MaximumEntries = 100000;

    private static void Fail() { throw new InvalidDataException("JOBFLOW_BOOTSTRAP_INPUT_REJECTED"); }
    private static ushort U16(byte[] b, int o) { return (ushort)(b[o] | (b[o + 1] << 8)); }
    private static uint U32(byte[] b, int o) { return (uint)(b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)); }

    private static byte[] ReadExact(FileStream stream, int count)
    {
        byte[] value = new byte[count];
        int offset = 0;
        while (offset < count)
        {
            int read = stream.Read(value, offset, count - offset);
            if (read <= 0) Fail();
            offset += read;
        }
        return value;
    }

    private static string DecodeName(byte[] raw)
    {
        if (raw.Length == 0) Fail();
        for (int index = 0; index < raw.Length; index++)
            if (raw[index] < 0x20 || raw[index] > 0x7e) Fail();
        return Encoding.ASCII.GetString(raw);
    }

    private static bool ReservedSegment(string segment)
    {
        string stem = segment.Split('.')[0];
        if (stem.Equals("CON", StringComparison.OrdinalIgnoreCase) ||
            stem.Equals("PRN", StringComparison.OrdinalIgnoreCase) ||
            stem.Equals("AUX", StringComparison.OrdinalIgnoreCase) ||
            stem.Equals("NUL", StringComparison.OrdinalIgnoreCase) ||
            stem.Equals("CONIN$", StringComparison.OrdinalIgnoreCase) ||
            stem.Equals("CONOUT$", StringComparison.OrdinalIgnoreCase) ||
            stem.Equals("CLOCK$", StringComparison.OrdinalIgnoreCase)) return true;
        if (stem.Length == 4 &&
            (stem.StartsWith("COM", StringComparison.OrdinalIgnoreCase) ||
             stem.StartsWith("LPT", StringComparison.OrdinalIgnoreCase)) &&
            stem[3] >= '1' && stem[3] <= '9') return true;
        return false;
    }

    private static bool ValidateName(string name, string prefix)
    {
        if (!name.StartsWith(prefix, StringComparison.Ordinal) || name.IndexOf('\\') >= 0 ||
            name.StartsWith("/", StringComparison.Ordinal) || name.IndexOf(':') >= 0 ||
            name.IndexOfAny(new char[] { '<', '>', '"', '|', '?', '*' }) >= 0) Fail();
        bool directory = name.EndsWith("/", StringComparison.Ordinal);
        string relative = name.Substring(prefix.Length);
        if (relative.Length == 0) return true;
        if (name.Length > 1024 || relative.Length > 768) Fail();
        string body = directory ? relative.Substring(0, relative.Length - 1) : relative;
        if (body.Length == 0) Fail();
        string[] parts = body.Split('/');
        foreach (string part in parts)
        {
            if (part.Length == 0 || part.Length > 255 || part == "." || part == ".." ||
                part.EndsWith(".", StringComparison.Ordinal) ||
                part.EndsWith(" ", StringComparison.Ordinal) || ReservedSegment(part)) Fail();
        }
        return directory;
    }

    public static JobFlowBootstrapZipEntry[] Preflight(
        FileStream stream,
        string expectedPrefix,
        long expectedPayloadBytes,
        int expectedPayloadFiles,
        int maximumClosureBytes)
    {
        if (stream == null || !stream.CanRead || !stream.CanSeek ||
            String.IsNullOrEmpty(expectedPrefix) || !expectedPrefix.EndsWith("/", StringComparison.Ordinal) ||
            expectedPayloadBytes < 1 || expectedPayloadFiles < 1 || maximumClosureBytes < 1) Fail();
        long length = stream.Length;
        int tailLength = checked((int)Math.Min(length, 65557L));
        stream.Position = length - tailLength;
        byte[] tail = ReadExact(stream, tailLength);
        int eocd = -1;
        for (int index = tail.Length - 22; index >= 0; index--)
        {
            if (U32(tail, index) == EndSignature && index + 22 + U16(tail, index + 20) == tail.Length)
            { eocd = index; break; }
        }
        if (eocd < 0 || U16(tail, eocd + 20) != 0 || U16(tail, eocd + 4) != 0 ||
            U16(tail, eocd + 6) != 0 || U16(tail, eocd + 8) != U16(tail, eocd + 10)) Fail();
        int entryCount = U16(tail, eocd + 10);
        uint centralSize = U32(tail, eocd + 12);
        uint centralOffset = U32(tail, eocd + 16);
        long eocdAbsolute = length - tailLength + eocd;
        if (entryCount < 1 || entryCount > MaximumEntries || entryCount > expectedPayloadFiles * 2L + 64L ||
            centralOffset == UInt32.MaxValue || centralSize == UInt32.MaxValue ||
            (long)centralOffset + centralSize != eocdAbsolute) Fail();

        var entries = new List<JobFlowBootstrapZipEntry>(entryCount);
        var aliases = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var exact = new HashSet<string>(StringComparer.Ordinal);
        var localOffsets = new HashSet<long>();
        var localRanges = new List<long[]>();
        var files = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long payloadBytes = 0;
        int payloadFiles = 0;
        long closureBytes = -1;
        stream.Position = centralOffset;
        for (int item = 0; item < entryCount; item++)
        {
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
            bool directory = ValidateName(name, expectedPrefix);
            // Complete-runtime archives have a canonical file-only layout.
            // Reject directory records so all independent verifiers consume
            // exactly the same signed ZIP grammar.
            if (directory) Fail();
            if (!exact.Add(name) || !aliases.Add(name) || !localOffsets.Add(localOffset)) Fail();
            string relativeTreeName = name.Substring(expectedPrefix.Length).TrimEnd('/');
            if (relativeTreeName.Length > 0)
            {
                string[] treeParts = relativeTreeName.Split('/');
                string parent = "";
                for (int part = 0; part < treeParts.Length - 1; part++)
                {
                    parent = parent.Length == 0 ? treeParts[part] : parent + "/" + treeParts[part];
                    if (files.Contains(parent)) Fail();
                    directories.Add(parent);
                }
                if (directory)
                {
                    if (files.Contains(relativeTreeName)) Fail();
                    directories.Add(relativeTreeName);
                }
                else
                {
                    if (directories.Contains(relativeTreeName) || !files.Add(relativeTreeName)) Fail();
                }
            }
            uint unixType = (external >> 16) & 0xF000;
            uint dos = external & 0xFFFF;
            if ((dos & (0x40U | 0x400U | 0x4000U)) != 0) Fail();
            if (directory)
            {
                if ((unixType != 0 && unixType != 0x4000) || (dos & 0x10U) == 0 ||
                    method != 0 || crc != 0 || compressed != 0 || uncompressed != 0) Fail();
            }
            else
            {
                if ((unixType != 0 && unixType != 0x8000) || (dos & 0x10U) != 0 ||
                    uncompressed > 536870912U || (uncompressed > 0 && compressed == 0) ||
                    (method == 0 && compressed != uncompressed)) Fail();
                if (uncompressed > 1048576U && ((double)uncompressed / compressed) > 200.0) Fail();
                if (name == expectedPrefix + "runtime-closure.json")
                {
                    if (closureBytes >= 0 || uncompressed < 2 || uncompressed > maximumClosureBytes) Fail();
                    closureBytes = uncompressed;
                }
                else
                {
                    payloadFiles++;
                    payloadBytes = checked(payloadBytes + uncompressed);
                }
            }
            entries.Add(new JobFlowBootstrapZipEntry {
                Name = name, IsDirectory = directory, CompressedSize = compressed,
                Length = uncompressed, LocalOffset = localOffset
            });

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
            if (!String.Equals(localName, name, StringComparison.Ordinal) ||
                localEnd > centralOffset || localEnd <= localStart) Fail();
            foreach (long[] range in localRanges)
                if (localStart < range[1] && localEnd > range[0]) Fail();
            localRanges.Add(new long[] { localStart, localEnd });
            stream.Position = saved;
        }
        if (stream.Position != (long)centralOffset + centralSize || closureBytes < 0 ||
            payloadFiles != expectedPayloadFiles || payloadBytes != expectedPayloadBytes ||
            directories.Count > expectedPayloadFiles + 64L) Fail();
        localRanges.Sort(delegate(long[] left, long[] right) { return left[0].CompareTo(right[0]); });
        long covered = 0;
        foreach (long[] range in localRanges)
        {
            if (range[0] != covered) Fail();
            covered = range[1];
        }
        if (covered != centralOffset) Fail();
        stream.Position = 0;
        return entries.ToArray();
    }
}

public sealed class JobFlowRuntimeHealthResult
{
    public int ExitCode { get; internal set; }
    public bool TimedOut { get; internal set; }
    public bool OutputOverflow { get; internal set; }
    public byte[] StandardOutput { get; internal set; }
    public byte[] StandardError { get; internal set; }
}

public static class JobFlowRuntimeHealthRunner
{
    private const uint CreateSuspended = 0x00000004;
    private const uint CreateNoWindow = 0x08000000;
    private const uint CreateUnicodeEnvironment = 0x00000400;
    private const uint ExtendedStartupInfoPresent = 0x00080000;
    private const uint StartfUseStdHandles = 0x00000100;
    private const uint HandleFlagInherit = 0x00000001;
    private const uint WaitObject0 = 0x00000000;
    private const uint WaitTimeout = 0x00000102;
    private const uint Infinite = 0xFFFFFFFF;
    private const uint JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectLimitProcessTime = 0x00000002;
    private const uint JobObjectLimitActiveProcess = 0x00000008;
    private const uint JobObjectLimitProcessMemory = 0x00000100;
    private const uint JobObjectLimitKillOnJobClose = 0x00002000;
    private const uint GenericRead = 0x80000000;
    private const uint GenericWrite = 0x40000000;
    private const uint FileShareRead = 0x00000001;
    private const uint FileShareWrite = 0x00000002;
    private const uint OpenExisting = 3;
    private const int OutputLimit = 8192;
    private const int WallClockMilliseconds = 30000;
    private static readonly IntPtr InvalidHandleValue = new IntPtr(-1);
    private static readonly IntPtr ProcThreadAttributeHandleList = new IntPtr(0x00020002);

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes
    {
        internal int Length;
        internal IntPtr SecurityDescriptor;
        internal int InheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StartupInfo
    {
        internal int cb;
        internal string reserved;
        internal string desktop;
        internal string title;
        internal uint x;
        internal uint y;
        internal uint xSize;
        internal uint ySize;
        internal uint xCountChars;
        internal uint yCountChars;
        internal uint fillAttribute;
        internal uint flags;
        internal ushort showWindow;
        internal ushort reserved2;
        internal IntPtr reserved2Pointer;
        internal IntPtr standardInput;
        internal IntPtr standardOutput;
        internal IntPtr standardError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct StartupInfoEx
    {
        internal StartupInfo StartupInfo;
        internal IntPtr AttributeList;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInformation
    {
        internal IntPtr Process;
        internal IntPtr Thread;
        internal uint ProcessId;
        internal uint ThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        internal ulong ReadOperationCount;
        internal ulong WriteOperationCount;
        internal ulong OtherOperationCount;
        internal ulong ReadTransferCount;
        internal ulong WriteTransferCount;
        internal ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectBasicLimitInformation
    {
        internal long PerProcessUserTimeLimit;
        internal long PerJobUserTimeLimit;
        internal uint LimitFlags;
        internal UIntPtr MinimumWorkingSetSize;
        internal UIntPtr MaximumWorkingSetSize;
        internal uint ActiveProcessLimit;
        internal UIntPtr Affinity;
        internal uint PriorityClass;
        internal uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JobObjectExtendedLimitInformationValue
    {
        internal JobObjectBasicLimitInformation BasicLimitInformation;
        internal IoCounters IoInfo;
        internal UIntPtr ProcessMemoryLimit;
        internal UIntPtr JobMemoryLimit;
        internal UIntPtr PeakProcessMemoryUsed;
        internal UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CreatePipe(
        out IntPtr readPipe,
        out IntPtr writePipe,
        ref SecurityAttributes pipeAttributes,
        uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFileW(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        ref SecurityAttributes securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObjectW(IntPtr jobAttributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        uint informationClass,
        ref JobObjectExtendedLimitInformationValue information,
        uint informationLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool InitializeProcThreadAttributeList(
        IntPtr attributeList,
        int attributeCount,
        int flags,
        ref IntPtr size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool UpdateProcThreadAttribute(
        IntPtr attributeList,
        uint flags,
        IntPtr attribute,
        IntPtr value,
        IntPtr size,
        IntPtr previousValue,
        IntPtr returnSize);

    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr attributeList);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcessW(
        string applicationName,
        StringBuilder commandLine,
        IntPtr processAttributes,
        IntPtr threadAttributes,
        bool inheritHandles,
        uint creationFlags,
        IntPtr environment,
        string currentDirectory,
        ref StartupInfoEx startupInfo,
        out ProcessInformation processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, uint exitCode);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    private sealed class BoundedPipeReader
    {
        private readonly FileStream stream;
        private readonly MemoryStream captured = new MemoryStream();
        private readonly Thread thread;
        private volatile bool overflow;
        private Exception failure;

        internal BoundedPipeReader(IntPtr handle)
        {
            stream = new FileStream(new SafeFileHandle(handle, true), FileAccess.Read, 4096, false);
            thread = new Thread(ReadAll);
            thread.IsBackground = true;
        }

        internal void Start() { thread.Start(); }

        private void ReadAll()
        {
            try
            {
                byte[] buffer = new byte[4096];
                int read;
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    int remaining = OutputLimit - checked((int)captured.Length);
                    if (remaining > 0)
                        captured.Write(buffer, 0, Math.Min(read, remaining));
                    if (read > remaining)
                        overflow = true;
                }
            }
            catch (Exception error) { failure = error; }
            finally { stream.Dispose(); }
        }

        internal bool Overflow { get { return overflow; } }

        internal byte[] Finish()
        {
            if (!thread.Join(5000) || failure != null)
                throw new InvalidDataException("JOBFLOW_BOOTSTRAP_INPUT_REJECTED");
            return captured.ToArray();
        }
    }

    private static void Fail()
    {
        throw new InvalidDataException("JOBFLOW_BOOTSTRAP_INPUT_REJECTED");
    }

    private static void CloseIfValid(ref IntPtr handle)
    {
        if (handle != IntPtr.Zero && handle != InvalidHandleValue)
        {
            CloseHandle(handle);
            handle = IntPtr.Zero;
        }
    }

    private static string AssertValue(string value)
    {
        if (String.IsNullOrEmpty(value) || value.IndexOf('\0') >= 0)
            Fail();
        return value;
    }

    private static byte[] BuildEnvironmentBlock(
        string systemRoot,
        string winDir,
        string temporaryRoot,
        string localApplicationData)
    {
        string[] entries = new string[] {
            "LOCALAPPDATA=" + AssertValue(localApplicationData),
            "SystemRoot=" + AssertValue(systemRoot),
            "TEMP=" + AssertValue(temporaryRoot),
            "TMP=" + AssertValue(temporaryRoot),
            "WinDir=" + AssertValue(winDir)
        };
        string block = String.Join("\0", entries) + "\0\0";
        return Encoding.Unicode.GetBytes(block);
    }

    public static JobFlowRuntimeHealthResult Run(
        string executable,
        string workingDirectory,
        string systemRoot,
        string winDir,
        string temporaryRoot,
        string localApplicationData)
    {
        executable = Path.GetFullPath(AssertValue(executable));
        workingDirectory = Path.GetFullPath(AssertValue(workingDirectory));
        temporaryRoot = Path.GetFullPath(AssertValue(temporaryRoot));
        if (executable.IndexOf('"') >= 0)
            Fail();

        SecurityAttributes inheritable = new SecurityAttributes();
        inheritable.Length = Marshal.SizeOf(typeof(SecurityAttributes));
        inheritable.InheritHandle = 1;
        IntPtr stdoutRead = IntPtr.Zero;
        IntPtr stdoutWrite = IntPtr.Zero;
        IntPtr stderrRead = IntPtr.Zero;
        IntPtr stderrWrite = IntPtr.Zero;
        IntPtr nullInput = IntPtr.Zero;
        IntPtr job = IntPtr.Zero;
        IntPtr attributes = IntPtr.Zero;
        IntPtr handleList = IntPtr.Zero;
        IntPtr environment = IntPtr.Zero;
        ProcessInformation process = new ProcessInformation();
        BoundedPipeReader outputReader = null;
        BoundedPipeReader errorReader = null;
        bool assigned = false;
        bool timedOut = false;
        try
        {
            if (!CreatePipe(out stdoutRead, out stdoutWrite, ref inheritable, 0) ||
                !SetHandleInformation(stdoutRead, HandleFlagInherit, 0) ||
                !CreatePipe(out stderrRead, out stderrWrite, ref inheritable, 0) ||
                !SetHandleInformation(stderrRead, HandleFlagInherit, 0))
                Fail();
            nullInput = CreateFileW(
                "NUL", GenericRead | GenericWrite, FileShareRead | FileShareWrite,
                ref inheritable, OpenExisting, 0, IntPtr.Zero);
            if (nullInput == InvalidHandleValue)
                Fail();

            job = CreateJobObjectW(IntPtr.Zero, null);
            if (job == IntPtr.Zero)
                Fail();
            JobObjectExtendedLimitInformationValue limits = new JobObjectExtendedLimitInformationValue();
            limits.BasicLimitInformation.PerProcessUserTimeLimit = 20L * 10000000L;
            limits.BasicLimitInformation.ActiveProcessLimit = 1;
            limits.BasicLimitInformation.LimitFlags =
                JobObjectLimitProcessTime | JobObjectLimitActiveProcess |
                JobObjectLimitProcessMemory | JobObjectLimitKillOnJobClose;
            limits.ProcessMemoryLimit = new UIntPtr(512UL * 1024UL * 1024UL);
            if (!SetInformationJobObject(
                job, JobObjectExtendedLimitInformation, ref limits,
                (uint)Marshal.SizeOf(typeof(JobObjectExtendedLimitInformationValue))))
                Fail();

            IntPtr attributeBytes = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref attributeBytes);
            if (attributeBytes == IntPtr.Zero)
                Fail();
            attributes = Marshal.AllocHGlobal(attributeBytes);
            if (!InitializeProcThreadAttributeList(attributes, 1, 0, ref attributeBytes))
                Fail();
            handleList = Marshal.AllocHGlobal(IntPtr.Size * 3);
            Marshal.WriteIntPtr(handleList, 0, nullInput);
            Marshal.WriteIntPtr(handleList, IntPtr.Size, stdoutWrite);
            Marshal.WriteIntPtr(handleList, IntPtr.Size * 2, stderrWrite);
            if (!UpdateProcThreadAttribute(
                attributes, 0, ProcThreadAttributeHandleList, handleList,
                new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero))
                Fail();

            StartupInfoEx startup = new StartupInfoEx();
            startup.StartupInfo.cb = Marshal.SizeOf(typeof(StartupInfoEx));
            startup.StartupInfo.flags = StartfUseStdHandles;
            startup.StartupInfo.standardInput = nullInput;
            startup.StartupInfo.standardOutput = stdoutWrite;
            startup.StartupInfo.standardError = stderrWrite;
            startup.AttributeList = attributes;

            byte[] environmentBytes = BuildEnvironmentBlock(
                systemRoot, winDir, temporaryRoot, localApplicationData);
            try
            {
                environment = Marshal.AllocHGlobal(environmentBytes.Length);
                Marshal.Copy(environmentBytes, 0, environment, environmentBytes.Length);
            }
            finally { Array.Clear(environmentBytes, 0, environmentBytes.Length); }

            StringBuilder command = new StringBuilder(
                "\"" + executable + "\" -I -B -X utf8 -m jobops.runtime_health");
            uint flags = CreateSuspended | CreateNoWindow | CreateUnicodeEnvironment | ExtendedStartupInfoPresent;
            if (!CreateProcessW(
                executable, command, IntPtr.Zero, IntPtr.Zero, true, flags,
                environment, workingDirectory, ref startup, out process))
                Fail();
            if (!AssignProcessToJobObject(job, process.Process))
            {
                TerminateProcess(process.Process, 0xE1);
                Fail();
            }
            assigned = true;

            CloseIfValid(ref nullInput);
            CloseIfValid(ref stdoutWrite);
            CloseIfValid(ref stderrWrite);
            outputReader = new BoundedPipeReader(stdoutRead);
            stdoutRead = IntPtr.Zero;
            errorReader = new BoundedPipeReader(stderrRead);
            stderrRead = IntPtr.Zero;
            outputReader.Start();
            errorReader.Start();
            if (ResumeThread(process.Thread) == Infinite)
                Fail();

            DateTime deadline = DateTime.UtcNow.AddMilliseconds(WallClockMilliseconds);
            while (true)
            {
                uint wait = WaitForSingleObject(process.Process, 50);
                if (wait == WaitObject0)
                    break;
                if (wait != WaitTimeout)
                    Fail();
                if (outputReader.Overflow || errorReader.Overflow || DateTime.UtcNow >= deadline)
                {
                    timedOut = DateTime.UtcNow >= deadline;
                    if (!TerminateJobObject(job, timedOut ? 0xE2U : 0xE3U))
                        Fail();
                    if (WaitForSingleObject(process.Process, 5000) != WaitObject0)
                        Fail();
                    break;
                }
            }
            uint exitCode;
            if (!GetExitCodeProcess(process.Process, out exitCode))
                Fail();
            CloseIfValid(ref job);
            byte[] standardOutput = outputReader.Finish();
            byte[] standardError = errorReader.Finish();
            return new JobFlowRuntimeHealthResult {
                ExitCode = unchecked((int)exitCode),
                TimedOut = timedOut,
                OutputOverflow = outputReader.Overflow || errorReader.Overflow,
                StandardOutput = standardOutput,
                StandardError = standardError
            };
        }
        catch
        {
            if (assigned && job != IntPtr.Zero)
                TerminateJobObject(job, 0xE4);
            else if (process.Process != IntPtr.Zero)
                TerminateProcess(process.Process, 0xE4);
            throw;
        }
        finally
        {
            CloseIfValid(ref stdoutRead);
            CloseIfValid(ref stdoutWrite);
            CloseIfValid(ref stderrRead);
            CloseIfValid(ref stderrWrite);
            CloseIfValid(ref nullInput);
            CloseIfValid(ref process.Thread);
            CloseIfValid(ref process.Process);
            CloseIfValid(ref job);
            if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
            if (handleList != IntPtr.Zero) Marshal.FreeHGlobal(handleList);
            if (attributes != IntPtr.Zero)
            {
                DeleteProcThreadAttributeList(attributes);
                Marshal.FreeHGlobal(attributes);
            }
        }
    }
}

public static class JobFlowBootstrapJson
{
    private sealed class Parser
    {
        private readonly string text;
        private int index;
        internal Parser(string value) { text = value; }
        private void Fail() { throw new InvalidDataException("JOBFLOW_BOOTSTRAP_INPUT_REJECTED"); }
        private void White() { while (index < text.Length && (text[index] == ' ' || text[index] == '\t' || text[index] == '\r' || text[index] == '\n')) index++; }
        private bool Take(char c) { White(); if (index < text.Length && text[index] == c) { index++; return true; } return false; }
        private void Literal(string value) { if (index + value.Length > text.Length || String.CompareOrdinal(text, index, value, 0, value.Length) != 0) Fail(); index += value.Length; }
        private int Hex(char c) { if (c >= '0' && c <= '9') return c - '0'; if (c >= 'a' && c <= 'f') return c - 'a' + 10; if (c >= 'A' && c <= 'F') return c - 'A' + 10; Fail(); return 0; }
        private string StringValue()
        {
            White(); if (index >= text.Length || text[index++] != '"') Fail();
            var value = new StringBuilder();
            while (index < text.Length)
            {
                char c = text[index++];
                if (c == '"') return value.ToString();
                if (c < 0x20) Fail();
                if (c != '\\') { value.Append(c); continue; }
                if (index >= text.Length) Fail();
                char e = text[index++];
                if (e == '"' || e == '\\' || e == '/') value.Append(e);
                else if (e == 'b') value.Append('\b'); else if (e == 'f') value.Append('\f');
                else if (e == 'n') value.Append('\n'); else if (e == 'r') value.Append('\r'); else if (e == 't') value.Append('\t');
                else if (e == 'u')
                {
                    if (index + 4 > text.Length) Fail(); int code = 0;
                    for (int n = 0; n < 4; n++) code = (code << 4) | Hex(text[index++]);
                    char first = (char)code;
                    if (Char.IsHighSurrogate(first))
                    {
                        if (index + 6 > text.Length || text[index++] != '\\' || text[index++] != 'u') Fail();
                        int lowCode = 0; for (int n = 0; n < 4; n++) lowCode = (lowCode << 4) | Hex(text[index++]);
                        char low = (char)lowCode; if (!Char.IsLowSurrogate(low)) Fail(); value.Append(first); value.Append(low);
                    }
                    else { if (Char.IsLowSurrogate(first)) Fail(); value.Append(first); }
                }
                else Fail();
            }
            Fail(); return null;
        }
        private void Number()
        {
            if (index < text.Length && text[index] == '-') index++;
            if (index >= text.Length) Fail();
            if (text[index] == '0') index++;
            else { if (text[index] < '1' || text[index] > '9') Fail(); while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++; }
            if (index < text.Length && text[index] == '.') { index++; int start = index; while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++; if (index == start) Fail(); }
            if (index < text.Length && (text[index] == 'e' || text[index] == 'E')) { index++; if (index < text.Length && (text[index] == '+' || text[index] == '-')) index++; int start = index; while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++; if (index == start) Fail(); }
        }
        private void Value(int depth)
        {
            if (depth > 64) Fail(); White(); if (index >= text.Length) Fail(); char c = text[index];
            if (c == '{') ObjectValue(depth + 1); else if (c == '[') ArrayValue(depth + 1); else if (c == '"') StringValue();
            else if (c == 't') Literal("true"); else if (c == 'f') Literal("false"); else if (c == 'n') Literal("null");
            else if (c == '-' || (c >= '0' && c <= '9')) Number(); else Fail();
        }
        private void ObjectValue(int depth)
        {
            if (!Take('{')) Fail(); var names = new HashSet<string>(StringComparer.Ordinal); White(); if (Take('}')) return;
            while (true) { string name = StringValue(); if (!names.Add(name) || !Take(':')) Fail(); Value(depth); if (Take('}')) return; if (!Take(',')) Fail(); }
        }
        private void ArrayValue(int depth)
        {
            if (!Take('[')) Fail(); White(); if (Take(']')) return;
            while (true) { Value(depth); if (Take(']')) return; if (!Take(',')) Fail(); }
        }
        internal void Parse() { Value(0); White(); if (index != text.Length) Fail(); }
    }
    public static void AssertNoDuplicateProperties(string value)
    {
        if (value == null) throw new InvalidDataException("JOBFLOW_BOOTSTRAP_INPUT_REJECTED");
        new Parser(value).Parse();
    }
}
'@
    $interopLoaded = $true
}
finally {
    if ([IO.Directory]::Exists($trustedTemporaryRoot)) {
        try {
            if ($interopLoaded) {
                [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollow($trustedTemporaryRoot)
            }
            else {
                $enumerator = [IO.Directory]::EnumerateFileSystemEntries($trustedTemporaryRoot).GetEnumerator()
                try { if ($enumerator.MoveNext()) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" } }
                finally { if ($enumerator -is [IDisposable]) { $enumerator.Dispose() } }
                [IO.Directory]::Delete($trustedTemporaryRoot, $false)
            }
        }
        catch {
            [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
            exit 1
        }
    }
}

function ConvertFrom-Base64UrlStrict([string]$Value, [int]$Minimum, [int]$Maximum) {
    if ([string]::IsNullOrEmpty($Value) -or $Value -notmatch '^[A-Za-z0-9_-]+$') {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $remainder = $Value.Length % 4
    if ($remainder -eq 1) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $padded = $Value.Replace('-', '+').Replace('_', '/')
    if ($remainder -gt 0) { $padded += ('=' * (4 - $remainder)) }
    try { $bytes = [Convert]::FromBase64String($padded) }
    catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if ($bytes.Length -lt $Minimum -or $bytes.Length -gt $Maximum) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return $bytes
}

function Test-JsonInteger([object]$Value, [long]$Minimum, [long]$Maximum) {
    return (($Value -is [int] -or $Value -is [long]) -and
        [long]$Value -ge $Minimum -and [long]$Value -le $Maximum)
}

function Assert-ExactProperties([object]$Value, [string[]]$Expected) {
    if ($null -eq $Value -or $Value -isnot [PSCustomObject]) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $actual = @($Value.PSObject.Properties | ForEach-Object { $_.Name })
    if ($actual.Count -ne $Expected.Count) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $set = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    foreach ($name in $Expected) { [void]$set.Add($name) }
    foreach ($name in $actual) {
        if (-not $set.Remove([string]$name)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
    if ($set.Count -ne 0) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
}

function Assert-Sha256([object]$Value) {
    if ($Value -isnot [string] -or [string]$Value -cnotmatch '^sha256:[0-9a-f]{64}$') {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Assert-LegacySha256([object]$Value) {
    if ($Value -isnot [string] -or [string]$Value -cnotmatch '^[0-9a-f]{64}$') {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function ConvertTo-SemVerParts([object]$Value) {
    if ($Value -isnot [string] -or [string]$Value -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $parts = @()
    foreach ($part in ([string]$Value).Split('.')) {
        try { $number = [uint32]::Parse($part, [Globalization.CultureInfo]::InvariantCulture) }
        catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        $parts += [uint64]$number
    }
    return $parts
}

function Compare-SemVer([object]$Left, [object]$Right) {
    $leftParts = @(ConvertTo-SemVerParts $Left)
    $rightParts = @(ConvertTo-SemVerParts $Right)
    for ($index = 0; $index -lt 3; $index++) {
        if ($leftParts[$index] -lt $rightParts[$index]) { return -1 }
        if ($leftParts[$index] -gt $rightParts[$index]) { return 1 }
    }
    return 0
}

function Assert-EmbeddedCompatibility([object]$Manifest) {
    if ((Compare-SemVer $bootstrapVersion $Manifest.policy.minimum_bootstrap_version) -lt 0 -or
        (Compare-SemVer $supportedUpdaterVersion $Manifest.policy.minimum_updater_version) -lt 0) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $raw = $hasher.ComputeHash($Bytes) }
    finally { $hasher.Dispose() }
    return "sha256:" + (-join ($raw | ForEach-Object { $_.ToString("x2") }))
}

function Get-StreamSha256([IO.Stream]$Stream) {
    $Stream.Position = 0
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $raw = $hasher.ComputeHash($Stream) }
    finally { $hasher.Dispose(); $Stream.Position = 0 }
    return "sha256:" + (-join ($raw | ForEach-Object { $_.ToString("x2") }))
}

function ConvertTo-CanonicalJson([object]$Value) {
    if ($null -eq $Value) { return "null" }
    if ($Value -is [bool]) { if ($Value) { return "true" } else { return "false" } }
    if ($Value -is [string]) { return (ConvertTo-Json ([string]$Value) -Compress) }
    if ($Value -is [int] -or $Value -is [long]) {
        return ([long]$Value).ToString([Globalization.CultureInfo]::InvariantCulture)
    }
    if ($Value -is [PSCustomObject]) {
        $properties = @($Value.PSObject.Properties | Sort-Object -Property Name -CaseSensitive)
        $members = foreach ($property in $properties) {
            (ConvertTo-Json ([string]$property.Name) -Compress) + ":" + (ConvertTo-CanonicalJson $property.Value)
        }
        return "{" + [string]::Join(",", [string[]]$members) + "}"
    }
    if ($Value -is [Collections.IEnumerable]) {
        $items = foreach ($item in $Value) { ConvertTo-CanonicalJson $item }
        return "[" + [string]::Join(",", [string[]]$items) + "]"
    }
    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
}

function Get-CanonicalJsonSha256([object]$Value) {
    return Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-CanonicalJson $Value)))
}

function Assert-ApplicationWheelProvenance(
    [object]$Provenance,
    [object]$ApplicationWheelSha256,
    [object]$SourceCommit,
    [object]$BuildLockSha256
) {
    if ($Provenance -isnot [PSCustomObject]) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    Assert-ExactProperties $Provenance @(
        "format", "source_commit", "source_git_tree_oid", "source_build_tree_sha256",
        "source_archive_sha256", "build_lock_sha256", "build_recipe_sha256",
        "pass_a_wheel_sha256", "pass_b_wheel_sha256", "reproducible"
    )
    Assert-Sha256 $ApplicationWheelSha256
    foreach ($name in @(
        "source_build_tree_sha256", "source_archive_sha256", "build_lock_sha256",
        "build_recipe_sha256", "pass_a_wheel_sha256", "pass_b_wheel_sha256"
    )) { Assert-Sha256 $Provenance.$name }
    if ([string]$Provenance.format -cne "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1" -or
        [string]$SourceCommit -cnotmatch '^[0-9a-f]{40}$' -or
        [string]$Provenance.source_commit -cne [string]$SourceCommit -or
        [string]$Provenance.source_git_tree_oid -cnotmatch '^[0-9a-f]{40}$' -or
        $Provenance.reproducible -isnot [bool] -or -not [bool]$Provenance.reproducible -or
        [string]$Provenance.pass_a_wheel_sha256 -cne [string]$ApplicationWheelSha256 -or
        [string]$Provenance.pass_b_wheel_sha256 -cne [string]$ApplicationWheelSha256) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    if ($null -ne $BuildLockSha256) {
        Assert-Sha256 $BuildLockSha256
        if ([string]$Provenance.build_lock_sha256 -cne [string]$BuildLockSha256) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
}

function Assert-ArchiveManifestShape([object]$Manifest) {
    $expectedManifestProperties = @(
        "schema_version", "product", "channel", "release", "predecessor", "asset",
        "runtime_closure", "publisher_attestation", "policy", "issued_at_utc"
    )
    if ($null -ne $Manifest.PSObject.Properties["legacy_v1_predecessors"]) {
        $expectedManifestProperties += "legacy_v1_predecessors"
    }
    Assert-ExactProperties $Manifest $expectedManifestProperties
    if (-not (Test-JsonInteger $Manifest.schema_version 2 2) -or
        [string]$Manifest.product -cne "JobFlow" -or [string]$Manifest.channel -cne "stable") {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-ExactProperties $Manifest.release @("version", "source_commit", "platform")
    if ([string]$Manifest.release.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$Manifest.release.source_commit -cnotmatch '^[0-9a-f]{40}$' -or
        [string]$Manifest.release.platform -cne "windows-x64") { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $version = [string]$Manifest.release.version

    Assert-ExactProperties $Manifest.predecessor @(
        "minimum_version", "maximum_version_exclusive", "disallow_downgrade", "require_current_runtime_closure"
    )
    if ([string]$Manifest.predecessor.minimum_version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$Manifest.predecessor.maximum_version_exclusive -cne $version -or
        $Manifest.predecessor.disallow_downgrade -isnot [bool] -or -not [bool]$Manifest.predecessor.disallow_downgrade -or
        $Manifest.predecessor.require_current_runtime_closure -isnot [bool] -or -not [bool]$Manifest.predecessor.require_current_runtime_closure) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }

    if ($null -ne $Manifest.PSObject.Properties["legacy_v1_predecessors"]) {
        if ($Manifest.legacy_v1_predecessors -is [string] -or
            $Manifest.legacy_v1_predecessors -isnot [Collections.IEnumerable]) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $legacyIdentities = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        $legacyDirectories = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        $previousIdentity = $null
        $legacyCount = 0
        foreach ($legacy in @($Manifest.legacy_v1_predecessors)) {
            $legacyCount++
            if ($legacyCount -gt 64 -or $legacy -isnot [PSCustomObject]) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            Assert-ExactProperties $legacy @("schema_version", "version", "source_sha256", "version_directory")
            if (-not (Test-JsonInteger $legacy.schema_version 1 1) -or
                [string]$legacy.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            Assert-LegacySha256 $legacy.source_sha256
            $expectedLegacyDirectory = "v$([string]$legacy.version)-$(([string]$legacy.source_sha256).Substring(0, 12))"
            if ([string]$legacy.version_directory -cne $expectedLegacyDirectory -or
                (Compare-SemVer $legacy.version $version) -ge 0) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            $identity = ([string]$legacy.version) + "|" + ([string]$legacy.source_sha256) + "|" + ([string]$legacy.version_directory)
            if (-not $legacyIdentities.Add($identity) -or
                -not $legacyDirectories.Add([string]$legacy.version_directory)) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            if ($null -ne $previousIdentity) {
                $versionComparison = Compare-SemVer $previousIdentity.version $legacy.version
                if ($versionComparison -gt 0 -or
                    ($versionComparison -eq 0 -and
                        [string]::CompareOrdinal([string]$previousIdentity.source_sha256, [string]$legacy.source_sha256) -ge 0)) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
            }
            $previousIdentity = $legacy
        }
        if ($legacyCount -lt 1) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }

    Assert-ExactProperties $Manifest.asset @("name", "bytes", "sha256", "archive_prefix")
    Assert-Sha256 $Manifest.asset.sha256
    if ([string]$Manifest.asset.name -cne "JobFlow-v$version-windows-x64-complete.zip" -or
        [string]$Manifest.asset.archive_prefix -cne "JobFlow-v$version-windows-x64/" -or
        -not (Test-JsonInteger $Manifest.asset.bytes 1 $maximumArchiveBytes)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }

    Assert-ExactProperties $Manifest.runtime_closure @(
        "manifest_sha256", "tree_sha256", "structural_status", "source_commit", "source_payload_sha256",
        "file_count", "total_bytes", "python_version", "platform", "build_inputs"
    )
    foreach ($name in @("manifest_sha256", "tree_sha256", "source_payload_sha256")) {
        Assert-Sha256 $Manifest.runtime_closure.$name
    }
    if ([string]$Manifest.runtime_closure.structural_status -cne "BUILT_UNATTESTED" -or
        [string]$Manifest.runtime_closure.source_commit -cne [string]$Manifest.release.source_commit -or
        [string]$Manifest.runtime_closure.source_payload_sha256 -cne [string]$Manifest.asset.sha256 -or
        [string]$Manifest.runtime_closure.python_version -cne "3.13.15" -or
        [string]$Manifest.runtime_closure.platform -cne "windows-x64" -or
        -not (Test-JsonInteger $Manifest.runtime_closure.file_count 1 $maximumRuntimeFileCount) -or
        -not (Test-JsonInteger $Manifest.runtime_closure.total_bytes 1 $maximumArchiveBytes)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-ExactProperties $Manifest.runtime_closure.build_inputs @(
        "python_artifact_sha256", "wheel_lock_sha256", "wheelhouse_tree_sha256",
        "application_wheel_sha256", "application_wheel_provenance", "builder_toolchain_sha256", "wheel_count"
    )
    foreach ($name in @("python_artifact_sha256", "wheel_lock_sha256", "wheelhouse_tree_sha256", "application_wheel_sha256", "builder_toolchain_sha256")) {
        Assert-Sha256 $Manifest.runtime_closure.build_inputs.$name
    }
    if (-not (Test-JsonInteger $Manifest.runtime_closure.build_inputs.wheel_count 0 10000)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-ApplicationWheelProvenance `
        $Manifest.runtime_closure.build_inputs.application_wheel_provenance `
        $Manifest.runtime_closure.build_inputs.application_wheel_sha256 `
        $Manifest.release.source_commit $null

    Assert-ExactProperties $Manifest.policy @(
        "minimum_updater_version", "minimum_bootstrap_version", "required_structural_status",
        "publisher_attestation_required", "final_submit_user_only", "automatic_retry_submission_unknown",
        "external_actions_during_update"
    )
    if ([string]$Manifest.policy.minimum_updater_version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$Manifest.policy.minimum_bootstrap_version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$Manifest.policy.required_structural_status -cne "BUILT_UNATTESTED" -or
        $Manifest.policy.publisher_attestation_required -isnot [bool] -or -not [bool]$Manifest.policy.publisher_attestation_required -or
        $Manifest.policy.final_submit_user_only -isnot [bool] -or -not [bool]$Manifest.policy.final_submit_user_only -or
        $Manifest.policy.automatic_retry_submission_unknown -isnot [bool] -or [bool]$Manifest.policy.automatic_retry_submission_unknown -or
        -not (Test-JsonInteger $Manifest.policy.external_actions_during_update 0 0)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }

    Assert-ExactProperties $Manifest.publisher_attestation @(
        "status", "format", "release_key_id", "evidence_format", "runtime_build_evidence_sha256",
        "publisher_evidence_sha256", "evidence_expires_at_utc", "signer_readiness_challenge_sha256",
        "runtime_closure_manifest_sha256", "runtime_tree_sha256", "build_inputs_sha256", "source_commit",
        "source_payload_sha256", "file_count", "total_bytes", "policy_sha256", "issued_at_utc"
    )
    foreach ($name in @(
        "runtime_build_evidence_sha256", "publisher_evidence_sha256", "signer_readiness_challenge_sha256",
        "runtime_closure_manifest_sha256", "runtime_tree_sha256", "build_inputs_sha256",
        "source_payload_sha256", "policy_sha256"
    )) {
        Assert-Sha256 $Manifest.publisher_attestation.$name
    }
    if ([string]$Manifest.publisher_attestation.status -cne "ATTESTED" -or
        [string]$Manifest.publisher_attestation.format -cne "JOBFLOW_PUBLISHER_ATTESTATION_V2" -or
        [string]$Manifest.publisher_attestation.evidence_format -cne "JOBFLOW_PUBLISHER_EVIDENCE_V1" -or
        [string]$Manifest.publisher_attestation.release_key_id -cne $trustedKeyId -or
        [string]$Manifest.publisher_attestation.runtime_closure_manifest_sha256 -cne [string]$Manifest.runtime_closure.manifest_sha256 -or
        [string]$Manifest.publisher_attestation.runtime_tree_sha256 -cne [string]$Manifest.runtime_closure.tree_sha256 -or
        [string]$Manifest.publisher_attestation.build_inputs_sha256 -cne (Get-CanonicalJsonSha256 $Manifest.runtime_closure.build_inputs) -or
        [string]$Manifest.publisher_attestation.source_commit -cne [string]$Manifest.runtime_closure.source_commit -or
        [string]$Manifest.publisher_attestation.source_payload_sha256 -cne [string]$Manifest.runtime_closure.source_payload_sha256 -or
        -not (Test-JsonInteger $Manifest.publisher_attestation.file_count 1 $maximumRuntimeFileCount) -or
        [long]$Manifest.publisher_attestation.file_count -ne [long]$Manifest.runtime_closure.file_count -or
        -not (Test-JsonInteger $Manifest.publisher_attestation.total_bytes 1 $maximumArchiveBytes) -or
        [long]$Manifest.publisher_attestation.total_bytes -ne [long]$Manifest.runtime_closure.total_bytes -or
        [string]$Manifest.publisher_attestation.policy_sha256 -cne (Get-CanonicalJsonSha256 $Manifest.policy)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    try {
        if ([string]$Manifest.publisher_attestation.issued_at_utc -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$' -or
            [string]$Manifest.publisher_attestation.evidence_expires_at_utc -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$' -or
            [string]$Manifest.issued_at_utc -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$') {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $attestedAt = [DateTimeOffset]::Parse([string]$Manifest.publisher_attestation.issued_at_utc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
        $evidenceExpiresAt = [DateTimeOffset]::Parse([string]$Manifest.publisher_attestation.evidence_expires_at_utc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
        $issuedAt = [DateTimeOffset]::Parse([string]$Manifest.issued_at_utc, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
    }
    catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if ($attestedAt -gt $issuedAt -or $issuedAt -ge $evidenceExpiresAt) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Assert-EmbeddedSignedManifestEvidence(
    [byte[]]$ManifestBytes,
    [byte[]]$SignatureEnvelopeBytes
) {
    $evidenceModulus = $null
    $evidenceExponent = $null
    $evidenceSignature = $null
    try {
        if ($null -eq $ManifestBytes -or $ManifestBytes.Length -lt 1 -or
            $ManifestBytes.Length -gt $maximumManifestBytes -or
            $null -eq $SignatureEnvelopeBytes -or $SignatureEnvelopeBytes.Length -lt 1 -or
            $SignatureEnvelopeBytes.Length -gt $maximumSignatureBytes) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $strict = New-Object Text.UTF8Encoding($false, $true)
        $envelopeText = $strict.GetString($SignatureEnvelopeBytes)
        $pattern = '^\{"algorithm":"(?<algorithm>[A-Za-z0-9_-]+)","key_id":"(?<key>sha256:[0-9a-f]{64})","schema_version":1,"signature_b64url":"(?<signature>[A-Za-z0-9_-]+)"\}$'
        $match = [Text.RegularExpressions.Regex]::Match(
            $envelopeText,
            $pattern,
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        if (-not $match.Success -or
            $match.Groups["algorithm"].Value -cne $trustedAlgorithm -or
            $match.Groups["key"].Value -cne $trustedKeyId) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $evidenceModulus = ConvertFrom-Base64UrlStrict $trustedModulusBase64Url 256 512
        $evidenceExponent = ConvertFrom-Base64UrlStrict $trustedExponentBase64Url 1 8
        $evidenceSignature = ConvertFrom-Base64UrlStrict $match.Groups["signature"].Value 256 512
        if ($evidenceSignature.Length -ne $evidenceModulus.Length) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $parameters = New-Object Security.Cryptography.RSAParameters
        $parameters.Modulus = $evidenceModulus
        $parameters.Exponent = $evidenceExponent
        $rsa = New-Object Security.Cryptography.RSACng
        try {
            $rsa.ImportParameters($parameters)
            if (-not $rsa.VerifyData(
                $ManifestBytes,
                $evidenceSignature,
                [Security.Cryptography.HashAlgorithmName]::SHA256,
                [Security.Cryptography.RSASignaturePadding]::Pkcs1
            )) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        }
        finally { $rsa.Dispose() }

        $evidenceManifestText = $strict.GetString($ManifestBytes)
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($evidenceManifestText)
        $manifest = $evidenceManifestText | ConvertFrom-Json
        if ($null -eq $manifest -or $manifest -isnot [PSCustomObject]) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $schema = $manifest.PSObject.Properties["schema_version"]
        if ($null -eq $schema -or
            -not ($schema.Value -is [int] -or $schema.Value -is [long]) -or
            [long]$schema.Value -ne 2) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        Assert-ArchiveManifestShape $manifest
        Assert-EmbeddedCompatibility $manifest
        if ([string]$manifest.publisher_attestation.release_key_id -cne $trustedKeyId) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        return [pscustomobject]@{
            manifest = $manifest
            manifest_sha256 = Get-BytesSha256 $ManifestBytes
            signature_envelope_sha256 = Get-BytesSha256 $SignatureEnvelopeBytes
            release_key_id = $trustedKeyId
        }
    }
    catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    finally {
        if ($null -ne $evidenceModulus) { [Array]::Clear($evidenceModulus, 0, $evidenceModulus.Length) }
        if ($null -ne $evidenceExponent) { [Array]::Clear($evidenceExponent, 0, $evidenceExponent.Length) }
        if ($null -ne $evidenceSignature) { [Array]::Clear($evidenceSignature, 0, $evidenceSignature.Length) }
    }
}

function Assert-RuntimeClosureShape([object]$Closure, [object]$SignedManifest) {
    Assert-ExactProperties $Closure @(
        "schema_version", "status", "artifact_type", "platform", "application_version", "source_commit",
        "python", "build_inputs", "layout", "file_count", "total_bytes", "tree_sha256", "files",
        "offline_smoke_tests", "protected_builder"
    )
    if (-not (Test-JsonInteger $Closure.schema_version 1 1) -or [string]$Closure.status -cne "BUILT_UNATTESTED" -or
        [string]$Closure.artifact_type -cne "complete-runtime" -or [string]$Closure.platform -cne "windows-x64" -or
        [string]$Closure.application_version -cne [string]$SignedManifest.release.version -or
        [string]$Closure.source_commit -cne [string]$SignedManifest.release.source_commit -or
        -not (Test-JsonInteger $Closure.file_count 1 $maximumRuntimeFileCount) -or
        [long]$Closure.file_count -ne [long]$SignedManifest.runtime_closure.file_count -or
        -not (Test-JsonInteger $Closure.total_bytes 1 $maximumArchiveBytes) -or
        [long]$Closure.total_bytes -ne [long]$SignedManifest.runtime_closure.total_bytes -or
        [string]$Closure.tree_sha256 -cne [string]$SignedManifest.runtime_closure.tree_sha256) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-Sha256 $Closure.tree_sha256
    Assert-ExactProperties $Closure.python @("version", "artifact_name", "artifact_sha256", "sigstore_identity", "sigstore_verified")
    Assert-Sha256 $Closure.python.artifact_sha256
    if ([string]$Closure.python.version -cne [string]$SignedManifest.runtime_closure.python_version -or
        [string]$Closure.python.artifact_name -cne "python-3.13.15-embed-amd64.zip" -or
        [string]$Closure.python.artifact_sha256 -cne [string]$SignedManifest.runtime_closure.build_inputs.python_artifact_sha256 -or
        [string]::IsNullOrWhiteSpace([string]$Closure.python.sigstore_identity) -or
        $Closure.python.sigstore_verified -isnot [bool] -or [bool]$Closure.python.sigstore_verified) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-ExactProperties $Closure.build_inputs @(
        "wheel_lock_sha256", "wheelhouse_tree_sha256", "application_wheel_sha256",
        "application_wheel_provenance", "builder_toolchain_sha256", "wheels"
    )
    if ($Closure.build_inputs.wheels -isnot [Collections.IList] -or
        $Closure.files -isnot [Collections.IList]) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    foreach ($name in @("wheel_lock_sha256", "wheelhouse_tree_sha256", "application_wheel_sha256", "builder_toolchain_sha256")) {
        Assert-Sha256 $Closure.build_inputs.$name
        if ([string]$Closure.build_inputs.$name -cne [string]$SignedManifest.runtime_closure.build_inputs.$name) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
    Assert-ApplicationWheelProvenance `
        $Closure.build_inputs.application_wheel_provenance `
        $Closure.build_inputs.application_wheel_sha256 $Closure.source_commit $null
    if ((Get-CanonicalJsonSha256 $Closure.build_inputs.application_wheel_provenance) -cne
        (Get-CanonicalJsonSha256 $SignedManifest.runtime_closure.build_inputs.application_wheel_provenance)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $wheels = @($Closure.build_inputs.wheels)
    if ($wheels.Count -ne [long]$SignedManifest.runtime_closure.build_inputs.wheel_count) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $wheelAliases = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($wheel in $wheels) {
        Assert-ExactProperties $wheel @("name", "version", "tag", "size", "sha256")
        Assert-Sha256 $wheel.sha256
        if ([string]$wheel.name -cnotmatch '^[A-Za-z0-9_.-]+$' -or -not $wheelAliases.Add([string]$wheel.name) -or
            [string]$wheel.version -cnotmatch '^[A-Za-z0-9_.+-]+$' -or [string]$wheel.tag -cnotmatch '^[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+$' -or
            -not (Test-JsonInteger $wheel.size 1 $maximumArchiveBytes)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
    Assert-ExactProperties $Closure.layout @("python", "python_pth", "application_root", "module")
    if ([string]$Closure.layout.python -cne "runtime/python.exe" -or [string]$Closure.layout.python_pth -cne "runtime/python313._pth" -or
        [string]$Closure.layout.application_root -cne "app" -or [string]$Closure.layout.module -cne "jobops.cli") {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-ExactProperties $Closure.offline_smoke_tests @("import_passed", "schema_passed", "external_actions")
    if ($Closure.offline_smoke_tests.import_passed -isnot [bool] -or -not [bool]$Closure.offline_smoke_tests.import_passed -or
        $Closure.offline_smoke_tests.schema_passed -isnot [bool] -or -not [bool]$Closure.offline_smoke_tests.schema_passed -or
        -not (Test-JsonInteger $Closure.offline_smoke_tests.external_actions 0 0)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    Assert-ExactProperties $Closure.protected_builder @("evidence_sha256", "deterministic_rebuild_match", "outer_signature_ready")
    Assert-Sha256 $Closure.protected_builder.evidence_sha256
    if ($Closure.protected_builder.deterministic_rebuild_match -isnot [bool] -or
        -not [bool]$Closure.protected_builder.deterministic_rebuild_match -or
        $Closure.protected_builder.outer_signature_ready -isnot [bool] -or
        [bool]$Closure.protected_builder.outer_signature_ready) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
}

function Set-CurrentUserOnlyDirectoryAcl([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity -or $null -eq $identity.User) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $security = New-Object Security.AccessControl.DirectorySecurity
    $security.SetOwner($identity.User)
    $security.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $identity.User,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
    [IO.Directory]::SetAccessControl($Path, $security)
}

function Set-CurrentUserOnlyFileAcl([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity -or $null -eq $identity.User) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $security = New-Object Security.AccessControl.FileSecurity
    $security.SetOwner($identity.User)
    $security.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $identity.User,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
    [IO.File]::SetAccessControl($Path, $security)
}

function Assert-CurrentUserOnlyAcl([string]$Path, [bool]$Directory) {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        if ($null -eq $identity -or $null -eq $identity.User) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $security = if ($Directory) {
            [IO.Directory]::GetAccessControl(
                $Path,
                [Security.AccessControl.AccessControlSections]'Access, Owner'
            )
        }
        else {
            [IO.File]::GetAccessControl(
                $Path,
                [Security.AccessControl.AccessControlSections]'Access, Owner'
            )
        }
        if (-not $security.AreAccessRulesProtected -or
            $security.GetOwner([Security.Principal.SecurityIdentifier]) -ne $identity.User) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $rules = @($security.GetAccessRules(
            $true,
            $true,
            [Security.Principal.SecurityIdentifier]
        ))
        $fullControl = $false
        foreach ($rule in $rules) {
            if ($rule.IsInherited -or
                $rule.IdentityReference -ne $identity.User -or
                $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            if (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq
                [Security.AccessControl.FileSystemRights]::FullControl) {
                $fullControl = $true
            }
        }
        if (-not $fullControl) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
    catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
}

function Assert-SafeDirectory([string]$Path) {
    try { return [JobFlowBootstrapFiles]::AssertLocalDirectoryPathWithoutReparse($Path) }
    catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
}

function New-OrValidateFixedDirectory([string]$Parent, [string]$Leaf) {
    if ([string]::IsNullOrWhiteSpace($Leaf) -or $Leaf -cnotmatch '^[A-Za-z0-9._-]+$') {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $parentFull = (Assert-SafeDirectory $Parent).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($parentFull, $Leaf))
    if ([IO.Path]::GetDirectoryName($path).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $parentFull -or
        [IO.Path]::GetFileName($path) -cne $Leaf -or [IO.File]::Exists($path)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    if (-not [IO.Directory]::Exists($path)) {
        [JobFlowBootstrapFiles]::CreateNewDirectory($path)
        Set-CurrentUserOnlyDirectoryAcl $path
    }
    return Assert-SafeDirectory $path
}

function Read-AndValidateDataMarker([string]$Path) {
    $bytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($Path, 128)
    try {
        $expected = [Text.UTF8Encoding]::new($false).GetBytes('{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}')
        if ($bytes.Length -ne $expected.Length) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        for ($index = 0; $index -lt $bytes.Length; $index++) {
            if ($bytes[$index] -ne $expected[$index]) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        }
    }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Initialize-OrValidateDataRoot([string]$JobOpsRoot) {
    $root = (Assert-SafeDirectory $JobOpsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $dataRoot = [IO.Path]::Combine($root, "Data")
    if ([IO.File]::Exists($dataRoot)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if (-not [IO.Directory]::Exists($dataRoot)) {
        $temporary = [IO.Path]::Combine($root, "Data-init-" + [Guid]::NewGuid().ToString("N") + ".tmp")
        try {
            [JobFlowBootstrapFiles]::CreateNewDirectory($temporary)
            Set-CurrentUserOnlyDirectoryAcl $temporary
            $temporary = Assert-SafeDirectory $temporary
            $temporaryMarker = [IO.Path]::Combine($temporary, ".jobflow-data-root")
            $bytes = [Text.UTF8Encoding]::new($false).GetBytes('{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}')
            try {
                $stream = [IO.File]::Open($temporaryMarker, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
                finally { $stream.Dispose() }
                Set-CurrentUserOnlyFileAcl $temporaryMarker
            }
            finally { [Array]::Clear($bytes, 0, $bytes.Length) }
            Read-AndValidateDataMarker $temporaryMarker
            [void](New-OrValidateFixedDirectory $temporary "state")
            # JOBFLOW_BOOTSTRAP_FRESH_DATA_READY_BOUNDARY
            [IO.Directory]::Move($temporary, $dataRoot)
            $temporary = $null
        }
        catch {
            if (-not [string]::IsNullOrEmpty($temporary) -and [IO.Directory]::Exists($temporary)) {
                [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded($temporary, 4, 128)
            }
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
    $dataRoot = Assert-SafeDirectory $dataRoot
    $marker = [IO.Path]::Combine($dataRoot, ".jobflow-data-root")
    Read-AndValidateDataMarker $marker
    $stateRoot = New-OrValidateFixedDirectory $dataRoot "state"
    return [pscustomobject]@{ data = $dataRoot; state = $stateRoot }
}

function Enter-ActivationMaintenanceLock([string]$StateRoot) {
    $state = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $path = [IO.Path]::Combine($state, ".jobflow-runtime-maintenance.lock")
    if ([IO.Directory]::Exists($path)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $existed = [IO.File]::Exists($path)
    $stream = $null
    try {
        $stream = [JobFlowBootstrapFiles]::OpenExclusiveLockFile($path)
        if (-not $existed) { Set-CurrentUserOnlyFileAcl $path }
        return $stream
    }
    catch {
        if ($null -ne $stream) { $stream.Dispose() }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Enter-ExistingActivationMaintenanceLock([string]$StateRoot) {
    $state = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $path = [IO.Path]::Combine($state, ".jobflow-runtime-maintenance.lock")
    if ([IO.Directory]::Exists($path) -or -not [IO.File]::Exists($path)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED"
    }
    try { return [JobFlowBootstrapFiles]::OpenExistingExclusiveLockFile($path) }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED" }
}

function Enter-LegacyMigrationDiscoveryLock([string]$StateRoot) {
    $state = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $path = [IO.Path]::Combine($state, ".authorized-discovery-task.lock")
    if ([IO.Directory]::Exists($path) -or -not [IO.File]::Exists($path)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    try { return [JobFlowBootstrapFiles]::OpenExistingExclusiveLockFile($path) }
    catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
}

function Enter-ExistingLegacyMigrationDiscoveryLock([string]$StateRoot) {
    $state = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $path = [IO.Path]::Combine($state, ".authorized-discovery-task.lock")
    if ([IO.Directory]::Exists($path) -or -not [IO.File]::Exists($path)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED"
    }
    try { return [JobFlowBootstrapFiles]::OpenExistingExclusiveLockFile($path) }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED" }
}

function Enter-RollbackDiscoveryLock([string]$StateRoot) {
    $state = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $path = [IO.Path]::Combine($state, ".authorized-discovery-task.lock")
    if ([IO.Directory]::Exists($path)) { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
    $existed = [IO.File]::Exists($path)
    $stream = $null
    try {
        $stream = [JobFlowBootstrapFiles]::OpenExclusiveLockFile($path)
        if (-not $existed) { Set-CurrentUserOnlyFileAcl $path }
        return $stream
    }
    catch {
        if ($null -ne $stream) { $stream.Dispose() }
        throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
    }
}

function Enter-BootstrapOperationLock {
    $jobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
    if ([IO.File]::Exists($jobOpsRoot)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if (-not [IO.Directory]::Exists($jobOpsRoot)) {
        [JobFlowBootstrapFiles]::CreateNewDirectory($jobOpsRoot)
        Set-CurrentUserOnlyDirectoryAcl $jobOpsRoot
    }
    $jobOpsRoot = Assert-SafeDirectory $jobOpsRoot
    $path = [IO.Path]::Combine($jobOpsRoot, ".jobflow-bootstrap.lock")
    if ([IO.Directory]::Exists($path)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $existed = [IO.File]::Exists($path)
    $stream = $null
    try {
        $stream = [JobFlowBootstrapFiles]::OpenExclusiveLockFile($path)
        if (-not $existed) { Set-CurrentUserOnlyFileAcl $path }
        return $stream
    }
    catch {
        if ($null -ne $stream) { $stream.Dispose() }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Enter-ExistingBootstrapOperationLock([string]$JobOpsRoot) {
    $root = (Assert-SafeDirectory $JobOpsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $path = [IO.Path]::Combine($root, ".jobflow-bootstrap.lock")
    if ([IO.Directory]::Exists($path) -or -not [IO.File]::Exists($path)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED"
    }
    try { return [JobFlowBootstrapFiles]::OpenExistingExclusiveLockFile($path) }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED" }
}

function Remove-BoundedBootstrapOrphans([string]$LocalDataRoot) {
    $jobOpsRoot = [IO.Path]::Combine($LocalDataRoot, "JobOps")
    if (-not [IO.Directory]::Exists($jobOpsRoot)) { return }
    Assert-SafeDirectory $jobOpsRoot | Out-Null
    $stagingRoot = [IO.Path]::Combine($jobOpsRoot, "BootstrapStagingV2")
    $tokenRoot = [IO.Path]::Combine($jobOpsRoot, "BootstrapStagingTokensV2")
    if ([IO.Directory]::Exists($stagingRoot)) {
        Assert-SafeDirectory $stagingRoot | Out-Null
        $stages = @(Get-ChildItem -LiteralPath $stagingRoot -Force)
        if ($stages.Count -gt 64) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        foreach ($entry in $stages) {
            if (-not $entry.PSIsContainer -or
                $entry.Name -cnotmatch '^stage-[0-9a-f]{32}$' -or
                ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
                $entry.FullName,
                $maximumExtractedTreeEntries,
                $maximumExtractedTreeBytes
            )
        }
        if (@(Get-ChildItem -LiteralPath $stagingRoot -Force).Count -ne 0) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
    if ([IO.Directory]::Exists($tokenRoot)) {
        Assert-SafeDirectory $tokenRoot | Out-Null
        $tokens = @(Get-ChildItem -LiteralPath $tokenRoot -Force)
        if ($tokens.Count -gt 64) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        foreach ($entry in $tokens) {
            if ($entry.PSIsContainer -or $entry.Name -cnotmatch '^[0-9a-f]{32}\.json$' -or
                ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            $locked = [JobFlowBootstrapFiles]::OpenLockedRegularFile($entry.FullName, 65536)
            try { $locked.VerifyUnchanged() }
            finally { $locked.Dispose() }
            [IO.File]::Delete($entry.FullName)
        }
        if (@(Get-ChildItem -LiteralPath $tokenRoot -Force).Count -ne 0) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
}

function New-SecureBootstrapStaging([string]$LocalDataRoot) {
    $jobOpsRoot = [IO.Path]::Combine($LocalDataRoot, "JobOps")
    $stagingRoot = [IO.Path]::Combine($jobOpsRoot, "BootstrapStagingV2")
    foreach ($path in @($jobOpsRoot, $stagingRoot)) {
        if (-not [IO.Directory]::Exists($path)) {
            [IO.Directory]::CreateDirectory($path) | Out-Null
        }
        Assert-SafeDirectory $path | Out-Null
    }
    Set-CurrentUserOnlyDirectoryAcl $stagingRoot
    $createdStage = $null
    try {
        for ($attempt = 0; $attempt -lt 8; $attempt++) {
            $leaf = "stage-" + [Guid]::NewGuid().ToString("N")
            $stage = [IO.Path]::Combine($stagingRoot, $leaf)
            if ([IO.Directory]::Exists($stage) -or [IO.File]::Exists($stage)) { continue }
            [JobFlowBootstrapFiles]::CreateNewDirectory($stage)
            $createdStage = $stage
            Set-CurrentUserOnlyDirectoryAcl $stage
            $verifiedStage = Assert-SafeDirectory $stage
            return [pscustomobject]@{ stage = $verifiedStage; staging_root = $stagingRoot }
        }
    }
    catch {
        if ($null -ne $createdStage -and [IO.Directory]::Exists($createdStage)) {
            [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
                $createdStage,
                $maximumExtractedTreeEntries,
                $maximumExtractedTreeBytes
            )
        }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
}

function Remove-SecureBootstrapStaging([string]$Stage, [string]$StagingRoot) {
    if ([string]::IsNullOrEmpty($Stage) -or [string]::IsNullOrEmpty($StagingRoot)) { return }
    $absolute = [IO.Path]::GetFullPath($Stage)
    $root = [IO.Path]::GetFullPath($StagingRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if ([IO.Path]::GetDirectoryName($absolute).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $root -or
        -not [IO.Path]::GetFileName($absolute).StartsWith("stage-", [StringComparison]::Ordinal)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    if ([IO.Directory]::Exists($absolute)) {
        [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
            $absolute,
            $maximumExtractedTreeEntries,
            $maximumExtractedTreeBytes
        )
    }
}

function Get-ExtractedInventory([string]$Stage, [string]$ClosureRelative) {
    $stageRoot = (Assert-SafeDirectory $Stage).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $paths = New-Object 'Collections.Generic.List[string]'
    $pending = New-Object 'Collections.Generic.Stack[string]'
    $visited = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $pending.Push($stageRoot)
    while ($pending.Count -gt 0) {
        $current = Assert-SafeDirectory $pending.Pop()
        if (-not $visited.Add($current)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        foreach ($entry in @(Get-ChildItem -LiteralPath $current -Force)) {
            $full = [IO.Path]::GetFullPath($entry.FullName)
            if (-not $full.StartsWith($stageRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
                ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            if ($entry.PSIsContainer) {
                $pending.Push($full)
                continue
            }
            if (($entry.Attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::Device -bor [IO.FileAttributes]::Encrypted)) -ne 0) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            $relative = $full.Substring($stageRoot.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/')
            if ($relative -cne $ClosureRelative) { $paths.Add($relative) }
            if ($paths.Count -gt $maximumRuntimeFileCount) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        }
    }
    $records = New-Object 'Collections.Generic.List[object]'
    foreach ($relative in $paths.ToArray()) {
        $full = [IO.Path]::Combine($stageRoot, $relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
        $locked = [JobFlowBootstrapFiles]::OpenLockedRegularFile($full, 536870912, $true)
        try {
            $sha = Get-StreamSha256 $locked.Stream
            $size = [long]$locked.Length
            $locked.VerifyUnchanged()
            $records.Add([pscustomobject]@{ path = $relative; size = $size; sha256 = $sha })
        }
        finally { $locked.Dispose() }
    }
    return $records.ToArray()
}

function Assert-ExtractedRuntimeLockBinding([string]$Root, [object]$Closure) {
    $runtimeLock = Read-StrictPortableJsonRegularFile `
        ([IO.Path]::Combine($Root, "config\windows-cp313-runtime.lock")) 1048576
    if ([string]$runtimeLock.sha256 -cne [string]$Closure.build_inputs.wheel_lock_sha256) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $value = $runtimeLock.value
    Assert-ExactProperties $value @("schema_version", "lock_type", "python_tag", "abi", "platform", "only_binary", "packages")
    if (-not (Test-JsonInteger $value.schema_version 1 1) -or
        [string]$value.lock_type -cne "runtime-wheelhouse" -or
        [string]$value.python_tag -cne "cp313" -or
        [string]$value.abi -cne "cp313-or-abi3" -or
        [string]$value.platform -cne "win_amd64" -or
        $value.only_binary -isnot [bool] -or -not [bool]$value.only_binary -or
        $value.packages -isnot [Collections.IList]) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $packages = @($value.packages)
    $wheels = @($Closure.build_inputs.wheels)
    if ($packages.Count -eq 0 -or $packages.Count -ne $wheels.Count) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $names = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $files = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    for ($index = 0; $index -lt $packages.Count; $index++) {
        $package = $packages[$index]
        $wheel = $wheels[$index]
        Assert-ExactProperties $package @("name", "version", "filename", "size", "sha256")
        $filename = [string]$package.filename
        if ($filename -notmatch '-([^-]+-[^-]+-[^-]+)\.whl$') { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        $tag = [string]$Matches[1]
        Assert-Sha256 $package.sha256
        if ([string]$package.name -cnotmatch '^[A-Za-z0-9_.-]+$' -or
            [string]$package.version -cnotmatch '^[A-Za-z0-9_.+-]+$' -or
            $filename -cnotmatch '^[A-Za-z0-9_.+-]+\.whl$' -or
            -not (Test-JsonInteger $package.size 1 $maximumArchiveBytes) -or
            -not $names.Add([string]$package.name) -or -not $files.Add($filename) -or
            [string]$wheel.name -cne [string]$package.name -or
            [string]$wheel.version -cne [string]$package.version -or
            [string]$wheel.tag -cne $tag -or
            [long]$wheel.size -ne [long]$package.size -or
            [string]$wheel.sha256 -cne [string]$package.sha256) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
    foreach ($forbidden in @("pip", "setuptools", "wheel")) {
        if ($names.Contains($forbidden)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
}

function Assert-ExtractedRuntime([string]$Root, [object]$Closure, [object]$Manifest) {
    Assert-RuntimeClosureShape $Closure $Manifest
    $actual = @(Get-ExtractedInventory $Root "runtime-closure.json")
    $expected = @($Closure.files)
    if ($actual.Count -ne $expected.Count -or $expected.Count -ne [long]$Closure.file_count) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $actualByPath = New-Object 'Collections.Generic.Dictionary[string,object]' ([StringComparer]::Ordinal)
    $actualAliases = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($actualRecord in $actual) {
        if (-not $actualAliases.Add([string]$actualRecord.path) -or
            $actualByPath.ContainsKey([string]$actualRecord.path)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $actualByPath.Add([string]$actualRecord.path, $actualRecord)
    }
    $aliases = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    [long]$total = 0
    foreach ($record in $expected) {
        Assert-ExactProperties $record @("path", "size", "sha256")
        Assert-Sha256 $record.sha256
        if ([string]$record.path -cnotmatch '^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*:)[^\\]+(?:/[^\\]+)*$' -or
            -not $aliases.Add([string]$record.path) -or -not (Test-JsonInteger $record.size 0 536870912) -or
            -not $actualByPath.ContainsKey([string]$record.path)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $actualRecord = $actualByPath[[string]$record.path]
        if ([string]$actualRecord.path -cne [string]$record.path -or
            [long]$actualRecord.size -ne [long]$record.size -or
            [string]$actualRecord.sha256 -cne [string]$record.sha256) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $total += [long]$record.size
    }
    if ($total -ne [long]$Closure.total_bytes -or
        (Get-CanonicalJsonSha256 $expected) -cne [string]$Closure.tree_sha256) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $required = @(
        ".jobops-root",
        "runtime/python.exe", "runtime/python313.dll", "runtime/python313._pth", "runtime/python313.zip",
        "app/jobops/__init__.py", "app/jobops/cli.py", "app/jobops/runtime_health.py"
    )
    foreach ($relative in $required) {
        if (-not $aliases.Contains($relative)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
    if (-not $aliases.Contains("config/windows-cp313-runtime.lock")) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    if (-not $aliases.Contains("config/windows-cp313-build.lock")) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-ExtractedRuntimeLockBinding $Root $Closure
    $buildLock = Read-StrictPortableJsonRegularFile `
        ([IO.Path]::Combine($Root, "config\windows-cp313-build.lock")) 1048576
    Assert-ApplicationWheelProvenance `
        $Closure.build_inputs.application_wheel_provenance `
        $Closure.build_inputs.application_wheel_sha256 $Closure.source_commit $buildLock.sha256
    $pth = [JobFlowBootstrapFiles]::ReadBoundedRegularFile(
        [IO.Path]::Combine($Root, "runtime\python313._pth"),
        1024
    )
    try {
        $expectedPth = [Text.UTF8Encoding]::new($false).GetBytes("python313.zip`n.`n../app`n")
        if ($pth.Length -ne $expectedPth.Length) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        for ($index = 0; $index -lt $pth.Length; $index++) {
            if ($pth[$index] -ne $expectedPth[$index]) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        }
    }
    finally { [Array]::Clear($pth, 0, $pth.Length) }
}

function Expand-AndVerifySignedArchive([object]$Manifest, [string]$Path, [bool]$RetainStageForActivation) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $archiveLock = $null
    $staging = $null
    $retainSuccessfulStage = $false
    try {
        $archiveLock = [JobFlowBootstrapFiles]::OpenLockedRegularFile($Path, $maximumArchiveBytes)
        if ([IO.Path]::GetFileName($archiveLock.FullPath) -cne [string]$Manifest.asset.name -or
            $archiveLock.Length -ne [long]$Manifest.asset.bytes) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        $archiveSha = Get-StreamSha256 $archiveLock.Stream
        if ($archiveSha -cne [string]$Manifest.asset.sha256) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        $preflight = [JobFlowBootstrapZip]::Preflight(
            $archiveLock.Stream,
            [string]$Manifest.asset.archive_prefix,
            [long]$Manifest.runtime_closure.total_bytes,
            [int]$Manifest.runtime_closure.file_count,
            $maximumClosureManifestBytes
        )
        $staging = New-SecureBootstrapStaging $trustedLocalDataRoot
        Add-Type -AssemblyName System.IO.Compression
        $zip = New-Object IO.Compression.ZipArchive($archiveLock.Stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try {
            if ($zip.Entries.Count -ne $preflight.Count) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
            for ($index = 0; $index -lt $preflight.Count; $index++) {
                $entry = $zip.Entries[$index]
                $checked = $preflight[$index]
                if ($entry.FullName -cne $checked.Name -or [long]$entry.Length -ne [long]$checked.Length -or
                    [long]$entry.CompressedLength -ne [long]$checked.CompressedSize) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
                $relative = $entry.FullName.Substring(([string]$Manifest.asset.archive_prefix).Length)
                if ([string]::IsNullOrEmpty($relative)) { continue }
                $target = [IO.Path]::GetFullPath([IO.Path]::Combine($staging.stage, $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)))
                if (-not $target.StartsWith($staging.stage.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
                if ($checked.IsDirectory) {
                    if (-not [IO.Directory]::Exists($target)) { [IO.Directory]::CreateDirectory($target) | Out-Null }
                    Assert-SafeDirectory $target | Out-Null
                    continue
                }
                $parent = [IO.Path]::GetDirectoryName($target)
                if (-not [IO.Directory]::Exists($parent)) { [IO.Directory]::CreateDirectory($parent) | Out-Null }
                Assert-SafeDirectory $parent | Out-Null
                $source = $entry.Open()
                $destination = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                try {
                    $buffer = New-Object byte[] 65536
                    [long]$written = 0
                    while (($read = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
                        $written += $read
                        if ($written -gt [long]$checked.Length) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
                        $destination.Write($buffer, 0, $read)
                    }
                    if ($written -ne [long]$checked.Length) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
                    $destination.Flush($true)
                }
                finally { $destination.Dispose(); $source.Dispose() }
            }
        }
        finally { $zip.Dispose() }
        $archiveLock.VerifyUnchanged()

        $closureRelative = "runtime-closure.json"
        $closurePath = [IO.Path]::Combine($staging.stage, $closureRelative)
        $closureBytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($closurePath, $maximumClosureManifestBytes)
        try {
            if ((Get-BytesSha256 $closureBytes) -cne [string]$Manifest.runtime_closure.manifest_sha256) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
            $closureText = $strictUtf8.GetString($closureBytes)
            [JobFlowBootstrapJson]::AssertNoDuplicateProperties($closureText)
            $closure = $closureText | ConvertFrom-Json
        }
        finally { [Array]::Clear($closureBytes, 0, $closureBytes.Length) }
        Assert-ExtractedRuntime $staging.stage $closure $Manifest
        # JOBFLOW_BOOTSTRAP_VERIFIED_STAGE_READY_BOUNDARY

        $archiveLock.VerifyUnchanged()
        $retainSuccessfulStage = $RetainStageForActivation
        return [pscustomobject]@{
            status = "JOBFLOW_BOOTSTRAP_RELEASE_VERIFIED"
            archive_sha256 = $archiveSha
            runtime_tree_sha256 = [string]$closure.tree_sha256
            runtime_file_count = [long]$closure.file_count
            python_entry = [string]$closure.layout.python
            closure = $closure
            stage = if ($RetainStageForActivation) { [string]$staging.stage } else { $null }
            staging_root = if ($RetainStageForActivation) { [string]$staging.staging_root } else { $null }
        }
    }
    finally {
        if ($null -ne $archiveLock) { $archiveLock.Dispose() }
        $cleanupFailed = $false
        if (-not $retainSuccessfulStage -and $null -ne $staging) {
            try { Remove-SecureBootstrapStaging $staging.stage $staging.staging_root }
            catch { $cleanupFailed = $true }
        }
        if ($cleanupFailed) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
}

function Read-StrictJsonRegularFile([string]$Path, [int]$MaximumBytes) {
    $bytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($Path, $MaximumBytes)
    try {
        $strict = New-Object Text.UTF8Encoding($false, $true)
        $text = $strict.GetString($bytes)
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($text)
        $value = $text | ConvertFrom-Json
        if ($null -eq $value -or $value -isnot [PSCustomObject]) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        return $value
    }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Read-StrictPortableJsonRegularFile([string]$Path, [int]$MaximumBytes) {
    $bytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($Path, $MaximumBytes)
    try {
        $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
        $text = $strictUtf8.GetString($bytes)
        if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $normalized = $text.Replace("`r`n", "`n")
        if ($normalized.Contains("`r")) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($text)
        $value = $text | ConvertFrom-Json
        return [pscustomobject]@{
            value = $value
            sha256 = Get-BytesSha256 ([Text.UTF8Encoding]::new($false).GetBytes($normalized))
        }
    }
    catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Get-PointerSchemaVersion([string]$Path) {
    if ([IO.Directory]::Exists($Path)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if (-not [IO.File]::Exists($Path)) { return $null }
    $value = Read-StrictJsonRegularFile $Path 65536
    $schema = $value.PSObject.Properties["schema_version"]
    if ($null -eq $schema -or
        -not ($schema.Value -is [int] -or $schema.Value -is [long])) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return [long]$schema.Value
}

function Assert-LegacyV1PointerShape([object]$Value) {
    Assert-ExactProperties $Value @("schema_version", "version_directory", "version", "source_sha256")
    if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
        [string]$Value.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-LegacySha256 $Value.source_sha256
    $expectedDirectory = "v$([string]$Value.version)-$(([string]$Value.source_sha256).Substring(0, 12))"
    if ([string]$Value.version_directory -cne $expectedDirectory) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function ConvertTo-LegacyV1PointerJson([object]$Value) {
    Assert-LegacyV1PointerShape $Value
    return '{"schema_version":1,"version_directory":"' + [string]$Value.version_directory +
        '","version":"' + [string]$Value.version + '","source_sha256":"' +
        [string]$Value.source_sha256 + '"}'
}

function Read-LegacyV1Pointer([string]$Path) {
    if ([IO.Directory]::Exists($Path) -or -not [IO.File]::Exists($Path)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $bytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($Path, 65536)
    try {
        $strict = New-Object Text.UTF8Encoding($false, $true)
        $text = $strict.GetString($bytes)
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($text)
        $value = $text | ConvertFrom-Json
        if ($null -eq $value -or $value -isnot [PSCustomObject]) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        Assert-LegacyV1PointerShape $value
        if ($text -cne (ConvertTo-LegacyV1PointerJson $value)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        return $value
    }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Read-InstalledPointerForMigration([string]$Path, [bool]$Required) {
    if ([IO.Directory]::Exists($Path)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if (-not [IO.File]::Exists($Path)) {
        if ($Required) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        return $null
    }
    $bytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($Path, 65536)
    try {
        $strict = New-Object Text.UTF8Encoding($false, $true)
        $text = $strict.GetString($bytes)
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($text)
        $value = $text | ConvertFrom-Json
        if ($null -eq $value -or $value -isnot [PSCustomObject]) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $schema = $value.PSObject.Properties["schema_version"]
        if ($null -eq $schema -or
            -not ($schema.Value -is [int] -or $schema.Value -is [long])) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        if ([long]$schema.Value -eq 1) {
            Assert-LegacyV1PointerShape $value
            if ($text -cne (ConvertTo-LegacyV1PointerJson $value)) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
        }
        elseif ([long]$schema.Value -eq 2) {
            Assert-InstalledPointerShape $value
        }
        else { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        return [pscustomobject]@{ schema_version = [long]$schema.Value; pointer = $value }
    }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Test-LegacyV1PointerAuthorized([object]$Manifest, [object]$Pointer) {
    Assert-LegacyV1PointerShape $Pointer
    $property = $Manifest.PSObject.Properties["legacy_v1_predecessors"]
    if ($null -eq $property) { return $false }
    foreach ($identity in @($property.Value)) {
        if ([long]$identity.schema_version -eq 1 -and
            [string]$identity.version -ceq [string]$Pointer.version -and
            [string]$identity.source_sha256 -ceq [string]$Pointer.source_sha256 -and
            [string]$identity.version_directory -ceq [string]$Pointer.version_directory) {
            return $true
        }
    }
    return $false
}

function Get-LegacyV1SourceTarget([string]$VersionsRoot, [object]$Pointer) {
    Assert-LegacyV1PointerShape $Pointer
    $root = (Assert-SafeDirectory $VersionsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $target = [IO.Path]::GetFullPath([IO.Path]::Combine($root, [string]$Pointer.version_directory))
    if ([IO.Path]::GetDirectoryName($target).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $root -or
        [IO.Path]::GetFileName($target) -cne [string]$Pointer.version_directory) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return Assert-SafeDirectory $target
}

function Test-LegacyV1SourceExcluded([string]$Relative) {
    $lower = $Relative.ToLowerInvariant()
    return (
        $lower -ceq "browser-companion/binding.json" -or
        $lower -ceq "browser-companion-binding.json" -or
        $lower -match '(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.tmp|\.git)(/|$)' -or
        $lower -match '\.(pyc|pyo|db|sqlite|sqlite3|dpapi|zip|7z|rar|log)$'
    )
}

function Get-LegacyV1SourceRecords([string]$Target) {
    $root = (Assert-SafeDirectory $Target).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $rootFiles = @(
        ".jobops-root", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE",
        "Install JobFlow Browser Companion.cmd", "MANIFEST.in", "README.md", "SECURITY.md",
        "Update JobFlow.cmd", "pyproject.toml"
    )
    $sourceDirectories = @(".agents", "browser-companion", "config", "docs", "schemas", "scripts", "src", "tests")
    $paths = New-Object 'Collections.Generic.List[object]'
    [long]$entryCount = 0
    [long]$directoryCount = 0
    foreach ($name in $rootFiles) {
        $path = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $name))
        if ([IO.Directory]::Exists($path)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        if ([IO.File]::Exists($path)) {
            $entryCount++
            if ($entryCount -gt $maximumLegacyV1Entries -or
                $name.Length -gt $maximumLegacyV1RelativePathChars) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            [void]$paths.Add([pscustomobject]@{ relative = $name; full = $path })
        }
    }
    foreach ($directoryName in $sourceDirectories) {
        $directory = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $directoryName))
        if ([IO.File]::Exists($directory) -or -not [IO.Directory]::Exists($directory)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $pending = New-Object 'Collections.Generic.Stack[string]'
        $visited = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $pending.Push((Assert-SafeDirectory $directory))
        $directoryCount++
        while ($pending.Count -gt 0) {
            $current = Assert-SafeDirectory $pending.Pop()
            if (-not $visited.Add($current)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
            foreach ($entry in @(Get-ChildItem -LiteralPath $current -Force)) {
                $entryCount++
                if ($entryCount -gt $maximumLegacyV1Entries) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
                $full = [IO.Path]::GetFullPath($entry.FullName)
                if (-not $full.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
                    ($entry.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device)) -ne 0) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
                $relative = $full.Substring($root.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/')
                if ($relative.Length -gt $maximumLegacyV1RelativePathChars -or
                    @($relative.Split('/')).Count -gt $maximumLegacyV1Depth) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
                if ($entry.PSIsContainer) {
                    if (Test-LegacyV1SourceExcluded $relative) { continue }
                    $directoryCount++
                    if ($directoryCount -gt $maximumLegacyV1Directories) {
                        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                    }
                    $pending.Push($full)
                    continue
                }
                if (($entry.Attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::Encrypted)) -ne 0) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
                if (-not (Test-LegacyV1SourceExcluded $relative)) {
                    [void]$paths.Add([pscustomobject]@{ relative = $relative; full = $full })
                    if ($paths.Count -gt $maximumLegacyV1Files) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
                }
            }
        }
    }
    $aliases = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in $paths) {
        if (-not $aliases.Add([string]$record.relative)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
    if (-not $aliases.Contains(".jobops-root")) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    return @($paths | Sort-Object -Property relative)
}

function Assert-LegacyV1InstalledSourceIdentity([string]$VersionsRoot, [object]$Pointer) {
    $target = Get-LegacyV1SourceTarget $VersionsRoot $Pointer
    $records = @(Get-LegacyV1SourceRecords $target)
    $manifestBuilder = New-Object Text.StringBuilder
    [long]$totalBytes = 0
    foreach ($record in $records) {
        $locked = [JobFlowBootstrapFiles]::OpenLockedRegularFile(
            [string]$record.full,
            $maximumLegacyV1FileBytes,
            $true
        )
        try {
            $sha = (Get-StreamSha256 $locked.Stream).Substring(7)
            $length = [long]$locked.Length
            if ($length -gt ($maximumLegacyV1TreeBytes - $totalBytes)) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            $totalBytes += $length
            $locked.VerifyUnchanged()
            [void]$manifestBuilder.Append([string]$record.relative).Append('|').Append($length).Append('|').Append($sha).Append("`n")
        }
        finally { $locked.Dispose() }
    }
    $manifestBytes = [Text.Encoding]::UTF8.GetBytes($manifestBuilder.ToString())
    try {
        $computed = (Get-BytesSha256 $manifestBytes).Substring(7)
        if ($computed -cne [string]$Pointer.source_sha256) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
    }
    finally { [Array]::Clear($manifestBytes, 0, $manifestBytes.Length) }
    return $target
}

function Get-ExistingLegacyV1Layout([string]$JobOpsRoot) {
    $jobOps = (Assert-SafeDirectory $JobOpsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $application = [IO.Path]::Combine($jobOps, "Application")
    $versions = [IO.Path]::Combine($application, "versions")
    $data = [IO.Path]::Combine($jobOps, "Data")
    $state = [IO.Path]::Combine($data, "state")
    foreach ($directory in @($application, $versions, $data, $state)) {
        if ([IO.File]::Exists($directory) -or -not [IO.Directory]::Exists($directory)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        [void](Assert-SafeDirectory $directory)
    }
    Read-AndValidateDataMarker ([IO.Path]::Combine($data, ".jobflow-data-root"))
    return [pscustomobject]@{
        jobops = $jobOps; application = $application; versions = $versions; data = $data; state = $state
    }
}

function Assert-LegacyV1ActivationEligibility([object]$Manifest) {
    $jobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
    if ([IO.File]::Exists($jobOpsRoot)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if (-not [IO.Directory]::Exists($jobOpsRoot)) { return $null }
    $jobOpsRoot = Assert-SafeDirectory $jobOpsRoot
    $currentPath = [IO.Path]::Combine($jobOpsRoot, "current.json")
    if ($null -eq $Manifest.PSObject.Properties["legacy_v1_predecessors"]) {
        $unapprovedSchema = Get-PointerSchemaVersion $currentPath
        if ($null -eq $unapprovedSchema -or $unapprovedSchema -eq 2) { return $null }
        if ($unapprovedSchema -eq 1) { throw "JOBFLOW_MANUAL_MIGRATION_REQUIRED" }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $currentRecord = Read-InstalledPointerForMigration $currentPath $false
    if ($null -eq $currentRecord -or [long]$currentRecord.schema_version -eq 2) { return $null }
    if ([long]$currentRecord.schema_version -ne 1) {
        throw "JOBFLOW_MANUAL_MIGRATION_REQUIRED"
    }
    $application = [IO.Path]::Combine($jobOpsRoot, "Application")
    $versions = [IO.Path]::Combine($application, "versions")
    foreach ($directory in @($application, $versions)) {
        if ([IO.File]::Exists($directory) -or -not [IO.Directory]::Exists($directory)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        [void](Assert-SafeDirectory $directory)
    }
    $current = $currentRecord.pointer
    if (-not (Test-LegacyV1PointerAuthorized $Manifest $current)) {
        throw "JOBFLOW_MANUAL_MIGRATION_REQUIRED"
    }
    [void](Assert-LegacyV1InstalledSourceIdentity $versions $current)
    $previousPath = [IO.Path]::Combine($jobOpsRoot, "previous.json")
    $previous = $null
    $previousRecord = Read-InstalledPointerForMigration $previousPath $false
    if ($null -ne $previousRecord) {
        if ([long]$previousRecord.schema_version -ne 1) {
            throw "JOBFLOW_MANUAL_MIGRATION_REQUIRED"
        }
        $previous = $previousRecord.pointer
        if (-not (Test-LegacyV1PointerAuthorized $Manifest $previous)) {
            throw "JOBFLOW_MANUAL_MIGRATION_REQUIRED"
        }
        [void](Assert-LegacyV1InstalledSourceIdentity $versions $previous)
    }
    # The program identity is now fully proven. Only after that boundary may
    # migration inspect the existing runtime-data marker and lock files.
    $layout = Get-ExistingLegacyV1Layout $jobOpsRoot
    return [pscustomobject]@{ layout = $layout; current = $current; previous = $previous }
}

function Assert-InstalledPointerShape([object]$Value) {
    Assert-ExactProperties $Value @(
        "schema_version", "product", "version_directory", "version", "source_commit",
        "source_payload_sha256", "runtime_closure_manifest_sha256", "runtime_tree_sha256",
        "release_key_id", "bootstrap_version", "platform"
    )
    if (-not (Test-JsonInteger $Value.schema_version 2 2) -or
        [string]$Value.product -cne "JobFlow" -or
        [string]$Value.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$Value.source_commit -cnotmatch '^[0-9a-f]{40}$' -or
        [string]$Value.release_key_id -cne $trustedKeyId -or
        [string]$Value.bootstrap_version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$Value.platform -cne "windows-x64") {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    foreach ($name in @("source_payload_sha256", "runtime_closure_manifest_sha256", "runtime_tree_sha256")) {
        Assert-Sha256 $Value.$name
    }
    $digest = ([string]$Value.source_payload_sha256).Substring(7)
    $expectedDirectory = "v$([string]$Value.version)-$($digest.Substring(0, 12))"
    if ([string]$Value.version_directory -cne $expectedDirectory -or
        (Compare-SemVer $Value.bootstrap_version $Value.version) -gt 0) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function New-InstalledPointer([object]$Manifest) {
    $payload = [string]$Manifest.runtime_closure.source_payload_sha256
    $digest = $payload.Substring(7)
    $pointer = [pscustomobject][ordered]@{
        schema_version = 2
        product = "JobFlow"
        version_directory = "v$([string]$Manifest.release.version)-$($digest.Substring(0, 12))"
        version = [string]$Manifest.release.version
        source_commit = [string]$Manifest.release.source_commit
        source_payload_sha256 = $payload
        runtime_closure_manifest_sha256 = [string]$Manifest.runtime_closure.manifest_sha256
        runtime_tree_sha256 = [string]$Manifest.runtime_closure.tree_sha256
        release_key_id = $trustedKeyId
        bootstrap_version = $bootstrapVersion
        platform = "windows-x64"
    }
    Assert-InstalledPointerShape $pointer
    return $pointer
}

function Read-InstalledPointer([string]$Path, [bool]$CurrentPointer) {
    if ([IO.Directory]::Exists($Path)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if (-not [IO.File]::Exists($Path)) { return $null }
    $value = Read-StrictJsonRegularFile $Path 65536
    $schema = $value.PSObject.Properties["schema_version"]
    if ($CurrentPointer -and $null -ne $schema -and
        ($schema.Value -is [int] -or $schema.Value -is [long]) -and
        [long]$schema.Value -eq 1) {
        throw "JOBFLOW_MANUAL_MIGRATION_REQUIRED"
    }
    Assert-InstalledPointerShape $value
    return $value
}

function New-InstalledClosureBinding([object]$Pointer, [object]$Closure) {
    return [pscustomobject]@{
        release = [pscustomobject]@{
            version = [string]$Pointer.version
            source_commit = [string]$Pointer.source_commit
        }
        runtime_closure = [pscustomobject]@{
            file_count = [long]$Closure.file_count
            total_bytes = [long]$Closure.total_bytes
            tree_sha256 = [string]$Pointer.runtime_tree_sha256
            python_version = [string]$Closure.python.version
            platform = [string]$Pointer.platform
            build_inputs = [pscustomobject]@{
                python_artifact_sha256 = [string]$Closure.python.artifact_sha256
                wheel_lock_sha256 = [string]$Closure.build_inputs.wheel_lock_sha256
                wheelhouse_tree_sha256 = [string]$Closure.build_inputs.wheelhouse_tree_sha256
                application_wheel_sha256 = [string]$Closure.build_inputs.application_wheel_sha256
                application_wheel_provenance = $Closure.build_inputs.application_wheel_provenance
                builder_toolchain_sha256 = [string]$Closure.build_inputs.builder_toolchain_sha256
                wheel_count = @($Closure.build_inputs.wheels).Count
            }
        }
    }
}

function Assert-InstalledRuntime([string]$VersionsRoot, [object]$Pointer) {
    Assert-InstalledPointerShape $Pointer
    $root = (Assert-SafeDirectory $VersionsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $target = [IO.Path]::GetFullPath([IO.Path]::Combine($root, [string]$Pointer.version_directory))
    if ([IO.Path]::GetDirectoryName($target).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $root -or
        [IO.Path]::GetFileName($target) -cne [string]$Pointer.version_directory) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-SafeDirectory $target | Out-Null
    $closurePath = [IO.Path]::Combine($target, "runtime-closure.json")
    $closureBytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($closurePath, $maximumClosureManifestBytes)
    try {
        if ((Get-BytesSha256 $closureBytes) -cne [string]$Pointer.runtime_closure_manifest_sha256) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $strict = New-Object Text.UTF8Encoding($false, $true)
        $closureText = $strict.GetString($closureBytes)
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($closureText)
        $closure = $closureText | ConvertFrom-Json
    }
    finally { [Array]::Clear($closureBytes, 0, $closureBytes.Length) }
    $binding = New-InstalledClosureBinding $Pointer $closure
    Assert-ExtractedRuntime $target $closure $binding
    return [pscustomobject]@{ target = $target; closure = $closure }
}

function Initialize-ActivationLayout {
    $jobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
    if ([IO.File]::Exists($jobOpsRoot)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if (-not [IO.Directory]::Exists($jobOpsRoot)) {
        [JobFlowBootstrapFiles]::CreateNewDirectory($jobOpsRoot)
    }
    $jobOpsRoot = Assert-SafeDirectory $jobOpsRoot
    Set-CurrentUserOnlyDirectoryAcl $jobOpsRoot
    $applicationRoot = New-OrValidateFixedDirectory $jobOpsRoot "Application"
    $versionsRoot = New-OrValidateFixedDirectory $applicationRoot "versions"
    $dataLayout = Initialize-OrValidateDataRoot $jobOpsRoot
    return [pscustomobject]@{
        jobops = $jobOpsRoot
        application = $applicationRoot
        versions = $versionsRoot
        data = [string]$dataLayout.data
        state = [string]$dataLayout.state
    }
}

function Get-ExistingActivationLayoutForRecovery([string]$JobOpsRoot) {
    try {
        if ([IO.File]::Exists($JobOpsRoot) -or -not [IO.Directory]::Exists($JobOpsRoot)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED"
        }
        $jobOps = (Assert-SafeDirectory $JobOpsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
        $application = [IO.Path]::Combine($jobOps, "Application")
        $versions = [IO.Path]::Combine($application, "versions")
        $data = [IO.Path]::Combine($jobOps, "Data")
        $state = [IO.Path]::Combine($data, "state")
        foreach ($directory in @($application, $versions, $data, $state)) {
            if ([IO.File]::Exists($directory) -or -not [IO.Directory]::Exists($directory)) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED"
            }
            [void](Assert-SafeDirectory $directory)
        }
        Read-AndValidateDataMarker ([IO.Path]::Combine($data, ".jobflow-data-root"))
        return [pscustomobject]@{
            jobops = $jobOps
            application = $application
            versions = $versions
            data = $data
            state = $state
        }
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED" }
}

function Get-ActivationTrustRoot([object]$Layout, [bool]$Create) {
    $state = (Assert-SafeDirectory ([string]$Layout.state)).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $root = [IO.Path]::GetFullPath([IO.Path]::Combine($state, "activation-trust"))
    if ([IO.Path]::GetDirectoryName($root).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $state -or
        [IO.Path]::GetFileName($root) -cne "activation-trust" -or [IO.File]::Exists($root)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    if (-not [IO.Directory]::Exists($root)) {
        if (-not $Create) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        [JobFlowBootstrapFiles]::CreateNewDirectory($root)
        Set-CurrentUserOnlyDirectoryAcl $root
    }
    $root = Assert-SafeDirectory $root
    Assert-CurrentUserOnlyAcl $root $true
    return $root
}

function Get-ActivationTrustVersionPath([object]$Layout, [object]$Pointer, [bool]$CreateRoot) {
    Assert-InstalledPointerShape $Pointer
    $root = (Get-ActivationTrustRoot $Layout $CreateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $leaf = [string]$Pointer.version_directory
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $leaf))
    if ([IO.Path]::GetDirectoryName($path).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $root -or
        [IO.Path]::GetFileName($path) -cne $leaf) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return $path
}

function New-ActivationTrustEvidence(
    [object]$Pointer,
    [string]$ManifestSha256,
    [string]$SignatureEnvelopeSha256,
    [string]$TransactionId
) {
    Assert-InstalledPointerShape $Pointer
    Assert-Sha256 $ManifestSha256
    Assert-Sha256 $SignatureEnvelopeSha256
    if ($TransactionId -cnotmatch '^[0-9a-f]{32}$') { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ACTIVATION_TRUST_EVIDENCE"
        version = [string]$Pointer.version
        version_directory = [string]$Pointer.version_directory
        transaction_id = $TransactionId
        manifest_sha256 = $ManifestSha256
        signature_envelope_sha256 = $SignatureEnvelopeSha256
        canonical_pointer_sha256 = Get-CanonicalJsonSha256 $Pointer
        runtime_closure_manifest_sha256 = [string]$Pointer.runtime_closure_manifest_sha256
        runtime_tree_sha256 = [string]$Pointer.runtime_tree_sha256
        release_key_id = [string]$Pointer.release_key_id
        source_payload_sha256 = [string]$Pointer.source_payload_sha256
    }
}

function Assert-ActivationTrustEvidenceShape(
    [object]$Value,
    [object]$Pointer,
    [string]$ManifestSha256,
    [string]$SignatureEnvelopeSha256,
    [string]$ExpectedTransactionId
) {
    if ($Value -isnot [PSCustomObject]) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    Assert-ExactProperties $Value @(
        "schema_version", "kind", "version", "version_directory", "transaction_id",
        "manifest_sha256", "signature_envelope_sha256", "canonical_pointer_sha256",
        "runtime_closure_manifest_sha256", "runtime_tree_sha256", "release_key_id",
        "source_payload_sha256"
    )
    foreach ($name in @(
        "manifest_sha256", "signature_envelope_sha256", "canonical_pointer_sha256",
        "runtime_closure_manifest_sha256", "runtime_tree_sha256", "release_key_id",
        "source_payload_sha256"
    )) { Assert-Sha256 $Value.$name }
    if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
        [string]$Value.kind -cne "JOBFLOW_ACTIVATION_TRUST_EVIDENCE" -or
        [string]$Value.version -cne [string]$Pointer.version -or
        [string]$Value.version_directory -cne [string]$Pointer.version_directory -or
        [string]$Value.transaction_id -cnotmatch '^[0-9a-f]{32}$' -or
        (-not [string]::IsNullOrEmpty($ExpectedTransactionId) -and
            [string]$Value.transaction_id -cne $ExpectedTransactionId) -or
        [string]$Value.manifest_sha256 -cne $ManifestSha256 -or
        [string]$Value.signature_envelope_sha256 -cne $SignatureEnvelopeSha256 -or
        [string]$Value.canonical_pointer_sha256 -cne (Get-CanonicalJsonSha256 $Pointer) -or
        [string]$Value.runtime_closure_manifest_sha256 -cne [string]$Pointer.runtime_closure_manifest_sha256 -or
        [string]$Value.runtime_tree_sha256 -cne [string]$Pointer.runtime_tree_sha256 -or
        [string]$Value.release_key_id -cne $trustedKeyId -or
        [string]$Value.release_key_id -cne [string]$Pointer.release_key_id -or
        [string]$Value.source_payload_sha256 -cne [string]$Pointer.source_payload_sha256) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Write-NewCurrentUserOnlyFile([string]$Path, [byte[]]$Bytes) {
    if ($null -eq $Bytes -or $Bytes.Length -lt 1 -or
        [IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $stream = $null
    try {
        $stream = New-Object IO.FileStream(
            $Path,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
    Set-CurrentUserOnlyFileAcl $Path
    Assert-CurrentUserOnlyAcl $Path $false
}

function Read-CanonicalActivationTrustEvidence([string]$Path) {
    $bytes = $null
    try {
        $bytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($Path, $maximumActivationEvidenceBytes)
        $strict = New-Object Text.UTF8Encoding($false, $true)
        $text = $strict.GetString($bytes)
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($text)
        $value = $text | ConvertFrom-Json
        if ($null -eq $value -or $value -isnot [PSCustomObject] -or
            $text -cne (ConvertTo-CanonicalJson $value)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        return [pscustomobject]@{ value = $value; bytes = $bytes }
    }
    catch {
        if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Assert-ActivationTrustDirectory(
    [object]$Layout,
    [object]$Pointer,
    [string]$Directory,
    [string]$ExpectedTransactionId,
    [byte[]]$ExpectedManifestBytes,
    [byte[]]$ExpectedSignatureBytes
) {
    $root = (Get-ActivationTrustRoot $Layout $false).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $directory = (Assert-SafeDirectory $Directory).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $directoryLeaf = [IO.Path]::GetFileName($directory)
    $temporaryLeaf = "." + [string]$Pointer.version_directory + ".write.tmp"
    if ([IO.Path]::GetDirectoryName($directory).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $root -or
        $directoryLeaf -cnotin @([string]$Pointer.version_directory, $temporaryLeaf)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-CurrentUserOnlyAcl $directory $true
    $expectedNames = @("activation-evidence.json", "release-manifest.json", "release-manifest.signature.json")
    $entries = @(Get-ChildItem -LiteralPath $directory -Force)
    if ($entries.Count -ne $expectedNames.Count) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    foreach ($entry in $entries) {
        if ($entry.PSIsContainer -or
            ($entry.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device)) -ne 0 -or
            $entry.Name -cnotin $expectedNames) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        Assert-CurrentUserOnlyAcl $entry.FullName $false
    }
    $manifestPath = [IO.Path]::Combine($directory, "release-manifest.json")
    $signaturePath = [IO.Path]::Combine($directory, "release-manifest.signature.json")
    $evidencePath = [IO.Path]::Combine($directory, "activation-evidence.json")
    $manifestBytes = $null
    $signatureBytes = $null
    $evidenceRecord = $null
    try {
        $manifestBytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($manifestPath, $maximumManifestBytes)
        $signatureBytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($signaturePath, $maximumSignatureBytes)
        if ($null -ne $ExpectedManifestBytes -and
            -not (Test-ByteArraysEqual $manifestBytes $ExpectedManifestBytes)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        if ($null -ne $ExpectedSignatureBytes -and
            -not (Test-ByteArraysEqual $signatureBytes $ExpectedSignatureBytes)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $signed = Assert-EmbeddedSignedManifestEvidence $manifestBytes $signatureBytes
        $manifestPointer = New-InstalledPointer $signed.manifest
        if (-not (Test-PointerValueEqual $manifestPointer $Pointer)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        [void](Assert-InstalledRuntime ([string]$Layout.versions) $Pointer)
        $evidenceRecord = Read-CanonicalActivationTrustEvidence $evidencePath
        Assert-ActivationTrustEvidenceShape `
            $evidenceRecord.value $Pointer ([string]$signed.manifest_sha256) `
            ([string]$signed.signature_envelope_sha256) $ExpectedTransactionId
        return [pscustomobject]@{
            evidence = $evidenceRecord.value
            manifest_sha256 = [string]$signed.manifest_sha256
            signature_envelope_sha256 = [string]$signed.signature_envelope_sha256
        }
    }
    finally {
        if ($null -ne $manifestBytes) { [Array]::Clear($manifestBytes, 0, $manifestBytes.Length) }
        if ($null -ne $signatureBytes) { [Array]::Clear($signatureBytes, 0, $signatureBytes.Length) }
        if ($null -ne $evidenceRecord -and $null -ne $evidenceRecord.bytes) {
            [Array]::Clear($evidenceRecord.bytes, 0, $evidenceRecord.bytes.Length)
        }
    }
}

function Ensure-ActivationTrustEvidence(
    [object]$Layout,
    [object]$Pointer,
    [byte[]]$ManifestBytes,
    [byte[]]$SignatureEnvelopeBytes,
    [string]$TransactionId
) {
    if ($TransactionId -cnotmatch '^[0-9a-f]{32}$') { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    [void](Assert-InstalledRuntime ([string]$Layout.versions) $Pointer)
    $signed = Assert-EmbeddedSignedManifestEvidence $ManifestBytes $SignatureEnvelopeBytes
    if (-not (Test-PointerValueEqual (New-InstalledPointer $signed.manifest) $Pointer)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $final = Get-ActivationTrustVersionPath $Layout $Pointer $true
    if ([IO.File]::Exists($final)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if ([IO.Directory]::Exists($final)) {
        $existing = Assert-ActivationTrustDirectory `
            $Layout $Pointer $final "" $ManifestBytes $SignatureEnvelopeBytes
        return [string]$existing.evidence.transaction_id
    }

    $root = [IO.Path]::GetDirectoryName($final)
    $temporaryLeaf = "." + [string]$Pointer.version_directory + ".write.tmp"
    $temporary = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $temporaryLeaf))
    if ([IO.Path]::GetDirectoryName($temporary).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne
            $root.TrimEnd([IO.Path]::DirectorySeparatorChar) -or
        [IO.Path]::GetFileName($temporary) -cne $temporaryLeaf -or
        [IO.File]::Exists($temporary) -or [IO.Directory]::Exists($temporary)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    try {
        [JobFlowBootstrapFiles]::CreateNewDirectory($temporary)
        Set-CurrentUserOnlyDirectoryAcl $temporary
        Assert-CurrentUserOnlyAcl $temporary $true
        Write-NewCurrentUserOnlyFile `
            ([IO.Path]::Combine($temporary, "release-manifest.json")) $ManifestBytes
        Write-NewCurrentUserOnlyFile `
            ([IO.Path]::Combine($temporary, "release-manifest.signature.json")) $SignatureEnvelopeBytes
        $evidence = New-ActivationTrustEvidence `
            $Pointer ([string]$signed.manifest_sha256) `
            ([string]$signed.signature_envelope_sha256) $TransactionId
        $evidenceBytes = [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-CanonicalJson $evidence))
        try {
            Write-NewCurrentUserOnlyFile `
                ([IO.Path]::Combine($temporary, "activation-evidence.json")) $evidenceBytes
        }
        finally { [Array]::Clear($evidenceBytes, 0, $evidenceBytes.Length) }
        [void](Assert-ActivationTrustDirectory `
            $Layout $Pointer $temporary $TransactionId $ManifestBytes $SignatureEnvelopeBytes)
        [IO.Directory]::Move($temporary, $final)
        $temporary = $null
        $published = Assert-ActivationTrustDirectory `
            $Layout $Pointer $final $TransactionId $ManifestBytes $SignatureEnvelopeBytes
        return [string]$published.evidence.transaction_id
    }
    catch {
        if (-not [string]::IsNullOrEmpty($temporary) -and [IO.Directory]::Exists($temporary)) {
            try {
                [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
                    $temporary,
                    $maximumActivationTrustEntries,
                    $maximumActivationTrustBytes
                )
            }
            catch { }
        }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
}

function Assert-ActivationTrustEvidenceForPointer(
    [object]$Layout,
    [object]$Pointer,
    [string]$ExpectedTransactionId
) {
    $directory = Get-ActivationTrustVersionPath $Layout $Pointer $false
    if ([IO.File]::Exists($directory) -or -not [IO.Directory]::Exists($directory)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return Assert-ActivationTrustDirectory `
        $Layout $Pointer $directory $ExpectedTransactionId $null $null
}

function Get-RuntimeHealthRoot([object]$Layout) {
    $jobOpsRoot = (Assert-SafeDirectory ([string]$Layout.jobops)).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $dataRoot = (Assert-SafeDirectory ([string]$Layout.data)).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $root = [IO.Path]::GetFullPath([IO.Path]::Combine($jobOpsRoot, "RuntimeHealthV1"))
    if ([IO.Path]::GetDirectoryName($root).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne $jobOpsRoot -or
        [IO.Path]::GetFileName($root) -cne "RuntimeHealthV1" -or
        [IO.Path]::GetPathRoot($root) -cne [IO.Path]::GetPathRoot($trustedLocalDataRoot) -or
        $root.StartsWith($dataRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $root -ceq $dataRoot) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return New-OrValidateFixedDirectory $jobOpsRoot "RuntimeHealthV1"
}

function Get-RuntimeHealthTemporaryPath(
    [object]$Layout,
    [string]$TransactionId,
    [string]$Phase
) {
    if ($TransactionId -cnotmatch '^[0-9a-f]{32}$' -or $Phase -notin @("pre", "post")) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $root = Get-RuntimeHealthRoot $Layout
    $leaf = "health-" + $TransactionId + "-" + $Phase
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $leaf))
    if ([IO.Path]::GetDirectoryName($path).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne
            $root.TrimEnd([IO.Path]::DirectorySeparatorChar) -or
        [IO.Path]::GetFileName($path) -cne $leaf) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return $path
}

function New-RuntimeHealthTemporary(
    [object]$Layout,
    [string]$TransactionId,
    [string]$Phase
) {
    $path = Get-RuntimeHealthTemporaryPath $Layout $TransactionId $Phase
    if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    [JobFlowBootstrapFiles]::CreateNewDirectory($path)
    try {
        Set-CurrentUserOnlyDirectoryAcl $path
        Assert-SafeDirectory $path | Out-Null
    }
    catch {
        try {
            [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
                $path,
                $maximumRuntimeHealthTemporaryEntries,
                $maximumRuntimeHealthTemporaryBytes
            )
        }
        catch { }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    return $path
}

function Remove-RuntimeHealthTemporary(
    [object]$Layout,
    [string]$TransactionId,
    [string]$Phase
) {
    $path = Get-RuntimeHealthTemporaryPath $Layout $TransactionId $Phase
    if ([IO.File]::Exists($path)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not [IO.Directory]::Exists($path)) { return }
    try {
        [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
            $path,
            $maximumRuntimeHealthTemporaryEntries,
            $maximumRuntimeHealthTemporaryBytes
        )
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Remove-RuntimeHealthTransactionTemporary(
    [object]$Layout,
    [string]$TransactionId
) {
    Remove-RuntimeHealthTemporary $Layout $TransactionId "pre"
    Remove-RuntimeHealthTemporary $Layout $TransactionId "post"
}

function Invoke-CandidateRuntimeHealth(
    [object]$Layout,
    [object]$Candidate,
    [string]$TransactionId,
    [string]$Phase
) {
    if ($Phase -notin @("pre", "post")) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    $failureCode = if ($Phase -ceq "pre") {
        "JOBFLOW_RUNTIME_HEALTH_PRE_FAILED"
    }
    else { "JOBFLOW_RUNTIME_HEALTH_POST_FAILED" }
    $temporary = $null
    $result = $null
    $failed = $false
    try {
        $installedBefore = Assert-InstalledRuntime ([string]$Layout.versions) $Candidate
        $target = [string]$installedBefore.target
        $python = [IO.Path]::GetFullPath([IO.Path]::Combine($target, "runtime\python.exe"))
        if ([IO.Path]::GetDirectoryName($python).TrimEnd([IO.Path]::DirectorySeparatorChar) -cne
                [IO.Path]::Combine($target, "runtime").TrimEnd([IO.Path]::DirectorySeparatorChar) -or
            [IO.Path]::GetFileName($python) -cne "python.exe") {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        $temporary = New-RuntimeHealthTemporary $Layout $TransactionId $Phase
        $result = [JobFlowRuntimeHealthRunner]::Run(
            $python,
            $target,
            $trustedWindowsRoot,
            $trustedWindowsRoot,
            $temporary,
            $trustedLocalDataRoot
        )
        $expectedOutput = [Text.Encoding]::ASCII.GetBytes("JOBFLOW_RUNTIME_HEALTH_OK_V1`n")
        try {
            if ($result.ExitCode -ne 0 -or $result.TimedOut -or $result.OutputOverflow -or
                -not (Test-ByteArraysEqual ([byte[]]$result.StandardOutput) $expectedOutput) -or
                ([byte[]]$result.StandardError).Length -ne 0) {
                $failed = $true
            }
        }
        finally { [Array]::Clear($expectedOutput, 0, $expectedOutput.Length) }
        [void](Assert-InstalledRuntime ([string]$Layout.versions) $Candidate)
    }
    catch { $failed = $true }
    finally {
        if ($null -ne $result) {
            if ($null -ne $result.StandardOutput) {
                [Array]::Clear($result.StandardOutput, 0, $result.StandardOutput.Length)
            }
            if ($null -ne $result.StandardError) {
                [Array]::Clear($result.StandardError, 0, $result.StandardError.Length)
            }
        }
        if ($null -ne $temporary) {
            try { Remove-RuntimeHealthTemporary $Layout $TransactionId $Phase }
            catch { $failed = $true }
        }
    }
    if ($failed) { throw $failureCode }
}

function Write-AtomicPointerTemporary([string]$JobOpsRoot, [object]$Value) {
    $path = [IO.Path]::Combine($JobOpsRoot, "pointer-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-CanonicalJson $Value))
    try {
        $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        Set-CurrentUserOnlyFileAcl $path
        [void](Read-InstalledPointer $path $false)
        return $path
    }
    catch {
        if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Publish-AtomicPointer([string]$Temporary, [string]$Destination, [string]$Backup) {
    if ([IO.Directory]::Exists($Destination)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if ([IO.File]::Exists($Destination)) {
        [IO.File]::Replace($Temporary, $Destination, $Backup, $true)
        return $true
    }
    [IO.File]::Move($Temporary, $Destination)
    return $false
}

function Restore-AtomicPointer(
    [string]$Destination,
    [bool]$OriginallyPresent,
    [string]$Backup,
    [bool]$Published
) {
    if (-not $Published) { return }
    if ($OriginallyPresent) {
        if (-not [IO.File]::Exists($Backup) -or -not [IO.File]::Exists($Destination)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        [IO.File]::Replace($Backup, $Destination, $null, $true)
    }
    elseif ([IO.File]::Exists($Destination)) {
        [IO.File]::Delete($Destination)
    }
}

function Publish-PointerPair(
    [string]$JobOpsRoot,
    [object]$NewCurrent,
    [object]$OldCurrent,
    [object]$OldPrevious
) {
    $currentPath = [IO.Path]::Combine($JobOpsRoot, "current.json")
    $previousPath = [IO.Path]::Combine($JobOpsRoot, "previous.json")
    $currentWasPresent = $null -ne $OldCurrent
    $previousWasPresent = $null -ne $OldPrevious
    $currentTemporary = $null
    $previousTemporary = $null
    $currentBackup = [IO.Path]::Combine($JobOpsRoot, "pointer-current-backup-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $previousBackup = [IO.Path]::Combine($JobOpsRoot, "pointer-previous-backup-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $currentPublished = $false
    $previousPublished = $false
    $committed = $false
    try {
        $currentTemporary = Write-AtomicPointerTemporary $JobOpsRoot $NewCurrent
        if ($currentWasPresent) {
            $previousTemporary = Write-AtomicPointerTemporary $JobOpsRoot $OldCurrent
            [void](Publish-AtomicPointer $previousTemporary $previousPath $previousBackup)
            $previousTemporary = $null
            $previousPublished = $true
            # JOBFLOW_ACTIVATION_PREVIOUS_POINTER_PUBLISHED_BOUNDARY
        }
        $replacedCurrent = Publish-AtomicPointer $currentTemporary $currentPath $currentBackup
        $currentTemporary = $null
        $currentPublished = $true
        # JOBFLOW_ACTIVATION_CURRENT_POINTER_PUBLISHED_BOUNDARY
        if ($replacedCurrent -ne $currentWasPresent) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        $verifiedCurrent = Read-InstalledPointer $currentPath $true
        if ((Get-CanonicalJsonSha256 $verifiedCurrent) -cne (Get-CanonicalJsonSha256 $NewCurrent)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        if ($currentWasPresent) {
            $verifiedPrevious = Read-InstalledPointer $previousPath $false
            if ((Get-CanonicalJsonSha256 $verifiedPrevious) -cne (Get-CanonicalJsonSha256 $OldCurrent)) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
        }
        $committed = $true
    }
    finally {
        if (-not $committed) {
            $rollbackFailed = $false
            try { Restore-AtomicPointer $currentPath $currentWasPresent $currentBackup $currentPublished }
            catch { $rollbackFailed = $true }
            try { Restore-AtomicPointer $previousPath $previousWasPresent $previousBackup $previousPublished }
            catch { $rollbackFailed = $true }
            if ($rollbackFailed) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        }
        foreach ($path in @($currentTemporary, $previousTemporary, $currentBackup, $previousBackup)) {
            if (-not [string]::IsNullOrEmpty($path) -and [IO.File]::Exists($path)) {
                [IO.File]::Delete($path)
            }
        }
    }
}

function Test-CandidateTargetIsProvablyUnreferenced(
    [string]$JobOpsRoot,
    [string]$VersionsRoot,
    [object]$Candidate
) {
    try {
        $current = Read-InstalledPointer ([IO.Path]::Combine($JobOpsRoot, "current.json")) $true
        $previous = Read-InstalledPointer ([IO.Path]::Combine($JobOpsRoot, "previous.json")) $false
        if ($null -eq $current -and $null -ne $previous) { return $false }
        foreach ($pointer in @($current, $previous)) {
            if ($null -eq $pointer) { continue }
            if ([string]$pointer.version_directory -ceq [string]$Candidate.version_directory) {
                return $false
            }
            [void](Assert-InstalledRuntime $VersionsRoot $pointer)
        }
        return $true
    }
    catch { return $false }
}

function Get-ActivationStatePaths([string]$StateRoot) {
    $root = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $dataRoot = [IO.Path]::GetDirectoryName($root)
    $jobOpsRoot = [IO.Path]::GetDirectoryName($dataRoot)
    if ([string]::IsNullOrEmpty($dataRoot) -or [string]::IsNullOrEmpty($jobOpsRoot)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    return [pscustomobject]@{
        main = [IO.Path]::Combine($root, ".jobflow-activation-transaction-v1.json")
        backup = [IO.Path]::Combine($root, ".jobflow-activation-transaction-v1.backup.json")
        main_temporary = [IO.Path]::Combine($root, ".jobflow-activation-transaction-v1.main.write.tmp")
        backup_temporary = [IO.Path]::Combine($root, ".jobflow-activation-transaction-v1.backup.write.tmp")
        receipt = [IO.Path]::Combine($jobOpsRoot, ".jobflow-activation-completion-v1.json")
        receipt_temporary = [IO.Path]::Combine($jobOpsRoot, ".jobflow-activation-completion-v1.write.tmp")
    }
}

function Remove-ReservedActivationTemporary([string]$Path) {
    if ([IO.Directory]::Exists($Path)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not [IO.File]::Exists($Path)) { return }
    $locked = $null
    try {
        $locked = [JobFlowBootstrapFiles]::OpenLockedRegularFile($Path, $maximumActivationStateBytes, $true)
        $locked.VerifyUnchanged()
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    finally { if ($null -ne $locked) { $locked.Dispose() } }
    try { [IO.File]::Delete($Path) }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Test-ByteArraysEqual([byte[]]$Left, [byte[]]$Right) {
    if ($null -eq $Left -or $null -eq $Right -or $Left.Length -ne $Right.Length) { return $false }
    for ($index = 0; $index -lt $Left.Length; $index++) {
        if ($Left[$index] -ne $Right[$index]) { return $false }
    }
    return $true
}

function New-ActivationJournalSemantic(
    [string]$TransactionId,
    [string]$State,
    [long]$Generation,
    [bool]$CandidateTargetWasPresent,
    [object]$OriginalCurrent,
    [object]$OriginalPrevious,
    [object]$Candidate
) {
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ACTIVATION_TRANSACTION"
        transaction_id = $TransactionId
        state = $State
        generation = $Generation
        candidate_target_was_present = $CandidateTargetWasPresent
        original_current = $OriginalCurrent
        original_previous = $OriginalPrevious
        candidate = $Candidate
    }
}

function New-ActivationJournalEnvelope(
    [string]$TransactionId,
    [string]$State,
    [long]$Generation,
    [bool]$CandidateTargetWasPresent,
    [object]$OriginalCurrent,
    [object]$OriginalPrevious,
    [object]$Candidate
) {
    $semantic = New-ActivationJournalSemantic `
        $TransactionId $State $Generation $CandidateTargetWasPresent $OriginalCurrent $OriginalPrevious $Candidate
    $value = [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ACTIVATION_TRANSACTION"
        transaction_id = $TransactionId
        state = $State
        generation = $Generation
        candidate_target_was_present = $CandidateTargetWasPresent
        original_current = $OriginalCurrent
        original_previous = $OriginalPrevious
        candidate = $Candidate
        semantic_sha256 = Get-CanonicalJsonSha256 $semantic
    }
    Assert-ActivationJournalShape $value
    return $value
}

function Assert-ActivationJournalShape([object]$Value) {
    Assert-ExactProperties $Value @(
        "schema_version", "kind", "transaction_id", "state", "generation", "candidate_target_was_present",
        "original_current", "original_previous", "candidate", "semantic_sha256"
    )
    if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
        [string]$Value.kind -cne "JOBFLOW_ACTIVATION_TRANSACTION" -or
        [string]$Value.transaction_id -cnotmatch '^[0-9a-f]{32}$' -or
        $Value.candidate_target_was_present -isnot [bool]) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    $expectedGeneration = 0
    if ([string]$Value.state -ceq "PREPARED") { $expectedGeneration = 1 }
    elseif ([string]$Value.state -ceq "PRE_HEALTH_OK") { $expectedGeneration = 2 }
    elseif ([string]$Value.state -ceq "POINTER_SWITCHED") { $expectedGeneration = 3 }
    elseif ([string]$Value.state -ceq "POST_HEALTH_OK") { $expectedGeneration = 4 }
    elseif ([string]$Value.state -ceq "COMMITTED") { $expectedGeneration = 5 }
    else { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not (Test-JsonInteger $Value.generation $expectedGeneration $expectedGeneration)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    try {
        if ($null -ne $Value.original_current) { Assert-InstalledPointerShape $Value.original_current }
        if ($null -ne $Value.original_previous) { Assert-InstalledPointerShape $Value.original_previous }
        Assert-InstalledPointerShape $Value.candidate
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if ($null -eq $Value.original_current -and $null -ne $Value.original_previous) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    if ($null -ne $Value.original_current) {
        if ((Compare-SemVer $Value.candidate.version $Value.original_current.version) -le 0 -or
            (Get-CanonicalJsonSha256 $Value.candidate) -ceq (Get-CanonicalJsonSha256 $Value.original_current)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
    }
    if ($null -ne $Value.original_previous -and
        (Get-CanonicalJsonSha256 $Value.original_previous) -ceq (Get-CanonicalJsonSha256 $Value.original_current)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    try { Assert-Sha256 $Value.semantic_sha256 }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $semantic = New-ActivationJournalSemantic `
        ([string]$Value.transaction_id) ([string]$Value.state) ([long]$Value.generation) `
        ([bool]$Value.candidate_target_was_present) $Value.original_current $Value.original_previous $Value.candidate
    if ([string]$Value.semantic_sha256 -cne (Get-CanonicalJsonSha256 $semantic)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Get-ActivationJournalImmutableSha256([object]$Value) {
    $immutable = [pscustomobject][ordered]@{
        schema_version = [long]$Value.schema_version
        kind = [string]$Value.kind
        transaction_id = [string]$Value.transaction_id
        candidate_target_was_present = [bool]$Value.candidate_target_was_present
        original_current = $Value.original_current
        original_previous = $Value.original_previous
        candidate = $Value.candidate
    }
    return Get-CanonicalJsonSha256 $immutable
}

function Read-CanonicalActivationStateFile([string]$Path, [string]$Kind) {
    $bytes = $null
    try {
        $bytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile($Path, $maximumActivationStateBytes)
        $strict = New-Object Text.UTF8Encoding($false, $true)
        $text = $strict.GetString($bytes)
        [JobFlowBootstrapJson]::AssertNoDuplicateProperties($text)
        $value = $text | ConvertFrom-Json
        if ($null -eq $value -or $value -isnot [PSCustomObject]) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        if ($Kind -ceq "journal") { Assert-ActivationJournalShape $value }
        elseif ($Kind -ceq "receipt") { Assert-ActivationCompletionReceiptShape $value }
        elseif ($Kind -ceq "migration_journal") { Assert-LegacyMigrationJournalShape $value }
        elseif ($Kind -ceq "migration_receipt") { Assert-LegacyMigrationCompletionReceiptShape $value }
        elseif ($Kind -ceq "rollback_journal") { Assert-RollbackJournalShape $value }
        elseif ($Kind -ceq "rollback_receipt") { Assert-RollbackCompletionReceiptShape $value }
        else { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        if ($text -cne (ConvertTo-CanonicalJson $value)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        return [pscustomobject]@{ value = $value; bytes = $bytes }
    }
    catch {
        if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Write-AtomicCanonicalActivationStateFile(
    [string]$Path,
    [string]$Temporary,
    [object]$Value,
    [string]$Kind,
    [string]$ReplacementBackup
) {
    if ([IO.Directory]::Exists($Path)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    Remove-ReservedActivationTemporary $Temporary
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-CanonicalJson $Value))
    try {
        $stream = New-Object IO.FileStream(
            $Temporary,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        Set-CurrentUserOnlyFileAcl $Temporary
        $temporaryValue = Read-CanonicalActivationStateFile $Temporary $Kind
        try {
            if (-not (Test-ByteArraysEqual $bytes ([byte[]]$temporaryValue.bytes))) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
        }
        finally { [Array]::Clear($temporaryValue.bytes, 0, $temporaryValue.bytes.Length) }
        if ([IO.File]::Exists($Path)) {
            $existing = [JobFlowBootstrapFiles]::OpenLockedRegularFile($Path, $maximumActivationStateBytes)
            try { $existing.VerifyUnchanged() }
            finally { $existing.Dispose() }
            if ([string]::IsNullOrEmpty($ReplacementBackup)) {
                [JobFlowBootstrapFiles]::ReplaceFileWithoutBackup($Temporary, $Path)
            }
            else {
                if ([IO.Directory]::Exists($ReplacementBackup)) {
                    throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
                }
                if ([IO.File]::Exists($ReplacementBackup)) {
                    $anchor = [JobFlowBootstrapFiles]::OpenLockedRegularFile(
                        $ReplacementBackup,
                        $maximumActivationStateBytes
                    )
                    try { $anchor.VerifyUnchanged() }
                    finally { $anchor.Dispose() }
                }
                [IO.File]::Replace($Temporary, $Path, $ReplacementBackup, $true)
            }
        }
        else { [IO.File]::Move($Temporary, $Path) }
        $published = Read-CanonicalActivationStateFile $Path $Kind
        try {
            if (-not (Test-ByteArraysEqual $bytes ([byte[]]$published.bytes))) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
        }
        finally { [Array]::Clear($published.bytes, 0, $published.bytes.Length) }
    }
    catch {
        if ([IO.File]::Exists($Temporary)) {
            try { Remove-ReservedActivationTemporary $Temporary } catch { }
        }
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Test-PointerValueEqual([object]$Left, [object]$Right) {
    if ($null -eq $Left -and $null -eq $Right) { return $true }
    if ($null -eq $Left -or $null -eq $Right) { return $false }
    return (Get-CanonicalJsonSha256 $Left) -ceq (Get-CanonicalJsonSha256 $Right)
}

function Get-ActivationLivePointerState([object]$Layout, [object]$Journal) {
    $current = Read-InstalledPointer ([IO.Path]::Combine([string]$Layout.jobops, "current.json")) $true
    $previous = Read-InstalledPointer ([IO.Path]::Combine([string]$Layout.jobops, "previous.json")) $false
    if ((Test-PointerValueEqual $current $Journal.original_current) -and
        (Test-PointerValueEqual $previous $Journal.original_previous)) {
        return [pscustomobject]@{ state = "ORIGINAL"; current = $current; previous = $previous }
    }
    if ($null -ne $Journal.original_current -and
        (Test-PointerValueEqual $current $Journal.original_current) -and
        (Test-PointerValueEqual $previous $Journal.original_current)) {
        return [pscustomobject]@{ state = "PREVIOUS_ONLY"; current = $current; previous = $previous }
    }
    if ((Test-PointerValueEqual $current $Journal.candidate) -and
        (Test-PointerValueEqual $previous $Journal.original_current)) {
        return [pscustomobject]@{ state = "SWITCHED"; current = $current; previous = $previous }
    }
    return [pscustomobject]@{ state = "IMPOSSIBLE"; current = $current; previous = $previous }
}

function Assert-ActivationJournalRuntimes([object]$Layout, [object]$Journal, [bool]$RequireCandidate) {
    try {
        if ($null -ne $Journal.original_current) {
            [void](Assert-InstalledRuntime ([string]$Layout.versions) $Journal.original_current)
        }
        if ($null -ne $Journal.original_previous) {
            [void](Assert-InstalledRuntime ([string]$Layout.versions) $Journal.original_previous)
        }
        if ($RequireCandidate) {
            [void](Assert-InstalledRuntime ([string]$Layout.versions) $Journal.candidate)
        }
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Test-ActivationStatePathIsSafeRegular([string]$Path) {
    if ([IO.Directory]::Exists($Path) -or -not [IO.File]::Exists($Path)) { return $false }
    $locked = $null
    try {
        $locked = [JobFlowBootstrapFiles]::OpenLockedRegularFile(
            $Path,
            $maximumActivationStateBytes,
            $true
        )
        $locked.VerifyUnchanged()
        return $true
    }
    catch { return $false }
    finally { if ($null -ne $locked) { $locked.Dispose() } }
}

function Read-ActivationJournalPair([object]$Layout) {
    $paths = Get-ActivationStatePaths ([string]$Layout.state)
    Remove-ReservedActivationTemporary ([string]$paths.main_temporary)
    Remove-ReservedActivationTemporary ([string]$paths.backup_temporary)
    if ([IO.Directory]::Exists([string]$paths.main) -or [IO.Directory]::Exists([string]$paths.backup)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    $backupExists = [IO.File]::Exists([string]$paths.backup)
    $mainExists = [IO.File]::Exists([string]$paths.main)
    if (-not $mainExists -and -not $backupExists) { return $null }
    if (-not $backupExists) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $main = $null
    $backup = $null
    try {
        $backup = Read-CanonicalActivationStateFile ([string]$paths.backup) "journal"
        if (-not $mainExists) {
            Write-AtomicCanonicalActivationStateFile ([string]$paths.main) ([string]$paths.main_temporary) $backup.value "journal"
            $main = Read-CanonicalActivationStateFile ([string]$paths.main) "journal"
        }
        else {
            if (-not (Test-ActivationStatePathIsSafeRegular ([string]$paths.main))) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            try { $main = Read-CanonicalActivationStateFile ([string]$paths.main) "journal" }
            catch {
                Write-AtomicCanonicalActivationStateFile `
                    ([string]$paths.main) ([string]$paths.main_temporary) $backup.value "journal"
                $main = Read-CanonicalActivationStateFile ([string]$paths.main) "journal"
            }
        }
        if (Test-ByteArraysEqual ([byte[]]$main.bytes) ([byte[]]$backup.bytes)) { return $main.value }

        if ((Get-ActivationJournalImmutableSha256 $main.value) -cne
            (Get-ActivationJournalImmutableSha256 $backup.value)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        if (([long]$main.value.generation - [long]$backup.value.generation) -ne 1) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        $expectedNewerState = ""
        if ([string]$backup.value.state -ceq "PREPARED") { $expectedNewerState = "PRE_HEALTH_OK" }
        elseif ([string]$backup.value.state -ceq "PRE_HEALTH_OK") { $expectedNewerState = "POINTER_SWITCHED" }
        elseif ([string]$backup.value.state -ceq "POINTER_SWITCHED") { $expectedNewerState = "POST_HEALTH_OK" }
        elseif ([string]$backup.value.state -ceq "POST_HEALTH_OK") { $expectedNewerState = "COMMITTED" }
        if ([string]$main.value.state -cne $expectedNewerState) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        # BACKUP is the durable recovery anchor.  A crash may leave MAIN exactly
        # one generation ahead, but recovery deliberately rolls MAIN back to the
        # anchored generation and then replays the transition from live pointers.
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.main) ([string]$paths.main_temporary) $backup.value "journal"
        $repaired = Read-CanonicalActivationStateFile ([string]$paths.main) "journal"
        try {
            if (-not (Test-ByteArraysEqual ([byte[]]$backup.bytes) ([byte[]]$repaired.bytes))) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
        }
        finally { [Array]::Clear($repaired.bytes, 0, $repaired.bytes.Length) }
        return $backup.value
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    finally {
        if ($null -ne $main -and $null -ne $main.bytes) { [Array]::Clear($main.bytes, 0, $main.bytes.Length) }
        if ($null -ne $backup -and $null -ne $backup.bytes) { [Array]::Clear($backup.bytes, 0, $backup.bytes.Length) }
    }
}

function Write-ActivationJournalPair([object]$Layout, [object]$Value) {
    Assert-ActivationJournalShape $Value
    $paths = Get-ActivationStatePaths ([string]$Layout.state)
    $mainExists = [IO.File]::Exists([string]$paths.main)
    $backupExists = [IO.File]::Exists([string]$paths.backup)
    if (-not $mainExists -and -not $backupExists) {
        if ([long]$Value.generation -ne 1 -or [string]$Value.state -cne "PREPARED") {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.backup) ([string]$paths.backup_temporary) $Value "journal"
        # JOBFLOW_ACTIVATION_JOURNAL_INITIAL_BACKUP_PUBLISHED_BOUNDARY
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.main) ([string]$paths.main_temporary) $Value "journal"
        # JOBFLOW_ACTIVATION_JOURNAL_INITIAL_MAIN_PUBLISHED_BOUNDARY
    }
    else {
        $existing = Read-ActivationJournalPair $Layout
        if ($null -eq $existing -or
            (Get-ActivationJournalImmutableSha256 $existing) -cne
                (Get-ActivationJournalImmutableSha256 $Value) -or
            ([long]$Value.generation - [long]$existing.generation) -ne 1) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        $expectedState = ""
        if ([string]$existing.state -ceq "PREPARED") { $expectedState = "PRE_HEALTH_OK" }
        elseif ([string]$existing.state -ceq "PRE_HEALTH_OK") { $expectedState = "POINTER_SWITCHED" }
        elseif ([string]$existing.state -ceq "POINTER_SWITCHED") { $expectedState = "POST_HEALTH_OK" }
        elseif ([string]$existing.state -ceq "POST_HEALTH_OK") { $expectedState = "COMMITTED" }
        if ([string]$Value.state -cne $expectedState) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.main) ([string]$paths.main_temporary) $Value "journal" ([string]$paths.backup)
        # JOBFLOW_ACTIVATION_JOURNAL_MAIN_ADVANCED_BOUNDARY
        $advancedMain = Read-CanonicalActivationStateFile ([string]$paths.main) "journal"
        $anchoredBackup = Read-CanonicalActivationStateFile ([string]$paths.backup) "journal"
        try {
            if ((Get-CanonicalJsonSha256 $advancedMain.value) -cne (Get-CanonicalJsonSha256 $Value) -or
                (Get-CanonicalJsonSha256 $anchoredBackup.value) -cne (Get-CanonicalJsonSha256 $existing)) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
        }
        finally {
            [Array]::Clear($advancedMain.bytes, 0, $advancedMain.bytes.Length)
            [Array]::Clear($anchoredBackup.bytes, 0, $anchoredBackup.bytes.Length)
        }
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.backup) ([string]$paths.backup_temporary) $Value "journal"
        # JOBFLOW_ACTIVATION_JOURNAL_BACKUP_SYNCHRONIZED_BOUNDARY
    }
    $verified = Read-ActivationJournalPair $Layout
    if ((Get-CanonicalJsonSha256 $verified) -cne (Get-CanonicalJsonSha256 $Value)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Set-ActivationJournalState([object]$Layout, [object]$Value, [string]$State) {
    $generation = 0
    if ($State -ceq "PRE_HEALTH_OK" -and [string]$Value.state -ceq "PREPARED") { $generation = 2 }
    elseif ($State -ceq "POINTER_SWITCHED" -and [string]$Value.state -ceq "PRE_HEALTH_OK") { $generation = 3 }
    elseif ($State -ceq "POST_HEALTH_OK" -and [string]$Value.state -ceq "POINTER_SWITCHED") { $generation = 4 }
    elseif ($State -ceq "COMMITTED" -and [string]$Value.state -ceq "POST_HEALTH_OK") { $generation = 5 }
    else { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $next = New-ActivationJournalEnvelope `
        ([string]$Value.transaction_id) $State $generation ([bool]$Value.candidate_target_was_present) `
        $Value.original_current $Value.original_previous $Value.candidate
    Write-ActivationJournalPair $Layout $next
    return $next
}

function New-ActivationCompletionReceiptSemantic([object]$Journal) {
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ACTIVATION_COMPLETION"
        transaction_id = [string]$Journal.transaction_id
        status = "COMMITTED"
        candidate = $Journal.candidate
    }
}

function New-ActivationCompletionReceipt([object]$Journal) {
    $semantic = New-ActivationCompletionReceiptSemantic $Journal
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ACTIVATION_COMPLETION"
        transaction_id = [string]$Journal.transaction_id
        status = "COMMITTED"
        candidate = $Journal.candidate
        semantic_sha256 = Get-CanonicalJsonSha256 $semantic
    }
}

function Assert-ActivationCompletionReceiptShape([object]$Value) {
    Assert-ExactProperties $Value @(
        "schema_version", "kind", "transaction_id", "status", "candidate", "semantic_sha256"
    )
    if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
        [string]$Value.kind -cne "JOBFLOW_ACTIVATION_COMPLETION" -or
        [string]$Value.transaction_id -cnotmatch '^[0-9a-f]{32}$' -or
        [string]$Value.status -cne "COMMITTED") {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    try { Assert-InstalledPointerShape $Value.candidate; Assert-Sha256 $Value.semantic_sha256 }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $semantic = [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ACTIVATION_COMPLETION"
        transaction_id = [string]$Value.transaction_id
        status = "COMMITTED"
        candidate = $Value.candidate
    }
    if ([string]$Value.semantic_sha256 -cne (Get-CanonicalJsonSha256 $semantic)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Read-ActivationCompletionReceipt([object]$Layout) {
    $paths = Get-ActivationStatePaths ([string]$Layout.state)
    Remove-ReservedActivationTemporary ([string]$paths.receipt_temporary)
    if ([IO.Directory]::Exists([string]$paths.receipt)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    if (-not [IO.File]::Exists([string]$paths.receipt)) { return $null }
    $read = Read-CanonicalActivationStateFile ([string]$paths.receipt) "receipt"
    try { return $read.value }
    finally { [Array]::Clear($read.bytes, 0, $read.bytes.Length) }
}

function Write-ActivationCompletionReceipt([object]$Layout, [object]$Journal) {
    $receipt = New-ActivationCompletionReceipt $Journal
    Assert-ActivationCompletionReceiptShape $receipt
    $paths = Get-ActivationStatePaths ([string]$Layout.state)
    Write-AtomicCanonicalActivationStateFile `
        ([string]$paths.receipt) ([string]$paths.receipt_temporary) $receipt "receipt"
    $verified = Read-ActivationCompletionReceipt $Layout
    if ((Get-CanonicalJsonSha256 $verified) -cne (Get-CanonicalJsonSha256 $receipt)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Remove-ActivationJournalPair([object]$Layout) {
    $paths = Get-ActivationStatePaths ([string]$Layout.state)
    [void](Read-ActivationJournalPair $Layout)
    try {
        [IO.File]::Delete([string]$paths.main)
        # JOBFLOW_ACTIVATION_JOURNAL_MAIN_REMOVED_BOUNDARY
        [IO.File]::Delete([string]$paths.backup)
        # JOBFLOW_ACTIVATION_JOURNAL_BACKUP_REMOVED_BOUNDARY
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    Remove-ReservedActivationTemporary ([string]$paths.main_temporary)
    Remove-ReservedActivationTemporary ([string]$paths.backup_temporary)
}

function Get-LegacyMigrationStatePaths([string]$StateRoot) {
    $root = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $dataRoot = [IO.Path]::GetDirectoryName($root)
    $jobOpsRoot = [IO.Path]::GetDirectoryName($dataRoot)
    if ([string]::IsNullOrEmpty($dataRoot) -or [string]::IsNullOrEmpty($jobOpsRoot)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    return [pscustomobject]@{
        main = [IO.Path]::Combine($root, ".jobflow-v1-v2-migration-transaction-v1.json")
        backup = [IO.Path]::Combine($root, ".jobflow-v1-v2-migration-transaction-v1.backup.json")
        main_temporary = [IO.Path]::Combine($root, ".jobflow-v1-v2-migration-transaction-v1.main.write.tmp")
        backup_temporary = [IO.Path]::Combine($root, ".jobflow-v1-v2-migration-transaction-v1.backup.write.tmp")
        receipt = [IO.Path]::Combine($jobOpsRoot, ".jobflow-v1-v2-migration-completion-v1.json")
        receipt_temporary = [IO.Path]::Combine($jobOpsRoot, ".jobflow-v1-v2-migration-completion-v1.write.tmp")
        current_quarantine = [IO.Path]::Combine($jobOpsRoot, ".jobflow-v1-v2-current.pointer.quarantine")
        previous_quarantine = [IO.Path]::Combine($jobOpsRoot, ".jobflow-v1-v2-previous.pointer.quarantine")
        launcher_quarantine = [IO.Path]::Combine($jobOpsRoot, ".jobflow-v1-v2-launchers.quarantine")
    }
}

function Test-LegacyMigrationArtifactsPresent([string]$JobOpsRoot) {
    $root = (Assert-SafeDirectory $JobOpsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    # Quarantines live outside Data.  Detect them before looking for the state
    # directory so a damaged or missing Data tree cannot make an interrupted
    # forward-only migration look like a fresh installation.
    foreach ($path in @(
        [IO.Path]::Combine($root, ".jobflow-v1-v2-current.pointer.quarantine"),
        [IO.Path]::Combine($root, ".jobflow-v1-v2-previous.pointer.quarantine"),
        [IO.Path]::Combine($root, ".jobflow-v1-v2-launchers.quarantine"),
        [IO.Path]::Combine($root, ".jobflow-v1-v2-migration-completion-v1.write.tmp")
    )) {
        if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) { return $true }
    }
    $state = [IO.Path]::Combine($root, "Data", "state")
    if ([IO.File]::Exists($state) -or -not [IO.Directory]::Exists($state)) { return $false }
    $paths = Get-LegacyMigrationStatePaths $state
    foreach ($path in @(
        [string]$paths.main,
        [string]$paths.backup,
        [string]$paths.main_temporary,
        [string]$paths.backup_temporary
    )) {
        if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) { return $true }
    }
    return $false
}

function Get-ExpectedDataMarkerSha256 {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes('{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}')
    try { return Get-BytesSha256 $bytes }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function New-LegacyMigrationJournalSemantic(
    [string]$TransactionId,
    [string]$State,
    [long]$Generation,
    [bool]$CandidateTargetWasPresent,
    [string]$SignedManifestSha256,
    [object]$OriginalCurrent,
    [object]$OriginalPrevious,
    [object]$Candidate
) {
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_LEGACY_V1_MIGRATION_TRANSACTION"
        transaction_id = $TransactionId
        state = $State
        generation = $Generation
        candidate_target_was_present = $CandidateTargetWasPresent
        signed_manifest_sha256 = $SignedManifestSha256
        data_marker_sha256 = Get-ExpectedDataMarkerSha256
        original_current = $OriginalCurrent
        original_previous = $OriginalPrevious
        candidate = $Candidate
    }
}

function New-LegacyMigrationJournalEnvelope(
    [string]$TransactionId,
    [string]$State,
    [long]$Generation,
    [bool]$CandidateTargetWasPresent,
    [string]$SignedManifestSha256,
    [object]$OriginalCurrent,
    [object]$OriginalPrevious,
    [object]$Candidate
) {
    $semantic = New-LegacyMigrationJournalSemantic `
        $TransactionId $State $Generation $CandidateTargetWasPresent $SignedManifestSha256 `
        $OriginalCurrent $OriginalPrevious $Candidate
    $value = [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_LEGACY_V1_MIGRATION_TRANSACTION"
        transaction_id = $TransactionId
        state = $State
        generation = $Generation
        candidate_target_was_present = $CandidateTargetWasPresent
        signed_manifest_sha256 = $SignedManifestSha256
        data_marker_sha256 = Get-ExpectedDataMarkerSha256
        original_current = $OriginalCurrent
        original_previous = $OriginalPrevious
        candidate = $Candidate
        semantic_sha256 = Get-CanonicalJsonSha256 $semantic
    }
    Assert-LegacyMigrationJournalShape $value
    return $value
}

function Assert-LegacyMigrationJournalShape([object]$Value) {
    Assert-ExactProperties $Value @(
        "schema_version", "kind", "transaction_id", "state", "generation",
        "candidate_target_was_present", "signed_manifest_sha256", "data_marker_sha256",
        "original_current", "original_previous", "candidate", "semantic_sha256"
    )
    if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
        [string]$Value.kind -cne "JOBFLOW_LEGACY_V1_MIGRATION_TRANSACTION" -or
        [string]$Value.transaction_id -cnotmatch '^[0-9a-f]{32}$' -or
        $Value.candidate_target_was_present -isnot [bool]) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    $expectedGeneration = 0
    if ([string]$Value.state -ceq "PREPARED") { $expectedGeneration = 1 }
    elseif ([string]$Value.state -ceq "V1_QUARANTINED") { $expectedGeneration = 2 }
    elseif ([string]$Value.state -ceq "PRE_HEALTH_OK") { $expectedGeneration = 3 }
    elseif ([string]$Value.state -ceq "POINTER_SWITCHED") { $expectedGeneration = 4 }
    elseif ([string]$Value.state -ceq "LAUNCHERS_READY") { $expectedGeneration = 5 }
    elseif ([string]$Value.state -ceq "POST_HEALTH_OK") { $expectedGeneration = 6 }
    elseif ([string]$Value.state -ceq "COMMITTED") { $expectedGeneration = 7 }
    else { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not (Test-JsonInteger $Value.generation $expectedGeneration $expectedGeneration)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    try {
        Assert-Sha256 $Value.signed_manifest_sha256
        Assert-Sha256 $Value.data_marker_sha256
        Assert-Sha256 $Value.semantic_sha256
        Assert-LegacyV1PointerShape $Value.original_current
        if ($null -ne $Value.original_previous) { Assert-LegacyV1PointerShape $Value.original_previous }
        Assert-InstalledPointerShape $Value.candidate
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if ([string]$Value.data_marker_sha256 -cne (Get-ExpectedDataMarkerSha256) -or
        (Compare-SemVer $Value.candidate.version $Value.original_current.version) -le 0 -or
        ($null -ne $Value.original_previous -and
            (Get-CanonicalJsonSha256 $Value.original_previous) -ceq
                (Get-CanonicalJsonSha256 $Value.original_current))) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    $semantic = New-LegacyMigrationJournalSemantic `
        ([string]$Value.transaction_id) ([string]$Value.state) ([long]$Value.generation) `
        ([bool]$Value.candidate_target_was_present) ([string]$Value.signed_manifest_sha256) `
        $Value.original_current $Value.original_previous $Value.candidate
    if ([string]$Value.semantic_sha256 -cne (Get-CanonicalJsonSha256 $semantic)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Get-LegacyMigrationJournalImmutableSha256([object]$Value) {
    $immutable = [pscustomobject][ordered]@{
        schema_version = [long]$Value.schema_version
        kind = [string]$Value.kind
        transaction_id = [string]$Value.transaction_id
        candidate_target_was_present = [bool]$Value.candidate_target_was_present
        signed_manifest_sha256 = [string]$Value.signed_manifest_sha256
        data_marker_sha256 = [string]$Value.data_marker_sha256
        original_current = $Value.original_current
        original_previous = $Value.original_previous
        candidate = $Value.candidate
    }
    return Get-CanonicalJsonSha256 $immutable
}

function Read-LegacyMigrationJournalPair([object]$Layout) {
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    Remove-ReservedActivationTemporary ([string]$paths.main_temporary)
    Remove-ReservedActivationTemporary ([string]$paths.backup_temporary)
    if ([IO.Directory]::Exists([string]$paths.main) -or [IO.Directory]::Exists([string]$paths.backup)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    $mainExists = [IO.File]::Exists([string]$paths.main)
    $backupExists = [IO.File]::Exists([string]$paths.backup)
    if (-not $mainExists -and -not $backupExists) { return $null }
    if (-not $backupExists) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $main = $null
    $backup = $null
    try {
        $backup = Read-CanonicalActivationStateFile ([string]$paths.backup) "migration_journal"
        if (-not $mainExists) {
            Write-AtomicCanonicalActivationStateFile `
                ([string]$paths.main) ([string]$paths.main_temporary) $backup.value "migration_journal"
            $main = Read-CanonicalActivationStateFile ([string]$paths.main) "migration_journal"
        }
        else {
            if (-not (Test-ActivationStatePathIsSafeRegular ([string]$paths.main))) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            try { $main = Read-CanonicalActivationStateFile ([string]$paths.main) "migration_journal" }
            catch {
                Write-AtomicCanonicalActivationStateFile `
                    ([string]$paths.main) ([string]$paths.main_temporary) $backup.value "migration_journal"
                $main = Read-CanonicalActivationStateFile ([string]$paths.main) "migration_journal"
            }
        }
        if (Test-ByteArraysEqual ([byte[]]$main.bytes) ([byte[]]$backup.bytes)) { return $main.value }
        if ((Get-LegacyMigrationJournalImmutableSha256 $main.value) -cne
                (Get-LegacyMigrationJournalImmutableSha256 $backup.value) -or
            ([long]$main.value.generation - [long]$backup.value.generation) -ne 1) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        $expectedNewerState = ""
        if ([string]$backup.value.state -ceq "PREPARED") { $expectedNewerState = "V1_QUARANTINED" }
        elseif ([string]$backup.value.state -ceq "V1_QUARANTINED") { $expectedNewerState = "PRE_HEALTH_OK" }
        elseif ([string]$backup.value.state -ceq "PRE_HEALTH_OK") { $expectedNewerState = "POINTER_SWITCHED" }
        elseif ([string]$backup.value.state -ceq "POINTER_SWITCHED") { $expectedNewerState = "LAUNCHERS_READY" }
        elseif ([string]$backup.value.state -ceq "LAUNCHERS_READY") { $expectedNewerState = "POST_HEALTH_OK" }
        elseif ([string]$backup.value.state -ceq "POST_HEALTH_OK") { $expectedNewerState = "COMMITTED" }
        if ([string]$main.value.state -cne $expectedNewerState) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.main) ([string]$paths.main_temporary) $backup.value "migration_journal"
        $repaired = Read-CanonicalActivationStateFile ([string]$paths.main) "migration_journal"
        try {
            if (-not (Test-ByteArraysEqual ([byte[]]$backup.bytes) ([byte[]]$repaired.bytes))) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
        }
        finally { [Array]::Clear($repaired.bytes, 0, $repaired.bytes.Length) }
        return $backup.value
    }
    finally {
        if ($null -ne $main -and $null -ne $main.bytes) { [Array]::Clear($main.bytes, 0, $main.bytes.Length) }
        if ($null -ne $backup -and $null -ne $backup.bytes) { [Array]::Clear($backup.bytes, 0, $backup.bytes.Length) }
    }
}

function Write-LegacyMigrationJournalPair([object]$Layout, [object]$Value) {
    Assert-LegacyMigrationJournalShape $Value
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    $mainExists = [IO.File]::Exists([string]$paths.main)
    $backupExists = [IO.File]::Exists([string]$paths.backup)
    if (-not $mainExists -and -not $backupExists) {
        if ([long]$Value.generation -ne 1 -or [string]$Value.state -cne "PREPARED") {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.backup) ([string]$paths.backup_temporary) $Value "migration_journal"
        # JOBFLOW_LEGACY_MIGRATION_JOURNAL_INITIAL_BACKUP_PUBLISHED_BOUNDARY
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.main) ([string]$paths.main_temporary) $Value "migration_journal"
        # JOBFLOW_LEGACY_MIGRATION_JOURNAL_INITIAL_MAIN_PUBLISHED_BOUNDARY
    }
    else {
        $existing = Read-LegacyMigrationJournalPair $Layout
        if ($null -eq $existing -or
            (Get-LegacyMigrationJournalImmutableSha256 $existing) -cne
                (Get-LegacyMigrationJournalImmutableSha256 $Value) -or
            ([long]$Value.generation - [long]$existing.generation) -ne 1) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        $expectedState = ""
        if ([string]$existing.state -ceq "PREPARED") { $expectedState = "V1_QUARANTINED" }
        elseif ([string]$existing.state -ceq "V1_QUARANTINED") { $expectedState = "PRE_HEALTH_OK" }
        elseif ([string]$existing.state -ceq "PRE_HEALTH_OK") { $expectedState = "POINTER_SWITCHED" }
        elseif ([string]$existing.state -ceq "POINTER_SWITCHED") { $expectedState = "LAUNCHERS_READY" }
        elseif ([string]$existing.state -ceq "LAUNCHERS_READY") { $expectedState = "POST_HEALTH_OK" }
        elseif ([string]$existing.state -ceq "POST_HEALTH_OK") { $expectedState = "COMMITTED" }
        if ([string]$Value.state -cne $expectedState) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.main) ([string]$paths.main_temporary) $Value "migration_journal" ([string]$paths.backup)
        $advancedMain = Read-CanonicalActivationStateFile ([string]$paths.main) "migration_journal"
        $anchoredBackup = Read-CanonicalActivationStateFile ([string]$paths.backup) "migration_journal"
        try {
            if ((Get-CanonicalJsonSha256 $advancedMain.value) -cne (Get-CanonicalJsonSha256 $Value) -or
                (Get-CanonicalJsonSha256 $anchoredBackup.value) -cne (Get-CanonicalJsonSha256 $existing)) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
        }
        finally {
            [Array]::Clear($advancedMain.bytes, 0, $advancedMain.bytes.Length)
            [Array]::Clear($anchoredBackup.bytes, 0, $anchoredBackup.bytes.Length)
        }
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.backup) ([string]$paths.backup_temporary) $Value "migration_journal"
    }
    $verified = Read-LegacyMigrationJournalPair $Layout
    if ((Get-CanonicalJsonSha256 $verified) -cne (Get-CanonicalJsonSha256 $Value)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Set-LegacyMigrationJournalState([object]$Layout, [object]$Value, [string]$State) {
    $generation = 0
    if ($State -ceq "V1_QUARANTINED" -and [string]$Value.state -ceq "PREPARED") { $generation = 2 }
    elseif ($State -ceq "PRE_HEALTH_OK" -and [string]$Value.state -ceq "V1_QUARANTINED") { $generation = 3 }
    elseif ($State -ceq "POINTER_SWITCHED" -and [string]$Value.state -ceq "PRE_HEALTH_OK") { $generation = 4 }
    elseif ($State -ceq "LAUNCHERS_READY" -and [string]$Value.state -ceq "POINTER_SWITCHED") { $generation = 5 }
    elseif ($State -ceq "POST_HEALTH_OK" -and [string]$Value.state -ceq "LAUNCHERS_READY") { $generation = 6 }
    elseif ($State -ceq "COMMITTED" -and [string]$Value.state -ceq "POST_HEALTH_OK") { $generation = 7 }
    else { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $next = New-LegacyMigrationJournalEnvelope `
        ([string]$Value.transaction_id) $State $generation ([bool]$Value.candidate_target_was_present) `
        ([string]$Value.signed_manifest_sha256) $Value.original_current $Value.original_previous $Value.candidate
    Write-LegacyMigrationJournalPair $Layout $next
    return $next
}

function New-LegacyMigrationCompletionReceiptSemantic([object]$Journal) {
    # This builder is used both before publication (with the migration journal)
    # and while validating the published receipt.  Normalize the predecessor
    # field explicitly so the canonical semantic hash is identical in both
    # representations.
    $predecessor = if ($null -ne $Journal.PSObject.Properties["predecessor"]) {
        $Journal.predecessor
    }
    else {
        $Journal.original_current
    }
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_LEGACY_V1_MIGRATION_COMPLETION"
        transaction_id = [string]$Journal.transaction_id
        status = "COMMITTED"
        signed_manifest_sha256 = [string]$Journal.signed_manifest_sha256
        predecessor = $predecessor
        candidate = $Journal.candidate
    }
}

function New-LegacyMigrationCompletionReceipt([object]$Journal) {
    $semantic = New-LegacyMigrationCompletionReceiptSemantic $Journal
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_LEGACY_V1_MIGRATION_COMPLETION"
        transaction_id = [string]$Journal.transaction_id
        status = "COMMITTED"
        signed_manifest_sha256 = [string]$Journal.signed_manifest_sha256
        predecessor = $Journal.original_current
        candidate = $Journal.candidate
        semantic_sha256 = Get-CanonicalJsonSha256 $semantic
    }
}

function Assert-LegacyMigrationCompletionReceiptShape([object]$Value) {
    Assert-ExactProperties $Value @(
        "schema_version", "kind", "transaction_id", "status", "signed_manifest_sha256",
        "predecessor", "candidate", "semantic_sha256"
    )
    if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
        [string]$Value.kind -cne "JOBFLOW_LEGACY_V1_MIGRATION_COMPLETION" -or
        [string]$Value.transaction_id -cnotmatch '^[0-9a-f]{32}$' -or
        [string]$Value.status -cne "COMMITTED") {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    try {
        Assert-Sha256 $Value.signed_manifest_sha256
        Assert-Sha256 $Value.semantic_sha256
        Assert-LegacyV1PointerShape $Value.predecessor
        Assert-InstalledPointerShape $Value.candidate
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $semantic = New-LegacyMigrationCompletionReceiptSemantic $Value
    if ([string]$Value.semantic_sha256 -cne (Get-CanonicalJsonSha256 $semantic)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Read-LegacyMigrationCompletionReceipt([object]$Layout) {
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    Remove-ReservedActivationTemporary ([string]$paths.receipt_temporary)
    if ([IO.Directory]::Exists([string]$paths.receipt)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not [IO.File]::Exists([string]$paths.receipt)) { return $null }
    $read = Read-CanonicalActivationStateFile ([string]$paths.receipt) "migration_receipt"
    try { return $read.value }
    finally { [Array]::Clear($read.bytes, 0, $read.bytes.Length) }
}

function Write-LegacyMigrationCompletionReceipt([object]$Layout, [object]$Journal) {
    $receipt = New-LegacyMigrationCompletionReceipt $Journal
    Assert-LegacyMigrationCompletionReceiptShape $receipt
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    Write-AtomicCanonicalActivationStateFile `
        ([string]$paths.receipt) ([string]$paths.receipt_temporary) $receipt "migration_receipt"
    $verified = Read-LegacyMigrationCompletionReceipt $Layout
    if ((Get-CanonicalJsonSha256 $verified) -cne (Get-CanonicalJsonSha256 $receipt)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Remove-LegacyMigrationJournalPair([object]$Layout) {
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    [void](Read-LegacyMigrationJournalPair $Layout)
    try {
        [IO.File]::Delete([string]$paths.main)
        # JOBFLOW_LEGACY_MIGRATION_JOURNAL_MAIN_REMOVED_BOUNDARY
        [IO.File]::Delete([string]$paths.backup)
        # JOBFLOW_LEGACY_MIGRATION_JOURNAL_BACKUP_REMOVED_BOUNDARY
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    Remove-ReservedActivationTemporary ([string]$paths.main_temporary)
    Remove-ReservedActivationTemporary ([string]$paths.backup_temporary)
}

function Read-ExactLegacyMigrationPointerFile([string]$Path, [object]$Expected) {
    if ([IO.Directory]::Exists($Path)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not [IO.File]::Exists($Path)) { return $null }
    try { $value = Read-LegacyV1Pointer $Path }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not (Test-PointerValueEqual $value $Expected)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    return $value
}

function Get-LegacyMigrationLivePointerState([object]$Layout, [object]$Journal) {
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    $currentPath = [IO.Path]::Combine([string]$Layout.jobops, "current.json")
    $previousPath = [IO.Path]::Combine([string]$Layout.jobops, "previous.json")
    try {
        $currentRecord = Read-InstalledPointerForMigration $currentPath $false
        $previousRecord = Read-InstalledPointerForMigration $previousPath $false
        $quarantinedCurrent = Read-ExactLegacyMigrationPointerFile `
            ([string]$paths.current_quarantine) $Journal.original_current
        $quarantinedPrevious = $null
        if ($null -ne $Journal.original_previous) {
            $quarantinedPrevious = Read-ExactLegacyMigrationPointerFile `
                ([string]$paths.previous_quarantine) $Journal.original_previous
        }
        elseif ([IO.File]::Exists([string]$paths.previous_quarantine) -or
            [IO.Directory]::Exists([string]$paths.previous_quarantine)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }

        $current = if ($null -eq $currentRecord) { $null } else { $currentRecord.pointer }
        $previous = if ($null -eq $previousRecord) { $null } else { $previousRecord.pointer }
        $currentIsOriginal = $null -ne $currentRecord -and
            [long]$currentRecord.schema_version -eq 1 -and
            (Test-PointerValueEqual $current $Journal.original_current)
        $previousIsOriginal = if ($null -eq $Journal.original_previous) {
            $null -eq $previousRecord
        }
        else {
            $null -ne $previousRecord -and [long]$previousRecord.schema_version -eq 1 -and
                (Test-PointerValueEqual $previous $Journal.original_previous)
        }
        $currentIsCandidate = $null -ne $currentRecord -and
            [long]$currentRecord.schema_version -eq 2 -and
            (Test-PointerValueEqual $current $Journal.candidate)
        $currentQuarantined = $null -ne $quarantinedCurrent
        $previousQuarantinePresent = $null -ne $quarantinedPrevious
        $expectedPreviousQuarantinePresent = $null -ne $Journal.original_previous

        if ($currentIsOriginal -and $previousIsOriginal -and
            -not $currentQuarantined -and -not $previousQuarantinePresent) {
            return [pscustomobject]@{ state = "ORIGINAL"; current = $current; previous = $previous }
        }
        # CURRENT_QUARANTINED is an intermediate state only when an original
        # previous pointer still has to be moved.  With no original previous
        # pointer, the same physical layout is already V1_QUARANTINED.
        if ($null -ne $Journal.original_previous -and
            $null -eq $currentRecord -and $previousIsOriginal -and
            $currentQuarantined -and -not $previousQuarantinePresent) {
            return [pscustomobject]@{ state = "CURRENT_QUARANTINED"; current = $null; previous = $previous }
        }
        if ($null -eq $currentRecord -and $null -eq $previousRecord -and
            $currentQuarantined -and
            $previousQuarantinePresent -eq $expectedPreviousQuarantinePresent) {
            return [pscustomobject]@{ state = "V1_QUARANTINED"; current = $null; previous = $null }
        }
        if ($currentIsCandidate -and $null -eq $previousRecord -and
            $currentQuarantined -and
            $previousQuarantinePresent -eq $expectedPreviousQuarantinePresent) {
            return [pscustomobject]@{ state = "CANDIDATE_PUBLISHED"; current = $current; previous = $null }
        }
        return [pscustomobject]@{ state = "IMPOSSIBLE"; current = $current; previous = $previous }
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Move-LegacyV1PointersToQuarantine([object]$Layout, [object]$Journal) {
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    $currentPath = [IO.Path]::Combine([string]$Layout.jobops, "current.json")
    $previousPath = [IO.Path]::Combine([string]$Layout.jobops, "previous.json")
    $live = Get-LegacyMigrationLivePointerState $Layout $Journal
    if ([string]$live.state -ceq "ORIGINAL") {
        if ([IO.File]::Exists([string]$paths.current_quarantine) -or
            [IO.Directory]::Exists([string]$paths.current_quarantine)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        try { [IO.File]::Move($currentPath, [string]$paths.current_quarantine) }
        catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        # JOBFLOW_LEGACY_MIGRATION_CURRENT_QUARANTINED_BOUNDARY
        $live = Get-LegacyMigrationLivePointerState $Layout $Journal
    }
    if ([string]$live.state -ceq "CURRENT_QUARANTINED") {
        if ($null -ne $Journal.original_previous) {
            if ([IO.File]::Exists([string]$paths.previous_quarantine) -or
                [IO.Directory]::Exists([string]$paths.previous_quarantine)) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            try { [IO.File]::Move($previousPath, [string]$paths.previous_quarantine) }
            catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
            # JOBFLOW_LEGACY_MIGRATION_PREVIOUS_QUARANTINED_BOUNDARY
        }
        $live = Get-LegacyMigrationLivePointerState $Layout $Journal
    }
    if ([string]$live.state -cne "V1_QUARANTINED") {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Publish-LegacyMigrationCandidatePointer([object]$Layout, [object]$Journal) {
    $live = Get-LegacyMigrationLivePointerState $Layout $Journal
    if ([string]$live.state -ceq "CANDIDATE_PUBLISHED") { return }
    if ([string]$live.state -cne "V1_QUARANTINED") {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    $currentPath = [IO.Path]::Combine([string]$Layout.jobops, "current.json")
    $temporary = $null
    try {
        $temporary = Write-AtomicPointerTemporary ([string]$Layout.jobops) $Journal.candidate
        [IO.File]::Move($temporary, $currentPath)
        $temporary = $null
        # JOBFLOW_LEGACY_MIGRATION_V2_POINTER_PUBLISHED_BOUNDARY
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    finally {
        if (-not [string]::IsNullOrEmpty($temporary) -and [IO.File]::Exists($temporary)) {
            try { [IO.File]::Delete($temporary) } catch { }
        }
    }
    $live = Get-LegacyMigrationLivePointerState $Layout $Journal
    if ([string]$live.state -cne "CANDIDATE_PUBLISHED") {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
}

function Remove-LegacyMigrationPointerQuarantine([object]$Layout, [object]$Journal) {
    if ([string]$Journal.state -cne "COMMITTED") { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    foreach ($entry in @(
        [pscustomobject]@{ path = [string]$paths.current_quarantine; value = $Journal.original_current },
        [pscustomobject]@{ path = [string]$paths.previous_quarantine; value = $Journal.original_previous }
    )) {
        if ($null -eq $entry.value) {
            if ([IO.File]::Exists([string]$entry.path) -or [IO.Directory]::Exists([string]$entry.path)) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            continue
        }
        if ([IO.File]::Exists([string]$entry.path)) {
            [void](Read-ExactLegacyMigrationPointerFile ([string]$entry.path) $entry.value)
            try { [IO.File]::Delete([string]$entry.path) }
            catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        }
        elseif ([IO.Directory]::Exists([string]$entry.path)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
    }
}

function Get-LegacyMigrationLauncherInventory([object]$Layout, [object]$Journal) {
    $target = [IO.Path]::Combine([string]$Layout.versions, [string]$Journal.candidate.version_directory)
    [void](Assert-InstalledRuntime ([string]$Layout.versions) $Journal.candidate)
    $sourceRoot = [IO.Path]::Combine($target, "scripts", "windows-runtime")
    $sourceRoot = (Assert-SafeDirectory $sourceRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $records = New-Object 'Collections.Generic.List[object]'
    $index = 0
    foreach ($name in @(
        "jobflow-bootstrap.ps1",
        "start-installed-jobflow.ps1", "check-installed-jobflow.ps1",
        "update-installed-jobflow.ps1", "rollback-installed-jobflow.ps1",
        "uninstall-installed-jobflow.ps1", "jobflow-runtime-locks.ps1",
        "manage-authorized-discovery-task.ps1", "run-authorized-discovery-task.ps1"
    )) {
        $source = [IO.Path]::Combine($sourceRoot, $name)
        if ([IO.Directory]::Exists($source) -or -not [IO.File]::Exists($source)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        $destination = [IO.Path]::Combine([string]$Layout.jobops, "bin", $name)
        [void]$records.Add([pscustomobject]@{
            index = $index; name = $name; source = $source; destination = $destination
        })
        $index++
    }
    foreach ($name in @(
        "Start JobFlow.cmd", "Check JobFlow.cmd", "Update JobFlow.cmd",
        "Rollback JobFlow.cmd", "Uninstall JobFlow.cmd"
    )) {
        $source = [IO.Path]::Combine($sourceRoot, $name)
        if ([IO.Directory]::Exists($source) -or -not [IO.File]::Exists($source)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        $destination = [IO.Path]::Combine([string]$Layout.jobops, $name)
        [void]$records.Add([pscustomobject]@{
            index = $index; name = $name; source = $source; destination = $destination
        })
        $index++
    }
    # PowerShell 5.1 can throw "Argument types do not match" while adapting a
    # generic List[object] through the array subexpression operator.  Materialize
    # an object array explicitly so migration recovery is deterministic on the
    # Windows PowerShell runtime used by the stable bootstrap.
    return $records.ToArray()
}

function Get-LockedRegularFileIdentity([string]$Path, [long]$MaximumBytes) {
    $locked = $null
    try {
        $locked = [JobFlowBootstrapFiles]::OpenLockedRegularFile($Path, $MaximumBytes, $true)
        $sha = Get-StreamSha256 $locked.Stream
        $length = [long]$locked.Length
        $locked.VerifyUnchanged()
        return [pscustomobject]@{ sha256 = $sha; length = $length }
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    finally { if ($null -ne $locked) { $locked.Dispose() } }
}

function Test-LegacyMigrationLauncherMatches([string]$Source, [string]$Destination) {
    if ([IO.Directory]::Exists($Destination) -or -not [IO.File]::Exists($Destination)) { return $false }
    try {
        $sourceIdentity = Get-LockedRegularFileIdentity $Source 4194304
        $destinationIdentity = Get-LockedRegularFileIdentity $Destination 4194304
        return [long]$sourceIdentity.length -eq [long]$destinationIdentity.length -and
            [string]$sourceIdentity.sha256 -ceq [string]$destinationIdentity.sha256
    }
    catch { return $false }
}

function Copy-LegacyMigrationLauncherForward(
    [string]$Source,
    [string]$Destination,
    [string]$Temporary
) {
    $sourceLock = $null
    $target = $null
    try {
        if ([IO.File]::Exists($Temporary) -or [IO.Directory]::Exists($Temporary)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        $sourceLock = [JobFlowBootstrapFiles]::OpenLockedRegularFile($Source, 4194304, $true)
        $target = [IO.File]::Open(
            $Temporary,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
        $sourceLock.Stream.Position = 0
        $sourceLock.Stream.CopyTo($target)
        $target.Flush($true)
        $target.Dispose()
        $target = $null
        $sourceLock.VerifyUnchanged()
        Set-CurrentUserOnlyFileAcl $Temporary
        $sourceIdentity = [pscustomobject]@{
            sha256 = Get-StreamSha256 $sourceLock.Stream
            length = [long]$sourceLock.Length
        }
        $temporaryIdentity = Get-LockedRegularFileIdentity $Temporary 4194304
        if ([long]$sourceIdentity.length -ne [long]$temporaryIdentity.length -or
            [string]$sourceIdentity.sha256 -cne [string]$temporaryIdentity.sha256) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        [IO.File]::Move($Temporary, $Destination)
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    finally {
        if ($null -ne $target) { $target.Dispose() }
        if ($null -ne $sourceLock) { $sourceLock.Dispose() }
        if ([IO.File]::Exists($Temporary)) { try { [IO.File]::Delete($Temporary) } catch { } }
    }
}

function Install-AndVerifyLegacyMigrationLaunchers([object]$Layout, [object]$Journal) {
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    $quarantine = [string]$paths.launcher_quarantine
    if ([IO.File]::Exists($quarantine)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not [IO.Directory]::Exists($quarantine)) {
        try {
            [JobFlowBootstrapFiles]::CreateNewDirectory($quarantine)
            Set-CurrentUserOnlyDirectoryAcl $quarantine
        }
        catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    }
    $quarantine = Assert-SafeDirectory $quarantine
    $bin = [IO.Path]::Combine([string]$Layout.jobops, "bin")
    if ([IO.File]::Exists($bin)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not [IO.Directory]::Exists($bin)) {
        try {
            [JobFlowBootstrapFiles]::CreateNewDirectory($bin)
            Set-CurrentUserOnlyDirectoryAcl $bin
        }
        catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    }
    [void](Assert-SafeDirectory $bin)

    foreach ($record in @(Get-LegacyMigrationLauncherInventory $Layout $Journal)) {
        $temporary = [string]$record.destination + "." + [string]$Journal.transaction_id + ".write.tmp"
        if ([IO.Directory]::Exists($temporary)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        if ([IO.File]::Exists($temporary)) {
            if (-not (Test-LegacyMigrationLauncherMatches ([string]$record.source) $temporary)) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            if (Test-LegacyMigrationLauncherMatches ([string]$record.source) ([string]$record.destination)) {
                try { [IO.File]::Delete($temporary) }
                catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
            }
            elseif (-not [IO.File]::Exists([string]$record.destination)) {
                try { [IO.File]::Move($temporary, [string]$record.destination) }
                catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
            }
            else { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        }
        if (Test-LegacyMigrationLauncherMatches ([string]$record.source) ([string]$record.destination)) {
            continue
        }
        $backup = [IO.Path]::Combine($quarantine, ("{0:D2}-{1}" -f [int]$record.index, [string]$record.name))
        if ([IO.Directory]::Exists([string]$record.destination) -or [IO.Directory]::Exists($backup)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        if ([IO.File]::Exists([string]$record.destination)) {
            if ([IO.File]::Exists($backup)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
            [void](Get-LockedRegularFileIdentity ([string]$record.destination) 4194304)
            try { [IO.File]::Move([string]$record.destination, $backup) }
            catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
            # JOBFLOW_LEGACY_MIGRATION_OLD_LAUNCHER_QUARANTINED_BOUNDARY
        }
        Copy-LegacyMigrationLauncherForward ([string]$record.source) ([string]$record.destination) $temporary
        # JOBFLOW_LEGACY_MIGRATION_V2_LAUNCHER_PUBLISHED_BOUNDARY
    }
    foreach ($record in @(Get-LegacyMigrationLauncherInventory $Layout $Journal)) {
        if (-not (Test-LegacyMigrationLauncherMatches ([string]$record.source) ([string]$record.destination))) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
    }
}

function Remove-LegacyMigrationLauncherQuarantine([object]$Layout, [object]$Journal) {
    if ([string]$Journal.state -cne "COMMITTED") { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    $paths = Get-LegacyMigrationStatePaths ([string]$Layout.state)
    $quarantine = [string]$paths.launcher_quarantine
    if ([IO.File]::Exists($quarantine)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if ([IO.Directory]::Exists($quarantine)) {
        try { [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded($quarantine, 32, 54525952) }
        catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    }
}

function Remove-BoundedPointerTransactionArtifacts([string]$JobOpsRoot) {
    $root = (Assert-SafeDirectory $JobOpsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $artifacts = New-Object 'Collections.Generic.List[string]'
    foreach ($path in [IO.Directory]::EnumerateFiles($root, "pointer-*.tmp", [IO.SearchOption]::TopDirectoryOnly)) {
        $leaf = [IO.Path]::GetFileName($path)
        if ($leaf -cmatch '^pointer-[0-9a-f]{32}\.tmp$' -or
            $leaf -cmatch '^pointer-(current|previous)-backup-[0-9a-f]{32}\.tmp$') {
            [void]$artifacts.Add([string]$path)
        }
    }
    if ($artifacts.Count -gt 16) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    foreach ($path in $artifacts) {
        $locked = $null
        try {
            $locked = [JobFlowBootstrapFiles]::OpenLockedRegularFile($path, 65536, $true)
            $locked.VerifyUnchanged()
        }
        catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        finally { if ($null -ne $locked) { $locked.Dispose() } }
        try { [IO.File]::Delete($path) }
        catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    }
}

function Restore-OriginalPointerPair([object]$Layout, [object]$Journal) {
    $jobOpsRoot = [string]$Layout.jobops
    $currentPath = [IO.Path]::Combine($jobOpsRoot, "current.json")
    $previousPath = [IO.Path]::Combine($jobOpsRoot, "previous.json")
    $liveCurrent = Read-InstalledPointer $currentPath $true
    $livePrevious = Read-InstalledPointer $previousPath $false
    if (-not (Test-PointerValueEqual $liveCurrent $Journal.original_current)) {
        if ($null -ne $Journal.original_current) {
            $temporary = Write-AtomicPointerTemporary $jobOpsRoot $Journal.original_current
            if ([IO.File]::Exists($currentPath)) {
                [JobFlowBootstrapFiles]::ReplaceFileWithoutBackup($temporary, $currentPath)
            }
            else { [IO.File]::Move($temporary, $currentPath) }
        }
        elseif ([IO.File]::Exists($currentPath)) { [IO.File]::Delete($currentPath) }
    }
    if (-not (Test-PointerValueEqual $livePrevious $Journal.original_previous)) {
        if ($null -ne $Journal.original_previous) {
            $temporary = Write-AtomicPointerTemporary $jobOpsRoot $Journal.original_previous
            if ([IO.File]::Exists($previousPath)) {
                [JobFlowBootstrapFiles]::ReplaceFileWithoutBackup($temporary, $previousPath)
            }
            else { [IO.File]::Move($temporary, $previousPath) }
        }
        elseif ([IO.File]::Exists($previousPath)) { [IO.File]::Delete($previousPath) }
    }
    $live = Get-ActivationLivePointerState $Layout $Journal
    if ([string]$live.state -cne "ORIGINAL") { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Remove-OwnedCandidateIfUnreferenced([object]$Layout, [object]$Journal) {
    if ([bool]$Journal.candidate_target_was_present) { return }
    $target = [IO.Path]::Combine([string]$Layout.versions, [string]$Journal.candidate.version_directory)
    if (-not [IO.Directory]::Exists($target)) { return }
    try { [void](Assert-InstalledRuntime ([string]$Layout.versions) $Journal.candidate) }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not (Test-CandidateTargetIsProvablyUnreferenced `
        ([string]$Layout.jobops) ([string]$Layout.versions) $Journal.candidate)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    try {
        [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
            $target,
            $maximumExtractedTreeEntries,
            $maximumExtractedTreeBytes
        )
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Remove-OwnedActivationTrustEvidenceIfUncommitted([object]$Layout, [object]$Journal) {
    if ([bool]$Journal.candidate_target_was_present) { return }
    $directory = Get-ActivationTrustVersionPath $Layout $Journal.candidate $false
    if ([IO.File]::Exists($directory)) { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
    if (-not [IO.Directory]::Exists($directory)) { return }
    try {
        [void](Assert-ActivationTrustEvidenceForPointer `
            $Layout $Journal.candidate ([string]$Journal.transaction_id))
        [JobFlowBootstrapFiles]::DeleteDirectoryTreeNoFollowBounded(
            $directory,
            $maximumActivationTrustEntries,
            $maximumActivationTrustBytes
        )
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Assert-NoLegacyMigrationConflictingTransactions([object]$Layout) {
    $activation = Get-ActivationStatePaths ([string]$Layout.state)
    foreach ($path in @(
        [string]$activation.main,
        [string]$activation.backup,
        [IO.Path]::Combine([string]$Layout.jobops, ".rollback-pointer-transaction.json"),
        [IO.Path]::Combine([string]$Layout.jobops, ".rollback-pointer-transaction.backup.json")
    )) {
        if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
    }
}

function Assert-LegacyMigrationCandidateReady([object]$Layout, [object]$Journal) {
    try { [void](Assert-InstalledRuntime ([string]$Layout.versions) $Journal.candidate) }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Recover-PendingLegacyMigration([object]$Layout) {
    try {
        Assert-NoLegacyMigrationConflictingTransactions $Layout
        $journal = Read-LegacyMigrationJournalPair $Layout
        if ($null -eq $journal) {
            return [pscustomobject]@{ recovered = $false; activation_committed = $false }
        }
        [void](Assert-ActivationTrustEvidenceForPointer `
            $Layout $journal.candidate ([string]$journal.transaction_id))
        Assert-LegacyMigrationCandidateReady $Layout $journal
        Read-AndValidateDataMarker ([IO.Path]::Combine([string]$Layout.data, ".jobflow-data-root"))

        if ([string]$journal.state -ceq "COMMITTED") {
            $current = Read-InstalledPointer `
                ([IO.Path]::Combine([string]$Layout.jobops, "current.json")) $true
            $previous = Read-InstalledPointer `
                ([IO.Path]::Combine([string]$Layout.jobops, "previous.json")) $false
            if (-not (Test-PointerValueEqual $current $journal.candidate) -or $null -ne $previous) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            Install-AndVerifyLegacyMigrationLaunchers $Layout $journal
            Write-LegacyMigrationCompletionReceipt $Layout $journal
            Remove-LegacyMigrationPointerQuarantine $Layout $journal
            Remove-LegacyMigrationLauncherQuarantine $Layout $journal
            Remove-RuntimeHealthTransactionTemporary $Layout ([string]$journal.transaction_id)
            Remove-LegacyMigrationJournalPair $Layout
            Remove-BoundedPointerTransactionArtifacts ([string]$Layout.jobops)
            return [pscustomobject]@{ recovered = $true; activation_committed = $true }
        }

        $live = Get-LegacyMigrationLivePointerState $Layout $journal
        if ([string]$live.state -ceq "IMPOSSIBLE") { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }

        if ([string]$journal.state -ceq "PREPARED") {
            if ([string]$live.state -in @("ORIGINAL", "CURRENT_QUARANTINED", "V1_QUARANTINED")) {
                if ([string]$live.state -ceq "ORIGINAL") {
                    [void](Assert-LegacyV1InstalledSourceIdentity `
                        ([string]$Layout.versions) $journal.original_current)
                    if ($null -ne $journal.original_previous) {
                        [void](Assert-LegacyV1InstalledSourceIdentity `
                            ([string]$Layout.versions) $journal.original_previous)
                    }
                }
                Move-LegacyV1PointersToQuarantine $Layout $journal
                $journal = Set-LegacyMigrationJournalState $Layout $journal "V1_QUARANTINED"
                # JOBFLOW_LEGACY_MIGRATION_V1_QUARANTINED_STATE_BOUNDARY
            }
            else { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        }

        if ([string]$journal.state -ceq "V1_QUARANTINED") {
            $live = Get-LegacyMigrationLivePointerState $Layout $journal
            if ([string]$live.state -cne "V1_QUARANTINED") {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            Invoke-CandidateRuntimeHealth $Layout $journal.candidate ([string]$journal.transaction_id) "pre"
            # JOBFLOW_LEGACY_MIGRATION_PRE_HEALTH_COMPLETED_BOUNDARY
            $journal = Set-LegacyMigrationJournalState $Layout $journal "PRE_HEALTH_OK"
            # JOBFLOW_LEGACY_MIGRATION_PRE_HEALTH_OK_STATE_BOUNDARY
        }

        if ([string]$journal.state -ceq "PRE_HEALTH_OK") {
            $live = Get-LegacyMigrationLivePointerState $Layout $journal
            if ([string]$live.state -ceq "V1_QUARANTINED") {
                Publish-LegacyMigrationCandidatePointer $Layout $journal
            }
            elseif ([string]$live.state -cne "CANDIDATE_PUBLISHED") {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            $journal = Set-LegacyMigrationJournalState $Layout $journal "POINTER_SWITCHED"
            # JOBFLOW_LEGACY_MIGRATION_POINTER_SWITCHED_STATE_BOUNDARY
        }

        if ([string]$journal.state -ceq "POINTER_SWITCHED") {
            $live = Get-LegacyMigrationLivePointerState $Layout $journal
            if ([string]$live.state -cne "CANDIDATE_PUBLISHED") {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            Install-AndVerifyLegacyMigrationLaunchers $Layout $journal
            $journal = Set-LegacyMigrationJournalState $Layout $journal "LAUNCHERS_READY"
            # JOBFLOW_LEGACY_MIGRATION_LAUNCHERS_READY_STATE_BOUNDARY
        }

        if ([string]$journal.state -ceq "LAUNCHERS_READY") {
            $live = Get-LegacyMigrationLivePointerState $Layout $journal
            if ([string]$live.state -cne "CANDIDATE_PUBLISHED") {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            Install-AndVerifyLegacyMigrationLaunchers $Layout $journal
            Invoke-CandidateRuntimeHealth $Layout $journal.candidate ([string]$journal.transaction_id) "post"
            # JOBFLOW_LEGACY_MIGRATION_POST_HEALTH_COMPLETED_BOUNDARY
            $journal = Set-LegacyMigrationJournalState $Layout $journal "POST_HEALTH_OK"
            # JOBFLOW_LEGACY_MIGRATION_POST_HEALTH_OK_STATE_BOUNDARY
        }

        if ([string]$journal.state -ceq "POST_HEALTH_OK") {
            $journal = Set-LegacyMigrationJournalState $Layout $journal "COMMITTED"
            # JOBFLOW_LEGACY_MIGRATION_COMMITTED_STATE_BOUNDARY
        }
        if ([string]$journal.state -cne "COMMITTED") {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
        Write-LegacyMigrationCompletionReceipt $Layout $journal
        # JOBFLOW_LEGACY_MIGRATION_COMPLETION_RECEIPT_BOUNDARY
        Remove-LegacyMigrationPointerQuarantine $Layout $journal
        Remove-LegacyMigrationLauncherQuarantine $Layout $journal
        Remove-RuntimeHealthTransactionTemporary $Layout ([string]$journal.transaction_id)
        Remove-LegacyMigrationJournalPair $Layout
        Remove-BoundedPointerTransactionArtifacts ([string]$Layout.jobops)
        return [pscustomobject]@{ recovered = $true; activation_committed = $true }
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Start-LegacyV1ToV2Migration(
    [object]$Manifest,
    [object]$Verified,
    [string]$SignedManifestSha256,
    [byte[]]$SignedManifestBytes,
    [byte[]]$SignatureEnvelopeBytes,
    [object]$Eligibility
) {
    $layout = $Eligibility.layout
    $candidate = New-InstalledPointer $Manifest
    if ((Compare-SemVer $candidate.version $Eligibility.current.version) -le 0) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    if ((Compare-SemVer $Eligibility.current.version $Manifest.predecessor.minimum_version) -lt 0 -or
        (Compare-SemVer $Eligibility.current.version $Manifest.predecessor.maximum_version_exclusive) -ge 0) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-NoLegacyMigrationConflictingTransactions $layout
    if ($null -ne (Read-LegacyMigrationCompletionReceipt $layout)) {
        throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
    }
    $paths = Get-LegacyMigrationStatePaths ([string]$layout.state)
    foreach ($path in @(
        [string]$paths.current_quarantine,
        [string]$paths.previous_quarantine,
        [string]$paths.launcher_quarantine
    )) {
        if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) {
            throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
        }
    }

    $target = [IO.Path]::Combine([string]$layout.versions, [string]$candidate.version_directory)
    $targetWasPresent = [IO.Directory]::Exists($target)
    if ([IO.File]::Exists($target)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    if ($targetWasPresent) { [void](Assert-InstalledRuntime ([string]$layout.versions) $candidate) }
    else {
        Assert-ExtractedRuntime ([string]$Verified.stage) $Verified.closure $Manifest
        try { [IO.Directory]::Move([string]$Verified.stage, $target) }
        catch { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
    }
    [void](Assert-InstalledRuntime ([string]$layout.versions) $candidate)
    # JOBFLOW_LEGACY_MIGRATION_CANDIDATE_TARGET_READY_BOUNDARY

    $transactionId = [Guid]::NewGuid().ToString("N")
    $transactionId = Ensure-ActivationTrustEvidence `
        $layout $candidate $SignedManifestBytes $SignatureEnvelopeBytes $transactionId
    # JOBFLOW_LEGACY_MIGRATION_ACTIVATION_TRUST_READY_BOUNDARY
    $journal = New-LegacyMigrationJournalEnvelope `
        $transactionId "PREPARED" 1 $targetWasPresent `
        $SignedManifestSha256 $Eligibility.current $Eligibility.previous $candidate
    Write-LegacyMigrationJournalPair $layout $journal
    # JOBFLOW_LEGACY_MIGRATION_PREPARED_BOUNDARY
    return Recover-PendingLegacyMigration $layout
}

function Recover-PendingActivation([object]$Layout) {
    try {
        [void](Read-ActivationCompletionReceipt $Layout)
        $journal = Read-ActivationJournalPair $Layout
        if ($null -eq $journal) {
            Remove-BoundedPointerTransactionArtifacts ([string]$Layout.jobops)
            return [pscustomobject]@{ recovered = $false; activation_committed = $false }
        }
        [void](Assert-ActivationTrustEvidenceForPointer `
            $Layout $journal.candidate ([string]$journal.transaction_id))
        Assert-ActivationJournalRuntimes $Layout $journal $false
        $live = Get-ActivationLivePointerState $Layout $journal
        if ([string]$live.state -ceq "IMPOSSIBLE") { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }

        if ([string]$journal.state -in @("PREPARED", "PRE_HEALTH_OK", "POINTER_SWITCHED")) {
            if ([string]$live.state -cne "ORIGINAL") { Restore-OriginalPointerPair $Layout $journal }
            Remove-OwnedActivationTrustEvidenceIfUncommitted $Layout $journal
            Remove-OwnedCandidateIfUnreferenced $Layout $journal
            Remove-RuntimeHealthTransactionTemporary $Layout ([string]$journal.transaction_id)
            Remove-ActivationJournalPair $Layout
            Remove-BoundedPointerTransactionArtifacts ([string]$Layout.jobops)
            return [pscustomobject]@{ recovered = $true; activation_committed = $false }
        }

        if ([string]$live.state -cne "SWITCHED") { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        Assert-ActivationJournalRuntimes $Layout $journal $true
        if ([string]$journal.state -ceq "POST_HEALTH_OK") {
            $journal = Set-ActivationJournalState $Layout $journal "COMMITTED"
        }
        if ([string]$journal.state -cne "COMMITTED") { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
        Write-ActivationCompletionReceipt $Layout $journal
        Remove-RuntimeHealthTransactionTemporary $Layout ([string]$journal.transaction_id)
        Remove-ActivationJournalPair $Layout
        Remove-BoundedPointerTransactionArtifacts ([string]$Layout.jobops)
        return [pscustomobject]@{ recovered = $true; activation_committed = $true }
    }
    catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
}

function Get-RollbackStatePaths([string]$StateRoot) {
    $root = (Assert-SafeDirectory $StateRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    return [pscustomobject]@{
        main = [IO.Path]::Combine($root, ".jobflow-rollback-transaction-v1.json")
        backup = [IO.Path]::Combine($root, ".jobflow-rollback-transaction-v1.backup.json")
        main_temporary = [IO.Path]::Combine($root, ".jobflow-rollback-transaction-v1.main.write.tmp")
        backup_temporary = [IO.Path]::Combine($root, ".jobflow-rollback-transaction-v1.backup.write.tmp")
        receipt = [IO.Path]::Combine($root, ".jobflow-rollback-completion-v1.json")
        receipt_temporary = [IO.Path]::Combine($root, ".jobflow-rollback-completion-v1.write.tmp")
    }
}

function New-RollbackJournalSemantic(
    [string]$TransactionId,
    [string]$State,
    [long]$Generation,
    [object]$OriginalCurrent,
    [object]$OriginalPrevious,
    [object]$Target
) {
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ROLLBACK_TRANSACTION"
        transaction_id = $TransactionId
        state = $State
        generation = $Generation
        original_current = $OriginalCurrent
        original_previous = $OriginalPrevious
        target = $Target
    }
}

function New-RollbackJournalEnvelope(
    [string]$TransactionId,
    [string]$State,
    [long]$Generation,
    [object]$OriginalCurrent,
    [object]$OriginalPrevious,
    [object]$Target
) {
    $semantic = New-RollbackJournalSemantic `
        $TransactionId $State $Generation $OriginalCurrent $OriginalPrevious $Target
    $value = [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ROLLBACK_TRANSACTION"
        transaction_id = $TransactionId
        state = $State
        generation = $Generation
        original_current = $OriginalCurrent
        original_previous = $OriginalPrevious
        target = $Target
        semantic_sha256 = Get-CanonicalJsonSha256 $semantic
    }
    Assert-RollbackJournalShape $value
    return $value
}

function Assert-RollbackJournalShape([object]$Value) {
    try {
        Assert-ExactProperties $Value @(
            "schema_version", "kind", "transaction_id", "state", "generation",
            "original_current", "original_previous", "target", "semantic_sha256"
        )
        if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
            [string]$Value.kind -cne "JOBFLOW_ROLLBACK_TRANSACTION" -or
            [string]$Value.transaction_id -cnotmatch '^[0-9a-f]{32}$') {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        $expectedGeneration = 0
        if ([string]$Value.state -ceq "PREPARED") { $expectedGeneration = 1 }
        elseif ([string]$Value.state -ceq "PRE_HEALTH_OK") { $expectedGeneration = 2 }
        elseif ([string]$Value.state -ceq "POINTER_SWITCHED") { $expectedGeneration = 3 }
        elseif ([string]$Value.state -ceq "POST_HEALTH_OK") { $expectedGeneration = 4 }
        elseif ([string]$Value.state -ceq "COMMITTED") { $expectedGeneration = 5 }
        else { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
        if (-not (Test-JsonInteger $Value.generation $expectedGeneration $expectedGeneration)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        Assert-InstalledPointerShape $Value.original_current
        Assert-InstalledPointerShape $Value.original_previous
        Assert-InstalledPointerShape $Value.target
        if (-not (Test-PointerValueEqual $Value.original_previous $Value.target) -or
            (Test-PointerValueEqual $Value.original_current $Value.target)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        Assert-Sha256 $Value.semantic_sha256
        $semantic = New-RollbackJournalSemantic `
            ([string]$Value.transaction_id) ([string]$Value.state) ([long]$Value.generation) `
            $Value.original_current $Value.original_previous $Value.target
        if ([string]$Value.semantic_sha256 -cne (Get-CanonicalJsonSha256 $semantic)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Get-RollbackJournalImmutableSha256([object]$Value) {
    return Get-CanonicalJsonSha256 ([pscustomobject][ordered]@{
        schema_version = [long]$Value.schema_version
        kind = [string]$Value.kind
        transaction_id = [string]$Value.transaction_id
        original_current = $Value.original_current
        original_previous = $Value.original_previous
        target = $Value.target
    })
}

function Read-RollbackJournalPair([object]$Layout) {
    $paths = Get-RollbackStatePaths ([string]$Layout.state)
    try {
        Remove-ReservedActivationTemporary ([string]$paths.main_temporary)
        Remove-ReservedActivationTemporary ([string]$paths.backup_temporary)
        if ([IO.Directory]::Exists([string]$paths.main) -or
            [IO.Directory]::Exists([string]$paths.backup)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        $mainExists = [IO.File]::Exists([string]$paths.main)
        $backupExists = [IO.File]::Exists([string]$paths.backup)
        if (-not $mainExists -and -not $backupExists) { return $null }
        if (-not $backupExists) { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
        $main = $null
        $backup = $null
        try {
            $backup = Read-CanonicalActivationStateFile ([string]$paths.backup) "rollback_journal"
            if (-not $mainExists) {
                Write-AtomicCanonicalActivationStateFile `
                    ([string]$paths.main) ([string]$paths.main_temporary) `
                    $backup.value "rollback_journal"
                $main = Read-CanonicalActivationStateFile ([string]$paths.main) "rollback_journal"
            }
            else {
                try { $main = Read-CanonicalActivationStateFile ([string]$paths.main) "rollback_journal" }
                catch {
                    Write-AtomicCanonicalActivationStateFile `
                        ([string]$paths.main) ([string]$paths.main_temporary) `
                        $backup.value "rollback_journal"
                    $main = Read-CanonicalActivationStateFile ([string]$paths.main) "rollback_journal"
                }
            }
            if (Test-ByteArraysEqual ([byte[]]$main.bytes) ([byte[]]$backup.bytes)) {
                return $main.value
            }
            if ((Get-RollbackJournalImmutableSha256 $main.value) -cne
                    (Get-RollbackJournalImmutableSha256 $backup.value) -or
                ([long]$main.value.generation - [long]$backup.value.generation) -ne 1) {
                throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
            }
            $expected = ""
            if ([string]$backup.value.state -ceq "PREPARED") { $expected = "PRE_HEALTH_OK" }
            elseif ([string]$backup.value.state -ceq "PRE_HEALTH_OK") { $expected = "POINTER_SWITCHED" }
            elseif ([string]$backup.value.state -ceq "POINTER_SWITCHED") { $expected = "POST_HEALTH_OK" }
            elseif ([string]$backup.value.state -ceq "POST_HEALTH_OK") { $expected = "COMMITTED" }
            if ([string]$main.value.state -cne $expected) {
                throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
            }
            Write-AtomicCanonicalActivationStateFile `
                ([string]$paths.main) ([string]$paths.main_temporary) `
                $backup.value "rollback_journal"
            return $backup.value
        }
        finally {
            if ($null -ne $main -and $null -ne $main.bytes) {
                [Array]::Clear($main.bytes, 0, $main.bytes.Length)
            }
            if ($null -ne $backup -and $null -ne $backup.bytes) {
                [Array]::Clear($backup.bytes, 0, $backup.bytes.Length)
            }
        }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Write-RollbackJournalPair([object]$Layout, [object]$Value) {
    try {
        Assert-RollbackJournalShape $Value
        $paths = Get-RollbackStatePaths ([string]$Layout.state)
        $mainExists = [IO.File]::Exists([string]$paths.main)
        $backupExists = [IO.File]::Exists([string]$paths.backup)
        if (-not $mainExists -and -not $backupExists) {
            if ([long]$Value.generation -ne 1 -or [string]$Value.state -cne "PREPARED") {
                throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
            }
            Write-AtomicCanonicalActivationStateFile `
                ([string]$paths.backup) ([string]$paths.backup_temporary) `
                $Value "rollback_journal"
            Write-AtomicCanonicalActivationStateFile `
                ([string]$paths.main) ([string]$paths.main_temporary) `
                $Value "rollback_journal"
        }
        else {
            $existing = Read-RollbackJournalPair $Layout
            if ($null -eq $existing -or
                (Get-RollbackJournalImmutableSha256 $existing) -cne
                    (Get-RollbackJournalImmutableSha256 $Value) -or
                ([long]$Value.generation - [long]$existing.generation) -ne 1) {
                throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
            }
            $expected = ""
            if ([string]$existing.state -ceq "PREPARED") { $expected = "PRE_HEALTH_OK" }
            elseif ([string]$existing.state -ceq "PRE_HEALTH_OK") { $expected = "POINTER_SWITCHED" }
            elseif ([string]$existing.state -ceq "POINTER_SWITCHED") { $expected = "POST_HEALTH_OK" }
            elseif ([string]$existing.state -ceq "POST_HEALTH_OK") { $expected = "COMMITTED" }
            if ([string]$Value.state -cne $expected) {
                throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
            }
            Write-AtomicCanonicalActivationStateFile `
                ([string]$paths.main) ([string]$paths.main_temporary) `
                $Value "rollback_journal" ([string]$paths.backup)
            Write-AtomicCanonicalActivationStateFile `
                ([string]$paths.backup) ([string]$paths.backup_temporary) `
                $Value "rollback_journal"
        }
        $verified = Read-RollbackJournalPair $Layout
        if ((Get-CanonicalJsonSha256 $verified) -cne (Get-CanonicalJsonSha256 $Value)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Set-RollbackJournalState([object]$Layout, [object]$Value, [string]$State) {
    $generation = 0
    if ($State -ceq "PRE_HEALTH_OK" -and [string]$Value.state -ceq "PREPARED") { $generation = 2 }
    elseif ($State -ceq "POINTER_SWITCHED" -and [string]$Value.state -ceq "PRE_HEALTH_OK") { $generation = 3 }
    elseif ($State -ceq "POST_HEALTH_OK" -and [string]$Value.state -ceq "POINTER_SWITCHED") { $generation = 4 }
    elseif ($State -ceq "COMMITTED" -and [string]$Value.state -ceq "POST_HEALTH_OK") { $generation = 5 }
    else { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
    $next = New-RollbackJournalEnvelope `
        ([string]$Value.transaction_id) $State $generation `
        $Value.original_current $Value.original_previous $Value.target
    Write-RollbackJournalPair $Layout $next
    return $next
}

function New-RollbackCompletionReceipt([object]$Journal) {
    $semantic = [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ROLLBACK_COMPLETION"
        transaction_id = [string]$Journal.transaction_id
        status = "COMMITTED"
        original_current = $Journal.original_current
        original_previous = $Journal.original_previous
        restored_current = $Journal.target
        restored_previous = $Journal.original_current
    }
    return [pscustomobject][ordered]@{
        schema_version = 1
        kind = "JOBFLOW_ROLLBACK_COMPLETION"
        transaction_id = [string]$Journal.transaction_id
        status = "COMMITTED"
        original_current = $Journal.original_current
        original_previous = $Journal.original_previous
        restored_current = $Journal.target
        restored_previous = $Journal.original_current
        semantic_sha256 = Get-CanonicalJsonSha256 $semantic
    }
}

function Assert-RollbackCompletionReceiptShape([object]$Value) {
    try {
        Assert-ExactProperties $Value @(
            "schema_version", "kind", "transaction_id", "status", "original_current",
            "original_previous", "restored_current", "restored_previous", "semantic_sha256"
        )
        if (-not (Test-JsonInteger $Value.schema_version 1 1) -or
            [string]$Value.kind -cne "JOBFLOW_ROLLBACK_COMPLETION" -or
            [string]$Value.transaction_id -cnotmatch '^[0-9a-f]{32}$' -or
            [string]$Value.status -cne "COMMITTED") {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        foreach ($pointer in @(
            $Value.original_current, $Value.original_previous,
            $Value.restored_current, $Value.restored_previous
        )) { Assert-InstalledPointerShape $pointer }
        if (-not (Test-PointerValueEqual $Value.original_previous $Value.restored_current) -or
            -not (Test-PointerValueEqual $Value.original_current $Value.restored_previous) -or
            (Test-PointerValueEqual $Value.restored_current $Value.restored_previous)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        Assert-Sha256 $Value.semantic_sha256
        $semantic = [pscustomobject][ordered]@{
            schema_version = 1
            kind = "JOBFLOW_ROLLBACK_COMPLETION"
            transaction_id = [string]$Value.transaction_id
            status = "COMMITTED"
            original_current = $Value.original_current
            original_previous = $Value.original_previous
            restored_current = $Value.restored_current
            restored_previous = $Value.restored_previous
        }
        if ([string]$Value.semantic_sha256 -cne (Get-CanonicalJsonSha256 $semantic)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Read-RollbackCompletionReceipt([object]$Layout) {
    $paths = Get-RollbackStatePaths ([string]$Layout.state)
    try {
        Remove-ReservedActivationTemporary ([string]$paths.receipt_temporary)
        if ([IO.Directory]::Exists([string]$paths.receipt)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        if (-not [IO.File]::Exists([string]$paths.receipt)) { return $null }
        $read = Read-CanonicalActivationStateFile ([string]$paths.receipt) "rollback_receipt"
        try { return $read.value }
        finally { [Array]::Clear($read.bytes, 0, $read.bytes.Length) }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Write-RollbackCompletionReceipt([object]$Layout, [object]$Journal) {
    try {
        $receipt = New-RollbackCompletionReceipt $Journal
        Assert-RollbackCompletionReceiptShape $receipt
        $paths = Get-RollbackStatePaths ([string]$Layout.state)
        Write-AtomicCanonicalActivationStateFile `
            ([string]$paths.receipt) ([string]$paths.receipt_temporary) `
            $receipt "rollback_receipt"
        $verified = Read-RollbackCompletionReceipt $Layout
        if ((Get-CanonicalJsonSha256 $verified) -cne (Get-CanonicalJsonSha256 $receipt)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Remove-RollbackJournalPair([object]$Layout) {
    $paths = Get-RollbackStatePaths ([string]$Layout.state)
    try {
        [void](Read-RollbackJournalPair $Layout)
        [IO.File]::Delete([string]$paths.main)
        [IO.File]::Delete([string]$paths.backup)
        Remove-ReservedActivationTemporary ([string]$paths.main_temporary)
        Remove-ReservedActivationTemporary ([string]$paths.backup_temporary)
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Get-RollbackLivePointerState([object]$Layout, [object]$Journal) {
    try {
        $current = Read-InstalledPointer `
            ([IO.Path]::Combine([string]$Layout.jobops, "current.json")) $true
        $previous = Read-InstalledPointer `
            ([IO.Path]::Combine([string]$Layout.jobops, "previous.json")) $false
        if ($null -eq $current -or $null -eq $previous) {
            return [pscustomobject]@{ state = "IMPOSSIBLE"; current = $current; previous = $previous }
        }
        if ((Test-PointerValueEqual $current $Journal.original_current) -and
            (Test-PointerValueEqual $previous $Journal.original_previous)) {
            return [pscustomobject]@{ state = "ORIGINAL"; current = $current; previous = $previous }
        }
        if ((Test-PointerValueEqual $current $Journal.original_current) -and
            (Test-PointerValueEqual $previous $Journal.original_current)) {
            return [pscustomobject]@{ state = "PREVIOUS_ONLY"; current = $current; previous = $previous }
        }
        if ((Test-PointerValueEqual $current $Journal.target) -and
            (Test-PointerValueEqual $previous $Journal.original_current)) {
            return [pscustomobject]@{ state = "SWITCHED"; current = $current; previous = $previous }
        }
        return [pscustomobject]@{ state = "IMPOSSIBLE"; current = $current; previous = $previous }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Test-RollbackPointerHealthy(
    [object]$Layout,
    [object]$Pointer,
    [string]$TransactionId,
    [string]$Phase
) {
    try {
        [void](Assert-ActivationTrustEvidenceForPointer $Layout $Pointer "")
        [void](Assert-InstalledRuntime ([string]$Layout.versions) $Pointer)
        Invoke-CandidateRuntimeHealth $Layout $Pointer $TransactionId $Phase
        [void](Assert-ActivationTrustEvidenceForPointer $Layout $Pointer "")
        return $true
    }
    catch { return $false }
}

function Assert-RollbackPointerPairTrusted(
    [object]$Layout,
    [object]$Current,
    [object]$Previous
) {
    try {
        Assert-InstalledPointerShape $Current
        Assert-InstalledPointerShape $Previous
        if (Test-PointerValueEqual $Current $Previous) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        [void](Assert-InstalledRuntime ([string]$Layout.versions) $Current)
        [void](Assert-InstalledRuntime ([string]$Layout.versions) $Previous)
        [void](Assert-ActivationTrustEvidenceForPointer $Layout $Current "")
        [void](Assert-ActivationTrustEvidenceForPointer $Layout $Previous "")
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Restore-RollbackOriginalPointerPair([object]$Layout, [object]$Journal) {
    try {
        Restore-OriginalPointerPair $Layout $Journal
        $live = Get-RollbackLivePointerState $Layout $Journal
        if ([string]$live.state -cne "ORIGINAL") {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        Assert-RollbackPointerPairTrusted $Layout $live.current $live.previous
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Recover-PendingRollback([object]$Layout) {
    try {
        $journal = Read-RollbackJournalPair $Layout
        if ($null -eq $journal) {
            return [pscustomobject]@{ recovered = $false; rollback_committed = $false; pointer = $null }
        }
        $live = Get-RollbackLivePointerState $Layout $journal
        if ([string]$live.state -ceq "IMPOSSIBLE") {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }

        # Both sides were signed v2 runtimes when the transaction was created.
        # Revalidate both before any recovery write; target health then decides
        # whether recovery moves forward or restores the original pair.
        Assert-RollbackPointerPairTrusted `
            $Layout $journal.original_current $journal.original_previous
        $targetHealthy = Test-RollbackPointerHealthy `
            $Layout $journal.target ([string]$journal.transaction_id) "pre"
        if (-not $targetHealthy) {
            $originalHealthy = Test-RollbackPointerHealthy `
                $Layout $journal.original_current ([string]$journal.transaction_id) "pre"
            if (-not $originalHealthy) { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
            Restore-RollbackOriginalPointerPair $Layout $journal
            Remove-RuntimeHealthTransactionTemporary $Layout ([string]$journal.transaction_id)
            Remove-RollbackJournalPair $Layout
            return [pscustomobject]@{
                recovered = $true; rollback_committed = $false; pointer = $journal.original_current
            }
        }

        if ([string]$journal.state -ceq "PREPARED") {
            $journal = Set-RollbackJournalState $Layout $journal "PRE_HEALTH_OK"
            # JOBFLOW_ROLLBACK_PRE_HEALTH_OK_STATE_BOUNDARY
        }
        $live = Get-RollbackLivePointerState $Layout $journal
        if ([string]$journal.state -ceq "PRE_HEALTH_OK") {
            if ([string]$live.state -ne "SWITCHED") {
                if ([string]$live.state -notin @("ORIGINAL", "PREVIOUS_ONLY")) {
                    throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
                }
                Publish-PointerPair `
                    ([string]$Layout.jobops) $journal.target `
                    $journal.original_current $journal.original_previous
            }
            $journal = Set-RollbackJournalState $Layout $journal "POINTER_SWITCHED"
            # JOBFLOW_ROLLBACK_POINTER_SWITCHED_STATE_BOUNDARY
        }
        $live = Get-RollbackLivePointerState $Layout $journal
        if ([string]$live.state -cne "SWITCHED") {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        if ([string]$journal.state -ceq "POINTER_SWITCHED") {
            try {
                [void](Assert-ActivationTrustEvidenceForPointer $Layout $live.current "")
                Invoke-CandidateRuntimeHealth `
                    $Layout $live.current ([string]$journal.transaction_id) "post"
            }
            catch {
                # A target which was healthy before the pointer switch may
                # still fail in its live, post-switch context.  Fail closed and
                # restore only after the original runtime and its signed
                # activation evidence have both been revalidated again.
                $originalHealthy = Test-RollbackPointerHealthy `
                    $Layout $journal.original_current ([string]$journal.transaction_id) "post"
                if (-not $originalHealthy) { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
                Restore-RollbackOriginalPointerPair $Layout $journal
                Remove-RuntimeHealthTransactionTemporary $Layout ([string]$journal.transaction_id)
                Remove-RollbackJournalPair $Layout
                return [pscustomobject]@{
                    recovered = $true
                    rollback_committed = $false
                    pointer = $journal.original_current
                    original_restored = $true
                }
            }
            $journal = Set-RollbackJournalState $Layout $journal "POST_HEALTH_OK"
            # JOBFLOW_ROLLBACK_POST_HEALTH_OK_STATE_BOUNDARY
        }
        if ([string]$journal.state -ceq "POST_HEALTH_OK") {
            $journal = Set-RollbackJournalState $Layout $journal "COMMITTED"
        }
        if ([string]$journal.state -cne "COMMITTED") {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        Write-RollbackCompletionReceipt $Layout $journal
        # JOBFLOW_ROLLBACK_COMPLETION_RECEIPT_BOUNDARY
        Remove-RuntimeHealthTransactionTemporary $Layout ([string]$journal.transaction_id)
        Remove-RollbackJournalPair $Layout
        return [pscustomobject]@{
            recovered = $true; rollback_committed = $true; pointer = $journal.target
        }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Assert-RollbackCompletionMatchesPointers([object]$Layout, [object]$Receipt) {
    try {
        $current = Read-InstalledPointer `
            ([IO.Path]::Combine([string]$Layout.jobops, "current.json")) $true
        $previous = Read-InstalledPointer `
            ([IO.Path]::Combine([string]$Layout.jobops, "previous.json")) $false
        if (-not (Test-PointerValueEqual $current $Receipt.restored_current) -or
            -not (Test-PointerValueEqual $previous $Receipt.restored_previous)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        Assert-RollbackPointerPairTrusted $Layout $current $previous
        return $current
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Start-RollbackTransaction([object]$Layout) {
    try {
        $current = Read-InstalledPointer `
            ([IO.Path]::Combine([string]$Layout.jobops, "current.json")) $true
        $previous = Read-InstalledPointer `
            ([IO.Path]::Combine([string]$Layout.jobops, "previous.json")) $false
        if ($null -eq $current -or $null -eq $previous) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        Assert-RollbackPointerPairTrusted $Layout $current $previous
        $journal = New-RollbackJournalEnvelope `
            ([Guid]::NewGuid().ToString("N")) "PREPARED" 1 $current $previous $previous
        Write-RollbackJournalPair $Layout $journal
        # JOBFLOW_ROLLBACK_PREPARED_BOUNDARY
        return Recover-PendingRollback $Layout
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Invoke-RollbackManagement([bool]$AuthorizeNewRollback) {
    $jobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
    if ([IO.File]::Exists($jobOpsRoot) -or -not [IO.Directory]::Exists($jobOpsRoot)) {
        throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
    }
    $operationLock = $null
    $maintenanceLock = $null
    $discoveryLock = $null
    try {
        # Rollback owns the complete fixed lock order.  The thin wrapper passes
        # no paths and acquires no locks of its own.
        $operationLock = Enter-ExistingBootstrapOperationLock $jobOpsRoot
        $layout = Get-ExistingActivationLayoutForRecovery $jobOpsRoot
        $maintenanceLock = Enter-ExistingActivationMaintenanceLock ([string]$layout.state)
        $discoveryLock = Enter-RollbackDiscoveryLock ([string]$layout.state)

        if (Test-LegacyMigrationArtifactsPresent $jobOpsRoot) {
            $migration = Recover-PendingLegacyMigration $layout
            return [pscustomobject]@{
                recovery_only = $true
                rollback_performed = $false
                rollback_committed_during_recovery = $false
                pointer = $null
                activation_recovery = $migration
                layout = $layout
            }
        }
        $activation = Recover-PendingActivation $layout
        if ([bool]$activation.recovered) {
            return [pscustomobject]@{
                recovery_only = $true
                rollback_performed = $false
                rollback_committed_during_recovery = $false
                pointer = $null
                activation_recovery = $activation
                layout = $layout
            }
        }
        $rollback = Recover-PendingRollback $layout
        if ([bool]$rollback.recovered) {
            return [pscustomobject]@{
                recovery_only = $true
                rollback_performed = [bool]$rollback.rollback_committed
                rollback_committed_during_recovery = [bool]$rollback.rollback_committed
                pointer = $rollback.pointer
                activation_recovery = $activation
                layout = $layout
            }
        }

        $receipt = Read-RollbackCompletionReceipt $layout
        if ($null -ne $receipt -and -not $AuthorizeNewRollback) {
            $pointer = Assert-RollbackCompletionMatchesPointers $layout $receipt
            return [pscustomobject]@{
                recovery_only = $false
                already_completed = $true
                rollback_performed = $false
                rollback_committed_during_recovery = $false
                pointer = $pointer
                activation_recovery = $activation
                layout = $layout
            }
        }
        $started = Start-RollbackTransaction $layout
        if (-not [bool]$started.rollback_committed) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        return [pscustomobject]@{
            recovery_only = $false
            already_completed = $false
            rollback_performed = $true
            rollback_committed_during_recovery = $false
            pointer = $started.pointer
            activation_recovery = $activation
            layout = $layout
        }
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
    finally {
        if ($null -ne $discoveryLock) { $discoveryLock.Dispose() }
        if ($null -ne $maintenanceLock) { $maintenanceLock.Dispose() }
        if ($null -ne $operationLock) { $operationLock.Dispose() }
    }
}

function Invoke-RecoverOnlyManagement {
    $jobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
    if ([IO.File]::Exists($jobOpsRoot)) { throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED" }
    if (-not [IO.Directory]::Exists($jobOpsRoot)) {
        return [pscustomobject]@{ recovered = $false; activation_committed = $false }
    }

    $operationLock = $null
    $maintenanceLock = $null
    $discoveryLock = $null
    try {
        # Management lock order is fixed: bootstrap operation first, then the
        # runtime activation-maintenance lock.  Both files must already belong
        # to a v2 installation; RecoverOnly never creates the installed layout.
        $operationLock = Enter-ExistingBootstrapOperationLock $jobOpsRoot
        $layout = Get-ExistingActivationLayoutForRecovery $jobOpsRoot
        $maintenanceLock = Enter-ExistingActivationMaintenanceLock ([string]$layout.state)
        if (Test-LegacyMigrationArtifactsPresent $jobOpsRoot) {
            $discoveryLock = Enter-ExistingLegacyMigrationDiscoveryLock ([string]$layout.state)
            return Recover-PendingLegacyMigration $layout
        }
        $activation = Recover-PendingActivation $layout
        if ([bool]$activation.recovered) { return $activation }

        # RecoverOnly is the mandatory barrier used by installers and the
        # updater before they inspect or mutate a runtime.  A crashed rollback
        # must therefore be completed under the same global lock order instead
        # of being left for a later explicit rollback invocation.
        if (Test-PendingRollbackArtifactsForFixedInstallation) {
            $discoveryLock = Enter-RollbackDiscoveryLock ([string]$layout.state)
            $rollback = Recover-PendingRollback $layout
            if ([bool]$rollback.recovered) {
                return [pscustomobject]@{
                    recovered = $true
                    activation_committed = $false
                    rollback_committed = [bool]$rollback.rollback_committed
                }
            }
        }
        return $activation
    }
    catch {
        if ($_.Exception.Message -ceq "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED") {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        throw "JOBFLOW_ACTIVATION_RECOVERY_FAILED"
    }
    finally {
        if ($null -ne $discoveryLock) { $discoveryLock.Dispose() }
        if ($null -ne $maintenanceLock) { $maintenanceLock.Dispose() }
        if ($null -ne $operationLock) { $operationLock.Dispose() }
    }
}

function Invoke-VerifyInstalledManagement {
    $jobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
    if ([IO.File]::Exists($jobOpsRoot) -or -not [IO.Directory]::Exists($jobOpsRoot)) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $operationLock = $null
    $maintenanceLock = $null
    $discoveryLock = $null
    try {
        $operationLock = Enter-ExistingBootstrapOperationLock $jobOpsRoot
        $layout = Get-ExistingActivationLayoutForRecovery $jobOpsRoot
        $maintenanceLock = Enter-ExistingActivationMaintenanceLock ([string]$layout.state)
        $recovery = $null
        $rollbackRecovery = [pscustomobject]@{
            recovered = $false
            rollback_committed = $false
        }
        if (Test-LegacyMigrationArtifactsPresent $jobOpsRoot) {
            $discoveryLock = Enter-ExistingLegacyMigrationDiscoveryLock ([string]$layout.state)
            $recovery = Recover-PendingLegacyMigration $layout
        }
        else {
            $recovery = Recover-PendingActivation $layout
        }

        # VerifyInstalled is the trust gate used by every installed launcher.
        # Finish a pending rollback before selecting and attesting the runtime
        # that the launcher is allowed to execute.
        if (Test-PendingRollbackArtifactsForFixedInstallation) {
            if ($null -eq $discoveryLock) {
                $discoveryLock = Enter-RollbackDiscoveryLock ([string]$layout.state)
            }
            $rollbackRecovery = Recover-PendingRollback $layout
        }

        $current = Read-InstalledPointer ([IO.Path]::Combine($jobOpsRoot, "current.json")) $true
        if ($null -eq $current -or -not (Test-JsonInteger $current.schema_version 2 2)) {
            throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
        }
        [void](Assert-InstalledRuntime ([string]$layout.versions) $current)
        $trust = Assert-ActivationTrustEvidenceForPointer $layout $current ""
        return [pscustomobject]@{
            pointer = $current
            trust = $trust
            recovery_performed = (
                [bool]$recovery.recovered -or [bool]$rollbackRecovery.recovered
            )
            activation_committed_during_recovery = [bool]$recovery.activation_committed
        }
    }
    catch {
        if ($_.Exception.Message -ceq "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED") {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    finally {
        if ($null -ne $discoveryLock) { $discoveryLock.Dispose() }
        if ($null -ne $maintenanceLock) { $maintenanceLock.Dispose() }
        if ($null -ne $operationLock) { $operationLock.Dispose() }
    }
}

function Test-PendingRollbackArtifactsForFixedInstallation {
    try {
        $jobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
        if ([IO.File]::Exists($jobOpsRoot) -or -not [IO.Directory]::Exists($jobOpsRoot)) {
            return $false
        }
        $dataRoot = [IO.Path]::Combine($jobOpsRoot, "Data")
        $stateRoot = [IO.Path]::Combine($dataRoot, "state")
        if ([IO.File]::Exists($dataRoot) -or [IO.File]::Exists($stateRoot)) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
        if (-not [IO.Directory]::Exists($dataRoot) -or
            -not [IO.Directory]::Exists($stateRoot)) {
            # A legacy v1 installation has no v2 Data/state tree and therefore
            # cannot contain a valid v2 rollback transaction.
            return $false
        }
        [void](Assert-SafeDirectory $dataRoot)
        [void](Assert-SafeDirectory $stateRoot)
        $paths = Get-RollbackStatePaths $stateRoot
        foreach ($path in @(
            [string]$paths.main,
            [string]$paths.backup,
            [string]$paths.main_temporary,
            [string]$paths.backup_temporary
        )) {
            if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) {
                return $true
            }
        }
        return $false
    }
    catch { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
}

function Activate-VerifiedRuntime(
    [object]$Manifest,
    [object]$Verified,
    [string]$SignedManifestSha256,
    [byte[]]$SignedManifestBytes,
    [byte[]]$SignatureEnvelopeBytes
) {
    $existingJobOpsRoot = [IO.Path]::Combine($trustedLocalDataRoot, "JobOps")
    if ([IO.Directory]::Exists($existingJobOpsRoot) -and
        (Test-LegacyMigrationArtifactsPresent $existingJobOpsRoot)) {
        $pendingLayout = Get-ExistingActivationLayoutForRecovery $existingJobOpsRoot
        $pendingMaintenanceLock = $null
        $pendingDiscoveryLock = $null
        try {
            $pendingMaintenanceLock = Enter-ExistingActivationMaintenanceLock ([string]$pendingLayout.state)
            $pendingDiscoveryLock = Enter-ExistingLegacyMigrationDiscoveryLock ([string]$pendingLayout.state)
            $pendingRecovery = Recover-PendingLegacyMigration $pendingLayout
            return [pscustomobject]@{
                recovery_only = [bool]$pendingRecovery.recovered
                activation_committed = [bool]$pendingRecovery.activation_committed
                legacy_migration_performed = [bool]$pendingRecovery.activation_committed
            }
        }
        finally {
            if ($null -ne $pendingDiscoveryLock) { $pendingDiscoveryLock.Dispose() }
            if ($null -ne $pendingMaintenanceLock) { $pendingMaintenanceLock.Dispose() }
        }
    }

    $legacyEligibility = Assert-LegacyV1ActivationEligibility $Manifest
    if ($null -ne $legacyEligibility) {
        $legacyMaintenanceLock = $null
        $legacyDiscoveryLock = $null
        try {
            $legacyMaintenanceLock = Enter-ActivationMaintenanceLock ([string]$legacyEligibility.layout.state)
            $legacyDiscoveryLock = Enter-LegacyMigrationDiscoveryLock ([string]$legacyEligibility.layout.state)
            $revalidated = Assert-LegacyV1ActivationEligibility $Manifest
            if ($null -eq $revalidated -or
                -not (Test-PointerValueEqual $legacyEligibility.current $revalidated.current) -or
                -not (Test-PointerValueEqual $legacyEligibility.previous $revalidated.previous)) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
            $migration = Start-LegacyV1ToV2Migration `
                $Manifest $Verified $SignedManifestSha256 `
                $SignedManifestBytes $SignatureEnvelopeBytes $revalidated
            if (-not [bool]$migration.activation_committed) {
                throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED"
            }
            $published = Read-InstalledPointer `
                ([IO.Path]::Combine([string]$revalidated.layout.jobops, "current.json")) $true
            return [pscustomobject]@{
                pointer = $published
                activation_performed = $true
                legacy_migration_performed = $true
            }
        }
        finally {
            if ($null -ne $legacyDiscoveryLock) { $legacyDiscoveryLock.Dispose() }
            if ($null -ne $legacyMaintenanceLock) { $legacyMaintenanceLock.Dispose() }
        }
    }

    $layout = Initialize-ActivationLayout
    $jobOpsRoot = [string]$layout.jobops
    $versionsRoot = [string]$layout.versions
    $maintenanceLock = $null
    $rollbackDiscoveryLock = $null
    try {
        $maintenanceLock = Enter-ActivationMaintenanceLock ([string]$layout.state)
        $recovery = Recover-PendingActivation $layout
        if ([bool]$recovery.recovered) {
            return [pscustomobject]@{
                recovery_only = $true
                activation_committed = [bool]$recovery.activation_committed
            }
        }
        if (Test-PendingRollbackArtifactsForFixedInstallation) {
            $rollbackDiscoveryLock = Enter-RollbackDiscoveryLock ([string]$layout.state)
            $rollbackRecovery = Recover-PendingRollback $layout
            if ([bool]$rollbackRecovery.recovered) {
                return [pscustomobject]@{
                    recovery_only = $true
                    activation_committed = $false
                    rollback_committed = [bool]$rollbackRecovery.rollback_committed
                }
            }
        }
        $currentPath = [IO.Path]::Combine($jobOpsRoot, "current.json")
        $previousPath = [IO.Path]::Combine($jobOpsRoot, "previous.json")
        $oldCurrent = Read-InstalledPointer $currentPath $true
        $oldPrevious = Read-InstalledPointer $previousPath $false
        if ($null -eq $oldCurrent -and $null -ne $oldPrevious) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        if ($null -ne $oldCurrent) { [void](Assert-InstalledRuntime $versionsRoot $oldCurrent) }
        if ($null -ne $oldPrevious) { [void](Assert-InstalledRuntime $versionsRoot $oldPrevious) }

        $candidate = New-InstalledPointer $Manifest
        if ($null -ne $oldCurrent) {
            $comparison = Compare-SemVer $candidate.version $oldCurrent.version
            if ($comparison -lt 0) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
            if ($comparison -eq 0) {
                if ([string]$candidate.source_payload_sha256 -cne [string]$oldCurrent.source_payload_sha256 -or
                    (Get-CanonicalJsonSha256 $candidate) -cne (Get-CanonicalJsonSha256 $oldCurrent)) {
                    throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
                }
                [void](Assert-InstalledRuntime $versionsRoot $candidate)
                [void](Ensure-ActivationTrustEvidence `
                    $layout $candidate $SignedManifestBytes $SignatureEnvelopeBytes `
                    ([Guid]::NewGuid().ToString("N")))
                return [pscustomobject]@{
                    pointer = $candidate
                    activation_performed = [bool]$recovery.activation_committed
                }
            }
            if ((Compare-SemVer $oldCurrent.version $Manifest.predecessor.minimum_version) -lt 0 -or
                (Compare-SemVer $oldCurrent.version $Manifest.predecessor.maximum_version_exclusive) -ge 0) {
                throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
            }
        }

        $target = [IO.Path]::Combine($versionsRoot, [string]$candidate.version_directory)
        $targetWasPresent = [IO.Directory]::Exists($target)
        if ([IO.File]::Exists($target)) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }
        if ($targetWasPresent) { [void](Assert-InstalledRuntime $versionsRoot $candidate) }
        try {
            if (-not $targetWasPresent) {
                Assert-ExtractedRuntime ([string]$Verified.stage) $Verified.closure $Manifest
                [IO.Directory]::Move([string]$Verified.stage, $target)
            }
            [void](Assert-InstalledRuntime $versionsRoot $candidate)
            $transactionId = Ensure-ActivationTrustEvidence `
                $layout $candidate $SignedManifestBytes $SignatureEnvelopeBytes `
                ([Guid]::NewGuid().ToString("N"))
            # JOBFLOW_ACTIVATION_TRUST_READY_BOUNDARY
            $journal = New-ActivationJournalEnvelope `
                $transactionId "PREPARED" 1 $targetWasPresent $oldCurrent $oldPrevious $candidate
            Write-ActivationJournalPair $layout $journal
            # JOBFLOW_ACTIVATION_CANDIDATE_TARGET_READY_BOUNDARY
            # JOBFLOW_ACTIVATION_PREPARED_BOUNDARY
            Invoke-CandidateRuntimeHealth $layout $candidate ([string]$journal.transaction_id) "pre"
            # JOBFLOW_ACTIVATION_PRE_HEALTH_COMPLETED_BOUNDARY
            $journal = Set-ActivationJournalState $layout $journal "PRE_HEALTH_OK"
            # JOBFLOW_ACTIVATION_PRE_HEALTH_OK_STATE_BOUNDARY
            [void](Assert-ActivationTrustEvidenceForPointer `
                $layout $candidate ([string]$journal.transaction_id))
            Publish-PointerPair $jobOpsRoot $candidate $oldCurrent $oldPrevious
            # JOBFLOW_ACTIVATION_POINTER_PAIR_PUBLISHED_BOUNDARY
            $journal = Set-ActivationJournalState $layout $journal "POINTER_SWITCHED"
            # JOBFLOW_ACTIVATION_POINTER_SWITCHED_STATE_BOUNDARY
            $publishedCandidate = Read-InstalledPointer $currentPath $true
            if ((Get-CanonicalJsonSha256 $publishedCandidate) -cne (Get-CanonicalJsonSha256 $candidate)) {
                throw "JOBFLOW_RUNTIME_HEALTH_POST_FAILED"
            }
            Invoke-CandidateRuntimeHealth $layout $publishedCandidate ([string]$journal.transaction_id) "post"
            # JOBFLOW_ACTIVATION_POST_HEALTH_COMPLETED_BOUNDARY
            $journal = Set-ActivationJournalState $layout $journal "POST_HEALTH_OK"
            # JOBFLOW_ACTIVATION_POST_HEALTH_OK_STATE_BOUNDARY
            $journal = Set-ActivationJournalState $layout $journal "COMMITTED"
            # JOBFLOW_ACTIVATION_COMMITTED_STATE_BOUNDARY
            Write-ActivationCompletionReceipt $layout $journal
            # JOBFLOW_ACTIVATION_COMPLETION_RECEIPT_BOUNDARY
            Remove-ActivationJournalPair $layout
            Remove-BoundedPointerTransactionArtifacts $jobOpsRoot
            return [pscustomobject]@{ pointer = $candidate; activation_performed = $true }
        }
        catch {
            $activationError = $_
            try { [void](Recover-PendingActivation $layout) }
            catch { throw "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED" }
            throw $activationError
        }
    }
    finally {
        if ($null -ne $rollbackDiscoveryLock) { $rollbackDiscoveryLock.Dispose() }
        if ($null -ne $maintenanceLock) { $maintenanceLock.Dispose() }
    }
}

try {
    if ($selectedMode -ceq "VerifyInstalled") {
        $installed = Invoke-VerifyInstalledManagement
        [ordered]@{
            schema_version = 1
            status = "JOBFLOW_INSTALLED_RUNTIME_VERIFIED"
            version = [string]$installed.pointer.version
            manifest_sha256 = [string]$installed.trust.manifest_sha256
            signature_envelope_sha256 = [string]$installed.trust.signature_envelope_sha256
            pointer_sha256 = [string]$installed.trust.evidence.canonical_pointer_sha256
            runtime_closure_manifest_sha256 = [string]$installed.pointer.runtime_closure_manifest_sha256
            runtime_tree_sha256 = [string]$installed.pointer.runtime_tree_sha256
            release_key_id = [string]$installed.pointer.release_key_id
            source_payload_sha256 = [string]$installed.pointer.source_payload_sha256
            signed_activation_evidence_verified = $true
            recovery_performed = [bool]$installed.recovery_performed
            activation_committed_during_recovery = [bool]$installed.activation_committed_during_recovery
            paths_disclosed = $false
            real_external_actions = 0
        } | ConvertTo-Json -Compress
        exit 0
    }
    if ($selectedMode -ceq "RecoverOnly") {
        $recovery = Invoke-RecoverOnlyManagement
        if ([bool]$recovery.recovered) {
            [ordered]@{
                schema_version = 1
                status = "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED"
                recovery_performed = $true
                activation_committed = [bool]$recovery.activation_committed
                retry_required = $true
                real_external_actions = 0
            } | ConvertTo-Json -Compress
            exit 6
        }
        [ordered]@{
            schema_version = 1
            status = "JOBFLOW_ACTIVATION_RECOVERY_NOT_PENDING"
            recovery_performed = $false
            activation_committed = $false
            retry_required = $false
            real_external_actions = 0
        } | ConvertTo-Json -Compress
        exit 0
    }
    if ($selectedMode -ceq "Rollback") {
        $rolled = Invoke-RollbackManagement $StartNewRollback.IsPresent
        if ([bool]$rolled.recovery_only -and $null -eq $rolled.pointer) {
            [ordered]@{
                schema_version = 1
                status = "JOBFLOW_ROLLBACK_RECOVERY_RETRY_REQUIRED"
                recovery_performed = $true
                rollback_performed = $false
                retry_required = $true
                paths_disclosed = $false
                real_external_actions = 0
            } | ConvertTo-Json -Compress
            exit 6
        }

        $rollbackPointer = $rolled.pointer
        if ($null -eq $rollbackPointer) { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
        $rollbackTrust = Assert-ActivationTrustEvidenceForPointer `
            $rolled.layout $rollbackPointer ""
        $rollbackStatus = "JOBFLOW_BOOTSTRAP_ROLLED_BACK"
        if ([bool]$rolled.already_completed) {
            $rollbackStatus = "JOBFLOW_ROLLBACK_ALREADY_COMMITTED"
        }
        elseif ([bool]$rolled.recovery_only -and -not [bool]$rolled.rollback_performed) {
            $rollbackStatus = "JOBFLOW_ROLLBACK_ORIGINAL_RESTORED"
        }
        [ordered]@{
            schema_version = 1
            status = $rollbackStatus
            version = [string]$rollbackPointer.version
            manifest_sha256 = [string]$rollbackTrust.manifest_sha256
            signature_envelope_sha256 = [string]$rollbackTrust.signature_envelope_sha256
            pointer_sha256 = [string]$rollbackTrust.evidence.canonical_pointer_sha256
            runtime_closure_manifest_sha256 = [string]$rollbackPointer.runtime_closure_manifest_sha256
            runtime_tree_sha256 = [string]$rollbackPointer.runtime_tree_sha256
            release_key_id = [string]$rollbackPointer.release_key_id
            source_payload_sha256 = [string]$rollbackPointer.source_payload_sha256
            signed_activation_evidence_verified = $true
            recovery_performed = [bool]$rolled.recovery_only
            rollback_performed = [bool]$rolled.rollback_performed
            rollback_committed_during_recovery = [bool]$rolled.rollback_committed_during_recovery
            already_completed = [bool]$rolled.already_completed
            retry_required = $false
            paths_disclosed = $false
            real_external_actions = 0
        } | ConvertTo-Json -Compress
        if ($rollbackStatus -ceq "JOBFLOW_ROLLBACK_ORIGINAL_RESTORED") { exit 6 }
        exit 0
    }

    # Manifest-only and archive/activation callers must not silently cross a
    # rollback crash boundary.  This check is pathless and only activates when
    # the fixed installed v2 state contains rollback transaction artifacts.
    # Activate-VerifiedRuntime repeats the recovery while holding the bootstrap
    # operation lock, closing the check-to-activation race.
    if ($selectedMode -in @("DescribeManifest", "ExpandArchive", "Activate") -and
        (Test-PendingRollbackArtifactsForFixedInstallation)) {
        $barrierRecovery = Invoke-RecoverOnlyManagement
        if ([bool]$barrierRecovery.recovered) {
            [ordered]@{
                schema_version = 1
                status = "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED"
                recovery_performed = $true
                activation_committed = [bool]$barrierRecovery.activation_committed
                retry_required = $true
                real_external_actions = 0
            } | ConvertTo-Json -Compress
            exit 6
        }
    }

    $manifestBytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile(
        $ManifestPath,
        $maximumManifestBytes
    )
    $signatureEnvelopeBytes = [JobFlowBootstrapFiles]::ReadBoundedRegularFile(
        $SignaturePath,
        $maximumSignatureBytes
    )
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $signatureEnvelopeText = $strictUtf8.GetString($signatureEnvelopeBytes)
    $signaturePattern = '^\{"algorithm":"(?<algorithm>[A-Za-z0-9_-]+)","key_id":"(?<key>sha256:[0-9a-f]{64})","schema_version":1,"signature_b64url":"(?<signature>[A-Za-z0-9_-]+)"\}$'
    $signatureMatch = [Text.RegularExpressions.Regex]::Match(
        $signatureEnvelopeText,
        $signaturePattern,
        [Text.RegularExpressions.RegexOptions]::CultureInvariant
    )
    if (-not $signatureMatch.Success -or
        $signatureMatch.Groups["algorithm"].Value -cne $trustedAlgorithm -or
        $signatureMatch.Groups["key"].Value -cne $trustedKeyId) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }

    $modulus = ConvertFrom-Base64UrlStrict $trustedModulusBase64Url 256 512
    $exponent = ConvertFrom-Base64UrlStrict $trustedExponentBase64Url 1 8
    $signature = ConvertFrom-Base64UrlStrict $signatureMatch.Groups["signature"].Value 256 512
    if ($signature.Length -ne $modulus.Length) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $parameters = New-Object Security.Cryptography.RSAParameters
    $parameters.Modulus = $modulus
    $parameters.Exponent = $exponent
    $rsa = New-Object Security.Cryptography.RSACng
    try {
        $rsa.ImportParameters($parameters)
        $signatureVerified = $rsa.VerifyData(
            $manifestBytes,
            $signature,
            [Security.Cryptography.HashAlgorithmName]::SHA256,
            [Security.Cryptography.RSASignaturePadding]::Pkcs1
        )
    }
    finally { $rsa.Dispose() }
    if (-not $signatureVerified) { throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED" }

    # JOBFLOW_BOOTSTRAP_SIGNATURE_VERIFIED_BOUNDARY
    $manifestText = $strictUtf8.GetString($manifestBytes)
    [JobFlowBootstrapJson]::AssertNoDuplicateProperties($manifestText)
    $manifestValue = $manifestText | ConvertFrom-Json
    if ($null -eq $manifestValue -or $manifestValue -isnot [PSCustomObject]) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $schemaProperty = $manifestValue.PSObject.Properties["schema_version"]
    if ($null -eq $schemaProperty -or
        -not ($schemaProperty.Value -is [int] -or $schemaProperty.Value -is [long]) -or
        $schemaProperty.Value -ne 2) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $publisherProperty = $manifestValue.PSObject.Properties["publisher_attestation"]
    if ($null -eq $publisherProperty -or $publisherProperty.Value -isnot [PSCustomObject]) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    $releaseKeyProperty = $publisherProperty.Value.PSObject.Properties["release_key_id"]
    if ($null -eq $releaseKeyProperty -or
        $releaseKeyProperty.Value -isnot [string] -or
        $releaseKeyProperty.Value -cne $trustedKeyId -or
        $releaseKeyProperty.Value -cne $signatureMatch.Groups["key"].Value) {
        throw "JOBFLOW_BOOTSTRAP_INPUT_REJECTED"
    }
    Assert-ArchiveManifestShape $manifestValue
    Assert-EmbeddedCompatibility $manifestValue
    $manifestSha = Get-BytesSha256 $manifestBytes

    $archiveMode = $selectedMode -in @("ExpandArchive", "Activate")
    if ($archiveMode) {
        if ($Activate.IsPresent) { [void](Assert-LegacyV1ActivationEligibility $manifestValue) }
        $operationLock = $null
        try {
            $operationLock = Enter-BootstrapOperationLock
            if ($Activate.IsPresent -and -not
                (Test-LegacyMigrationArtifactsPresent ([IO.Path]::Combine($trustedLocalDataRoot, "JobOps")))) {
                [void](Assert-LegacyV1ActivationEligibility $manifestValue)
            }
            Remove-BoundedBootstrapOrphans $trustedLocalDataRoot
            if ($Activate.IsPresent) {
                $staged = $null
                try {
                    $staged = Expand-AndVerifySignedArchive $manifestValue $ArchivePath $true
                    $activated = Activate-VerifiedRuntime `
                        $manifestValue $staged $manifestSha $manifestBytes $signatureEnvelopeBytes
                    if ([bool]$activated.recovery_only) {
                        [ordered]@{
                            status = "JOBFLOW_ACTIVATION_RECOVERED_RETRY_REQUIRED"
                            activation_committed = [bool]$activated.activation_committed
                            retry_required = $true
                            real_external_actions = 0
                        } | ConvertTo-Json -Compress
                        exit 0
                    }
                    $activationOutput = [ordered]@{
                        status = "JOBFLOW_BOOTSTRAP_ACTIVATED"
                        version = [string]$activated.pointer.version
                        source_payload_sha256 = [string]$activated.pointer.source_payload_sha256
                        runtime_tree_sha256 = [string]$activated.pointer.runtime_tree_sha256
                        activation_performed = [bool]$activated.activation_performed
                        real_external_actions = 0
                    }
                    if ([bool]$activated.legacy_migration_performed) {
                        $activationOutput.legacy_migration_performed = $true
                    }
                    $activationOutput | ConvertTo-Json -Compress
                    exit 0
                }
                finally {
                    if ($null -ne $staged -and
                        -not [string]::IsNullOrWhiteSpace([string]$staged.stage) -and
                        -not [string]::IsNullOrWhiteSpace([string]$staged.staging_root)) {
                        Remove-SecureBootstrapStaging ([string]$staged.stage) ([string]$staged.staging_root)
                    }
                }
            }
            $verified = Expand-AndVerifySignedArchive $manifestValue $ArchivePath $false
            [ordered]@{
                schema_version = 1
                status = [string]$verified.status
                signature_verified = $true
                key_id = $trustedKeyId
                manifest_schema_version = 2
                publisher_attestation_bound = $true
                manifest_sha256 = $manifestSha
                archive_sha256 = [string]$verified.archive_sha256
                runtime_tree_sha256 = [string]$verified.runtime_tree_sha256
                runtime_file_count = [long]$verified.runtime_file_count
                python_entry = [string]$verified.python_entry
                staging_path_disclosed = $false
                activation_performed = $false
                real_external_actions = 0
            } | ConvertTo-Json -Compress
            exit 0
        }
        finally {
            if ($null -ne $operationLock) { $operationLock.Dispose() }
        }
    }
    [ordered]@{
        schema_version = 1
        status = "JOBFLOW_BOOTSTRAP_MANIFEST_VERIFIED"
        signature_verified = $true
        key_id = $trustedKeyId
        manifest_schema_version = 2
        publisher_attestation_bound = $true
        manifest_sha256 = $manifestSha
        manifest_bytes = $manifestBytes.Length
        real_external_actions = 0
    } | ConvertTo-Json -Compress
    exit 0
}
catch {
    if ($_.Exception.Message -ceq "JOBFLOW_ACTIVATION_RECOVERY_FAILED") {
        [Console]::Error.WriteLine("JOBFLOW_ACTIVATION_RECOVERY_FAILED")
        exit 3
    }
    if ($_.Exception.Message -ceq "JOBFLOW_MANUAL_MIGRATION_REQUIRED") {
        [Console]::Error.WriteLine("JOBFLOW_MANUAL_MIGRATION_REQUIRED")
        exit 2
    }
    if ($_.Exception.Message -ceq "JOBFLOW_ACTIVATION_RECOVERY_REQUIRED") {
        [Console]::Error.WriteLine("JOBFLOW_ACTIVATION_RECOVERY_REQUIRED")
        exit 3
    }
    if ($_.Exception.Message -ceq "JOBFLOW_RUNTIME_HEALTH_PRE_FAILED") {
        [Console]::Error.WriteLine("JOBFLOW_RUNTIME_HEALTH_PRE_FAILED")
        exit 4
    }
    if ($_.Exception.Message -ceq "JOBFLOW_RUNTIME_HEALTH_POST_FAILED") {
        [Console]::Error.WriteLine("JOBFLOW_RUNTIME_HEALTH_POST_FAILED")
        exit 5
    }
    if ($_.Exception.Message -ceq "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED") {
        [Console]::Error.WriteLine("JOBFLOW_ROLLBACK_RECOVERY_REQUIRED")
        exit 3
    }
    [Console]::Error.WriteLine("JOBFLOW_BOOTSTRAP_FAILED")
    exit 1
}
finally {
    if ($null -ne $manifestBytes) { [Array]::Clear($manifestBytes, 0, $manifestBytes.Length) }
    if ($null -ne $signatureEnvelopeBytes) { [Array]::Clear($signatureEnvelopeBytes, 0, $signatureEnvelopeBytes.Length) }
    if ($null -ne $modulus) { [Array]::Clear($modulus, 0, $modulus.Length) }
    if ($null -ne $exponent) { [Array]::Clear($exponent, 0, $exponent.Length) }
    if ($null -ne $signature) { [Array]::Clear($signature, 0, $signature.Length) }
}
