[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sourcePath = Join-Path $PSScriptRoot "native-messaging\JobFlowBrowserCompanionHost.cs"
$storeIdentityPath = Join-Path $projectRoot "config\browser-companion-stores.json"
$projectMarkerPath = Join-Path $projectRoot ".jobops-root"
$hostName = "com.jobflow.browser_companion"

function Assert-JobFlowSourcePath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($projectRoot)
    $prefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
        -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_NATIVE_HOST_SOURCE_PATH_FORBIDDEN"
    }
    $cursor = $absolute
    while ($true) {
        if (-not (Test-Path -LiteralPath $cursor)) {
            throw "JOBFLOW_NATIVE_HOST_SOURCE_PATH_NOT_FOUND"
        }
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_NATIVE_HOST_SOURCE_REPARSE_FORBIDDEN"
        }
        if ($cursor.Equals($root, [StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent)) {
            throw "JOBFLOW_NATIVE_HOST_SOURCE_PATH_FORBIDDEN"
        }
        $cursor = $parent
    }
}

if (-not (Test-Path -LiteralPath $projectMarkerPath -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "JOBFLOW_NATIVE_HOST_SOURCE_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $storeIdentityPath -PathType Leaf)) {
    throw "JOBFLOW_BROWSER_STORE_IDENTITY_NOT_FOUND"
}
foreach ($path in @($projectRoot, $projectMarkerPath, $PSScriptRoot, $sourcePath, $storeIdentityPath)) {
    Assert-JobFlowSourcePath $path
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND"
}

$localAppDataRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
$localRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobOps"))
$hostRoot = [IO.Path]::GetFullPath((Join-Path $localRoot "BrowserCompanionHost"))
$installId = [Guid]::NewGuid().ToString("N")
$stagingRoot = [IO.Path]::GetFullPath((Join-Path $localRoot (".BrowserCompanionHost.install-" + $installId)))
$backupRoot = [IO.Path]::GetFullPath((Join-Path $localRoot (".BrowserCompanionHost.backup-" + $installId)))
$hostExecutable = Join-Path $hostRoot "JobFlowBrowserCompanionHost.exe"
$hostManifest = Join-Path $hostRoot ($hostName + ".json")
$stagedExecutable = Join-Path $stagingRoot "JobFlowBrowserCompanionHost.exe"
$stagedManifest = Join-Path $stagingRoot ($hostName + ".json")
$registrySubkeys = @(
    "Software\Google\Chrome\NativeMessagingHosts\$hostName",
    "Software\Microsoft\Edge\NativeMessagingHosts\$hostName"
)

function Assert-ExistingAncestorChainNoReparse([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_NATIVE_HOST_LOCAL_APP_DATA_REPARSE_FORBIDDEN"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Initialize-JobFlowNativeHostFileIdentityApi {
    if ($null -ne ("JobFlowNativeHostInstallerNative.FileIdentity" -as [type])) { return }
    Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace JobFlowNativeHostInstallerNative {
    [StructLayout(LayoutKind.Sequential)] public struct FileTime { public uint Low; public uint High; }
    [StructLayout(LayoutKind.Sequential)] public struct FileIdentity {
        public uint Attributes; public FileTime CreationTime; public FileTime LastAccessTime;
        public FileTime LastWriteTime; public uint VolumeSerialNumber; public uint SizeHigh;
        public uint SizeLow; public uint LinkCount; public uint FileIndexHigh; public uint FileIndexLow;
    }
    public static class FileIdentityApi {
        [DllImport("kernel32.dll", SetLastError=true)]
        public static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileIdentity information);
    }
}
"@ -ErrorAction Stop
}

function Get-OpenNativeHostFileLinkCount([IO.FileStream]$Stream, [string]$Code) {
    Initialize-JobFlowNativeHostFileIdentityApi
    $information = New-Object JobFlowNativeHostInstallerNative.FileIdentity
    if (-not [JobFlowNativeHostInstallerNative.FileIdentityApi]::GetFileInformationByHandle(
        $Stream.SafeFileHandle, [ref]$information
    )) { throw $Code }
    return [long]$information.LinkCount
}

function Assert-SingleLinkNativeHostLeaf([string]$Path, [string]$Code) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try { if ((Get-OpenNativeHostFileLinkCount $stream $Code) -ne 1) { throw $Code } }
    finally { $stream.Dispose() }
}

function Assert-JobFlowLocalPath([string]$Path) {
    Assert-ExistingAncestorChainNoReparse $localAppDataRoot
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.Equals($localRoot, [StringComparison]::OrdinalIgnoreCase) -and
        -not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_NATIVE_HOST_PATH_FORBIDDEN"
    }
    $cursor = $resolved
    while ($cursor -and $cursor.StartsWith($localRoot, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_NATIVE_HOST_REPARSE_FORBIDDEN"
            }
        }
        if ($cursor -eq $localRoot) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Assert-JobFlowLocalTree([string]$Path) {
    Assert-JobFlowLocalPath $Path
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $rootItem = Get-Item -LiteralPath $Path -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_NATIVE_HOST_REPARSE_FORBIDDEN"
    }
    if (-not $rootItem.PSIsContainer) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
        Assert-JobFlowLocalPath $item.FullName
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_NATIVE_HOST_REPARSE_FORBIDDEN"
        }
        if (-not $item.PSIsContainer) {
            Assert-SingleLinkNativeHostLeaf $item.FullName "JOBFLOW_NATIVE_HOST_HARDLINK_FORBIDDEN"
        }
    }
}

function Set-CurrentUserOnly([string]$Path) {
    Assert-JobFlowLocalPath $Path
    if (Test-Path -LiteralPath $Path -PathType Container) {
        Assert-JobFlowLocalTree $Path
    }
    else {
        Assert-SingleLinkNativeHostLeaf $Path "JOBFLOW_NATIVE_HOST_HARDLINK_FORBIDDEN"
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sid = $identity.User
    $item = Get-Item -LiteralPath $Path -Force
    $directories = @()
    $files = @()
    if ($item.PSIsContainer) {
        $directories = @($item) + @(Get-ChildItem -LiteralPath $Path -Directory -Recurse -Force)
        $files = @(Get-ChildItem -LiteralPath $Path -File -Recurse -Force)
    }
    else {
        $files = @($item)
    }
    foreach ($directory in $directories) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit),
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $acl.SetAccessRule($rule)
        [IO.Directory]::SetAccessControl($directory.FullName, $acl)
    }
    foreach ($file in $files) {
        $acl = New-Object Security.AccessControl.FileSecurity
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $acl.SetAccessRule($rule)
        [IO.File]::SetAccessControl($file.FullName, $acl)
    }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace("-", "")
    }
    finally {
        $sha.Dispose()
    }
}

function Read-SourceFileCapture([string]$Path) {
    Assert-JobFlowSourcePath $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) { throw "JOBFLOW_NATIVE_HOST_SOURCE_PATH_FORBIDDEN" }
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        if ($stream.Length -ne $item.Length -or $stream.Length -gt [int]::MaxValue) {
            throw "JOBFLOW_NATIVE_HOST_SOURCE_CHANGED"
        }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) { throw "JOBFLOW_NATIVE_HOST_SOURCE_CHANGED" }
            $offset += $read
        }
        if ($stream.ReadByte() -ne -1) { throw "JOBFLOW_NATIVE_HOST_SOURCE_CHANGED" }
    }
    finally { $stream.Dispose() }
    return @{
        Bytes = $bytes
        Length = [long]$bytes.Length
        Sha256 = Get-BytesSha256 $bytes
    }
}

function Get-LocalFileEvidence([string]$Path) {
    Assert-JobFlowLocalPath $Path
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_NATIVE_HOST_REPARSE_FORBIDDEN"
    }
    Assert-SingleLinkNativeHostLeaf $Path "JOBFLOW_NATIVE_HOST_HARDLINK_FORBIDDEN"
    $bytes = [IO.File]::ReadAllBytes($Path)
    return @{ Length = [long]$bytes.Length; Sha256 = Get-BytesSha256 $bytes }
}

function Copy-LocalHostTreeCreateNew([string]$Source, [string]$Destination, [string]$Code) {
    Assert-JobFlowLocalTree $Source
    Assert-JobFlowLocalPath $Destination
    if (Test-Path -LiteralPath $Destination) { throw $Code }
    $sourceRoot = [IO.Path]::GetFullPath($Source).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $sourceItems = @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force)
    if (@($sourceItems | Where-Object { $_.PSIsContainer }).Count -ne 0) { throw $Code }
    New-Item -ItemType Directory -Path $Destination | Out-Null
    Assert-JobFlowLocalTree $Destination
    $expectedNames = New-Object 'System.Collections.Generic.List[string]'
    foreach ($file in @($sourceItems | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($sourceRoot.Length + 1)
        $expectedNames.Add($relative.Replace([IO.Path]::DirectorySeparatorChar, '/'))
        $target = Join-Path $Destination $relative
        Assert-JobFlowLocalPath $target
        Assert-SingleLinkNativeHostLeaf $file.FullName $Code
        $expected = Get-LocalFileEvidence $file.FullName
        $sourceStream = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $targetStream = $null
        try {
            if ((Get-OpenNativeHostFileLinkCount $sourceStream $Code) -ne 1 -or
                $sourceStream.Length -ne [long]$expected.Length) { throw $Code }
            $targetStream = [IO.File]::Open(
                $target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
            )
            $sourceStream.CopyTo($targetStream)
            $targetStream.Flush($true)
        }
        finally {
            if ($null -ne $targetStream) { $targetStream.Dispose() }
            $sourceStream.Dispose()
        }
        $actual = Get-LocalFileEvidence $target
        if ($actual.Length -ne $expected.Length -or $actual.Sha256 -cne $expected.Sha256) { throw $Code }
    }
    Assert-JobFlowLocalTree $Destination
    $destinationRoot = [IO.Path]::GetFullPath($Destination).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $actualNames = @(Get-ChildItem -LiteralPath $destinationRoot -File -Recurse -Force | ForEach-Object {
        $_.FullName.Substring($destinationRoot.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/')
    } | Sort-Object)
    if (($actualNames -join "`n") -cne (@($expectedNames | Sort-Object) -join "`n")) { throw $Code }
}

function Assert-InstalledHostSnapshot([string]$Root, [hashtable]$Expected) {
    Assert-JobFlowLocalTree $Root
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "JOBFLOW_NATIVE_HOST_BUILD_FAILED"
    }
    $actualItems = @(Get-ChildItem -LiteralPath $Root -Recurse -Force)
    if (@($actualItems | Where-Object { $_.PSIsContainer }).Count -gt 0) {
        throw "JOBFLOW_NATIVE_HOST_STAGED_SNAPSHOT_INVALID"
    }
    $actualFiles = @($actualItems | Where-Object { -not $_.PSIsContainer })
    $actualRelative = @($actualFiles | ForEach-Object {
        $_.FullName.Substring($Root.TrimEnd('\').Length + 1).Replace('\', '/')
    } | Sort-Object)
    $expectedRelative = @($Expected.Keys | Sort-Object)
    if (($actualRelative -join "`n") -cne ($expectedRelative -join "`n")) {
        throw "JOBFLOW_NATIVE_HOST_STAGED_SNAPSHOT_INVALID"
    }
    foreach ($relative in $expectedRelative) {
        $path = Join-Path $Root ($relative.Replace('/', '\'))
        $actual = Get-LocalFileEvidence $path
        if ($actual.Length -ne $Expected[$relative].Length -or
            $actual.Sha256 -cne $Expected[$relative].Sha256) {
            throw "JOBFLOW_NATIVE_HOST_STAGED_SNAPSHOT_INVALID"
        }
    }
}

function Test-JsonIntegerOne([object]$Value) {
    return (($Value -is [int]) -or ($Value -is [long])) -and ([long]$Value -eq 1)
}

function Enter-NativeHostInstallMutex() {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    # Global scope prevents two sessions owned by the same Windows user from
    # racing the shared per-user host directory and HKCU registration.
    $name = "Global\JobFlow.NativeHostInstaller." + ($sid -replace '[^A-Za-z0-9_.-]', '_')
    $mutex = New-Object Threading.Mutex($false, $name)
    try {
        try { $acquired = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw "JOBFLOW_NATIVE_HOST_INSTALL_BUSY" }
        return $mutex
    }
    catch {
        $mutex.Dispose()
        throw
    }
}

function Open-RegistryKey([string]$Subkey, [bool]$Writable) {
    return [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Subkey, $Writable)
}

function New-RegistryKey([string]$Subkey) {
    return [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($Subkey, $true)
}

function Remove-RegistryKey([string]$Subkey) {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($Subkey, $false)
}

function Read-RegistryDefault([string]$Subkey) {
    $key = Open-RegistryKey $Subkey $false
    if ($null -eq $key) {
        return @{ KeyExists = $false; DefaultExists = $false; Value = $null; Kind = $null }
    }
    try {
        $defaultExists = @($key.GetValueNames()) -contains ""
        return @{
            KeyExists = $true
            DefaultExists = $defaultExists
            Value = if ($defaultExists) {
                $key.GetValue("", $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            } else { $null }
            Kind = if ($defaultExists) { $key.GetValueKind("") } else { $null }
        }
    }
    finally { $key.Dispose() }
}

function Write-RegistryDefault([string]$Subkey, [string]$Value) {
    $key = New-RegistryKey $Subkey
    if ($null -eq $key) { throw "JOBFLOW_NATIVE_HOST_REGISTRY_FAILED" }
    try { $key.SetValue("", $Value, [Microsoft.Win32.RegistryValueKind]::String) }
    finally { $key.Dispose() }
}

function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) {
    if (-not $Previous.KeyExists) {
        Remove-RegistryKey $Subkey
        return
    }
    $key = New-RegistryKey $Subkey
    if ($null -eq $key) { throw "JOBFLOW_NATIVE_HOST_REGISTRY_FAILED" }
    try {
        if ($Previous.DefaultExists) {
            $key.SetValue("", $Previous.Value, [Microsoft.Win32.RegistryValueKind]$Previous.Kind)
        }
        else {
            $key.DeleteValue("", $false)
        }
    }
    finally { $key.Dispose() }
}

foreach ($path in @($hostRoot, $stagingRoot, $backupRoot, $hostExecutable, $hostManifest)) {
    Assert-JobFlowLocalPath $path
}
$registryBackup = @{}
$registryBackupCaptured = $false
$registryMutationStarted = $false

$hostInstalled = $false
$hostBackedUp = $false
$installationCommitted = $false
$rollbackComplete = $false
$preserveHostBackup = $false
$installMutex = $null
$installMutexHeld = $false
try {
    $installMutex = Enter-NativeHostInstallMutex
    $installMutexHeld = $true
    # Do not create even the shared JobOps root before owning the same mutex as
    # uninstall. Otherwise a losing concurrent installer can recreate an empty
    # root after uninstall has removed it.
    New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
    # The native-host installer owns only BrowserCompanionHost and its randomized
    # staging/backup siblings. Do not traverse unrelated JobOps data here.
    Assert-JobFlowLocalPath $localRoot
    $sourceCapture = Read-SourceFileCapture $sourcePath
    $storeIdentityCapture = Read-SourceFileCapture $storeIdentityPath
    $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
    try {
        $sourceText = $strictUtf8.GetString($sourceCapture.Bytes)
        $storeIdentityText = $strictUtf8.GetString($storeIdentityCapture.Bytes)
    }
    catch { throw "JOBFLOW_BROWSER_STORE_IDENTITY_INVALID" }

    try { $identity = $storeIdentityText | ConvertFrom-Json }
    catch { throw "JOBFLOW_BROWSER_STORE_IDENTITY_INVALID" }
    $identityProperties = @($identity.PSObject.Properties.Name)
    if (-not ($identityProperties -contains "schema_version") -or
        -not ($identityProperties -contains "native_host_name") -or
        -not ($identityProperties -contains "extension_ids") -or
        -not (Test-JsonIntegerOne $identity.schema_version) -or
        -not ($identity.native_host_name -is [string]) -or
        $identity.native_host_name -cne $hostName -or
        -not ($identity.extension_ids -is [System.Array])) {
        throw "JOBFLOW_BROWSER_STORE_IDENTITY_INVALID"
    }
    $extensionIds = @($identity.extension_ids)
    if ($extensionIds.Count -lt 1 -or
        ($extensionIds | Where-Object {
            -not ($_ -is [string]) -or $_ -cnotmatch '^[a-p]{32}$'
        }).Count -gt 0 -or
        (@($extensionIds | Select-Object -Unique).Count -ne $extensionIds.Count)) {
        throw "JOBFLOW_BROWSER_STORE_IDENTITY_INVALID"
    }
    $allowedOrigins = @($extensionIds | ForEach-Object { "chrome-extension://$_/" })

    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    Assert-JobFlowLocalTree $stagingRoot
    foreach ($leaf in @($stagedExecutable, $stagedManifest)) {
        Assert-JobFlowLocalPath $leaf
        if (Test-Path -LiteralPath $leaf) { throw "JOBFLOW_NATIVE_HOST_STAGED_SNAPSHOT_INVALID" }
    }
    Add-Type -TypeDefinition $sourceText -ReferencedAssemblies @("System.Web.Extensions") -OutputAssembly $stagedExecutable -OutputType ConsoleApplication
    if (-not (Test-Path -LiteralPath $stagedExecutable -PathType Leaf)) {
        throw "JOBFLOW_NATIVE_HOST_BUILD_FAILED"
    }
    $manifestValue = [ordered]@{
        name = $hostName
        description = "JobFlow local installation binding host"
        path = $hostExecutable
        type = "stdio"
        allowed_origins = $allowedOrigins
    }
    $manifestJson = $manifestValue | ConvertTo-Json -Depth 4 -Compress
    [IO.File]::WriteAllText($stagedManifest, $manifestJson, (New-Object Text.UTF8Encoding($false)))
    $stagedExpected = @{
        "JobFlowBrowserCompanionHost.exe" = Get-LocalFileEvidence $stagedExecutable
        ($hostName + ".json") = Get-LocalFileEvidence $stagedManifest
    }
    Assert-InstalledHostSnapshot $stagingRoot $stagedExpected
    Set-CurrentUserOnly $stagingRoot
    Assert-InstalledHostSnapshot $stagingRoot $stagedExpected

    foreach ($subkey in $registrySubkeys) { $registryBackup[$subkey] = Read-RegistryDefault $subkey }
    $registryBackupCaptured = $true

    if (Test-Path -LiteralPath $hostRoot) {
        Assert-JobFlowLocalTree $hostRoot
        if (-not (Test-Path -LiteralPath $hostRoot -PathType Container)) {
            throw "JOBFLOW_NATIVE_HOST_PATH_FORBIDDEN"
        }
        if (Test-Path -LiteralPath $backupRoot) {
            throw "JOBFLOW_NATIVE_HOST_PATH_FORBIDDEN"
        }
        Move-Item -LiteralPath $hostRoot -Destination $backupRoot
        $hostBackedUp = $true
        Assert-JobFlowLocalTree $backupRoot
    }
    Assert-InstalledHostSnapshot $stagingRoot $stagedExpected
    Move-Item -LiteralPath $stagingRoot -Destination $hostRoot
    $hostInstalled = $true
    Assert-InstalledHostSnapshot $hostRoot $stagedExpected
    Set-CurrentUserOnly $hostRoot
    Assert-InstalledHostSnapshot $hostRoot $stagedExpected
    $registryMutationStarted = $true
    foreach ($subkey in $registrySubkeys) { Write-RegistryDefault $subkey $hostManifest }
    $installationCommitted = $true
}
catch {
    $originalFailure = $_.Exception
    $rollbackFailures = New-Object 'System.Collections.Generic.List[string]'
    if ($registryBackupCaptured -and $registryMutationStarted) {
        foreach ($subkey in $registrySubkeys) {
            try { Restore-RegistryDefault $subkey $registryBackup[$subkey] }
            catch { [void]$rollbackFailures.Add("registry:$subkey") }
        }
    }
    if ($hostInstalled -and (Test-Path -LiteralPath $hostRoot -PathType Container)) {
        try {
            Assert-JobFlowLocalTree $hostRoot
            Set-CurrentUserOnly $hostRoot
            Remove-Item -LiteralPath $hostRoot -Recurse -Force
        }
        catch { [void]$rollbackFailures.Add("active-host") }
    }
    if ($hostBackedUp) {
        if (-not (Test-Path -LiteralPath $backupRoot -PathType Container)) {
            [void]$rollbackFailures.Add("host-backup-missing")
        }
        else {
            try {
                Assert-JobFlowLocalTree $backupRoot
                if (Test-Path -LiteralPath $hostRoot) {
                    throw "JOBFLOW_NATIVE_HOST_ROLLBACK_TARGET_OCCUPIED"
                }
                if (Test-Path -LiteralPath $stagingRoot) {
                    throw "JOBFLOW_NATIVE_HOST_ROLLBACK_STAGING_OCCUPIED"
                }
                Copy-LocalHostTreeCreateNew $backupRoot $stagingRoot "JOBFLOW_NATIVE_HOST_HARDLINK_FORBIDDEN"
                Move-Item -LiteralPath $stagingRoot -Destination $hostRoot
                Assert-JobFlowLocalTree $hostRoot
                Set-CurrentUserOnly $hostRoot
            }
            catch { [void]$rollbackFailures.Add("host-backup") }
        }
    }
    if ($rollbackFailures.Count -gt 0) {
        $preserveHostBackup = $hostBackedUp -and (Test-Path -LiteralPath $backupRoot -PathType Container)
        throw "JOBFLOW_NATIVE_HOST_ROLLBACK_FAILED"
    }
    $rollbackComplete = $true
    throw $originalFailure
}
finally {
    if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
        try {
            Assert-JobFlowLocalTree $stagingRoot
            Set-CurrentUserOnly $stagingRoot
            Remove-Item -LiteralPath $stagingRoot -Recurse -Force
        }
        catch { Write-Warning "JOBFLOW_NATIVE_HOST_STAGING_CLEANUP_FAILED" }
    }
    if (Test-Path -LiteralPath $backupRoot -PathType Container) {
        if ($preserveHostBackup) {
            Write-Warning "JOBFLOW_NATIVE_HOST_BACKUP_PRESERVED"
        }
        else {
            try {
                Assert-JobFlowLocalTree $backupRoot
                Set-CurrentUserOnly $backupRoot
                Remove-Item -LiteralPath $backupRoot -Recurse -Force
            }
            catch {
                Write-Warning "JOBFLOW_NATIVE_HOST_BACKUP_CLEANUP_FAILED"
            }
        }
    }
    if ($installMutexHeld -and $null -ne $installMutex) {
        try { $installMutex.ReleaseMutex() }
        catch { Write-Warning "JOBFLOW_NATIVE_HOST_MUTEX_RELEASE_FAILED" }
        $installMutexHeld = $false
    }
    if ($null -ne $installMutex) { $installMutex.Dispose() }
}

Write-Host "JobFlow Browser Companion native host is registered for Chrome and Edge."
exit 0
