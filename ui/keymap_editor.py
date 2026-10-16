"""
Editor visual del mapa de teclas, al estilo del de BlueStacks.

De fondo se muestra una captura del celular (abre el juego antes de tocar
«Nueva captura») y encima cada control como un círculo que se arrastra a su
botón. A la derecha se elige la tecla de cada uno: se pulsa el botón de la
tecla y después la tecla (o el botón del mouse) que se quiera usar.
"""
from __future__ import annotations

import copy
import subprocess

from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from keymap import model
from keymap.model import Control, Keymap, key_label

from .theme import COLORS
from .widgets import section_title

# Si no hay captura, un lienzo vacío con la proporción típica (20:9).
DEFAULT_SCENE = (2400, 1080)
TAP_RADIUS = 0.04  # tamaño mínimo de dibujo (marcas del joystick/cámara)
CURSOR_MODE_LABELS = {  # también para la tecla de pausa: «auto» y «mantener» son iguales
    "auto": "Automático (toque o mantener)",
    "toggle": "Alternar (un toque)",
    "hold": "Mantener apretada",
}
SUSPEND_MODE_LABELS = {"hold": "Mientras la mantengo", "toggle": "Alternar con cada toque"}
SPECIAL_LABELS = {
    "editor_key": "Abrir este editor",
    "toggle_key": "Activar/desactivar",
    "suspend_key": "Pausar (todo al PC)",
    "cursor_key": "Soltar el mouse",
    "overlay_key": "Ver/ocultar teclas",
    "quick_key": "Ajustes rápidos",
}
KIND_LABELS = {"tap": "Tecla", "joystick": "Joystick", "aim": "Cámara (mouse)"}

_QT_MOUSE_BUTTONS = {
    Qt.MouseButton.LeftButton: "BTN_LEFT",
    Qt.MouseButton.RightButton: "BTN_RIGHT",
    Qt.MouseButton.MiddleButton: "BTN_MIDDLE",
    Qt.MouseButton.BackButton: "BTN_SIDE",
    Qt.MouseButton.ForwardButton: "BTN_EXTRA",
}


def _evdev_name(scan_code: int) -> str | None:
    """En Linux (X11 y Wayland) el código nativo de Qt es el de evdev + 8."""
    if scan_code <= 8:
        return None
    try:
        from keymap.inputs import code_name  # noqa: PLC0415

        return code_name(scan_code - 8)
    except Exception:  # noqa: BLE001 - sin evdev no se puede nombrar la tecla
        return None


# --------------------------------------------------------------------------
# Botón que captura una tecla
# --------------------------------------------------------------------------
class KeyCaptureButton(QPushButton):
    """Muestra una tecla; al pulsarlo espera la siguiente tecla o botón del
    mouse y la guarda. Esc cancela; Supr deja la tecla vacía. También acepta la rueda."""

    changed = pyqtSignal(str)

    def __init__(self, value: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.value = value
        self.capturing = False
        self.setMinimumWidth(110)
        self.setToolTip("Pulsa aquí y después la tecla o el botón del mouse que quieras usar.")
        self.clicked.connect(self._start)
        self._refresh()

    def set_value(self, value: str) -> None:
        self.value = value
        self._refresh()

    def _refresh(self) -> None:
        self.setText("Presiona una tecla…" if self.capturing else key_label(self.value))

    def _start(self) -> None:
        if self.capturing:
            return
        self.capturing = True
        self._refresh()
        self.grabKeyboard()
        self.grabMouse()

    def _finish(self, value: str | None) -> None:
        self.capturing = False
        self.releaseKeyboard()
        self.releaseMouse()
        if value is not None and value != self.value:
            self.value = value
            self.changed.emit(value)
        self._refresh()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self.capturing:
            super().keyPressEvent(event)
            return
        if event.isAutoRepeat():
            return
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)
        elif event.key() == Qt.Key.Key_Delete:
            self._finish("")
        else:
            name = _evdev_name(event.nativeScanCode())
            if name is None:
                QMessageBox.warning(
                    self, "Tecla no reconocida",
                    "No se pudo identificar esa tecla (falta el paquete «evdev»).",
                )
            self._finish(name)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.capturing:
            super().mousePressEvent(event)
            return
        self._finish(_QT_MOUSE_BUTTONS.get(event.button()))

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.capturing:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta:
            self._finish("WHEEL_UP" if delta > 0 else "WHEEL_DOWN")

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if self.capturing:
            self._finish(None)
        super().focusOutEvent(event)


# --------------------------------------------------------------------------
# Lienzo
# --------------------------------------------------------------------------
class ControlItem(QGraphicsObject):
    selected = pyqtSignal(object)
    resized = pyqtSignal(object)

    def __init__(self, control: Control, scene_size: tuple[int, int]):
        super().__init__()
        self.control = control
        self.scene_size = scene_size
        self.highlight = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(2 if control.kind == "tap" else 1)
        self.sync_from_control()

    def radius(self) -> float:
        height = self.scene_size[1]
        if self.control.kind == "tap":
            return max(self.control.size * height, 8)
        return max(self.control.radius * height, TAP_RADIUS * height)

    def sync_from_control(self) -> None:
        self.prepareGeometryChange()
        width, height = self.scene_size
        self.setPos(self.control.x * width, self.control.y * height)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        r = self.radius() + 8
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.radius()
        accent = QColor(COLORS["accent"])
        edge = QColor(COLORS["gold"]) if self.highlight else accent
        fill = QColor(0, 0, 0, 150)
        height = self.scene_size[1]
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(int(height * 0.026), 10))
        painter.setFont(font)

        if self.control.kind == "aim":
            pen = QPen(edge, max(height * 0.004, 2), Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(224, 48, 63, 35)))
            painter.drawEllipse(QPointF(0, 0), r, r)
            painter.setPen(QPen(edge, max(height * 0.004, 2)))
            tick = TAP_RADIUS * height
            painter.drawLine(QPointF(-tick, 0), QPointF(tick, 0))
            painter.drawLine(QPointF(0, -tick), QPointF(0, tick))
            self._label(painter, QPointF(0, tick * 1.8), "Mouse")
            return

        if self.control.kind == "joystick":
            painter.setPen(QPen(edge, max(height * 0.004, 2)))
            painter.setBrush(QBrush(QColor(0, 0, 0, 90)))
            painter.drawEllipse(QPointF(0, 0), r, r)
            keys = self.control.keys
            offset = r * 0.62
            for direction, (dx, dy) in {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}.items():
                self._label(painter, QPointF(dx * offset, dy * offset), key_label(keys.get(direction, "")))
            painter.setBrush(QBrush(accent))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(0, 0), r * 0.12, r * 0.12)
            return

        painter.setPen(QPen(edge, max(height * 0.004, 2)))
        painter.setBrush(QBrush(fill))
        painter.drawEllipse(QPointF(0, 0), r, r)
        self._label(painter, QPointF(0, 0), key_label(self.control.key))

    @staticmethod
    def _label(painter: QPainter, center: QPointF, text: str) -> None:
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 10
        height = metrics.height() + 2
        rect = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)
        painter.setPen(QColor("white"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            width, height = self.scene_size
            x = min(max(value.x(), 0), width)
            y = min(max(value.y(), 0), height)
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            width, height = self.scene_size
            self.control.x = self.pos().x() / width
            self.control.y = self.pos().y() / height
        return super().itemChange(change, value)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Rueda del mouse sobre un control: lo agranda o achica."""
        step = 1.08 if event.delta() > 0 else 1 / 1.08
        if self.control.kind == "tap":
            self.control.size = min(max(self.control.size * step, 0.015), 0.15)
        else:
            self.control.radius = min(max(self.control.radius * step, 0.03), 0.6)
        self.sync_from_control()
        self.selected.emit(self)
        self.resized.emit(self)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.selected.emit(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class CanvasView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor(COLORS["crust"]))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def fit(self) -> None:
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit()


class ScreencapWorker(QThread):
    done = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial

    def run(self) -> None:
        try:
            result = subprocess.run(
                ["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.failed.emit(str(exc))
            return
        if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG"):
            self.failed.emit(result.stderr.decode(errors="replace").strip() or "captura vacía")
            return
        self.done.emit(result.stdout)


# --------------------------------------------------------------------------
# Diálogo
# --------------------------------------------------------------------------
class KeymapEditor(QDialog):
    def __init__(self, keymap: Keymap, serial: str | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Mapa de teclas · {keymap.display_name}")
        self.resize(1280, 680)
        self.keymap = copy.deepcopy(keymap)
        self.serial = serial
        self.saved = False
        self.items: list[ControlItem] = []
        self.current: ControlItem | None = None
        self._size_spin: QDoubleSpinBox | None = None
        self.scene_size = DEFAULT_SCENE
        self._capture_worker: ScreencapWorker | None = None

        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self.scene)
        self._background = None

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        left = QVBoxLayout()
        left.addWidget(self.view, 1)
        self.capture_hint = QLabel()
        self.capture_hint.setProperty("role", "hint")
        self.capture_hint.setWordWrap(True)
        left.addWidget(self.capture_hint)
        root.addLayout(left, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(372)
        scroll.setWidget(self._build_side())
        root.addWidget(scroll)

        self._set_scene_size(DEFAULT_SCENE)
        self._rebuild_items()
        if serial:
            self.on_capture()
        else:
            self.capture_hint.setText("Conecta y detecta el celular para ver una captura del juego de fondo.")

    # -------------------------------------------------------------- panel
    def _build_side(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(352)
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        col.addWidget(section_title("Controles"))
        self.list = QListWidget()
        self.list.setMinimumHeight(170)
        self.list.currentRowChanged.connect(self._on_list_row)
        col.addWidget(self.list, 1)

        add_row = QHBoxLayout()
        for kind, text in (("tap", "+ Tecla"), ("joystick", "+ Joystick"), ("aim", "+ Cámara")):
            btn = QPushButton(text)
            btn.setObjectName("ghost")
            btn.clicked.connect(lambda _=False, k=kind: self.on_add(k))
            add_row.addWidget(btn)
            setattr(self, f"add_{kind}_btn", btn)
        col.addLayout(add_row)

        self.detail = QWidget()
        self.detail_form = QFormLayout(self.detail)
        self.detail_form.setContentsMargins(0, 4, 0, 0)
        col.addWidget(self.detail)

        col.addWidget(section_title("Teclas especiales"))
        special = QFormLayout()
        special.setVerticalSpacing(6)
        self.special_buttons: dict[str, KeyCaptureButton] = {}
        for attr, label in SPECIAL_LABELS.items():
            btn = KeyCaptureButton(getattr(self.keymap, attr))
            btn.changed.connect(lambda v, a=attr: self._set_global(a, v))
            self.special_buttons[attr] = btn
            special.addRow(label, btn)
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.1, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setDecimals(2)
        self.opacity_spin.setValue(self.keymap.overlay_opacity)
        self.opacity_spin.setToolTip("Qué tan visibles se ven las teclas dibujadas sobre el juego.")
        self.opacity_spin.valueChanged.connect(lambda v: setattr(self.keymap, "overlay_opacity", v))
        special.addRow("Opacidad en juego", self.opacity_spin)
        self.cursor_mode_combo = QComboBox()
        for mode, text in CURSOR_MODE_LABELS.items():
            self.cursor_mode_combo.addItem(text, mode)
        self.cursor_mode_combo.setCurrentIndex(max(0, self.cursor_mode_combo.findData(self.keymap.cursor_mode)))
        self.cursor_mode_combo.setToolTip(
            "Cómo funciona la tecla de cursor. Automático: un toque corto deja el cursor "
            "libre hasta otro toque, y mantenida lo libera solo mientras la aprietas."
        )
        self.cursor_mode_combo.currentIndexChanged.connect(
            lambda _i: setattr(self.keymap, "cursor_mode", self.cursor_mode_combo.currentData())
        )
        special.addRow("Modo del cursor", self.cursor_mode_combo)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(*model.AIM_RATE_RANGE)
        self.rate_spin.setSingleStep(5)
        self.rate_spin.setSuffix(" Hz")
        self.rate_spin.setValue(self.keymap.aim_rate)
        self.rate_spin.setToolTip(
            "Cuántas veces por segundo se manda la cámara al celular. Más alto es más "
            "suave, pero si el celular no aguanta se atrasa y el disparo se queda pegado. "
            "Si pasa, bájala."
        )
        self.rate_spin.valueChanged.connect(lambda v: setattr(self.keymap, "aim_rate", v))
        special.addRow("Frecuencia de cámara", self.rate_spin)
        aim = self.keymap.aim()
        if aim is not None:
            # La tecla de congelar la cámara también está en el control de cámara;
            # aquí está a la vista, junto a las demás teclas especiales.
            self.freeze_button = KeyCaptureButton(aim.suspend_key)
            self.freeze_button.setToolTip(
                "Congela la cámara sin soltar el mouse (por ejemplo, para abrir la mochila "
                "sin que la mira se mueva). Mantenida o alternando, según «Al apretarla»."
            )
            self.freeze_button.changed.connect(self._set_freeze_key)
            special.addRow("Congelar cámara", self.freeze_button)
            self.freeze_mode = QComboBox()
            for value, text in SUSPEND_MODE_LABELS.items():
                self.freeze_mode.addItem(text, value)
            self.freeze_mode.setCurrentIndex(max(0, self.freeze_mode.findData(aim.suspend_mode)))
            self.freeze_mode.currentIndexChanged.connect(
                lambda _i: setattr(self.keymap.aim(), "suspend_mode", self.freeze_mode.currentData())
            )
            special.addRow("Al apretarla", self.freeze_mode)
        col.addLayout(special)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 12px;")
        col.addWidget(self.warning)

        tools = QHBoxLayout()
        self.capture_btn = QPushButton("Nueva captura")
        self.capture_btn.setObjectName("ghost")
        self.capture_btn.setEnabled(bool(self.serial))
        self.capture_btn.clicked.connect(self.on_capture)
        tools.addWidget(self.capture_btn)
        self.reset_btn = QPushButton("Restaurar de fábrica")
        self.reset_btn.setObjectName("ghost")
        self.reset_btn.setVisible(model.is_preset(self.keymap.name))
        self.reset_btn.clicked.connect(self.on_reset)
        tools.addWidget(self.reset_btn)
        col.addLayout(tools)

        actions = QHBoxLayout()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Guardar")
        save.setObjectName("primary")
        save.clicked.connect(self.on_save)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(save)
        col.addLayout(actions)
        return side

    # ------------------------------------------------------------- lienzo
    def _set_scene_size(self, size: tuple[int, int]) -> None:
        self.scene_size = size
        self.scene.setSceneRect(0, 0, *size)
        for item in self.items:
            item.scene_size = size
            item.sync_from_control()
        self.view.fit()

    def _rebuild_items(self, select: int | None = None) -> None:
        for item in self.items:
            self.scene.removeItem(item)
        self.items = []
        self.list.blockSignals(True)
        self.list.clear()
        for control in self.keymap.controls:
            item = ControlItem(control, self.scene_size)
            item.selected.connect(self._select_item)
            item.resized.connect(self._on_item_resized)
            self.scene.addItem(item)
            self.items.append(item)
            self.list.addItem(QListWidgetItem(self._list_text(control)))
        self.list.blockSignals(False)
        for kind in ("joystick", "aim"):
            getattr(self, f"add_{kind}_btn").setEnabled(
                not any(c.kind == kind for c in self.keymap.controls)
            )
        self.current = None
        if self.items:
            index = select if select is not None and 0 <= select < len(self.items) else 0
            self.list.setCurrentRow(index)
        else:
            self._show_detail(None)
        self._refresh_warnings()

    @staticmethod
    def _list_text(control: Control) -> str:
        name = control.name or KIND_LABELS[control.kind]
        if control.kind == "tap":
            return f"{name}  ·  {key_label(control.key)}"
        if control.kind == "joystick":
            keys = "".join(key_label(control.keys.get(d, "")) for d in model.JOYSTICK_DIRECTIONS)
            return f"{name}  ·  {keys}"
        return f"{name}  ·  Mouse"

    def _on_item_resized(self, item: ControlItem) -> None:
        if item is self.current:
            spin = self._size_spin
            if spin is not None:
                spin.blockSignals(True)
                spin.setValue(item.control.size if item.control.kind == "tap" else item.control.radius)
                spin.blockSignals(False)

    def _select_item(self, item: ControlItem) -> None:
        if item in self.items:
            self.list.setCurrentRow(self.items.index(item))

    def _on_list_row(self, row: int) -> None:
        if self.current is not None:
            self.current.highlight = False
            self.current.update()
        self.current = self.items[row] if 0 <= row < len(self.items) else None
        if self.current is not None:
            self.current.highlight = True
            self.current.update()
        self._show_detail(self.current)

    # ------------------------------------------------------------ detalle
    def _show_detail(self, item: ControlItem | None) -> None:
        while self.detail_form.rowCount():
            self.detail_form.removeRow(0)
        self._size_spin = None
        if item is None:
            return
        control = item.control

        name = QLineEdit(control.name)
        name.setPlaceholderText(KIND_LABELS[control.kind])
        name.textChanged.connect(lambda text: self._edit(item, name=text))
        self.detail_form.addRow("Nombre", name)

        if control.kind == "tap":
            key = KeyCaptureButton(control.key)
            key.changed.connect(lambda v: self._edit(item, key=v))
            self.detail_form.addRow("Tecla", key)
            size = QDoubleSpinBox()
            size.setRange(0.015, 0.15)
            size.setSingleStep(0.005)
            size.setDecimals(3)
            size.setValue(control.size)
            size.setToolTip(
                "Tamaño del círculo (en el editor y sobre el juego). También con la "
                "rueda del mouse encima del control. No cambia dónde se toca."
            )
            size.valueChanged.connect(lambda v: self._edit(item, size=v))
            self._size_spin = size
            self.detail_form.addRow("Tamaño", size)
        elif control.kind == "joystick":
            labels = {"up": "Arriba", "left": "Izquierda", "down": "Abajo", "right": "Derecha"}
            for direction in model.JOYSTICK_DIRECTIONS:
                btn = KeyCaptureButton(control.keys.get(direction, ""))
                btn.changed.connect(lambda v, d=direction: self._edit_joystick_key(item, d, v))
                self.detail_form.addRow(labels[direction], btn)

        if control.kind in ("joystick", "aim"):
            radius = QDoubleSpinBox()
            radius.setRange(0.03, 0.6)
            radius.setSingleStep(0.01)
            radius.setDecimals(2)
            radius.setValue(control.radius)
            radius.setToolTip(
                "Joystick: hasta dónde se empuja el stick. Cámara: cuánto se puede "
                "arrastrar el dedo antes de volver al centro. En proporción al alto "
                "de la pantalla."
            )
            radius.valueChanged.connect(lambda v: self._edit(item, radius=v))
            self._size_spin = radius
            self.detail_form.addRow("Radio", radius)
        if control.kind == "aim":
            for attr, label, tip in (
                ("sensitivity", "Sensibilidad ↔",
                 "Cuánto gira la cámara a los lados por cada movimiento del mouse. "
                 "Si a 0,5 te queda lenta, súbela de a poco (o sube la sensibilidad "
                 "dentro del juego)."),
                ("sensitivity_y", "Sensibilidad ↕",
                 "Cuánto sube o baja la mira por cada movimiento del mouse."),
            ):
                sens = QDoubleSpinBox()
                sens.setRange(0.05, 10.0)
                sens.setSingleStep(0.05)
                sens.setDecimals(2)
                sens.setValue(getattr(control, attr))
                sens.setToolTip(tip)
                sens.valueChanged.connect(lambda v, a=attr: self._edit(item, **{a: v}))
                self.detail_form.addRow(label, sens)
            suspend = KeyCaptureButton(control.suspend_key)
            suspend.changed.connect(lambda v: self._edit(item, suspend_key=v))
            suspend.setToolTip(
                "Tecla o botón del mouse que congela la cámara sin soltar el mouse "
                "(por ejemplo, para abrir la mochila sin que la mira se mueva)."
            )
            self.detail_form.addRow("Congelar cámara", suspend)
            mode = QComboBox()
            for value, text in SUSPEND_MODE_LABELS.items():
                mode.addItem(text, value)
            mode.setCurrentIndex(max(0, mode.findData(control.suspend_mode)))
            mode.currentIndexChanged.connect(lambda _i: self._edit(item, suspend_mode=mode.currentData()))
            self.detail_form.addRow("Al apretarla", mode)

        delete = QPushButton("Eliminar control")
        delete.setObjectName("danger")
        delete.clicked.connect(lambda: self.on_delete(item))
        self.detail_form.addRow("", delete)

    def _edit(self, item: ControlItem, **changes) -> None:
        for key, value in changes.items():
            setattr(item.control, key, value)
        item.sync_from_control()
        self.list.item(self.items.index(item)).setText(self._list_text(item.control))
        self._refresh_warnings()

    def _edit_joystick_key(self, item: ControlItem, direction: str, value: str) -> None:
        keys = dict(item.control.keys)
        keys[direction] = value
        self._edit(item, keys=keys)

    def _set_freeze_key(self, value: str) -> None:
        aim = self.keymap.aim()
        if aim is not None:
            aim.suspend_key = value
            self._refresh_warnings()
            row = next((i for i, c in enumerate(self.keymap.controls) if c is aim), None)
            if row is not None and 0 <= row < len(self.items):
                self._show_detail(self.items[row]) if self.list.currentRow() == row else None

    def _set_global(self, attr: str, value: str) -> None:
        if not value and attr in ("editor_key", "toggle_key"):
            QMessageBox.warning(self, "Tecla necesaria", "Esta tecla no puede quedar vacía.")
            self.special_buttons[attr].set_value(getattr(self.keymap, attr))
            return
        setattr(self.keymap, attr, value)
        self._refresh_warnings()

    def _refresh_warnings(self) -> None:
        problems = self.keymap.conflicts()
        missing = [c.name or KIND_LABELS[c.kind] for c in self.keymap.controls if c.kind == "tap" and not c.key]
        if missing:
            problems.append("Sin tecla: " + ", ".join(missing) + ".")
        self.warning.setText("\n".join(problems))

    # ----------------------------------------------------------- acciones
    def on_add(self, kind: str) -> None:
        if kind == "tap":
            control = Control(kind="tap", x=0.5, y=0.5)
        elif kind == "joystick":
            control = Control(
                kind="joystick", x=0.15, y=0.7, radius=0.13,
                keys={"up": "KEY_W", "left": "KEY_A", "down": "KEY_S", "right": "KEY_D"},
            )
        else:
            control = Control(kind="aim", x=0.62, y=0.4, radius=model.DEFAULT_AIM_RADIUS)
        self.keymap.controls.append(control)
        self._rebuild_items(select=len(self.keymap.controls) - 1)

    def on_delete(self, item: ControlItem) -> None:
        index = self.items.index(item)
        del self.keymap.controls[index]
        self._rebuild_items(select=max(index - 1, 0))

    def on_reset(self) -> None:
        confirm = QMessageBox.question(
            self, "Restaurar de fábrica",
            "¿Volver a las posiciones y teclas de fábrica de este mapa? Se pierden tus cambios al guardar.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        preset = model.load_preset(self.keymap.name)
        self.keymap.controls = preset.controls
        for attr, btn in self.special_buttons.items():
            setattr(self.keymap, attr, getattr(preset, attr))
            btn.set_value(getattr(preset, attr))
        self.keymap.overlay_opacity = preset.overlay_opacity
        self.opacity_spin.setValue(preset.overlay_opacity)
        self.keymap.cursor_mode = preset.cursor_mode
        self.cursor_mode_combo.setCurrentIndex(max(0, self.cursor_mode_combo.findData(preset.cursor_mode)))
        self.keymap.aim_rate = preset.aim_rate
        self.rate_spin.setValue(preset.aim_rate)
        self._rebuild_items()

    def on_capture(self) -> None:
        if not self.serial or (self._capture_worker and self._capture_worker.isRunning()):
            return
        self.capture_btn.setEnabled(False)
        self.capture_hint.setText("Tomando captura del celular…")
        worker = ScreencapWorker(self.serial)
        worker.done.connect(self._on_capture_done)
        worker.failed.connect(self._on_capture_failed)
        worker.finished.connect(lambda: self.capture_btn.setEnabled(True))
        self._capture_worker = worker
        worker.start()

    def _on_capture_done(self, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "PNG"):
            self._on_capture_failed("no se pudo leer la imagen")
            return
        if self._background is not None:
            self.scene.removeItem(self._background)
        self._background = self.scene.addPixmap(pixmap)
        self._background.setZValue(-1)
        self._set_scene_size((pixmap.width(), pixmap.height()))
        if pixmap.height() > pixmap.width():
            self.capture_hint.setText(
                "La captura salió en vertical: abre el juego en el celular (en una "
                "partida o el campo de entrenamiento) y pulsa «Nueva captura»."
            )
        else:
            self.capture_hint.setText(
                "Arrastra cada círculo sobre su botón del juego. Para ver el HUD "
                "completo, saca la captura dentro de una partida o del campo de entrenamiento."
            )

    def _on_capture_failed(self, message: str) -> None:
        self.capture_hint.setText(f"No se pudo tomar la captura: {message}")

    def on_save(self) -> None:
        try:
            model.save(self.keymap)
        except OSError as exc:
            QMessageBox.warning(self, "No se pudo guardar", str(exc))
            return
        self.saved = True
        self.accept()

    def done(self, result: int) -> None:  # noqa: D401 - QDialog.done
        if self._capture_worker is not None and self._capture_worker.isRunning():
            self._capture_worker.wait(3000)
        super().done(result)
