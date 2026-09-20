"""Unit tests for the HTML report. Run: python tests/test_report.py (or pytest)."""
import re
import tempfile
import time
from pathlib import Path

from pcassist import db, persistence, report, tools, winhealth


# These tests must not read this machine's event logs or autostart entries: both sources are empty here.
winhealth.read_raw = lambda hours: {}
persistence.read_items = lambda: []


def _db(samples: int = 400, jump: float | None = 80.0, proc: str = "chrome.exe") -> Path:
    """`samples` readings 30 s apart ending now: RAM ~50% (the last 30 at `jump`), a GPU temperature and a process."""
    path = Path(tempfile.mkdtemp()) / "t.db"
    now = time.time()
    with db.connect(path) as conn:
        for i in range(samples):
            ts = now - 30 * (samples - i)
            ram = jump if jump is not None and i >= samples - 30 else 50.0 + i % 3 * 0.1
            conn.execute("INSERT INTO system_metrics (ts, cpu_percent, ram_percent, ram_used_mb) VALUES (?,?,?,?)",
                         (ts, 20.0 + i % 5, ram, ram * 300))
            conn.execute("INSERT INTO gpu_metrics (ts, idx, util_percent, temp_c) VALUES (?,0,?,?)", (ts, 30.0, 55.0))
        conn.execute("INSERT INTO process_snapshots (ts, pid, name, cpu_percent, rss_mb) VALUES (?,?,?,?,?)",
                     (now - 60, 7, proc, 12.0, 900.0))
        conn.execute("INSERT INTO disk_usage VALUES (?,?,?,?)", (now - 60, "C:\\", 1000.0, 400.0))
    tools.set_db(path)
    return path


def test_report_has_all_sections_and_no_external_resources():
    _db()
    html = report.build_report(2)
    for part in ("<h1>PC report</h1>", "Summary", "Unusual periods", "Process watch", "GPU temperature", "Disks", "Heaviest processes"):
        assert part in html, part
    assert html.count("<svg") == 4 and "<script" not in html.lower()
    assert not re.search(r"(?:src|href)=['\"]?https?://", html)


def test_unusual_period_is_listed():
    _db(jump=80.0)
    html = report.build_report(2)
    clean = html.split("Nothing unusual in")[1].split("</p>")[0]      # the metrics without findings
    assert "gpu_temp_c" in clean and "ram_percent" not in clean        # RAM jumped: it is in the table, not here
    assert "80.0" in html.split("Unusual periods")[1].split("Nothing unusual in")[0]


def test_machine_supplied_names_are_escaped():
    _db(proc="<script>alert(1)</script>")
    html = report.build_report(2)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_empty_database_still_produces_a_page():
    tools.set_db(Path(tempfile.mkdtemp()) / "empty.db")
    html = report.build_report(24)
    assert "No collected data" in html and "pcassist collect" in html and html.startswith("<!doctype html>")


def test_a_gap_splits_the_line_and_a_short_recording_is_reported():
    pts = [(i * 30.0, 1.0) for i in range(20)] + [(36000 + i * 30.0, 2.0) for i in range(20)]
    assert [len(s) for s in report.segments(pts)] == [20, 20]
    _db(samples=100)
    assert "recorded only" in report.build_report(24)    # 100 samples = under an hour of a 24 h period


def test_health_and_startup_sections_show_findings_and_mark_accepted_ones():
    from datetime import datetime, timedelta

    from pcassist import ack

    path = _db()
    iso = lambda m: (datetime.now() - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S.0+03:00")
    real_raw, real_items = winhealth.read_raw, persistence.read_items
    winhealth.read_raw = lambda hours: {"defender": {"service": True, "antivirus": True, "realtime": False, "tamper_protected": True,
                                                     "signatures": iso(60), "quick_scan": iso(600), "full_scan": None}}
    try:
        html = report.build_report(2)
        assert "System health" in html and "real-time protection <span class='warn'>OFF</span>" in html
        assert "Defender real-time protection is OFF" in html and "<span class='warn'>high</span>" in html
        ack.acknowledge(path, "defender-realtime-off", "on purpose")
        html = report.build_report(2)
        assert "accepted since" in html and "on purpose" in html and "<span class='warn'>high</span>" not in html.split("System health")[1].split("Startup changes")[0]
        # startup: an old baseline, then a new hidden-script entry
        conn = db.connect(path)
        base = {"kind": "service", "name": "Known", "command": "C:" + chr(92) + "Windows" + chr(92) + "k.exe", "detail": ""}
        persistence.snapshot(conn, now=time.time() - 3600, reader=lambda: [base])
        bad = {"kind": "scheduled_task", "name": chr(92) + "Sneaky", "detail": "",
               "command": "wscript.exe //B " + chr(34) + "C:" + chr(92) + "x" + chr(92) + "run.vbs" + chr(34)}
        persistence.snapshot(conn, now=time.time() - 60, reader=lambda: [base, bad])
        html = report.build_report(2)
    finally:
        winhealth.read_raw, persistence.read_items = real_raw, real_items
    assert "Startup changes" in html and "Sneaky" in html and "script host" in html


def test_chart_axes_never_go_below_zero_for_percent():
    _db()
    html = report.build_report(2)
    labels = [int(v) for v in re.findall(r"<text x='40'[^>]*>(-?\d+)</text>", html)]
    assert labels and min(labels) >= 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
