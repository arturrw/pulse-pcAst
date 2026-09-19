# A/B batch: several settings variants x N repeats through bench_run.ps1, then a summary table.
#   powershell -File scripts\bench_batch.ps1 [-Repeats 3] [-Tag ab1]
# Variants are interleaved (base, msaa2, shadows, base, ...) so drift (thermals, background load)
# hits every variant equally. Edit $Variants to test other settings.
param(
    [int]$Repeats = 3,
    [string]$Tag = (Get-Date -Format "MMdd_HHmm"),
    [string[]]$Only = @("base", "msaa2", "shadowM")   # variants to run, see $All below
)

$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Bench = Join-Path $PSScriptRoot "bench_run.ps1"

# cs2_video.txt values: shadow_quality 0=Low 1=Medium 2=High; ao_detail 0=Off 1=Low 2=Medium; shaderquality 0=Low 1=High;
# dynamic_shadows 0=Sun only 1=All; msaa_samples 0/2/4/8
$All = [ordered]@{
    base    = @{}
    msaa2   = @{ 'setting.msaa_samples' = '2' }
    shadowM = @{ 'setting.videocfg_shadow_quality' = '1' }
    shadowL = @{ 'setting.videocfg_shadow_quality' = '0' }
    dynOff  = @{ 'setting.videocfg_dynamic_shadows' = '0' }
    aoOff   = @{ 'setting.videocfg_ao_detail' = '0' }
    shaderL = @{ 'setting.shaderquality' = '0' }
    floor   = @{ 'setting.msaa_samples' = '0'; 'setting.videocfg_shadow_quality' = '0'; 'setting.videocfg_dynamic_shadows' = '0'
                 'setting.videocfg_ao_detail' = '0'; 'setting.shaderquality' = '0' }   # everything cheap: is there any headroom at all?
}
$Only = @($Only | ForEach-Object { $_ -split "," } | Where-Object { $_ })   # -File passes "a,b" as one string
$Variants = [ordered]@{}
foreach ($v in $Only) {
    if (-not $All.Contains($v)) { throw "unknown variant '$v' (known: $($All.Keys -join ', '))" }
    $Variants[$v] = $All[$v]
}

:outer for ($i = 1; $i -le $Repeats; $i++) {
    foreach ($v in $Variants.Keys) {
        $name = "${Tag}_${v}_$i"
        Write-Host "`n=== $name ==="
        & $Bench -Name $name -Set $Variants[$v]   # in-process: a hashtable can't be passed to a child powershell -File
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $Root "data\bench\$name.end.txt"))) {
            Write-Host "run $name failed, stopping the batch"; break outer
        }
    }
}

Write-Host "`n=== Summary ($Tag) ==="
& $Python -m pcassist session summary (Join-Path $Root "data\bench") --tag $Tag --process cs2.exe
