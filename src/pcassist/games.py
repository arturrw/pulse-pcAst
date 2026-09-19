"""Game session analysis: a PresentMon frame CSV (+ optional MSI Afterburner .hml log) -> a report.

Record PresentMon with --date_time (scripts/record_presentmon.ps1 does). All numbers are computed
here, deterministically; the LLM only ever explains a finished report.
"""
import csv
import statistics
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

HITCH_MS = 30.0            # a frame slower than this counts as a hitch
BOUND_THRESHOLD = 0.9      # busy share of the frame time above which a unit is the limiter
SLOW_MS = (16.7, 33.3, 50.0, 100.0)

HW_KEYS = ("GPU usage", "Memory usage", "GPU temperature", "Core clock", "Power",
           "CPU usage", "CPU temperature", "CPU power")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None  # PresentMon/Afterburner write NA / N/A for unavailable values


# ---------------------------------------------------------------- loading

def _parse_pm_time(s: str) -> datetime:
    day, clock = s.split(" ")
    y, m, d = map(int, day.split("-"))
    hms, _, frac = clock.partition(".")
    h, mi, se = map(int, hms.split(":"))
    return datetime(y, m, d, h, mi, se, int(frac[:6].ljust(6, "0")) if frac else 0)


def parse_presentmon(path, process: str | None = None) -> tuple[list[dict], list[str]]:
    """Frames of the main swap chain (the one with most frames), sorted by time.
    Times are kept exactly as in the file. Returns (frames, notes)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "TimeInDateTime" not in reader.fieldnames:
            raise ValueError("no TimeInDateTime column: record PresentMon with --date_time")
        rows = [r for r in reader if not process or r["Application"].lower() == process.lower()]
    chains = Counter((r["ProcessID"], r["SwapChainAddress"]) for r in rows
                     if r["SwapChainAddress"] != "0x0")
    if not chains:
        raise ValueError("no frames found" + (f" for {process}" if process else ""))
    main = chains.most_common(1)[0][0]
    notes = []
    other = len(rows) - chains[main]
    if other:
        notes.append(f"ignored {other} frames from other swap chains/processes")
    frames = []
    for r in rows:
        if (r["ProcessID"], r["SwapChainAddress"]) != main:
            continue
        ft = _num(r["MsBetweenPresents"])
        if not ft or ft <= 0:
            continue
        frames.append({"t": _parse_pm_time(r["TimeInDateTime"]), "ft": ft,
                       "cpu": _num(r.get("MsCPUBusy")), "gpu": _num(r.get("MsGPUBusy")),
                       "app": _num(r.get("MsBetweenAppStart"))})
    frames.sort(key=lambda fr: fr["t"])
    return frames, notes


_HML_DATE_FORMATS = ("%d-%m-%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _parse_hml_time(s: str) -> datetime:
    for fmt in _HML_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unknown Afterburner date format: {s!r}")


def parse_hml(path) -> dict[datetime, dict]:
    """Afterburner hardware monitoring log v1.x: {second: {sensor: value}} (local time)."""
    lines = Path(path).read_text(encoding="latin-1").splitlines()
    header = next((l for l in lines if l.startswith("02,")), None)
    if header is None:
        raise ValueError("not an Afterburner hardware monitoring log (no header record)")
    names = [h.strip() for h in header.split(",", 2)[2].split(",")]
    samples = {}
    for l in lines:
        if not l.startswith("80,"):
            continue
        parts = [p.strip() for p in l.split(",")]
        samples[_parse_hml_time(parts[1])] = {n: _num(v) for n, v in zip(names, parts[2:])}
    return samples


def detect_offset(frames: list[dict], samples: dict) -> tuple[float, bool]:
    """PresentMon stamps frames in a different time zone than Afterburner (3 h here). Compare the
    middles of the two recordings and round to 30 min. Returns (hours, ok); ok=False when the guess
    is implausible: more than 14 h, more than 10 min left over after rounding, or the hardware
    log covers less than half of the PresentMon capture (e.g. logs from different sessions)."""
    pm0, pm1 = frames[0]["t"], frames[-1]["t"]
    ab0, ab1 = min(samples), max(samples)
    diff = ((pm0 + (pm1 - pm0) / 2) - (ab0 + (ab1 - ab0) / 2)).total_seconds()
    off = round(diff / 1800) * 1800
    shift = timedelta(seconds=off)
    overlap = (min(pm1 - shift, ab1) - max(pm0 - shift, ab0)).total_seconds()
    ok = abs(off) <= 14 * 3600 and abs(diff - off) <= 600 and overlap >= 0.5 * (pm1 - pm0).total_seconds()
    return off / 3600, ok


# ---------------------------------------------------------------- analysis

def _pct(sorted_vals: list[float], p: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100))]


def frame_stats(frames: list[dict]) -> dict:
    ft = sorted(f["ft"] for f in frames)
    n = len(ft)
    worst = ft[::-1]
    k1, k01 = max(1, n // 100), max(1, n // 1000)
    return {
        "frames": n,
        "avg_fps": n / (sum(ft) / 1000),
        "median_ms": _pct(ft, 50),
        "p99_ms": _pct(ft, 99),
        "worst_ms": ft[-1],
        "low1_fps": 1000 / (sum(worst[:k1]) / k1),     # mean of the slowest 1% of frames
        "low01_fps": 1000 / (sum(worst[:k01]) / k01),  # ... slowest 0.1%
        "slow": {th: sum(1 for x in ft if x > th) for th in SLOW_MS},
    }


def bottleneck(frames: list[dict]) -> dict | None:
    """Share of frames where the GPU / the CPU was busy for most of the frame time.
    None if PresentMon gave no busy times."""
    gpu = cpu = neither = 0
    for f in frames:
        if f["gpu"] is None or f["cpu"] is None:
            continue
        if f["gpu"] / f["ft"] > BOUND_THRESHOLD:
            gpu += 1
        elif f["cpu"] / (f["app"] or f["ft"]) > BOUND_THRESHOLD:
            cpu += 1
        else:
            neither += 1
    n = gpu + cpu + neither
    if not n:
        return None
    gpu_ms = [f["gpu"] for f in frames if f["gpu"] is not None]
    cpu_ms = [f["cpu"] for f in frames if f["cpu"] is not None]
    return {"gpu": gpu / n, "cpu": cpu / n, "neither": neither / n,
            "gpu_busy_ms": statistics.mean(gpu_ms), "cpu_busy_ms": statistics.mean(cpu_ms)}


def find_play_window(frames: list[dict], min_ratio: float = 0.5,
                     max_gap_s: int = 10) -> tuple[datetime, datetime]:
    """Longest stretch of seconds whose FPS is at least min_ratio of the median second, allowing
    short dips (max_gap_s). Cuts off map loading and menus. Override with an explicit window
    if the capture is mostly loading."""
    per_sec = Counter(f["t"].replace(microsecond=0) for f in frames)
    seconds = sorted(per_sec)
    threshold = min_ratio * statistics.median(per_sec.values())
    good = [s for s in seconds if per_sec[s] >= threshold]
    best = cur = (good[0], good[0])
    for s in good[1:]:
        cur = (cur[0], s) if (s - cur[1]).total_seconds() <= max_gap_s + 1 else (s, s)
        if cur[1] - cur[0] > best[1] - best[0]:
            best = cur
    return best[0], best[1] + timedelta(seconds=1)


def hitches(frames: list[dict], hw: dict | None, top: int = 5) -> dict:
    """The worst seconds by total time lost to slow frames, with what the hardware was doing then."""
    per_sec = defaultdict(list)
    for f in frames:
        if f["ft"] > HITCH_MS:
            per_sec[f["t"].replace(microsecond=0)].append(f["ft"])
    out = []
    for sec, v in sorted(per_sec.items(), key=lambda kv: -sum(kv[1]))[:top]:
        row = {"t": sec, "frames": len(v), "worst_ms": max(v)}
        if hw and sec in hw:
            row.update({k: hw[sec].get(k) for k in ("GPU usage", "CPU usage", "Memory usage")})
        out.append(row)
    return {"seconds": len(per_sec), "worst": out}


def hardware_summary(hw: dict, start: datetime, end: datetime) -> dict | None:
    rows = [d for t, d in hw.items() if start <= t < end]
    if not rows:
        return None
    out = {}
    for k in HW_KEYS:
        v = [d[k] for d in rows if d.get(k) is not None]
        if v:
            out[k] = {"avg": statistics.mean(v), "max": max(v)}
    out["seconds"] = len(rows)
    out["temp_limit_s"] = sum(1 for d in rows if (d.get("Temp limit") or 0) > 0)
    out["power_limit_s"] = sum(1 for d in rows if (d.get("Power limit") or 0) > 0)
    cores = [[v for k, v in d.items() if k.startswith("CPU") and k.endswith(" usage")
              and k != "CPU usage" and v is not None] for d in rows]
    busiest = [max(c) for c in cores if c]
    if busiest:
        out["busiest_core_avg"] = statistics.mean(busiest)
        out["busiest_core_95_s"] = sum(1 for x in busiest if x >= 95)
    return out


def analyze(pm_csv, hml=None, process: str | None = None, start: datetime | time | None = None,
            end: datetime | time | None = None, offset_hours: float | None = None) -> dict:
    """Full report. start/end (a datetime, or a time of day on the capture's date) are in Afterburner
    (local) time when an .hml is given, otherwise in the PresentMon file's own time.
    offset_hours: PresentMon minus local time (auto-detected with an .hml)."""
    frames, notes = parse_presentmon(pm_csv, process)
    samples = parse_hml(hml) if hml else None
    if offset_hours is None:
        offset_hours = 0.0
        if samples:
            offset_hours, ok = detect_offset(frames, samples)
            if not ok:
                notes.append("PresentMon and Afterburner recordings do not overlap in time: "
                             "hardware stats may be wrong, pass --pm-offset-hours")
    shift = timedelta(hours=offset_hours)
    for f in frames:
        f["t"] -= shift
    day = frames[0]["t"].date()
    start = datetime.combine(day, start) if isinstance(start, time) else start
    end = datetime.combine(day, end) if isinstance(end, time) else end
    auto = start is None and end is None
    if auto:
        start, end = find_play_window(frames)
    else:
        start = start or frames[0]["t"]
        end = end or frames[-1]["t"] + timedelta(seconds=1)
    play = [f for f in frames if start <= f["t"] < end]
    if not play:
        raise ValueError("no frames in the selected window")
    if len(play) < 600:
        notes.append(f"only {len(play)} frames in the window: numbers are not reliable")
    hw = hardware_summary(samples, start, end) if samples else None
    return {
        "capture": {"frames": len(frames), "start": frames[0]["t"], "end": frames[-1]["t"]},
        "window": {"start": start, "end": end, "auto": auto},
        "time_offset_hours": offset_hours if samples else None,
        "frames": frame_stats(play),
        "bottleneck": bottleneck(play),
        "hitches": hitches(play, samples),
        "hardware": hw,
        "notes": notes,
    }


# ---------------------------------------------------------------- text output

def _t(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def format_report(r: dict) -> str:
    fs, w = r["frames"], r["window"]
    secs = (w["end"] - w["start"]).total_seconds()
    lines = [
        f"Capture: {r['capture']['frames']} frames, {_t(r['capture']['start'])}-{_t(r['capture']['end'])}",
        f"Analyzed window: {_t(w['start'])}-{_t(w['end'])} ({secs:.0f} s, "
        f"{'auto-detected, loading/menus cut off' if w['auto'] else 'manual'})",
        "",
        f"FPS: avg {fs['avg_fps']:.0f} | 1% low {fs['low1_fps']:.0f} | 0.1% low {fs['low01_fps']:.0f}",
        f"Frame time: median {fs['median_ms']:.1f} ms | p99 {fs['p99_ms']:.1f} ms | worst {fs['worst_ms']:.0f} ms",
        "Slow frames: " + ", ".join(f"> {th:g} ms: {n}" for th, n in fs["slow"].items()),
    ]
    b = r["bottleneck"]
    if b:
        lines += ["", f"Limiter: GPU {b['gpu']:.0%} of frames | CPU {b['cpu']:.0%} | neither {b['neither']:.0%} "
                      f"(busy per frame: GPU {b['gpu_busy_ms']:.1f} ms, CPU {b['cpu_busy_ms']:.1f} ms)"]
    h = r["hitches"]
    if h["worst"]:
        lines += ["", f"Hitches (> {HITCH_MS:g} ms): in {h['seconds']} s. Worst:"]
        for x in h["worst"]:
            ctx = ""
            if x.get("GPU usage") is not None:
                ctx = f" | GPU {x['GPU usage']:.0f}%, CPU {x['CPU usage']:.0f}%, VRAM {x['Memory usage']:.0f} MB"
            lines.append(f"  {_t(x['t'])}: {x['frames']} frames, worst {x['worst_ms']:.0f} ms{ctx}")
    hw = r["hardware"]
    if hw:
        lines += ["", "Hardware (Afterburner):"]
        for k in HW_KEYS:
            if k in hw:
                lines.append(f"  {k}: avg {hw[k]['avg']:.1f}, max {hw[k]['max']:.1f}")
        lines.append(f"  Throttling: temp limit {hw['temp_limit_s']} s, power limit {hw['power_limit_s']} s")
        if "busiest_core_avg" in hw:
            lines.append(f"  Busiest CPU core: avg {hw['busiest_core_avg']:.0f}%, "
                         f">= 95% for {hw['busiest_core_95_s']} s")
    if r["time_offset_hours"]:
        lines.append(f"\nPresentMon time is {r['time_offset_hours']:+g} h from local time (auto-detected).")
    lines += [f"Note: {n}" for n in r["notes"]]
    return "\n".join(lines)


def format_compare(a: dict, b: dict) -> str:
    """Deltas between two reports (A = before, B = after). Numbers only, no verdict."""
    fa, fb = a["frames"], b["frames"]

    def row(name, va, vb, unit="", better_higher=True, digits=0):
        d = vb - va
        pct = f" ({d / va:+.0%})" if va else ""
        mark = "" if abs(d) < 10 ** -digits else (" better" if (d > 0) == better_higher else " worse")
        return f"{name:<20} {va:>8.{digits}f} -> {vb:>8.{digits}f}{unit}  {d:+.{digits}f}{pct}{mark}"

    lines = [row("avg FPS", fa["avg_fps"], fb["avg_fps"]),
             row("1% low FPS", fa["low1_fps"], fb["low1_fps"]),
             row("0.1% low FPS", fa["low01_fps"], fb["low01_fps"]),
             row("p99 frame time", fa["p99_ms"], fb["p99_ms"], " ms", False, 1),
             row("frames > 33 ms", fa["slow"][33.3], fb["slow"][33.3], "", False)]
    ha, hb = a["hardware"], b["hardware"]
    if ha and hb:
        for k in ("GPU usage", "Memory usage", "GPU temperature", "Power"):
            if k in ha and k in hb:
                lines.append(row(k + " avg", ha[k]["avg"], hb[k]["avg"], "", False, 1))
    da = (a["window"]["end"] - a["window"]["start"]).total_seconds()
    db_ = (b["window"]["end"] - b["window"]["start"]).total_seconds()
    lines.append("")
    if min(da, db_) < 60:
        lines.append("Warning: a window is shorter than 60 s, the comparison is weak.")
    if max(da, db_) > 1.5 * min(da, db_):
        lines.append(f"Warning: windows differ in length ({da:.0f} s vs {db_:.0f} s); "
                     "compare the same scene/route.")
    lines.append("Single runs vary from run to run: repeat each setting at least twice before trusting a small delta.")
    return "\n".join(lines)
