"""Accepted risks: findings the user has looked at and decided to live with.

A finding has a stable id (like "defender-realtime-off" or "autorun:scheduled_task:MyTask"). Once acknowledged it is
no longer alerted and no longer counts as a problem in the tools, but it stays visible in the report, marked
"accepted", with the note and the date, so an accepted risk is never invisible. An id ending in * matches a prefix
("crash:*"). The list is a small JSON file next to the database; deleting an entry makes the finding count again.

This changes nothing on the machine: it only decides what the assistant nags about."""
import json
import time
from pathlib import Path


def _file(db_path) -> Path:
    return Path(db_path).parent / "acknowledged.json"


def load(db_path) -> dict[str, dict]:
    try:
        data = json.loads(_file(db_path).read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def match(acked: dict[str, dict], finding_id: str) -> dict | None:
    """The acknowledgement covering this finding id (exact, or a `prefix*` entry), or None."""
    if finding_id in acked:
        return acked[finding_id]
    for key, entry in acked.items():
        if key.endswith("*") and finding_id.startswith(key[:-1]):
            return entry
    return None


def acknowledge(db_path, finding_id: str, note: str = "", now: float | None = None) -> dict:
    acked = load(db_path)
    acked[finding_id] = {"note": note, "since": time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))}
    _file(db_path).write_text(json.dumps(acked, ensure_ascii=False, indent=1), encoding="utf-8")
    return acked[finding_id]


def forget(db_path, finding_id: str) -> bool:
    acked = load(db_path)
    if finding_id not in acked:
        return False
    del acked[finding_id]
    _file(db_path).write_text(json.dumps(acked, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def mark(db_path, findings: list[dict]) -> list[dict]:
    """Add `accepted` (with the note) to every finding whose id is acknowledged."""
    acked = load(db_path)
    for f in findings:
        entry = match(acked, f.get("id", ""))
        f["accepted"] = bool(entry)
        if entry:
            f["accepted_note"] = entry.get("note", "")
            f["accepted_since"] = entry.get("since", "")
    return findings
