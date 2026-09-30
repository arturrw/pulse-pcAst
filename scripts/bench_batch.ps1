# A/B batch: several settings variants x N repeats through bench_run.ps1, then a summary table.
#   powershell -File scripts\bench_batch.ps1 [-Repeats 3] [-Tag ab1] [-Scene bots]
# Variants are interleaved (base, msaa2, shadows, base, ...) so drift (thermals, background load)
# hits every variant equally. The variants are in bench_variants.json.
param(
    [int]$Repeats = 3,
    [int]$From = 1,                                    # first repeat to run: resume an interrupted batch with the same -Tag
    [string]$Tag = (Get-Date -Format "MMdd_HHmm"),
    [string[]]$Only = @("base", "msaa2", "shadowM"),  # variants to run, see bench_variants.json
    [ValidateSet("benchmark", "bots")][string]$Scene = "benchmark",  # see bench_run.ps1
    [string]$DataDir = "",                             # see bench_run.ps1
    [string]$Pulse = ""
)

$Root = Split-Path $PSScriptRoot -Parent
if (-not $DataDir) { $DataDir = Join-Path $Root "data" }
$BenchDir = Join-Path $DataDir "bench"
$Bench = Join-Path $PSScriptRoot "bench_run.ps1"
# The app asks a running batch to stop by creating this file. It is checked between runs, so the run in progress
# still finishes and restores the settings (killing the script mid-run would leave the test settings in place).
$StopFile = Join-Path $BenchDir "batch.stop"
function Invoke-Pulse {
    if ($Pulse) { & $Pulse @args } else { & (Join-Path $Root ".venv\Scripts\python.exe") -m pulse @args }
}

# Variants (settings + the name the app shows) live in bench_variants.json next to this script; add new ones there.
function Get-BenchVariants([string]$Path) {
    $all = [ordered]@{}
    foreach ($v in (Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json).PSObject.Properties) {
        if ($v.Name.StartsWith("_")) { continue }
        $set = @{}
        foreach ($k in $v.Value.set.PSObject.Properties) { $set[$k.Name] = [string]$k.Value }
        $all[$v.Name] = $set
    }
    $all
}
if ($MyInvocation.InvocationName -eq '.') { return }   # dot-sourced for tests: functions only
$All = Get-BenchVariants (Join-Path $PSScriptRoot "bench_variants.json")
$Only = @($Only | ForEach-Object { $_ -split "," } | Where-Object { $_ })   # -File passes "a,b" as one string
$Variants = [ordered]@{}
foreach ($v in $Only) {
    if (-not $All.Contains($v)) { throw "unknown variant '$v' (known: $($All.Keys -join ', '))" }
    $Variants[$v] = $All[$v]
}

:outer for ($i = $From; $i -le $Repeats; $i++) {
    foreach ($v in $Variants.Keys) {
        if (Test-Path $StopFile) { Write-Host "stopped on request"; break outer }
        $name = "${Tag}_${v}_$i"
        Write-Host "`n=== $name ==="
        # in-process: a hashtable can't be passed to a child powershell -File
        & $Bench -Name $name -Set $Variants[$v] -Scene $Scene -DataDir $DataDir -Pulse $Pulse
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $BenchDir "$name.end.txt"))) {
            Write-Host "run $name failed, stopping the batch"; break outer
        }
    }
}

Write-Host "`n=== Summary ($Tag) ==="
Invoke-Pulse session summary $BenchDir --tag $Tag --process cs2.exe --slices
