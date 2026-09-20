import sqlite3
import time
from pathlib import Path

from . import paths

DEFAULT_DB = paths.data_dir() / "metrics.db"

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

-- which file each process name ran from (a few rows, not one per sample), and the cached signature check of it
CREATE TABLE IF NOT EXISTS process_exes (
    name TEXT,
    exe TEXT,
    first_seen REAL,
    last_seen REAL,
    PRIMARY KEY (name, exe)
);

-- outbound TCP to public addresses ("out": addr = remote address, port = remote port) and TCP listening on something
-- other than localhost ("listen": addr = bind address, port = local port); a few rows, not one per sample
CREATE TABLE IF NOT EXISTS process_connections (
    name TEXT,
    kind TEXT,
    addr TEXT,
    port INTEGER,
    first_seen REAL,
    last_seen REAL,
    PRIMARY KEY (name, kind, addr, port)
);

-- what starts by itself (Run keys, startup folders, scheduled tasks, services); the first snapshot is the baseline
CREATE TABLE IF NOT EXISTS autoruns (
    kind TEXT,
    name TEXT,
    command TEXT,
    detail TEXT,
    first_seen REAL,
    last_seen REAL,
    PRIMARY KEY (kind, name, command)
);

-- which kinds of autostart entry the snapshot code knows how to read (see persistence.SUPPORTED_KINDS)
CREATE TABLE IF NOT EXISTS autorun_kinds (
    kind TEXT PRIMARY KEY
);

-- the last on-demand traffic measurements (vigil netstats): bytes per process over a short window
CREATE TABLE IF NOT EXISTS net_traffic (
    ts REAL,
    name TEXT,
    sent INTEGER,
    received INTEGER,
    seconds REAL,
    top_destination TEXT
);

CREATE TABLE IF NOT EXISTS binaries (
    exe TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    sig_status TEXT,
    signer TEXT,
    checked_ts REAL
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


TABLES = ("system_metrics", "gpu_metrics", "process_snapshots", "disk_usage")


def prune(conn: sqlite3.Connection, keep_days: float, now: float | None = None) -> int:
    """Delete rows older than keep_days from every table. Returns the number of rows removed."""
    cutoff = (time.time() if now is None else now) - keep_days * 86400
    removed = 0
    for table in TABLES:
        removed += conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,)).rowcount
    removed += conn.execute("DELETE FROM process_exes WHERE last_seen < ?", (cutoff,)).rowcount
    removed += conn.execute("DELETE FROM process_connections WHERE last_seen < ?", (cutoff,)).rowcount
    removed += conn.execute("DELETE FROM net_traffic WHERE ts < ?", (cutoff,)).rowcount
    removed += conn.execute("DELETE FROM binaries WHERE checked_ts < ?", (cutoff,)).rowcount
    # autoruns keep their baseline (first_seen), so only entries that disappeared long ago are dropped
    removed += conn.execute("DELETE FROM autoruns WHERE last_seen < ?", (cutoff,)).rowcount
    conn.commit()
    return removed


def save_sample(conn: sqlite3.Connection, sample: dict) -> None:
    _insert(conn, "system_metrics", [sample["system"]])
    _insert(conn, "gpu_metrics", sample["gpus"])
    _insert(conn, "process_snapshots", sample["processes"])
    _insert(conn, "disk_usage", sample["disks"])
    for e in sample.get("exes", []):   # first_seen stays, last_seen moves on
        conn.execute("INSERT INTO process_exes (name, exe, first_seen, last_seen) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(name, exe) DO UPDATE SET last_seen = excluded.last_seen",
                     (e["name"], e["exe"], e["ts"], e["ts"]))
    for c in sample.get("conns", []):
        conn.execute("INSERT INTO process_connections (name, kind, addr, port, first_seen, last_seen) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name, kind, addr, port) DO UPDATE SET last_seen = excluded.last_seen",
                     (c["name"], c["kind"], c["addr"], c["port"], c["ts"], c["ts"]))
    conn.commit()
