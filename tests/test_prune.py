"""Unit tests for db.prune. Run: python tests/test_prune.py (or pytest)."""
import tempfile
import time
from pathlib import Path

from pcassist import db


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_prune_removes_only_old_rows_in_every_table():
    now = time.time()
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    for age_days in (100, 10):
        ts = now - age_days * 86400
        conn.execute("INSERT INTO system_metrics (ts) VALUES (?)", (ts,))
        conn.execute("INSERT INTO gpu_metrics (ts, idx) VALUES (?, 0)", (ts,))
        conn.execute("INSERT INTO process_snapshots (ts, pid) VALUES (?, 1)", (ts,))
        conn.execute("INSERT INTO disk_usage (ts, mount) VALUES (?, 'C:')", (ts,))
    assert db.prune(conn, 90, now) == 4
    assert all(_count(conn, t) == 1 for t in db.TABLES)


def test_prune_nothing_to_remove():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    conn.execute("INSERT INTO system_metrics (ts) VALUES (?)", (time.time(),))
    assert db.prune(conn, 90) == 0 and _count(conn, "system_metrics") == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
