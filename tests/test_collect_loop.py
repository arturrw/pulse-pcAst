"""The background collector must survive a bad sample and exit after repeated failures.
Run: python tests/test_collect_loop.py (or pytest)."""
import argparse
import tempfile
from pathlib import Path

from pulse import cli, db


class FakeCollector:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)  # True = ok sample, False = raise

    def sample(self):
        if not self.outcomes:
            raise KeyboardInterrupt  # ends the loop like Ctrl+C
        if not self.outcomes.pop(0):
            raise OSError("nvml hiccup")
        return {"system": {"ts": 1.0, "cpu_percent": 1, "cpu_freq_mhz": 1, "ram_used_mb": 1, "ram_percent": 1,
                           "swap_percent": 1, "disk_read_mbps": 0, "disk_write_mbps": 0,
                           "net_sent_kbps": 0, "net_recv_kbps": 0},
                "gpus": [], "processes": [], "disks": []}


def _run(outcomes, monkeypatch_targets=None):
    args = argparse.Namespace(db=str(Path(tempfile.mkdtemp()) / "t.db"), interval=1, once=False, keep_days=0)
    saved = cli.Collector, cli.time.sleep, cli._print_sample
    cli.Collector, cli.time.sleep, cli._print_sample = (lambda: FakeCollector(outcomes)), (lambda s: None), (lambda s: None)
    try:
        cli.cmd_collect(args)
    finally:
        cli.Collector, cli.time.sleep, cli._print_sample = saved
    return args.db


def test_survives_isolated_failures():
    path = _run([True, False, False, True])
    with db.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM system_metrics").fetchone()[0] == 1  # same ts -> one row


def test_exits_after_ten_consecutive_failures():
    try:
        _run([False] * 10 + [True])
    except OSError:
        return
    raise AssertionError("collector should have re-raised after 10 consecutive failures")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
