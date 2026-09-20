"""Unit tests for the alert checks, the cooldown and the notification log. Run: python tests/test_alerts.py (or pytest)."""
import json
import tempfile
import time
from pathlib import Path

from pcassist import alerts, binaries, db, tools


def _db(*, gpu_temp: float = 60.0, last_sample_ago: float = 30.0, disk_used: float = 400.0, ram_tail: float | None = None,
        samples: int = 400) -> Path:
    """`samples` readings 30 s apart ending `last_sample_ago` seconds ago; RAM ~50% (the last 30 at ram_tail)."""
    path = Path(tempfile.mkdtemp()) / "t.db"
    end = time.time() - last_sample_ago
    with db.connect(path) as conn:
        for i in range(samples):
            ts = end - 30 * (samples - 1 - i)
            ram = ram_tail if ram_tail is not None and i >= samples - 30 else 50.0 + i % 3 * 0.1
            conn.execute("INSERT INTO system_metrics (ts, cpu_percent, ram_percent, ram_used_mb) VALUES (?,?,?,?)",
                         (ts, 10.0, ram, ram * 300))
            conn.execute("INSERT INTO gpu_metrics (ts, idx, util_percent, temp_c) VALUES (?,0,?,?)", (ts, 30.0, gpu_temp))
        conn.execute("INSERT INTO disk_usage VALUES (?,?,?,?)", (end, "C:", 1000.0, disk_used))
    tools.set_db(path)
    return path


def _keys(found):
    return {a.key for a in found}


def test_a_quiet_machine_raises_nothing():
    _db()
    assert alerts.collect_alerts() == []


def test_hot_gpu_needs_a_sustained_high_temperature():
    _db(gpu_temp=90.0)
    found = alerts.collect_alerts()
    assert _keys(found) == {"gpu-hot"} and found[0].severity == "high"
    path = _db(gpu_temp=60.0)                       # one hot reading among cool ones is not an alert
    with db.connect(path) as conn:
        conn.execute("UPDATE gpu_metrics SET temp_c = 95 WHERE ts = (SELECT MAX(ts) FROM gpu_metrics)")
    assert "gpu-hot" not in _keys(alerts.collect_alerts())


def test_stopped_collector_is_reported_but_a_fresh_sample_is_not():
    _db(last_sample_ago=45 * 60)
    assert "collector-stale" in _keys(alerts.collect_alerts())
    _db(last_sample_ago=60)
    assert "collector-stale" not in _keys(alerts.collect_alerts())


def test_nearly_full_disk_is_high():
    _db(disk_used=990.0)                             # 10 GB free
    found = alerts.collect_alerts()
    assert "disk-low-C:" in _keys(found) and {a.severity for a in found if a.key == "disk-low-C:"} == {"high"}


def test_unusual_ram_period_is_reported_once_it_is_recent():
    _db(ram_tail=85.0)
    found = [a for a in alerts.collect_alerts() if a.key.startswith("unusual-ram_percent")]
    assert len(found) == 1 and "85" in found[0].body
    assert not [a for a in alerts.collect_alerts() if "ram_used_mb" in a.key]      # one event, one alert (not also in MB)


def test_cooldown_skips_repeats_and_lets_them_through_later():
    a = alerts.Alert("k", "high", "t", "b")
    now = 1_000_000.0
    assert alerts.select_new([a], {}, now) == [a]
    assert alerts.select_new([a], {"k": now - 3600}, now) == []                       # sent an hour ago
    assert alerts.select_new([a], {"k": now - 7 * 3600}, now) == [a]                  # high: repeats after 6 h
    m = alerts.Alert("m", "medium", "t", "b")
    assert alerts.select_new([m], {"m": now - 7 * 3600}, now) == []                   # medium waits 24 h


def test_run_once_notifies_logs_remembers_and_dry_run_touches_nothing():
    path = _db(gpu_temp=90.0)
    sent = []
    dry = alerts.run_once(path, notify_fn=lambda t, b: sent.append(t) or True, dry_run=True)
    assert [a.key for a in dry] == ["gpu-hot"] and sent == [] and not (path.parent / "alerts_state.json").exists()
    first = alerts.run_once(path, notify_fn=lambda t, b: sent.append(t) or True)
    again = alerts.run_once(path, notify_fn=lambda t, b: sent.append(t) or True)
    assert [a.key for a in first] == ["gpu-hot"] and again == [] and sent == ["GPU is very hot"]
    assert "gpu-hot" in json.loads((path.parent / "alerts_state.json").read_text())
    log = (path.parent / "alerts.log").read_text(encoding="utf-8")
    assert "[high] GPU is very hot" in log and "could not be shown" not in log


def test_a_failed_notification_is_still_logged():
    path = _db(gpu_temp=90.0)
    alerts.run_once(path, notify_fn=lambda t, b: False)
    assert "notification could not be shown" in (path.parent / "alerts.log").read_text(encoding="utf-8")
    assert alerts.recent_log(path)[0].startswith(time.strftime("%Y-%m-%d"))


def test_a_broken_check_does_not_hide_the_others():
    _db(gpu_temp=90.0)
    real = alerts.check_disks
    alerts.check_disks = lambda now: 1 / 0
    try:
        found = alerts.collect_alerts()
    finally:
        alerts.check_disks = real
    assert "gpu-hot" in _keys(found) and any(a.key.startswith("check-failed") for a in found)


def test_disguised_file_becomes_a_high_alert():
    path = _db()
    root = Path(tempfile.mkdtemp())
    bad = root.joinpath("dl_risky", "svchost.exe")
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"MZ")
    with db.connect(path) as conn:
        conn.execute("INSERT INTO process_exes VALUES (?,?,?,?)", ("svchost.exe", str(bad), time.time() - 600, time.time() - 30))
        conn.execute("INSERT INTO process_snapshots VALUES (?,?,?,?,?)", (time.time() - 30, 77, "svchost.exe", 1.0, 40.0))
    real, real_dirs = binaries.check_signatures, binaries.RISKY_DIRS
    binaries.check_signatures = lambda paths: {p: ("NotSigned", "") for p in paths}
    binaries.RISKY_DIRS = (chr(92) + "dl_risky" + chr(92),)
    try:
        found = [a for a in alerts.collect_alerts() if a.key.startswith("file-")]
    finally:
        binaries.check_signatures, binaries.RISKY_DIRS = real, real_dirs
    assert len(found) == 1 and found[0].severity == "high" and "system program name" in found[0].body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
