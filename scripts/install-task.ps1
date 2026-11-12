<#
.SYNOPSIS
  Start `python -m outages serve` at logon with Task Scheduler, and wake the PC before each run.

.DESCRIPTION
  Registers two tasks for the current user (Phase 6 spec 4.11):
    "OutageTracker serve"  at logon: .venv\Scripts\pythonw.exe -m outages serve in the project
                           root; no time limit, restart 3 x 1 min on failure, start when
                           available, run on battery, never a second instance.
    "OutageTracker wake"   daily 05:58 and 19:58, wakes the computer (cmd.exe /c exit 0), so the
                           06:00 / 20:00 runs happen on a sleeping PC. Skipped with -NoWake.
  serve itself refuses to run twice (data\serve.lock), so a terminal run alongside is harmless.
  Wake timers must be allowed in the power plan ("Allow wake timers").

.PARAMETER NoWake
  Don't register the wake task (a laptop that sleeps in a bag).

.PARAMETER Uninstall
  Remove both tasks.

.EXAMPLE
  powershell -NoProfile -File scripts\install-task.ps1 -WhatIf
  powershell -NoProfile -File scripts\install-task.ps1
  powershell -NoProfile -File scripts\install-task.ps1 -Uninstall
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$NoWake,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ServeTask = "OutageTracker serve"
$WakeTask = "OutageTracker wake"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Test-AccessDenied($err) {
    $msg = "$($err.Exception.Message)"
    return ($msg -match "Access is denied" -or $msg -match "0x80070005")
}

function Remove-OutageTask($name) {
    if ($PSCmdlet.ShouldProcess($name, "Unregister-ScheduledTask")) {
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
            Write-Host "removed: $name"
        } catch {
            if (Test-AccessDenied $_) { throw }
            Write-Host "not installed: $name"
        }
    }
}

function Register-OutageTask($name, $action, $triggers, $settings, $principal, $description) {
    if ($PSCmdlet.ShouldProcess($name, "Register-ScheduledTask")) {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers `
            -Settings $settings -Principal $principal -Description $description -Force | Out-Null
        Write-Host "registered: $name"
    }
}

try {
    if ($Uninstall) {
        Remove-OutageTask $ServeTask
        Remove-OutageTask $WakeTask
        exit 0
    }

    $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $pythonw)) {
        Write-Error "No $pythonw. Create the venv first: py -m venv .venv; .venv\Scripts\pip install -e ." -ErrorAction Continue
        exit 1
    }

    $user = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

    $serveAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m outages serve" -WorkingDirectory $Root
    $serveTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $serveSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-OutageTask $ServeTask $serveAction $serveTrigger $serveSettings $principal `
        "Cebu Brownout Tracker: check + alerts at 06:00 and 20:00, the bot and the map."

    if ($NoWake) {
        Write-Host "skipped: $WakeTask (-NoWake)"
    } else {
        $wakeAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c exit 0"
        $wakeTriggers = @(
            (New-ScheduledTaskTrigger -Daily -At "05:58"),
            (New-ScheduledTaskTrigger -Daily -At "19:58")
        )
        $wakeSettings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
        Register-OutageTask $WakeTask $wakeAction $wakeTriggers $wakeSettings $principal `
            "Wakes the PC two minutes before the Cebu Brownout Tracker's 06:00 and 20:00 runs."
    }
    exit 0
} catch {
    if (Test-AccessDenied $_) {
        Write-Error "Run this from an elevated PowerShell (Run as administrator)." -ErrorAction Continue
        exit 1
    }
    throw
}
