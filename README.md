# pc-ai-assistant

Local AI assistant that analyzes the state of your own computer. Everything runs locally
(psutil + NVML for metrics, SQLite for storage, Ollama for the LLM). Read-only: it never
changes or deletes anything.

## Status
- [x] Stage 1: metrics collector -> SQLite
- [x] Stage 2: LLM tools + chat via Ollama (`qwen3:8b`)
- [ ] Stage 3: anomaly detection, disk-fill forecast
- [ ] Stage 4: dashboard / reports

## Setup
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Usage
```powershell
pcassist collect --once          # one sample, prints summary
pcassist collect --interval 30   # keep collecting every 30 s
pcassist chat                    # chat with the local model (needs Ollama running)
pcassist chat --think            # enable model reasoning (slower)
```
