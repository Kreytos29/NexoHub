"""Parsers de salidas de adb/scrcpy y utilidades puras."""
import pytest

from detectors import android_device, resolution, wireless

LIST_ENCODERS = """\
[server] INFO: Device: [samsung] samsung SM-A525F (Android 14)
List of video encoders:
    --video-codec=h264 --video-encoder=c2.qti.avc.encoder    (hw) [vendor]
    --video-codec=h265 --video-encoder=c2.qti.hevc.encoder   (hw) [vendor]
    --video-codec=h264 --video-encoder=c2.android.avc.encoder (sw)
List of audio encoders:
    --audio-codec=opus --audio-encoder=c2.android.opus.encoder (sw)
"""


def test_parse_encoders(monkeypatch):
    monkeypatch.setattr(android_device, "_run", lambda cmd: LIST_ENCODERS)
    encoders = android_device._parse_encoders("X1")
    assert [(e.codec, e.kind) for e in encoders] == [("h264", "hw"), ("h265", "hw"), ("h264", "sw")]
    device = android_device.AndroidDevice(serial="X1", encoders=encoders)
    assert device.best_encoder.name == "c2.qti.avc.encoder"
    assert device.best_encoder_for_codec("h265").name == "c2.qti.hevc.encoder"
    assert device.best_encoder_for_codec("av1") is None


def test_list_connected_serials_blocking_rules(monkeypatch):
    monkeypatch.setattr(android_device, "_check_binaries", lambda: None)
    out = (
        "List of devices attached\nAAA\tdevice usb:1\nBBB\tunauthorized usb:2\n"
    )
    monkeypatch.setattr(android_device, "_run", lambda cmd: out)
    assert android_device.list_connected_serials() == ["AAA"]  # otro sin autorizar no bloquea
    with pytest.raises(android_device.AndroidDeviceError, match="autorizado"):
        android_device.list_connected_serials(target="BBB")

    only_unauth = "List of devices attached\nBBB\tunauthorized usb:2\n"
    monkeypatch.setattr(android_device, "_run", lambda cmd: only_unauth)
    with pytest.raises(android_device.AndroidDeviceError):
        android_device.list_connected_serials()


def test_detect_prefers_the_usb_serial_over_wifi_twin(monkeypatch):
    monkeypatch.setattr(android_device, "list_connected_serials", lambda target=None: ["1.2.3.4:5555", "ABC"])
    monkeypatch.setattr(android_device, "_getprop", lambda serial, prop: "x")
    monkeypatch.setattr(android_device, "_parse_encoders", lambda serial: [])
    assert android_device.detect().serial == "ABC"


def test_detect_requires_explicit_serial_with_two_phones(monkeypatch):
    monkeypatch.setattr(android_device, "list_connected_serials", lambda target=None: ["AAA", "BBB"])
    with pytest.raises(android_device.AndroidDeviceError, match="2 celulares"):
        android_device.detect()


def test_wireless_serial():
    assert wireless.is_wireless_serial("192.168.1.23:5555")
    assert not wireless.is_wireless_serial("R58M12ABCD")
    assert not wireless.is_wireless_serial("")


def test_wm_size_parsing():
    physical, override = resolution._parse_sizes("Physical size: 1080x2400\nOverride size: 720x1600\n")
    assert physical == (1080, 2400) and override == (720, 1600)
    assert resolution._parse_sizes("Physical size: 1080x2400\n") == ((1080, 2400), None)


def test_size_for_monitor_is_portrait():
    assert resolution.size_for_monitor(1920, 1080) == (1080, 1920)
