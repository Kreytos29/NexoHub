"""
Tema visual de NexoHub: negro + rojo.

Una sola paleta (COLORS) y una sola hoja de estilos (STYLESHEET) para toda
la app, más utilidades chicas de estilo. Los íconos viven en ui/icons.py.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget

ASSETS = Path(__file__).resolve().parent.parent / "assets"

COLORS = {
    "base": "#0b0d12",
    "mantle": "#12151c",
    "crust": "#080a0e",
    "surface0": "#1a1e27",
    "surface1": "#242935",
    "surface2": "#343b4a",
    "surface3": "#4a5162",
    "field": "#11141b",
    "text": "#eef0f4",
    "subtext": "#8b93a3",
    # Rojo de marca (el mismo que ya usaban el icono del instalador y el
    # borde del overlay de FPS) en vez del azul que se habia colado en
    # algun momento -- esto es lo que hace que la app se sienta "negro y
    # rojo" de punta a punta en vez de mezclar paletas sueltas.
    "accent": "#e0303f",
    "accent2": "#e0303f",
    "accent_hover": "#ff4d5e",
    "accent_active": "#b8232f",
    "accent_soft": "#2a1013",
    "accent_border": "#5c2129",
    "gold": "#e8b84b",
    "teal": "#3fc9c1",
    "green": "#3ddc97",
    "term_green": "#39ff6a",
    "yellow": "#f2bd4e",
    # Rojo de "peligro/error", deliberadamente mas rosado que el accent
    # de marca -- son dos rojos distintos a proposito (uno es identidad,
    # el otro es semantica de "algo salio mal"), para que no se confundan
    # un boton primario con un aviso de error.
    "red": "#ff5566",
}
# Boton primario en color solido: mas "panel de control profesional"
# que degradado -- el color hace un solo trabajo (llamar la atencion a
# LA accion principal), no decora.
GRADIENT_PRIMARY = COLORS["accent"]
GRADIENT_PRIMARY_HOVER = COLORS["accent_hover"]

STYLESHEET = f"""
QWidget {{
    background-color: {COLORS['base']};
    color: {COLORS['text']};
    font-size: 13px;
    font-family: "Inter", "Segoe UI", "Cantarell", "Ubuntu", sans-serif;
}}
QMainWindow {{
    background-color: {COLORS['crust']};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QWidget#contentArea {{
    background-color: {COLORS['base']};
}}
QToolTip {{
    background-color: {COLORS['surface1']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['surface2']};
    border-radius: 8px;
    padding: 8px 11px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['surface2']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['surface3']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QLabel#brand {{
    color: {COLORS['text']};
    font-size: 17px;
    font-weight: 800;
    letter-spacing: 0.3px;
}}
QLabel#brandSubtitle {{
    color: {COLORS['subtext']};
    font-size: 11px;
    letter-spacing: 0.2px;
}}
QPushButton#authorBadge {{
    background-color: {COLORS['surface0']};
    color: {COLORS['subtext']};
    border: 1px solid {COLORS['surface2']};
    border-radius: 12px;
    padding: 6px 14px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton#authorBadge:hover {{
    background-color: {COLORS['accent_soft']};
    color: {COLORS['accent']};
    border-color: {COLORS['accent_border']};
}}
QPushButton#exitButton {{
    background-color: transparent;
    color: {COLORS['subtext']};
    border: none;
    border-radius: 10px;
    padding: 8px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton#exitButton:hover {{
    background-color: {COLORS['accent_soft']};
    color: {COLORS['red']};
}}

/* --- barra superior --- */
QWidget#topbar {{
    background-color: {COLORS['base']};
    border-bottom: 1px solid {COLORS['surface0']};
}}
QLabel[role="pill"] {{
    background-color: {COLORS['surface0']};
    color: {COLORS['subtext']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 12px;
    padding: 4px 13px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#statusDot {{
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}}
QLabel#statusText {{
    color: {COLORS['text']};
    font-size: 12px;
    font-weight: 700;
}}
QFrame#statusChip {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 16px;
}}

/* --- contenedores de seccion (reemplazan al QGroupBox por defecto) --- */
QGroupBox {{
    background-color: {COLORS['mantle']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 14px;
    margin-top: 20px;
    padding-top: 22px;
    padding-bottom: 4px;
    font-weight: 700;
    font-size: 13px;
    color: {COLORS['text']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 18px;
    top: 3px;
    padding: 0 6px;
    color: {COLORS['text']};
    letter-spacing: 0.2px;
}}
QPushButton {{
    background-color: {COLORS['surface0']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 10px;
    padding: 9px 17px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {COLORS['surface1']};
    border: 1px solid {COLORS['surface2']};
}}
QPushButton:pressed {{
    background-color: {COLORS['surface0']};
}}
QPushButton:disabled {{
    color: {COLORS['subtext']};
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface0']};
}}
QPushButton#primary {{
    background: {GRADIENT_PRIMARY};
    border: 1px solid {COLORS['accent']};
    color: #ffffff;
    font-weight: 700;
}}
QPushButton#primary:hover {{
    background: {GRADIENT_PRIMARY_HOVER};
    border: 1px solid {COLORS['accent_hover']};
}}
QPushButton#primary:pressed {{
    background-color: {COLORS['accent_active']};
    border: 1px solid {COLORS['accent_active']};
}}
QPushButton#primary:disabled {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface0']};
    color: {COLORS['subtext']};
}}
QPushButton#bigAction {{
    padding: 13px 24px;
    font-size: 13px;
    border-radius: 11px;
}}
QFrame#actionBar {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 12px;
}}
QToolButton#helpButton {{
    background-color: {COLORS['surface0']};
    color: {COLORS['subtext']};
    border-radius: 10px;
    min-width: 20px;
    max-width: 20px;
    min-height: 20px;
    max-height: 20px;
    font-weight: 700;
}}
QToolButton#helpButton:hover {{
    background-color: {COLORS['accent']};
    color: #ffffff;
}}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 10px;
    padding: 7px 11px;
    selection-background-color: {COLORS['accent']};
    selection-color: #ffffff;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border: 1px solid {COLORS['surface2']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {COLORS['accent']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QLabel[role="hint"] {{
    color: {COLORS['subtext']};
    font-size: 12px;
    line-height: 140%;
}}
QLabel[role="field-hint"] {{
    color: {COLORS['subtext']};
    font-size: 11px;
}}
QCheckBox {{
    spacing: 9px;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 6px;
    border: 1px solid {COLORS['surface2']};
    background-color: {COLORS['field']};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {COLORS['surface3']};
}}
QCheckBox::indicator:checked {{
    background: {GRADIENT_PRIMARY};
    border: 1px solid {COLORS['accent']};
}}

/* --- tarjetas de diagnostico / dependencias --- */
QFrame#diagCard, QFrame#depRow {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface0']};
    border-left: 3px solid {COLORS['surface1']};
    border-radius: 12px;
}}
QFrame#diagCard:hover, QFrame#depRow:hover {{
    background-color: {COLORS['surface0']};
}}
QFrame#diagCard[empty="true"] {{
    border-left: 3px solid {COLORS['surface1']};
}}
QFrame#diagCard[empty="false"] {{
    border-left: 3px solid {COLORS['green']};
}}
QFrame#depRow[status="ok"] {{
    border-left: 3px solid {COLORS['green']};
}}
QFrame#depRow[status="missing"] {{
    border-left: 3px solid {COLORS['red']};
}}
QFrame#depRow[status="warn"] {{
    border-left: 3px solid {COLORS['yellow']};
}}
QLabel[role="card-label"] {{
    color: {COLORS['subtext']};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2px;
}}
QLabel[role="card-value"] {{
    color: {COLORS['text']};
    font-size: 14px;
    font-weight: 600;
}}

/* --- tarjetas de perfil --- */
QFrame#profileCard {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 13px;
}}
QFrame#profileCard:hover {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface2']};
}}
QFrame#profileCard[selected="true"] {{
    border: 2px solid {COLORS['accent']};
    background-color: {COLORS['accent_soft']};
}}
QLabel[role="profile-title"] {{
    color: {COLORS['text']};
    font-size: 14px;
    font-weight: 700;
}}
QLabel[role="profile-desc"] {{
    color: {COLORS['subtext']};
    font-size: 11px;
}}
QLabel#profileCheck {{
    color: {COLORS['accent']};
    font-size: 15px;
    font-weight: 800;
}}
QLabel#profileIcon {{
    border-radius: 9px;
    font-size: 15px;
}}


/* --- secciones colapsables --- */
QFrame#collapsible {{
    background-color: {COLORS['mantle']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 12px;
}}
/* --- terminal (pagina Registro): monoespaciada y en verde, a proposito --
   es la unica pantalla de la app que se permite verse "tecnica" sin
   vueltas, porque es exactamente lo que es: el comando real y su salida
   cruda. */
QFrame#terminal {{
    background-color: #05070a;
    border: 1px solid #113322;
    border-radius: 12px;
}}
QPlainTextEdit#terminalView {{
    background-color: transparent;
    border: none;
    color: {COLORS['term_green']};
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace;
    font-size: 12.5px;
    selection-background-color: {COLORS['term_green']};
    selection-color: #05070a;
}}

/* =====================  v1.1: refinamientos  ===================== */
QLabel, QCheckBox, QRadioButton {{
    background: transparent;
}}
QCheckBox::indicator:checked {{
    background: {GRADIENT_PRIMARY};
    border: 1px solid {COLORS['accent']};
    image: url({ASSETS.as_posix()}/check.svg);
}}
QCheckBox::indicator:disabled {{
    background-color: {COLORS['mantle']};
    border: 1px solid {COLORS['surface0']};
}}
QCheckBox:disabled {{
    color: {COLORS['subtext']};
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox::drop-down {{
    border: none;
    width: 28px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    image: url({ASSETS.as_posix()}/chevron-down.svg);
    width: 14px;
    height: 14px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface2']};
    border-radius: 8px;
    padding: 4px;
    outline: 0;
    selection-background-color: {COLORS['accent_soft']};
    selection-color: {COLORS['text']};
}}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {{
    color: {COLORS['surface3']};
    background-color: {COLORS['mantle']};
    border: 1px solid {COLORS['surface0']};
}}
QSpinBox {{
    padding-right: 22px;
}}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border: none;
    background: transparent;
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border: none;
    background: transparent;
}}
QSpinBox::up-arrow {{
    image: url({ASSETS.as_posix()}/chevron-up.svg);
    width: 10px;
    height: 10px;
}}
QSpinBox::down-arrow {{
    image: url({ASSETS.as_posix()}/chevron-down.svg);
    width: 10px;
    height: 10px;
}}
QMenu {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface2']};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 20px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {COLORS['accent_soft']};
}}
QMenu::separator {{
    height: 1px;
    background: {COLORS['surface1']};
    margin: 5px 8px;
}}
QStatusBar {{
    background-color: {COLORS['crust']};
    color: {COLORS['subtext']};
    border-top: 1px solid {COLORS['surface0']};
    font-size: 12px;
}}
QStatusBar::item {{
    border: none;
}}
QDialog {{
    background-color: {COLORS['base']};
}}

/* --- estado de la barra superior --- */
QFrame#statusChip[state="live"] {{
    background-color: {COLORS['accent_soft']};
    border: 1px solid {COLORS['accent_border']};
}}

/* --- tarjeta principal del dispositivo --- */
QFrame#heroCard {{
    background-color: {COLORS['mantle']};
    border: 1px solid {COLORS['surface0']};
    border-radius: 16px;
}}
QFrame#heroIcon {{
    background-color: {COLORS['accent_soft']};
    border: 1px solid {COLORS['accent_border']};
    border-radius: 14px;
}}
QFrame#heroIcon[connected="false"] {{
    background-color: {COLORS['surface0']};
    border: 1px solid {COLORS['surface1']};
}}
QLabel#heroTitle {{
    font-size: 18px;
    font-weight: 800;
}}
QLabel#heroSub {{
    color: {COLORS['subtext']};
    font-size: 12px;
}}
QFrame#telemetryChip {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 10px;
}}
QFrame#telemetryChip[level="warn"] {{
    border: 1px solid {COLORS['yellow']};
}}
QFrame#telemetryChip[level="hot"] {{
    border: 1px solid {COLORS['red']};
}}
QLabel#telemetryText {{
    font-size: 12px;
    font-weight: 700;
}}
QLabel#sectionTitle {{
    color: {COLORS['subtext']};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}

/* --- avisos (banner) --- */
QFrame#banner {{
    background-color: rgba(242, 189, 78, 26);
    border: 1px solid rgba(242, 189, 78, 115);
    border-radius: 12px;
}}
QFrame#banner[kind="error"] {{
    background-color: rgba(255, 85, 102, 26);
    border: 1px solid rgba(255, 85, 102, 115);
}}
QLabel#bannerText {{
    font-size: 12px;
}}

/* --- botones --- */
QListWidget {{
    background-color: {COLORS['field']};
    border: 1px solid {COLORS['surface1']};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 5px 6px;
    border-radius: 6px;
}}
QListWidget::item:selected {{
    background-color: {COLORS['accent_soft']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['accent_border']};
}}
QListWidget::item:hover:!selected {{
    background-color: {COLORS['surface0']};
}}
QPushButton#ghost {{
    background-color: transparent;
    border: none;
    color: {COLORS['subtext']};
    padding: 6px 8px;
    font-weight: 600;
}}
QPushButton#ghost:hover {{
    color: {COLORS['text']};
    background-color: {COLORS['surface0']};
}}
QPushButton#iconBtn {{
    padding: 9px 10px;
}}
QPushButton#danger {{
    color: {COLORS['red']};
}}
QPushButton#recordToggle:checked {{
    background-color: {COLORS['accent_soft']};
    border: 1px solid {COLORS['accent']};
    color: {COLORS['red']};
}}

/* --- acordeón --- */
QPushButton#accHeader {{
    background-color: transparent;
    border: none;
    border-radius: 12px;
    padding: 0;
    text-align: left;
}}
QPushButton#accHeader:hover {{
    background-color: {COLORS['surface0']};
}}
QLabel#accTitle {{
    font-size: 13px;
    font-weight: 700;
}}
QLabel#accBadge {{
    color: {COLORS['subtext']};
    font-size: 11px;
}}
"""


def apply_shadow(widget: QWidget, blur: int = 28, alpha: int = 70) -> None:
    """Sombra sutil para dar sensación de tarjeta elevada."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, 3)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
