"""
Lectura de teclado y mouse directo del kernel (evdev).

En Wayland (Hyprland, GNOME…) una app no puede capturar teclas globales ni
el movimiento crudo del mouse, así que se leen los dispositivos de
/dev/input/event*. Hace falta pertenecer al grupo ``input``.

En modo juego los dispositivos se capturan en exclusiva (``grab``) para que
las teclas mapeadas no lleguen además a otras ventanas. Lo que el mapa no
usa (Esc, Enter, Super, Alt+Tab, la rueda…) se reenvía al sistema por un
dispositivo virtual de uinput, así el teclado nunca queda «muerto». Si el
proceso muere, el kernel suelta la captura solo.
"""
from __future__ import annotations

import errno
import os
import platform
import selectors
import time
from pathlib import Path

import logging_setup

log = logging_setup.get_logger()

VIRTUAL_DEVICE_NAME = "NexoHub keymap passthrough"
RESCAN_SECONDS = 2.0
# Cada cuánto se compara lo que cree el programa que está apretado con lo que
# el kernel dice que realmente lo está (por si se perdió un «soltar»).
RECONCILE_SECONDS = 0.1
LAG_WARN_SECONDS = 0.05  # retraso al leer un evento que vale la pena avisar


class InputError(RuntimeError):
    pass


def _evdev():
    try:
        import evdev  # noqa: PLC0415
    except ImportError as exc:
        raise InputError(
            "Falta el paquete de Python «evdev» (en Arch: python-evdev). "
            "Instálalo desde la sección Dependencias."
        ) from exc
    return evdev


def code_name(code: int) -> str:
    """Nombre evdev de una tecla o botón (``KEY_W``, ``BTN_LEFT``…)."""
    ecodes = _evdev().ecodes
    name = ecodes.KEY.get(code) or ecodes.BTN.get(code)
    if isinstance(name, (list, tuple)):
        name = name[0]
    return name or f"KEY_{code}"


def check_access() -> str | None:
    """None si se puede leer /dev/input; si no, un mensaje explicando qué falta."""
    try:
        evdev = _evdev()
    except InputError as exc:
        return str(exc)
    if not evdev.list_devices():
        return (
            "No hay permiso para leer el teclado y el mouse (/dev/input). Agrega "
            "tu usuario al grupo «input» (sudo usermod -aG input $USER) y "
            "vuelve a iniciar sesión."
        )
    return None


def uinput_problem() -> str | None:
    """Por qué no se puede usar /dev/uinput (o None si se puede). Sin uinput
    el mapeo funciona, pero en modo juego las teclas que el mapa no usa (Esc,
    Enter, Alt+Tab…) no llegan al PC."""
    try:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ENODEV, errno.ENOENT, errno.ENXIO):
            release = platform.release()
            if not Path("/lib/modules", release).is_dir():
                return (
                    f"El kernel se actualizó y todavía corres el anterior ({release}): "
                    "sus módulos ya no están en el disco y no se puede cargar «uinput». "
                    "Reinicia la PC."
                )
            return "El módulo «uinput» del kernel no está cargado (sudo modprobe uinput)."
        if exc.errno in (errno.EACCES, errno.EPERM):
            return (
                "No hay permiso sobre /dev/uinput. Agrega tu usuario al grupo «input» "
                "y vuelve a iniciar sesión."
            )
        return f"No se pudo abrir /dev/uinput: {exc}"
    os.close(fd)
    return None


def _kind(device) -> str | None:
    ecodes = _evdev().ecodes
    caps = device.capabilities()
    keys = set(caps.get(ecodes.EV_KEY, []))
    rels = set(caps.get(ecodes.EV_REL, []))
    if {ecodes.REL_X, ecodes.REL_Y} <= rels and ecodes.BTN_LEFT in keys:
        return "mouse"
    if {ecodes.KEY_A, ecodes.KEY_Z, ecodes.KEY_SPACE} <= keys:
        return "keyboard"
    return None


class KeyTracker:
    """Quién tiene apretada cada tecla.

    Un mismo teclado o mouse suele aparecer como varios dispositivos en
    /dev/input (teclado + «consumer control», receptor 2.4G + mouse…) y a veces
    la misma pulsación llega por más de uno. Sin esto, una tecla de pausa se
    procesaba dos veces (pausa y reanudar al instante) y un botón del mouse
    podía soltarse antes de tiempo. Aquí una tecla cuenta como apretada desde
    que el primer dispositivo la aprieta hasta que el último la suelta."""

    def __init__(self):
        self._holders: dict[str, set[str]] = {}

    def press(self, name: str, source: str) -> bool:
        """True si es la primera pulsación (nadie más la tenía apretada)."""
        holders = self._holders.setdefault(name, set())
        first = not holders
        holders.add(source)
        return first

    def release(self, name: str, source: str) -> bool:
        """True si ya nadie la tiene apretada. Una tecla que no se vio apretar
        (ya estaba apretada al empezar) cuenta como la última."""
        holders = self._holders.get(name)
        if not holders or source not in holders:
            return not holders
        holders.discard(source)
        if holders:
            return False
        del self._holders[name]
        return True

    def held_by(self, source: str) -> list[str]:
        return [name for name, holders in self._holders.items() if source in holders]

    def drop_source(self, source: str) -> list[str]:
        """Un dispositivo desapareció: teclas que quedaron sin nadie que las suelte."""
        freed = []
        for name in list(self._holders):
            holders = self._holders[name]
            if source in holders:
                holders.discard(source)
                if not holders:
                    del self._holders[name]
                    freed.append(name)
        return freed


class InputHub:
    """Lee todos los teclados y mouse y le pasa los eventos al motor."""

    def __init__(self, engine, only: list[str] | None = None):
        self.evdev = _evdev()
        self.engine = engine
        # Si se indica, solo se usan los dispositivos cuyo nombre contenga
        # alguno de estos textos (útil con varios teclados, y para pruebas).
        self.only = [o.lower() for o in (only or []) if o]
        self.devices: dict[str, tuple[object, str]] = {}  # path -> (device, kind)
        # Dispositivos que no son teclado ni mouse (audio, HDMI, mandos…): se
        # miran una sola vez. Abrirlos en cada reescaneo bloqueaba el bucle
        # ~500 ms cada 2 s, y en ese rato el kernel perdía eventos (clics
        # pegados, retraso y cámara a tirones).
        self._ignored: set[str] = set()
        self.selector = selectors.DefaultSelector()
        self.grab_keyboards = False
        self.grab_mice = False
        self._stop = False
        self._uinput = None
        self._consumed: set[str] = set()  # teclas que tomó el mapa y siguen apretadas
        self._tracker = KeyTracker()
        self._grab_warned: set[str] = set()
        self._dropping: set[str] = set()  # dispositivos a media pérdida de eventos (SYN_DROPPED)
        self._last_lag_warning = 0.0
        self.warning: str | None = None  # aviso para mostrar (p. ej. sin uinput)

    # --------------------------------------------------------- dispositivos
    def _open_uinput(self) -> None:
        ecodes = self.evdev.ecodes
        # Solo teclas comunes y botones de mouse: si se declararan también
        # BTN_TOUCH/BTN_TOOL_PEN, libinput lo tomaría por una tableta gráfica.
        keys = list(range(ecodes.KEY_ESC, ecodes.KEY_MICMUTE + 1))
        keys += list(range(ecodes.BTN_LEFT, ecodes.BTN_TASK + 1))
        caps = {
            ecodes.EV_KEY: sorted(set(keys)),
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL, ecodes.REL_HWHEEL],
        }
        problem = uinput_problem()
        if problem is None:
            try:
                self._uinput = self.evdev.UInput(caps, name=VIRTUAL_DEVICE_NAME)
            except (OSError, self.evdev.UInputError) as exc:
                problem = f"No se pudo crear el dispositivo virtual: {exc}"
        if problem:
            self._uinput = None
            self.warning = (
                f"{problem} Mientras tanto, en modo juego las teclas que el mapa no "
                "usa (Esc, Enter, Alt+Tab…) no llegan al PC: usa la tecla de pausa."
            )
            log.warning("Mapeo de teclas: %s", self.warning)

    def scan(self) -> None:
        current = set(self.evdev.list_devices())
        for path in list(self.devices):
            if path not in current:
                self._drop(path)
        self._ignored &= current  # si el número se reutiliza, se vuelve a mirar
        for path in current - set(self.devices) - self._ignored:
            try:
                device = self.evdev.InputDevice(path)
            except OSError:
                continue
            kind = _kind(device) if device.name != VIRTUAL_DEVICE_NAME else None
            if kind and self.only and not any(o in device.name.lower() for o in self.only):
                kind = None
            if kind is None:
                device.close()
                self._ignored.add(path)
                continue
            self.devices[path] = (device, kind)
            self.selector.register(device, selectors.EVENT_READ, path)
            log.info("Mapeo de teclas: usando %s «%s».", "teclado" if kind == "keyboard" else "mouse", device.name)
            self._apply_grab(device, kind)

    def _drop(self, path: str) -> None:
        device, kind = self.devices.pop(path)
        # Si se desconectó con algo apretado nadie lo soltará: se suelta aquí
        # (si no, un clic de disparo se quedaría pegado).
        for name in self._tracker.drop_source(path):
            self._release(name)
        try:
            self.selector.unregister(device)
        except (KeyError, ValueError):
            pass
        try:
            device.close()
        except OSError:
            pass

    def set_grab(self, keyboards: bool, mice: bool) -> None:
        self.grab_keyboards, self.grab_mice = keyboards, mice
        for device, kind in list(self.devices.values()):
            self._apply_grab(device, kind)

    def _apply_grab(self, device, kind: str) -> None:
        want = self.grab_keyboards if kind == "keyboard" else self.grab_mice
        try:
            if want:
                device.grab()
            else:
                device.ungrab()
        except OSError as exc:
            # EBUSY al soltar algo que no estaba capturado es normal; al
            # capturar significa que otro programa ya lo tiene y el cursor
            # se seguiría moviendo por el PC.
            if want and exc.errno == errno.EBUSY and device.name not in self._grab_warned:
                self._grab_warned.add(device.name)
                log.warning("Mapeo de teclas: no se pudo capturar «%s» (otro programa lo tiene).", device.name)

    # --------------------------------------------------------------- bucle
    def run(self) -> None:
        self.run_with()

    def run_with(self, on_ready=None) -> None:
        self._open_uinput()
        self.scan()
        if not self.devices:
            raise InputError("No se encontró ningún teclado ni mouse en /dev/input.")
        if on_ready:
            on_ready()
        next_scan = time.monotonic() + RESCAN_SECONDS
        next_reconcile = time.monotonic() + RECONCILE_SECONDS
        try:
            while not self._stop:
                # La cámara se manda a ritmo fijo: si hay movimiento pendiente,
                # se despierta a tiempo para enviarlo aunque no llegue nada.
                deadline = self.engine.next_deadline()
                timeout = 0.25 if deadline is None else min(deadline, 0.25)
                for selector_key, _mask in self.selector.select(timeout=timeout):
                    path = selector_key.data
                    entry = self.devices.get(path)
                    if entry is None:
                        continue
                    try:
                        events = list(entry[0].read())
                    except OSError:
                        # Se desconectó (mouse inalámbrico que se durmió, etc.).
                        self._drop(path)
                        continue
                    self._dispatch(events, entry[1], path)
                self.engine.tick()
                if time.monotonic() >= next_reconcile:
                    self._reconcile_all()
                    next_reconcile = time.monotonic() + RECONCILE_SECONDS
                if time.monotonic() >= next_scan:
                    self.scan()
                    next_scan = time.monotonic() + RESCAN_SECONDS
        finally:
            self.close()

    def stop(self) -> None:
        self._stop = True

    def close(self) -> None:
        self.set_grab(False, False)
        for path in list(self.devices):
            self._drop(path)
        if self._uinput is not None:
            try:
                self._uinput.close()
            except OSError:
                pass
            self._uinput = None

    def _dispatch(self, events, kind: str, path: str = "") -> None:
        ecodes = self.evdev.ecodes
        dx = dy = 0
        forwarded = False
        if events:
            self._check_lag(events[-1])
        for event in events:
            if event.type == ecodes.EV_SYN:
                if event.code == ecodes.SYN_DROPPED:
                    # El kernel perdió eventos (nos tardamos en leer): lo que
                    # venga hasta el próximo SYN_REPORT no es confiable, y un
                    # «soltar» pudo perderse; se vuelve a leer qué está apretado.
                    self._dropping.add(path)
                    log.warning("Mapeo de teclas: se perdieron eventos de entrada (%s).", path)
                elif event.code == ecodes.SYN_REPORT and path in self._dropping:
                    self._dropping.discard(path)
                    forwarded |= self._reconcile(path)
                continue
            if path in self._dropping:
                continue
            if event.type == ecodes.EV_KEY:
                name = code_name(event.code)
                if event.value == 2:  # repetición automática
                    consumed = name in self._consumed
                elif event.value == 1:
                    consumed = self._press(name, path)
                else:
                    consumed = self._release_from(name, path)
                if not consumed:
                    forwarded |= self._forward(kind, event)
            elif event.type == ecodes.EV_REL:
                if event.code == ecodes.REL_X:
                    dx += event.value
                elif event.code == ecodes.REL_Y:
                    dy += event.value
                elif event.code == ecodes.REL_WHEEL:
                    if self._is_grabbed(kind) and event.value and self.engine.handle_wheel(event.value):
                        continue  # la rueda está mapeada: no llega al PC
                    forwarded |= self._forward(kind, event)
                elif event.code == ecodes.REL_HWHEEL:
                    forwarded |= self._forward(kind, event)
        if dx or dy:
            if self._is_grabbed(kind):
                self.engine.handle_mouse(dx, dy)
        if forwarded and self._uinput is not None:
            self._uinput.syn()

    def _check_lag(self, event) -> None:
        lag = time.time() - event.timestamp()
        now = time.monotonic()
        if lag > LAG_WARN_SECONDS and now - self._last_lag_warning > 5:
            self._last_lag_warning = now
            log.warning("Mapeo de teclas: los eventos de entrada llegan con %.0f ms de retraso.", lag * 1000)

    def _reconcile_all(self) -> None:
        for path in list(self.devices):
            if self._tracker.held_by(path):
                if self._reconcile(path) and self._uinput is not None:
                    self._uinput.syn()

    def _reconcile(self, path: str) -> bool:
        """Suelta lo que el programa cree apretado y el kernel dice que no.
        Devuelve True si reenvió algo al sistema (hace falta un syn)."""
        entry = self.devices.get(path)
        if entry is None:
            return False
        device, kind = entry
        try:
            really_down = {code_name(code) for code in device.active_keys()}
        except OSError:
            return False
        forwarded = False
        ecodes = self.evdev.ecodes
        for name in self._tracker.held_by(path):
            if name in really_down:
                continue
            log.info("Mapeo de teclas: se soltó %s sin aviso; se suelta.", name)
            if not self._release_from(name, path) and self._is_grabbed(kind) and self._uinput is not None:
                code = ecodes.ecodes.get(name)
                if isinstance(code, int):
                    self._uinput.write(ecodes.EV_KEY, code, 0)
                    forwarded = True
        return forwarded

    def _press(self, name: str, path: str) -> bool:
        if not self._tracker.press(name, path):
            return name in self._consumed  # la misma tecla por otro dispositivo
        consumed = self.engine.handle_key(name, True)
        if consumed:
            self._consumed.add(name)
        return consumed

    def _release_from(self, name: str, path: str) -> bool:
        consumed = name in self._consumed
        if self._tracker.release(name, path):
            consumed |= self._release(name)
        return consumed

    def _release(self, name: str) -> bool:
        handled = self.engine.handle_key(name, False)
        was_consumed = name in self._consumed
        self._consumed.discard(name)
        return handled or was_consumed

    def _is_grabbed(self, kind: str) -> bool:
        return self.grab_keyboards if kind == "keyboard" else self.grab_mice

    def _forward(self, kind: str, event) -> bool:
        # Si el dispositivo no está capturado, el sistema ya recibió el evento.
        if not self._is_grabbed(kind) or self._uinput is None:
            return False
        self._uinput.write(event.type, event.code, event.value)
        return True
