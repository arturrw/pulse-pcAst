"""Read-only tools the LLM can call. Docstrings double as the tool descriptions."""
import time
from pathlib import Path

from . import db, scan
from .collectors import Collector, collect_disks

_db_path = db.DEFAULT_DB

# metric name -> (table, column). Whitelist: names come from the model, never interpolate raw input.
METRICS = {
    "cpu_percent": ("system_metrics", "cpu_percent"),
    "ram_percent": ("system_metrics", "ram_percent"),
    "ram_used_mb": ("system_metrics", "ram_used_mb"),
    "swap_percent": ("system_metrics", "swap_percent"),
    "disk_read_mbps": ("system_metrics", "disk_read_mbps"),
    "disk_write_mbps": ("system_metrics", "disk_write_mbps"),
    "net_sent_kbps": ("system_metrics", "net_sent_kbps"),
    "net_recv_kbps": ("system_metrics", "net_recv_kbps"),
    "gpu_util_percent": ("gpu_metrics", "util_percent"),
    "gpu_mem_used_mb": ("gpu_metrics", "mem_used_mb"),
    "gpu_temp_c": ("gpu_metrics", "temp_c"),
    "gpu_power_w": ("gpu_metrics", "power_w"),
}


def set_db(path) -> None:
    global _db_path
    _db_path = path


MIN_GAP_S = 600  # a silence longer than this (and than 5x the usual sample step) means the collector was not running


def _recorded_seconds(timestamps: list[float]) -> float:
    """Time actually covered by samples: long gaps (PC off/asleep) are not counted, unlike last - first.
    A gap is anything over 5x the median step, so a collector run with a long --interval still counts."""
    steps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    if not steps:
        return 0.0
    limit = max(MIN_GAP_S, 5 * sorted(steps)[len(steps) // 2])
    return sum(x for x in steps if x <= limit)


def _round(d: dict) -> dict:
    return {k: round(v, 1) if isinstance(v, float) else v for k, v in d.items()}


def current_status() -> dict:
    """Take a live snapshot of the computer right now: CPU, RAM, disk I/O activity
    (read/write MB/s - this is disk "load", not free space) and network rates,
    GPU load/VRAM/temperature/power, and the top processes by CPU and memory."""
    s = Collector().sample()
    procs = s["processes"]
    return {
        "system": _round({k: v for k, v in s["system"].items() if k != "ts"}),
        "gpus": [_round({k: v for k, v in g.items() if k not in ("ts", "idx")}) for g in s["gpus"]],
        "top_by_cpu": [_round({"name": p["name"], "pid": p["pid"], "cpu_percent": p["cpu_percent"], "rss_mb": p["rss_mb"]})
                       for p in sorted(procs, key=lambda p: p["cpu_percent"], reverse=True)[:8]],
        "top_by_memory": [_round({"name": p["name"], "pid": p["pid"], "cpu_percent": p["cpu_percent"], "rss_mb": p["rss_mb"]})
                          for p in sorted(procs, key=lambda p: p["rss_mb"], reverse=True)[:8]],
    }


def disk_usage() -> list[dict]:
    """Report free/used/total space for every disk/partition, in GB (storage capacity,
    not disk I/O activity - use current_status for read/write load)."""
    return [_round({"disk": d["mount"].rstrip("\\"), "total_gb": d["total_gb"], "used_gb": d["used_gb"],
                    "free_gb": d["total_gb"] - d["used_gb"],
                    "used_percent": 100 * d["used_gb"] / d["total_gb"] if d["total_gb"] else 0})
            for d in collect_disks(time.time())]


def top_processes(sort_by: str = "cpu", minutes: int = 10, limit: int = 10) -> list[dict]:
    """List the heaviest processes over a recent time window, from collected history.

    Args:
        sort_by: 'cpu' (average CPU percent) or 'memory' (peak RAM in MB).
        minutes: How many minutes of history to look at.
        limit: Maximum number of processes to return.
    """
    order = "AVG(cpu_percent)" if sort_by == "cpu" else "MAX(rss_mb)"
    since = time.time() - int(minutes) * 60
    with db.connect(_db_path) as conn:
        rows = conn.execute(
            f"""SELECT name, AVG(cpu_percent), MAX(rss_mb), COUNT(*)
                FROM process_snapshots WHERE ts >= ? AND pid != 0 GROUP BY name
                ORDER BY {order} DESC LIMIT ?""",
            (since, int(limit)),
        ).fetchall()
    return [_round({"name": n, "avg_cpu_percent": c, "max_rss_mb": m, "samples": k}) for n, c, m, k in rows]


def metrics_history(metric: str, minutes: int = 60) -> dict:
    """Summarize one metric over a recent time window from collected history (min, avg, max, latest).

    Args:
        metric: One of cpu_percent, ram_percent, ram_used_mb, swap_percent, disk_read_mbps,
            disk_write_mbps, net_sent_kbps, net_recv_kbps, gpu_util_percent, gpu_mem_used_mb,
            gpu_temp_c, gpu_power_w.
        minutes: How many minutes of history to look at.
    """
    if metric not in METRICS:
        return {"error": f"unknown metric '{metric}'", "available": sorted(METRICS)}
    table, col = METRICS[metric]
    since = time.time() - int(minutes) * 60
    with db.connect(_db_path) as conn:
        n, lo, avg, hi, last_ts = conn.execute(
            f"SELECT COUNT({col}), MIN({col}), AVG({col}), MAX({col}), MAX(ts) FROM {table} WHERE ts >= ?",
            (since,),
        ).fetchone()
        last = conn.execute(f"SELECT {col} FROM {table} ORDER BY ts DESC LIMIT 1").fetchone()
        if n:
            stamps = [t for (t,) in conn.execute(f"SELECT ts FROM {table} WHERE ts >= ? ORDER BY ts", (since,))]
            peak_ts = conn.execute(
                f"SELECT ts FROM {table} WHERE ts >= ? AND {col} = ? ORDER BY ts DESC LIMIT 1", (since, hi)
            ).fetchone()[0]
            recent = conn.execute(
                f"SELECT ts, {col} FROM {table} WHERE ts >= ? ORDER BY ts LIMIT 1", (max(since, last_ts - 600),)
            ).fetchone()
    if not n:
        return {"metric": metric, "error": "no collected data in this window; run `pcassist collect`"}
    covers = _recorded_seconds(stamps) / 60   # recorded time, not the span: the PC may have been off in between
    result = {"metric": metric, "minutes": int(minutes), "samples": n,
              "data_covers_minutes": covers,
              "newest_sample_minutes_ago": (time.time() - last_ts) / 60,
              "min": lo, "avg": avg, "max": hi, "max_was_minutes_ago": (time.time() - peak_ts) / 60,
              "latest": last[0]}
    if recent[0] < last_ts:
        result["change_over_last_10_min"] = last[0] - recent[1]
    if covers < int(minutes) * 0.5:
        result["warning"] = (f"collected data covers only ~{covers:.0f} min of the requested {int(minutes)} min; "
                             "tell the user the summary is for that shorter period only")
    return _round(result)


MIN_FORECAST_HOURS = 24  # below this a linear trend is mostly noise (temp files, caches)


def _slope_per_day(points: list[tuple[float, float]]) -> float:
    """Least-squares slope of (ts_seconds, used_gb) in GB/day."""
    n = len(points)
    mt = sum(t for t, _ in points) / n
    mu = sum(u for _, u in points) / n
    var = sum((t - mt) ** 2 for t, _ in points)
    if var == 0:
        return 0.0
    return sum((t - mt) * (u - mu) for t, u in points) / var * 86400


def disk_forecast(days: int = 30) -> list[dict]:
    """Forecast when each disk will fill up, from the trend in collected history
    (growth in GB/day and days until full). Check `confidence`: with under 24 hours of
    history the estimate is unreliable, say so.

    Args:
        days: How many days of history to fit the trend on.
    """
    since = time.time() - int(days) * 86400
    out = []
    with db.connect(_db_path) as conn:
        mounts = [m for (m,) in conn.execute("SELECT DISTINCT mount FROM disk_usage WHERE ts >= ?", (since,))]
        for mount in sorted(mounts):
            pts = conn.execute("SELECT ts, used_gb, total_gb FROM disk_usage WHERE mount = ? AND ts >= ? ORDER BY ts",
                               (mount, since)).fetchall()
            stamps = [t for t, _, _ in pts]
            hours = _recorded_seconds(stamps) / 3600       # time actually recorded, gaps (PC off) not counted
            span = (stamps[-1] - stamps[0]) / 3600
            used, total = pts[-1][1], pts[-1][2]
            row = {"disk": mount.rstrip("\\"), "used_gb": used, "free_gb": total - used,
                   "history_hours": hours, "span_hours": span, "confidence": "ok" if hours >= MIN_FORECAST_HOURS else "low"}
            if len(pts) < 2 or span <= 0:
                row["note"] = "not enough samples for a trend"
            else:
                rate = _slope_per_day([(t, u) for t, u, _ in pts])
                row["growth_gb_per_day"] = rate
                if rate > 0.01:
                    row["days_until_full"] = (total - used) / rate
                else:
                    row["note"] = "usage is not growing, no fill-up expected"
            if row["confidence"] == "low":
                row["warning"] = (f"only {hours:.1f} h of recorded history"
                                  + (f" over a {span:.0f} h span (the collector was off in between)" if span > 1.5 * hours + 1 else "")
                                  + f" (need {MIN_FORECAST_HOURS}+ h); tell the user this estimate is unreliable")
            out.append(_round(row))
    if not out:
        return [{"error": "no collected data in this window; run `pcassist collect`"}]
    return out


def largest_folders(path: str = "C:\\", limit: int = 10) -> dict:
    """Find which subfolders of a directory take the most disk space (read-only scan, up to ~45 s).
    Use it to answer 'what is filling my disk?'. Drill down by calling it again on a big subfolder.

    Args:
        path: Directory to scan, e.g. 'C:\\' or 'C:\\Users'.
        limit: How many of the largest subfolders to return.
    """
    r = scan.largest_children(path, int(limit))
    if "loose_files_gb" in r:
        # Unambiguous name: the model otherwise reads "loose files" as a headline number.
        r["files_directly_in_this_folder_gb"] = r.pop("loose_files_gb")
    return r


def _recordings() -> dict[str, Path]:
    """PresentMon recordings (name = file name without .csv) in data/sessions and data/bench, next to the metrics DB."""
    data = Path(_db_path).parent
    found = {}
    for sub in ("sessions", "bench"):
        for p in (data / sub).glob("*.csv"):
            found.setdefault(p.stem, p)
    return found


def _find_recording(name: str) -> Path:
    """Exact name, else the only name containing it; the model must pick from game_sessions, never a raw path."""
    found = _recordings()
    key = str(name).strip().removesuffix(".csv").lower()
    if not key:
        raise ValueError("empty session name; call game_sessions to see the names")
    exact = [n for n in found if n.lower() == key]
    part = exact or [n for n in found if key in n.lower()]
    if len(part) != 1:
        hint = f"ambiguous, matches: {sorted(part)[:8]}" if part else "no such recording"
        raise ValueError(f"{hint}; call game_sessions to see the names")
    return found[part[0]]


def game_sessions(limit: int = 10) -> list[dict]:
    """List recorded game sessions and benchmark runs (PresentMon), newest first, with the name to pass to
    game_session_report / game_sessions_compare. Use it first for any question about a game's FPS or lags.

    Args:
        limit: How many recordings to list.
    """
    found = sorted(_recordings().items(), key=lambda kv: kv[1].stat().st_mtime, reverse=True)
    out = [{"name": n, "recorded": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            "kind": "benchmark run" if p.parent.name == "bench" else "session"} for n, p in found[:int(limit)]]
    return out or [{"error": "no recordings in data/sessions or data/bench; record one with scripts/record_presentmon.ps1"}]


def _report(name: str) -> dict:
    from . import games

    return games.analyze(_find_recording(name))   # recordings are already limited to the game's process


def game_session_report(name: str) -> dict:
    """FPS report of one recorded game session: average FPS, 1% / 0.1% lows, frame times, what limits the
    frame rate (GPU or CPU), the slowest 10-second stretches and the worst hitches. All numbers are computed
    from the recording. Get the name from game_sessions.

    Args:
        name: Recording name as listed by game_sessions.
    """
    try:
        r = _report(name)
    except (OSError, ValueError) as e:
        return {"error": str(e)}
    fs, w = r["frames"], r["window"]
    slices = r["slices"][:-1] or r["slices"]   # the last slice is usually partial
    slow = sorted(range(len(slices)), key=lambda i: slices[i])[:3]
    out = {"name": name, "window_seconds": (w["end"] - w["start"]).total_seconds(),
           "avg_fps": fs["avg_fps"], "low1_fps": fs["low1_fps"], "low01_fps": fs["low01_fps"],
           "median_frame_ms": fs["median_ms"], "p99_frame_ms": fs["p99_ms"], "worst_frame_ms": fs["worst_ms"],
           "frames_over_33ms": fs["slow"][33.3],
           "slowest_10s_stretches": [{"from_second": i * 10, "avg_fps": slices[i]} for i in sorted(slow)],
           "hitches_over_30ms": {"seconds_with_hitches": r["hitches"]["seconds"],
                                 "worst": [{"time": h["t"].strftime("%H:%M:%S"), "frames": h["frames"],
                                            "worst_ms": h["worst_ms"], "probably_game_exit": h["near_end"]}
                                           for h in r["hitches"]["worst"][:3]]},
           "notes": r["notes"]}
    b = r["bottleneck"]
    if b:
        out["limiter_percent_of_frames"] = {k: 100 * b[k] for k in ("gpu", "cpu", "neither")}
    return _round_deep(out)


def game_sessions_compare(before: str, after: str) -> dict:
    """Compare two recorded sessions or benchmark runs (e.g. before and after a settings change): differences
    in average FPS, 1% / 0.1% lows and p99 frame time. Only meaningful for the same scene or route; a single
    run varies from run to run, so tell the user small differences may be noise.

    Args:
        before: Name of the first recording (from game_sessions).
        after: Name of the second recording (from game_sessions).
    """
    try:
        a, b = _report(before)["frames"], _report(after)["frames"]
    except (OSError, ValueError) as e:
        return {"error": str(e)}
    keys = {"avg_fps": "avg_fps", "low1_fps": "low1_fps", "low01_fps": "low01_fps", "p99_frame_ms": "p99_ms"}
    out = {"before": before, "after": after}
    for label, k in keys.items():
        out[label] = {"before": a[k], "after": b[k], "change_percent": 100 * (b[k] / a[k] - 1) if a[k] else None}
    out["note"] = "single runs vary; repeat each setting at least twice before trusting a small difference"
    return _round_deep(out)


def _round_deep(x):
    if isinstance(x, dict):
        return {k: _round_deep(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_round_deep(v) for v in x]
    return round(x, 1) if isinstance(x, float) else x


TOOLS = [current_status, disk_usage, top_processes, metrics_history, disk_forecast, largest_folders,
         game_sessions, game_session_report, game_sessions_compare]
TOOL_MAP = {f.__name__: f for f in TOOLS}
