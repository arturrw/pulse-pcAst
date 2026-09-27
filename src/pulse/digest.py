"""The morning digest: one short notification that says whether the last day was quiet or what to look at.

It reads what the other checks already know (Windows health and Defender, autostart changes, flagged processes,
disks, unusual stretches, alerts sent) and never invents anything. Accepted risks (`pulse ack`) are counted
separately and do not make a day "not quiet". The full picture stays in the HTML report, which the digest refreshes.

Once a week (the first scheduled digest 6.5+ days after the last one) it also sends "this week against the last one":
temperatures, how fast the disks fill, new autostart entries, and hours of recording and of games for context."""
import time
from pathlib import Path

from . import alerts, anomaly, db, persistence, tools

MAX_BODY = 230        # a toast shows about this much; the rest is in data/digest.log and the report
WEEK = 7 * 86400
WEEK_EVERY = 6.5 * 86400    # a scheduled run that starts a little early (StartWhenAvailable) must not skip a week
WEEK_MIN_HOURS = 24         # recorded in EACH week before the comparison is sent by itself: less says nothing
DISK_EDGE = 86400           # a disk sample this close to a week's edge stands for it; farther, that week has no number


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


def _hours(conn, a: float, b: float) -> float:
    stamps = [t for (t,) in conn.execute("SELECT ts FROM system_metrics WHERE ts >= ? AND ts < ? ORDER BY ts", (a, b))]
    return anomaly.recorded_seconds(stamps) / 3600


def _temps(conn, table: str, column: str, a: float, b: float) -> tuple[float, float] | None:
    avg, peak = conn.execute(f"SELECT AVG({column}), MAX({column}) FROM {table} WHERE ts >= ? AND ts < ? AND {column} > 0",
                             (a, b)).fetchone()
    return None if avg is None else (avg, peak)


def _used_at(conn, mount: str, t: float) -> float | None:
    row = conn.execute("SELECT used_gb FROM disk_usage WHERE mount = ? AND ts <= ? AND ts >= ? ORDER BY ts DESC LIMIT 1",
                       (mount, t, t - DISK_EDGE)).fetchone()
    return row[0] if row else None


def _game_hours(a: float, b: float) -> float:
    total = 0.0
    for p in tools._recordings().values():
        if span := tools._recording_span(p):
            total += max(0.0, min(span[1], b) - max(span[0], a))
    return total / 3600


def _new_autostart(conn, a: float, b: float) -> list[str]:
    """Names first seen in [a, b): an entry that only changed its command (an update) or a per-user service that came
    back under a new suffix at logon is not new, and this app's own background jobs are left out. The first snapshot
    is the baseline, never new."""
    base = conn.execute("SELECT MIN(first_seen) FROM autoruns").fetchone()[0]
    programs = persistence._own_programs()
    first: dict[tuple, float] = {}
    for kind, name, command, seen in conn.execute("SELECT kind, name, command, first_seen FROM autoruns"):
        if persistence.is_own_job(kind, name, command, programs):
            continue
        key = (kind, persistence.entry_name(kind, name))
        first[key] = min(seen, first.get(key, seen))
    return [name for (_, name), seen in sorted(first.items(), key=lambda kv: kv[1])
            if base is not None and seen > base + 1 and a <= seen < b]


def _short_name(name: str) -> str:
    """The last part of a task path, at most 40 characters: \\SoftLanding\\S-1-5-21-...\\Task-{GUID} is unreadable in a toast."""
    leaf = name.rstrip("\\").rsplit("\\", 1)[-1]
    return leaf if len(leaf) <= 40 else leaf[:39] + "…"


def build_week(now: float | None = None) -> dict:
    """{'title', 'body', 'lines', 'this_hours', 'last_hours'}: the last 7 days against the 7 before them.
    A number is left out when either week has no data for it; nothing is guessed."""
    now = time.time() if now is None else now
    mid, start = now - WEEK, now - 2 * WEEK
    lines, short = [], []
    with db.connect(tools._db_path) as conn:
        this_h, last_h = _hours(conn, mid, now), _hours(conn, start, mid)
        for label, key, table, column in (("Processor", "CPU", "system_metrics", "cpu_temp_c"),
                                          ("Graphics card", "GPU", "gpu_metrics", "temp_c")):
            new, old = _temps(conn, table, column, mid, now), _temps(conn, table, column, start, mid)
            if new and old:
                lines.append(f"{label} temperature: average {new[0]:.0f} °C (last week {old[0]:.0f}), "
                             f"peak {new[1]:.0f} °C (last week {old[1]:.0f})")
                short.append(f"{key} {new[0]:.0f} °C ({old[0]:.0f})")
        mounts = [m for (m,) in conn.execute("SELECT DISTINCT mount FROM disk_usage WHERE ts >= ?", (start - DISK_EDGE,))]
        for mount in sorted(mounts):
            ends = [_used_at(conn, mount, t) for t in (start, mid, now)]
            if None in ends:
                continue
            disk = mount.rstrip("\\")
            grew_new, grew_old = ends[2] - ends[1], ends[1] - ends[0]
            lines.append(f"Disk {disk} {grew_new:+.1f} GB this week (last week {grew_old:+.1f} GB)")
            if abs(grew_new) >= 1 or abs(grew_old) >= 1:
                short.append(f"{disk} {grew_new:+.0f} GB ({grew_old:+.0f})")
        new_auto, old_auto = _new_autostart(conn, mid, now), _new_autostart(conn, start, mid)
    names = ", ".join(map(_short_name, new_auto[:3])) + (", ..." if len(new_auto) > 3 else "")
    lines.append(f"New autostart entries: {len(new_auto)} this week" + (f" ({names})" if new_auto else "")
                 + f", {len(old_auto)} last week")
    short.append(f"{len(new_auto)} new autostart ({len(old_auto)})")
    games_new, games_old = _game_hours(mid, now), _game_hours(start, mid)
    if games_new or games_old:
        lines.append(f"Game recordings: {games_new:.1f} h this week, {games_old:.1f} h last week (games heat the PC up)")
    lines.append(f"Recorded {this_h:.0f} h this week, {last_h:.0f} h last week")
    body = "; ".join(short) + ". Last week in brackets."
    if min(this_h, last_h) < WEEK_MIN_HOURS:
        body = f"Only {this_h:.0f} h recorded this week and {last_h:.0f} h last week, a weak comparison. " + body
    return {"title": "This week against the last one", "body": body[:MAX_BODY], "lines": lines,
            "this_hours": this_h, "last_hours": last_h}


def _last_week_sent(log: Path) -> float | None:
    try:
        rows = [ln for ln in log.read_text(encoding="utf-8").splitlines() if " [week] " in ln]
        return time.mktime(time.strptime(rows[-1][:16], "%Y-%m-%d %H:%M")) if rows else None
    except (OSError, ValueError):
        return None


def week_due(db_path, now: float | None = None) -> bool:
    """True when no weekly comparison went out in the last 6.5 days and both weeks have enough recording."""
    now = time.time() if now is None else now
    last = _last_week_sent(Path(db_path).parent / "digest.log")
    if last is not None and now - last < WEEK_EVERY:
        return False
    with db.connect(db_path) as conn:
        return min(_hours(conn, now - WEEK, now), _hours(conn, now - 2 * WEEK, now - WEEK)) >= WEEK_MIN_HOURS


def _log(db_path, now: float | None, status: str, title: str, body: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() if now is None else now))
    with open(Path(db_path).parent / "digest.log", "a", encoding="utf-8") as f:
        f.write(f"{stamp} [{status}] {title} - {body}\n")


def run_week(db_path, notify_fn=alerts.notify, dry_run: bool = False, now: float | None = None) -> dict:
    """Build the weekly comparison, show it and log it (as [week] in data/digest.log). Nothing is sent with dry_run."""
    tools.set_db(db_path)
    w = build_week(now)
    if not dry_run:
        w["shown"] = alerts.send(notify_fn, w["title"], w["body"], "overview")
        _log(db_path, now, "week", w["title"], w["body"])
    return w


def run(db_path, notify_fn=alerts.notify, dry_run: bool = False, refresh_report: bool = True, now: float | None = None,
        weekly_if_due: bool = False) -> dict:
    """Build the digest, show it, log it, and refresh the report. Nothing is sent or written with dry_run.
    With weekly_if_due (the scheduled job) the weekly comparison follows when it is due."""
    tools.set_db(db_path)
    d = build(now)
    if dry_run:
        return d
    d["shown"] = alerts.send(notify_fn, d["title"], d["body"], "findings" if d["todo"] else "overview")
    _log(db_path, now, d["status"], d["title"], d["body"])
    if weekly_if_due and week_due(db_path, now):
        d["week"] = run_week(db_path, notify_fn, now=now)
    if refresh_report:
        from . import report

        out = Path(db_path).parent / "reports" / "latest.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.build_report(24), encoding="utf-8")
        d["report"] = str(out)
    return d
