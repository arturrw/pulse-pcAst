"""Unit tests for game session analysis. Run: python tests/test_games.py (or pytest)."""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from vigil import games

BASE = datetime(2026, 9, 19, 14, 0, 0)  # local (Afterburner) time
PM_SHIFT = timedelta(hours=3)           # PresentMon stamps 3 h ahead in these logs

PM_HEADER = ["Application", "ProcessID", "SwapChainAddress", "TimeInDateTime", "MsBetweenPresents",
             "MsBetweenAppStart", "MsCPUBusy", "MsGPUBusy"]


def _pm_time(dt: datetime) -> str:
    return f"{dt.year}-{dt.month}-{dt.day} {dt:%H:%M:%S}.{dt.microsecond:06d}000"  # PresentMon: no zero padding


def _write_pm(segments, chain="0xA", extra_rows=(), header=None) -> Path:
    """segments: [(seconds, frame_ms, cpu_ms, gpu_ms)] laid out back to back from BASE (+ PM_SHIFT)."""
    path = Path(tempfile.mkdtemp()) / "pm.csv"
    lines = [",".join(header or PM_HEADER)]
    t = BASE + PM_SHIFT
    for secs, ft, cpu, gpu in segments:
        end = t + timedelta(seconds=secs)
        while t < end:
            lines.append(f"cs2.exe,1,{chain},{_pm_time(t)},{ft},{ft},{cpu},{gpu}")
            t += timedelta(milliseconds=ft)
    lines += list(extra_rows)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_hml(seconds: int, overrides: dict | None = None) -> Path:
    overrides = overrides or {}
    path = Path(tempfile.mkdtemp()) / "s.hml"
    names = ["GPU usage", "Memory usage", "CPU usage", "Temp limit", "Power limit"]
    lines = ["00, 19-09-2026 14:00:00, Hardware monitoring log v1.6",
             "01, 19-09-2026 14:00:00, GPU",
             "02, 19-09-2026 14:00:00, " + ",".join(n.ljust(16) for n in names)]
    for i in range(seconds):
        t = BASE + timedelta(seconds=i)
        vals = {"GPU usage": 90.0, "Memory usage": 6000.0, "CPU usage": 30.0, "Temp limit": 0.0, "Power limit": 0.0}
        vals.update(overrides.get(i, {}))
        lines.append(f"80, {t:%d-%m-%Y %H:%M:%S}, " + ",".join(f"{vals[n]:.3f}".ljust(16) for n in names))
    path.write_text("\r\n".join(lines) + "\r\n", encoding="latin-1")
    return path


def test_parse_keeps_main_swapchain_and_survives_na():
    junk = [f"cs2.exe,1,0x0,{_pm_time(BASE + PM_SHIFT)},100,100,NA,NA"] * 5
    p = _write_pm([(2, 10, "NA", "NA")], extra_rows=junk)
    frames, notes = games.parse_presentmon(p)
    assert len(frames) == 200 and all(f["cpu"] is None for f in frames)
    assert any("ignored 5" in n for n in notes)
    assert frames == sorted(frames, key=lambda f: f["t"])


def test_missing_date_time_column_is_explained():
    p = _write_pm([(1, 10, 5, 5)], header=[h if h != "TimeInDateTime" else "TimeInSeconds" for h in PM_HEADER])
    try:
        games.parse_presentmon(p)
    except ValueError as e:
        assert "--date_time" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_lows_and_slow_frame_counts():
    frames = [{"t": BASE, "ft": 10.0, "cpu": None, "gpu": None, "app": None}] * 990
    frames += [{"t": BASE, "ft": 100.0, "cpu": None, "gpu": None, "app": None}] * 10
    s = games.frame_stats(frames)
    assert s["frames"] == 1000 and s["slow"][50.0] == 10 and s["slow"][100.0] == 0
    assert abs(s["low1_fps"] - 10) < 0.01          # slowest 1% (10 frames) are all 100 ms
    assert abs(s["low01_fps"] - 10) < 0.01
    assert abs(s["avg_fps"] - 1000 / ((990 * 10 + 10 * 100) / 1000)) < 0.01


def test_bottleneck_gpu_vs_cpu():
    gpu_bound = [{"t": BASE, "ft": 10.0, "cpu": 4.0, "gpu": 9.8, "app": 10.0}] * 10
    cpu_bound = [{"t": BASE, "ft": 10.0, "cpu": 9.8, "gpu": 4.0, "app": 10.0}] * 10
    idle = [{"t": BASE, "ft": 10.0, "cpu": 3.0, "gpu": 3.0, "app": 10.0}] * 10
    b = games.bottleneck(gpu_bound + cpu_bound + idle)
    assert (round(b["gpu"], 2), round(b["cpu"], 2), round(b["neither"], 2)) == (0.33, 0.33, 0.33)
    assert games.bottleneck([{"t": BASE, "ft": 10.0, "cpu": None, "gpu": None, "app": None}]) is None


def test_play_window_cuts_off_loading_and_keeps_short_dip():
    # 20 s loading at ~10 fps, 60 s play with a 3 s freeze inside, 5 s of menu at the end
    p = _write_pm([(20, 100, 50, 5), (30, 8, 4, 6), (3, 300, 5, 5), (30, 8, 4, 6), (5, 100, 50, 5)])
    frames, _ = games.parse_presentmon(p)
    start, end = games.find_play_window(frames)
    t0 = BASE + PM_SHIFT
    assert abs((start - (t0 + timedelta(seconds=20))).total_seconds()) <= 1
    assert abs((end - (t0 + timedelta(seconds=83))).total_seconds()) <= 1   # freeze bridged, menu cut


def test_offset_detection_and_overlap_check():
    p = _write_pm([(60, 10, 5, 5)])
    frames, _ = games.parse_presentmon(p)
    hours, ok = games.detect_offset(frames, games.parse_hml(_write_hml(90)))
    assert hours == 3.0 and ok
    far = {BASE + timedelta(hours=5): {}, BASE + timedelta(hours=5, seconds=1): {}}  # a different, 1 s long recording
    _, ok = games.detect_offset(frames, far)
    assert not ok


def test_hitch_gets_hardware_context_and_throttle_is_counted():
    p = _write_pm([(30, 8, 4, 6), (1, 500, 5, 5), (30, 8, 4, 6)])
    hml = _write_hml(70, {30: {"GPU usage": 4.0, "Memory usage": 7400.0, "Temp limit": 1.0}})
    r = games.analyze(p, hml)
    assert r["time_offset_hours"] == 3.0
    top = r["hitches"]["worst"][0]
    assert top["worst_ms"] == 500 and top["GPU usage"] == 4.0 and top["Memory usage"] == 7400.0
    assert r["hardware"]["temp_limit_s"] == 1 and r["hardware"]["Memory usage"]["max"] == 7400.0
    assert "Hitches" in games.format_report(r)
    assert top["near_end"] is False


def test_hitch_near_the_end_is_marked_as_probable_exit():
    p = _write_pm([(40, 8, 4, 6), (1, 500, 5, 5)])
    r = games.analyze(p, start=(BASE + PM_SHIFT).time(), offset_hours=0)
    assert r["hitches"]["worst"][0]["near_end"] is True
    assert "probably the game exiting" in games.format_report(r)


def test_manual_window_and_short_capture_warning():
    p = _write_pm([(10, 10, 5, 5)])
    r = games.analyze(p, start=(BASE + PM_SHIFT + timedelta(seconds=2)).time(),
                      end=(BASE + PM_SHIFT + timedelta(seconds=5)).time(), offset_hours=0)
    assert not r["window"]["auto"] and r["frames"]["frames"] == 300
    assert any("not reliable" in n for n in r["notes"])   # 300 < 600 frames
    assert r["frames"]["avg_fps"] > 99


def test_compare_marks_better_and_worse():
    def rep(fps_ms, low):
        p = _write_pm([(30, fps_ms, 4, 6)])
        r = games.analyze(p)
        r["frames"]["low1_fps"] = low
        return r
    text = games.format_compare(rep(10, 50), rep(8, 40))
    assert "avg FPS" in text and "better" in text and "1% low FPS" in text and "worse" in text
    assert "repeat each setting" in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
