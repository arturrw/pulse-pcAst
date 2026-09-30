"""Unit tests for the game_* chat tools (recording lookup and reports). Run: python tests/test_game_tools.py (or pytest)."""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_games import _write_pm  # noqa: E402

from pulse import tools  # noqa: E402


def _data_dir() -> Path:
    """A temp data dir with two benchmark recordings and a session, tools pointed at its metrics.db."""
    root = Path(tempfile.mkdtemp())
    (root / "bench").mkdir()
    (root / "sessions").mkdir()
    shutil.copy(_write_pm([(30, 4, 3, 5)]), root / "bench" / "t_base_1.csv")
    shutil.copy(_write_pm([(30, 8, 6, 5)]), root / "bench" / "t_slow_1.csv")
    shutil.copy(_write_pm([(30, 4, 3, 5)]), root / "sessions" / "cs2_20260919_120000.csv")
    tools.set_db(root / "metrics.db")
    return root


def test_lists_recordings_with_kind():
    _data_dir()
    rows = tools.game_sessions(10)
    assert {r["name"] for r in rows} == {"t_base_1", "t_slow_1", "cs2_20260919_120000"}
    assert {r["name"]: r["kind"] for r in rows}["t_base_1"] == "benchmark run"


def test_no_recordings_is_an_error_row():
    tools.set_db(Path(tempfile.mkdtemp()) / "metrics.db")
    assert "error" in tools.game_sessions()[0]


def test_lookup_exact_partial_and_ambiguous():
    _data_dir()
    assert tools._find_recording("t_base_1").name == "t_base_1.csv"
    assert tools._find_recording("T_BASE_1.csv").name == "t_base_1.csv"   # case and extension do not matter
    assert tools._find_recording("slow").name == "t_slow_1.csv"           # unique partial match
    for bad in ("t_", "nothing", "", "../../metrics"):                   # ambiguous / unknown / empty / path
        try:
            tools._find_recording(bad)
        except ValueError as e:
            assert "game_sessions" in str(e)
        else:
            raise AssertionError(f"'{bad}' should not resolve")


def test_report_has_numbers_and_errors_are_returned_not_raised():
    _data_dir()
    r = tools.game_session_report("t_base_1")
    assert r["avg_fps"] > 0 and r["low1_fps"] > 0 and "slowest_10s_stretches" in r
    assert "error" in tools.game_session_report("nope")


def test_compare_reports_percent_change():
    _data_dir()
    c = tools.game_sessions_compare("t_base_1", "t_slow_1")
    assert c["avg_fps"]["before"] > c["avg_fps"]["after"] and c["avg_fps"]["change_percent"] < 0
    assert "error" in tools.game_sessions_compare("t_base_1", "nope")


def test_recordings_know_their_game_and_get_a_readable_title():
    root = _data_dir()
    other = (root / "sessions" / "cs2_20260919_120000.csv").read_text(encoding="utf-8").replace("cs2.exe,", "Hades2.exe,")
    (root / "sessions" / "hades_1.csv").write_text(other, encoding="utf-8")
    games = {r["name"]: r["game"] for r in tools.game_sessions(10)}
    assert games["t_base_1"] == "cs2.exe" and games["hades_1"] == "Hades2.exe"
    assert tools.game_title("cs2.exe") == "Counter-Strike 2"
    assert tools.game_title("FortniteClient-Win64-Shipping.exe") == "Fortnite"
    assert tools.game_title("some_new_game.exe") == "Some New Game"


def test_benchmark_names_split_into_batch_and_variant():
    assert tools.split_run_name("0919_1546_base_1") == ("0919_1546", "base")
    assert tools.split_run_name("combo_fsr3ShadowShaderL_3") == ("combo", "fsr3ShadowShaderL")
    assert tools.split_run_name("dust2_test") == (None, None) and tools.split_run_name("trial") == (None, None)
    rows = {r["name"]: r for r in (_data_dir() and tools.game_sessions(10))}
    assert rows["t_base_1"]["batch"] == "t" and rows["t_base_1"]["variant"] == "base"
    assert "batch" not in rows["cs2_20260919_120000"]                      # play sessions are not batches


def _tune_dir() -> Path:
    """_data_dir plus an auto-tune batch: base, msaa2 (much faster), aoLow (the same), fsr4 (faster, already set)."""
    root = _data_dir()
    for variant, ft in (("base", 4), ("msaa2", 3), ("aoLow", 4), ("fsr4", 3)):
        for i in (1, 2):
            shutil.copy(_write_pm([(30, ft, 2, 3)]), root / "bench" / f"tune0930_{variant}_{i}.csv")
    return root


CURRENT = {"available": True, "cs2_running": False, "settings": {
    "setting.msaa_samples": {"value": "4", "label": "4x"}, "setting.videocfg_ao_detail": {"value": "2", "label": "medium"},
    "setting.videocfg_fsr_detail": {"value": "4", "label": "level 4"}}}


def test_autotune_result_ranks_steps_and_offers_only_what_the_app_can_still_set():
    from pulse import cs2settings
    _tune_dir()
    real = cs2settings.read_settings
    cs2settings.read_settings = lambda: CURRENT
    try:
        r = tools.cs2_autotune_result()
    finally:
        cs2settings.read_settings = real
    assert r["available"] and r["batch"] == "tune0930" and r["your_settings"]["runs"] == 2
    steps = {s["step"]: s for s in r["steps"]}
    assert steps["MSAA 2x"]["verdict"] == "Better" and steps["MSAA 2x"]["avg_fps_change_percent"] > 20
    assert steps["MSAA 2x"]["setting"] == {"key": "setting.msaa_samples", "value": "2", "from": "4x", "to": "2x"}
    assert steps["Ambient occlusion low"]["verdict"] == "About the same" and "setting" not in steps["Ambient occlusion low"]
    assert r["recommendation"].startswith("With your settings CS2 runs at 250 FPS")
    assert "MSAA 2x is worth it: +33% (about +83 FPS)." in r["recommendation"]
    assert "FSR level 4 is worth it: +33% (about +83 FPS), but it is already set" in r["recommendation"]
    assert "can stay as they are" in r["recommendation"]
    assert "setting" not in steps["FSR level 4"]                            # already the current value: nothing to apply
    assert [s["step"] for s in r["steps"]][-1] == "Ambient occlusion low"   # best gain first


def test_autotune_result_without_a_tune_batch_says_so():
    _data_dir()                                                             # batch "t" is not an auto-tune
    r = tools.cs2_autotune_result()
    assert r["available"] is False and "bench_autotune" in r["reason"]


def test_a_step_is_offered_only_for_a_single_setting_the_app_changes():
    assert tools.cs2_step("msaa2", CURRENT)["current_label"] == "4x"
    assert tools.cs2_step("floor", CURRENT) is None                        # several settings at once
    assert tools.cs2_step("res1080", CURRENT) is None                      # resolution: not a setting the app changes
    assert tools.cs2_step("msaa2", {"available": False}) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
