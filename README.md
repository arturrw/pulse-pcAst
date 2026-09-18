# pc-ai-assistant

Local AI assistant that analyzes the state of your own computer. Everything runs locally
(psutil + NVML for metrics, SQLite for storage, Ollama for the LLM). Read-only: it never
changes or deletes anything.

## Status
- [x] Stage 1: metrics collector -> SQLite
- [x] Stage 2: LLM tools + chat via Ollama (`qwen3:8b`)
- [ ] Stage 3: anomaly detection, disk-fill forecast
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

## Chat tools
The model answers only by calling these read-only tools:

| Tool | Use |
|---|---|
| `current_status` | live CPU/RAM/disk I/O/network/GPU and top processes |
| `disk_usage` | free/used space per disk |
| `top_processes` | heaviest processes over a recent window (from history) |
| `metrics_history` | min/avg/max/latest of one metric over a window (from history) |
| `largest_folders` | what takes the most space in a directory (scan up to ~45 s) |

## Tests
`tests/eval_tools.py` checks that the model picks the right tool for typical questions
(e.g. "disk load" -> `current_status`, "free space" -> `disk_usage`). Needs Ollama running:
```powershell
python tests\eval_tools.py --runs 2   # add --slow to include the folder scan case
```
