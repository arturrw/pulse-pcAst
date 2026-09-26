"""CPU temperature, and AMD / Intel graphics card data, from LibreHardwareMonitor when it runs with its web server on.

Windows gives a normal user no reliable CPU temperature: the ACPI thermal zone class needs administrator rights and
on many boards is a fixed motherboard value. LibreHardwareMonitor reads the real sensors through its own driver (it
runs as administrator itself); with Options > Remote Web Server > Run it serves them as JSON. Pulse only reads that
page, on this machine, and never asks for rights: when LibreHardwareMonitor is not running the temperature is None.
NVIDIA cards are read through NVIDIA's own library (collectors.py); the same tree gives the other vendors' cards."""
import http.client
import json
import re
import time
import urllib.request

URL = "http://127.0.0.1:8085/data.json"   # LibreHardwareMonitor's default port
TIMEOUT = 0.5
RETRY_AFTER = 60.0          # after a failed read, do not knock again for a minute (the live row asks every 2 s)
SANE = (5.0, 125.0)         # outside this a sensor is misread or unplugged

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # a system proxy must not see localhost


def _value(node: dict) -> float | None:
    m = _NUM.search(str(node.get("Value", "")))
    v = float(m.group().replace(",", ".")) if m else None
    return v if v is not None and SANE[0] <= v <= SANE[1] else None


def _cpu_temperatures(node: dict, in_cpu: bool = False, in_temps: bool = False) -> list[tuple[str, float]]:
    """(sensor name, degrees) of every CPU temperature in LibreHardwareMonitor's tree. Newer versions tag sensors
    with SensorId ("/amdcpu/0/temperature/2") and Type; older ones only have icons, so both are understood."""
    icon = str(node.get("ImageURL", ""))
    sid = str(node.get("SensorId", ""))
    in_cpu = in_cpu or icon.endswith("cpu.png") or sid.startswith(("/intelcpu/", "/amdcpu/"))
    in_temps = in_temps or icon.endswith("temperature.png")
    kids = node.get("Children") or []
    if kids:
        return [t for k in kids for t in _cpu_temperatures(k, in_cpu, in_temps)]
    is_temp = node.get("Type") == "Temperature" or "/temperature/" in sid or in_temps
    name = str(node.get("Text", ""))
    v = _value(node)
    if in_cpu and is_temp and v is not None and "distance" not in name.lower():   # "Distance to TjMax" is headroom
        return [(name, v)]
    return []


def cpu_temp(tree: dict) -> float | None:
    """The processor temperature from a data.json tree: the package sensor (Intel "CPU Package", AMD "Package" or
    "Core (Tctl/Tdie)"), else "Core Max", else the hottest core. None when the tree has no CPU temperature."""
    temps = _cpu_temperatures(tree)
    for key in ("package", "tctl/tdie", "tdie", "tctl", "core max"):
        hit = [v for n, v in temps if key in n.lower()]
        if hit:
            return max(hit)
    return max((v for _, v in temps), default=None)


def _number(node: dict) -> float | None:
    """A sensor's value in its base unit: MB for memory ("1.5 GB" -> 1536), else as shown."""
    raw = str(node.get("Value", ""))
    m = _NUM.search(raw)
    if not m:
        return None
    v = float(m.group().replace(",", "."))
    return v * 1024 if "GB" in raw else v


_GPU_VENDORS = (("/gpu-nvidia/", "nvidia"), ("/gpu-amd/", "amd"), ("/gpu-intel/", "intel"))
_GPU_ICONS = {"nvidia.png": "nvidia", "ati.png": "amd", "amd.png": "amd", "intel.png": "intel", "gpu.png": None}
_KIND_OF_GROUP = (("temperature", "Temperature"), ("load", "Load"), ("power", "Power"), ("small", "SmallData"), ("data", "Data"))


def _leaves(node: dict, group: str = "") -> list[tuple[str, str, str, dict]]:
    """(sensor type, name, SensorId, node) of every sensor under a hardware node. The type comes from the sensor's
    Type (newer versions) or from its group ("Temperatures", "Load", "Powers", "Data", "SmallData")."""
    kids = node.get("Children") or []
    if kids:
        text = str(node.get("Text", "")).lower().replace(" ", "")
        kind = next((k for key, k in _KIND_OF_GROUP if key in text), group)
        return [leaf for k in kids for leaf in _leaves(k, kind)]
    return [(str(node.get("Type") or group), str(node.get("Text", "")), str(node.get("SensorId", "")), node)]


def _pick(leaves, kind: str, names: tuple[str, ...]) -> float | None:
    found = [(n.lower(), _number(x)) for k, n, _, x in leaves if k == kind]
    for key in names:
        hit = [v for n, v in found if n == key and v is not None]
        if hit:
            return hit[0]
    return None


def gpus(tree: dict) -> list[dict]:
    """Graphics cards in a data.json tree, the main one first (a discrete card before an integrated Intel one, then
    the one with more memory): {vendor, name, util_percent, temp_c, mem_used_mb, mem_total_mb, power_w}; a value the
    card does not report is None."""
    out = []
    for computer in tree.get("Children") or []:
        for hw in computer.get("Children") or []:
            leaves = _leaves(hw)
            sid = next((i for _, _, i, _ in leaves if i), "")
            vendor = next((v for prefix, v in _GPU_VENDORS if sid.startswith(prefix)), None)
            icon = str(hw.get("ImageURL", "")).rsplit("/", 1)[-1]
            if vendor is None and icon in _GPU_ICONS and any(k == "Load" and "gpu" in n.lower() for k, n, _, _ in leaves):
                vendor = _GPU_ICONS[icon] or "unknown"
            if vendor is None:
                continue
            temp = _pick(leaves, "Temperature", ("gpu core", "gpu"))
            out.append({"vendor": vendor, "name": str(hw.get("Text", "")),
                        "util_percent": _pick(leaves, "Load", ("gpu core", "d3d 3d")),
                        "temp_c": temp if temp is not None and SANE[0] <= temp <= SANE[1] else None,
                        "mem_used_mb": _pick(leaves, "SmallData", ("gpu memory used", "d3d dedicated memory used"))
                        or _pick(leaves, "Data", ("gpu memory used",)),
                        "mem_total_mb": _pick(leaves, "SmallData", ("gpu memory total", "d3d dedicated memory total"))
                        or _pick(leaves, "Data", ("gpu memory total",)),
                        "power_w": _pick(leaves, "Power", ("gpu package", "gpu power", "gpu core"))})
    return sorted(out, key=lambda g: (g["vendor"] == "intel", -(g["mem_total_mb"] or 0)))


def fetch(url: str = URL, timeout: float = TIMEOUT) -> dict:
    with _opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class Reader:
    """Reads LibreHardwareMonitor on demand; one read serves every caller for MAX_AGE seconds (the collector asks
    for the CPU temperature and the graphics card in the same sample). After a failure it stays quiet for
    RETRY_AFTER seconds, so a missing LibreHardwareMonitor costs one refused connection a minute."""
    MAX_AGE = 1.0

    def __init__(self, url: str = URL, fetcher=fetch, clock=time.monotonic) -> None:
        self.url, self._fetch, self._clock = url, fetcher, clock
        self._retry_at = 0.0
        self._tree: dict | None = None
        self._tree_at = -1e9
        self.available = False     # the last read reached LibreHardwareMonitor

    def tree(self, force: bool = False) -> dict | None:
        now = self._clock()
        if not force and self._tree is not None and now - self._tree_at < self.MAX_AGE:
            return self._tree
        if not force and now < self._retry_at:
            return None
        try:
            tree = self._fetch(self.url)
        except (OSError, ValueError, http.client.HTTPException):
            self.available, self._retry_at, self._tree = False, now + RETRY_AFTER, None
            return None
        self.available = True
        self._tree, self._tree_at = (tree if isinstance(tree, dict) else None), now
        return self._tree

    def cpu_temp(self, force: bool = False) -> float | None:
        t = self.tree(force)
        return cpu_temp(t) if t else None

    def gpus(self, force: bool = False) -> list[dict]:
        t = self.tree(force)
        return gpus(t) if t else []


READER = Reader()


def probe() -> dict:
    """For the Setup page: is LibreHardwareMonitor answering, its CPU temperature right now and its main graphics card."""
    r = Reader()
    t = r.cpu_temp(force=True)
    cards = r.gpus()
    return {"available": r.available, "cpu_temp_c": t, "url": URL, "gpu": cards[0]["name"] if cards else None}
