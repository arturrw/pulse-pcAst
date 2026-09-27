"""Unit tests for the nightly mode of eval_tools.py (no Ollama needed). Run: python tests/test_eval_nightly.py (or pytest)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import eval_tools  # noqa: E402

LOG = ["2026-09-25 03:12 [ok] qwen3:8b runs=3 180/189 failed: a | b",
       "2026-09-26 03:14 [ok] gpt-oss:20b runs=3 150/189 failed: c",
       "2026-09-26 03:30 [error] qwen3:8b Ollama is not reachable"]


def test_a_small_dip_is_noise_and_a_real_drop_notifies():
    assert eval_tools.compare_with_last(LOG, "qwen3:8b", 176, 189) == ("ok", None)        # 95.2% -> 93.1%
    status, note = eval_tools.compare_with_last(LOG, "qwen3:8b", 170, 189)                # -> 89.9%
    assert status == "drop" and "170/189" in note and "last run 95%" in note


def test_the_baseline_is_the_same_model_and_an_error_line_is_not_a_score():
    assert eval_tools.compare_with_last(LOG, "gpt-oss:20b", 150, 189) == ("ok", None)
    assert eval_tools.compare_with_last(LOG[:2], "gpt-oss:20b", 170, 189) == ("ok", None)     # better is fine


def test_the_first_run_notifies_only_when_something_failed():
    assert eval_tools.compare_with_last([], "qwen3:8b", 189, 189) == ("ok", None)
    status, note = eval_tools.compare_with_last([], "qwen3:8b", 180, 189)
    assert status == "first" and "first nightly run" in note


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
