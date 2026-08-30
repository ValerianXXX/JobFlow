[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputBundle,
    [Parameter(Mandatory = $true)][string]$GitPath,
    [Parameter(Mandatory = $true)][string]$SourceCommit,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$VerificationPythonPath = "",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
trap {
    $code = [string]$_.Exception.Message
    if ($code -notmatch '^JOBFLOW_RUNTIME_[A-Z0-9_]+$') {
        $code = "JOBFLOW_RUNTIME_BUILD_FAILED"
    }
    [Console]::Error.WriteLine($code)
    exit 1
}

if ($SourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw "JOBFLOW_RUNTIME_SOURCE_COMMIT_INVALID"
}

try {
    $expectedProject = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    $project = if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $expectedProject
    }
    else { [IO.Path]::GetFullPath($ProjectRoot) }
    $bundle = [IO.Path]::GetFullPath($InputBundle)
    $git = [IO.Path]::GetFullPath($GitPath)
    $output = [IO.Path]::GetFullPath($OutputDirectory)
    $expectedVerificationPython = [IO.Path]::GetFullPath(
        (Join-Path $expectedProject ".venv\Scripts\python.exe")
    )
    $verificationPython = if ([string]::IsNullOrWhiteSpace($VerificationPythonPath)) {
        $expectedVerificationPython
    }
    else { [IO.Path]::GetFullPath($VerificationPythonPath) }
}
catch {
    throw "JOBFLOW_RUNTIME_BUILD_PATH_INVALID"
}
if ($project -cne $expectedProject -or $verificationPython -cne $expectedVerificationPython) {
    throw "JOBFLOW_RUNTIME_BUILD_PATH_UNBOUNDED"
}

$prepare = Join-Path $project "scripts\prepare-windows-runtime-inputs.ps1"
$builder = Join-Path $project "scripts\build-windows-runtime-closure.ps1"
foreach ($path in @($prepare, $builder, $verificationPython, $git)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "JOBFLOW_RUNTIME_BUILD_INPUT_MISSING"
    }
}
if (-not (Test-Path -LiteralPath $bundle -PathType Container)) {
    throw "JOBFLOW_RUNTIME_INPUT_BUNDLE_INVALID"
}

# This verifier performs no network request. The protected builder then opens,
# authenticates and read-locks every individual input for its complete use.
& $prepare -Destination $bundle -VerifyOnly -PythonPath $verificationPython -ProjectRoot $project

$artifact = Join-Path $bundle "python\python-3.13.15-embed-amd64.zip"
$sigstore = Join-Path $bundle "python\python-3.13.15-embed-amd64.zip.sigstore"
$wheelhouse = Join-Path $bundle "wheelhouse"
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf) -or
    -not (Test-Path -LiteralPath $sigstore -PathType Leaf) -or
    -not (Test-Path -LiteralPath $wheelhouse -PathType Container)) {
    throw "JOBFLOW_RUNTIME_INPUT_BUNDLE_INVALID"
}

& $builder `
    -PythonArtifactPath $artifact `
    -SigstoreBundlePath $sigstore `
    -WheelhousePath $wheelhouse `
    -GitPath $git `
    -SourceCommit $SourceCommit `
    -OutputDirectory $output `
    -ProjectRoot $project
