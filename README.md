# pc-ai-assistant

Local AI assistant that analyzes the state of your own computer. Everything runs locally
(psutil + NVML for metrics, SQLite for storage, Ollama for the LLM). Read-only: it never
changes or deletes anything.

## Status
- [x] Stage 1: metrics collector -> SQLite
- [x] Stage 2: LLM tools + chat via Ollama (`qwen3:8b`)
- [x] Stage 3a: disk-fill forecast (`disk_forecast`, needs 24+ h of history to be reliable)
- [x] Stage 3b: anomaly detection for state metrics (inside `metrics_history`, statistical, see below)
- [x] Stage 4: HTML report of the history (`pcassist report`); there is no live dashboard

## Quick start
1. Install Python 3.10+ and [Ollama](https://ollama.com), then `ollama pull qwen3:8b`.
2. In the project folder:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -e .
   ```
3. Start collecting metrics in the background (a hidden Task Scheduler job, no admin rights; it needs hours to
   days of history before the answers get interesting):
   ```powershell
   powershell -File scripts\autostart.ps1 install
   ```
4. Ask about your PC: `pcassist chat`. For example "how much free space do I have?", "was there a spike in GPU
   temperature in the last hour?", "what is using the most CPU?", "when will my disk fill up?" (a reliable answer
   needs 24+ h of history). The model only calls read-only tools; it cannot change anything.
5. See the history at a glance: `pcassist report --open` (charts, unusual periods, disks, heaviest processes).
6. Optional, for games: record a session with PresentMon (see [Game sessions](#game-sessions-fps--frame-time-analysis))
   and ask "show the FPS of my last recording" or "compare recordings A and B".

Everything runs and stays on this machine: the database, recordings and reports live in `data/`, which is git-ignored.

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
pcassist report --hours 24 --open  # HTML report of the last 24 h (written to data/reports/latest.html)
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

## Process watch (unusual load)
`process_watch` (chat tool and a section of the report) looks at the processes in two ways.

**Against their own history** (`procwatch.py`): a name that never appeared before, a process using far more CPU than
its usual level (or a lot of CPU with nothing to compare to), and a process whose memory keeps growing. That is the
shape of a cryptominer or a runaway program. While there is under 24 h of history this part is marked low confidence,
because "never recorded" then mostly means "not seen yet".

**By the file it runs from** (`binaries.py`): the collector records the file path of each process it can read, and the
tool flags a Windows system name (`svchost.exe`, `lsass.exe`, ...) running from any folder but System32, a program
running from Downloads / Temp / Public / the Recycle Bin, and files whose digital signature is broken or untrusted.
Signatures are checked with Windows' own `Get-AuthenticodeSignature`, once per file (cached in the database, checked
again only if the file changes); nothing is uploaded anywhere. This part does not depend on how long the history is.
An unsigned program in Program Files is normal and is not flagged; an unsigned file in Downloads is "high".

**By the network** (`netwatch.py`): the collector also records who talks to whom: outbound TCP connections to public
addresses and TCP ports listening beyond localhost, as (process, address, port) with first and last time seen. No
traffic volume (Windows does not give it per process) and no content. The tool reports a suspicious file that connects
out at all (high), a connection to a port typical of mining pools, Tor or IRC botnets, a program that started
listening for incoming connections when it never did before, and a program with a small stable set of destinations
that suddenly talks to a new one. The last two are only judged after 24 h of network history and skip browser-like
processes with dozens of destinations, otherwise the first day would be all noise.

**Privacy:** the connection table is a list of the public addresses your PC talked to. It stays in the local database
(`data/`, git-ignored, pruned with the rest of the history after 90 days), is not shown in full anywhere (the chat and
the report only list flagged connections) and nothing is looked up or sent (no reverse DNS, no reputation service).

It is **not** an antivirus and never says a process is malicious or safe. The collector records only the heaviest
processes (~26 per sample) and cannot read the file path of protected Windows processes, so a quiet process that
never opens a connection is invisible to it, and there is no parent process or amount of data sent. Anything odd
still needs a human check: Task Manager -> Open file location, and a Windows Defender scan. Thresholds are in
`src/pcassist/procwatch.py`, `src/pcassist/binaries.py` and `src/pcassist/netwatch.py`.

## Alerts
`pcassist alerts` checks the history once and shows a Windows notification (also written to `data/alerts.log` and
listed in the report) for things worth interrupting you for: the collector stopped recording, a GPU at 85 C or more
for 5 minutes, a disk with under 15 GB (or 5%) free or a 14-day fill-up forecast, an unusual stretch of temperature /
RAM / swap that is not explained by a game, and the serious findings of `process_watch` (a disguised or tampered
file, a suspicious file using the network, a connection to a mining-pool / Tor / IRC port, a program that started
listening for incoming connections, a process using 40% of the CPU, memory growing 1 GB/h). The same alert is not repeated for 6 h (serious) or
24 h (the rest). A name that is merely new is not alerted: that evidence is too weak.
```powershell
pcassist alerts --test                                     # show a test notification
pcassist alerts --dry-run                                  # print what would be sent, send and remember nothing
powershell -File scripts\autostart.ps1 install -Task alerts  # check every 15 minutes in the background
```
If the test notification does not appear, check that Windows Focus assist / Do not disturb is off; the alerts still
reach `data/alerts.log` and the report. Thresholds are at the top of `src/pcassist/alerts.py`.

## Report
`pcassist report` writes one self-contained HTML file (inline SVG charts, no scripts, no network, light and dark
theme): a summary (min / average / max), unusual periods, charts of GPU temperature, CPU, RAM and GPU load, disks
with the fill-up forecast, the heaviest processes and the latest game recordings. It uses the same functions as the
chat tools, so both always show the same numbers. Gaps where the PC was off are left blank instead of being joined
by a line, and the report says how much of the period was actually recorded.

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

`scripts\bench_batch.ps1 -Repeats 2 -Tag x -Only base,shadowL,fsr3` runs the workshop FPS benchmark
per variant with PresentMon and prints a table (`--slices` adds FPS per 10 s of the route). Results
land in `data/bench/` (git-ignored). Findings on 1440p, all settings max as `base` (264 avg FPS,
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
| everything minimum, 1440p | +27% | +33% | +22% |
| FSR 3 + shadows and shaders Low (separate batch, 3 runs, see below) | +31% | +65% | +70% |

- The route is deterministic: the same places (20-40 s, 80-90 s) are slow in every run and every variant.
  There GPU time is about 2x and CPU busy time 3-6x the median, so lows come from heavy scenes, not random hitches.
- Only 1-2 frames per run exceed 30 ms, at fixed route seconds (0 s, 7 s) whatever the settings: a
  scripted event of the map, not a settings problem.
- Resolution is the main lever for lows. To keep 1440p output, FSR gets the same lows as 1080p;
  the FSR values 3 and 4 are within noise of each other. Values of `videocfg_fsr_detail`: 0 = Disabled,
  3 = Balanced (both confirmed against the game menu); by the menu order and FPS growth 1 = Ultra Quality,
  2 = Quality, 4 = Performance (not confirmed directly).
- Shadow and shader quality are worth about +10% together, AO and dynamic shadows are not worth touching.
- Recommended combo, `-Only base,fsr3,fsr3ShadowShaderL -Repeats 3`, 1440p (repeat spread 0.8-1.4 FPS):

  | variant | avg FPS | 1% low | 0.1% low |
  |---|---|---|---|
  | base | 265.4 | 92.6 | 60.3 |
  | FSR 3 | 320.3 | 138.6 | 99.2 |
  | FSR 3 + shadows and shaders Low | 347.2 | 152.9 | 102.3 |

  Low shadows and shaders on top of FSR 3 add +8% avg and +10% 1% low; 0.1% low is within noise
  (102 vs 99). The slow route stretches level out: 20-30 s goes 188 -> 249 -> 296 FPS (base -> FSR 3 -> combo),
  80-90 s goes 214 -> 294 -> 324. Hitches are unchanged (1-2 frames at 0 s and 7 s). Alone, FSR 3 gave
  a higher 1% low here (+50%) than in the 2-run table above (+37%), so treat the lows as +-10%.
- Not measured: image quality (FSR softens the picture), CPU-limited scenes (this map is GPU-bound).

## Chat tools
The model answers only by calling these read-only tools:

| Tool | Use |
|---|---|
| `current_status` | live CPU/RAM/disk I/O/network/GPU and top processes |
| `disk_usage` | free/used space per disk |
| `top_processes` | heaviest processes over a recent window (from history) |
| `metrics_history` | min/avg/max/latest, when the max happened and the change over the last 10 min (from history); for GPU temperature, RAM and swap also the unusual periods (see below) |
| `disk_forecast` | growth in GB/day and days until each disk is full (linear trend; flagged unreliable under 24 h of history) |
| `largest_folders` | what takes the most space in a directory (scan up to ~45 s) |
| `game_sessions` | recorded game sessions and benchmark runs (PresentMon CSV in `data/sessions`, `data/bench`) |
| `game_session_report` | FPS, 1% / 0.1% lows, limiter, slowest 10 s stretches and hitches of one recording |
| `game_sessions_compare` | avg FPS / lows / p99 of two recordings side by side |
| `process_watch` | processes that stand out against their own history: never recorded before, far more CPU than usual, memory growing (behavioral only, not a malware scan) |

## Anomaly detection
`src/pcassist/anomaly.py`: a value counts as unusual when it stays far outside the median of the previous
~2.5 h (robust z-score on median / MAD, only past samples are used), for at least ~3 min, and moves at
least a per-metric minimum (8 degrees, 8 points of RAM, 5 points of swap) from what was typical. A gap in the
history (PC off) resets the window, so the first ~37 min after each start are not checked. Only state metrics
are checked: load metrics (GPU usage, disk, network, CPU) swing whenever a game starts, so their outliers are
just workload. It reports statistical outliers, not faults.

Scored on the labeled [Numenta Anomaly Benchmark](https://github.com/numenta/NAB) (MIT license; not part of this
repo: `git clone --depth 1 https://github.com/numenta/NAB data/nab`, only its CSV/JSON data is read, none of its
code is run): 41 of 55 labeled windows found at 32 false alarms per 10k points with the loosest settings; requiring
6 samples in a row gives 25 of 55 at 11 false alarms. Bursty series (disk writes, request counts) are the weak spot.
Parameters were tuned on the same data, so treat the numbers as optimistic:
```powershell
python tests\eval_nab.py --sweep
```

## Choosing a model
`tests/eval_tools.py` (21 questions in Russian and English: tool choice, numbers in the answer, answer language) on an
RTX 3070 Ti with 8 GB VRAM and 32 GB RAM:

| model | eval | time per question | memory |
|---|---|---|---|
| `qwen3:8b` (default) | 59/63 (94%) | ~5 s | fits in VRAM (6 GB) |
| `gpt-oss:20b` (`--model gpt-oss:20b`) | 19/21 (90%) | ~18 s | 14 GB, 57% of it runs on the CPU |

The difference is within the noise of these small samples, so the default stays the fast model; `gpt-oss:20b` is
worth trying when answers must be as precise as possible and speed does not matter. The remaining
misses are noise: the average is sometimes left out of a "was there a spike" answer, and `gpt-oss:20b` occasionally
lets Russian words into an English answer. The unusual-period check used to be a separate `anomalies` tool; models
picked it for spike questions about half the time, so it now lives inside `metrics_history` (one tool, no wrong choice).
The model copies examples from the system prompt, so keep the examples in it language-neutral (a Russian example
made English questions get Russian answers, an English one the opposite).

## Tests
`tests/eval_tools.py` checks that the model picks the right tool for typical questions
(e.g. "disk load" -> `current_status`, "free space" -> `disk_usage`). Needs Ollama running:
```powershell
python tests\eval_tools.py --runs 2   # add --slow to include the folder scan case
python tests\test_forecast.py        # unit tests for disk_forecast, no Ollama needed
python tests\test_prune.py           # unit tests for history cleanup
python tests\test_game_tools.py      # unit tests for the game_* chat tools
python tests\test_anomaly.py         # detector and the unusual-period check, no Ollama needed
python tests\test_report.py          # HTML report (sections, escaping, gaps), no Ollama needed
python tests\test_procwatch.py       # process behavior and file location/signature checks, synthetic data, no Ollama
python tests\test_netwatch.py        # network findings and connection recording, no Ollama needed
python tests\test_alerts.py          # alert checks, cooldown and notification log, no Ollama needed
python tests\test_collect_loop.py   # collector survives bad samples
```
