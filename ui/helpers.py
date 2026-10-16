"""Funciones puras (sin Qt) de la interfaz, para poder probarlas con pytest."""
from __future__ import annotations

import re
import unicodedata

# A partir de estos valores se avisa que la calidad va a mejorar pero el
# delay/fluidez pueden resentirse (USB 2.0, WiFi compartido, celulares de
# gama media, etc.).
FPS_WARN_THRESHOLD = 60
BITRATE_WARN_THRESHOLD_MBPS = 20.0
# Temperatura (°C) del celular a partir de la cual se avisa.
HOT_TEMP_C = 42.0

# Claves de config que forman parte de un perfil de uso.
PROFILE_KEYS = (
    "video_codec", "video_encoder", "bitrate_max", "max_size", "max_fps",
    "video_buffer", "fps_overlay", "fullscreen", "always_on_top", "borderless",
    "capture_orientation", "stay_awake", "turn_screen_off", "show_touches",
    "power_off_on_close", "input_mode", "gamepad", "audio_route",
    "audio_codec", "audio_bitrate", "audio_buffer", "extra_flags",
)
# Al aplicar un perfil, estas claves vuelven a "automático" antes de poner
# lo que el perfil defina (así un perfil no hereda el codec del anterior).
RESET_ON_PROFILE = ("video_codec", "video_encoder", "bitrate_max", "max_size", "extra_flags")

# Claves que no se guardan en config.yaml (son de una sola sesión).
SESSION_ONLY_KEYS = ("record", "record_path")


def parse_bitrate_mbps(text: str) -> float | None:
    """Convierte '32M', '500K' o un número puro en bps a Mbps. None si el
    texto está vacío o no se puede interpretar."""
    text = (text or "").strip().upper()
    if not text:
        return None
    try:
        if text.endswith("M"):
            return float(text[:-1])
        if text.endswith("K"):
            return float(text[:-1]) / 1000.0
        return float(text) / 1_000_000.0
    except ValueError:
        return None


def stream_warning_reasons(max_fps: int, bitrate_text: str) -> list[str]:
    """Motivos por los que conviene avisar de FPS/bitrate demasiado altos."""
    reasons = []
    if max_fps and max_fps > FPS_WARN_THRESHOLD:
        reasons.append(f"{max_fps} FPS")
    mbps = parse_bitrate_mbps(bitrate_text)
    if mbps is not None and mbps > BITRATE_WARN_THRESHOLD_MBPS:
        reasons.append(f"{mbps:.0f} Mbps de bitrate")
    return reasons


def slugify(name: str) -> str:
    """'Mi perfil ñoño!' -> 'mi_perfil_nono'. Vacío si no queda nada útil."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def describe_profile(values: dict) -> str:
    """Resumen corto de un perfil para su tarjeta: 'h264 · 6M · 1280 px · 60 FPS'."""
    parts = []
    if values.get("video_codec"):
        parts.append(str(values["video_codec"]))
    if values.get("bitrate_max"):
        parts.append(str(values["bitrate_max"]))
    if values.get("max_size"):
        parts.append(f"{values['max_size']} px")
    if values.get("max_fps"):
        parts.append(f"{values['max_fps']} FPS")
    return " · ".join(parts) or "Perfil propio"


def format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
