"""Overlay de FPS en vivo: placa flotante estilo BlueStacks (gtk-layer-shell)
con respaldo en una ventana Qt común."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

from detectors import dependencies

from .theme import COLORS


class _LegacyFpsOverlay(QWidget):
    """Overlay de respaldo: ventana Qt común, siempre encima, con el FPS
    en vivo. Se usa solo si gtk-layer-shell no está disponible (ver
    FpsOverlay). En X11 funciona razonablemente bien; en Wayland
    (Hyprland incluido) el compositor puede ignorar tanto la posición
    fija como el 'quédate siempre arriba' -- ahí el cliente no tiene
    permiso para imponer ninguna de las dos cosas."""

    def __init__(self, position: str = "bottom-left"):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Sin esto, un QWidget comun (a diferencia de QFrame/QPushButton)
        # IGNORA el background-color/border-radius del stylesheet y queda
        # completamente transparente -- el texto del FPS "flotando" sin
        # ningun recuadro detras, que es justo lo que se perdia contra
        # colores parecidos del juego.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drag_pos = None
        self._position = position

        self.setStyleSheet(
            f"background-color: #000000; border-radius: 10px; "
            f"border: 1px solid {COLORS['accent']};"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        self.label = QLabel("FPS: --")
        self.label.setStyleSheet(
            f"color: {COLORS['green']}; font-size: 16px; font-weight: 800; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(self.label)
        self.resize(130, 42)
        self._move_to_corner()

    def set_position(self, position: str) -> None:
        self._position = position
        self._move_to_corner()

    def _move_to_corner(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 28
        x = geo.left() + margin if "left" in self._position else geo.right() - self.width() - margin
        y = geo.top() + margin if "top" in self._position else geo.bottom() - self.height() - margin
        self.move(x, y)

    def set_fps(self, value: float) -> None:
        color = COLORS["green"]
        if value <= 0:
            color = COLORS["subtext"]
        elif value < 20:
            color = COLORS["red"]
        elif value < 45:
            color = COLORS["yellow"]
        self.label.setStyleSheet(
            f"color: {color}; font-size: 16px; font-weight: 800; "
            f"background: transparent; border: none;"
        )
        self.label.setText(f"FPS: {value:.0f}" if value > 0 else "FPS: --")

    # -- arrastrar la ventanita con el mouse ---------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        self._drag_pos = None


class FpsOverlay:
    """
    Overlay de FPS estilo BlueStacks/emulador: una placa chica, fija en
    la esquina elegida (abajo a la izquierda por defecto), garantizada
    por encima de todo -- incluida la ventana de scrcpy en pantalla
    completa.

    No es un QWidget: corre fps_overlay_helper.py como un proceso GTK3
    aparte que pide la capa "overlay" de wlr-layer-shell (el mismo
    mecanismo que usan waybar o las notificaciones), porque eso es lo
    único que garantiza esa posición y ese "siempre encima" en Wayland
    -- un QWidget de Qt no puede. Se le mandan los valores de FPS por
    su entrada estándar. Si gtk-layer-shell no está instalado, se cae
    de vuelta sola a _LegacyFpsOverlay (una ventana Qt normal) para no
    dejar la función completamente rota.
    """

    HELPER_PATH = Path(__file__).resolve().parent.parent / "fps_overlay_helper.py"

    def __init__(self, log=None):
        self._log = log or (lambda _msg: None)
        self._process: subprocess.Popen | None = None
        self._legacy: _LegacyFpsOverlay | None = None
        self._warned = False
        self._position = "bottom-left"

    def _start_process(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        if not dependencies.gtk_layer_shell_available():
            return False
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(self.HELPER_PATH), self._position],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"No se pudo iniciar el overlay de FPS: {exc}")
            self._process = None
            return False
        # El overlay GTK puede tardar un instante en arrancar, o fallar
        # casi al toque si algo del entorno grafico no coincide con lo
        # que "gtk_layer_shell_available()" pudo chequear en frio (ej.
        # sesion Wayland vs XWayland). Se verifica en 600ms sin bloquear
        # la UI -- si ya murio, se muestra el motivo real en vez de
        # quedar en silencio.
        QTimer.singleShot(600, self._check_process_alive)
        return True

    def _check_process_alive(self) -> None:
        if self._process is None or self._process.poll() is None:
            return  # sigue vivo, todo bien
        stderr_text = ""
        if self._process.stderr is not None:
            try:
                stderr_text = self._process.stderr.read().strip()
            except (OSError, ValueError):
                pass
        self._process = None
        detail = f": {stderr_text.splitlines()[-1]}" if stderr_text else ""
        self._log(
            f"El overlay de FPS (gtk-layer-shell) se cerró solo al "
            f"arrancar{detail}. Usando la ventana de respaldo."
        )
        self._use_legacy()
        self._legacy.set_position(self._position)
        self._legacy.set_fps(0)
        self._legacy.show()

    def _use_legacy(self) -> None:
        if not self._warned:
            self._warned = True
            self._log(
                "Overlay de FPS: no se encontró gtk-layer-shell instalado, "
                "se usa una ventana común de respaldo (en Wayland/Hyprland "
                "puede no quedar fija ni 'siempre encima'). Instalalo desde "
                "la página 'Dependencias' para el overlay estilo BlueStacks."
            )
        if self._legacy is None:
            self._legacy = _LegacyFpsOverlay()

    def show(self, position: str | None = None) -> None:
        if position:
            self._position = position
        if self._start_process():
            self._send(f"POS:{self._position}")
            self._send("SHOW")
            return
        self._use_legacy()
        self._legacy.set_position(self._position)
        self._legacy.set_fps(0)
        self._legacy.show()

    def hide(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._send("HIDE")
        if self._legacy is not None:
            self._legacy.hide()

    def set_fps(self, value: float) -> None:
        if self._process is not None and self._process.poll() is None:
            self._send(f"{value:.1f}")
        elif self._legacy is not None:
            self._legacy.set_fps(value)

    def _send(self, line: str) -> None:
        if self._process is None or self._process.stdin is None:
            return
        try:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            self._process = None

    def close(self) -> None:
        """Termina el proceso auxiliar. Se llama al cerrar NexoHub para
        no dejar un proceso GTK huerfano corriendo en segundo plano."""
        if self._process is not None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
            except OSError:
                pass
            try:
                self._process.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._process = None
        if self._legacy is not None:
            self._legacy.hide()

