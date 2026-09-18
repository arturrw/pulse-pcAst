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
        $trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $set = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
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
