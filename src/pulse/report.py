"""HTML report of the collected history: one self-contained file (inline SVG, no scripts, no network).

Everything is computed from the database by the same functions the chat tools use, so the report and the
chat always agree. Names that come from the machine (process names) are HTML-escaped."""
import time
from html import escape

from . import alerts, anomaly, db, netstats, tools

CHARTS = [("gpu_temp_c", "GPU temperature", "°C"), ("cpu_percent", "CPU load", "%"),
          ("ram_percent", "RAM used", "%"), ("gpu_util_percent", "GPU load", "%")]
MAX_POINTS = 360          # chart resolution: a day at 30 s is 2880 samples, averaged into buckets


def cells(hours: float) -> int:
    """How many equal time cells the charts of a period are split into (one cell is at least a minute wide). All charts
    of a report use the same cells, so the same cell number means the same moment in every chart."""
    return max(20, min(MAX_POINTS, int(hours * 60)))
W, H, PAD_L, PAD_R, PAD_T, PAD_B = 720, 170, 46, 10, 10, 24

STYLE = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1c1a;--mute:#6b6b66;--line:#2f6fdb;--grid:#e3e3de;--warn:#b25b00;--ok:#2a7d46}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecece8;--mute:#9a9a94;--line:#6ea0ff;--grid:#33332f;--warn:#ffb15c;--ok:#5fcf8b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1000px;margin:0 auto;padding:20px 16px 40px}h1{font-size:22px;margin:0 0 2px}h2{font-size:17px;margin:26px 0 8px}
.mute{color:var(--mute)}section{background:var(--card);border-radius:10px;padding:12px 16px;margin-top:12px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:4px 10px 4px 0;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
th{color:var(--mute);font-weight:500}svg{width:100%;height:auto;display:block}.warn{color:var(--warn)}.ok{color:var(--ok)}
svg text{fill:var(--mute);font-size:11px}svg .g{stroke:var(--grid)}svg .l{fill:none;stroke:var(--line);stroke-width:1.6;stroke-linejoin:round}
td,th{overflow-wrap:anywhere;vertical-align:top}section{overflow-wrap:anywhere}
/* hover on a chart: a guide line, a dot and a label with the time and the value (no script needed) */
svg .hit{fill:transparent}svg .cur,svg .dot,svg .tip{display:none;pointer-events:none}svg .cur{stroke:var(--mute);stroke-dasharray:3 3}svg .dot{fill:var(--line)}
svg .tip rect{fill:var(--ink)}svg .tip text{fill:var(--card);font-size:12px}
svg .pt:hover .cur,svg .pt:hover .dot,svg .pt:hover .tip{display:block}
/* shown inside the app: no second background and no narrow column, the app page already provides them */
body.embed{background:transparent}body.embed main{max-width:none;padding:0 0 24px}body.embed h1{display:none}body.embed section{background:var(--card);border:1px solid var(--grid);border-radius:12px}
"""
# hovering one moment in a chart shows the guide, dot and label of that moment in all charts (pure CSS: no script runs here)
STYLE += "".join(f".charts:has(.c{k}:hover) .c{k} :is(.cur,.dot,.tip){{display:block}}" for k in range(MAX_POINTS))


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


def downsample(points: list[tuple[float, float]], t0: float, t1: float, n: int = MAX_POINTS) -> list[tuple[float, float]]:
    """Average into the n time cells of the period; a cell without samples stays empty, so gaps survive. Each point is
    placed in the middle of its cell."""
    width = (t1 - t0) / n
    buckets: dict[int, list[float]] = {}
    for t, v in points:
        buckets.setdefault(min(n - 1, max(0, int((t - t0) / width))), []).append(v)
    return [(t0 + (k + 0.5) * width, sum(v) / len(v)) for k, v in sorted(buckets.items())]


def chart(metric: str, title: str, unit: str, hours: float, now: float) -> str:
    t0, t1 = now - hours * 3600, now
    n = cells(hours)
    pts = downsample(series(metric, hours, now), t0, t1, n)
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
    long = hours > 24          # over more than a day the time alone is ambiguous: the axis and the hover labels carry the date
    for frac in (0, 0.25, 0.5, 0.75, 1):
        t = t0 + (t1 - t0) * frac
        anchor = "start" if frac == 0 else "end" if frac == 1 else "middle"
        parts.append(f"<text x='{x(t):.1f}' y='{H - 6}' text-anchor='{anchor}'>{_clock(t, '%d %b %H:%M' if long else '%H:%M')}</text>")
    for seg in segments(pts):
        if len(seg) == 1:   # a lone sample between two gaps: a dot, not an invisible line
            parts.append(f"<circle cx='{x(seg[0][0]):.1f}' cy='{y(seg[0][1]):.1f}' r='1.6' fill='var(--line)'/>")
        else:
            parts.append("<polyline class='l' points='" + " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in seg) + "'/>")
    cw = (W - PAD_L - PAD_R) / n
    for t, v in pts:   # one invisible column per cell that has data: hovering it shows that moment (in every chart)
        k = min(n - 1, int((t - t0) / ((t1 - t0) / n)))
        cx = x(t)
        label = f"{_clock(t, '%d %b %H:%M' if long else '%H:%M')} \u00b7 {v:.1f} {unit}"
        bw = 7 * len(label) + 12
        bx = min(max(cx - bw / 2, PAD_L), W - PAD_R - bw)
        parts.append(f"<g class='pt c{k}'><rect class='hit' x='{PAD_L + k * cw:.1f}' y='{PAD_T}' width='{max(cw, 1):.1f}' height='{H - PAD_T - PAD_B}'/>"
                     f"<line class='cur' x1='{cx:.1f}' x2='{cx:.1f}' y1='{PAD_T}' y2='{H - PAD_B}'/>"
                     f"<circle class='dot' cx='{cx:.1f}' cy='{y(v):.1f}' r='3.5'/>"
                     f"<g class='tip'><rect x='{bx:.1f}' y='{PAD_T}' width='{bw}' height='20' rx='5'/>"
                     f"<text x='{bx + bw / 2:.1f}' y='{PAD_T + 14}' text-anchor='middle'>{escape(label)}</text></g></g>")
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


def watch(hours: float) -> str:
    r = tools.process_watch(int(hours * 60))
    if "error" in r:
        return ""
    rows = [[f"<span class='warn'>{escape(x['name'])}</span>", escape("; ".join(x["reasons"])),
             escape(x["signature"]) + (f" ({escape(x['signer'][:40])})" if x["signer"] else ""), escape(x["exe"])]
            for x in r.get("suspect_files", [])]
    net = r.get("network", {})
    rows += [[f"<span class='warn'>{escape(n['name'])}</span>", "suspicious file is using the network",
              escape(", ".join(n["destinations"])), ""] for n in net.get("from_suspicious_files", [])]
    rows += [[escape(n["name"]), f"connects to port {n['port']}", escape("typical of " + n["typical_of"]),
              escape(", ".join(n["destinations"]))] for n in net.get("suspicious_ports", [])]
    rows += [[escape(n["name"]), "started listening for connections", escape(f"ports {', '.join(map(str, n['ports']))}"),
              escape(n["bind"])] for n in net.get("new_listeners", [])]
    rows += [[escape(n["name"]), "talks to a new destination", f"{n['new']} new (usually {n['usual_destinations']})",
              escape(", ".join(n["examples"]))] for n in net.get("new_destinations", [])]
    rows += [[escape(x["name"]), "never recorded before", f"first seen {escape(x['first_seen'])}",
             f"CPU avg {_num(x['avg_cpu_percent'])}%, max mem {_num(x['max_rss_mb'], 0)} MB"] for x in r["new"]]
    rows += [[escape(x["name"]), "far more CPU than usual" if x["usual_cpu_percent_p95"] is not None else "heavy, no history",
              f"avg {_num(x['avg_cpu_percent'])}%", "usually up to " + _num(x["usual_cpu_percent_p95"]) + "%"
              if x["usual_cpu_percent_p95"] is not None else ""] for x in r["busy"]]
    rows += [[escape(x["name"]), "memory keeps growing", f"{_num(x['from_mb'], 0)} → {_num(x['to_mb'], 0)} MB",
              f"{_num(x['mb_per_hour'], 0)} MB/h over {_num(x['hours'])} h"] for x in r["growing"]]
    weak = (f"<p class='warn'>Only {_num(r['baseline_hours'])} h of earlier history to compare with: a name that is "
            "new here may simply not have been recorded yet.</p>") if r["confidence"] == "low" else ""
    body = (_table(["process", "what stands out", "", ""], rows) if rows
            else "<p class='ok'>No process stood out against its own history.</p>")
    minor = f" ({r['new_minor_count']} short-lived new names ignored.)" if r["new_minor_count"] else ""
    return ("<h2>Process watch</h2>" + body + weak
            + f"<p class='mute'>{escape(r['how_it_works'][0].upper() + r['how_it_works'][1:])}. "
              f"Not a malware scan: check anything odd yourself (Task Manager → Open file location, a Defender scan).{escape(minor)}</p>")


def _finding_rows(items: list[dict], title_key: str, detail_fn) -> list[list[str]]:
    rows = []
    for x in items:
        status = (f"<span class='mute'>accepted since {escape(x.get('accepted_since', ''))}"
                  f"{(': ' + escape(x['accepted_note'])) if x.get('accepted_note') else ''}</span>") if x.get("accepted")             else f"<span class='warn'>{escape(x['severity'])}</span>"
        rows.append([escape(str(x[title_key])), escape(detail_fn(x)), status])
    return rows


def health_section(hours: float) -> str:
    r = tools.system_health(int(hours))
    if not r.get("available"):
        return ""
    d = r["defender"]
    line = ""
    if d.get("available"):
        state = "on" if d.get("realtime_protection") else "<span class='warn'>OFF</span>"
        line = (f"<p>Defender: real-time protection {state}, signatures {d.get('signatures_age_days', '?')} days old, "
                f"last full scan {escape(str(d.get('last_full_scan') or 'never'))}.</p>")
    rows = _finding_rows(r["findings"], "title", lambda x: x["detail"])
    body = _table(["finding", "detail", "status"], rows) if rows else "<p class='ok'>Nothing wrong in the Windows logs.</p>"
    return f"<h2>System health (Windows logs, Defender)</h2>{line}{body}<p class='mute'>{escape(r['note'])}</p>"


def startup_section(hours: float) -> str:
    r = tools.startup_changes(int(hours))
    if not r.get("available"):
        return ""
    items = r["new_or_changed"] + r["already_present_but_suspicious"]
    rows = _finding_rows(items, "name", lambda x: f"{x['kind'].replace('_', ' ')}: " + "; ".join(x["reasons"] or ["new entry"]) + f" ({x['command'][:80]})")
    known = sum(r["entries_known"].values())
    body = _table(["entry", "what stands out", "status"], rows) if rows else         f"<p class='ok'>No new or suspicious autostart entries ({known} entries known since {escape(r['baseline_at'])}).</p>"
    return f"<h2>Startup changes</h2>{body}<p class='mute'>Run keys, startup folders, scheduled tasks and services; only changes after the first snapshot are reported.</p>"


def traffic_section() -> str:
    """The latest `pulse netstats` measurement, if it is not older than a week."""
    with db.connect(tools._db_path) as conn:
        ts = conn.execute("SELECT MAX(ts) FROM net_traffic").fetchone()[0]
        if ts is None or time.time() - ts > 7 * 86400:
            return ""
        rows = conn.execute("SELECT name, sent, received, seconds, top_destination FROM net_traffic WHERE ts = ? "
                            "ORDER BY sent DESC LIMIT 8", (ts,)).fetchall()
    table = _table(["process", "sent", "received", "biggest destination"],
                   [[escape(n), netstats.human(sent), netstats.human(recv), escape(top or "")] for n, sent, recv, _, top in rows])
    return (f"<h2>Last traffic measurement</h2><p class='mute'>{_clock(ts, '%Y-%m-%d %H:%M')}, {rows[0][3]:.0f} s, public addresses "
            f"only. One short window: a browser uploading a file is normal.</p>{table}")


def alerts_section() -> str:
    lines = alerts.recent_log(tools._db_path)
    if not lines:
        return ""
    return ("<h2>Recent alerts</h2><table>" + "".join(f"<tr><td>{escape(x)}</td></tr>" for x in lines) + "</table>")


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
        body = "<section><p>No collected data in this period. Start the collector: <code>pulse collect</code>.</p></section>"
    else:
        cov = (f"<p class='warn'>The collector recorded only {recorded:.1f} h of these {hours:g} h "
               "(the PC was off or asleep in between); gaps are left blank.</p>") if recorded < hours * 0.8 else ""
        charts = "<div class='charts'>" + "".join(chart(m, t, u, hours, now) for m, t, u in CHARTS) + "</div>"
        body = "".join(f"<section>{s}</section>" for s in (cov + overview(hours, now), unusual(hours), alerts_section(), health_section(hours), startup_section(hours), watch(hours), traffic_section(), charts,
                                                          disks(), processes(hours), games()) if s)
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>PC report</title><style>{STYLE}</style></head><body><main><h1>PC report</h1>"
            f"<div class='mute'>last {hours:g} h, up to {_clock(now, '%Y-%m-%d %H:%M')} · generated locally, nothing leaves this machine</div>"
            f"{body}</main></body></html>")
