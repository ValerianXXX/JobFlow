[CmdletBinding()]
param(
    [switch]$Json,
    [string]$PythonPath = "",
    [string]$GitPath = ""
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
    RELEASE_GIT_PATH_REQUIRED = @("提供受信任 Git 的绝对路径", "Provide the absolute path to the trusted Git executable")
    RELEASE_GIT_PATH_INVALID = @("检查受信任 Git 的绝对路径", "Check the absolute path to the trusted Git executable")
    RELEASE_GIT_UNTRUSTED = @("所选 Git 未通过发布工具身份策略", "The selected Git did not pass the release-tool identity policy")
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
    CLEAN_WINDOWS_PROFILE_TEST_OUTDATED = @("在当前精确提交和浏览器伴侣版本上重新执行干净 Windows 测试", "Repeat the clean Windows test against the exact current commit and Browser Companion version")
    CLEAN_WINDOWS_PROFILE_EVIDENCE_INVALID = @("修复干净 Windows 测试证明后再发布", "Repair the clean Windows test attestation before release")
    BROWSER_COMPANION_STORES_PENDING = @("核对 Chrome 与 Edge 商店中的扩展版本", "Verify the published Chrome and Edge extension versions")
    BROWSER_COMPANION_STORES_OUTDATED = @("先将所需浏览器伴侣版本发布到 Chrome 与 Edge 商店", "Publish the required Browser Companion version to both Chrome and Edge stores")
    BROWSER_COMPANION_STORES_INVALID = @("修复浏览器伴侣清单或商店版本证明", "Repair the Browser Companion manifest or store-version attestation")
    INDEPENDENT_QA_STALE_OR_MISSING = @("在最终冻结提交上重新运行独立 QA", "Run fresh independent QA on the final frozen commit")
    RELEASE_TAG_MISSING = @("全部证据通过后创建本地注释或签名标签", "Create a local annotated or signed tag after every gate passes")
    RELEASE_TAG_MISMATCH = @("检查本地标签与版本不一致", "Review the local tag and version mismatch")
    RELEASE_ATTESTATION_MISSING = @("准备并验证受保护签名的完整发布证明链", "Prepare and verify the protected, signed release-attestation chain")
    RELEASE_ATTESTATION_INVALID = @("修复发布包、运行时证明和发布者证明之间的绑定", "Repair the binding among the release bundle, runtime evidence, and publisher evidence")
    RELEASE_RUNTIME_CLOSURE_UNATTESTED = @("当前本机验证未覆盖公开签名所依赖的完整运行时闭包", "The local check does not attest the complete runtime closure used for public signing")
    CLEAN_WINDOWS_EVIDENCE_MISSING = @("在干净 Windows 中验证精确签名候选包并导出验收证明", "Verify the exact signed candidate on clean Windows and export its acceptance evidence")
    CLEAN_WINDOWS_EVIDENCE_INVALID = @("在精确签名候选包和当前浏览器伴侣版本上重做干净 Windows 验收", "Repeat clean-Windows acceptance against the exact signed candidate and current Browser Companion version")
}

function New-UnavailableResult {
    param([string]$Code, [string]$Action)
    $version = "0.0.0"
    try {
        $metadata = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
        $versionMatch = [regex]::Match(
            $metadata,
            '(?m)^version\s*=\s*"(?<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"\s*$'
        )
        if ($versionMatch.Success) {
            $version = $versionMatch.Groups["version"].Value
        }
    }
    catch {
        $version = "0.0.0"
    }
    $blockers = @(
        "RELEASE_ATTESTATION_MISSING",
        "RELEASE_RUNTIME_CLOSURE_UNATTESTED",
        $Code
    ) | Select-Object -Unique
    return [ordered]@{
        schema_version = 1
        status = "PUBLIC_RELEASE_BLOCKED"
        public_release_ready = $false
        runtime_closure_status = "UNATTESTED"
        release_attestation_status = "MISSING"
        clean_windows_evidence_status = "NOT_CHECKED"
        release_attestation_failure_code = "RELEASE_ATTESTATION_MISSING"
        version = $version
        head_commit = ("0" * 40)
        worktree_clean = $false
        version_consistent = $false
        local_verification_status = "MISSING_OR_STALE"
        public_repository_status = "FAIL"
        source_candidate_status = "MISSING"
        independent_qa_fresh = $false
        author_identity_status = "REVIEW_REQUIRED"
        release_tag_status = "MISSING"
        manual_release_gates = [ordered]@{
            repository_metadata = "PENDING"
            private_vulnerability_reporting = "PENDING"
            sanitized_screenshots = "PENDING"
            clean_windows_profile = "PENDING"
            browser_companion_stores = "PENDING"
        }
        blockers = @($blockers)
        upload_performed = $false
        network_actions = 0
        real_external_actions = 0
        next_safe_action = $Action
    }
}

function Resolve-ReleaseGitPath {
    param([string]$RequestedPath)

    $configured = if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $RequestedPath
    }
    elseif (-not [string]::IsNullOrWhiteSpace($env:JOBFLOW_RELEASE_GIT_PATH)) {
        $env:JOBFLOW_RELEASE_GIT_PATH
    }
    else {
        ""
    }
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($configured)) {
        if (-not [IO.Path]::IsPathRooted($configured)) {
            return $null
        }
        $candidates = @($configured)
    }
    else {
        foreach ($folder in @(
            [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles),
            [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
        )) {
            if (-not [string]::IsNullOrWhiteSpace($folder)) {
                $candidates += (Join-Path $folder "Git\mingw64\bin\git.exe")
            }
        }
    }
    foreach ($candidate in $candidates) {
        try {
            $absolute = [IO.Path]::GetFullPath($candidate)
            if (-not [IO.Path]::IsPathRooted($absolute)) {
                continue
            }
            $attributes = [IO.File]::GetAttributes($absolute)
            if (
                [IO.File]::Exists($absolute) -and
                [IO.Path]::GetFileName($absolute) -ieq "git.exe" -and
                -not (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
            ) {
                return $absolute
            }
        }
        catch {
            continue
        }
    }
    return $null
}

$result = $null
$resolvedGit = Resolve-ReleaseGitPath -RequestedPath $GitPath
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $result = New-UnavailableResult "PYTHON_RUNTIME_MISSING" "Run Install JobFlow.cmd"
}
elseif ([string]::IsNullOrWhiteSpace($resolvedGit)) {
    $result = New-UnavailableResult "RELEASE_GIT_PATH_REQUIRED" "Provide -GitPath with the absolute trusted Git executable"
}
else {
    $raw = ""
    Push-Location $projectRoot
    try {
        $savedErrorPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $raw = (& $venvPython -m jobops.release_readiness --git-path $resolvedGit 2>$null | Out-String).Trim()
        }
        finally {
            $ErrorActionPreference = $savedErrorPreference
        }
    }
    finally {
        Pop-Location
    }
    try {
        if ([string]::IsNullOrWhiteSpace($raw)) {
            throw "JOBFLOW_RELEASE_READINESS_OUTPUT_EMPTY"
        }
        $parsed = $raw | ConvertFrom-Json
        if ($null -eq $parsed -or [string]::IsNullOrWhiteSpace([string]$parsed.status)) {
            throw "JOBFLOW_RELEASE_READINESS_OUTPUT_INVALID"
        }
        $result = $parsed
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
    if ($result.status -eq "PUBLIC_RELEASE_READY" -and $result.public_release_ready -eq $true) {
        Write-Host "[READY] 已验证精确签名包、运行时闭包、干净 Windows 证明和全部发布门禁。" -ForegroundColor Green
        Write-Host "        The exact signed bundle, runtime closure, clean-Windows evidence, and every release gate are verified." -ForegroundColor Green
        Write-Host ""
        Write-Host "本命令没有上传、创建标签或执行真实外部动作。" -ForegroundColor Green
        Write-Host "This command did not upload, create a tag, or perform a real external action." -ForegroundColor Green
    }
    else {
        Write-Host "[BLOCKED] 本地 QA 可以运行，但当前不能公开签名或发布；这是安全停止，不是程序故障。" -ForegroundColor Yellow
        Write-Host "          Local QA may run, but public signing and release remain blocked; this is a safe stop, not an application failure." -ForegroundColor Yellow
        Write-Host ""
        foreach ($code in @($result.blockers)) {
            $guidance = $actions[$code]
            Write-Host "- $code" -ForegroundColor Yellow
            if ($null -ne $guidance) {
                Write-Host "  $($guidance[0]) / $($guidance[1])"
            }
        }
        Write-Host ""
        Write-Host "未创建仓库或标签；上传、网络与真实外部动作均为 0。" -ForegroundColor Green
        Write-Host "No repository or tag was created; uploads, network actions, and real external actions are 0." -ForegroundColor Green
    }
}

if ($result.status -eq "PUBLIC_RELEASE_READY" -and $result.public_release_ready -eq $true) {
    exit 0
}
exit 2
