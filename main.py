#!/usr/bin/env python3
"""
NexoHub (scrcpy-autoconfig), modo línea de comandos: detecta tu setup y
genera/corre el comando scrcpy óptimo. Todo se configura desde config.yaml
(o desde la GUI: ``python3 gui.py``).
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from rich.console import Console
from rich.table import Table

import adb_tools
import autoconfig
import command_builder
import config_store
from detectors import android_device, cpu, monitor, privileged, wireless
from version import APP_NAME, __version__

console = Console()

# Compatibilidad: la GUI y scripts viejos importaban estos nombres desde aquí.
load_config = config_store.load_config
apply_profile = autoconfig.apply_profile


def print_diagnosis(info: autoconfig.SetupInfo) -> None:
    device, usb, mon, cpu_status = info.device, info.usb, info.monitor, info.cpu
    table = Table(title=f"Diagnóstico de {APP_NAME}")
    table.add_column("Componente")
    table.add_column("Valor")

    encoder = device.best_encoder
    conexion = f"WiFi ({device.serial})" if wireless.is_wireless_serial(device.serial) else "USB"
    table.add_row("Conexión", conexion)
    table.add_row("Celular", f"{device.brand} {device.model} (Android {device.android_version})")
    table.add_row("Chipset", device.chipset)
    table.add_row(
        "Mejor encoder",
        f"{encoder.name} ({encoder.codec}, {encoder.kind})" if encoder else "no detectado",
    )
    table.add_row("Velocidad USB", usb.speed_label)
    table.add_row("USB autosuspend", "sí" if usb.any_autosuspend else "no")
    table.add_row("Monitor", f"{mon.name} {mon.width}x{mon.height}")
    table.add_row("CPU governor", cpu_status.governor)
    console.print(table)


def print_devices() -> int:
    try:
        devices = adb_tools.list_devices()
    except adb_tools.AdbToolError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    if not devices:
        console.print("No hay celulares conectados.")
        return 0
    table = Table(title="Celulares conectados")
    table.add_column("Serial")
    table.add_column("Modelo")
    table.add_column("Vía")
    table.add_column("Estado")
    for entry in devices:
        table.add_row(entry.serial, entry.model or "-", entry.transport.upper(), entry.state_label)
    console.print(table)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexohub-cli", description=__doc__)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--auto", action="store_true", help="Detecta todo y corre scrcpy directamente."
    )
    parser.add_argument(
        "--diagnose", action="store_true", help="Solo muestra el diagnóstico, no corre nada."
    )
    parser.add_argument(
        "--print-command", action="store_true", help="Solo imprime el comando, no lo ejecuta."
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="Lista los celulares que ve ADB y sale."
    )
    parser.add_argument(
        "--serial", default=None, help="Serial del celular a usar (ver --list-devices)."
    )
    parser.add_argument(
        "--screenshot",
        action="store_true",
        help="Guarda una captura de pantalla del celular y sale.",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Graba la sesión en la carpeta de grabaciones (ver config.yaml: recordings_dir).",
    )
    parser.add_argument(
        "--fix-usb-power", action="store_true", help="Fuerza power/control=on en la cadena USB."
    )
    parser.add_argument(
        "--fix-cpu-governor", action="store_true", help="Cambia el CPU governor a 'performance'."
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Nombre de un perfil definido en config.yaml (ej. competitive, streaming, quality).",
    )
    parser.add_argument(
        "--wifi",
        action="store_true",
        help=(
            "Cambia el celular (conectado por USB) a modo WiFi/LAN antes de "
            "armar el comando. El celular y el PC deben estar en la misma red."
        ),
    )
    parser.add_argument(
        "--wifi-port",
        type=int,
        default=wireless.DEFAULT_PORT,
        help=f"Puerto TCP a usar en modo WiFi (default: {wireless.DEFAULT_PORT}).",
    )
    return parser


def take_screenshot(config: dict, serial: str | None) -> int:
    """Captura de pantalla: solo necesita adb, no el resto de la detección."""
    try:
        target = android_device.pick_serial(serial or config.get("preferred_device_serial") or None)
        path = adb_tools.screenshot(target, config_store.captures_dir(config))
    except (android_device.AndroidDeviceError, adb_tools.AdbToolError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    console.print(f"[green]Captura guardada:[/green] {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_devices:
        return print_devices()

    if not any([args.auto, args.diagnose, args.print_command, args.screenshot]):
        parser.print_help()
        return 0

    config, config_warnings = config_store.load_config_safe()
    for warning in config_warnings:
        console.print(f"[yellow]⚠[/yellow] {warning}")

    try:
        config = autoconfig.apply_profile(config, args.profile)
        if args.screenshot:
            return take_screenshot(config, args.serial)
        info = autoconfig.gather(
            config, serial=args.serial, fallback_monitors=[monitor.assumed_monitor()]
        )
    except autoconfig.SetupError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    for note in info.notes:
        console.print(f"[yellow]Aviso:[/yellow] {note}")

    device, usb, cpu_status = info.device, info.usb, info.cpu

    if args.wifi:
        try:
            address = wireless.enable_over_wifi(device.serial, port=args.wifi_port)
        except wireless.WirelessError as exc:
            console.print(f"[red]No se pudo activar el modo WiFi:[/red] {exc}")
            return 1
        console.print(f"[green]Conectado por WiFi en:[/green] {address}")
        console.print("Ya puedes desconectar el cable USB.")
        # Se re-detecta el celular usando la nueva dirección como serial: la
        # topología USB ya no aplica (command_builder usa un bitrate
        # conservador propio para WiFi), pero el resto (chipset, encoders)
        # se vuelve a leer igual, ahora vía la conexión inalámbrica.
        try:
            device = android_device.detect(serial=address)
        except android_device.AndroidDeviceError as exc:
            console.print(f"[red]Error leyendo el celular por WiFi:[/red] {exc}")
            return 1
        info.device = device
        usb = info.usb = type(usb)()

    fix_usb = args.fix_usb_power or config.get("auto_fix_usb_power")
    fix_cpu = args.fix_cpu_governor or config.get("auto_fix_cpu_governor")
    if fix_usb or fix_cpu:
        try:
            touched = privileged.apply_privileged_fixes(usb, fix_usb, fix_cpu)
            if touched:
                console.print(f"[green]USB power forzado a 'on' en:[/green] {touched}")
            if fix_cpu:
                console.print("[green]CPU governor cambiado a 'performance'.[/green]")
                cpu_status = info.cpu = cpu.detect()
        except privileged.PrivilegedActionError as exc:
            console.print(f"[red]No se pudieron aplicar los ajustes con privilegios:[/red] {exc}")

    if args.diagnose:
        print_diagnosis(info)
        return 0

    overrides = dict(config)
    if args.record:
        overrides["record"] = True
        overrides["record_path"] = str(
            adb_tools.timestamped_path(
                config_store.recordings_dir(config),
                "NexoHub",
                f".{overrides.get('record_format') or 'mkv'}",
            )
        )
        config_store.recordings_dir(config).mkdir(parents=True, exist_ok=True)

    result = command_builder.build(device, usb, info.monitor, cpu_status, overrides=overrides)

    for warning in result.warnings:
        console.print(f"[yellow]⚠[/yellow] {warning}")

    console.print(f"[bold]Comando generado:[/bold] {result.as_string()}")

    if args.print_command:
        return 0

    console.print("[bold green]Lanzando scrcpy...[/bold green]")
    completed = subprocess.run(result.as_command(), check=False)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
