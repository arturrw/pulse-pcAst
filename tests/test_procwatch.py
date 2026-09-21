"""Unit tests for the process behavior check. Run: python tests/test_procwatch.py (or pytest)."""
import tempfile
import time
from pathlib import Path

from pulse import binaries, db, tools

BS = chr(92)


def _db(baseline_hours: float = 30, window_min: int = 60, miner: bool = True, leak: bool = True) -> Path:
    """Baseline: `baseline_hours` of chrome.exe at ~1% CPU, samples 30 s apart. The last `window_min` minutes add a
    'miner.exe' at 40% CPU (never seen before), chrome at 20% (20x its usual), a 'leaker.exe' growing 200 -> 1500 MB
    and a one-shot 'updater.exe' seen once."""
    path = Path(tempfile.mkdtemp()) / "t.db"
    now = time.time()
    rows = []
    n_base, n_win = int(baseline_hours * 120), window_min * 2
    for i in range(n_base):
        rows.append((now - 30 * (n_base + n_win - i) - 1, 1, "chrome.exe", 1.0 + i % 3 * 0.1, 500.0))
    for i in range(n_win):
        ts = now - 30 * (n_win - i)
        rows.append((ts, 1, "chrome.exe", 20.0, 500.0))
        if miner:
            rows.append((ts, 2, "miner.exe", 40.0, 300.0))
        if leak:
            rows.append((ts, 3, "leaker.exe", 0.5, 200.0 + 1300.0 * i / (n_win - 1)))
    rows.append((now - 40, 4, "updater.exe", 0.4, 30.0))
    with db.connect(path) as conn:
        conn.executemany("INSERT INTO process_snapshots (ts, pid, name, cpu_percent, rss_mb) VALUES (?,?,?,?,?)", rows)
    tools.set_db(path)
    return path


def test_flags_new_busy_and_growing_processes():
    _db()
    r = tools.process_watch(65)          # 65, not 60: a margin, the test data starts a minute inside the window
    assert r["confidence"] == "ok" and "warning" not in r
    assert [x["name"] for x in r["new"]] == ["miner.exe", "leaker.exe"]        # sorted by CPU; updater is minor
    assert r["new_minor_count"] == 1
    busy = {x["name"]: x for x in r["busy"]}
    assert busy["chrome.exe"]["usual_cpu_percent_p95"] < 2 and "own usual" in busy["chrome.exe"]["why"]
    assert busy["miner.exe"]["usual_cpu_percent_p95"] is None                  # nothing to compare with
    assert [x["name"] for x in r["growing"]] == ["leaker.exe"] and r["growing"][0]["to_mb"] > 1400
    assert "cannot tell malware" in r["how_it_works"]


def test_short_history_lowers_confidence():
    _db(baseline_hours=3)
    r = tools.process_watch(65)
    assert r["confidence"] == "low" and "weak evidence" in r["warning"]


def test_a_normal_window_finds_nothing():
    _db(miner=False, leak=False)
    r = tools.process_watch(65)
    assert r["new"] == [] and r["growing"] == [] and [x["name"] for x in r["busy"]] == ["chrome.exe"]   # only the 20% chrome


def test_no_data_is_an_error_and_the_tool_is_registered():
    tools.set_db(Path(tempfile.mkdtemp()) / "empty.db")
    assert "pulse collect" in tools.process_watch(65)["error"]
    assert "process_watch" in {f.__name__ for f in tools.TOOLS}


def _files(fake_status: dict[str, str]) -> Path:
    """A metrics.db whose process_exes point at real (empty) files in odd and normal places; `fake_status` maps a
    file name to the signature status the stubbed checker returns."""
    root = Path(tempfile.mkdtemp())
    now = time.time()
    layout = {"svchost.exe": ("Users", "x", "dl_risky"), "setup.exe": ("Users", "x", "dl_risky"),
              "tool.exe": ("Program Files", "Tool"), "patched.exe": ("Program Files", "Other"),
              "real_svchost.exe": ("Windows", "System32")}
    conn = db.connect(root / "t.db")
    for pid, (name, folder) in enumerate(layout.items(), start=10):
        d = root.joinpath(*folder)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"MZ")
        shown = "svchost.exe" if name == "real_svchost.exe" else name
        conn.execute("INSERT INTO process_exes VALUES (?,?,?,?)", (shown, str(d / name), now - 600, now - 30))
        conn.execute("INSERT INTO process_snapshots VALUES (?,?,?,?,?)", (now - 30, pid, shown, 1.0, 50.0))
    conn.commit()
    conn.close()
    tools.set_db(root / "t.db")
    return root


def _watch(fake_status: dict[str, str]) -> dict:
    real, real_dirs = binaries.check_signatures, binaries.RISKY_DIRS
    binaries.RISKY_DIRS = (BS + "dl_risky" + BS,)   # the test folder itself lives under Temp, which the real list flags
    try:
        _files(fake_status)
        binaries.check_signatures = lambda paths: {p: (fake_status.get(Path(p).name, "Valid"), "CN=Test") for p in paths}
        return tools.process_watch(60)
    finally:
        binaries.check_signatures, binaries.RISKY_DIRS = real, real_dirs


def W(*parts):
    """A Windows path built without writing a backslash in the source."""
    return BS.join(parts)


def test_location_rules_on_plain_paths():
    f = binaries.location_flags
    assert f("svchost.exe", W("C:", "Windows", "System32", "svchost.exe")) == []
    assert f("explorer.exe", W("C:", "Windows", "explorer.exe")) == []
    assert "system program name" in f("svchost.exe", W("C:", "Users", "a", "AppData", "Roaming", "svchost.exe"))[0]
    assert "system program name" in f("Explorer.EXE", W("D:", "tools", "explorer.exe"))[0]   # case does not matter
    for risky in (W("C:", "Users", "a", "AppData", "Local", "Temp", "x.exe"), W("C:", "Users", "a", "Downloads", "x.exe"),
                  W("C:", "Users", "Public", "x.exe"), W("C:", "$Recycle.Bin", "S-1", "x.exe"), W("C:", "Windows", "Temp", "x.exe")):
        assert f("x.exe", risky), risky
    for fine in (W("C:", "Program Files", "App", "x.exe"), W("C:", "Users", "a", "AppData", "Local", "Programs", "App", "x.exe"),
                 W("C:", "Python314", "python.exe")):
        assert f("x.exe", fine) == [], fine


def test_file_checks_flag_disguise_broken_signature_and_unsigned_downloads():
    r = _watch({"setup.exe": "NotSigned", "patched.exe": "HashMismatch"})
    flagged = {Path(f["exe"]).name: f for f in r["suspect_files"]}
    assert set(flagged) == {"svchost.exe", "setup.exe", "patched.exe"}          # tool.exe and System32 svchost are fine
    assert all(f["severity"] == "high" for f in flagged.values())
    assert "system program name" in flagged["svchost.exe"]["reasons"][0]
    assert "changed after it was signed" in " ".join(flagged["patched.exe"]["reasons"])
    assert "not signed" in " ".join(flagged["setup.exe"]["reasons"])
    assert "3 file(s)" in r["summary"] and "(3 high)" in r["summary"]


def test_a_signed_file_in_downloads_is_only_medium_and_unsigned_program_files_is_not_flagged():
    r = _watch({"tool.exe": "NotSigned"})
    sev = {Path(f["exe"]).name: f["severity"] for f in r["suspect_files"]}
    assert sev == {"svchost.exe": "high", "setup.exe": "medium"}                  # unsigned tool.exe is common: not flagged


def test_signature_cache_is_reused_until_the_file_changes():
    root = _files({})
    calls = []
    conn = db.connect(root / "t.db")
    f = str(next(root.rglob("tool.exe")))
    checker = lambda paths: calls.append(list(paths)) or {p: ("Valid", "x") for p in paths}
    binaries.ensure_checked(conn, [f], checker)
    binaries.ensure_checked(conn, [f], checker)
    assert len(calls) == 1                                                        # second call: cached
    Path(f).write_bytes(b"MZ changed")
    binaries.ensure_checked(conn, [f], checker)
    assert len(calls) == 2                                                        # size changed: checked again


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
