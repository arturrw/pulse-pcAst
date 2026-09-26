"""Automatic game recording: when it starts PresentMon, when it does not, what it keeps. Never runs a real PresentMon."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pulse import autorec, gamelib, settings  # noqa: E402


class FakeProc:
    def __init__(self, cmd):
        self.cmd, self.returncode = cmd, None

    def poll(self):
        return self.returncode


class Rig:
    def __init__(self, d, games=("cs2.exe",), on=True):
        self.now = 1_800_000_000.0
        self.started: list[FakeProc] = []
        for g in games:
            gamelib.GameList(d).add(g)
        settings.save(d, {"auto_record": on})
        self.rec = autorec.AutoRecorder(d, launcher=self._launch, clock=lambda: self.now, exe_finder=lambda: Path("PresentMon.exe"))

    def _launch(self, cmd):
        self.started.append(FakeProc(cmd))
        return self.started[-1]

    def output(self, proc) -> Path:
        return Path(proc.cmd[proc.cmd.index("--output_file") + 1])

    def finish(self, proc, seconds, code=0, size=1000):
        self.now += seconds
        if size:
            self.output(proc).write_bytes(b"x" * size)
        proc.returncode = code


def test_a_listed_game_starts_one_recording_that_stops_with_the_game():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(d)
        r.rec.tick({"explorer.exe", "CS2.exe"})
        r.rec.tick({"explorer.exe", "CS2.exe"})            # still running: not started twice
        assert len(r.started) == 1
        cmd = r.started[0].cmd
        assert cmd[cmd.index("--process_name") + 1] == "cs2.exe" and "--terminate_on_proc_exit" in cmd
        assert r.output(r.started[0]).parent == Path(d) / "sessions" / "auto"
        assert autorec.read_state(d)["status"] == "recording"
        r.finish(r.started[0], 600)
        r.rec.tick({"explorer.exe"})
        assert r.output(r.started[0]).exists() and autorec.read_state(d)["last"] == r.output(r.started[0]).name


def test_nothing_starts_when_off_unlisted_or_presentmon_already_runs():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(d, on=False)
        r.rec.tick({"cs2.exe"})
        settings.save(d, {"auto_record": True})
        r.rec.tick({"hades2.exe"})                          # not in the list
        r.rec.tick({"cs2.exe", "PresentMon-2.5.1-x64.exe"})  # a manual recording or benchmark owns the capture
        assert r.started == []


def test_a_short_session_is_not_kept():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(d)
        r.rec.tick({"cs2.exe"})
        r.finish(r.started[0], 60)
        r.rec.tick(set())
        assert not r.output(r.started[0]).exists() and "not kept" in autorec.read_state(d)["note"]


def test_an_instant_failure_waits_an_hour_instead_of_retrying_every_sample():
    with tempfile.TemporaryDirectory() as d:
        r = Rig(d)
        r.rec.tick({"cs2.exe"})
        r.finish(r.started[0], 2, code=1, size=0)             # e.g. not in Performance Log Users yet
        r.rec.tick({"cs2.exe"})
        assert len(r.started) == 1 and autorec.read_state(d)["status"] == "failed"
        r.now += autorec.RETRY_AFTER_FAIL
        r.rec.tick({"cs2.exe"})
        assert len(r.started) == 2


def test_the_oldest_auto_recordings_go_beyond_the_limit_and_nothing_else_does():
    with tempfile.TemporaryDirectory() as d:
        settings.save(d, {"auto_record_gb": 1})
        auto = Path(d) / "sessions" / "auto"
        auto.mkdir(parents=True)
        manual = Path(d) / "sessions" / "mine.csv"
        manual.write_bytes(b"x")
        for i, name in enumerate(("a.csv", "b.csv", "c.csv")):
            p = auto / name
            p.write_bytes(b"x" * 400)
            os.utime(p, (1000 + i, 1000 + i))
        real, autorec.GB = autorec.GB, 1000                   # "1 GB" = 1000 bytes here
        try:
            removed = autorec.AutoRecorder(d, launcher=None, exe_finder=lambda: None).cleanup()
        finally:
            autorec.GB = real
        assert removed == [] and sorted(p.name for p in auto.glob("*.csv")) == ["b.csv", "c.csv"]   # a.csv went at start-up
        assert manual.exists()


def test_the_join_command_only_takes_a_real_user_sid():
    cmd = autorec.join_command("S-1-5-21-1111111111-2222222222-3333333333-1001")
    assert "S-1-5-32-559" in cmd[-1] and "RunAs" in cmd[-1]
    for bad in ("S-1-5-21-1-2-3-4'; calc; '", "Administrator", "", None, "S-1-5-32-544"):
        try:
            autorec.join_command(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert autorec.user_sid(lambda *a: '"pc\\me","S-1-5-21-1-2-3-1001"\r\n') == "S-1-5-21-1-2-3-1001"
    assert autorec.user_sid(lambda *a: "garbage") is None
    assert autorec.in_perf_log_users(lambda *a: '"BUILTIN\\Performance Log Users","Alias","S-1-5-32-559","Enabled group"') is True
    assert autorec.in_perf_log_users(lambda *a: '"BUILTIN\\Performance Log Users","Alias","S-1-5-32-559","Group used for deny only"') is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
