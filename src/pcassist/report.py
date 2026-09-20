"""HTML report of the collected history: one self-contained file (inline SVG, no scripts, no network).

Everything is computed from the database by the same functions the chat tools use, so the report and the
chat always agree. Names that come from the machine (process names) are HTML-escaped."""
import time
from html import escape

from . import anomaly, db, tools

CHARTS = [("gpu_temp_c", "GPU temperature", "°C"), ("cpu_percent", "CPU load", "%"),
          ("ram_percent", "RAM used", "%"), ("gpu_util_percent", "GPU load", "%")]
MAX_POINTS = 360          # chart resolution: a day at 30 s is 2880 samples, averaged into buckets
W, H, PAD_L, PAD_R, PAD_T, PAD_B = 720, 170, 46, 10, 10, 24

STYLE = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1c1a;--mute:#6b6b66;--line:#2f6fdb;--grid:#e3e3de;--warn:#b25b00;--ok:#2a7d46}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecece8;--mute:#9a9a94;--line:#6ea0ff;--grid:#33332f;--warn:#ffb15c;--ok:#5fcf8b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:800px;margin:0 auto;padding:20px 16px 40px}h1{font-size:22px;margin:0 0 2px}h2{font-size:17px;margin:26px 0 8px}
.mute{color:var(--mute)}section{background:var(--card);border-radius:10px;padding:12px 16px;margin-top:12px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:4px 10px 4px 0;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
th{color:var(--mute);font-weight:500}svg{width:100%;height:auto;display:block}.warn{color:var(--warn)}.ok{color:var(--ok)}
svg text{fill:var(--mute);font-size:11px}svg .g{stroke:var(--grid)}svg .l{fill:none;stroke:var(--line);stroke-width:1.6;stroke-linejoin:round}
"""


def _clock(ts: float, fmt: str = "%H:%M") -> str:
    return time.strftime(fmt, time.localtime(ts))


def _num(x, digits: int = 1) -> str:
    return f"{x:.{digits}f}" if isinstance(x, (int, float)) else "-"


def series(metric: str, hours: float, now: float) -> list[tuple[float, float]]:
    table, col = tools.METRICS[metric]
    with db.connect(tools._db_path) as conn:
        rows = conn.execute(f"SELECT ts, {col} FROM {table} WHERE ts >= ? AND {col} IS NOT NULL ORDER BY ts",
                            (now - hours * 3600,)).fetchall()
    return rows


def segments(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Continuous runs: a line is not drawn across a stretch when the collector was off."""
    if not points:
        return []
    limit = anomaly.gap_limit([t for t, _ in points])
    out = [[points[0]]]
    for a, b in zip(points, points[1:]):
        if b[0] - a[0] > limit:
            out.append([])
        out[-1].append(b)
    return out


def downsample(points: list[tuple[float, float]], t0: float, t1: float) -> list[tuple[float, float]]:
    """Average into at most MAX_POINTS time buckets (empty buckets stay empty, so gaps survive)."""
    if len(points) <= MAX_POINTS:
        return points
    width = (t1 - t0) / MAX_POINTS
    buckets: dict[int, list[float]] = {}
    for t, v in points:
        buckets.setdefault(min(MAX_POINTS - 1, int((t - t0) / width)), []).append(v)
    return [(t0 + (k + 0.5) * width, sum(v) / len(v)) for k, v in sorted(buckets.items())]


def chart(metric: str, title: str, unit: str, hours: float, now: float) -> str:
    t0, t1 = now - hours * 3600, now
    pts = downsample(series(metric, hours, now), t0, t1)
    if len(pts) < 2:
        return f"<h2>{escape(title)}</h2><p class='mute'>no data in this period</p>"
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    if min(v for _, v in pts) >= 0:
        lo = max(lo, 0.0)       # a load or temperature axis never goes below zero
    if unit == "%":
        hi = min(hi, 100.0)
    x = lambda t: PAD_L + (t - t0) / (t1 - t0) * (W - PAD_L - PAD_R)
    y = lambda v: PAD_T + (hi - v) / (hi - lo) * (H - PAD_T - PAD_B)
    parts = [f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='{escape(title)} over the last {hours:g} hours'>"]
    for frac in (0, 0.5, 1):   # three horizontal guides with their values
        v = lo + (hi - lo) * frac
        parts.append(f"<line class='g' x1='{PAD_L}' x2='{W - PAD_R}' y1='{y(v):.1f}' y2='{y(v):.1f}'/>"
                     f"<text x='{PAD_L - 6}' y='{y(v) + 4:.1f}' text-anchor='end'>{v:.0f}</text>")
    for frac in (0, 0.25, 0.5, 0.75, 1):
        t = t0 + (t1 - t0) * frac
        anchor = "start" if frac == 0 else "end" if frac == 1 else "middle"
        parts.append(f"<text x='{x(t):.1f}' y='{H - 6}' text-anchor='{anchor}'>{_clock(t)}</text>")
    for seg in segments(pts):
        if len(seg) == 1:   # a lone sample between two gaps: a dot, not an invisible line
            parts.append(f"<circle cx='{x(seg[0][0]):.1f}' cy='{y(seg[0][1]):.1f}' r='1.6' fill='var(--line)'/>")
        else:
            parts.append("<polyline class='l' points='" + " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in seg) + "'/>")
    parts.append("</svg>")
    return f"<h2>{escape(title)} <span class='mute'>({escape(unit)})</span></h2>" + "".join(parts)


def _table(head: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    return ("<table><tr>" + "".join(f"<th>{escape(h)}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>")


def overview(hours: float, now: float) -> str:
    rows = []
    for metric, title, unit in CHARTS:
        r = tools._history(metric, int(hours * 60))
        if "error" in r:
            rows.append([escape(title), "-", "-", "-", "-"])
        else:
            rows.append([escape(title), f"{_num(r['min'])} {escape(unit)}", f"{_num(r['avg'])} {escape(unit)}",
                         f"{_num(r['max'])} {escape(unit)}", f"{_clock(now - r['max_was_minutes_ago'] * 60)}"])
    return "<h2>Summary</h2>" + _table(["", "min", "average", "max", "max at"], rows)


def unusual(hours: float) -> str:
    lines, clean = [], []
    for metric in anomaly.STATE_METRICS:
        r = tools.anomalies(metric, int(hours * 60))
        if "error" in r:
            continue
        if not r["events"]:
            clean.append(metric)
        for e in r["events"]:
            game = f" during {escape(e['during_game'])}" if e["during_game"] else ""
            lines.append([escape(metric), f"<span class='warn'>{escape(e['started'])}</span>{game}",
                          f"{_num(e['duration_minutes'], 0)} min", f"{_num(e['typical_value'])} → {_num(e['most_unusual_value'])}"])
    note = ("<p class='mute'>A statistical check against the previous hours, not a fault diagnosis. Load metrics "
            "(CPU, GPU usage, disks, network) are not checked: they swing with whatever you run.</p>")
    ok = f"<p class='ok'>Nothing unusual in {escape(', '.join(clean))}.</p>" if clean else ""
    if not lines and not clean:
        ok = "<p class='mute'>no data in this period</p>"
    return "<h2>Unusual periods</h2>" + _table(["metric", "when", "lasted", "typical → most unusual"], lines) + ok + note


def disks() -> str:
    rows = []
    for r in tools.disk_forecast(30):
        if "error" in r:
            return "<h2>Disks</h2><p class='mute'>no data</p>"
        days = r.get("days_until_full")
        fill = "not growing" if days is None else f"~{days:.0f} days"
        flag = " <span class='warn'>(few data)</span>" if r["confidence"] == "low" else ""
        growth = r.get("growth_gb_per_day")
        if growth is not None and abs(growth) < 0.05:
            growth = 0.0        # not "-0.0" for a disk that is simply flat"
        rows.append([escape(r["disk"]), f"{_num(r['free_gb'], 0)} GB free of {_num(r['used_gb'] + r['free_gb'], 0)}",
                     f"{_num(growth, 1)} GB/day", fill + flag])
    return "<h2>Disks</h2>" + _table(["disk", "free", "growth", "full in"], rows)


def processes(hours: float) -> str:
    minutes = int(hours * 60)
    cpu = [[escape(p["name"]), f"{_num(p['avg_cpu_percent'])} %"] for p in tools.top_processes("cpu", minutes, 6)]
    mem = [[escape(p["name"]), f"{_num(p['max_rss_mb'], 0)} MB"] for p in tools.top_processes("memory", minutes, 6)]
    return ("<h2>Heaviest processes</h2><table><tr><td style='vertical-align:top;width:50%'>"
            + (_table(["by CPU (average)", ""], cpu) or "<p class='mute'>no data</p>") + "</td><td style='vertical-align:top'>"
            + (_table(["by memory (peak)", ""], mem) or "<p class='mute'>no data</p>") + "</td></tr></table>")


def games(limit: int = 5) -> str:
    rows = []
    for rec in tools.game_sessions(limit):
        if "error" in rec:
            return ""
        r = tools.game_session_report(rec["name"])
        if "error" in r:
            continue
        lim = r.get("limiter_percent_of_frames", {})
        top = max(lim, key=lim.get).upper() if lim else "-"
        rows.append([escape(rec["name"]), escape(rec["recorded"]), _num(r["avg_fps"], 0), _num(r["low1_fps"], 0), top])
    if not rows:
        return ""
    return "<h2>Recent game recordings</h2>" + _table(["recording", "recorded", "avg FPS", "1% low", "limiter"], rows)


def build_report(hours: float = 24, now: float | None = None) -> str:
    now = now or time.time()
    table, _ = tools.METRICS["cpu_percent"]
    with db.connect(tools._db_path) as conn:
        stamps = [t for (t,) in conn.execute(f"SELECT ts FROM {table} WHERE ts >= ? ORDER BY ts", (now - hours * 3600,))]
    recorded = tools._recorded_seconds(stamps) / 3600
    if len(stamps) < 2:
        body = "<section><p>No collected data in this period. Start the collector: <code>pcassist collect</code>.</p></section>"
    else:
        cov = (f"<p class='warn'>The collector recorded only {recorded:.1f} h of these {hours:g} h "
               "(the PC was off or asleep in between); gaps are left blank.</p>") if recorded < hours * 0.8 else ""
        charts = "".join(chart(m, t, u, hours, now) for m, t, u in CHARTS)
        body = "".join(f"<section>{s}</section>" for s in (cov + overview(hours, now), unusual(hours), charts,
                                                          disks(), processes(hours), games()) if s)
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>PC report</title><style>{STYLE}</style></head><body><main><h1>PC report</h1>"
            f"<div class='mute'>last {hours:g} h, up to {_clock(now, '%Y-%m-%d %H:%M')} · generated locally, nothing leaves this machine</div>"
            f"{body}</main></body></html>")
