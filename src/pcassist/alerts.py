"""Alerts: look at the collected history and tell the user when something needs attention.

One call = one check (Task Scheduler runs it every 15 minutes). Findings go to a Windows notification and to
data/alerts.log; the same finding is not repeated for a while (data/alerts_state.json). Read-only: it only reads
the history and shows a message, it never changes or stops anything.

Only things worth interrupting for are alerted: a stopped collector, a hot GPU, a nearly full disk, an unusual
stretch of temperature / RAM / swap, and the serious findings of process_watch (a disguised or tampered file,
a process eating the CPU). Weak evidence (a process name that is merely new) is left for the report."""
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import anomaly, db, tools

TEMP_ALERT_C = 85.0          # the RTX 3070 Ti starts throttling in the low 80s
TEMP_SAMPLES = 10            # the last ~5 min must all be this hot, one spike is not an alert
DISK_FREE_GB = 15.0
DISK_FREE_PERCENT = 5.0
DISK_FORECAST_DAYS = 14
STALE_MINUTES = 20           # no new sample for this long: the collector is not running
BUSY_ALERT_CPU = 40.0        # percent of the whole machine, averaged over the window
GROWTH_ALERT_MB_H = 1000.0
COOLDOWN_HOURS = {"high": 6.0, "medium": 24.0}
BS = chr(92)
# Windows PowerShell's own app id: a toast needs a registered one, and this needs no installation.
APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}" + BS + "WindowsPowerShell" + BS + "v1.0" + BS + "powershell.exe"
_TOAST = (
    "$t=[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
    "$x=$t::GetTemplateContent('ToastText02');$n=$x.GetElementsByTagName('text');"
    "$n.Item(0).AppendChild($x.CreateTextNode($env:PCA_TITLE))|Out-Null;"
    "$n.Item(1).AppendChild($x.CreateTextNode($env:PCA_BODY))|Out-Null;"
    "$t::CreateToastNotifier($env:PCA_APPID).Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


@dataclass
class Alert:
    key: str          # what makes it "the same alert" for the cooldown
    severity: str     # high | medium
    title: str
    body: str


def check_collector(conn, now: float) -> list[Alert]:
    last = conn.execute("SELECT MAX(ts) FROM system_metrics").fetchone()[0]
    if last is None or now - last < STALE_MINUTES * 60:
        return []
    mins = (now - last) / 60
    return [Alert("collector-stale", "medium", "Metrics collector is not recording",
                  f"No new sample for {mins:.0f} min. If the PC did not just wake up, check "
                  "scripts/autostart.ps1 status.")]


def check_gpu_temp(conn, now: float) -> list[Alert]:
    rows = conn.execute("SELECT ts, temp_c FROM gpu_metrics WHERE temp_c IS NOT NULL ORDER BY ts DESC LIMIT ?",
                        (TEMP_SAMPLES,)).fetchall()
    if len(rows) < TEMP_SAMPLES or now - rows[0][0] > 600 or min(t for _, t in rows) < TEMP_ALERT_C:
        return []
    return [Alert("gpu-hot", "high", "GPU is very hot",
                  f"The GPU has been at {min(t for _, t in rows):.0f}-{max(t for _, t in rows):.0f} C for the last "
                  f"{(rows[0][0] - rows[-1][0]) / 60:.0f} min. Check fans, dust and airflow.")]


def check_disks(now: float) -> list[Alert]:
    out = []
    for r in tools.disk_forecast(30):
        if "error" in r:
            return []
        name, free = r["disk"], r["free_gb"]
        total = free + r["used_gb"]
        if free < DISK_FREE_GB or 100 * free / max(total, 1e-9) < DISK_FREE_PERCENT:
            out.append(Alert(f"disk-low-{name}", "high", f"Disk {name} is almost full",
                             f"{free:.0f} GB free of {total:.0f} GB."))
        elif r["confidence"] == "ok" and r.get("days_until_full") is not None and r["days_until_full"] < DISK_FORECAST_DAYS:
            out.append(Alert(f"disk-forecast-{name}", "medium", f"Disk {name} may fill up soon",
                             f"At {r['growth_gb_per_day']:.1f} GB/day it is full in about {r['days_until_full']:.0f} days "
                             f"({free:.0f} GB free)."))
    return out


# ram_used_mb is the same event as ram_percent in other units: alerting on both would show two notifications for one thing
ALERT_METRICS = tuple(m for m in anomaly.STATE_METRICS if m != "ram_used_mb")


def check_unusual(now: float) -> list[Alert]:
    out = []
    for metric in ALERT_METRICS:
        r = tools.anomalies(metric, 120)
        for e in r.get("events", []):
            if e["during_game"] or e["minutes_ago"] > 90:
                continue   # a game explains high RAM/swap; older events were already reported
            out.append(Alert(f"unusual-{metric}-{e['started']}", "medium", f"Unusual {metric}",
                             f"Since {e['started']} for {e['duration_minutes']:.0f} min: typically "
                             f"{e['typical_value']:.0f}, up to {e['most_unusual_value']:.0f}."))
    return out


def check_processes(now: float) -> list[Alert]:
    r = tools.process_watch(180)
    if "error" in r:
        return []
    out = []
    for f in r["suspect_files"]:
        if f["severity"] == "high":
            out.append(Alert(f"file-{f['exe']}", "high", f"Suspicious file: {f['name']}",
                             f"{'; '.join(f['reasons'])}. Path: {f['exe']}. Odd is not proof: check it before "
                             "trusting or deleting it (Task Manager > Open file location, a Defender scan)."))
    net = r.get("network", {})
    for n in net.get("from_suspicious_files", []):
        out.append(Alert(f"net-file-{n['name']}", "high", f"{n['name']} (suspicious file) is using the network",
                         f"It connected to {', '.join(n['destinations'])}{' and more' if n['count'] > 3 else ''}. "
                         "Its file has a suspicious location or signature (see the report)."))
    for n in net.get("suspicious_ports", []):
        out.append(Alert(f"net-port-{n['name']}-{n['port']}", "medium", f"{n['name']} connects to port {n['port']}",
                         f"Port {n['port']} is typical of {n['typical_of']}: {', '.join(n['destinations'])}. "
                         "Often harmless, worth a look if you do not know why."))
    for n in net.get("new_listeners", []):
        out.append(Alert(f"net-listen-{n['name']}", "medium", f"{n['name']} started listening for connections",
                         f"It accepts incoming connections on {n['bind']} (ports {', '.join(map(str, n['ports']))}); "
                         "it never did before."))
    for b in r["busy"]:
        if b["avg_cpu_percent"] >= BUSY_ALERT_CPU:
            usual = ("" if b["usual_cpu_percent_p95"] is None else f" (usually up to {b['usual_cpu_percent_p95']:.0f}%)")
            out.append(Alert(f"busy-{b['name']}", "medium", f"{b['name']} is using a lot of CPU",
                             f"{b['avg_cpu_percent']:.0f}% of the machine on average{usual}; {b['why']}."))
    for g in r["growing"]:
        if g["mb_per_hour"] >= GROWTH_ALERT_MB_H:
            out.append(Alert(f"growing-{g['name']}", "medium", f"{g['name']} keeps growing in memory",
                             f"{g['from_mb']:.0f} -> {g['to_mb']:.0f} MB over {g['hours']:.1f} h."))
    return out


def collect_alerts(now: float | None = None) -> list[Alert]:
    """All current findings (before the cooldown). A failing check is skipped, it must not hide the others."""
    now = time.time() if now is None else now
    found: list[Alert] = []
    with db.connect(tools._db_path) as conn:
        for check in (lambda: check_collector(conn, now), lambda: check_gpu_temp(conn, now),
                      lambda: check_disks(now), lambda: check_unusual(now), lambda: check_processes(now)):
            try:
                found += check()
            except Exception as e:   # noqa: BLE001 - one broken check must not silence the rest
                found.append(Alert(f"check-failed-{type(e).__name__}", "medium", "An alert check failed",
                                   f"{type(e).__name__}: {e}"))
    return found


def select_new(alerts: list[Alert], state: dict[str, float], now: float) -> list[Alert]:
    """Drop what was already sent within its cooldown."""
    return [a for a in alerts if now - state.get(a.key, 0.0) >= COOLDOWN_HOURS[a.severity] * 3600]


def notify(title: str, body: str) -> bool:
    """A Windows toast notification. Returns False when it could not be shown (the log still has the alert)."""
    env = {**os.environ, "PCA_TITLE": title, "PCA_BODY": body, "PCA_APPID": APP_ID}
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST], env=env,
                             capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _load_state(path: Path) -> dict[str, float]:
    try:
        return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def run_once(db_path, now: float | None = None, notify_fn=notify, dry_run: bool = False) -> list[Alert]:
    """Check, skip repeats, notify, log. With dry_run nothing is shown or remembered. Returns what was sent."""
    now = time.time() if now is None else now
    tools.set_db(db_path)
    data = Path(db_path).parent
    state_path, log_path = data / "alerts_state.json", data / "alerts.log"
    state = _load_state(state_path)
    new = select_new(collect_alerts(now), state, now)
    if dry_run:
        return new
    lines = []
    for a in new:
        shown = notify_fn(a.title, a.body)
        state[a.key] = now
        lines.append(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(now))} [{a.severity}] {a.title} - {a.body}"
                     + ("" if shown else "  (notification could not be shown)"))
    if lines:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    state = {k: v for k, v in state.items() if now - v < 7 * 86400}   # forget old keys
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return new


def recent_log(db_path, limit: int = 8) -> list[str]:
    try:
        return (Path(db_path).parent / "alerts.log").read_text(encoding="utf-8").strip().splitlines()[-limit:][::-1]
    except OSError:
        return []
