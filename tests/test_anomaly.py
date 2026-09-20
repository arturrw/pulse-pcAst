"""Unit tests for the streaming anomaly detector. Run: python tests/test_anomaly.py (or pytest)."""
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_games import _write_pm  # noqa: E402

from pcassist import anomaly, db, tools  # noqa: E402


def _wave(n=600):
    return [50 + 5 * math.sin(i / 20) for i in range(n)]


def test_quiet_series_has_no_events():
    assert anomaly.events(anomaly.scores(_wave(), 288), 8, 12) == []


def test_level_shift_is_detected_and_lone_spike_needs_min_run():
    v = _wave()
    v[400] += 60                                  # one-sample spike
    v[500:520] = [x + 40 for x in v[500:520]]     # 20 samples of a shifted level
    sc = anomaly.scores(v, 288)
    assert len(anomaly.events(sc, 8, 12, min_run=1)) == 2
    sustained = anomaly.events(sc, 8, 12, min_run=6)
    assert len(sustained) == 1 and 500 <= sustained[0][0] <= 505


def test_scores_use_only_the_past():
    v = _wave()
    a = anomaly.scores(v, 288)
    v2 = v[:450] + [1000.0] * 150                 # changing the future must not change earlier scores
    assert anomaly.scores(v2, 288)[:450] == a[:450]


def test_flat_series_does_not_blow_up_on_tiny_noise():
    v = [100.0] * 300 + [100.05] * 5
    assert max(anomaly.scores(v, 288)) < 8


def test_gap_resets_the_window_instead_of_flagging_the_reboot():
    # 500 samples around 50, the PC is off for 10 h, then the level is 5: without a reset the first samples are outliers
    v = [50.0 + (i % 3) for i in range(500)] + [5.0 + (i % 3) for i in range(300)]
    t = [30.0 * i for i in range(500)] + [30.0 * i + 36000 for i in range(500, 800)]
    assert max(anomaly.scores(v, 288)) > 8                         # glued together: a fake anomaly at the reboot
    after = anomaly.scores(v, 288, t)[500:]
    assert max(after[:72]) == 0.0 and max(after) < 8               # with timestamps: window refills, nothing flagged
    assert max(anomaly.scores(v, 288, t)[:500]) < 8


def test_gap_limit_adapts_to_the_sample_step():
    assert anomaly.gap_limit([0, 30, 60, 90]) == 600
    assert anomaly.gap_limit([0, 3600, 7200, 10800]) == 5 * 3600   # a collector with an hourly interval


def test_state_metrics_exclude_load_metrics():
    assert "gpu_temp_c" in anomaly.STATE_METRICS and "gpu_util_percent" not in anomaly.STATE_METRICS


def test_close_events_are_merged_and_far_ones_are_not():
    sc = [0.0] * 100
    for i in (10, 11, 20, 60):
        sc[i] = 50.0
    ev = anomaly.events(sc, 8, merge=12)
    assert [(a, b) for a, b, _ in ev] == [(10, 20), (60, 60)]


def _ram_db(tail_level: float, samples: int = 400, tail: int = 30) -> Path:
    """A metrics.db with `samples` RAM readings 30 s apart ending now: ~50% with tiny noise, the last `tail` at tail_level."""
    root = Path(tempfile.mkdtemp())
    now = time.time()
    with db.connect(root / "metrics.db") as conn:
        conn.executemany("INSERT INTO system_metrics (ts, ram_percent) VALUES (?, ?)",
                         [(now - 30 * (samples - i), (50.0 + i % 3 * 0.1) if i < samples - tail else tail_level)
                          for i in range(samples)])
    tools.set_db(root / "metrics.db")
    return root


def test_tool_reports_a_sustained_ram_jump():
    _ram_db(80.0)
    r = tools.anomalies("ram_percent", 600)
    assert r["events_found"] == 1
    e = r["events"][0]
    assert 49 < e["typical_value"] < 51 and e["most_unusual_value"] == 80.0 and e["during_game"] is None
    assert e["duration_minutes"] > 10 and e["minutes_ago"] < 20


def test_tool_ignores_a_statistically_odd_but_tiny_change():
    _ram_db(53.0)                                      # +3 points on a rock-steady metric: z is huge, change is not
    assert tools.anomalies("ram_percent", 600)["events_found"] == 0


def test_tool_rejects_load_metrics_and_reports_no_data():
    _ram_db(80.0)
    r = tools.anomalies("gpu_util_percent", 600)
    assert "error" in r and "gpu_temp_c" in r["available"]
    tools.set_db(Path(tempfile.mkdtemp()) / "empty.db")
    assert "pcassist collect" in tools.anomalies("ram_percent", 600)["error"]


def test_event_during_a_recorded_game_is_marked():
    root = _ram_db(80.0)
    (root / "bench").mkdir()
    shutil.copy(_write_pm([(30, 4, 3, 5)]), root / "bench" / "g_run_1.csv")   # a 30 s capture that ended just now
    os.utime(root / "bench" / "g_run_1.csv", None)
    assert tools.anomalies("ram_percent", 600)["events"][0]["during_game"] == "g_run_1"


def test_metrics_history_carries_unusual_periods_for_state_metrics_only():
    _ram_db(80.0)
    r = tools.metrics_history("ram_percent", 600)
    assert r["max"] == 80.0 and r["unusual_periods_found"] == 1 and r["unusual_periods"][0]["most_unusual_value"] == 80.0
    assert "unusual_periods_found" not in tools.metrics_history("cpu_percent", 600)
    assert "anomalies" not in {f.__name__ for f in tools.TOOLS}          # one tool, so the model has no choice to get wrong


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
