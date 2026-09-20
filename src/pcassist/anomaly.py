"""Streaming anomaly detector for one metric: robust z-score against a rolling median / MAD of the past.

Deterministic and read-only, like the rest of the tools. Uses only samples BEFORE the current one, so it
behaves the same live as on a recorded history."""
import bisect
from collections import deque

MAD_TO_SIGMA = 1.4826   # MAD * this ~ standard deviation for normally distributed data
MIN_GAP_S = 600         # a silence longer than this (and than 5x the usual sample step) means the collector was not running

# Metrics of the machine's STATE: they should not jump around without a reason, so a statistical outlier means
# something. Load metrics (GPU util/power/VRAM, disk and network I/O, CPU) swing whenever the user starts a game
# or a build, so their outliers are normal workload, not anomalies.
STATE_METRICS = ("gpu_temp_c", "ram_percent", "ram_used_mb", "swap_percent")

# A statistical outlier on a very steady metric can be a change nobody cares about (swap 2.3% -> 2.6% scores z=83).
# An event is reported only when the value also moves at least this much from what was typical (metric's own units).
MIN_DEVIATION = {"gpu_temp_c": 8.0, "ram_percent": 8.0, "ram_used_mb": 2500.0, "swap_percent": 5.0}


def gap_limit(timestamps: list[float]) -> float:
    """Silence (seconds) after which the collector counts as having been off: 5x the median step, at least 10 min,
    so a collector run with a long --interval is not treated as full of gaps."""
    steps = sorted(b - a for a, b in zip(timestamps, timestamps[1:]))
    return max(MIN_GAP_S, 5 * steps[len(steps) // 2]) if steps else MIN_GAP_S


def scores(values: list[float], window: int = 288, timestamps: list[float] | None = None) -> list[float]:
    """Robust z-score of every value against the previous `window` values (0 until there are enough of them).
    The scale never drops below 0.1% of the median (or 1e-6), so a flat series does not explode on a tiny wiggle.
    With `timestamps`, a gap (PC off/asleep, see gap_limit) resets the window: values from before and after a
    reboot say nothing about each other, and the first samples after it are scored 0 while the window refills."""
    warmup = max(10, window // 4)
    limit = gap_limit(timestamps) if timestamps else None
    past: deque[float] = deque()
    ordered: list[float] = []
    out = []
    for i, x in enumerate(values):
        if limit and i and timestamps[i] - timestamps[i - 1] > limit:
            past.clear()
            ordered.clear()
        if len(past) >= warmup:
            n = len(ordered)
            med = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
            dev = sorted(abs(v - med) for v in ordered)
            mad = dev[n // 2] if n % 2 else (dev[n // 2 - 1] + dev[n // 2]) / 2
            scale = max(MAD_TO_SIGMA * mad, 0.001 * abs(med), 1e-6)
            out.append(abs(x - med) / scale)
        else:
            out.append(0.0)
        past.append(x)
        bisect.insort(ordered, x)
        if len(past) > window:
            old = past.popleft()
            del ordered[bisect.bisect_left(ordered, old)]
    return out


def events(score: list[float], threshold: float = 8.0, merge: int = 12, min_run: int = 1) -> list[tuple[int, int, float]]:
    """Runs of at least `min_run` consecutive points scoring above `threshold` (a lone spike is not sustained),
    merged when closer than `merge` samples. Returns (first_index, last_index, peak_score) per event."""
    runs: list[list] = []
    for i, s in enumerate(score):
        if s < threshold:
            continue
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
            runs[-1][2] = max(runs[-1][2], s)
        else:
            runs.append([i, i, s])
    found: list[list] = []
    for a, b, peak in runs:
        if b - a + 1 < min_run:
            continue
        if found and a - found[-1][1] <= merge:
            found[-1][1] = b
            found[-1][2] = max(found[-1][2], peak)
        else:
            found.append([a, b, peak])
    return [tuple(e) for e in found]
