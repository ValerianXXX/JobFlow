[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$extensionRoot = Join-Path $projectRoot "browser-companion"
$manifestPath = Join-Path $extensionRoot "manifest.json"
$expectedId = "hhlliaaafegldkmcgmaoaelabipcaooj"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "JOBFLOW_BROWSER_COMPANION_NOT_FOUND"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
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

$browserPath = $null
$extensionsUrl = $null
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

Write-Host ""
Write-Host "JobFlow Browser Companion"
Write-Host "1. In the browser page, turn on Developer mode."
Write-Host "2. Choose Load unpacked."
Write-Host "3. Select the extension folder that is opening now."
Write-Host "4. Confirm that the extension ID is: $expectedId"
Write-Host "5. Return to JobFlow. Installation is needed only once."
Write-Host ""
Start-Process -FilePath "explorer.exe" -ArgumentList @("/select,`"$manifestPath`"")
Start-Process -FilePath $browserPath -ArgumentList @("--new-window", $extensionsUrl)
exit 0
