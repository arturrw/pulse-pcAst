# Record per-frame timings of a running game with PresentMon (needs admin: asks for it itself).
#   powershell -File scripts\record_presentmon.ps1 [-Process cs2.exe] [-Name cs2_presentmon]
# Start it, then launch the game. Recording stops when the game exits (or Ctrl+C).
param(
    [string]$Process = "cs2.exe",
    [string]$Name = "cs2_presentmon"
)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
                 "-Process", $Process, "-Name", $Name)
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

$exe = Join-Path $env:USERPROFILE "Tools\PresentMon\PresentMon-2.5.1-x64.exe"
$out = Join-Path (Split-Path $PSScriptRoot -Parent) "data\sessions\$Name.csv"
if (-not (Test-Path $exe)) { Write-Host "PresentMon not found: $exe"; Read-Host "Enter to close"; exit 1 }
New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null

Write-Host "Waiting for $Process. Output: $out"
Write-Host "Launch the game now. Stops when the game exits, or press Ctrl+C."
& $exe --process_name $Process --output_file $out --date_time --terminate_on_proc_exit
Write-Host "Done: $out"
Read-Host "Enter to close"
