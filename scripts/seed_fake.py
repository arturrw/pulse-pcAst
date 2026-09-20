r"""Fill a SEPARATE database with synthetic history, for trying out chat/forecast without waiting.

    .\.venv\Scripts\python.exe scripts\seed_fake.py [--days 14] [--out data\fake.db]
    .\.venv\Scripts\python.exe -m pcassist chat --db data\fake.db
(use the project venv: the system python does not have pcassist installed)
"""
import argparse
import math
import random
import time
from pathlib import Path

from pcassist import db

STEP = 300  # seconds between samples (real collector uses 30; 5 min keeps the file small)
PROCS = [("chrome.exe", 6, 1800), ("Code.exe", 4, 900), ("python.exe", 8, 400),
         ("Discord.exe", 1, 500), ("explorer.exe", 1, 250), ("ollama.exe", 12, 3000)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", default=str(Path(db.DEFAULT_DB).with_name("fake.db")))
    a = ap.parse_args()
    if Path(a.out).resolve() == Path(db.DEFAULT_DB).resolve():
        raise SystemExit("Refusing to write fake data into the real database.")

    rng = random.Random(42)
    conn = db.connect(a.out)
    now = time.time()
    start = now - a.days * 86400
    ts = start
    while ts <= now:
        day_frac = (ts % 86400) / 86400
        busy = max(0.0, math.sin((day_frac - 0.3) * math.pi * 2)) * 0.6 + 0.1  # daytime load
        cpu = min(100, 8 + busy * 50 + rng.random() * 10)
        ram_mb = 9000 + busy * 6000 + rng.gauss(0, 300)
        conn.execute("INSERT OR REPLACE INTO system_metrics VALUES (?,?,?,?,?,?,?,?,?,?)", (
            ts, cpu, 3200 + busy * 1200, ram_mb, ram_mb / 32768 * 100, 5 + busy * 5,
            rng.random() * 20 * busy, rng.random() * 10 * busy, rng.random() * 300, rng.random() * 2000))
        gpu = busy * 60 + rng.random() * 10
        conn.execute("INSERT OR REPLACE INTO gpu_metrics VALUES (?,?,?,?,?,?,?,?)", (
            ts, 0, "NVIDIA GeForce RTX (fake)", gpu, 1500 + gpu * 50, 12288, 40 + gpu * 0.4, 30 + gpu * 1.5))
        for pid, (name, c, mb) in enumerate(PROCS, start=1000):
            conn.execute("INSERT OR REPLACE INTO process_snapshots VALUES (?,?,?,?,?)",
                         (ts, pid, name, max(0, c * busy * 3 + rng.random() * 2), mb * (0.8 + busy * 0.4)))
        days = (ts - start) / 86400
        conn.execute("INSERT OR REPLACE INTO disk_usage VALUES (?,?,?,?)",
                     (ts, "C:\\", 476.0, 300 + 4.5 * days + rng.gauss(0, 0.3)))  # ~4.5 GB/day growth
        conn.execute("INSERT OR REPLACE INTO disk_usage VALUES (?,?,?,?)", (ts, "D:\\", 931.0, 420 + rng.gauss(0, 0.2)))
        ts += STEP
    conn.commit()
    print(f"Wrote {a.days} days of fake history to {a.out}")


if __name__ == "__main__":
    main()
