[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "JobFlow is not installed yet. Run .\scripts\install-jobflow.ps1 first."
}

Push-Location $projectRoot
try {
    $arguments = @("-m", "jobops.cli", "onboarding-center")
    if ($NoBrowser) { $arguments += "--no-browser" }
    & $venvPython @arguments
    $jobflowExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($jobflowExitCode -ne 0) {
    throw "JobFlow stopped with exit code $jobflowExitCode."
}
