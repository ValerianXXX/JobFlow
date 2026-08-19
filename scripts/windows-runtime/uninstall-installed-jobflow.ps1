[CmdletBinding()]
param(
    [string]$InstallRoot = "",
    [switch]$RemoveUserData,
    [switch]$UserConfirmed
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND" }
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "JobOps"))
$localRoot = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}
else { [IO.Path]::GetFullPath($InstallRoot) }
if (-not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_UNINSTALL_ROOT_FORBIDDEN"
}
if ($RemoveUserData -and -not $UserConfirmed) {
    throw "删除本机资料需要同时提供 -RemoveUserData -UserConfirmed。 / Removing local profile data requires both -RemoveUserData and -UserConfirmed."
}
$skipBrowserIntegrationForAcceptance = $env:JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY -eq "1"
if ($skipBrowserIntegrationForAcceptance) {
    $temporaryBoundary = [IO.Path]::GetFullPath($env:TEMP)
    $localAppDataRoot = [IO.Path]::GetDirectoryName($expectedRoot)
    $acceptanceRoot = [IO.Path]::GetDirectoryName($localAppDataRoot)
    $temporaryPrefix = $temporaryBoundary.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $acceptanceRoot.StartsWith($temporaryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        ([IO.Path]::GetFileName($acceptanceRoot)) -notlike "jobflow-fixed-install-qa-*" -or
        ([IO.Path]::GetFileName($localAppDataRoot)) -ne "LocalAppData"
    ) {
        throw "JOBFLOW_UNINSTALL_ACCEPTANCE_BYPASS_FORBIDDEN"
    }
}

function Assert-SafeRemovalTarget([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -eq $localRoot -or -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_UNINSTALL_TARGET_FORBIDDEN"
    }
    if (Test-Path -LiteralPath $absolute) {
        $items = @((Get-Item -LiteralPath $absolute -Force))
        if ((Get-Item -LiteralPath $absolute -Force).PSIsContainer) {
            $items += @(Get-ChildItem -LiteralPath $absolute -Recurse -Force)
        }
        foreach ($item in $items) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_UNINSTALL_REPARSE_FORBIDDEN"
            }
        }
    }
}

function Remove-SafeTarget([string]$Path) {
    Assert-SafeRemovalTarget $Path
    $absolute = [IO.Path]::GetFullPath($Path)
    $extended = if ($absolute.StartsWith("\\", [StringComparison]::Ordinal)) {
        "\\?\UNC\" + $absolute.TrimStart('\')
    }
    else { "\\?\" + $absolute }
    $item = Get-Item -LiteralPath $absolute -Force
    if ($item.PSIsContainer) {
        foreach ($file in [IO.Directory]::EnumerateFiles($extended, "*", [IO.SearchOption]::AllDirectories)) {
            [IO.File]::SetAttributes($file, [IO.FileAttributes]::Normal)
        }
        [IO.Directory]::Delete($extended, $true)
    }
    else {
        [IO.File]::SetAttributes($extended, [IO.FileAttributes]::Normal)
        [IO.File]::Delete($extended)
    }
}

$rootItem = Get-Item -LiteralPath $localRoot -Force -ErrorAction SilentlyContinue
if ($null -ne $rootItem -and ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "JOBFLOW_UNINSTALL_REPARSE_FORBIDDEN"
}

if (-not $skipBrowserIntegrationForAcceptance) {
    $hostName = "com.jobflow.browser_companion"
    foreach ($subkey in @(
        "Software\Google\Chrome\NativeMessagingHosts\$hostName",
        "Software\Microsoft\Edge\NativeMessagingHosts\$hostName"
    )) {
        [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($subkey, $false)
    }
}

if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
    $programsRoot = [IO.Path]::GetFullPath((Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))
    $menuRoot = [IO.Path]::GetFullPath((Join-Path $programsRoot "JobFlow"))
    $prefix = $programsRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($menuRoot.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $menuRoot -PathType Container)) {
        $menuItems = @((Get-Item -LiteralPath $menuRoot -Force)) + @(Get-ChildItem -LiteralPath $menuRoot -Recurse -Force)
        if (($menuItems | Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 }).Count -ne 0) {
            throw "JOBFLOW_UNINSTALL_START_MENU_REPARSE_FORBIDDEN"
        }
        Remove-Item -LiteralPath $menuRoot -Recurse -Force
    }
}

$targets = @(
    "Application", "BrowserCompanion", "BrowserCompanionHost", "bin",
    "Start JobFlow.cmd", "Check JobFlow.cmd", "Update JobFlow.cmd", "Rollback JobFlow.cmd", "Uninstall JobFlow.cmd",
    "current.json", "previous.json", "browser-companion-binding.json"
)
if ($RemoveUserData) { $targets += @("Data", "private") }
foreach ($relative in $targets) {
    $target = Join-Path $localRoot $relative
    if (-not (Test-Path -LiteralPath $target)) { continue }
    Remove-SafeTarget $target
}

if (Test-Path -LiteralPath $localRoot -PathType Container) {
    $remaining = @(Get-ChildItem -LiteralPath $localRoot -Force)
    if ($remaining.Count -eq 0) { Remove-Item -LiteralPath $localRoot -Force }
}

if ($RemoveUserData) {
    Write-Host "JobFlow 程序和本机用户资料已删除。浏览器商店扩展仍需由你在浏览器中移除。 / JobFlow and its local user data were removed. Remove the store extension separately in your browser."
}
else {
    Write-Host "JobFlow 程序已删除；本机 Profile、队列和私人资料仍保留，重新安装后可继续使用。 / JobFlow was removed; local profile, queue, and private data were preserved for a future reinstall."
}
exit 0
