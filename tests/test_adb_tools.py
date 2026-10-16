import adb_tools

DEVICES = """\
* daemon not running; starting now at tcp:5037
* daemon started successfully
List of devices attached
R58M12ABCD             device usb:1-2 product:a52sxq model:SM_A525F device:a52sxq transport_id:1
192.168.1.23:5555      device product:a52sxq model:SM_A525F device:a52sxq transport_id:2
ZY22XXXX               unauthorized usb:1-3 transport_id:3
emulator-5554          offline

"""

BATTERY = """\
Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  status: 2
  health: 2
  present: true
  level: 85
  scale: 100
  voltage: 4150
  temperature: 312
  technology: Li-ion
"""


def test_parse_devices():
    devices = adb_tools.parse_devices(DEVICES)
    assert [d.serial for d in devices] == [
        "R58M12ABCD", "192.168.1.23:5555", "ZY22XXXX", "emulator-5554",
    ]
    usb, wifi, unauth, offline = devices
    assert usb.model == "SM A525F" and usb.transport == "usb" and usb.is_ready
    assert wifi.transport == "wifi"
    assert unauth.state == "unauthorized" and not unauth.is_ready
    assert "sin autorizar" in unauth.label
    assert offline.model == ""


def test_parse_devices_empty():
    assert adb_tools.parse_devices("List of devices attached\n\n") == []
    assert adb_tools.parse_devices("") == []


def test_parse_battery():
    info = adb_tools.parse_battery(BATTERY)
    assert info.level == 85
    assert info.temperature_c == 31.2
    assert info.is_charging and info.powered
    assert not info.is_hot
    assert info.summary() == "Batería 85% ⚡ · 31.2 °C"


def test_battery_hot_and_scale():
    info = adb_tools.parse_battery("level: 50\nscale: 200\ntemperature: 431\nstatus: 3\n")
    assert info.level == 25  # se normaliza a porcentaje
    assert info.is_hot and not info.is_charging
    assert info.status_text == "descargando"


def test_parse_battery_garbage():
    info = adb_tools.parse_battery("nada útil")
    assert info.level is None and info.temperature_c is None
    assert info.summary() == ""


def test_screenshot_saves_png(tmp_path, monkeypatch):
    import subprocess

    png = adb_tools.PNG_MAGIC + b"fake-data"
    monkeypatch.setattr(adb_tools.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        adb_tools.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=png, stderr=b""),
    )
    first = adb_tools.screenshot("X1", tmp_path / "caps")
    second = adb_tools.screenshot("X1", tmp_path / "caps")
    assert first.read_bytes() == png
    assert first != second  # no se pisan
    assert first.suffix == ".png"


def test_screenshot_rejects_non_png(tmp_path, monkeypatch):
    import subprocess

    import pytest

    monkeypatch.setattr(adb_tools.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        adb_tools.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout=b"error", stderr=b"closed"),
    )
    with pytest.raises(adb_tools.AdbToolError, match="closed"):
        adb_tools.screenshot("X1", tmp_path)
    assert not list(tmp_path.iterdir())  # no deja archivos a medias


def test_require_missing_binary(monkeypatch):
    import pytest

    monkeypatch.setattr(adb_tools.shutil, "which", lambda name: None)
    with pytest.raises(adb_tools.AdbToolError, match="adb"):
        adb_tools.list_devices()
