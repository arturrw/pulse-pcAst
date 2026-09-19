# One unattended pass of the "CS2 FPS BENCHMARK DUST2" workshop map, recorded with PresentMon.
#   powershell -File scripts\bench_run.ps1 [-Name base_1] [-Set @{ 'setting.msaa_samples' = '2' }]
# Sets the given cs2_video.txt keys (game must be closed), starts PresentMon, launches CS2 on the
# benchmark map, waits for the game's own VProf report, closes the game, restores your settings and
# prints the session report. Needs admin OR membership in "Performance Log Users" (PresentMon/ETW):
#   Once, as admin (then log off/on):
#   Add-LocalGroupMember -SID S-1-5-32-559 -Member "$env:USERDOMAIN\$env:USERNAME"
param(
    [string]$Name = ("bench_" + (Get-Date -Format "yyyyMMdd_HHmmss")),
    [hashtable]$Set = @{},
    [string]$MapId = "3240880604",
    [string]$MapName = "de_dust2",   # what the Play > Workshop menu loads; its cfg pulls in the benchmark setup
    [int]$TimeoutMin = 10,
    [switch]$KeepSettings
)

$Root = Split-Path $PSScriptRoot -Parent
$OutDir = Join-Path $Root "data\bench"
$PresentMon = Join-Path $env:USERPROFILE "Tools\PresentMon\PresentMon-2.5.1-x64.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

function Set-VideoSettings([string]$Path, [hashtable]$Values) {
    # Replaces the value of each "key" "value" pair; throws if a key is missing (a typo must not go unnoticed).
    $text = [IO.File]::ReadAllText($Path)
    foreach ($key in $Values.Keys) {
        $m = [regex]::Match($text, '("' + [regex]::Escape($key) + '"\s+")([^"]*)(")')
        if (-not $m.Success) { throw "setting '$key' not found in $Path" }
        $text = $text.Substring(0, $m.Groups[2].Index) + [string]$Values[$key] + $text.Substring($m.Groups[3].Index)
    }
    [IO.File]::WriteAllText($Path, $text)
}

function Get-SteamPaths {
    $steam = (Resolve-Path (Get-ItemProperty "HKCU:\Software\Valve\Steam").SteamPath).Path
    $libs = @($steam) + @(Select-String -Path (Join-Path $steam "steamapps\libraryfolders.vdf") -Pattern '"path"\s+"([^"]+)"' |
        ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' })
    $game = $libs | ForEach-Object { Join-Path $_ "steamapps\common\Counter-Strike Global Offensive\game\csgo" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    # the Steam account whose CS2 settings changed last is the one in use
    $video = Get-ChildItem (Join-Path $steam "userdata") -Directory | ForEach-Object { Join-Path $_.FullName "730\local\cfg\cs2_video.txt" } |
        Where-Object { Test-Path $_ } | Sort-Object { (Get-Item $_).LastWriteTime } -Descending | Select-Object -First 1
    [pscustomobject]@{ SteamExe = (Join-Path $steam "steam.exe"); GameDir = $game; Video = $video }
}

if ($MyInvocation.InvocationName -eq '.') { return }   # dot-sourced for tests: functions only

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$inLogGroup = [bool]([Security.Principal.WindowsIdentity]::GetCurrent().Groups | Where-Object { $_.Value -eq 'S-1-5-32-559' })
if (-not ($isAdmin -or $inLogGroup)) {
    Write-Host "PresentMon needs admin rights or membership in 'Performance Log Users'. Once, as admin (then log off/on):"
    Write-Host '  Add-LocalGroupMember -SID S-1-5-32-559 -Member "$env:USERDOMAIN\$env:USERNAME"'
    exit 1
}
if (-not (Test-Path $PresentMon)) { Write-Host "PresentMon not found: $PresentMon"; exit 1 }
if (Get-Process cs2 -ErrorAction SilentlyContinue) { Write-Host "CS2 is running: close it first (its settings would be overwritten on exit)."; exit 1 }
$p = Get-SteamPaths
if (-not $p.GameDir -or -not $p.Video) { Write-Host "Could not find the CS2 folder or cs2_video.txt (Steam: $($p.SteamExe))"; exit 1 }
New-Item -ItemType Directory -Force (Join-Path $OutDir "backup") | Out-Null
$csv = Join-Path $OutDir "$Name.csv"
if (Test-Path $csv) { Write-Host "$csv already exists, pick another -Name."; exit 1 }
$log = Join-Path $p.GameDir "console.log"
$backup = Join-Path $OutDir ("backup\cs2_video_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".txt")
Copy-Item $p.Video $backup
Write-Host "Settings backup: $backup"

$pm = $null
try {
    if ($Set.Count -gt 0) {
        Set-VideoSettings $p.Video $Set
        Write-Host ("Applied: " + (($Set.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ", "))
    }
    if (Test-Path $log) { Remove-Item $log }
    $pm = Start-Process $PresentMon -ArgumentList "--process_name cs2.exe --output_file `"$csv`" --date_time --stop_existing_session" -NoNewWindow -PassThru
    Start-Sleep -Seconds 3
    if ($pm.HasExited) { throw "PresentMon exited right after start (see its message above)" }
    Start-Process $p.SteamExe -ArgumentList "-applaunch 730 -novid -condebug +map_workshop $MapId $MapName"
    Write-Host "Launched CS2 on $MapName. Waiting for the game..."

    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Get-Process cs2 -ErrorAction SilentlyContinue)) {
        if ((Get-Date) -gt $deadline) { throw "CS2 did not start within 3 minutes" }
        Start-Sleep -Seconds 2
    }
    Write-Host "CS2 is running. Waiting for the benchmark report (up to $TimeoutMin min)..."
    $deadline = (Get-Date).AddMinutes($TimeoutMin)
    $done = $false
    while ((Get-Date) -lt $deadline -and (Get-Process cs2 -ErrorAction SilentlyContinue)) {
        Start-Sleep -Seconds 3
        if ((Test-Path $log) -and (Select-String -Path $log -Pattern "VProfLite stopped" -Quiet -ErrorAction SilentlyContinue)) { $done = $true; break }
    }
    if (-not $done) { throw "no VProf report in console.log (the benchmark did not start by itself, or the map failed to load)" }
    Start-Sleep -Seconds 3
    $lines = Get-Content $log
    $stopLine = $lines | Select-String "^(\d\d/\d\d \d\d:\d\d:\d\d) \[VProf\] VProfLite stopped" | Select-Object -Last 1
    $startLine = $lines | Select-String "^(\d\d/\d\d \d\d:\d\d:\d\d) \[VProf\] VProfLite started" | Select-Object -Last 1
    $from = ($lines | Select-String "-- Performance report --" | Select-Object -Last 1).LineNumber
    if ($from) { $lines[($from - 1)..($lines.Count - 1)] | Set-Content (Join-Path $OutDir "$Name.vprof.txt") }
    $fps = $lines | Select-String "FPS: Avg=" | Select-Object -Last 1
    Write-Host "Game report: $($fps.Line)"
}
finally {
    Get-Process cs2 -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 4
    if ($pm -and -not $pm.HasExited) { Stop-Process -Id $pm.Id -Force }
    & $PresentMon --terminate_existing_session *> $null   # a killed PresentMon leaves its ETW session running
    if ($Set.Count -gt 0 -and -not $KeepSettings) { Copy-Item $backup $p.Video -Force; Write-Host "Settings restored." }
}
if ((Test-Path $csv) -and $stopLine -and $startLine) {
    # Analysis window = the benchmark itself (game's VProf start..stop), not loading or game exit.
    # PresentMon's clock is not local time (3 h ahead here): estimate the offset from its last frame vs. now.
    # The last CSV line is cut off (PresentMon is killed), so take the last complete one.
    $last = Get-Content $csv -Tail 5 | ForEach-Object { $_.Split(',') } | Where-Object { $_ -match '^\d{4}-\d+-\d+ \d+:\d+:\d+' } | Select-Object -Last 1
    $y, $mo, $d = ($last.Split(' ')[0]).Split('-'); $hms = $last.Split(' ')[1].Split('.')[0].Split(':')
    $lastPm = Get-Date -Year $y -Month $mo -Day $d -Hour $hms[0] -Minute $hms[1] -Second $hms[2]
    $offset = [Math]::Round(($lastPm - (Get-Date)).TotalMinutes / 30) * 30
    $times = foreach ($ln in $startLine, $stopLine) {
        $t = [datetime]::ParseExact($ln.Matches[0].Groups[1].Value, "MM/dd HH:mm:ss", $null)
        $t.AddYears((Get-Date).Year - $t.Year).AddMinutes($offset).ToString("yyyy-M-d HH:mm:ss")
    }
    $times | Set-Content (Join-Path $OutDir "$Name.end.txt")
}
if (Test-Path $csv) {
    & $Python -m pcassist session report $csv --process cs2.exe
}
