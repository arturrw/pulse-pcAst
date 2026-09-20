"""Unit tests for the streaming anomaly detector. Run: python tests/test_anomaly.py (or pytest)."""
import math

from pcassist import anomaly


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
