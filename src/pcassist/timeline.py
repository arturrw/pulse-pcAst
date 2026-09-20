"""What was going on at a given moment: everything the assistant knows about a time window, on one timeline.

Sources, all local and read-only: the metric history (CPU, RAM, GPU), the process snapshots (what was heaviest, what
appeared for the first time), the network table (new destinations), new autostart entries, the alert log, game
recordings, gaps where the collector was not recording, and the Windows event logs (crashes, shutdowns, Defender).
It only lines things up in time: it does not say what caused what."""
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import anomaly, winhealth

MAX_ENTRIES = 40
MAX_HALF_WINDOW_MIN = 240
_UNITS = {"m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1, "мин": 1, "минуту": 1, "минуты": 1, "минут": 1,
          "h": 60, "hour": 60, "hours": 60, "ч": 60, "час": 60, "часа": 60, "часов": 60}
METRICS = (("cpu_percent", "system_metrics", "cpu_percent", "%"), ("ram_percent", "system_metrics", "ram_percent", "%"),
           ("gpu_temp_c", "gpu_metrics", "temp_c", " C"), ("gpu_util_percent", "gpu_metrics", "util_percent", "%"))


def parse_when(text: str, now: datetime) -> datetime | None:
    """'now', '14:03', 'yesterday 21:30', '2026-09-20 14:03', '45 minutes ago', '2 часа назад' -> a local datetime."""
    t = (text or "").strip().lower()
    if t in ("", "now", "сейчас"):
        return now
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([a-zа-я]+)\s*(?:ago|назад)", t)
    if m and m.group(2) in _UNITS:
        return now - timedelta(minutes=float(m.group(1).replace(",", ".")) * _UNITS[m.group(2)])
    day_shift = 0
    m = re.match(r"(yesterday|вчера)\s*(?:at|в)?\s*(.*)$", t)
    if m:
        day_shift, t = 1, m.group(2).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        moment = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0) - timedelta(days=day_shift)
        return moment - timedelta(days=1) if (moment > now and not day_shift) else moment   # a time in the future means yesterday
    return None


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _in(dt: datetime | None, lo: datetime, hi: datetime) -> bool:
    return dt is not None and lo <= dt <= hi


def events_in_window(raw: dict, lo: datetime, hi: datetime) -> list[tuple[datetime, str, str]]:
    """Windows event log records that fall inside the window, as (time, type, text)."""
    out = []
    listed = winhealth._list
    for r in listed(raw.get("power")):
        t = winhealth._time(r.get("t"))
        if _in(t, lo, hi):
            out.append((t, "windows", {41: "Unexpected shutdown (kernel power)", 6008: "Unexpected shutdown", 1001: "Blue screen (bugcheck)"}
                        .get(r.get("id"), "Power event")))
    for key, label in (("hardware", "Hardware error (WHEA)"), ("disk", "Disk / file system error"), ("display", "Graphics driver problem")):
        for r in listed(raw.get(key)):
            t = winhealth._time(r.get("t"))
            if _in(t, lo, hi):
                out.append((t, "windows", f"{label}: {r.get('p')}"))
    for r in listed(raw.get("crashes")):
        t = winhealth._time(r.get("t"))
        if _in(t, lo, hi):
            out.append((t, "windows", f"{'Application crash' if r.get('id') == 1000 else 'Application hang'}: {r.get('a0')}"))
    names = {1116: "Defender detected a threat", 1117: "Defender took action on a threat", 5001: "Defender real-time protection turned OFF",
             5000: "Defender real-time protection turned on"}
    for r in listed(raw.get("defender_events")):
        t = winhealth._time(r.get("t"))
        if _in(t, lo, hi) and r.get("id") in names:
            out.append((t, "defender", names[r["id"]]))
    for r in listed(raw.get("threats")):
        t = winhealth._time(r.get("t"))
        if _in(t, lo, hi):
            out.append((t, "defender", f"Defender detection: {r.get('name')}"))
    return out


def build(conn, center: datetime, half_minutes: int, now: datetime, alerts_log: list[str] | None = None,
          recordings: list[tuple[float, float, str]] | None = None, raw_events: dict | None = None) -> dict:
    half = max(1, min(int(half_minutes), MAX_HALF_WINDOW_MIN))
    lo_dt, hi_dt = center - timedelta(minutes=half), min(center + timedelta(minutes=half), now)
    lo, hi = lo_dt.timestamp(), hi_dt.timestamp()
    entries: list[tuple[float, str, str]] = []

    def add(ts: float, kind: str, text: str) -> None:
        entries.append((ts, kind, text))

    have = conn.execute("SELECT COUNT(*) FROM system_metrics WHERE ts BETWEEN ? AND ?", (lo, hi)).fetchone()[0]
    stamps = [t for (t,) in conn.execute("SELECT ts FROM system_metrics WHERE ts BETWEEN ? AND ? ORDER BY ts", (lo, hi))]
    notes = []
    if not have:
        notes.append("no metrics were recorded in this window (the PC was off or the collector was not running)")

    metrics = {}
    for name, table, col, unit in METRICS:
        row = conn.execute(f"SELECT AVG({col}), MAX({col}), MIN({col}) FROM {table} WHERE ts BETWEEN ? AND ? AND {col} IS NOT NULL",
                           (lo, hi)).fetchone()
        before = conn.execute(f"SELECT AVG({col}) FROM {table} WHERE ts BETWEEN ? AND ? AND {col} IS NOT NULL",
                              (lo - 3 * 3600, lo)).fetchone()[0]
        if row[0] is None:
            continue
        metrics[name] = {"window_avg": row[0], "window_max": row[1], "window_min": row[2], "before_avg_3h": before}
        if before is not None and abs(row[0] - before) >= (8 if unit == " C" else 15):
            add(lo, "metric", f"{name} averaged {row[0]:.0f}{unit} here versus {before:.0f}{unit} in the 3 hours before")

    top = [(n, c, m) for n, c, m in conn.execute(
        "SELECT name, AVG(cpu_percent), MAX(rss_mb) FROM process_snapshots WHERE ts BETWEEN ? AND ? AND pid != 0 "
        "GROUP BY name ORDER BY AVG(cpu_percent) DESC LIMIT 5", (lo, hi))]
    for name, first in conn.execute("SELECT name, MIN(ts) FROM process_snapshots WHERE pid != 0 GROUP BY name HAVING MIN(ts) BETWEEN ? AND ?", (lo, hi)):
        if conn.execute("SELECT 1 FROM process_snapshots WHERE ts < ? LIMIT 1", (lo,)).fetchone():   # only if history precedes the window
            add(first, "process", f"{name} appears in the history for the first time")
    for name, kind_, addr, port, first in conn.execute(
            "SELECT name, kind, addr, port, first_seen FROM process_connections WHERE first_seen BETWEEN ? AND ? ORDER BY first_seen LIMIT 12", (lo, hi)):
        add(first, "network", f"{name} {'connects to' if kind_ == 'out' else 'listens on'} {addr}:{port} for the first time")
    base = conn.execute("SELECT MIN(first_seen) FROM autoruns").fetchone()[0]
    for kind, name, command, first in conn.execute(
            "SELECT kind, name, command, first_seen FROM autoruns WHERE first_seen BETWEEN ? AND ?", (lo, hi)):
        if base is not None and first > base + 1:
            add(first, "autostart", f"new autostart entry ({kind.replace('_', ' ')}): {name} -> {command[:80]}")
    # a gap may start before the window or end after it: look at the nearest samples outside it as well
    prev = conn.execute("SELECT MAX(ts) FROM system_metrics WHERE ts < ?", (lo,)).fetchone()[0]
    nxt = conn.execute("SELECT MIN(ts) FROM system_metrics WHERE ts > ?", (hi,)).fetchone()[0]
    around = ([prev] if prev else []) + stamps + ([nxt] if nxt else [])
    limit = anomaly.gap_limit(stamps)
    for a, b in zip(around, around[1:]):
        if b - a > limit and b >= lo and a <= hi:
            add(max(a, lo), "gap", f"no data from {_clock(a)} to {_clock(b)} (PC off or the collector was not running)")
    for line in alerts_log or []:
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d) \[(\w+)\] (.*)", line)
        if m:
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            if lo_dt <= t <= hi_dt:
                add(t.timestamp(), "alert", f"alert sent [{m.group(2)}]: {m.group(3)[:110]}")
    for g0, g1, name in recordings or []:
        if g1 >= lo and g0 <= hi:
            add(max(g0, lo), "game", f"a game recording overlaps: {name}")
    for t, kind, text in events_in_window(raw_events or {}, lo_dt, hi_dt):
        add(t.timestamp(), kind, text)

    entries.sort()
    timeline = [{"time": _clock(ts), "type": kind, "text": text} for ts, kind, text in entries[:MAX_ENTRIES]]
    return {"window": {"from": lo_dt.strftime("%Y-%m-%d %H:%M"), "to": hi_dt.strftime("%Y-%m-%d %H:%M")},
            "samples_in_window": have, "metrics": metrics,
            "heaviest_processes": [{"name": n, "avg_cpu_percent": c, "max_rss_mb": m} for n, c, m in top],
            "timeline": timeline, "timeline_truncated": len(entries) > MAX_ENTRIES, "notes": notes,
            "summary": f"{len(entries)} thing(s) on the timeline between {lo_dt:%H:%M} and {hi_dt:%H:%M}"
                       + ("; no metrics were recorded then" if not have else "")}
