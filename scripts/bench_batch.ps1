# A/B batch: several settings variants x N repeats through bench_run.ps1, then a summary table.
#   powershell -File scripts\bench_batch.ps1 [-Repeats 3] [-Tag ab1]
# Variants are interleaved (base, msaa2, shadows, base, ...) so drift (thermals, background load)
# hits every variant equally. Edit $Variants to test other settings.
param(
    [int]$Repeats = 3,
    [string]$Tag = (Get-Date -Format "MMdd_HHmm")
)

$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Bench = Join-Path $PSScriptRoot "bench_run.ps1"

$Variants = [ordered]@{
    base    = @{}
    msaa2   = @{ 'setting.msaa_samples' = '2' }
    shadowM = @{ 'setting.videocfg_shadow_quality' = '1' }   # verify the value scale for "Medium" in cs2_video.txt
}

for ($i = 1; $i -le $Repeats; $i++) {
    foreach ($v in $Variants.Keys) {
        $name = "${Tag}_${v}_$i"
        Write-Host "`n=== $name ==="
        & powershell -NoProfile -File $Bench -Name $name -Set $Variants[$v]
        if ($LASTEXITCODE -ne 0) { Write-Host "run $name failed, continuing" }
    }
}

Write-Host "`n=== Summary ($Tag) ==="
& $Python -m pcassist session summary (Join-Path $Root "data\bench") --tag $Tag --process cs2.exe
