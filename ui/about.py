"""Diálogo «Acerca de NexoHub»: versión, entorno, rutas y atajos de teclado."""
from __future__ import annotations

import platform
import sys

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import config_store
import logging_setup
from version import APP_NAME, APP_TAGLINE, AUTHOR_HANDLE, AUTHOR_URL, LICENSE_NAME, __version__

from . import icons
from .theme import COLORS
from .widgets import divider, icon_button, section_title
from .workers import VersionInfoWorker

# (atajo, qué hace). La ventana principal registra estos mismos atajos.
SHORTCUTS = [
    ("F5", "Detectar celular"),
    ("Ctrl+Enter", "Lanzar scrcpy"),
    ("Ctrl+.", "Detener la transmisión"),
    ("Ctrl+S", "Guardar ajustes"),
    ("F12", "Captura de pantalla del celular"),
    ("F1", "Esta ventana"),
    ("Ctrl+Q", "Salir"),
]


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Acerca de {APP_NAME}")
        self.setMinimumWidth(560)
        self._scrcpy = "…"
        self._adb = "…"
        self._worker: VersionInfoWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(64))
        logo.setFixedSize(64, 64)
        header.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        name = QLabel(APP_NAME)
        name.setObjectName("brand")
        name.setStyleSheet("font-size: 22px;")
        titles.addWidget(name)
        subtitle = QLabel(f"{APP_TAGLINE} · versión {__version__} · licencia {LICENSE_NAME}")
        subtitle.setObjectName("brandSubtitle")
        titles.addWidget(subtitle)
        author = QPushButton(AUTHOR_HANDLE)
        author.setObjectName("authorBadge")
        author.setCursor(Qt.CursorShape.PointingHandCursor)
        author.setToolTip(f"Abrir {AUTHOR_URL}")
        author.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_URL)))
        titles.addSpacing(4)
        titles.addWidget(author, 0, Qt.AlignmentFlag.AlignLeft)
        header.addLayout(titles, 1)
        root.addLayout(header)

        blurb = QLabel(
            "Detecta tu celular, tu monitor y tu equipo, y arma el comando de scrcpy "
            "ideal para transmitir o grabar con la menor latencia posible."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {COLORS['subtext']};")
        root.addWidget(blurb)

        root.addWidget(divider())
        root.addWidget(section_title("Entorno"))
        self.env_grid = QGridLayout()
        self.env_grid.setHorizontalSpacing(18)
        self.env_grid.setVerticalSpacing(6)
        self._env_values: dict[str, QLabel] = {}
        self._raw: dict[str, str] = {}
        rows = [
            ("python", "Python", platform.python_version()),
            ("qt", "PyQt / Qt", f"{PYQT_VERSION_STR} / {QT_VERSION_STR}"),
            ("scrcpy", "scrcpy", self._scrcpy),
            ("adb", "adb", self._adb),
            ("system", "Sistema", f"{platform.system()} {platform.release()}"),
            ("platform", "Pantalla", self._platform_name()),
            ("config", "Configuración", str(config_store.user_config_path())),
            ("log", "Registro", str(logging_setup.log_file_path())),
        ]
        for index, (key, label, value) in enumerate(rows):
            caption = QLabel(label)
            caption.setStyleSheet(f"color: {COLORS['subtext']};")
            self._raw[key] = value
            # Un espacio de ancho cero tras cada «/» deja partir rutas largas en varias líneas.
            shown = QLabel(value.replace("/", "/\u200b"))
            shown.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            shown.setWordWrap(True)
            self.env_grid.addWidget(caption, index, 0, Qt.AlignmentFlag.AlignTop)
            self.env_grid.addWidget(shown, index, 1)
            self._env_values[key] = shown
        self.env_grid.setColumnStretch(1, 1)
        root.addLayout(self.env_grid)

        root.addWidget(divider())
        root.addWidget(section_title("Atajos de teclado"))
        keys = QGridLayout()
        keys.setHorizontalSpacing(18)
        keys.setVerticalSpacing(5)
        for index, (combo, text) in enumerate(SHORTCUTS):
            chip = QLabel(combo)
            chip.setStyleSheet(
                f"background-color: {COLORS['surface0']}; border: 1px solid {COLORS['surface1']}; "
                f"border-radius: 6px; padding: 2px 8px; font-weight: 700; font-size: 11px;"
            )
            keys.addWidget(chip, index, 0, Qt.AlignmentFlag.AlignLeft)
            keys.addWidget(QLabel(text), index, 1)
        keys.setColumnStretch(1, 1)
        root.addLayout(keys)

        root.addWidget(divider())
        buttons = QHBoxLayout()
        self.copy_btn = icon_button("copy", "Copiar datos", tooltip=(
            "Copia versión, entorno y rutas al portapapeles para pegarlos en un reporte de error."
        ))
        self.copy_btn.clicked.connect(self._copy_info)
        folder_btn = icon_button("folder", "Abrir carpeta", tooltip="Abre la carpeta donde NexoHub guarda tus ajustes.")
        folder_btn.clicked.connect(self._open_config_folder)
        close_btn = QPushButton("Cerrar")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(folder_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self._load_versions()

    # -- datos ---------------------------------------------------------------
    @staticmethod
    def _platform_name() -> str:
        app = QGuiApplication.instance()
        return app.platformName() if app is not None else sys.platform

    def _load_versions(self) -> None:
        worker = VersionInfoWorker()
        worker.done.connect(self._on_versions)
        self._worker = worker
        worker.start()

    def _on_versions(self, scrcpy: str | None, adb: str | None) -> None:
        self._scrcpy = scrcpy or "no encontrado"
        self._adb = adb or "no encontrado"
        self._raw["scrcpy"] = self._scrcpy
        self._raw["adb"] = self._adb
        self._env_values["scrcpy"].setText(self._scrcpy)
        self._env_values["adb"].setText(self._adb)

    def info_text(self) -> str:
        lines = [f"{APP_NAME} {__version__}"]
        for key in ("python", "qt", "scrcpy", "adb", "system", "platform", "config", "log"):
            caption = {
                "python": "Python", "qt": "PyQt / Qt", "scrcpy": "scrcpy", "adb": "adb",
                "system": "Sistema", "platform": "Pantalla", "config": "Configuración",
                "log": "Registro",
            }[key]
            lines.append(f"{caption}: {self._raw[key]}")
        return "\n".join(lines)

    def _copy_info(self) -> None:
        QGuiApplication.clipboard().setText(self.info_text())
        self.copy_btn.setText("Copiado")

    def _open_config_folder(self) -> None:
        folder = config_store.user_config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -- ciclo de vida -------------------------------------------------------
    def done(self, result: int) -> None:  # noqa: A003 (nombre de Qt)
        # El hilo es corto (dos `--version`), pero no se destruye en pleno vuelo.
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        super().done(result)
