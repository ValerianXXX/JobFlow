[CmdletBinding()]
param(
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "未找到 JobFlow 项目根目录。 / JOBFLOW_PROJECT_ROOT_NOT_FOUND"
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "无法定位当前用户的本机应用目录。 / JOBFLOW_LOCAL_APP_DATA_NOT_FOUND"
}
$localAppDataRoot = (Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path
$localRoot = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot "JobOps"))
$applicationRoot = Join-Path $localRoot "Application"
$versionsRoot = Join-Path $applicationRoot "versions"
$dataRoot = Join-Path $localRoot "Data"
$binRoot = Join-Path $localRoot "bin"
$currentPointerPath = Join-Path $localRoot "current.json"
$previousPointerPath = Join-Path $localRoot "previous.json"
$installId = [Guid]::NewGuid().ToString("N").Substring(0, 12)
# Keep staging paths short enough for stock Windows systems without LongPathsEnabled.
$stagingRoot = Join-Path $localRoot (".i-" + $installId)
$repairBackupRoot = Join-Path $localRoot (".r-" + $installId)
$skipBrowserIntegrationForAcceptance = $env:JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY -eq "1"

if ($skipBrowserIntegrationForAcceptance) {
    $temporaryBoundary = [IO.Path]::GetFullPath($env:TEMP)
    $acceptanceRoot = [IO.Path]::GetDirectoryName($localAppDataRoot)
    $temporaryPrefix = $temporaryBoundary.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $acceptanceRoot.StartsWith($temporaryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        ([IO.Path]::GetFileName($acceptanceRoot)) -notlike "jobflow-fixed-install-qa-*" -or
        ([IO.Path]::GetFileName($localAppDataRoot)) -ne "LocalAppData"
    ) {
        throw "JOBFLOW_INSTALL_ACCEPTANCE_BYPASS_FORBIDDEN"
    }
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

function Assert-NoReparse([string]$Path, [string]$Boundary, [string]$Code) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $limit = [IO.Path]::GetFullPath($Boundary)
    $prefix = $limit.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -ne $limit -and -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw $Code
    }
    $cursor = $absolute
    while ($cursor -and ($cursor -eq $limit -or $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase))) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw $Code
            }
        }
        if ($cursor -eq $limit) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Assert-JobFlowLocalPath([string]$Path) {
    Assert-NoReparse $localAppDataRoot $localAppDataRoot "JOBFLOW_LOCAL_APP_DATA_REPARSE_FORBIDDEN"
    Assert-NoReparse $Path $localRoot "JOBFLOW_INSTALL_PATH_FORBIDDEN_OR_LINKED"
}

function Assert-SourcePath([string]$Path) {
    Assert-NoReparse $Path $projectRoot "JOBFLOW_INSTALL_SOURCE_LINK_FORBIDDEN"
}

function Write-JsonAtomic([string]$Path, [object]$Value) {
    Assert-JobFlowLocalPath $Path
    $temporary = Join-Path $localRoot (([IO.Path]::GetFileName($Path)) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    $backup = Join-Path $localRoot (([IO.Path]::GetFileName($Path)) + "." + [Guid]::NewGuid().ToString("N") + ".backup")
    Assert-JobFlowLocalPath $temporary
    Assert-JobFlowLocalPath $backup
    $json = $Value | ConvertTo-Json -Depth 6 -Compress
    try {
        [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporary, $Path, $backup, $true)
        }
        else {
            Move-Item -LiteralPath $temporary -Destination $Path
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Remove-Item -LiteralPath $backup -Force
        }
    }
}

function Read-InstalledPointer([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    Assert-JobFlowLocalPath $Path
    try { $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
    catch { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
    $directory = [string]$value.version_directory
    if (
        $value.schema_version -ne 1 -or
        $directory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' -or
        [string]::IsNullOrWhiteSpace([string]$value.version) -or
        ([string]$value.source_sha256) -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "JOBFLOW_INSTALLED_POINTER_INVALID"
    }
    $target = [IO.Path]::GetFullPath((Join-Path $versionsRoot $directory))
    Assert-JobFlowLocalPath $target
    if (-not (Test-Path -LiteralPath $target -PathType Container)) {
        throw "JOBFLOW_INSTALLED_POINTER_TARGET_MISSING"
    }
    return $value
}

function Test-VersionHealth([string]$VersionRoot) {
    Assert-JobFlowLocalPath $VersionRoot
    $pythonPath = Join-Path $VersionRoot ".venv\Scripts\python.exe"
    $healthPath = Join-Path $VersionRoot "scripts\check-jobflow.ps1"
    if (
        -not (Test-Path -LiteralPath (Join-Path $VersionRoot ".jobops-root") -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $healthPath -PathType Leaf)
    ) { return $false }
    $savedDataRoot = $env:JOBFLOW_DATA_ROOT
    try {
        $env:JOBFLOW_DATA_ROOT = $dataRoot
        $null = & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $healthPath -Json -PythonPath $pythonPath 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch { return $false }
    finally {
        if ($null -eq $savedDataRoot) { Remove-Item Env:JOBFLOW_DATA_ROOT -ErrorAction SilentlyContinue }
        else { $env:JOBFLOW_DATA_ROOT = $savedDataRoot }
    }
}

function Set-CurrentUserOnly([string]$Path) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $grant = "*$($identity.User.Value):(OI)(CI)F"
    & "$env:SystemRoot\System32\icacls.exe" $Path "/inheritance:r" "/grant:r" $grant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_INSTALL_ACL_FAILED" }
    if (@(Get-ChildItem -LiteralPath $Path -Force).Count -gt 0) {
        & "$env:SystemRoot\System32\icacls.exe" (Join-Path $Path "*") "/reset" "/T" "/C" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_INSTALL_CHILD_ACL_FAILED" }
    }
}

function Install-StableLaunchers {
    $runtimeSource = Join-Path $projectRoot "scripts\windows-runtime"
    $required = @(
        "start-installed-jobflow.ps1",
        "check-installed-jobflow.ps1",
        "update-installed-jobflow.ps1",
        "rollback-installed-jobflow.ps1",
        "uninstall-installed-jobflow.ps1",
        "Start JobFlow.cmd",
        "Check JobFlow.cmd",
        "Update JobFlow.cmd",
        "Rollback JobFlow.cmd",
        "Uninstall JobFlow.cmd"
    )
    foreach ($name in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $runtimeSource $name) -PathType Leaf)) {
            throw "JOBFLOW_STABLE_LAUNCHER_MISSING"
        }
    }
    New-Item -ItemType Directory -Path $binRoot -Force | Out-Null
    foreach ($name in $required | Where-Object { $_.EndsWith(".ps1", [StringComparison]::OrdinalIgnoreCase) }) {
        Copy-Item -LiteralPath (Join-Path $runtimeSource $name) -Destination (Join-Path $binRoot $name) -Force
    }
    foreach ($name in $required | Where-Object { $_.EndsWith(".cmd", [StringComparison]::OrdinalIgnoreCase) }) {
        Copy-Item -LiteralPath (Join-Path $runtimeSource $name) -Destination (Join-Path $localRoot $name) -Force
    }

    if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
        $programsRoot = [IO.Path]::GetFullPath((Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))
        $menuRoot = [IO.Path]::GetFullPath((Join-Path $programsRoot "JobFlow"))
        $programsPrefix = $programsRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if (-not $menuRoot.StartsWith($programsPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "JOBFLOW_START_MENU_PATH_FORBIDDEN"
        }
        New-Item -ItemType Directory -Path $menuRoot -Force | Out-Null
        $shell = New-Object -ComObject WScript.Shell
        foreach ($entry in @(
            @{ Name = "JobFlow.lnk"; Target = "Start JobFlow.cmd" },
            @{ Name = "Check JobFlow.lnk"; Target = "Check JobFlow.cmd" },
            @{ Name = "Update JobFlow.lnk"; Target = "Update JobFlow.cmd" },
            @{ Name = "Roll Back JobFlow.lnk"; Target = "Rollback JobFlow.cmd" },
            @{ Name = "Uninstall JobFlow.lnk"; Target = "Uninstall JobFlow.cmd" }
        )) {
            $shortcut = $shell.CreateShortcut((Join-Path $menuRoot $entry.Name))
            $shortcut.TargetPath = Join-Path $localRoot $entry.Target
            $shortcut.WorkingDirectory = $localRoot
            $shortcut.Save()
        }
    }
}

try {
    $python = Find-SupportedPython
    if ($null -eq $python) {
        throw "需要 Python 3.11 或更高版本。请安装 Windows 版 Python 后重试。 / Python 3.11 or newer is required."
    }
    $pythonCommand = [string]$python.Command
    $pythonPrefix = @($python.Prefix)

    $pyprojectText = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
    if ($pyprojectText -notmatch '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)"\s*$') {
        throw "JOBFLOW_INSTALL_VERSION_INVALID"
    }
    $version = [string]$Matches[1]

    $rootFiles = @(
        ".jobops-root", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE",
        "Install JobFlow Browser Companion.cmd", "MANIFEST.in", "README.md", "SECURITY.md", "Update JobFlow.cmd", "pyproject.toml"
    )
    $sourceDirectories = @(".agents", "browser-companion", "config", "docs", "schemas", "scripts", "src", "tests")
    $installFiles = [System.Collections.Generic.List[object]]::new()
    foreach ($name in $rootFiles) {
        $path = Join-Path $projectRoot $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Assert-SourcePath $path
            $installFiles.Add([pscustomobject]@{ Relative = $name; Source = $path })
        }
    }
    foreach ($directoryName in $sourceDirectories) {
        $directory = Join-Path $projectRoot $directoryName
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            throw "JOBFLOW_INSTALL_SOURCE_INCOMPLETE"
        }
        Assert-SourcePath $directory
        foreach ($file in Get-ChildItem -LiteralPath $directory -File -Recurse -Force) {
            Assert-SourcePath $file.FullName
            $relative = $file.FullName.Substring($projectRoot.Length).TrimStart('\', '/').Replace('\', '/')
            $lower = $relative.ToLowerInvariant()
            if (
                $lower -eq "browser-companion/binding.json" -or
                $lower -eq "browser-companion-binding.json" -or
                $lower -match '(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.tmp|\.git)(/|$)' -or
                $lower -match '\.(pyc|pyo|db|sqlite|sqlite3|dpapi|zip|7z|rar|log)$'
            ) { continue }
            $installFiles.Add([pscustomobject]@{ Relative = $relative; Source = $file.FullName })
        }
    }
    $rootMarkerRecords = @($installFiles | Where-Object { $_.Relative -eq ".jobops-root" })
    if ($rootMarkerRecords.Count -ne 1) {
        throw "JOBFLOW_INSTALL_SOURCE_INCOMPLETE"
    }
    $duplicates = $installFiles | Group-Object { $_.Relative.ToLowerInvariant() } | Where-Object { $_.Count -ne 1 }
    if ($duplicates) { throw "JOBFLOW_INSTALL_SOURCE_DUPLICATE" }
    $installFiles = @($installFiles | Sort-Object -Property Relative)

    $manifestBuilder = New-Object Text.StringBuilder
    foreach ($record in $installFiles) {
        $item = Get-Item -LiteralPath $record.Source -Force
        $hash = (Get-FileHash -LiteralPath $record.Source -Algorithm SHA256).Hash.ToLowerInvariant()
        [void]$manifestBuilder.Append($record.Relative).Append('|').Append($item.Length).Append('|').Append($hash).Append("`n")
    }
    $manifestBytes = [Text.Encoding]::UTF8.GetBytes($manifestBuilder.ToString())
    $sourceHasher = [Security.Cryptography.SHA256]::Create()
    try { $sourceHash = -join ($sourceHasher.ComputeHash($manifestBytes) | ForEach-Object { $_.ToString("x2") }) }
    finally { $sourceHasher.Dispose() }
    $versionDirectory = "v$version-$($sourceHash.Substring(0, 12))"
    $targetVersionRoot = Join-Path $versionsRoot $versionDirectory

    foreach ($path in @(
        $localRoot, $applicationRoot, $versionsRoot, $dataRoot, $binRoot,
        $currentPointerPath, $previousPointerPath, $stagingRoot, $repairBackupRoot, $targetVersionRoot
    )) { Assert-JobFlowLocalPath $path }

    New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $applicationRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $versionsRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $dataRoot | Out-Null
    }
    $dataMarkerPath = Join-Path $dataRoot ".jobflow-data-root"
    if (Test-Path -LiteralPath $dataMarkerPath -PathType Leaf) {
        try { $dataMarker = Get-Content -LiteralPath $dataMarkerPath -Raw | ConvertFrom-Json }
        catch { throw "JOBFLOW_RUNTIME_DATA_MARKER_INVALID" }
        if ($dataMarker.schema_version -ne 1 -or $dataMarker.kind -ne "JOBFLOW_RUNTIME_DATA") {
            throw "JOBFLOW_RUNTIME_DATA_MARKER_INVALID"
        }
    }
    else {
        [IO.File]::WriteAllText($dataMarkerPath, '{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}', (New-Object Text.UTF8Encoding($false)))
    }
    foreach ($area in @("state", "workspace", "reports")) {
        New-Item -ItemType Directory -Path (Join-Path $dataRoot $area) -Force | Out-Null
    }
    Set-CurrentUserOnly $dataRoot

    $existingPointer = Read-InstalledPointer $currentPointerPath
    $versionWasRepaired = $false
    $targetAlreadyHealthy = (Test-Path -LiteralPath $targetVersionRoot -PathType Container) -and (Test-VersionHealth $targetVersionRoot)
    if (-not $targetAlreadyHealthy) {
        Write-Host "正在准备固定版本目录…… / Preparing the fixed application version..."
        if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
            Remove-Item -LiteralPath $stagingRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
        try {
            foreach ($record in $installFiles) {
                $destination = Join-Path $stagingRoot $record.Relative
                Assert-JobFlowLocalPath $destination
                $parent = [IO.Path]::GetDirectoryName($destination)
                if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
                    New-Item -ItemType Directory -Path $parent -Force | Out-Null
                }
                Copy-Item -LiteralPath $record.Source -Destination $destination -Force
            }

            $stagedPython = Join-Path $stagingRoot ".venv\Scripts\python.exe"
            Write-Host "正在创建隔离运行环境…… / Creating the isolated runtime..."
            & $pythonCommand @pythonPrefix -m venv (Join-Path $stagingRoot ".venv")
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $stagedPython -PathType Leaf)) {
                throw "JOBFLOW_INSTALL_VENV_FAILED"
            }
            Push-Location $stagingRoot
            try {
                Write-Host "正在准备经过测试的构建工具…… / Preparing tested build tools..."
                & $stagedPython -m pip install --quiet --disable-pip-version-check --no-input "setuptools>=77,<81" "wheel>=0.43,<1"
                if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_INSTALL_BUILD_TOOLS_FAILED" }
                Write-Host "正在安装 JobFlow…… / Installing JobFlow..."
                & $stagedPython -m pip install --quiet --disable-pip-version-check --no-input --no-build-isolation ".[build]"
                if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_INSTALL_DEPENDENCIES_FAILED" }
                & $stagedPython -m pip check
                if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_INSTALL_DEPENDENCY_CHECK_FAILED" }
            }
            finally { Pop-Location }

            if (-not (Test-VersionHealth $stagingRoot)) {
                throw "JOBFLOW_INSTALL_HEALTH_CHECK_FAILED"
            }
            if (Test-Path -LiteralPath $targetVersionRoot -PathType Container) {
                Move-Item -LiteralPath $targetVersionRoot -Destination $repairBackupRoot
                $versionWasRepaired = $true
            }
            Move-Item -LiteralPath $stagingRoot -Destination $targetVersionRoot
        }
        catch {
            if (-not (Test-Path -LiteralPath $targetVersionRoot -PathType Container) -and (Test-Path -LiteralPath $repairBackupRoot -PathType Container)) {
                Move-Item -LiteralPath $repairBackupRoot -Destination $targetVersionRoot
            }
            throw
        }
    }

    if (-not (Test-VersionHealth $targetVersionRoot)) {
        throw "JOBFLOW_INSTALL_FINAL_HEALTH_CHECK_FAILED"
    }

    if ($skipBrowserIntegrationForAcceptance) {
        Write-Host "隔离验收仅验证核心安装；浏览器注册未触碰。 / Isolated acceptance verifies the core install without touching browser registration."
    }
    else {
        Write-Host "正在安装安全浏览器通道…… / Installing the secure browser channel..."
        $companionInstaller = Join-Path $targetVersionRoot "scripts\install-jobflow-browser-companion.ps1"
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $companionInstaller -NoLaunch
        if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_BROWSER_COMPANION_INSTALL_FAILED" }
    }

    Install-StableLaunchers
    $newPointer = [ordered]@{
        schema_version = 1
        version_directory = $versionDirectory
        version = $version
        source_sha256 = $sourceHash
    }
    if ($null -ne $existingPointer -and [string]$existingPointer.version_directory -ne $versionDirectory) {
        Write-JsonAtomic $previousPointerPath $existingPointer
    }
    elseif ($null -eq $existingPointer -and (Test-Path -LiteralPath $previousPointerPath -PathType Leaf)) {
        Remove-Item -LiteralPath $previousPointerPath -Force
    }
    Write-JsonAtomic $currentPointerPath $newPointer

    if (-not (Test-VersionHealth $targetVersionRoot)) {
        if ($null -ne $existingPointer) { Write-JsonAtomic $currentPointerPath $existingPointer }
        throw "JOBFLOW_INSTALL_POST_SWITCH_HEALTH_CHECK_FAILED"
    }
    if ($versionWasRepaired -and (Test-Path -LiteralPath $repairBackupRoot -PathType Container)) {
        Remove-Item -LiteralPath $repairBackupRoot -Recurse -Force
    }

    Write-Host "JobFlow $version 已安装到当前用户的固定目录（Python $($python.Version)）。 / JobFlow $version is installed in the current user's fixed app directory (Python $($python.Version))."
    Write-Host "个人资料、队列和报告保存在独立数据目录；更新或回滚不会覆盖它们。 / Profile data, queues, and reports are stored separately and survive updates or rollback."
    Write-Host "可从 Windows 开始菜单打开 JobFlow、检查签名更新、运行自检、回滚或卸载。 / Open, check signed updates, run diagnostics, roll back, or uninstall JobFlow from the Windows Start menu."

    if (-not $NoLaunch) {
        $storeConfig = Get-Content -LiteralPath (Join-Path $targetVersionRoot "config\browser-companion-stores.json") -Raw | ConvertFrom-Json
        $storeUrl = if (-not [string]::IsNullOrWhiteSpace([string]$storeConfig.edge_addons_url)) {
            [string]$storeConfig.edge_addons_url
        }
        else { [string]$storeConfig.chrome_web_store_url }
        if ($storeUrl.StartsWith("https://", [StringComparison]::OrdinalIgnoreCase)) {
            Start-Process $storeUrl
        }
    }
}
finally {
    foreach ($path in @($stagingRoot, $repairBackupRoot)) {
        if (Test-Path -LiteralPath $path -PathType Container) {
            Assert-JobFlowLocalPath $path
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}
