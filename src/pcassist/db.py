import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "metrics.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS system_metrics (
    ts REAL PRIMARY KEY,
    cpu_percent REAL,
    cpu_freq_mhz REAL,
    ram_used_mb REAL,
    ram_percent REAL,
    swap_percent REAL,
    disk_read_mbps REAL,
    disk_write_mbps REAL,
    net_sent_kbps REAL,
    net_recv_kbps REAL
);

CREATE TABLE IF NOT EXISTS gpu_metrics (
    ts REAL,
    idx INTEGER,
    name TEXT,
    util_percent REAL,
    mem_used_mb REAL,
    mem_total_mb REAL,
    temp_c REAL,
    power_w REAL,
    PRIMARY KEY (ts, idx)
);

CREATE TABLE IF NOT EXISTS process_snapshots (
    ts REAL,
    pid INTEGER,
    name TEXT,
    cpu_percent REAL,
    rss_mb REAL,
    PRIMARY KEY (ts, pid)
);

CREATE TABLE IF NOT EXISTS disk_usage (
    ts REAL,
    mount TEXT,
    total_gb REAL,
    used_gb REAL,
    PRIMARY KEY (ts, mount)
);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def _insert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


def save_sample(conn: sqlite3.Connection, sample: dict) -> None:
    _insert(conn, "system_metrics", [sample["system"]])
    _insert(conn, "gpu_metrics", sample["gpus"])
    _insert(conn, "process_snapshots", sample["processes"])
    _insert(conn, "disk_usage", sample["disks"])
    conn.commit()
