"""Unit tests for the morning digest. Run: python tests/test_digest.py (or pytest)."""
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from pulse import ack, db, digest, persistence, tools, winhealth

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


def _two_weeks(cpu_temps=(50.0, 60.0), gpu_temps=(55.0, 55.0), used=(400.0, 403.0, 415.0), hours_per_week=48) -> Path:
    """Samples every 10 min: `hours_per_week` h in each of the last two weeks, disk used at the start, middle and end."""
    path = Path(tempfile.mkdtemp()) / "t.db"
    now = time.time()
    with db.connect(path) as conn:
        for week, (ct, gt) in enumerate(zip(cpu_temps, gpu_temps)):   # 0 = last week, 1 = this week
            start = now - (2 - week) * digest.WEEK + 3600
            for i in range(hours_per_week * 6 + 1):
                ts = start + 600 * i
                conn.execute("INSERT INTO system_metrics (ts, cpu_percent, cpu_temp_c) VALUES (?,?,?)", (ts, 10.0, ct))
                conn.execute("INSERT INTO gpu_metrics (ts, idx, temp_c) VALUES (?,0,?)", (ts, gt))
        for t, u in zip((now - 2 * digest.WEEK - 60, now - digest.WEEK - 60, now - 60), used):
            conn.execute("INSERT INTO disk_usage VALUES (?,?,?,?)", (t, "C:\\", 1000.0, u))
    tools.set_db(path)
    return path


def test_the_week_is_compared_with_the_one_before():
    path = _two_weeks()
    conn = db.connect(path)
    base = {"kind": "service", "name": "Known", "command": "C:\\Windows\\k.exe", "detail": ""}
    user_svc = {"kind": "service", "name": "CDPUserSvc_a45ae", "command": "svchost.exe -k UnistackSvcGroup", "detail": ""}
    persistence.snapshot(conn, now=time.time() - 13 * 86400, reader=lambda: [base, user_svc])
    # the next logon: the per-user service comes back under a new suffix and Known gets an update; neither is new
    persistence.snapshot(conn, now=time.time() - 2 * 86400, reader=lambda: [
        {**base, "command": "C:\\Windows\\k2.exe"}, {**user_svc, "name": "CDPUserSvc_b6638"},
        {**base, "name": "NewThing", "command": "C:\\x.exe"}])
    w = digest.build_week()
    text = "\n".join(w["lines"])
    assert "Processor temperature: average 60 °C (last week 50), peak 60 °C (last week 50)" in text
    assert "Graphics card temperature: average 55 °C (last week 55)" in text
    assert "Disk C: +12.0 GB this week (last week +3.0 GB)" in text
    assert "New autostart entries: 1 this week (NewThing), 0 last week" in text      # the baseline is not "new"
    assert "Recorded 48 h this week, 48 h last week" in text
    assert w["body"].startswith("CPU 60 °C (50)") and "C: +12 GB (+3)" in w["body"] and len(w["body"]) <= digest.MAX_BODY


def test_a_week_without_a_number_leaves_it_out_and_says_the_comparison_is_weak():
    _two_weeks(hours_per_week=5)
    with db.connect(tools._db_path) as conn:
        conn.execute("DELETE FROM disk_usage WHERE ts < ?", (time.time() - digest.WEEK - 3600,))   # no disk figure two weeks ago
        conn.execute("DELETE FROM gpu_metrics WHERE ts < ?", (time.time() - digest.WEEK,))
    w = digest.build_week()
    assert not any(line.startswith(("Disk", "Graphics")) for line in w["lines"])
    assert w["body"].startswith("Only 5 h recorded this week and 5 h last week")


def test_the_scheduled_digest_adds_the_weekly_comparison_once_a_week_and_only_with_enough_history():
    path = _two_weeks()
    shown = []
    d = digest.run(path, notify_fn=lambda t, b: shown.append(t) or True, refresh_report=False, weekly_if_due=True)
    assert "week" in d and shown[-1] == "This week against the last one"
    assert "[week]" in (path.parent / "digest.log").read_text(encoding="utf-8")
    d = digest.run(path, notify_fn=lambda t, b: shown.append(t) or True, refresh_report=False, weekly_if_due=True)
    assert "week" not in d and len(shown) == 3          # the next day: only the daily one
    assert digest.week_due(path, now=time.time() + 6.6 * 86400) is False   # no history that far ahead: nothing to compare
    d = digest.run(_two_weeks(hours_per_week=5), notify_fn=lambda t, b: True, refresh_report=False, weekly_if_due=True)
    assert "week" not in d                               # too little recorded in each week
    d = digest.run(_two_weeks(), notify_fn=lambda t, b: True, refresh_report=False)
    assert "week" not in d                               # "run the summary now" from the app sends only the daily one


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
