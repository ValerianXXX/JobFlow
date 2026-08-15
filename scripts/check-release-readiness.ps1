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

$actions = @{
    GIT_REPOSITORY_REQUIRED = @("源码包尚未初始化为本地 Git 仓库；这不影响 JobFlow 正常使用", "The source package is not initialized as a local Git repository; normal JobFlow use is unaffected")
    GIT_WORKTREE_NOT_CLEAN = @("先提交并验证本地改动", "Commit and verify the local changes")
    VERSION_METADATA_MISMATCH = @("统一版本号与变更记录", "Align the version metadata and changelog")
    LOCAL_RELEASE_VERIFICATION_NOT_PASSING = @("运行完整本地发布验证", "Run the complete local release verification")
    PUBLIC_REPOSITORY_CONTENT_FAILED = @("修复公开内容扫描发现", "Resolve the public-content scan findings")
    GIT_AUTHOR_IDENTITY_REVIEW_REQUIRED = @("确认公开使用 GitHub noreply 作者身份", "Confirm a public GitHub noreply author identity")
    SOURCE_CANDIDATE_MISSING = @("构建确定性的本地源码候选包", "Build the deterministic local source candidate")
    SOURCE_CANDIDATE_STALE = @("从当前提交重新构建源码候选包", "Rebuild the source candidate from the current commit")
    SOURCE_CANDIDATE_FAIL = @("检查本地源码候选包失败", "Review the local source-candidate failure")
    GITHUB_REPOSITORY_METADATA_REQUIRED = @("确认仓库所有者、名称、简介、主题与可见性", "Confirm repository owner, name, descriptions, topics, and visibility")
    PRIVATE_VULNERABILITY_REPORTING_UNCONFIRMED = @("确认未来仓库的私密漏洞报告方式", "Confirm private vulnerability reporting for the future repository")
    SANITIZED_SCREENSHOTS_NOT_APPROVED = @("生成并审阅脱敏的中英文演示截图", "Capture and approve sanitized Chinese and English demo screenshots")
    CLEAN_WINDOWS_PROFILE_TEST_REQUIRED = @("在干净的受支持 Windows 用户中测试候选包", "Test the candidate in a clean supported Windows user profile")
    INDEPENDENT_QA_STALE_OR_MISSING = @("在最终冻结提交上重新运行独立 QA", "Run fresh independent QA on the final frozen commit")
    RELEASE_TAG_MISSING = @("全部证据通过后创建本地注释或签名标签", "Create a local annotated or signed tag after every gate passes")
    RELEASE_TAG_MISMATCH = @("检查本地标签与版本不一致", "Review the local tag and version mismatch")
}

function New-UnavailableResult {
    param([string]$Code, [string]$Action)
    return [ordered]@{
        schema_version = 1
        status = "PUBLIC_RELEASE_BLOCKED"
        blockers = @($Code)
        upload_performed = $false
        network_actions = 0
        real_external_actions = 0
        next_safe_action = $Action
    }
}

$result = $null
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $result = New-UnavailableResult "PYTHON_RUNTIME_MISSING" "Run Install JobFlow.cmd"
}
else {
    $raw = ""
    Push-Location $projectRoot
    try {
        $savedErrorPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $raw = (& $venvPython -m jobops.release_readiness 2>$null | Out-String).Trim()
        }
        finally {
            $ErrorActionPreference = $savedErrorPreference
        }
    }
    finally {
        Pop-Location
    }
    try {
        $result = $raw | ConvertFrom-Json
    }
    catch {
        $result = New-UnavailableResult "READINESS_REPORT_INVALID" "Run Check JobFlow.cmd, then retry"
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
}
else {
    Write-Host ""
    Write-Host "JobFlow 发布就绪检查 / Public release readiness" -ForegroundColor Cyan
    Write-Host ""
    if ($result.status -eq "PUBLIC_RELEASE_READY") {
        Write-Host "[PASS] 所有本地门禁与已确认人工门禁均通过。 / All local and confirmed human gates pass." -ForegroundColor Green
    }
    else {
        Write-Host "[BLOCKED] 当前不能公开发布；这是安全停止，不是程序故障。" -ForegroundColor Yellow
        Write-Host "          Public release is not ready; this is a safe stop, not an application failure." -ForegroundColor Yellow
        Write-Host ""
        foreach ($code in @($result.blockers)) {
            $guidance = $actions[$code]
            Write-Host "- $code" -ForegroundColor Yellow
            if ($null -ne $guidance) {
                Write-Host "  $($guidance[0]) / $($guidance[1])"
            }
        }
    }
    Write-Host ""
    Write-Host "未创建仓库或标签；上传、网络与真实外部动作均为 0。" -ForegroundColor Green
    Write-Host "No repository or tag was created; uploads, network actions, and real external actions are 0." -ForegroundColor Green
}

if ($result.status -ne "PUBLIC_RELEASE_READY") { exit 2 }
