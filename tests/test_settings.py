"""Settings validation, quiet hours, the alert filter, and the plain-language texts."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vigil import alerts, explain, settings  # noqa: E402


def test_bad_values_are_refused_and_good_ones_kept():
    for bad in ({"digest_time": "25:00"}, {"digest_time": "9:00"}, {"alerts_interval": 7}, {"alerts_interval": True},
                {"alerts_min_severity": "low"}, {"quiet_on": "yes"}, {"nope": 1}, {"collect_interval": "30; calc"}):
        try:
            settings.clean(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert settings.clean({"digest_time": "07:45", "quiet_on": True, "alerts_interval": 30}) == {"digest_time": "07:45", "quiet_on": True, "alerts_interval": 30}


def test_settings_persist_and_a_damaged_file_falls_back_to_defaults():
    with tempfile.TemporaryDirectory() as d:
        assert settings.load(d) == settings.DEFAULTS
        settings.save(d, {"digest_time": "10:30"})
        assert settings.load(d)["digest_time"] == "10:30" and settings.load(d)["collect_interval"] == 30
        (Path(d) / "settings.json").write_text('{"digest_time": "bad", "collect_interval": 60}', encoding="utf-8")
        assert settings.load(d)["digest_time"] == "09:00"          # one bad value drops the file back to the defaults
        (Path(d) / "settings.json").write_text("not json", encoding="utf-8")
        assert settings.load(d) == settings.DEFAULTS


def test_quiet_hours_may_cross_midnight():
    cfg = {**settings.DEFAULTS, "quiet_on": True, "quiet_from": "23:00", "quiet_to": "09:00"}
    at = lambda h, m=0: settings.in_quiet_hours(cfg, h * 60 + m)
    assert at(23) and at(2) and at(8, 59) and not at(9) and not at(14) and not at(22, 59)
    assert not settings.in_quiet_hours({**cfg, "quiet_on": False}, 2 * 60)
    day = {**cfg, "quiet_from": "13:00", "quiet_to": "15:00"}
    assert settings.in_quiet_hours(day, 14 * 60) and not settings.in_quiet_hours(day, 16 * 60)
    assert not settings.in_quiet_hours({**cfg, "quiet_to": "23:00"}, 23 * 60)


def test_alerts_stay_silent_in_quiet_hours_and_can_be_limited_to_serious_ones():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "metrics.db"
        found = [alerts.Alert("a", "high", "serious", "x"), alerts.Alert("b", "medium", "meh", "y")]
        real = alerts.collect_alerts
        alerts.collect_alerts = lambda now: list(found)
        try:
            shown = []
            noon = time.mktime((2026, 9, 21, 12, 0, 0, 0, 0, -1))
            night = time.mktime((2026, 9, 21, 3, 0, 0, 0, 0, -1))
            settings.save(d, {"quiet_on": True})
            assert alerts.run_once(db, now=night, notify_fn=lambda t, b: shown.append(t) or True) == [] and shown == []
            got = alerts.run_once(db, now=noon, notify_fn=lambda t, b: shown.append(t) or True)   # what was held back comes now
            assert [a.title for a in got] == ["serious", "meh"]
            settings.save(d, {"alerts_min_severity": "high"})
            (Path(d) / "alerts_state.json").unlink()
            got = alerts.run_once(db, now=noon, notify_fn=lambda t, b: True)
            assert [a.title for a in got] == ["serious"]
        finally:
            alerts.collect_alerts = real


def test_every_finding_kind_gets_a_plain_explanation_chosen_by_kind_not_by_its_text():
    for f in ({"id": "bugcheck"}, {"id": "whea"}, {"id": "defender-realtime-off"}, {"id": "crash:x.exe"}, {"id": "defender-threat:Bad"},
              {"id": "autorun:run_key:Foo"}, {"id": "autorun:browser_extension:x"}, {"id": None, "source": "Processes"}, {"id": "brand-new-kind"}):
        x = explain.finding({**f, "title": "t", "detail": "d"})
        assert x["what"] and x["why"] and x["todo"] and x["ask"], f
    hostile = explain.finding({"id": "autorun:run_key:<script>", "title": "<script>alert(1)</script>", "detail": ""})
    assert "<script>" not in hostile["what"] + hostile["why"] + hostile["todo"]
    assert explain.event("network")["what"] and explain.event("something-new")["what"]
    assert explain.process("chrome.exe")["what"].startswith("Google Chrome") and "Rust" in explain.process("RUSTC.EXE")["what"]


def test_game_verdicts_use_plain_words():
    base = {"avg_fps": 346.0, "low1_fps": 150.0, "frames_over_33ms": 2, "window_seconds": 129.0, "limiter_percent_of_frames": {"gpu": 64, "cpu": 14, "neither": 22}}
    assert explain.game_summary(base)["verdict"] == "Smooth" and "graphics card" in explain.game_summary(base)["limiter"]
    assert explain.game_summary({**base, "avg_fps": 25.0, "low1_fps": 12.0})["tone"] == "bad"
    assert explain.game_summary({**base, "avg_fps": 55.0, "low1_fps": 30.0})["tone"] == "warn"
    same = explain.game_compare_text({"avg_fps": {"change_percent": 1.0}, "low1_fps": {"change_percent": -2.0}})
    assert same["verdict"] == "About the same"
    assert explain.game_compare_text({"avg_fps": {"change_percent": 20.0}, "low1_fps": {"change_percent": 10.0}})["verdict"] == "Second is better"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
