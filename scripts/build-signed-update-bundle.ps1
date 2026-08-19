[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}

function Find-Python {
    $venv = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venv -PathType Leaf) { return @{ Command = $venv; Prefix = @() } }
    foreach ($candidate in @(
        @{ Name = "python"; Prefix = @() },
        @{ Name = "py"; Prefix = @("-3") }
    )) {
        $command = Get-Command $candidate.Name -ErrorAction SilentlyContinue
        if ($null -ne $command) { return @{ Command = $command.Source; Prefix = @($candidate.Prefix) } }
    }
    throw "JOBFLOW_RELEASE_PYTHON_NOT_FOUND"
}

function Assert-ProjectPath([string]$Path, [string]$Code) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw $Code }
    $cursor = $absolute
    while ($cursor -and $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

$reportPath = Join-Path $projectRoot "reports\release-candidate.json"
if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
    throw "JOBFLOW_RELEASE_CANDIDATE_REPORT_MISSING"
}
try { $candidate = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json }
catch { throw "JOBFLOW_RELEASE_CANDIDATE_REPORT_INVALID" }
if (
    $candidate.schema_version -ne 1 -or
    $candidate.status -ne "RELEASE_CANDIDATE_BUILT" -or
    $candidate.uploaded -ne $false -or
    ([string]$candidate.version) -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
    ([string]$candidate.commit) -notmatch '^[0-9a-f]{40}$' -or
    ([string]$candidate.artifact_sha256) -notmatch '^sha256:[0-9a-f]{64}$'
) { throw "JOBFLOW_RELEASE_CANDIDATE_REPORT_INVALID" }

$gitStatus = (& git -C $projectRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_RELEASE_GIT_FAILED" }
if (-not [string]::IsNullOrWhiteSpace(($gitStatus -join "`n"))) { throw "JOBFLOW_RELEASE_WORKTREE_NOT_CLEAN" }
$head = (& git -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -cne [string]$candidate.commit) {
    throw "JOBFLOW_RELEASE_COMMIT_MISMATCH"
}

$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "dist"))
$archivePath = [IO.Path]::GetFullPath((Join-Path $distRoot ([string]$candidate.artifact_name)))
Assert-ProjectPath $distRoot "JOBFLOW_RELEASE_DIST_PATH_UNTRUSTED"
Assert-ProjectPath $archivePath "JOBFLOW_RELEASE_ARCHIVE_PATH_UNTRUSTED"
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) { throw "JOBFLOW_RELEASE_ARCHIVE_MISSING" }
$archive = Get-Item -LiteralPath $archivePath
$archiveHash = "sha256:" + (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archive.Length -ne [long]$candidate.artifact_bytes -or $archiveHash -cne [string]$candidate.artifact_sha256) {
    throw "JOBFLOW_RELEASE_ARCHIVE_IDENTITY_MISMATCH"
}

$manifestPath = Join-Path $distRoot "JobFlow-update-manifest.json"
$signaturePath = Join-Path $distRoot "JobFlow-update-manifest.sig.json"
$python = Find-Python
$pythonCommand = [string]$python.Command
$pythonPrefix = @($python.Prefix)
$env:PYTHONPATH = Join-Path $projectRoot "src"
& $pythonCommand @pythonPrefix -m jobops.update_manifest build `
    --archive $archivePath --version ([string]$candidate.version) `
    --commit ([string]$candidate.commit) --output $manifestPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED" }

$signOutput = & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $projectRoot "scripts\release-signing.ps1") `
    -Action Sign -ManifestPath $manifestPath -SignatureOutput $signaturePath
if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_RELEASE_MANIFEST_SIGN_FAILED" }
try { $signResult = $signOutput | ConvertFrom-Json }
catch { throw "JOBFLOW_RELEASE_SIGNATURE_RESULT_INVALID" }
if ($signResult.status -ne "UPDATE_MANIFEST_SIGNED") { throw "JOBFLOW_RELEASE_MANIFEST_SIGN_FAILED" }

$channelPath = Join-Path $projectRoot "config\update-channel.json"
$verifyOutput = & $pythonCommand @pythonPrefix -m jobops.update_manifest verify `
    --manifest $manifestPath --signature $signaturePath --archive $archivePath `
    --current-version "0.0.0" --channel $channelPath
if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_RELEASE_SIGNED_BUNDLE_VERIFY_FAILED" }
try { $verification = $verifyOutput | ConvertFrom-Json }
catch { throw "JOBFLOW_RELEASE_SIGNED_BUNDLE_RESULT_INVALID" }
if ($verification.status -ne "UPDATE_BUNDLE_VERIFIED" -or $verification.finding_count -ne 0) {
    throw "JOBFLOW_RELEASE_SIGNED_BUNDLE_VERIFY_FAILED"
}

[ordered]@{
    schema_version = 1
    status = "SIGNED_UPDATE_BUNDLE_READY"
    version = [string]$candidate.version
    commit = [string]$candidate.commit
    archive_name = [IO.Path]::GetFileName($archivePath)
    archive_sha256 = $archiveHash
    manifest_name = [IO.Path]::GetFileName($manifestPath)
    manifest_sha256 = "sha256:" + (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    signature_name = [IO.Path]::GetFileName($signaturePath)
    signature_sha256 = "sha256:" + (Get-FileHash -LiteralPath $signaturePath -Algorithm SHA256).Hash.ToLowerInvariant()
    key_id = [string]$signResult.key_id
    uploaded = $false
    external_network_actions = 0
    real_external_actions = 0
} | ConvertTo-Json -Compress
exit 0
