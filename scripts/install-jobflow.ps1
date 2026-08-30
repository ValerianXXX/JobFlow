[CmdletBinding()]
param(
    [switch]$NoLaunch,
    [string]$TrustedUpdatePayloadManifest = "",
    [string]$TrustedUpdatePayloadManifestSha256 = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    $nativeArchitecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture
}
catch {
    throw "无法验证 Windows 处理器架构。 / JOBFLOW_WINDOWS_ARCHITECTURE_UNVERIFIED"
}
if (
    -not [Environment]::Is64BitOperatingSystem -or
    $nativeArchitecture -ne [Runtime.InteropServices.Architecture]::X64
) {
    throw "JobFlow 当前只提供 AMD64/x64 Windows 依赖锁；ARM64 或其他架构尚不受支持。 / JOBFLOW_WINDOWS_AMD64_REQUIRED"
}
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
$rollbackPointerTransactionPath = Join-Path $localRoot ".rollback-pointer-transaction.json"
$rollbackPointerTransactionBackupPath = Join-Path $localRoot ".rollback-pointer-transaction.backup.json"
$installId = [Guid]::NewGuid().ToString("N").Substring(0, 12)
# Keep staging paths short enough for stock Windows systems without LongPathsEnabled.
$stagingRoot = Join-Path $localRoot (".i-" + $installId)
$buildRoot = Join-Path $localRoot (".b-" + $installId)
$repairBackupRoot = Join-Path $localRoot (".r-" + $installId)
$launcherRollbackRoot = Join-Path $localRoot (".l-" + $installId)
$skipBrowserIntegrationForAcceptance = $env:JOBFLOW_INSTALL_ACCEPTANCE_CORE_ONLY -eq "1"
$runtimeLockStream = $null
$discoveryLockStream = $null
$pythonExecutableLock = $null
$powerShellExecutableLock = $null
$icaclsExecutableLock = $null
$trustedUpdatePayloadManifestLock = $null
$stagingDirectoryContext = $null
$buildDirectoryContext = $null
$trustedUpdateSourceLocks = [System.Collections.Generic.List[IO.FileStream]]::new()
$trustedUpdateContext = $null
$targetVersionRoot = $null
$targetExistedBefore = $false
$versionWasRepaired = $false
$activationCommitted = $false
$preserveTargetOnFailure = $false
$stableLauncherSnapshot = $null
$launchersMutated = $false
$stableLauncherFiles = @(
    "jobflow-bootstrap.ps1",
    "start-installed-jobflow.ps1",
    "check-installed-jobflow.ps1",
    "update-installed-jobflow.ps1",
    "rollback-installed-jobflow.ps1",
    "uninstall-installed-jobflow.ps1",
    "jobflow-runtime-locks.ps1",
    "manage-authorized-discovery-task.ps1",
    "run-authorized-discovery-task.ps1",
    "Start JobFlow.cmd",
    "Check JobFlow.cmd",
    "Update JobFlow.cmd",
    "Rollback JobFlow.cmd",
    "Uninstall JobFlow.cmd"
)
$stableShortcutEntries = @(
    @{ Name = "JobFlow.lnk"; Target = "Start JobFlow.cmd" },
    @{ Name = "Check JobFlow.lnk"; Target = "Check JobFlow.cmd" },
    @{ Name = "Update JobFlow.lnk"; Target = "Update JobFlow.cmd" },
    @{ Name = "Roll Back JobFlow.lnk"; Target = "Rollback JobFlow.cmd" },
    @{ Name = "Uninstall JobFlow.lnk"; Target = "Uninstall JobFlow.cmd" }
)
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

function Test-TrustedExecutableSignature(
    [string]$Path,
    [string]$Organization,
    [string]$Code
) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Code }
    $securityModule = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
    if (-not (Test-Path -LiteralPath $securityModule -PathType Leaf)) { throw $Code }
    Microsoft.PowerShell.Core\Import-Module -Name $securityModule -Force -ErrorAction Stop
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $Path
    if (
        [string]$signature.Status -cne "Valid" -or
        $null -eq $signature.SignerCertificate -or
        [string]$signature.SignerCertificate.Subject -notmatch (
            '(^|,\s*)O=' + [regex]::Escape($Organization) + '(,|$)'
        )
    ) { throw $Code }
}

function Get-TrustedWindowsPowerShell {
    $systemDirectory = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
    $candidate = [IO.Path]::GetFullPath((Join-Path $systemDirectory "WindowsPowerShell\v1.0\powershell.exe"))
    $prefix = $systemDirectory.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
    }
    Assert-ExistingAncestorChainNoReparse $candidate "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
    $lock = [IO.File]::Open($candidate, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        # Protected Windows binaries can legitimately have a second WinSxS
        # hardlink.  Trust here is established by the fixed System32 path, the
        # held handle/final-path check, the non-reparse ancestor chain, ADS
        # rejection, and Microsoft's Authenticode signature.  Requiring one
        # link rejects an unmodified stock Windows PowerShell installation.
        if ((Get-OpenInstallerFileLinkCount $lock "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED") -lt 1) {
            throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
        }
        if (-not (Get-OpenInstallerFinalPath $lock "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED").Equals(
            $candidate, [StringComparison]::OrdinalIgnoreCase
        )) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
        Assert-NoInstallerAlternateDataStreams $candidate "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
        Test-TrustedExecutableSignature $candidate "Microsoft Corporation" "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
        return @{ Path = $candidate; Lock = $lock }
    }
    catch {
        $lock.Dispose()
        throw
    }
}

function Get-CanonicalPythonCandidates {
    $installationDirectories = [System.Collections.Generic.List[string]]::new()
    $userPythonRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Programs\Python"
    $programRoots = @(
        [Environment]::GetFolderPath("ProgramFiles"),
        [Environment]::GetFolderPath("ProgramFilesX86")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    if (Test-Path -LiteralPath $userPythonRoot -PathType Container) {
        foreach ($directory in @(Get-ChildItem -LiteralPath $userPythonRoot -Directory -Force)) {
            if ($directory.Name -match '^Python3[0-9._-]*$') { $installationDirectories.Add($directory.FullName) }
        }
    }
    foreach ($programRoot in $programRoots) {
        foreach ($directory in @(Get-ChildItem -LiteralPath $programRoot -Directory -Filter "Python3*" -Force -ErrorAction SilentlyContinue)) {
            if ($directory.Name -match '^Python3[0-9._-]*$') { $installationDirectories.Add($directory.FullName) }
        }
        $foundationRoot = Join-Path $programRoot "Python Software Foundation"
        if (Test-Path -LiteralPath $foundationRoot -PathType Container) {
            foreach ($directory in @(Get-ChildItem -LiteralPath $foundationRoot -Directory -Force)) {
                if ($directory.Name -match '^Python3[0-9._-]*$') { $installationDirectories.Add($directory.FullName) }
            }
        }
    }

    $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($directory in @($installationDirectories | Sort-Object -Descending)) {
        $candidate = [IO.Path]::GetFullPath((Join-Path $directory "python.exe"))
        if ($seen.Add($candidate)) { $candidate }
    }
}

function Find-SupportedPython {
    foreach ($candidate in @(Get-CanonicalPythonCandidates)) {
        $lock = $null
        try {
            Assert-ExistingAncestorChainNoReparse $candidate "JOBFLOW_TRUSTED_PYTHON_REQUIRED"
            $lock = [IO.File]::Open($candidate, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
            if ((Get-OpenInstallerFileLinkCount $lock "JOBFLOW_TRUSTED_PYTHON_REQUIRED") -ne 1) { continue }
            if (-not (Get-OpenInstallerFinalPath $lock "JOBFLOW_TRUSTED_PYTHON_REQUIRED").Equals(
                $candidate, [StringComparison]::OrdinalIgnoreCase
            )) { continue }
            Assert-NoInstallerAlternateDataStreams $candidate "JOBFLOW_TRUSTED_PYTHON_REQUIRED"
            Test-TrustedExecutableSignature $candidate "Python Software Foundation" "JOBFLOW_TRUSTED_PYTHON_REQUIRED"
            $versionLines = @(& $candidate -I -P -S -B -X utf8 -c "import platform,struct,sys; print(f'{platform.python_implementation()}|{sys.version_info.major}.{sys.version_info.minor}|{struct.calcsize(chr(80))*8}|{sys.platform}')" 2>$null)
            if ($LASTEXITCODE -ne 0 -or $versionLines.Count -ne 1) { continue }
            $versionText = ([string]$versionLines[0]).Trim()
            if ($versionText -match '^CPython\|(3\.(11|12))\|64\|win32$') {
                $result = @{ Command = $candidate; Version = [string]$Matches[1]; Minor = [string]$Matches[2]; Lock = $lock }
                $lock = $null
                return $result
            }
        }
        catch { }
        finally { if ($null -ne $lock) { $lock.Dispose() } }
    }
    return $null
}

function ConvertTo-WindowsProcessArgument([string]$Value, [string]$FailureCode) {
    if ($null -eq $Value -or $Value.IndexOf([char]0) -ge 0 -or $Value -match '[\r\n]') { throw $FailureCode }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('"')
    [int]$backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
            continue
        }
        if ($character -eq [char]34) {
            if ($backslashes -gt 0) { [void]$builder.Append([string]::new([char]92, ($backslashes * 2))) }
            [void]$builder.Append('\"')
        }
        else {
            if ($backslashes -gt 0) { [void]$builder.Append([string]::new([char]92, $backslashes)) }
            [void]$builder.Append($character)
        }
        $backslashes = 0
    }
    if ($backslashes -gt 0) { [void]$builder.Append([string]::new([char]92, ($backslashes * 2))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-IsolatedInstallerPython(
    [string]$PythonPath,
    [string[]]$Arguments,
    [string]$FailureCode
) {
    $absolutePython = [IO.Path]::GetFullPath($PythonPath)
    if (-not $absolutePython.Equals($PythonPath, [StringComparison]::OrdinalIgnoreCase)) { throw $FailureCode }
    $workingDirectory = [IO.Path]::GetFullPath((Get-Location).Path)
    if (-not (Test-Path -LiteralPath $workingDirectory -PathType Container)) { throw $FailureCode }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $absolutePython
    $start.WorkingDirectory = $workingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    $start.EnvironmentVariables.Clear()
    foreach ($name in @(
        "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "SystemRoot", "WINDIR",
        "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemDrive", "ComSpec"
    )) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) { $start.EnvironmentVariables[$name] = $value }
    }
    $start.EnvironmentVariables["PIP_CONFIG_FILE"] = "NUL"
    $start.EnvironmentVariables["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    $start.EnvironmentVariables["PIP_NO_INPUT"] = "1"
    $start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
    $start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1"
    $quoted = @($Arguments | ForEach-Object { ConvertTo-WindowsProcessArgument ([string]$_) $FailureCode })
    $start.Arguments = [string]::Join(" ", $quoted)
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw $FailureCode }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { throw $FailureCode }
    }
    finally { $process.Dispose() }
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

function Assert-ExistingAncestorChainNoReparse([string]$Path, [string]$Code) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw $Code
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Assert-JobFlowLocalPath([string]$Path) {
    Assert-ExistingAncestorChainNoReparse $localAppDataRoot "JOBFLOW_LOCAL_APP_DATA_REPARSE_FORBIDDEN"
    Assert-NoReparse $localAppDataRoot $localAppDataRoot "JOBFLOW_LOCAL_APP_DATA_REPARSE_FORBIDDEN"
    Assert-NoReparse $Path $localRoot "JOBFLOW_INSTALL_PATH_FORBIDDEN_OR_LINKED"
}

function Assert-LocalTreeNoReparse([string]$Root, [string]$Code) {
    Assert-JobFlowLocalPath $Root
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        Assert-JobFlowLocalPath $directory
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw $Code
            }
            Assert-JobFlowLocalPath $item.FullName
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
            else { Assert-SingleLinkInstallerLeaf $item.FullName $Code -MustExist }
        }
    }
}

function Assert-SourcePath([string]$Path) {
    Assert-NoReparse $Path $projectRoot "JOBFLOW_INSTALL_SOURCE_LINK_FORBIDDEN"
}

function Get-FileSha256([string]$Path) {
    # Do not depend on an optional PowerShell hashing cmdlet: Windows
    # PowerShell can lose inbox-module commands when a PowerShell 7 module
    # path shadows them. Keep the
    # stream and hasher lifetime explicit so every success and failure path
    # releases the same file handle deterministically.
    $stream = $null
    $hasher = $null
    try {
        $stream = [IO.FileStream]::new(
            $Path,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $hasher = [Security.Cryptography.SHA256]::Create()
        $bytes = $hasher.ComputeHash($stream)
        return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        if ($null -ne $hasher) { $hasher.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Assert-SourceRecord([object]$Record) {
    Assert-SourcePath ([string]$Record.Source)
    if (-not (Test-Path -LiteralPath $Record.Source -PathType Leaf)) {
        throw "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL"
    }
    $item = Get-Item -LiteralPath $Record.Source -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [long]$item.Length -ne [long]$Record.Length
    ) {
        throw "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL"
    }
    $hash = Get-FileSha256 ([string]$Record.Source)
    if ($hash -ne [string]$Record.Sha256) {
        throw "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL"
    }
}

function Get-SafeStagedFiles([string]$Root) {
    Assert-JobFlowLocalPath $Root
    $result = [System.Collections.Generic.List[object]]::new()
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        Assert-JobFlowLocalPath $directory
        foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH"
            }
            if ($item.PSIsContainer) {
                $relativeDirectory = $item.FullName.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
                if ($relativeDirectory -eq ".venv") { continue }
                $pending.Push($item.FullName)
            }
            else {
                $result.Add($item)
            }
        }
    }
    return @($result)
}

function Assert-StagedSourceSnapshot([string]$Root, [object[]]$Records) {
    Assert-JobFlowLocalPath $Root
    $expected = @{}
    foreach ($record in @($Records)) {
        $key = ([string]$record.Relative).Replace('\', '/').ToLowerInvariant()
        if ($expected.ContainsKey($key)) { throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH" }
        $expected[$key] = $record
    }
    $actual = @{}
    foreach ($file in Get-SafeStagedFiles $Root) {
        $relative = $file.FullName.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
        $key = $relative.ToLowerInvariant()
        if ($key -eq ".venv" -or $key.StartsWith(".venv/", [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        if ($actual.ContainsKey($key)) { throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH" }
        $actual[$key] = $file
    }
    if ($actual.Count -ne $expected.Count) { throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH" }
    foreach ($key in $expected.Keys) {
        if (-not $actual.ContainsKey($key)) { throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH" }
        $record = $expected[$key]
        $file = $actual[$key]
        Assert-JobFlowLocalPath $file.FullName
        Assert-SingleLinkInstallerLeaf $file.FullName "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH" -MustExist
        Assert-NoInstallerAlternateDataStreams $file.FullName "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH"
        if ([long]$file.Length -ne [long]$record.Length) {
            throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH"
        }
        $hash = Get-FileSha256 ([string]$file.FullName)
        if ($hash -ne [string]$record.Sha256) {
            throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH"
        }
    }
}

function Assert-StagedSourceRecord([string]$Root, [string]$Relative, [object[]]$Records) {
    Assert-JobFlowLocalPath $Root
    $normalized = $Relative.Replace('\', '/').ToLowerInvariant()
    $matches = @($Records | Where-Object {
        ([string]$_.Relative).Replace('\', '/').ToLowerInvariant() -eq $normalized
    })
    if ($matches.Count -ne 1) { throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH" }
    $record = $matches[0]
    $path = Join-Path $Root ([string]$record.Relative)
    Assert-JobFlowLocalPath $path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH"
    }
    $item = Get-Item -LiteralPath $path -Force
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [long]$item.Length -ne [long]$record.Length -or
        (Get-FileSha256 $path) -ne [string]$record.Sha256
    ) {
        throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH"
    }
    return $path
}

function Get-OpenInstallerStreamSha256([IO.FileStream]$Stream) {
    $originalPosition = $Stream.Position
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Stream.Position = 0
        $bytes = $hasher.ComputeHash($Stream)
        return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $Stream.Position = $originalPosition
        $hasher.Dispose()
    }
}

function ConvertTo-JobFlowCanonicalJsonString([string]$Value) {
    $json = ConvertTo-Json -InputObject $Value -Compress
    return $json.Replace('\u0026', '&').Replace('\u0027', "'").Replace('\u003c', '<').Replace('\u003e', '>')
}

function Get-TrustedPayloadInventorySha256([object[]]$Directories, [object[]]$Records) {
    $directoryJson = @($Directories | ForEach-Object {
        ConvertTo-JobFlowCanonicalJsonString ([string]$_)
    })
    $recordJson = @($Records | ForEach-Object {
        $length = [long]$_.length
        $relative = ConvertTo-JobFlowCanonicalJsonString ([string]$_.relative)
        $sha256 = ConvertTo-JobFlowCanonicalJsonString ([string]$_.sha256)
        "{`"length`":$length,`"relative`":$relative,`"sha256`":$sha256}"
    })
    $canonical = "{`"directories`":[" + [string]::Join(",", $directoryJson) +
        "],`"records`":[" + [string]::Join(",", $recordJson) + "]}"
    $bytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($canonical)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally { $hasher.Dispose() }
}

function Assert-NoInstallerAlternateDataStreams([string]$Path, [string]$Code) {
    try { $streams = @(Get-Item -LiteralPath $Path -Stream * -ErrorAction Stop) }
    catch { throw $Code }
    if (
        $streams.Count -ne 1 -or
        @(':$DATA', '::$DATA') -notcontains [string]$streams[0].Stream
    ) { throw $Code }
}

function Initialize-SafeInstallerParentDirectory(
    [string]$DestinationRoot,
    [string]$Destination,
    [string]$Code
) {
    $absoluteRoot = [IO.Path]::GetFullPath($DestinationRoot)
    $absoluteDestination = [IO.Path]::GetFullPath($Destination)
    $rootPrefix = $absoluteRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absoluteDestination.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw $Code }
    Assert-JobFlowLocalPath $absoluteRoot
    if (-not (Test-Path -LiteralPath $absoluteRoot -PathType Container)) { throw $Code }
    $parent = [IO.Path]::GetDirectoryName($absoluteDestination)
    $relativeParent = $parent.Substring($absoluteRoot.Length).TrimStart('\', '/')
    if (-not [string]::IsNullOrWhiteSpace($relativeParent)) {
        foreach ($component in $relativeParent.Split([IO.Path]::DirectorySeparatorChar)) {
            if ([string]::IsNullOrWhiteSpace($component) -or $component -eq '.' -or $component -eq '..') { throw $Code }
        }
    }
    Assert-JobFlowLocalPath $parent
    Assert-JobFlowLocalPath $absoluteDestination
    if (Test-Path -LiteralPath $absoluteDestination) { throw $Code }
    $context = Open-StableInstallerDirectoryChain $parent $Code -CreateMissing
    try {
        Assert-StableInstallerDirectoryContext $context $Code
        $parentItem = Get-Item -LiteralPath $parent -Force
        if (($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
        if ([IO.File]::Exists($absoluteDestination) -or [IO.Directory]::Exists($absoluteDestination)) { throw $Code }
        return $context
    }
    catch {
        Close-StableInstallerDirectoryContext $context
        throw
    }
}

function Copy-VerifiedSourceSnapshot([string]$DestinationRoot, [object[]]$Records) {
    Assert-JobFlowLocalPath $DestinationRoot
    if (Test-Path -LiteralPath $DestinationRoot) {
        throw "JOBFLOW_INSTALL_STAGING_COLLISION"
    }
    $rootContext = New-StableInstallerDirectoryRoot $DestinationRoot "JOBFLOW_INSTALL_STAGING_COLLISION"
    try {
    foreach ($record in @($Records)) {
        $destination = Join-Path $DestinationRoot ([string]$record.Relative)
        $parentContext = Initialize-SafeInstallerParentDirectory $DestinationRoot $destination "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE"
        $sourceStream = $null
        $ownsSourceStream = $false
        $output = $null
        try {
            $lockedStreamProperty = $record.PSObject.Properties["LockedStream"]
            if ($null -ne $lockedStreamProperty -and $lockedStreamProperty.Value -is [IO.FileStream]) {
                $sourceStream = [IO.FileStream]$lockedStreamProperty.Value
            }
            else {
                Assert-SourceRecord $record
                $sourceStream = [IO.File]::Open(
                    [string]$record.Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
                )
                $ownsSourceStream = $true
            }
            if (
                (Get-OpenInstallerFileLinkCount $sourceStream "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL") -ne 1 -or
                $sourceStream.Length -ne [long]$record.Length -or
                (Get-OpenInstallerStreamSha256 $sourceStream) -cne [string]$record.Sha256
            ) { throw "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL" }

            # Native FILE_CREATE is the handle-relative equivalent of
            # [IO.FileMode]::CreateNew with [IO.FileShare]::None.  It is rooted
            # in the already verified parent handle, so a path rename cannot
            # redirect this write outside the staging identity.
            $output = Open-NewInstallerFileRelative $parentContext $destination "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE"
            Assert-StableInstallerDirectoryContext $parentContext "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE"
            Assert-OpenInstallerFileAtPath $output $destination "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE"
            $sourceStream.Position = 0
            $sourceStream.CopyTo($output)
            $output.Flush($true)
            if (
                (Get-OpenInstallerFileLinkCount $output "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE") -ne 1 -or
                $output.Length -ne [long]$record.Length -or
                (Get-OpenInstallerStreamSha256 $output) -cne [string]$record.Sha256
            ) { throw "JOBFLOW_INSTALL_STAGING_SOURCE_MISMATCH" }
            if ((Get-OpenInstallerStreamSha256 $sourceStream) -cne [string]$record.Sha256) {
                throw "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL"
            }
        }
        finally {
            if ($null -ne $output) { $output.Dispose() }
            if ($ownsSourceStream -and $null -ne $sourceStream) { $sourceStream.Dispose() }
            Close-StableInstallerDirectoryContext $parentContext
        }
        Assert-JobFlowLocalPath $destination
        Assert-SingleLinkInstallerLeaf $destination "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE" -MustExist
        Assert-NoInstallerAlternateDataStreams $destination "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE"
    }
    Assert-StagedSourceSnapshot $DestinationRoot $Records
    Assert-StableInstallerDirectoryContext $rootContext "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE"
    return $rootContext
    }
    catch {
        Close-StableInstallerDirectoryContext $rootContext
        throw
    }
}

function Test-JsonIntegerOne([object]$Value) {
    if ($null -eq $Value) { return $false }
    $valueType = $Value.GetType()
    return (
        ($valueType -eq [int] -or $valueType -eq [long]) -and
        [long]$Value -eq 1
    )
}

function Test-JsonString([object]$Value) {
    return $null -ne $Value -and $Value.GetType() -eq [string]
}

function Test-JsonIntegerInRange([object]$Value, [long]$Minimum, [long]$Maximum) {
    if ($null -eq $Value) { return $false }
    $valueType = $Value.GetType()
    return (
        ($valueType -eq [int] -or $valueType -eq [long]) -and
        [long]$Value -ge $Minimum -and [long]$Value -le $Maximum
    )
}

function Test-ExactJsonProperties([object]$Value, [string[]]$Expected) {
    if ($null -eq $Value -or $Value -is [Array] -or -not ($Value -is [pscustomobject])) { return $false }
    $actualNames = @($Value.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @($Expected | Sort-Object)
    return ($actualNames -join '|') -ceq ($expectedNames -join '|')
}

function Read-OpenInstallerUtf8Text([IO.FileStream]$Stream, [long]$MaximumBytes, [string]$Code) {
    if ($Stream.Length -lt 2 -or $Stream.Length -gt $MaximumBytes) { throw $Code }
    $buffer = New-Object byte[] ([int]$Stream.Length)
    $Stream.Position = 0
    $offset = 0
    while ($offset -lt $buffer.Length) {
        $read = $Stream.Read($buffer, $offset, $buffer.Length - $offset)
        if ($read -le 0) { throw $Code }
        $offset += $read
    }
    $Stream.Position = 0
    try { return (New-Object Text.UTF8Encoding($false, $true)).GetString($buffer) }
    catch { throw $Code }
}

function ConvertTo-StrictJobFlowVersionTuple([string]$Value, [string]$Code) {
    if (-not (Test-JsonString $Value) -or $Value -notmatch '^([0-9]+)\.([0-9]+)\.([0-9]+)$') { throw $Code }
    $parts = [System.Collections.Generic.List[long]]::new()
    foreach ($component in $Value.Split('.')) {
        $parsed = 0L
        if (-not [long]::TryParse($component, [Globalization.NumberStyles]::None, [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) {
            throw $Code
        }
        $parts.Add($parsed)
    }
    return @($parts)
}

function Test-JobFlowVersionStrictlyGreater([string]$Available, [string]$Current) {
    $availableParts = @(ConvertTo-StrictJobFlowVersionTuple $Available "JOBFLOW_TRUSTED_UPDATE_RELEASE_INVALID")
    $currentParts = @(ConvertTo-StrictJobFlowVersionTuple $Current "JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_INVALID")
    for ($index = 0; $index -lt 3; $index++) {
        if ($availableParts[$index] -gt $currentParts[$index]) { return $true }
        if ($availableParts[$index] -lt $currentParts[$index]) { return $false }
    }
    return $false
}

function Initialize-JobFlowInstallerFileIdentityApi {
    if ($null -ne ("JobFlowInstallerNative.FileIdentity" -as [type])) { return }
    Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;
namespace JobFlowInstallerNative {
    [StructLayout(LayoutKind.Sequential)] public struct FileTime { public uint Low; public uint High; }
    [StructLayout(LayoutKind.Sequential)] public struct FileIdentity {
        public uint Attributes; public FileTime CreationTime; public FileTime LastAccessTime;
        public FileTime LastWriteTime; public uint VolumeSerialNumber; public uint SizeHigh;
        public uint SizeLow; public uint LinkCount; public uint FileIndexHigh; public uint FileIndexLow;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct UnicodeString {
        public ushort Length; public ushort MaximumLength; public System.IntPtr Buffer;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct ObjectAttributes {
        public int Length; public System.IntPtr RootDirectory; public System.IntPtr ObjectName;
        public uint Attributes; public System.IntPtr SecurityDescriptor; public System.IntPtr SecurityQualityOfService;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct IoStatusBlock {
        public System.IntPtr Status; public System.IntPtr Information;
    }
    public static class FileIdentityApi {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern SafeFileHandle CreateFileW(
            string path, uint desiredAccess, uint shareMode, System.IntPtr securityAttributes,
            uint creationDisposition, uint flagsAndAttributes, System.IntPtr templateFile
        );
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern bool CreateDirectoryW(string path, System.IntPtr securityAttributes);
        [DllImport("kernel32.dll", SetLastError=true)]
        public static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileIdentity information);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern uint GetFinalPathNameByHandle(
            SafeFileHandle handle, StringBuilder path, uint capacity, uint flags
        );
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern bool MoveFileEx(string source, string destination, uint flags);
        [DllImport("ntdll.dll")]
        private static extern int NtCreateFile(
            out System.IntPtr fileHandle, uint desiredAccess, ref ObjectAttributes objectAttributes,
            out IoStatusBlock ioStatusBlock, System.IntPtr allocationSize, uint fileAttributes,
            uint shareAccess, uint createDisposition, uint createOptions,
            System.IntPtr eaBuffer, uint eaLength
        );
        private static SafeFileHandle CreateNewRelative(
            SafeFileHandle parent, string name, uint desiredAccess, uint shareAccess,
            uint fileAttributes, uint createOptions
        ) {
            if (parent == null || parent.IsInvalid || parent.IsClosed || string.IsNullOrEmpty(name) ||
                name.IndexOfAny(new char[] {'\\', '/', ':'}) >= 0) {
                return new SafeFileHandle(System.IntPtr.Zero, true);
            }
            System.IntPtr buffer = System.IntPtr.Zero;
            System.IntPtr unicodePointer = System.IntPtr.Zero;
            bool addedRef = false;
            try {
                byte[] nameBytes = Encoding.Unicode.GetBytes(name);
                if (nameBytes.Length < 2 || nameBytes.Length > 32766) {
                    return new SafeFileHandle(System.IntPtr.Zero, true);
                }
                buffer = Marshal.StringToHGlobalUni(name);
                UnicodeString unicode = new UnicodeString {
                    Length = (ushort)nameBytes.Length,
                    MaximumLength = (ushort)(nameBytes.Length + 2),
                    Buffer = buffer
                };
                unicodePointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UnicodeString)));
                Marshal.StructureToPtr(unicode, unicodePointer, false);
                parent.DangerousAddRef(ref addedRef);
                ObjectAttributes attributes = new ObjectAttributes {
                    Length = Marshal.SizeOf(typeof(ObjectAttributes)),
                    RootDirectory = parent.DangerousGetHandle(), ObjectName = unicodePointer,
                    Attributes = 0x40, SecurityDescriptor = System.IntPtr.Zero,
                    SecurityQualityOfService = System.IntPtr.Zero
                };
                IoStatusBlock io;
                System.IntPtr raw;
                int status = NtCreateFile(
                    out raw, desiredAccess, ref attributes, out io, System.IntPtr.Zero,
                    fileAttributes, shareAccess, 2, createOptions, System.IntPtr.Zero, 0
                );
                if (status != 0 || raw == System.IntPtr.Zero || raw == new System.IntPtr(-1)) {
                    return new SafeFileHandle(System.IntPtr.Zero, true);
                }
                return new SafeFileHandle(raw, true);
            }
            finally {
                if (addedRef) parent.DangerousRelease();
                if (unicodePointer != System.IntPtr.Zero) Marshal.FreeHGlobal(unicodePointer);
                if (buffer != System.IntPtr.Zero) Marshal.FreeHGlobal(buffer);
            }
        }
        public static SafeFileHandle CreateNewFileRelative(SafeFileHandle parent, string name, uint shareAccess) {
            return CreateNewRelative(parent, name, 0x00100183, shareAccess, 0x80, 0x60);
        }
        public static SafeFileHandle CreateNewDirectoryRelative(SafeFileHandle parent, string name) {
            return CreateNewRelative(parent, name, 0x00100081, 0x3, 0x10, 0x21);
        }
    }
}
"@ -ErrorAction Stop
}

function Get-OpenInstallerFileLinkCount([IO.FileStream]$Stream, [string]$Code) {
    Initialize-JobFlowInstallerFileIdentityApi
    $information = New-Object JobFlowInstallerNative.FileIdentity
    if (-not [JobFlowInstallerNative.FileIdentityApi]::GetFileInformationByHandle(
        $Stream.SafeFileHandle, [ref]$information
    )) { throw $Code }
    return [long]$information.LinkCount
}

function Get-OpenInstallerFinalPath([IO.FileStream]$Stream, [string]$Code) {
    Initialize-JobFlowInstallerFileIdentityApi
    $builder = [Text.StringBuilder]::new(32768)
    $length = [JobFlowInstallerNative.FileIdentityApi]::GetFinalPathNameByHandle(
        $Stream.SafeFileHandle, $builder, [uint32]$builder.Capacity, 0
    )
    if ($length -lt 1 -or $length -ge $builder.Capacity) { throw $Code }
    $value = $builder.ToString()
    if ($value.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        $value = '\\' + $value.Substring(8)
    }
    elseif ($value.StartsWith('\\?\', [StringComparison]::OrdinalIgnoreCase)) {
        $value = $value.Substring(4)
    }
    return [IO.Path]::GetFullPath($value)
}

function Get-InstallerHandleIdentity(
    [Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
    [string]$Code
) {
    Initialize-JobFlowInstallerFileIdentityApi
    $information = New-Object JobFlowInstallerNative.FileIdentity
    if (-not [JobFlowInstallerNative.FileIdentityApi]::GetFileInformationByHandle($Handle, [ref]$information)) {
        throw $Code
    }
    return $information
}

function Get-InstallerFinalPathFromHandle(
    [Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
    [string]$Code
) {
    Initialize-JobFlowInstallerFileIdentityApi
    $builder = [Text.StringBuilder]::new(32768)
    $length = [JobFlowInstallerNative.FileIdentityApi]::GetFinalPathNameByHandle(
        $Handle, $builder, [uint32]$builder.Capacity, 0
    )
    if ($length -lt 1 -or $length -ge $builder.Capacity) { throw $Code }
    $value = $builder.ToString()
    if ($value.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        $value = '\\' + $value.Substring(8)
    }
    elseif ($value.StartsWith('\\?\', [StringComparison]::OrdinalIgnoreCase)) {
        $value = $value.Substring(4)
    }
    return [IO.Path]::GetFullPath($value)
}

function Open-StableInstallerDirectoryHandle([string]$Path, [string]$Code) {
    Initialize-JobFlowInstallerFileIdentityApi
    $absolute = [IO.Path]::GetFullPath($Path)
    # Deliberately omit FILE_SHARE_DELETE.  While this handle is retained the
    # directory cannot be renamed, deleted, or replaced by a junction.
    $handle = [JobFlowInstallerNative.FileIdentityApi]::CreateFileW(
        $absolute, 0x80, 0x3, [IntPtr]::Zero, 3, (0x02000000 -bor 0x00200000), [IntPtr]::Zero
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $identity = Get-InstallerHandleIdentity $handle $Code
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0
        ) { throw $Code }
        $final = Get-InstallerFinalPathFromHandle $handle $Code
        if (-not $final.Equals($absolute, [StringComparison]::OrdinalIgnoreCase)) { throw $Code }
        return [pscustomobject]@{
            Path = $absolute
            Handle = $handle
            Volume = [uint32]$identity.VolumeSerialNumber
            IndexHigh = [uint32]$identity.FileIndexHigh
            IndexLow = [uint32]$identity.FileIndexLow
        }
    }
    catch {
        $handle.Dispose()
        throw
    }
}

function Close-StableInstallerDirectoryContext([object]$Context) {
    if ($null -eq $Context -or $null -eq $Context.Locks) { return }
    for ($index = @($Context.Locks).Count - 1; $index -ge 0; $index--) {
        $lock = @($Context.Locks)[$index]
        if ($null -ne $lock -and $null -ne $lock.Handle) { $lock.Handle.Dispose() }
    }
}

function Assert-StableInstallerDirectoryContext([object]$Context, [string]$Code) {
    if ($null -eq $Context -or @($Context.Locks).Count -lt 1) { throw $Code }
    foreach ($lock in @($Context.Locks)) {
        if ($null -eq $lock.Handle -or $lock.Handle.IsInvalid -or $lock.Handle.IsClosed) { throw $Code }
        $identity = Get-InstallerHandleIdentity $lock.Handle $Code
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$identity.VolumeSerialNumber -ne [uint32]$lock.Volume -or
            [uint32]$identity.FileIndexHigh -ne [uint32]$lock.IndexHigh -or
            [uint32]$identity.FileIndexLow -ne [uint32]$lock.IndexLow -or
            -not (Get-InstallerFinalPathFromHandle $lock.Handle $Code).Equals(
                [string]$lock.Path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
    }
}

function New-InstallerDirectoryRelativeToLock([object]$ParentLock, [string]$Name, [string]$Code) {
    if ($null -eq $ParentLock -or $null -eq $ParentLock.Handle) { throw $Code }
    $handle = [JobFlowInstallerNative.FileIdentityApi]::CreateNewDirectoryRelative(
        $ParentLock.Handle, $Name
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $identity = Get-InstallerHandleIdentity $handle $Code
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0
        ) { throw $Code }
        $expected = [IO.Path]::GetFullPath((Join-Path ([string]$ParentLock.Path) $Name))
        if (-not (Get-InstallerFinalPathFromHandle $handle $Code).Equals(
            $expected, [StringComparison]::OrdinalIgnoreCase
        )) { throw $Code }
    }
    finally { $handle.Dispose() }
}

function Open-NewInstallerFileRelative(
    [object]$ParentContext,
    [string]$Destination,
    [string]$Code
) {
    Assert-StableInstallerDirectoryContext $ParentContext $Code
    $locks = @($ParentContext.Locks)
    $parentLock = $locks[$locks.Count - 1]
    $absolute = [IO.Path]::GetFullPath($Destination)
    if (-not [IO.Path]::GetDirectoryName($absolute).Equals(
        [string]$parentLock.Path, [StringComparison]::OrdinalIgnoreCase
    )) { throw $Code }
    $handle = [JobFlowInstallerNative.FileIdentityApi]::CreateNewFileRelative(
        $parentLock.Handle, [IO.Path]::GetFileName($absolute), 0
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $stream = [IO.FileStream]::new($handle, [IO.FileAccess]::ReadWrite)
        $handle = $null
        Assert-OpenInstallerFileAtPath $stream $absolute $Code
        Assert-StableInstallerDirectoryContext $ParentContext $Code
        return $stream
    }
    catch {
        if ($null -ne $handle) { $handle.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        throw
    }
}

function Open-StableInstallerDirectoryChain(
    [string]$Path,
    [string]$Code,
    [switch]$CreateMissing
) {
    Initialize-JobFlowInstallerFileIdentityApi
    $absolute = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($absolute)
    if ([string]::IsNullOrWhiteSpace($root)) { throw $Code }
    $paths = [System.Collections.Generic.List[string]]::new()
    [void]$paths.Add($root)
    $relative = $absolute.Substring($root.Length).TrimEnd('\')
    $cursor = $root
    if (-not [string]::IsNullOrWhiteSpace($relative)) {
        foreach ($component in $relative.Split('\')) {
            if ([string]::IsNullOrWhiteSpace($component) -or $component -eq '.' -or $component -eq '..') { throw $Code }
            $cursor = Join-Path $cursor $component
            [void]$paths.Add([IO.Path]::GetFullPath($cursor))
        }
    }
    $locks = [System.Collections.Generic.List[object]]::new()
    try {
        foreach ($candidate in $paths) {
            if (-not [IO.Directory]::Exists($candidate)) {
                if (-not $CreateMissing) { throw $Code }
                if ($locks.Count -lt 1) { throw $Code }
                New-InstallerDirectoryRelativeToLock $locks[$locks.Count - 1] ([IO.Path]::GetFileName($candidate)) $Code
            }
            [void]$locks.Add((Open-StableInstallerDirectoryHandle $candidate $Code))
        }
        $context = [pscustomobject]@{ Path = $absolute; Locks = @($locks.ToArray()) }
        Assert-StableInstallerDirectoryContext $context $Code
        return $context
    }
    catch {
        foreach ($lock in @($locks)) { if ($null -ne $lock.Handle) { $lock.Handle.Dispose() } }
        throw
    }
}

function New-StableInstallerDirectoryRoot([string]$Path, [string]$Code) {
    Initialize-JobFlowInstallerFileIdentityApi
    $absolute = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($absolute)
    $parentContext = Open-StableInstallerDirectoryChain $parent $Code
    try {
        Assert-StableInstallerDirectoryContext $parentContext $Code
        $parentLocks = @($parentContext.Locks)
        New-InstallerDirectoryRelativeToLock $parentLocks[$parentLocks.Count - 1] ([IO.Path]::GetFileName($absolute)) $Code
        return (Open-StableInstallerDirectoryChain $absolute $Code)
    }
    finally { Close-StableInstallerDirectoryContext $parentContext }
}

function Assert-OpenInstallerFileAtPath([IO.FileStream]$Stream, [string]$Path, [string]$Code) {
    $identity = Get-InstallerHandleIdentity $Stream.SafeFileHandle $Code
    if (
        ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -ne 0 -or
        ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [long]$identity.LinkCount -ne 1 -or
        -not (Get-InstallerFinalPathFromHandle $Stream.SafeFileHandle $Code).Equals(
            [IO.Path]::GetFullPath($Path), [StringComparison]::OrdinalIgnoreCase
        )
    ) { throw $Code }
}

function Remove-SafeInstallerTree([string]$Path, [string]$Code) {
    $absolute = [IO.Path]::GetFullPath($Path)
    Assert-JobFlowLocalPath $absolute
    $leaf = [IO.Path]::GetFileName($absolute)
    $parent = [IO.Path]::GetDirectoryName($absolute)
    $isTemporaryRoot = $parent.Equals($localRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $leaf -match '^\.(i|b|r|l)-[0-9a-f]{12}$'
    $isVersionRoot = $parent.Equals($versionsRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $leaf -match '^v[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}$'
    if (-not ($isTemporaryRoot -or $isVersionRoot) -or -not [IO.Directory]::Exists($absolute)) { throw $Code }

    function Remove-StableInstallerDirectory([string]$Directory) {
        $directoryContext = Open-StableInstallerDirectoryChain $Directory $Code
        try {
            Assert-StableInstallerDirectoryContext $directoryContext $Code
            foreach ($child in @(Get-ChildItem -LiteralPath $Directory -Force -ErrorAction Stop)) {
                $childPath = [IO.Path]::GetFullPath($child.FullName)
                if (-not [IO.Path]::GetDirectoryName($childPath).Equals(
                    [IO.Path]::GetFullPath($Directory), [StringComparison]::OrdinalIgnoreCase
                )) { throw $Code }
                if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    # Never traverse or unlink an identity that was not created
                    # as an ordinary child.  Preserve the residue for review.
                    throw $Code
                }
                if ($child.PSIsContainer) {
                    Remove-StableInstallerDirectory $childPath
                    continue
                }
                Assert-NoInstallerAlternateDataStreams $childPath $Code
                $stream = [IO.File]::Open(
                    $childPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite
                )
                try { Assert-OpenInstallerFileAtPath $stream $childPath $Code }
                finally { $stream.Dispose() }
                # File.Delete is non-recursive.  Even a same-user replacement
                # after the identity handle closes cannot make this operation
                # traverse a junction or delete an external directory tree.
                [IO.File]::Delete($childPath)
            }
            Assert-StableInstallerDirectoryContext $directoryContext $Code
            $last = @($directoryContext.Locks).Count - 1
            $leafLock = @($directoryContext.Locks)[$last]
            $leafLock.Handle.Dispose()
            $leafLock.Handle = $null
            [IO.Directory]::Delete([IO.Path]::GetFullPath($Directory), $false)
        }
        finally { Close-StableInstallerDirectoryContext $directoryContext }
    }
    Remove-StableInstallerDirectory $absolute
}

function Assert-SingleLinkInstallerLeaf([string]$Path, [string]$Code, [switch]$MustExist) {
    $absolute = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $absolute)) {
        if ($MustExist) { throw $Code }
        return
    }
    $item = Get-Item -LiteralPath $absolute -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
    # Identity inspection must coexist with JobFlow's own read/write lock
    # handles; no content is written through this handle.
    $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $stream = [IO.File]::Open($absolute, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
    try { if ((Get-OpenInstallerFileLinkCount $stream $Code) -ne 1) { throw $Code } }
    finally { $stream.Dispose() }
}

function Assert-TrustedPayloadRelative([string]$Relative, [string]$Code) {
    if (
        [string]::IsNullOrWhiteSpace($Relative) -or $Relative -notmatch '^[\x20-\x7e]+$' -or $Relative.Contains("\") -or
        $Relative.Contains(":") -or $Relative.IndexOfAny([char[]]'<>"|?*') -ge 0 -or
        $Relative.StartsWith("/") -or $Relative.EndsWith("/")
    ) { throw $Code }
    foreach ($part in $Relative.Split('/')) {
        if (
            [string]::IsNullOrWhiteSpace($part) -or $part -eq "." -or $part -eq ".." -or
            $part.EndsWith(".") -or $part.EndsWith(" ")
        ) { throw $Code }
        $base = $part.Split('.')[0].ToUpperInvariant()
        if (@("CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9") -contains $base) {
            throw $Code
        }
    }
}

function Assert-TrustedUpdatePayloadContextContract([object]$Payload) {
    $expectedTopNames = @(
        "schema_version", "status", "transaction_nonce", "release", "expected_current",
        "archive_sha256", "archive_prefix", "directory_count", "file_count", "directories",
        "records", "inventory_sha256", "extracted_root_sha256"
    )
    if (
        -not (Test-ExactJsonProperties $Payload $expectedTopNames) -or
        -not (Test-JsonIntegerInRange $Payload.schema_version 2 2) -or
        -not (Test-JsonString $Payload.status) -or
        -not (Test-JsonString $Payload.transaction_nonce) -or
        -not (Test-JsonString $Payload.archive_sha256) -or
        -not (Test-JsonString $Payload.archive_prefix) -or
        -not (Test-JsonString $Payload.inventory_sha256) -or
        -not (Test-JsonString $Payload.extracted_root_sha256) -or
        [string]$Payload.status -cne "UPDATE_EXTRACTED_PAYLOAD_ATTESTED" -or
        ([string]$Payload.transaction_nonce) -notmatch '^[0-9a-f]{64}$' -or
        ([string]$Payload.archive_sha256) -notmatch '^[0-9a-f]{64}$' -or
        ([string]$Payload.inventory_sha256) -notmatch '^[0-9a-f]{64}$' -or
        ([string]$Payload.extracted_root_sha256) -notmatch '^[0-9a-f]{64}$' -or
        [string]$Payload.inventory_sha256 -cne [string]$Payload.extracted_root_sha256 -or
        -not (Test-JsonIntegerInRange $Payload.file_count 1 2147483647) -or
        -not (Test-JsonIntegerInRange $Payload.directory_count 0 2147483647) -or
        -not ($Payload.records -is [Array]) -or
        -not ($Payload.directories -is [Array]) -or
        [long]$Payload.file_count -ne @($Payload.records).Count -or
        [long]$Payload.directory_count -ne @($Payload.directories).Count -or
        @($Payload.records).Count -lt 1
    ) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }

    if (
        -not (Test-ExactJsonProperties $Payload.release @(
            "version", "commit", "archive_name", "archive_size", "archive_sha256", "archive_prefix"
        )) -or
        -not (Test-ExactJsonProperties $Payload.expected_current @(
            "version_directory", "version", "source_sha256"
        ))
    ) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
    $releaseVersion = [string]$Payload.release.version
    $releaseCommit = [string]$Payload.release.commit
    $expectedCurrentVersion = [string]$Payload.expected_current.version
    foreach ($stringValue in @(
        $Payload.release.version, $Payload.release.commit, $Payload.release.archive_name,
        $Payload.release.archive_sha256, $Payload.release.archive_prefix,
        $Payload.expected_current.version_directory, $Payload.expected_current.version,
        $Payload.expected_current.source_sha256
    )) {
        if (-not (Test-JsonString $stringValue)) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
    }
    ConvertTo-StrictJobFlowVersionTuple $releaseVersion "JOBFLOW_TRUSTED_UPDATE_RELEASE_INVALID" | Out-Null
    ConvertTo-StrictJobFlowVersionTuple $expectedCurrentVersion "JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_INVALID" | Out-Null
    if (
        $releaseCommit -notmatch '^[0-9a-f]{40}$' -or
        -not (Test-JsonIntegerInRange $Payload.release.archive_size 1 1073741824) -or
        ([string]$Payload.release.archive_sha256) -notmatch '^[0-9a-f]{64}$' -or
        [string]$Payload.release.archive_sha256 -cne [string]$Payload.archive_sha256 -or
        [string]$Payload.release.archive_prefix -cne [string]$Payload.archive_prefix -or
        [string]$Payload.release.archive_prefix -cne "JobFlow-v$releaseVersion/" -or
        [string]$Payload.release.archive_name -cne "JobFlow-v$releaseVersion-$($releaseCommit.Substring(0, 12))-source.zip" -or
        ([string]$Payload.expected_current.version_directory) -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' -or
        ([string]$Payload.expected_current.source_sha256) -notmatch '^[0-9a-f]{64}$' -or
        -not (Test-JobFlowVersionStrictlyGreater $releaseVersion $expectedCurrentVersion)
    ) { throw "JOBFLOW_TRUSTED_UPDATE_CONTEXT_MISMATCH" }

    $seenDirectories = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($directory in @($Payload.directories)) {
        if ($directory.GetType() -ne [string]) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
        $relative = [string]$directory
        Assert-TrustedPayloadRelative $relative "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        if (
            $relative.Normalize([Text.NormalizationForm]::FormC) -cne $relative -or
            -not $seenDirectories.Add($relative)
        ) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
    }
    $seenFiles = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in @($Payload.records)) {
        if (-not (Test-ExactJsonProperties $record @("length", "relative", "sha256"))) {
            throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        }
        if (-not (Test-JsonString $record.relative) -or -not (Test-JsonString $record.sha256)) {
            throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        }
        $relative = [string]$record.relative
        Assert-TrustedPayloadRelative $relative "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        if (
            $relative.Normalize([Text.NormalizationForm]::FormC) -cne $relative -or
            -not $seenFiles.Add($relative) -or
            -not (Test-JsonIntegerInRange $record.length 0 2147483648) -or
            ([string]$record.sha256) -notmatch '^[0-9a-f]{64}$'
        ) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
    }
    $inventoryDigest = Get-TrustedPayloadInventorySha256 @($Payload.directories) @($Payload.records)
    if (
        $inventoryDigest -cne [string]$Payload.inventory_sha256 -or
        $inventoryDigest -cne [string]$Payload.extracted_root_sha256
    ) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
}

function Get-TrustedUpdatePayloadRecords {
    if ([string]::IsNullOrWhiteSpace($TrustedUpdatePayloadManifest) -ne [string]::IsNullOrWhiteSpace($TrustedUpdatePayloadManifestSha256)) {
        throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_ARGUMENTS_INVALID"
    }
    if ([string]::IsNullOrWhiteSpace($TrustedUpdatePayloadManifest)) { return $null }
    if ($TrustedUpdatePayloadManifestSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_DIGEST_INVALID"
    }
    Assert-JobFlowLocalPath $projectRoot
    $manifestPath = [IO.Path]::GetFullPath($TrustedUpdatePayloadManifest)
    Assert-JobFlowLocalPath $manifestPath
    Assert-SingleLinkInstallerLeaf $manifestPath "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" -MustExist
    $script:trustedUpdatePayloadManifestLock = [IO.File]::Open(
        $manifestPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
    if ((Get-OpenInstallerFileLinkCount $script:trustedUpdatePayloadManifestLock "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID") -ne 1) {
        throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
    }
    if ((Get-OpenInstallerStreamSha256 $script:trustedUpdatePayloadManifestLock) -cne $TrustedUpdatePayloadManifestSha256) {
        throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_DIGEST_MISMATCH"
    }
    try {
        $payloadText = Read-OpenInstallerUtf8Text $script:trustedUpdatePayloadManifestLock 67108864 "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        $payload = $payloadText | ConvertFrom-Json
    }
    catch { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
    Assert-TrustedUpdatePayloadContextContract $payload
    $script:trustedUpdateContext = $payload

    $expectedDirectories = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in @($payload.directories)) {
        if ($entry.GetType() -ne [string]) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
        $relative = [string]$entry
        Assert-TrustedPayloadRelative $relative "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        if ($relative.Normalize([Text.NormalizationForm]::FormC) -cne $relative) {
            throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        }
        if (-not $expectedDirectories.Add($relative)) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
    }
    $expectedFiles = [System.Collections.Generic.Dictionary[string, object]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in @($payload.records)) {
        $recordNames = @($record.PSObject.Properties.Name | Sort-Object)
        if (($recordNames -join "|") -cne "length|relative|sha256") { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
        $relative = [string]$record.relative
        Assert-TrustedPayloadRelative $relative "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID"
        if (
            $relative.Normalize([Text.NormalizationForm]::FormC) -cne $relative -or
            $expectedFiles.ContainsKey($relative) -or
            -not (Test-JsonIntegerInRange $record.length 0 2147483648) -or
            ([string]$record.sha256) -notmatch '^[0-9a-f]{64}$'
        ) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_MANIFEST_INVALID" }
        $expectedFiles.Add($relative, $record)
    }

    $actualDirectories = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $actualFiles = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($projectRoot)
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        Assert-SourcePath $directory
        foreach ($item in @(Get-ChildItem -LiteralPath $directory -Force)) {
            Assert-SourcePath $item.FullName
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH"
            }
            $relative = $item.FullName.Substring($projectRoot.Length).TrimStart('\', '/').Replace('\', '/')
            Assert-TrustedPayloadRelative $relative "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH"
            if ($item.PSIsContainer) {
                if (-not $actualDirectories.Add($relative)) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH" }
                $pending.Push($item.FullName)
            }
            else {
                if (-not $actualFiles.Add($relative)) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH" }
            }
        }
    }
    if ($actualDirectories.Count -ne $expectedDirectories.Count -or $actualFiles.Count -ne $expectedFiles.Count) {
        throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH"
    }
    foreach ($relative in $actualDirectories) {
        if (-not $expectedDirectories.Contains($relative)) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH" }
    }

    $verified = [System.Collections.Generic.List[object]]::new()
    foreach ($relative in @($expectedFiles.Keys | Sort-Object)) {
        if (-not $actualFiles.Contains($relative)) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH" }
        $record = $expectedFiles[$relative]
        $source = [IO.Path]::GetFullPath((Join-Path $projectRoot $relative))
        Assert-SourcePath $source
        $lock = [IO.File]::Open($source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $trustedUpdateSourceLocks.Add($lock)
        if (
            (Get-OpenInstallerFileLinkCount $lock "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH") -ne 1 -or
            $lock.Length -ne [long]$record.length -or
            (Get-OpenInstallerStreamSha256 $lock) -cne [string]$record.sha256
        ) { throw "JOBFLOW_TRUSTED_UPDATE_PAYLOAD_TREE_MISMATCH" }
        $verified.Add([pscustomobject]@{
            Relative = $relative; Source = $source; Length = [long]$record.length
            Sha256 = [string]$record.sha256; LockedStream = $lock
        })
    }
    return @($verified)
}

function Move-InstallerFileReplaceExisting([string]$Source, [string]$Destination, [string]$Code) {
    Initialize-JobFlowInstallerFileIdentityApi
    if (-not [JobFlowInstallerNative.FileIdentityApi]::MoveFileEx($Source, $Destination, 0x9)) { throw $Code }
}

function Copy-InstallerFileAtomic([string]$Source, [string]$Destination, [string]$Code) {
    $absoluteDestination = [IO.Path]::GetFullPath($Destination)
    $parent = [IO.Path]::GetDirectoryName($absoluteDestination)
    $temporary = Join-Path $parent (([IO.Path]::GetFileName($absoluteDestination)) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    $input = $null
    $output = $null
    try {
        $input = [IO.File]::Open($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $output = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $input.CopyTo($output)
        $output.Flush($true)
        $output.Dispose(); $output = $null
        Assert-SingleLinkInstallerLeaf $temporary $Code -MustExist
        Assert-SingleLinkInstallerLeaf $absoluteDestination $Code
        Move-InstallerFileReplaceExisting $temporary $absoluteDestination $Code
    }
    finally {
        if ($null -ne $output) { $output.Dispose() }
        if ($null -ne $input) { $input.Dispose() }
        if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Enter-JobFlowFileLock {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$TimeoutCode,
        [int]$TimeoutSeconds = 30
    )
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Path))
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw $TimeoutCode }
    $stream = [IO.File]::Open(
        $Path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite
    )
    try {
        if ((Get-OpenInstallerFileLinkCount $stream "JOBFLOW_INSTALL_LOCK_FILE_LINKED") -ne 1) {
            throw "JOBFLOW_INSTALL_LOCK_FILE_LINKED"
        }
        if ($stream.Length -lt 1) {
            $stream.SetLength(1)
            $stream.Flush()
        }
        $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $TimeoutSeconds))
        while ($true) {
            try {
                $stream.Lock(0, 1)
                return $stream
            }
            catch [IO.IOException] {
                if ([DateTime]::UtcNow -ge $deadline) { throw $TimeoutCode }
                Start-Sleep -Milliseconds 50
            }
        }
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function Exit-JobFlowFileLock([object]$Stream) {
    if ($null -eq $Stream) { return }
    try { $Stream.Unlock(0, 1) } catch { }
    $Stream.Dispose()
}

function Assert-OrdinaryPointerLeafOrAbsent([string]$Path) {
    Assert-JobFlowLocalPath $Path
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "JOBFLOW_INSTALLED_POINTER_INVALID"
    }
    Assert-SingleLinkInstallerLeaf $Path "JOBFLOW_INSTALLED_POINTER_INVALID" -MustExist
}

function Write-JsonAtomic([string]$Path, [object]$Value) {
    Assert-OrdinaryPointerLeafOrAbsent $Path
    $temporary = Join-Path $localRoot (([IO.Path]::GetFileName($Path)) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    $backup = Join-Path $localRoot (([IO.Path]::GetFileName($Path)) + "." + [Guid]::NewGuid().ToString("N") + ".backup")
    Assert-JobFlowLocalPath $temporary
    Assert-JobFlowLocalPath $backup
    $json = $Value | ConvertTo-Json -Depth 6 -Compress
    $previousExisted = Test-Path -LiteralPath $Path
    $previousJson = $null
    if ($previousExisted) {
        Assert-JobFlowLocalPath $Path
        $previousJson = [IO.File]::ReadAllText($Path)
    }
    $committed = $false
    $preserveBackup = $false
    try {
        [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
        Assert-OrdinaryPointerLeafOrAbsent $Path
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temporary, $Path, $backup, $true)
        }
        else {
            [IO.File]::Move($temporary, $Path)
        }
        # The pointer now names the new value. Cleanup below is deliberately
        # outside the commit decision so a failed backup deletion cannot make
        # the caller roll back or delete the active application directory.
        $committed = $true
    }
    catch {
        $writeError = $_
        $stateKnown = $false
        try {
            Assert-JobFlowLocalPath $Path
            if (Test-Path -LiteralPath $Path -PathType Leaf) {
                $currentJson = [IO.File]::ReadAllText($Path)
                if ([string]::Equals($currentJson, $json, [StringComparison]::Ordinal)) {
                    # File.Replace/Move may report an error after the directory
                    # entry is already durable. Exact byte comparison proves the
                    # requested pointer value won, so continue without deleting
                    # the newly referenced version.
                    $committed = $true
                    $preserveBackup = $true
                    Write-Warning "JOBFLOW_INSTALLED_POINTER_COMMIT_RECOVERED"
                }
                elseif ($previousExisted -and [string]::Equals($currentJson, $previousJson, [StringComparison]::Ordinal)) {
                    $stateKnown = $true
                }
            }
            elseif (-not $previousExisted) {
                $stateKnown = $true
            }
        }
        catch {
            $stateKnown = $false
        }
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            try { Remove-Item -LiteralPath $temporary -Force }
            catch { Write-Warning "JOBFLOW_INSTALLED_POINTER_TEMP_CLEANUP_FAILED" }
        }
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            # File.Replace can fail after the filesystem has created a usable
            # backup. Preserve it whenever the commit result is uncertain.
            Write-Warning "JOBFLOW_INSTALLED_POINTER_BACKUP_PRESERVED"
        }
        if (-not $committed) {
            if (-not $stateKnown) { throw "JOBFLOW_INSTALLED_POINTER_COMMIT_UNKNOWN" }
            throw $writeError
        }
    }
    if (-not $committed) { throw "JOBFLOW_INSTALLED_POINTER_COMMIT_UNKNOWN" }
    if (Test-Path -LiteralPath $temporary -PathType Leaf) {
        try { Remove-Item -LiteralPath $temporary -Force }
        catch { Write-Warning "JOBFLOW_INSTALLED_POINTER_TEMP_CLEANUP_FAILED" }
    }
    if (-not $preserveBackup -and (Test-Path -LiteralPath $backup -PathType Leaf)) {
        try { Remove-Item -LiteralPath $backup -Force }
        catch { Write-Warning "JOBFLOW_INSTALLED_POINTER_BACKUP_CLEANUP_FAILED" }
    }
}

function Read-InstalledPointer([string]$Path) {
    Assert-OrdinaryPointerLeafOrAbsent $Path
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
    catch { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
    if ($null -eq $value -or $value -is [Array] -or -not ($value -is [pscustomobject])) {
        throw "JOBFLOW_INSTALLED_POINTER_INVALID"
    }
    $schemaVersion = $value.PSObject.Properties["schema_version"]
    $directoryProperty = $value.PSObject.Properties["version_directory"]
    $versionProperty = $value.PSObject.Properties["version"]
    $sourceHashProperty = $value.PSObject.Properties["source_sha256"]
    if (
        $null -eq $schemaVersion -or
        $null -eq $directoryProperty -or
        $null -eq $versionProperty -or
        $null -eq $sourceHashProperty
    ) { throw "JOBFLOW_INSTALLED_POINTER_INVALID" }
    $directory = $directoryProperty.Value
    $versionValue = $versionProperty.Value
    $sourceHashValue = $sourceHashProperty.Value
    if (
        -not (Test-JsonIntegerOne $schemaVersion.Value) -or
        -not (Test-JsonString $directory) -or
        -not (Test-JsonString $versionValue) -or
        -not (Test-JsonString $sourceHashValue) -or
        $directory -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$' -or
        [string]::IsNullOrWhiteSpace($versionValue) -or
        $sourceHashValue -notmatch '^[0-9a-f]{64}$'
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

function Assert-TrustedUpdateHandoffContext(
    [object]$Context,
    [object]$CurrentPointer,
    [string]$ProjectVersion
) {
    if ($null -eq $Context) { return }
    if ($null -eq $CurrentPointer) { throw "JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_MISMATCH" }
    $expected = $Context.expected_current
    if (
        [string]$Context.release.version -cne $ProjectVersion -or
        [string]$Context.release.archive_prefix -cne "JobFlow-v$ProjectVersion/" -or
        [string]$CurrentPointer.version_directory -cne [string]$expected.version_directory -or
        [string]$CurrentPointer.version -cne [string]$expected.version -or
        [string]$CurrentPointer.source_sha256 -cne [string]$expected.source_sha256 -or
        -not (Test-JobFlowVersionStrictlyGreater $ProjectVersion ([string]$CurrentPointer.version))
    ) { throw "JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_MISMATCH" }
}

function Test-VersionHealth([string]$VersionRoot) {
    Assert-JobFlowLocalPath $VersionRoot
    $markerPath = Join-Path $VersionRoot ".jobops-root"
    $pythonPath = Join-Path $VersionRoot ".venv\Scripts\python.exe"
    $healthPath = Join-Path $VersionRoot "scripts\check-jobflow.ps1"
    try {
        foreach ($path in @($markerPath, $pythonPath, $healthPath)) {
            Assert-JobFlowLocalPath $path
        }
    }
    catch { return $false }
    if (
        -not (Test-Path -LiteralPath $markerPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $healthPath -PathType Leaf)
    ) { return $false }
    $savedDataRoot = $env:JOBFLOW_DATA_ROOT
    try {
        $env:JOBFLOW_DATA_ROOT = $dataRoot
        Push-Location $VersionRoot
        try {
            $null = & $trustedPowerShellPath -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
                -File ".\scripts\check-jobflow.ps1" -Json -PythonPath ".\.venv\Scripts\python.exe" 2>$null
            return $LASTEXITCODE -eq 0
        }
        finally { Pop-Location }
    }
    catch { return $false }
    finally {
        if ($null -eq $savedDataRoot) { Remove-Item Env:JOBFLOW_DATA_ROOT -ErrorAction SilentlyContinue }
        else { $env:JOBFLOW_DATA_ROOT = $savedDataRoot }
    }
}

function Set-CurrentUserOnly([string]$Path) {
    Assert-LocalTreeNoReparse $Path "JOBFLOW_INSTALL_ACL_TREE_LINKED"
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $grant = "*$($identity.User.Value):(OI)(CI)F"
    & $trustedIcaclsPath $Path "/inheritance:r" "/grant:r" $grant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_INSTALL_ACL_FAILED" }
    if (@(Get-ChildItem -LiteralPath $Path -Force).Count -gt 0) {
        & $trustedIcaclsPath (Join-Path $Path "*") "/reset" "/T" "/C" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_INSTALL_CHILD_ACL_FAILED" }
    }
}

function Assert-StableStartMenuPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($env:APPDATA)) {
        throw "JOBFLOW_START_MENU_APP_DATA_NOT_FOUND"
    }
    $appDataRoot = [IO.Path]::GetFullPath($env:APPDATA)
    Assert-ExistingAncestorChainNoReparse $appDataRoot "JOBFLOW_START_MENU_PATH_FORBIDDEN_OR_LINKED"
    $programsRoot = [IO.Path]::GetFullPath((Join-Path $appDataRoot "Microsoft\Windows\Start Menu\Programs"))
    $menuRoot = [IO.Path]::GetFullPath((Join-Path $programsRoot "JobFlow"))
    $absolute = [IO.Path]::GetFullPath($Path)
    $appDataPrefix = $appDataRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $programsPrefix = $programsRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $menuPrefix = $menuRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $programsRoot.StartsWith($appDataPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not $menuRoot.StartsWith($programsPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        ($absolute -ne $menuRoot -and -not $absolute.StartsWith($menuPrefix, [StringComparison]::OrdinalIgnoreCase))
    ) {
        throw "JOBFLOW_START_MENU_PATH_FORBIDDEN"
    }
    # Recheck the boundary and every existing ancestor beneath it immediately
    # before reads or writes. This rejects redirected APPDATA, Programs, the
    # JobFlow folder, and any linked shortcut leaf.
    Assert-NoReparse $appDataRoot $appDataRoot "JOBFLOW_START_MENU_PATH_FORBIDDEN_OR_LINKED"
    Assert-NoReparse $programsRoot $appDataRoot "JOBFLOW_START_MENU_PATH_FORBIDDEN_OR_LINKED"
    Assert-NoReparse $menuRoot $appDataRoot "JOBFLOW_START_MENU_PATH_FORBIDDEN_OR_LINKED"
    Assert-NoReparse $absolute $appDataRoot "JOBFLOW_START_MENU_PATH_FORBIDDEN_OR_LINKED"
}

function Get-StableStartMenuRoot {
    if ([string]::IsNullOrWhiteSpace($env:APPDATA)) { return $null }
    $programsRoot = [IO.Path]::GetFullPath((Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))
    $menuRoot = [IO.Path]::GetFullPath((Join-Path $programsRoot "JobFlow"))
    $programsPrefix = $programsRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $menuRoot.StartsWith($programsPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_START_MENU_PATH_FORBIDDEN"
    }
    Assert-StableStartMenuPath $menuRoot
    return $menuRoot
}

function Assert-StableLauncherTarget([string]$Target, [object]$MenuRoot) {
    $absolute = [IO.Path]::GetFullPath($Target)
    if ($null -ne $MenuRoot) {
        $menuAbsolute = [IO.Path]::GetFullPath([string]$MenuRoot)
        $menuPrefix = $menuAbsolute.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if ($absolute -eq $menuAbsolute -or $absolute.StartsWith($menuPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Assert-StableStartMenuPath $absolute
            return
        }
    }
    Assert-JobFlowLocalPath $absolute
}

function Get-StableLauncherTargets {
    $targets = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $stableLauncherFiles | Where-Object { $_.EndsWith(".ps1", [StringComparison]::OrdinalIgnoreCase) }) {
        $targets.Add((Join-Path $binRoot $name))
    }
    foreach ($name in $stableLauncherFiles | Where-Object { $_.EndsWith(".cmd", [StringComparison]::OrdinalIgnoreCase) }) {
        $targets.Add((Join-Path $localRoot $name))
    }
    $menuRoot = Get-StableStartMenuRoot
    if ($null -ne $menuRoot) {
        foreach ($entry in $stableShortcutEntries) {
            $target = Join-Path $menuRoot $entry.Name
            Assert-StableStartMenuPath $target
            $targets.Add($target)
        }
    }
    return @($targets)
}

function New-StableLauncherSnapshot {
    Assert-JobFlowLocalPath $launcherRollbackRoot
    if (Test-Path -LiteralPath $launcherRollbackRoot) {
        throw "JOBFLOW_STABLE_LAUNCHER_BACKUP_COLLISION"
    }
    $menuRoot = Get-StableStartMenuRoot
    $snapshot = [pscustomobject]@{
        Records = [System.Collections.Generic.List[object]]::new()
        BinRootExisted = (Test-Path -LiteralPath $binRoot -PathType Container)
        MenuRoot = $menuRoot
        MenuRootExisted = ($null -ne $menuRoot -and (Test-Path -LiteralPath $menuRoot -PathType Container))
    }
    New-Item -ItemType Directory -Path $launcherRollbackRoot -Force | Out-Null
    try {
        $index = 0
        foreach ($target in Get-StableLauncherTargets) {
            $index += 1
            Assert-StableLauncherTarget $target $menuRoot
            if (Test-Path -LiteralPath $target) {
                $item = Get-Item -LiteralPath $target -Force
                if (-not $item.PSIsContainer -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
                    Assert-SingleLinkInstallerLeaf $target "JOBFLOW_STABLE_LAUNCHER_TARGET_LINKED" -MustExist
                    $backup = Join-Path $launcherRollbackRoot (("{0:D3}.backup" -f $index))
                    Copy-InstallerFileAtomic $target $backup "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"
                    Assert-SingleLinkInstallerLeaf $backup "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED" -MustExist
                    $snapshot.Records.Add([pscustomobject]@{ Target = $target; Existed = $true; Backup = $backup })
                }
                else {
                    throw "JOBFLOW_STABLE_LAUNCHER_TARGET_INVALID"
                }
            }
            else {
                $snapshot.Records.Add([pscustomobject]@{ Target = $target; Existed = $false; Backup = $null })
            }
        }
        return $snapshot
    }
    catch {
        if (Test-Path -LiteralPath $launcherRollbackRoot -PathType Container) {
            try {
                Assert-LocalTreeNoReparse $launcherRollbackRoot "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"
                Remove-SafeInstallerTree $launcherRollbackRoot "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"
            }
            catch { Write-Warning "JOBFLOW_STABLE_LAUNCHER_BACKUP_CLEANUP_FAILED" }
        }
        throw
    }
}

function Restore-StableLauncherSnapshot([object]$Snapshot) {
    $rollbackFailed = $false
    foreach ($record in @($Snapshot.Records)) {
        try {
            Assert-StableLauncherTarget ([string]$record.Target) $Snapshot.MenuRoot
            $parent = [IO.Path]::GetDirectoryName([string]$record.Target)
            if ($record.Existed) {
                if (-not (Test-Path -LiteralPath $record.Backup -PathType Leaf)) {
                    throw "JOBFLOW_STABLE_LAUNCHER_BACKUP_MISSING"
                }
                Assert-SingleLinkInstallerLeaf $record.Backup "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED" -MustExist
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
                Copy-InstallerFileAtomic $record.Backup $record.Target "JOBFLOW_STABLE_LAUNCHER_ROLLBACK_TARGET_INVALID"
            }
            elseif (Test-Path -LiteralPath $record.Target) {
                $item = Get-Item -LiteralPath $record.Target -Force
                if ($item.PSIsContainer) { throw "JOBFLOW_STABLE_LAUNCHER_ROLLBACK_TARGET_INVALID" }
                Assert-SingleLinkInstallerLeaf $record.Target "JOBFLOW_STABLE_LAUNCHER_ROLLBACK_TARGET_INVALID" -MustExist
                Remove-Item -LiteralPath $record.Target -Force
            }
        }
        catch { $rollbackFailed = $true }
    }
    foreach ($directoryRecord in @(
        @{ Path = $binRoot; Existed = [bool]$Snapshot.BinRootExisted },
        @{ Path = $Snapshot.MenuRoot; Existed = [bool]$Snapshot.MenuRootExisted }
    )) {
        if ($null -eq $directoryRecord.Path -or $directoryRecord.Existed) { continue }
        try {
            if ($directoryRecord.Path -eq $Snapshot.MenuRoot) {
                Assert-StableStartMenuPath ([string]$directoryRecord.Path)
            }
            else {
                Assert-JobFlowLocalPath ([string]$directoryRecord.Path)
            }
            if (
                (Test-Path -LiteralPath $directoryRecord.Path -PathType Container) -and
                @(Get-ChildItem -LiteralPath $directoryRecord.Path -Force).Count -eq 0
            ) {
                Remove-Item -LiteralPath $directoryRecord.Path -Force
            }
        }
        catch { $rollbackFailed = $true }
    }
    if ($rollbackFailed) {
        throw "JOBFLOW_STABLE_LAUNCHER_ROLLBACK_FAILED"
    }
    if (Test-Path -LiteralPath $launcherRollbackRoot -PathType Container) {
        try {
            Assert-LocalTreeNoReparse $launcherRollbackRoot "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"
            Remove-SafeInstallerTree $launcherRollbackRoot "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"
        }
        catch {
            # The launcher state is already restored. Retain the backup and
            # preserve the original activation error rather than masking it.
            Write-Warning "JOBFLOW_STABLE_LAUNCHER_BACKUP_CLEANUP_FAILED"
        }
    }
}

function Install-StableLaunchers([string]$VersionRoot, [object[]]$Records) {
    Assert-StagedSourceSnapshot $VersionRoot $Records
    $runtimeSource = Join-Path $VersionRoot "scripts\windows-runtime"
    Assert-JobFlowLocalPath $runtimeSource
    foreach ($name in $stableLauncherFiles) {
        $source = Join-Path $runtimeSource $name
        Assert-JobFlowLocalPath $source
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "JOBFLOW_STABLE_LAUNCHER_MISSING"
        }
    }
    New-Item -ItemType Directory -Path $binRoot -Force | Out-Null
    Assert-JobFlowLocalPath $binRoot
    foreach ($name in $stableLauncherFiles | Where-Object { $_.EndsWith(".ps1", [StringComparison]::OrdinalIgnoreCase) }) {
        $relative = "scripts/windows-runtime/$name"
        $source = Assert-StagedSourceRecord $VersionRoot $relative $Records
        Copy-InstallerFileAtomic $source (Join-Path $binRoot $name) "JOBFLOW_STABLE_LAUNCHER_TARGET_INVALID"
    }
    foreach ($name in $stableLauncherFiles | Where-Object { $_.EndsWith(".cmd", [StringComparison]::OrdinalIgnoreCase) }) {
        $relative = "scripts/windows-runtime/$name"
        $source = Assert-StagedSourceRecord $VersionRoot $relative $Records
        Copy-InstallerFileAtomic $source (Join-Path $localRoot $name) "JOBFLOW_STABLE_LAUNCHER_TARGET_INVALID"
    }

    $menuRoot = Get-StableStartMenuRoot
    if ($null -ne $menuRoot) {
        Assert-StableStartMenuPath $menuRoot
        New-Item -ItemType Directory -Path $menuRoot -Force | Out-Null
        Assert-StableStartMenuPath $menuRoot
        $shell = New-Object -ComObject WScript.Shell
        foreach ($entry in $stableShortcutEntries) {
            $shortcutPath = Join-Path $menuRoot $entry.Name
            Assert-StableStartMenuPath $shortcutPath
            Assert-SingleLinkInstallerLeaf $shortcutPath "JOBFLOW_STABLE_LAUNCHER_TARGET_LINKED"
            $shortcutTemporary = Join-Path $menuRoot (([IO.Path]::GetFileNameWithoutExtension($entry.Name)) + "." + [Guid]::NewGuid().ToString("N") + ".lnk")
            Assert-StableStartMenuPath $shortcutTemporary
            try {
                $shortcut = $shell.CreateShortcut($shortcutTemporary)
                $shortcut.TargetPath = Join-Path $localRoot $entry.Target
                $shortcut.WorkingDirectory = $localRoot
                $shortcut.Save()
                Assert-SingleLinkInstallerLeaf $shortcutTemporary "JOBFLOW_STABLE_LAUNCHER_TARGET_INVALID" -MustExist
                Move-InstallerFileReplaceExisting $shortcutTemporary $shortcutPath "JOBFLOW_STABLE_LAUNCHER_TARGET_INVALID"
            }
            finally {
                if (Test-Path -LiteralPath $shortcutTemporary -PathType Leaf) {
                    Remove-Item -LiteralPath $shortcutTemporary -Force
                }
            }
        }
    }
}

try {
    $trustedPowerShell = Get-TrustedWindowsPowerShell
    $trustedPowerShellPath = [string]$trustedPowerShell.Path
    $powerShellExecutableLock = $trustedPowerShell.Lock
    $trustedIcaclsPath = [IO.Path]::GetFullPath((Join-Path ([Environment]::SystemDirectory) "icacls.exe"))
    Assert-ExistingAncestorChainNoReparse $trustedIcaclsPath "JOBFLOW_TRUSTED_ICACLS_REQUIRED"
    $icaclsExecutableLock = [IO.File]::Open(
        $trustedIcaclsPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
    # Stock Windows may expose System32\icacls.exe as a legitimate WinSxS
    # hardlink.  The fixed System32 path, held handle/final-path identity,
    # non-reparse ancestor chain, ADS rejection, and Microsoft Authenticode
    # signature establish trust without requiring exactly one link.
    if (
        (Get-OpenInstallerFileLinkCount $icaclsExecutableLock "JOBFLOW_TRUSTED_ICACLS_REQUIRED") -lt 1 -or
        -not (Get-OpenInstallerFinalPath $icaclsExecutableLock "JOBFLOW_TRUSTED_ICACLS_REQUIRED").Equals(
            $trustedIcaclsPath, [StringComparison]::OrdinalIgnoreCase
        )
    ) { throw "JOBFLOW_TRUSTED_ICACLS_REQUIRED" }
    Assert-NoInstallerAlternateDataStreams $trustedIcaclsPath "JOBFLOW_TRUSTED_ICACLS_REQUIRED"
    Test-TrustedExecutableSignature $trustedIcaclsPath "Microsoft Corporation" "JOBFLOW_TRUSTED_ICACLS_REQUIRED"
    $python = Find-SupportedPython
    if ($null -eq $python) {
        throw "需要来自 Python Software Foundation 的 64 位 Python 3.11 或 3.12。请从 python.org 安装受支持的 Windows 版本后重试。 / A Python Software Foundation-signed 64-bit Python 3.11 or 3.12 installation is required. Install a supported Windows release from python.org and retry."
    }
    $pythonCommand = [string]$python.Command
    $pythonExecutableLock = $python.Lock
    $trustedPayloadFiles = Get-TrustedUpdatePayloadRecords

    $pyprojectText = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
    if ($pyprojectText -notmatch '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)"\s*$') {
        throw "JOBFLOW_INSTALL_VERSION_INVALID"
    }
    $version = [string]$Matches[1]
    if ($null -ne $trustedUpdateContext -and [string]$trustedUpdateContext.release.version -cne $version) {
        throw "JOBFLOW_TRUSTED_UPDATE_RELEASE_VERSION_MISMATCH"
    }

    $rootFiles = @(
        ".jobops-root", "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE",
        "Install JobFlow Browser Companion.cmd", "MANIFEST.in", "README.md", "SECURITY.md", "Update JobFlow.cmd", "pyproject.toml"
    )
    $sourceDirectories = @(".agents", "browser-companion", "config", "docs", "schemas", "scripts", "src", "tests")
    $installFiles = [System.Collections.Generic.List[object]]::new()
    if ($null -ne $trustedPayloadFiles) {
        foreach ($record in @($trustedPayloadFiles)) {
            $relative = [string]$record.Relative
            $lower = $relative.ToLowerInvariant()
            $isRootFile = @($rootFiles | Where-Object { $_.Equals($relative, [StringComparison]::OrdinalIgnoreCase) }).Count -eq 1
            $isSourceFile = $false
            foreach ($directoryName in $sourceDirectories) {
                if ($relative.StartsWith($directoryName + "/", [StringComparison]::OrdinalIgnoreCase)) {
                    $isSourceFile = $true
                    break
                }
            }
            if (-not $isRootFile -and -not $isSourceFile) { continue }
            if (
                $lower -eq "browser-companion/binding.json" -or
                $lower -eq "browser-companion-binding.json" -or
                $lower -match '(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.tmp|\.git)(/|$)' -or
                $lower -match '\.(pyc|pyo|db|sqlite|sqlite3|dpapi|zip|7z|rar|log)$'
            ) { continue }
            $installFiles.Add($record)
        }
    }
    else {
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
    }
    $rootMarkerRecords = @($installFiles | Where-Object { $_.Relative -eq ".jobops-root" })
    if ($rootMarkerRecords.Count -ne 1) {
        throw "JOBFLOW_INSTALL_SOURCE_INCOMPLETE"
    }
    $duplicates = $installFiles | Group-Object { $_.Relative.ToLowerInvariant() } | Where-Object { $_.Count -ne 1 }
    if ($duplicates) { throw "JOBFLOW_INSTALL_SOURCE_DUPLICATE" }
    $installFiles = @($installFiles | Sort-Object -Property Relative)

    $verifiedInstallFiles = [System.Collections.Generic.List[object]]::new()
    $manifestBuilder = New-Object Text.StringBuilder
    foreach ($record in $installFiles) {
        Assert-SourcePath $record.Source
        $item = Get-Item -LiteralPath $record.Source -Force
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL"
        }
        $hash = Get-FileSha256 ([string]$record.Source)
        $itemAfterHash = Get-Item -LiteralPath $record.Source -Force
        if (
            $itemAfterHash.PSIsContainer -or
            ($itemAfterHash.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [long]$itemAfterHash.Length -ne [long]$item.Length
        ) {
            throw "JOBFLOW_INSTALL_SOURCE_CHANGED_DURING_INSTALL"
        }
        $verified = [pscustomobject]@{
            Relative = [string]$record.Relative
            Source = [string]$record.Source
            Length = [long]$itemAfterHash.Length
            Sha256 = [string]$hash
        }
        $verifiedInstallFiles.Add($verified)
        [void]$manifestBuilder.Append($verified.Relative).Append('|').Append($verified.Length).Append('|').Append($verified.Sha256).Append("`n")
    }
    $installFiles = @($verifiedInstallFiles)
    $dependencyLockRelative = "config/windows-cp3$($python.Minor)-requirements.lock"
    $dependencyLockRecords = @($installFiles | Where-Object {
        ([string]$_.Relative).Equals($dependencyLockRelative, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($dependencyLockRecords.Count -ne 1) { throw "JOBFLOW_INSTALL_DEPENDENCY_LOCK_MISSING" }
    $manifestBytes = [Text.Encoding]::UTF8.GetBytes($manifestBuilder.ToString())
    $sourceHasher = [Security.Cryptography.SHA256]::Create()
    try { $sourceHash = -join ($sourceHasher.ComputeHash($manifestBytes) | ForEach-Object { $_.ToString("x2") }) }
    finally { $sourceHasher.Dispose() }
    $versionDirectory = "v$version-$($sourceHash.Substring(0, 12))"
    $targetVersionRoot = Join-Path $versionsRoot $versionDirectory

    foreach ($path in @(
        $localRoot, $applicationRoot, $versionsRoot, $dataRoot, $binRoot,
        $currentPointerPath, $previousPointerPath, $stagingRoot, $buildRoot, $repairBackupRoot,
        $launcherRollbackRoot, $targetVersionRoot, $rollbackPointerTransactionPath,
        $rollbackPointerTransactionBackupPath
    )) { Assert-JobFlowLocalPath $path }

    $runtimeLockPath = Join-Path $dataRoot "state\.jobflow-runtime-maintenance.lock"
    $discoveryLockPath = Join-Path $dataRoot "state\.authorized-discovery-task.lock"
    Assert-JobFlowLocalPath $runtimeLockPath
    Assert-JobFlowLocalPath $discoveryLockPath
    $existingPointer = $null
    if ($null -ne $trustedUpdateContext) {
        # An updater handoff is allowed only against the exact installed state
        # that the parent verified. The lock files must already exist, so lock
        # acquisition itself cannot create or repair any state before this
        # comparison succeeds.
        foreach ($requiredDirectory in @(
            $localRoot, $applicationRoot, $versionsRoot, $dataRoot, (Join-Path $dataRoot "state")
        )) {
            Assert-JobFlowLocalPath $requiredDirectory
            if (-not (Test-Path -LiteralPath $requiredDirectory -PathType Container)) {
                throw "JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_MISMATCH"
            }
        }
        foreach ($requiredLock in @($runtimeLockPath, $discoveryLockPath)) {
            Assert-SingleLinkInstallerLeaf $requiredLock "JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_MISMATCH" -MustExist
            if ((Get-Item -LiteralPath $requiredLock -Force).Length -lt 1) {
                throw "JOBFLOW_TRUSTED_UPDATE_CURRENT_IDENTITY_MISMATCH"
            }
        }
        $runtimeLockStream = (Enter-JobFlowFileLock $runtimeLockPath "JOBFLOW_INSTALL_RUNNING_INSTANCE_ACTIVE")
        $discoveryLockStream = (Enter-JobFlowFileLock $discoveryLockPath "JOBFLOW_INSTALL_DISCOVERY_RUN_ACTIVE")
        foreach ($rollbackJournalPath in @($rollbackPointerTransactionPath, $rollbackPointerTransactionBackupPath)) {
            if (Test-Path -LiteralPath $rollbackJournalPath) { throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED" }
        }
        $existingPointer = Read-InstalledPointer $currentPointerPath
        Assert-TrustedUpdateHandoffContext $trustedUpdateContext $existingPointer $version
    }

    New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $applicationRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $versionsRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $dataRoot | Out-Null
    }
    Assert-LocalTreeNoReparse $dataRoot "JOBFLOW_RUNTIME_DATA_REPARSE_FORBIDDEN"
    $dataMarkerPath = Join-Path $dataRoot ".jobflow-data-root"
    Assert-JobFlowLocalPath $dataMarkerPath
    if (Test-Path -LiteralPath $dataMarkerPath -PathType Leaf) {
        try { $dataMarker = Get-Content -LiteralPath $dataMarkerPath -Raw | ConvertFrom-Json }
        catch { throw "JOBFLOW_RUNTIME_DATA_MARKER_INVALID" }
        if (
            $null -eq $dataMarker -or
            $dataMarker -is [Array] -or
            -not ($dataMarker -is [pscustomobject]) -or
            -not (Test-JsonIntegerOne $dataMarker.schema_version) -or
            -not (Test-JsonString $dataMarker.kind) -or
            $dataMarker.kind -cne "JOBFLOW_RUNTIME_DATA"
        ) {
            throw "JOBFLOW_RUNTIME_DATA_MARKER_INVALID"
        }
    }
    else {
        [IO.File]::WriteAllText($dataMarkerPath, '{"schema_version":1,"kind":"JOBFLOW_RUNTIME_DATA"}', (New-Object Text.UTF8Encoding($false)))
    }
    foreach ($area in @("state", "workspace", "reports")) {
        $areaPath = Join-Path $dataRoot $area
        Assert-JobFlowLocalPath $areaPath
        if ((Test-Path -LiteralPath $areaPath) -and -not (Test-Path -LiteralPath $areaPath -PathType Container)) {
            throw "JOBFLOW_RUNTIME_DATA_LAYOUT_INVALID"
        }
        New-Item -ItemType Directory -Path $areaPath -Force | Out-Null
    }
    Assert-LocalTreeNoReparse $dataRoot "JOBFLOW_RUNTIME_DATA_REPARSE_FORBIDDEN"
    Set-CurrentUserOnly $dataRoot

    if ($null -eq $runtimeLockStream) {
        $runtimeLockStream = Enter-JobFlowFileLock $runtimeLockPath "JOBFLOW_INSTALL_RUNNING_INSTANCE_ACTIVE"
    }
    if ($null -eq $discoveryLockStream) {
        $discoveryLockStream = Enter-JobFlowFileLock $discoveryLockPath "JOBFLOW_INSTALL_DISCOVERY_RUN_ACTIVE"
    }

    # A rollback journal means pointer recovery has not been proven complete.
    # This check is authoritative only after both maintenance locks are held,
    # and precedes every installed-pointer read or mutation.
    foreach ($rollbackJournalPath in @($rollbackPointerTransactionPath, $rollbackPointerTransactionBackupPath)) {
        Assert-JobFlowLocalPath $rollbackJournalPath
        if (Test-Path -LiteralPath $rollbackJournalPath) {
            throw "JOBFLOW_ROLLBACK_RECOVERY_REQUIRED"
        }
    }

    # This snapshot controls failure cleanup. It must describe the state after
    # this installer owns both maintenance locks, not the stale state observed
    # by a competing same-version process before it waited for the lock.
    $targetExistedBefore = Test-Path -LiteralPath $targetVersionRoot -PathType Container
    if ($null -eq $existingPointer) {
        $existingPointer = Read-InstalledPointer $currentPointerPath
    }
    if ($targetExistedBefore) {
        Assert-LocalTreeNoReparse $targetVersionRoot "JOBFLOW_INSTALL_EXISTING_VERSION_LINKED"
    }
    $targetAlreadyHealthy = (Test-Path -LiteralPath $targetVersionRoot -PathType Container) -and (Test-VersionHealth $targetVersionRoot)
    if (-not $targetAlreadyHealthy) {
        Write-Host "正在准备固定版本目录…… / Preparing the fixed application version..."
        if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
            Remove-SafeInstallerTree $stagingRoot "JOBFLOW_INSTALL_STAGING_CLEANUP_LINKED"
        }
        try {
            $stagingDirectoryContext = Copy-VerifiedSourceSnapshot $stagingRoot $installFiles
            $buildDirectoryContext = Copy-VerifiedSourceSnapshot $buildRoot $installFiles
            Set-CurrentUserOnly $stagingRoot
            Set-CurrentUserOnly $buildRoot

            $stagedPython = Join-Path $stagingRoot ".venv\Scripts\python.exe"
            Write-Host "正在创建隔离运行环境…… / Creating the isolated runtime..."
            Push-Location $localRoot
            try {
                Invoke-IsolatedInstallerPython $pythonCommand @(
                    "-I", "-P", "-B", "-X", "utf8", "-m", "venv",
                    (Join-Path ([IO.Path]::GetFileName($stagingRoot)) ".venv")
                ) "JOBFLOW_INSTALL_VENV_FAILED"
            }
            finally { Pop-Location }
            if (-not (Test-Path -LiteralPath $stagedPython -PathType Leaf)) { throw "JOBFLOW_INSTALL_VENV_FAILED" }
            Push-Location $buildRoot
            try {
                $dependencyLockPath = Join-Path $buildRoot $dependencyLockRelative
                Assert-JobFlowLocalPath $dependencyLockPath
                Assert-StagedSourceRecord $buildRoot $dependencyLockRelative $installFiles | Out-Null
                Write-Host "正在安装哈希锁定的 Windows 运行依赖…… / Installing the hash-locked Windows runtime dependencies..."
                Invoke-IsolatedInstallerPython $stagedPython @(
                    "-I", "-P", "-B", "-X", "utf8", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-input",
                    "--require-virtualenv", "install", "--quiet", "--no-cache-dir", "--only-binary", ":all:",
                    "--no-deps", "--require-hashes", "--index-url", "https://pypi.org/simple",
                    "--requirement", $dependencyLockRelative
                ) "JOBFLOW_INSTALL_DEPENDENCIES_FAILED"
                Write-Warning "JOBFLOW_RUNTIME_CLOSURE_UNATTESTED; wheel downloads are hash-locked, but the complete installed runtime closure is not yet independently attested."
                Write-Host "正在安装 JobFlow…… / Installing JobFlow..."
                Invoke-IsolatedInstallerPython $stagedPython @(
                    "-I", "-P", "-B", "-X", "utf8", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-input",
                    "--require-virtualenv", "install", "--quiet", "--no-index", "--no-deps",
                    "--no-build-isolation", "."
                ) "JOBFLOW_INSTALL_PACKAGE_FAILED"
                Invoke-IsolatedInstallerPython $stagedPython @(
                    "-I", "-P", "-B", "-X", "utf8", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-input",
                    "--require-virtualenv", "check"
                ) "JOBFLOW_INSTALL_DEPENDENCY_CHECK_FAILED"
            }
            finally { Pop-Location }

            if (-not (Test-VersionHealth $stagingRoot)) {
                throw "JOBFLOW_INSTALL_HEALTH_CHECK_FAILED"
            }
            # Recheck the complete source payload immediately before the fixed
            # directory is committed. Only the locally generated .venv tree is
            # outside this immutable source snapshot.
            Assert-StagedSourceSnapshot $stagingRoot $installFiles
            if (Test-Path -LiteralPath $targetVersionRoot -PathType Container) {
                Assert-LocalTreeNoReparse $targetVersionRoot "JOBFLOW_INSTALL_EXISTING_VERSION_LINKED"
                if (Test-Path -LiteralPath $repairBackupRoot) {
                    throw "JOBFLOW_INSTALL_REPAIR_BACKUP_COLLISION"
                }
                Move-Item -LiteralPath $targetVersionRoot -Destination $repairBackupRoot
                $versionWasRepaired = $true
                Assert-LocalTreeNoReparse $repairBackupRoot "JOBFLOW_INSTALL_REPAIR_BACKUP_LINKED"
            }
            Assert-StableInstallerDirectoryContext $stagingDirectoryContext "JOBFLOW_INSTALL_STAGING_DESTINATION_UNSAFE"
            Close-StableInstallerDirectoryContext $stagingDirectoryContext
            $stagingDirectoryContext = $null
            Assert-LocalTreeNoReparse $stagingRoot "JOBFLOW_INSTALL_STAGING_CLEANUP_LINKED"
            Move-Item -LiteralPath $stagingRoot -Destination $targetVersionRoot
        }
        catch {
            if (-not (Test-Path -LiteralPath $targetVersionRoot -PathType Container) -and (Test-Path -LiteralPath $repairBackupRoot -PathType Container)) {
                Assert-LocalTreeNoReparse $repairBackupRoot "JOBFLOW_INSTALL_REPAIR_BACKUP_LINKED"
                Move-Item -LiteralPath $repairBackupRoot -Destination $targetVersionRoot
                Assert-LocalTreeNoReparse $targetVersionRoot "JOBFLOW_INSTALL_ROLLBACK_TREE_LINKED"
            }
            throw
        }
    }

    if (-not (Test-VersionHealth $targetVersionRoot)) {
        throw "JOBFLOW_INSTALL_FINAL_HEALTH_CHECK_FAILED"
    }
    Assert-StagedSourceSnapshot $targetVersionRoot $installFiles

    $newPointer = [ordered]@{
        schema_version = 1
        version_directory = $versionDirectory
        version = $version
        source_sha256 = $sourceHash
    }
    $companionInstaller = Join-Path $targetVersionRoot "scripts\install-jobflow-browser-companion.ps1"
    Assert-StagedSourceRecord $targetVersionRoot "scripts/install-jobflow-browser-companion.ps1" $installFiles | Out-Null
    $previousPointerExisted = Test-Path -LiteralPath $previousPointerPath -PathType Leaf
    $previousPointerBefore = $null
    $willMutatePreviousPointer = (
        ($null -ne $existingPointer -and [string]$existingPointer.version_directory -ne $versionDirectory) -or
        ($null -eq $existingPointer -and $previousPointerExisted)
    )
    if ($willMutatePreviousPointer -and $previousPointerExisted) {
        $previousPointerBefore = Read-InstalledPointer $previousPointerPath
    }
    $pointerSwitched = $false
    $previousPointerMutated = $false
    try {
        $stableLauncherSnapshot = New-StableLauncherSnapshot
        $launchersMutated = $true
        Install-StableLaunchers $targetVersionRoot $installFiles

        # The active core is switched and health-checked before the companion is
        # committed. The companion helper is itself transactional, so a failure
        # leaves the previous browser runtime and native host intact.
        $pointerSwitched = $true
        Write-JsonAtomic $currentPointerPath $newPointer
        if (-not (Test-VersionHealth $targetVersionRoot)) {
            throw "JOBFLOW_INSTALL_POST_SWITCH_HEALTH_CHECK_FAILED"
        }

        if ($null -ne $existingPointer -and [string]$existingPointer.version_directory -ne $versionDirectory) {
            $previousPointerMutated = $true
            Write-JsonAtomic $previousPointerPath $existingPointer
        }
        elseif ($null -eq $existingPointer -and $previousPointerExisted) {
            $previousPointerMutated = $true
            Assert-SingleLinkInstallerLeaf $previousPointerPath "JOBFLOW_INSTALLED_POINTER_INVALID" -MustExist
            Remove-Item -LiteralPath $previousPointerPath -Force
        }

        if ($skipBrowserIntegrationForAcceptance) {
            Write-Host "隔离验收仅验证核心安装；浏览器注册未触碰。 / Isolated acceptance verifies the core install without touching browser registration."
        }
        else {
            Write-Host "正在安装安全浏览器通道…… / Installing the secure browser channel..."
            Assert-StagedSourceRecord $targetVersionRoot "scripts/install-jobflow-browser-companion.ps1" $installFiles | Out-Null
            & $trustedPowerShellPath -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
                -File $companionInstaller -NoLaunch
            if ($LASTEXITCODE -ne 0) { throw "JOBFLOW_BROWSER_COMPANION_INSTALL_FAILED" }
        }
        $activationCommitted = $true
    }
    catch {
        $activationError = $_
        $pointerCommitUnknown = ([string]$activationError.Exception.Message) -match "JOBFLOW_INSTALLED_POINTER_COMMIT_UNKNOWN"
        if ($pointerCommitUnknown) {
            # The active pointer cannot be proved old or new. Preserve every
            # possibly referenced version and any atomic-write backup; a
            # deletion-based rollback could otherwise create a dangling pointer.
            $preserveTargetOnFailure = $true
            $pointerSwitched = $false
            $previousPointerMutated = $false
        }
        $pointerRollbackFailed = $false
        $launcherRollbackFailed = $false
        try {
            if ($pointerSwitched) {
                if ($null -ne $existingPointer) {
                    Write-JsonAtomic $currentPointerPath $existingPointer
                }
                elseif (Test-Path -LiteralPath $currentPointerPath -PathType Leaf) {
                    Assert-SingleLinkInstallerLeaf $currentPointerPath "JOBFLOW_INSTALLED_POINTER_INVALID" -MustExist
                    Remove-Item -LiteralPath $currentPointerPath -Force
                }
            }
            if ($previousPointerMutated) {
                if ($previousPointerExisted) {
                    Write-JsonAtomic $previousPointerPath $previousPointerBefore
                }
                elseif (Test-Path -LiteralPath $previousPointerPath -PathType Leaf) {
                    Assert-SingleLinkInstallerLeaf $previousPointerPath "JOBFLOW_INSTALLED_POINTER_INVALID" -MustExist
                    Remove-Item -LiteralPath $previousPointerPath -Force
                }
            }
        }
        catch {
            $pointerRollbackFailed = $true
            $preserveTargetOnFailure = $true
        }
        if ($launchersMutated -and $null -ne $stableLauncherSnapshot) {
            try {
                Restore-StableLauncherSnapshot $stableLauncherSnapshot
                $stableLauncherSnapshot = $null
                $launchersMutated = $false
            }
            catch {
                $launcherRollbackFailed = $true
                # A launcher may still reference the just-installed target when
                # restoration is incomplete. Preserve that target so a failed
                # rollback cannot turn a recoverable launcher mismatch into a
                # guaranteed broken shortcut or command entry.
                $preserveTargetOnFailure = $true
            }
        }
        if ($pointerRollbackFailed -and $launcherRollbackFailed) {
            throw "JOBFLOW_INSTALL_POINTER_AND_LAUNCHER_ROLLBACK_FAILED"
        }
        if ($pointerRollbackFailed) {
            throw "JOBFLOW_INSTALL_POINTER_ROLLBACK_FAILED"
        }
        if ($launcherRollbackFailed) {
            throw "JOBFLOW_STABLE_LAUNCHER_ROLLBACK_FAILED"
        }
        throw $activationError
    }

    Write-Host "JobFlow $version 已安装到当前用户的固定目录（Python $($python.Version)）。 / JobFlow $version is installed in the current user's fixed app directory (Python $($python.Version))."
    Write-Host "个人资料、队列和报告保存在独立数据目录；更新或回滚不会覆盖它们。 / Profile data, queues, and reports are stored separately and survive updates or rollback."
    Write-Host "可从 Windows 开始菜单打开 JobFlow、检查签名更新、运行自检、回滚或卸载。 / Open, check signed updates, run diagnostics, roll back, or uninstall JobFlow from the Windows Start menu."

    if (-not $NoLaunch -and -not $skipBrowserIntegrationForAcceptance) {
        Assert-StagedSourceRecord $targetVersionRoot "scripts/install-jobflow-browser-companion.ps1" $installFiles | Out-Null
        & $trustedPowerShellPath -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $companionInstaller -OpenStoreOnly
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "浏览器商店未自动打开；安装已完成，可稍后从 JobFlow 手动打开。 / JOBFLOW_BROWSER_COMPANION_STORE_LAUNCH_FAILED"
        }
    }
}
catch {
    $installError = $_
    if (-not $activationCommitted -and -not $preserveTargetOnFailure -and -not [string]::IsNullOrWhiteSpace([string]$targetVersionRoot)) {
        try {
            if ($versionWasRepaired -and (Test-Path -LiteralPath $repairBackupRoot -PathType Container)) {
                if (Test-Path -LiteralPath $targetVersionRoot -PathType Container) {
                    Assert-LocalTreeNoReparse $targetVersionRoot "JOBFLOW_INSTALL_ROLLBACK_TREE_LINKED"
                    Remove-SafeInstallerTree $targetVersionRoot "JOBFLOW_INSTALL_ROLLBACK_TREE_LINKED"
                }
                Assert-LocalTreeNoReparse $repairBackupRoot "JOBFLOW_INSTALL_REPAIR_BACKUP_LINKED"
                Move-Item -LiteralPath $repairBackupRoot -Destination $targetVersionRoot
                Assert-LocalTreeNoReparse $targetVersionRoot "JOBFLOW_INSTALL_ROLLBACK_TREE_LINKED"
            }
            elseif (-not $targetExistedBefore -and (Test-Path -LiteralPath $targetVersionRoot -PathType Container)) {
                Assert-LocalTreeNoReparse $targetVersionRoot "JOBFLOW_INSTALL_ROLLBACK_TREE_LINKED"
                Remove-SafeInstallerTree $targetVersionRoot "JOBFLOW_INSTALL_ROLLBACK_TREE_LINKED"
            }
        }
        catch {
            $preserveTargetOnFailure = $true
            throw "JOBFLOW_INSTALL_APPLICATION_ROLLBACK_FAILED"
        }
    }
    throw $installError
}
finally {
    foreach ($stream in $trustedUpdateSourceLocks) { if ($null -ne $stream) { $stream.Dispose() } }
    if ($null -ne $trustedUpdatePayloadManifestLock) { $trustedUpdatePayloadManifestLock.Dispose() }
    if ($null -ne $pythonExecutableLock) { $pythonExecutableLock.Dispose() }
    if ($null -ne $powerShellExecutableLock) { $powerShellExecutableLock.Dispose() }
    if ($null -ne $icaclsExecutableLock) { $icaclsExecutableLock.Dispose() }
    Exit-JobFlowFileLock $discoveryLockStream
    Exit-JobFlowFileLock $runtimeLockStream
    Close-StableInstallerDirectoryContext $stagingDirectoryContext
    $stagingDirectoryContext = $null
    Close-StableInstallerDirectoryContext $buildDirectoryContext
    $buildDirectoryContext = $null
    if (Test-Path -LiteralPath $stagingRoot -PathType Container) {
        try {
            Assert-JobFlowLocalPath $stagingRoot
            Remove-SafeInstallerTree $stagingRoot "JOBFLOW_INSTALL_STAGING_CLEANUP_LINKED"
        }
        catch { Write-Warning "JOBFLOW_INSTALL_STAGING_CLEANUP_FAILED" }
    }
    if (Test-Path -LiteralPath $buildRoot -PathType Container) {
        try {
            Assert-JobFlowLocalPath $buildRoot
            Remove-SafeInstallerTree $buildRoot "JOBFLOW_INSTALL_BUILD_CLEANUP_LINKED"
        }
        catch { Write-Warning "JOBFLOW_INSTALL_BUILD_CLEANUP_FAILED" }
    }
    if ($activationCommitted -and (Test-Path -LiteralPath $repairBackupRoot -PathType Container)) {
        try {
            Remove-SafeInstallerTree $repairBackupRoot "JOBFLOW_INSTALL_REPAIR_BACKUP_LINKED"
        }
        catch { Write-Warning "JOBFLOW_INSTALL_REPAIR_BACKUP_CLEANUP_FAILED" }
    }
    elseif (Test-Path -LiteralPath $repairBackupRoot -PathType Container) {
        Write-Warning "JOBFLOW_INSTALL_REPAIR_BACKUP_PRESERVED"
    }
    if ($activationCommitted -and (Test-Path -LiteralPath $launcherRollbackRoot -PathType Container)) {
        try {
            Remove-SafeInstallerTree $launcherRollbackRoot "JOBFLOW_STABLE_LAUNCHER_BACKUP_LINKED"
        }
        catch { Write-Warning "JOBFLOW_STABLE_LAUNCHER_BACKUP_CLEANUP_FAILED" }
    }
    elseif (Test-Path -LiteralPath $launcherRollbackRoot -PathType Container) {
        Write-Warning "JOBFLOW_STABLE_LAUNCHER_BACKUP_PRESERVED"
    }
}
