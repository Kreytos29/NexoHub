"""python -m keymap --map freefire [--serial XXXX]: mapeo de teclas sin GUI."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

import adb_tools
import logging_setup
from keymap import model
from keymap.control import ControlError
from keymap.inputs import InputError
from keymap.runner import KeymapRunner


def sniff(seconds: float) -> int:
    """Diagnóstico: qué manda cada dispositivo al apretar un botón (movimientos
    agrupados). Sirve para ver si un botón del mouse manda algo inesperado."""
    import selectors
    import time

    from keymap.inputs import InputError, _evdev, _kind, code_name

    try:
        evdev = _evdev()
    except InputError as exc:
        print(exc, file=sys.stderr)
        return 1
    ecodes = evdev.ecodes
    selector = selectors.DefaultSelector()
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        if _kind(device):
            selector.register(device, selectors.EVENT_READ)
            print(f"escuchando {path}  {device.name}")
    print(f"Aprieta los botones durante {seconds:.0f} s…")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for key, _mask in selector.select(timeout=0.2):
            device = key.fileobj
            moved = [0, 0]
            for event in device.read():
                if event.type == ecodes.EV_KEY:
                    print(f"{device.name:<34} {code_name(event.code):<14} {'ABAJO' if event.value else 'ARRIBA'}")
                elif event.type == ecodes.EV_REL and event.code in (ecodes.REL_X, ecodes.REL_Y):
                    moved[event.code == ecodes.REL_Y] += event.value
            if moved != [0, 0]:
                print(f"{device.name:<34} mueve dx={moved[0]} dy={moved[1]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m keymap", description="Mapeo de teclado y mouse a toques.")
    parser.add_argument("--map", default="freefire", help=f"mapa a usar ({', '.join(model.available())})")
    parser.add_argument("--serial", help="celular a usar si hay más de uno")
    parser.add_argument("--device", action="append", help="usar solo teclados/mouse cuyo nombre contenga esto (repetible)")
    parser.add_argument("--active", action="store_true", help="empezar ya en modo juego")
    parser.add_argument("--sniff", type=float, metavar="SEGUNDOS", help="mostrar los eventos crudos de teclados y mouse (sin capturarlos) y salir")
    parser.add_argument("--no-overlay", action="store_true", help="no dibujar las teclas sobre el juego")
    args = parser.parse_args(argv)

    if args.sniff:
        return sniff(args.sniff)

    logger = logging_setup.setup_logging()
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    logger.addHandler(console)
    try:
        keymap = model.load(args.map)
    except model.KeymapError as exc:
        print(exc, file=sys.stderr)
        return 2

    serial = args.serial
    if not serial:
        ready = [d for d in adb_tools.list_devices() if d.is_ready]
        if len(ready) != 1:
            print("Conecta un celular (o indica cuál con --serial).", file=sys.stderr)
            return 2
        serial = ready[0].serial

    runner = KeymapRunner(
        serial, keymap, devices=args.device, overlay=not args.no_overlay,
        on_warning=lambda msg: print(f"Aviso: {msg}", file=sys.stderr),
        on_command=lambda name: print("(el editor se abre desde la interfaz de NexoHub)") if name == "editor" else None,
    )
    signal.signal(signal.SIGINT, lambda *_: runner.stop())
    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    if args.active:
        threading.Timer(1.5, lambda: runner.set_active(True)).start()
    print(
        f"Mapa «{keymap.display_name}». {model.key_label(keymap.toggle_key)}: modo juego · "
        f"{model.key_label(keymap.suspend_key)}: pausa · {model.key_label(keymap.cursor_key)}: cursor · "
        f"{model.key_label(keymap.overlay_key)}: teclas en pantalla · Ctrl+C: salir"
    )
    try:
        runner.run()
    except (ControlError, InputError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
