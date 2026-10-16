"""
Íconos vectoriales (SVG en línea) de NexoHub.

Reemplazan a los emojis de antes: un emoji se dibuja distinto según la
fuente instalada (o no se dibuja), mientras que estos íconos salen igual de
nítidos en cualquier distro, a cualquier escala y con el color del tema.
Estilo: trazo de 1.8 px sobre una grilla de 24×24, extremos redondeados.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
LOGO_PATH = ASSETS_DIR / "nexohub.svg"

# Cada ícono es el interior de un <svg viewBox="0 0 24 24">. "currentColor"
# se reemplaza por el color pedido; los elementos con fill="currentColor"
# son sólidos, el resto se dibuja solo con trazo.
_BODIES: dict[str, str] = {
    "phone": (
        '<rect x="7" y="2.5" width="10" height="19" rx="2.4"/>'
        '<path d="M10.8 18.4h2.4"/>'
    ),
    "monitor": '<rect x="3" y="4" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7M12 16.5v4"/>',
    "volume": (
        '<path d="M4 9.5v5h3.6l4.4 4V5.5l-4.4 4H4z"/>'
        '<path d="M15.6 9.2a4 4 0 010 5.6M18.3 6.6a7.7 7.7 0 010 10.8"/>'
    ),
    "keyboard": (
        '<rect x="2.5" y="6" width="19" height="12" rx="2.2"/>'
        '<path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M7.5 14h9"/>'
    ),
    "sliders": (
        '<path d="M4 7h9M19 7h1M4 17h1M11 17h9"/>'
        '<circle cx="16" cy="7" r="2.2"/><circle cx="8" cy="17" r="2.2"/>'
    ),
    "terminal": (
        '<rect x="3" y="4.5" width="18" height="15" rx="2.2"/>'
        '<path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/>'
    ),
    "package": (
        '<path d="M12 3l8 4.4v9.2L12 21l-8-4.4V7.4L12 3z"/>'
        '<path d="M4.3 7.6L12 12l7.7-4.4M12 12v8.8"/>'
    ),
    "camera": (
        '<path d="M4 8h3l1.6-2.5h6.8L17 8h3a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1V9a1 1 0 011-1z"/>'
        '<circle cx="12" cy="13" r="3.6"/>'
    ),
    "record": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.6" fill="currentColor"/>',
    "play": '<path d="M7.5 4.8v14.4a.6.6 0 00.9.5l11.6-7.2a.6.6 0 000-1L8.4 4.3a.6.6 0 00-.9.5z" fill="currentColor"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.4" fill="currentColor"/>',
    "wifi": (
        '<path d="M2.8 9.3a13.2 13.2 0 0118.4 0M5.9 12.7a8.8 8.8 0 0112.2 0M9 16.1a4.4 4.4 0 016 0"/>'
        '<circle cx="12" cy="19.4" r="1.1" fill="currentColor"/>'
    ),
    "usb": (
        '<path d="M12 3v14.5"/><circle cx="12" cy="19.5" r="1.6"/>'
        '<path d="M12 11.5L8 9.2v-2.2M12 8.5l4-2.3"/>'
        '<rect x="6.6" y="5" width="2.8" height="2.6" rx=".4"/>'
        '<circle cx="16.4" cy="5.7" r="1.4"/>'
    ),
    "battery": '<rect x="2.5" y="7.5" width="16" height="9.5" rx="2.2"/><path d="M21.2 10.8v2.8"/>',
    "thermometer": '<path d="M10 14.4V5.6a2 2 0 014 0v8.8a4 4 0 11-4 0z"/><path d="M12 9.5v6"/>',
    "refresh": (
        '<path d="M20 5.5V10h-4.5"/><path d="M4 18.5V14h4.5"/>'
        '<path d="M5.7 9.3A7.5 7.5 0 0118.6 7.4L20 10M18.3 14.7A7.5 7.5 0 015.4 16.6L4 14"/>'
    ),
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.7h.01"/>',
    "power": '<path d="M12 3.5v8.5"/><path d="M6.6 6.9a7.8 7.8 0 1010.8 0"/>',
    "folder": '<path d="M3 7.2A2.2 2.2 0 015.2 5h3.6l2.2 2.6h7.8A2.2 2.2 0 0121 9.8v7A2.2 2.2 0 0118.8 19H5.2A2.2 2.2 0 013 16.8V7.2z"/>',
    "copy": (
        '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.2"/>'
        '<path d="M15.5 8.5V6.2a2 2 0 00-2-2H6.2a2 2 0 00-2 2v7.3a2 2 0 002 2h2.3"/>'
    ),
    "save": (
        '<path d="M5 4h11l3 3v12a1 1 0 01-1 1H6a1 1 0 01-1-1V4z"/>'
        '<path d="M8 4v5h7V4M8 20v-6h8v6"/>'
    ),
    "trash": '<path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l.9 12.5h9.2L17.5 7M10 11v5.5M14 11v5.5"/>',
    "check": '<path d="M4.5 12.5l5 5 10-11"/>',
    "alert": '<path d="M12 3.8L2.8 19.6h18.4L12 3.8z"/><path d="M12 10v4.6M12 17.4h.01"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "chevron_right": '<path d="M9 5.5l6.5 6.5L9 18.5"/>',
    "chevron_down": '<path d="M5.5 9l6.5 6.5L18.5 9"/>',
    "gamepad": (
        '<path d="M7.2 7h9.6a4.6 4.6 0 014.5 5.5l-.8 4a2.5 2.5 0 01-4.2 1.3L14.4 16H9.6l-1.9 1.8a2.5 2.5 0 01-4.2-1.3l-.8-4A4.6 4.6 0 017.2 7z"/>'
        '<path d="M8 10.2v3.2M6.4 11.8h3.2"/><path d="M15.4 10.8h.01M17.4 12.6h.01"/>'
    ),
    "zap": '<path d="M13 2.8L5 13.5h6l-1 7.7 8-10.7h-6l1-7.7z"/>',
    "broadcast": (
        '<circle cx="12" cy="12" r="2"/>'
        '<path d="M7.8 7.8a6 6 0 000 8.4M16.2 7.8a6 6 0 010 8.4M4.9 4.9a10.1 10.1 0 000 14.2M19.1 4.9a10.1 10.1 0 010 14.2"/>'
    ),
    "diamond": '<path d="M12 3.5L21 12l-9 8.5L3 12l9-8.5z"/><path d="M3 12h18"/>',
    "star": '<path d="M12 3.6l2.5 5.3 5.8.8-4.2 4.1 1 5.8-5.1-2.8-5.1 2.8 1-5.8-4.2-4.1 5.8-.8L12 3.6z"/>',
    "window": '<rect x="3" y="4.5" width="18" height="15" rx="2.2"/><path d="M3 9h18M6.5 6.8h.01M9 6.8h.01"/>',
    "cursor": '<path d="M5 3.5l13 6.2-5.6 1.7L10.8 17 5 3.5z"/>',
    "film": (
        '<rect x="3" y="4.5" width="18" height="15" rx="2.2"/>'
        '<path d="M8 4.5v15M16 4.5v15M3 9h5M3 15h5M16 9h5M16 15h5"/>'
    ),
}

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

_cache: dict[tuple, QPixmap] = {}


def available() -> list[str]:
    return sorted(_BODIES)


def svg_source(name: str, color: str, width: float = 1.8) -> str:
    body = _BODIES[name].replace("currentColor", color)
    return _SVG_TEMPLATE.format(color=color, width=width, body=body)


def _device_pixel_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _render(svg: str, size: int) -> QPixmap:
    ratio = _device_pixel_ratio()
    px = max(1, round(size * ratio))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def pixmap(name: str, color: str, size: int = 18, width: float = 1.8) -> QPixmap:
    key = (name, color, size, width)
    if key not in _cache:
        _cache[key] = _render(svg_source(name, color, width), size)
    return _cache[key]


def icon(name: str, color: str, size: int = 18, disabled_color: str | None = None) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal)
    if disabled_color:
        result.addPixmap(pixmap(name, disabled_color, size), QIcon.Mode.Disabled)
    return result


def logo_pixmap(size: int = 64) -> QPixmap:
    key = ("__logo__", size)
    if key not in _cache:
        _cache[key] = _render(LOGO_PATH.read_text(encoding="utf-8"), size)
    return _cache[key]


def app_icon() -> QIcon:
    """Ícono de la aplicación (ventana, barra de tareas, 'Acerca de')."""
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(logo_pixmap(size))
    return result
