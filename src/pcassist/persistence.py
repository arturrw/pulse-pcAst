"""What starts by itself: Run keys, startup folders, scheduled tasks and services, and what changed in them.

Malware needs to survive a reboot, so a NEW autostart entry is one of the strongest signals there is. The first
snapshot is only a baseline (everything already there is "known"); after that every new or changed entry is reported,
and flagged when its command looks like a download-and-run trick, starts from a Temp / Downloads folder, or points at
a file with a broken signature.

Read-only: one PowerShell call lists the entries (no administrator rights needed), nothing is changed or removed.
Like everything here it is a hint for a human look, not a verdict: installers add entries all the time."""
import json
import os
import re
import subprocess
import time

from . import binaries

MAX_LISTED = 15
STALE_SECONDS = 600         # a snapshot younger than this is reused by the chat tool

_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$r = New-Object System.Collections.ArrayList
function Add-Item($kind, $name, $cmd, $detail) { [void]$r.Add([pscustomobject]@{ kind = $kind; name = "$name"; command = "$cmd"; detail = "$detail" }) }
foreach ($k in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce',
               'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce',
               'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run') {
  $p = Get-ItemProperty -LiteralPath $k
  if ($p) { $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { Add-Item 'run_key' (("$k" -replace 'Software\\Microsoft\\Windows\\CurrentVersion\\', '') + '\' + $_.Name) $_.Value '' } }
}
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in [Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup')) {
  Get-ChildItem -LiteralPath $dir -File | Where-Object { $_.Name -ne 'desktop.ini' } | ForEach-Object {
    $cmd = $_.FullName; if ($_.Extension -eq '.lnk') { $l = $sh.CreateShortcut($_.FullName); $cmd = ($l.TargetPath + ' ' + $l.Arguments).Trim() }
    Add-Item 'startup_folder' $_.Name $cmd $dir }
}
Get-ScheduledTask | ForEach-Object {
  $acts = @($_.Actions | ForEach-Object { if ($_.Execute) { ($_.Execute + ' ' + $_.Arguments).Trim() } else { '<COM handler>' } }) -join ' ; '
  Add-Item 'scheduled_task' ($_.TaskPath + $_.TaskName) $acts ($_.State.ToString() + ' as ' + $_.Principal.UserId)
}
Get-CimInstance Win32_Service | ForEach-Object { Add-Item 'service' $_.Name $_.PathName ($_.StartMode + ' ' + $_.State + ' as ' + $_.StartName) }
ConvertTo-Json -InputObject @($r) -Depth 3 -Compress
"""

# (regex on the command line, what it means)
PATTERNS = [
    (r"(?i)-e(nc|ncodedcommand)?\s+[A-Za-z0-9+/=]{20,}", "an encoded PowerShell command (hides what it runs)"),
    (r"(?i)frombase64string|-encodedcommand", "a Base64-encoded payload"),
    (r"(?i)\b(iex|invoke-expression)\b", "runs text as code (Invoke-Expression)"),
    (r"(?i)downloadstring|downloadfile|invoke-webrequest|\biwr\b|net\.webclient|start-bitstransfer", "downloads something from the internet"),
    (r"(?i)\bmshta(\.exe)?\b", "mshta (runs scripts from a web page)"),
    (r"(?i)certutil[^&|;]*-(urlcache|decode)|bitsadmin[^&|;]*/transfer", "a download / decode trick with a system tool"),
    (r"(?i)regsvr32[^&|;]*(/i:|scrobj)|rundll32[^&|;]*javascript", "a script started through regsvr32 / rundll32"),
    (r"(?i)https?://", "the command contains a web address"),
    (r"(?i)(wscript|cscript)(\.exe)?\s+[^&|;]*\.(vbs|js|jse|vbe|wsf)", "a Windows script host running a script file"),
]

_EXE = re.compile(r'^\s*"?([A-Za-z]:[\\/][^"]*?\.(?:exe|com|bat|cmd|ps1|vbs|js|scr|dll|sys))(?=["\s,]|$)', re.I)


def read_items() -> list[dict]:
    """[] when PowerShell cannot run: the caller must not treat an empty list as 'everything was removed'."""
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=120,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(res.stdout) if (res.stdout or "").strip() else []
        return data if isinstance(data, list) else [data]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def command_exe(command: str) -> str | None:
    """The file a command line starts: 'C:\\A B\\x.exe -k y' -> 'C:\\A B\\x.exe'; environment variables are expanded."""
    cmd = os.path.expandvars((command or "").strip())
    m = _EXE.match(cmd)
    return m.group(1) if m else None


def snapshot(conn, now: float | None = None, reader=read_items) -> int:
    """Record the entries seen right now. The first snapshot ever is the baseline. Returns the number of entries."""
    now = time.time() if now is None else now
    items = reader()
    if not items:
        return 0
    for it in items:
        conn.execute("INSERT INTO autoruns (kind, name, command, detail, first_seen, last_seen) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(kind, name, command) DO UPDATE SET last_seen = excluded.last_seen, detail = excluded.detail",
                     (it["kind"], it["name"], it["command"], it.get("detail", ""), now, now))
    conn.commit()
    return len(items)


def last_snapshot(conn) -> float | None:
    return conn.execute("SELECT MAX(last_seen) FROM autoruns").fetchone()[0]


def _reasons(name: str, command: str) -> tuple[list[str], str | None]:
    reasons = [why for rx, why in PATTERNS if re.search(rx, command or "")]
    exe = command_exe(command)
    if exe:
        reasons += binaries.location_flags(os.path.basename(exe), exe)
    return reasons, exe


def assess(conn, since: float, checker=binaries.check_signatures) -> dict:
    """New / changed entries since `since` (never before the baseline) and old ones that already look bad."""
    base = conn.execute("SELECT MIN(first_seen) FROM autoruns").fetchone()[0]
    if base is None:
        return {"available": False, "note": "no autostart snapshot yet (it is taken every 15 minutes by the alerts job "
                                            "and whenever this is asked)"}
    rows = conn.execute("SELECT kind, name, command, detail, first_seen FROM autoruns").fetchall()
    total = {}
    for r in rows:
        total[r[0]] = total.get(r[0], 0) + 1
    new_rows = [r for r in rows if r[4] > base + 1 and r[4] >= since]
    exes = sorted({e for r in new_rows if (e := command_exe(r[2])) and os.path.exists(e)})
    if exes:
        try:
            binaries.ensure_checked(conn, exes, checker=checker)
        except Exception:   # noqa: BLE001 - a failing signature check must not hide the entries themselves
            pass

    def judge(r, is_new: bool) -> dict | None:
        kind, name, command, detail, first = r
        reasons, exe = _reasons(name, command)
        status, signer = binaries.signature_of(conn, exe) if (exe and is_new) else ("Unknown", "")
        severity = "low"
        if reasons or status == "HashMismatch":
            severity = "high"
            if status == "HashMismatch":
                reasons.append(binaries.BAD_SIGNATURES["HashMismatch"])
        elif is_new and status == "NotSigned":
            severity, reasons = "medium", ["the file is not digitally signed"]
        elif is_new and kind in ("scheduled_task", "service") and exe and "\\windows\\" not in exe.lower():
            severity = "medium"
        if not is_new and severity == "low":
            return None
        prev = conn.execute("SELECT command FROM autoruns WHERE kind = ? AND name = ? AND command != ? ORDER BY first_seen DESC LIMIT 1",
                            (kind, name, command)).fetchone() if is_new else None
        return {"id": f"autorun:{kind}:{name}", "kind": kind, "name": name, "command": command[:200], "detail": detail,
                "severity": severity,
                "signature": status if exe and is_new else None, "reasons": reasons,
                "first_seen": time.strftime("%Y-%m-%d %H:%M", time.localtime(first)),
                "changed_from": prev[0][:120] if prev else None}

    new = sorted(filter(None, (judge(r, True) for r in new_rows)), key=lambda x: (x["severity"] != "high", x["severity"] != "medium", x["name"]))
    old = sorted(filter(None, (judge(r, False) for r in rows if r not in new_rows)), key=lambda x: x["name"])
    return {"available": True, "baseline_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(base)),
            "entries_known": total, "new_or_changed": new[:MAX_LISTED], "new_or_changed_count": len(new),
            "high": sum(x["severity"] == "high" for x in new),
            "already_present_but_suspicious": old[:MAX_LISTED],
            "note": "everything present at the baseline counts as known: only later changes are reported as new"}
