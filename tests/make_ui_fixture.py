"""Builds the data folder the interface check (ui_smoke.mjs) runs on: a synthetic history, a play session, two benchmark
batch variants with repeats and two saved chats. Run: python tests/make_ui_fixture.py <folder>"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_games import _write_pm  # noqa: E402


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "bench").mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE.parent / "scripts" / "seed_fake.py"), "--out", str(root / "metrics.db")], check=True)
    shutil.copy(_write_pm([(30, 4, 3, 5)]), root / "sessions" / "cs2_20260919_120000.csv")
    for variant, frame_ms in (("base", 4), ("fsr3", 3)):                  # a batch "demo": fsr3 is faster than base
        for n in (1, 2):
            shutil.copy(_write_pm([(30, frame_ms, frame_ms - 1, frame_ms - 1)]), root / "bench" / f"demo_{variant}_{n}.csv")
    chats = [{"id": "aaa111", "title": "How much free space do I have?", "updated": 1790000200,
              "log": [{"who": "me", "text": "How much free space do I have?"}, {"who": "bot", "text": "You have 218 GB free on C:.", "used": ["disk_usage"]}]},
             {"id": "bbb222", "title": "Is my antivirus on?", "updated": 1790000100,
              "log": [{"who": "me", "text": "Is my antivirus on?"}, {"who": "bot", "text": "Real-time protection is off.", "used": ["system_health"]}]}]
    (root / "chats.json").write_text(json.dumps({"chats": chats}), encoding="utf-8")
    print("fixture ready in", root)


if __name__ == "__main__":
    main()
