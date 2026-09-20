"""Health of the machine as Windows itself records it: crashes, unexpected shutdowns, driver and disk errors, and the
state of Windows Defender.

Read-only. One PowerShell call reads the Application / System / Defender event logs (all readable without administrator
rights) and Defender's status; `analyze` then turns the raw records into findings. The Security log (failed logins)
needs administrator rights and is not read. Nothing is changed, and nothing is sent anywhere."""
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime

SIGNATURE_MAX_AGE_DAYS = 7
REPEAT_ALERT = 5            # the same crash / detection this many times in the window is a pattern, not bad luck

_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$since = (Get-Date).AddHours(-[double]$env:PCA_HOURS)
function Ev($f, $max) {
  Get-WinEvent -FilterHashtable $f -MaxEvents $max -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $_.Properties
    [pscustomobject]@{ t = $_.TimeCreated.ToString('o'); id = $_.Id; p = $_.ProviderName; lvl = $_.Level
      m = (($_.Message -split "`n")[0]); a0 = $(if ($p.Count -gt 0) { "$($p[0].Value)" }); a3 = $(if ($p.Count -gt 3) { "$($p[3].Value)" }) }
  }
}
$o = [ordered]@{}
$o.power    = @(Ev @{LogName='System'; Id=41,1001,6008; StartTime=$since} 50)
$o.hardware = @(Ev @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$since} 50)
$o.disk     = @(Ev @{LogName='System'; ProviderName='disk','Ntfs','volmgr','stornvme'; Level=1,2,3; StartTime=$since} 100)
$o.display  = @(Ev @{LogName='System'; ProviderName='nvlddmkm','Display'; Level=1,2,3; StartTime=$since} 50)
$o.crashes  = @(Ev @{LogName='Application'; ProviderName='Application Error','Application Hang'; Id=1000,1002; StartTime=$since} 300)
$o.defender_events = @(Ev @{LogName='Microsoft-Windows-Windows Defender/Operational'; Id=1116,1117,1118,1119,5000,5001; StartTime=$since} 200)
$mp = Get-MpComputerStatus
$o.defender = if ($mp) { [pscustomobject]@{ service = [bool]$mp.AMServiceEnabled; antivirus = [bool]$mp.AntivirusEnabled
    realtime = [bool]$mp.RealTimeProtectionEnabled; tamper_protected = [bool]$mp.IsTamperProtected
    signatures = $(if ($mp.AntivirusSignatureLastUpdated) { $mp.AntivirusSignatureLastUpdated.ToString('o') })
    quick_scan = $(if ($mp.QuickScanEndTime) { $mp.QuickScanEndTime.ToString('o') })
    full_scan = $(if ($mp.FullScanEndTime) { $mp.FullScanEndTime.ToString('o') }) } } else { $null }
$names = @{}; Get-MpThreat | ForEach-Object { $names[$_.ThreatID] = $_.ThreatName }
$o.threats = @(Get-MpThreatDetection | Where-Object { $_.InitialDetectionTime -ge $since } | ForEach-Object {
  [pscustomobject]@{ t = $_.InitialDetectionTime.ToString('o'); name = $names[$_.ThreatID]; status = $_.ThreatStatusID
    resource = "$(($_.Resources | Select-Object -First 1))" } })
ConvertTo-Json -InputObject $o -Depth 6 -Compress
"""


def read_raw(hours: float) -> dict:
    """Run the PowerShell query. Returns {} when it cannot run (the caller says so instead of guessing)."""
    env = {**os.environ, "PCA_HOURS": str(hours)}
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS], env=env,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return json.loads(res.stdout) if (res.stdout or "").strip() else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


_ISO = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:\.(\d+))?")


def _time(s: str | None) -> datetime | None:
    """Local wall-clock time from PowerShell's round-trip format ('2026-09-20T13:13:29.1234567+03:00'). Parsed by hand:
    Python 3.10's fromisoformat rejects 7 fractional digits and a colon in the offset, and would drop every event."""
    m = _ISO.match(s or "")
    if not m:
        return None
    frac = (m.group(3) or "0")[:6].ljust(6, "0")
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}.{frac}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _list(x) -> list:
    """PowerShell's ConvertTo-Json turns a one-element array into a bare object and an empty one into null."""
    return [] if x is None else x if isinstance(x, list) else [x]


def _redact(text: str) -> str:
    return re.sub(r"(?i)([\\/]users[\\/])[^\\/]+", r"\1<user>", text or "")


def analyze(raw: dict, now: datetime | None = None) -> dict:
    """Findings from the raw records. Pure function: no Windows needed to test it."""
    now = now or datetime.now()
    if not raw:
        return {"available": False, "note": "could not read the Windows event logs (PowerShell did not run)"}
    findings: list[dict] = []

    def add(fid: str, severity: str, title: str, detail: str) -> None:
        findings.append({"id": fid, "severity": severity, "title": title, "detail": detail})

    def stamp(rows) -> list[str]:
        return sorted(t.strftime("%Y-%m-%d %H:%M") for r in rows if (t := _time(r.get("t"))))

    power = _list(raw.get("power"))
    shutdowns = sorted(set(stamp([r for r in power if r.get("id") in (41, 6008)])))   # 41 and 6008 describe the same event
    bugchecks = stamp([r for r in power if r.get("id") == 1001])
    if bugchecks:
        add("bugcheck", "high", "Blue screen (bugcheck)", f"{len(bugchecks)} in the period, last {bugchecks[-1]}")
    if shutdowns:
        add("unexpected-shutdown", "medium", "Unexpected shutdown", f"{len(shutdowns)} time(s) without a normal shutdown (power loss, crash or "
                                             f"forced off), last {shutdowns[-1]}")
    hw = _list(raw.get("hardware"))
    if hw:
        add("whea", "high", "Hardware errors (WHEA)", f"{len(hw)} error(s) reported by the CPU / memory / PCIe, last {stamp(hw)[-1]}")
    disk = _list(raw.get("disk"))
    if disk:
        by = defaultdict(int)
        for r in disk:
            by[r.get("p")] += 1
        add("disk-errors", "medium", "Disk or file system errors", ", ".join(f"{k}: {v}" for k, v in sorted(by.items())) + f"; last {stamp(disk)[-1]}")
    display = _list(raw.get("display"))
    if display:
        add("gpu-driver", "medium", "Graphics driver problems", f"{len(display)} event(s) (driver stopped responding / reset), last {stamp(display)[-1]}")

    crashes: dict[str, dict] = {}
    for r in _list(raw.get("crashes")):
        app = (r.get("a0") or "?")
        c = crashes.setdefault(app, {"app": app, "count": 0, "hangs": 0, "module": r.get("a3") or "", "last": ""})
        c["count" if r.get("id") == 1000 else "hangs"] += 1
        c["last"] = max(c["last"], (_time(r.get("t")) or datetime.min).strftime("%Y-%m-%d %H:%M"))
    crash_list = sorted(crashes.values(), key=lambda c: -(c["count"] + c["hangs"]))
    for c in crash_list:
        if c["count"] + c["hangs"] >= REPEAT_ALERT:
            add(f"crash:{c['app']}", "medium", f"{c['app']} keeps crashing", f"{c['count']} crash(es), {c['hangs']} hang(s), last {c['last']}")

    d = raw.get("defender") or {}
    defender: dict = {"available": bool(d)}
    if d:
        sig, quick, full = _time(d.get("signatures")), _time(d.get("quick_scan")), _time(d.get("full_scan"))
        defender.update(realtime_protection=d.get("realtime"), service_running=d.get("service"),
                        antivirus_enabled=d.get("antivirus"), tamper_protection=d.get("tamper_protected"),
                        signatures_age_days=round((now - sig).total_seconds() / 86400, 1) if sig else None,
                        last_quick_scan=quick.strftime("%Y-%m-%d %H:%M") if quick else None,
                        last_full_scan=full.strftime("%Y-%m-%d %H:%M") if full else None)
        if not d.get("service") or not d.get("antivirus"):
            add("defender-not-running", "high", "Windows Defender is not running", "the antivirus service or engine is off")
        elif not d.get("realtime"):
            add("defender-realtime-off", "high", "Defender real-time protection is OFF", "files are not scanned as they are opened or run")
        if sig and (now - sig).days >= SIGNATURE_MAX_AGE_DAYS:
            add("defender-signatures-old", "medium", "Defender signatures are old", f"last updated {sig:%Y-%m-%d}")
    events = _list(raw.get("defender_events"))
    off = stamp([r for r in events if r.get("id") == 5001])
    if off:
        defender["realtime_protection_turned_off_events"] = off[-5:]
    threats: dict[str, dict] = {}
    for r in _list(raw.get("threats")):
        t = threats.setdefault(r.get("name") or "unknown", {"threat": r.get("name") or "unknown", "count": 0, "last": "",
                                                            "example_target": _redact(r.get("resource") or "")[:90]})
        t["count"] += 1
        t["last"] = max(t["last"], (_time(r.get("t")) or datetime.min).strftime("%Y-%m-%d %H:%M"))
    threat_list = sorted(threats.values(), key=lambda t: -t["count"])
    for t in threat_list:
        add(f"defender-threat:{t['threat']}", "high" if t["count"] >= REPEAT_ALERT else "medium", f"Defender detected {t['threat']}",
            f"{t['count']} time(s), last {t['last']}; target: {t['example_target']}")

    findings.sort(key=lambda f: (f["severity"] != "high", f["title"]))
    high = sum(f["severity"] == "high" for f in findings)
    return {"available": True, "findings": findings, "findings_high": high, "defender": defender,
            "unexpected_shutdowns": shutdowns[-5:], "bugchecks": bugchecks[-5:],
            "app_crashes": crash_list[:6], "defender_detections": threat_list[:6],
            "note": "read from the Windows event logs; the Security log (failed logins) needs administrator rights and is not read",
            "summary": (f"{len(findings)} finding(s) ({high} high)" if findings else "nothing wrong in the Windows logs")}


def health(hours: float = 168, reader=read_raw, now: datetime | None = None) -> dict:
    out = analyze(reader(hours), now)
    if out.get("available"):
        out["hours"] = hours
    return out
