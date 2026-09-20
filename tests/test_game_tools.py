"""Unit tests for the game_* chat tools (recording lookup and reports). Run: python tests/test_game_tools.py (or pytest)."""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_games import _write_pm  # noqa: E402

from vigil import tools  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
