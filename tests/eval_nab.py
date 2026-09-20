"""Score the anomaly detector on the Numenta Anomaly Benchmark (labeled anomaly windows).

Run: python tests/eval_nab.py [--nab data/nab] [--sweep]
NAB is not part of this repo: `git clone --depth 1 https://github.com/numenta/NAB data/nab` (MIT license, data
only is read, none of its code is run). Per category: how many labeled windows had at least one event inside
(recall) and how many events fired outside every window (false alarms per 10k points).
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pcassist import anomaly

CATEGORIES = ("realAWSCloudwatch", "realKnownCause", "artificialWithAnomaly")   # PC-like metrics + easy synthetic


def _ts(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def load(nab: Path, categories) -> list[dict]:
    windows = json.loads((nab / "labels" / "combined_windows.json").read_text())
    series = []
    for name, wins in sorted(windows.items()):
        if name.split("/")[0] not in categories:
            continue
        with open(nab / "data" / name, newline="") as f:
            rows = list(csv.reader(f))[1:]
        series.append({"name": name, "cat": name.split("/")[0], "t": [_ts(r[0]) for r in rows],
                       "v": [float(r[1]) for r in rows], "windows": [(_ts(a), _ts(b)) for a, b in wins]})
    return series


def evaluate(series: list[dict], window: int, threshold: float, merge: int, cache: dict, min_run: int = 1) -> dict:
    stats = defaultdict(lambda: {"windows": 0, "hit": 0, "false": 0, "points": 0})
    for s in series:
        key = (s["name"], window)
        if key not in cache:
            cache[key] = anomaly.scores(s["v"], window)
        evs = anomaly.events(cache[key], threshold, merge, min_run)
        st = stats[s["cat"]]
        st["points"] += len(s["v"])
        st["windows"] += len(s["windows"])
        st["hit"] += sum(any(any(a <= s["t"][i] <= b for i in range(e[0], e[1] + 1)) for e in evs)
                         for a, b in s["windows"])
        st["false"] += sum(not any(a <= s["t"][e[0]] <= b or a <= s["t"][e[1]] <= b for a, b in s["windows"])
                           for e in evs)
    return stats


def show(stats: dict, label: str) -> None:
    tw = sum(v["windows"] for v in stats.values())
    th = sum(v["hit"] for v in stats.values())
    tf = sum(v["false"] for v in stats.values())
    tp = sum(v["points"] for v in stats.values())
    print(f"{label:<28} recall {th}/{tw} ({th / tw:.0%}) | false alarms {tf} ({10000 * tf / tp:.1f} per 10k points)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--nab", default="data/nab")
    p.add_argument("--window", type=int, default=288, help="rolling window in samples (288 = 1 day at 5 min)")
    p.add_argument("--threshold", type=float, default=8.0)
    p.add_argument("--merge", type=int, default=12)
    p.add_argument("--min-run", type=int, default=1, help="consecutive samples above the threshold to count")
    p.add_argument("--sweep", action="store_true", help="try several windows and thresholds")
    args = p.parse_args()
    nab = Path(args.nab)
    if not (nab / "labels" / "combined_windows.json").exists():
        print(f"NAB not found in {nab}; see the docstring of this file")
        return 1
    series = load(nab, CATEGORIES)
    cache: dict = {}
    if args.sweep:
        for w in (288,):
            for k in (5, 8, 12):
                for run in (1, 3, 6, 12):
                    show(evaluate(series, w, k, args.merge, cache, run), f"window {w}, thr {k}, min_run {run}")
        return 0
    stats = evaluate(series, args.window, args.threshold, args.merge, cache, args.min_run)
    for cat, v in stats.items():
        print(f"{cat:<24} windows {v['hit']}/{v['windows']} hit | false alarms {v['false']} "
              f"({10000 * v['false'] / v['points']:.1f} per 10k points)")
    show(stats, "TOTAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
