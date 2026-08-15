[CmdletBinding()]
param(
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sourceExtensionRoot = Join-Path $projectRoot "browser-companion"
$sourceManifestPath = Join-Path $sourceExtensionRoot "manifest.json"
$expectedId = "hhlliaaafegldkmcgmaoaelabipcaooj"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $sourceManifestPath -PathType Leaf)) {
    throw "JOBFLOW_BROWSER_COMPANION_NOT_FOUND"
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND"
}

$localRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "JobOps"))
$runtimeExtensionRoot = [IO.Path]::GetFullPath((Join-Path $localRoot "BrowserCompanion"))
$bindingPath = [IO.Path]::GetFullPath((Join-Path $localRoot "browser-companion-binding.json"))
$installId = [Guid]::NewGuid().ToString("N")
$stagingRoot = [IO.Path]::GetFullPath((Join-Path $localRoot (".BrowserCompanion.install-" + $installId)))
$runtimeBackup = [IO.Path]::GetFullPath((Join-Path $localRoot (".BrowserCompanion.backup-" + $installId)))
$bindingTemporary = [IO.Path]::GetFullPath((Join-Path $localRoot (".browser-companion-binding-" + $installId + ".tmp")))
$bindingBackup = [IO.Path]::GetFullPath((Join-Path $localRoot (".browser-companion-binding-" + $installId + ".backup")))

function Assert-JobFlowLocalPath([string]$Path) {
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

function Set-CurrentUserOnly([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $grant = "*$($identity.User.Value):(F)"
    & "$env:SystemRoot\System32\icacls.exe" $Path "/inheritance:r" "/grant:r" $grant | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "JOBFLOW_BROWSER_COMPANION_ACL_FAILED"
    }
}

foreach ($path in @($runtimeExtensionRoot, $bindingPath, $stagingRoot, $runtimeBackup, $bindingTemporary, $bindingBackup)) {
    Assert-JobFlowLocalPath $path
}
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null

$manifest = Get-Content -LiteralPath $sourceManifestPath -Raw | ConvertFrom-Json
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
$bindingJson = $binding | ConvertTo-Json -Compress

$runtimeInstalled = $false
$bindingInstalled = $false
try {
    New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceExtensionRoot -Force | Where-Object { $_.Name -ne "binding.json" } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $stagingRoot -Recurse -Force
    }
    [IO.File]::WriteAllText((Join-Path $stagingRoot "binding.json"), $bindingJson, (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($bindingTemporary, $bindingJson, (New-Object Text.UTF8Encoding($false)))
    Set-CurrentUserOnly (Join-Path $stagingRoot "binding.json")
    Set-CurrentUserOnly $bindingTemporary

    if (Test-Path -LiteralPath $runtimeExtensionRoot) {
        Move-Item -LiteralPath $runtimeExtensionRoot -Destination $runtimeBackup
    }
    Move-Item -LiteralPath $stagingRoot -Destination $runtimeExtensionRoot
    $runtimeInstalled = $true
    if (Test-Path -LiteralPath $bindingPath) {
        Move-Item -LiteralPath $bindingPath -Destination $bindingBackup
    }
    Move-Item -LiteralPath $bindingTemporary -Destination $bindingPath
    $bindingInstalled = $true
    Set-CurrentUserOnly (Join-Path $runtimeExtensionRoot "binding.json")
    Set-CurrentUserOnly $bindingPath
}
catch {
    if ($bindingInstalled -and (Test-Path -LiteralPath $bindingPath -PathType Leaf)) {
        Remove-Item -LiteralPath $bindingPath -Force
    }
    if (Test-Path -LiteralPath $bindingBackup -PathType Leaf) {
        Move-Item -LiteralPath $bindingBackup -Destination $bindingPath -Force
    }
    if ($runtimeInstalled -and (Test-Path -LiteralPath $runtimeExtensionRoot -PathType Container)) {
        Assert-JobFlowLocalPath $runtimeExtensionRoot
        Remove-Item -LiteralPath $runtimeExtensionRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $runtimeBackup -PathType Container) {
        Move-Item -LiteralPath $runtimeBackup -Destination $runtimeExtensionRoot
    }
    throw
}
finally {
    foreach ($path in @($stagingRoot, $runtimeBackup)) {
        if (Test-Path -LiteralPath $path -PathType Container) {
            Assert-JobFlowLocalPath $path
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
    foreach ($path in @($bindingTemporary, $bindingBackup)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Assert-JobFlowLocalPath $path
            Remove-Item -LiteralPath $path -Force
        }
    }
    [Array]::Clear($secretBytes, 0, $secretBytes.Length)
    $secretText = $null
    $binding = $null
    $bindingJson = $null
}

$runtimeManifestPath = Join-Path $runtimeExtensionRoot "manifest.json"
$browserPath = $null
$extensionsUrl = $null
if (-not $NoLaunch) {
    $edge = Get-Command "msedge.exe" -ErrorAction SilentlyContinue
    if ($edge) {
        $browserPath = $edge.Source
        $extensionsUrl = "edge://extensions/"
    }
    if (-not $browserPath) {
        $edgeCandidate = Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"
        if (Test-Path -LiteralPath $edgeCandidate -PathType Leaf) {
            $browserPath = $edgeCandidate
            $extensionsUrl = "edge://extensions/"
        }
    }
    if (-not $browserPath) {
        $chromeCandidates = @(
            (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
            (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
        )
        $browserPath = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if ($browserPath) { $extensionsUrl = "chrome://extensions/" }
    }
    if (-not $browserPath) {
        throw "EDGE_OR_CHROME_REQUIRED"
    }
}

Write-Host ""
Write-Host "JobFlow Browser Companion"
Write-Host "1. In the browser page, turn on Developer mode."
Write-Host "2. Remove an older JobFlow Browser Companion, or click Reload if it already points to the installed folder."
Write-Host "3. Choose Load unpacked and select the BrowserCompanion folder that is opening now."
Write-Host "4. Confirm version $($manifest.version) and extension ID: $expectedId"
Write-Host "5. Keep site access on When clicked if you prefer. Pairing uses this Windows installation binding."
Write-Host "6. Refresh the JobFlow page once, then choose Connect browser."
Write-Host ""
if (-not $NoLaunch) {
    Start-Process -FilePath "explorer.exe" -ArgumentList @("/select,`"$runtimeManifestPath`"")
    Start-Process -FilePath $browserPath -ArgumentList @("--new-window", $extensionsUrl)
}
exit 0
