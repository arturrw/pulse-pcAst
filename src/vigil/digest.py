"""The morning digest: one short notification that says whether the last day was quiet or what to look at.

It reads what the other checks already know (Windows health and Defender, autostart changes, flagged processes,
disks, unusual stretches, alerts sent) and never invents anything. Accepted risks (`vigil ack`) are counted
separately and do not make a day "not quiet". The full picture stays in the HTML report, which the digest refreshes."""
import time
from pathlib import Path

from . import alerts, anomaly, db, tools

MAX_BODY = 230        # a toast shows about this much; the rest is in data/digest.log and the report


def _recorded_hours(now: float) -> float:
    with db.connect(tools._db_path) as conn:
        stamps = [t for (t,) in conn.execute("SELECT ts FROM system_metrics WHERE ts >= ? ORDER BY ts", (now - 86400,))]
    return anomaly.recorded_seconds(stamps) / 3600


def _alerts_sent(now: float) -> int:
    n = 0
    for line in alerts.recent_log(tools._db_path, 1000):
        try:
            if now - time.mktime(time.strptime(line[:16], "%Y-%m-%d %H:%M")) <= 86400:
                n += 1
        except ValueError:
            continue
    return n


def build(now: float | None = None) -> dict:
    """{'status': quiet | look, 'title', 'body', 'lines': [...]} for the last 24 hours."""
    now = time.time() if now is None else now
    todo, lines = [], []

    health = tools.system_health(24)
    if health.get("available"):
        open_ = [f for f in health["findings"] if not f["accepted"]]
        accepted = health["accepted_count"]
        lines.append(f"Windows and Defender: {len(open_)} open finding(s)" + (f", {accepted} accepted by you" if accepted else ""))
        todo += [f"{f['title']}" for f in open_]
    startup = tools.startup_changes(24)
    if startup.get("available"):
        new = [x for x in startup["new_or_changed"] if not x["accepted"] and x["severity"] != "low"]
        lines.append(f"Autostart: {len(new)} new or changed entr(ies) worth a look, {startup['new_or_changed_count']} new in total")
        todo += [f"new autostart entry {x['name']}" for x in new]
    watch = tools.process_watch(1440)
    if "error" not in watch:
        files = [f for f in watch["suspect_files"] if f["severity"] == "high"]
        net = watch.get("network", {})
        flagged = len(files) + len(net.get("from_suspicious_files", [])) + len(net.get("suspicious_ports", []))
        lines.append(f"Processes: {flagged} flagged (odd file location, signature or network use)")
        todo += [f"suspicious file {f['name']}" for f in files]
        todo += [f"{n['name']} on port {n['port']}" for n in net.get("suspicious_ports", [])]
    unusual = 0
    for metric in anomaly.STATE_METRICS:
        r = tools.anomalies(metric, 1440)
        unusual += sum(1 for e in r.get("events", []) if not e["during_game"] and e["most_unusual_value"] >= alerts.ALERT_MIN_LEVEL.get(metric, 1e9))
    lines.append(f"Unusual and high temperature / RAM / swap: {unusual}")
    if unusual:
        todo.append(f"{unusual} unusual high stretch(es) of temperature, RAM or swap")
    disks = [d for d in tools.disk_forecast(30) if "error" not in d]
    if disks:
        low = min(disks, key=lambda d: d["free_gb"])
        lines.append(f"Disks: least free is {low['disk']} with {low['free_gb']:.0f} GB")
        todo += [f"disk {d['disk']} is almost full ({d['free_gb']:.0f} GB free)" for d in disks
                 if d["free_gb"] < alerts.DISK_FREE_GB or 100 * d["free_gb"] / max(d["free_gb"] + d["used_gb"], 1e-9) < alerts.DISK_FREE_PERCENT]
        todo += [f"disk {d['disk']} may fill in {d['days_until_full']:.0f} days" for d in disks
                 if d.get("days_until_full") is not None and d["days_until_full"] < alerts.DISK_FORECAST_DAYS and d["confidence"] == "ok"]
    hours = _recorded_hours(now)
    lines.append(f"Recorded {hours:.0f} of the last 24 h; {_alerts_sent(now)} alert(s) sent")

    status = "look" if todo else "quiet"
    title = "Morning digest: nothing new to look at" if not todo else f"Morning digest: {len(todo)} thing(s) to look at"
    body = ("Nothing new needs attention. " + lines[0]) if not todo else "; ".join(todo[:4]) + ("; ..." if len(todo) > 4 else "")
    if hours < 12:
        body += f" (the PC recorded only {hours:.0f} h)"
    return {"status": status, "title": title, "body": body[:MAX_BODY], "lines": lines, "todo": todo}


def run(db_path, notify_fn=alerts.notify, dry_run: bool = False, refresh_report: bool = True, now: float | None = None) -> dict:
    """Build the digest, show it, log it, and refresh the report. Nothing is sent or written with dry_run."""
    tools.set_db(db_path)
    d = build(now)
    if dry_run:
        return d
    d["shown"] = notify_fn(d["title"], d["body"])
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() if now is None else now))
    with open(Path(db_path).parent / "digest.log", "a", encoding="utf-8") as f:
        f.write(f"{stamp} [{d['status']}] {d['title']} - {d['body']}\n")
    if refresh_report:
        from . import report

        out = Path(db_path).parent / "reports" / "latest.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.build_report(24), encoding="utf-8")
        d["report"] = str(out)
    return d
