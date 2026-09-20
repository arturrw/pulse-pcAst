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

SUPPORTED_KINDS = ("run_key", "startup_folder", "scheduled_task", "service", "wmi_consumer", "browser_extension", "browser_setting")
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
Get-CimInstance -Namespace root\subscription -ClassName __EventConsumer | ForEach-Object {
  Add-Item 'wmi_consumer' $_.Name (($_.CimClass.CimClassName + ' ' + $_.CommandLineTemplate + $_.ExecutablePath + $_.ScriptText).Trim()) ''
}
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


# Chromium-family browsers: (name, environment variable of the base folder, path below it). Opera keeps its profile
# directly in the folder; the others have Default / Profile N folders.
BROWSERS = (("Edge", "LOCALAPPDATA", ("Microsoft", "Edge", "User Data")), ("Chrome", "LOCALAPPDATA", ("Google", "Chrome", "User Data")),
            ("Brave", "LOCALAPPDATA", ("BraveSoftware", "Brave-Browser", "User Data")), ("Yandex", "LOCALAPPDATA", ("Yandex", "YandexBrowser", "User Data")),
            ("Vivaldi", "LOCALAPPDATA", ("Vivaldi", "User Data")), ("Opera", "APPDATA", ("Opera Software", "Opera Stable")),
            ("Opera GX", "APPDATA", ("Opera Software", "Opera GX Stable")))
# Chromium's extension "location" codes; built-in components (5, 10) are left out, they are not the user's business.
EXT_LOCATION = {1: "installed from the store", 2: "added by another program (external preferences)", 3: "added by another program (registry)",
                4: "loaded unpacked (developer mode)", 6: "external download", 7: "installed by policy", 8: "loaded from the command line",
                9: "forced by policy"}
EXT_SEVERITY = {4: "high", 8: "high", 2: "medium", 3: "medium", 6: "medium", 7: "medium", 9: "medium"}
_RANK = {"low": 0, "medium": 1, "high": 2}


def read_browser_extensions(roots=None) -> list[dict]:
    """Extensions and the developer-mode switch of every Chromium-family browser profile, read from the profile files
    (no PowerShell). `roots` is [(browser, folder)] for tests; by default the browsers' usual folders."""
    if roots is None:
        roots = [(b, os.path.join(os.environ[var], *sub)) for b, var, sub in BROWSERS if os.environ.get(var)]
    out = []
    for browser, root in roots:
        if not os.path.isdir(root):
            continue
        has_profile_files = any(os.path.exists(os.path.join(root, f)) for f in ("Preferences", "Secure Preferences"))
        profiles = [("", root)] if has_profile_files else [
            (d, os.path.join(root, d)) for d in sorted(os.listdir(root)) if d == "Default" or d.startswith("Profile ")]
        for profile, folder in profiles:
            prefs = {}
            for fname in ("Preferences", "Secure Preferences"):
                try:
                    with open(os.path.join(folder, fname), encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, ValueError):
                    continue
                ext = data.get("extensions") or {}
                prefs.setdefault("settings", {}).update(ext.get("settings") or {})
                if (ext.get("ui") or {}).get("developer_mode"):
                    prefs["developer_mode"] = True
            label = f"{browser}/{profile}" if profile else browser
            for ext_id, e in (prefs.get("settings") or {}).items():
                loc = e.get("location") if isinstance(e, dict) else None
                if loc not in EXT_LOCATION:
                    continue
                name = (e.get("manifest") or {}).get("name") or ext_id
                out.append({"kind": "browser_extension", "name": f"{label}/{ext_id}", "detail": f"location={loc}",
                            "command": f"{name} [{EXT_LOCATION[loc]}] {e.get('path', '')}".strip()})
            if prefs.get("developer_mode"):
                out.append({"kind": "browser_setting", "name": f"{label}/developer_mode", "command": "on", "detail": ""})
    return out


def _kind_rules(kind: str, command: str, detail: str) -> tuple[str, list[str]]:
    """(severity, reasons) that come from what kind of entry this is."""
    if kind == "browser_extension":
        try:
            loc = int(detail.split("=")[1])
        except (IndexError, ValueError):
            return "low", []
        return (EXT_SEVERITY[loc], [EXT_LOCATION[loc]]) if loc in EXT_SEVERITY else ("low", [])
    if kind == "browser_setting" and command == "on":
        return "medium", ["developer mode is on: it lets unpacked extensions load"]
    if kind == "wmi_consumer" and re.search(r"CommandLineEventConsumer|ActiveScriptEventConsumer", command or ""):
        return "high", ["a WMI event subscription that runs a command or script: a hidden way to start things"]
    return "low", []


def read_items() -> list[dict]:
    """PowerShell entries plus browser extensions. [] from PowerShell means it could not run: the caller must not treat
    an empty list as 'everything was removed'."""
    items = _read_powershell()
    return items + read_browser_extensions() if items else items


def _read_powershell() -> list[dict]:
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
    # A kind of entry this tool learned to read AFTER the baseline was taken (say, browser extensions) joins the baseline
    # in its first snapshot: nothing about it was known before, so nothing can be called a change. Kinds are registered
    # explicitly (not inferred from rows) so an empty kind at baseline time, like an empty startup folder, still counts
    # a later entry as new.
    registered = {k for (k,) in conn.execute("SELECT kind FROM autorun_kinds")} | {k for (k,) in conn.execute("SELECT DISTINCT kind FROM autoruns")}
    baseline = conn.execute("SELECT MIN(first_seen) FROM autoruns").fetchone()[0]
    newly_supported = {k for k in SUPPORTED_KINDS if k not in registered} if baseline is not None else set()
    for it in items:
        first = baseline if it["kind"] in newly_supported else now
        conn.execute("INSERT INTO autoruns (kind, name, command, detail, first_seen, last_seen) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(kind, name, command) DO UPDATE SET last_seen = excluded.last_seen, detail = excluded.detail",
                     (it["kind"], it["name"], it["command"], it.get("detail", ""), first, now))
    conn.executemany("INSERT OR IGNORE INTO autorun_kinds (kind) VALUES (?)", [(k,) for k in SUPPORTED_KINDS])
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
        kind_severity, kind_reasons = _kind_rules(kind, command, detail)
        severity = "low"
        if reasons or status == "HashMismatch":
            severity = "high"
            if status == "HashMismatch":
                reasons.append(binaries.BAD_SIGNATURES["HashMismatch"])
        elif is_new and status == "NotSigned":
            severity, reasons = "medium", ["the file is not digitally signed"]
        elif is_new and kind in ("scheduled_task", "service") and exe and "\\windows\\" not in exe.lower():
            severity = "medium"
        if kind_reasons:
            reasons = reasons + [r for r in kind_reasons if r not in reasons]
            severity = max(severity, kind_severity, key=_RANK.get)
        if not is_new and severity != "high":
            return None   # an older entry is listed only when it looks really bad; the rest would just be clutter
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
