"""
Orquesta los detectores y devuelve el estado del setup. Lo usan tanto la
CLI (main.py) como la GUI (ui/), así que NO imprime ni llama a sys.exit:
los problemas salen como ``SetupError`` (con el motivo real, para mostrarlo
tal cual) y los avisos no fatales como ``SetupInfo.notes``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config_store
from detectors import android_device, cpu, monitor, usb_topology, wireless
from detectors.android_device import AndroidDevice
from detectors.cpu import CpuStatus
from detectors.monitor import Monitor
from detectors.usb_topology import UsbTopology

# Se re-exportan para que quien ya importaba "load_config" desde aquí o desde
# main.py siga funcionando.
load_config = config_store.load_config


class SetupError(RuntimeError):
    """No se pudo detectar algo imprescindible (celular o monitor)."""


class ProfileError(SetupError):
    """El perfil pedido no existe en la configuración."""


@dataclass
class SetupInfo:
    device: AndroidDevice
    usb: UsbTopology
    monitor: Monitor
    cpu: CpuStatus
    notes: list[str] = field(default_factory=list)


def apply_profile(config: dict, profile_name: str | None) -> dict:
    """Los "profiles" de la config son bloques con las mismas claves que el
    nivel raíz; aplicar uno sobreescribe esas claves sobre la config base."""
    if not profile_name:
        return config
    profiles = config.get("profiles") or {}
    if profile_name not in profiles:
        raise ProfileError(
            f"El perfil '{profile_name}' no existe. "
            f"Perfiles disponibles: {', '.join(profiles) or 'ninguno'}."
        )
    merged = dict(config)
    # label/description son solo para la GUI; no son opciones de scrcpy.
    overlay = {k: v for k, v in profiles[profile_name].items() if k not in ("label", "description")}
    merged.update(overlay)
    # Los perfiles de fábrica hablan de "audio: true/false"; la ruta de audio
    # efectiva es "audio_route", que si no se tradujera ganaría siempre.
    if "audio" in overlay and "audio_route" not in overlay:
        merged["audio_route"] = "scrcpy" if overlay["audio"] else "off"
    return merged


def gather(
    config: dict,
    serial: str | None = None,
    fallback_monitors: list[Monitor] | None = None,
) -> SetupInfo:
    """Corre los detectores. ``serial`` fuerza un celular concreto (si no,
    se usa ``preferred_device_serial`` de la config o el único conectado).
    ``fallback_monitors`` se usa si screeninfo no puede leer la pantalla."""
    notes: list[str] = []

    try:
        device = android_device.detect(
            serial=serial or config.get("preferred_device_serial") or None
        )
    except android_device.AndroidDeviceError as exc:
        raise SetupError(str(exc)) from exc

    if wireless.is_wireless_serial(device.serial):
        usb = UsbTopology()  # sin cable: no hay topología USB que leer
    else:
        try:
            usb = usb_topology.find_topology_by_serial(device.serial)
        except usb_topology.UsbTopologyError as exc:
            notes.append(f"USB: {exc}")
            usb = UsbTopology()

    try:
        monitors = monitor.detect_all()
    except monitor.MonitorError as exc:
        if not fallback_monitors:
            raise SetupError(f"Error detectando el monitor: {exc}") from exc
        monitors = fallback_monitors
        mon = monitor.pick_target(monitors, config.get("preferred_monitor") or None)
        notes.append(f"{exc} Se usa un monitor alternativo: {mon.name} {mon.width}x{mon.height}.")
    else:
        mon = monitor.pick_target(monitors, config.get("preferred_monitor") or None)

    try:
        cpu_status = cpu.detect()
    except cpu.CpuError as exc:
        notes.append(f"CPU: {exc}")
        cpu_status = CpuStatus(governor="unknown", current_freq_mhz=0, max_freq_mhz=0)

    return SetupInfo(device=device, usb=usb, monitor=mon, cpu=cpu_status, notes=notes)
