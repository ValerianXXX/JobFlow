[CmdletBinding()]
param(
    [switch]$Json,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvPython = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    Join-Path $projectRoot ".venv\Scripts\python.exe"
}
else {
    $PythonPath
}
$checks = [System.Collections.Generic.List[object]]::new()

function Add-JobFlowCheck {
    param(
        [string]$Id,
        [bool]$Passed,
        [string]$LabelZh,
        [string]$LabelEn,
        [string]$ActionZh,
        [string]$ActionEn
    )
    $checks.Add([ordered]@{
        id = $Id
        status = if ($Passed) { "PASS" } else { "FAIL" }
        label_zh = $LabelZh
        label_en = $LabelEn
        action_zh = if ($Passed) { "无需操作" } else { $ActionZh }
        action_en = if ($Passed) { "No action needed" } else { $ActionEn }
    })
}

$rootReady = Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf
Add-JobFlowCheck "PROJECT_ROOT" $rootReady "项目文件完整" "Project files present" "重新解压完整源码包" "Extract the complete source package again"

$pythonReady = Test-Path -LiteralPath $venvPython -PathType Leaf
Add-JobFlowCheck "PYTHON_RUNTIME" $pythonReady "本地运行环境已安装" "Local runtime installed" "双击 Install JobFlow.cmd" "Run Install JobFlow.cmd"

$packageReady = $false
$dependenciesReady = $false
$cliReady = $false
$detectedVersion = $null
if ($pythonReady) {
    $savedErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $versionText = & $venvPython -c "import jobops; print(jobops.__version__)" 2>$null
        $packageReady = $LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$versionText)
        if ($packageReady) { $detectedVersion = ([string]$versionText).Trim() }
        $dependencyText = & $venvPython -m pip check 2>$null
        $dependenciesReady = $LASTEXITCODE -eq 0
        $helpText = & $venvPython -m jobops.cli --help 2>$null
        $cliReady = $LASTEXITCODE -eq 0 -and $helpText -match "onboarding-center" -and $helpText -match "demo"
    }
    finally {
        $ErrorActionPreference = $savedErrorPreference
    }
}
Add-JobFlowCheck "PACKAGE_IMPORT" $packageReady "JobFlow 程序可载入" "JobFlow package loads" "重新运行 Install JobFlow.cmd" "Run Install JobFlow.cmd again"
Add-JobFlowCheck "DEPENDENCIES" $dependenciesReady "依赖完整" "Dependencies healthy" "重新运行 Install JobFlow.cmd" "Run Install JobFlow.cmd again"
Add-JobFlowCheck "CLI" $cliReady "启动入口可用" "Startup entry available" "重新运行 Install JobFlow.cmd" "Run Install JobFlow.cmd again"

$requiredSchemas = @(
    "candidate-profile.schema.json",
    "onboarding-answer-bank.schema.json",
    "onboarding-completion.schema.json",
    "external-claim-set.schema.json",
    "application-readiness.schema.json",
    "resume-tailoring-manifest.schema.json",
    "review-packet.schema.json",
    "release-readiness.schema.json"
)
$schemasReady = -not ($requiredSchemas | Where-Object { -not (Test-Path -LiteralPath (Join-Path $projectRoot "schemas\$_") -PathType Leaf) })
Add-JobFlowCheck "SCHEMAS" $schemasReady "核心 Schema 齐全" "Core Schemas present" "重新解压完整源码包" "Extract the complete source package again"

$secureStoreReady = Test-Path -LiteralPath (Join-Path $projectRoot ".agents\skills\job-application-operator\scripts\secure-store.ps1") -PathType Leaf
Add-JobFlowCheck "SECURE_STORE" $secureStoreReady "DPAPI 安全存储组件齐全" "DPAPI secure-store helper present" "重新解压完整源码包" "Extract the complete source package again"

$privateStoreHealthy = $false
$databasePath = Join-Path $projectRoot "state\jobops.db"
if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
    $privateStoreHealthy = $true
}
elseif ($pythonReady -and $packageReady -and $secureStoreReady) {
    $savedErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $privateRaw = (& $venvPython -m jobops.cli check-private-store 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($privateRaw)) {
            $privateResult = $privateRaw | ConvertFrom-Json
            $privateStoreHealthy = $privateResult.status -eq "PRIVATE_STORE_HEALTHY"
        }
    }
    catch {
        $privateStoreHealthy = $false
    }
    finally {
        $ErrorActionPreference = $savedErrorPreference
    }
}
Add-JobFlowCheck "PRIVATE_STORE_INTEGRITY" $privateStoreHealthy "私密引用、密文与临时区一致" "Private references, ciphertext and staging are consistent" "不要启动；先保留现状并运行发布就绪检查以查看脱敏原因" "Do not start; preserve the current state and run the release-readiness check for a redacted cause"

$uiReady = @("index.html", "app.js", "styles.css") | ForEach-Object {
    Test-Path -LiteralPath (Join-Path $projectRoot "src\jobops\ui\$_") -PathType Leaf
} | Where-Object { -not $_ } | Measure-Object | Select-Object -ExpandProperty Count
$uiReady = $uiReady -eq 0
Add-JobFlowCheck "LOCAL_UI" $uiReady "本机界面文件齐全" "Local UI files present" "重新解压完整源码包" "Extract the complete source package again"

$companionReady = @(
    "Install JobFlow Browser Companion.cmd",
    "browser-companion\manifest.json",
    "browser-companion\service-worker.js",
    "browser-companion\dom.js",
    "browser-companion\popup.html"
) | ForEach-Object {
    Test-Path -LiteralPath (Join-Path $projectRoot $_) -PathType Leaf
} | Where-Object { -not $_ } | Measure-Object | Select-Object -ExpandProperty Count
$companionReady = $companionReady -eq 0
Add-JobFlowCheck "BROWSER_COMPANION" $companionReady "浏览器伴侣文件齐全" "Browser Companion files present" "重新解压完整源码包" "Extract the complete source package again"

$policyReady = $false
$policyPath = Join-Path $projectRoot "config\policy.json"
if (Test-Path -LiteralPath $policyPath -PathType Leaf) {
    try {
        $policy = Get-Content -LiteralPath $policyPath -Raw | ConvertFrom-Json
        $policyReady = (
            $policy.external_actions_enabled -eq $false -and
            $policy.user_present_browser_assist_enabled -eq $true -and
            $policy.final_submit_implementation_present -eq $false -and
            $policy.unattended_submission_enabled -eq $false -and
            $policy.phase_5_6_authorization -eq "PER_APPLICATION_USER_PRESENT_PREFILL_UPLOAD_ONLY" -and
            [int]$policy.real_transport_adapters_registered -eq 1 -and
            $policy.scheduler_mode -eq "fake_clock_only"
        )
    }
    catch {
        $policyReady = $false
    }
}
Add-JobFlowCheck "SAFETY_POLICY" $policyReady "仅允许逐申请在场预填与上传；提交和无人值守动作锁定" "Only per-application user-present fill/upload is enabled; submit and unattended actions are locked" "不要启动；恢复原始 config/policy.json 后重试" "Do not start; restore config/policy.json and retry"

$failed = @($checks | Where-Object { $_.status -ne "PASS" })
$result = [ordered]@{
    schema_version = 1
    status = if ($failed.Count -eq 0) { "JOBFLOW_READY" } else { "JOBFLOW_NEEDS_REPAIR" }
    version = $detectedVersion
    checks_passed = $checks.Count - $failed.Count
    checks_total = $checks.Count
    checks = $checks
    private_values_read = 0
    private_values_emitted = 0
    network_actions = 0
    real_external_actions = 0
    next_safe_action = if ($failed.Count -eq 0) { "Start JobFlow.cmd" } else { $failed[0].action_en }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 4
}
else {
    Write-Host ""
    Write-Host "JobFlow 一键自检 / One-click health check" -ForegroundColor Cyan
    Write-Host ""
    foreach ($item in $checks) {
        $color = if ($item.status -eq "PASS") { "Green" } else { "Red" }
        Write-Host "[$($item.status)] $($item.label_zh) / $($item.label_en)" -ForegroundColor $color
        if ($item.status -ne "PASS") {
            Write-Host "       $($item.action_zh) / $($item.action_en)"
        }
    }
    Write-Host ""
    if ($failed.Count -eq 0) {
        Write-Host "JobFlow 可以启动。私人资料未被读取，网络与真实外部动作均为 0。" -ForegroundColor Green
        Write-Host "JobFlow is ready. No private data was read; network and real external actions are 0." -ForegroundColor Green
    }
    else {
        Write-Host "JobFlow 需要修复；请先按第一条失败项操作。 / JobFlow needs repair; follow the first failed check." -ForegroundColor Red
    }
}

if ($failed.Count -ne 0) { exit 2 }
