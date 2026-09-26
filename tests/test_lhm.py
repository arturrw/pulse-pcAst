"""Unit tests for reading the CPU temperature from LibreHardwareMonitor. Run: python tests/test_lhm.py (or pytest)."""
import tempfile
from pathlib import Path

from pulse import collectors, db, lhm


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


def _group(text, icon, *sensors):
    return _node(text, list(sensors), ImageURL=f"images_icon/{icon}.png")


AMD_RX = _node("AMD Radeon RX 7800 XT", [
    _group("Temperatures", "temperature", _sensor("GPU Core", "64.0 °C", "/gpu-amd/0/temperature/0"),
           _sensor("GPU Hot Spot", "81.0 °C", "/gpu-amd/0/temperature/2")),
    _group("Load", "load", _sensor("GPU Core", "97.0 %", "/gpu-amd/0/load/0", "Load"), _sensor("GPU Memory", "40.0 %", "/gpu-amd/0/load/1", "Load")),
    _group("Powers", "power", _sensor("GPU Package", "240.5 W", "/gpu-amd/0/power/0", "Power")),
    _group("Data", "data", _sensor("GPU Memory Used", "9.5 GB", "/gpu-amd/0/data/0", "Data"), _sensor("GPU Memory Total", "16.0 GB", "/gpu-amd/0/data/2", "Data")),
], ImageURL="images_icon/ati.png")
AMD_IGPU = _node("AMD Radeon(TM) Graphics", [
    _group("Load", "load", _sensor("GPU Core", "3.0 %", "/gpu-amd/1/load/0", "Load")),
    _group("Data", "data", _sensor("GPU Memory Total", "0.5 GB", "/gpu-amd/1/data/2", "Data")),
], ImageURL="images_icon/ati.png")
INTEL_IGPU = _node("Intel(R) UHD Graphics 770", [
    _group("Load", "load", _sensor("D3D 3D", "12.0 %", "/gpu-intel/0/load/0", "Load")),
    _group("Small Data", "smalldata", _sensor("D3D Dedicated Memory Used", "128.0 MB", "/gpu-intel/0/smalldata/0", "SmallData")),
], ImageURL="images_icon/intel.png")


def test_amd_card_values_and_the_discrete_card_comes_first():
    cards = lhm.gpus(_tree(INTEL, AMD_IGPU, AMD_RX))                           # the processor is not a card
    assert [c["name"] for c in cards] == ["AMD Radeon RX 7800 XT", "AMD Radeon(TM) Graphics"]
    rx = cards[0]
    assert rx == {"vendor": "amd", "name": "AMD Radeon RX 7800 XT", "util_percent": 97.0, "temp_c": 64.0,
                  "mem_used_mb": 9.5 * 1024, "mem_total_mb": 16.0 * 1024, "power_w": 240.5}   # GB read as MB, core not hot spot
    assert cards[1]["temp_c"] is None and cards[1]["power_w"] is None                   # what a card does not report is None


def test_an_intel_integrated_card_goes_after_any_other_and_reads_d3d_load():
    cards = lhm.gpus(_tree(INTEL_IGPU, AMD_IGPU))
    assert [c["vendor"] for c in cards] == ["amd", "intel"]
    assert cards[1]["util_percent"] == 12.0 and cards[1]["mem_used_mb"] == 128.0 and cards[1]["mem_total_mb"] is None


def test_old_versions_without_sensor_ids_find_the_card_by_its_icon():
    old = _node("Radeon RX 580", [_group("Load", "load", _node("GPU Core", Value="55.0 %")),
                                  _group("Temperatures", "temperature", _node("GPU Core", Value="70.0 °C"))], ImageURL="images_icon/ati.png")
    assert lhm.gpus(_tree(old))[0]["util_percent"] == 55.0 and lhm.gpus(_tree(old))[0]["temp_c"] == 70.0
    assert lhm.gpus(_tree(INTEL)) == []


def test_one_read_serves_the_cpu_temperature_and_the_card_in_the_same_sample():
    now, calls = [0.0], []
    r = lhm.Reader(fetcher=lambda url: calls.append(url) or _tree(INTEL, AMD_RX), clock=lambda: now[0])
    assert r.cpu_temp() == 61.0 and r.gpus()[0]["name"] == "AMD Radeon RX 7800 XT" and len(calls) == 1
    now[0] = 2.0
    r.gpus()
    assert len(calls) == 2                                                            # the next sample reads again


def test_without_nvidia_the_main_card_is_recorded_like_an_nvidia_one():
    reader = lhm.Reader(fetcher=lambda url: _tree(INTEL, INTEL_IGPU, AMD_RX))
    rows = collectors.gpus_from_lhm(5.0, reader)
    assert len(rows) == 1 and rows[0]["name"] == "AMD Radeon RX 7800 XT" and rows[0]["idx"] == 0 and "vendor" not in rows[0]
    path = Path(tempfile.mkdtemp()) / "g.db"
    with db.connect(path) as conn:                                                    # same columns as the NVIDIA rows
        db.save_sample(conn, {"system": {"ts": 5.0}, "gpus": rows, "processes": [], "disks": []})
        assert conn.execute("SELECT name, util_percent, temp_c, power_w FROM gpu_metrics").fetchone() == ("AMD Radeon RX 7800 XT", 97.0, 64.0, 240.5)
    real = collectors.nvidia_present
    collectors.nvidia_present = lambda: False
    real_reader, lhm.READER = lhm.READER, reader
    try:
        assert collectors.collect_gpus(6.0)[0]["name"] == "AMD Radeon RX 7800 XT"
    finally:
        collectors.nvidia_present, lhm.READER = real, real_reader
    assert collectors.gpus_from_lhm(5.0, lhm.Reader(fetcher=lambda url: _tree(INTEL))) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
