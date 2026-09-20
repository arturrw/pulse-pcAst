"""Where things live: next to the source checkout normally, or in the user's profile when the program is a frozen app
(installed under Program Files, where nothing may be written)."""
import os
import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    if frozen():
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "pcassist"
    return Path(__file__).resolve().parents[2] / "data"


def resource(*parts: str) -> Path:
    """A file shipped with the program (scripts/autostart.ps1)."""
    base = Path(getattr(sys, "_MEIPASS", "")) if frozen() else Path(__file__).resolve().parents[2]
    return base.joinpath(*parts)
