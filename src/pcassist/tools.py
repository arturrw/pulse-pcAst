"""Read-only tools the LLM can call. Docstrings double as the tool descriptions."""
import time

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
        n, lo, avg, hi, first_ts, last_ts = conn.execute(
            f"SELECT COUNT({col}), MIN({col}), AVG({col}), MAX({col}), MIN(ts), MAX(ts) FROM {table} WHERE ts >= ?",
            (since,),
        ).fetchone()
        last = conn.execute(f"SELECT {col} FROM {table} ORDER BY ts DESC LIMIT 1").fetchone()
        if n:
            peak_ts = conn.execute(
                f"SELECT ts FROM {table} WHERE ts >= ? AND {col} = ? ORDER BY ts DESC LIMIT 1", (since, hi)
            ).fetchone()[0]
            recent = conn.execute(
                f"SELECT ts, {col} FROM {table} WHERE ts >= ? ORDER BY ts LIMIT 1", (max(since, last_ts - 600),)
            ).fetchone()
    if not n:
        return {"metric": metric, "error": "no collected data in this window; run `pcassist collect`"}
    covers = (last_ts - first_ts) / 60
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
            hours = (pts[-1][0] - pts[0][0]) / 3600
            used, total = pts[-1][1], pts[-1][2]
            row = {"disk": mount.rstrip("\\"), "used_gb": used, "free_gb": total - used,
                   "history_hours": hours, "confidence": "ok" if hours >= MIN_FORECAST_HOURS else "low"}
            if len(pts) < 2 or hours <= 0:
                row["note"] = "not enough samples for a trend"
            else:
                rate = _slope_per_day([(t, u) for t, u, _ in pts])
                row["growth_gb_per_day"] = rate
                if rate > 0.01:
                    row["days_until_full"] = (total - used) / rate
                else:
                    row["note"] = "usage is not growing, no fill-up expected"
            if row["confidence"] == "low":
                row["warning"] = (f"only {hours:.1f} h of history (need {MIN_FORECAST_HOURS}+ h); "
                                  "tell the user this estimate is unreliable")
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


TOOLS = [current_status, disk_usage, top_processes, metrics_history, disk_forecast, largest_folders]
TOOL_MAP = {f.__name__: f for f in TOOLS}
