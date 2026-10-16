"""
Sesión de control con scrcpy-server para inyectar toques en el celular.

Se sube al celular el mismo ``scrcpy-server`` que trae scrcpy y se lo
arranca con ``video=false audio=false control=true``: una segunda sesión,
independiente de la ventana de scrcpy, por la que solo viajan toques. Es
igual de rápido que el control de scrcpy (mucho más que ``adb shell
input``) y no deja nada instalado: el archivo vive en /data/local/tmp.

Sin video, el servidor NO reescala las coordenadas: espera píxeles reales en
la orientación actual de la pantalla (verificado en Android 16 con scrcpy
5.0.1). Por eso la sesión consulta cada tanto el tamaño y la rotación.
"""
from __future__ import annotations

import queue
import random
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import logging_setup

log = logging_setup.get_logger()

# Rutas habituales del servidor según la distro/empaquetado.
SERVER_CANDIDATES = (
    "/usr/share/scrcpy/scrcpy-server",
    "/usr/local/share/scrcpy/scrcpy-server",
    "/usr/share/scrcpy/scrcpy-server.jar",
    "/opt/scrcpy/scrcpy-server",
)
DEVICE_SERVER_PATH = "/data/local/tmp/nexohub-keymap-server.jar"

# Protocolo de control de scrcpy (mensajes cliente -> servidor).
TYPE_INJECT_TOUCH_EVENT = 2
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2
_TOUCH_STRUCT = struct.Struct(">BBqiiHHHii")

_ROTATION_RE = re.compile(r"mCurrentRotation=ROTATION_(\d+)")
_SIZE_RE = re.compile(r"(Physical|Override) size:\s*(\d+)x(\d+)")


class ControlError(RuntimeError):
    pass


def find_server(explicit: str | None = None) -> Path:
    """Ubica el scrcpy-server instalado junto con scrcpy."""
    candidates = [explicit] if explicit else []
    candidates += list(SERVER_CANDIDATES)
    scrcpy = shutil.which("scrcpy")
    if scrcpy:
        prefix = Path(scrcpy).resolve().parent.parent
        candidates.append(str(prefix / "share" / "scrcpy" / "scrcpy-server"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise ControlError(
        "No se encontró el archivo scrcpy-server (viene con el paquete de "
        "scrcpy). Reinstala scrcpy desde la sección Dependencias."
    )


def encode_touch(action: int, pointer_id: int, x: int, y: int, width: int, height: int) -> bytes:
    # Dedos, no mouse: sin botones (igual que manda el cliente de scrcpy para
    # los toques), así Android los trata como pantalla táctil pura.
    pressure = 0 if action == ACTION_UP else 0xFFFF
    return _TOUCH_STRUCT.pack(
        TYPE_INJECT_TOUCH_EVENT,
        action,
        pointer_id,
        max(0, min(int(x), width - 1)),
        max(0, min(int(y), height - 1)),
        width,
        height,
        pressure,
        0,
        0,
    )


def parse_screen(output: str) -> tuple[int, int] | None:
    """Tamaño lógico de la pantalla en su orientación actual, a partir de
    ``wm size`` + la rotación de ``dumpsys window displays``."""
    sizes = {kind: (int(w), int(h)) for kind, w, h in _SIZE_RE.findall(output)}
    size = sizes.get("Override") or sizes.get("Physical")
    if size is None:
        return None
    width, height = size
    rotation = _ROTATION_RE.search(output)
    if rotation and rotation.group(1) in ("90", "270"):
        width, height = height, width
    return width, height


class ControlSession:
    """Un scrcpy-server corriendo en el celular solo para recibir toques."""

    POLL_SECONDS = 2.0

    def __init__(self, serial: str, scrcpy_version: str, server_path: str | None = None):
        self.serial = serial
        self.version = scrcpy_version
        self.server_path = find_server(server_path)
        self.scid = f"{random.randrange(1, 0x7FFFFFFF):08x}"
        self.port = 0
        self._sock: socket.socket | None = None
        self._server: subprocess.Popen | None = None
        # Los toques se encolan y los manda otro hilo: leer el teclado nunca
        # espera a la red, y lo que se acumula se manda junto en un solo envío.
        self._outbox: queue.SimpleQueue = queue.SimpleQueue()
        self._sender: threading.Thread | None = None
        self._closed = threading.Event()
        self.screen: tuple[int, int] | None = None
        self.on_screen_change = None  # callable(width, height) | None

    # ----------------------------------------------------------------- adb
    def _adb(self, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["adb", "-s", self.serial, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlError(f"adb no respondió: {exc}") from exc

    def read_screen(self) -> tuple[int, int] | None:
        result = self._adb(
            "shell",
            "wm size; dumpsys window displays | grep -m1 mCurrentRotation",
            timeout=5,
        )
        return parse_screen(result.stdout)

    # ------------------------------------------------------------ arranque
    def start(self) -> None:
        pushed = self._adb("push", str(self.server_path), DEVICE_SERVER_PATH, timeout=30)
        if pushed.returncode != 0:
            raise ControlError(f"No se pudo copiar el servidor al celular: {pushed.stderr.strip()}")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        forward = self._adb("forward", f"tcp:{self.port}", f"localabstract:scrcpy_{self.scid}")
        if forward.returncode != 0:
            raise ControlError(f"adb forward falló: {forward.stderr.strip()}")

        server_cmd = (
            f"CLASSPATH={DEVICE_SERVER_PATH} app_process / com.genymobile.scrcpy.Server "
            f"{self.version} scid={self.scid} log_level=info "
            "video=false audio=false control=true tunnel_forward=true "
            "clipboard_autosync=false power_on=false"
        )
        self._server = subprocess.Popen(
            ["adb", "-s", self.serial, "shell", server_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=self._pump_server_log, daemon=True).start()

        try:
            self._sock = self._connect()
        except ControlError:
            self.close()
            raise

        self.screen = self.read_screen()
        if self.screen is None:
            self.close()
            raise ControlError("No se pudo leer la resolución de la pantalla del celular.")
        log.info("Mapeo de teclas: sesión de control lista (pantalla %sx%s).", *self.screen)
        threading.Thread(target=self._drain_socket, daemon=True).start()
        threading.Thread(target=self._poll_screen, daemon=True).start()
        self._sender = threading.Thread(target=self._send_loop, daemon=True)
        self._sender.start()

    def _connect(self) -> socket.socket:
        # El servidor tarda un poco en abrir su socket; mientras tanto adb
        # acepta la conexión y la cierra sin mandar nada.
        deadline = time.monotonic() + 8
        last_error = "sin respuesta"
        while time.monotonic() < deadline:
            if self._server and self._server.poll() is not None:
                raise ControlError(
                    "scrcpy-server se cerró al arrancar (¿versión distinta entre scrcpy y su servidor?)."
                )
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                sock.settimeout(2)
                if sock.recv(1) == b"\x00":
                    meta = b""
                    while len(meta) < 64:
                        chunk = sock.recv(64 - len(meta))
                        if not chunk:
                            break
                        meta += chunk
                    sock.settimeout(None)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    return sock
                sock.close()
            except OSError as exc:
                last_error = str(exc)
            time.sleep(0.1)
        raise ControlError(f"No se pudo conectar con scrcpy-server: {last_error}")

    def _pump_server_log(self) -> None:
        assert self._server and self._server.stdout
        for line in self._server.stdout:
            line = line.rstrip()
            if line:
                log.info("keymap-server: %s", line)

    def _drain_socket(self) -> None:
        # El servidor puede mandar mensajes (portapapeles, etc.); se leen y
        # descartan para que el buffer del socket no se llene.
        sock = self._sock
        while sock and not self._closed.is_set():
            try:
                if not sock.recv(4096):
                    break
            except OSError:
                break
        if not self._closed.is_set():
            log.warning("Mapeo de teclas: el servidor cerró la conexión.")

    def _poll_screen(self) -> None:
        while not self._closed.wait(self.POLL_SECONDS):
            try:
                screen = self.read_screen()
            except ControlError:
                continue
            if screen and screen != self.screen:
                log.info("Mapeo de teclas: pantalla ahora %sx%s.", *screen)
                self.screen = screen
                if self.on_screen_change:
                    self.on_screen_change(*screen)

    # ---------------------------------------------------------------- uso
    @property
    def alive(self) -> bool:
        return self._sock is not None and not self._closed.is_set() and (
            self._server is not None and self._server.poll() is None
        )

    def touch(self, action: int, pointer_id: int, x: float, y: float) -> None:
        """Toque en píxeles de la pantalla en su orientación actual."""
        if self._sock is None or self.screen is None:
            return
        width, height = self.screen
        self._outbox.put(encode_touch(action, pointer_id, round(x), round(y), width, height))

    def _send_loop(self) -> None:
        last_warning = 0.0
        while True:
            data = self._outbox.get()
            if data is None:
                return
            batch = [data]
            closing = False
            try:
                while True:
                    more = self._outbox.get_nowait()
                    if more is None:
                        closing = True
                        break
                    batch.append(more)
            except queue.Empty:
                pass
            sock = self._sock
            if sock is None:
                return
            started = time.monotonic()
            try:
                sock.sendall(b"".join(batch))
            except OSError as exc:
                log.warning("Mapeo de teclas: no se pudo mandar el toque (%s).", exc)
                if closing:
                    return
                continue
            if closing:
                return
            took = time.monotonic() - started
            if took > 0.03 and started - last_warning > 5:
                last_warning = started
                log.warning("Mapeo de teclas: el envío de toques al celular tardó %.0f ms.", took * 1000)

    def close(self) -> None:
        self._closed.set()
        self._outbox.put(None)
        if self._sender is not None:
            self._sender.join(timeout=1)  # que salgan los «soltar» pendientes
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._server is not None and self._server.poll() is None:
            self._server.terminate()
            try:
                self._server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._server.kill()
        if self.port:
            try:
                self._adb("forward", "--remove", f"tcp:{self.port}", timeout=5)
            except ControlError:
                pass
            self.port = 0
