"""How much data each process sent and received: an on-demand measurement, run by the user from an elevated terminal.

Windows does not give per-process network volume to normal programs (checked: the I/O counters of a process that moves
30 MB over a socket show 0). The only real source is ETW, the event tracing of the system, and starting a trace needs
administrator rights. So this is a command you run yourself when you want to know "who is uploading right now":
    pcassist netstats --seconds 60        (in a terminal opened as administrator)
It starts a short trace of Microsoft-Windows-Kernel-Network with `logman`, stops it, adds the sizes up per process and
destination, prints the result and DELETES the trace file (it holds the addresses you talked to). Nothing stays running
with high rights, nothing is changed on the machine besides the temporary trace session, and nothing is sent anywhere.

Payload sizes as the network stack saw them, TCP and UDP, IPv4 and IPv6. Loopback and LAN traffic is left out unless
asked for. A big upload from a browser is normal; the point is to see who talks how much, not to judge it."""
import ctypes
import ipaddress
import json
import os
import socket
import struct
import subprocess
import time
from pathlib import Path

SESSION = "pcassist-net"
PROVIDER = "Microsoft-Windows-Kernel-Network"
# Event ids of the provider: data sent / received, TCP and UDP, IPv4 and IPv6.
SEND_IDS = {10, 26, 42, 58}
RECV_IDS = {11, 27, 43, 59}
UPLOAD_NOTICE_BYTES = 20 * 1024 * 1024      # a process that sent this much in one measurement is pointed out

_READ_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$ids = 10,11,26,27,42,43,58,59
$agg = @{}
Get-WinEvent -Path $env:PCA_ETL -Oldest | Where-Object { $ids -contains $_.Id } | ForEach-Object {
  $p = $_.Properties
  $key = "$($_.Id)|$($p[0].Value)|$($p[2].Value)|$($p[3].Value)|$($p[4].Value)|$($p[5].Value)"
  $agg[$key] = [int64]$agg[$key] + [int64]$p[1].Value
}
$out = foreach ($k in $agg.Keys) { $f = $k -split '\|'; [pscustomobject]@{ id = [int]$f[0]; pid = [int]$f[1]; d = $f[2]; s = $f[3]; dp = [int]$f[4]; sp = [int]$f[5]; size = $agg[$k] } }
ConvertTo-Json -InputObject @($out) -Compress
"""


class NetstatsError(Exception):
    """Something the user can act on (not elevated, logman failed, ...): the message says what."""


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _ip(value) -> str:
    """ETW hands out IPv4 addresses as a number (also as digits in a string, as PowerShell prints it) read from network
    byte order; IPv6 addresses arrive as text. Return dotted text either way."""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, int):
        return socket.inet_ntoa(struct.pack("<I", value & 0xFFFFFFFF))
    return str(value).strip()


def _port(value) -> int:
    """Ports arrive with their two bytes swapped (443 shows up as 47873)."""
    p = int(value) & 0xFFFF
    return ((p & 0xFF) << 8) | (p >> 8)


def _peer(row: dict) -> tuple[str, int]:
    """The remote end of a record. The provider names the fields from the connection's point of view, not the packet's
    (measured: on a receive event the public server is `daddr`), so do not trust the names: the public address is the
    remote one, and when neither or both are public, daddr is."""
    d, s = _ip(row["d"]), _ip(row["s"])
    d_pub, s_pub = _is_remote(d, False), _is_remote(s, False)
    if s_pub and not d_pub:
        return s, _port(row["sp"])
    return d, _port(row["dp"])


def _is_remote(addr: str, include_local: bool) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return include_local
    return include_local or ip.is_global


def summarize(rows: list[dict], names: dict[int, str] | None = None, seconds: float = 60.0, include_local: bool = False) -> list[dict]:
    """Per process: bytes sent and received, and where the most went. `rows` are the aggregated ETW records
    {id, pid, d (destination address), s (source address), dp, sp, size}."""
    names = names or {}
    procs: dict[int, dict] = {}
    for r in rows:
        send = r["id"] in SEND_IDS
        if not send and r["id"] not in RECV_IDS:
            continue
        remote, port = _peer(r)
        if not _is_remote(remote, include_local):
            continue
        p = procs.setdefault(r["pid"], {"pid": r["pid"], "sent": 0, "received": 0, "dest": {}})
        p["sent" if send else "received"] += int(r["size"])
        key = f"{remote}:{port}"
        p["dest"][key] = p["dest"].get(key, 0) + int(r["size"])
    out = []
    for pid, p in procs.items():
        top = sorted(p["dest"].items(), key=lambda kv: -kv[1])[:3]
        out.append({"pid": pid, "name": names.get(pid) or f"pid {pid} (gone)", "sent": p["sent"], "received": p["received"],
                    "sent_per_second": p["sent"] / max(seconds, 1e-9), "top_destinations": [{"to": k, "bytes": v} for k, v in top]})
    out.sort(key=lambda x: (-x["sent"], -x["received"]))
    return out


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def format_report(result: list[dict], seconds: float, include_local: bool) -> str:
    scope = "all traffic" if include_local else "traffic to public addresses (loopback and LAN left out)"
    lines = [f"Network traffic per process over {seconds:.0f} s, {scope}:", ""]
    if not result:
        return "\n".join(lines + ["Nothing was sent or received in this window."])
    lines.append(f"{'process':<28}{'sent':>10}{'received':>11}   biggest destination")
    for r in result[:15]:
        top = r["top_destinations"][0] if r["top_destinations"] else None
        dest = f"{top['to']} ({human(top['bytes'])})" if top else ""
        lines.append(f"{r['name'][:27]:<28}{human(r['sent']):>10}{human(r['received']):>11}   {dest}")
    heavy = [r for r in result if r["sent"] >= UPLOAD_NOTICE_BYTES]
    if heavy:
        lines += ["", "Sent a lot in this window: " + ", ".join(f"{r['name']} ({human(r['sent'])})" for r in heavy)
                  + ". A browser or a sync client uploading a file is normal; a program you do not know uploading is worth a look."]
    lines += ["", "This is one short window: run it again while something seems wrong."]
    return "\n".join(lines)


def _run(cmd: list[str], env: dict | None = None, timeout: int = 300) -> tuple[int, str]:
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                         env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def capture(seconds: int, workdir: Path, runner=_run, sleep=time.sleep, admin=is_admin, keep: bool = False,
            progress=lambda text: None) -> list[dict]:
    """Run the trace and return the aggregated records. Raises NetstatsError with a plain explanation."""
    if not admin():
        raise NetstatsError("this needs administrator rights (Windows only lets an administrator start a network trace). "
                            "Open a terminal as administrator (Win+X, then Terminal (Admin)) and run the command again.")
    workdir.mkdir(parents=True, exist_ok=True)
    etl = workdir / "net.etl"
    runner(["logman", "stop", SESSION, "-ets"])                       # a leftover session from an earlier run, ignore the result
    code, out = runner(["logman", "start", SESSION, "-p", PROVIDER, "0xffffffffffffffff", "0xff", "-o", str(etl), "-ets"])
    if code != 0:
        raise NetstatsError(f"could not start the trace: {out.strip()[:300]}")
    try:
        progress(f"Measuring for {seconds} s ...")
        sleep(seconds)
    finally:
        runner(["logman", "stop", SESSION, "-ets"])                   # always stop it, even if interrupted
    try:
        progress("Adding the sizes up (this can take a moment) ...")
        code, out = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", _READ_PS], env={**os.environ, "PCA_ETL": str(etl)}, timeout=900)
        try:
            rows = json.loads(out) if out.strip() else []
        except ValueError:
            raise NetstatsError(f"could not read the trace: {out.strip()[:300]}") from None
        return rows if isinstance(rows, list) else [rows]
    finally:
        if not keep:
            for f in (etl, etl.with_suffix(".etl.gz")):
                try:
                    f.unlink()
                except OSError:
                    pass


def names_of(pids) -> dict[int, str]:
    import psutil

    out = {}
    for pid in pids:
        try:
            out[pid] = psutil.Process(pid).name()
        except psutil.Error:
            pass
    return out


def save(conn, result: list[dict], seconds: float, now: float | None = None) -> None:
    now = time.time() if now is None else now
    for r in result[:20]:
        top = r["top_destinations"][0]["to"] if r["top_destinations"] else ""
        conn.execute("INSERT INTO net_traffic (ts, name, sent, received, seconds, top_destination) VALUES (?,?,?,?,?,?)",
                     (now, r["name"], r["sent"], r["received"], seconds, top))
    conn.commit()
