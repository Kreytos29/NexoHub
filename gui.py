#!/usr/bin/env python3
"""
NexoHub — interfaz gráfica (PyQt6).

Este archivo es solo el punto de entrada: crea la aplicación, aplica el tema
y abre la ventana. Toda la interfaz vive en el paquete ``ui/``:

    ui/main_window.py   ventana, barra superior, atajos
    ui/config_page.py   pantalla principal (controlador)
    ui/device_panel.py  tarjeta del celular
    ui/pages.py         ajustes de video, ventana, audio, entrada, grabación…
    ui/workers.py       hilos (detección, lanzamiento, capturas, WiFi…)

La contraseña de root (cpupower, power USB, instalar paquetes) nunca se pide
en la terminal: todo pasa por pkexec, que muestra el diálogo nativo de polkit.
"""
from __future__ import annotations

import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

import logging_setup
from version import APP_ID, APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    logging_setup.setup_logging()
    log = logging_setup.get_logger()
    log.info("Iniciando %s %s", APP_NAME, __version__)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName(APP_ID)

    # Fuente explícita para que se vea igual entre distros y temas. Si «Inter»
    # no está instalada, Qt cae a la mejor sans-serif disponible.
    font = QFont("Inter")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(10)
    app.setFont(font)

    from ui import icons
    from ui.main_window import MainWindow
    from ui.theme import STYLESHEET

    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(icons.app_icon())

    window = MainWindow()
    window.show()
    code = app.exec()
    log.info("%s cerrado (código %s)", APP_NAME, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
