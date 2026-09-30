"""The notes of one version from CHANGELOG.md, for its GitHub release and latest.json (the update banner shows them).
    python packaging/release_notes.py 0.4.4      -> prints the section, exits 1 when the version has none"""
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def notes(version: str, text: str) -> str | None:
    """The body of the "## <version>" section, without the heading; None when there is no such section or it is empty."""
    m = re.search(rf"^## {re.escape(version)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    body = m.group(1).strip() if m else ""
    return body or None


if __name__ == "__main__":
    body = notes(sys.argv[1].lstrip("v"), CHANGELOG.read_text(encoding="utf-8"))
    if body is None:
        sys.exit(f"CHANGELOG.md has no section for {sys.argv[1]}: add one before tagging")
    sys.stdout.reconfigure(encoding="utf-8")
    print(body)
