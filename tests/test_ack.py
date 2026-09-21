"""Unit tests for accepted risks. Run: python tests/test_ack.py (or pytest)."""
import contextlib
import io
import tempfile
from pathlib import Path

from pulse import ack, cli


def _db() -> Path:
    return Path(tempfile.mkdtemp()) / "metrics.db"


def test_acknowledge_persists_and_forget_removes():
    db = _db()
    assert ack.load(db) == {}
    e = ack.acknowledge(db, "defender-realtime-off", "my choice", now=1_790_000_000)
    assert e["note"] == "my choice" and e["since"].startswith("2026-")
    assert ack.load(db)["defender-realtime-off"]["note"] == "my choice"
    assert ack.forget(db, "defender-realtime-off") is True and ack.load(db) == {}
    assert ack.forget(db, "never-there") is False


def test_a_prefix_entry_covers_the_ids_below_it_and_nothing_else():
    acked = {"crash:*": {"note": "n"}, "exact-id": {"note": "e"}}
    assert ack.match(acked, "crash:game.exe")["note"] == "n"
    assert ack.match(acked, "exact-id")["note"] == "e"
    assert ack.match(acked, "exact-id-2") is None and ack.match(acked, "other") is None


def test_mark_adds_the_flag_and_the_note_to_findings():
    db = _db()
    ack.acknowledge(db, "a", "because")
    found = ack.mark(db, [{"id": "a"}, {"id": "b"}, {}])
    assert found[0]["accepted"] and found[0]["accepted_note"] == "because" and "accepted_since" in found[0]
    assert found[1]["accepted"] is False and found[2]["accepted"] is False


def test_a_missing_or_damaged_file_means_nothing_is_accepted():
    db = _db()
    (db.parent / "acknowledged.json").write_text("{not json", encoding="utf-8")
    assert ack.load(db) == {}
    (db.parent / "acknowledged.json").write_text('["a list"]', encoding="utf-8")
    assert ack.load(db) == {}


def test_the_command_accepts_lists_and_forgets():
    db = _db()

    def run(*argv) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["ack", *argv, "--db", str(db)])
        return out.getvalue()

    assert "No accepted risks" in run()
    assert "Accepted 'x'" in run("x", "--note", "why")
    listing = run()
    assert "x  (since" in listing and "why" in listing
    assert "No longer accepted" in run("--forget", "x") and "No accepted risks" in run()
    assert "was not in the list" in run("--forget", "x")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
