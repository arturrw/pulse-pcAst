# One unattended CS2 benchmark pass, recorded with PresentMon.
#   powershell -File scripts\bench_run.ps1 [-Name base_1] [-Set @{ 'setting.msaa_samples' = '2' }] [-Scene bots]
# Scenes:
#   benchmark (default) - the "CS2 FPS BENCHMARK DUST2" workshop map: a scripted camera flight past frozen bots.
#                         Repeatable, but light on the processor (the graphics card limits it).
#   bots                - a local deathmatch on de_dust2 with -Bots bots fighting and the spectator camera following
#                         the action: bot AI, animation and effects load the processor, like a real match. Not
#                         frame-for-frame repeatable, so run several repeats and look at the spread.
# Sets the given cs2_video.txt keys (game must be closed), starts PresentMon, launches CS2, waits for the
# measurement to end, closes the game, restores your settings and prints the session report. Needs admin OR membership in "Performance Log Users" (PresentMon/ETW):
#   Once, as admin (then log off/on):
#   Add-LocalGroupMember -SID S-1-5-32-559 -Member "$env:USERDOMAIN\$env:USERNAME"
param(
    [string]$Name = ("bench_" + (Get-Date -Format "yyyyMMdd_HHmmss")),
    [hashtable]$Set = @{},
    [string]$MapId = "3240880604",
    [string]$MapName = "de_dust2",   # what the Play > Workshop menu loads; its cfg pulls in the benchmark setup
    [int]$TimeoutMin = 10,
    [ValidateSet("benchmark", "bots")][string]$Scene = "benchmark",
    [int]$Bots = 16,                 # bots scene: how many bots fight
    [int]$WarmupSec = 30,            # bots scene: time for the bots to spread out before measuring
    [int]$MeasureSec = 120,          # bots scene: length of the measured stretch
    [switch]$KeepSettings,
    [string]$DataDir = "",           # where bench\ goes (default: the checkout's data folder; the app passes its own)
    [string]$Pulse = ""              # pulse.exe of an installed app (default: the checkout's python -m pulse)
)

$Root = Split-Path $PSScriptRoot -Parent
if (-not $DataDir) { $DataDir = Join-Path $Root "data" }
$OutDir = Join-Path $DataDir "bench"
# the newest PresentMon console program in the tools folder, the same place the app looks
$PresentMon = Get-ChildItem (Join-Path $env:USERPROFILE "Tools\PresentMon") -Filter "PresentMon*.exe" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
function Invoke-Pulse {
    if ($Pulse) { & $Pulse @args } else { & (Join-Path $Root ".venv\Scripts\python.exe") -m pulse @args }
}

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

# The bots scene hooks into CS2's own override slot: after gamemode_deathmatch.cfg the game runs
# cfg/gamemode_deathmatch_server.cfg if it exists (console.log says "couldn't exec ... gamemode_*_server.cfg" when not).
# Both files carry this marker on their first line, so a file left behind by a crashed run is recognised and removed,
# and a file the user wrote themselves is never touched.
$SceneMarker = "// written by Pulse scripts/bench_run.ps1 -Scene bots; deleted after the run"

function Get-BotSceneFiles([string]$GameDir, [int]$Bots, [int]$WarmupSec, [int]$MeasureSec) {
    $cfg = Join-Path $GameDir "cfg"
    $server = @(
        $SceneMarker,
        "sv_cheats 1",                            # exec_async (the timed script below) needs cheats; local server only
        "bot_quota_mode normal", "bot_quota $Bots", "bot_difficulty 2",
        "mp_warmup_end", "mp_timelimit 60", "mp_roundtime 60", "mp_ignore_round_win_conditions 1",
        "exec_async pulse_bots_timeline"
    )
    $timeline = @(
        $SceneMarker,
        "sleep 8000",                             # the player is connected by now
        "jointeam 1", "spec_autodirector 1", "spec_mode 4",   # spectate: the camera follows whoever is fighting
        "cl_drawhud 0", "r_drawviewmodel 0", "fps_max 0",
        "engine_no_focus_sleep 0",                # out of focus the game sleeps 20 ms a frame: 34 FPS instead of hundreds
        "sleep $($WarmupSec * 1000)",
        # the markers go through chat: -condebug does not write echo output to console.log, chat lines it does
        "say PULSE_BENCH_START",
        "sleep $($MeasureSec * 1000)",
        "say PULSE_BENCH_STOP",
        "engine_no_focus_sleep 20"                # the game's default, in case it gets saved
        # no disconnect: the game reloads the map after it (and runs this timeline again); the script closes the game
    )
    @(
        [pscustomobject]@{ Path = (Join-Path $cfg "gamemode_deathmatch_server.cfg"); Lines = $server },
        [pscustomobject]@{ Path = (Join-Path $cfg "pulse_bots_timeline.cfg"); Lines = $timeline }
    )
}

function Remove-BotSceneFiles($Files) {
    foreach ($f in $Files) {
        if ((Test-Path $f.Path) -and ((Get-Content $f.Path -TotalCount 1) -eq $SceneMarker)) { Remove-Item $f.Path }
    }
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
if (-not $PresentMon) { Write-Host "PresentMon not found in $env:USERPROFILE\Tools\PresentMon"; exit 1 }
if (Get-Process cs2 -ErrorAction SilentlyContinue) { Write-Host "CS2 is running: close it first (its settings would be overwritten on exit)."; exit 1 }
$p = Get-SteamPaths
if (-not $p.GameDir -or -not $p.Video) { Write-Host "Could not find the CS2 folder or cs2_video.txt (Steam: $($p.SteamExe))"; exit 1 }
New-Item -ItemType Directory -Force (Join-Path $OutDir "backup") | Out-Null
$csv = Join-Path $OutDir "$Name.csv"
if (Test-Path $csv) { Write-Host "$csv already exists, pick another -Name."; exit 1 }
$log = Join-Path $p.GameDir "console.log"
$sceneFiles = @()
if ($Scene -eq "bots") {
    $sceneFiles = Get-BotSceneFiles $p.GameDir $Bots $WarmupSec $MeasureSec
    Remove-BotSceneFiles $sceneFiles   # left over from a crashed run
    $foreign = @($sceneFiles | Where-Object { Test-Path $_.Path })
    if ($foreign) { Write-Host "$($foreign[0].Path) exists and was not written by this script: move it away first."; exit 1 }
    if (-not $PSBoundParameters.ContainsKey('TimeoutMin')) {   # a failed scene loops, so do not wait the default 10 min
        $TimeoutMin = [Math]::Ceiling(($WarmupSec + $MeasureSec) / 60) + 4
    }
    $startPattern, $stopPattern = "PULSE_BENCH_START", "PULSE_BENCH_STOP"
} else {
    $startPattern, $stopPattern = "\[VProf\] VProfLite started", "\[VProf\] VProfLite stopped"
}
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
    foreach ($f in $sceneFiles) { [IO.File]::WriteAllLines($f.Path, [string[]]$f.Lines) }
    $pm = Start-Process $PresentMon -ArgumentList "--process_name cs2.exe --output_file `"$csv`" --date_time --stop_existing_session" -NoNewWindow -PassThru
    Start-Sleep -Seconds 3
    if ($pm.HasExited) { throw "PresentMon exited right after start (see its message above)" }
    $launch = if ($Scene -eq "bots") { "+game_type 1 +game_mode 2 +map $MapName" } else { "+map_workshop $MapId $MapName" }
    Start-Process $p.SteamExe -ArgumentList "-applaunch 730 -novid -condebug $launch"
    Write-Host "Launched CS2 on $MapName ($Scene scene). Waiting for the game..."

    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Get-Process cs2 -ErrorAction SilentlyContinue)) {
        if ((Get-Date) -gt $deadline) { throw "CS2 did not start within 3 minutes" }
        Start-Sleep -Seconds 2
    }
    Write-Host "CS2 is running. Waiting for the end of the measurement (up to $TimeoutMin min)..."
    $deadline = (Get-Date).AddMinutes($TimeoutMin)
    $done = $false
    while ((Get-Date) -lt $deadline -and (Get-Process cs2 -ErrorAction SilentlyContinue)) {
        Start-Sleep -Seconds 3
        if ((Test-Path $log) -and (Select-String -Path $log -Pattern $stopPattern -Quiet -ErrorAction SilentlyContinue)) { $done = $true; break }
    }
    if (-not $done) { throw "the measurement did not finish (no '$stopPattern' in console.log): the scene did not start by itself, or the map failed to load" }
    Start-Sleep -Seconds 3
    $lines = Get-Content $log
    $stopLine = $lines | Select-String "^(\d\d/\d\d \d\d:\d\d:\d\d) .*$stopPattern" | Select-Object -Last 1
    $startLine = $lines | Select-String "^(\d\d/\d\d \d\d:\d\d:\d\d) .*$startPattern" | Select-Object -Last 1
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
    Remove-BotSceneFiles $sceneFiles
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
    Invoke-Pulse session report $csv --process cs2.exe
    # "Composed" present modes mean the game window was not in front (another window had focus): not comparable
    $modes = Import-Csv $csv | Group-Object PresentMode | Sort-Object Count -Descending
    if ($modes -and $modes[0].Name -like "Composed*") {
        Write-Host "WARNING: most frames were '$($modes[0].Name)': the game was not the focused full-screen window. Do not use the PC during a run; this result is not comparable."
    }
}
