"""Read-only collectors for system, GPU, process and disk metrics."""
import ipaddress
import time

import psutil

try:
    import pynvml

    pynvml.nvmlInit()
    _NVML = True
except Exception:  # no NVIDIA driver / library
    _NVML = False

MB = 1024**2
GB = 1024**3
TOP_PROCESSES = 15


def collect_gpus(ts: float) -> list[dict]:
    if not _NVML:
        return []
    gpus = []
    for i in range(pynvml.nvmlDeviceGetCount()):
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        name = pynvml.nvmlDeviceGetName(h)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        gpus.append(
            {
                "ts": ts,
                "idx": i,
                "name": name.decode() if isinstance(name, bytes) else name,
                "util_percent": pynvml.nvmlDeviceGetUtilizationRates(h).gpu,
                "mem_used_mb": mem.used / MB,
                "mem_total_mb": mem.total / MB,
                "temp_c": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
                "power_w": pynvml.nvmlDeviceGetPowerUsage(h) / 1000,
            }
        )
    return gpus


def collect_disks(ts: float) -> list[dict]:
    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append(
            {"ts": ts, "mount": part.mountpoint, "total_gb": u.total / GB, "used_gb": u.used / GB}
        )
    return disks


class Collector:
    """Keeps previous counters so it can report per-second rates."""

    def __init__(self) -> None:
        self._prev_time = time.time()
        self._prev_disk = psutil.disk_io_counters()
        self._prev_net = psutil.net_io_counters()
        psutil.cpu_percent(None)
        self._exe_cache: dict[tuple, str] = {}
        self.last_exes: list[dict] = []

    def _processes(self, ts: float, window: float = 1.0) -> list[dict]:
        procs = [p for p in psutil.process_iter(["pid", "name"]) if p.pid != 0]  # 0 = System Idle
        for p in procs:
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(window)
        n_cpu = psutil.cpu_count() or 1
        rows = []
        by_pid = {p.pid: p for p in procs}
        for p in procs:
            try:
                rows.append(
                    {
                        "ts": ts,
                        "pid": p.pid,
                        "name": p.info["name"] or "?",
                        "cpu_percent": p.cpu_percent(None) / n_cpu,
                        "rss_mb": p.memory_info().rss / MB,
                    }
                )
            except psutil.Error:
                continue
        top = sorted(rows, key=lambda r: r["cpu_percent"], reverse=True)[:TOP_PROCESSES]
        top_ids = {r["pid"] for r in top}
        top += [r for r in sorted(rows, key=lambda r: r["rss_mb"], reverse=True)[:TOP_PROCESSES]
                if r["pid"] not in top_ids]
        self.last_exes = self._exes(top, by_pid, ts)
        return top

    def _exes(self, rows: list[dict], by_pid: dict, ts: float) -> list[dict]:
        """(name, file path) of the recorded processes. The path of a running process never changes, so it is
        looked up once per process (pid + start time); protected system processes refuse it and are skipped."""
        out = {}
        for r in rows:
            p = by_pid.get(r["pid"])
            try:
                key = (r["pid"], p.create_time())
                if key not in self._exe_cache:
                    self._exe_cache[key] = p.exe()
                exe = self._exe_cache[key]
            except (psutil.Error, AttributeError, OSError):
                continue
            if exe:
                out[(r["name"], exe)] = {"ts": ts, "name": r["name"], "exe": exe}
        if len(self._exe_cache) > 5000:   # pids come and go; keep the cache from growing for weeks
            self._exe_cache.clear()
        return list(out.values())

    def _connections(self, ts: float) -> list[dict]:
        """Outbound TCP to public addresses and TCP listening beyond localhost, as (process name, address, port).
        Only who talks to whom: psutil cannot say how much, and nothing of the traffic itself is read."""
        try:
            conns = psutil.net_connections(kind="tcp")
        except (psutil.Error, OSError):
            return []
        names: dict[int, str] = {}
        out: dict[tuple, dict] = {}
        for c in conns:
            if not c.pid:
                continue
            if c.status in ("ESTABLISHED", "SYN_SENT") and c.raddr:
                kind, addr, port = "out", c.raddr.ip, c.raddr.port
                try:
                    if not ipaddress.ip_address(addr).is_global:
                        continue
                except ValueError:
                    continue
            elif c.status == "LISTEN" and c.laddr and c.laddr.ip not in ("127.0.0.1", "::1"):
                kind, addr, port = "listen", c.laddr.ip, c.laddr.port
            else:
                continue
            if c.pid not in names:
                try:
                    names[c.pid] = psutil.Process(c.pid).name()
                except psutil.Error:
                    names[c.pid] = ""
            if names[c.pid]:
                out[(names[c.pid], kind, addr, port)] = {"ts": ts, "name": names[c.pid], "kind": kind, "addr": addr,
                                                          "port": port}
        return list(out.values())

    def sample(self) -> dict:
        processes = self._processes(ts := time.time())
        now = time.time()
        dt = max(now - self._prev_time, 1e-6)
        disk, net = psutil.disk_io_counters(), psutil.net_io_counters()
        freq = psutil.cpu_freq()
        ram, swap = psutil.virtual_memory(), psutil.swap_memory()

        system = {
            "ts": ts,
            "cpu_percent": psutil.cpu_percent(None),
            "cpu_freq_mhz": freq.current if freq else None,
            "ram_used_mb": ram.used / MB,
            "ram_percent": ram.percent,
            "swap_percent": swap.percent,
            "disk_read_mbps": (disk.read_bytes - self._prev_disk.read_bytes) / MB / dt,
            "disk_write_mbps": (disk.write_bytes - self._prev_disk.write_bytes) / MB / dt,
            "net_sent_kbps": (net.bytes_sent - self._prev_net.bytes_sent) / 1024 / dt,
            "net_recv_kbps": (net.bytes_recv - self._prev_net.bytes_recv) / 1024 / dt,
        }
        self._prev_time, self._prev_disk, self._prev_net = now, disk, net
        return {
            "system": system,
            "gpus": collect_gpus(ts),
            "processes": processes,
            "exes": self.last_exes,
            "conns": self._connections(ts),
            "disks": collect_disks(ts),
        }
