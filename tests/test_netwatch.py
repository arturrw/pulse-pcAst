"""Unit tests for the network side of the process check. Run: python tests/test_netwatch.py (or pytest)."""
import tempfile
import time
from collections import namedtuple
from pathlib import Path

import psutil

from pulse import alerts, binaries, collectors, db, netwatch, persistence, tools, winhealth


# These tests must not read this machine's event logs or autostart entries: both sources are empty here.
winhealth.read_raw = lambda hours: {}
winhealth.read_defender_policy = lambda: {}
persistence.read_items = lambda: []

DAY = 86400.0
Addr = namedtuple("Addr", "ip port")
Conn = namedtuple("Conn", "fd family type laddr raddr status pid")


def _db(rows: list[tuple], history_hours: float = 48) -> Path:
    """process_connections rows (name, kind, addr, port, hours_ago_first_seen, hours_ago_last_seen); the collector
    is taken to have been recording for `history_hours`, so the oldest row anchors the baseline."""
    path = Path(tempfile.mkdtemp()) / "t.db"
    now = time.time()
    with db.connect(path) as conn:
        conn.execute("INSERT INTO process_connections VALUES ('anchor.exe','out','8.8.8.8',443,?,?)",
                     (now - history_hours * 3600, now - history_hours * 3600))
        for name, kind, addr, port, first_h, last_h in rows:
            conn.execute("INSERT INTO process_connections VALUES (?,?,?,?,?,?)",
                         (name, kind, addr, port, now - first_h * 3600, now - last_h * 3600))
        conn.execute("INSERT INTO process_snapshots VALUES (?,?,?,?,?)", (now - 30, 1, "x.exe", 1.0, 10.0))
    tools.set_db(path)
    return path


def _analyze(path: Path, flagged=frozenset(), hours: float = 6) -> dict:
    now = time.time()
    with db.connect(path) as conn:
        return netwatch.analyze(conn, now - hours * 3600, now, set(flagged))


def test_no_data_yet_is_said_plainly():
    path = Path(tempfile.mkdtemp()) / "t.db"
    with db.connect(path) as conn:
        r = netwatch.analyze(conn, time.time() - 3600, time.time(), set())
    assert r["available"] is False and "no network data" in r["note"]


def test_suspicious_ports_and_flagged_files_need_no_history():
    path = _db([("miner.exe", "out", "45.9.9.9", 3333, 1, 0.1), ("tor.exe", "out", "1.2.3.4", 9001, 1, 0.1),
                ("dropper.exe", "out", "6.6.6.6", 443, 1, 0.1), ("chrome.exe", "out", "2.2.2.2", 443, 1, 0.1)],
               history_hours=3)                                                    # under 24 h of history
    r = _analyze(path, flagged={"dropper.exe"})
    assert r["confidence"] == "low" and "24 h" in r["note"]
    assert {(p["name"], p["port"]) for p in r["suspicious_ports"]} == {("miner.exe", 3333), ("tor.exe", 9001)}
    assert [f["name"] for f in r["from_suspicious_files"]] == ["dropper.exe"]
    assert r["new_listeners"] == [] and r["new_destinations"] == []              # judged only with a day of history


def test_new_listener_and_new_destination_after_a_day_of_history():
    rows = [("app.exe", "out", f"9.9.9.{i}", 443, 40, 1) for i in range(3)]       # app.exe: 3 usual destinations
    rows += [("app.exe", "out", "7.7.7.7", 443, 1, 0.1),                          # ... and a new one now
             ("old.exe", "listen", "0.0.0.0", 5000, 40, 0.1),                     # listened before: not news
             ("old.exe", "listen", "0.0.0.0", 5001, 1, 0.1),                      # same program, another port: not news
             ("newsrv.exe", "listen", "0.0.0.0", 8080, 1, 0.1)]                   # never listened before
    r = _analyze(_db(rows))
    assert r["confidence"] == "ok"
    assert [n["name"] for n in r["new_listeners"]] == ["newsrv.exe"] and r["new_listeners"][0]["ports"] == [8080]
    assert [n["name"] for n in r["new_destinations"]] == ["app.exe"] and r["new_destinations"][0]["usual_destinations"] == 3


def test_browser_like_processes_with_many_destinations_are_not_judged():
    rows = [("browser.exe", "out", f"10.1.{i // 200}.{i % 200}", 443, 40, 1) for i in range(60)]
    rows.append(("browser.exe", "out", "3.3.3.3", 443, 1, 0.1))
    assert _analyze(_db(rows))["new_destinations"] == []


def test_process_watch_and_alerts_carry_the_network_findings():
    _db([("miner.exe", "out", "45.9.9.9", 3333, 1, 0.1)], history_hours=3)
    r = tools.process_watch(60)
    assert r["network"]["suspicious_ports"][0]["port"] == 3333 and "1 network finding" in r["summary"]
    found = [a for a in alerts.collect_alerts() if a.key.startswith("net-")]
    assert [(a.key, a.severity) for a in found] == [("net-port-miner.exe-3333", "medium")]
    assert "mining pool" in found[0].body


def test_collector_records_only_public_outbound_and_non_local_listeners():
    conns = [Conn(0, 2, 1, Addr("192.168.1.5", 50000), Addr("8.8.8.8", 443), "ESTABLISHED", 10),   # public out: kept
             Conn(0, 2, 1, Addr("192.168.1.5", 50001), Addr("192.168.1.9", 445), "ESTABLISHED", 10),  # LAN: dropped
             Conn(0, 2, 1, Addr("127.0.0.1", 50002), Addr("127.0.0.1", 11434), "ESTABLISHED", 10),    # loopback: dropped
             Conn(0, 2, 1, Addr("0.0.0.0", 8080), (), "LISTEN", 11),                                  # exposed: kept
             Conn(0, 2, 1, Addr("127.0.0.1", 9090), (), "LISTEN", 11),                                # local only: dropped
             Conn(0, 2, 1, Addr("192.168.1.5", 50003), Addr("1.1.1.1", 443), "TIME_WAIT", 10),        # closed: dropped
             Conn(0, 2, 1, Addr("192.168.1.5", 50004), Addr("1.1.1.1", 443), "ESTABLISHED", None)]    # no pid: dropped
    real_conns, real_proc = psutil.net_connections, psutil.Process
    psutil.net_connections = lambda kind="inet": conns
    psutil.Process = lambda pid: type("P", (), {"name": lambda self: {10: "a.exe", 11: "srv.exe"}[pid]})()
    try:
        rows = collectors.Collector()._connections(123.0)
    finally:
        psutil.net_connections, psutil.Process = real_conns, real_proc
    assert sorted((r["name"], r["kind"], r["addr"], r["port"]) for r in rows) == [
        ("a.exe", "out", "8.8.8.8", 443), ("srv.exe", "listen", "0.0.0.0", 8080)]


def test_a_failing_connection_listing_records_nothing_instead_of_crashing():
    real = psutil.net_connections
    psutil.net_connections = lambda kind="inet": (_ for _ in ()).throw(psutil.AccessDenied())
    try:
        assert collectors.Collector()._connections(1.0) == []
    finally:
        psutil.net_connections = real


def test_connections_are_stored_once_and_last_seen_moves_on():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    row = {"name": "a.exe", "kind": "out", "addr": "8.8.8.8", "port": 443}
    for ts in (100.0, 130.0, 160.0):
        db.save_sample(conn, {"system": {"ts": ts}, "gpus": [], "processes": [], "disks": [], "conns": [{**row, "ts": ts}]})
    assert conn.execute("SELECT COUNT(*), MIN(first_seen), MAX(last_seen) FROM process_connections").fetchone() == (1, 100.0, 160.0)
    assert db.prune(conn, 0.001, now=10 ** 9) > 0 and conn.execute("SELECT COUNT(*) FROM process_connections").fetchone()[0] == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
