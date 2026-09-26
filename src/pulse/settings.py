"""The user's choices for the background jobs, kept in data/settings.json next to the database. Every value is checked
against a fixed list or a strict pattern: nothing typed by the user (or sent by a web page) is used as-is, and the values
that reach the PowerShell script are only digits and a time like 09:30."""
import json
import re
from pathlib import Path

DEFAULTS = {"collect_interval": 30, "alerts_interval": 15, "alerts_min_severity": "medium",
            "quiet_on": False, "quiet_from": "23:00", "quiet_to": "09:00", "digest_time": "09:00",
            "auto_record": False, "auto_record_gb": 5}
CHOICES = {"collect_interval": (10, 15, 30, 60, 120, 300), "alerts_interval": (5, 15, 30, 60, 120, 360),
           "alerts_min_severity": ("high", "medium"), "auto_record_gb": (1, 2, 5, 10, 20, 50)}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TIME_KEYS = ("quiet_from", "quiet_to", "digest_time")


def clean(values: dict) -> dict:
    """The known keys of `values`, validated. Anything else raises ValueError with a message for the user."""
    out = {}
    for k, v in (values or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k}")
        if k in CHOICES:
            if isinstance(v, bool) or v not in CHOICES[k]:
                raise ValueError(f"{k}: choose one of {', '.join(map(str, CHOICES[k]))}")
        elif k in TIME_KEYS:
            if not isinstance(v, str) or not _TIME.match(v):
                raise ValueError(f"{k}: use a time like 09:30")
        elif not isinstance(v, bool):
            raise ValueError(f"{k}: must be on or off")
        out[k] = v
    return out


def load(data_dir) -> dict:
    try:
        stored = json.loads((Path(data_dir) / "settings.json").read_text(encoding="utf-8"))
        stored = clean({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError, AttributeError):
        stored = {}
    return {**DEFAULTS, **stored}


def save(data_dir, values: dict) -> dict:
    merged = {**load(data_dir), **clean(values)}
    (Path(data_dir) / "settings.json").write_text(json.dumps(merged, indent=1), encoding="utf-8")
    return merged


def in_quiet_hours(cfg: dict, minutes_of_day: int) -> bool:
    """True when notifications should stay silent now. The window may cross midnight (23:00 to 09:00)."""
    if not cfg.get("quiet_on"):
        return False
    to_min = lambda s: int(s[:2]) * 60 + int(s[3:])
    a, b = to_min(cfg["quiet_from"]), to_min(cfg["quiet_to"])
    if a == b:
        return False
    return a <= minutes_of_day < b if a < b else (minutes_of_day >= a or minutes_of_day < b)
