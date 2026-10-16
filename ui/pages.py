"""
Contenido de cada sección (acordeón) de la pantalla de configuración.

Cada página es un widget independiente que expone sus controles como
``fields``: un mapa clave-de-config -> Field, así el controlador
(config_page.py) lee y escribe el formulario completo con un solo bucle en
vez de tocar widget por widget.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from detectors import wireless

from .theme import COLORS
from .widgets import divider, help_button, icon_button, section_title

# --------------------------------------------------------------------------
# Textos de ayuda largos
# --------------------------------------------------------------------------
INPUT_MODE_HELP = (
    "Cómo se manda tu teclado/mouse del PC al celular:\n\n"
    "• sdk (default de scrcpy): inyección estándar vía ADB. La más "
    "compatible, pero varios juegos competitivos la detectan como "
    "input sintético y la bloquean.\n\n"
    "• uhid (recomendado): emula un teclado/mouse real a nivel de "
    "kernel de Android. La mayoría de los anti-cheat no lo distinguen "
    "de hardware real. Buen equilibrio compatibilidad/estabilidad.\n\n"
    "• aoa (Android Open Accessory): emula el protocolo USB real, aún "
    "más difícil de detectar que uhid, pero más exigente con el cable "
    "y puede ser menos estable en algunos setups.\n\n"
    "• disabled: solo transmite pantalla, sin mandar ningún input "
    "(agrega --no-control). Útil para solo mirar o grabar. Con este modo "
    "se desactivan las opciones que necesitan control (apagar pantalla, "
    "mostrar toques, mantener despierto, gamepad)."
)

ENCODER_HELP = (
    "El nombre exacto del encoder de video de hardware (ej. "
    "c2.mtk.avc.encoder, c2.qti.avc.encoder...) depende del chipset de "
    "CADA celular. Déjalo vacío para que siempre se detecte automático "
    "el mejor disponible en el celular conectado: forzarlo a mano solo "
    "tiene sentido si sabes el nombre exacto de tu chipset y quieres uno "
    "específico."
)

KEYMAP_HELP = (
    "Convierte teclado y mouse en toques sobre la pantalla del celular, como "
    "BlueStacks o Panda Mouse, pero sin instalar nada en el celular.\n\n"
    "Teclas (todas se cambian en el editor, también por botones del mouse):\n"
    "• F9: activa y desactiva el mapeo (modo juego). En modo juego el teclado y "
    "el mouse quedan para el juego; las teclas que el mapa no usa (Esc, Enter, "
    "Alt+Tab…) siguen llegando al PC.\n"
    "• F8: pausa y reanuda sin desactivar (por ejemplo, para escribir en el chat).\n"
    "• Alt: muestra el cursor para tocar menús; otra vez Alt vuelve a la cámara.\n"
    "• F10: abre el editor del mapa, aunque estés jugando; al guardar se aplica "
    "al instante.\n"
    "• F7: muestra u oculta las teclas dibujadas sobre el juego.\n\n"
    "Las posiciones vienen aproximadas para el HUD de fábrica: ajústalas con "
    "«Editar mapa…» sobre una captura del juego.\n\n"
    "Requiere estar en el grupo «input» (para leer el teclado y el mouse) y "
    "el paquete de Python «evdev». Mientras está activo, el mouse de la "
    "ventana de scrcpy usa el modo sdk para que los clics sean toques."
)

GAMEPAD_HELP = (
    "Reenvía un mando conectado al PC como gamepad del celular "
    "(--gamepad de scrcpy):\n\n"
    "• Desactivado: no se envía nada.\n\n"
    "• uhid (recomendado): simula un mando HID real usando el módulo HID "
    "del kernel de Android. Funciona por USB y por WiFi.\n\n"
    "• aoa: simula el mando con el protocolo USB Accessory v2. Puede "
    "funcionar solo por cable USB.\n\n"
    "Sirve para juegos que ya traen soporte de mando; no convierte "
    "teclas en toques de pantalla."
)


# --------------------------------------------------------------------------
# Campos del formulario
# --------------------------------------------------------------------------
@dataclass
class Field:
    """Un control del formulario ligado a una clave de config.

    kind: check | text | spin | combo_text | combo_data | flags
    default: valor que muestra el control cuando la config no dice nada.
    none_value: valor del control que significa "sin definir" (se guarda
        como None y el programa usa su detección automática).
    """

    key: str
    widget: QWidget
    kind: str
    default: Any = None
    none_value: Any = None

    # -- lectura -----------------------------------------------------------
    def read(self) -> Any:
        widget = self.widget
        if self.kind == "check":
            value: Any = widget.isChecked()
        elif self.kind in ("text", "flags"):
            value = widget.text().strip()
        elif self.kind == "spin":
            value = widget.value()
        elif self.kind == "combo_text":
            value = widget.currentText()
        elif self.kind == "combo_data":
            value = widget.currentData()
        else:  # pragma: no cover
            raise ValueError(f"kind desconocido: {self.kind}")
        if self.kind == "flags":
            return split_flags(value)
        if self.none_value is not None and value == self.none_value:
            return None
        return value

    # -- escritura (sin disparar señales: el llamador decide qué refrescar) --
    def write(self, value: Any) -> None:
        if value is None:
            value = self.none_value if self.none_value is not None else self.default
        widget = self.widget
        blocked = widget.blockSignals(True)
        try:
            if self.kind == "check":
                widget.setChecked(bool(value))
            elif self.kind == "text":
                widget.setText("" if value is None else str(value))
            elif self.kind == "flags":
                widget.setText(join_flags(value))
            elif self.kind == "spin":
                try:
                    widget.setValue(int(value))
                except (TypeError, ValueError):
                    widget.setValue(int(self.default or 0))
            elif self.kind == "combo_text":
                index = widget.findText(str(value))
                widget.setCurrentIndex(index if index >= 0 else max(0, widget.findText(str(self.default))))
            elif self.kind == "combo_data":
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else max(0, widget.findData(self.default)))
        finally:
            widget.blockSignals(blocked)

    def connect_edited(self, slot) -> None:
        widget = self.widget
        if self.kind == "check":
            widget.toggled.connect(slot)
        elif self.kind in ("text", "flags"):
            widget.textChanged.connect(slot)
        elif self.kind == "spin":
            widget.valueChanged.connect(slot)
        else:
            widget.currentIndexChanged.connect(slot)


def split_flags(text: str) -> list[str]:
    """Separa flags como lo haría una terminal (respeta comillas)."""
    import shlex

    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def join_flags(flags) -> str:
    import shlex

    if isinstance(flags, str):
        return flags
    return shlex.join([str(f) for f in (flags or [])])


class FieldPage(QWidget):
    """Base de las páginas: arma un layout vertical y registra campos."""

    edited = pyqtSignal(str)  # clave de config del campo que cambió

    def __init__(self, spacing: int = 14):
        super().__init__()
        self.fields: dict[str, Field] = {}
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(spacing)

    def add_field(self, key: str, widget: QWidget, kind: str, default: Any = None, none_value: Any = None) -> Field:
        field = Field(key, widget, kind, default, none_value)
        self.fields[key] = field
        field.connect_edited(lambda *_, k=key: self.edited.emit(k))
        return field

    def hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "hint")
        label.setWordWrap(True)
        self.layout_.addWidget(label)
        return label

    @staticmethod
    def form() -> tuple[QWidget, QFormLayout]:
        box = QWidget()
        form = QFormLayout(box)
        form.setContentsMargins(0, 2, 0, 0)
        form.setSpacing(10)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return box, form

    @staticmethod
    def labeled_help(text: str, title: str, help_text: str) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel(text))
        row.addWidget(help_button(title, help_text, container))
        return container


def _combo(items: list[tuple[str, Any]]) -> QComboBox:
    combo = QComboBox()
    for label, data in items:
        combo.addItem(label, data)
    return combo


# --------------------------------------------------------------------------
# Video
# --------------------------------------------------------------------------
class VideoPage(FieldPage):
    match_monitor_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.hint(
            "El perfil de uso ya deja esto en buenos valores por defecto: "
            "tócalo solo si sabes lo que estás cambiando."
        )
        self.layout_.addWidget(section_title("Resolución y codec"))
        box, form = self.form()

        codec = QComboBox()
        codec.addItems(["auto", "h264", "h265", "av1"])
        codec.setToolTip(
            "Formato de compresión de video. 'auto' deja que se elija el "
            "que mejor soporte tenga en el celular. h264 suele dar menor "
            "latencia; h265/av1 mejor calidad por bit."
        )
        self.add_field("video_codec", codec, "combo_text", "auto", "auto")
        form.addRow("Video codec", codec)

        encoder = QLineEdit()
        encoder.setPlaceholderText("vacío = detectar el mejor (recomendado)")
        encoder.setToolTip(ENCODER_HELP)
        self.add_field("video_encoder", encoder, "text", "", "")
        form.addRow(self.labeled_help("Video encoder", "Video encoder", ENCODER_HELP), encoder)

        bitrate = QLineEdit()
        bitrate.setPlaceholderText("ej. 8M, 16M, 32M (vacío = auto)")
        bitrate.setToolTip(
            "Bitrate máximo de video. Más alto = mejor calidad pero más "
            "ancho de banda USB. Si lo dejas vacío se ajusta según la "
            "velocidad USB detectada."
        )
        self.add_field("bitrate_max", bitrate, "text", "", "")
        form.addRow("Bitrate máx.", bitrate)

        max_size = QSpinBox()
        max_size.setRange(0, 4096)
        max_size.setSpecialValueText("auto")
        max_size.setSuffix(" px")
        max_size.setToolTip(
            "Lado más largo del video en píxeles. 'auto' ya usa la "
            "resolución de tu monitor destino: este campo es para "
            "ajustarla a gusto (por ej. bajarla un poco para ganar FPS)."
        )
        self.add_field("max_size", max_size, "spin", 0, 0)
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_row.addWidget(max_size)
        self.match_monitor_btn = QPushButton("Usar la de mi monitor")
        self.match_monitor_btn.setToolTip(
            "Pone el tamaño máximo en el lado más largo de tu monitor "
            "principal, para que la transmisión llene toda la pantalla sin "
            "recortes ni franjas negras en pantalla completa."
        )
        self.match_monitor_btn.clicked.connect(self.match_monitor_clicked)
        size_row.addWidget(self.match_monitor_btn)
        size_row.addStretch(1)
        form.addRow("Tamaño máx.", size_row)

        buffer_spin = QSpinBox()
        buffer_spin.setRange(0, 500)
        buffer_spin.setSuffix(" ms")
        buffer_spin.setToolTip(
            "Retardo extra antes de mostrar cada cuadro (--video-buffer). 0 = "
            "mínima latencia. Súbelo de a poco (20-50 ms) solo si por WiFi se "
            "ven tirones: suaviza la imagen a cambio de más delay."
        )
        self.add_field("video_buffer", buffer_spin, "spin", 0)
        form.addRow("Buffer de video", buffer_spin)
        self.layout_.addWidget(box)
        self.layout_.addWidget(divider())

        self.layout_.addWidget(section_title("FPS y overlay"))
        fps_box, fps_form = self.form()
        max_fps = QSpinBox()
        max_fps.setRange(0, 240)
        max_fps.setSpecialValueText("sin límite")
        max_fps.setToolTip(
            "Limita el frame rate de captura en el celular (--max-fps). "
            "Sin límite usa lo que entregue la pantalla."
        )
        self.add_field("max_fps", max_fps, "spin", 0)
        fps_form.addRow("FPS máximo", max_fps)

        overlay_row = QHBoxLayout()
        overlay_row.setSpacing(10)
        overlay = QCheckBox("Mostrar overlay de FPS en pantalla")
        overlay.setToolTip(
            "Abre una ventanita flotante, siempre encima de la transmisión, "
            "con el FPS real en vivo. Se cierra sola al detener o cerrar scrcpy."
        )
        self.add_field("fps_overlay", overlay, "check", True)
        position = _combo(
            [
                ("Abajo a la izquierda", "bottom-left"),
                ("Abajo a la derecha", "bottom-right"),
                ("Arriba a la izquierda", "top-left"),
                ("Arriba a la derecha", "top-right"),
            ]
        )
        position.setToolTip("En qué esquina de la pantalla aparece el overlay de FPS.")
        self.add_field("fps_overlay_position", position, "combo_data", "bottom-left")
        overlay_row.addWidget(overlay)
        overlay_row.addWidget(position)
        overlay_row.addStretch(1)
        fps_form.addRow("", overlay_row)
        self.layout_.addWidget(fps_box)


# --------------------------------------------------------------------------
# Ventana y pantalla del celular
# --------------------------------------------------------------------------
class WindowPage(FieldPage):
    def __init__(self):
        super().__init__()
        self.hint(
            "Cómo se comporta la ventana de scrcpy en tu PC y qué hace el "
            "celular mientras transmite."
        )
        self.layout_.addWidget(section_title("Ventana de scrcpy"))
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 2, 0, 0)
        col.setSpacing(9)

        fullscreen = QCheckBox("Iniciar en pantalla completa")
        fullscreen.setToolTip(
            "Abre la ventana ya a pantalla completa (--fullscreen) en el "
            "monitor detectado. Combínalo con 'Usar la de mi monitor' en "
            "Video para que se vea nítido."
        )
        self.add_field("fullscreen", fullscreen, "check", False)
        on_top = QCheckBox("Siempre encima de otras ventanas")
        on_top.setToolTip("--always-on-top: la ventana de scrcpy no queda tapada por otras.")
        self.add_field("always_on_top", on_top, "check", False)
        borderless = QCheckBox("Sin bordes ni barra de título")
        borderless.setToolTip("--window-borderless: oculta las decoraciones de la ventana.")
        self.add_field("borderless", borderless, "check", False)
        for check in (fullscreen, on_top, borderless):
            col.addWidget(check)
        self.layout_.addWidget(box)

        form_box, form = self.form()
        title = QLineEdit()
        title.setPlaceholderText("vacío = el nombre del celular")
        title.setToolTip(
            "Título de la ventana de scrcpy (--window-title). Útil para que "
            "OBS u otro programa la encuentre siempre por el mismo nombre."
        )
        self.add_field("window_title", title, "text", "", "")
        form.addRow("Título de la ventana", title)

        orientation = _combo(
            [
                ("Automática (sigue al celular)", "auto"),
                ("Bloquear a la actual", "@"),
                ("Girar 0°", "0"),
                ("Girar 90°", "90"),
                ("Girar 180°", "180"),
                ("Girar 270°", "270"),
            ]
        )
        orientation.setToolTip(
            "Orientación del video capturado (--capture-orientation). "
            "'Bloquear a la actual' mantiene la orientación que tiene el "
            "celular ahora aunque el juego rote la pantalla."
        )
        self.add_field("capture_orientation", orientation, "combo_data", "auto", "auto")
        form.addRow("Orientación", orientation)
        self.layout_.addWidget(form_box)
        self.layout_.addWidget(divider())

        self.layout_.addWidget(section_title("Pantalla del celular"))
        phone_box = QWidget()
        phone_col = QVBoxLayout(phone_box)
        phone_col.setContentsMargins(0, 2, 0, 0)
        phone_col.setSpacing(9)
        stay = QCheckBox("Mantener el celular despierto")
        stay.setToolTip("--stay-awake: evita que el celular bloquee la pantalla mientras transmites.")
        self.add_field("stay_awake", stay, "check", True)
        screen_off = QCheckBox("Apagar la pantalla del celular al transmitir")
        screen_off.setToolTip(
            "--turn-screen-off: la pantalla física del celular se apaga pero "
            "la transmisión y el control siguen. Ahorra batería y calor en "
            "sesiones largas."
        )
        self.add_field("turn_screen_off", screen_off, "check", False)
        touches = QCheckBox("Mostrar los toques en la pantalla")
        touches.setToolTip(
            "--show-touches: muestra un punto donde se toca la pantalla del "
            "celular (solo toques físicos, no los clics desde el PC)."
        )
        self.add_field("show_touches", touches, "check", False)
        power_off = QCheckBox("Apagar la pantalla al cerrar scrcpy")
        power_off.setToolTip("--power-off-on-close: al terminar la transmisión, el celular apaga su pantalla.")
        self.add_field("power_off_on_close", power_off, "check", False)
        for check in (stay, screen_off, touches, power_off):
            phone_col.addWidget(check)
        self.layout_.addWidget(phone_box)
        self.hint(
            "Estas opciones necesitan control activo: con el modo de entrada "
            "'disabled' se omiten."
        )


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
class AudioPage(FieldPage):
    route_changed = pyqtSignal()
    bluetooth_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Ruta del audio"))
        self.route_combo = _combo(
            [
                ("Apagado (sin audio)", "off"),
                ("Por USB, junto con el video", "scrcpy"),
                ("Bluetooth (externo, recomendado)", "bluetooth"),
                ("WiFi en paralelo (dual, menor lag)", "wifi_dual"),
            ]
        )
        self.route_combo.setToolTip(
            "USB: el audio comparte el mismo túnel que video/control (más "
            "simple, puede sumar lag en USB 2.0). Bluetooth: el celular le "
            "manda el audio al PC directo por Bluetooth, sin tocar scrcpy "
            "(cero impacto en video/input, pero ~100-150 ms de desfase "
            "típico de A2DP). WiFi (dual): una segunda instancia de scrcpy, "
            "solo audio, corre en paralelo por WiFi mientras video/control "
            "siguen por USB; necesita Android 11+."
        )
        self.add_field("audio_route", self.route_combo, "combo_data", "off")
        row.addWidget(self.route_combo, 1)
        self.bt_btn = icon_button(
            "volume", "Bluetooth",
            tooltip=(
                "Revisa si el PC tiene Bluetooth y qué dispositivos de audio "
                "ya tiene emparejados/conectados. El emparejamiento en sí se "
                "hace desde el applet de Bluetooth del sistema, no desde aquí."
            ),
        )
        self.bt_btn.clicked.connect(self.bluetooth_clicked)
        row.addWidget(self.bt_btn)
        self.layout_.addLayout(row)
        self.layout_.addWidget(divider())

        self.layout_.addWidget(section_title("Ajustes finos"))
        box, form = self.form()
        codec = QComboBox()
        codec.addItems(["auto", "opus", "aac", "flac", "raw"])
        codec.setToolTip(
            "Codec de audio de scrcpy (default: opus). En varios chipsets "
            "'aac' tiene encoder de hardware, mientras que opus suele ser "
            "software: si el audio mete lag en competitivo, prueba aac primero."
        )
        self.add_field("audio_codec", codec, "combo_text", "auto", "auto")
        form.addRow("Audio codec", codec)

        bitrate = QLineEdit()
        bitrate.setPlaceholderText("ej. 64K, 128K (vacío = 128K)")
        bitrate.setToolTip(
            "Bitrate de audio. Bajarlo (ej. 64K) reduce lo que compite con "
            "video+control en el mismo túnel USB/ADB."
        )
        self.add_field("audio_bitrate", bitrate, "text", "", "")
        form.addRow("Audio bitrate", bitrate)

        buffer_spin = QSpinBox()
        buffer_spin.setRange(0, 500)
        buffer_spin.setValue(50)
        buffer_spin.setSuffix(" ms")
        buffer_spin.setToolTip(
            "Buffer de audio (default de scrcpy: 50 ms). Bajarlo reduce el "
            "delay de audio, pero si baja demasiado pueden aparecer cortes o "
            "\"glitches\". Ve probando de a poco (50 → 30 → 15)."
        )
        self.add_field("audio_buffer", buffer_spin, "spin", 50)
        form.addRow("Audio buffer", buffer_spin)
        self._fine_widgets = (codec, bitrate, buffer_spin)
        self.layout_.addWidget(box)

        self.route_combo.currentIndexChanged.connect(lambda *_: self.route_changed.emit())

    def route(self) -> str:
        return self.route_combo.currentData()

    def set_route(self, route: str) -> None:
        blocked = self.route_combo.blockSignals(True)
        index = self.route_combo.findData(route)
        if index >= 0:
            self.route_combo.setCurrentIndex(index)
        self.route_combo.blockSignals(blocked)
        self.update_fine_enabled(route)

    def update_fine_enabled(self, route: str) -> None:
        """Los ajustes finos solo aplican si hay audio de scrcpy corriendo en
        algún lado: en el canal principal ('scrcpy') o en el sidecar WiFi
        ('wifi_dual'). Con 'off' o 'bluetooth' no hay ningún --audio-* que afinar."""
        enabled = route in ("scrcpy", "wifi_dual")
        for widget in self._fine_widgets:
            widget.setEnabled(enabled)


# --------------------------------------------------------------------------
# Entrada: teclado, mouse, gamepad
# --------------------------------------------------------------------------
class InputPage(FieldPage):
    edit_keymap_clicked = pyqtSignal(str)
    new_keymap_clicked = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.layout_.addWidget(section_title("Teclado, mouse y mando"))
        box, form = self.form()

        mode = QComboBox()
        mode.addItems(["uhid", "sdk", "aoa", "disabled"])
        mode.setToolTip(INPUT_MODE_HELP)
        self.add_field("input_mode", mode, "combo_text", "uhid")
        form.addRow(self.labeled_help("Modo de entrada", "Modo de entrada", INPUT_MODE_HELP), mode)

        gamepad = _combo(
            [
                ("Desactivado", "disabled"),
                ("uhid (recomendado)", "uhid"),
                ("aoa (solo USB)", "aoa"),
            ]
        )
        gamepad.setToolTip(GAMEPAD_HELP)
        self.add_field("gamepad", gamepad, "combo_data", "disabled", "disabled")
        form.addRow(self.labeled_help("Gamepad", "Gamepad", GAMEPAD_HELP), gamepad)
        self.layout_.addWidget(box)
        self.layout_.addWidget(divider())

        self.layout_.addWidget(section_title("Mapeo de teclas a toques"))
        self.hint(
            "Juega con teclado y mouse títulos pensados para pantalla táctil, como "
            "en los emuladores: cada tecla toca un botón del juego, WASD mueve el "
            "joystick y el mouse mueve la cámara. No instala nada en el celular."
        )
        box, form = self.form()
        enabled = QCheckBox("Activar al lanzar scrcpy")
        enabled.setToolTip(KEYMAP_HELP)
        self.add_field("keymap_enabled", enabled, "check", False)
        form.addRow(self.labeled_help("Mapeo de teclas", "Mapeo de teclas", KEYMAP_HELP), enabled)

        map_row = QWidget()
        map_layout = QHBoxLayout(map_row)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(8)
        self.keymap_combo = QComboBox()
        self.reload_keymaps()
        self.add_field("keymap", self.keymap_combo, "combo_data", "freefire")
        map_layout.addWidget(self.keymap_combo, 1)
        edit_btn = QPushButton("Editar mapa…")
        edit_btn.setObjectName("ghost")
        edit_btn.setToolTip("Abre el editor visual: mueve cada control sobre una captura del juego.")
        edit_btn.clicked.connect(lambda: self.edit_keymap_clicked.emit(self.keymap_combo.currentData() or ""))
        map_layout.addWidget(edit_btn)
        new_btn = QPushButton("Nuevo…")
        new_btn.setObjectName("ghost")
        new_btn.setToolTip("Crea un mapa nuevo para otro juego, a partir del elegido.")
        new_btn.clicked.connect(lambda: self.new_keymap_clicked.emit(self.keymap_combo.currentData() or ""))
        map_layout.addWidget(new_btn)
        form.addRow("Mapa", map_row)

        overlay = QCheckBox("Mostrar las teclas sobre el juego")
        overlay.setToolTip(
            "Dibuja cada tecla encima de su botón, sobre la ventana de scrcpy (como "
            "BlueStacks). Se oculta y se muestra en el juego con su tecla (F7). "
            "Necesita gtk-layer-shell y, por ahora, Hyprland."
        )
        self.add_field("keymap_overlay", overlay, "check", True)
        form.addRow("", overlay)

        self.keymap_state = QLabel("Desactivado")
        self.keymap_state.setProperty("role", "field-hint")
        form.addRow("Estado", self.keymap_state)
        self.layout_.addWidget(box)

    def reload_keymaps(self, select: str | None = None) -> None:
        """Rellena el combo con los mapas disponibles (de fábrica y propios)."""
        from keymap import model  # noqa: PLC0415 - evita cargar el módulo al importar la UI

        current = select or self.keymap_combo.currentData()
        blocked = self.keymap_combo.blockSignals(True)
        self.keymap_combo.clear()
        for name in model.available():
            try:
                label = model.load(name).display_name
            except model.KeymapError:
                label = f"{name} (con errores)"
            self.keymap_combo.addItem(label, name)
        index = self.keymap_combo.findData(current)
        self.keymap_combo.setCurrentIndex(max(index, 0))
        self.keymap_combo.blockSignals(blocked)
        if select is not None and index >= 0:
            self.edited.emit("keymap")

    def set_keymap_state(self, text: str, color: str | None = None) -> None:
        self.keymap_state.setText(text)
        self.keymap_state.setStyleSheet(f"color: {color};" if color else "")


# --------------------------------------------------------------------------
# Grabación y capturas
# --------------------------------------------------------------------------
class RecordingPage(FieldPage):
    open_recordings_clicked = pyqtSignal()
    open_captures_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.hint(
            "Activa el botón \"Grabar\" de la barra inferior para guardar la "
            "sesión en un archivo. Las capturas de pantalla se sacan con el "
            "botón de cámara en la tarjeta del celular (o con F12)."
        )
        self.layout_.addWidget(section_title("Grabación"))
        box, form = self.form()
        fmt = QComboBox()
        fmt.addItems(["mkv", "mp4"])
        fmt.setToolTip(
            "mkv es más seguro: si algo se corta a mitad de la grabación, el "
            "archivo se puede reproducir igual. mp4 es más compatible con "
            "editores, pero solo queda bien si scrcpy cierra con normalidad."
        )
        self.add_field("record_format", fmt, "combo_text", "mkv")
        form.addRow("Formato", fmt)
        self.recordings_edit = self._folder_row(
            form, "Carpeta de grabaciones", "recordings_dir", "~/Videos/NexoHub", self.open_recordings_clicked
        )
        self.layout_.addWidget(box)
        self.layout_.addWidget(divider())
        self.layout_.addWidget(section_title("Capturas de pantalla"))
        box2, form2 = self.form()
        self.captures_edit = self._folder_row(
            form2, "Carpeta de capturas", "captures_dir", "~/Pictures/NexoHub", self.open_captures_clicked
        )
        self.layout_.addWidget(box2)
        self.hint(
            "Nota: la grabación incluye audio solo con la ruta \"Por USB, junto "
            "con el video\". Las apps que bloquean capturas (banca, DRM) salen en negro."
        )

    def _folder_row(self, form: QFormLayout, label: str, key: str, placeholder: str, open_signal) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(f"vacío = {placeholder}")
        self.add_field(key, edit, "text", "", "")
        browse = icon_button("folder", "Elegir…", tooltip="Elegir otra carpeta")
        browse.clicked.connect(lambda: self._browse(edit))
        open_btn = icon_button("folder", "Abrir", tooltip="Abrir la carpeta en el explorador de archivos")
        open_btn.clicked.connect(open_signal)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        row.addWidget(open_btn)
        form.addRow(label, row)
        return edit

    def _browse(self, edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Elegir carpeta", edit.text() or "")
        if chosen:
            edit.setText(chosen)


# --------------------------------------------------------------------------
# Avanzado
# --------------------------------------------------------------------------
class AdvancedPage(FieldPage):
    reset_clicked = pyqtSignal()
    open_config_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.hint("Conexión, flags manuales de scrcpy y ajustes del sistema que casi nunca hace falta tocar.")
        self.layout_.addWidget(section_title("Conexión y flags"))
        box, form = self.form()

        port = QSpinBox()
        port.setRange(1024, 65535)
        port.setValue(wireless.DEFAULT_PORT)
        port.setToolTip(
            "Puerto TCP que usa ADB para la conexión WiFi/LAN. El default "
            "(5555) funciona en casi todos los casos; cámbialo solo si ese "
            "puerto está ocupado o bloqueado en tu red."
        )
        self.add_field("wifi_port", port, "spin", wireless.DEFAULT_PORT)
        form.addRow("Puerto WiFi (ADB)", port)

        flags = QLineEdit()
        flags.setPlaceholderText("flags extra de scrcpy, ej: --video-codec-options=i-frame-interval=1")
        flags.setToolTip(
            "Cualquier flag adicional de scrcpy separado por espacios, tal "
            "cual se le pasaría en la terminal (se respetan las comillas)."
        )
        self.add_field("extra_flags", flags, "flags", [])
        form.addRow("Flags extra", flags)

        auto_detect = QCheckBox("Detectar el celular automáticamente al enchufarlo")
        auto_detect.setToolTip(
            "Si hay exactamente un celular conectado y todavía no se detectó "
            "nada, NexoHub corre 'Detectar' solo."
        )
        self.add_field("auto_detect", auto_detect, "check", True)
        form.addRow("", auto_detect)
        self.layout_.addWidget(box)
        self.layout_.addWidget(divider())

        self.layout_.addWidget(section_title("Ajustes del sistema"))
        fixes = QWidget()
        col = QVBoxLayout(fixes)
        col.setContentsMargins(0, 2, 0, 0)
        col.setSpacing(8)
        usb = QCheckBox("Forzar USB power a 'on'")
        usb.setToolTip(
            "Evita que el sistema suspenda el puerto USB del celular "
            "(autosuspend), lo que puede causar desconexiones o caídas de FPS. "
            "Pide contraseña gráfica al lanzar (una sola vez, aunque haya "
            "varios hubs USB de por medio)."
        )
        self.add_field("auto_fix_usb_power", usb, "check", False)
        cpu = QCheckBox("Forzar CPU 'performance'")
        cpu.setToolTip(
            "Cambia el governor de CPU del PC a 'performance' para evitar "
            "bajones de rendimiento por ahorro de energía. Si también activaste "
            "el fix de USB, se pide todo con una sola contraseña."
        )
        self.add_field("auto_fix_cpu_governor", cpu, "check", False)
        col.addWidget(usb)
        col.addWidget(cpu)
        self.layout_.addWidget(fixes)
        self.layout_.addWidget(divider())

        self.layout_.addWidget(section_title("Datos de la app"))
        row = QHBoxLayout()
        row.setSpacing(10)
        open_btn = icon_button("folder", "Abrir carpeta de ajustes", tooltip="Abre la carpeta donde NexoHub guarda tus ajustes y el registro.")
        open_btn.clicked.connect(self.open_config_clicked)
        reset_btn = icon_button(
            "refresh", "Restablecer ajustes", color=COLORS["red"],
            tooltip="Borra tus ajustes y perfiles propios y vuelve a los valores de fábrica.",
        )
        reset_btn.clicked.connect(self.reset_clicked)
        row.addWidget(open_btn)
        row.addWidget(reset_btn)
        row.addStretch(1)
        self.layout_.addLayout(row)


# --------------------------------------------------------------------------
# Comando y registro
# --------------------------------------------------------------------------
class LogPage(QWidget):
    toast = pyqtSignal(str, str)  # mensaje, tipo

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        hint = QLabel("Comando armado y salida en vivo de scrcpy.")
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addStretch(1)
        copy_cmd = icon_button("copy", "Copiar comando", tooltip="Copia el comando de scrcpy listo para pegar en una terminal.")
        copy_cmd.clicked.connect(self.copy_command)
        copy_log = icon_button("copy", "Copiar registro")
        copy_log.clicked.connect(self.copy_log)
        save_log = icon_button("save", "Guardar…", tooltip="Guarda el registro en un archivo de texto.")
        save_log.clicked.connect(self.save_log)
        clear = icon_button("trash", "Limpiar")
        clear.clicked.connect(self.clear_log)
        for btn in (copy_cmd, copy_log, save_log, clear):
            toolbar.addWidget(btn)
        layout.addLayout(toolbar)

        terminal = QFrame()
        terminal.setObjectName("terminal")
        terminal_layout = QVBoxLayout(terminal)
        terminal_layout.setContentsMargins(16, 14, 16, 16)
        terminal_layout.setSpacing(10)

        self.command_view = QPlainTextEdit()
        self.command_view.setObjectName("terminalView")
        self.command_view.setReadOnly(True)
        self.command_view.setMaximumHeight(72)
        self.command_view.setPlaceholderText(
            "$ pulsa \"Generar comando\" o \"Lanzar scrcpy\" para verlo aquí..."
        )
        terminal_layout.addWidget(self.command_view)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("terminalView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setMinimumHeight(180)
        terminal_layout.addWidget(self.log_view)
        layout.addWidget(terminal, 1)

    def append(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")

    def set_command(self, text: str) -> None:
        self.command_view.setPlainText(text)

    def copy_command(self) -> None:
        text = self.command_view.toPlainText().strip()
        if not text:
            self.toast.emit("Todavía no hay comando: pulsa \"Generar comando\".", "info")
            return
        # El comando va con el prefijo "$ " solo para mostrarlo; al copiar se
        # quitan los "$ " y los comentarios para que se pueda pegar directo.
        lines = [ln[2:] if ln.startswith("$ ") else ln for ln in text.splitlines() if not ln.startswith("#")]
        QApplication.clipboard().setText("\n".join(ln for ln in lines if ln.strip()))
        self.toast.emit("Comando copiado al portapapeles", "success")

    def copy_log(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.toast.emit("Registro copiado al portapapeles", "success")

    def save_log(self) -> None:
        default = time.strftime("nexohub_%Y%m%d_%H%M%S.log")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar registro", default, "Registro (*.log *.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.command_view.toPlainText() + "\n\n" + self.log_view.toPlainText() + "\n")
        except OSError as exc:
            self.toast.emit(f"No se pudo guardar: {exc}", "error")
            return
        self.toast.emit("Registro guardado", "success")

    def clear_log(self) -> None:
        self.log_view.clear()
