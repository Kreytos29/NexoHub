"""
El mapeo como proceso aparte: ``python -m keymap.service --serial X --map NOMBRE``.

La interfaz (PyQt) lo lanza así en vez de correrlo en un hilo propio: dentro
del mismo proceso, el hilo que lee el teclado y el mouse competía por el GIL
con la interfaz, y cualquier pausa de ella se notaba como retraso al apretar
una tecla o como un «soltar» que llegaba tarde. En un proceso aparte nada de
eso puede pasar.

Protocolo: una línea JSON por mensaje.

  stdin  (interfaz -> servicio)
    {"type": "keymap", "name": ..., "data": {...}}   cambiar el mapa en caliente
    {"type": "active", "value": true|false}
    {"type": "stop"}

  stdout (servicio -> interfaz)
    {"type": "state", "state": "off|active|cursor|suspended"}
    {"type": "command", "name": "editor"}
    {"type": "warning", "message": ...}
    {"type": "failed", "message": ...}

Cerrar stdin también detiene el servicio.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading

import logging_setup
from keymap import model
from keymap.control import ControlError
from keymap.inputs import InputError
from keymap.runner import KeymapRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m keymap.service")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--no-overlay", action="store_true")
    args = parser.parse_args(argv)

    # stdout es solo para el protocolo: cualquier print suelto va a stderr.
    protocol, sys.stdout = sys.stdout, sys.stderr
    write_lock = threading.Lock()

    def emit(**message) -> None:
        with write_lock:
            try:
                protocol.write(json.dumps(message) + "\n")
                protocol.flush()
            except (BrokenPipeError, OSError):
                pass

    log = logging_setup.setup_logging()
    try:
        keymap = model.load(args.map)
        runner = KeymapRunner(
            args.serial, keymap, overlay=not args.no_overlay,
            on_state_change=lambda state: emit(type="state", state=state),
            on_command=lambda name: emit(type="command", name=name),
            on_warning=lambda message: emit(type="warning", message=message),
        )
    except model.KeymapError as exc:
        emit(type="failed", message=str(exc))
        return 1

    def listen() -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                kind = message.get("type")
                if kind == "keymap":
                    runner.reload(model.Keymap.from_dict(message["name"], message["data"]))
                elif kind == "active":
                    runner.set_active(bool(message.get("value")))
                elif kind == "stop":
                    break
            except Exception:  # noqa: BLE001 - un mensaje malo no debe tumbar el mapeo
                log.exception("Mapeo de teclas: mensaje de la interfaz inválido")
        runner.stop()  # "stop" o la interfaz cerró el pipe (se cerró o se cayó)

    threading.Thread(target=listen, daemon=True).start()
    try:
        runner.run()
    except (ControlError, InputError) as exc:
        emit(type="failed", message=str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        log.exception("Mapeo de teclas")
        emit(type="failed", message=f"Error inesperado: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
