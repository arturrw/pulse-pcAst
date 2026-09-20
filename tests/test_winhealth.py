"""Unit tests for the Windows event log / Defender health check. Run: python tests/test_winhealth.py (or pytest)."""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from pcassist import ack, tools, winhealth

NOW = datetime(2026, 9, 20, 22, 0)


def _t(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.0000000+03:00")


def _defender(realtime=True, service=True, sig_days=0.5) -> dict:
    return {"service": service, "antivirus": service, "realtime": realtime, "tamper_protected": True,
            "signatures": _t(sig_days * 24), "quick_scan": _t(5), "full_scan": None}


def _raw(**parts) -> dict:
    base = {"power": [], "hardware": [], "disk": [], "display": [], "crashes": [], "defender_events": [], "threats": [],
            "defender": _defender()}
    base.update(parts)
    return base


def test_a_healthy_machine_has_no_findings():
    r = winhealth.analyze(_raw(), NOW)
    assert r["available"] and r["findings"] == [] and r["summary"] == "nothing wrong in the Windows logs"
    assert r["defender"]["realtime_protection"] is True and r["defender"]["last_full_scan"] is None


def test_one_shutdown_reported_by_two_events_counts_once_and_bluescreen_is_high():
    power = [{"t": _t(3), "id": 41}, {"t": _t(3), "id": 6008}, {"t": _t(9), "id": 1001}]
    r = winhealth.analyze(_raw(power=power), NOW)
    f = {x["id"]: x for x in r["findings"]}
    assert "1 time(s)" in f["unexpected-shutdown"]["detail"] and f["unexpected-shutdown"]["severity"] == "medium"
    assert f["bugcheck"]["severity"] == "high" and r["findings"][0]["id"] == "bugcheck"      # high first


def test_defender_off_or_stopped_is_high_and_old_signatures_are_flagged():
    off = {x["id"]: x for x in winhealth.analyze(_raw(defender=_defender(realtime=False)), NOW)["findings"]}
    assert off["defender-realtime-off"]["severity"] == "high"
    stopped = {x["id"] for x in winhealth.analyze(_raw(defender=_defender(service=False)), NOW)["findings"]}
    assert "defender-not-running" in stopped and "defender-realtime-off" not in stopped     # not both for one cause
    old = {x["id"] for x in winhealth.analyze(_raw(defender=_defender(sig_days=9)), NOW)["findings"]}
    assert "defender-signatures-old" in old


def test_repeated_detections_are_high_and_the_user_name_is_removed_from_paths():
    threats = [{"t": _t(h), "name": "Some.Threat", "resource": "file:_C:" + chr(92) + "Users" + chr(92) + "alice" + chr(92) + "x.exe"}
               for h in range(6)]
    r = winhealth.analyze(_raw(threats=threats), NOW)
    f = r["findings"][0]
    assert f["id"] == "defender-threat:Some.Threat" and f["severity"] == "high" and "6 time(s)" in f["detail"]
    assert "alice" not in f["detail"] and "<user>" in f["detail"]


def test_crash_lists_group_by_app_and_only_repeats_become_findings():
    crashes = [{"t": _t(h), "id": 1000, "a0": "game.exe", "a3": "d3d11.dll"} for h in range(5)] + \
              [{"t": _t(1), "id": 1002, "a0": "rare.exe"}]
    r = winhealth.analyze(_raw(crashes=crashes), NOW)
    assert [c["app"] for c in r["app_crashes"]] == ["game.exe", "rare.exe"] and r["app_crashes"][1]["hangs"] == 1
    assert [x["id"] for x in r["findings"]] == ["crash:game.exe"]


def test_powershell_json_quirks_are_handled():
    # a one-element array comes back as a bare object, an empty one as null
    r = winhealth.analyze(_raw(hardware={"t": _t(2), "id": 18, "p": "WHEA"}, disk=None, power=None), NOW)
    assert [x["id"] for x in r["findings"]] == ["whea"]


def test_no_powershell_means_unavailable_not_healthy():
    assert winhealth.analyze({}, NOW)["available"] is False
    assert winhealth.health(24, reader=lambda h: {})["available"] is False


def test_accepted_findings_are_marked_and_not_counted_by_the_tool():
    db = Path(tempfile.mkdtemp()) / "t.db"
    tools.set_db(db)
    real = winhealth.read_raw
    winhealth.read_raw = lambda hours: _raw(defender=_defender(realtime=False))
    try:
        assert tools.system_health(24)["findings_high"] == 1
        ack.acknowledge(db, "defender-realtime-off", "on purpose")
        r = tools.system_health(24)
    finally:
        winhealth.read_raw = real
    f = r["findings"][0]
    assert f["accepted"] and f["accepted_note"] == "on purpose" and r["findings_high"] == 0 and r["accepted_count"] == 1
    assert "1 accepted" in r["summary"] and "0 open" in r["summary"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
