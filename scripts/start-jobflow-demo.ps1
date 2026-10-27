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
    throw "演示需要本地开发环境；请按 README 的 Development 步骤创建 .venv。正式安装器不会创建 .venv。 / The source demo needs the Development setup in README. The signed desktop installer does not create .venv."
}

Push-Location $projectRoot
try {
    $arguments = @("-m", "jobops.cli", "demo")
    if ($NoBrowser) { $arguments += "--no-browser" }
    & $venvPython @arguments
    $jobflowExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($jobflowExitCode -ne 0) {
    throw "JobFlow 合成演示未能正常启动（代码 $jobflowExitCode）。 / JobFlow synthetic demo stopped with exit code $jobflowExitCode."
}
