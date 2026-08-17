[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "未找到 JobFlow 项目根目录。 / JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}

function Find-SupportedPython {
    $candidates = @(
        @{ Name = "python"; Prefix = @() },
        @{ Name = "py"; Prefix = @("-3") }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Name -ErrorAction SilentlyContinue
        if ($null -eq $command) { continue }
        $prefix = @($candidate.Prefix)
        $versionText = & $command.Source @prefix -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or $null -eq $versionText) { continue }
        $parts = $versionText.Trim().Split('.')
        if ($parts.Count -lt 2) { continue }
        if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
            return @{ Command = $command.Source; Prefix = $prefix; Version = $versionText.Trim() }
        }
    }
    return $null
}

$python = Find-SupportedPython
if ($null -eq $python) {
    throw "需要 Python 3.11 或更高版本。请安装 Windows 版 Python 后重试。 / Python 3.11 or newer is required."
}
$pythonCommand = [string]$python.Command
$pythonPrefix = @($python.Prefix)

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host "正在创建 JobFlow 的本地运行环境…… / Creating the local JobFlow environment..."
    & $pythonCommand @pythonPrefix -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "无法创建 JobFlow 本地环境。 / Unable to create the local JobFlow environment." }
}

Push-Location $projectRoot
try {
    Write-Host "正在准备经过测试的构建工具…… / Preparing tested build tools..."
    & $venvPython -m pip install --quiet --disable-pip-version-check --no-input "setuptools>=77,<81" "wheel>=0.43,<1"
    if ($LASTEXITCODE -ne 0) { throw "无法准备 JobFlow 构建工具。请检查网络后重试。 / Unable to prepare JobFlow build tools; check the network and retry." }
    Write-Host "正在安装 JobFlow…… / Installing JobFlow..."
    & $venvPython -m pip install --quiet --disable-pip-version-check --no-input --no-build-isolation --editable ".[build]"
    if ($LASTEXITCODE -ne 0) { throw "无法安装 JobFlow 依赖。 / Unable to install JobFlow dependencies." }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "JobFlow 依赖检查未通过。 / JobFlow dependency verification failed." }
}
finally {
    Pop-Location
}

Write-Host "正在准备一次性浏览器伴侣…… / Preparing the one-time Browser Companion setup..."
$companionInstaller = Join-Path $PSScriptRoot "install-jobflow-browser-companion.ps1"
if (-not (Test-Path -LiteralPath $companionInstaller -PathType Leaf)) {
    throw "浏览器伴侣安装入口缺失。请重新解压完整 JobFlow 包。 / Browser Companion installer is missing; extract the complete JobFlow package again."
}
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $companionInstaller
if ($LASTEXITCODE -ne 0) {
    throw "浏览器伴侣准备未完成。 / Browser Companion setup did not finish."
}

Write-Host "JobFlow 安装完成（Python $($python.Version)）。 / JobFlow installation is ready."
Write-Host "浏览器只要求一次安全操作：从打开的官方商店页面安装 JobFlow Browser Companion。 / The browser requires one security action: install JobFlow Browser Companion from the official store page that opens."
Write-Host "JobFlow 现在会自动打开；以后双击 Start JobFlow.cmd 即可。 / JobFlow will open now; later, double-click Start JobFlow.cmd."
