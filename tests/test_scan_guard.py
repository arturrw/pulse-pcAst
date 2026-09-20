"""Unit tests for the path check on the model-facing folder scan. Run: python tests/test_scan_guard.py (or pytest)."""
import os
import tempfile
from pathlib import Path

from vigil import scan, tools


def _sandbox() -> tuple[Path, set[str]]:
    """A temp folder with three subfolders, and the 'fixed drives' set that contains its drive."""
    root = Path(tempfile.mkdtemp())
    for name, size in (("big", 4000), ("mid", 1000), ("small", 10)):
        (root / name).mkdir()
        (root / name / "f.bin").write_bytes(b"x" * size)
    return root, {os.path.splitdrive(str(root.resolve()))[0] + "\\"}


def test_a_folder_on_a_fixed_drive_is_accepted_and_resolved():
    root, drives = _sandbox()
    real, why = scan.check_model_path(str(root), drives)
    assert why is None and Path(real) == root.resolve()
    through_dotdot, why = scan.check_model_path(str(root / "big" / ".."), drives)      # ".." is resolved before the check
    assert why is None and Path(through_dotdot) == root.resolve()


def test_network_device_relative_and_odd_paths_are_refused():
    root, drives = _sandbox()
    bad = {
        "\\\\server\\share\\folder": "absolute path on a local drive",             # UNC
        "\\\\?\\C:\\Windows": "absolute path on a local drive",                     # extended-length
        "\\\\.\\PhysicalDrive0": "absolute path on a local drive",                  # device
        "Users\\me": "absolute path on a local drive",                              # relative
        "..\\..\\Windows": "absolute path on a local drive",
        "": "empty path",
        "   ": "empty path",
        str(root) + "\n": None,                                                     # trailing newline is stripped: fine
        "C:\\Users\x00": "control characters",
    }
    for path, expected in bad.items():
        real, why = scan.check_model_path(path, drives)
        if expected is None:
            assert why is None, path
        else:
            assert real is None and expected in why, (path, why)


def test_a_drive_that_is_not_fixed_and_a_plain_file_are_refused():
    root, drives = _sandbox()
    real, why = scan.check_model_path("Z:\\anything", drives)
    assert real is None and "not a local fixed drive" in why
    real, why = scan.check_model_path(str(root / "big" / "f.bin"), drives)
    assert real is None and "not a directory" in why
    real, why = scan.check_model_path(str(root), set())                                  # no fixed drives known: refuse all
    assert real is None and "not a local fixed drive" in why


def test_the_tool_refuses_and_clamps_the_row_limit():
    root, drives = _sandbox()
    real_fixed = tools.fixed_drives
    tools.fixed_drives = lambda: drives
    try:
        refused = tools.largest_folders("\\\\server\\share")
        assert refused["error"].startswith("not scanned:") and "folders" not in refused
        ok = tools.largest_folders(str(root), limit=1000)
        assert [Path(f["folder"]).name for f in ok["folders"]] == ["big", "mid", "small"]   # largest first
        assert len(tools.largest_folders(str(root), limit=0)["folders"]) == 1                # at least one row
        assert len(tools.largest_folders(str(root), limit=2)["folders"]) == 2
    finally:
        tools.fixed_drives = real_fixed


def test_the_cli_scan_is_not_restricted():
    root, _ = _sandbox()
    assert scan.largest_children(str(root))["folders"]                                    # the user's own command still works anywhere


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
