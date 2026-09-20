"""The background jobs (collector, alert check, morning digest) and the local model: their state, and installing or
removing the jobs. Used by the web app's Setup page; the same actions exist on the command line (scripts/autostart.ps1).

Only three fixed job names and two fixed actions are accepted, and only our own script is run: nothing the user (or a
web page) sends is ever put into a command line."""
import json
import subprocess
from pathlib import Path

TASKS = ("collect", "alerts", "digest")
ACTIONS = ("install", "remove")
DESCRIPTIONS = {"collect": "records metrics, processes and connections every 30 s",
                "alerts": "checks for problems every 15 min and shows a notification",
                "digest": "one summary notification every morning at 09:00"}
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "autostart.ps1"


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def tasks_status(runner=_run) -> dict[str, str | None]:
    """{'collect': 'Running' | 'Ready' | 'Disabled' | None (not installed), ...}"""
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "ConvertTo-Json -InputObject @(Get-ScheduledTask | Where-Object { $_.TaskName -like 'pcassist-*' } | "
              "ForEach-Object { [pscustomobject]@{ n = $_.TaskName; s = $_.State.ToString() } }) -Compress")
    try:
        code, out = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
        rows = json.loads(out) if out.strip() else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return {t: None for t in TASKS}
    rows = rows if isinstance(rows, list) else [rows]
    state = {r["n"]: r["s"] for r in rows if isinstance(r, dict) and "n" in r}
    return {t: state.get(f"pcassist-{t}") for t in TASKS}


def change_task(task: str, action: str, runner=_run) -> tuple[bool, str]:
    """Install or remove one background job with our own script. Anything else is refused."""
    if task not in TASKS or action not in ACTIONS:
        return False, "unknown job or action"
    if not SCRIPT.exists():
        return False, "scripts/autostart.ps1 was not found (this needs a source checkout of the project)"
    try:
        code, out = runner(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-File", str(SCRIPT),
                            action, "-Task", task], 120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run PowerShell: {e}"
    return code == 0, out.strip()[-400:]


def ollama_status(model: str, client_factory=None) -> dict:
    """Is the local model server up, and is the chosen model pulled? Never raises."""
    try:
        if client_factory is None:
            import ollama

            client_factory = ollama.Client
        client = client_factory()
        listing = client.list()
        models = listing.get("models", []) if isinstance(listing, dict) else getattr(listing, "models", [])
        names = [(m.get("model") or m.get("name") or "") if isinstance(m, dict) else (getattr(m, "model", None) or "") for m in models]
    except Exception as e:   # noqa: BLE001 - "Ollama is not running" comes in many exception types
        return {"running": False, "model": model, "model_ready": False, "installed": [],
                "hint": "Ollama is not running. Start the Ollama app (or run `ollama serve`)."}
    ready = any(n == model or n.split(":")[0] == model.split(":")[0] and ":" not in model for n in names)
    return {"running": True, "model": model, "model_ready": ready, "installed": sorted(n for n in names if n),
            "hint": "" if ready else f"The model is not installed. Run: ollama pull {model}"}
