[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
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
$maxArchiveBytes = 1GB

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND" }
$localAppDataRoot = (Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobOps"))
$localRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_UPDATE_INSTALLED_ROOT_INVALID"
}
$versionsRoot = Join-Path $localRoot "Application\versions"
$dataRoot = Join-Path $localRoot "Data"
$currentPointerPath = Join-Path $localRoot "current.json"
$updateId = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$stagingRoot = Join-Path $localRoot (".u-" + $updateId)

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

function Read-CurrentPointer {
    Assert-JobFlowLocalPath $currentPointerPath
    if (-not (Test-Path -LiteralPath $currentPointerPath -PathType Leaf)) {
        throw "JOBFLOW_UPDATE_CURRENT_VERSION_MISSING"
    }
    try { $value = Get-Content -LiteralPath $currentPointerPath -Raw | ConvertFrom-Json }
    catch { throw "JOBFLOW_UPDATE_POINTER_INVALID" }
    $directory = [string]$value.version_directory
    $version = [string]$value.version
    if (
        $value.schema_version -ne 1 -or
        $directory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' -or
        $version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        ([string]$value.source_sha256) -notmatch '^[0-9a-f]{64}$'
    ) { throw "JOBFLOW_UPDATE_POINTER_INVALID" }
    $root = [IO.Path]::GetFullPath((Join-Path $versionsRoot $directory))
    Assert-JobFlowLocalPath $root
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (
        -not (Test-Path -LiteralPath (Join-Path $root ".jobops-root") -PathType Leaf) -or
        -not (Test-Path -LiteralPath $python -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $root "config\update-channel.json") -PathType Leaf)
    ) { throw "JOBFLOW_UPDATE_CURRENT_VERSION_INCOMPLETE" }
    return @{ Value = $value; Root = $root; Python = $python }
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
                    $output = New-Object IO.FileStream($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                    try {
                        $buffer = New-Object byte[] 65536
                        [long]$total = 0
                        while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                            $total += $read
                            if ($total -gt $MaximumBytes) { throw "JOBFLOW_UPDATE_DOWNLOAD_SIZE_INVALID" }
                            $output.Write($buffer, 0, $read)
                        }
                        $output.Flush($true)
                    }
                    finally { $output.Dispose() }
                }
                finally { $input.Dispose() }
                if ((Get-Item -LiteralPath $Destination).Length -lt 1) { throw "JOBFLOW_UPDATE_DOWNLOAD_EMPTY" }
                return
            }
            finally { $response.Dispose() }
        }
        throw "JOBFLOW_UPDATE_REDIRECT_INVALID"
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Find-ReleaseAsset([object]$Release, [string]$Name) {
    $matches = @($Release.assets | Where-Object { [string]$_.name -ceq $Name })
    if ($matches.Count -ne 1) { throw "JOBFLOW_UPDATE_RELEASE_ASSET_INVALID" }
    $url = [Uri]([string]$matches[0].browser_download_url)
    $escapedTag = [Uri]::EscapeDataString([string]$Release.tag_name)
    $expectedPath = "/$expectedRepository/releases/download/$escapedTag/$Name"
    if ($url.Scheme -ne "https" -or $url.DnsSafeHost -ne "github.com" -or $url.AbsolutePath -cne $expectedPath) {
        throw "JOBFLOW_UPDATE_RELEASE_ASSET_URL_INVALID"
    }
    return $url
}

Assert-JobFlowLocalPath $versionsRoot
Assert-JobFlowLocalPath $dataRoot
Assert-JobFlowLocalPath $stagingRoot
$current = Read-CurrentPointer
$channelPath = Join-Path $current.Root "config\update-channel.json"
try { $channel = Get-Content -LiteralPath $channelPath -Raw | ConvertFrom-Json }
catch { throw "JOBFLOW_UPDATE_CHANNEL_INVALID" }
if (
    [string]$channel.repository -cne $expectedRepository -or
    [string]$channel.latest_release_api_url -cne $expectedApiUrl -or
    [string]$channel.manifest_asset_name -cne $manifestAssetName -or
    [string]$channel.signature_asset_name -cne $signatureAssetName -or
    [string]$channel.signature.key_id -cne $expectedKeyId
) { throw "JOBFLOW_UPDATE_CHANNEL_INVALID" }

New-Item -ItemType Directory -Path $stagingRoot | Out-Null
$releasePath = Join-Path $stagingRoot "release.json"
$manifestPath = Join-Path $stagingRoot $manifestAssetName
$signaturePath = Join-Path $stagingRoot $signatureAssetName
try {
    Write-Host "正在检查 JobFlow 的签名稳定版更新…… / Checking for a signed stable JobFlow update..."
    Receive-AllowedHttpsFile ([Uri]$expectedApiUrl) $releasePath $maxReleaseBytes @("api.github.com") "application/vnd.github+json"
    try { $release = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json }
    catch { throw "JOBFLOW_UPDATE_RELEASE_METADATA_INVALID" }
    if (
        $release.draft -ne $false -or
        $release.prerelease -ne $false -or
        ([string]$release.tag_name) -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$'
    ) { throw "JOBFLOW_UPDATE_RELEASE_METADATA_INVALID" }

    $manifestUrl = Find-ReleaseAsset $release $manifestAssetName
    $signatureUrl = Find-ReleaseAsset $release $signatureAssetName
    Receive-AllowedHttpsFile $manifestUrl $manifestPath $maxManifestBytes $allowedDownloadHosts "application/octet-stream"
    Receive-AllowedHttpsFile $signatureUrl $signaturePath $maxSignatureBytes $allowedDownloadHosts "application/octet-stream"

    $inspectOutput = & $current.Python -m jobops.update_manifest inspect `
        --manifest $manifestPath --signature $signaturePath `
        --current-version ([string]$current.Value.version) --channel $channelPath
    if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_UPDATE_SIGNATURE_CHECK_FAILED" }
    try { $inspection = $inspectOutput | ConvertFrom-Json }
    catch { throw "JOBFLOW_UPDATE_SIGNATURE_RESULT_INVALID" }
    if ($inspection.status -eq "UPDATE_CURRENT") {
        Write-Host "你已经在使用最新的稳定版 JobFlow。 / You already have the latest stable JobFlow version."
        exit 0
    }
    if ($inspection.status -ne "UPDATE_AVAILABLE" -or $inspection.signature_verified -ne $true) {
        throw "JOBFLOW_UPDATE_SIGNATURE_CHECK_FAILED"
    }
    if ([string]$inspection.tag_name -cne [string]$release.tag_name) {
        throw "JOBFLOW_UPDATE_RELEASE_IDENTITY_MISMATCH"
    }
    if ([long]$inspection.asset_bytes -gt $maxArchiveBytes) { throw "JOBFLOW_UPDATE_ARCHIVE_SIZE_INVALID" }

    $archiveName = [string]$inspection.asset_name
    $archivePath = Join-Path $stagingRoot $archiveName
    $archiveUrl = Find-ReleaseAsset $release $archiveName
    Write-Host "正在下载并验证更新包；当前版本在验证完成前不会改变…… / Downloading and verifying the update; the current version remains unchanged until validation passes..."
    Receive-AllowedHttpsFile $archiveUrl $archivePath ([long]$inspection.asset_bytes) $allowedDownloadHosts "application/octet-stream"

    $verifyOutput = & $current.Python -m jobops.update_manifest verify `
        --manifest $manifestPath --signature $signaturePath --archive $archivePath `
        --current-version ([string]$current.Value.version) --channel $channelPath
    if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_UPDATE_BUNDLE_CHECK_FAILED" }
    try { $verification = $verifyOutput | ConvertFrom-Json }
    catch { throw "JOBFLOW_UPDATE_BUNDLE_RESULT_INVALID" }
    if ($verification.status -ne "UPDATE_BUNDLE_VERIFIED" -or $verification.archive_verified -ne $true) {
        throw "JOBFLOW_UPDATE_BUNDLE_CHECK_FAILED"
    }

    $extractRoot = Join-Path $stagingRoot "extracted"
    New-Item -ItemType Directory -Path $extractRoot | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    $expectedPackageName = "JobFlow-v$([string]$inspection.available_version)"
    $children = @(Get-ChildItem -LiteralPath $extractRoot -Force)
    if ($children.Count -ne 1 -or -not $children[0].PSIsContainer -or $children[0].Name -cne $expectedPackageName) {
        throw "JOBFLOW_UPDATE_EXTRACTED_LAYOUT_INVALID"
    }
    $packageRoot = $children[0].FullName
    Assert-JobFlowLocalPath $packageRoot
    $installerPath = Join-Path $packageRoot ([string]$inspection.installer_relative_path)
    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
        throw "JOBFLOW_UPDATE_INSTALLER_MISSING"
    }

    Write-Host "签名与完整性检查已通过，正在安装并验证新版本…… / Signature and integrity checks passed; installing and validating the new version..."
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installerPath -NoLaunch
    if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_UPDATE_INSTALL_FAILED" }
    $updated = Read-CurrentPointer
    if ([string]$updated.Value.version -cne [string]$inspection.available_version) {
        throw "JOBFLOW_UPDATE_SWITCH_FAILED"
    }

    # The installer already ran the final shared-data health check while it
    # held both the foreground-runtime and discovery-run maintenance locks.
    # Re-running it here after those locks are released could race a newly
    # awakened task or a user reopening JobFlow.
    Write-Host "JobFlow 已安全更新到 $($updated.Value.version)。请关闭旧窗口并重新打开 JobFlow。 / JobFlow was safely updated to $($updated.Value.version). Close the old window and start JobFlow again."
    exit 0
}
finally {
    if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
        Assert-JobFlowLocalPath $stagingRoot
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
