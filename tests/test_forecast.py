"""Unit tests for disk_forecast on synthetic history. Run: python tests/test_forecast.py (or pytest)."""
import tempfile
import time
from pathlib import Path

from pcassist import db, tools


def _make_db(rows_by_mount: dict[str, list[tuple[float, float]]], total: float = 1000.0) -> Path:
    """rows: mount -> [(hours_ago, used_gb)]"""
    path = Path(tempfile.mkdtemp()) / "t.db"
    now = time.time()
    with db.connect(path) as conn:
        for mount, rows in rows_by_mount.items():
            conn.executemany("INSERT INTO disk_usage VALUES (?,?,?,?)",
                             [(now - h * 3600, mount, total, used) for h, used in rows])
    return path


def _forecast(path: Path) -> dict:
    tools.set_db(path)
    return {r["disk"]: r for r in tools.disk_forecast()}


def test_growing_disk_with_enough_history():
    # 10 GB/day for 3 days, currently 700 of 1000 GB used -> 30 days left
    rows = [(h, 700 - 10 * h / 24) for h in range(72, -1, -6)]
    r = _forecast(_make_db({"C:\\": rows}))["C:"]
    assert r["confidence"] == "ok" and "warning" not in r
    assert abs(r["growth_gb_per_day"] - 10) < 0.2
    assert abs(r["days_until_full"] - 30) < 1


def test_short_history_is_flagged_low_confidence():
    r = _forecast(_make_db({"C:\\": [(1.0, 500.0), (0.5, 500.4), (0.0, 500.8)]}))["C:"]
    assert r["confidence"] == "low" and "unreliable" in r["warning"]


def test_flat_or_shrinking_disk_has_no_fill_date():
    flat = _forecast(_make_db({"D:\\": [(48, 20.0), (24, 20.0), (0, 20.0)]}))["D:"]
    shrinking = _forecast(_make_db({"D:\\": [(48, 30.0), (24, 25.0), (0, 20.0)]}))["D:"]
    for r in (flat, shrinking):
        assert "days_until_full" not in r and "not growing" in r["note"]


def test_single_sample_does_not_crash():
    r = _forecast(_make_db({"C:\\": [(0, 500.0)]}))["C:"]
    assert "days_until_full" not in r and r["confidence"] == "low"


def test_empty_db_reports_error():
    tools.set_db(Path(tempfile.mkdtemp()) / "empty.db")
    assert "error" in tools.disk_forecast()[0]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
