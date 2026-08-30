[CmdletBinding()]
param(
    [string]$NodePath = "",
    [string]$GitPath = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$trustedSystemDirectory = [Environment]::SystemDirectory
$trustedWindowsRoot = [IO.Directory]::GetParent($trustedSystemDirectory).FullName
$env:SystemRoot = $trustedWindowsRoot
$windowsModuleRoot = Join-Path $trustedSystemDirectory "WindowsPowerShell\v1.0\Modules"
foreach ($moduleName in @("Microsoft.PowerShell.Management", "Microsoft.PowerShell.Security", "Microsoft.PowerShell.Utility")) {
    $moduleManifest = Join-Path (Join-Path $windowsModuleRoot $moduleName) ($moduleName + ".psd1")
    if (-not [IO.File]::Exists($moduleManifest)) {
        throw "JOBFLOW_RELEASE_POWERSHELL_MODULE_MISSING"
    }
    Import-Module -Name $moduleManifest -ErrorAction Stop
}
$projectRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path)
if (-not (Microsoft.PowerShell.Management\Test-Path -LiteralPath (Join-Path $projectRoot ".jobops-root") -PathType Leaf)) {
    throw "JOBFLOW_RELEASE_PROJECT_ROOT_INVALID"
}

function Resolve-AbsoluteTool {
    param(
        [string]$ExplicitPath,
        [string[]]$Candidates,
        [string]$CommandName,
        [string]$FailureCode
    )
    $ordered = [Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) { $ordered.Add($ExplicitPath) }
    foreach ($candidate in $Candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) { $ordered.Add($candidate) }
    }
    foreach ($candidate in $ordered) {
        try {
            $absolute = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($candidate))
            if (Microsoft.PowerShell.Management\Test-Path -LiteralPath $absolute -PathType Leaf) { return $absolute }
        }
        catch { }
    }
    throw $FailureCode
}

function Test-ReparsePoint {
    param([string]$Path)
    $item = Microsoft.PowerShell.Management\Get-Item -LiteralPath $Path -Force
    return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Resolve-TrustedReleasePython {
    param([string]$Root)

    # No caller-authored interpreter path is accepted. Historical contract
    # label retained for release-audit continuity:
    # -m jobops.release_verification run --node $node --git $git
    # Bounded executable: .venv\Scripts\python.exe
    # Public release verification never accepts a caller-selected interpreter
    # and never searches PATH. The installer-created project venv is the sole
    # bounded location. Its executable must retain an explicitly pinned PSF
    # Authenticode identity throughout this transaction.
    $venvRoot = [IO.Path]::GetFullPath((Join-Path $Root ".venv"))
    $scriptsRoot = [IO.Path]::GetFullPath((Join-Path $venvRoot "Scripts"))
    $candidate = [IO.Path]::GetFullPath((Join-Path $scriptsRoot "python.exe"))
    foreach ($path in @($venvRoot, $scriptsRoot, $candidate)) {
        if (-not (Microsoft.PowerShell.Management\Test-Path -LiteralPath $path)) {
            throw "JOBFLOW_RELEASE_PYTHON_MISSING"
        }
        if (Test-ReparsePoint $path) {
            throw "JOBFLOW_RELEASE_PYTHON_PATH_UNSAFE"
        }
    }
    if (-not (Microsoft.PowerShell.Management\Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "JOBFLOW_RELEASE_PYTHON_MISSING"
    }

    $trustPath = Join-Path $Root "config\release-toolchain.json"
    if (-not (Microsoft.PowerShell.Management\Test-Path -LiteralPath $trustPath -PathType Leaf)) {
        throw "JOBFLOW_RELEASE_PYTHON_TRUST_CONFIG_MISSING"
    }
    try {
        $trust = Microsoft.PowerShell.Management\Get-Content -LiteralPath $trustPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch { throw "JOBFLOW_RELEASE_PYTHON_TRUST_CONFIG_INVALID" }
    $pythonPolicy = $trust.tools.python
    if ([int]$trust.schema_version -ne 1 -or $null -eq $pythonPolicy -or $null -eq $pythonPolicy.allowed_signers) {
        throw "JOBFLOW_RELEASE_PYTHON_TRUST_CONFIG_INVALID"
    }

    # Keep the launcher open without delete/write sharing from the first trust
    # decision until the child exits.  This closes the ordinary replace-after-
    # validation race for the executable itself.  The surrounding Python/DLL
    # closure is still classified as UNATTESTED and cannot authorize signing.
    $lock = [IO.File]::Open(
        $candidate,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $candidate
        if ([string]$signature.Status -ne "Valid" -or $null -eq $signature.SignerCertificate) {
            throw "JOBFLOW_RELEASE_PYTHON_SIGNATURE_INVALID"
        }
        $subject = [string]$signature.SignerCertificate.Subject
        $thumbprint = ([string]$signature.SignerCertificate.Thumbprint).ToUpperInvariant()
        $matchingSigner = @($pythonPolicy.allowed_signers) | Where-Object {
            [string]$_.subject -ceq $subject -and
            ([string]$_.thumbprint).ToUpperInvariant() -ceq $thumbprint
        } | Select-Object -First 1
        if ($null -eq $matchingSigner) {
            throw "JOBFLOW_RELEASE_PYTHON_SIGNER_UNPINNED"
        }

        $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($candidate).FileVersion
        if ([string]::IsNullOrWhiteSpace($version)) {
            throw "JOBFLOW_RELEASE_PYTHON_VERSION_INVALID"
        }
        if ($version -notmatch '^3\.(11|12)(?:\.|$)') {
            throw "JOBFLOW_RELEASE_PYTHON_VERSION_UNPINNED"
        }

        $sha256 = (Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToUpperInvariant()
        return [pscustomobject]@{
            Path = $candidate
            Sha256 = $sha256
            Subject = $subject
            Thumbprint = $thumbprint
            Version = $version
            Lock = $lock
        }
    }
    catch {
        $lock.Dispose()
        throw
    }
}

function Set-ProcessEnvironment {
    param([Collections.IDictionary]$Values)
    foreach ($entry in @([Environment]::GetEnvironmentVariables("Process").GetEnumerator())) {
        [Environment]::SetEnvironmentVariable([string]$entry.Key, $null, "Process")
    }
    foreach ($name in $Values.Keys) {
        [Environment]::SetEnvironmentVariable([string]$name, [string]$Values[$name], "Process")
    }
}

function Get-MinimalChildEnvironment {
    param([string]$NodeModules)
    $current = [Environment]::GetEnvironmentVariables("Process")
    $result = [ordered]@{}
    foreach ($name in @(
        "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP", "ProgramData",
        "ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles", "CommonProgramFiles(x86)",
        "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"
    )) {
        if ($current.Contains($name) -and -not [string]::IsNullOrWhiteSpace([string]$current[$name])) {
            $result[$name] = [string]$current[$name]
        }
    }
    $result["SystemRoot"] = $trustedWindowsRoot
    $result["WINDIR"] = $trustedWindowsRoot
    $result["COMSPEC"] = Join-Path $trustedSystemDirectory "cmd.exe"
    $result["PATH"] = $trustedSystemDirectory + ";" + (Join-Path $trustedSystemDirectory "WindowsPowerShell\v1.0")
    $result["JOBFLOW_RELEASE_VERIFICATION"] = "1"
    if (-not [string]::IsNullOrWhiteSpace($NodeModules)) {
        $result["NODE_PATH"] = $NodeModules
    }
    return $result
}

$pythonIdentity = Resolve-TrustedReleasePython $projectRoot
$python = $pythonIdentity.Path
$node = Resolve-AbsoluteTool $NodePath @(
    $env:JOBFLOW_NODE_PATH,
    (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"),
    (Join-Path $env:ProgramFiles "nodejs\node.exe")
) "node" "JOBFLOW_RELEASE_NODE_MISSING"
$git = Resolve-AbsoluteTool $GitPath @(
    (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"),
    (Join-Path $env:ProgramFiles "Git\cmd\git.exe")
) "git" "JOBFLOW_RELEASE_GIT_MISSING"

$sourceRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "src"))
$testsRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "tests"))
$sitePackages = [IO.Path]::GetFullPath((Join-Path $projectRoot ".venv\Lib\site-packages"))
foreach ($path in @($sourceRoot, $testsRoot, $sitePackages)) {
    if (-not (Microsoft.PowerShell.Management\Test-Path -LiteralPath $path -PathType Container)) {
        throw "JOBFLOW_RELEASE_PYTHON_IMPORT_ROOT_MISSING"
    }
    if (Test-ReparsePoint $path) {
        throw "JOBFLOW_RELEASE_PYTHON_IMPORT_ROOT_UNSAFE"
    }
}

$bootstrap = @'
import runpy
import sys

source_root, tests_root, site_packages = sys.argv[1:4]
sys.dont_write_bytecode = True
sys.path[:0] = [source_root, tests_root, site_packages]
sys.argv = ['jobops.release_verification'] + sys.argv[4:]
runpy.run_module('jobops.release_verification', run_name='__main__', alter_sys=False)
'@

$savedProcessEnvironment = [ordered]@{}
foreach ($entry in [Environment]::GetEnvironmentVariables("Process").GetEnumerator()) {
    $savedProcessEnvironment[[string]$entry.Key] = [string]$entry.Value
}

$locationPushed = $false
try {
    Push-Location $projectRoot
    $locationPushed = $true
    $bundledNodeModules = Join-Path (Split-Path -Parent (Split-Path -Parent $node)) "node_modules"
    $childNodeModules = ""
    if (Microsoft.PowerShell.Management\Test-Path -LiteralPath $bundledNodeModules -PathType Container) {
        $childNodeModules = $bundledNodeModules
    }
    Set-ProcessEnvironment (Get-MinimalChildEnvironment $childNodeModules)

    # -I ignores Python environment and user-site configuration, -P excludes
    # the current directory, and -S prevents automatic site/sitecustomize
    # execution. Only the three bounded import roots above are added by this
    # fixed bootstrap after interpreter startup.
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $resultLines = @(& $python -I -P -S -B -X utf8 -c $bootstrap $sourceRoot $testsRoot $sitePackages run --node $node --git $git)
        $exitCode = [int]$LASTEXITCODE
    }
    finally { $ErrorActionPreference = $savedPreference }

    $finalPythonHash = (Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $python -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($finalPythonHash -cne $pythonIdentity.Sha256) {
        throw "JOBFLOW_RELEASE_PYTHON_CHANGED_DURING_RUN"
    }
    $finalSignature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $python
    if (
        [string]$finalSignature.Status -ne "Valid" -or
        $null -eq $finalSignature.SignerCertificate -or
        [string]$finalSignature.SignerCertificate.Subject -cne $pythonIdentity.Subject -or
        ([string]$finalSignature.SignerCertificate.Thumbprint).ToUpperInvariant() -cne $pythonIdentity.Thumbprint
    ) { throw "JOBFLOW_RELEASE_PYTHON_CHANGED_DURING_RUN" }

    if ($exitCode -ne 0) { throw "JOBFLOW_RELEASE_VERIFICATION_FAILED" }
    $resultText = ($resultLines -join "`n").Trim()
    try { $result = $resultText | ConvertFrom-Json }
    catch { throw "JOBFLOW_RELEASE_RESULT_INVALID" }
    if (
        $result.status -ne "RELEASE_VERIFICATION_RECORDED" -or
        $result.source_commit -notmatch '^[0-9a-f]{40}$' -or
        [int]$result.real_external_actions -ne 0
    ) { throw "JOBFLOW_RELEASE_RESULT_INVALID" }

    Write-Host ""
    Write-Host "JobFlow release verification passed as local QA and was recorded locally; public signing remains blocked." -ForegroundColor Green
    Write-Host ("Trusted Python {0}; SHA-256 {1}." -f $pythonIdentity.Version, $pythonIdentity.Sha256) -ForegroundColor Green
    Write-Host "No push, tag, upload, recruiting-site visit, or real external action was performed." -ForegroundColor Green
}
finally {
    if ($locationPushed) { Pop-Location }
    Set-ProcessEnvironment $savedProcessEnvironment
    if ($null -ne $pythonIdentity -and $null -ne $pythonIdentity.Lock) {
        $pythonIdentity.Lock.Dispose()
    }
}
