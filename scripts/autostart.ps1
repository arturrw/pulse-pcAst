# Run pulse in the background: the metrics collector (default) or the alert check.
#   powershell -File scripts\autostart.ps1 install   # register + start now
#   powershell -File scripts\autostart.ps1 remove    # stop + unregister
#   powershell -File scripts\autostart.ps1 status
#   powershell -File scripts\autostart.ps1 install -Task alerts   # notifications, checked every 15 min
#   powershell -File scripts\autostart.ps1 install -Task digest   # one summary notification every morning at 09:00
param([Parameter(Mandatory)][ValidateSet('install', 'remove', 'status')][string]$Action,
      [ValidateSet('collect', 'alerts', 'digest')][string]$Task = 'collect',
      [string]$Exe = '',   # the installed app's windowless exe; without it the checkout's .venv python is used
      [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')][string]$At = '09:00',   # digest: time of day
      [ValidateRange(1, 1440)][int]$Minutes = 15,                              # alerts: how often to check
      [ValidateRange(5, 3600)][int]$Interval = 30)                             # collect: seconds between samples

$TaskName = "pulse-$Task"
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'   # pythonw = no console window

switch ($Action) {
    'install' {
        if ($Exe) {
            if (-not (Test-Path $Exe)) { throw "Not found: $Exe" }
            $Pythonw = $Exe; $Pfx = ''; $Root = Split-Path -Parent $Exe
        } else {
            $Pfx = '-m pulse '
            if (-not (Test-Path $Pythonw)) { throw "Not found: $Pythonw (create .venv and run: pip install -e .)" }
        }
        # The app used to be called pcassist, then vigil; a job of the same kind under an old name is replaced, not left running next to this one.
        foreach ($Old in @("pcassist-$Task", "vigil-$Task")) {
            if (Get-ScheduledTask -TaskName $Old -ErrorAction SilentlyContinue) {
                Stop-ScheduledTask -TaskName $Old -ErrorAction SilentlyContinue
                Unregister-ScheduledTask -TaskName $Old -Confirm:$false
                Write-Host "Replaced the old '$Old' job."
            }
        }
        if ($Task -eq 'digest') {
            # Once a day at 09:00; StartWhenAvailable runs it when the PC is switched on later, so a late start still gets one.
            $act = New-ScheduledTaskAction -Execute $Pythonw -Argument "${Pfx}digest" -WorkingDirectory $Root
            $trg = New-ScheduledTaskTrigger -Daily -At $At
            $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable
            Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg -Settings $set `
                -Description 'pulse morning digest (one summary notification)' -Force | Out-Null
            Write-Host "Installed '$TaskName' (runs every day at $At)."
            return
        }
        if ($Task -eq 'alerts') {
            # One short check every 15 minutes; a check that is still running (a slow PowerShell call) is not doubled.
            $act = New-ScheduledTaskAction -Execute $Pythonw -Argument "${Pfx}alerts" -WorkingDirectory $Root
            $trg = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable
            Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg -Settings $set `
                -Description 'pulse alert check (Windows notifications)' -Force | Out-Null
            Write-Host "Installed '$TaskName' (runs every $Minutes minutes)."
            return
        }
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue   # a running collector would keep the old interval
        $act = New-ScheduledTaskAction -Execute $Pythonw -Argument "${Pfx}collect --interval $Interval" -WorkingDirectory $Root
        # Trigger 1 starts it at logon. Trigger 2 is a watchdog firing every 5 min from now on: while the
        # collector runs, IgnoreNew makes it a no-op; if the process died (crash, killed) it comes back.
        # (Task Scheduler's "restart on failure" does not cover a killed process, and a repetition attached
        # to the logon trigger only starts counting at the next logon, hence a separate time trigger.)
        $trg = @(
            New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
            New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
        )
        $set = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew `
            -StartWhenAvailable
        Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg -Settings $set `
            -Description 'pulse background metrics collector' -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Installed and started '$TaskName'."
    }
    'remove' {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed '$TaskName'."
    }
    'status' {
        Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
        Get-Process pythonw -ErrorAction SilentlyContinue | Select-Object Id, StartTime, Path
    }
}
