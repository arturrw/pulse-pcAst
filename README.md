# pc-ai-assistant

Local AI assistant that analyzes the state of your own computer. Everything runs locally
(psutil + NVML for metrics, SQLite for storage, Ollama for the LLM). Read-only: it never
changes or deletes anything.

## Status
- [x] Stage 1: metrics collector -> SQLite
- [x] Stage 2: LLM tools + chat via Ollama (`qwen3:8b`)
- [x] Stage 3a: disk-fill forecast (`disk_forecast`, needs 24+ h of history to be reliable)
- [ ] Stage 3b: anomaly detection
- [ ] Stage 4: dashboard / reports

## Requirements
- Windows, Python 3.10+
- [Ollama](https://ollama.com) running locally with a tool-capable model: `ollama pull qwen3:8b`
- NVIDIA GPU for GPU metrics (via NVML)

## Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Usage
```powershell
pcassist collect --once          # one sample, prints summary
pcassist collect --interval 30   # keep collecting every 30 s (history for the chat)
pcassist scan C:\ --limit 10     # largest subfolders of a directory
pcassist chat                    # chat with the local model (needs Ollama running)
pcassist chat --think            # enable model reasoning (slower)
pcassist chat --model <name>     # use another Ollama model
```

Metrics go to `data/metrics.db` (override with `--db`). Questions about history
("how did GPU temperature change over the last hour?") need `pcassist collect` to have run
for a while; without data the assistant tells you to start it.

## Background collection (autostart)
History-based answers (and a reliable `disk_forecast`, 24+ h) need `collect` running all the time.
A Task Scheduler job starts it hidden at every logon (no admin rights needed; no console window;
no 72 h time limit; runs on battery; a watchdog trigger re-launches it every 5 min if the process died,
while a running collector makes that a no-op):
```powershell
powershell -File scripts\autostart.ps1 install   # register and start now
powershell -File scripts\autostart.ps1 status
powershell -File scripts\autostart.ps1 remove    # stop and unregister
```
Data stays in `data/metrics.db` (roughly 5-10 MB/day, measured from the first samples at the default 30 s interval; history older than 90 days is deleted automatically, change with `collect --keep-days N`, 0 = keep all).
Manual cleanup and file shrink: `pcassist prune --days 30`.

## Game sessions (FPS / frame time analysis)
Analyzes one recorded game session: FPS, 1% / 0.1% lows, what limits the frame rate (GPU or CPU),
hitches with what the hardware was doing at that moment, temperatures, VRAM. All numbers are computed
deterministically; nothing depends on the LLM.

Record (the game must not use anti-cheat that blocks ETW; PresentMon does not inject into the game):
1. Install PresentMon (console, `PresentMon-2.5.1-x64.exe`) to `%USERPROFILE%\Tools\PresentMon\`.
2. Optional, for temperatures/VRAM/CPU: MSI Afterburner -> Settings -> Monitoring -> enable *Log history to file*
   (put the `.hml` into `data\sessions\`).
3. Start the recorder, then the game. It asks for admin rights itself and stops when the game exits. `-Name` is optional (default: game + date/time); it refuses to overwrite an existing file:
```powershell
powershell -File scripts\record_presentmon.ps1 -Process cs2.exe -Name before_shadows_high
```
Analyze (window = the longest stretch of normal FPS, so map loading is cut off; override with `--start/--end HH:MM`):
```powershell
python -m pcassist session report data\sessions\cs2_presentmon.csv --hml data\sessions\cs2.hml --process cs2.exe
python -m pcassist session compare before.csv after.csv --hml-before a.hml --hml-after b.hml
```
PresentMon and Afterburner stamp time in different zones; the shift is detected automatically from the two
recordings (`--pm-offset-hours` overrides). `data/sessions/` is git-ignored. Use the same scene or benchmark
route for a before/after comparison, and repeat each setting at least twice: single runs vary.

### CS2 settings A/B (unattended benchmark)

`scriptsench_batch.ps1 -Repeats 2 -Tag x -Only base,shadowL,fsr3` runs the workshop FPS benchmark
per variant with PresentMon and prints a table (`--slices` adds FPS per 10 s of the route). Results
land in `data/bench/` (git-ignored). Findings on RTX / 1440p, all settings max as `base` (264 avg FPS,
1% low 92, 0.1% low 61), 2 runs per variant, repeat spread is 0.1-5 FPS:

| variant | avg FPS | 1% low | 0.1% low |
|---|---|---|---|
| shadows Low | +4.5% | +5% | +6% |
| shaders Low | +4.6% | +3% | +3% |
| AO off | +2% | +1% | +2% |
| dynamic shadows off | 0 (noise) | +1% | +5% |
| shadows + shaders Low | +10% | +10% | +7% |
| FSR 2 / 3 / 4 | +14% / +20% / +19% | +32% / +37% / +41% | +33% / +39% / +32% |
| 1080p | +14% | +27% | +42% |
| everything minimum, 1440p | +27% | +33% | +16% |

- The route is deterministic: the same places (20-40 s, 80-90 s) are slow in every run and every variant.
  There GPU time is about 2x and CPU busy time 3-6x the median, so lows come from heavy scenes, not random hitches.
- Only 1-2 frames per run exceed 30 ms, at fixed route seconds (0 s, 7 s) whatever the settings: a
  scripted event of the map, not a settings problem.
- Resolution is the main lever for lows. To keep 1440p output, FSR gets the same lows as 1080p;
  the FSR values 3 and 4 are within noise of each other. Which number is which FSR preset is unverified
  (0 = off, FPS grows with the value); check in the game menu.
- Shadow and shader quality are worth about +10% together, AO and dynamic shadows are not worth touching.
- Not measured: image quality (FSR softens the picture), CPU-limited scenes (this map is GPU-bound).

## Chat tools
The model answers only by calling these read-only tools:

| Tool | Use |
|---|---|
| `current_status` | live CPU/RAM/disk I/O/network/GPU and top processes |
| `disk_usage` | free/used space per disk |
| `top_processes` | heaviest processes over a recent window (from history) |
| `metrics_history` | min/avg/max/latest, when the max happened and the change over the last 10 min (from history) |
| `disk_forecast` | growth in GB/day and days until each disk is full (linear trend; flagged unreliable under 24 h of history) |
| `largest_folders` | what takes the most space in a directory (scan up to ~45 s) |

## Tests
`tests/eval_tools.py` checks that the model picks the right tool for typical questions
(e.g. "disk load" -> `current_status`, "free space" -> `disk_usage`). Needs Ollama running:
```powershell
python tests\eval_tools.py --runs 2   # add --slow to include the folder scan case
python tests\test_forecast.py        # unit tests for disk_forecast, no Ollama needed
python tests\test_prune.py           # unit tests for history cleanup
python tests\test_collect_loop.py   # collector survives bad samples
```
