"""Página de Dependencias: verifica e instala lo que NexoHub necesita."""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from detectors import dependencies

from .theme import COLORS, apply_shadow as _apply_shadow
from .widgets import DependencyRow
from .workers import DependencyCheckWorker, DependencyInstallWorker


class DependenciesWidget(QWidget):
    checked = pyqtSignal(object)  # DependencyReport, cada vez que termina una verificación

    def __init__(self):
        super().__init__()
        self.report: dependencies.DependencyReport | None = None
        self.check_worker: DependencyCheckWorker | None = None
        self.install_worker: DependencyInstallWorker | None = None
        self.rows: dict[str, DependencyRow] = {}
        self._build_ui()
        self.on_check()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(14)

        box = QGroupBox("Requisitos del sistema")
        _apply_shadow(box)
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(10)

        hint = QLabel(
            "Se detecta el gestor de paquetes de tu distro (pacman, apt, dnf, "
            "zypper) y, si falta algo, se instala pidiendo la contraseña en "
            "una ventana gráfica -- nunca en la terminal."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        box_layout.addWidget(hint)

        specs = []
        for binary in dependencies.REQUIRED_BINARIES:
            specs.append((binary, "obligatorio"))
        for binary in dependencies.OPTIONAL_BINARIES:
            detail = dependencies.OPTIONAL_BINARY_DETAILS.get(
                binary, dependencies.DEFAULT_OPTIONAL_DETAIL
            )
            specs.append((binary, detail))
        for pip_name in dependencies.PYTHON_PACKAGES.values():
            specs.append((pip_name, "paquete de Python"))

        for name, detail in specs:
            row = DependencyRow(name, detail)
            self.rows[name] = row
            box_layout.addWidget(row)

        root.addWidget(box)

        self.polkit_warning = QLabel("")
        self.polkit_warning.setWordWrap(True)
        self.polkit_warning.setStyleSheet(f"color: {COLORS['yellow']};")
        self.polkit_warning.hide()
        root.addWidget(self.polkit_warning)

        action_bar = QFrame()
        action_bar.setObjectName("actionBar")
        _apply_shadow(action_bar)
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(16, 14, 16, 14)
        actions.setSpacing(10)
        self.check_btn = QPushButton("Volver a verificar")
        self.check_btn.setObjectName("bigAction")
        self.check_btn.clicked.connect(self.on_check)
        self.install_btn = QPushButton("Instalar lo que falta")
        self.install_btn.setObjectName("primary")
        self.install_btn.setStyleSheet("padding: 12px 26px; font-size: 13px; border-radius: 8px;")
        self.install_btn.clicked.connect(self.on_install)
        actions.addWidget(self.check_btn)
        actions.addStretch(1)
        actions.addWidget(self.install_btn)
        root.addWidget(action_bar)

        log_box = QGroupBox("Registro")
        _apply_shadow(log_box)
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(100)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_box)
        root.addStretch(1)

    def on_check(self) -> None:
        self.check_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self._log("Verificando dependencias...")
        self.check_worker = DependencyCheckWorker()
        self.check_worker.done.connect(self._on_check_done)
        self.check_worker.start()

    def _on_check_done(self, report: dependencies.DependencyReport) -> None:
        self.report = report
        self.checked.emit(report)
        self.check_btn.setEnabled(True)
        self._update_rows(report)
        self.install_btn.setEnabled(not report.all_ok)
        if report.all_ok:
            self._log("Todo listo: no falta ninguna dependencia.")
        else:
            faltan = ", ".join(report.missing_binaries + report.missing_python)
            self._log(f"Falta instalar: {faltan}")

        if not report.polkit_agent_running:
            self.polkit_warning.setText(
                "⚠ No se detectó un agente de polkit corriendo. Sin uno, las "
                "ventanas de contraseña no van a aparecer. En Hyprland "
                "instala 'hyprpolkitagent' (o 'polkit-gnome') y arráncalo "
                "junto con tu sesión."
            )
            self.polkit_warning.show()
        else:
            self.polkit_warning.hide()

    def _update_rows(self, report: dependencies.DependencyReport) -> None:
        for binary in dependencies.REQUIRED_BINARIES:
            self.rows[binary].set_status(ok=binary not in report.missing_binaries)
        for binary in dependencies.OPTIONAL_BINARIES:
            missing = binary in report.missing_binaries
            self.rows[binary].set_status(ok=not missing, warn=missing)
        for pip_name in dependencies.PYTHON_PACKAGES.values():
            self.rows[pip_name].set_status(ok=pip_name not in report.missing_python)

    def on_install(self) -> None:
        if not self.report or self.report.all_ok:
            return
        commands = self.report.install_commands
        preview = "\n".join(commands) if commands else "(nada por instalar)"
        confirm = QMessageBox.question(
            self,
            "Instalar dependencias",
            "Se van a ejecutar estos comandos:\n\n"
            f"{preview}\n\n"
            "Los que necesitan root pediran la contraseña en una ventana "
            "gráfica. ¿Continuar?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.install_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.install_worker = DependencyInstallWorker(self.report)
        self.install_worker.log.connect(self._log)
        self.install_worker.finished_ok.connect(self._on_install_done)
        self.install_worker.failed.connect(self._on_install_failed)
        self.install_worker.start()

    def _on_install_done(self) -> None:
        self._log("Instalación completa. Verificando de nuevo...")
        self.on_check()

    def _on_install_failed(self, message: str) -> None:
        self.check_btn.setEnabled(True)
        self.install_btn.setEnabled(True)
        self._log(f"Error al instalar: {message}")
        QMessageBox.warning(self, "Error al instalar", message)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
