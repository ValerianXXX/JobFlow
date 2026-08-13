[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    throw "Python 3.11 or newer is required. Install Python for Windows, then run this script again."
}

$versionText = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect the Python installation." }
$parts = $versionText.Trim().Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
    throw "JobFlow requires Python 3.11 or newer."
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $pythonCommand.Source -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the private JobFlow environment." }
}

& $venvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Unable to update the JobFlow installer." }
& $venvPython -m pip install --disable-pip-version-check -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw "Unable to install JobFlow dependencies." }

Write-Host "JobFlow installation is ready."
Write-Host "Run: .\scripts\start-jobflow.ps1"
