"""Read-only tools the LLM can call. Docstrings double as the tool descriptions."""
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil

from . import ack, anomaly, binaries, db, netwatch, persistence, procwatch, scan, timeline, winhealth
from .collectors import Collector, battery_info, collect_disks, static_hardware_info

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


def _recorded_seconds(timestamps: list[float]) -> float:
    return anomaly.recorded_seconds(timestamps)


def _round(d: dict) -> dict:
    return {k: round(v, 1) if isinstance(v, float) else v for k, v in d.items()}


def current_status() -> dict:
    """Take a live snapshot of the computer right now: CPU, RAM, disk I/O activity
    (read/write MB/s - this is disk "load", not free space) and network rates,
    GPU load/VRAM/temperature/power, and the top processes by CPU and memory.
    "system" also carries static hardware facts (cpu_name, cpu_cores_logical, cpu_cores_physical,
    ram_total_mb, gpu_driver_version, hostname) for "what CPU/how many cores/how much RAM/what's
    this PC's name" questions - never derive these from a percentage or estimate them, only report
    what is here; a null field means that fact is not available. "battery" is percent/plugged_in,
    or null on a desktop with no battery."""
    s = Collector().sample()
    procs = s["processes"]
    return {
        "system": _round({**{k: v for k, v in s["system"].items() if k != "ts"}, **static_hardware_info()}),
        "battery": battery_info(),
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


def _history(metric: str, minutes: int) -> dict:
    """min / avg / max / latest of one metric over a window, plus how much of the window the data really covers."""
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
        return {"metric": metric, "error": "no collected data in this window; run `pulse collect`"}
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


def metrics_history(metric: str, minutes: int = 60) -> dict:
    """Summarize one metric over a recent time window from collected history: min, avg, max (and when the max
    happened), latest, the change over the last 10 minutes. Use it for any question about how a metric looked or
    changed: "was there a spike", "how hot did the GPU get", "was anything unusual / strange / an anomaly".
    For gpu_temp_c, ram_percent, ram_used_mb and swap_percent the result also lists `unusual_periods`: stretches
    where the value stayed far outside its recent normal (a statistical check, not a fault diagnosis), each marked
    `during_game` when a game recording overlaps. Load metrics (CPU, GPU usage, disk, network) are not checked
    for unusual periods: they swing with whatever the user runs. It is about history: for what a value is right
    now ("what is the temperature now") use current_status instead.

    Args:
        metric: One of cpu_percent, ram_percent, ram_used_mb, swap_percent, disk_read_mbps,
            disk_write_mbps, net_sent_kbps, net_recv_kbps, gpu_util_percent, gpu_mem_used_mb,
            gpu_temp_c, gpu_power_w.
        minutes: How many minutes of history to look at.
    """
    result = _history(metric, minutes)
    if "error" in result or metric not in anomaly.STATE_METRICS:
        return result
    found = anomalies(metric, minutes)
    if "error" not in found:
        result["unusual_periods_found"] = found["events_found"]
        result["unusual_periods"] = found["events"]
        result["unusual_periods_check"] = found["how_it_works"]
    return result


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
        return [{"error": "no collected data in this window; run `pulse collect`"}]
    return out


MAX_FOLDERS = 25   # rows the model may ask for


def fixed_drives() -> set[str]:
    """Roots of the local fixed drives (C:, D:, ...); CD/DVD, network and removable drives are left out."""
    return {p.mountpoint for p in psutil.disk_partitions(all=False) if "fixed" in p.opts}


def largest_folders(path: str = "C:\\", limit: int = 10) -> dict:
    """Find which subfolders of a directory take the most disk space (read-only scan, up to ~45 s).
    Use it to answer 'what is filling my disk?'. Drill down by calling it again on a big subfolder.
    Only absolute paths on local drives work (C:\\, C:\\Users); network shares and device paths are refused.

    Args:
        path: Directory to scan, e.g. 'C:\\' or 'C:\\Users'.
        limit: How many of the largest subfolders to return.
    """
    real, why = scan.check_model_path(path, fixed_drives())
    if why:   # the path came from the model: only local fixed drives, no network shares, no device paths
        return {"error": f"not scanned: {why}"}
    r = scan.largest_children(real, max(1, min(int(limit), MAX_FOLDERS)))
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


KNOWN_GAMES = {"cs2.exe": "Counter-Strike 2", "csgo.exe": "Counter-Strike: Global Offensive", "dota2.exe": "Dota 2",
               "valorant-win64-shipping.exe": "VALORANT", "fortniteclient-win64-shipping.exe": "Fortnite",
               "r5apex.exe": "Apex Legends", "overwatch.exe": "Overwatch 2", "gta5.exe": "Grand Theft Auto V",
               "eldenring.exe": "Elden Ring", "cyberpunk2077.exe": "Cyberpunk 2077", "witcher3.exe": "The Witcher 3",
               "rocketleague.exe": "Rocket League", "bg3.exe": "Baldur's Gate 3", "bg3_dx11.exe": "Baldur's Gate 3",
               "hogwartslegacy.exe": "Hogwarts Legacy", "league of legends.exe": "League of Legends"}
_app_cache: dict[tuple, str] = {}
_RUN_NAME = re.compile(r"^(.+)_([^_]+)_(\d+)$")     # <batch>_<variant>_<repeat>, as scripts/bench_batch.ps1 writes them
_stats_cache: dict[tuple, dict] = {}


def split_run_name(name: str) -> tuple[str | None, str | None]:
    """(batch, variant) of a benchmark run named <batch>_<variant>_<n>, else (None, None)."""
    m = _RUN_NAME.match(name)
    return (m.group(1), m.group(2)) if m else (None, None)


def run_stats(name: str) -> dict:
    """The frame statistics of one recording, remembered per file version (an analysis takes about half a second)."""
    path = _find_recording(name)
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _stats_cache:
        from . import games

        _stats_cache[key] = games.analyze(path)["frames"]
    return _stats_cache[key]


def game_title(app: str) -> str:
    """A readable name for a game's program: a known one, else the file name without .exe / -Win64-Shipping, spaced."""
    known = KNOWN_GAMES.get(app.lower())
    if known:
        return known
    stem = re.sub(r"(?i)\.exe$|[-_ ]win(64|32)[-_ ]shipping$", "", app)
    stem = re.sub(r"(?i)[-_ ]win(64|32)[-_ ]shipping$", "", stem)
    return re.sub(r"[-_]+", " ", stem).strip().title() or app


def _application_of(path: Path) -> str:
    """The program a PresentMon recording belongs to (the first column of its first row); cached per file version."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _app_cache:
        app = ""
        try:
            with open(path, "rb") as f:
                f.readline()
                app = f.readline().decode("utf-8", "replace").split(",")[0].strip()
        except OSError:
            pass
        _app_cache[key] = app or "unknown"
    return _app_cache[key]


def game_sessions(limit: int = 10) -> list[dict]:
    """List recorded game sessions and benchmark runs (PresentMon), newest first, with the name to pass to
    game_session_report / game_sessions_compare. Use it first for any question about a game's FPS or lags.

    Args:
        limit: How many recordings to list.
    """
    found = sorted(_recordings().items(), key=lambda kv: kv[1].stat().st_mtime, reverse=True)
    out = []
    for n, p in found[:int(limit)]:
        row = {"name": n, "recorded": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
               "kind": "benchmark run" if p.parent.name == "bench" else "session", "game": _application_of(p)}
        if row["kind"] == "benchmark run":
            row["batch"], row["variant"] = split_run_name(n)
        out.append(row)
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


def _recording_span(path: Path) -> tuple[float, float] | None:
    """(start, end) of a PresentMon recording in local epoch seconds: end = file modification time, start = end minus
    the capture length (last minus first row). PresentMon writes its own clock, so only the length is trusted."""
    from . import games

    try:
        with open(path, "rb") as f:
            head = f.readline().decode("utf-8-sig").strip().split(",")
            first = f.readline().decode("utf-8", "replace").split(",")
            f.seek(max(0, path.stat().st_size - 4096))
            last = f.read().decode("utf-8", "replace").strip().splitlines()[-1].split(",")
        i = head.index("TimeInDateTime")
        length = (games._parse_pm_time(last[i]) - games._parse_pm_time(first[i])).total_seconds()
        end = path.stat().st_mtime
        return end - length, end
    except (OSError, ValueError, IndexError):
        return None


ANOMALY_WINDOW = 288      # samples of history the detector compares against (~2.4 h at the default 30 s)
ANOMALY_MIN_RUN = 6       # a deviation must last this many samples to count (~3 min): lone spikes are noise
ANOMALY_THRESHOLD = 8.0   # robust z-score


def anomalies(metric: str, minutes: int = 1440) -> dict:
    """(Not a model tool: metrics_history calls it for state metrics.) Find unusual periods of a machine-STATE metric in collected history: values far outside what was normal
    over the preceding hours (a robust statistical check, not a fault diagnosis). Only state metrics are supported
    (gpu_temp_c, ram_percent, ram_used_mb, swap_percent): load metrics such as GPU usage, disk or network
    activity swing whenever the user starts a game, so their outliers are ordinary workload. An event that
    overlaps a recorded game session has `during_game` set; a high RAM or swap value then is likely the game.
    Use it only for "anything unusual / strange / abnormal" questions. Questions about a spike, peak, jump or how a
    metric changed need the numbers from metrics_history instead, not this tool.

    Args:
        metric: One of gpu_temp_c, ram_percent, ram_used_mb, swap_percent.
        minutes: How many minutes of history to look at.
    """
    if metric not in anomaly.STATE_METRICS:
        return {"error": f"'{metric}' is not a state metric", "available": list(anomaly.STATE_METRICS),
                "next_step": f"call metrics_history(metric='{metric}') and report its numbers",
                "note": "load metrics (GPU util, power, disk, network, CPU) change with whatever the user runs, "
                        "so unusual values there are not anomalies; say so briefly and give the numbers"}
    table, col = METRICS[metric]
    now = time.time()
    since = now - int(minutes) * 60
    with db.connect(_db_path) as conn:   # a few hours before the window so the detector has history to compare to
        rows = conn.execute(f"SELECT ts, {col} FROM {table} WHERE ts >= ? AND {col} IS NOT NULL ORDER BY ts",
                            (since - 6 * 3600,)).fetchall()
    ts, vals = [r[0] for r in rows], [r[1] for r in rows]
    inside = [i for i, t in enumerate(ts) if t >= since]
    if len(inside) < 2:
        return {"metric": metric, "error": "no collected data in this window; run `pulse collect`"}
    step = sorted(b - a for a, b in zip(ts, ts[1:]))[len(ts) // 2 - 1]
    scored = anomaly.scores(vals, ANOMALY_WINDOW, ts)
    games_seen = [(span, name) for name, p in _recordings().items() if (span := _recording_span(p))]
    found = []
    for a, b, peak in anomaly.events(scored, ANOMALY_THRESHOLD, 12, ANOMALY_MIN_RUN):
        if ts[a] < since:
            continue
        seg = vals[a:b + 1]
        typical = sorted(vals[max(0, a - ANOMALY_WINDOW):a])
        if max(abs(v - typical[len(typical) // 2]) for v in seg) < anomaly.MIN_DEVIATION[metric]:
            continue   # statistically unusual for a very steady metric, but too small a change to matter
        game = next((name for (g0, g1), name in games_seen if g0 - 60 <= ts[b] and ts[a] <= g1 + 60), None)
        found.append(_round({
            "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts[a])),
            "minutes_ago": (now - ts[a]) / 60, "duration_minutes": (ts[b] - ts[a]) / 60 + step / 60,
            "typical_value": typical[len(typical) // 2],
            "value_at_start": vals[a], "most_unusual_value": max(seg, key=lambda v: abs(v - typical[len(typical) // 2])),
            "z_score": min(peak, 999.0), "during_game": game}))
    recorded = _recorded_seconds([ts[i] for i in inside]) / 60
    out = {"metric": metric, "minutes": int(minutes), "data_covers_minutes": recorded,
           "events_found": len(found), "events": found[-10:],
           "ignored_if_change_smaller_than": anomaly.MIN_DEVIATION[metric],
           "how_it_works": f"a value counts when it stays far outside the median of the previous ~{ANOMALY_WINDOW * step / 3600:.1f} h "
                           f"for at least {ANOMALY_MIN_RUN * step / 60:.0f} min; the first ~{ANOMALY_WINDOW // 4 * step / 60:.0f} min "
                           "after the collector (re)starts are not checked"}
    out["summary"] = (f"{len(found)} unusual period(s) of {metric} in the last {int(minutes)} min"
                      + (f"; the collected data covers only ~{recorded:.0f} min of that" if recorded < int(minutes) * 0.5 else ""))
    if recorded < int(minutes) * 0.5:
        out["warning"] = (f"collected data covers only ~{recorded:.0f} min of the requested {int(minutes)} min; "
                          "tell the user the check is for that shorter period only")
    return _round(out)


def process_watch(minutes: int = 1440) -> dict:
    """Look for processes that behave unusually against their own history: a name never recorded before, a process
    using far more CPU than it usually does (or a lot of CPU with nothing to compare to), or one whose memory keeps
    growing. This is a behavioral check, NOT an antivirus: only names, CPU and memory of the heaviest processes are
    recorded (no file path, signature or network use), so it cannot say whether anything is malicious and a quiet
    process is invisible. Never call a process malware or safe from this; report what stands out and its
    limits. `confidence` is low while there is under 24 h of recorded history: then "never recorded" is weak evidence.

    Args:
        minutes: How many minutes of recent history to examine (older history is the baseline). The default is one
            day (1440); use 60 only when the user asks about the last hour.
    """
    now = time.time()
    since = now - int(minutes) * 60
    with db.connect(_db_path) as conn:
        r = procwatch.analyze(conn, now - int(minutes) * 60, now)
        n = conn.execute("SELECT COUNT(*) FROM process_snapshots WHERE ts >= ?", (since,)).fetchone()[0]
        exes = [e for (e,) in conn.execute("SELECT DISTINCT exe FROM process_exes WHERE last_seen >= ?", (since,))]
        try:   # the signature check runs Windows PowerShell once per new file; a failure must not break the tool
            binaries.ensure_checked(conn, exes, checker=binaries.check_signatures)
        except Exception:
            pass
        files = binaries.assess(conn, since)
        net = netwatch.analyze(conn, since, now, {f["name"] for f in files["flagged"]})
    if not n:
        return {"error": "no collected process data in this window; run `pulse collect`"}
    for row in r["new"]:
        row["first_seen"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(row.pop("first_seen_ts")))
    spans = [(span, name) for name, p in _recordings().items() if (span := _recording_span(p))]
    r["game_recordings_in_window"] = sorted(name for (g0, g1), name in spans if g1 >= now - int(minutes) * 60)
    r["minutes"] = int(minutes)
    high = sum(f["severity"] == "high" for f in files["flagged"])
    r["suspect_files"] = files["flagged"][:8]
    r["file_checks"] = {"names_with_known_file": files["files_with_path"], "names_recorded": files["names_recorded"],
                        "unsigned_files": files["unsigned_files"],
                        "note": "the file path of protected Windows processes cannot be read, so they are not covered"}
    r["network"] = net
    n_net = sum(len(net.get(k, [])) for k in ("from_suspicious_files", "suspicious_ports", "new_listeners", "new_destinations"))
    counts = (f"{len(files['flagged'])} file(s) with a suspicious location or signature ({high} high), "
              f"{n_net} network finding(s){'' if net.get('available') else ' (no network data yet)'}, "
              f"{r['new_total']} never-recorded name(s), {len(r['busy'])} busier than usual, "
              f"{len(r['growing'])} with growing memory")
    caveat = (f"LOW confidence for the history-based flags: only {r['baseline_hours']:.1f} h of earlier history, so new "
              "names are weak evidence (file location and signature checks do not depend on history length). "
              if r["confidence"] == "low" else "")
    r = {"summary": f"{counts}. {caveat}This is a behavioral check, not a malware scan: it cannot say a process is "
                    "safe or malicious, and quiet processes are invisible.", **r}
    if r["confidence"] == "low":
        r["warning"] = (f"only {r['baseline_hours']:.1f} h of earlier history to compare with (need "
                        f"{procwatch.MIN_BASELINE_HOURS}+ h): 'new' names are weak evidence; tell the user")
    return _round_deep(r)


def system_health(hours: int = 168) -> dict:
    """How the machine itself has been doing, from the Windows event logs and Windows Defender: blue screens, unexpected
    shutdowns, hardware and disk errors, graphics-driver resets, apps that keep crashing, and Defender's state
    (real-time protection on or off, Group Policy values that switch it off, old signatures, threats it detected). Use it for "why did my PC crash / freeze",
    "is my antivirus on", "did Defender find anything". Findings the user has accepted are marked `accepted` and are
    not counted as problems. The Security log (failed logins) needs administrator rights and is not read.

    Args:
        hours: How many hours back to look (default a week).
    """
    r = winhealth.health(int(hours), reader=winhealth.read_raw, policy_reader=winhealth.read_defender_policy)
    if r.get("available"):
        ack.mark(_db_path, r["findings"])
        open_ = [f for f in r["findings"] if not f["accepted"]]
        r["findings_high"] = sum(f["severity"] == "high" for f in open_)
        r["accepted_count"] = len(r["findings"]) - len(open_)
        r["summary"] = (f"{len(open_)} open finding(s) ({r['findings_high']} high)"
                        + (f", {r['accepted_count']} accepted by the user" if r["accepted_count"] else "")
                        if r["findings"] else "nothing wrong in the Windows logs")
    return _round_deep(r)


def startup_changes(hours: int = 168) -> dict:
    """What starts by itself on this PC (Run keys, startup folders, scheduled tasks, services, WMI subscriptions,
    browser extensions and the browsers' developer-mode switch) and what changed:
    entries that are new since the first snapshot, and old ones that already look bad (a hidden script host, an
    encoded PowerShell command, a download-and-run trick, a program started from Temp or Downloads, a broken
    signature). A new autostart entry is one of the strongest signs of malware, but installers add entries too:
    report what stands out and never call an entry malicious or safe. Entries the user accepted are marked.

    Args:
        hours: How far back to look for new entries (default a week).
    """
    now = time.time()
    with db.connect(_db_path) as conn:
        last = persistence.last_snapshot(conn)
        if last is None or now - last > persistence.STALE_SECONDS:
            persistence.snapshot(conn, reader=persistence.read_items)
        r = persistence.assess(conn, now - int(hours) * 3600, checker=binaries.check_signatures)
    if r.get("available"):
        for key in ("new_or_changed", "already_present_but_suspicious"):
            ack.mark(_db_path, r[key])
        r["high"] = sum(x["severity"] == "high" and not x["accepted"] for x in r["new_or_changed"])
        r["already_present_open"] = sum(not x["accepted"] for x in r["already_present_but_suspicious"])
        r["summary"] = (f"{r['new_or_changed_count']} new or changed entr(ies) since {r['baseline_at']} ({r['high']} high), "
                        f"{r['already_present_open']} older entr(ies) that look suspicious")
    return _round_deep(r)


def what_happened(when: str = "now", minutes: int = 30) -> dict:
    """Everything the assistant knows about one moment, on a single timeline: metric changes, the heaviest and the
    newly appeared processes, new network destinations, new autostart entries, alerts that were sent, game recordings,
    gaps when the PC or the collector was off, and Windows events (crashes, shutdowns, Defender). Use it for "what
    happened at 14:03", "why did it freeze around 9 pm", "what was going on yesterday evening". It lines things up in
    time and does not say what caused what.

    Args:
        when: The moment: "14:03", "yesterday 21:30", "2026-09-20 14:03" or "45 minutes ago". Default now.
        minutes: Look this many minutes before and after the moment (default 30, at most 240).
    """
    from . import alerts   # imported here: alerts itself imports this module

    now = datetime.now()
    center = timeline.parse_when(when, now)
    if center is None:
        return {"error": f"could not understand the time '{when}'; use 14:03, yesterday 21:30, 2026-09-20 14:03 or '45 minutes ago'"}
    lo_dt = center - timedelta(minutes=max(1, min(int(minutes), timeline.MAX_HALF_WINDOW_MIN)))
    raw = winhealth.read_raw(max(1, int((now - lo_dt).total_seconds() // 3600) + 1))
    spans = [(sp[0], sp[1], name) for name, p in _recordings().items() if (sp := _recording_span(p))]
    with db.connect(_db_path) as conn:
        r = timeline.build(conn, center, int(minutes), now, alerts.recent_log(_db_path, 500), spans, raw)
    r["moment"] = center.strftime("%Y-%m-%d %H:%M")
    return _round_deep(r)


def _round_deep(x):
    if isinstance(x, dict):
        return {k: _round_deep(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_round_deep(v) for v in x]
    return round(x, 1) if isinstance(x, float) else x


TOOLS = [current_status, disk_usage, top_processes, metrics_history, disk_forecast, largest_folders,
         game_sessions, game_session_report, game_sessions_compare, process_watch, system_health, startup_changes, what_happened]
TOOL_MAP = {f.__name__: f for f in TOOLS}
