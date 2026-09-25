"""Unit tests for reading/applying/reverting CS2's video settings. Run: python tests/test_cs2settings.py (or pytest)."""
import tempfile
import time
from pathlib import Path

from pulse import cs2settings as cs2

# These tests must not depend on CS2 actually being installed or running on this machine.
cs2.cs2_running = lambda: False

VIDEO_TEXT = (
    '"VideoConfig"\n{\n'
    '\t"setting.msaa_samples"\t\t"4"\n'
    '\t"setting.videocfg_shadow_quality"\t\t"2"\n'
    '\t"setting.some_other_key"\t\t"1"\n'
    '}\n'
)


def _video_file(tmp: Path, text: str = VIDEO_TEXT) -> Path:
    p = tmp / "cs2_video.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_video_settings_path_picks_freshest_userdata_account():
    tmp = Path(tempfile.mkdtemp())
    steam = tmp / "steam"
    old = steam / "userdata" / "111" / "730" / "local" / "cfg"
    new = steam / "userdata" / "222" / "730" / "local" / "cfg"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "cs2_video.txt").write_text(VIDEO_TEXT, encoding="utf-8")
    time.sleep(0.05)
    (new / "cs2_video.txt").write_text(VIDEO_TEXT, encoding="utf-8")
    assert cs2.video_settings_path(steam) == new / "cs2_video.txt"


def test_video_settings_path_none_without_userdata():
    assert cs2.video_settings_path(Path(tempfile.mkdtemp())) is None


def test_read_settings_reports_value_and_label():
    tmp = Path(tempfile.mkdtemp())
    userdata = tmp / "userdata" / "1" / "730" / "local" / "cfg"
    userdata.mkdir(parents=True)
    _video_file(userdata)
    cs2.video_settings_path = lambda: userdata / "cs2_video.txt"
    try:
        r = cs2.read_settings()
        assert r["available"] is True
        assert r["settings"]["setting.msaa_samples"] == {"value": "4", "label": "4x", "options": cs2.SETTINGS["setting.msaa_samples"]}
        assert r["settings"]["setting.videocfg_shadow_quality"]["label"] == "high"
    finally:
        del cs2.video_settings_path


def test_apply_setting_backs_up_then_writes_only_the_given_key():
    tmp = Path(tempfile.mkdtemp())
    video = _video_file(tmp)
    backups = tmp / "backups"
    cs2.video_settings_path = lambda: video
    try:
        r = cs2.apply_setting("setting.videocfg_shadow_quality", "1", backups)
        assert r["previous"] == "2" and r["applied"] == "1" and r["applied_label"] == "medium"
        text = video.read_text(encoding="utf-8")
        assert '"setting.videocfg_shadow_quality"\t\t"1"' in text
        assert '"setting.msaa_samples"\t\t"4"' in text          # untouched
        backup_path = backups / r["backup"]
        assert backup_path.read_text(encoding="utf-8") == VIDEO_TEXT
    finally:
        del cs2.video_settings_path


def test_apply_setting_rejects_unknown_key_or_value():
    tmp = Path(tempfile.mkdtemp())
    video = _video_file(tmp)
    cs2.video_settings_path = lambda: video
    try:
        try:
            cs2.apply_setting("setting.nope", "1", tmp / "b")
            assert False, "should have raised"
        except cs2.Cs2Error:
            pass
        try:
            cs2.apply_setting("setting.msaa_samples", "3", tmp / "b")
            assert False, "should have raised"
        except cs2.Cs2Error:
            pass
        assert video.read_text(encoding="utf-8") == VIDEO_TEXT   # nothing was written
    finally:
        del cs2.video_settings_path


def test_apply_setting_refuses_while_cs2_is_running():
    tmp = Path(tempfile.mkdtemp())
    video = _video_file(tmp)
    cs2.video_settings_path = lambda: video
    real_running = cs2.cs2_running
    cs2.cs2_running = lambda: True
    try:
        try:
            cs2.apply_setting("setting.msaa_samples", "2", tmp / "b")
            assert False, "should have raised"
        except cs2.Cs2Error as e:
            assert "running" in str(e)
    finally:
        cs2.cs2_running = real_running
        del cs2.video_settings_path


def test_revert_restores_the_backed_up_file():
    tmp = Path(tempfile.mkdtemp())
    video = _video_file(tmp)
    backups = tmp / "backups"
    cs2.video_settings_path = lambda: video
    try:
        applied = cs2.apply_setting("setting.msaa_samples", "0", backups)
        assert '"setting.msaa_samples"\t\t"0"' in video.read_text(encoding="utf-8")
        cs2.revert(backups, applied["backup"])
        assert video.read_text(encoding="utf-8") == VIDEO_TEXT
    finally:
        del cs2.video_settings_path


def test_revert_rejects_a_name_that_is_not_one_of_its_own_backups():
    tmp = Path(tempfile.mkdtemp())
    backups = tmp / "backups"
    backups.mkdir()
    outside = tmp / "secret.txt"
    outside.write_text("not a backup", encoding="utf-8")
    for bad in ("../secret.txt", "not_a_backup_name.txt", "cs2_video_20260101_000000.txt"):
        try:
            cs2.revert(backups, bad)
            assert False, f"should have raised for {bad!r}"
        except cs2.Cs2Error:
            pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
