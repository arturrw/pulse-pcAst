"""Read, apply and revert CS2's own video settings (cs2_video.txt): the same file and mechanism
scripts/bench_run.ps1 uses for A/B testing, now reachable from the app.

Only a small, named whitelist of keys is exposed (SETTINGS) - each with a short human label per value,
since a raw cfg key/number means nothing to someone clicking a button. Every apply is preceded by a
timestamped backup of the whole file, so a change can always be undone with revert()."""
import re
import time
from pathlib import Path

import psutil

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

# key -> {value: label}, in the order shown to the user
SETTINGS = {
    "setting.msaa_samples": {"0": "off", "2": "2x", "4": "4x", "8": "8x"},
    "setting.videocfg_shadow_quality": {"0": "low", "1": "medium", "2": "high"},
}

BACKUP_NAME_RE = re.compile(r"cs2_video_\d{8}_\d{6}\.txt")


class Cs2Error(Exception):
    """A setting could not be read, applied or reverted; the message is safe to show the user as-is."""


def _key_pattern(key: str) -> re.Pattern:
    return re.compile(r'("' + re.escape(key) + r'"\s+")([^"]*)(")')


def _steam_root() -> Path | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            path, _ = winreg.QueryValueEx(k, "SteamPath")
        return Path(path)
    except OSError:
        return None


def library_paths(steam: Path) -> list[Path]:
    """Steam's own install folder plus every extra library drive from libraryfolders.vdf."""
    libs = [steam]
    try:
        text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        libs.append(Path(m.group(1).replace("\\\\", "\\")))
    return libs


def video_settings_path(steam: Path | None = None) -> Path | None:
    """cs2_video.txt of the Steam account that changed CS2 settings most recently: usually there is only
    one account on the machine, but pick the freshest file if several have ever played CS2 here."""
    steam = steam if steam is not None else _steam_root()
    if steam is None or not steam.is_dir():
        return None
    userdata = steam / "userdata"
    if not userdata.is_dir():
        return None
    candidates = [p for d in userdata.iterdir() if d.is_dir()
                 for p in [d / "730" / "local" / "cfg" / "cs2_video.txt"] if p.exists()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def cs2_running() -> bool:
    return any((p.info["name"] or "").lower() == "cs2.exe" for p in psutil.process_iter(["name"]))


def read_settings() -> dict:
    """Current value of every whitelisted setting, or `available: False` with why not."""
    path = video_settings_path()
    if path is None:
        return {"available": False, "reason": "CS2's settings file was not found (Steam not installed, "
                                               "or CS2 was never launched on this account)"}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"available": False, "reason": f"could not read {path}: {e}"}
    settings = {}
    for key, labels in SETTINGS.items():
        m = _key_pattern(key).search(text)
        value = m.group(2) if m else None
        settings[key] = {"value": value, "label": labels.get(value, value), "options": labels}
    return {"available": True, "path": str(path), "cs2_running": cs2_running(), "settings": settings}


def apply_setting(key: str, value, backup_dir: Path) -> dict:
    """Backs up cs2_video.txt, then writes one whitelisted key. Refuses while CS2 is running: it writes this
    same file on exit, which would silently undo the change."""
    if key not in SETTINGS:
        raise Cs2Error(f"'{key}' is not a setting this app changes")
    value = str(value)
    if value not in SETTINGS[key]:
        raise Cs2Error(f"'{value}' is not a valid value for {key} (allowed: {', '.join(SETTINGS[key])})")
    if cs2_running():
        raise Cs2Error("CS2 is running: close it first, otherwise it overwrites this file with its own settings on exit")
    path = video_settings_path()
    if path is None:
        raise Cs2Error("CS2's settings file was not found")
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        raise Cs2Error(f"could not read {path}: {e}") from None
    m = _key_pattern(key).search(text)
    if not m:
        raise Cs2Error(f"setting '{key}' not found in {path}")
    previous = m.group(2)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"cs2_video_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    backup_path.write_text(text, encoding="utf-8")
    new_text = text[:m.start(2)] + value + text[m.end(2):]
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        raise Cs2Error(f"could not write {path}: {e}") from None
    return {"key": key, "previous": previous, "previous_label": SETTINGS[key].get(previous, previous),
            "applied": value, "applied_label": SETTINGS[key][value], "backup": backup_path.name}


def revert(backup_dir: Path, backup_name: str) -> dict:
    """Restores cs2_video.txt from a backup apply_setting made (only that exact naming is accepted, and only
    a file inside backup_dir, so this cannot be used to write an arbitrary file back over the settings)."""
    if not BACKUP_NAME_RE.fullmatch(backup_name or ""):
        raise Cs2Error("not a settings backup made by this app")
    backup_path = backup_dir / backup_name
    if backup_path.parent != backup_dir or not backup_path.is_file():
        raise Cs2Error("that backup no longer exists")
    if cs2_running():
        raise Cs2Error("CS2 is running: close it first, otherwise it overwrites this file with its own settings on exit")
    path = video_settings_path()
    if path is None:
        raise Cs2Error("CS2's settings file was not found")
    try:
        path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as e:
        raise Cs2Error(f"could not write {path}: {e}") from None
    return {"restored_from": backup_name}
