#requires -Version 5.1
<# Current-user Task Scheduler service. No passwords, elevation, PATH changes or network setup. #>
[CmdletBinding()]
param(
    [ValidateSet('install', 'start', 'status', 'stop', 'uninstall')]
    [string]$Action = 'install',
    [string]$Python = $env:RB_PYTHON,
    [string]$DataDir = '',
    [ValidateRange(1, 65535)][int]$Port = 8798
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This installer requires native Windows PowerShell 5.1+.' }

$SourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if (-not $DataDir) { $DataDir = if ($env:RB_DATA_DIR) { $env:RB_DATA_DIR } else { $SourceRoot } }
$DataDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DataDir)
$DataDir = [IO.Path]::GetFullPath($DataDir)
$OwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$utf8 = New-Object Text.UTF8Encoding($false)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $identityBytes = $utf8.GetBytes(($SourceRoot + '|' + $DataDir + '|' + $OwnerSid).ToLowerInvariant())
    $tag = ([BitConverter]::ToString($sha.ComputeHash($identityBytes))).Replace('-', '').Substring(0, 20)
} finally { $sha.Dispose() }
$TaskName = 'AgentReviewBoard-' + $tag
$TaskPath = '\'
$Marker = 'agent-review-board.windows.v1:' + $tag + ':' + $OwnerSid
$Manifest = Join-Path $DataDir 'windows-service.json'
Import-Module ScheduledTasks -ErrorAction Stop

function Quote-Native([string]$Value) {
    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'Unsupported quote or newline in native argument.'
    }
    # CommandLineToArgvW/CRT quoting: double trailing backslashes before closing quote.
    return '"' + [regex]::Replace($Value, '(\\+)$', '$1$1') + '"'
}

function Service-Arguments($Config) {
    return '-X utf8 -u ' + (Quote-Native (Join-Path $SourceRoot 'board_platform.py')) +
        ' serve --data-dir ' + (Quote-Native $DataDir) + ' --port ' + $Config.port
}

function Get-Task {
    # Missing is normal; scheduler/permission failures must not be hidden.
    # Enumerating avoids a CIM "not found" error on an empty root task folder.
    return Get-ScheduledTask -ErrorAction Stop |
        Where-Object { $null -ne $_ -and $_.TaskPath -eq $TaskPath -and $_.TaskName -eq $TaskName }
}

function Read-Config {
    if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { return $null }
    $config = [IO.File]::ReadAllText($Manifest, $utf8) | ConvertFrom-Json
    if ($config.version -ne 1 -or $config.owner_sid -ne $OwnerSid -or
        $config.source_root -ne $SourceRoot -or $config.data_dir -ne $DataDir -or
        $config.task_name -ne $TaskName -or $config.marker -ne $Marker -or
        $config.port -lt 1 -or $config.port -gt 65535 -or
        -not [IO.Path]::IsPathRooted($config.python)) {
        throw "Refusing foreign or invalid service manifest: $Manifest"
    }
    return $config
}

function Assert-Owned($Task, $Config) {
    if ($null -eq $Task) { return }
    if ($null -eq $Config) { throw "Refusing to adopt task without our manifest: $TaskName" }
    $principalSid = $Task.Principal.UserId
    if ($principalSid -ne $OwnerSid) {
        $account = New-Object Security.Principal.NTAccount($principalSid)
        $principalSid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $actions = @($Task.Actions)
    if ($Task.Description -ne $Marker -or $principalSid -ne $OwnerSid -or
        $Task.Principal.LogonType -ne 'Interactive' -or $Task.Principal.RunLevel -ne 'Limited' -or
        $actions.Count -ne 1 -or $actions[0].Execute -ne $Config.python -or
        $actions[0].Arguments -cne (Service-Arguments $Config) -or
        $actions[0].WorkingDirectory -ne $SourceRoot) {
        throw "Refusing to operate on a task whose ownership/action changed: $TaskName"
    }
}

function Write-Config($Config) {
    [IO.Directory]::CreateDirectory($DataDir) | Out-Null
    $temp = $Manifest + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temp, ($Config | ConvertTo-Json), $utf8)
        if (Test-Path -LiteralPath $Manifest) {
            # PS 5.1 converts $null string arguments to an invalid empty path.
            $replaceBackup = $Manifest + '.' + [Guid]::NewGuid().ToString('N') + '.previous'
            [IO.File]::Replace($temp, $Manifest, $replaceBackup)
            Remove-Item -LiteralPath $replaceBackup -ErrorAction SilentlyContinue
        }
        else { [IO.File]::Move($temp, $Manifest) }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
    }
}

function Write-ServiceLog([string]$Message) {
    $logDir = Join-Path $DataDir 'logs'
    [IO.Directory]::CreateDirectory($logDir) | Out-Null
    [IO.File]::AppendAllText((Join-Path $logDir 'windows-service.log'),
        ([DateTimeOffset]::Now.ToString('o') + ' ' + $Message + [Environment]::NewLine), $utf8)
}

function Resolve-Python {
    # Windows PowerShell 5.1 strips embedded double quotes in native argv.
    $probe = "import json,os,sys; assert os.name == 'nt' and sys.version_info >= (3,11), 'native Windows Python 3.11+ required'; print(json.dumps(sys.executable))"
    if ($Python) {
        $candidate = (Resolve-Path -LiteralPath $Python -ErrorAction Stop).ProviderPath
        $output = & $candidate -X utf8 -c $probe
    } else {
        $launcher = (Get-Command py.exe -CommandType Application -ErrorAction Stop).Source
        $output = & $launcher -3 -X utf8 -c $probe
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python check failed. Use py -3 or -Python C:\path\python.exe (3.11+). Never PATH python3.' }
    $resolved = ($output -join "`n") | ConvertFrom-Json
    if (-not [IO.Path]::IsPathRooted($resolved) -or -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw 'Python launcher did not return a real executable path.'
    }
    return $resolved
}

function Test-BeforeInstall([string]$Exe) {
    $testState = Join-Path ([IO.Path]::GetTempPath()) ('review-board-preflight-' + [Guid]::NewGuid().ToString('N'))
    $previousState = $env:RB_DATA_DIR
    $previousUtf8 = $env:PYTHONUTF8
    $previousEncoding = $env:PYTHONIOENCODING
    try {
        $env:RB_DATA_DIR = $testState
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        foreach ($test in @('test_lib.py', 'test_explore.py', 'test_platform.py', 'test_public.py', 'test_snapshot_boundary.py')) {
            & $Exe -X utf8 -B (Join-Path $SourceRoot ('tests\' + $test))
            if ($LASTEXITCODE -ne 0) { throw "Pre-install validation failed: $test" }
        }
    } finally {
        $env:RB_DATA_DIR = $previousState
        $env:PYTHONUTF8 = $previousUtf8
        $env:PYTHONIOENCODING = $previousEncoding
        if (Test-Path -LiteralPath $testState) { Remove-Item -LiteralPath $testState -Recurse -Force }
    }
}

function Stop-Owned($Task, $Config) {
    Assert-Owned $Task $Config
    if ($null -eq $Task) { return }
    $wasActive = $Task.State -in @('Running', 'Queued')
    # Disable prevents queued triggers/restart-on-failure from undoing an explicit stop.
    Disable-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Out-Null
    if ($wasActive -or (Get-Task).State -in @('Running', 'Queued')) {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $current = Get-Task
        if ($null -eq $current -or $current.State -notin @('Running', 'Queued')) { return }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Owned task did not stop: $TaskName"
}

function Start-Owned($Task, $Config) {
    if ($null -eq $Task) { throw 'Service is not installed. Run install first.' }
    Assert-Owned $Task $Config
    Enable-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Out-Null
    if ($Task.State -ne 'Running') { Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $runningSince = $null
    do {
        $current = Get-Task
        if ($current.State -eq 'Running') {
            if ($null -eq $runningSince) { $runningSince = [DateTime]::UtcNow }
            if (([DateTime]::UtcNow - $runningSince).TotalSeconds -ge 2) {
                Write-ServiceLog "running task=$TaskName port=$($Config.port)"
                Write-Output "Running $TaskName; loopback port $($Config.port); logs: $(Join-Path $DataDir 'logs')"
                return
            }
        } else { $runningSince = $null }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Service did not stay running. Inspect $(Join-Path $DataDir 'logs\windows-server.log') and Task Scheduler."
}

$config = Read-Config
$task = Get-Task
Assert-Owned $task $config
switch ($Action) {
    'install' {
        $exe = Resolve-Python
        Test-BeforeInstall $exe  # all validation precedes changes to registered services
        $newConfig = [pscustomobject]@{
            version = 1; owner_sid = $OwnerSid; source_root = $SourceRoot; data_dir = $DataDir
            task_name = $TaskName; marker = $Marker; python = $exe; port = $Port
        }
        $scheduledAction = New-ScheduledTaskAction -Execute $exe -Argument (Service-Arguments $newConfig) -WorkingDirectory $SourceRoot
        $principal = New-ScheduledTaskPrincipal -UserId $OwnerSid -LogonType Interactive -RunLevel Limited
        # No stored credential: startup is effective only with an interactive user session;
        # the logon trigger handles reboot -> first login, and normal later logins.
        $triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $OwnerSid))
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $definition = New-ScheduledTask -Action $scheduledAction -Trigger $triggers -Principal $principal `
            -Settings $settings -Description $Marker
        $oldState = if ($null -ne $task) { [string]$task.State } else { $null }
        try {
            if ($null -ne $task) { Stop-Owned $task $config }
            Write-Config $newConfig
            Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -InputObject $definition -Force | Out-Null
        } catch {
            if ($null -ne $config) {
                Write-Config $config
                $current = Get-Task
                Assert-Owned $current $config
                if ($null -ne $current -and $oldState -ne 'Disabled') {
                    Enable-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Out-Null
                    if ($oldState -in @('Running', 'Queued')) {
                        Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
                    }
                }
            } elseif (Test-Path -LiteralPath $Manifest) { Remove-Item -LiteralPath $Manifest }
            throw
        }
        Write-ServiceLog "installed task=$TaskName python=$exe port=$Port"
        Start-Owned (Get-Task) $newConfig
    }
    'start' { Start-Owned $task $config }
    'status' {
        if ($null -eq $task) { Write-Output "Not installed: $TaskName"; break }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath
        [pscustomobject]@{ Task = $TaskName; State = $task.State; LastResult = $info.LastTaskResult
            LastRun = $info.LastRunTime; Python = $config.python; DataDir = $DataDir
            Port = $config.port; ServerLog = (Join-Path $DataDir 'logs\windows-server.log') } | Format-List
    }
    'stop' {
        if ($null -ne $task) { Stop-Owned $task $config; Write-ServiceLog "stopped task=$TaskName" }
        Write-Output "Stopped: $TaskName"
    }
    'uninstall' {
        if ($null -ne $task) {
            Stop-Owned $task $config
            Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
        }
        if ($null -ne $config) {
            Write-ServiceLog "uninstalled task=$TaskName (documents, state and logs retained)"
            Remove-Item -LiteralPath $Manifest
        }
        Write-Output "Uninstalled: $TaskName; documents, state and logs retained."
    }
}
