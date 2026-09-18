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
no 72 h time limit; runs on battery; restarts up to 3 times if it crashes):
```powershell
powershell -File scripts\autostart.ps1 install   # register and start now
powershell -File scripts\autostart.ps1 status
powershell -File scripts\autostart.ps1 remove    # stop and unregister
```
Data stays in `data/metrics.db` (~10-15 MB/day at the default 30 s interval; nothing is pruned yet).

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
```
