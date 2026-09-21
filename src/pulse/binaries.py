"""Where a process's file lives and whether it is signed: the cheap, strong signals for spotting a disguised program.

Read-only. Signatures are checked with Windows' own Get-AuthenticodeSignature (it only reads the file), once per
file: the result is cached in the `binaries` table until the file's size or modification time changes. Nothing is
uploaded anywhere. Signals are hints for a human look, never a verdict: plenty of honest programs are unsigned."""
import json
import os
import subprocess
import time

# Names Windows itself runs. If one of these runs from anywhere else, it is a classic disguise.
_SYS32 = ("\\windows\\system32\\", "\\windows\\syswow64\\")
SYSTEM_NAMES = {n: _SYS32 for n in (
    "svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "services.exe", "smss.exe", "wininit.exe",
    "taskhostw.exe", "conhost.exe", "dwm.exe", "spoolsv.exe", "runtimebroker.exe", "searchindexer.exe",
    "audiodg.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "dllhost.exe", "wmiprvse.exe")}
SYSTEM_NAMES["explorer.exe"] = ("\\windows\\explorer.exe",)

# Folders a normal program is not installed into, but where downloaded or dropped files land.
RISKY_DIRS = ("\\appdata\\local\\temp\\", "\\windows\\temp\\", "\\downloads\\", "\\users\\public\\", "\\$recycle.bin\\")

BAD_SIGNATURES = {"HashMismatch": "signature is broken: the file was changed after it was signed",
                  "NotTrusted": "signed by a certificate Windows does not trust",
                  "Invalid": "signature is invalid"}

SIGNATURE_BATCH = 40
_PS_SCRIPT = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$p=[Console]::In.ReadToEnd()|ConvertFrom-Json;"
    "$r=@($p|ForEach-Object{$s=Get-AuthenticodeSignature -LiteralPath $_ -ErrorAction SilentlyContinue;"
    "[pscustomobject]@{path=$_;status=if($s){$s.Status.ToString()}else{'Unknown'};"
    "signer=if($s -and $s.SignerCertificate){$s.SignerCertificate.Subject}else{''}}});"
    "ConvertTo-Json -InputObject $r -Compress"
)


def location_flags(name: str, exe: str) -> list[str]:
    """Reasons the place a process runs from is odd (empty list: nothing odd)."""
    path, low = exe, exe.lower()
    flags = []
    allowed = SYSTEM_NAMES.get(name.lower())
    if allowed and not any(a in low for a in allowed):
        flags.append(f"{name} is a Windows system program name, but this one runs from {os.path.dirname(path)}")
    for d in RISKY_DIRS:
        if d in low:
            flags.append(f"runs from {d.strip(chr(92))}, where downloaded or dropped files land")
            break
    return flags


def check_signatures(paths: list[str]) -> dict[str, tuple[str, str]]:
    """{path: (status, signer)} for Windows executables. Status is Valid, NotSigned, HashMismatch, NotTrusted, ...
    Any failure to run PowerShell gives 'Unknown' for the whole batch instead of an exception."""
    out: dict[str, tuple[str, str]] = {}
    for i in range(0, len(paths), SIGNATURE_BATCH):
        batch = paths[i:i + SIGNATURE_BATCH]
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                                 input=json.dumps(batch), capture_output=True, text=True, encoding="utf-8",
                                 timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rows = json.loads(res.stdout) if res.stdout.strip() else []
            rows = [rows] if isinstance(rows, dict) else rows
            got = {r["path"]: (r["status"], r["signer"] or "") for r in rows}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            got = {}
        for p in batch:
            out[p] = got.get(p, ("Unknown", ""))
    return out


def _stat(path: str) -> tuple[float, int] | None:
    try:
        st = os.stat(path)
        return st.st_mtime, st.st_size
    except OSError:
        return None


def ensure_checked(conn, exes: list[str], checker=check_signatures, now: float | None = None) -> None:
    """Fill the `binaries` cache for files not checked yet or changed since (size or modification time)."""
    now = time.time() if now is None else now
    todo, stats = [], {}
    for exe in exes:
        st = _stat(exe)
        stats[exe] = st
        row = conn.execute("SELECT mtime, size FROM binaries WHERE exe = ?", (exe,)).fetchone()
        if st is None:
            if row is None:
                conn.execute("INSERT INTO binaries VALUES (?, NULL, NULL, 'Missing', '', ?)", (exe, now))
        elif row is None or (row[0], row[1]) != st:
            todo.append(exe)
    for exe, (status, signer) in checker(todo).items() if todo else []:
        mtime, size = stats[exe]
        conn.execute("INSERT OR REPLACE INTO binaries VALUES (?, ?, ?, ?, ?, ?)", (exe, mtime, size, status, signer, now))
    conn.commit()


def signature_of(conn, exe: str) -> tuple[str, str]:
    row = conn.execute("SELECT sig_status, signer FROM binaries WHERE exe = ?", (exe,)).fetchone()
    return (row[0], row[1] or "") if row else ("Unknown", "")


def assess(conn, since: float) -> dict:
    """Files that ran since `since`, judged on where they live and their signature.
    high: a disguise (system name from the wrong folder), a broken signature, or an unsigned file in a download/temp
    folder. medium: signed but from such a folder, or signed by a certificate Windows does not trust."""
    rows = conn.execute("SELECT name, exe FROM process_exes WHERE last_seen >= ?", (since,)).fetchall()
    known_names = {n for (n,) in conn.execute("SELECT DISTINCT name FROM process_snapshots WHERE ts >= ?", (since,))}
    findings, unsigned = [], 0
    for name, exe in rows:
        status, signer = signature_of(conn, exe)
        flags = location_flags(name, exe)
        risky_dir = any("runs from" in f for f in flags)
        disguise = any("system program name" in f for f in flags)
        if status == "NotSigned":
            unsigned += 1
        reasons = list(flags)
        if status in BAD_SIGNATURES:
            reasons.append(BAD_SIGNATURES[status])
        if status == "NotSigned" and risky_dir:
            reasons.append("and it is not signed")
        if not reasons:
            continue
        severity = "high" if (disguise or status == "HashMismatch" or (risky_dir and status == "NotSigned")) else "medium"
        findings.append({"name": name, "exe": exe, "severity": severity, "signature": status, "signer": signer,
                         "reasons": reasons})
    findings.sort(key=lambda f: (f["severity"] != "high", f["name"]))
    return {"files_with_path": len(rows), "names_recorded": len(known_names), "unsigned_files": unsigned,
            "flagged": findings}
