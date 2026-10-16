"""Ajustes rápidos del mapeo, para abrir en pleno juego (tecla F6 por defecto):
sensibilidad de la cámara y su frecuencia, con el cambio aplicado en vivo.

Al cerrarla el mapeo vuelve solo al modo juego."""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from keymap import model
from keymap.model import Keymap

from .keymap_editor import SUSPEND_MODE_LABELS, KeyCaptureButton

APPLY_DELAY_MS = 150  # junta varios cambios seguidos antes de aplicarlos


class QuickSettings(QDialog):
    def __init__(self, keymap: Keymap, on_apply, parent: QWidget | None = None):
        super().__init__(parent)
        self.keymap = keymap
        self.on_apply = on_apply  # callable(Keymap): aplica el mapa al mapeo en marcha
        self.changed = False
        self.setWindowTitle("Mapa de teclas — ajustes rápidos")
        self.setMinimumWidth(360)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._apply)

        layout = QVBoxLayout(self)
        aim = keymap.aim()
        form = QFormLayout()
        if aim is None:
            layout.addWidget(QLabel("Este mapa no tiene una cámara (mouse) que ajustar."))
        else:
            for attr, label in (("sensitivity", "Sensibilidad ↔"), ("sensitivity_y", "Sensibilidad ↕")):
                spin = QDoubleSpinBox()
                spin.setRange(0.05, 10.0)
                spin.setSingleStep(0.05)
                spin.setDecimals(2)
                spin.setValue(getattr(aim, attr))
                spin.valueChanged.connect(lambda v, a=attr: self._set(aim, a, v))
                form.addRow(label, spin)
            radius = QDoubleSpinBox()
            radius.setRange(0.1, 0.6)
            radius.setSingleStep(0.01)
            radius.setDecimals(2)
            radius.setValue(aim.radius)
            radius.setToolTip(
                "Cuánto puede arrastrarse el dedo de la cámara antes de volver al centro "
                "(proporción del alto). Más grande: menos cortes al girar."
            )
            radius.valueChanged.connect(lambda v: self._set(aim, "radius", v))
            form.addRow("Zona de la cámara", radius)
            release = KeyCaptureButton(keymap.cursor_key)
            release.setToolTip(
                "Suelta el mouse para usar el cursor (el teclado sigue mapeado). Un toque corto "
                "lo deja libre hasta otro toque; mantenida, solo mientras la aprietas."
            )
            release.changed.connect(lambda v: self._set(keymap, "cursor_key", v))
            form.addRow("Soltar el mouse", release)
            suspend = KeyCaptureButton(aim.suspend_key)
            suspend.setToolTip("Tecla o botón del mouse que congela la cámara sin soltar el mouse.")
            suspend.changed.connect(lambda v: self._set(aim, "suspend_key", v))
            form.addRow("Congelar cámara", suspend)
            mode = QComboBox()
            for value, text in SUSPEND_MODE_LABELS.items():
                mode.addItem(text, value)
            mode.setCurrentIndex(max(0, mode.findData(aim.suspend_mode)))
            mode.currentIndexChanged.connect(lambda _i: self._set(aim, "suspend_mode", mode.currentData()))
            form.addRow("Al apretarla", mode)
        rate = QSpinBox()
        rate.setRange(*model.AIM_RATE_RANGE)
        rate.setSingleStep(5)
        rate.setSuffix(" Hz")
        rate.setValue(keymap.aim_rate)
        rate.setToolTip("Más alto es más suave, pero si el celular no aguanta se atrasa. Bájala si se atrasa.")
        rate.valueChanged.connect(lambda v: self._set(keymap, "aim_rate", v))
        form.addRow("Frecuencia de cámara", rate)
        layout.addLayout(form)

        hint = QLabel("Los cambios se aplican al instante. Al cerrar vuelves al juego.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        close = QPushButton("Volver al juego")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def _set(self, target, attr: str, value) -> None:
        setattr(target, attr, value)
        self.changed = True
        self._timer.start(APPLY_DELAY_MS)

    def _apply(self) -> None:
        self.on_apply(self.keymap)

    def done(self, result: int) -> None:  # noqa: D401 - QDialog.done
        if self._timer.isActive():
            self._timer.stop()
            self._apply()
        super().done(result)
