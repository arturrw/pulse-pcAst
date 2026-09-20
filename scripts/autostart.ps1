# Run pcassist in the background: the metrics collector (default) or the alert check.
#   powershell -File scripts\autostart.ps1 install   # register + start now
#   powershell -File scripts\autostart.ps1 remove    # stop + unregister
#   powershell -File scripts\autostart.ps1 status
#   powershell -File scripts\autostart.ps1 install -Task alerts   # notifications, checked every 15 min
#   powershell -File scripts\autostart.ps1 install -Task digest   # one summary notification every morning at 09:00
param([Parameter(Mandatory)][ValidateSet('install', 'remove', 'status')][string]$Action,
      [ValidateSet('collect', 'alerts', 'digest')][string]$Task = 'collect')

$TaskName = "pcassist-$Task"
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'   # pythonw = no console window

switch ($Action) {
    'install' {
        if (-not (Test-Path $Pythonw)) { throw "Not found: $Pythonw (create .venv and run: pip install -e .)" }
        if ($Task -eq 'digest') {
            # Once a day at 09:00; StartWhenAvailable runs it when the PC is switched on later, so a late start still gets one.
            $act = New-ScheduledTaskAction -Execute $Pythonw -Argument '-m pcassist digest' -WorkingDirectory $Root
            $trg = New-ScheduledTaskTrigger -Daily -At 9:00AM
            $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable
            Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg -Settings $set `
                -Description 'pcassist morning digest (one summary notification)' -Force | Out-Null
            Write-Host "Installed '$TaskName' (runs every day at 09:00)."
            return
        }
        if ($Task -eq 'alerts') {
            # One short check every 15 minutes; a check that is still running (a slow PowerShell call) is not doubled.
            $act = New-ScheduledTaskAction -Execute $Pythonw -Argument '-m pcassist alerts' -WorkingDirectory $Root
            $trg = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable
            Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg -Settings $set `
                -Description 'pcassist alert check (Windows notifications)' -Force | Out-Null
            Write-Host "Installed '$TaskName' (runs every 15 minutes)."
            return
        }
        $act = New-ScheduledTaskAction -Execute $Pythonw -Argument '-m pcassist collect --interval 30' -WorkingDirectory $Root
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
            -Description 'pcassist background metrics collector' -Force | Out-Null
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
