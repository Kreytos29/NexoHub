"""
Cambia la resolución de PANTALLA del celular vía ADB (`adb shell wm
size`) -- esto es DISTINTO de "Max size (px)"/--max-size de scrcpy, que
solo reescala la imagen ya capturada para que pese menos o entre en la
ventana de la PC, sin tocar en absoluto lo que Android cree que mide su
propia pantalla.

Cambiar esto le hace creer al sistema (y a los juegos, que muchas veces
adaptan su render interno al tamaño de pantalla que Android reporta)
que el celular tiene otra resolución real. Sirve, por ejemplo, para
igualar la resolución del celular a la del monitor de la PC -- un
monitor de 1920x1080 sugiere un celular a 1080x1920 (mismo panel, en
vertical), para que el juego ya renderice al tamaño final en vez de
que Android escale para un lado y scrcpy para el otro.

Riesgo: no es gratis. Una resolución no nativa puede hacer que
lanzadores/apps del sistema (y algunos juegos, que la detectan como
intento de ventaja) se vean o se comporten raro. Por eso esto siempre
se ofrece junto con `reset_size()`, para volver a la nativa con un
solo click.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


class ResolutionError(RuntimeError):
    pass


@dataclass
class ScreenSize:
    width: int
    height: int
    is_override: bool  # True si es una resolucion forzada, no el panel fisico

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


_SIZE_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


def _run(serial: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "wm", *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except FileNotFoundError as exc:
        raise ResolutionError("No se pudo ejecutar 'adb'.") from exc
    output = (result.stdout or "") + (result.stderr or "")
    return output


def _parse_sizes(output: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """`adb shell wm size` devuelve algo como:
        Physical size: 1080x2400
        Override size: 1920x1080
    La línea 'Override' solo aparece si hay una resolución forzada
    activa en este momento; si no, solo esta 'Physical'."""
    physical = None
    override = None
    for line in output.splitlines():
        match = _SIZE_RE.search(line)
        if not match:
            continue
        size = (int(match.group(1)), int(match.group(2)))
        lowered = line.lower()
        if "physical" in lowered:
            physical = size
        elif "override" in lowered:
            override = size
    return physical, override


def get_current(serial: str) -> ScreenSize:
    """La resolución que Android está usando AHORA MISMO: la forzada
    (override), si hay una activa, o si no la física del panel."""
    output = _run(serial, "size")
    physical, override = _parse_sizes(output)
    if override is not None:
        return ScreenSize(*override, is_override=True)
    if physical is not None:
        return ScreenSize(*physical, is_override=False)
    raise ResolutionError(
        f"No se pudo leer la resolución del celular: {output.strip()!r}"
    )


def get_native(serial: str) -> ScreenSize:
    """La resolución FÍSICA del panel, ignorando cualquier override
    activo -- la que hay que mostrar como 'nativa' para restablecer."""
    output = _run(serial, "size")
    physical, _override = _parse_sizes(output)
    if physical is not None:
        return ScreenSize(*physical, is_override=False)
    raise ResolutionError(
        f"No se pudo leer la resolución física del celular: {output.strip()!r}"
    )


def set_size(serial: str, width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ResolutionError("La resolución tiene que ser mayor a 0x0.")
    output = _run(serial, "size", f"{width}x{height}")
    lowered = output.lower()
    if "error" in lowered or "exception" in lowered:
        raise ResolutionError(output.strip() or "adb rechazó el cambio de resolución.")


def reset_size(serial: str) -> None:
    """Vuelve a la resolución física del panel (borra el override)."""
    output = _run(serial, "size", "reset")
    lowered = output.lower()
    if "error" in lowered or "exception" in lowered:
        raise ResolutionError(output.strip() or "adb rechazó el reset de resolución.")


def size_for_monitor(monitor_width: int, monitor_height: int) -> tuple[int, int]:
    """Sugiere una resolución de celular a partir de la del monitor de
    la PC: se invierte a vertical (lado corto de ancho, lado largo de
    alto) porque el celular se sostiene y se juega mayormente así,
    aunque el monitor sea horizontal -- ej. monitor 1920x1080 sugiere
    celular 1080x1920."""
    long_side = max(monitor_width, monitor_height)
    short_side = min(monitor_width, monitor_height)
    return short_side, long_side
