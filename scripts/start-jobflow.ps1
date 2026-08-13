[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "未找到 JobFlow 项目根目录。 / JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "JobFlow 尚未安装。请先双击 Install JobFlow.cmd。 / JobFlow is not installed yet; run Install JobFlow.cmd first."
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
    throw "JobFlow 未能正常启动（代码 $jobflowExitCode）。 / JobFlow stopped with exit code $jobflowExitCode."
}
