"""
Lanza y alimenta keymap/overlay_helper.py (las teclas dibujadas sobre el
juego). Sin gtk-layer-shell o fuera de Hyprland simplemente no se muestra:
el mapeo funciona igual.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import logging_setup
from detectors import dependencies
from keymap.model import Keymap

log = logging_setup.get_logger()

HELPER_PATH = Path(__file__).resolve().parent / "overlay_helper.py"


def unavailable_reason() -> str | None:
    if not dependencies.gtk_layer_shell_available():
        return "falta gtk-layer-shell (se instala desde Dependencias)"
    if shutil.which("hyprctl") is None:
        return "por ahora solo funciona en Hyprland (hace falta ubicar la ventana de scrcpy)"
    return None


class OverlayProcess:
    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()  # se escribe desde el hilo del mapeo y desde la GUI
        self.visible = True

    def start(self) -> bool:
        reason = unavailable_reason()
        if reason:
            log.info("Mapeo de teclas: sin teclas en pantalla (%s).", reason)
            return False
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(HELPER_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.warning("Mapeo de teclas: no se pudo abrir el overlay: %s", exc)
            self._process = None
            return False
        return True

    def _send(self, message: dict) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                return
            try:
                process.stdin.write((json.dumps(message) + "\n").encode())
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                self._process = None

    def set_keymap(self, keymap: Keymap) -> None:
        controls = [c.to_dict() for c in keymap.controls]
        self._send({
            "type": "keymap",
            "controls": controls,
            "keys": keymap.special_keys(),
            "opacity": keymap.overlay_opacity,
        })

    def set_screen(self, width: int, height: int) -> None:
        self._send({"type": "screen", "width": width, "height": height})

    def set_state(self, state: str) -> None:
        self._send({"type": "state", "state": state})

    def toggle(self) -> bool:
        self.visible = not self.visible
        self._send({"type": "visible", "value": self.visible})
        return self.visible

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
