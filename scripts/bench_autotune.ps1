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
    [ValidateSet("benchmark", "bots")][string]$Scene = "benchmark"
)

$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"

$steps = & $Python -m pulse session tune-plan
if ($LASTEXITCODE -ne 0) { exit 1 }   # the reason is already printed
$variants = @("base") + ($steps -split ",")
$minutes = $variants.Count * ($Repeats - $From + 1) * $(if ($Scene -eq "bots") { 4 } else { 3 })
Write-Host "Auto-tune $Tag : $($variants -join ', ') x $Repeats runs, about $minutes min. Do not use the PC meanwhile."

& (Join-Path $PSScriptRoot "bench_batch.ps1") -Repeats $Repeats -From $From -Tag $Tag -Only $variants -Scene $Scene

Write-Host "`n=== Auto-tune ($Tag) ==="
& $Python -m pulse session tune-report (Join-Path $Root "data\bench") --tag $Tag --process cs2.exe
