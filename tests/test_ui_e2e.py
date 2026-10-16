"""Pruebas de la interfaz de punta a punta, sin celular ni pantalla.

Usan Qt en modo «offscreen» y un ``adb``/``scrcpy`` simulados (tests/fakes)
que se ponen primero en el PATH. Cubren el recorrido real: detectar, lanzar,
detener, capturar, grabar, perfiles propios, desconexión y cierre.
"""
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

FAKES = Path(__file__).parent / "fakes"
SERIAL = "FAKE123"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    from ui.theme import STYLESHEET

    app.setStyleSheet(STYLESHEET)
    return app


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """adb/scrcpy simulados en el PATH; el estado del 'celular' vive en archivos."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("adb", "scrcpy"):
        target = bin_dir / name
        shutil.copy(FAKES / name, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    state = tmp_path / "fake_state"
    state.mkdir()
    (state / "connected").touch()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_STATE", str(state))
    return state


@pytest.fixture
def window(qapp, fake_env, tmp_path, monkeypatch):
    from ui import config_page
    from ui.main_window import MainWindow

    monkeypatch.setattr(config_page, "POLL_INTERVAL_MS", 250)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings)
    win.show()
    yield win
    win.close()
    pump(qapp, 0.2)


def pump(app, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(app, condition, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def detect(app, win):
    cw = win.config_widget
    cw.on_detect()
    assert wait_until(app, lambda: cw.device is not None), "no se detectó el celular simulado"
    return cw


# --------------------------------------------------------------------------
def test_window_builds_with_builtin_profiles(qapp, window):
    cw = window.config_widget
    assert {"__custom__", "competitive", "streaming", "quality"} <= set(cw.profile_selector.cards)
    assert not cw.launch_btn.isEnabled()  # todavía no hay celular detectado
    assert not cw.stop_btn.isEnabled()


def test_detect_launch_and_stop(qapp, window, fake_env):
    cw = detect(qapp, window)
    assert cw.device.serial == SERIAL
    assert "Pixel" in window.status_text.text()
    assert cw.launch_btn.isEnabled()

    cw.on_launch()
    assert wait_until(qapp, cw.is_streaming, 5)
    assert wait_until(qapp, lambda: "FPS" in window.status_text.text(), 6)
    assert window.status_chip.property("state") == "live"
    assert "En vivo" in window.windowTitle()
    args = (fake_env / "scrcpy.log").read_text()
    assert f"-s {SERIAL}" in args and "--print-fps" in args

    cw.on_stop()
    assert wait_until(qapp, lambda: not cw.is_streaming(), 8)
    assert wait_until(qapp, lambda: window.status_chip.property("state") == "idle", 3)
    assert cw.launch_btn.isEnabled() and not cw.stop_btn.isEnabled()


def test_screenshot_is_saved(qapp, window, tmp_path):
    cw = detect(qapp, window)
    cw.on_screenshot()
    folder = Path(os.environ["HOME"]) / "Pictures" / "NexoHub"
    assert wait_until(qapp, lambda: folder.exists() and any(folder.glob("*.png")), 10)
    assert next(folder.glob("*.png")).read_bytes().startswith(b"\x89PNG")


def test_recording_adds_record_flag(qapp, window, fake_env):
    cw = detect(qapp, window)
    cw.record_btn.setChecked(True)
    cw.on_launch()
    assert wait_until(qapp, cw.is_streaming, 5)
    assert wait_until(qapp, lambda: (fake_env / "scrcpy.log").exists(), 5)
    assert "--record=" in (fake_env / "scrcpy.log").read_text()
    cw.on_stop()
    assert wait_until(qapp, lambda: not cw.is_streaming(), 8)
    videos = Path(os.environ["HOME"]) / "Videos" / "NexoHub"
    assert any(videos.glob("*.mkv"))


def test_custom_profile_roundtrip(qapp, window, monkeypatch):
    cw = window.config_widget
    cw._on_profile_selected("competitive")
    assert cw.profile_selector.current == "competitive"
    # Editar a mano algo del perfil lo deja en «Personalizado».
    cw.video_page.fields["bitrate_max"].widget.setText("9M")  # como si lo escribiera el usuario
    assert cw.profile_selector.current == "__custom__"

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Mi juego", True)))
    cw.on_save_profile()
    assert "mi_juego" in cw.profile_selector.cards
    saved = Path(os.environ["XDG_CONFIG_HOME"]) / "nexohub" / "config.yaml"
    assert "mi_juego" in saved.read_text()

    cw.on_delete_profile("mi_juego")
    assert "mi_juego" not in cw.profile_selector.cards
    assert "mi_juego" not in saved.read_text()


def test_save_settings_writes_only_differences(qapp, window):
    cw = window.config_widget
    cw.video_page.fields["max_fps"].widget.setValue(90)
    cw.on_save_config()
    saved = Path(os.environ["XDG_CONFIG_HOME"]) / "nexohub" / "config.yaml"
    text = saved.read_text()
    assert "max_fps: 90" in text
    assert "wifi_port" not in text  # igual al valor de fábrica: no se guarda


def test_device_lost_and_found_again(qapp, window, fake_env, monkeypatch):
    from ui import config_page

    monkeypatch.setattr(config_page, "MISSES_BEFORE_LOST", 2)
    cw = detect(qapp, window)
    (fake_env / "connected").unlink()
    assert wait_until(qapp, lambda: cw.device is None, 10), "no notó que se desenchufó"
    assert window.status_text.text() == "Sin conectar"
    (fake_env / "connected").touch()
    assert wait_until(qapp, lambda: cw.device is not None, 10), "no volvió a detectar solo"


def test_close_while_streaming_stops_cleanly(qapp, window, fake_env):
    cw = detect(qapp, window)
    cw.on_launch()
    assert wait_until(qapp, cw.is_streaming, 5)
    window.close()
    pump(qapp, 0.3)
    assert not window.isVisible()
    assert not cw.is_streaming()


def test_about_dialog_lists_versions(qapp, window):
    from ui.about import AboutDialog

    dialog = AboutDialog(window)
    assert wait_until(qapp, lambda: "4.1" in dialog._raw["scrcpy"], 8)
    text = dialog.info_text()
    assert "NexoHub" in text
    assert "scrcpy: 4.1" in text
    dialog.close()


def test_wifi_switch_and_back(qapp, window, fake_env):
    cw = detect(qapp, window)
    cw.on_toggle_wifi()
    assert wait_until(qapp, lambda: cw.connection_mode == "wifi", 10)
    assert cw.device.serial == "192.168.1.50:5555"
    assert "WiFi" in window.status_text.text()
    assert (fake_env / "wifi").exists()

    cw.on_toggle_wifi()
    assert wait_until(qapp, lambda: cw.connection_mode == "usb" and cw.device is None, 10)
    assert not (fake_env / "wifi").exists()


def test_phone_resolution_apply_and_reset(qapp, window, fake_env):
    cw = detect(qapp, window)
    section = cw.device_panel.resolution_section
    cw._run_resolution_worker("set", 720, 1600)
    assert wait_until(qapp, lambda: (fake_env / "wmsize").exists(), 10)
    assert (fake_env / "wmsize").read_text().strip() == "720x1600"
    cw._run_resolution_worker("reset")
    assert wait_until(qapp, lambda: not (fake_env / "wmsize").exists(), 10)
    assert wait_until(qapp, lambda: "nativa" in section.status_label.text(), 5)


def test_scrcpy_failure_is_reported(qapp, window, fake_env):
    cw = detect(qapp, window)
    (fake_env / "scrcpy_fail").touch()
    cw.on_launch()
    assert wait_until(qapp, lambda: not cw.is_streaming(), 8)
    pump(qapp, 0.3)
    assert cw.launch_btn.isEnabled() and not cw.stop_btn.isEnabled()
    assert "Could not find any ADB device" in cw.log_page.log_view.toPlainText()
