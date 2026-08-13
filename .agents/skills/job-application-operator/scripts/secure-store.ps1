[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Put','Get','Test','Delete')][string]$Operation,
    [Parameter(Mandatory = $false)][string]$Reference,
    [Parameter(Mandatory = $false)][ValidateSet('Utf8','Base64')][string]$InputEncoding = 'Utf8',
    [Parameter(Mandatory = $false)][ValidateSet('Utf8','Base64')][string]$OutputEncoding = 'Utf8'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$localData = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { [Environment]::GetFolderPath('LocalApplicationData') } else { $env:LOCALAPPDATA }
$privateRoot = Join-Path $localData 'JobOps\private'
[System.IO.Directory]::CreateDirectory($privateRoot) | Out-Null

function Resolve-RefPath([string]$Ref) {
    if ($Ref -notmatch '^secure-ref:[A-Za-z0-9_-]{8,128}$') { throw 'Invalid secure reference.' }
    $id = $Ref.Substring('secure-ref:'.Length)
    return Join-Path $privateRoot ($id + '.dpapi')
}

if ($Operation -eq 'Put') {
    $inputValue = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrEmpty($inputValue)) { throw 'Private input is empty.' }
    $id = if ($Reference) {
        if ($Reference -notmatch '^secure-ref:[A-Za-z0-9_-]{8,128}$') { throw 'Invalid secure reference.' }
        $Reference.Substring('secure-ref:'.Length)
    } else { [Guid]::NewGuid().ToString('N') }
    $ref = 'secure-ref:' + $id
    $path = Resolve-RefPath $ref
    $bytes = if ($InputEncoding -eq 'Base64') { [Convert]::FromBase64String($inputValue) } else { [Text.Encoding]::UTF8.GetBytes($inputValue) }
    try {
        $cipher = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        [IO.File]::WriteAllBytes($path, $cipher)
        $hash = [Security.Cryptography.SHA256]::Create()
        try { $hex = -join ($hash.ComputeHash($cipher) | ForEach-Object { $_.ToString('x2') }) } finally { $hash.Dispose() }
        [pscustomobject]@{status='STORED';secure_ref=$ref;ciphertext_sha256=('sha256:'+$hex);plaintext_logged=$false} | ConvertTo-Json -Compress
    }
    finally {
        if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
        $inputValue = $null
    }
    exit 0
}

$path = Resolve-RefPath $Reference
if ($Operation -eq 'Test') {
    [pscustomobject]@{status=if(Test-Path -LiteralPath $path -PathType Leaf){'PRESENT'}else{'MISSING'};secure_ref=$Reference;plaintext_logged=$false} | ConvertTo-Json -Compress
    exit 0
}
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Secure reference not found.' }
if ($Operation -eq 'Delete') {
    Remove-Item -LiteralPath $path -Force
    [pscustomobject]@{status='DELETED';secure_ref=$Reference;plaintext_logged=$false;secure_erase_claimed=$false} | ConvertTo-Json -Compress
    exit 0
}
$cipher = [IO.File]::ReadAllBytes($path)
$plainBytes = [Security.Cryptography.ProtectedData]::Unprotect($cipher, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
try {
    if ($OutputEncoding -eq 'Base64') { [Console]::Out.Write([Convert]::ToBase64String($plainBytes)) }
    else { [Console]::Out.Write([Text.Encoding]::UTF8.GetString($plainBytes)) }
}
finally { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
