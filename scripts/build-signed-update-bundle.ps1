[CmdletBinding()]
param(
    [string]$Stage,
    [string]$CompleteRuntimeArchivePath,
    [string]$RuntimeClosurePath,
    [string]$RuntimeBuildEvidencePath,
    [string]$PublisherEvidencePath,
    [string]$ReleasePythonArtifactPath,
    [string]$LegacyV1PredecessorsPath,
    [string]$PredecessorMinimumVersion,
    [string]$MinimumUpdaterVersion,
    [string]$MinimumBootstrapVersion,
    [string]$PresignManifestPath,
    [string]$SigningRequestPath,
    [string]$SignatureEnvelopePath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = $null

function Get-KnownFolderPath([Environment+SpecialFolder]$Folder, [string]$Code) {
    $value = [Environment]::GetFolderPath($Folder)
    if ([string]::IsNullOrWhiteSpace($value) -or -not [IO.Path]::IsPathRooted($value)) { throw $Code }
    return [IO.Path]::GetFullPath($value)
}

function Assert-NoReparsePath([string]$Path, [string]$Code, [switch]$MustExist) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) { throw $Code }
    $absolute = [IO.Path]::GetFullPath($Path)
    if ($MustExist -and -not [IO.File]::Exists($absolute) -and -not [IO.Directory]::Exists($absolute)) { throw $Code }
    $root = [IO.Path]::GetPathRoot($absolute)
    if ([string]::IsNullOrWhiteSpace($root)) { throw $Code }
    $cursor = $root.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    if ([string]::IsNullOrWhiteSpace($cursor)) { $cursor = $root }
    $relative = $absolute.Substring($root.Length)
    foreach ($component in $relative.Split(@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar), [StringSplitOptions]::RemoveEmptyEntries)) {
        $cursor = Join-Path $cursor $component
        if ([IO.File]::Exists($cursor) -or [IO.Directory]::Exists($cursor)) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
        }
    }
    return $absolute
}

function Get-ReleaseToolchainPolicy([object]$PolicyLock) {
    $expectedPath = [IO.Path]::GetFullPath((Join-Path $projectRoot "config\release-toolchain.json"))
    if (
        $null -eq $PolicyLock -or
        -not [string]::Equals([string]$PolicyLock.path, $expectedPath, [StringComparison]::OrdinalIgnoreCase)
    ) { throw "JOBFLOW_RELEASE_TOOLCHAIN_POLICY_INVALID" }
    $value = Read-LockedJsonObject $PolicyLock "JOBFLOW_RELEASE_TOOLCHAIN_POLICY_INVALID"
    if (
        $null -eq $value -or
        -not (Test-JsonInteger $value.schema_version 1) -or
        $null -eq $value.tools
    ) { throw "JOBFLOW_RELEASE_TOOLCHAIN_POLICY_INVALID" }
    return $value
}

function Find-GitApplication {
    $profile = Get-KnownFolderPath ([Environment+SpecialFolder]::UserProfile) "JOBFLOW_RELEASE_GIT_NOT_FOUND"
    $programFiles = Get-KnownFolderPath ([Environment+SpecialFolder]::ProgramFiles) "JOBFLOW_RELEASE_GIT_NOT_FOUND"
    foreach ($candidate in @(
        (Join-Path $profile ".cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\mingw64\bin\git.exe"),
        (Join-Path $programFiles "Git\mingw64\bin\git.exe")
    )) {
        $absolute = [IO.Path]::GetFullPath($candidate)
        if ([IO.File]::Exists($absolute) -and [IO.Path]::GetExtension($absolute) -ceq ".exe") {
            Assert-NoReparsePath $absolute "JOBFLOW_RELEASE_GIT_NOT_TRUSTED" -MustExist | Out-Null
            return $absolute
        }
    }
    throw "JOBFLOW_RELEASE_GIT_NOT_FOUND"
}

function Find-NodeApplication {
    $profile = Get-KnownFolderPath ([Environment+SpecialFolder]::UserProfile) "JOBFLOW_RELEASE_NODE_NOT_FOUND"
    $programFiles = Get-KnownFolderPath ([Environment+SpecialFolder]::ProgramFiles) "JOBFLOW_RELEASE_NODE_NOT_FOUND"
    foreach ($candidate in @(
        (Join-Path $profile ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"),
        (Join-Path $programFiles "nodejs\node.exe")
    )) {
        $absolute = [IO.Path]::GetFullPath($candidate)
        if ([IO.File]::Exists($absolute) -and [IO.Path]::GetExtension($absolute) -ceq ".exe") {
            Assert-NoReparsePath $absolute "JOBFLOW_RELEASE_NODE_NOT_TRUSTED" -MustExist | Out-Null
            return $absolute
        }
    }
    throw "JOBFLOW_RELEASE_NODE_NOT_FOUND"
}

function Get-StreamSha256([IO.Stream]$Stream) {
    if ($null -eq $Stream -or -not $Stream.CanRead -or -not $Stream.CanSeek) {
        throw "JOBFLOW_RELEASE_HASH_STREAM_INVALID"
    }
    $originalPosition = $Stream.Position
    $algorithm = $null
    try {
        $Stream.Position = 0
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $digest = $algorithm.ComputeHash($Stream)
        return ([BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        if ($null -ne $algorithm) { $algorithm.Dispose() }
        $Stream.Position = $originalPosition
    }
}

function Get-FileSha256([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $stream = $null
    try {
        $stream = [IO.File]::Open(
            $absolute,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        return Get-StreamSha256 -Stream $stream
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Get-AuthenticatedToolIdentity([string]$Tool, [string]$Path, [object]$Policy) {
    $code = "JOBFLOW_RELEASE_" + $Tool.ToUpperInvariant() + "_NOT_TRUSTED"
    $absolute = Assert-NoReparsePath $Path $code -MustExist
    if (-not [IO.File]::Exists($absolute) -or [IO.Path]::GetExtension($absolute) -cne ".exe") { throw $code }
    $toolPolicy = $Policy.tools.PSObject.Properties[$Tool].Value
    if (
        $null -eq $toolPolicy -or
        -not ($toolPolicy.file_names -is [Array]) -or
        $toolPolicy.file_names.Count -lt 1 -or
        -not ($toolPolicy.allowed_signers -is [Array]) -or
        $toolPolicy.allowed_signers.Count -lt 1 -or
        -not ($toolPolicy.file_names -ccontains [IO.Path]::GetFileName($absolute))
    ) { throw "JOBFLOW_RELEASE_TOOLCHAIN_POLICY_INVALID" }
    $systemPowerShell = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\powershell.exe"
    Assert-NoReparsePath $systemPowerShell $code -MustExist | Out-Null
    $encodedToolPath = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($absolute))
    $signatureScript = @"
`$ProgressPreference = 'SilentlyContinue'
`$InformationPreference = 'SilentlyContinue'
`$VerbosePreference = 'SilentlyContinue'
`$WarningPreference = 'SilentlyContinue'
`$securityModule = Join-Path `$PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
Import-Module -Name `$securityModule -ErrorAction Stop
`$toolPath = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('$encodedToolPath'))
`$signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath `$toolPath
if (`$signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or `$null -eq `$signature.SignerCertificate) { exit 41 }
[pscustomobject]@{
    status = [string]`$signature.Status
    subject = [string]`$signature.SignerCertificate.Subject
    thumbprint = ([string]`$signature.SignerCertificate.Thumbprint).ToUpperInvariant()
} | ConvertTo-Json -Compress
"@
    $encodedSignatureScript = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($signatureScript))
    $signatureCommand = Invoke-SanitizedTextCommand "powershell" $systemPowerShell @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", $encodedSignatureScript
    ) $projectRoot $null
    if ($signatureCommand.exit_code -ne 0 -or -not [string]::IsNullOrWhiteSpace($signatureCommand.stderr)) { throw $code }
    try { $signature = $signatureCommand.stdout | ConvertFrom-Json }
    catch { throw $code }
    if ($null -eq $signature -or $signature.status -cne "Valid") { throw $code }
    $subject = [string]$signature.subject
    $thumbprint = ([string]$signature.thumbprint).ToUpperInvariant()
    $allowed = $false
    foreach ($signer in $toolPolicy.allowed_signers) {
        if (
            $null -ne $signer -and
            ($signer.subject -is [string]) -and [string]$signer.subject -ceq $subject -and
            ($signer.thumbprint -is [string]) -and ([string]$signer.thumbprint).ToUpperInvariant() -ceq $thumbprint
        ) { $allowed = $true; break }
    }
    if (-not $allowed) { throw $code }
    return [pscustomobject]@{
        tool = $Tool
        path = $absolute
        sha256 = "sha256:" + (Get-FileSha256 $absolute)
        signer_subject = $subject
        signer_thumbprint = $thumbprint
    }
}

function Enter-AuthenticatedToolLock([string]$Tool, [string]$Path, [object]$Policy) {
    $code = "JOBFLOW_RELEASE_" + $Tool.ToUpperInvariant() + "_NOT_TRUSTED"
    $absolute = Assert-NoReparsePath $Path $code -MustExist
    if (-not [IO.File]::Exists($absolute) -or [IO.Path]::GetExtension($absolute) -cne ".exe") { throw $code }
    $stream = $null
    try {
        # Deny writes and deletion before Authenticode or path-based hashing is
        # attempted.  Both operations may reopen the executable by name; this
        # held handle binds those reads to the same ordinary single-link file.
        $stream = [IO.File]::Open(
            $absolute,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $openIdentity = Get-OpenOutputFileIdentity $stream $code
        if ([long]$openIdentity.link_count -ne 1) { throw $code }
        $before = Get-AuthenticatedToolIdentity $Tool $absolute $Policy
        if (-not [string]::Equals([string]$before.path, $absolute, [StringComparison]::OrdinalIgnoreCase)) {
            throw $code
        }
        if (("sha256:" + (Get-StreamSha256 $stream)) -cne [string]$before.sha256) {
            throw $code
        }
        return [pscustomobject]@{ identity = $before; stream = $stream }
    }
    catch {
        if ($null -ne $stream) { $stream.Dispose() }
        throw
    }
}

function Assert-AuthenticatedToolUnchanged([object]$Lock, [object]$Policy) {
    if ($null -eq $Lock -or $null -eq $Lock.identity -or $null -eq $Lock.stream) {
        throw "JOBFLOW_RELEASE_TOOL_IDENTITY_INVALID"
    }
    $after = Get-AuthenticatedToolIdentity ([string]$Lock.identity.tool) ([string]$Lock.identity.path) $Policy
    if (
        [string]$after.sha256 -cne [string]$Lock.identity.sha256 -or
        [string]$after.signer_subject -cne [string]$Lock.identity.signer_subject -or
        [string]$after.signer_thumbprint -cne [string]$Lock.identity.signer_thumbprint -or
        ("sha256:" + (Get-StreamSha256 $Lock.stream)) -cne [string]$Lock.identity.sha256
    ) { throw ("JOBFLOW_RELEASE_" + ([string]$Lock.identity.tool).ToUpperInvariant() + "_IDENTITY_CHANGED") }
}

function ConvertTo-NativeArgument([string]$Value) {
    if ($null -eq $Value) { $Value = "" }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($slashes * 2) + 1)))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) { [void]$builder.Append(('\' * $slashes)); $slashes = 0 }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) { [void]$builder.Append(('\' * ($slashes * 2))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Set-SanitizedProcessEnvironment(
    [Diagnostics.ProcessStartInfo]$Start,
    [string]$Tool,
    [string]$Executable,
    [string]$TrustedGitApplication,
    [string]$LocalAppDataOverride = $null
) {
    $system = [Environment]::SystemDirectory
    $windows = [IO.Path]::GetDirectoryName($system)
    $profile = Get-KnownFolderPath ([Environment+SpecialFolder]::UserProfile) "JOBFLOW_RELEASE_ENVIRONMENT_INVALID"
    $knownLocal = Get-KnownFolderPath ([Environment+SpecialFolder]::LocalApplicationData) "JOBFLOW_RELEASE_ENVIRONMENT_INVALID"
    $local = if ([string]::IsNullOrWhiteSpace($LocalAppDataOverride)) {
        $knownLocal
    }
    else {
        Assert-NoReparsePath $LocalAppDataOverride "JOBFLOW_RELEASE_ENVIRONMENT_INVALID" -MustExist
    }
    $roaming = Get-KnownFolderPath ([Environment+SpecialFolder]::ApplicationData) "JOBFLOW_RELEASE_ENVIRONMENT_INVALID"
    $programData = Get-KnownFolderPath ([Environment+SpecialFolder]::CommonApplicationData) "JOBFLOW_RELEASE_ENVIRONMENT_INVALID"
    $temporary = Join-Path $knownLocal "Temp"
    $paths = New-Object Collections.Generic.List[string]
    $paths.Add([IO.Path]::GetDirectoryName($Executable))
    $paths.Add($system)
    $paths.Add($windows)
    $paths.Add((Join-Path $system "WindowsPowerShell\v1.0"))
    # Python and PowerShell must never inherit Git's unbound mingw/usr DLL and
    # helper closure. Git receives its own narrowly scoped environment only.
    $gitExecutable = if ($Tool -ceq "git") { $Executable } else { $null }
    if (-not [string]::IsNullOrWhiteSpace($gitExecutable)) {
        Assert-NoReparsePath $gitExecutable "JOBFLOW_RELEASE_GIT_NOT_TRUSTED" -MustExist | Out-Null
        $paths.Add([IO.Path]::GetDirectoryName($gitExecutable))
        $gitRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($gitExecutable)))
        $paths.Add((Join-Path $gitRoot "mingw64\bin"))
        $paths.Add((Join-Path $gitRoot "usr\bin"))
    }
    $Start.EnvironmentVariables.Clear()
    foreach ($entry in @{
        SystemRoot = $windows
        WINDIR = $windows
        COMSPEC = (Join-Path $system "cmd.exe")
        USERPROFILE = $profile
        LOCALAPPDATA = $local
        APPDATA = $roaming
        PROGRAMDATA = $programData
        TEMP = $temporary
        TMP = $temporary
        PATH = (($paths | Where-Object { [IO.Directory]::Exists($_) } | Select-Object -Unique) -join [IO.Path]::PathSeparator)
    }.GetEnumerator()) { $Start.EnvironmentVariables[[string]$entry.Key] = [string]$entry.Value }
    if (-not [string]::IsNullOrWhiteSpace($gitExecutable)) {
        $Start.EnvironmentVariables["GIT_CONFIG_NOSYSTEM"] = "1"
        $Start.EnvironmentVariables["GIT_CONFIG_GLOBAL"] = "NUL"
        $Start.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0"
        $Start.EnvironmentVariables["GIT_OPTIONAL_LOCKS"] = "0"
        $Start.EnvironmentVariables["LC_ALL"] = "C"
    }
}

function Invoke-SanitizedTextCommand(
    [string]$Tool,
    [string]$Executable,
    [string[]]$Arguments,
    [string]$WorkingDirectory,
    [string]$TrustedGitApplication,
    [string]$LocalAppDataOverride = $null
) {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join " ")
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    Set-SanitizedProcessEnvironment $start $Tool $Executable $TrustedGitApplication $LocalAppDataOverride
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_RELEASE_COMMAND_FAILED" }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        return [pscustomobject]@{ exit_code = $process.ExitCode; stdout = $stdout; stderr = $stderr }
    }
    finally { $process.Dispose() }
}

function Invoke-IsolatedPythonModule(
    [string]$PythonApplication,
    [string]$GitApplication,
    [string]$Module,
    [string[]]$Arguments
) {
    if ($Module -notmatch '^jobops\.[a-z_]+$') { throw "JOBFLOW_RELEASE_PYTHON_MODULE_INVALID" }
    if (
        [string]::IsNullOrWhiteSpace([string]$script:isolatedPythonSource) -or
        [string]::IsNullOrWhiteSpace([string]$script:isolatedPythonProjectRoot) -or
        $null -eq $script:protectedStagingContext
    ) { throw "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID" }
    Assert-ProtectedStagingContext $script:protectedStagingContext "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
    $source = [IO.Path]::GetFullPath([string]$script:isolatedPythonSource)
    $moduleProjectRoot = [IO.Path]::GetFullPath([string]$script:isolatedPythonProjectRoot)
    Assert-ProjectPath $source "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
    Assert-ProjectPath $moduleProjectRoot "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
    Assert-NoReparsePath $source "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID" -MustExist | Out-Null
    Assert-NoReparsePath $moduleProjectRoot "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID" -MustExist | Out-Null
    $bootstrap = "import runpy,sys; source=sys.argv[1]; module=sys.argv[2]; sys.path.insert(0,source); sys.argv=sys.argv[2:]; runpy.run_module(module,run_name='__main__',alter_sys=True)"
    $pythonArguments = @(
        "-I", "-P", "-S", "-B", "-X", "utf8",
        "-c", $bootstrap, $source, $Module
    ) + @($Arguments)
    return Invoke-SanitizedTextCommand "python" $PythonApplication $pythonArguments $moduleProjectRoot $null
}

function Invoke-SanitizedGit([string]$GitApplication, [string[]]$Arguments) {
    $gitDirectory = Join-Path $projectRoot ".git"
    $base = @(
        ("--git-dir=" + $gitDirectory),
        ("--work-tree=" + $projectRoot),
        "--no-pager",
        "-c", "core.hooksPath=NUL",
        "-c", "core.fsmonitor=false"
    )
    return Invoke-SanitizedTextCommand "git" $GitApplication ($base + @($Arguments)) $projectRoot $null
}

function Assert-ProjectPath([string]$Path, [string]$Code) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw $Code }
    $cursor = $absolute
    while ($cursor -and $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $Code }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Test-JsonInteger([object]$Value, [long]$Expected) {
    return (($Value -is [int]) -or ($Value -is [long])) -and ([long]$Value -eq $Expected)
}

function Test-JsonIntegerAtLeast([object]$Value, [long]$Minimum) {
    return (($Value -is [int]) -or ($Value -is [long])) -and ([long]$Value -ge $Minimum)
}

function Test-JsonStringArray([object]$Value, [string[]]$Expected) {
    if (-not ($Value -is [Array]) -or $Value.Count -ne $Expected.Count) { return $false }
    for ($index = 0; $index -lt $Expected.Count; $index++) {
        if (-not ($Value[$index] -is [string]) -or $Value[$index] -cne $Expected[$index]) { return $false }
    }
    return $true
}

function Test-JsonObjectKeys([object]$Value, [string[]]$Expected) {
    if ($null -eq $Value -or $Value -is [string] -or $Value -is [Array]) { return $false }
    $actual = @($Value.PSObject.Properties | ForEach-Object { [string]$_.Name })
    if ($actual.Count -ne $Expected.Count) { return $false }
    foreach ($name in $Expected) {
        if (-not ($actual -ccontains $name)) { return $false }
    }
    return $true
}

function Get-ProjectVersion([object]$PyprojectLock) {
    if ($null -eq $PyprojectLock -or $null -eq $PyprojectLock.stream) {
        throw "JOBFLOW_RELEASE_PROJECT_VERSION_INVALID"
    }
    $expectedPath = [IO.Path]::GetFullPath((Join-Path $projectRoot "pyproject.toml"))
    if ([string]$PyprojectLock.path -cne $expectedPath) {
        throw "JOBFLOW_RELEASE_PROJECT_VERSION_INVALID"
    }
    $insideProject = $false
    $versions = @()
    try {
        $PyprojectLock.stream.Position = 0
        $reader = [IO.StreamReader]::new(
            $PyprojectLock.stream,
            ([Text.UTF8Encoding]::new($false, $true)),
            $true,
            1024,
            $true
        )
        try {
            while (-not $reader.EndOfStream) {
                $trimmed = $reader.ReadLine().Trim()
                if ($trimmed -match '^\[([^]]+)\]$') {
                    $insideProject = $Matches[1] -ceq "project"
                    continue
                }
                if ($insideProject -and $trimmed -match '^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*(?:#.*)?$') {
                    $versions += $Matches[1]
                }
            }
        }
        finally {
            $reader.Dispose()
            $PyprojectLock.stream.Position = 0
        }
    }
    catch { throw "JOBFLOW_RELEASE_PROJECT_VERSION_INVALID" }
    if ($versions.Count -ne 1) { throw "JOBFLOW_RELEASE_PROJECT_VERSION_INVALID" }
    return [string]$versions[0]
}

function Assert-ReleaseCandidate([object]$Value) {
    $topLevelKeys = @(
        "schema_version", "status", "version", "commit", "artifact_name", "artifact_sha256",
        "artifact_bytes", "reproducible_builds", "archive", "source_smoke",
        "repository_content_status", "author_identity_status", "uploaded",
        "external_network_actions", "real_external_actions"
    )
    $archiveKeys = @("status", "file_count", "finding_count", "findings")
    $sourceSmokeKeys = @(
        "status", "binding", "supported_locales", "offline_discovery", "offline_candidates",
        "snapshot_persisted", "candidate_queue_mutations", "private_values_emitted",
        "external_network_actions", "real_external_actions", "private_store_health",
        "private_ciphertext_files", "loopback_requests", "security_headers",
        "project_state_isolated", "local_app_data_isolated"
    )
    $expectedArtifact = $null
    if (
        $null -ne $Value -and
        ($Value.version -is [string]) -and $Value.version -match '^[0-9]+\.[0-9]+\.[0-9]+$' -and
        ($Value.commit -is [string]) -and $Value.commit -match '^[0-9a-f]{40}$'
    ) {
        $expectedArtifact = "JobFlow-v$($Value.version)-$($Value.commit.Substring(0, 12))-source.zip"
    }
    if (
        $null -eq $Value -or
        -not (Test-JsonObjectKeys $Value $topLevelKeys) -or
        -not (Test-JsonInteger $Value.schema_version 1) -or
        -not ($Value.status -is [string]) -or $Value.status -cne "RELEASE_CANDIDATE_BUILT" -or
        -not ($Value.uploaded -is [bool]) -or $Value.uploaded -ne $false -or
        -not ($Value.version -is [string]) -or $Value.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        -not ($Value.commit -is [string]) -or $Value.commit -notmatch '^[0-9a-f]{40}$' -or
        $null -eq $expectedArtifact -or
        -not ($Value.artifact_name -is [string]) -or $Value.artifact_name -cne $expectedArtifact -or
        -not ($Value.artifact_sha256 -is [string]) -or $Value.artifact_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
        -not (($Value.artifact_bytes -is [int]) -or ($Value.artifact_bytes -is [long])) -or
        [long]$Value.artifact_bytes -lt 1 -or
        -not (Test-JsonIntegerAtLeast $Value.reproducible_builds 2) -or
        $null -eq $Value.archive -or
        -not (Test-JsonObjectKeys $Value.archive $archiveKeys) -or
        -not ($Value.archive.status -is [string]) -or $Value.archive.status -cne "PASS" -or
        -not (Test-JsonIntegerAtLeast $Value.archive.file_count 1) -or
        -not (Test-JsonInteger $Value.archive.finding_count 0) -or
        -not ($Value.archive.findings -is [Array]) -or $Value.archive.findings.Count -ne 0 -or
        $null -eq $Value.source_smoke -or
        -not (Test-JsonObjectKeys $Value.source_smoke $sourceSmokeKeys) -or
        -not ($Value.source_smoke.status -is [string]) -or $Value.source_smoke.status -cne "PASS" -or
        -not ($Value.source_smoke.binding -is [string]) -or $Value.source_smoke.binding -cne "127.0.0.1" -or
        -not (Test-JsonStringArray $Value.source_smoke.supported_locales @("zh", "en")) -or
        -not ($Value.source_smoke.offline_discovery -is [string]) -or $Value.source_smoke.offline_discovery -cne "PASS" -or
        -not (Test-JsonIntegerAtLeast $Value.source_smoke.offline_candidates 1) -or
        -not ($Value.source_smoke.snapshot_persisted -is [bool]) -or $Value.source_smoke.snapshot_persisted -ne $false -or
        -not (Test-JsonInteger $Value.source_smoke.candidate_queue_mutations 0) -or
        -not (Test-JsonInteger $Value.source_smoke.private_values_emitted 0) -or
        -not (Test-JsonInteger $Value.source_smoke.external_network_actions 0) -or
        -not (Test-JsonInteger $Value.source_smoke.real_external_actions 0) -or
        -not ($Value.source_smoke.private_store_health -is [string]) -or $Value.source_smoke.private_store_health -cne "PASS" -or
        -not (Test-JsonInteger $Value.source_smoke.private_ciphertext_files 0) -or
        -not (Test-JsonIntegerAtLeast $Value.source_smoke.loopback_requests 1) -or
        -not ($Value.source_smoke.security_headers -is [string]) -or $Value.source_smoke.security_headers -cne "PASS" -or
        -not ($Value.source_smoke.project_state_isolated -is [bool]) -or $Value.source_smoke.project_state_isolated -ne $true -or
        -not ($Value.source_smoke.local_app_data_isolated -is [bool]) -or $Value.source_smoke.local_app_data_isolated -ne $true -or
        -not ($Value.repository_content_status -is [string]) -or $Value.repository_content_status -cne "PASS" -or
        -not ($Value.author_identity_status -is [string]) -or $Value.author_identity_status -cne "PASS" -or
        -not (Test-JsonInteger $Value.external_network_actions 0) -or
        -not (Test-JsonInteger $Value.real_external_actions 0)
    ) { throw "JOBFLOW_RELEASE_CANDIDATE_REPORT_INVALID" }
}

function Initialize-JobFlowReleaseFileIdentityApi {
    if ($null -ne ("JobFlowReleaseNative.FileIdentity" -as [type])) { return }
    Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
namespace JobFlowReleaseNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct FileTime { public uint Low; public uint High; }
    [StructLayout(LayoutKind.Sequential)]
    public struct FileIdentity {
        public uint Attributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint SizeHigh;
        public uint SizeLow;
        public uint LinkCount;
        public uint FileIndexHigh;
        public uint FileIndexLow;
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
    [StructLayout(LayoutKind.Sequential, Pack = 1)] internal struct FileDispositionInfo {
        public byte DeleteFile;
    }
    public static class FileIdentityApi {
        private static bool IsSafeLeafName(string name) {
            if (String.IsNullOrEmpty(name) || name == "." || name == ".." ||
                name.Length > 255 || name.IndexOfAny(System.IO.Path.GetInvalidFileNameChars()) >= 0 ||
                !String.Equals(name, name.TrimEnd(' ', '.'), StringComparison.Ordinal)) {
                return false;
            }
            string stem = name.Split('.')[0].ToUpperInvariant();
            if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" ||
                stem == "CLOCK$") {
                return false;
            }
            if (stem.Length == 4 && (stem.StartsWith("COM", StringComparison.Ordinal) ||
                stem.StartsWith("LPT", StringComparison.Ordinal)) && stem[3] >= '1' && stem[3] <= '9') {
                return false;
            }
            return true;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern SafeFileHandle CreateFileW(
            string path, uint desiredAccess, uint shareMode, System.IntPtr securityAttributes,
            uint creationDisposition, uint flagsAndAttributes, System.IntPtr templateFile
        );
        [DllImport("kernel32.dll", SetLastError=true)]
        public static extern bool GetFileInformationByHandle(
            SafeFileHandle handle,
            out FileIdentity information
        );
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        public static extern uint GetFinalPathNameByHandleW(
            SafeFileHandle handle, StringBuilder path, uint capacity, uint flags
        );
        [DllImport("kernel32.dll", SetLastError=true)]
        private static extern bool SetFileInformationByHandle(
            SafeFileHandle handle, int informationClass,
            ref FileDispositionInfo information, uint bufferSize
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
        private static SafeFileHandle OpenRelative(
            SafeFileHandle parent, string name, uint desiredAccess, uint shareAccess,
            uint fileAttributes, uint createDisposition, uint createOptions
        ) {
            if (parent == null || parent.IsInvalid || parent.IsClosed || !IsSafeLeafName(name) ||
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
                    fileAttributes, shareAccess, createDisposition, createOptions, System.IntPtr.Zero, 0
                );
                if (status != 0 || raw == System.IntPtr.Zero || raw == new System.IntPtr(-1)) {
                    if (raw != System.IntPtr.Zero && raw != new System.IntPtr(-1)) {
                        using (SafeFileHandle failed = new SafeFileHandle(raw, true)) { }
                    }
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
            // SYNCHRONIZE | FILE_READ/WRITE_ATTRIBUTES | FILE_READ/WRITE_DATA.
            // DELETE is acquired only by the short-lived cleanup handle.
            return OpenRelative(parent, name, 0x00100183, shareAccess, 0x80, 2, 0x60);
        }
        public static SafeFileHandle CreateNewDirectoryRelative(SafeFileHandle parent, string name) {
            // SYNCHRONIZE | FILE_READ/WRITE_ATTRIBUTES.  DELETE is deferred.
            return OpenRelative(parent, name, 0x00100081, 0x3, 0x10, 2, 0x21);
        }
        public static SafeFileHandle OpenFileRelative(SafeFileHandle parent, string name, uint shareAccess) {
            return OpenRelative(parent, name, 0x00100081, shareAccess, 0x80, 1, 0x00200060);
        }
        public static SafeFileHandle OpenDirectoryRelative(SafeFileHandle parent, string name, uint shareAccess) {
            return OpenRelative(parent, name, 0x00100080, shareAccess, 0x10, 1, 0x00200021);
        }
        public static SafeFileHandle OpenDeleteFileRelative(SafeFileHandle parent, string name) {
            return OpenRelative(parent, name, 0x00110081, 0x1, 0x80, 1, 0x00200060);
        }
        public static SafeFileHandle OpenDeleteDirectoryRelative(SafeFileHandle parent, string name) {
            return OpenRelative(parent, name, 0x00110080, 0x3, 0x10, 1, 0x00200021);
        }
        public static bool MarkDelete(SafeFileHandle handle) {
            FileDispositionInfo information = new FileDispositionInfo { DeleteFile = 1 };
            return SetFileInformationByHandle(
                handle, 4, ref information, (uint)Marshal.SizeOf(typeof(FileDispositionInfo))
            );
        }
    }
}
"@ -ErrorAction Stop
}

function Get-ReleaseHandleIdentity(
    [Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
    [string]$Code
) {
    Initialize-JobFlowReleaseFileIdentityApi
    $information = New-Object JobFlowReleaseNative.FileIdentity
    if (-not [JobFlowReleaseNative.FileIdentityApi]::GetFileInformationByHandle($Handle, [ref]$information)) {
        throw $Code
    }
    return $information
}

function Get-ReleaseFinalPathFromHandle(
    [Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
    [string]$Code
) {
    Initialize-JobFlowReleaseFileIdentityApi
    $builder = [Text.StringBuilder]::new(32768)
    $length = [JobFlowReleaseNative.FileIdentityApi]::GetFinalPathNameByHandleW(
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

function Open-StableReleaseDirectoryHandle([string]$Path, [string]$Code) {
    Initialize-JobFlowReleaseFileIdentityApi
    $absolute = [IO.Path]::GetFullPath($Path)
    # Omit FILE_SHARE_DELETE so this existing ancestor cannot be renamed,
    # deleted, or replaced by a junction while the context is retained.
    $handle = [JobFlowReleaseNative.FileIdentityApi]::CreateFileW(
        $absolute, 0x80, 0x3, [IntPtr]::Zero, 3,
        (0x02000000 -bor 0x00200000), [IntPtr]::Zero
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $identity = Get-ReleaseHandleIdentity $handle $Code
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0
        ) { throw $Code }
        $final = Get-ReleaseFinalPathFromHandle $handle $Code
        if (-not $final.Equals($absolute, [StringComparison]::OrdinalIgnoreCase)) { throw $Code }
        return [pscustomobject]@{
            Path = $absolute; Handle = $handle; CanDelete = $false
            Volume = [uint32]$identity.VolumeSerialNumber
            IndexHigh = [uint32]$identity.FileIndexHigh
            IndexLow = [uint32]$identity.FileIndexLow
        }
    }
    catch { $handle.Dispose(); throw }
}

function Assert-StableReleaseDirectoryLocks([object[]]$Locks, [string]$Code) {
    if ($null -eq $Locks -or @($Locks).Count -lt 1) { throw $Code }
    foreach ($lock in @($Locks)) {
        if ($null -eq $lock.Handle -or $lock.Handle.IsInvalid -or $lock.Handle.IsClosed) { throw $Code }
        $identity = Get-ReleaseHandleIdentity $lock.Handle $Code
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$identity.VolumeSerialNumber -ne [uint32]$lock.Volume -or
            [uint32]$identity.FileIndexHigh -ne [uint32]$lock.IndexHigh -or
            [uint32]$identity.FileIndexLow -ne [uint32]$lock.IndexLow -or
            -not (Get-ReleaseFinalPathFromHandle $lock.Handle $Code).Equals(
                [string]$lock.Path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
    }
}

function Open-StableReleaseDirectoryChain([string]$Path, [string]$Code) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($absolute)
    if ([string]::IsNullOrWhiteSpace($root)) { throw $Code }
    $paths = [Collections.Generic.List[string]]::new()
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
    $locks = [Collections.Generic.List[object]]::new()
    try {
        foreach ($candidate in $paths) {
            if (-not [IO.Directory]::Exists($candidate)) { throw $Code }
            [void]$locks.Add((Open-StableReleaseDirectoryHandle $candidate $Code))
        }
        Assert-StableReleaseDirectoryLocks @($locks.ToArray()) $Code
        return [pscustomobject]@{ Path = $absolute; Locks = @($locks.ToArray()) }
    }
    catch {
        foreach ($lock in @($locks)) { if ($null -ne $lock.Handle) { $lock.Handle.Dispose() } }
        throw
    }
}

function New-StableReleaseChildDirectory([object]$ParentLock, [string]$Name, [string]$Code) {
    if (
        $null -eq $ParentLock -or $null -eq $ParentLock.Handle -or
        [string]::IsNullOrWhiteSpace($Name) -or $Name -match '[\\/:]'
    ) { throw $Code }
    $expected = [IO.Path]::GetFullPath((Join-Path ([string]$ParentLock.Path) $Name))
    $creator = [JobFlowReleaseNative.FileIdentityApi]::CreateNewDirectoryRelative($ParentLock.Handle, $Name)
    if ($null -eq $creator -or $creator.IsInvalid) {
        if ($null -ne $creator) { $creator.Dispose() }
        throw $Code
    }
    $transition = $null
    $final = $null
    try {
        $identity = Get-ReleaseHandleIdentity $creator $Code
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not (Get-ReleaseFinalPathFromHandle $creator $Code).Equals(
                $expected, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
        # A share-all read-only transition keeps the identity continuously
        # open while the creator's DELETE/write rights are dropped.
        $transition = [JobFlowReleaseNative.FileIdentityApi]::OpenDirectoryRelative(
            $ParentLock.Handle, $Name, 7
        )
        if ($null -eq $transition -or $transition.IsInvalid) { throw $Code }
        $transitionIdentity = Get-ReleaseHandleIdentity $transition $Code
        $creator.Dispose(); $creator = $null
        $final = [JobFlowReleaseNative.FileIdentityApi]::OpenDirectoryRelative(
            $ParentLock.Handle, $Name, 3
        )
        if ($null -eq $final -or $final.IsInvalid) { throw $Code }
        $finalIdentity = Get-ReleaseHandleIdentity $final $Code
        if (
            [uint32]$transitionIdentity.VolumeSerialNumber -ne [uint32]$identity.VolumeSerialNumber -or
            [uint32]$transitionIdentity.FileIndexHigh -ne [uint32]$identity.FileIndexHigh -or
            [uint32]$transitionIdentity.FileIndexLow -ne [uint32]$identity.FileIndexLow -or
            [uint32]$finalIdentity.VolumeSerialNumber -ne [uint32]$identity.VolumeSerialNumber -or
            [uint32]$finalIdentity.FileIndexHigh -ne [uint32]$identity.FileIndexHigh -or
            [uint32]$finalIdentity.FileIndexLow -ne [uint32]$identity.FileIndexLow -or
            -not (Get-ReleaseFinalPathFromHandle $final $Code).Equals(
                $expected, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
        $transition.Dispose(); $transition = $null
        return [pscustomobject]@{
            Path = $expected; Handle = $final; CanDelete = $true
            ParentLock = $ParentLock; Name = $Name
            Volume = [uint32]$finalIdentity.VolumeSerialNumber
            IndexHigh = [uint32]$finalIdentity.FileIndexHigh
            IndexLow = [uint32]$finalIdentity.FileIndexLow
        }
    }
    catch {
        # The child was created through the retained parent handle.  If any
        # post-create validation fails, retain a same-identity transition
        # handle while dropping the creator/final handles, then delete only
        # that relative child.  Never fall back to the rendered path.
        $cleanupTransition = $null
        $deleteHandle = $null
        try {
            $anchor = if ($null -ne $final -and -not $final.IsInvalid -and -not $final.IsClosed) {
                $final
            }
            elseif ($null -ne $transition -and -not $transition.IsInvalid -and -not $transition.IsClosed) {
                $transition
            }
            else { $creator }
            if ($null -eq $anchor -or $anchor.IsInvalid -or $anchor.IsClosed) { throw $Code }
            $anchorIdentity = Get-ReleaseHandleIdentity $anchor $Code
            if (
                ([uint32]$anchorIdentity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
                ([uint32]$anchorIdentity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
                -not (Get-ReleaseFinalPathFromHandle $anchor $Code).Equals(
                    $expected, [StringComparison]::OrdinalIgnoreCase
                )
            ) { throw $Code }
            $cleanupTransition = [JobFlowReleaseNative.FileIdentityApi]::OpenDirectoryRelative(
                $ParentLock.Handle, $Name, 7
            )
            if ($null -eq $cleanupTransition -or $cleanupTransition.IsInvalid) { throw $Code }
            $cleanupIdentity = Get-ReleaseHandleIdentity $cleanupTransition $Code
            if (
                [uint32]$cleanupIdentity.VolumeSerialNumber -ne [uint32]$anchorIdentity.VolumeSerialNumber -or
                [uint32]$cleanupIdentity.FileIndexHigh -ne [uint32]$anchorIdentity.FileIndexHigh -or
                [uint32]$cleanupIdentity.FileIndexLow -ne [uint32]$anchorIdentity.FileIndexLow -or
                -not (Get-ReleaseFinalPathFromHandle $cleanupTransition $Code).Equals(
                    $expected, [StringComparison]::OrdinalIgnoreCase
                )
            ) { throw $Code }
            if ($null -ne $creator) { $creator.Dispose(); $creator = $null }
            if ($null -ne $transition) { $transition.Dispose(); $transition = $null }
            if ($null -ne $final) { $final.Dispose(); $final = $null }
            $deleteHandle = [JobFlowReleaseNative.FileIdentityApi]::OpenDeleteDirectoryRelative(
                $ParentLock.Handle, $Name
            )
            if ($null -eq $deleteHandle -or $deleteHandle.IsInvalid) { throw $Code }
            $deleteIdentity = Get-ReleaseHandleIdentity $deleteHandle $Code
            if (
                [uint32]$deleteIdentity.VolumeSerialNumber -ne [uint32]$anchorIdentity.VolumeSerialNumber -or
                [uint32]$deleteIdentity.FileIndexHigh -ne [uint32]$anchorIdentity.FileIndexHigh -or
                [uint32]$deleteIdentity.FileIndexLow -ne [uint32]$anchorIdentity.FileIndexLow -or
                -not (Get-ReleaseFinalPathFromHandle $deleteHandle $Code).Equals(
                    $expected, [StringComparison]::OrdinalIgnoreCase
                )
            ) { throw $Code }
            Mark-ReleaseHandleDelete $deleteHandle $Code
        }
        catch { }
        finally {
            if ($null -ne $deleteHandle) { $deleteHandle.Dispose() }
            if ($null -ne $cleanupTransition) { $cleanupTransition.Dispose() }
            if ($null -ne $creator) { $creator.Dispose() }
            if ($null -ne $transition) { $transition.Dispose() }
            if ($null -ne $final) { $final.Dispose() }
        }
        throw
    }
}

function Open-NewReleaseFileRelative(
    [object]$ParentLock,
    [string]$Name,
    [uint32]$ShareAccess,
    [string]$Code
) {
    if (
        $null -eq $ParentLock -or $null -eq $ParentLock.Handle -or
        [string]::IsNullOrWhiteSpace($Name) -or $Name -match '[\\/:]'
    ) { throw $Code }
    $handle = [JobFlowReleaseNative.FileIdentityApi]::CreateNewFileRelative(
        $ParentLock.Handle, $Name, $ShareAccess
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    $stream = $null
    try {
        $stream = [IO.FileStream]::new($handle, [IO.FileAccess]::ReadWrite)
        $handle = $null
        $expected = [IO.Path]::GetFullPath((Join-Path ([string]$ParentLock.Path) $Name))
        $identity = Get-OpenOutputFileIdentity $stream $Code
        if (
            [long]$identity.link_count -ne 1 -or
            -not (Get-ReleaseFinalPathFromHandle $stream.SafeFileHandle $Code).Equals(
                $expected, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
        return [pscustomobject]@{
            path = $expected; stream = $stream; ParentLock = $ParentLock; Name = $Name
        }
    }
    catch {
        if ($null -ne $handle) { $handle.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        throw
    }
}

function Open-ReleaseRelativeReadStream(
    [object]$ParentLock,
    [string]$Name,
    [uint32]$ShareAccess,
    [string]$Code
) {
    $handle = [JobFlowReleaseNative.FileIdentityApi]::OpenFileRelative(
        $ParentLock.Handle, $Name, $ShareAccess
    )
    if ($null -eq $handle -or $handle.IsInvalid) {
        if ($null -ne $handle) { $handle.Dispose() }
        throw $Code
    }
    try {
        $stream = [IO.FileStream]::new($handle, [IO.FileAccess]::Read)
        $handle = $null
        return $stream
    }
    catch {
        if ($null -ne $handle) { $handle.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        throw
    }
}

function Convert-NewReleaseFileToReadLock(
    [object]$Output,
    [long]$MaximumBytes,
    [string]$Code
) {
    if (
        $null -eq $Output -or $null -eq $Output.stream -or
        $null -eq $Output.ParentLock -or [string]::IsNullOrWhiteSpace([string]$Output.Name)
    ) { throw $Code }
    $creator = $Output.stream
    $creatorIdentity = $null
    $length = $null
    $sha256 = $null
    $transition = $null
    $final = $null
    try {
        $creator.Flush($true)
        $creatorIdentity = Get-OpenOutputFileIdentity $creator $Code
        $length = [long]$creator.Length
        $sha256 = "sha256:" + (Get-StreamSha256 $creator)
        if ($length -lt 1 -or $length -gt $MaximumBytes -or [long]$creatorIdentity.link_count -ne 1) {
            throw $Code
        }
        $transition = Open-ReleaseRelativeReadStream $Output.ParentLock $Output.Name 7 $Code
        $transitionIdentity = Get-OpenOutputFileIdentity $transition $Code
        if (-not (Test-SameOutputFileIdentity $creatorIdentity $transitionIdentity)) { throw $Code }
        $creator.Dispose(); $creator = $null; $Output.stream = $null
        $final = Open-ReleaseRelativeReadStream $Output.ParentLock $Output.Name 1 $Code
        $finalIdentity = Get-OpenOutputFileIdentity $final $Code
        if (
            -not (Test-SameOutputFileIdentity $creatorIdentity $finalIdentity) -or
            [long]$final.Length -ne $length -or
            ("sha256:" + (Get-StreamSha256 $final)) -cne $sha256 -or
            -not (Get-ReleaseFinalPathFromHandle $final.SafeFileHandle $Code).Equals(
                [string]$Output.path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $Code }
        $transition.Dispose(); $transition = $null
        $lock = [pscustomobject]@{
            path = [string]$Output.path; stream = $final
            ParentLock = $Output.ParentLock; Name = [string]$Output.Name
            volume = [long]$finalIdentity.volume
            file_index = [uint64]$finalIdentity.file_index
            length = $length; sha256 = $sha256; code = $Code
            maximum_bytes = $MaximumBytes
        }
        $final = $null
        return $lock
    }
    catch {
        if ($null -ne $creator) {
            try { Remove-NewReleaseFileOutput $Output } catch { }
            $creator = $null
        }
        elseif ($null -ne $creatorIdentity -and $null -ne $length -and $null -ne $sha256) {
            $anchor = if ($null -ne $final) { $final } else { $transition }
            if ($null -ne $anchor) {
                $cleanupLock = [pscustomobject]@{
                    path = [string]$Output.path; stream = $anchor
                    ParentLock = $Output.ParentLock; Name = [string]$Output.Name
                    volume = [long]$creatorIdentity.volume
                    file_index = [uint64]$creatorIdentity.file_index
                    length = [long]$length; sha256 = [string]$sha256
                    code = $Code; maximum_bytes = $MaximumBytes
                }
                if ($anchor -eq $final) { $final = $null } else { $transition = $null }
                try { Remove-ProtectedStagedFileLock $cleanupLock } catch { }
            }
        }
        if ($null -ne $transition) { $transition.Dispose() }
        if ($null -ne $final) { $final.Dispose() }
        throw
    }
}

function Mark-ReleaseHandleDelete(
    [Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
    [string]$Code
) {
    if ($null -eq $Handle -or $Handle.IsInvalid -or $Handle.IsClosed) { throw $Code }
    if (-not [JobFlowReleaseNative.FileIdentityApi]::MarkDelete($Handle)) { throw $Code }
}

function Remove-NewReleaseFileOutput([object]$Output) {
    $code = "JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED"
    if ($null -eq $Output -or $null -eq $Output.stream) { return }
    if (
        $null -eq $Output.ParentLock -or $null -eq $Output.ParentLock.Handle -or
        $Output.ParentLock.Handle.IsClosed -or
        [string]::IsNullOrWhiteSpace([string]$Output.Name)
    ) { throw $code }
    $creator = $Output.stream
    $transition = $null
    $deleteHandle = $null
    try {
        $creator.Flush($true)
        $creatorIdentity = Get-OpenOutputFileIdentity $creator $code
        if (
            [long]$creatorIdentity.link_count -ne 1 -or
            -not (Get-ReleaseFinalPathFromHandle $creator.SafeFileHandle $code).Equals(
                [string]$Output.path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $code }
        $transition = Open-ReleaseRelativeReadStream $Output.ParentLock $Output.Name 7 $code
        $transitionIdentity = Get-OpenOutputFileIdentity $transition $code
        if (
            -not (Test-SameOutputFileIdentity $creatorIdentity $transitionIdentity) -or
            -not (Get-ReleaseFinalPathFromHandle $transition.SafeFileHandle $code).Equals(
                [string]$Output.path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $code }
        $creator.Dispose(); $creator = $null; $Output.stream = $null
        $deleteHandle = [JobFlowReleaseNative.FileIdentityApi]::OpenDeleteFileRelative(
            $Output.ParentLock.Handle, [string]$Output.Name
        )
        if ($null -eq $deleteHandle -or $deleteHandle.IsInvalid) { throw $code }
        $deleteIdentity = Get-ReleaseHandleIdentity $deleteHandle $code
        if (
            ([uint32]$deleteIdentity.Attributes -band [uint32][IO.FileAttributes]::Directory) -ne 0 -or
            ([uint32]$deleteIdentity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$deleteIdentity.LinkCount -ne 1 -or
            [long]$deleteIdentity.VolumeSerialNumber -ne [long]$creatorIdentity.volume -or
            (Get-ReleaseHandleFileIndex $deleteIdentity) -ne [uint64]$creatorIdentity.file_index -or
            -not (Get-ReleaseFinalPathFromHandle $deleteHandle $code).Equals(
                [string]$Output.path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $code }
        Mark-ReleaseHandleDelete $deleteHandle $code
    }
    finally {
        if ($null -ne $deleteHandle) { $deleteHandle.Dispose() }
        if ($null -ne $transition) { $transition.Dispose() }
        if ($null -ne $creator) { try { $creator.Dispose() } catch { } }
        $Output.stream = $null
    }
}

function Move-OutputFileReplaceExisting([string]$Source, [string]$Destination, [string]$Code) {
    Initialize-JobFlowReleaseFileIdentityApi
    # MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH.  Both paths are in
    # the same verified dist directory, so this is one durable rename.
    if (-not [JobFlowReleaseNative.FileIdentityApi]::MoveFileEx($Source, $Destination, 0x9)) {
        throw $Code
    }
}

function Get-OutputFileIdentity([string]$Path, [string]$Code) {
    Initialize-JobFlowReleaseFileIdentityApi
    $stream = $null
    try {
        $stream = [IO.File]::Open(
            [IO.Path]::GetFullPath($Path),
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        return Get-OpenOutputFileIdentity $stream $Code
    }
    catch { throw $Code }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
}

function Get-OpenOutputFileIdentity([IO.FileStream]$Stream, [string]$Code) {
    Initialize-JobFlowReleaseFileIdentityApi
    $information = New-Object JobFlowReleaseNative.FileIdentity
    if (-not [JobFlowReleaseNative.FileIdentityApi]::GetFileInformationByHandle(
        $Stream.SafeFileHandle,
        [ref]$information
    )) { throw $Code }
    return [pscustomobject]@{
        link_count = [long]$information.LinkCount
        volume = [long]$information.VolumeSerialNumber
        file_index = (([uint64]$information.FileIndexHigh -shl 32) -bor [uint64]$information.FileIndexLow)
    }
}

function Test-SameOutputFileIdentity([object]$Left, [object]$Right) {
    return (
        $null -ne $Left -and $null -ne $Right -and
        [long]$Left.volume -eq [long]$Right.volume -and
        [uint64]$Left.file_index -eq [uint64]$Right.file_index -and
        [long]$Left.link_count -eq 1 -and [long]$Right.link_count -eq 1
    )
}

function Assert-OrdinaryOutputLeaf([string]$Path, [string]$Code, [switch]$MustExist, [switch]$SingleLink) {
    $absolute = [IO.Path]::GetFullPath($Path)
    Assert-ProjectPath $absolute $Code
    if (-not (Test-Path -LiteralPath $absolute)) {
        if ($MustExist) { throw $Code }
        return
    }
    $item = Get-Item -LiteralPath $absolute -Force
    if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw $Code
    }
    if ($SingleLink -and (Get-OutputFileIdentity $absolute $Code).link_count -ne 1) { throw $Code }
}

function Enter-OutputTransactionLock([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    Assert-ProjectPath $absolute "JOBFLOW_RELEASE_OUTPUT_LOCK_UNTRUSTED"
    Assert-OrdinaryOutputLeaf $absolute "JOBFLOW_RELEASE_OUTPUT_LOCK_UNTRUSTED"
    $stream = $null
    try {
        $stream = [IO.File]::Open(
            $absolute,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::Read
        )
    }
    catch [IO.IOException] { throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_ACTIVE" }
    try {
        $item = Get-Item -LiteralPath $absolute -Force
        if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw "JOBFLOW_RELEASE_OUTPUT_LOCK_UNTRUSTED"
        }
        if ((Get-OpenOutputFileIdentity $stream "JOBFLOW_RELEASE_OUTPUT_LOCK_UNTRUSTED").link_count -ne 1) {
            throw "JOBFLOW_RELEASE_OUTPUT_LOCK_UNTRUSTED"
        }
        $stream.Flush($true)
        return $stream
    }
    catch {
        $stream.Dispose()
        throw
    }
}

function New-ExclusiveOutputFile([string]$Directory, [string]$Stem, [string]$Code) {
    Assert-ProjectPath $Directory "JOBFLOW_RELEASE_OUTPUT_PATH_UNTRUSTED"
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        $name = $Stem + "." + [Guid]::NewGuid().ToString("N") + ".tmp"
        $path = Join-Path $Directory $name
        Assert-ProjectPath $path "JOBFLOW_RELEASE_OUTPUT_PATH_UNTRUSTED"
        $stream = $null
        $parentLock = $null
        $leafName = $null
        $created = $null
        try {
            if (
                $null -ne $script:releaseDistContext -and
                [IO.Path]::GetFullPath($Directory).Equals(
                    [string]$script:releaseDistContext.Path,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) {
                Assert-StableReleaseDirectoryLocks @($script:releaseDistContext.Locks) $Code
                $parent = @($script:releaseDistContext.Locks)[@($script:releaseDistContext.Locks).Count - 1]
                $created = Open-NewReleaseFileRelative $parent $name 1 $Code
                $stream = $created.stream
                $path = [string]$created.path
                $parentLock = $created.ParentLock
                $leafName = [string]$created.Name
            }
            else {
                $stream = [IO.File]::Open(
                    $path,
                    [IO.FileMode]::CreateNew,
                    [IO.FileAccess]::ReadWrite,
                    [IO.FileShare]::Read
                )
            }
            if ((Get-OpenOutputFileIdentity $stream $Code).link_count -ne 1) { throw $Code }
            return [pscustomobject]@{
                path = $path
                stream = $stream
                ParentLock = $parentLock
                Name = $leafName
            }
        }
        catch [IO.IOException] {
            if ($null -ne $created -and $null -ne $created.stream) {
                try { Remove-NewReleaseFileOutput $created } catch { }
            }
            elseif ($null -ne $stream) { $stream.Dispose() }
        }
        catch {
            if ($null -ne $created -and $null -ne $created.stream) {
                try { Remove-NewReleaseFileOutput $created } catch { }
            }
            elseif ($null -ne $stream) { $stream.Dispose() }
            throw
        }
    }
    throw "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_FAILED"
}

function New-UniqueOutputPath([string]$Directory, [string]$Stem) {
    Assert-ProjectPath $Directory "JOBFLOW_RELEASE_OUTPUT_PATH_UNTRUSTED"
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        $path = Join-Path $Directory ($Stem + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
        Assert-ProjectPath $path "JOBFLOW_RELEASE_OUTPUT_PATH_UNTRUSTED"
        if (-not [IO.File]::Exists($path) -and -not [IO.Directory]::Exists($path)) { return $path }
    }
    throw "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_FAILED"
}

function Invoke-DeterministicGitArchive(
    [string]$GitApplication,
    [string]$ProjectRoot,
    [string]$Commit,
    [string]$Prefix,
    [IO.FileStream]$OutputStream
) {
    if (
        -not [IO.Path]::IsPathRooted($GitApplication) -or
        -not [IO.File]::Exists($GitApplication) -or
        [IO.Path]::GetExtension($GitApplication) -cne ".exe" -or
        $Commit -notmatch '^[0-9a-f]{40}$' -or
        $Prefix -notmatch '^JobFlow-v[0-9]+\.[0-9]+\.[0-9]+/$' -or
        $null -eq $OutputStream -or -not $OutputStream.CanWrite -or -not $OutputStream.CanSeek
    ) { throw "JOBFLOW_RELEASE_ARCHIVE_HEAD_PROOF_FAILED" }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $GitApplication
    $start.WorkingDirectory = $ProjectRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $gitDirectory = Join-Path $ProjectRoot ".git"
    $arguments = @(
        ("--git-dir=" + $gitDirectory),
        ("--work-tree=" + $ProjectRoot),
        "--no-pager",
        "-c", "core.hooksPath=NUL",
        "-c", "core.fsmonitor=false",
        "archive", "--format=zip", ("--prefix=" + $Prefix), $Commit
    )
    $start.Arguments = (($arguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join " ")
    Set-SanitizedProcessEnvironment $start "git" $GitApplication $null
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "JOBFLOW_RELEASE_ARCHIVE_HEAD_PROOF_FAILED" }
        $process.StandardOutput.BaseStream.CopyTo($OutputStream)
        $errorText = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $OutputStream.Flush($true)
        if ($process.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($errorText)) {
            throw "JOBFLOW_RELEASE_ARCHIVE_HEAD_PROOF_FAILED"
        }
    }
    finally { $process.Dispose() }
}

function New-OutputCommitRecord([string]$TemporaryPath, [string]$DestinationPath) {
    $temporary = [IO.Path]::GetFullPath($TemporaryPath)
    $destination = [IO.Path]::GetFullPath($DestinationPath)
    Assert-OrdinaryOutputLeaf $temporary "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED" -MustExist -SingleLink
    Assert-OrdinaryOutputLeaf $destination "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED"
    $existed = [IO.File]::Exists($destination)
    $backup = $null
    $oldHash = $null
    $temporaryIdentity = Get-OutputFileIdentity $temporary "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED"
    if ([long]$temporaryIdentity.link_count -ne 1) { throw "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED" }
    try {
        if ($existed) {
            $backup = Join-Path ([IO.Path]::GetDirectoryName($destination)) `
                (([IO.Path]::GetFileName($destination)) + "." + [Guid]::NewGuid().ToString("N") + ".bak")
            Assert-ProjectPath $backup "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED"
            $source = $null
            $target = $null
            try {
                $source = [IO.File]::Open($destination, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
                $target = [IO.File]::Open($backup, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
                $source.CopyTo($target)
                $target.Flush($true)
                $oldHash = "sha256:" + (Get-StreamSha256 -Stream $source)
            }
            finally {
                if ($null -ne $target) { $target.Dispose() }
                if ($null -ne $source) { $source.Dispose() }
            }
            Assert-OrdinaryOutputLeaf $backup "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED" -MustExist -SingleLink
            if (("sha256:" + (Get-FileSha256 $backup)) -cne $oldHash) {
                throw "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED"
            }
        }
        return [pscustomobject]@{
            temporary = $temporary
            destination = $destination
            backup = $backup
            existed = $existed
            old_hash = $oldHash
            new_hash = $null
            temporary_identity = $temporaryIdentity
            attempted = $false
            committed = $false
        }
    }
    catch {
        $originalFailure = [string]$_.Exception.Message
        if ($null -ne $backup -and [IO.File]::Exists($backup)) {
            try {
                Assert-OrdinaryOutputLeaf $backup "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED" -MustExist -SingleLink
                [IO.File]::Delete($backup)
            }
            catch { throw "JOBFLOW_RELEASE_OUTPUT_RECOVERY_REQUIRED" }
        }
        throw $originalFailure
    }
}

function New-OutputCommitRecordPair(
    [string]$FirstTemporaryPath,
    [string]$FirstDestinationPath,
    [string]$SecondTemporaryPath,
    [string]$SecondDestinationPath
) {
    $first = $null
    $second = $null
    try {
        $first = New-OutputCommitRecord $FirstTemporaryPath $FirstDestinationPath
        $second = New-OutputCommitRecord $SecondTemporaryPath $SecondDestinationPath
        return [pscustomobject]@{ first = $first; second = $second }
    }
    catch {
        $originalFailure = [string]$_.Exception.Message
        foreach ($record in @($second, $first)) {
            if ($null -eq $record) { continue }
            try { Remove-OutputCommitBackup $record }
            catch { throw "JOBFLOW_RELEASE_OUTPUT_RECOVERY_REQUIRED" }
        }
        throw $originalFailure
    }
}

function Move-OutputFileAtomic([string]$Source, [string]$Destination, [bool]$ReplaceExisting) {
    if ($ReplaceExisting) {
        Move-OutputFileReplaceExisting $Source $Destination "JOBFLOW_RELEASE_OUTPUT_COMMIT_FAILED"
    }
    else { [IO.File]::Move($Source, $Destination) }
}

function Commit-TemporaryOutput([object]$Record) {
    Assert-OrdinaryOutputLeaf $Record.temporary "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED" -MustExist -SingleLink
    Assert-OrdinaryOutputLeaf $Record.destination "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED"
    if ($Record.existed) {
        Assert-OrdinaryOutputLeaf $Record.backup "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED" -MustExist -SingleLink
        if (("sha256:" + (Get-FileSha256 $Record.backup)) -cne [string]$Record.old_hash) {
            throw "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED"
        }
    }
    $preMoveIdentity = Get-OutputFileIdentity $Record.temporary "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED"
    if (-not (Test-SameOutputFileIdentity $Record.temporary_identity $preMoveIdentity)) {
        throw "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_CHANGED"
    }
    $currentHash = "sha256:" + (Get-FileSha256 $Record.temporary)
    if ($null -ne $Record.new_hash -and [string]$Record.new_hash -cne $currentHash) {
        throw "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_CHANGED"
    }
    $Record.new_hash = $currentHash
    $Record.attempted = $true
    Move-OutputFileAtomic $Record.temporary $Record.destination ([bool]$Record.existed)
    $postMoveIdentity = Get-OutputFileIdentity $Record.destination "JOBFLOW_RELEASE_OUTPUT_COMMIT_FAILED"
    if (-not (Test-SameOutputFileIdentity $Record.temporary_identity $postMoveIdentity)) {
        throw "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_CHANGED"
    }
    if (("sha256:" + (Get-FileSha256 $Record.destination)) -cne [string]$Record.new_hash) {
        throw "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_CHANGED"
    }
    $Record.committed = $true
}

function Restore-CommittedOutput([object]$Record) {
    if (-not $Record.attempted) { return }
    if ($Record.existed) {
        Assert-OrdinaryOutputLeaf $Record.backup "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED" -MustExist -SingleLink
        if (("sha256:" + (Get-FileSha256 $Record.backup)) -cne [string]$Record.old_hash) {
            throw "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED"
        }
        $rollbackOutput = New-ExclusiveOutputFile ([IO.Path]::GetDirectoryName($Record.destination)) `
            (([IO.Path]::GetFileName($Record.destination)) + ".rollback") `
            "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED"
        $rollbackTemporary = [string]$rollbackOutput.path
        try {
            $backupSource = [IO.File]::Open(
                $Record.backup, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
            )
            try {
                $backupSource.CopyTo($rollbackOutput.stream)
                $rollbackOutput.stream.Flush($true)
            }
            finally {
                $backupSource.Dispose()
                $rollbackOutput.stream.Dispose()
            }
            Assert-OrdinaryOutputLeaf $rollbackTemporary "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED" -MustExist -SingleLink
            Assert-OrdinaryOutputLeaf $Record.destination "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED"
            if ([IO.File]::Exists($Record.destination)) {
                Move-OutputFileReplaceExisting $rollbackTemporary $Record.destination "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED"
            }
            else { [IO.File]::Move($rollbackTemporary, $Record.destination) }
        }
        finally {
            if ([IO.File]::Exists($rollbackTemporary)) { [IO.File]::Delete($rollbackTemporary) }
        }
        if (("sha256:" + (Get-FileSha256 $Record.destination)) -cne [string]$Record.old_hash) {
            throw "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED"
        }
    }
    elseif ([IO.File]::Exists($Record.destination)) {
        Assert-OrdinaryOutputLeaf $Record.destination "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED" -MustExist
        if ($null -eq $Record.new_hash -or ("sha256:" + (Get-FileSha256 $Record.destination)) -cne [string]$Record.new_hash) {
            throw "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED"
        }
        [IO.File]::Delete($Record.destination)
    }
}

function Remove-OutputCommitBackup([object]$Record) {
    if ($null -ne $Record.backup -and [IO.File]::Exists($Record.backup)) {
        Assert-OrdinaryOutputLeaf $Record.backup "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED" -MustExist -SingleLink
        [IO.File]::Delete($Record.backup)
    }
}

function Remove-TemporaryOutput([string]$Path) {
    if (-not [string]::IsNullOrWhiteSpace($Path) -and [IO.File]::Exists($Path)) {
        Assert-OrdinaryOutputLeaf $Path "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED" -MustExist -SingleLink
        [IO.File]::Delete($Path)
    }
}

function Assert-TransactionHash([object]$Value, [switch]$AllowNull) {
    if ($AllowNull -and $null -eq $Value) { return }
    if (-not ($Value -is [string]) -or $Value -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"
    }
}

function Convert-TransactionRecord(
    [object]$Value,
    [string]$ExpectedDestinationName,
    [string]$DistRoot
) {
    $required = @("destination_name", "existed", "backup_name", "old_hash", "new_hash")
    foreach ($name in $required) {
        if (-not ($Value.PSObject.Properties.Name -ccontains $name)) {
            throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"
        }
    }
    if (-not ($Value.destination_name -is [string]) -or $Value.destination_name -cne $ExpectedDestinationName) {
        throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"
    }
    if (-not ($Value.existed -is [bool])) { throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" }
    Assert-TransactionHash $Value.new_hash
    if ($Value.existed) {
        if (
            -not ($Value.backup_name -is [string]) -or
            $Value.backup_name -notmatch ('^' + [Regex]::Escape($ExpectedDestinationName) + '\.[0-9a-f]{32}\.bak$')
        ) { throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" }
        Assert-TransactionHash $Value.old_hash
        $backup = Join-Path $DistRoot ([string]$Value.backup_name)
        Assert-OrdinaryOutputLeaf $backup "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" -MustExist -SingleLink
        if (("sha256:" + (Get-FileSha256 $backup)) -cne [string]$Value.old_hash) {
            throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"
        }
    }
    else {
        if ($null -ne $Value.backup_name -or $null -ne $Value.old_hash) {
            throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"
        }
        $backup = $null
    }
    return [pscustomobject]@{
        temporary = $null
        destination = Join-Path $DistRoot $ExpectedDestinationName
        backup = $backup
        existed = [bool]$Value.existed
        old_hash = $Value.old_hash
        new_hash = [string]$Value.new_hash
        attempted = $true
        committed = $true
    }
}

function Write-OutputTransactionMarker(
    [string]$MarkerPath,
    [object]$ManifestRecord,
    [object]$SignatureRecord
) {
    Assert-OrdinaryOutputLeaf $MarkerPath "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_ACTIVE"
    if ([IO.File]::Exists($MarkerPath)) { throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_ACTIVE" }
    foreach ($record in @($ManifestRecord, $SignatureRecord)) {
        Assert-TransactionHash $record.new_hash
        if ($record.existed) {
            Assert-TransactionHash $record.old_hash
            Assert-OrdinaryOutputLeaf $record.backup "JOBFLOW_RELEASE_OUTPUT_BACKUP_UNTRUSTED" -MustExist -SingleLink
        }
    }
    $transaction = [ordered]@{
        schema_version = 1
        status = "SIGNED_OUTPUT_TRANSACTION_PREPARED"
        transaction_id = [Guid]::NewGuid().ToString("N")
        manifest = [ordered]@{
            destination_name = [IO.Path]::GetFileName($ManifestRecord.destination)
            existed = [bool]$ManifestRecord.existed
            backup_name = if ($ManifestRecord.existed) { [IO.Path]::GetFileName($ManifestRecord.backup) } else { $null }
            old_hash = $ManifestRecord.old_hash
            new_hash = $ManifestRecord.new_hash
        }
        signature = [ordered]@{
            destination_name = [IO.Path]::GetFileName($SignatureRecord.destination)
            existed = [bool]$SignatureRecord.existed
            backup_name = if ($SignatureRecord.existed) { [IO.Path]::GetFileName($SignatureRecord.backup) } else { $null }
            old_hash = $SignatureRecord.old_hash
            new_hash = $SignatureRecord.new_hash
        }
    }
    $markerOutput = New-ExclusiveOutputFile ([IO.Path]::GetDirectoryName($MarkerPath)) `
        (([IO.Path]::GetFileName($MarkerPath)) + ".marker") `
        "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"
    $temporary = [string]$markerOutput.path
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($transaction | ConvertTo-Json -Depth 5 -Compress))
        try {
            $markerOutput.stream.Write($bytes, 0, $bytes.Length)
            $markerOutput.stream.Flush($true)
        }
        finally { $markerOutput.stream.Dispose() }
        Assert-OrdinaryOutputLeaf $temporary "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" -MustExist -SingleLink
        Move-OutputFileReplaceExisting $temporary $MarkerPath "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID"
        Assert-OrdinaryOutputLeaf $MarkerPath "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" -MustExist -SingleLink
    }
    finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
}

function Remove-OutputTransactionMarker([string]$MarkerPath) {
    Assert-OrdinaryOutputLeaf $MarkerPath "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" -MustExist -SingleLink
    [IO.File]::Delete($MarkerPath)
}

function Recover-PendingOutputTransaction([string]$MarkerPath, [string]$DistRoot, [switch]$ForceRollback) {
    if (-not [IO.File]::Exists($MarkerPath)) { return $false }
    Assert-OrdinaryOutputLeaf $MarkerPath "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" -MustExist -SingleLink
    try { $value = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json }
    catch { throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" }
    if (
        $null -eq $value -or
        -not (Test-JsonInteger $value.schema_version 1) -or
        -not ($value.status -is [string]) -or $value.status -cne "SIGNED_OUTPUT_TRANSACTION_PREPARED" -or
        -not ($value.transaction_id -is [string]) -or $value.transaction_id -notmatch '^[0-9a-f]{32}$'
    ) { throw "JOBFLOW_RELEASE_OUTPUT_TRANSACTION_INVALID" }
    $manifestRecord = Convert-TransactionRecord $value.manifest "JobFlow-update-manifest.json" $DistRoot
    $signatureRecord = Convert-TransactionRecord $value.signature "JobFlow-update-manifest.sig.json" $DistRoot
    $records = @($manifestRecord, $signatureRecord)
    $allNew = $true
    foreach ($record in $records) {
        if (
            -not [IO.File]::Exists($record.destination) -or
            ("sha256:" + (Get-FileSha256 $record.destination)) -cne [string]$record.new_hash
        ) { $allNew = $false }
    }
    if ($ForceRollback -or -not $allNew) {
        foreach ($record in $records) { Restore-CommittedOutput $record }
        foreach ($record in $records) {
            if ($record.existed) {
                if (
                    -not [IO.File]::Exists($record.destination) -or
                    ("sha256:" + (Get-FileSha256 $record.destination)) -cne [string]$record.old_hash
                ) { throw "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED" }
            }
            elseif ([IO.File]::Exists($record.destination)) { throw "JOBFLOW_RELEASE_OUTPUT_ROLLBACK_FAILED" }
        }
    }
    Remove-OutputTransactionMarker $MarkerPath
    foreach ($record in $records) {
        try { Remove-OutputCommitBackup $record }
        catch { Write-Warning "JOBFLOW_RELEASE_OUTPUT_BACKUP_CLEANUP_FAILED" }
    }
    return $true
}

function Invoke-FormalOutputRollbackOrRequireRecovery([string]$MarkerPath, [string]$DistRoot) {
    if (-not [IO.File]::Exists($MarkerPath)) { return }
    try {
        [void](Recover-PendingOutputTransaction $MarkerPath $DistRoot -ForceRollback)
    }
    catch {
        # The marker and verified backups are the only durable recovery state.
        # Never remove them when rollback cannot prove that the old pair was restored.
        throw "JOBFLOW_RELEASE_OUTPUT_RECOVERY_REQUIRED"
    }
}

function Assert-PresignManifestArchiveIdentity([object]$Value, [object]$Candidate, [object]$ArchiveLock) {
    $expectedName = "JobFlow-v" + [string]$Candidate.version + "-windows-x64-complete.zip"
    if (
        $null -eq $Value -or
        -not (Test-JsonInteger $Value.schema_version 2) -or
        $null -eq $Value.release -or
        -not ($Value.release.version -is [string]) -or $Value.release.version -cne [string]$Candidate.version -or
        -not ($Value.release.source_commit -is [string]) -or $Value.release.source_commit -cne [string]$Candidate.commit -or
        $null -eq $Value.asset -or
        -not ($Value.asset.name -is [string]) -or $Value.asset.name -cne $expectedName -or
        -not ($Value.asset.sha256 -is [string]) -or $Value.asset.sha256 -cne [string]$ArchiveLock.sha256 -or
        -not (($Value.asset.bytes -is [int]) -or ($Value.asset.bytes -is [long])) -or
        [long]$Value.asset.bytes -ne [long]$ArchiveLock.length
    ) { throw "JOBFLOW_RELEASE_SIGNED_ARCHIVE_IDENTITY_MISMATCH" }
}

function Assert-ExplicitAbsoluteInputFile(
    [string]$Path,
    [long]$MaximumBytes,
    [string]$Code
) {
    try {
        if (
            [string]::IsNullOrWhiteSpace($Path) -or
            -not [IO.Path]::IsPathRooted($Path) -or
            $Path.StartsWith('\\', [StringComparison]::Ordinal) -or
            $Path.StartsWith('//', [StringComparison]::Ordinal) -or
            $Path.StartsWith('\??\', [StringComparison]::Ordinal)
        ) { throw $Code }
        $absolute = [IO.Path]::GetFullPath($Path)
        if (-not [string]::Equals($absolute, $Path, [StringComparison]::OrdinalIgnoreCase)) { throw $Code }
        $root = [IO.Path]::GetPathRoot($absolute)
        if (
            [string]::IsNullOrWhiteSpace($root) -or
            $root -notmatch '^[A-Za-z]:\\$' -or
            $absolute.Substring($root.Length).Contains(":")
        ) { throw $Code }
        Assert-NoReparsePath $absolute $Code -MustExist | Out-Null
        if (-not [IO.File]::Exists($absolute)) { throw $Code }
        $item = Get-Item -LiteralPath $absolute -Force
        if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw $Code }
        if ([long]$item.Length -lt 1 -or [long]$item.Length -gt $MaximumBytes) { throw $Code }
        return $absolute
    }
    catch { throw $Code }
}

function New-ProtectedInputStagingRoot([string]$DistRoot) {
    $code = "JOBFLOW_RELEASE_INPUT_STAGING_FAILED"
    Assert-ProjectPath $DistRoot $code
    $distContext = Open-StableReleaseDirectoryChain $DistRoot $code
    try {
        Assert-StableReleaseDirectoryLocks @($distContext.Locks) $code
        $parentLock = @($distContext.Locks)[@($distContext.Locks).Count - 1]
        for ($attempt = 0; $attempt -lt 8; $attempt++) {
            $name = ".protected-signing-inputs-" + [Guid]::NewGuid().ToString("N")
            $rootLock = $null
            try {
                $rootLock = New-StableReleaseChildDirectory $parentLock $name $code
                $owned = [Collections.Generic.List[object]]::new()
                [void]$owned.Add($rootLock)
                $directories = [Collections.Generic.Dictionary[string,object]]::new(
                    [StringComparer]::OrdinalIgnoreCase
                )
                $directories.Add("", $rootLock)
                $context = [pscustomobject]@{
                    Path = [string]$rootLock.Path
                    AncestryLocks = @($distContext.Locks)
                    RootLock = $rootLock
                    OwnedDirectoryLocks = $owned
                    Directories = $directories
                }
                Assert-StableReleaseDirectoryLocks (@($context.AncestryLocks) + @($rootLock)) $code
                return $context
            }
            catch {
                if ($null -ne $rootLock -and $null -ne $rootLock.Handle) {
                    try { Remove-ProtectedStagedDirectoryLock $rootLock } catch { }
                }
            }
        }
        throw $code
    }
    catch {
        $distLocks = @($distContext.Locks)
        for ($index = $distLocks.Count - 1; $index -ge 0; $index--) {
            $lock = $distLocks[$index]
            if ($null -ne $lock.Handle) { $lock.Handle.Dispose() }
        }
        throw
    }
}

function Assert-ProtectedStagingContext([object]$Context, [string]$Code) {
    if (
        $null -eq $Context -or $null -eq $Context.RootLock -or
        $null -eq $Context.Directories -or $null -eq $Context.OwnedDirectoryLocks
    ) { throw $Code }
    Assert-StableReleaseDirectoryLocks (
        @($Context.AncestryLocks) + @($Context.OwnedDirectoryLocks)
    ) $Code
}

function New-ProtectedStagingDirectory([object]$Context, [string]$RelativePath) {
    $code = "JOBFLOW_RELEASE_INPUT_STAGING_FAILED"
    Assert-ProtectedStagingContext $Context $code
    if (
        [string]::IsNullOrWhiteSpace($RelativePath) -or
        $RelativePath -match '[\\/:]' -or
        $RelativePath -eq '.' -or $RelativePath -eq '..' -or
        $Context.Directories.ContainsKey($RelativePath)
    ) { throw $code }
    $lock = New-StableReleaseChildDirectory $Context.RootLock $RelativePath $code
    try {
        $Context.Directories.Add($RelativePath, $lock)
        [void]$Context.OwnedDirectoryLocks.Add($lock)
        Assert-ProtectedStagingContext $Context $code
        return [string]$lock.Path
    }
    catch {
        try { Remove-ProtectedStagedDirectoryLock $lock } catch { }
        throw
    }
}

function Get-ProtectedStagingParentLock(
    [object]$Context,
    [string]$RelativePath,
    [string]$Code
) {
    Assert-ProtectedStagingContext $Context $Code
    if (
        [string]::IsNullOrWhiteSpace($RelativePath) -or
        [IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath.Contains(":") -or
        $RelativePath -match '(^|[\\/])\.\.([\\/]|$)'
    ) { throw $Code }
    $normalized = $RelativePath.Replace('/', '\')
    $parent = [IO.Path]::GetDirectoryName($normalized)
    if ([string]::IsNullOrWhiteSpace($parent)) { $parent = "" }
    if ($parent.Contains('\') -or -not $Context.Directories.ContainsKey($parent)) { throw $Code }
    return [pscustomobject]@{
        ParentLock = $Context.Directories[$parent]
        Leaf = [IO.Path]::GetFileName($normalized)
        Target = [IO.Path]::GetFullPath((Join-Path ([string]$Context.Path) $normalized))
    }
}

function Copy-LockedInputToProtectedStaging(
    [object]$SourceLock,
    [object]$StagingContext,
    [string]$RelativePath,
    [long]$MaximumBytes,
    [string]$Code
) {
    if (
        $null -eq $SourceLock -or
        [string]::IsNullOrWhiteSpace($RelativePath) -or
        [IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath.Contains(":") -or
        $RelativePath -match '(^|[\\/])\.\.([\\/]|$)'
    ) { throw $Code }
    $binding = Get-ProtectedStagingParentLock $StagingContext $RelativePath $Code
    $target = [string]$binding.Target
    $output = $null
    $stream = $null
    $readLock = $null
    try {
        $output = Open-NewReleaseFileRelative $binding.ParentLock $binding.Leaf 1 $Code
        $stream = $output.stream
        $SourceLock.stream.Position = 0
        $SourceLock.stream.CopyTo($stream)
        $SourceLock.stream.Position = 0
        $stream.Flush($true)
        $writeIdentity = Get-OpenOutputFileIdentity $stream $Code
        $writeLength = [long]$stream.Length
        $writeSha256 = "sha256:" + (Get-StreamSha256 $stream)
        if (
            [long]$writeIdentity.link_count -ne 1 -or
            $writeLength -ne [long]$SourceLock.length -or
            $writeLength -lt 1 -or
            $writeLength -gt $MaximumBytes -or
            $writeSha256 -cne [string]$SourceLock.sha256
        ) { throw $Code }
        $readLock = Convert-NewReleaseFileToReadLock $output $MaximumBytes $Code
        $stream = $null
        if (
            [long]$readLock.length -ne $writeLength -or
            [string]$readLock.sha256 -cne $writeSha256 -or
            [string]$readLock.sha256 -cne [string]$SourceLock.sha256 -or
            -not ([string]$readLock.path).Equals($target, [StringComparison]::OrdinalIgnoreCase)
        ) { throw $Code }
        Assert-ProtectedStagingContext $StagingContext $Code
        return $readLock
    }
    catch {
        if ($null -ne $readLock) {
            try { Remove-ProtectedStagedFileLock $readLock } catch { }
        }
        elseif ($null -ne $output -and $null -ne $output.stream) {
            try { Remove-NewReleaseFileOutput $output } catch { }
        }
        throw $Code
    }
}

function Assert-ProtectedStagingBinding([object]$SourceLock, [object]$StagingLock) {
    Assert-InputFileLockUnchanged $SourceLock
    Assert-InputFileLockUnchanged $StagingLock
    if (
        [long]$SourceLock.length -ne [long]$StagingLock.length -or
        [string]$SourceLock.sha256 -cne [string]$StagingLock.sha256
    ) { throw "JOBFLOW_RELEASE_INPUT_STAGING_CHANGED" }
}

function Add-ProtectedStagingBinding(
    [object]$SourceLock,
    [object]$StagingContext,
    [string]$RelativePath,
    [long]$MaximumBytes,
    [string]$Code,
    [object]$StagingLocks,
    [object]$StagingBindings,
    [object]$StagingPaths
) {
    $staged = Copy-LockedInputToProtectedStaging $SourceLock $StagingContext $RelativePath $MaximumBytes $Code
    $StagingLocks.Add($staged)
    $StagingBindings.Add([pscustomobject]@{ source = $SourceLock; staged = $staged })
    $StagingPaths.Add([string]$staged.path)
    return $staged
}

function New-SealedReleaseProducerArchive(
    [object]$StagingContext,
    [object]$ProducerSources,
    [object]$StagingLocks
) {
    $code = "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
    Assert-ProtectedStagingContext $StagingContext $code
    $output = Open-NewReleaseFileRelative $StagingContext.RootLock "producer.pyz" 1 $code
    $archive = $null
    $lock = $null
    $completed = $false
    try {
        Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
        $archive = [IO.Compression.ZipArchive]::new(
            $output.stream, [IO.Compression.ZipArchiveMode]::Create, $true
        )
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        [int]$entryCount = 0
        foreach ($source in $ProducerSources) {
            $relative = [string]$source.relative
            if (-not $relative.StartsWith("src\jobops\", [StringComparison]::Ordinal)) { continue }
            if ([IO.Path]::GetExtension($relative) -cne ".py") { throw $code }
            $entryName = $relative.Substring(4).Replace('\', '/')
            if (
                -not $entryName.StartsWith("jobops/", [StringComparison]::Ordinal) -or
                -not $seen.Add($entryName)
            ) { throw $code }
            Assert-InputFileLockUnchanged $source.lock
            $entry = $archive.CreateEntry($entryName, [IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = [DateTimeOffset]::new(1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
            $entryStream = $entry.Open()
            try {
                $source.lock.stream.Position = 0
                $source.lock.stream.CopyTo($entryStream)
                $source.lock.stream.Position = 0
            }
            finally { $entryStream.Dispose() }
            $entryCount++
        }
        if ($entryCount -lt 2 -or -not $seen.Contains("jobops/__init__.py") -or -not $seen.Contains("jobops/update_manifest.py")) {
            throw $code
        }
        $archive.Dispose()
        $archive = $null
        $lock = Convert-NewReleaseFileToReadLock $output 16777216 $code
        Assert-ProtectedStagingContext $StagingContext $code
        foreach ($source in $ProducerSources) { Assert-InputFileLockUnchanged $source.lock }
        [void]$StagingLocks.Add($lock)
        $completed = $true
        return $lock
    }
    finally {
        if ($null -ne $archive) { $archive.Dispose() }
        if (-not $completed -and $null -ne $lock) {
            try { Remove-ProtectedStagedFileLock $lock } catch { }
        }
        elseif (-not $completed -and $null -ne $output -and $null -ne $output.stream) {
            try { Remove-NewReleaseFileOutput $output } catch { }
        }
    }
}

function Assert-AllProtectedStagingBindings([object]$Bindings) {
    foreach ($binding in $Bindings) {
        Assert-ProtectedStagingBinding $binding.source $binding.staged
    }
}

function Assert-AllInputFileLocksUnchanged([object]$Locks) {
    foreach ($lock in $Locks) { Assert-InputFileLockUnchanged $lock }
}

function Get-ReleasePythonRuntimePolicy([object]$ToolchainPolicy, [object]$RuntimeSourceLock) {
    $code = "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
    try {
        $runtimePolicy = $ToolchainPolicy.python_execution_runtime
        $runtimeSource = Read-LockedJsonObject $RuntimeSourceLock $code
        $policyKeys = @(
            "source_policy", "python_tag", "maximum_files", "maximum_entry_bytes",
            "maximum_uncompressed_bytes", "maximum_compression_ratio", "required_entries",
            "active_pth_entries"
        )
        if (
            $null -eq $runtimePolicy -or
            -not (Test-JsonObjectKeys $runtimePolicy $policyKeys) -or
            -not ($runtimePolicy.source_policy -is [string]) -or
            $runtimePolicy.source_policy -cne "config/windows-runtime-source.json" -or
            -not ($runtimePolicy.python_tag -is [string]) -or
            $runtimePolicy.python_tag -notmatch '^python[0-9]{3}$' -or
            -not (Test-JsonIntegerAtLeast $runtimePolicy.maximum_files 8) -or
            [long]$runtimePolicy.maximum_files -gt 256 -or
            -not (Test-JsonIntegerAtLeast $runtimePolicy.maximum_entry_bytes 1048576) -or
            [long]$runtimePolicy.maximum_entry_bytes -gt 134217728 -or
            -not (Test-JsonIntegerAtLeast $runtimePolicy.maximum_uncompressed_bytes 8388608) -or
            [long]$runtimePolicy.maximum_uncompressed_bytes -gt 268435456 -or
            -not (Test-JsonIntegerAtLeast $runtimePolicy.maximum_compression_ratio 1) -or
            [long]$runtimePolicy.maximum_compression_ratio -gt 500 -or
            -not ($runtimePolicy.required_entries -is [Array]) -or
            $runtimePolicy.required_entries.Count -lt 8 -or
            -not (Test-JsonStringArray $runtimePolicy.active_pth_entries @(
                ([string]$runtimePolicy.python_tag + ".zip"), "."
            ))
        ) { throw $code }
        $sourcePython = $runtimeSource.python
        if (
            $null -eq $runtimeSource -or
            -not (Test-JsonInteger $runtimeSource.schema_version 1) -or
            -not ($runtimeSource.status -is [string]) -or $runtimeSource.status -cne "PINNED_OFFICIAL_SOURCE" -or
            -not ($runtimeSource.platform -is [string]) -or $runtimeSource.platform -cne "windows-x64" -or
            -not ($runtimeSource.architecture -is [string]) -or $runtimeSource.architecture -cne "AMD64" -or
            $null -eq $sourcePython -or
            -not ($sourcePython.version -is [string]) -or $sourcePython.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            -not ($sourcePython.artifact_name -is [string]) -or
            $sourcePython.artifact_name -cne ("python-" + [string]$sourcePython.version + "-embed-amd64.zip") -or
            -not (($sourcePython.artifact_bytes -is [int]) -or ($sourcePython.artifact_bytes -is [long])) -or
            [long]$sourcePython.artifact_bytes -lt 1 -or [long]$sourcePython.artifact_bytes -gt 134217728 -or
            -not ($sourcePython.artifact_sha256 -is [string]) -or
            $sourcePython.artifact_sha256 -notmatch '^sha256:[0-9a-f]{64}$' -or
            $null -eq $runtimeSource.builder -or
            -not ($runtimeSource.builder.python_version -is [string]) -or
            [string]$runtimeSource.builder.python_version -cne [string]$sourcePython.version -or
            $null -eq $runtimeSource.isolation -or
            -not ($runtimeSource.isolation.import_site -is [bool]) -or
            $runtimeSource.isolation.import_site -ne $false -or
            -not ($runtimeSource.isolation.python_pth -is [Array]) -or
            $runtimeSource.isolation.python_pth.Count -lt 2 -or
            [string]$runtimeSource.isolation.python_pth[0] -cne ([string]$runtimePolicy.python_tag + ".zip") -or
            [string]$runtimeSource.isolation.python_pth[1] -cne "."
        ) { throw $code }
        $versionParts = ([string]$sourcePython.version).Split('.')
        $expectedTag = "python" + $versionParts[0] + $versionParts[1]
        if ([string]$runtimePolicy.python_tag -cne $expectedTag) { throw $code }
        $required = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $runtimePolicy.required_entries) {
            if (
                -not ($entry -is [string]) -or
                [string]::IsNullOrWhiteSpace([string]$entry) -or
                [string]$entry -match '[\\/:]' -or
                -not $required.Add([string]$entry)
            ) { throw $code }
        }
        foreach ($mandatory in @(
            "python.exe", "python3.dll", ($expectedTag + ".dll"), ($expectedTag + ".zip"),
            ($expectedTag + "._pth"), "vcruntime140.dll", "vcruntime140_1.dll",
            "_hashlib.pyd", "unicodedata.pyd", "select.pyd"
        )) {
            if (-not $required.Contains($mandatory)) { throw $code }
        }
        return [pscustomobject]@{
            source = $runtimeSource
            policy = $runtimePolicy
            python_tag = $expectedTag
            artifact_name = [string]$sourcePython.artifact_name
            artifact_bytes = [long]$sourcePython.artifact_bytes
            artifact_sha256 = [string]$sourcePython.artifact_sha256
        }
    }
    catch { throw $code }
}

function Test-SafeReleasePythonArchiveName([string]$Name) {
    if (
        [string]::IsNullOrWhiteSpace($Name) -or
        $Name.Length -gt 128 -or
        $Name -match '[\\/:]' -or
        $Name -match '[\x00-\x1f]' -or
        $Name.EndsWith(".", [StringComparison]::Ordinal) -or
        $Name.EndsWith(" ", [StringComparison]::Ordinal) -or
        [IO.Path]::IsPathRooted($Name)
    ) { return $false }
    $extension = [IO.Path]::GetExtension($Name)
    return (
        $Name -ceq "LICENSE.txt" -or
        @(".exe", ".dll", ".pyd", ".zip", "._pth", ".cat") -ccontains $extension.ToLowerInvariant()
    )
}

function Read-StrictReleasePythonPth([object]$PthLock, [string[]]$Expected) {
    $code = "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
    $active = New-Object Collections.Generic.List[string]
    try {
        $PthLock.stream.Position = 0
        $reader = [IO.StreamReader]::new(
            $PthLock.stream,
            ([Text.UTF8Encoding]::new($false, $true)),
            $true,
            1024,
            $true
        )
        try {
            while (-not $reader.EndOfStream) {
                $line = $reader.ReadLine()
                if ($line.Contains([char]0)) { throw $code }
                $trimmed = $line.Trim()
                if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#", [StringComparison]::Ordinal)) {
                    continue
                }
                if ($trimmed -match '^(?i:import\s+site)$') { throw $code }
                if (
                    [IO.Path]::IsPathRooted($trimmed) -or
                    $trimmed.Contains(":") -or
                    $trimmed -match '(^|[\\/])\.\.([\\/]|$)'
                ) { throw $code }
                $active.Add($trimmed)
            }
        }
        finally { $reader.Dispose() }
    }
    catch { throw $code }
    finally { $PthLock.stream.Position = 0 }
    if (-not (Test-JsonStringArray $active.ToArray() $Expected)) { throw $code }
}

function Expand-LockedReleasePythonRuntime(
    [object]$ArchiveLock,
    [object]$RuntimePolicy,
    [object]$StagingContext,
    [object]$StagingLocks,
    [object]$StagingPaths
) {
    $code = "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
    Assert-ProtectedStagingContext $StagingContext $code
    if (-not $StagingContext.Directories.ContainsKey("python-runtime")) { throw $code }
    $runtimeLock = $StagingContext.Directories["python-runtime"]
    $runtimeDirectory = [string]$runtimeLock.Path
    if (
        [IO.Path]::GetFileName([string]$ArchiveLock.path) -cne [string]$RuntimePolicy.artifact_name -or
        [long]$ArchiveLock.length -ne [long]$RuntimePolicy.artifact_bytes -or
        [string]$ArchiveLock.sha256 -cne [string]$RuntimePolicy.artifact_sha256
    ) { throw $code }
    $runtimeLocks = New-Object Collections.Generic.List[object]
    $byName = @{}
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $archive = $null
    try {
        Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
        $ArchiveLock.stream.Position = 0
        $archive = [IO.Compression.ZipArchive]::new(
            $ArchiveLock.stream,
            [IO.Compression.ZipArchiveMode]::Read,
            $true
        )
        if ($archive.Entries.Count -lt 8 -or $archive.Entries.Count -gt [long]$RuntimePolicy.policy.maximum_files) {
            throw $code
        }
        [long]$total = 0
        foreach ($entry in $archive.Entries) {
            $name = [string]$entry.FullName
            $unixKind = (([long]$entry.ExternalAttributes -shr 16) -band 0xF000)
            if (
                $name -cne [string]$entry.Name -or
                -not (Test-SafeReleasePythonArchiveName $name) -or
                -not $seen.Add($name) -or
                $name -ieq "pyvenv.cfg" -or
                (($entry.ExternalAttributes -band 0x10) -ne 0) -or
                (($entry.ExternalAttributes -band 0x400) -ne 0) -or
                ($unixKind -ne 0 -and $unixKind -ne 0x8000) -or
                [long]$entry.Length -lt 1 -or
                [long]$entry.Length -gt [long]$RuntimePolicy.policy.maximum_entry_bytes -or
                [long]$entry.CompressedLength -lt 1 -or
                ([double]$entry.Length / [double]$entry.CompressedLength) -gt [double]$RuntimePolicy.policy.maximum_compression_ratio
            ) { throw $code }
            $total += [long]$entry.Length
            if ($total -gt [long]$RuntimePolicy.policy.maximum_uncompressed_bytes) { throw $code }
            $target = [IO.Path]::GetFullPath((Join-Path $runtimeDirectory $name))
            $StagingPaths.Add($target)
            $sourceStream = $null
            $outputStream = $null
            $output = $null
            $leafLock = $null
            try {
                $sourceStream = $entry.Open()
                $output = Open-NewReleaseFileRelative $runtimeLock $name 1 $code
                $outputStream = $output.stream
                $buffer = New-Object byte[] (64 * 1024)
                [long]$written = 0
                while (($count = $sourceStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $written += $count
                    if ($written -gt [long]$entry.Length -or $written -gt [long]$RuntimePolicy.policy.maximum_entry_bytes) {
                        throw $code
                    }
                    $outputStream.Write($buffer, 0, $count)
                }
                $outputStream.Flush($true)
                $writeIdentity = Get-OpenOutputFileIdentity $outputStream $code
                $writeHash = "sha256:" + (Get-StreamSha256 $outputStream)
                if ($written -ne [long]$entry.Length -or [long]$writeIdentity.link_count -ne 1) { throw $code }
                $leafLock = Convert-NewReleaseFileToReadLock `
                    $output ([long]$RuntimePolicy.policy.maximum_entry_bytes) $code
                $outputStream = $null
                if (
                    [long]$leafLock.length -ne $written -or
                    [string]$leafLock.sha256 -cne $writeHash -or
                    -not ([string]$leafLock.path).Equals($target, [StringComparison]::OrdinalIgnoreCase)
                ) { throw $code }
            }
            catch {
                if ($null -ne $leafLock) {
                    try { Remove-ProtectedStagedFileLock $leafLock } catch { }
                }
                elseif ($null -ne $output -and $null -ne $output.stream) {
                    try { Remove-NewReleaseFileOutput $output } catch { }
                }
                throw
            }
            finally { if ($null -ne $sourceStream) { $sourceStream.Dispose() } }
            $runtimeLocks.Add($leafLock)
            $StagingLocks.Add($leafLock)
            $byName[$name.ToLowerInvariant()] = $leafLock
            Assert-ProtectedStagingContext $StagingContext $code
        }
    }
    catch { throw $code }
    finally {
        if ($null -ne $archive) { $archive.Dispose() }
        $ArchiveLock.stream.Position = 0
    }
    foreach ($required in $RuntimePolicy.policy.required_entries) {
        if (-not $byName.ContainsKey(([string]$required).ToLowerInvariant())) { throw $code }
    }
    $pthName = ([string]$RuntimePolicy.python_tag + "._pth").ToLowerInvariant()
    if (-not $byName.ContainsKey($pthName)) { throw $code }
    Read-StrictReleasePythonPth $byName[$pthName] ([string[]]$RuntimePolicy.policy.active_pth_entries)
    $pythonName = "python.exe"
    if (-not $byName.ContainsKey($pythonName)) { throw $code }
    Assert-AllInputFileLocksUnchanged $runtimeLocks
    return [pscustomobject]@{
        python_path = [string]$byName[$pythonName].path
        locks = $runtimeLocks
    }
}

function Get-ReleaseHandleFileIndex([object]$Identity) {
    return (([uint64]$Identity.FileIndexHigh -shl 32) -bor [uint64]$Identity.FileIndexLow)
}

function Assert-ProtectedStagedFileLockHandleUnchanged([object]$Lock, [string]$Code) {
    if (
        $null -eq $Lock -or $null -eq $Lock.stream -or
        $Lock.stream.SafeFileHandle.IsClosed -or $Lock.stream.SafeFileHandle.IsInvalid
    ) { throw $Code }
    $current = Get-OpenOutputFileIdentity $Lock.stream $Code
    if (
        [long]$current.link_count -ne 1 -or
        [long]$current.volume -ne [long]$Lock.volume -or
        [uint64]$current.file_index -ne [uint64]$Lock.file_index -or
        [long]$Lock.stream.Length -ne [long]$Lock.length -or
        ("sha256:" + (Get-StreamSha256 $Lock.stream)) -cne [string]$Lock.sha256 -or
        -not (Get-ReleaseFinalPathFromHandle $Lock.stream.SafeFileHandle $Code).Equals(
            [string]$Lock.path, [StringComparison]::OrdinalIgnoreCase
        )
    ) { throw $Code }
}

function Remove-ProtectedStagedFileLock([object]$Lock) {
    $code = "JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED"
    if ($null -eq $Lock -or $null -eq $Lock.stream) { return }
    if (
        $null -eq $Lock.ParentLock -or [string]::IsNullOrWhiteSpace([string]$Lock.Name) -or
        $null -eq $Lock.ParentLock.Handle -or $Lock.ParentLock.Handle.IsClosed
    ) { throw $code }
    $transition = $null
    $deleteHandle = $null
    try {
        Assert-ProtectedStagedFileLockHandleUnchanged $Lock $code
        $transition = Open-ReleaseRelativeReadStream $Lock.ParentLock ([string]$Lock.Name) 7 $code
        $transitionIdentity = Get-OpenOutputFileIdentity $transition $code
        if (
            [long]$transitionIdentity.volume -ne [long]$Lock.volume -or
            [uint64]$transitionIdentity.file_index -ne [uint64]$Lock.file_index -or
            [long]$transitionIdentity.link_count -ne 1 -or
            [long]$transition.Length -ne [long]$Lock.length -or
            ("sha256:" + (Get-StreamSha256 $transition)) -cne [string]$Lock.sha256 -or
            -not (Get-ReleaseFinalPathFromHandle $transition.SafeFileHandle $code).Equals(
                [string]$Lock.path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $code }

        $Lock.stream.Dispose()
        $Lock.stream = $null
        $deleteHandle = [JobFlowReleaseNative.FileIdentityApi]::OpenDeleteFileRelative(
            $Lock.ParentLock.Handle, [string]$Lock.Name
        )
        if ($null -eq $deleteHandle -or $deleteHandle.IsInvalid) { throw $code }
        $deleteIdentity = Get-ReleaseHandleIdentity $deleteHandle $code
        if (
            ([uint32]$deleteIdentity.Attributes -band [uint32][IO.FileAttributes]::Directory) -ne 0 -or
            ([uint32]$deleteIdentity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$deleteIdentity.LinkCount -ne 1 -or
            [long]$deleteIdentity.VolumeSerialNumber -ne [long]$Lock.volume -or
            (Get-ReleaseHandleFileIndex $deleteIdentity) -ne [uint64]$Lock.file_index -or
            -not (Get-ReleaseFinalPathFromHandle $deleteHandle $code).Equals(
                [string]$Lock.path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $code }
        Mark-ReleaseHandleDelete $deleteHandle $code
    }
    finally {
        if ($null -ne $deleteHandle) { $deleteHandle.Dispose() }
        if ($null -ne $transition) { $transition.Dispose() }
        if ($null -ne $Lock.stream) {
            try { $Lock.stream.Dispose() } catch { }
            $Lock.stream = $null
        }
    }
}

function Remove-ProtectedStagedDirectoryLock([object]$Lock) {
    $code = "JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED"
    if ($null -eq $Lock -or $null -eq $Lock.Handle -or $Lock.Handle.IsClosed) { return }
    if (
        $null -eq $Lock.ParentLock -or [string]::IsNullOrWhiteSpace([string]$Lock.Name) -or
        $null -eq $Lock.ParentLock.Handle -or $Lock.ParentLock.Handle.IsClosed
    ) { throw $code }
    $transition = $null
    $deleteHandle = $null
    try {
        $identity = Get-ReleaseHandleIdentity $Lock.Handle $code
        if (
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$identity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$identity.VolumeSerialNumber -ne [uint32]$Lock.Volume -or
            [uint32]$identity.FileIndexHigh -ne [uint32]$Lock.IndexHigh -or
            [uint32]$identity.FileIndexLow -ne [uint32]$Lock.IndexLow
        ) { throw $code }
        $transition = [JobFlowReleaseNative.FileIdentityApi]::OpenDirectoryRelative(
            $Lock.ParentLock.Handle, [string]$Lock.Name, 7
        )
        if ($null -eq $transition -or $transition.IsInvalid) { throw $code }
        $transitionIdentity = Get-ReleaseHandleIdentity $transition $code
        if (
            [uint32]$transitionIdentity.VolumeSerialNumber -ne [uint32]$Lock.Volume -or
            [uint32]$transitionIdentity.FileIndexHigh -ne [uint32]$Lock.IndexHigh -or
            [uint32]$transitionIdentity.FileIndexLow -ne [uint32]$Lock.IndexLow -or
            -not (Get-ReleaseFinalPathFromHandle $transition $code).Equals(
                [string]$Lock.Path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $code }

        $Lock.Handle.Dispose()
        $deleteHandle = [JobFlowReleaseNative.FileIdentityApi]::OpenDeleteDirectoryRelative(
            $Lock.ParentLock.Handle, [string]$Lock.Name
        )
        if ($null -eq $deleteHandle -or $deleteHandle.IsInvalid) { throw $code }
        $deleteIdentity = Get-ReleaseHandleIdentity $deleteHandle $code
        if (
            ([uint32]$deleteIdentity.Attributes -band [uint32][IO.FileAttributes]::Directory) -eq 0 -or
            ([uint32]$deleteIdentity.Attributes -band [uint32][IO.FileAttributes]::ReparsePoint) -ne 0 -or
            [uint32]$deleteIdentity.VolumeSerialNumber -ne [uint32]$Lock.Volume -or
            [uint32]$deleteIdentity.FileIndexHigh -ne [uint32]$Lock.IndexHigh -or
            [uint32]$deleteIdentity.FileIndexLow -ne [uint32]$Lock.IndexLow -or
            -not (Get-ReleaseFinalPathFromHandle $deleteHandle $code).Equals(
                [string]$Lock.Path, [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw $code }
        Mark-ReleaseHandleDelete $deleteHandle $code
    }
    finally {
        if ($null -ne $deleteHandle) { $deleteHandle.Dispose() }
        if ($null -ne $transition) { $transition.Dispose() }
        if ($null -ne $Lock.Handle -and -not $Lock.Handle.IsClosed) { $Lock.Handle.Dispose() }
    }
}

function Remove-ProtectedInputStagingRoot([object]$Context) {
    $code = "JOBFLOW_RELEASE_INPUT_STAGING_CLEANUP_FAILED"
    if ($null -eq $Context) { return }
    Assert-ProtectedStagingContext $Context $code
    $failure = $false
    $ownedLocks = @($Context.OwnedDirectoryLocks)
    for ($index = $ownedLocks.Count - 1; $index -ge 0; $index--) {
        try { Remove-ProtectedStagedDirectoryLock $ownedLocks[$index] }
        catch { $failure = $true }
    }
    $ancestryLocks = @($Context.AncestryLocks)
    for ($index = $ancestryLocks.Count - 1; $index -ge 0; $index--) {
        $lock = $ancestryLocks[$index]
        if ($null -ne $lock -and $null -ne $lock.Handle -and -not $lock.Handle.IsClosed) {
            try { $lock.Handle.Dispose() } catch { $failure = $true }
        }
    }
    if ($failure) { throw $code }
}

function Enter-InputFileLock(
    [string]$Path,
    [long]$MaximumBytes,
    [string]$Code
) {
    $absolute = Assert-ExplicitAbsoluteInputFile $Path $MaximumBytes $Code
    $stream = $null
    try {
        $stream = [IO.File]::Open(
            $absolute,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $identity = Get-OpenOutputFileIdentity $stream $Code
        if ([long]$identity.link_count -ne 1 -or $stream.Length -lt 1 -or $stream.Length -gt $MaximumBytes) {
            throw $Code
        }
        return [pscustomobject]@{
            path = $absolute
            stream = $stream
            volume = [long]$identity.volume
            file_index = [uint64]$identity.file_index
            length = [long]$stream.Length
            sha256 = "sha256:" + (Get-StreamSha256 $stream)
            code = $Code
            maximum_bytes = $MaximumBytes
        }
    }
    catch {
        if ($null -ne $stream) { $stream.Dispose() }
        throw
    }
}

function Assert-InputFileLockUnchanged([object]$Lock) {
    if ($null -eq $Lock -or $null -eq $Lock.stream -or [string]::IsNullOrWhiteSpace([string]$Lock.code)) {
        throw "JOBFLOW_RELEASE_INPUT_IDENTITY_INVALID"
    }
    $absolute = Assert-ExplicitAbsoluteInputFile ([string]$Lock.path) ([long]$Lock.maximum_bytes) ([string]$Lock.code)
    $current = Get-OutputFileIdentity $absolute ([string]$Lock.code)
    if (
        [long]$current.link_count -ne 1 -or
        [long]$current.volume -ne [long]$Lock.volume -or
        [uint64]$current.file_index -ne [uint64]$Lock.file_index -or
        [long]$Lock.stream.Length -ne [long]$Lock.length -or
        ("sha256:" + (Get-StreamSha256 $Lock.stream)) -cne [string]$Lock.sha256 -or
        ("sha256:" + (Get-FileSha256 $absolute)) -cne [string]$Lock.sha256
    ) { throw "JOBFLOW_RELEASE_INPUT_CHANGED" }
}

function Test-LockedBytesEqual([object]$Lock, [string]$Path, [string]$Code) {
    $candidate = $null
    try {
        $candidate = [IO.File]::Open(
            [IO.Path]::GetFullPath($Path),
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        if ($candidate.Length -ne [long]$Lock.length) { return $false }
        $left = New-Object byte[] (64 * 1024)
        $right = New-Object byte[] (64 * 1024)
        $Lock.stream.Position = 0
        $candidate.Position = 0
        while ($true) {
            $leftCount = $Lock.stream.Read($left, 0, $left.Length)
            $rightCount = $candidate.Read($right, 0, $right.Length)
            if ($leftCount -ne $rightCount) { return $false }
            if ($leftCount -eq 0) { return $true }
            for ($index = 0; $index -lt $leftCount; $index++) {
                if ($left[$index] -ne $right[$index]) { return $false }
            }
        }
    }
    catch { throw $Code }
    finally {
        $Lock.stream.Position = 0
        if ($null -ne $candidate) { $candidate.Dispose() }
    }
}

function Copy-StreamToExclusiveOutput([IO.Stream]$Source, [string]$Directory, [string]$Stem) {
    $output = New-ExclusiveOutputFile $Directory $Stem "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_FAILED"
    $completed = $false
    try {
        $Source.Position = 0
        $Source.CopyTo($output.stream)
        $output.stream.Flush($true)
        $completed = $true
        return [string]$output.path
    }
    finally {
        $Source.Position = 0
        $output.stream.Dispose()
        if (-not $completed -and [IO.File]::Exists([string]$output.path)) {
            try { [IO.File]::Delete([string]$output.path) } catch { }
        }
    }
}

function Move-NonformalOutput([string]$TemporaryPath, [string]$DestinationPath) {
    Assert-OrdinaryOutputLeaf $TemporaryPath "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_UNTRUSTED" -MustExist -SingleLink
    Assert-OrdinaryOutputLeaf $DestinationPath "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED"
    if ([IO.File]::Exists($DestinationPath)) {
        Assert-OrdinaryOutputLeaf $DestinationPath "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED" -MustExist -SingleLink
        Move-OutputFileReplaceExisting $TemporaryPath $DestinationPath "JOBFLOW_RELEASE_OUTPUT_COMMIT_FAILED"
    }
    else { [IO.File]::Move($TemporaryPath, $DestinationPath) }
}

function Invoke-RequiredPython([string[]]$Arguments, [string]$FallbackCode) {
    if ($null -eq $script:releasePythonRuntimeLocks -or $null -eq $script:releasePythonArtifactLock) {
        throw "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
    }
    Assert-InputFileLockUnchanged $script:releasePythonArtifactLock
    Assert-AllInputFileLocksUnchanged $script:releasePythonRuntimeLocks
    $result = Invoke-IsolatedPythonModule $script:pythonApplication $script:gitApplication "jobops.update_manifest" $Arguments
    Assert-InputFileLockUnchanged $script:releasePythonArtifactLock
    Assert-AllInputFileLocksUnchanged $script:releasePythonRuntimeLocks
    if ($result.exit_code -eq 0 -and [string]::IsNullOrWhiteSpace($result.stderr)) { return $result }
    $code = $null
    if (-not [string]::IsNullOrWhiteSpace($result.stdout)) {
        try {
            $parsed = $result.stdout.TrimStart([char]0xFEFF) | ConvertFrom-Json
            if ($parsed.code -is [string] -and [string]$parsed.code -match '^JOBFLOW_[A-Z0-9_]+$') {
                $code = [string]$parsed.code
            }
        }
        catch { $code = $null }
    }
    if ([string]::IsNullOrWhiteSpace($code)) { $code = $FallbackCode }
    throw $code
}

function Invoke-RequiredPythonCanonicalOutput(
    [string[]]$Arguments,
    [string]$Directory,
    [string]$Stem,
    [string]$FallbackCode
) {
    if ($null -eq $script:releasePythonRuntimeLocks -or $null -eq $script:releasePythonArtifactLock) {
        throw "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
    }
    Assert-InputFileLockUnchanged $script:releasePythonArtifactLock
    Assert-AllInputFileLocksUnchanged $script:releasePythonRuntimeLocks
    if (
        [string]::IsNullOrWhiteSpace([string]$script:isolatedPythonSource) -or
        [string]::IsNullOrWhiteSpace([string]$script:isolatedPythonProjectRoot) -or
        $null -eq $script:protectedStagingContext
    ) { throw "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID" }

    $source = [IO.Path]::GetFullPath([string]$script:isolatedPythonSource)
    $moduleProjectRoot = [IO.Path]::GetFullPath([string]$script:isolatedPythonProjectRoot)
    Assert-ProjectPath $source "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
    Assert-ProjectPath $moduleProjectRoot "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
    Assert-NoReparsePath $source "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID" -MustExist | Out-Null
    Assert-NoReparsePath $moduleProjectRoot "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID" -MustExist | Out-Null
    Assert-ProtectedStagingContext $script:protectedStagingContext "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
    $output = New-ExclusiveOutputFile $Directory $Stem "JOBFLOW_RELEASE_OUTPUT_TEMPORARY_FAILED"
    $completed = $false
    $process = $null
    $lock = $null
    try {
        $bootstrap = "import runpy,sys; source=sys.argv[1]; module=sys.argv[2]; sys.path.insert(0,source); sys.argv=sys.argv[2:]; runpy.run_module(module,run_name='__main__',alter_sys=True)"
        $pythonArguments = @(
            "-I", "-P", "-S", "-B", "-X", "utf8",
            "-c", $bootstrap, $source, "jobops.update_manifest"
        ) + @($Arguments) + @("--emit-canonical-stdout")
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = [string]$script:pythonApplication
        $start.Arguments = (($pythonArguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join " ")
        $start.WorkingDirectory = $moduleProjectRoot
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
        $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
        Set-SanitizedProcessEnvironment $start "python" ([string]$script:pythonApplication) $null $null
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $start
        if (-not $process.Start()) { throw $FallbackCode }
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $buffer = New-Object byte[] (16 * 1024)
        [long]$written = 0
        while (($count = $process.StandardOutput.BaseStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $written += $count
            if ($written -gt 65536) {
                try { $process.Kill() } catch { }
                throw $FallbackCode
            }
            $output.stream.Write($buffer, 0, $count)
        }
        $process.WaitForExit()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $output.stream.Flush($true)
        Assert-InputFileLockUnchanged $script:releasePythonArtifactLock
        Assert-AllInputFileLocksUnchanged $script:releasePythonRuntimeLocks

        if ($process.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace([string]$stderr)) {
            $code = $null
            if ($output.stream.Length -gt 0 -and $output.stream.Length -le 65536) {
                try {
                    $output.stream.Position = 0
                    $reader = [IO.StreamReader]::new(
                        $output.stream,
                        ([Text.UTF8Encoding]::new($false, $true)),
                        $true,
                        1024,
                        $true
                    )
                    try { $failureValue = $reader.ReadToEnd() | ConvertFrom-Json }
                    finally { $reader.Dispose(); $output.stream.Position = 0 }
                    if ($failureValue.code -is [string] -and [string]$failureValue.code -match '^JOBFLOW_[A-Z0-9_]+$') {
                        $code = [string]$failureValue.code
                    }
                }
                catch { $code = $null }
            }
            if ([string]::IsNullOrWhiteSpace($code)) { $code = $FallbackCode }
            throw $code
        }
        if ($written -lt 2 -or $written -ne $output.stream.Length) { throw $FallbackCode }
        $identity = Get-OpenOutputFileIdentity $output.stream $FallbackCode
        if ([long]$identity.link_count -ne 1) { throw $FallbackCode }
        $output.stream.Position = 0
        $reader = [IO.StreamReader]::new(
            $output.stream,
            ([Text.UTF8Encoding]::new($false, $true)),
            $true,
            1024,
            $true
        )
        try {
            $canonicalText = $reader.ReadToEnd()
            if (
                [string]::IsNullOrWhiteSpace($canonicalText) -or
                $canonicalText.Length -ne $canonicalText.Trim().Length -or
                -not $canonicalText.StartsWith("{", [StringComparison]::Ordinal) -or
                -not $canonicalText.EndsWith("}", [StringComparison]::Ordinal)
            ) { throw $FallbackCode }
            $canonicalValue = $canonicalText | ConvertFrom-Json
            if ($null -eq $canonicalValue -or $canonicalValue -is [Array]) { throw $FallbackCode }
        }
        catch { throw $FallbackCode }
        finally { $reader.Dispose(); $output.stream.Position = 0 }
        $lock = Convert-NewReleaseFileToReadLock $output 65536 $FallbackCode
        $completed = $true
        return $lock
    }
    finally {
        if ($null -ne $process) { $process.Dispose() }
        if (-not $completed) {
            if ($null -ne $lock) {
                try { Remove-ProtectedStagedFileLock $lock } catch { }
            }
            elseif ($null -ne $output -and $null -ne $output.stream) {
                try { Remove-NewReleaseFileOutput $output } catch { }
            }
        }
    }
}

function Get-TrustedUtcNow {
    return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ", [Globalization.CultureInfo]::InvariantCulture)
}

function Get-PresignIssuedAt([object]$ManifestLock) {
    try {
        $ManifestLock.stream.Position = 0
        $reader = [IO.StreamReader]::new(
            $ManifestLock.stream,
            ([Text.UTF8Encoding]::new($false, $true)),
            $true,
            1024,
            $true
        )
        try { $value = $reader.ReadToEnd() | ConvertFrom-Json }
        finally { $reader.Dispose(); $ManifestLock.stream.Position = 0 }
    }
    catch { throw "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID" }
    if (
        -not (Test-JsonInteger $value.schema_version 2) -or
        -not ($value.issued_at_utc -is [string]) -or
        [string]$value.issued_at_utc -notmatch '^20[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?Z$'
    ) { throw "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID" }
    return [string]$value.issued_at_utc
}

function Assert-RequiredText([string]$Value, [string]$Pattern, [string]$Code) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch $Pattern) { throw $Code }
}

function Read-LockedJsonObject([object]$Lock, [string]$Code) {
    try {
        $Lock.stream.Position = 0
        $reader = [IO.StreamReader]::new(
            $Lock.stream,
            ([Text.UTF8Encoding]::new($false, $true)),
            $true,
            1024,
            $true
        )
        try { $value = $reader.ReadToEnd() | ConvertFrom-Json }
        finally { $reader.Dispose(); $Lock.stream.Position = 0 }
    }
    catch { throw $Code }
    if ($null -eq $value) { throw $Code }
    return $value
}

function ConvertTo-CanonicalJsonStringLiteral([string]$Value, [string]$Code) {
    $builder = [Text.StringBuilder]::new($Value.Length + 2)
    [void]$builder.Append([char]34)
    for ($index = 0; $index -lt $Value.Length; $index += 1) {
        $character = $Value[$index]
        $ordinal = [int]$character
        switch ($ordinal) {
            8 { [void]$builder.Append('\b'); continue }
            9 { [void]$builder.Append('\t'); continue }
            10 { [void]$builder.Append('\n'); continue }
            12 { [void]$builder.Append('\f'); continue }
            13 { [void]$builder.Append('\r'); continue }
            34 { [void]$builder.Append('\"'); continue }
            92 { [void]$builder.Append('\\'); continue }
        }
        if ($ordinal -lt 32) {
            [void]$builder.Append('\u')
            [void]$builder.Append($ordinal.ToString('x4', [Globalization.CultureInfo]::InvariantCulture))
            continue
        }
        if ([char]::IsHighSurrogate($character)) {
            if ($index + 1 -ge $Value.Length -or -not [char]::IsLowSurrogate($Value[$index + 1])) {
                throw $Code
            }
            [void]$builder.Append($character)
            $index += 1
            [void]$builder.Append($Value[$index])
            continue
        }
        if ([char]::IsLowSurrogate($character)) { throw $Code }
        [void]$builder.Append($character)
    }
    [void]$builder.Append([char]34)
    return $builder.ToString()
}

function ConvertTo-CanonicalJsonText([object]$Value, [string]$Code) {
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [string]) { return ConvertTo-CanonicalJsonStringLiteral ([string]$Value) $Code }
    if ($Value -is [bool]) {
        if ([bool]$Value) { return 'true' }
        return 'false'
    }
    if (
        $Value -is [byte] -or $Value -is [sbyte] -or
        $Value -is [int16] -or $Value -is [uint16] -or
        $Value -is [int32] -or $Value -is [uint32] -or
        $Value -is [int64] -or $Value -is [uint64]
    ) {
        return ([Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture))
    }
    if ($Value -is [Array] -or $Value -is [Collections.IList]) {
        $items = New-Object Collections.Generic.List[string]
        foreach ($item in $Value) { $items.Add((ConvertTo-CanonicalJsonText $item $Code)) }
        return '[' + [string]::Join(',', $items.ToArray()) + ']'
    }

    $propertyValues = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    if ($Value -is [Collections.IDictionary]) {
        foreach ($key in $Value.Keys) {
            if (-not ($key -is [string])) { throw $Code }
            if ($propertyValues.ContainsKey([string]$key)) { throw $Code }
            $propertyValues.Add([string]$key, $Value[$key])
        }
    }
    elseif ($Value -is [Management.Automation.PSCustomObject]) {
        foreach ($property in $Value.PSObject.Properties) {
            if ($propertyValues.ContainsKey([string]$property.Name)) { throw $Code }
            $propertyValues.Add([string]$property.Name, $property.Value)
        }
    }
    else { throw $Code }

    [string[]]$names = @($propertyValues.Keys)
    [Array]::Sort($names, [StringComparer]::Ordinal)
    $members = New-Object Collections.Generic.List[string]
    foreach ($name in $names) {
        $members.Add(
            (ConvertTo-CanonicalJsonStringLiteral $name $Code) + ':' +
            (ConvertTo-CanonicalJsonText $propertyValues[$name] $Code)
        )
    }
    return '{' + [string]::Join(',', $members.ToArray()) + '}'
}

function Read-LockedCanonicalJsonObject(
    [object]$Lock,
    [string]$Code,
    [long]$MaximumBytes = 262144
) {
    try {
        $length = [long]$Lock.stream.Length
        if ($MaximumBytes -lt 2 -or $MaximumBytes -gt [int]::MaxValue) { throw $Code }
        if ($length -lt 2 -or $length -gt $MaximumBytes) { throw $Code }
        $bytes = New-Object byte[] ([int]$length)
        $Lock.stream.Position = 0
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $Lock.stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) { throw $Code }
            $offset += $read
        }
        $strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
        $rawText = $strictUtf8.GetString($bytes)
        $value = $rawText | ConvertFrom-Json
        if ($null -eq $value -or -not ($value -is [Management.Automation.PSCustomObject])) { throw $Code }
        $canonicalText = ConvertTo-CanonicalJsonText $value $Code
        $canonicalBytes = $strictUtf8.GetBytes($canonicalText)
        if ($canonicalBytes.Length -ne $bytes.Length) { throw $Code }
        for ($index = 0; $index -lt $bytes.Length; $index += 1) {
            if ($bytes[$index] -ne $canonicalBytes[$index]) { throw $Code }
        }
        return $value
    }
    catch { throw $Code }
    finally { $Lock.stream.Position = 0 }
}

function Assert-RuntimeEvidenceApplicationWheelProvenanceBinding(
    [object]$Closure,
    [object]$RuntimeEvidence,
    [string]$Code
) {
    try {
        if (
            $null -eq $Closure -or $null -eq $RuntimeEvidence -or
            -not ($Closure -is [Management.Automation.PSCustomObject]) -or
            -not ($RuntimeEvidence -is [Management.Automation.PSCustomObject])
        ) { throw $Code }
        $closureBuildInputsProperty = $Closure.PSObject.Properties['build_inputs']
        $evidenceBuildInputsProperty = $RuntimeEvidence.PSObject.Properties['build_inputs']
        if ($null -eq $closureBuildInputsProperty -or $null -eq $evidenceBuildInputsProperty) {
            throw $Code
        }
        $closureBuildInputs = $closureBuildInputsProperty.Value
        $evidenceBuildInputs = $evidenceBuildInputsProperty.Value
        if (
            -not ($closureBuildInputs -is [Management.Automation.PSCustomObject]) -or
            -not ($evidenceBuildInputs -is [Management.Automation.PSCustomObject])
        ) { throw $Code }
        $closureWheelProperty = $closureBuildInputs.PSObject.Properties['application_wheel_sha256']
        $evidenceWheelProperty = $evidenceBuildInputs.PSObject.Properties['application_wheel_sha256']
        $closureProvenanceProperty = $closureBuildInputs.PSObject.Properties['application_wheel_provenance']
        $evidenceProvenanceProperty = $evidenceBuildInputs.PSObject.Properties['application_wheel_provenance']
        if (
            $null -eq $closureWheelProperty -or $null -eq $evidenceWheelProperty -or
            $null -eq $closureProvenanceProperty -or $null -eq $evidenceProvenanceProperty -or
            -not ($closureWheelProperty.Value -is [string]) -or
            [string]$closureWheelProperty.Value -notmatch '^sha256:[0-9a-f]{64}$' -or
            [string]$closureWheelProperty.Value -cne [string]$evidenceWheelProperty.Value
        ) { throw $Code }
        $closureProvenance = ConvertTo-CanonicalJsonText $closureProvenanceProperty.Value $Code
        $evidenceProvenance = ConvertTo-CanonicalJsonText $evidenceProvenanceProperty.Value $Code
        if ($closureProvenance -cne $evidenceProvenance) { throw $Code }
    }
    catch { throw $Code }
}

function Assert-CanonicalEvidenceWindow(
    [object]$Evidence,
    [string]$TrustedTime,
    [int]$MaximumHours,
    [string]$Code
) {
    try {
        if (-not ($Evidence.issued_at_utc -is [string]) -or -not ($Evidence.expires_at_utc -is [string])) {
            throw $Code
        }
        $formats = [string[]]@('yyyy-MM-ddTHH:mm:ssZ', 'yyyy-MM-ddTHH:mm:ss.FFFFFFZ')
        $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
        $issued = [DateTimeOffset]::ParseExact(
            [string]$Evidence.issued_at_utc,
            $formats,
            [Globalization.CultureInfo]::InvariantCulture,
            $styles
        )
        $expires = [DateTimeOffset]::ParseExact(
            [string]$Evidence.expires_at_utc,
            $formats,
            [Globalization.CultureInfo]::InvariantCulture,
            $styles
        )
        $current = [DateTimeOffset]::ParseExact(
            $TrustedTime,
            $formats,
            [Globalization.CultureInfo]::InvariantCulture,
            $styles
        )
        if (
            $issued -ge $expires -or
            ($expires - $issued) -gt [TimeSpan]::FromHours($MaximumHours) -or
            $issued -gt $current.AddMinutes(5) -or
            $current -ge $expires
        ) { throw $Code }
    }
    catch { throw $Code }
}

function Assert-CleanReleaseCandidateRepository([object]$Candidate, [object]$PyprojectLock) {
    $gitStatusCommand = Invoke-SanitizedGit $script:gitApplication @("status", "--porcelain", "--untracked-files=all")
    if ($gitStatusCommand.exit_code -ne 0 -or -not [string]::IsNullOrWhiteSpace($gitStatusCommand.stderr)) {
        throw "JOBFLOW_RELEASE_GIT_FAILED"
    }
    if (-not [string]::IsNullOrWhiteSpace($gitStatusCommand.stdout)) { throw "JOBFLOW_RELEASE_WORKTREE_NOT_CLEAN" }
    $headCommand = Invoke-SanitizedGit $script:gitApplication @("rev-parse", "HEAD")
    $head = $headCommand.stdout.Trim()
    if (
        $headCommand.exit_code -ne 0 -or
        -not [string]::IsNullOrWhiteSpace($headCommand.stderr) -or
        $head -cne [string]$Candidate.commit
    ) { throw "JOBFLOW_RELEASE_COMMIT_MISMATCH" }
    Assert-InputFileLockUnchanged $PyprojectLock
    if ((Get-ProjectVersion $PyprojectLock) -cne [string]$Candidate.version) {
        throw "JOBFLOW_RELEASE_PROJECT_VERSION_MISMATCH"
    }
}

function Complete-ProtectedSigningOutcome(
    [string]$PrimaryFailure,
    [bool]$CleanupFailed,
    [string]$Result
) {
    if ($CleanupFailed) {
        if (-not [string]::IsNullOrWhiteSpace($PrimaryFailure)) {
            # Preserve the primary fixed failure (especially durable recovery
            # gates) while making non-authoritative cleanup residue visible.
            [Console]::Error.WriteLine("JOBFLOW_RELEASE_CLEANUP_RESIDUE_WARNING")
        }
        else { throw "JOBFLOW_RELEASE_CLEANUP_RESIDUE" }
    }
    if (-not [string]::IsNullOrWhiteSpace($PrimaryFailure)) { throw $PrimaryFailure }
    return $Result
}

function Invoke-ProtectedSigningHandoff {
    if ($Stage -cne "Prepare" -and $Stage -cne "Finalize") { throw "JOBFLOW_PROTECTED_SIGNING_STAGE_REQUIRED" }
    foreach ($pathValue in @(
        $CompleteRuntimeArchivePath,
        $RuntimeClosurePath,
        $RuntimeBuildEvidencePath,
        $PublisherEvidencePath,
        $ReleasePythonArtifactPath
    )) {
        if ([string]::IsNullOrWhiteSpace($pathValue)) { throw "JOBFLOW_PROTECTED_SIGNING_INPUT_REQUIRED" }
    }
    Assert-RequiredText $PredecessorMinimumVersion '^[0-9]+\.[0-9]+\.[0-9]+$' "JOBFLOW_RELEASE_VERSION_POLICY_INVALID"
    Assert-RequiredText $MinimumUpdaterVersion '^[0-9]+\.[0-9]+\.[0-9]+$' "JOBFLOW_RELEASE_VERSION_POLICY_INVALID"
    Assert-RequiredText $MinimumBootstrapVersion '^[0-9]+\.[0-9]+\.[0-9]+$' "JOBFLOW_RELEASE_VERSION_POLICY_INVALID"
    if ($Stage -ceq "Prepare") {
        if (
            -not [string]::IsNullOrWhiteSpace($PresignManifestPath) -or
            -not [string]::IsNullOrWhiteSpace($SigningRequestPath) -or
            -not [string]::IsNullOrWhiteSpace($SignatureEnvelopePath)
        ) { throw "JOBFLOW_PROTECTED_SIGNING_STAGE_INPUT_INVALID" }
    }
    else {
        foreach ($pathValue in @($PresignManifestPath, $SigningRequestPath, $SignatureEnvelopePath)) {
            if ([string]::IsNullOrWhiteSpace($pathValue)) { throw "JOBFLOW_PROTECTED_SIGNING_INPUT_REQUIRED" }
        }
    }

    $toolchainPolicy = $null
    $script:pythonApplication = $null
    $script:releasePythonArtifactLock = $null
    $script:releasePythonRuntimeLocks = $null
    $script:gitApplication = $null
    $nodeApplication = $null
    $gitLock = $null
    $nodeLock = $null
    $inputLocks = New-Object Collections.Generic.List[object]
    $stagingLocks = New-Object Collections.Generic.List[object]
    $stagingBindings = New-Object Collections.Generic.List[object]
    $stagingPaths = New-Object Collections.Generic.List[string]
    $generatedLocks = New-Object Collections.Generic.List[object]
    $temporaryPaths = New-Object Collections.Generic.List[string]
    $outputLock = $null
    $distRoot = $null
    $stagingRoot = $null
    $stagingContext = $null
    $script:releaseDistContext = $null
    $script:protectedStagingContext = $null
    $handoffFailure = $null
    $handoffResult = $null
    $cleanupFailed = $false
    try {
        Initialize-JobFlowReleaseFileIdentityApi
        $toolchainPolicyPath = [IO.Path]::GetFullPath((Join-Path $projectRoot "config\release-toolchain.json"))
        $toolchainPolicyLock = Enter-InputFileLock $toolchainPolicyPath 262144 "JOBFLOW_RELEASE_TOOLCHAIN_POLICY_INVALID"
        $inputLocks.Add($toolchainPolicyLock)
        $toolchainPolicy = Get-ReleaseToolchainPolicy $toolchainPolicyLock
        $script:gitApplication = Find-GitApplication
        $nodeApplication = Find-NodeApplication
        $gitLock = Enter-AuthenticatedToolLock "git" $script:gitApplication $toolchainPolicy
        $nodeLock = Enter-AuthenticatedToolLock "node" $nodeApplication $toolchainPolicy

        $reportPath = Join-Path $projectRoot "reports\release-candidate.json"
        if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
            throw "JOBFLOW_RELEASE_CANDIDATE_REPORT_MISSING"
        }
        $candidateLock = Enter-InputFileLock $reportPath 262144 "JOBFLOW_RELEASE_CANDIDATE_REPORT_INVALID"
        $inputLocks.Add($candidateLock)
        $candidate = Read-LockedJsonObject $candidateLock "JOBFLOW_RELEASE_CANDIDATE_REPORT_INVALID"
        Assert-ReleaseCandidate $candidate

        # The isolated producer is copied from this small, explicitly locked
        # clean-HEAD closure.  Python never imports release logic from the live
        # worktree, and the staged root contains the exact schemas and pinned
        # policy files that the producer reads at runtime.
        $producerSourceSpecs = @(
            [pscustomobject]@{ relative = ".jobops-root"; maximum = 4096 },
            [pscustomobject]@{ relative = "pyproject.toml"; maximum = 262144 },
            [pscustomobject]@{ relative = "src\jobops\__init__.py"; maximum = 262144 },
            [pscustomobject]@{ relative = "src\jobops\update_manifest.py"; maximum = 2097152 },
            [pscustomobject]@{ relative = "src\jobops\errors.py"; maximum = 262144 },
            [pscustomobject]@{ relative = "src\jobops\publisher_attestation.py"; maximum = 2097152 },
            [pscustomobject]@{ relative = "src\jobops\release_candidate.py"; maximum = 2097152 },
            [pscustomobject]@{ relative = "src\jobops\runtime_schema.py"; maximum = 2097152 },
            [pscustomobject]@{ relative = "src\jobops\util.py"; maximum = 262144 },
            [pscustomobject]@{ relative = "src\jobops\public_release.py"; maximum = 2097152 },
            [pscustomobject]@{ relative = "src\jobops\release_toolchain.py"; maximum = 2097152 },
            [pscustomobject]@{ relative = "schemas\update-manifest-v2.schema.json"; maximum = 262144 },
            [pscustomobject]@{ relative = "schemas\runtime-closure.schema.json"; maximum = 262144 },
            [pscustomobject]@{ relative = "schemas\runtime-build-evidence-v1.schema.json"; maximum = 262144 },
            [pscustomobject]@{ relative = "schemas\publisher-evidence-v1.schema.json"; maximum = 262144 },
            [pscustomobject]@{ relative = "config\update-channel.json"; maximum = 262144 },
            [pscustomobject]@{ relative = "config\windows-runtime-source.json"; maximum = 262144 },
            [pscustomobject]@{ relative = "config\windows-cp313-runtime.lock"; maximum = 262144 },
            [pscustomobject]@{ relative = "config\windows-cp313-build.lock"; maximum = 262144 }
        )
        $producerSources = New-Object Collections.Generic.List[object]
        $producerSources.Add([pscustomobject]@{
            relative = "config\release-toolchain.json"
            maximum = 262144
            lock = $toolchainPolicyLock
        })
        $pyprojectLock = $null
        $runtimeSourcePolicyLock = $null
        foreach ($spec in $producerSourceSpecs) {
            $sourcePath = [IO.Path]::GetFullPath((Join-Path $projectRoot ([string]$spec.relative)))
            $sourceLock = Enter-InputFileLock $sourcePath ([long]$spec.maximum) "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID"
            $inputLocks.Add($sourceLock)
            $producerSources.Add([pscustomobject]@{
                relative = [string]$spec.relative
                maximum = [long]$spec.maximum
                lock = $sourceLock
            })
            if ([string]$spec.relative -ceq "pyproject.toml") { $pyprojectLock = $sourceLock }
            if ([string]$spec.relative -ceq "config\windows-runtime-source.json") {
                $runtimeSourcePolicyLock = $sourceLock
            }
        }
        if ($null -eq $pyprojectLock -or $null -eq $runtimeSourcePolicyLock) {
            throw "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
        }
        # A clean check while every producer byte is held with delete/write
        # sharing denied proves that the closure is the exact current HEAD.
        Assert-CleanReleaseCandidateRepository $candidate $pyprojectLock

        $releasePythonPolicy = Get-ReleasePythonRuntimePolicy $toolchainPolicy $runtimeSourcePolicyLock
        $releasePythonArtifactLock = Enter-InputFileLock `
            $ReleasePythonArtifactPath 134217728 "JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID"
        $inputLocks.Add($releasePythonArtifactLock)
        $script:releasePythonArtifactLock = $releasePythonArtifactLock

        $archiveLock = Enter-InputFileLock $CompleteRuntimeArchivePath 1610612736 "JOBFLOW_RELEASE_ARCHIVE_INPUT_INVALID"
        $inputLocks.Add($archiveLock)
        $closureLock = Enter-InputFileLock $RuntimeClosurePath 16777216 "JOBFLOW_RELEASE_CLOSURE_INPUT_INVALID"
        $inputLocks.Add($closureLock)
        $runtimeEvidenceLock = Enter-InputFileLock $RuntimeBuildEvidencePath 262144 "JOBFLOW_RELEASE_RUNTIME_EVIDENCE_INPUT_INVALID"
        $inputLocks.Add($runtimeEvidenceLock)
        $publisherEvidenceLock = Enter-InputFileLock $PublisherEvidencePath 262144 "JOBFLOW_RELEASE_PUBLISHER_EVIDENCE_INPUT_INVALID"
        $inputLocks.Add($publisherEvidenceLock)
        $legacyLock = $null
        if (-not [string]::IsNullOrWhiteSpace($LegacyV1PredecessorsPath)) {
            $legacyLock = Enter-InputFileLock $LegacyV1PredecessorsPath 65536 "JOBFLOW_RELEASE_LEGACY_ALLOWLIST_INPUT_INVALID"
            $inputLocks.Add($legacyLock)
        }

        $currentTrustedTime = Get-TrustedUtcNow
        $issuedAt = $currentTrustedTime
        $closureValue = Read-LockedCanonicalJsonObject `
            $closureLock "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED" 16777216
        $runtimeEvidenceValue = Read-LockedCanonicalJsonObject `
            $runtimeEvidenceLock "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED"
        $publisherEvidenceValue = Read-LockedCanonicalJsonObject `
            $publisherEvidenceLock "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED"
        Assert-RuntimeEvidenceApplicationWheelProvenanceBinding `
            $closureValue $runtimeEvidenceValue "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED"
        Assert-CanonicalEvidenceWindow `
            $runtimeEvidenceValue $currentTrustedTime 24 "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED"
        Assert-CanonicalEvidenceWindow `
            $publisherEvidenceValue $currentTrustedTime 4 "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED"
        $presignManifestLock = $null
        $signingRequestLock = $null
        $signatureLock = $null
        if ($Stage -ceq "Finalize") {
            $presignManifestLock = Enter-InputFileLock $PresignManifestPath 65536 "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID"
            $inputLocks.Add($presignManifestLock)
            $signingRequestLock = Enter-InputFileLock $SigningRequestPath 65536 "JOBFLOW_RELEASE_SIGNING_REQUEST_INVALID"
            $inputLocks.Add($signingRequestLock)
            $signatureLock = Enter-InputFileLock $SignatureEnvelopePath 16384 "JOBFLOW_RELEASE_SIGNATURE_ENVELOPE_INVALID"
            $inputLocks.Add($signatureLock)
            $issuedAt = Get-PresignIssuedAt $presignManifestLock
            try {
                $issuedInstant = [DateTimeOffset]::ParseExact(
                    $issuedAt,
                    [string[]]@("yyyy-MM-ddTHH:mm:ssZ", "yyyy-MM-ddTHH:mm:ss.FFFFFFZ"),
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
                )
                $currentInstant = [DateTimeOffset]::ParseExact(
                    $currentTrustedTime,
                    "yyyy-MM-ddTHH:mm:ss.FFFFFFZ",
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
                )
            }
            catch { throw "JOBFLOW_RELEASE_PRESIGN_TIME_INVALID" }
            if ($issuedInstant -gt $currentInstant) { throw "JOBFLOW_RELEASE_PRESIGN_TIME_INVALID" }
        }

        $distRoot = Join-Path $projectRoot "dist"
        [IO.Directory]::CreateDirectory($distRoot) | Out-Null
        Assert-ProjectPath $distRoot "JOBFLOW_RELEASE_OUTPUT_PATH_UNTRUSTED"
        Assert-NoReparsePath $distRoot "JOBFLOW_RELEASE_OUTPUT_PATH_UNTRUSTED" -MustExist | Out-Null
        $stagingContext = New-ProtectedInputStagingRoot $distRoot
        $stagingRoot = [string]$stagingContext.Path
        $script:protectedStagingContext = $stagingContext
        $script:releaseDistContext = [pscustomobject]@{
            Path = [IO.Path]::GetFullPath($distRoot)
            Locks = @($stagingContext.AncestryLocks)
        }
        foreach ($directory in @("inputs", "schemas", "config", "python-runtime")) {
            [void](New-ProtectedStagingDirectory $stagingContext $directory)
        }
        foreach ($source in $producerSources) {
            if ([string]$source.relative -like "src\jobops\*.py") { continue }
            [void](Add-ProtectedStagingBinding `
                $source.lock $stagingContext ([string]$source.relative) ([long]$source.maximum) `
                "JOBFLOW_RELEASE_PYTHON_SOURCE_INVALID" $stagingLocks $stagingBindings $stagingPaths)
        }
        $sealedProducerLock = New-SealedReleaseProducerArchive $stagingContext $producerSources $stagingLocks
        $releasePythonRuntime = Expand-LockedReleasePythonRuntime `
            $releasePythonArtifactLock $releasePythonPolicy $stagingContext $stagingLocks $stagingPaths
        $script:pythonApplication = [string]$releasePythonRuntime.python_path
        $script:releasePythonRuntimeLocks = $releasePythonRuntime.locks
        $expectedArchiveName = "JobFlow-v" + [string]$candidate.version + "-windows-x64-complete.zip"
        $stagedArchiveLock = Add-ProtectedStagingBinding $archiveLock $stagingContext ("inputs\" + $expectedArchiveName) 1610612736 "JOBFLOW_RELEASE_ARCHIVE_INPUT_INVALID" $stagingLocks $stagingBindings $stagingPaths
        $stagedClosureLock = Add-ProtectedStagingBinding $closureLock $stagingContext "inputs\runtime-closure.json" 16777216 "JOBFLOW_RELEASE_CLOSURE_INPUT_INVALID" $stagingLocks $stagingBindings $stagingPaths
        $stagedRuntimeEvidenceLock = Add-ProtectedStagingBinding $runtimeEvidenceLock $stagingContext "inputs\runtime-build-evidence.json" 262144 "JOBFLOW_RELEASE_RUNTIME_EVIDENCE_INPUT_INVALID" $stagingLocks $stagingBindings $stagingPaths
        $stagedPublisherEvidenceLock = Add-ProtectedStagingBinding $publisherEvidenceLock $stagingContext "inputs\publisher-evidence.json" 262144 "JOBFLOW_RELEASE_PUBLISHER_EVIDENCE_INPUT_INVALID" $stagingLocks $stagingBindings $stagingPaths
        $stagedLegacyLock = $null
        if ($null -ne $legacyLock) {
            $stagedLegacyLock = Add-ProtectedStagingBinding $legacyLock $stagingContext "inputs\legacy-v1-predecessors.json" 65536 "JOBFLOW_RELEASE_LEGACY_ALLOWLIST_INPUT_INVALID" $stagingLocks $stagingBindings $stagingPaths
        }
        $stagedPresignManifestLock = $null
        $stagedSigningRequestLock = $null
        $stagedSignatureLock = $null
        if ($Stage -ceq "Finalize") {
            $stagedPresignManifestLock = Add-ProtectedStagingBinding $presignManifestLock $stagingContext "inputs\presign-manifest.json" 65536 "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID" $stagingLocks $stagingBindings $stagingPaths
            $stagedSigningRequestLock = Add-ProtectedStagingBinding $signingRequestLock $stagingContext "inputs\signing-request.json" 65536 "JOBFLOW_RELEASE_SIGNING_REQUEST_INVALID" $stagingLocks $stagingBindings $stagingPaths
            $stagedSignatureLock = Add-ProtectedStagingBinding $signatureLock $stagingContext "inputs\signature-envelope.json" 16384 "JOBFLOW_RELEASE_SIGNATURE_ENVELOPE_INVALID" $stagingLocks $stagingBindings $stagingPaths
        }
        $script:isolatedPythonProjectRoot = $stagingRoot
        $script:isolatedPythonSource = [string]$sealedProducerLock.path
        Assert-ProtectedStagingContext $stagingContext "JOBFLOW_RELEASE_INPUT_STAGING_CHANGED"
        Assert-AllProtectedStagingBindings $stagingBindings
        Assert-CleanReleaseCandidateRepository $candidate $pyprojectLock

        $buildArguments = @(
            "build",
            "--archive", [string]$stagedArchiveLock.path,
            "--version", [string]$candidate.version,
            "--commit", [string]$candidate.commit,
            "--runtime-closure", [string]$stagedClosureLock.path,
            "--runtime-build-evidence", [string]$stagedRuntimeEvidenceLock.path,
            "--publisher-evidence", [string]$stagedPublisherEvidenceLock.path,
            "--predecessor-minimum-version", $PredecessorMinimumVersion,
            "--minimum-updater-version", $MinimumUpdaterVersion,
            "--minimum-bootstrap-version", $MinimumBootstrapVersion,
            "--issued-at-utc", $issuedAt,
            "--validation-time-utc", $currentTrustedTime,
            "--schema-dir", (Join-Path $stagingRoot "schemas"),
            "--channel", (Join-Path $stagingRoot "config\update-channel.json")
        )
        if ($null -ne $stagedLegacyLock) {
            $buildArguments += @(
                "--legacy-v1-predecessors", [string]$stagedLegacyLock.path
            )
        }
        $builtManifestLock = Invoke-RequiredPythonCanonicalOutput `
            $buildArguments $distRoot "JobFlow-update-manifest.presign" "JOBFLOW_RELEASE_MANIFEST_BUILD_FAILED"
        $manifestTemporary = [string]$builtManifestLock.path
        $generatedLocks.Add($builtManifestLock)
        Assert-OrdinaryOutputLeaf $manifestTemporary "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID" -MustExist -SingleLink
        Assert-AllProtectedStagingBindings $stagingBindings
        $builtManifestValue = Read-LockedJsonObject $builtManifestLock "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID"
        Assert-PresignManifestArchiveIdentity $builtManifestValue $candidate $archiveLock

        $requestArguments = @(
            "presign-request",
            "--manifest", $manifestTemporary,
            "--runtime-closure", [string]$stagedClosureLock.path,
            "--runtime-build-evidence", [string]$stagedRuntimeEvidenceLock.path,
            "--publisher-evidence", [string]$stagedPublisherEvidenceLock.path,
            "--schema-dir", (Join-Path $stagingRoot "schemas"),
            "--channel", (Join-Path $stagingRoot "config\update-channel.json")
        )
        if ($null -ne $stagedLegacyLock) {
            $requestArguments += @(
                "--legacy-v1-predecessors", [string]$stagedLegacyLock.path
            )
        }
        $builtRequestLock = Invoke-RequiredPythonCanonicalOutput `
            $requestArguments $distRoot "JobFlow-update-signing-request" "JOBFLOW_RELEASE_SIGNING_REQUEST_BUILD_FAILED"
        $requestTemporary = [string]$builtRequestLock.path
        $generatedLocks.Add($builtRequestLock)
        Assert-OrdinaryOutputLeaf $requestTemporary "JOBFLOW_RELEASE_SIGNING_REQUEST_INVALID" -MustExist -SingleLink

        Assert-AllProtectedStagingBindings $stagingBindings
        Assert-InputFileLockUnchanged $builtManifestLock
        Assert-InputFileLockUnchanged $builtRequestLock
        foreach ($lock in $inputLocks) { Assert-InputFileLockUnchanged $lock }
        Assert-AuthenticatedToolUnchanged $gitLock $toolchainPolicy
        Assert-AuthenticatedToolUnchanged $nodeLock $toolchainPolicy
        Assert-CleanReleaseCandidateRepository $candidate $pyprojectLock

        if ($Stage -ceq "Prepare") {
            $manifestDestination = Join-Path $distRoot "JobFlow-update-manifest.presign.json"
            $requestDestination = Join-Path $distRoot "JobFlow-update-signing-request.json"
            $outputLockPath = Join-Path $distRoot ".signed-update-output.lock"
            $outputLock = Enter-OutputTransactionLock $outputLockPath
            foreach ($destination in @($manifestDestination, $requestDestination)) {
                Assert-OrdinaryOutputLeaf $destination "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED"
                if ([IO.File]::Exists($destination)) {
                    Assert-OrdinaryOutputLeaf $destination "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED" -MustExist -SingleLink
                }
            }
            $presignRecord = $null
            $requestRecord = $null
            $preparePairResolved = $false
            try {
                Assert-InputFileLockUnchanged $builtManifestLock
                Assert-InputFileLockUnchanged $builtRequestLock
                $presignCommitTemporary = Copy-StreamToExclusiveOutput $builtManifestLock.stream $distRoot "JobFlow-update-manifest.presign.commit"
                $temporaryPaths.Add($presignCommitTemporary)
                $requestCommitTemporary = Copy-StreamToExclusiveOutput $builtRequestLock.stream $distRoot "JobFlow-update-signing-request.commit"
                $temporaryPaths.Add($requestCommitTemporary)
                $recordPair = New-OutputCommitRecordPair `
                    $presignCommitTemporary $manifestDestination `
                    $requestCommitTemporary $requestDestination
                $presignRecord = $recordPair.first
                $requestRecord = $recordPair.second
                $presignRecord.new_hash = [string]$builtManifestLock.sha256
                $requestRecord.new_hash = [string]$builtRequestLock.sha256
                Commit-TemporaryOutput $presignRecord
                $temporaryPaths.Remove($presignCommitTemporary) | Out-Null
                Commit-TemporaryOutput $requestRecord
                $temporaryPaths.Remove($requestCommitTemporary) | Out-Null
                if (
                    ("sha256:" + (Get-FileSha256 $manifestDestination)) -cne [string]$builtManifestLock.sha256 -or
                    ("sha256:" + (Get-FileSha256 $requestDestination)) -cne [string]$builtRequestLock.sha256
                ) { throw "JOBFLOW_RELEASE_OUTPUT_COMMIT_FAILED" }
                $preparePairResolved = $true
            }
            catch {
                $originalFailure = [string]$_.Exception.Message
                $rollbackFailed = $false
                foreach ($record in @($requestRecord, $presignRecord)) {
                    if ($null -eq $record) { continue }
                    try { Restore-CommittedOutput $record }
                    catch { $rollbackFailed = $true }
                }
                if ($rollbackFailed) { throw "JOBFLOW_RELEASE_PRESIGN_RECOVERY_REQUIRED" }
                $preparePairResolved = $true
                throw $originalFailure
            }
            finally {
                if ($preparePairResolved) {
                    if ($null -ne $presignRecord) { Remove-OutputCommitBackup $presignRecord }
                    if ($null -ne $requestRecord) { Remove-OutputCommitBackup $requestRecord }
                }
            }
            throw "JOBFLOW_PROTECTED_SIGNATURE_REQUIRED"
        }

        if (-not (Test-LockedBytesEqual $stagedPresignManifestLock ([string]$builtManifestLock.path) "JOBFLOW_RELEASE_PRESIGN_MANIFEST_INVALID")) {
            throw "JOBFLOW_RELEASE_PRESIGN_MANIFEST_MISMATCH"
        }
        if (-not (Test-LockedBytesEqual $stagedSigningRequestLock ([string]$builtRequestLock.path) "JOBFLOW_RELEASE_SIGNING_REQUEST_INVALID")) {
            throw "JOBFLOW_RELEASE_SIGNING_REQUEST_MISMATCH"
        }
        Invoke-RequiredPython @(
            "inspect",
            "--manifest", $manifestTemporary,
            "--signature", [string]$stagedSignatureLock.path,
            "--current-version", [string]$candidate.version,
            "--schema-dir", (Join-Path $stagingRoot "schemas"),
            "--channel", (Join-Path $stagingRoot "config\update-channel.json")
        ) "JOBFLOW_PROTECTED_SIGNATURE_INVALID" | Out-Null

        Assert-AllProtectedStagingBindings $stagingBindings
        Assert-InputFileLockUnchanged $builtManifestLock
        Assert-InputFileLockUnchanged $builtRequestLock
        foreach ($lock in $inputLocks) { Assert-InputFileLockUnchanged $lock }
        Assert-AuthenticatedToolUnchanged $gitLock $toolchainPolicy
        Assert-AuthenticatedToolUnchanged $nodeLock $toolchainPolicy
        Assert-CleanReleaseCandidateRepository $candidate $pyprojectLock

        $outputLockPath = Join-Path $distRoot ".signed-update-output.lock"
        $transactionMarkerPath = Join-Path $distRoot ".signed-update-output.transaction.json"
        $outputLock = Enter-OutputTransactionLock $outputLockPath
        [void](Recover-PendingOutputTransaction $transactionMarkerPath $distRoot)

        $manifestDestination = Join-Path $distRoot "JobFlow-update-manifest.json"
        $signatureDestination = Join-Path $distRoot "JobFlow-update-manifest.sig.json"
        $archiveDestination = Join-Path $distRoot $expectedArchiveName
        $runtimeEvidenceDestination = Join-Path $distRoot "JobFlow-runtime-build-evidence.json"
        $publisherEvidenceDestination = Join-Path $distRoot "JobFlow-publisher-evidence.json"
        $manifestRecord = $null
        $signatureRecord = $null
        $payloadRecords = New-Object Collections.Generic.List[object]
        $formalTransactionResolved = $false
        try {
            # The archive and its two canonical public evidence documents are
            # staged first under fixed names.  They are not authoritative until
            # the signed manifest pair commits below, and every failure rolls
            # them back to their prior verified bytes.
            $payloadSpecs = @(
                [pscustomobject]@{
                    lock = $stagedArchiveLock
                    destination = $archiveDestination
                    stem = "JobFlow-release-archive.commit"
                },
                [pscustomobject]@{
                    lock = $stagedRuntimeEvidenceLock
                    destination = $runtimeEvidenceDestination
                    stem = "JobFlow-runtime-build-evidence.commit"
                },
                [pscustomobject]@{
                    lock = $stagedPublisherEvidenceLock
                    destination = $publisherEvidenceDestination
                    stem = "JobFlow-publisher-evidence.commit"
                }
            )
            foreach ($spec in $payloadSpecs) {
                Assert-OrdinaryOutputLeaf `
                    ([string]$spec.destination) "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED"
                if ([IO.File]::Exists([string]$spec.destination)) {
                    Assert-OrdinaryOutputLeaf `
                        ([string]$spec.destination) "JOBFLOW_RELEASE_OUTPUT_DESTINATION_UNTRUSTED" `
                        -MustExist -SingleLink
                }
                Assert-InputFileLockUnchanged $spec.lock
                $payloadTemporary = Copy-StreamToExclusiveOutput `
                    $spec.lock.stream $distRoot ([string]$spec.stem)
                $temporaryPaths.Add($payloadTemporary)
                $payloadRecord = New-OutputCommitRecord `
                    $payloadTemporary ([string]$spec.destination)
                $payloadRecord.new_hash = [string]$spec.lock.sha256
                $payloadRecords.Add($payloadRecord)
                Commit-TemporaryOutput $payloadRecord
                $temporaryPaths.Remove($payloadTemporary) | Out-Null
                if (
                    ("sha256:" + (Get-FileSha256 ([string]$spec.destination))) -cne `
                        [string]$spec.lock.sha256
                ) { throw "JOBFLOW_RELEASE_OUTPUT_COMMIT_FAILED" }
            }

            Assert-InputFileLockUnchanged $builtManifestLock
            Assert-ProtectedStagingBinding $signatureLock $stagedSignatureLock
            $manifestCommitTemporary = Copy-StreamToExclusiveOutput $builtManifestLock.stream $distRoot "JobFlow-update-manifest.commit"
            $temporaryPaths.Add($manifestCommitTemporary)
            $signatureCommitTemporary = Copy-StreamToExclusiveOutput $stagedSignatureLock.stream $distRoot "JobFlow-update-manifest.sig.commit"
            $temporaryPaths.Add($signatureCommitTemporary)
            $recordPair = New-OutputCommitRecordPair `
                $manifestCommitTemporary $manifestDestination `
                $signatureCommitTemporary $signatureDestination
            $manifestRecord = $recordPair.first
            $signatureRecord = $recordPair.second
            $manifestRecord.new_hash = [string]$builtManifestLock.sha256
            $signatureRecord.new_hash = [string]$stagedSignatureLock.sha256
            Write-OutputTransactionMarker $transactionMarkerPath $manifestRecord $signatureRecord
            Commit-TemporaryOutput $manifestRecord
            $temporaryPaths.Remove($manifestCommitTemporary) | Out-Null
            Commit-TemporaryOutput $signatureRecord
            $temporaryPaths.Remove($signatureCommitTemporary) | Out-Null
            if (
                ("sha256:" + (Get-FileSha256 $manifestDestination)) -cne [string]$manifestRecord.new_hash -or
                ("sha256:" + (Get-FileSha256 $signatureDestination)) -cne [string]$signatureRecord.new_hash
            ) { throw "JOBFLOW_RELEASE_OUTPUT_COMMIT_FAILED" }
            Remove-OutputTransactionMarker $transactionMarkerPath
            $formalTransactionResolved = $true
        }
        catch {
            $originalFailure = [string]$_.Exception.Message
            $rollbackFailed = $false
            if ([IO.File]::Exists($transactionMarkerPath)) {
                try { Invoke-FormalOutputRollbackOrRequireRecovery $transactionMarkerPath $distRoot }
                catch { $rollbackFailed = $true }
            }
            for ($index = $payloadRecords.Count - 1; $index -ge 0; $index--) {
                try { Restore-CommittedOutput $payloadRecords[$index] }
                catch { $rollbackFailed = $true }
            }
            # Before the marker is durably written, no formal destination has
            # been changed.  Returned records may nevertheless own verified
            # pre-transaction backups; clean those without touching outputs.
            $formalTransactionResolved = $true
            if ($rollbackFailed) { throw "JOBFLOW_RELEASE_OUTPUT_RECOVERY_REQUIRED" }
            throw $originalFailure
        }
        finally {
            if ($formalTransactionResolved) {
                if ($null -ne $manifestRecord) { Remove-OutputCommitBackup $manifestRecord }
                if ($null -ne $signatureRecord) { Remove-OutputCommitBackup $signatureRecord }
                foreach ($record in $payloadRecords) { Remove-OutputCommitBackup $record }
            }
        }

        $handoffResult = [ordered]@{
            schema_version = 2
            status = "SIGNED_UPDATE_BUNDLE_READY"
            version = [string]$candidate.version
            manifest_sha256 = [string]$manifestRecord.new_hash
            signature_sha256 = [string]$signatureRecord.new_hash
            archive_sha256 = [string]$stagedArchiveLock.sha256
            runtime_build_evidence_sha256 = [string]$stagedRuntimeEvidenceLock.sha256
            publisher_evidence_sha256 = [string]$stagedPublisherEvidenceLock.sha256
            release_key_id = "sha256:1037057f8578a60ac5b3dc030cb2d70ad945ec3b5fb51fa3944fcafa77146339"
            paths_disclosed = $false
            private_values_emitted = 0
            external_actions = 0
            real_external_actions = 0
        } | ConvertTo-Json -Compress
    }
    catch { $handoffFailure = [string]$_.Exception.Message }
    finally {
        if ($null -ne $outputLock) {
            try { $outputLock.Dispose() } catch { $cleanupFailed = $true }
        }
        foreach ($lock in $generatedLocks) {
            try { Remove-ProtectedStagedFileLock $lock }
            catch { $cleanupFailed = $true }
        }
        foreach ($path in $temporaryPaths) {
            try { Remove-TemporaryOutput $path } catch { $cleanupFailed = $true }
        }
        foreach ($lock in $stagingLocks) {
            try { Remove-ProtectedStagedFileLock $lock }
            catch { $cleanupFailed = $true }
        }
        if ($null -ne $stagingContext) {
            try { Remove-ProtectedInputStagingRoot $stagingContext }
            catch { $cleanupFailed = $true }
        }
        $script:isolatedPythonSource = $null
        $script:isolatedPythonProjectRoot = $null
        $script:pythonApplication = $null
        $script:releasePythonArtifactLock = $null
        $script:releasePythonRuntimeLocks = $null
        $script:protectedStagingContext = $null
        $script:releaseDistContext = $null
        foreach ($lock in $inputLocks) {
            if ($null -ne $lock -and $null -ne $lock.stream) {
                try { $lock.stream.Dispose() } catch { $cleanupFailed = $true }
            }
        }
        foreach ($lock in @($nodeLock, $gitLock)) {
            if ($null -ne $lock -and $null -ne $lock.stream) {
                try { $lock.stream.Dispose() } catch { $cleanupFailed = $true }
            }
        }
    }
    Complete-ProtectedSigningOutcome $handoffFailure $cleanupFailed $handoffResult
}

try {
    try {
        $projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
            throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND"
        }
    }
    catch { throw "JOBFLOW_PROJECT_ROOT_NOT_FOUND" }
    Invoke-ProtectedSigningHandoff
}
catch {
    $code = [string]$_.Exception.Message
    if ($code -notmatch '^JOBFLOW_[A-Z0-9_]+$') { $code = "JOBFLOW_PROTECTED_SIGNING_FAILED" }
    [Console]::Error.WriteLine($code)
    exit 2
}
