"""
El mapa de teclas: qué tecla (o botón del mouse) toca qué parte de la
pantalla del celular.

Las posiciones se guardan normalizadas (0 a 1, relativas al ancho y alto de
la pantalla tal como se ve, normalmente en horizontal), así el mismo mapa
sirve con cualquier resolución, incluso si cambias la del celular desde
NexoHub. Las teclas se nombran como en evdev (``KEY_W``, ``BTN_LEFT``…); la rueda del
mouse son ``WHEEL_UP`` y ``WHEEL_DOWN`` (un toque corto por cada «clic» de la rueda).

Tipos de control:

  tap       una tecla mantiene apretado un punto (disparar, saltar, recargar…)
  joystick  cuatro teclas (WASD) mueven el stick virtual de movimiento
  aim       el mouse mueve la cámara arrastrando un dedo virtual (apuntar).
            ``suspend_key`` congela la cámara (el mouse sigue capturado) mientras
            está apretada (hold) o hasta apretarla otra vez (toggle)

Teclas especiales (por mapa, cualquier tecla o botón del mouse):

  editor_key   abre el editor del mapa (F10)
  toggle_key   activa/desactiva el mapeo (F9)
  suspend_key  pausa/reanuda el mapeo sin desactivarlo (F8)
  cursor_key   suelta el cursor para tocar menús (Alt). ``cursor_mode``:
               auto (toque corto alterna, mantenida es temporal), toggle o hold
  overlay_key  muestra/oculta las teclas sobre el juego (F7)
  quick_key    abre los ajustes rápidos (sensibilidad en vivo) en pleno juego (F6)

Los mapas viven en ``~/.config/nexohub/keymaps/<nombre>.yaml``; los de fábrica
(``keymap/presets/``) se usan como punto de partida y nunca se modifican.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import config_store

PRESETS_DIR = Path(__file__).resolve().parent / "presets"

KINDS = ("tap", "joystick", "aim")
JOYSTICK_DIRECTIONS = ("up", "left", "down", "right")
SPECIAL_KEYS = {
    # atributo: (tecla por defecto, descripción)
    "editor_key": ("KEY_F10", "abrir el editor"),
    "toggle_key": ("KEY_F9", "activar/desactivar"),
    "suspend_key": ("KEY_F8", "pausar"),
    "cursor_key": ("KEY_LEFTALT", "mostrar cursor"),
    "overlay_key": ("KEY_F7", "mostrar/ocultar teclas"),
    "quick_key": ("KEY_F6", "ajustes rápidos"),
}
DEFAULT_TAP_SIZE = 0.045
DEFAULT_AIM_RADIUS = 0.45
DEFAULT_AIM_SENSITIVITY = 0.5
CURSOR_MODES = ("auto", "toggle", "hold")
SUSPEND_MODES = ("hold", "toggle")
DEFAULT_AIM_RATE = 125  # Hz a los que se manda la cámara al celular
AIM_RATE_RANGE = (30, 500)
# Valores de fábrica de la primera versión, que hacían la cámara inusable
# (el dedo llegaba al borde de su zona enseguida y se reapoyaba sin parar).
_V1_AIM = {"radius": 0.3, "sensitivity": 1.0}


_KEY_LABELS = {
    "BTN_LEFT": "Clic izq.",
    "BTN_RIGHT": "Clic der.",
    "BTN_MIDDLE": "Clic medio",
    "BTN_SIDE": "Mouse atrás",
    "BTN_EXTRA": "Mouse adelante",
    "WHEEL_UP": "Rueda ↑",
    "WHEEL_DOWN": "Rueda ↓",
    "KEY_SPACE": "Espacio",
    "KEY_LEFTALT": "Alt",
    "KEY_RIGHTALT": "AltGr",
    "KEY_LEFTCTRL": "Ctrl",
    "KEY_RIGHTCTRL": "Ctrl der.",
    "KEY_LEFTSHIFT": "Shift",
    "KEY_RIGHTSHIFT": "Shift der.",
    "KEY_TAB": "Tab",
    "KEY_CAPSLOCK": "Bloq Mayús",
    "KEY_ENTER": "Enter",
    "KEY_ESC": "Esc",
    "KEY_BACKSPACE": "Retroceso",
    "KEY_GRAVE": "º",
    "KEY_SEMICOLON": "Ñ",
    "KEY_UP": "↑",
    "KEY_DOWN": "↓",
    "KEY_LEFT": "←",
    "KEY_RIGHT": "→",
}


def key_label(name: str) -> str:
    """Nombre corto y legible de una tecla evdev (``KEY_W`` -> ``W``)."""
    if not name:
        return "—"
    if name in _KEY_LABELS:
        return _KEY_LABELS[name]
    if name.startswith("KEY_KP"):
        return "Num " + name[6:].title()
    for prefix in ("KEY_", "BTN_"):
        if name.startswith(prefix):
            rest = name[len(prefix):]
            return rest if len(rest) <= 3 else rest.title()
    return name


class KeymapError(RuntimeError):
    pass


@dataclass
class Control:
    kind: str
    x: float
    y: float
    name: str = ""
    key: str = ""  # tap
    keys: dict[str, str] = field(default_factory=dict)  # joystick: up/left/down/right
    # joystick: largo del stick; aim: cuánto puede arrastrar el dedo antes de
    # levantarlo y volver al centro. Fracción del alto de la pantalla.
    radius: float = 0.12
    # aim: píxeles del celular por unidad de movimiento del mouse.
    sensitivity: float = DEFAULT_AIM_SENSITIVITY  # horizontal
    sensitivity_y: float = DEFAULT_AIM_SENSITIVITY  # vertical
    size: float = DEFAULT_TAP_SIZE  # tap: tamaño del círculo en pantalla (solo visual)
    suspend_key: str = ""  # aim: tecla (o botón del mouse) que congela la cámara
    suspend_mode: str = "hold"  # aim: hold | toggle

    @property
    def bound_keys(self) -> list[str]:
        if self.kind == "tap":
            return [self.key] if self.key else []
        if self.kind == "joystick":
            return [k for k in self.keys.values() if k]
        if self.kind == "aim" and self.suspend_key:
            return [self.suspend_key]
        return []

    def to_dict(self) -> dict:
        data = {"kind": self.kind, "name": self.name, "x": round(self.x, 4), "y": round(self.y, 4)}
        if self.kind == "tap":
            data["key"] = self.key
            data["size"] = round(self.size, 4)
        elif self.kind == "joystick":
            data["keys"] = dict(self.keys)
            data["radius"] = round(self.radius, 4)
        elif self.kind == "aim":
            data["radius"] = round(self.radius, 4)
            data["sensitivity"] = round(self.sensitivity, 3)
            data["sensitivity_y"] = round(self.sensitivity_y, 3)
            if self.suspend_key:
                data["suspend_key"] = self.suspend_key
                data["suspend_mode"] = self.suspend_mode
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Control":
        kind = str(data.get("kind", "tap"))
        if kind not in KINDS:
            raise KeymapError(f"Tipo de control desconocido: {kind!r} (válidos: {', '.join(KINDS)}).")
        try:
            control = cls(
                kind=kind,
                x=float(data["x"]),
                y=float(data["y"]),
                name=str(data.get("name") or ""),
                key=str(data.get("key") or ""),
                keys={str(k): str(v) for k, v in (data.get("keys") or {}).items() if v},
                radius=float(data.get("radius", DEFAULT_AIM_RADIUS if kind == "aim" else 0.12)),
                sensitivity=float(data.get("sensitivity", DEFAULT_AIM_SENSITIVITY)),
                size=float(data.get("size", DEFAULT_TAP_SIZE)),
                suspend_key=str(data.get("suspend_key") or ""),
                suspend_mode=str(data.get("suspend_mode") or "hold"),
            )
            if control.suspend_mode not in SUSPEND_MODES:
                control.suspend_mode = "hold"
            control.sensitivity_y = float(data.get("sensitivity_y", control.sensitivity))
        except (KeyError, TypeError, ValueError) as exc:
            raise KeymapError(f"Control mal formado: {data!r}") from exc
        if kind == "aim" and "sensitivity_y" not in data and all(
            float(data.get(k, -1)) == v for k, v in _V1_AIM.items()
        ):
            # Mapa guardado por la primera versión con la cámara de fábrica.
            control.radius = DEFAULT_AIM_RADIUS
            control.sensitivity = control.sensitivity_y = DEFAULT_AIM_SENSITIVITY
        control.x = min(max(control.x, 0.0), 1.0)
        control.y = min(max(control.y, 0.0), 1.0)
        return control


@dataclass
class Keymap:
    name: str
    title: str = ""
    package: str = ""  # app de Android a la que está pensado (informativo)
    editor_key: str = SPECIAL_KEYS["editor_key"][0]
    toggle_key: str = SPECIAL_KEYS["toggle_key"][0]
    suspend_key: str = SPECIAL_KEYS["suspend_key"][0]
    cursor_key: str = SPECIAL_KEYS["cursor_key"][0]
    overlay_key: str = SPECIAL_KEYS["overlay_key"][0]
    quick_key: str = SPECIAL_KEYS["quick_key"][0]
    overlay_opacity: float = 0.7  # opacidad de las teclas dibujadas sobre el juego
    cursor_mode: str = "auto"  # auto | toggle | hold (ver arriba)
    aim_rate: int = DEFAULT_AIM_RATE  # envíos de cámara por segundo (menos = no atrasa al celular)
    controls: list[Control] = field(default_factory=list)

    def special_keys(self) -> dict[str, str]:
        return {attr: getattr(self, attr) for attr in SPECIAL_KEYS}

    @property
    def display_name(self) -> str:
        return self.title or self.name

    def aim(self) -> Control | None:
        return next((c for c in self.controls if c.kind == "aim"), None)

    def conflicts(self) -> list[str]:
        """Teclas asignadas a más de un control (o a las teclas especiales)."""
        seen: dict[str, str] = {}
        problems = []
        reserved: dict[str, str] = {}
        for attr, (_default, label) in SPECIAL_KEYS.items():
            key = getattr(self, attr)
            if not key:
                continue
            if key in reserved:
                problems.append(f"{key} es la tecla para {reserved[key]} y también para {label}.")
            reserved[key] = label
        for control in self.controls:
            for key in control.bound_keys:
                label = control.name or control.kind
                if key in reserved:
                    problems.append(f"{key} está en «{label}» y también es la tecla para {reserved[key]}.")
                elif key in seen:
                    problems.append(f"{key} está en «{seen[key]}» y en «{label}».")
                else:
                    seen[key] = label
        if len(self.controls) and sum(c.kind == "aim" for c in self.controls) > 1:
            problems.append("Hay más de una zona de cámara (aim); solo se usa la primera.")
        return problems

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "package": self.package,
            **self.special_keys(),
            "overlay_opacity": round(self.overlay_opacity, 2),
            "aim_rate": int(self.aim_rate),
            "cursor_mode": self.cursor_mode,
            "controls": [c.to_dict() for c in self.controls],
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Keymap":
        if not isinstance(data, dict):
            raise KeymapError(f"El mapa «{name}» no tiene el formato esperado.")
        keys = {attr: str(data.get(attr) or default) for attr, (default, _label) in SPECIAL_KEYS.items()}
        if "editor_key" not in data and data.get("toggle_key") == "KEY_F10":
            # En la primera versión F10 activaba el mapeo; ahora abre el
            # editor y activar/desactivar pasó a F9.
            keys["toggle_key"] = SPECIAL_KEYS["toggle_key"][0]
        try:
            opacity = float(data.get("overlay_opacity", 0.7))
        except (TypeError, ValueError):
            opacity = 0.7
        try:
            rate = int(data.get("aim_rate", DEFAULT_AIM_RATE))
        except (TypeError, ValueError):
            rate = DEFAULT_AIM_RATE
        return cls(
            name=name,
            aim_rate=min(max(rate, AIM_RATE_RANGE[0]), AIM_RATE_RANGE[1]),
            cursor_mode=str(data.get("cursor_mode")) if data.get("cursor_mode") in CURSOR_MODES else "auto",
            title=str(data.get("title") or ""),
            package=str(data.get("package") or ""),
            overlay_opacity=min(max(opacity, 0.1), 1.0),
            controls=[Control.from_dict(c) for c in (data.get("controls") or [])],
            **keys,
        )


# --------------------------------------------------------------------------
# Archivos
# --------------------------------------------------------------------------
def user_dir() -> Path:
    return config_store.config_home() / "keymaps"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "mapa"


def _read(path: Path) -> Keymap:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise KeymapError(f"No se pudo leer {path}: {exc}") from exc
    return Keymap.from_dict(path.stem, data)


def available() -> list[str]:
    """Nombres de todos los mapas: los del usuario y los de fábrica."""
    names = {p.stem for p in PRESETS_DIR.glob("*.yaml")}
    if user_dir().is_dir():
        names |= {p.stem for p in user_dir().glob("*.yaml")}
    return sorted(names)


def is_preset(name: str) -> bool:
    return (PRESETS_DIR / f"{name}.yaml").is_file()


def has_user_copy(name: str) -> bool:
    return (user_dir() / f"{name}.yaml").is_file()


def load(name: str) -> Keymap:
    """La copia del usuario gana sobre la de fábrica."""
    for path in (user_dir() / f"{name}.yaml", PRESETS_DIR / f"{name}.yaml"):
        if path.is_file():
            return _read(path)
    raise KeymapError(f"No existe el mapa de teclas «{name}».")


def load_preset(name: str) -> Keymap:
    path = PRESETS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise KeymapError(f"«{name}» no es un mapa de fábrica.")
    return _read(path)


def save(keymap: Keymap) -> Path:
    directory = user_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{keymap.name}.yaml"
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".keymap-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# Mapa de teclas de NexoHub (lo escribe el editor; se puede editar a mano).\n")
            yaml.safe_dump(keymap.to_dict(), fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def delete_user_copy(name: str) -> bool:
    path = user_dir() / f"{name}.yaml"
    if path.is_file():
        path.unlink()
        return True
    return False
