"""Unit tests for reading the CPU temperature from LibreHardwareMonitor. Run: python tests/test_lhm.py (or pytest)."""
import tempfile
from pathlib import Path

from pulse import db, lhm


def _node(text, children=(), **kw):
    return {"id": 1, "Text": text, "Min": "", "Value": "", "Max": "", "ImageURL": "", "Children": list(children), **kw}


def _sensor(text, value, sid="", kind="Temperature"):
    return _node(text, Value=value, SensorId=sid, Type=kind)


def _tree(*hardware):
    return _node("Sensor", [_node("DESKTOP-TEST", list(hardware), ImageURL="images_icon/computer.png")])


INTEL = _node("Intel Core i7-12700K", [
    _node("Temperatures", [_sensor("CPU Core #1", "58.0 °C", "/intelcpu/0/temperature/0"),
                           _sensor("CPU Core #1 Distance to TjMax", "42.0 °C", "/intelcpu/0/temperature/1"),
                           _sensor("CPU Package", "61.0 °C", "/intelcpu/0/temperature/6")], ImageURL="images_icon/temperature.png"),
    _node("Load", [_sensor("CPU Total", "12.5 %", "/intelcpu/0/load/0", "Load")], ImageURL="images_icon/load.png"),
], ImageURL="images_icon/cpu.png")
GPU = _node("NVIDIA GeForce RTX 3070 Ti", [
    _node("Temperatures", [_sensor("GPU Core", "70.0 °C", "/gpu-nvidia/0/temperature/0")], ImageURL="images_icon/temperature.png"),
], ImageURL="images_icon/nvidia.png")


def test_intel_package_wins_and_distance_to_tjmax_is_ignored():
    assert lhm.cpu_temp(_tree(GPU, INTEL)) == 61.0


def test_amd_tctl_with_a_decimal_comma():
    amd = _node("AMD Ryzen 7 5800X3D", [_node("Temperatures", [
        _sensor("Core (Tctl/Tdie)", "72,5 °C", "/amdcpu/0/temperature/2"), _sensor("CCD1 (Tdie)", "70,0 °C", "/amdcpu/0/temperature/3")],
        ImageURL="images_icon/temperature.png")], ImageURL="images_icon/cpu.png")
    assert lhm.cpu_temp(_tree(amd)) == 72.5


def test_old_versions_without_sensor_ids_are_read_from_icons():
    old = _node("Intel Core i5", [_node("Temperatures", [_node("CPU Core #1", Value="50.0 °C"), _node("CPU Core #2", Value="55.0 °C")],
                                        ImageURL="images_icon/temperature.png")], ImageURL="images_icon/cpu.png")
    assert lhm.cpu_temp(_tree(old)) == 55.0                                          # no package: the hottest core


def test_no_cpu_or_a_broken_sensor_gives_none():
    assert lhm.cpu_temp(_tree(GPU)) is None
    broken = _node("CPU", [_sensor("CPU Package", "0.0 °C", "/intelcpu/0/temperature/6")], ImageURL="images_icon/cpu.png")
    assert lhm.cpu_temp(_tree(broken)) is None


def test_reader_backs_off_after_a_failure():
    now, calls = [0.0], []
    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionRefusedError()
        return _tree(INTEL)
    r = lhm.Reader(fetcher=fetch, clock=lambda: now[0])
    assert r.cpu_temp() is None and not r.available
    now[0] = 30
    assert r.cpu_temp() is None and len(calls) == 1                                  # still waiting: no second knock
    now[0] = 61
    assert r.cpu_temp() == 61.0 and r.available
    assert lhm.Reader(fetcher=lambda u: (_ for _ in ()).throw(ValueError("bad json"))).cpu_temp() is None


def test_an_old_database_gets_the_new_column():
    path = Path(tempfile.mkdtemp()) / "old.db"
    import sqlite3
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE system_metrics (ts REAL PRIMARY KEY, cpu_percent REAL, cpu_freq_mhz REAL, ram_used_mb REAL, "
                "ram_percent REAL, swap_percent REAL, disk_read_mbps REAL, disk_write_mbps REAL, net_sent_kbps REAL, net_recv_kbps REAL)")
    old.commit()
    old.close()
    with db.connect(path) as conn:
        db.save_sample(conn, {"system": {"ts": 1.0, "cpu_percent": 5.0, "cpu_temp_c": 48.0}, "gpus": [], "processes": [], "disks": []})
        assert conn.execute("SELECT cpu_temp_c FROM system_metrics").fetchone()[0] == 48.0
    db.connect(path).close()                                                         # a second open does not add it again


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
