"""Unit tests for the morning digest. Run: python tests/test_digest.py (or pytest)."""
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from vigil import ack, db, digest, persistence, tools, winhealth

# Nothing here may read this machine's event logs, registry or autostart entries.
winhealth.read_raw = lambda hours: {}
winhealth.read_defender_policy = lambda: {}
persistence.read_items = lambda: []


def _db(hours_recorded: float = 24.0, disk_used: float = 400.0) -> Path:
    path = Path(tempfile.mkdtemp()) / "t.db"
    end = time.time()
    n = int(hours_recorded * 120)
    with db.connect(path) as conn:
        for i in range(n):
            ts = end - 30 * (n - i)
            conn.execute("INSERT INTO system_metrics (ts, cpu_percent, ram_percent, ram_used_mb) VALUES (?,?,?,?)", (ts, 10.0, 40.0, 12000.0))
            conn.execute("INSERT INTO gpu_metrics (ts, idx, util_percent, temp_c) VALUES (?,0,?,?)", (ts, 20.0, 55.0))
        conn.execute("INSERT INTO disk_usage VALUES (?,?,?,?)", (end, "C:", 1000.0, disk_used))
    tools.set_db(path)
    return path


def _raw_defender_off() -> dict:
    iso = lambda m: (datetime.now() - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S.0+03:00")
    return {"power": [], "hardware": [], "disk": [], "display": [], "crashes": [], "defender_events": [], "threats": [],
            "defender": {"service": True, "antivirus": True, "realtime": False, "tamper_protected": True,
                         "signatures": iso(60), "quick_scan": iso(600), "full_scan": None}}


def test_a_quiet_day_says_nothing_new_and_gives_the_numbers():
    _db()
    d = digest.build()
    assert d["status"] == "quiet" and d["todo"] == [] and "nothing new" in d["title"].lower()
    assert any("Disks: least free is C: with 600 GB" in line for line in d["lines"])
    assert any("Recorded 24 of the last 24 h" in line for line in d["lines"])
    assert "Nothing new needs attention" in d["body"]


def test_open_findings_make_the_day_worth_a_look_and_accepted_ones_do_not():
    path = _db()
    real = winhealth.read_raw
    winhealth.read_raw = lambda hours: _raw_defender_off()
    try:
        d = digest.build()
        assert d["status"] == "look" and "1 thing(s) to look at" in d["title"] and "Defender real-time protection is OFF" in d["body"]
        ack.acknowledge(path, "defender-realtime-off", "mine")
        d = digest.build()
    finally:
        winhealth.read_raw = real
    assert d["status"] == "quiet" and any("1 accepted by you" in line for line in d["lines"])     # counted, not hidden


def test_a_new_hidden_script_in_autostart_and_a_nearly_full_disk_are_listed():
    _db(disk_used=990.0)
    conn = db.connect(tools._db_path)
    base = {"kind": "service", "name": "Known", "command": "C:" + chr(92) + "Windows" + chr(92) + "k.exe", "detail": ""}
    bad = {"kind": "scheduled_task", "name": chr(92) + "Updater", "detail": "",
           "command": "wscript.exe //B " + chr(34) + "C:" + chr(92) + "x" + chr(92) + "run.vbs" + chr(34)}
    persistence.snapshot(conn, now=time.time() - 3600, reader=lambda: [base])
    persistence.snapshot(conn, now=time.time() - 60, reader=lambda: [base, bad])
    d = digest.build()
    assert d["status"] == "look" and any("new autostart entry" in t and "Updater" in t for t in d["todo"])
    assert any("disk C: is almost full (10 GB free)" in t for t in d["todo"])


def test_a_short_recording_is_mentioned_and_the_body_fits_a_notification():
    _db(hours_recorded=3)
    d = digest.build()
    assert "recorded only 3 h" in d["body"] and len(d["body"]) <= digest.MAX_BODY


def test_dry_run_touches_nothing_and_a_real_run_shows_logs_and_refreshes_the_report():
    path = _db()
    shown = []
    dry = digest.run(path, notify_fn=lambda t, b: shown.append(t) or True, dry_run=True)
    assert dry["status"] == "quiet" and shown == [] and not (path.parent / "digest.log").exists()
    d = digest.run(path, notify_fn=lambda t, b: shown.append((t, b)) or True)
    assert d["shown"] is True and len(shown) == 1 and shown[0][0] == d["title"]
    assert "[quiet]" in (path.parent / "digest.log").read_text(encoding="utf-8")
    report = Path(d["report"])
    assert report.exists() and "PC report" in report.read_text(encoding="utf-8")


def test_a_notification_that_cannot_be_shown_is_still_logged():
    path = _db()
    d = digest.run(path, notify_fn=lambda t, b: False, refresh_report=False)
    assert d["shown"] is False and "digest.log" in [p.name for p in path.parent.iterdir()]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
