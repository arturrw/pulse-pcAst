import argparse
import time

from . import db
from .collectors import Collector


def _print_sample(s: dict) -> None:
    sy = s["system"]
    print(
        f"CPU {sy['cpu_percent']:.0f}% | RAM {sy['ram_percent']:.0f}% "
        f"({sy['ram_used_mb'] / 1024:.1f} GB) | disk R/W "
        f"{sy['disk_read_mbps']:.1f}/{sy['disk_write_mbps']:.1f} MB/s"
    )
    for g in s["gpus"]:
        print(
            f"GPU {g['name']}: {g['util_percent']:.0f}% | VRAM "
            f"{g['mem_used_mb']:.0f}/{g['mem_total_mb']:.0f} MB | {g['temp_c']}°C | {g['power_w']:.0f} W"
        )
    top = sorted(s["processes"], key=lambda r: r["cpu_percent"], reverse=True)[:3]
    print("Top CPU: " + ", ".join(f"{p['name']} {p['cpu_percent']:.1f}%" for p in top))


def cmd_collect(args: argparse.Namespace) -> None:
    conn = db.connect(args.db)
    collector = Collector()
    print(f"Writing to {args.db}. Ctrl+C to stop.")
    try:
        while True:
            sample = collector.sample()
            db.save_sample(conn, sample)
            _print_sample(sample)
            if args.once:
                break
            time.sleep(max(args.interval - 1, 0))
    except KeyboardInterrupt:
        print("Stopped.")


def cmd_scan(args: argparse.Namespace) -> None:
    from .scan import largest_children

    r = largest_children(args.path, args.limit, args.seconds)
    if "error" in r:
        raise SystemExit(r["error"])
    print(f"{r['path']}  (loose files: {r['loose_files_gb']} GB)")
    for f in r["folders"]:
        print(f"{f['size_gb']:>9.2f} GB  {f['folder']}" + ("" if f["complete"] else "  (partial: time limit)"))


def cmd_chat(args: argparse.Namespace) -> None:
    from . import tools
    from .chat import run_chat

    tools.set_db(args.db)
    run_chat(args.model, args.think, args.num_ctx)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcassist")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="collect metrics into SQLite")
    c.add_argument("--interval", type=int, default=30, help="seconds between samples")
    c.add_argument("--once", action="store_true", help="take one sample and exit")
    c.add_argument("--db", default=str(db.DEFAULT_DB))
    c.set_defaults(func=cmd_collect)

    sc = sub.add_parser("scan", help="show the largest subfolders of a directory (read-only)")
    sc.add_argument("path", nargs="?", default="C:\\")
    sc.add_argument("--limit", type=int, default=10)
    sc.add_argument("--seconds", type=int, default=120, help="time budget for the scan")
    sc.set_defaults(func=cmd_scan)

    ch = sub.add_parser("chat", help="ask questions about your PC via a local Ollama model")
    ch.add_argument("--model", default="qwen3:8b")
    ch.add_argument("--think", action="store_true", help="enable model reasoning (slower)")
    ch.add_argument("--num-ctx", type=int, default=8192, help="context window in tokens")
    ch.add_argument("--db", default=str(db.DEFAULT_DB))
    ch.set_defaults(func=cmd_chat)

    args = parser.parse_args(argv)
    args.func(args)
    return 0
