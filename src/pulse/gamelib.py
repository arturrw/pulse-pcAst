"""The user's own list of games (games.json next to the metrics database) and what is needed to record one.

A game is a program name such as `Hades2.exe`. A game with recordings appears in the library by itself (the recordings
say which program they belong to); this list is for adding one before it has any recording, and for giving it a title."""
import json
import os
import re
from pathlib import Path

import psutil

PROCESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()\-]{0,62}\.exe$")
GAME_FOLDERS = ("steamapps/common/", "epic games/", "riot games/", "gog games/", "ubisoft game launcher/games/", "origin games/", "ea games/")   # matched against the path with / separators
OWN_PROGRAMS = ("pulse.exe", "pulsew.exe", "pulse-desktop.exe", "python.exe", "pythonw.exe", "powershell.exe", "cmd.exe",
                "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "galaxyclient.exe", "battle.net.exe", "origin.exe",
                "eadesktop.exe", "riotclientservices.exe", "wallpaper32.exe", "wallpaper64.exe")


def valid_process(name) -> str:
    """The program name if it is safe to hand to the recorder (a plain .exe file name), else ValueError."""
    if not isinstance(name, str) or not PROCESS_RE.match(name.strip()):
        raise ValueError("enter the program name as shown in Task Manager > Details, for example Hades2.exe")
    return name.strip()


def presentmon_folder() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Tools" / "PresentMon"


def presentmon_exe() -> Path | None:
    """The newest PresentMon console program in the tools folder, if there is one."""
    found = sorted(presentmon_folder().glob("PresentMon*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


class GameList:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "games.json"

    def load(self) -> list[dict]:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8")).get("games", [])
        except (OSError, ValueError, AttributeError):
            return []
        out = []
        for g in items if isinstance(items, list) else []:
            try:
                out.append({"process": valid_process(g["process"]), "title": str(g.get("title") or "")[:60]})
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        return out

    def _save(self, games: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"games": games}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, process, title="") -> dict:
        process = valid_process(process)
        games = self.load()
        for g in games:
            if g["process"].lower() == process.lower():
                if title:
                    g["title"] = str(title)[:60]
                self._save(games)
                return g
        entry = {"process": process, "title": str(title or "")[:60]}
        self._save(games + [entry])
        return entry

    def remove(self, process) -> bool:
        process = valid_process(process)
        games = self.load()
        kept = [g for g in games if g["process"].lower() != process.lower()]
        if len(kept) == len(games):
            return False
        self._save(kept)
        return True


def running_programs(limit: int = 60) -> list[dict]:
    """Programs running now that could be a game: everything but Windows itself, the ones that look installed as games
    first, then the heaviest. One row per program name."""
    windows = (os.environ.get("SystemRoot") or "C:\\Windows").lower()
    best: dict[str, dict] = {}
    for p in psutil.process_iter(["name", "exe", "memory_info"]):
        try:
            name, exe, mem = p.info["name"], (p.info["exe"] or ""), p.info["memory_info"]
        except (psutil.Error, OSError):
            continue
        if not name or not PROCESS_RE.match(name) or name.lower() in OWN_PROGRAMS or exe.lower().startswith(windows):
            continue
        row = {"process": name, "mb": round((mem.rss if mem else 0) / 1048576),
               "game_like": any(f in exe.lower().replace("\\", "/") for f in GAME_FOLDERS)}
        if name not in best or row["mb"] > best[name]["mb"]:
            best[name] = row
    return sorted(best.values(), key=lambda r: (not r["game_like"], -r["mb"]))[:limit]
