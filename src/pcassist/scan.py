"""Read-only folder size scanner. Never modifies, moves or deletes anything."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

GB = 1024**3
REPARSE_POINT = 0x400  # Windows junctions/symlinks: skip to avoid loops and double counting


def _dir_size(root: str, deadline: float) -> tuple[int, bool]:
    """Total size of files under root. Returns (bytes, complete). complete=False if time ran out."""
    total, stack = 0, [root]
    while stack:
        if time.monotonic() > deadline:
            return total, False
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        if getattr(st, "st_file_attributes", 0) & REPARSE_POINT or e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += st.st_size
                    except OSError:
                        continue
        except OSError:  # access denied, vanished directory
            continue
    return total, True


def largest_children(path: str, limit: int = 10, max_seconds: int = 45) -> dict:
    """Sizes of the immediate subfolders (and loose files) of `path`, largest first."""
    root = Path(path)
    if not root.is_dir():
        return {"error": f"'{path}' is not a directory"}
    deadline = time.monotonic() + max_seconds
    children, loose = [], 0
    try:
        for e in os.scandir(root):
            try:
                st = e.stat(follow_symlinks=False)
                if getattr(st, "st_file_attributes", 0) & REPARSE_POINT or e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    children.append(e.path)
                else:
                    loose += st.st_size
            except OSError:
                continue
    except OSError as err:
        return {"error": f"cannot read '{path}': {err}"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda c: (c, *_dir_size(c, deadline)), children))

    rows = sorted(results, key=lambda r: r[1], reverse=True)
    return {
        "path": str(root),
        "complete": all(ok for _, _, ok in results),
        "loose_files_gb": round(loose / GB, 2),
        "folders": [{"folder": p, "size_gb": round(s / GB, 2), "complete": ok} for p, s, ok in rows[:limit]],
    }
