"""Behavioral check of the process history: which processes act unusually against their own past.

This is NOT an antivirus and cannot say whether anything is malicious. The collector stores only pid, name, CPU
and memory of the heaviest processes (top by CPU plus top by memory, ~26 per sample): no file path, signature,
parent, command line or per-process network. So it can only flag things that are heavy enough to be recorded:
  - a name that appears in the history for the first time,
  - a process using far more CPU than it usually does (or a lot, with no history to compare to),
  - a process whose memory keeps growing.
The flags are candidates for a human look, nothing more."""
from collections import defaultdict

from . import anomaly

MIN_BASELINE_HOURS = 24     # less recorded history than this and "never seen before" is mostly just "not seen yet"
BUSY_MIN_SAMPLES = 20       # ~10 min of samples at the default 30 s
BUSY_ABS_CPU = 10.0         # percent of the WHOLE machine (cpu_percent is divided by the core count)
BUSY_FACTOR = 3.0           # times its own usual (95th percentile) level
BASELINE_MIN_SAMPLES = 50   # samples of a name before its "usual" level means anything
GROWTH_MIN_MB = 500.0
GROWTH_MIN_SAMPLES = 60     # ~30 min
GROWTH_MIN_MB_PER_HOUR = 300.0
NEW_MIN_SAMPLES = 20        # a new name is worth listing only if it stayed ~10 min in the snapshots ...
NEW_MIN_CPU = 5.0           # ... or at least once used this much CPU (one-shot helpers and updaters are just counted)
LIMIT = 6                   # findings per category

HOW = ("behavioral flags from pid, name, CPU and memory of the heaviest processes only; no file path, signature "
       "or network data, so this cannot tell malware from a normal program and a quiet process is invisible")


def _p95(values: list[float]) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * 0.95))]


def _slope_mb_per_hour(points: list[tuple[float, float]]) -> float:
    n = len(points)
    mt, mv = sum(t for t, _ in points) / n, sum(v for _, v in points) / n
    var = sum((t - mt) ** 2 for t, _ in points)
    return sum((t - mt) * (v - mv) for t, v in points) / var * 3600 if var else 0.0


def analyze(conn, since: float, now: float) -> dict:
    """Findings for the window [since, now], compared with everything recorded before `since`."""
    win = conn.execute("SELECT ts, pid, name, cpu_percent, rss_mb FROM process_snapshots "
                       "WHERE ts >= ? AND pid != 0 ORDER BY ts", (since,)).fetchall()
    base_ts = [t for (t,) in conn.execute("SELECT DISTINCT ts FROM process_snapshots WHERE ts < ? ORDER BY ts", (since,))]
    baseline_hours = anomaly.recorded_seconds(base_ts) / 3600
    base_names = {n for (n,) in conn.execute("SELECT DISTINCT name FROM process_snapshots WHERE ts < ?", (since,))}

    by_name: dict[str, list] = defaultdict(list)
    by_pid: dict[tuple, list] = defaultdict(list)
    for ts, pid, name, cpu, rss in win:
        by_name[name].append((ts, cpu, rss))
        by_pid[(name, pid)].append((ts, rss))

    new, busy, growing = [], [], []
    for name, rows in by_name.items():
        cpus = [c for _, c, _ in rows]
        avg = sum(cpus) / len(cpus)
        if name not in base_names:
            new.append({"name": name, "first_seen_ts": rows[0][0], "samples": len(rows),
                        "avg_cpu_percent": avg, "max_cpu_percent": max(cpus), "max_rss_mb": max(r for _, _, r in rows)})
        if len(rows) < BUSY_MIN_SAMPLES or avg < BUSY_ABS_CPU:
            continue
        past = [c for (c,) in conn.execute("SELECT cpu_percent FROM process_snapshots WHERE name = ? AND ts < ?",
                                           (name, since))]
        if len(past) >= BASELINE_MIN_SAMPLES:
            usual = _p95(past)
            if avg >= BUSY_FACTOR * max(usual, 1.0):
                busy.append({"name": name, "avg_cpu_percent": avg, "usual_cpu_percent_p95": usual,
                             "samples": len(rows), "why": "far above its own usual level"})
        elif name not in base_names:
            busy.append({"name": name, "avg_cpu_percent": avg, "usual_cpu_percent_p95": None, "samples": len(rows),
                         "why": "heavy and never recorded before, nothing to compare with"})
    for (name, pid), rows in by_pid.items():
        if len(rows) < GROWTH_MIN_SAMPLES:
            continue
        growth = rows[-1][1] - rows[0][1]
        rate = _slope_mb_per_hour(rows)
        if growth >= GROWTH_MIN_MB and rate >= GROWTH_MIN_MB_PER_HOUR:
            growing.append({"name": name, "pid": pid, "from_mb": rows[0][1], "to_mb": rows[-1][1],
                            "hours": (rows[-1][0] - rows[0][0]) / 3600, "mb_per_hour": rate})
    minor = [r for r in new if r["samples"] < NEW_MIN_SAMPLES and r["max_cpu_percent"] < NEW_MIN_CPU]
    new = [r for r in new if r not in minor]
    new.sort(key=lambda r: -r["avg_cpu_percent"])
    busy.sort(key=lambda r: -r["avg_cpu_percent"])
    growing.sort(key=lambda r: -r["mb_per_hour"])
    return {"baseline_hours": baseline_hours,
            "confidence": "ok" if baseline_hours >= MIN_BASELINE_HOURS else "low",
            "new": new[:LIMIT], "new_total": len(new), "new_minor_count": len(minor), "busy": busy[:LIMIT], "growing": growing[:LIMIT],
            "how_it_works": HOW}
