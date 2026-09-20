"""Unit tests for the "what happened at ..." timeline. Run: python tests/test_timeline.py (or pytest)."""
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from vigil import db, timeline, tools, winhealth

NOW = datetime(2026, 9, 20, 22, 30)


def test_times_are_understood_in_the_usual_shapes():
    p = lambda s: timeline.parse_when(s, NOW)
    assert p("now") == NOW and p("") == NOW and p("сейчас") == NOW
    assert p("14:03") == datetime(2026, 9, 20, 14, 3)
    assert p("23:15") == datetime(2026, 9, 19, 23, 15)                     # later than now today: it means yesterday
    assert p("yesterday 21:30") == datetime(2026, 9, 19, 21, 30) and p("вчера в 21:30") == datetime(2026, 9, 19, 21, 30)
    assert p("2026-09-18 09:05") == datetime(2026, 9, 18, 9, 5) and p("2026-09-18T09:05:30") == datetime(2026, 9, 18, 9, 5, 30)
    assert p("45 minutes ago") == NOW - timedelta(minutes=45) and p("2 часа назад") == NOW - timedelta(hours=2)
    assert p("1.5 hours ago") == NOW - timedelta(minutes=90)
    for junk in ("blah", "25:99", "tomorrow", "12"):
        assert p(junk) is None, junk


def _seeded(now: datetime):
    """A metrics.db around `now`: quiet CPU for 3 h, then a busy stretch; one new process, one new connection, one new
    autostart entry, a gap in the data, an alert in the log and a game recording."""
    root = Path(tempfile.mkdtemp())
    conn = db.connect(root / "t.db")
    end = now.timestamp()
    for i in range(0, 4 * 120):                               # 4 hours, one sample per 30 s, ending at `end`
        ts = end - 30 * (4 * 120 - i)
        if end - 1800 - 1000 < ts < end - 1800 - 60:          # a ~15 minute hole ~30 min ago (longer than the 10 minute gap limit)
            continue
        busy = ts > end - 3000
        conn.execute("INSERT INTO system_metrics (ts, cpu_percent, ram_percent) VALUES (?,?,?)", (ts, 80.0 if busy else 10.0, 40.0))
        conn.execute("INSERT INTO gpu_metrics (ts, idx, temp_c, util_percent) VALUES (?,0,?,?)", (ts, 60.0, 30.0))
        conn.execute("INSERT INTO process_snapshots VALUES (?,?,?,?,?)", (ts, 1, "steady.exe", 5.0, 100.0))
        if ts > end - 1500:
            conn.execute("INSERT INTO process_snapshots VALUES (?,?,?,?,?)", (ts, 2, "newcomer.exe", 40.0, 300.0))
    conn.execute("INSERT INTO process_connections VALUES ('newcomer.exe','out','203.0.113.5',4444,?,?)", (end - 1400, end - 10))
    conn.execute("INSERT INTO autoruns VALUES ('service','Base','base.exe','',?,?)", (end - 90000, end))
    conn.execute("INSERT INTO autoruns VALUES ('scheduled_task','Fresh','fresh.exe','',?,?)", (end - 1300, end))
    conn.commit()
    return root, conn


def test_the_timeline_lines_everything_up_in_time_and_skips_the_baseline():
    root, conn = _seeded(NOW)
    log = [(NOW - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M") + " [medium] Unusual x - detail"]
    rec = [((NOW - timedelta(minutes=8)).timestamp(), NOW.timestamp(), "cs2_run_1")]
    raw = {"crashes": [{"t": (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.0+03:00"), "id": 1000, "a0": "boom.exe"}],
           "defender_events": [{"t": (NOW - timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%S.0+03:00"), "id": 5001}]}
    r = timeline.build(conn, NOW - timedelta(minutes=15), 20, NOW, log, rec, raw)
    times = [e["time"] for e in r["timeline"]]
    assert times == sorted(times)                                                                # chronological
    text = " | ".join(e["text"] for e in r["timeline"])
    assert "newcomer.exe appears in the history for the first time" in text
    assert "connects to 203.0.113.5:4444" in text and "new autostart entry (scheduled task): Fresh" in text
    assert "Base" not in text                                                                    # the baseline is not news
    assert "alert sent [medium]" in text and "a game recording overlaps: cs2_run_1" in text
    assert "Application crash: boom.exe" in text and "real-time protection turned OFF" in text
    assert r["heaviest_processes"][0]["name"] == "newcomer.exe"
    assert r["metrics"]["cpu_percent"]["window_avg"] > r["metrics"]["cpu_percent"]["before_avg_3h"]


def test_a_gap_is_reported_and_an_empty_window_says_so():
    root, conn = _seeded(NOW)
    r = timeline.build(conn, NOW - timedelta(minutes=32), 10, NOW)
    assert any(e["type"] == "gap" and "no data from" in e["text"] for e in r["timeline"])
    empty = timeline.build(conn, NOW - timedelta(days=3), 10, NOW)
    assert empty["samples_in_window"] == 0 and "no metrics were recorded" in empty["summary"] and empty["notes"]


def test_the_window_is_clamped_and_never_reaches_into_the_future():
    root, conn = _seeded(NOW)
    r = timeline.build(conn, NOW, 100000, NOW)
    assert r["window"]["to"] == NOW.strftime("%Y-%m-%d %H:%M")
    assert r["window"]["from"] == (NOW - timedelta(minutes=timeline.MAX_HALF_WINDOW_MIN)).strftime("%Y-%m-%d %H:%M")


def test_the_tool_understands_the_time_and_refuses_junk():
    root, _ = _seeded(datetime.now())
    tools.set_db(root / "t.db")
    real = winhealth.read_raw
    winhealth.read_raw = lambda hours: {}
    try:
        ok = tools.what_happened("20 minutes ago", 15)
        bad = tools.what_happened("blah")
    finally:
        winhealth.read_raw = real
    assert ok["samples_in_window"] > 0 and "timeline" in ok and ok["moment"]
    assert "could not understand" in bad["error"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
