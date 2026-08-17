[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sourcePath = Join-Path $PSScriptRoot "native-messaging\JobFlowBrowserCompanionHost.cs"
$storeIdentityPath = Join-Path $projectRoot "config\browser-companion-stores.json"
$hostName = "com.jobflow.browser_companion"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "JOBFLOW_NATIVE_HOST_SOURCE_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $storeIdentityPath -PathType Leaf)) {
    throw "JOBFLOW_BROWSER_STORE_IDENTITY_NOT_FOUND"
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND"
}

$localRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "JobOps"))
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

function Assert-JobFlowLocalPath([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
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

function Set-CurrentUserOnly([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $directoryGrant = "*$($identity.User.Value):(OI)(CI)(F)"
    $fileGrant = "*$($identity.User.Value):(F)"
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
        & "$env:SystemRoot\System32\icacls.exe" $directory.FullName "/inheritance:r" "/grant:r" $directoryGrant "/C" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "JOBFLOW_NATIVE_HOST_ACL_FAILED"
        }
    }
    foreach ($file in $files) {
        & "$env:SystemRoot\System32\icacls.exe" $file.FullName "/inheritance:r" "/grant:r" $fileGrant "/C" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "JOBFLOW_NATIVE_HOST_ACL_FAILED"
        }
    }
}

function Read-RegistryDefault([string]$Subkey) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Subkey, $false)
    if ($null -eq $key) { return @{ Exists = $false; Value = $null } }
    try { return @{ Exists = $true; Value = $key.GetValue("", $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) } }
    finally { $key.Dispose() }
}

function Write-RegistryDefault([string]$Subkey, [string]$Value) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($Subkey, $true)
    if ($null -eq $key) { throw "JOBFLOW_NATIVE_HOST_REGISTRY_FAILED" }
    try { $key.SetValue("", $Value, [Microsoft.Win32.RegistryValueKind]::String) }
    finally { $key.Dispose() }
}

function Restore-RegistryDefault([string]$Subkey, [hashtable]$Previous) {
    if ($Previous.Exists) {
        Write-RegistryDefault $Subkey ([string]$Previous.Value)
    }
    else {
        [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($Subkey, $false)
    }
}

foreach ($path in @($hostRoot, $stagingRoot, $backupRoot, $hostExecutable, $hostManifest)) {
    Assert-JobFlowLocalPath $path
}
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null

$identity = Get-Content -LiteralPath $storeIdentityPath -Raw | ConvertFrom-Json
if ($identity.schema_version -ne 1 -or $identity.native_host_name -ne $hostName) {
    throw "JOBFLOW_BROWSER_STORE_IDENTITY_INVALID"
}
$extensionIds = @($identity.extension_ids | ForEach-Object { [string]$_ }) | Select-Object -Unique
if ($extensionIds.Count -lt 1 -or ($extensionIds | Where-Object { $_ -notmatch '^[a-p]{32}$' }).Count -gt 0) {
    throw "JOBFLOW_BROWSER_STORE_IDENTITY_INVALID"
}
$allowedOrigins = @($extensionIds | ForEach-Object { "chrome-extension://$_/" })
$registryBackup = @{}
foreach ($subkey in $registrySubkeys) { $registryBackup[$subkey] = Read-RegistryDefault $subkey }

$hostInstalled = $false
try {
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    Add-Type -Path $sourcePath -ReferencedAssemblies @("System.Web.Extensions") -OutputAssembly $stagedExecutable -OutputType ConsoleApplication
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
    Set-CurrentUserOnly $stagingRoot

    if (Test-Path -LiteralPath $hostRoot -PathType Container) {
        Move-Item -LiteralPath $hostRoot -Destination $backupRoot
    }
    Move-Item -LiteralPath $stagingRoot -Destination $hostRoot
    $hostInstalled = $true
    Set-CurrentUserOnly $hostRoot
    foreach ($subkey in $registrySubkeys) { Write-RegistryDefault $subkey $hostManifest }
}
catch {
    foreach ($subkey in $registrySubkeys) {
        try { Restore-RegistryDefault $subkey $registryBackup[$subkey] } catch { }
    }
    if ($hostInstalled -and (Test-Path -LiteralPath $hostRoot -PathType Container)) {
        Assert-JobFlowLocalPath $hostRoot
        Remove-Item -LiteralPath $hostRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $backupRoot -PathType Container) {
        Move-Item -LiteralPath $backupRoot -Destination $hostRoot
    }
    throw
}
finally {
    foreach ($path in @($stagingRoot, $backupRoot)) {
        if (Test-Path -LiteralPath $path -PathType Container) {
            Assert-JobFlowLocalPath $path
            Set-CurrentUserOnly $path
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

Write-Host "JobFlow Browser Companion native host is registered for Chrome and Edge."
exit 0
