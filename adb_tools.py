"""
Utilidades de ADB que no pertenecen a la detección del setup: listar los
celulares conectados (con su modelo), leer batería/temperatura, tomar
capturas de pantalla y consultar versiones de adb/scrcpy.

Todo es síncrono y pensado para correr dentro de un QThread (la GUI) o
directamente desde la CLI.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Valores de BatteryManager.BATTERY_STATUS_* que devuelve `dumpsys battery`.
_BATTERY_STATUS = {
    1: "desconocido",
    2: "cargando",
    3: "descargando",
    4: "sin cargar",
    5: "completa",
}


class AdbToolError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Ejecución
# --------------------------------------------------------------------------
def _require(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise AdbToolError(
            f"No se encontró '{binary}' en el PATH. Instálalo desde la "
            f"sección Dependencias."
        )
    return path


def _run(cmd: list[str], timeout: float = 15, binary: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=not binary, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise AdbToolError(f"No se pudo ejecutar: {' '.join(cmd)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbToolError(f"Se agotó el tiempo de espera: {' '.join(cmd)}") from exc


# --------------------------------------------------------------------------
# Lista de dispositivos
# --------------------------------------------------------------------------
@dataclass
class DeviceEntry:
    serial: str
    state: str  # device | unauthorized | offline | ...
    model: str = ""
    transport: str = "usb"  # usb | wifi

    @property
    def is_ready(self) -> bool:
        return self.state == "device"

    @property
    def label(self) -> str:
        name = self.model or "Android"
        via = "WiFi" if self.transport == "wifi" else "USB"
        suffix = "" if self.is_ready else f" · {self.state_label}"
        return f"{name} · {via}{suffix}"

    @property
    def state_label(self) -> str:
        return {
            "unauthorized": "sin autorizar",
            "offline": "sin conexión",
        }.get(self.state, self.state)


_WIRELESS_SERIAL_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$")


def parse_devices(output: str) -> list[DeviceEntry]:
    """Parsea `adb devices -l`. Ignora las líneas de arranque del demonio
    ('* daemon started...') y cualquier cosa antes del encabezado."""
    devices: list[DeviceEntry] = []
    started = False
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        if line.lower().startswith("list of devices"):
            started = True
            continue
        if not started:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        extras = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        model = extras.get("model", "").replace("_", " ").strip()
        transport = "wifi" if _WIRELESS_SERIAL_RE.match(serial) else "usb"
        devices.append(DeviceEntry(serial=serial, state=state, model=model, transport=transport))
    return devices


def list_devices() -> list[DeviceEntry]:
    _require("adb")
    result = _run(["adb", "devices", "-l"])
    return parse_devices(result.stdout or "")


# --------------------------------------------------------------------------
# Batería y temperatura
# --------------------------------------------------------------------------
@dataclass
class BatteryInfo:
    level: int | None = None
    temperature_c: float | None = None
    status_code: int | None = None
    powered: bool = False
    extras: dict = field(default_factory=dict)

    @property
    def status_text(self) -> str:
        return _BATTERY_STATUS.get(self.status_code or 0, "")

    @property
    def is_charging(self) -> bool:
        return self.status_code == 2

    @property
    def is_hot(self) -> bool:
        return self.temperature_c is not None and self.temperature_c >= 42.0

    def summary(self) -> str:
        parts = []
        if self.level is not None:
            bolt = " ⚡" if self.is_charging else ""
            parts.append(f"Batería {self.level}%{bolt}")
        if self.temperature_c is not None:
            parts.append(f"{self.temperature_c:.1f} °C")
        return " · ".join(parts)


def parse_battery(output: str) -> BatteryInfo:
    """Parsea la salida de `adb shell dumpsys battery`."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip().lower()] = value.strip()

    def as_int(key: str) -> int | None:
        try:
            return int(values[key])
        except (KeyError, ValueError):
            return None

    level = as_int("level")
    scale = as_int("scale") or 100
    if level is not None and scale not in (0, 100):
        level = round(level * 100 / scale)
    temp_tenths = as_int("temperature")
    powered = any(
        values.get(key, "").lower() == "true"
        for key in ("ac powered", "usb powered", "wireless powered", "dock powered")
    )
    return BatteryInfo(
        level=level,
        temperature_c=(temp_tenths / 10.0) if temp_tenths is not None else None,
        status_code=as_int("status"),
        powered=powered,
    )


def battery(serial: str) -> BatteryInfo:
    _require("adb")
    result = _run(["adb", "-s", serial, "shell", "dumpsys", "battery"], timeout=10)
    info = parse_battery(result.stdout or "")
    if info.level is None and info.temperature_c is None:
        raise AdbToolError("El celular no devolvió datos de batería.")
    return info


# --------------------------------------------------------------------------
# Captura de pantalla
# --------------------------------------------------------------------------
def _unique_path(directory: Path, prefix: str, suffix: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = directory / f"{prefix}_{stamp}{suffix}"
    counter = 2
    while path.exists():
        path = directory / f"{prefix}_{stamp}_{counter}{suffix}"
        counter += 1
    return path


def timestamped_path(directory: Path, prefix: str, suffix: str) -> Path:
    """Ruta nueva con fecha y hora, sin pisar archivos existentes."""
    return _unique_path(directory, prefix, suffix)


def screenshot(serial: str, directory: Path, prefix: str = "NexoHub") -> Path:
    """Toma una captura con `adb exec-out screencap -p` y la guarda como PNG.

    Ojo: las apps que marcan su ventana como segura (FLAG_SECURE: banca,
    DRM, algunos juegos) salen en negro; es una restricción de Android."""
    _require("adb")
    result = _run(
        ["adb", "-s", serial, "exec-out", "screencap", "-p"], timeout=25, binary=True
    )
    data = result.stdout or b""
    if result.returncode != 0 or not data.startswith(PNG_MAGIC):
        stderr = (result.stderr or b"").decode(errors="replace").strip()
        raise AdbToolError(
            "No se pudo capturar la pantalla del celular"
            + (f": {stderr}" if stderr else ".")
        )
    directory.mkdir(parents=True, exist_ok=True)
    path = _unique_path(directory, prefix, ".png")
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------
# Versiones (para "Acerca de" y reportes de errores)
# --------------------------------------------------------------------------
def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def scrcpy_version() -> str | None:
    if shutil.which("scrcpy") is None:
        return None
    try:
        result = _run(["scrcpy", "--version"], timeout=8)
    except AdbToolError:
        return None
    line = _first_line(result.stdout or result.stderr or "")
    match = re.search(r"scrcpy\s+(\S+)", line)
    return match.group(1) if match else (line or None)


def adb_version() -> str | None:
    if shutil.which("adb") is None:
        return None
    try:
        result = _run(["adb", "version"], timeout=8)
    except AdbToolError:
        return None
    match = re.search(r"Android Debug Bridge version\s+(\S+)", result.stdout or "")
    return match.group(1) if match else (_first_line(result.stdout or "") or None)
