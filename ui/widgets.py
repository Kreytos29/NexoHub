"""Widgets reutilizables de NexoHub: tarjetas, acordeón, avisos y toasts."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .theme import COLORS


def help_button(title: str, text: str, parent: QWidget | None = None) -> QToolButton:
    """Botón circular '?' que abre un diálogo con la explicación completa de
    un campo. Complementa el tooltip para quien no pasa el mouse."""
    btn = QToolButton(parent)
    btn.setObjectName("helpButton")
    btn.setText("?")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(lambda: QMessageBox.information(parent, title, text))
    return btn


def section_title(text: str) -> QLabel:
    """Encabezado liviano para subdividir una sección sin meter otra caja
    con borde adentro (evita el efecto 'caja dentro de caja')."""
    label = QLabel(text.upper())
    label.setObjectName("sectionTitle")
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"background-color: {COLORS['surface0']}; max-height: 1px; border: none;")
    return line


def repolish(widget: QWidget) -> None:
    """Fuerza a Qt a releer las propiedades dinámicas del stylesheet."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def icon_button(
    icon_name: str,
    text: str = "",
    *,
    color: str | None = None,
    tooltip: str = "",
    object_name: str | None = None,
    size: int = 17,
) -> QPushButton:
    """Botón con ícono vectorial (y texto opcional)."""
    btn = QPushButton(text)
    tint = color or COLORS["text"]
    btn.setIcon(icons.icon(icon_name, tint, size, disabled_color=COLORS["surface3"]))
    btn.setIconSize(QSize(size, size))
    if tooltip:
        btn.setToolTip(tooltip)
    if object_name:
        btn.setObjectName(object_name)
    elif not text:
        btn.setObjectName("iconBtn")
    return btn


def reflow_grid(layout: QGridLayout, widgets: list[QWidget], columns: int) -> None:
    """Reacomoda widgets en una grilla de N columnas (para tarjetas que se
    suman o se quitan en tiempo de ejecución)."""
    while layout.count():
        layout.takeAt(0)
    for index, widget in enumerate(widgets):
        layout.addWidget(widget, index // columns, index % columns)
    for col in range(columns):
        layout.setColumnStretch(col, 1)


# --------------------------------------------------------------------------
# Tarjetas
# --------------------------------------------------------------------------
class DiagCard(QFrame):
    """Tarjeta pequeña label+valor para el panel de diagnóstico. El texto se
    muestra completo (con wrap): nada queda cortado ni exige scroll."""

    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("diagCard")
        self.setProperty("empty", "true")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)
        self._label = QLabel(label)
        self._label.setProperty("role", "card-label")
        self._value = QLabel("Sin detectar todavía")
        self._value.setProperty("role", "card-value")
        self._value.setWordWrap(True)
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)
        self.setProperty("empty", "false")
        repolish(self)

    def clear(self) -> None:
        self._value.setText("Sin detectar todavía")
        self.setProperty("empty", "true")
        repolish(self)


class DependencyRow(QFrame):
    """Fila-tarjeta para un requisito del sistema: estado, nombre y detalle."""

    def __init__(self, name: str, detail: str):
        super().__init__()
        self.setObjectName("depRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self._status_label = QLabel("…")
        self._status_label.setFixedWidth(96)
        layout.addWidget(self._status_label)

        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        name_label = QLabel(name)
        name_label.setProperty("role", "card-value")
        detail_label = QLabel(detail)
        detail_label.setProperty("role", "field-hint")
        text_box.addWidget(name_label)
        text_box.addWidget(detail_label)
        layout.addLayout(text_box, 1)

        self.set_status(ok=False, warn=False, unknown=True)

    def set_status(self, ok: bool, warn: bool = False, unknown: bool = False) -> None:
        if unknown:
            text, color, state = "…", COLORS["subtext"], "unknown"
        elif warn:
            text, color, state = "⚠ opcional", COLORS["yellow"], "warn"
        elif ok:
            text, color, state = "✓ instalado", COLORS["green"], "ok"
        else:
            text, color, state = "✗ falta", COLORS["red"], "missing"
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.setProperty("status", state)
        repolish(self)


class ProfileCard(QFrame):
    """Tarjeta grande y clickeable para elegir un perfil, en vez de un combo
    escondido. El estado 'seleccionado' se ve en el borde. Los perfiles
    propios del usuario se pueden eliminar con clic derecho."""

    clicked = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        key: str,
        icon_name: str,
        title: str,
        description: str,
        tooltip: str,
        tint: str = "accent",
        deletable: bool = False,
    ):
        super().__init__()
        self.key = key
        self.deletable = deletable
        self.setObjectName("profileCard")
        self.setProperty("selected", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._icon_name = icon_name
        self._tint = COLORS.get(tint, COLORS["accent"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._icon_label = QLabel()
        self._icon_label.setObjectName("profileIcon")
        self._icon_label.setFixedSize(34, 34)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setProperty("role", "profile-title")
        head.addWidget(self._icon_label)
        head.addWidget(title_label, 1)
        self._check_label = QLabel()
        self._check_label.setPixmap(icons.pixmap("check", self._tint, 16, 2.4))
        self._check_label.hide()
        head.addWidget(self._check_label)
        layout.addLayout(head)

        desc_label = QLabel(description)
        desc_label.setProperty("role", "profile-desc")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        self._apply_icon_style(selected=False)

    def _apply_icon_style(self, selected: bool) -> None:
        # En reposo todos los íconos son neutros: el color solo aparece para
        # SEÑALAR cuál es la tarjeta elegida, no como decoración por tarjeta.
        if selected:
            self._icon_label.setStyleSheet(
                f"background-color: {COLORS['accent_soft']}; "
                f"border: 1px solid {self._tint}; border-radius: 9px;"
            )
            self._icon_label.setPixmap(icons.pixmap(self._icon_name, self._tint, 18))
        else:
            self._icon_label.setStyleSheet(
                f"background-color: {COLORS['surface0']}; "
                f"border: 1px solid {COLORS['surface1']}; border-radius: 9px;"
            )
            self._icon_label.setPixmap(icons.pixmap(self._icon_name, COLORS["subtext"], 18))

    def mousePressEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (nombre de Qt)
        if not self.deletable:
            return
        menu = QMenu(self)
        delete_action = menu.addAction(icons.icon("trash", COLORS["red"], 16), "Eliminar perfil")
        if menu.exec(event.globalPos()) is delete_action:
            self.delete_requested.emit(self.key)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self._check_label.setVisible(selected)
        self._apply_icon_style(selected)
        repolish(self)


# --------------------------------------------------------------------------
# Acordeón
# --------------------------------------------------------------------------
class CollapsibleSection(QFrame):
    """Sección plegable con ícono, título y un resumen en vivo a la derecha
    (visible aun con la sección cerrada, para ver los valores de un vistazo)."""

    toggled = pyqtSignal(bool)

    def __init__(self, icon_name: str, title: str, expanded: bool = False):
        super().__init__()
        self.setObjectName("collapsible")
        self.title = title
        self._icon_name = icon_name
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QPushButton()
        self.header.setObjectName("accHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setFixedHeight(52)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.clicked.connect(self._on_toggle)

        row = QHBoxLayout(self.header)
        row.setContentsMargins(18, 0, 16, 0)
        row.setSpacing(11)
        self._icon_label = QLabel()
        self._title_label = QLabel(title)
        self._title_label.setObjectName("accTitle")
        self._summary = QLabel("")
        self._summary.setObjectName("accBadge")
        self._chevron = QLabel()
        for label in (self._icon_label, self._title_label, self._summary, self._chevron):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self._icon_label)
        row.addWidget(self._title_label)
        row.addStretch(1)
        row.addWidget(self._summary)
        row.addWidget(self._chevron)
        outer.addWidget(self.header)

        self.content = QWidget()
        self.content.setVisible(expanded)
        outer.addWidget(self.content)
        self._refresh_header()

    def _refresh_header(self) -> None:
        expanded = self.header.isChecked()
        tint = COLORS["accent"] if expanded else COLORS["subtext"]
        self._icon_label.setPixmap(icons.pixmap(self._icon_name, tint, 18))
        chevron = "chevron_down" if expanded else "chevron_right"
        self._chevron.setPixmap(icons.pixmap(chevron, COLORS["subtext"], 14, 2.2))

    def _on_toggle(self) -> None:
        expanded = self.header.isChecked()
        self.content.setVisible(expanded)
        self._refresh_header()
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        if self.header.isChecked() == expanded:
            return
        self.header.setChecked(expanded)
        self._on_toggle()

    def set_summary(self, text: str) -> None:
        self._summary.setText(text)


# --------------------------------------------------------------------------
# Avisos y notificaciones
# --------------------------------------------------------------------------
class Banner(QFrame):
    """Aviso persistente dentro de la página (a diferencia del Toast, que se
    cierra solo). Opcionalmente con un botón de acción."""

    action_clicked = pyqtSignal()

    def __init__(self, text: str = "", action_text: str = "", kind: str = "warn"):
        super().__init__()
        self.setObjectName("banner")
        self.setProperty("kind", kind)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 12, 11)
        layout.setSpacing(12)
        self._icon = QLabel()
        self._icon.setPixmap(
            icons.pixmap("alert", COLORS["yellow"] if kind == "warn" else COLORS["red"], 20)
        )
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._text = QLabel(text)
        self._text.setObjectName("bannerText")
        self._text.setWordWrap(True)
        layout.addWidget(self._text, 1)
        self._button = QPushButton(action_text)
        self._button.setVisible(bool(action_text))
        self._button.clicked.connect(self.action_clicked)
        layout.addWidget(self._button, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_text(self, text: str) -> None:
        self._text.setText(text)


class Toast(QFrame):
    """Notificación chica que aparece sobre el contenido y se cierra sola,
    para avisos rápidos de éxito/error/info que no piden decidir nada (a
    diferencia de un QMessageBox, que bloquea hasta que le das OK). Puede
    llevar un botón de acción (ej. "Abrir carpeta")."""

    closed = pyqtSignal(object)

    _ACCENT_KEY = {"success": "green", "error": "red", "info": "accent"}
    _ICON = {"success": "check", "error": "x", "info": "info"}

    def __init__(
        self,
        parent: QWidget,
        message: str,
        kind: str = "info",
        action_text: str = "",
        action: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self._dismissed = False
        color = COLORS.get(self._ACCENT_KEY.get(kind, "accent"), COLORS["accent"])
        self.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['surface0']}; "
            f"border: 1px solid {COLORS['surface2']}; "
            f"border-left: 4px solid {color}; border-radius: 10px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 8, 10)
        layout.setSpacing(10)

        icon = QLabel()
        icon.setPixmap(icons.pixmap(self._ICON.get(kind, "info"), color, 16, 2.4))
        icon.setStyleSheet("border: none; background: transparent;")
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        label = QLabel(message)
        label.setWordWrap(True)
        label.setMaximumWidth(300)
        label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 12px; font-weight: 600; "
            f"border: none; background: transparent;"
        )
        layout.addWidget(label, 1)

        if action_text and action is not None:
            action_btn = QPushButton(action_text)
            action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            action_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {color}; border: none; "
                f"font-size: 12px; font-weight: 700; padding: 4px 8px; }}"
                f"QPushButton:hover {{ color: {COLORS['text']}; }}"
            )
            action_btn.clicked.connect(lambda: (action(), self.dismiss()))
            layout.addWidget(action_btn, 0, Qt.AlignmentFlag.AlignTop)

        close_btn = QToolButton()
        close_btn.setIcon(icons.icon("x", COLORS["subtext"], 12))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; padding: 4px; }"
        )
        close_btn.clicked.connect(self.dismiss)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.setFixedWidth(360)
        self.adjustSize()

        # Los errores (y los avisos con acción) se quedan más tiempo: hay algo
        # que leer o hacer; el resto es solo una confirmación de "listo".
        duration_ms = 7000 if kind == "error" or action else 3000
        QTimer.singleShot(duration_ms, self.dismiss)

    def dismiss(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.closed.emit(self)
        self.hide()
        self.deleteLater()
