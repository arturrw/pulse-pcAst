"""Network side of the process check: which process talks to which public address, and what listens for others.

What is recorded (see collectors.py): outbound TCP connections to PUBLIC addresses and TCP ports listening on
something other than localhost, as (process name, address, port) with first / last time seen. No traffic volume and
no content: psutil cannot tell how much a process sent. Everything stays in the local database.

Signals, from strong to weak:
  - a file with a suspicious location or signature (binaries.assess) that connects out at all,
  - a connection to a port typical of mining pools, Tor or IRC botnets,
  - a program that started listening for incoming connections on all interfaces,
  - a program with a small, stable set of destinations that suddenly talks to a new one.
The last two need a day of history to mean anything (a fresh database has seen nothing "before") and the last one
ignores browser-like processes with hundreds of destinations. Hints for a human look, never a verdict."""
MIN_BASELINE_HOURS = 24
VARIETY_LIMIT = 40          # a process with more distinct destinations than this (browser, game, updater) is not judged
LIMIT = 6

SUSPICIOUS_PORTS = {
    3333: "mining pool (stratum)", 4444: "mining pool or a common backdoor port", 5555: "mining pool",
    7777: "mining pool", 14444: "mining pool (Monero)", 14433: "mining pool (Monero, TLS)", 45560: "mining pool",
    6667: "IRC (old botnet control channel)", 6697: "IRC over TLS",
    9001: "Tor relay", 9030: "Tor directory", 9050: "Tor proxy", 9150: "Tor Browser proxy",
}


def _group(rows, key_fn, sample_fn) -> list[dict]:
    groups: dict = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    return [{"name": k[0], **sample_fn(k, v)} for k, v in groups.items()]


def analyze(conn, since: float, now: float, flagged_names: set[str]) -> dict:
    """Findings for the window [since, now]; `flagged_names` are processes whose file binaries.assess found odd."""
    first = conn.execute("SELECT MIN(first_seen) FROM process_connections").fetchone()[0]
    if first is None:
        return {"available": False, "note": "no network data recorded yet (the collector records it from now on)"}
    baseline_hours = max(0.0, (since - first) / 3600)
    ok = baseline_hours >= MIN_BASELINE_HOURS
    rows = conn.execute("SELECT name, kind, addr, port, first_seen, last_seen FROM process_connections "
                        "WHERE last_seen >= ?", (since,)).fetchall()
    out = [r for r in rows if r[1] == "out"]

    flagged = _group([r for r in out if r[0] in flagged_names], lambda r: (r[0],),
                     lambda k, v: {"destinations": [f"{r[2]}:{r[3]}" for r in v[:3]], "count": len(v),
                                   "why": "its file has a suspicious location or signature"})
    ports = _group([r for r in out if r[3] in SUSPICIOUS_PORTS], lambda r: (r[0], r[3]),
                   lambda k, v: {"port": k[1], "typical_of": SUSPICIOUS_PORTS[k[1]],
                                 "destinations": [r[2] for r in v[:3]]})
    listeners, new_dest = [], []
    if ok:
        # new = the program never listened before at all (a known one moving to another port is not news)
        never = lambda r: not conn.execute("SELECT 1 FROM process_connections WHERE name = ? AND kind = 'listen' "
                                           "AND first_seen < ? LIMIT 1", (r[0], since)).fetchone()
        listeners = _group([r for r in rows if r[1] == "listen" and r[4] >= since and never(r)], lambda r: (r[0],),
                           lambda k, v: {"ports": sorted({r[3] for r in v})[:6], "bind": v[0][2]})
        for name in {r[0] for r in out if r[4] >= since}:
            before = conn.execute("SELECT COUNT(*) FROM process_connections WHERE name = ? AND kind = 'out' "
                                  "AND first_seen < ?", (name, since)).fetchone()[0]
            if 1 <= before <= VARIETY_LIMIT:
                fresh = [r for r in out if r[0] == name and r[4] >= since]
                new_dest.append({"name": name, "new": len(fresh), "usual_destinations": before,
                                 "examples": [f"{r[2]}:{r[3]}" for r in fresh[:3]]})
    return {"available": True, "confidence": "ok" if ok else "low", "baseline_hours": baseline_hours,
            "from_suspicious_files": flagged[:LIMIT], "suspicious_ports": ports[:LIMIT],
            "new_listeners": listeners[:LIMIT], "new_destinations": new_dest[:LIMIT],
            "note": ("new listeners and new destinations are only judged after 24 h of network history"
                     if not ok else "")}
