"""Unit tests for the CS2 auto-tune plan and ranking. Run: python tests/test_autotune.py (or pytest)."""
import json
from pathlib import Path

from pulse import autotune, cs2settings, games

VARIANTS = json.loads((Path(__file__).parent.parent / "scripts" / "bench_variants.json").read_text(encoding="utf-8"))


def test_every_step_is_a_variant_that_sets_exactly_that_value():
    for key, ladder in autotune.LADDERS.items():
        for value, variant in ladder[1:]:
            assert variant in VARIANTS, f"{variant} missing from bench_variants.json"
            assert VARIANTS[variant]["set"] == {key: value}, variant


def test_every_step_can_be_applied_from_the_app():
    for key, ladder in autotune.LADDERS.items():
        assert [v for v, _ in ladder if v not in cs2settings.SETTINGS.get(key, {})] == [], key


def test_plan_takes_one_step_down_from_the_current_value():
    current = {"setting.msaa_samples": "4", "setting.videocfg_shadow_quality": "2", "setting.videocfg_fsr_detail": "0"}
    assert autotune.plan(current) == ["fsr1", "msaa2", "shadowM"]


def test_plan_skips_cheapest_missing_and_unknown_values():
    current = {"setting.msaa_samples": "0", "setting.shaderquality": "7", "setting.videocfg_dynamic_shadows": "0"}
    assert autotune.plan(current) == []


def test_current_values_reads_cs2_video_text():
    text = '"VideoConfig"\n{\n\t"setting.msaa_samples"\t\t"4"\n\t"setting.videocfg_ao_detail"\t\t"2"\n\t"setting.other"\t\t"1"\n}\n'
    assert autotune.current_values(text) == {"setting.msaa_samples": "4", "setting.videocfg_ao_detail": "2"}


def _row(label, avg, low, verdict, ref=False):
    return {"label": label, "runs": 2, "avg_fps": 300.0, "low1_fps": 110.0, "reference": ref,
            "avg_change": None if ref else avg, "low1_change": None if ref else low, "verdict": verdict}


def test_report_ranks_by_gain_and_names_what_is_worth_it():
    rows = [_row("Your settings", None, None, "Reference", ref=True),
            _row("Ambient occlusion low", 1.0, 0.0, "About the same"),
            _row("FSR level 4", 12.0, 8.0, "Better"),
            _row("MSAA 2x", 6.0, 2.0, "Better")]
    real = games.batch_rows
    games.batch_rows = lambda paths, process=None: rows
    try:
        text = autotune.report([], "cs2.exe")
    finally:
        games.batch_rows = real
    lines = text.splitlines()
    order = [i for i, ln in enumerate(lines) for name in ("FSR level 4", "MSAA 2x", "Ambient occlusion low") if ln.startswith(name)]
    assert order == sorted(order) and len(order) == 3
    assert "Worth lowering: FSR level 4 (+12%), MSAA 2x (+6%)." in text
    assert "Keep as they are (lowering gains nothing beyond the noise): Ambient occlusion low." in text


def test_report_without_steps_says_so():
    real = games.batch_rows
    games.batch_rows = lambda paths, process=None: [_row("Your settings", None, None, "Reference", ref=True)]
    try:
        assert autotune.report([]).startswith("no usable runs")
    finally:
        games.batch_rows = real


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
