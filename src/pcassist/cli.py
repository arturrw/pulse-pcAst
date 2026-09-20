import argparse
import time
from pathlib import Path

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
    last_prune = 0.0
    failures = 0
    try:
        while True:
            if args.keep_days and time.time() - last_prune >= 86400:
                removed = db.prune(conn, args.keep_days)
                last_prune = time.time()
                if removed:
                    print(f"Pruned {removed} rows older than {args.keep_days} days.")
            try:
                sample = collector.sample()
                db.save_sample(conn, sample)
                _print_sample(sample)
                failures = 0
            except Exception as e:
                # Long-running background job: one bad sample (NVML hiccup, locked DB) must not kill it.
                # After repeated failures exit, so the autostart watchdog starts a fresh process.
                if args.once:
                    raise
                failures += 1
                print(f"Sample failed ({failures}/10): {type(e).__name__}: {e}")
                if failures >= 10:
                    raise
            if args.once:
                break
            time.sleep(max(args.interval - 1, 0))
    except KeyboardInterrupt:
        print("Stopped.")


def cmd_prune(args: argparse.Namespace) -> None:
    conn = db.connect(args.db)
    removed = db.prune(conn, args.days)
    conn.execute("VACUUM")  # give the freed space back to the file system
    print(f"Removed {removed} rows older than {args.days} days.")


def cmd_scan(args: argparse.Namespace) -> None:
    from .scan import largest_children

    r = largest_children(args.path, args.limit, args.seconds)
    if "error" in r:
        raise SystemExit(r["error"])
    print(f"{r['path']}  (loose files: {r['loose_files_gb']} GB)")
    for f in r["folders"]:
        print(f"{f['size_gb']:>9.2f} GB  {f['folder']}" + ("" if f["complete"] else "  (partial: time limit)"))


def _hhmmss(text: str | None):
    if not text:
        return None
    from datetime import datetime

    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    raise SystemExit(f"bad time {text!r}, use HH:MM or HH:MM:SS")


def _analyze(pm_csv, hml, args):
    from . import games

    try:
        return games.analyze(pm_csv, hml, args.process, _hhmmss(getattr(args, "start", None)),
                             _hhmmss(getattr(args, "end", None)), args.pm_offset_hours)
    except (OSError, ValueError) as e:
        raise SystemExit(f"{pm_csv}: {e}")


def cmd_session(args: argparse.Namespace) -> None:
    from . import games

    if args.session_cmd == "report":
        print(games.format_report(_analyze(args.presentmon, args.hml, args)))
    elif args.session_cmd == "summary":
        paths = sorted(Path(args.folder).glob(f"{args.tag}_*.csv"))
        print(games.summarize_runs(paths, args.process, args.slices))
    else:
        a = _analyze(args.before, args.hml_before, args)
        b = _analyze(args.after, args.hml_after, args)
        print(games.format_compare(a, b))


def cmd_chat(args: argparse.Namespace) -> None:
    from . import tools
    from .chat import run_chat

    tools.set_db(args.db)
    run_chat(args.model, args.think, args.num_ctx)


def cmd_report(args: argparse.Namespace) -> None:
    import os

    from . import report, tools

    tools.set_db(args.db)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.build_report(args.hours), encoding="utf-8")
    print(f"Report written to {out.resolve()}")
    if args.open:
        os.startfile(out.resolve())   # Windows: the default browser


def cmd_alerts(args: argparse.Namespace) -> None:
    from . import alerts

    if args.test:
        ok = alerts.notify("pcassist test", "If you can read this, alerts can reach you.")
        print("Notification shown." if ok else "The notification could not be shown (see README, Alerts).")
        return
    found = alerts.run_once(args.db, dry_run=args.dry_run)
    verb = "Would send" if args.dry_run else "Sent"
    print(f"{verb} {len(found)} alert(s).")
    for a in found:
        print(f"  [{a.severity}] {a.title}: {a.body}")


def cmd_ack(args: argparse.Namespace) -> None:
    from . import ack

    if args.forget:
        print("No longer accepted." if ack.forget(args.db, args.forget) else f"'{args.forget}' was not in the list.")
    elif args.finding:
        e = ack.acknowledge(args.db, args.finding, args.note or "")
        print(f"Accepted '{args.finding}' since {e['since']}. It stays in the report, marked accepted; it no longer alerts.")
    else:
        acked = ack.load(args.db)
        if not acked:
            print("No accepted risks. Accept one with: pcassist ack <finding id> --note \"why\"")
        for k, v in sorted(acked.items()):
            print(f"{k}  (since {v.get('since', '?')})  {v.get('note', '')}")


def cmd_digest(args: argparse.Namespace) -> None:
    from . import digest

    d = digest.run(args.db, dry_run=args.dry_run, refresh_report=not args.no_report)
    print(d["title"])
    for line in d["lines"]:
        print("  " + line)
    for item in d["todo"]:
        print("  ! " + item)
    if not args.dry_run:
        print("Notification shown." if d.get("shown") else "The notification could not be shown; it is in data/digest.log.")


def cmd_netstats(args: argparse.Namespace) -> None:
    from . import netstats

    workdir = Path(args.db).parent / "netstats"
    try:
        rows = netstats.capture(args.seconds, workdir, keep=args.keep, progress=print)
    except netstats.NetstatsError as e:
        raise SystemExit(f"netstats: {e}")
    if args.debug:
        ids = {}
        for r in rows:
            ids[r["id"]] = ids.get(r["id"], 0) + 1
        print(f"debug: {len(rows)} aggregated records, per event id: {ids}; first: {rows[:2]}")
    result = netstats.summarize(rows, netstats.names_of({r["pid"] for r in rows}), args.seconds, args.all)
    print(netstats.format_report(result, args.seconds, args.all))
    conn = db.connect(args.db)
    netstats.save(conn, result, args.seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcassist")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="collect metrics into SQLite")
    c.add_argument("--interval", type=int, default=30, help="seconds between samples")
    c.add_argument("--once", action="store_true", help="take one sample and exit")
    c.add_argument("--keep-days", type=int, default=90,
                   help="delete history older than this many days (checked at start and daily; 0 = keep all)")
    c.add_argument("--db", default=str(db.DEFAULT_DB))
    c.set_defaults(func=cmd_collect)

    pr = sub.add_parser("prune", help="delete history older than N days and shrink the database file")
    pr.add_argument("--days", type=int, default=90)
    pr.add_argument("--db", default=str(db.DEFAULT_DB))
    pr.set_defaults(func=cmd_prune)

    sc = sub.add_parser("scan", help="show the largest subfolders of a directory (read-only)")
    sc.add_argument("path", nargs="?", default="C:\\")
    sc.add_argument("--limit", type=int, default=10)
    sc.add_argument("--seconds", type=int, default=120, help="time budget for the scan")
    sc.set_defaults(func=cmd_scan)

    se = sub.add_parser("session", help="analyze a recorded game session (PresentMon CSV + Afterburner log)")
    se_sub = se.add_subparsers(dest="session_cmd", required=True)
    rep = se_sub.add_parser("report", help="FPS, lows, limiter, hitches and hardware stats of one session")
    rep.add_argument("presentmon", help="PresentMon CSV recorded with --date_time")
    rep.add_argument("--hml", help="Afterburner .hml log of the same session (adds temperatures, VRAM, CPU)")
    rep.add_argument("--start", help="window start HH:MM[:SS] (default: auto, cuts off loading)")
    rep.add_argument("--end", help="window end HH:MM[:SS]")
    cmp_ = se_sub.add_parser("compare", help="deltas between two sessions (before -> after a settings change)")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    cmp_.add_argument("--hml-before")
    cmp_.add_argument("--hml-after")
    sm = se_sub.add_parser("summary", help="table of repeated runs (<tag>_<variant>_<n>.csv) with spread")
    sm.add_argument("folder")
    sm.add_argument("--tag", required=True)
    sm.add_argument("--process", default=None)
    sm.add_argument("--slices", action="store_true", help="also FPS per 10 s of the route, per variant")
    for p_ in (rep, cmp_):
        p_.add_argument("--process", default=None, help="only frames of this exe, e.g. cs2.exe")
        p_.add_argument("--pm-offset-hours", type=float, default=None,
                        help="PresentMon time minus local time (default: auto-detected from the .hml)")
    se.set_defaults(func=cmd_session)

    ch = sub.add_parser("chat", help="ask questions about your PC via a local Ollama model")
    ch.add_argument("--model", default="qwen3:8b")
    ch.add_argument("--think", action="store_true", help="enable model reasoning (slower)")
    ch.add_argument("--num-ctx", type=int, default=8192, help="context window in tokens")
    ch.add_argument("--db", default=str(db.DEFAULT_DB))
    ch.set_defaults(func=cmd_chat)

    rp = sub.add_parser("report", help="write an HTML report (charts, unusual periods, disks, games) from the history")
    rp.add_argument("--hours", type=float, default=24, help="how far back to look")
    rp.add_argument("--out", default=str(db.DEFAULT_DB.parent / "reports" / "latest.html"))
    rp.add_argument("--open", action="store_true", help="open the report in the default browser")
    rp.add_argument("--db", default=str(db.DEFAULT_DB))
    rp.set_defaults(func=cmd_report)

    al = sub.add_parser("alerts", help="check the history once and show a Windows notification for what needs attention")
    al.add_argument("--dry-run", action="store_true", help="only print what would be sent; send and remember nothing")
    al.add_argument("--test", action="store_true", help="show a test notification and exit")
    al.add_argument("--db", default=str(db.DEFAULT_DB))
    al.set_defaults(func=cmd_alerts)

    dg = sub.add_parser("digest", help="a short summary of the last 24 h as one notification (and a fresh report)")
    dg.add_argument("--dry-run", action="store_true", help="only print it; show, log and write nothing")
    dg.add_argument("--no-report", action="store_true", help="do not refresh data/reports/latest.html")
    dg.add_argument("--db", default=str(db.DEFAULT_DB))
    dg.set_defaults(func=cmd_digest)

    ns = sub.add_parser("netstats", help="who sends and receives how much (run in a terminal opened as administrator)")
    ns.add_argument("--seconds", type=int, default=60, help="how long to measure")
    ns.add_argument("--all", action="store_true", help="include loopback and LAN traffic")
    ns.add_argument("--keep", action="store_true", help="keep the trace file (data/netstats/net.etl): it holds the addresses you talked to")
    ns.add_argument("--debug", action="store_true", help="print how many records of each event id were read")
    ns.add_argument("--db", default=str(db.DEFAULT_DB))
    ns.set_defaults(func=cmd_netstats)

    ak = sub.add_parser("ack", help="accept a finding you know about (it stops alerting, stays in the report) or list them")
    ak.add_argument("finding", nargs="?", help="finding id, e.g. defender-realtime-off; an id ending in * matches a prefix")
    ak.add_argument("--note", help="why you accept it")
    ak.add_argument("--forget", metavar="ID", help="stop accepting this finding")
    ak.add_argument("--db", default=str(db.DEFAULT_DB))
    ak.set_defaults(func=cmd_ack)

    args = parser.parse_args(argv)
    args.func(args)
    return 0
