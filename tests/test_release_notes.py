"""The release notes come from CHANGELOG.md. Run: python tests/test_release_notes.py (or pytest)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
from release_notes import CHANGELOG, notes  # noqa: E402

TEXT = "# Changelog\n\nintro\n\n## 1.2.0\n\n- new thing\n  wrapped\n- other\n\n## 1.1.0\n\n- old\n\n## 1.0.0\n"


def test_a_section_is_its_body_up_to_the_next_version():
    assert notes("1.2.0", TEXT) == "- new thing\n  wrapped\n- other"
    assert notes("1.1.0", TEXT) == "- old"


def test_a_missing_or_empty_section_is_none():
    assert notes("1.0.0", TEXT) is None
    assert notes("1.3.0", TEXT) is None
    assert notes("1.2", TEXT) is None                   # a prefix of another version is not that version


def test_the_current_version_has_notes():
    # the installer workflow refuses to publish a version without them: catch it before the tag is pushed
    version = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    assert notes(version, CHANGELOG.read_text(encoding="utf-8")), f"CHANGELOG.md needs a '## {version}' section"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
