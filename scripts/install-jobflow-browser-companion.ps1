[CmdletBinding()]
param(
    [switch]$NoLaunch,
    [switch]$Development,
    [switch]$OpenStoreOnly,
    [ValidateSet("auto", "chrome", "edge")]
    [string]$PreferredBrowser = "auto"
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sourceExtensionRoot = Join-Path $projectRoot "browser-companion"
$sourceManifestPath = Join-Path $sourceExtensionRoot "manifest.json"
$expectedId = "hhlliaaafegldkmcgmaoaelabipcaooj"
$storeConfigPath = Join-Path $projectRoot "config\browser-companion-stores.json"
$projectMarkerPath = Join-Path $projectRoot ".jobops-root"

function Assert-JobFlowSourcePath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($projectRoot)
    $prefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.Equals($root, [StringComparison]::OrdinalIgnoreCase) -and
        -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_BROWSER_COMPANION_SOURCE_PATH_FORBIDDEN"
    }
    $cursor = $absolute
    while ($true) {
        if (-not (Test-Path -LiteralPath $cursor)) {
            throw "JOBFLOW_BROWSER_COMPANION_SOURCE_PATH_NOT_FOUND"
        }
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_BROWSER_COMPANION_SOURCE_REPARSE_FORBIDDEN"
        }
        if ($cursor.Equals($root, [StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent)) {
            throw "JOBFLOW_BROWSER_COMPANION_SOURCE_PATH_FORBIDDEN"
        }
        $cursor = $parent
    }
}

function Assert-JobFlowSourceTree([string]$Root) {
    Assert-JobFlowSourcePath $Root
    $pending = New-Object 'System.Collections.Generic.List[string]'
    $pending.Add([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $index = $pending.Count - 1
        $current = $pending[$index]
        $pending.RemoveAt($index)
        foreach ($child in @(Get-ChildItem -LiteralPath $current -Force)) {
            Assert-JobFlowSourcePath $child.FullName
            if ($child.PSIsContainer) {
                $pending.Add($child.FullName)
            }
        }
    }
}

function Get-JobFlowStreamSha256([IO.Stream]$Stream) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return -join ($hasher.ComputeHash($Stream) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hasher.Dispose()
    }
}

function Get-JobFlowBytesSha256([byte[]]$Bytes) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return -join ($hasher.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hasher.Dispose()
    }
}

function Get-JobFlowSourceRelativePath([string]$Root, [string]$Path) {
    $absoluteRoot = [IO.Path]::GetFullPath($Root)
    $absolutePath = [IO.Path]::GetFullPath($Path)
    $prefix = $absoluteRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolutePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_BROWSER_COMPANION_SOURCE_PATH_FORBIDDEN"
    }
    return $absolutePath.Substring($prefix.Length).Replace([IO.Path]::DirectorySeparatorChar, '/')
}

function Get-JobFlowSourceFiles([string]$Root) {
    Assert-JobFlowSourceTree $Root
    $files = New-Object 'System.Collections.Generic.List[string]'
    $pending = New-Object 'System.Collections.Generic.List[string]'
    $pending.Add([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $index = $pending.Count - 1
        $current = $pending[$index]
        $pending.RemoveAt($index)
        foreach ($child in @(Get-ChildItem -LiteralPath $current -Force)) {
            Assert-JobFlowSourcePath $child.FullName
            if ($child.PSIsContainer) {
                $pending.Add($child.FullName)
            }
            else {
                $relative = Get-JobFlowSourceRelativePath $Root $child.FullName
                if (-not $relative.Equals("binding.json", [StringComparison]::OrdinalIgnoreCase)) {
                    $files.Add($child.FullName)
                }
            }
        }
    }
    return @($files | Sort-Object)
}

function Get-JobFlowSourceFileIdentity([string]$Path) {
    Assert-JobFlowSourcePath $Path
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        Assert-JobFlowSourcePath $Path
        $length = $stream.Length
        $sha256 = Get-JobFlowStreamSha256 $stream
        return [pscustomobject]@{ Length = $length; Sha256 = $sha256 }
    }
    finally {
        $stream.Dispose()
    }
}

function Get-JobFlowSourceSnapshot([string]$Root) {
    $snapshot = @()
    foreach ($path in @(Get-JobFlowSourceFiles $Root)) {
        $identity = Get-JobFlowSourceFileIdentity $path
        $snapshot += [pscustomobject]@{
            RelativePath = Get-JobFlowSourceRelativePath $Root $path
            Length = [long]$identity.Length
            Sha256 = [string]$identity.Sha256
        }
    }
    return @($snapshot | Sort-Object RelativePath)
}

function Assert-JobFlowSourceSnapshot([string]$Root, [object[]]$Snapshot) {
    $expected = @{}
    foreach ($entry in @($Snapshot)) {
        if ($expected.ContainsKey([string]$entry.RelativePath)) {
            throw "JOBFLOW_BROWSER_COMPANION_SOURCE_CHANGED"
        }
        $expected[[string]$entry.RelativePath] = $entry
    }
    $currentFiles = @(Get-JobFlowSourceFiles $Root)
    if ($currentFiles.Count -ne $expected.Count) {
        throw "JOBFLOW_BROWSER_COMPANION_SOURCE_CHANGED"
    }
    foreach ($path in $currentFiles) {
        $relative = Get-JobFlowSourceRelativePath $Root $path
        if (-not $expected.ContainsKey($relative)) {
            throw "JOBFLOW_BROWSER_COMPANION_SOURCE_CHANGED"
        }
        $identity = Get-JobFlowSourceFileIdentity $path
        $entry = $expected[$relative]
        if ([long]$identity.Length -ne [long]$entry.Length -or
            -not ([string]$identity.Sha256).Equals([string]$entry.Sha256, [StringComparison]::Ordinal)) {
            throw "JOBFLOW_BROWSER_COMPANION_SOURCE_CHANGED"
        }
    }
}

if (-not (Test-Path -LiteralPath $projectMarkerPath -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $sourceManifestPath -PathType Leaf)) {
    throw "JOBFLOW_BROWSER_COMPANION_NOT_FOUND"
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $storeConfigPath -PathType Leaf)) {
    throw "JOBFLOW_BROWSER_COMPANION_STORE_CONFIG_NOT_FOUND"
}
foreach ($sourcePath in @($projectRoot, $projectMarkerPath, $sourceExtensionRoot, $sourceManifestPath, $storeConfigPath)) {
    Assert-JobFlowSourcePath $sourcePath
}
Assert-JobFlowSourceTree $sourceExtensionRoot
$storeConfig = Get-Content -LiteralPath $storeConfigPath -Raw | ConvertFrom-Json
$chromeStoreUrl = [string]$storeConfig.chrome_web_store_url
$edgeStoreUrl = [string]$storeConfig.edge_addons_url

function Assert-OfficialStoreUrl([string]$Url, [string]$ExpectedHost, [string]$ExpectedPath) {
    try { $uri = [Uri]$Url }
    catch { throw "JOBFLOW_BROWSER_COMPANION_STORE_CONFIG_INVALID" }
    if (
        -not $uri.IsAbsoluteUri -or
        $uri.Scheme -ne "https" -or
        -not $uri.IsDefaultPort -or
        -not [string]::IsNullOrWhiteSpace($uri.UserInfo) -or
        -not $uri.Host.Equals($ExpectedHost, [StringComparison]::OrdinalIgnoreCase) -or
        -not $uri.AbsolutePath.TrimEnd('/').Equals($ExpectedPath, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::IsNullOrWhiteSpace($uri.Query) -or
        -not [string]::IsNullOrWhiteSpace($uri.Fragment)
    ) {
        throw "JOBFLOW_BROWSER_COMPANION_STORE_CONFIG_INVALID"
    }
}

function Get-CanonicalSpecialFolderRoot([Environment+SpecialFolder]$Folder) {
    $root = [Environment]::GetFolderPath($Folder)
    if ([string]::IsNullOrWhiteSpace($root)) { return $null }
    $absolute = [IO.Path]::GetFullPath($root)
    if (-not (Test-Path -LiteralPath $absolute -PathType Container)) { return $null }
    $item = Get-Item -LiteralPath $absolute -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
    return $absolute
}

function Import-TrustedAuthenticodeModule {
    if ($script:jobFlowAuthenticodeModuleLoaded) { return }
    $systemDirectory = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
    $modulePath = [IO.Path]::GetFullPath((Join-Path $systemDirectory "WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"))
    $prefix = $systemDirectory.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $modulePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_TRUSTED_AUTHENTICODE_MODULE_INVALID"
    }
    $volumeRoot = [IO.Path]::GetPathRoot($modulePath)
    $cursor = $modulePath
    while ($true) {
        if (-not (Test-Path -LiteralPath $cursor)) { throw "JOBFLOW_TRUSTED_AUTHENTICODE_MODULE_INVALID" }
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_TRUSTED_AUTHENTICODE_MODULE_INVALID"
        }
        if ($cursor.Equals($volumeRoot, [StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent)) { throw "JOBFLOW_TRUSTED_AUTHENTICODE_MODULE_INVALID" }
        $cursor = $parent
    }
    Microsoft.PowerShell.Core\Import-Module -Name $modulePath -Force -ErrorAction Stop
    $script:jobFlowAuthenticodeModuleLoaded = $true
}

function Assert-TrustedExecutablePath(
    [string]$Path,
    [string]$CanonicalRoot,
    [string[]]$AllowedCommonNames,
    [string]$ExpectedOrganization,
    [string]$FailureCode
) {
    if ([string]::IsNullOrWhiteSpace($Path) -or [string]::IsNullOrWhiteSpace($CanonicalRoot)) {
        throw $FailureCode
    }
    $absolute = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($CanonicalRoot)
    $prefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw $FailureCode
    }
    if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) { throw $FailureCode }

    $volumeRoot = [IO.Path]::GetPathRoot($absolute)
    $cursor = $absolute
    while ($true) {
        if (-not (Test-Path -LiteralPath $cursor)) { throw $FailureCode }
        $item = Get-Item -LiteralPath $cursor -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $FailureCode }
        if ($cursor.Equals($volumeRoot, [StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent)) { throw $FailureCode }
        $cursor = $parent
    }

    $leaf = Get-Item -LiteralPath $absolute -Force
    if ($leaf.PSIsContainer -or -not ($leaf -is [IO.FileInfo])) { throw $FailureCode }
    Import-TrustedAuthenticodeModule
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $absolute
    if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or $null -eq $signature.SignerCertificate) {
        throw $FailureCode
    }
    $commonName = $signature.SignerCertificate.GetNameInfo(
        [Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
        $false
    )
    if ($AllowedCommonNames -notcontains $commonName) { throw $FailureCode }
    $organizationPattern = "(?:^|,\s*)O=" + [Regex]::Escape($ExpectedOrganization) + "(?:,|$)"
    if ([string]$signature.SignerCertificate.Subject -notmatch $organizationPattern) { throw $FailureCode }
    return $absolute
}

function New-TrustedExecutableTarget(
    [string]$Path,
    [string]$CanonicalRoot,
    [string[]]$AllowedCommonNames,
    [string]$ExpectedOrganization,
    [string]$FailureCode
) {
    $trustedPath = Assert-TrustedExecutablePath $Path $CanonicalRoot $AllowedCommonNames $ExpectedOrganization $FailureCode
    return [pscustomobject]@{
        Path = $trustedPath
        CanonicalRoot = [IO.Path]::GetFullPath($CanonicalRoot)
        AllowedCommonNames = @($AllowedCommonNames)
        ExpectedOrganization = $ExpectedOrganization
        FailureCode = $FailureCode
    }
}

function Open-TrustedExecutableLock($Target) {
    Assert-TrustedExecutablePath `
        $Target.Path `
        $Target.CanonicalRoot `
        @($Target.AllowedCommonNames) `
        $Target.ExpectedOrganization `
        $Target.FailureCode | Out-Null
    return [IO.File]::Open(
        $Target.Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
}

function Start-TrustedExecutable($Target, [object[]]$ArgumentList) {
    $executableLock = Open-TrustedExecutableLock $Target
    try {
        Assert-TrustedExecutablePath `
            $Target.Path `
            $Target.CanonicalRoot `
            @($Target.AllowedCommonNames) `
            $Target.ExpectedOrganization `
            $Target.FailureCode | Out-Null
        Microsoft.PowerShell.Management\Start-Process -FilePath $Target.Path -ArgumentList $ArgumentList
    }
    finally {
        $executableLock.Dispose()
    }
}

function Invoke-TrustedConsoleExecutable($Target, [string[]]$ArgumentList, [string]$FailureCode) {
    $executableLock = Open-TrustedExecutableLock $Target
    try {
        Assert-TrustedExecutablePath `
            $Target.Path `
            $Target.CanonicalRoot `
            @($Target.AllowedCommonNames) `
            $Target.ExpectedOrganization `
            $Target.FailureCode | Out-Null
        & $Target.Path @ArgumentList
        if ($LASTEXITCODE -ne 0) { throw $FailureCode }
    }
    finally {
        $executableLock.Dispose()
    }
}

function Get-TrustedBrowserExecutable([ValidateSet("chrome", "edge")][string]$Browser) {
    $roots = @()
    if ($Browser -eq "edge") {
        $roots = @(
            (Get-CanonicalSpecialFolderRoot ([Environment+SpecialFolder]::ProgramFilesX86)),
            (Get-CanonicalSpecialFolderRoot ([Environment+SpecialFolder]::ProgramFiles)),
            (Get-CanonicalSpecialFolderRoot ([Environment+SpecialFolder]::LocalApplicationData))
        )
        $relativePath = "Microsoft\Edge\Application\msedge.exe"
        $commonNames = @("Microsoft Corporation")
        $organization = "Microsoft Corporation"
        $failureCode = "JOBFLOW_TRUSTED_EDGE_EXECUTABLE_INVALID"
    }
    else {
        $roots = @(
            (Get-CanonicalSpecialFolderRoot ([Environment+SpecialFolder]::ProgramFiles)),
            (Get-CanonicalSpecialFolderRoot ([Environment+SpecialFolder]::ProgramFilesX86)),
            (Get-CanonicalSpecialFolderRoot ([Environment+SpecialFolder]::LocalApplicationData))
        )
        $relativePath = "Google\Chrome\Application\chrome.exe"
        $commonNames = @("Google LLC")
        $organization = "Google LLC"
        $failureCode = "JOBFLOW_TRUSTED_CHROME_EXECUTABLE_INVALID"
    }
    $invalidCandidateFound = $false
    foreach ($root in @($roots | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } | Select-Object -Unique)) {
        $candidate = Join-Path $root $relativePath
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            return New-TrustedExecutableTarget $candidate $root $commonNames $organization $failureCode
        }
        catch {
            $invalidCandidateFound = $true
            continue
        }
    }
    if ($invalidCandidateFound) { throw $failureCode }
    return $null
}

function Get-TrustedWindowsPowerShellExecutable {
    $systemRoot = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
    $candidate = Join-Path $systemRoot "WindowsPowerShell\v1.0\powershell.exe"
    return New-TrustedExecutableTarget `
        $candidate `
        $systemRoot `
        @("Microsoft Windows") `
        "Microsoft Corporation" `
        "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_INVALID"
}

function Get-TrustedWindowsExplorerExecutable {
    $systemDirectory = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
    $windowsRoot = [IO.Path]::GetDirectoryName($systemDirectory)
    $candidate = Join-Path $windowsRoot "explorer.exe"
    return New-TrustedExecutableTarget `
        $candidate `
        $windowsRoot `
        @("Microsoft Windows") `
        "Microsoft Corporation" `
        "JOBFLOW_TRUSTED_WINDOWS_EXPLORER_INVALID"
}

function Get-TrustedWindowsIcaclsExecutable {
    $systemDirectory = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
    $candidate = Join-Path $systemDirectory "icacls.exe"
    return New-TrustedExecutableTarget `
        $candidate `
        $systemDirectory `
        @("Microsoft Windows") `
        "Microsoft Corporation" `
        "JOBFLOW_TRUSTED_WINDOWS_ICACLS_INVALID"
}

function Get-DefaultBrowserKind {
    try {
        $choice = Get-ItemProperty -LiteralPath "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice" -ErrorAction Stop
        $programId = [string]$choice.ProgId
        if ($programId -match "^ChromeHTML") { return "chrome" }
        if ($programId -match "^MSEdgeHTM") { return "edge" }
    }
    catch { }
    return "unknown"
}

function Get-BrowserLaunchTarget([string]$RequestedBrowser = "auto") {
    $preferred = if ($RequestedBrowser -eq "auto") { Get-DefaultBrowserKind } else { $RequestedBrowser }
    if ($RequestedBrowser -eq "chrome") {
        $chrome = Get-TrustedBrowserExecutable "chrome"
        if ($null -eq $chrome) { throw "JOBFLOW_TRUSTED_CHROME_REQUIRED" }
        $chrome | Add-Member -NotePropertyName Kind -NotePropertyValue "chrome"
        $chrome | Add-Member -NotePropertyName StoreUrl -NotePropertyValue $chromeStoreUrl
        $chrome | Add-Member -NotePropertyName ManagementUrl -NotePropertyValue "chrome://extensions/"
        return $chrome
    }
    if ($RequestedBrowser -eq "edge") {
        $edge = Get-TrustedBrowserExecutable "edge"
        if ($null -eq $edge) { throw "JOBFLOW_TRUSTED_EDGE_REQUIRED" }
        $edge | Add-Member -NotePropertyName Kind -NotePropertyValue "edge"
        $edge | Add-Member -NotePropertyName StoreUrl -NotePropertyValue $edgeStoreUrl
        $edge | Add-Member -NotePropertyName ManagementUrl -NotePropertyValue "edge://extensions/"
        return $edge
    }

    $edgeFailure = $null
    $chromeFailure = $null
    try { $edge = Get-TrustedBrowserExecutable "edge" }
    catch { $edge = $null; $edgeFailure = $_ }
    try { $chrome = Get-TrustedBrowserExecutable "chrome" }
    catch { $chrome = $null; $chromeFailure = $_ }
    if ($preferred -eq "chrome" -and $null -ne $chrome) {
        $chrome | Add-Member -NotePropertyName Kind -NotePropertyValue "chrome"
        $chrome | Add-Member -NotePropertyName StoreUrl -NotePropertyValue $chromeStoreUrl
        $chrome | Add-Member -NotePropertyName ManagementUrl -NotePropertyValue "chrome://extensions/"
        return $chrome
    }
    if ($preferred -eq "edge" -and $null -ne $edge) {
        $edge | Add-Member -NotePropertyName Kind -NotePropertyValue "edge"
        $edge | Add-Member -NotePropertyName StoreUrl -NotePropertyValue $edgeStoreUrl
        $edge | Add-Member -NotePropertyName ManagementUrl -NotePropertyValue "edge://extensions/"
        return $edge
    }
    if ($null -ne $edge) {
        $edge | Add-Member -NotePropertyName Kind -NotePropertyValue "edge"
        $edge | Add-Member -NotePropertyName StoreUrl -NotePropertyValue $edgeStoreUrl
        $edge | Add-Member -NotePropertyName ManagementUrl -NotePropertyValue "edge://extensions/"
        return $edge
    }
    if ($null -ne $chrome) {
        $chrome | Add-Member -NotePropertyName Kind -NotePropertyValue "chrome"
        $chrome | Add-Member -NotePropertyName StoreUrl -NotePropertyValue $chromeStoreUrl
        $chrome | Add-Member -NotePropertyName ManagementUrl -NotePropertyValue "chrome://extensions/"
        return $chrome
    }
    if ($null -ne $edgeFailure) { throw $edgeFailure }
    if ($null -ne $chromeFailure) { throw $chromeFailure }
    throw "JOBFLOW_TRUSTED_EDGE_OR_CHROME_REQUIRED"
}

function Open-OfficialStorePage {
    $target = Get-BrowserLaunchTarget $PreferredBrowser
    Start-TrustedExecutable $target @("--new-window", $target.StoreUrl)
}

function Show-StoreOpenFallback {
    Write-Warning "JOBFLOW_BROWSER_COMPANION_STORE_OPEN_FAILED"
    Write-Host "JobFlow is installed, but the browser store page could not be opened automatically."
    Write-Host "Chrome: $chromeStoreUrl"
    Write-Host "Edge: $edgeStoreUrl"
}

Assert-OfficialStoreUrl $chromeStoreUrl "chromewebstore.google.com" "/detail/pgcnlkfakkacphkdojdbphccjnbbefic"
Assert-OfficialStoreUrl $edgeStoreUrl "microsoftedge.microsoft.com" "/addons/detail/cebejbohadiofomfiplljnpdefjeiccp"
if ($OpenStoreOnly) {
    if ($NoLaunch -or $Development) { throw "JOBFLOW_BROWSER_COMPANION_LAUNCH_MODE_INVALID" }
    if ($PreferredBrowser -eq "auto") {
        try { Open-OfficialStorePage }
        catch { Show-StoreOpenFallback }
    }
    else {
        Open-OfficialStorePage
    }
    exit 0
}

$localAppDataRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
$localRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobOps"))
$runtimeExtensionRoot = [IO.Path]::GetFullPath((Join-Path $localRoot "BrowserCompanion"))
$bindingPath = [IO.Path]::GetFullPath((Join-Path $localRoot "browser-companion-binding.json"))
$installId = [Guid]::NewGuid().ToString("N")
$stagingRoot = [IO.Path]::GetFullPath((Join-Path $localRoot (".BrowserCompanion.install-" + $installId)))
$runtimeBackup = [IO.Path]::GetFullPath((Join-Path $localRoot (".BrowserCompanion.backup-" + $installId)))
$bindingTemporary = [IO.Path]::GetFullPath((Join-Path $localRoot (".browser-companion-binding-" + $installId + ".tmp")))
$bindingBackup = [IO.Path]::GetFullPath((Join-Path $localRoot (".browser-companion-binding-" + $installId + ".backup")))
$installLockPath = [IO.Path]::GetFullPath((Join-Path $localRoot ".browser-companion-install.lock"))
$installLockStream = $null
$nativeHostInstaller = Join-Path $PSScriptRoot "install-jobflow-native-host.ps1"

function Assert-ExistingCompanionAncestorChainNoReparse([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_BROWSER_COMPANION_LOCAL_APP_DATA_REPARSE_FORBIDDEN"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Initialize-JobFlowCompanionFileIdentityApi {
    if ($null -ne ("JobFlowCompanionNative.FileIdentity" -as [type])) { return }
    Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace JobFlowCompanionNative {
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

function Get-OpenCompanionFileLinkCount([IO.FileStream]$Stream, [string]$Code) {
    Initialize-JobFlowCompanionFileIdentityApi
    $information = New-Object JobFlowCompanionNative.FileIdentity
    if (-not [JobFlowCompanionNative.FileIdentityApi]::GetFileInformationByHandle(
        $Stream.SafeFileHandle, [ref]$information
    )) { throw $Code }
    return [long]$information.LinkCount
}

function Assert-SingleLinkCompanionLeaf([string]$Path, [string]$Code, [switch]$MustExist) {
    if (-not (Test-Path -LiteralPath $Path)) {
        if ($MustExist) { throw $Code }
        return
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try { if ((Get-OpenCompanionFileLinkCount $stream $Code) -ne 1) { throw $Code } }
    finally { $stream.Dispose() }
}

function Assert-JobFlowLocalPath([string]$Path) {
    Assert-ExistingCompanionAncestorChainNoReparse $localAppDataRoot
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_BROWSER_COMPANION_PATH_FORBIDDEN"
    }
    $cursor = $resolved
    while ($cursor -and $cursor.StartsWith($localRoot, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_BROWSER_COMPANION_REPARSE_FORBIDDEN"
            }
        }
        if ($cursor -eq $localRoot) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Assert-JobFlowLocalTree([string]$Root) {
    Assert-JobFlowLocalPath $Root
    $pending = New-Object 'System.Collections.Generic.List[string]'
    $pending.Add([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $index = $pending.Count - 1
        $current = $pending[$index]
        $pending.RemoveAt($index)
        foreach ($child in @(Get-ChildItem -LiteralPath $current -Force)) {
            Assert-JobFlowLocalPath $child.FullName
            if ($child.PSIsContainer) {
                $pending.Add($child.FullName)
            }
            else {
                Assert-SingleLinkCompanionLeaf $child.FullName "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN" -MustExist
            }
        }
    }
}

function Get-JobFlowLocalRelativePath([string]$Root, [string]$Path) {
    $absoluteRoot = [IO.Path]::GetFullPath($Root)
    $absolutePath = [IO.Path]::GetFullPath($Path)
    $prefix = $absoluteRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolutePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_BROWSER_COMPANION_PATH_FORBIDDEN"
    }
    return $absolutePath.Substring($prefix.Length).Replace([IO.Path]::DirectorySeparatorChar, '/')
}

function Get-JobFlowLocalFiles([string]$Root) {
    Assert-JobFlowLocalTree $Root
    $files = New-Object 'System.Collections.Generic.List[string]'
    $pending = New-Object 'System.Collections.Generic.List[string]'
    $pending.Add([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $index = $pending.Count - 1
        $current = $pending[$index]
        $pending.RemoveAt($index)
        foreach ($child in @(Get-ChildItem -LiteralPath $current -Force)) {
            Assert-JobFlowLocalPath $child.FullName
            if ($child.PSIsContainer) {
                $pending.Add($child.FullName)
            }
            else {
                $files.Add($child.FullName)
            }
        }
    }
    return @($files | Sort-Object)
}

function Get-JobFlowLocalFileIdentity([string]$Path) {
    Assert-JobFlowLocalPath $Path
    $stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        Assert-JobFlowLocalPath $Path
        $length = $stream.Length
        $sha256 = Get-JobFlowStreamSha256 $stream
        $linkCount = Get-OpenCompanionFileLinkCount $stream "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN"
        if ($linkCount -ne 1) { throw "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN" }
        return [pscustomobject]@{ Length = $length; Sha256 = $sha256; LinkCount = $linkCount }
    }
    finally {
        $stream.Dispose()
    }
}

function Copy-JobFlowLocalFileCreateNew([string]$Source, [string]$Destination, [string]$Code) {
    Assert-JobFlowLocalPath $Source
    Assert-JobFlowLocalPath $Destination
    Assert-SingleLinkCompanionLeaf $Source $Code -MustExist
    if (Test-Path -LiteralPath $Destination) { throw $Code }
    $expected = Get-JobFlowLocalFileIdentity $Source
    $sourceStream = [IO.File]::Open($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $destinationStream = $null
    try {
        if ((Get-OpenCompanionFileLinkCount $sourceStream $Code) -ne 1 -or
            $sourceStream.Length -ne [long]$expected.Length) { throw $Code }
        $destinationStream = [IO.File]::Open(
            $Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
        )
        $sourceStream.CopyTo($destinationStream)
        $destinationStream.Flush($true)
    }
    finally {
        if ($null -ne $destinationStream) { $destinationStream.Dispose() }
        $sourceStream.Dispose()
    }
    $actual = Get-JobFlowLocalFileIdentity $Destination
    if ($actual.Length -ne $expected.Length -or $actual.Sha256 -cne $expected.Sha256) { throw $Code }
}

function Copy-JobFlowLocalTreeCreateNew([string]$Source, [string]$Destination, [string]$Code) {
    Assert-JobFlowLocalTree $Source
    Assert-JobFlowLocalPath $Destination
    if (Test-Path -LiteralPath $Destination) { throw $Code }
    $sourceRoot = [IO.Path]::GetFullPath($Source).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $expectedItems = @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force | ForEach-Object {
        $_.FullName.Substring($sourceRoot.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/')
    } | Sort-Object)
    New-Item -ItemType Directory -Path $Destination | Out-Null
    Assert-JobFlowLocalTree $Destination
    foreach ($directory in @(Get-ChildItem -LiteralPath $sourceRoot -Directory -Recurse -Force | Sort-Object FullName)) {
        $relative = $directory.FullName.Substring($sourceRoot.Length + 1)
        $targetDirectory = Join-Path $Destination $relative
        Assert-JobFlowLocalPath $targetDirectory
        New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
        Assert-JobFlowLocalPath $targetDirectory
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $sourceRoot -File -Recurse -Force | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($sourceRoot.Length + 1)
        Copy-JobFlowLocalFileCreateNew $file.FullName (Join-Path $Destination $relative) $Code
    }
    Assert-JobFlowLocalTree $Destination
    $destinationRoot = [IO.Path]::GetFullPath($Destination).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $actualItems = @(Get-ChildItem -LiteralPath $destinationRoot -Recurse -Force | ForEach-Object {
        $_.FullName.Substring($destinationRoot.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, '/')
    } | Sort-Object)
    if (($actualItems -join "`n") -cne ($expectedItems -join "`n")) { throw $Code }
}

function Copy-JobFlowSourceSnapshot([string]$Root, [string]$Staging, [object[]]$Snapshot) {
    foreach ($entry in @($Snapshot | Sort-Object RelativePath)) {
        $relativePlatformPath = ([string]$entry.RelativePath).Replace('/', [IO.Path]::DirectorySeparatorChar)
        $sourcePath = [IO.Path]::GetFullPath((Join-Path $Root $relativePlatformPath))
        $destinationPath = [IO.Path]::GetFullPath((Join-Path $Staging $relativePlatformPath))
        Assert-JobFlowSourcePath $sourcePath
        Assert-JobFlowLocalPath $destinationPath
        $sourceStream = [IO.File]::Open(
            $sourcePath,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        try {
            Assert-JobFlowSourcePath $sourcePath
            if ($sourceStream.Length -ne [long]$entry.Length) {
                throw "JOBFLOW_BROWSER_COMPANION_SOURCE_CHANGED"
            }
            $sha256 = Get-JobFlowStreamSha256 $sourceStream
            if (-not $sha256.Equals([string]$entry.Sha256, [StringComparison]::Ordinal)) {
                throw "JOBFLOW_BROWSER_COMPANION_SOURCE_CHANGED"
            }
            Assert-JobFlowSourcePath $sourcePath
            $sourceStream.Position = 0
            $destinationParent = [IO.Path]::GetDirectoryName($destinationPath)
            Assert-JobFlowLocalPath $destinationParent
            New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
            Assert-JobFlowLocalPath $destinationParent
            $destinationStream = [IO.File]::Open(
                $destinationPath,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            try {
                $sourceStream.CopyTo($destinationStream)
                $destinationStream.Flush()
            }
            finally {
                $destinationStream.Dispose()
            }
        }
        finally {
            $sourceStream.Dispose()
        }
    }
}

function Assert-JobFlowStagingSnapshot(
    [string]$Staging,
    [object[]]$Snapshot,
    [string]$BindingJson
) {
    Assert-JobFlowLocalTree $Staging
    $expected = @{}
    foreach ($entry in @($Snapshot)) {
        $relative = [string]$entry.RelativePath
        if ($expected.ContainsKey($relative)) {
            throw "JOBFLOW_BROWSER_COMPANION_STAGING_MISMATCH"
        }
        $expected[$relative] = $entry
    }
    $bindingBytes = (New-Object Text.UTF8Encoding($false)).GetBytes($BindingJson)
    try {
        $expected["binding.json"] = [pscustomobject]@{
            RelativePath = "binding.json"
            Length = [long]$bindingBytes.Length
            Sha256 = Get-JobFlowBytesSha256 $bindingBytes
        }
        $currentFiles = @(Get-JobFlowLocalFiles $Staging)
        if ($currentFiles.Count -ne $expected.Count) {
            throw "JOBFLOW_BROWSER_COMPANION_STAGING_MISMATCH"
        }
        foreach ($path in $currentFiles) {
            $relative = Get-JobFlowLocalRelativePath $Staging $path
            if (-not $expected.ContainsKey($relative)) {
                throw "JOBFLOW_BROWSER_COMPANION_STAGING_MISMATCH"
            }
            $identity = Get-JobFlowLocalFileIdentity $path
            $entry = $expected[$relative]
            if ([long]$identity.Length -ne [long]$entry.Length -or
                -not ([string]$identity.Sha256).Equals([string]$entry.Sha256, [StringComparison]::Ordinal)) {
                throw "JOBFLOW_BROWSER_COMPANION_STAGING_MISMATCH"
            }
        }
    }
    finally {
        [Array]::Clear($bindingBytes, 0, $bindingBytes.Length)
    }
}

function Remove-JobFlowContainerBestEffort([string]$Path, [string]$WarningCode) {
    try {
        if (Test-Path -LiteralPath $Path -PathType Container) {
            Assert-JobFlowLocalTree $Path
            Remove-Item -LiteralPath $Path -Recurse -Force
        }
    }
    catch {
        try { Write-Warning $WarningCode -WarningAction Continue }
        catch { }
    }
}

function Remove-JobFlowFileBestEffort([string]$Path, [string]$WarningCode) {
    try {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            Assert-JobFlowLocalPath $Path
            Assert-SingleLinkCompanionLeaf $Path "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN" -MustExist
            Remove-Item -LiteralPath $Path -Force
        }
    }
    catch {
        try { Write-Warning $WarningCode -WarningAction Continue }
        catch { }
    }
}

function Set-CurrentUserOnly([string]$Path) {
    Assert-JobFlowLocalPath $Path
    if (Test-Path -LiteralPath $Path -PathType Container) {
        Assert-JobFlowLocalTree $Path
    }
    else {
        Assert-SingleLinkCompanionLeaf $Path "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN" -MustExist
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $grant = "*$($identity.User.Value):(F)"
    $trustedIcacls = Get-TrustedWindowsIcaclsExecutable
    Invoke-TrustedConsoleExecutable `
        $trustedIcacls `
        @($Path, "/inheritance:r", "/grant:r", $grant) `
        "JOBFLOW_BROWSER_COMPANION_ACL_FAILED"
}

function Enter-CompanionInstallLock {
    $stream = [IO.File]::Open(
        $installLockPath,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::ReadWrite
    )
    try {
        if ((Get-OpenCompanionFileLinkCount $stream "JOBFLOW_BROWSER_COMPANION_INSTALL_LOCK_LINKED") -ne 1) {
            throw "JOBFLOW_BROWSER_COMPANION_INSTALL_LOCK_LINKED"
        }
        if ($stream.Length -lt 1) {
            $stream.SetLength(1)
            $stream.Flush()
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ($true) {
            try {
                $stream.Lock(0, 1)
                return $stream
            }
            catch [IO.IOException] {
                if ([DateTime]::UtcNow -ge $deadline) {
                    throw "JOBFLOW_BROWSER_COMPANION_INSTALL_ALREADY_RUNNING"
                }
                Start-Sleep -Milliseconds 50
            }
        }
    }
    catch {
        $stream.Dispose()
        throw
    }
}

foreach ($path in @($runtimeExtensionRoot, $bindingPath, $stagingRoot, $runtimeBackup, $bindingTemporary, $bindingBackup, $installLockPath)) {
    Assert-JobFlowLocalPath $path
}
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
$installLockStream = Enter-CompanionInstallLock
try {
    Set-CurrentUserOnly $installLockPath
    if (-not (Test-Path -LiteralPath $nativeHostInstaller -PathType Leaf)) {
        throw "JOBFLOW_NATIVE_HOST_INSTALLER_NOT_FOUND"
    }
    Assert-JobFlowSourcePath $nativeHostInstaller

$sourceSnapshot = @(Get-JobFlowSourceSnapshot $sourceExtensionRoot)
$manifest = Get-Content -LiteralPath $sourceManifestPath -Raw | ConvertFrom-Json
Assert-JobFlowSourceSnapshot $sourceExtensionRoot $sourceSnapshot
if ($manifest.manifest_version -ne 3 -or [string]::IsNullOrWhiteSpace($manifest.key)) {
    throw "JOBFLOW_BROWSER_COMPANION_MANIFEST_INVALID"
}
$keyBytes = [Convert]::FromBase64String([string]$manifest.key)
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $hex = -join (($hasher.ComputeHash($keyBytes) | Select-Object -First 16) | ForEach-Object { $_.ToString("x2") })
}
finally {
    $hasher.Dispose()
}
$derivedId = -join ($hex.ToCharArray() | ForEach-Object {
    $digit = [Convert]::ToInt32([string]$_, 16)
    [char]([int][char]'a' + $digit)
})
if ($derivedId -ne $expectedId) {
    throw "JOBFLOW_BROWSER_COMPANION_ID_MISMATCH"
}

$secretBytes = $null
$secretText = $null
$binding = $null
if (Test-Path -LiteralPath $bindingPath -PathType Leaf) {
    try {
        $existingBinding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
        $existingKeys = @($existingBinding.PSObject.Properties.Name | Sort-Object)
        $candidateSecret = [string]$existingBinding.secret_b64url
        $candidateInstallationId = [string]$existingBinding.installation_id
        $candidateSecretBytes = [Convert]::FromBase64String(
            $candidateSecret.Replace('-', '+').Replace('_', '/') + ("=" * ((4 - $candidateSecret.Length % 4) % 4))
        )
        if (
            ($existingKeys -join ',') -eq 'installation_id,schema_version,secret_b64url' -and
            $existingBinding.schema_version -eq 1 -and
            $candidateInstallationId -match '^[a-f0-9]{32}$' -and
            $candidateSecret -match '^[A-Za-z0-9_-]{43}$' -and
            $candidateSecretBytes.Length -eq 32
        ) {
            $binding = [ordered]@{
                schema_version = 1
                installation_id = $candidateInstallationId
                secret_b64url = $candidateSecret
            }
        }
        [Array]::Clear($candidateSecretBytes, 0, $candidateSecretBytes.Length)
        $candidateSecretBytes = $null
        $candidateSecret = $null
        $candidateInstallationId = $null
        $existingBinding = $null
    }
    catch {
        $binding = $null
    }
}
if ($null -eq $binding) {
    $secretBytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($secretBytes) }
    finally { $generator.Dispose() }
    $secretText = [Convert]::ToBase64String($secretBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $binding = [ordered]@{
        schema_version = 1
        installation_id = ([Guid]::NewGuid().ToString("N"))
        secret_b64url = $secretText
    }
}
$bindingJson = $binding | ConvertTo-Json -Compress

$runtimeInstalled = $false
$bindingInstalled = $false
$preserveRollbackBackups = $false
$activationCommitted = $false
$transactionFailed = $false
$rollbackComplete = $false
try {
    Assert-JobFlowSourceSnapshot $sourceExtensionRoot $sourceSnapshot
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    Copy-JobFlowSourceSnapshot $sourceExtensionRoot $stagingRoot $sourceSnapshot
    Assert-JobFlowSourceSnapshot $sourceExtensionRoot $sourceSnapshot
    [IO.File]::WriteAllText((Join-Path $stagingRoot "binding.json"), $bindingJson, (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($bindingTemporary, $bindingJson, (New-Object Text.UTF8Encoding($false)))
    Set-CurrentUserOnly (Join-Path $stagingRoot "binding.json")
    Set-CurrentUserOnly $bindingTemporary
    Assert-JobFlowStagingSnapshot $stagingRoot $sourceSnapshot $bindingJson
    Assert-JobFlowSourceSnapshot $sourceExtensionRoot $sourceSnapshot

    if (Test-Path -LiteralPath $runtimeExtensionRoot) {
        Assert-JobFlowLocalTree $runtimeExtensionRoot
        Move-Item -LiteralPath $runtimeExtensionRoot -Destination $runtimeBackup
    }
    Move-Item -LiteralPath $stagingRoot -Destination $runtimeExtensionRoot
    $runtimeInstalled = $true
    if (Test-Path -LiteralPath $bindingPath) {
        Assert-SingleLinkCompanionLeaf $bindingPath "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN" -MustExist
        Move-Item -LiteralPath $bindingPath -Destination $bindingBackup
    }
    Move-Item -LiteralPath $bindingTemporary -Destination $bindingPath
    $bindingInstalled = $true
    Set-CurrentUserOnly (Join-Path $runtimeExtensionRoot "binding.json")
    Set-CurrentUserOnly $bindingPath
    Assert-JobFlowSourcePath $nativeHostInstaller
    $trustedPowerShell = Get-TrustedWindowsPowerShellExecutable
    $nativeHostArguments = @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $nativeHostInstaller)
    if ($Development) { $nativeHostArguments += "-Development" }
    Invoke-TrustedConsoleExecutable `
        $trustedPowerShell `
        $nativeHostArguments `
        "JOBFLOW_NATIVE_HOST_INSTALL_FAILED"
    $activationCommitted = $true
}
catch {
    $transactionFailed = $true
    $installFailure = $_
    $rollbackFailures = New-Object 'System.Collections.Generic.List[string]'
    if ($bindingInstalled -and (Test-Path -LiteralPath $bindingPath -PathType Leaf)) {
        try {
            Assert-JobFlowLocalPath $bindingPath
            Remove-Item -LiteralPath $bindingPath -Force
        }
        catch {
            $rollbackFailures.Add("REMOVE_NEW_BINDING")
        }
    }
    if (Test-Path -LiteralPath $bindingBackup -PathType Leaf) {
        try {
            Assert-JobFlowLocalPath $bindingBackup
            if (Test-Path -LiteralPath $bindingPath) {
                throw "JOBFLOW_BROWSER_COMPANION_ROLLBACK_BINDING_DESTINATION_OCCUPIED"
            }
            Assert-SingleLinkCompanionLeaf $bindingBackup "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN" -MustExist
            if (Test-Path -LiteralPath $bindingTemporary) {
                throw "JOBFLOW_BROWSER_COMPANION_ROLLBACK_BINDING_TEMP_OCCUPIED"
            }
            Copy-JobFlowLocalFileCreateNew $bindingBackup $bindingTemporary "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN"
            Move-Item -LiteralPath $bindingTemporary -Destination $bindingPath
        }
        catch {
            $rollbackFailures.Add("RESTORE_BINDING_BACKUP")
        }
    }
    if ($runtimeInstalled -and (Test-Path -LiteralPath $runtimeExtensionRoot -PathType Container)) {
        try {
            Assert-JobFlowLocalTree $runtimeExtensionRoot
            Remove-Item -LiteralPath $runtimeExtensionRoot -Recurse -Force
        }
        catch {
            $rollbackFailures.Add("REMOVE_NEW_RUNTIME")
        }
    }
    if (Test-Path -LiteralPath $runtimeBackup -PathType Container) {
        try {
            Assert-JobFlowLocalTree $runtimeBackup
            if (Test-Path -LiteralPath $runtimeExtensionRoot) {
                throw "JOBFLOW_BROWSER_COMPANION_ROLLBACK_RUNTIME_DESTINATION_OCCUPIED"
            }
            if (Test-Path -LiteralPath $stagingRoot) {
                throw "JOBFLOW_BROWSER_COMPANION_ROLLBACK_RUNTIME_TEMP_OCCUPIED"
            }
            Copy-JobFlowLocalTreeCreateNew $runtimeBackup $stagingRoot "JOBFLOW_BROWSER_COMPANION_HARDLINK_FORBIDDEN"
            Move-Item -LiteralPath $stagingRoot -Destination $runtimeExtensionRoot
        }
        catch {
            $rollbackFailures.Add("RESTORE_RUNTIME_BACKUP")
        }
    }
    if ($rollbackFailures.Count -gt 0) {
        $preserveRollbackBackups = $true
        throw "JOBFLOW_BROWSER_COMPANION_ROLLBACK_FAILED"
    }
    $rollbackComplete = $true
    throw $installFailure
}
finally {
    $cleanupWarning = if ($activationCommitted) {
        "JOBFLOW_BROWSER_COMPANION_POST_COMMIT_CLEANUP_FAILED"
    }
    else {
        "JOBFLOW_BROWSER_COMPANION_FAILURE_CLEANUP_FAILED"
    }
    Remove-JobFlowContainerBestEffort $stagingRoot $cleanupWarning
    Remove-JobFlowFileBestEffort $bindingTemporary $cleanupWarning
    if ($activationCommitted -or ($transactionFailed -and $rollbackComplete -and -not $preserveRollbackBackups)) {
        Remove-JobFlowContainerBestEffort $runtimeBackup $cleanupWarning
        Remove-JobFlowFileBestEffort $bindingBackup $cleanupWarning
    }
    if ($secretBytes -is [Array]) { [Array]::Clear($secretBytes, 0, $secretBytes.Length) }
    $secretText = $null
    $binding = $null
    $bindingJson = $null
}

$runtimeManifestPath = Join-Path $runtimeExtensionRoot "manifest.json"

Write-Host ""
Write-Host "JobFlow Browser Companion"
if ($Development) {
    Write-Host "Development mode: load the unpacked Local AppData BrowserCompanion folder."
    Write-Host "Confirm version $($manifest.version) and extension ID: $expectedId"
}
else {
    Write-Host "Install the signed extension from the official store page that is opening."
    Write-Host "The Windows-only secure channel is registered for this user. No extension folder selection is required."
}
Write-Host "Keep site access on When clicked if you prefer. Refresh JobFlow after installing or updating the extension."
Write-Host ""
if (-not $NoLaunch) {
    if ($Development) {
        $target = Get-BrowserLaunchTarget
        $trustedExplorer = Get-TrustedWindowsExplorerExecutable
        Start-TrustedExecutable $trustedExplorer @("/select,`"$runtimeManifestPath`"")
        Start-TrustedExecutable $target @("--new-window", $target.ManagementUrl)
    }
    else {
        try { Open-OfficialStorePage }
        catch { Show-StoreOpenFallback }
    }
}
}
finally {
    if ($null -ne $installLockStream) {
        try { $installLockStream.Unlock(0, 1) } catch { }
        $installLockStream.Dispose()
    }
}
exit 0
