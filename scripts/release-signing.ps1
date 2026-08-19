[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Initialize", "Sign")]
    [string]$Action,
    [string]$ManifestPath,
    [string]$SignatureOutput,
    [switch]$EmitChannel
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Security
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND"
}

$localAppDataRoot = (Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path
$jobFlowRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobOps"))
$keyRoot = [IO.Path]::GetFullPath((Join-Path $jobFlowRoot "ReleaseSigning"))
$markerPath = Join-Path $keyRoot ".jobflow-release-signing-root"
$keyPath = Join-Path $keyRoot "release-signing-key.dpapi"
$algorithm = "RSA-PKCS1-v1_5-SHA256"

function Assert-NoReparse([string]$Path, [string]$Boundary, [string]$Code) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $limit = [IO.Path]::GetFullPath($Boundary)
    $prefix = $limit.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -ne $limit -and -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw $Code
    }
    $cursor = $absolute
    while ($cursor -and ($cursor -eq $limit -or $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase))) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
        }
        if ($cursor -eq $limit) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Assert-KeyPath([string]$Path) {
    Assert-NoReparse $localAppDataRoot $localAppDataRoot "JOBFLOW_RELEASE_KEY_ROOT_UNTRUSTED"
    Assert-NoReparse $Path $jobFlowRoot "JOBFLOW_RELEASE_KEY_PATH_UNTRUSTED"
}

function Set-CurrentUserOnly([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $grant = "*$($identity.User.Value):(OI)(CI)F"
    & "$env:SystemRoot\System32\icacls.exe" $Path "/inheritance:r" "/grant:r" $grant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_RELEASE_KEY_ACL_FAILED" }
}

function ConvertTo-Base64Url([byte[]]$Bytes) {
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function ConvertFrom-Base64Value([string]$Value) {
    return [Convert]::FromBase64String($Value)
}

function Get-ParameterJson([Security.Cryptography.RSAParameters]$Parameters) {
    return [ordered]@{
        D = [Convert]::ToBase64String($Parameters.D)
        DP = [Convert]::ToBase64String($Parameters.DP)
        DQ = [Convert]::ToBase64String($Parameters.DQ)
        Exponent = [Convert]::ToBase64String($Parameters.Exponent)
        InverseQ = [Convert]::ToBase64String($Parameters.InverseQ)
        Modulus = [Convert]::ToBase64String($Parameters.Modulus)
        P = [Convert]::ToBase64String($Parameters.P)
        Q = [Convert]::ToBase64String($Parameters.Q)
    } | ConvertTo-Json -Compress
}

function Import-PrivateParameters([string]$Json) {
    $value = $Json | ConvertFrom-Json
    $required = @("D", "DP", "DQ", "Exponent", "InverseQ", "Modulus", "P", "Q")
    foreach ($name in $required) {
        if ([string]::IsNullOrWhiteSpace([string]$value.$name)) { throw "JOBFLOW_RELEASE_KEY_INVALID" }
    }
    $parameters = New-Object Security.Cryptography.RSAParameters
    $parameters.D = ConvertFrom-Base64Value ([string]$value.D)
    $parameters.DP = ConvertFrom-Base64Value ([string]$value.DP)
    $parameters.DQ = ConvertFrom-Base64Value ([string]$value.DQ)
    $parameters.Exponent = ConvertFrom-Base64Value ([string]$value.Exponent)
    $parameters.InverseQ = ConvertFrom-Base64Value ([string]$value.InverseQ)
    $parameters.Modulus = ConvertFrom-Base64Value ([string]$value.Modulus)
    $parameters.P = ConvertFrom-Base64Value ([string]$value.P)
    $parameters.Q = ConvertFrom-Base64Value ([string]$value.Q)
    if ($parameters.Modulus.Length -lt 256 -or $parameters.Exponent.Length -lt 1) {
        throw "JOBFLOW_RELEASE_KEY_INVALID"
    }
    return $parameters
}

function Protect-Key([Security.Cryptography.RSAParameters]$Parameters) {
    $plain = [Text.Encoding]::UTF8.GetBytes((Get-ParameterJson $Parameters))
    try {
        return [Security.Cryptography.ProtectedData]::Protect(
            $plain,
            [Text.Encoding]::UTF8.GetBytes("JobFlow release signing key v1"),
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
    }
    finally { [Array]::Clear($plain, 0, $plain.Length) }
}

function Read-Key {
    Assert-KeyPath $keyPath
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf) -or
        (Get-Content -LiteralPath $markerPath -Raw).Trim() -ne "JOBFLOW_RELEASE_SIGNING_ROOT_V1" -or
        -not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
        throw "JOBFLOW_RELEASE_KEY_NOT_INITIALIZED"
    }
    $protected = [IO.File]::ReadAllBytes($keyPath)
    try {
        $plain = [Security.Cryptography.ProtectedData]::Unprotect(
            $protected,
            [Text.Encoding]::UTF8.GetBytes("JobFlow release signing key v1"),
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        try { return Import-PrivateParameters ([Text.Encoding]::UTF8.GetString($plain)) }
        finally { [Array]::Clear($plain, 0, $plain.Length) }
    }
    catch { throw "JOBFLOW_RELEASE_KEY_DECRYPT_FAILED" }
    finally { [Array]::Clear($protected, 0, $protected.Length) }
}

function Get-PublicDescriptor([Security.Cryptography.RSAParameters]$Parameters) {
    $modulus = ConvertTo-Base64Url $Parameters.Modulus
    $exponent = ConvertTo-Base64Url $Parameters.Exponent
    $canonical = "{`"algorithm`":`"$algorithm`",`"e`":`"$exponent`",`"n`":`"$modulus`"}"
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = -join ($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonical)) | ForEach-Object { $_.ToString("x2") }) }
    finally { $hasher.Dispose() }
    return [ordered]@{
        algorithm = $algorithm
        key_id = "sha256:$digest"
        modulus_b64url = $modulus
        exponent_b64url = $exponent
    }
}

function Write-BytesAtomic([string]$Path, [byte[]]$Bytes) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($absolute)
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $temporary = Join-Path $parent (([IO.Path]::GetFileName($absolute)) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllBytes($temporary, $Bytes)
        Move-Item -LiteralPath $temporary -Destination $absolute -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
    }
}

if ($Action -eq "Initialize") {
    Assert-KeyPath $keyRoot
    if (Test-Path -LiteralPath $keyPath -PathType Leaf) {
        $parameters = Read-Key
        $created = $false
    }
    else {
        if (Test-Path -LiteralPath $keyRoot -PathType Container) { Assert-KeyPath $keyRoot }
        else { New-Item -ItemType Directory -Path $keyRoot -Force | Out-Null }
        [IO.File]::WriteAllText($markerPath, "JOBFLOW_RELEASE_SIGNING_ROOT_V1", (New-Object Text.UTF8Encoding($false)))
        $rsa = New-Object Security.Cryptography.RSACng 3072
        try {
            $parameters = $rsa.ExportParameters($true)
            $protected = Protect-Key $parameters
            try { Write-BytesAtomic $keyPath $protected }
            finally { [Array]::Clear($protected, 0, $protected.Length) }
        }
        finally { $rsa.Dispose() }
        Set-CurrentUserOnly $keyRoot
        $created = $true
    }
    $public = Get-PublicDescriptor $parameters
    $result = [ordered]@{ schema_version = 1; status = "RELEASE_SIGNING_KEY_READY"; created = $created; key_id = $public.key_id }
    if ($EmitChannel) {
        $result.channel = [ordered]@{
            schema_version = 1
            product = "JobFlow"
            channel = "stable"
            repository = "ValerianXXX/JobFlow"
            latest_release_api_url = "https://api.github.com/repos/ValerianXXX/JobFlow/releases/latest"
            manifest_asset_name = "JobFlow-update-manifest.json"
            signature_asset_name = "JobFlow-update-manifest.sig.json"
            allowed_download_hosts = @("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
            signature = $public
        }
    }
    $result | ConvertTo-Json -Depth 8 -Compress
    exit 0
}

if ([string]::IsNullOrWhiteSpace($ManifestPath) -or [string]::IsNullOrWhiteSpace($SignatureOutput)) {
    throw "JOBFLOW_RELEASE_SIGNING_ARGUMENT_REQUIRED"
}
$manifest = (Resolve-Path -LiteralPath $ManifestPath).Path
Assert-NoReparse $manifest $projectRoot "JOBFLOW_RELEASE_MANIFEST_PATH_UNTRUSTED"
$output = [IO.Path]::GetFullPath($SignatureOutput)
$outputParent = [IO.Path]::GetDirectoryName($output)
$projectPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $output.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_RELEASE_SIGNATURE_OUTPUT_FORBIDDEN"
}
if (-not (Test-Path -LiteralPath $outputParent -PathType Container)) {
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
}
Assert-NoReparse $outputParent $projectRoot "JOBFLOW_RELEASE_SIGNATURE_OUTPUT_UNTRUSTED"
$parameters = Read-Key
$public = Get-PublicDescriptor $parameters
$rsa = New-Object Security.Cryptography.RSACng
$manifestBytes = [IO.File]::ReadAllBytes($manifest)
try {
    $rsa.ImportParameters($parameters)
    $signature = $rsa.SignData(
        $manifestBytes,
        [Security.Cryptography.HashAlgorithmName]::SHA256,
        [Security.Cryptography.RSASignaturePadding]::Pkcs1
    )
    $encoded = ConvertTo-Base64Url $signature
    $json = "{`"algorithm`":`"$algorithm`",`"key_id`":`"$($public.key_id)`",`"schema_version`":1,`"signature_b64url`":`"$encoded`"}"
    Write-BytesAtomic $output ([Text.Encoding]::UTF8.GetBytes($json))
}
finally {
    $rsa.Dispose()
    [Array]::Clear($manifestBytes, 0, $manifestBytes.Length)
    if ($null -ne $signature) { [Array]::Clear($signature, 0, $signature.Length) }
}
@{ schema_version = 1; status = "UPDATE_MANIFEST_SIGNED"; key_id = $public.key_id } | ConvertTo-Json -Compress
exit 0
