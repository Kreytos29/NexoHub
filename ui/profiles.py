"""Selector de perfiles de uso: tarjetas de fábrica + perfiles propios."""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from . import icons
from .theme import COLORS
from .widgets import ProfileCard, reflow_grid, section_title

CUSTOM_PROFILE_KEY = "__custom__"

# (clave, ícono, título, descripción corta, tooltip largo, color de acento)
PROFILE_DEFS = [
    (
        CUSTOM_PROFILE_KEY, "sliders", "Personalizado",
        "Ajustas todo a mano en los apartados de abajo.",
        "No aplica ningún preset. Los valores son los que dejaste (o los "
        "que traiga tu configuración) en los apartados de abajo.",
        "surface2",
    ),
    (
        "competitive", "zap", "Competitivo",
        "Mínima latencia para juegos rápidos.",
        "Prioriza responsividad sobre calidad de imagen: bitrate y "
        "resolución más bajos, y fuerza frames clave frecuentes para "
        "reducir el 'ghosting' en movimientos rápidos. Pensado para "
        "juegos donde cada milisegundo de delay importa.",
        "accent",
    ),
    (
        "streaming", "broadcast", "Directo / Streaming",
        "Buena calidad para el espectador, sin recalentar el celular.",
        "Pensado para transmitir en vivo: mejor calidad de imagen que "
        "'Competitivo', pero usando h264 (más liviano para el encoder "
        "del celular que h265) y sin el flag agresivo de frames clave, "
        "para no exigir tanto al celular ni acortar su batería durante "
        "sesiones largas.",
        "gold",
    ),
    (
        "quality", "diamond", "Calidad",
        "Máxima nitidez, sin importar el bitrate.",
        "Prioriza la nitidez de imagen por sobre todo lo demás: usa "
        "h265 y el bitrate/resolución más altos. Ideal si vas a grabar "
        "o si tu conexión USB y celular aguantan sin problema.",
        "teal",
    ),
]
BUILTIN_KEYS = {key for key, *_ in PROFILE_DEFS}

COLUMNS = 4


class ProfileSelector(QWidget):
    selected = pyqtSignal(str)
    save_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.cards: dict[str, ProfileCard] = {}
        self.titles: dict[str, str] = {}
        self.current = CUSTOM_PROFILE_KEY
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(section_title("Perfil de uso"))
        header.addStretch(1)
        self.save_btn = QPushButton("Guardar como perfil…")
        self.save_btn.setObjectName("ghost")
        self.save_btn.setIcon(icons.icon("plus", COLORS["subtext"], 14, disabled_color=COLORS["surface3"]))
        self.save_btn.setToolTip(
            "Guarda los valores actuales (video, audio, entrada, ventana…) "
            "como un perfil propio que aparece aquí como una tarjeta más."
        )
        self.save_btn.clicked.connect(self.save_requested)
        header.addWidget(self.save_btn)
        root.addLayout(header)

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        root.addLayout(self.grid)

    def set_profiles(self, available: dict[str, dict], builtin_keys: set[str] | None = None) -> None:
        """Reconstruye las tarjetas. ``available`` es el mapa de perfiles de la
        config; los que no son de fábrica se muestran como perfiles propios."""
        builtin_keys = BUILTIN_KEYS if builtin_keys is None else builtin_keys
        for card in self.cards.values():
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        self.titles.clear()

        ordered: list[ProfileCard] = []
        for key, icon_name, title, desc, tooltip, tint in PROFILE_DEFS:
            if key != CUSTOM_PROFILE_KEY and key not in available:
                continue
            ordered.append(self._make_card(key, icon_name, title, desc, tooltip, tint, False))

        for key, profile in available.items():
            if key in BUILTIN_KEYS:
                continue
            label = profile.get("label") or key.replace("_", " ").title()
            deletable = key not in builtin_keys
            desc = profile.get("description") or ("Perfil propio." if deletable else "Perfil incluido.")
            tooltip = (
                f"Perfil propio «{label}». Clic derecho para eliminarlo."
                if deletable else f"Perfil incluido «{label}»."
            )
            ordered.append(self._make_card(key, "star", label, desc, tooltip, "teal", deletable))

        reflow_grid(self.grid, ordered, COLUMNS)
        self.select(self.current if self.current in self.cards else CUSTOM_PROFILE_KEY)

    def _make_card(self, key, icon_name, title, desc, tooltip, tint, deletable) -> ProfileCard:
        card = ProfileCard(key, icon_name, title, desc, tooltip, tint, deletable)
        card.clicked.connect(self.selected)
        card.delete_requested.connect(self.delete_requested)
        self.cards[key] = card
        self.titles[key] = title
        return card

    def select(self, key: str) -> None:
        if key not in self.cards:
            key = CUSTOM_PROFILE_KEY
        self.current = key
        for card_key, card in self.cards.items():
            card.set_selected(card_key == key)

    def title_of(self, key: str) -> str:
        return self.titles.get(key, "Personalizado")
