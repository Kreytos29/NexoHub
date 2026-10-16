"""Ventana principal: barra superior con estado en vivo, contenido con scroll,
barra de acciones fija, avisos tipo «toast» y atajos de teclado."""
from __future__ import annotations

import time

from PyQt6.QtCore import QByteArray, QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from version import APP_NAME, APP_TAGLINE, AUTHOR_HANDLE, AUTHOR_URL, __version__

from . import helpers, icons
from .about import AboutDialog
from .config_page import ConfigWidget
from .theme import COLORS
from .widgets import Toast, icon_button

DEFAULT_SIZE = (1020, 900)
MIN_SIZE = (880, 620)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("NexoHub", "NexoHub")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*DEFAULT_SIZE)
        self._toasts: list[Toast] = []
        self._device_text = "Sin conectar"
        self._device_ok = False
        self._streaming = False
        self._stream_started = 0.0
        self._fps = 0.0
        self._closing = False

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())

        self.config_widget = ConfigWidget(self.settings)
        cw = self.config_widget
        cw.status_message.connect(self._show_status)
        cw.device_status.connect(self._on_device_status)
        cw.toast_requested.connect(self._show_toast)
        cw.streaming_changed.connect(self._on_streaming_changed)
        cw.fps_changed.connect(self._on_fps_changed)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("contentArea")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(28, 24, 28, 18)
        inner_layout.addWidget(cw)
        self.scroll.setWidget(inner)
        cw.scroll_to_requested.connect(self._scroll_to)
        root.addWidget(self.scroll, 1)

        # La barra de acciones va fuera del scroll para que Lanzar/Detener
        # estén siempre a la mano, sin importar qué acordeón esté abierto.
        bar_wrap = QWidget()
        bar_wrap.setObjectName("actionBarWrap")
        bar_layout = QVBoxLayout(bar_wrap)
        bar_layout.setContentsMargins(28, 0, 28, 16)
        bar_layout.addWidget(cw.action_bar)
        root.addWidget(bar_wrap)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._show_status("Listo")

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._refresh_status_chip)

        self._install_shortcuts()
        self._restore_geometry()
        self._refresh_status_chip()

    # ------------------------------------------------------------------
    # Barra superior
    # ------------------------------------------------------------------
    def _build_topbar(self) -> QWidget:
        topbar = QWidget()
        topbar.setObjectName("topbar")
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(28, 14, 28, 14)
        layout.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(38))
        logo.setFixedSize(38, 38)
        layout.addWidget(logo)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        name = QLabel(APP_NAME)
        name.setObjectName("brand")
        subtitle = QLabel(APP_TAGLINE)
        subtitle.setObjectName("brandSubtitle")
        brand_box.addWidget(name)
        brand_box.addWidget(subtitle)
        layout.addLayout(brand_box)

        layout.addStretch(1)

        version = QLabel(f"v{__version__}")
        version.setProperty("role", "pill")
        version.setToolTip("Versión de NexoHub")
        layout.addWidget(version)

        self.status_chip = QFrame()
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setProperty("state", "idle")
        chip_layout = QHBoxLayout(self.status_chip)
        chip_layout.setContentsMargins(14, 8, 16, 8)
        chip_layout.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        self.status_text = QLabel("Sin conectar")
        self.status_text.setObjectName("statusText")
        chip_layout.addWidget(self.status_dot)
        chip_layout.addWidget(self.status_text)
        layout.addWidget(self.status_chip)

        author_btn = QPushButton(AUTHOR_HANDLE)
        author_btn.setObjectName("authorBadge")
        author_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        author_btn.setToolTip(f"Abrir {AUTHOR_URL} en el navegador")
        author_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_URL)))
        layout.addWidget(author_btn)

        info_btn = icon_button(
            "info", color=COLORS["subtext"], tooltip="Acerca de NexoHub y atajos (F1)",
            object_name="exitButton",
        )
        info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        info_btn.clicked.connect(self.show_about)
        layout.addWidget(info_btn)

        exit_btn = icon_button(
            "power", color=COLORS["subtext"],
            tooltip="Cierra NexoHub (Ctrl+Q). Si hay una transmisión activa, se detiene.",
            object_name="exitButton",
        )
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.clicked.connect(self.close)
        layout.addWidget(exit_btn)
        return topbar

    def _refresh_status_chip(self) -> None:
        """Redibuja el chip: en vivo (tiempo + FPS) o estado del celular."""
        live = self._streaming
        if live:
            text = "En vivo · " + helpers.format_elapsed(time.monotonic() - self._stream_started)
            if self._fps > 0:
                text += f" · {self._fps:.0f} FPS"
            dot = COLORS["red"]
        else:
            text = self._device_text
            dot = COLORS["green"] if self._device_ok else COLORS["surface2"]
        self.status_text.setText(text)
        self.status_dot.setStyleSheet(f"background-color: {dot};")
        state = "live" if live else "idle"
        if self.status_chip.property("state") != state:
            self.status_chip.setProperty("state", state)
            self.status_chip.style().unpolish(self.status_chip)
            self.status_chip.style().polish(self.status_chip)
        self.setWindowTitle(f"{APP_NAME} · En vivo" if live else APP_NAME)

    # ------------------------------------------------------------------
    # Señales del controlador
    # ------------------------------------------------------------------
    def _on_device_status(self, text: str, ok: bool) -> None:
        self._device_text = text
        self._device_ok = ok
        self._refresh_status_chip()

    def _on_streaming_changed(self, streaming: bool) -> None:
        self._streaming = streaming
        self._fps = 0.0
        if streaming:
            self._stream_started = time.monotonic()
            self._clock.start()
        else:
            self._clock.stop()
        self._refresh_status_chip()

    def _on_fps_changed(self, fps: float) -> None:
        self._fps = fps

    def _scroll_to(self, widget) -> None:
        if widget is not None:
            self.scroll.ensureWidgetVisible(widget, 0, 40)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    # ------------------------------------------------------------------
    # Toasts
    # ------------------------------------------------------------------
    def _show_toast(self, message: str, kind: str, action_text: str = "", action=None) -> None:
        toast = Toast(self, message, kind, action_text, action)
        toast.closed.connect(self._on_toast_closed)
        self._toasts.append(toast)
        toast.show()
        toast.raise_()
        self._reposition_toasts()

    def _on_toast_closed(self, toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._reposition_toasts()

    def _reposition_toasts(self) -> None:
        margin = 22
        spacing = 10
        # Por encima de la barra de acciones y de la barra de estado.
        y = self.height() - margin - self.statusBar().height() - self.config_widget.action_bar.height() - 20
        for toast in reversed(self._toasts):
            y -= toast.height()
            toast.move(self.width() - toast.width() - margin, y)
            y -= spacing

    def resizeEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        super().resizeEvent(event)
        self._reposition_toasts()

    # ------------------------------------------------------------------
    # Atajos
    # ------------------------------------------------------------------
    def _install_shortcuts(self) -> None:
        cw = self.config_widget
        bindings = [
            ("F5", cw.on_detect),
            ("Ctrl+Return", cw.on_launch),
            ("Ctrl+Enter", cw.on_launch),
            ("Ctrl+.", cw.on_stop),
            ("Ctrl+S", cw.on_save_config),
            ("F12", cw.on_screenshot),
            ("F1", self.show_about),
            ("Ctrl+Q", self.close),
        ]
        self._shortcuts = []
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda cb=callback: cb())
            self._shortcuts.append(shortcut)

    def show_about(self) -> None:
        AboutDialog(self).exec()

    # ------------------------------------------------------------------
    # Geometría y cierre
    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        try:
            data = self.settings.value("window/geometry")
            if isinstance(data, QByteArray) and not data.isEmpty():
                self.restoreGeometry(data)
        except Exception:  # noqa: BLE001 - la geometría guardada nunca debe impedir abrir
            pass

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (nombre de Qt)
        if self._closing:
            event.accept()
            return
        if not self.config_widget.confirm_close():
            event.ignore()
            return
        self._closing = True
        try:
            self.settings.setValue("window/geometry", self.saveGeometry())
        except Exception:  # noqa: BLE001
            pass
        self.config_widget.cleanup()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)
