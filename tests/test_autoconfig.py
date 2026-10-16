import pytest

import autoconfig
from detectors import android_device, cpu, monitor, usb_topology
from detectors.monitor import Monitor


def test_apply_profile_overlays_and_translates_audio():
    config = {
        "audio_route": "off",
        "bitrate_max": "32M",
        "profiles": {"x": {"bitrate_max": "6M", "audio": True, "label": "X", "description": "d"}},
    }
    merged = autoconfig.apply_profile(config, "x")
    assert merged["bitrate_max"] == "6M"
    assert merged["audio_route"] == "scrcpy"  # "audio: true" ya no queda tapado
    assert "label" not in merged and "description" not in merged
    assert config["bitrate_max"] == "32M"  # no muta el original


def test_apply_profile_unknown_raises():
    with pytest.raises(autoconfig.ProfileError, match="nope"):
        autoconfig.apply_profile({"profiles": {"a": {}}}, "nope")
    assert autoconfig.apply_profile({"a": 1}, None) == {"a": 1}


def _patch_all(monkeypatch, phone, usb, mon, cpu_status):
    monkeypatch.setattr(android_device, "detect", lambda serial=None: phone)
    monkeypatch.setattr(usb_topology, "find_topology_by_serial", lambda serial: usb)
    monkeypatch.setattr(monitor, "detect_all", lambda: [mon])
    monkeypatch.setattr(cpu, "detect", lambda: cpu_status)


def test_gather_ok(monkeypatch, phone, usb3, monitor, cpu_perf):
    _patch_all(monkeypatch, phone, usb3, monitor, cpu_perf)
    info = autoconfig.gather({})
    assert info.device is phone and info.monitor is monitor and info.notes == []


def test_gather_device_error_keeps_real_message(monkeypatch):
    def boom(serial=None):
        raise android_device.AndroidDeviceError("Hay 2 celulares conectados")

    monkeypatch.setattr(android_device, "detect", boom)
    with pytest.raises(autoconfig.SetupError, match="Hay 2 celulares"):
        autoconfig.gather({})


def test_gather_passes_serial_and_preferred(monkeypatch, phone, usb3, monitor, cpu_perf):
    seen = []
    _patch_all(monkeypatch, phone, usb3, monitor, cpu_perf)
    monkeypatch.setattr(android_device, "detect", lambda serial=None: seen.append(serial) or phone)
    autoconfig.gather({"preferred_device_serial": "AAA"})
    autoconfig.gather({"preferred_device_serial": "AAA"}, serial="BBB")
    assert seen == ["AAA", "BBB"]


def test_gather_usb_failure_is_a_note(monkeypatch, phone, monitor, cpu_perf):
    _patch_all(monkeypatch, phone, None, monitor, cpu_perf)

    def fail(serial):
        raise usb_topology.UsbTopologyError("sin udev")

    monkeypatch.setattr(usb_topology, "find_topology_by_serial", fail)
    info = autoconfig.gather({})
    assert any("sin udev" in n for n in info.notes)
    assert info.usb.negotiated_speed == "unknown"


def test_gather_skips_usb_lookup_for_wifi(monkeypatch, phone, usb3, monitor, cpu_perf):
    phone.serial = "192.168.1.9:5555"
    _patch_all(monkeypatch, phone, usb3, monitor, cpu_perf)
    monkeypatch.setattr(
        usb_topology, "find_topology_by_serial",
        lambda s: (_ for _ in ()).throw(AssertionError("no debe buscar USB por WiFi")),
    )
    assert autoconfig.gather({}).notes == []


def test_gather_monitor_fallback(monkeypatch, phone, usb3, cpu_perf):
    _patch_all(monkeypatch, phone, usb3, None, cpu_perf)

    def fail():
        raise monitor.MonitorError("sin pantalla")

    monkeypatch.setattr(monitor, "detect_all", fail)
    with pytest.raises(autoconfig.SetupError, match="monitor"):
        autoconfig.gather({})
    fallback = [Monitor("eDP-1", 2560, 1440, True)]
    info = autoconfig.gather({}, fallback_monitors=fallback)
    assert info.monitor.width == 2560
    assert info.notes and "sin pantalla" in info.notes[0]


def test_gather_cpu_failure_is_a_note(monkeypatch, phone, usb3, monitor, cpu_perf):
    _patch_all(monkeypatch, phone, usb3, monitor, cpu_perf)

    def fail():
        raise cpu.CpuError("sin cpufreq")

    monkeypatch.setattr(cpu, "detect", fail)
    info = autoconfig.gather({})
    assert info.cpu.governor == "unknown" and info.notes
