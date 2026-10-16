"""
Lo que corre mientras se juega: sesión de control + motor + teclado/mouse +
teclas dibujadas sobre el juego.

Se usa desde la GUI (en un QThread) y desde la línea de comandos:

    python -m keymap --map freefire [--serial XXXX]

``run()`` bloquea hasta que se llama a ``stop()`` (o falla algo).
"""
from __future__ import annotations

import adb_tools
import logging_setup
from keymap.control import ControlSession
from keymap.engine import Engine
from keymap.inputs import InputError, InputHub, check_access
from keymap.model import Keymap, key_label
from keymap.overlay import OverlayProcess

log = logging_setup.get_logger()

STATE_LOG = {
    "off": "desactivado",
    "active": "modo juego",
    "cursor": "cursor libre",
    "suspended": "en pausa",
}


class KeymapRunner:
    def __init__(
        self,
        serial: str,
        keymap: Keymap,
        on_state_change=None,
        on_command=None,
        on_warning=None,
        devices: list[str] | None = None,
        overlay: bool = True,
    ):
        self.serial = serial
        self.keymap = keymap
        self.on_state_change = on_state_change  # callable(state)
        self.on_command = on_command  # callable("editor")
        self.on_warning = on_warning  # callable(message)
        self.devices = devices
        self.use_overlay = overlay
        self.session: ControlSession | None = None
        self.hub: InputHub | None = None
        self.engine: Engine | None = None
        self.overlay: OverlayProcess | None = None
        self._stopped = False

    def run(self) -> None:
        problem = check_access()
        if problem:
            raise InputError(problem)
        version = adb_tools.scrcpy_version()
        if not version:
            raise InputError("No se encontró scrcpy instalado.")

        self.session = ControlSession(self.serial, version)
        self.session.start()
        try:
            self.engine = Engine(self.keymap, self.session, self._state_changed, self._command)
            self.session.on_screen_change = self._screen_changed
            if self.use_overlay:
                self.overlay = OverlayProcess()
                if self.overlay.start():
                    self.overlay.set_keymap(self.keymap)
                    self.overlay.set_screen(*self.session.screen)
            self.hub = InputHub(self.engine, only=self.devices)
            if self._stopped:
                return
            keys = self.keymap
            log.info(
                "Mapeo de teclas «%s» listo: %s activa, %s pausa, %s cursor, %s editor, %s teclas en pantalla.",
                keys.display_name, key_label(keys.toggle_key), key_label(keys.suspend_key),
                key_label(keys.cursor_key), key_label(keys.editor_key), key_label(keys.overlay_key),
            )
            self.hub.run_with(on_ready=self._report_hub_warning)
        finally:
            if self.engine:
                self.engine.release_all()
            if self.overlay:
                self.overlay.close()
            self.session.close()

    def _report_hub_warning(self) -> None:
        if self.hub and self.hub.warning and self.on_warning:
            self.on_warning(self.hub.warning)

    def _screen_changed(self, width: int, height: int) -> None:
        if self.engine:
            self.engine.screen_changed()
        if self.overlay:
            self.overlay.set_screen(width, height)

    def _state_changed(self, state: str) -> None:
        if self.hub and self.engine:
            self.hub.set_grab(keyboards=self.engine.captures_keyboard, mice=self.engine.captures_mouse)
        if self.overlay:
            self.overlay.set_state(state)
        log.info("Mapeo de teclas: %s.", STATE_LOG.get(state, state))
        if self.on_state_change:
            self.on_state_change(state)

    def _command(self, name: str) -> None:
        if name == "overlay" and self.overlay:
            visible = self.overlay.toggle()
            log.info("Mapeo de teclas: teclas en pantalla %s.", "visibles" if visible else "ocultas")
        elif self.on_command:
            self.on_command(name)

    def reload(self, keymap: Keymap) -> None:
        """Aplica un mapa editado sin cortar la transmisión."""
        self.keymap = keymap
        if self.engine:
            self.engine.set_keymap(keymap)
        if self.overlay:
            self.overlay.set_keymap(keymap)
        log.info("Mapeo de teclas: mapa «%s» recargado.", keymap.display_name)

    def set_active(self, active: bool) -> None:
        if self.engine:
            self.engine.set_active(active)

    def stop(self) -> None:
        self._stopped = True
        if self.engine:
            self.engine.set_active(False)
        if self.hub:
            self.hub.stop()
