<#
.SYNOPSIS
  Start the tray (`python -m outages tray`, which runs serve) at logon, and wake the PC before each run.

.DESCRIPTION
  Registers two tasks for the current user (Phase 6 spec 4.11):
    "OutageTracker serve"  at logon: .venv\Scripts\pythonw.exe -m outages tray in the project
                           root (the tray starts serve as a hidden child and restarts it);
                           no time limit, restart 3 x 1 min on failure, start when
                           available, run on battery, never a second instance.
                           Needs the tray extra: .venv\Scripts\pip install -e ".[tray]".
    "OutageTracker wake"   daily 05:58 and 19:58, wakes the computer and keeps it awake for
                           15 min (pythonw -c "...power.hold(900)"), so the 06:00 / 20:00 runs
                           happen on a sleeping PC. Skipped with -NoWake.
  serve itself refuses to run twice (data\serve.lock), so a terminal run alongside is harmless.
  Wake timers must be allowed in the power plan ("Allow wake timers").

.PARAMETER NoWake
  Don't register the wake task (a laptop that sleeps in a bag).

.PARAMETER Uninstall
  Remove both tasks.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-task.ps1 -WhatIf
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-task.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-task.ps1 -Uninstall
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

    # The logon task runs the tray; without its extra it would exit at once and serve would
    # never start. python.exe (not pythonw) so the exit code is waited for.
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    & $python -c "import pystray, PIL" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'The tray extra is missing. Install it first: .venv\Scripts\pip install -e ".[tray]"' -ErrorAction Continue
        exit 1
    }

    $user = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

    $serveAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m outages tray" -WorkingDirectory $Root
    $serveTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $serveSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-OutageTask $ServeTask $serveAction $serveTrigger $serveSettings $principal `
        "Cebu Brownout Tracker tray: starts and watches serve (runs at 06:00 and 20:00, the bot and the map)."

    if ($NoWake) {
        Write-Host "skipped: $WakeTask (-NoWake)"
    } else {
        # A timer wake with no user input sleeps again after ~2 min (the unattended sleep
        # timeout): hold the PC awake for 15 min, past the start of the run. serve then keeps
        # it awake until each run ends.
        $wakeAction = New-ScheduledTaskAction -Execute $pythonw `
            -Argument "-c `"import outages.power as p; p.hold(900)`"" -WorkingDirectory $Root
        $wakeTriggers = @(
            (New-ScheduledTaskTrigger -Daily -At "05:58"),
            (New-ScheduledTaskTrigger -Daily -At "19:58")
        )
        $wakeSettings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
        Register-OutageTask $WakeTask $wakeAction $wakeTriggers $wakeSettings $principal `
            "Wakes the PC at 05:58 and 19:58 and keeps it awake 15 min for the Cebu Brownout Tracker's runs."
    }
    exit 0
} catch {
    if (Test-AccessDenied $_) {
        Write-Error "Run this from an elevated PowerShell (Run as administrator)." -ErrorAction Continue
        exit 1
    }
    throw
}
