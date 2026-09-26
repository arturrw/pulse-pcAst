"""CPU temperature from LibreHardwareMonitor, when it runs with its web server on.

Windows gives a normal user no reliable CPU temperature: the ACPI thermal zone class needs administrator rights and
on many boards is a fixed motherboard value. LibreHardwareMonitor reads the real sensors through its own driver (it
runs as administrator itself); with Options > Remote Web Server > Run it serves them as JSON. Pulse only reads that
page, on this machine, and never asks for rights: when LibreHardwareMonitor is not running the temperature is None."""
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


def fetch(url: str = URL, timeout: float = TIMEOUT) -> dict:
    with _opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class Reader:
    """Reads the temperature on demand; after a failure it stays quiet for RETRY_AFTER seconds, so a missing
    LibreHardwareMonitor costs one refused connection a minute, not one every sample."""

    def __init__(self, url: str = URL, fetcher=fetch, clock=time.monotonic) -> None:
        self.url, self._fetch, self._clock = url, fetcher, clock
        self._retry_at = 0.0
        self.available = False     # the last read reached LibreHardwareMonitor

    def cpu_temp(self, force: bool = False) -> float | None:
        if not force and self._clock() < self._retry_at:
            return None
        try:
            tree = self._fetch(self.url)
        except (OSError, ValueError, http.client.HTTPException):
            self.available, self._retry_at = False, self._clock() + RETRY_AFTER
            return None
        self.available = True
        return cpu_temp(tree) if isinstance(tree, dict) else None


READER = Reader()


def probe() -> dict:
    """For the Setup page: is LibreHardwareMonitor answering, and what CPU temperature does it give right now."""
    r = Reader()
    t = r.cpu_temp(force=True)
    return {"available": r.available, "cpu_temp_c": t, "url": URL}
