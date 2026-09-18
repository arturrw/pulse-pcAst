# Run the metrics collector in the background at every Windows logon.
#   powershell -File scripts\autostart.ps1 install   # register + start now
#   powershell -File scripts\autostart.ps1 remove    # stop + unregister
#   powershell -File scripts\autostart.ps1 status
param([Parameter(Mandatory)][ValidateSet('install', 'remove', 'status')][string]$Action)

$TaskName = 'pcassist-collect'
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'   # pythonw = no console window

switch ($Action) {
    'install' {
        if (-not (Test-Path $Pythonw)) { throw "Not found: $Pythonw (create .venv and run: pip install -e .)" }
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
