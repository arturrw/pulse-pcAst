# Auto-tune: every CS2 graphics setting one step lower than yours, each benchmarked against your own settings, then a
# ranking of what each step gains.
#   powershell -File scripts\bench_autotune.ps1 [-Repeats 2] [-Scene bots]
# The steps come from your current cs2_video.txt (pulse session tune-plan); the runs are an ordinary bench_batch.ps1
# batch, so your settings are restored after every run and the app shows the batch like any other.
# Do not use the PC while it runs: an unfocused game is not measured fairly.
param(
    [int]$Repeats = 2,
    [int]$From = 1,                                    # resume an interrupted auto-tune with the same -Tag
    [string]$Tag = ("tune" + (Get-Date -Format "MMddHHmm")),
    [ValidateSet("benchmark", "bots")][string]$Scene = "benchmark",
    [string]$DataDir = "",                             # see bench_run.ps1
    [string]$Pulse = "",
    [switch]$Log                                       # keep everything printed in <DataDir>\bench\<Tag>.log (the app reads it)
)

$Root = Split-Path $PSScriptRoot -Parent
if (-not $DataDir) { $DataDir = Join-Path $Root "data" }
$BenchDir = Join-Path $DataDir "bench"
New-Item -ItemType Directory -Force $BenchDir | Out-Null
function Invoke-Pulse {
    if ($Pulse) { & $Pulse @args } else { & (Join-Path $Root ".venv\Scripts\python.exe") -m pulse @args }
}
if ($Log) { Start-Transcript -Path (Join-Path $BenchDir "$Tag.log") -Append | Out-Null }
try {
    $steps = Invoke-Pulse session tune-plan
    if ($LASTEXITCODE -ne 0) { exit 1 }   # the reason is already printed
    $variants = @("base") + ($steps -split ",")
    $minutes = $variants.Count * ($Repeats - $From + 1) * $(if ($Scene -eq "bots") { 4 } else { 3 })
    Write-Host "Auto-tune $Tag : $($variants -join ', ') x $Repeats runs, about $minutes min. Do not use the PC meanwhile."

    & (Join-Path $PSScriptRoot "bench_batch.ps1") -Repeats $Repeats -From $From -Tag $Tag -Only $variants -Scene $Scene -DataDir $DataDir -Pulse $Pulse

    Write-Host "`n=== Auto-tune ($Tag) ==="
    Invoke-Pulse session tune-report $BenchDir --tag $Tag --process cs2.exe | Out-Host
}
finally {
    if ($Log) { Stop-Transcript | Out-Null }
}
