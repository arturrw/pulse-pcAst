"""Records a game with PresentMon by itself while it runs. The collector calls AutoRecorder.tick() after every sample
with the names of all running programs; a game from the user's list (games.json) that is running with no PresentMon
around starts a hidden recording into data/sessions/auto/, which PresentMon ends by itself when the game exits.

No administrator rights at run time: PresentMon works for a member of the built-in Performance Log Users group, which
the user joins once (join_command, confirmed by Windows). Recordings under two minutes are deleted, and the folder is
kept under the user's size limit by deleting the oldest; hand-made recordings and benchmark runs are never touched."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import gamelib, settings

PERF_LOG_USERS_SID = "S-1-5-32-559"   # the group's name is translated on non-English Windows, its SID is not
SESSION = "pulse_auto"
MIN_SECONDS = 120
QUICK_FAIL_SECONDS = 20
RETRY_AFTER_FAIL = 3600
GB = 1024**3
_USER_SID = re.compile(r"^S-1-5-21(-\d{1,10}){3,4}$")


def _launch(cmd: list[str]):
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class AutoRecorder:
    def __init__(self, data_dir, launcher=_launch, clock=time.time, exe_finder=gamelib.presentmon_exe):
        self.data = Path(data_dir)
        self.folder = self.data / "sessions" / "auto"
        self.launch, self.clock, self.exe_finder = launcher, clock, exe_finder
        self.proc = None
        self.current: dict | None = None
        self.retry_at = 0.0
        self.cleanup()

    def tick(self, running: set[str]) -> None:
        if self.proc is not None:
            if self.proc.poll() is None:
                return
            self._finished()
        cfg = settings.load(self.data)
        if not cfg["auto_record"] or self.clock() < self.retry_at:
            return
        lower = {n.lower() for n in running}
        if any(n.startswith("presentmon") for n in lower):
            return                                        # a manual recording or a benchmark run owns the capture
        game = next((g["process"] for g in gamelib.GameList(self.data).load() if g["process"].lower() in lower), None)
        if game is None:
            return
        exe = self.exe_finder()
        if exe is None:
            self._state({"status": "no_presentmon"})
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"\.exe$", "", game, flags=re.I)
        path = self.folder / f"{stem}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(self.clock()))}.csv"
        cmd = [str(exe), "--process_name", game, "--output_file", str(path), "--date_time", "--terminate_on_proc_exit",
               "--no_console_stats", "--session_name", SESSION, "--stop_existing_session"]
        try:
            self.proc = self.launch(cmd)
        except OSError as e:
            self._failed(game, str(e))
            return
        self.current = {"game": game, "file": path.name, "path": path, "since": self.clock()}
        self._state({"status": "recording", "game": game, "file": path.name, "since": self.current["since"]})

    def _finished(self) -> None:
        cur, code = self.current, self.proc.returncode
        self.proc, self.current = None, None
        took = self.clock() - cur["since"]
        exists = cur["path"].exists() and cur["path"].stat().st_size > 0
        if code != 0 and took < QUICK_FAIL_SECONDS and not exists:
            self._failed(cur["game"], f"PresentMon stopped at once (exit code {code}); most likely no permission to record")
            return
        if took < MIN_SECONDS:
            cur["path"].unlink(missing_ok=True)
            self._state({"status": "idle", "note": f"{cur['file']} was shorter than {MIN_SECONDS // 60} min and was not kept"})
        else:
            self._state({"status": "idle", "last": cur["file"]})
        self.cleanup()

    def _failed(self, game: str, error: str) -> None:
        self.retry_at = self.clock() + RETRY_AFTER_FAIL
        self._state({"status": "failed", "game": game, "error": error, "retry_at": self.retry_at})

    def cleanup(self) -> list[str]:
        """Delete the oldest auto recordings until the folder fits the size limit; the one being written stays."""
        limit = settings.load(self.data)["auto_record_gb"] * GB
        busy = self.current["path"] if self.current else None
        try:
            files = sorted((p for p in self.folder.glob("*.csv") if p != busy), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in self.folder.glob("*.csv"))
        except OSError:
            return []
        removed = []
        while files and total > limit:
            p = files.pop(0)
            try:
                size = p.stat().st_size
                p.unlink()
            except OSError:
                continue
            total -= size
            removed.append(p.name)
        return removed

    def _state(self, state: dict) -> None:
        try:
            (self.data / "autorec_state.json").write_text(json.dumps({**state, "ts": self.clock()}), encoding="utf-8")
        except OSError:
            pass


def read_state(data_dir) -> dict:
    try:
        s = json.loads((Path(data_dir) / "autorec_state.json").read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _whoami(*args: str) -> str:
    exe = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "whoami.exe"   # not a same-named tool on PATH
    res = subprocess.run([str(exe), *args, "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=15,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return res.stdout or ""


def in_perf_log_users(run=_whoami) -> bool | None:
    """Does this logon carry the group? Joining takes effect only after signing out and in again. None if unknown."""
    try:
        lines = run("/groups").splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    return any(PERF_LOG_USERS_SID in ln and "deny only" not in ln.lower() for ln in lines)


def user_sid(run=_whoami) -> str | None:
    try:
        sid = run("/user").strip().rsplit(",", 1)[-1].strip().strip('"')
    except (OSError, subprocess.SubprocessError):
        return None
    return sid if _USER_SID.match(sid) else None


def join_command(sid: str) -> list[str]:
    """Adds the user to Performance Log Users; Windows asks for administrator confirmation. The SID is checked to be
    only digits and dashes before it goes into the command."""
    if not _USER_SID.match(sid or ""):
        raise ValueError("unexpected user id")
    inner = f"Add-LocalGroupMember -SID {PERF_LOG_USERS_SID} -Member {sid} -ErrorAction Stop"
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile','-NonInteractive','-Command','{inner}'"]
