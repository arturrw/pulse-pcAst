"""Unit tests for the HTML report. Run: python tests/test_report.py (or pytest)."""
import re
import tempfile
import time
from pathlib import Path

from pulse import db, persistence, report, tools, winhealth


# These tests must not read this machine's event logs or autostart entries: both sources are empty here.
winhealth.read_raw = lambda hours: {}
winhealth.read_defender_policy = lambda: {}
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
    assert "No collected data" in html and "pulse collect" in html and html.startswith("<!doctype html>")


def test_a_gap_splits_the_line_and_a_short_recording_is_reported():
    pts = [(i * 30.0, 1.0) for i in range(20)] + [(36000 + i * 30.0, 2.0) for i in range(20)]
    assert [len(s) for s in report.segments(pts)] == [20, 20]
    _db(samples=100)
    assert "recorded only" in report.build_report(24)    # 100 samples = under an hour of a 24 h period


def test_health_and_startup_sections_show_findings_and_mark_accepted_ones():
    from datetime import datetime, timedelta

    from pulse import ack

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


def test_the_last_traffic_measurement_is_shown_only_while_it_is_recent():
    path = _db()
    assert "Last traffic measurement" not in report.build_report(2)
    conn = db.connect(path)
    ts = time.time() - 60                                     # one measurement: all its rows share the time
    conn.execute("INSERT INTO net_traffic VALUES (?,?,?,?,?,?)", (ts, "uploader.exe", 40 * 1024 * 1024, 2048, 60.0, "8.8.8.8:443"))
    conn.execute("INSERT INTO net_traffic VALUES (?,?,?,?,?,?)", (ts, "<b>evil</b>.exe", 10, 10, 60.0, ""))
    conn.commit()
    html = report.build_report(2)
    assert "Last traffic measurement" in html and "uploader.exe" in html and "40.0 MB" in html and "8.8.8.8:443" in html
    assert "<b>evil</b>" not in html and "&lt;b&gt;evil" in html                                  # names are escaped
    conn.execute("UPDATE net_traffic SET ts = ts - ?", (8 * 86400,))
    conn.commit()
    assert "Last traffic measurement" not in report.build_report(2)                                # older than a week: not shown


def test_chart_axes_never_go_below_zero_for_percent():
    _db()
    html = report.build_report(2)
    labels = [int(v) for v in re.findall(r"<text x='40'[^>]*>(-?\d+)</text>", html)]
    assert labels and min(labels) >= 0


def test_every_chart_sample_has_a_hover_label_with_time_and_value():
    _db(samples=100)
    html = report.build_report(2)
    assert html.count("class='pt c") >= 4 * 50                       # one hoverable column per plotted sample, in all four charts
    assert re.search(r"class='tip'.*?>\d\d:\d\d · 55\.0 °C<", html)  # the label carries the time and the value with its unit
    assert "<script" not in html                                     # done with CSS only: the report still runs no script


def test_a_period_longer_than_a_day_puts_dates_on_the_axis_and_in_the_hover_labels():
    _db(samples=100)
    assert re.search(r">\d\d [A-Z][a-z]{2} \d\d:\d\d<", report.build_report(48))       # axis: "22 Sep 14:00"
    assert re.search(r"class='tip'.*?>\d\d [A-Z][a-z]{2} \d\d:\d\d · ", report.build_report(48))
    assert not re.search(r">\d\d [A-Z][a-z]{2} \d\d:\d\d<", report.build_report(2))     # within a day the time alone is enough


def test_the_charts_share_time_cells_so_hovering_one_moment_shows_it_in_every_chart():
    _db(samples=100)
    html = report.build_report(2)
    charts = html[html.index("<div class='charts'>"):]
    by_chart = [set(re.findall(r"class='pt (c\d+)'", part)) for part in charts.split("<svg")[1:5]]
    assert len(by_chart) == 4 and all(by_chart)
    assert by_chart[0] & by_chart[1] & by_chart[2] & by_chart[3]                   # the same moment has the same cell in all four
    assert ".charts:has(.c0:hover) .c0 :is(.cur,.dot,.tip)" in html                # and hovering it lights that cell everywhere
    assert report.cells(1) == 60 and report.cells(24) == 360 and report.cells(0.1) == 20


def test_sparse_samples_do_not_leave_holes_between_the_hover_columns():
    path = Path(tempfile.mkdtemp()) / "t.db"
    now = time.time()
    with db.connect(path) as conn:
        for i in range(288):                                                 # a day of samples, one every 5 minutes
            ts = now - 300 * (288 - i)
            conn.execute("INSERT INTO system_metrics (ts, cpu_percent, ram_percent, ram_used_mb) VALUES (?,?,?,?)", (ts, 20.0 + i % 5, 50.0, 15000.0))
            conn.execute("INSERT INTO gpu_metrics (ts, idx, util_percent, temp_c) VALUES (?,0,?,?)", (ts, 30.0, 55.0))
    tools.set_db(path)
    html = report.build_report(24)
    ids = sorted(int(x) for x in re.findall(r"class='pt c(\d+)'", html.split("<svg")[1]))
    assert len(ids) > 100 and ids == list(range(ids[0], ids[-1] + 1))      # a cell per 7.5 minutes here, none of them empty
    assert report.cells(24, 300) < report.cells(24, 30) == 360


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
