"""Pone la raíz del proyecto en sys.path (el proyecto no es un paquete
instalable: se ejecuta desde su carpeta) y aísla la config de cada test."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detectors.android_device import AndroidDevice, Encoder  # noqa: E402
from detectors.cpu import CpuStatus  # noqa: E402
from detectors.monitor import Monitor  # noqa: E402
from detectors.usb_topology import UsbNode, UsbTopology  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Ningún test toca el ~/.config ni el ~/.local/state reales."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def phone():
    return AndroidDevice(
        serial="R58M12ABCD",
        model="SM-A525F",
        brand="samsung",
        android_version="14",
        chipset="atoll",
        encoders=[
            Encoder("c2.qti.avc.encoder", "h264", "hw"),
            Encoder("c2.qti.hevc.encoder", "h265", "hw"),
            Encoder("c2.android.avc.encoder", "h264", "sw"),
        ],
    )


@pytest.fixture
def usb2():
    return UsbTopology(device_node=UsbNode("/sys/x", speed_mbps="480", power_control="on"))


@pytest.fixture
def usb3():
    return UsbTopology(device_node=UsbNode("/sys/x", speed_mbps="5000", power_control="on"))


@pytest.fixture
def monitor():
    return Monitor(name="DP-1", width=1920, height=1080, is_primary=True)


@pytest.fixture
def cpu_perf():
    return CpuStatus(governor="performance", current_freq_mhz=3000, max_freq_mhz=4000)
