# A/B batch: several settings variants x N repeats through bench_run.ps1, then a summary table.
#   powershell -File scripts\bench_batch.ps1 [-Repeats 3] [-Tag ab1] [-Scene bots]
# Variants are interleaved (base, msaa2, shadows, base, ...) so drift (thermals, background load)
# hits every variant equally. The variants are in bench_variants.json.
param(
    [int]$Repeats = 3,
    [int]$From = 1,                                    # first repeat to run: resume an interrupted batch with the same -Tag
    [string]$Tag = (Get-Date -Format "MMdd_HHmm"),
    [string[]]$Only = @("base", "msaa2", "shadowM"),  # variants to run, see bench_variants.json
    [ValidateSet("benchmark", "bots")][string]$Scene = "benchmark"   # see bench_run.ps1
)

$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Bench = Join-Path $PSScriptRoot "bench_run.ps1"

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
        $name = "${Tag}_${v}_$i"
        Write-Host "`n=== $name ==="
        & $Bench -Name $name -Set $Variants[$v] -Scene $Scene   # in-process: a hashtable can't be passed to a child powershell -File
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $Root "data\bench\$name.end.txt"))) {
            Write-Host "run $name failed, stopping the batch"; break outer
        }
    }
}

Write-Host "`n=== Summary ($Tag) ==="
& $Python -m pulse session summary (Join-Path $Root "data\bench") --tag $Tag --process cs2.exe --slices
