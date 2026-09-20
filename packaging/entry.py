import os
import sys

# The windowless build has no console: give print() somewhere to go instead of crashing.
for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))

from pcassist.cli import main  # noqa: E402

raise SystemExit(main())
