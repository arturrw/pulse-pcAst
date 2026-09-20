"""Unit tests for the on-demand traffic measurement. Run: python tests/test_netstats.py (or pytest).

The real trace needs administrator rights, so `capture` is driven here with a fake command runner: no trace is ever
started by these tests, not even on a machine (or a CI runner) that happens to be elevated."""
import json
import tempfile
from pathlib import Path

from pcassist import db, netstats

MB = 1024 * 1024


def _sw(port: int) -> int:
    """A port the way the trace delivers it: the two bytes swapped (443 -> 47873)."""
    return ((port & 0xFF) << 8) | (port >> 8)


def _row(i, pid, d, s, dp, sp, size):
    """An aggregated trace record; ports are given as people write them and stored the way ETW delivers them."""
    return {"id": i, "pid": pid, "d": d, "s": s, "dp": _sw(dp), "sp": _sw(sp), "size": size}


def test_a_record_from_a_real_trace_is_decoded():
    # taken from the first measurement on a real machine: numbers as digit strings, swapped ports, the server in `daddr`
    real = {"id": 11, "pid": 24052, "d": "3952361644", "s": "1997973696", "dp": 47873, "sp": 15863, "size": 24}
    r = netstats.summarize([real], {24052: "app.exe"}, 30)
    assert r == [{"pid": 24052, "name": "app.exe", "sent": 0, "received": 24, "sent_per_second": 0.0,
                  "top_destinations": [{"to": "172.64.148.235:443", "bytes": 24}]}]
    assert netstats._port(47873) == 443 and netstats._port(15863) == 63293 and netstats._ip("1997973696") == "192.168.22.119"


def test_the_remote_end_is_the_public_address_whichever_field_it_is_in():
    a = _row(11, 1, "8.8.8.8", "192.168.1.5", 443, 50000, 10)         # connection view: the server is daddr (what Windows does)
    b = _row(11, 1, "192.168.1.5", "8.8.8.8", 50000, 443, 20)         # packet view: the server is the source
    assert netstats._peer(a) == ("8.8.8.8", 443) and netstats._peer(b) == ("8.8.8.8", 443)
    lan = _row(10, 1, "192.168.1.9", "192.168.1.5", 445, 50000, 5)     # neither is public: daddr
    assert netstats._peer(lan) == ("192.168.1.9", 445)
    assert netstats.summarize([a, b])[0]["received"] == 30


def test_addresses_are_read_as_text_or_as_a_network_order_number():
    assert netstats._ip("8.8.8.8") == "8.8.8.8" and netstats._ip(" 1.1.1.1 ") == "1.1.1.1"
    assert netstats._ip(67305985) == "1.2.3.4"                    # bytes 01 02 03 04 read as a little-endian number
    assert netstats._ip(0x08080808) == "8.8.8.8"


def test_sent_and_received_are_told_apart_and_the_peer_is_the_source_on_receive():
    rows = [_row(10, 100, "8.8.8.8", "192.168.1.5", 443, 50000, 5000),          # TCP send: peer = destination
            _row(11, 100, "192.168.1.5", "8.8.8.8", 50000, 443, 900),           # TCP receive: peer = source
            _row(42, 100, "9.9.9.9", "192.168.1.5", 53, 51000, 80)]            # UDP send
    r = netstats.summarize(rows, {100: "app.exe"}, seconds=10)[0]
    assert r["name"] == "app.exe" and r["sent"] == 5080 and r["received"] == 900 and r["sent_per_second"] == 508.0
    assert r["top_destinations"][0] == {"to": "8.8.8.8:443", "bytes": 5900}      # send and receive of one peer add up


def test_loopback_lan_and_link_local_are_left_out_unless_asked_for():
    rows = [_row(10, 1, "127.0.0.1", "127.0.0.1", 11434, 5000, 999), _row(10, 1, "192.168.1.9", "192.168.1.5", 445, 5001, 888),
            _row(26, 1, "fe80::1", "fe80::2", 80, 5002, 777), _row(10, 1, "8.8.8.8", "192.168.1.5", 443, 5003, 100)]
    assert netstats.summarize(rows)[0]["sent"] == 100
    assert netstats.summarize(rows, include_local=True)[0]["sent"] == 999 + 888 + 777 + 100


def test_unknown_event_ids_are_ignored_and_processes_are_sorted_by_what_they_sent():
    rows = [_row(10, 1, "8.8.8.8", "10.0.0.1", 443, 1, 100), _row(10, 2, "1.1.1.1", "10.0.0.1", 443, 2, 900),
            _row(12, 3, "203.0.113.7", "10.0.0.1", 443, 3, 5000)]                     # 12 is a connect event, not data
    r = netstats.summarize(rows, {1: "small.exe"})
    assert [x["pid"] for x in r] == [2, 1] and r[0]["name"] == "pid 2 (gone)" and r[1]["name"] == "small.exe"


def test_the_report_shows_a_table_points_out_big_uploads_and_handles_an_empty_window():
    rows = [_row(10, 1, "8.8.8.8", "10.0.0.1", 443, 1, 40 * MB), _row(11, 1, "10.0.0.1", "8.8.8.8", 1, 443, 2 * MB),
            _row(10, 2, "1.1.1.1", "10.0.0.1", 443, 2, 10 * 1024)]
    text = netstats.format_report(netstats.summarize(rows, {1: "uploader.exe", 2: "quiet.exe"}, 60), 60, False)
    assert "uploader.exe" in text and "40.0 MB" in text and "8.8.8.8:443" in text and "public addresses" in text
    assert "Sent a lot in this window: uploader.exe (40.0 MB)" in text and "quiet.exe" in text.split("Sent a lot")[0]
    assert "Nothing was sent or received" in netstats.format_report([], 30, False)
    assert netstats.human(512) == "512 B" and netstats.human(2048) == "2.0 KB" and netstats.human(3 * 1024 ** 3) == "3.0 GB"


class _Runner:
    """Records the commands; the 'start' command creates the trace file like logman would."""

    def __init__(self, read_output: str = "[]", start_code: int = 0, start_text: str = ""):
        self.calls, self.read_output, self.start_code, self.start_text = [], read_output, start_code, start_text

    def __call__(self, cmd, env=None, timeout=300):
        self.calls.append((cmd, env))
        if cmd[0] == "logman" and cmd[1] == "start":
            if self.start_code == 0:
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"ETL")
            return self.start_code, self.start_text
        if cmd[0] == "powershell":
            return 0, self.read_output
        return 0, ""


def test_capture_runs_stop_start_wait_stop_read_and_deletes_the_trace_file():
    work = Path(tempfile.mkdtemp())
    rows = [_row(10, 1, "8.8.8.8", "10.0.0.1", 443, 1, 1000)]
    run, waited, said = _Runner(read_output=json.dumps(rows)), [], []
    out = netstats.capture(30, work, runner=run, sleep=waited.append, admin=lambda: True, progress=said.append)
    assert out == rows and waited == [30]
    kinds = [" ".join(c[0][:2]) if c[0][0] == "logman" else c[0][0] for c in run.calls]
    assert kinds == ["logman stop", "logman start", "logman stop", "powershell"]                 # a leftover session is stopped first
    start = run.calls[1][0]
    assert netstats.PROVIDER in start and "-ets" in start and "0xffffffffffffffff" in start
    assert run.calls[3][1]["PCA_ETL"].endswith("net.etl")
    assert not (work / "net.etl").exists()                                                       # the trace holds addresses: deleted
    assert any("Measuring for 30 s" in s for s in said)


def test_the_trace_file_is_kept_only_on_request_and_a_single_object_becomes_a_list():
    work = Path(tempfile.mkdtemp())
    one = _row(10, 1, "8.8.8.8", "10.0.0.1", 443, 1, 5)
    out = netstats.capture(1, work, runner=_Runner(read_output=json.dumps(one)), sleep=lambda s: None, admin=lambda: True, keep=True)
    assert out == [one] and (work / "net.etl").exists()


def test_without_administrator_rights_nothing_is_started():
    run = _Runner()
    try:
        netstats.capture(10, Path(tempfile.mkdtemp()), runner=run, sleep=lambda s: None, admin=lambda: False)
        raise AssertionError("should have refused")
    except netstats.NetstatsError as e:
        assert "administrator" in str(e) and "Win+X" in str(e)
    assert run.calls == []


def test_a_trace_that_cannot_start_says_why_and_never_waits():
    waited = []
    try:
        netstats.capture(10, Path(tempfile.mkdtemp()), runner=_Runner(start_code=1, start_text="Access is denied"),
                         sleep=waited.append, admin=lambda: True)
        raise AssertionError("should have failed")
    except netstats.NetstatsError as e:
        assert "could not start the trace" in str(e) and "Access is denied" in str(e)
    assert waited == []


def test_the_session_is_stopped_even_when_the_wait_is_interrupted_and_garbage_is_reported():
    run = _Runner()

    def interrupted(_):
        raise KeyboardInterrupt

    try:
        netstats.capture(10, Path(tempfile.mkdtemp()), runner=run, sleep=interrupted, admin=lambda: True)
    except KeyboardInterrupt:
        pass
    assert [c[0][1] for c in run.calls if c[0][0] == "logman"] == ["stop", "start", "stop"]      # the last stop ran
    try:
        netstats.capture(1, Path(tempfile.mkdtemp()), runner=_Runner(read_output="not json"), sleep=lambda s: None, admin=lambda: True)
        raise AssertionError("should have failed")
    except netstats.NetstatsError as e:
        assert "could not read the trace" in str(e)


def test_results_are_saved_for_the_report_and_is_admin_returns_a_bool():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    result = netstats.summarize([_row(10, 1, "8.8.8.8", "10.0.0.1", 443, 1, 4096)], {1: "a.exe"}, 30)
    netstats.save(conn, result, 30, now=1000.0)
    assert conn.execute("SELECT name, sent, received, seconds, top_destination FROM net_traffic").fetchall() == [("a.exe", 4096, 0, 30.0, "8.8.8.8:443")]
    assert isinstance(netstats.is_admin(), bool)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
