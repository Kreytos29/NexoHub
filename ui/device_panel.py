"""Tarjeta principal del celular: estado, selector, telemetría y acciones."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

import adb_tools

from . import icons
from .theme import COLORS, apply_shadow
from .widgets import CollapsibleSection, DiagCard, icon_button, repolish

DIAG_FIELDS = [
    "Conexión", "Celular", "Chipset", "Mejor encoder",
    "Velocidad USB", "USB autosuspend", "Monitor", "CPU governor",
]


class DevicePanel(QFrame):
    detect_clicked = pyqtSignal()
    wifi_clicked = pyqtSignal()
    screenshot_clicked = pyqtSignal()
    restart_adb_clicked = pyqtSignal()
    refresh_clicked = pyqtSignal()
    device_chosen = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("heroCard")
        apply_shadow(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(16)

        # --- fila superior: ícono, nombre, estado -------------------------
        top = QHBoxLayout()
        top.setSpacing(16)
        self.icon_frame = QFrame()
        self.icon_frame.setObjectName("heroIcon")
        self.icon_frame.setProperty("connected", "false")
        self.icon_frame.setFixedSize(58, 58)
        icon_layout = QVBoxLayout(self.icon_frame)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_layout.addWidget(self.icon_label)
        top.addWidget(self.icon_frame, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        self.title_label = QLabel("Sin celular conectado")
        self.title_label.setObjectName("heroTitle")
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("heroSub")
        self.subtitle_label.setWordWrap(True)
        text_col.addWidget(self.title_label)
        text_col.addWidget(self.subtitle_label)

        self.telemetry_row = QHBoxLayout()
        self.telemetry_row.setSpacing(8)
        self.battery_chip, self.battery_text = self._chip("battery")
        self.temp_chip, self.temp_text = self._chip("thermometer")
        self.telemetry_row.addWidget(self.battery_chip)
        self.telemetry_row.addWidget(self.temp_chip)
        self.telemetry_row.addStretch(1)
        self.battery_chip.hide()
        self.temp_chip.hide()
        text_col.addSpacing(4)
        text_col.addLayout(self.telemetry_row)
        top.addLayout(text_col, 1)

        self.link_pill = QLabel("")
        self.link_pill.setProperty("role", "pill")
        self.link_pill.hide()
        top.addWidget(self.link_pill, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        # --- selector de celular ----------------------------------------
        selector = QHBoxLayout()
        selector.setSpacing(8)
        selector.addWidget(QLabel("Celular"))
        self.combo = QComboBox()
        self.combo.setToolTip(
            "Celulares que ve ADB ahora mismo. Si conectas más de uno, elige "
            "con cuál trabajar y pulsa Detectar."
        )
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        selector.addWidget(self.combo, 1)
        self.refresh_btn = icon_button(
            "refresh", tooltip="Actualizar la lista de celulares (se actualiza sola cada pocos segundos)"
        )
        self.refresh_btn.clicked.connect(self.refresh_clicked)
        selector.addWidget(self.refresh_btn)
        root.addLayout(selector)

        # --- acciones -----------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.detect_btn = QPushButton("Detectar")
        self.detect_btn.setObjectName("primary")
        self.detect_btn.setStyleSheet("padding: 12px 26px; font-size: 13px; border-radius: 10px;")
        self.detect_btn.setToolTip("Lee el celular por ADB, la topología USB, el monitor y el CPU.  (F5)")
        self.detect_btn.clicked.connect(self.detect_clicked)
        apply_shadow(self.detect_btn, blur=22, alpha=80)
        actions.addWidget(self.detect_btn)

        self.wifi_btn = icon_button(
            "wifi", "Conectar por WiFi",
            tooltip=(
                "Cambia el celular a modo WiFi/LAN para usar scrcpy sin cable. "
                "El celular y el PC deben estar en la misma red. Primero hay "
                "que detectar por USB al menos una vez."
            ),
        )
        self.wifi_btn.setEnabled(False)
        self.wifi_btn.clicked.connect(self.wifi_clicked)
        actions.addWidget(self.wifi_btn)

        self.screenshot_btn = icon_button(
            "camera", "Captura",
            tooltip="Guarda una captura de pantalla del celular y la copia al portapapeles.  (F12)",
        )
        self.screenshot_btn.setEnabled(False)
        self.screenshot_btn.clicked.connect(self.screenshot_clicked)
        actions.addWidget(self.screenshot_btn)
        actions.addStretch(1)

        self.restart_btn = QPushButton("¿Problemas? Reiniciar ADB")
        self.restart_btn.setObjectName("ghost")
        self.restart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_btn.setToolTip(
            "Mata y vuelve a levantar el servidor adb (adb kill-server + "
            "adb start-server). Úsalo si el celular queda marcado "
            "'unauthorized'/'offline' sin motivo, si cambiaste de celular "
            "y quedó un estado colgado, o si 'Detectar' no refleja lo que "
            "hay conectado ahora mismo."
        )
        self.restart_btn.clicked.connect(self.restart_adb_clicked)
        actions.addWidget(self.restart_btn)
        root.addLayout(actions)

        # --- detalles técnicos ---------------------------------------------
        self.diag_section = CollapsibleSection("monitor", "Detalles técnicos del dispositivo")
        diag_inner = QVBoxLayout(self.diag_section.content)
        diag_inner.setContentsMargins(16, 4, 16, 16)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.diag_cards: dict[str, DiagCard] = {}
        columns = 3
        for i, name in enumerate(DIAG_FIELDS):
            card = DiagCard(name)
            self.diag_cards[name] = card
            grid.addWidget(card, i // columns, i % columns)
        for col in range(columns):
            grid.setColumnStretch(col, 1)
        diag_inner.addLayout(grid)
        root.addWidget(self.diag_section)

        self.resolution_section = PhoneResolutionSection()
        root.addWidget(self.resolution_section)

        self.show_disconnected()

    # ------------------------------------------------------------------
    def _chip(self, icon_name: str) -> tuple[QFrame, QLabel]:
        chip = QFrame()
        chip.setObjectName("telemetryChip")
        chip.setProperty("level", "ok")
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(10, 5, 12, 5)
        layout.setSpacing(7)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap(icon_name, COLORS["subtext"], 15))
        text = QLabel("")
        text.setObjectName("telemetryText")
        layout.addWidget(icon_label)
        layout.addWidget(text)
        return chip, text

    # -- estados --------------------------------------------------------------
    def _set_icon(self, connected: bool) -> None:
        self.icon_frame.setProperty("connected", "true" if connected else "false")
        repolish(self.icon_frame)
        color = COLORS["accent"] if connected else COLORS["subtext"]
        self.icon_label.setPixmap(icons.pixmap("phone", color, 28, 1.7))

    def show_disconnected(self, hint: str | None = None) -> None:
        self._set_icon(False)
        self.title_label.setText("Sin celular conectado")
        self.subtitle_label.setText(
            hint
            or "Conéctalo por USB con la depuración USB activada. Si es la "
            "primera vez, el celular te pedirá aceptar el permiso en pantalla."
        )
        self.link_pill.hide()
        self.battery_chip.hide()
        self.temp_chip.hide()
        self.screenshot_btn.setEnabled(False)
        self.wifi_btn.setEnabled(False)
        self.wifi_btn.setText("Conectar por WiFi")
        for card in self.diag_cards.values():
            card.clear()
        self.resolution_section.set_active(False)
        self.resolution_section.set_status("Detecta el celular para ver su resolución.")

    def show_connected(self, name: str, subtitle: str, wireless: bool) -> None:
        self._set_icon(True)
        self.title_label.setText(name)
        self.subtitle_label.setText(subtitle)
        self.set_connection_mode(wireless)
        self.screenshot_btn.setEnabled(True)
        self.wifi_btn.setEnabled(True)
        self.resolution_section.set_active(True)

    def set_connection_mode(self, wireless: bool) -> None:
        self.link_pill.setText("WiFi" if wireless else "USB")
        self.link_pill.show()
        self.wifi_btn.setText("Volver a USB" if wireless else "Conectar por WiFi")
        self.wifi_btn.setIcon(
            icons.icon("usb" if wireless else "wifi", COLORS["text"], 17, disabled_color=COLORS["surface3"])
        )

    def set_detecting(self, busy: bool) -> None:
        self.detect_btn.setEnabled(not busy)
        self.detect_btn.setText("Detectando…" if busy else "Detectar")

    def set_diagnosis(self, values: dict[str, str]) -> None:
        for name, value in values.items():
            if name in self.diag_cards:
                self.diag_cards[name].set_value(value)

    def set_telemetry(self, info: "adb_tools.BatteryInfo | None") -> None:
        if info is None:
            return
        if info.level is not None:
            bolt = "  ⚡" if info.is_charging else ""
            self.battery_text.setText(f"{info.level}%{bolt}")
            level = "warn" if info.level <= 15 and not info.is_charging else "ok"
            self.battery_chip.setProperty("level", level)
            repolish(self.battery_chip)
            self.battery_chip.show()
        if info.temperature_c is not None:
            self.temp_text.setText(f"{info.temperature_c:.1f} °C")
            if info.is_hot:
                level = "hot"
            elif info.temperature_c >= 38:
                level = "warn"
            else:
                level = "ok"
            self.temp_chip.setProperty("level", level)
            repolish(self.temp_chip)
            self.temp_chip.show()

    # -- selector -------------------------------------------------------------
    def set_devices(self, entries: list, keep_serial: str | None = None) -> None:
        """Actualiza el combo sin disparar 'device_chosen' ni perder la selección."""
        current = keep_serial or self.selected_serial()
        blocked = self.combo.blockSignals(True)
        self.combo.clear()
        if not entries:
            self.combo.addItem("Sin celulares conectados", None)
            self.combo.model().item(0).setEnabled(False)
        for entry in entries:
            self.combo.addItem(entry.label, entry.serial)
        index = self.combo.findData(current) if current else -1
        if index >= 0:
            self.combo.setCurrentIndex(index)
        elif entries:
            ready = next((i for i, e in enumerate(entries) if e.is_ready), 0)
            self.combo.setCurrentIndex(ready)
        self.combo.blockSignals(blocked)

    def selected_serial(self) -> str | None:
        return self.combo.currentData()

    def select_serial(self, serial: str) -> None:
        index = self.combo.findData(serial)
        if index >= 0:
            blocked = self.combo.blockSignals(True)
            self.combo.setCurrentIndex(index)
            self.combo.blockSignals(blocked)

    def _on_combo_changed(self, _index: int) -> None:
        serial = self.combo.currentData()
        if serial:
            self.device_chosen.emit(serial)


class PhoneResolutionSection(CollapsibleSection):
    """Cambia la resolución REAL de la pantalla del celular (adb shell wm size)."""

    use_monitor_clicked = pyqtSignal()
    refresh_clicked = pyqtSignal()
    apply_clicked = pyqtSignal(int, int)
    reset_clicked = pyqtSignal()

    def __init__(self):
        super().__init__("phone", "Resolución del celular (ADB)")
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(17, 4, 17, 17)
        layout.setSpacing(12)

        hint = QLabel(
            "Esto cambia la resolución REAL de la pantalla del celular (vía "
            "ADB), no la del video en la sección Video. Sirve para igualar el "
            "celular a tu monitor (ej. monitor 1920x1080 → celular 1080x1920) "
            "para que el juego ya renderice al tamaño final. Algunos juegos y "
            "lanzadores pueden verse raros con una resolución no nativa; "
            "\"Restablecer\" vuelve a la de fábrica en cualquier momento."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        self.status_label = QLabel("Detecta el celular para ver su resolución.")
        self.status_label.setProperty("role", "hint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_row.addWidget(QLabel("Ancho"))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(240, 7680)
        self.width_spin.setValue(1080)
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×"))
        self.height_spin = QSpinBox()
        self.height_spin.setRange(240, 7680)
        self.height_spin.setValue(1920)
        size_row.addWidget(self.height_spin)
        size_row.addStretch(1)
        layout.addLayout(size_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.use_monitor_btn = QPushButton("Usar la de mi monitor")
        self.use_monitor_btn.setToolTip(
            "Completa Ancho/Alto invirtiendo la resolución del monitor "
            "detectado (monitor horizontal → celular vertical). No aplica "
            "nada todavía, solo llena los campos."
        )
        self.use_monitor_btn.clicked.connect(self.use_monitor_clicked)
        self.refresh_btn = QPushButton("Leer actual")
        self.refresh_btn.setToolTip("Vuelve a leer la resolución actual del celular por ADB.")
        self.refresh_btn.clicked.connect(self.refresh_clicked)
        self.reset_btn = QPushButton("Restablecer a nativa")
        self.reset_btn.setToolTip("Corre 'adb shell wm size reset': vuelve a la resolución de fábrica.")
        self.reset_btn.clicked.connect(self.reset_clicked)
        self.apply_btn = QPushButton("Aplicar")
        self.apply_btn.setToolTip("Corre 'adb shell wm size ANCHOxALTO' en el celular.")
        self.apply_btn.clicked.connect(lambda: self.apply_clicked.emit(self.width_spin.value(), self.height_spin.value()))
        btn_row.addWidget(self.use_monitor_btn)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.reset_btn)
        btn_row.addWidget(self.apply_btn)
        layout.addLayout(btn_row)
        self.set_active(False)

    def _buttons(self) -> tuple[QPushButton, ...]:
        return (self.use_monitor_btn, self.apply_btn, self.reset_btn, self.refresh_btn)

    def set_active(self, active: bool) -> None:
        for btn in self._buttons():
            btn.setEnabled(active)

    def set_busy(self, busy: bool) -> None:
        for btn in self._buttons():
            btn.setEnabled(not busy)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_size(self, width: int, height: int) -> None:
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
