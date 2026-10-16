"""Hilos de trabajo (QThread): todo lo que puede tardar (ADB, scrcpy, pip,
pkexec) corre aquí para que la ventana nunca se congele."""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

import adb_tools
import autoconfig
import logging_setup
from detectors import android_device, bluetooth, dependencies, privileged, resolution, wireless

# scrcpy con --print-fps imprime líneas tipo "INFO: 59 fps" cada segundo.
FPS_LINE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps", re.IGNORECASE)


class DetectWorker(QThread):
    done = pyqtSignal(object)  # autoconfig.SetupInfo
    failed = pyqtSignal(str)

    def __init__(self, config: dict, serial: str | None = None, fallback_monitors=None):
        super().__init__()
        self.config = config
        self.serial = serial
        self.fallback_monitors = fallback_monitors

    def run(self) -> None:
        try:
            info = autoconfig.gather(
                self.config, serial=self.serial, fallback_monitors=self.fallback_monitors
            )
        except autoconfig.SetupError as exc:
            # El motivo real (varios celulares, sin autorizar, adb faltante...)
            # llega tal cual a la pantalla en vez de un mensaje genérico.
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Error inesperado al detectar: {exc}")
            return
        self.done.emit(info)


class LaunchWorker(QThread):
    log = pyqtSignal(str)
    fps_update = pyqtSignal(float)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, command: list[str], usb, fix_usb: bool, fix_cpu: bool):
        super().__init__()
        self.command = command
        self.usb = usb
        self.fix_usb = fix_usb
        self.fix_cpu = fix_cpu
        self.process: subprocess.Popen | None = None
        self._stop_requested = False
        # Últimas líneas de salida: si scrcpy falla, el motivo real casi
        # siempre está ahí ("ERROR: Could not find any ADB device"...).
        self._tail: deque[str] = deque(maxlen=8)

    def run(self) -> None:
        if self.fix_usb or self.fix_cpu:
            try:
                touched = privileged.apply_privileged_fixes(self.usb, self.fix_usb, self.fix_cpu)
                if touched:
                    self.log.emit(f"USB power forzado a 'on' en: {touched}")
                if self.fix_cpu:
                    self.log.emit("CPU governor cambiado a 'performance'.")
            except privileged.PrivilegedActionError as exc:
                self.log.emit(f"No se pudieron aplicar los ajustes con privilegios: {exc}")

        self.log.emit("Comando: " + shlex.join(self.command))
        try:
            # scrcpy, al no escribir a una terminal sino a un pipe, cambia
            # su salida de "por linea" a "por bloques" (buffering estandar
            # de libc) -- eso hacia que las lineas de --print-fps tardaran
            # demasiado en llegar (o nunca llegaran antes de detener
            # scrcpy). "stdbuf -oL -eL" fuerza salida por linea sin tocar
            # scrcpy en si. Si no esta disponible, se sigue sin el wrapper
            # (funciona igual, pero el overlay puede demorar en reaccionar).
            command = self.command
            stdbuf_path = shutil.which("stdbuf")
            if stdbuf_path:
                command = [stdbuf_path, "-oL", "-eL", *self.command]
            else:
                self.log.emit(
                    "Aviso: no se encontró 'stdbuf' (paquete coreutils). "
                    "El overlay de FPS puede tardar en actualizarse."
                )
            # stdout+stderr combinados y leidos linea a linea: es lo que
            # permite tanto loguear en vivo como parsear las lineas
            # "INFO: N fps" que imprime --print-fps para el overlay.
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return

        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.rstrip()
            if not line:
                continue
            match = FPS_LINE_RE.search(line)
            if match:
                try:
                    self.fps_update.emit(float(match.group(1)))
                except ValueError:
                    pass
            if not match:
                self._tail.append(line)
            self.log.emit(line)

        returncode = self.process.wait()
        if self._stop_requested or returncode == 0:
            self.finished_ok.emit()
        else:
            detail = "\n".join(self._tail)
            message = f"scrcpy terminó con código {returncode}."
            if detail:
                message += f"\n\nÚltimas líneas de salida:\n{detail}"
            self.failed.emit(message)

    def stop(self) -> None:
        """Pide que se detenga la transmisión (botón 'Detener'). No
        bloquea: manda SIGTERM y deja que el bucle de lectura de stdout
        del hilo detecte la salida del proceso solo."""
        self._stop_requested = True
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:  # noqa: BLE001
                pass

    def kill(self) -> None:
        """Último recurso si el proceso no respondió a stop() a tiempo."""
        if self.process and self.process.poll() is None:
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass



class AdbRestartWorker(QThread):
    """Corre 'adb kill-server' + 'adb start-server' fuera del hilo de UI."""
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            message = android_device.restart_adb_server()
        except android_device.AndroidDeviceError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(message)


class PhoneResolutionWorker(QThread):
    """Lee o cambia la resolución de PANTALLA del celular vía ADB ('adb
    shell wm size'), fuera del hilo de UI -- 'action' es 'get', 'set' o
    'reset'; 'width'/'height' solo aplican para 'set'."""
    done = pyqtSignal(object)  # detectors.resolution.ScreenSize
    failed = pyqtSignal(str)

    def __init__(self, serial: str, action: str, width: int = 0, height: int = 0):
        super().__init__()
        self.serial = serial
        self.action = action
        self.width = width
        self.height = height

    def run(self) -> None:
        try:
            if self.action == "set":
                resolution.set_size(self.serial, self.width, self.height)
            elif self.action == "reset":
                resolution.reset_size(self.serial)
            size = resolution.get_current(self.serial)
        except resolution.ResolutionError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(size)


class WifiEnableWorker(QThread):
    """Cambia el celular de USB a WiFi/LAN (adb tcpip + adb connect)."""

    done = pyqtSignal(str)  # nueva direccion "ip:puerto"
    failed = pyqtSignal(str)

    def __init__(self, usb_serial: str, port: int):
        super().__init__()
        self.usb_serial = usb_serial
        self.port = port

    def run(self) -> None:
        try:
            address = wireless.enable_over_wifi(self.usb_serial, port=self.port)
        except wireless.WirelessError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(address)


class WifiDisableWorker(QThread):
    """Cierra la conexión 'adb connect' inalámbrica (adb disconnect)."""

    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, address: str):
        super().__init__()
        self.address = address

    def run(self) -> None:
        try:
            wireless.disconnect(self.address)
        except wireless.WirelessError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit()


class BluetoothCheckWorker(QThread):
    """Chequea el estado de Bluetooth del PC (solo lectura, no empareja nada)."""

    done = pyqtSignal(object)  # BluetoothStatus
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            status = bluetooth.detect()
        except bluetooth.BluetoothError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(status)


class DependencyCheckWorker(QThread):
    done = pyqtSignal(object)  # DependencyReport

    def run(self) -> None:
        self.done.emit(dependencies.check())


class DependencyInstallWorker(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, report: dependencies.DependencyReport):
        super().__init__()
        self.report = report

    def run(self) -> None:
        try:
            if self.report.missing_binaries:
                self.log.emit(
                    "Pidiendo autenticación gráfica para instalar paquetes "
                    "del sistema..."
                )
                dependencies.install_missing_system_packages(self.report)
                self.log.emit("Paquetes del sistema instalados.")
            if self.report.missing_python:
                self.log.emit("Instalando paquetes de Python con pip...")
                dependencies.install_missing_python_packages(self.report)
                self.log.emit("Paquetes de Python instalados.")
        except dependencies.DependencyError as exc:
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit()



class StatusPollWorker(QThread):
    """Consulta liviana y periódica: qué celulares ve ADB y, si se pide, la
    batería/temperatura del celular elegido. Corre cada pocos segundos para
    que la app note enchufar/desenchufar sin tener que pulsar 'Detectar'."""

    done = pyqtSignal(object, object)  # list[DeviceEntry], BatteryInfo | None
    failed = pyqtSignal(str)

    def __init__(self, serial: str | None, with_battery: bool):
        super().__init__()
        self.serial = serial
        self.with_battery = with_battery

    def run(self) -> None:
        try:
            devices = adb_tools.list_devices()
        except adb_tools.AdbToolError as exc:
            self.failed.emit(str(exc))
            return
        battery = None
        if self.serial and self.with_battery:
            ready = {d.serial for d in devices if d.is_ready}
            if self.serial in ready:
                try:
                    battery = adb_tools.battery(self.serial)
                except adb_tools.AdbToolError:
                    battery = None
        self.done.emit(devices, battery)


class ScreenshotWorker(QThread):
    done = pyqtSignal(object)  # Path
    failed = pyqtSignal(str)

    def __init__(self, serial: str, directory: Path):
        super().__init__()
        self.serial = serial
        self.directory = directory

    def run(self) -> None:
        try:
            path = adb_tools.screenshot(self.serial, self.directory)
        except adb_tools.AdbToolError as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(path)


class VersionInfoWorker(QThread):
    """Lee las versiones de scrcpy y adb para el diálogo 'Acerca de'."""

    done = pyqtSignal(object, object)  # scrcpy, adb (str | None)

    def run(self) -> None:
        self.done.emit(adb_tools.scrcpy_version(), adb_tools.adb_version())


class KeymapWorker(QThread):
    """Mapeo de teclas a toques mientras se transmite (ver keymap/).

    Corre en un proceso aparte (keymap/service.py) para que la interfaz no le
    quite tiempo a la lectura del teclado y el mouse."""

    state_changed = pyqtSignal(str)  # off | active | cursor | suspended
    command = pyqtSignal(str)  # "editor": se pulsó la tecla para abrir el editor
    warning = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, serial: str, keymap_name: str, overlay: bool = True):
        super().__init__()
        self.serial = serial
        self.keymap_name = keymap_name
        self.overlay = overlay
        self._process: subprocess.Popen | None = None
        self._stop_requested = False
        self._failed = False
        self._send_lock = threading.Lock()

    def run(self) -> None:
        log = logging_setup.get_logger()
        command = [
            sys.executable, "-m", "keymap.service", "--serial", self.serial, "--map", self.keymap_name,
        ]
        if not self.overlay:
            command.append("--no-overlay")
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parent.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.failed.emit(f"No se pudo iniciar el mapeo: {exc}")
            return
        process = self._process
        if self._stop_requested:
            self.stop()
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                kind = message["type"]
            except (ValueError, KeyError, TypeError):
                log.info("keymap: %s", line)  # salida suelta (p. ej. un error de Python)
                continue
            if kind == "state":
                self.state_changed.emit(message["state"])
            elif kind == "command":
                self.command.emit(message["name"])
            elif kind == "warning":
                self.warning.emit(message["message"])
            elif kind == "failed":
                self._failed = True
                self.failed.emit(message["message"])
        code = process.wait()
        if code != 0 and not self._failed and not self._stop_requested:
            self.failed.emit(f"El mapeo se cerró inesperadamente (código {code}); mira el registro.")

    def _send(self, message: dict) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            return
        with self._send_lock:
            try:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def set_active(self, active: bool) -> None:
        self._send({"type": "active", "value": bool(active)})

    def reload(self, keymap) -> None:
        self._send({"type": "keymap", "name": keymap.name, "data": keymap.to_dict()})

    def stop(self) -> None:
        self._stop_requested = True
        process = self._process
        if process is None:
            return
        self._send({"type": "stop"})
        # Si no sale solo (colgado), se fuerza; el kernel suelta el teclado y
        # el mouse al morir el proceso.
        threading.Timer(5.0, lambda: process.poll() is None and process.kill()).start()
