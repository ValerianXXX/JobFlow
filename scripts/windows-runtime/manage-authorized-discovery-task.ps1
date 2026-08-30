[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Register", "Remove", "Status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$logicalName = "JOBFLOW_AUTHORIZED_DISCOVERY"
$taskName = "JobFlow Authorized Read-Only Discovery"
$wakeIntervalMinutes = 15

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw "JOBFLOW_LOCAL_APP_DATA_NOT_FOUND" }
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "JobOps"))
$localRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $localRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "JOBFLOW_DISCOVERY_TASK_ROOT_INVALID"
}

function Assert-LocalPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = $localRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($absolute -ne $localRoot -and -not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "JOBFLOW_DISCOVERY_TASK_PATH_FORBIDDEN"
    }
    $cursor = $absolute
    while ($cursor -and ($cursor -eq $localRoot -or $cursor.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase))) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "JOBFLOW_DISCOVERY_TASK_REPARSE_FORBIDDEN"
            }
        }
        if ($cursor -eq $localRoot) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Get-TrustedWindowsPowerShell {
    $systemDirectory = [IO.Path]::GetFullPath([Environment]::SystemDirectory)
    $candidate = [IO.Path]::GetFullPath((Join-Path $systemDirectory "WindowsPowerShell\v1.0\powershell.exe"))
    $prefix = $systemDirectory.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (
        -not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $candidate -PathType Leaf)
    ) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
    $securityModule = Join-Path $systemDirectory "WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
    if (-not (Test-Path -LiteralPath $securityModule -PathType Leaf)) {
        throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED"
    }
    Microsoft.PowerShell.Core\Import-Module -Name $securityModule -Force -ErrorAction Stop
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $candidate
    if (
        [string]$signature.Status -cne "Valid" -or
        $null -eq $signature.SignerCertificate -or
        [string]$signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)'
    ) { throw "JOBFLOW_TRUSTED_WINDOWS_POWERSHELL_REQUIRED" }
    return $candidate
}

$lockStream = $null
$lockAcquired = $false
if ($env:JOBFLOW_DISCOVERY_TASK_LOCK_HELD -ne "1") {
    $lockDirectory = Join-Path $localRoot "Data\state"
    $lockPath = Join-Path $lockDirectory ".authorized-discovery-task.lock"
    Assert-LocalPath $lockDirectory
    New-Item -ItemType Directory -Path $lockDirectory -Force | Out-Null
    Assert-LocalPath $lockPath
    $lockStream = [IO.File]::Open(
        $lockPath,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::ReadWrite
    )
    if ($lockStream.Length -lt 1) {
        $lockStream.SetLength(1)
        $lockStream.Flush()
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not $lockAcquired) {
        try {
            $lockStream.Lock(0, 1)
            $lockAcquired = $true
        }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw "JOBFLOW_DISCOVERY_TASK_LOCK_TIMEOUT"
            }
            Start-Sleep -Milliseconds 50
        }
    }
}

try {
function Get-TaskOrNull {
    return Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

function Write-Result([string]$Status) {
    [ordered]@{
        schema_version = 1
        status = $Status
        task_name = $logicalName
        interactive_user_only = $true
        stores_password = $false
        wake_interval_minutes = $wakeIntervalMinutes
        application_actions = 0
        browser_actions = 0
        material_uploads = 0
        final_submits = 0
    } | ConvertTo-Json -Compress
}

$runnerPath = Join-Path $localRoot "bin\run-authorized-discovery-task.ps1"
Assert-LocalPath $runnerPath
$powershellPath = Get-TrustedWindowsPowerShell
$taskArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runnerPath + '"'

if ($Action -eq "Register") {
    if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
        throw "JOBFLOW_DISCOVERY_TASK_RUNNER_MISSING"
    }
    $taskAction = New-ScheduledTaskAction -Execute $powershellPath -Argument $taskArguments
    $taskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $wakeIntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $definition = New-ScheduledTask -Action $taskAction -Trigger $taskTrigger -Principal $principal -Settings $settings `
        -Description "Explicitly authorized, read-only JobFlow company-careers discovery. No application or browser actions."
    Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
}
elseif ($Action -eq "Remove") {
    if ($null -ne (Get-TaskOrNull)) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}

$task = Get-TaskOrNull
if ($null -eq $task) {
    Write-Result "NOT_REGISTERED"
    return
}
if ($Action -eq "Status" -or $Action -eq "Register") {
    $expectedPowerShell = [IO.Path]::GetFullPath((Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\powershell.exe"))
    $actions = @($task.Actions)
    if (
        $actions.Count -ne 1 -or
        -not ([IO.Path]::GetFullPath([string]$actions[0].Execute)).Equals($expectedPowerShell, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([string]$actions[0].Arguments).Equals($taskArguments, [StringComparison]::Ordinal)
    ) {
        throw "JOBFLOW_DISCOVERY_TASK_DEFINITION_CHANGED"
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if (
        -not ([string]$task.Principal.UserId).Equals($identity, [StringComparison]::OrdinalIgnoreCase) -or
        ([string]$task.Principal.LogonType) -ne "Interactive" -or
        ([string]$task.Principal.RunLevel) -ne "Limited"
    ) {
        throw "JOBFLOW_DISCOVERY_TASK_PRINCIPAL_CHANGED"
    }
    $triggers = @($task.Triggers)
    if (
        $triggers.Count -ne 1 -or
        ([string]$triggers[0].Repetition.Interval) -ne "PT15M"
    ) {
        throw "JOBFLOW_DISCOVERY_TASK_TRIGGER_CHANGED"
    }
    if (
        ([string]$task.Settings.MultipleInstances) -ne "IgnoreNew" -or
        ([string]$task.Settings.ExecutionTimeLimit) -ne "PT10M"
    ) {
        throw "JOBFLOW_DISCOVERY_TASK_SETTINGS_CHANGED"
    }
}
Write-Result "REGISTERED"
}
finally {
    if ($null -ne $lockStream) {
        if ($lockAcquired) {
            try { $lockStream.Unlock(0, 1) } catch { }
        }
        $lockStream.Dispose()
    }
}
