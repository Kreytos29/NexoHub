"""
Motor del mapeo: recibe teclas y movimiento del mouse y produce toques.

Es lógica pura (no sabe nada de evdev ni de sockets) para poder probarla
sin hardware. Quien lo usa le pasa un ``sink`` con:

    sink.screen -> (ancho, alto) actuales en píxeles, o None
    sink.touch(action, pointer_id, x, y)

y llama a ``tick()`` cuando ``next_deadline()`` lo pide (la cámara se
envía a ritmo fijo, ver abajo).

Estados (``state``):

  off        inactivo: todo pasa al PC normalmente (no se captura nada)
  active     modo juego: teclado y mouse capturados; las teclas tocan la
             pantalla y el mouse mueve la cámara
  cursor     modo juego con el mouse suelto para tocar menús (Alt)
  suspended  en pausa: todo vuelve al PC hasta reanudar (tecla de pausa)

Las teclas especiales actúan al SOLTARLAS: si se capturara el teclado con
la tecla todavía apretada, el compositor nunca vería que se soltó y la
repetiría sin fin.

Cámara: el mouse arrastra un dedo virtual. Lo que hace que se sienta
fluida (y lo que estaba mal en la primera versión):

  * El movimiento se acumula y se manda a ritmo fijo (AIM_INTERVAL), no un
    toque por cada lectura del mouse (hasta 1000 por segundo).
  * Al apoyar el dedo no se mueve en el mismo instante: el juego suele
    ignorar el desplazamiento del cuadro en que empieza un toque.
  * La zona del dedo es grande, así casi nunca hay que levantarlo en medio
    de un giro; y si el mouse se queda quieto con el dedo lejos del centro,
    se levanta en ese momento (sin que se note) para tener recorrido.
"""
from __future__ import annotations

import math
import threading
import time

from keymap.control import ACTION_DOWN, ACTION_MOVE, ACTION_UP
from keymap.model import Control, Keymap

AIM_POINTER_ID = 9
FIRST_POINTER_ID = 10
# s entre movimientos de cámara (~125 por segundo). Más rápido no se nota y
# puede atrasar al celular: si procesa menos toques de los que se le mandan se
# forma una cola, y el «soltar» del disparo queda detrás (sigue disparando).
AIM_INTERVAL = 0.008
AIM_IDLE_LIFT = 0.25  # s quieto antes de levantar el dedo de la cámara
AIM_IDLE_OFFSET = 0.35  # ...si está más lejos que esto del centro (fracción del radio)
AIM_IDLE_LIFT_ANY = 0.6  # s quieto tras los que se levanta aunque esté en el centro
# Una tecla especial no vuelve a actuar antes de esto (un teclado que repite la
# pulsación por otro dispositivo la haría actuar dos veces).
SPECIAL_DEBOUNCE = 0.15
WHEEL_PULSE = 0.04  # s que dura el toque de cada «clic» de la rueda
# El dedo de la cámara no se acerca más que esto a los bordes: Android abre las
# notificaciones con un deslizamiento desde arriba y «atrás» desde los lados.
SCREEN_MARGIN_X = 0.03  # fracción del ancho (el gesto «atrás» es una franja angosta)
SCREEN_MARGIN_Y = 0.06  # fracción del alto (notificaciones arriba, inicio abajo)
# Donde se apoya el dedo de la cámara no puede haber un botón del juego: un
# dedo que aterriza sobre «disparar» (u otro) lo aprieta, y además la cámara
# se corta. Radio de la zona prohibida alrededor de cada control, como
# múltiplo de su tamaño (fracción del alto de la pantalla).
BUTTON_KEEPOUT = 1.7
MIN_KEEPOUT = 0.055
# Lo máximo que se mueve el dedo en un solo envío (fracción del radio). Un
# salto mayor es un error del mouse (un botón o el sensor que manda un
# movimiento enorme) y se descarta en vez de lanzar el dedo por la pantalla.
AIM_MAX_STEP = 0.6
# Lo que hay que mantener apretada la tecla de cursor para que, al soltarla,
# se vuelva al juego (si se suelta antes, el cursor queda libre hasta otro toque).
CURSOR_HOLD = 0.3
# Al reapoyar el dedo, se lo pone esta fracción del radio "hacia atrás" de
# la dirección del giro: así tiene más recorrido justo hacia donde se gira.
AIM_RECENTER_BACK = 0.5
# Pasos intermedios al empujar el stick: algunos juegos ignoran un dedo que
# aparece ya desplazado, y otros lo toman como un toque si no se mueve.
JOYSTICK_STEPS = 3

STATES = ("off", "active", "cursor", "suspended")
_DIRECTION_VECTORS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


class Engine:
    def __init__(self, keymap: Keymap, sink, on_state_change=None, on_command=None, clock=time.monotonic):
        self.sink = sink
        self.on_state_change = on_state_change  # callable(state)
        self.on_command = on_command  # callable("editor" | "overlay")
        self.clock = clock
        self.state = "off"
        self._lock = threading.RLock()
        self._down: dict[int, tuple[float, float]] = {}  # dedos apoyados
        self._directions: set[str] = set()
        self._aim_pos: tuple[float, float] | None = None
        self._aim_pending = [0.0, 0.0]
        self._aim_last_send = 0.0
        self._aim_last_move = 0.0
        self._special_at: dict[str, float] = {}
        self._held_special: tuple[str, float] | None = None  # (tecla, cuándo) de pausa/cursor apretada
        self._pulses: dict[int, float] = {}  # dedo -> cuándo se levanta (rueda)
        self._aim_frozen = False  # cámara congelada por su tecla de suspender
        self._interval = AIM_INTERVAL
        self.set_keymap(keymap)

    # ------------------------------------------------------------- mapa
    def set_keymap(self, keymap: Keymap) -> None:
        """Cambia el mapa en caliente (al guardar en el editor)."""
        with self._lock:
            self.release_all()
            self.keymap = keymap
            self._interval = 1.0 / max(1, keymap.aim_rate)
            self._by_key: dict[str, tuple[Control, int]] = {}
            self._joystick: tuple[Control, int] | None = None
            self._aim = keymap.aim()
            # Zonas donde no se apoya la cámara: (x, y, radio) normalizados.
            self._keepouts = [
                (c.x, c.y, max(c.size * BUTTON_KEEPOUT, MIN_KEEPOUT))
                for c in keymap.controls
                if c.kind == "tap"
            ] + [
                (c.x, c.y, max(c.radius, MIN_KEEPOUT))
                for c in keymap.controls
                if c.kind == "joystick"
            ]
            for index, control in enumerate(keymap.controls):
                pointer_id = FIRST_POINTER_ID + index
                if control.kind == "tap" and control.key:
                    self._by_key.setdefault(control.key, (control, pointer_id))
                elif control.kind == "joystick" and self._joystick is None:
                    self._joystick = (control, pointer_id)
                    for key in control.keys.values():
                        self._by_key.setdefault(key, (control, pointer_id))

    # ----------------------------------------------------------- estados
    @property
    def active(self) -> bool:
        return self.state != "off"

    @property
    def captures_keyboard(self) -> bool:
        return self.state in ("active", "cursor")

    @property
    def captures_mouse(self) -> bool:
        return self.state == "active"

    def set_state(self, state: str) -> None:
        with self._lock:
            if state == self.state:
                return
            previous, self.state = self.state, state
            if state in ("off", "suspended"):
                self.release_all()
            if state == "off":
                self._held_special = None
            elif state == "cursor":
                self._lift_aim()
                # Lo apretado con botones del mouse se suelta: el mouse deja
                # de estar capturado y no llegaría el "soltar".
                for key, (control, pointer_id) in self._by_key.items():
                    if key.startswith("BTN_") and control.kind == "tap":
                        self._up(pointer_id)
            if state != "active":
                self._aim_frozen = False
            if previous == "cursor" or state == "active":
                self._aim_pending = [0.0, 0.0]
        if self.on_state_change:
            self.on_state_change(state)

    def set_active(self, active: bool) -> None:
        self.set_state("active" if active else "off")

    # ------------------------------------------------------------ entradas
    def _special_ready(self, key: str) -> bool:
        now = self.clock()
        if now - self._special_at.get(key, -1e9) < SPECIAL_DEBOUNCE:
            return False
        self._special_at[key] = now
        return True

    def handle_key(self, key: str, pressed: bool) -> bool:
        """Devuelve True si la tecla pertenece al mapeo (se consume)."""
        with self._lock:
            keymap = self.keymap
            if key == keymap.editor_key:
                if not pressed and self._special_ready(key):
                    self.set_state("off")
                    self._command("editor")
                return True
            if key == keymap.quick_key:
                if not pressed and self._special_ready(key):
                    self.set_state("off")
                    self._command("quick")
                return True
            if key == keymap.toggle_key:
                if not pressed and self._special_ready(key):
                    self.set_state("off" if self.active else "active")
                return True
            if self.state == "off":
                return False
            if key == keymap.overlay_key:
                if not pressed and self._special_ready(key):
                    self._command("overlay")
                return True
            if key == keymap.suspend_key:
                self._hold_or_toggle(key, "suspended", "auto", pressed)
                return True
            if self.state == "suspended":
                return False
            if key == keymap.cursor_key:
                self._hold_or_toggle(key, "cursor", keymap.cursor_mode, pressed)
                return True
            if self.state == "cursor" and key.startswith("BTN_"):
                return False
            if self._aim is not None and key == self._aim.suspend_key:
                self._aim_suspend_key(pressed)
                return True
            binding = self._by_key.get(key)
            if binding is None:
                return False
            control, pointer_id = binding
            if control.kind == "joystick":
                self._joystick_key(control, pointer_id, key, pressed)
            elif pressed:
                self._press(pointer_id, control.x, control.y)
            else:
                self._up(pointer_id)
            return True

    def _hold_or_toggle(self, key: str, target: str, mode: str, pressed: bool) -> None:
        """Pausa y cursor libre. ``mode``: toggle alterna al soltar; hold lo
        activa solo mientras está apretada; auto hace ambas (un toque corto
        alterna, mantenida es temporal, como la tecla «Suspender» de BlueStacks)."""
        now = self.clock()
        if pressed:
            if mode != "toggle" and self.state == "active" and self._held_special is None:
                self.set_state(target)
                self._held_special = (key, now)
                self._special_at[key] = now  # un segundo dispositivo que repite la pulsación no la deshace
            return
        held, self._held_special = self._held_special, None
        if held is not None and held[0] == key:
            self._special_at[key] = now
            if mode == "hold" or now - held[1] >= CURSOR_HOLD:
                self.set_state("active")
        elif self._special_ready(key):
            self.set_state("active" if self.state == target else target)

    def _aim_suspend_key(self, pressed: bool) -> None:
        """Congela la cámara (el mouse sigue capturado) con la tecla de la
        cámara: mantenida (hold) o alternando al soltar (toggle)."""
        if self.state != "active":
            return
        if self._aim.suspend_mode == "toggle":
            if not pressed and self._special_ready(self._aim.suspend_key):
                self._set_aim_frozen(not self._aim_frozen)
        else:
            self._set_aim_frozen(pressed)

    def _set_aim_frozen(self, frozen: bool) -> None:
        if frozen == self._aim_frozen:
            return
        self._aim_frozen = frozen
        if frozen:
            self._lift_aim()
        self._aim_pending = [0.0, 0.0]

    def _command(self, name: str) -> None:
        if self.on_command:
            self.on_command(name)

    def handle_wheel(self, direction: int) -> bool:
        """Un «clic» de la rueda (+1 arriba, -1 abajo). True si lo toma el mapa."""
        with self._lock:
            if self.state != "active":
                return False
            binding = self._by_key.get("WHEEL_UP" if direction > 0 else "WHEEL_DOWN")
            if binding is None or binding[0].kind != "tap":
                return False
            control, pointer_id = binding
            self._press(pointer_id, control.x, control.y)
            if pointer_id in self._down:
                self._pulses[pointer_id] = self.clock() + WHEEL_PULSE
            return True

    def handle_mouse(self, dx: float, dy: float) -> None:
        with self._lock:
            if self.state != "active" or self._aim is None or self._aim_frozen:
                return
            self._aim_pending[0] += dx * self._aim.sensitivity
            self._aim_pending[1] += dy * self._aim.sensitivity_y
            now = self.clock()
            self._aim_last_move = now
            if now - self._aim_last_send >= self._interval - 1e-9:
                self._flush_aim(now)

    def tick(self) -> None:
        """Manda la cámara acumulada y levanta el dedo si quedó quieto."""
        with self._lock:
            self._release_pulses()
            if self.state != "active" or self._aim is None:
                return
            now = self.clock()
            if self._has_pending() and now - self._aim_last_send >= self._interval - 1e-9:
                self._flush_aim(now)
            elif self._aim_pos is not None and not self._has_pending():
                idle = now - self._aim_last_move
                center, limit = self._aim_geometry()
                if idle >= AIM_IDLE_LIFT_ANY or (
                    idle >= AIM_IDLE_LIFT
                    and center
                    and math.dist(self._aim_pos, center) > AIM_IDLE_OFFSET * limit
                ):
                    self._lift_aim()

    def next_deadline(self) -> float | None:
        """Segundos hasta el próximo ``tick()`` útil (None: no hace falta)."""
        with self._lock:
            now = self.clock()
            waits = [max(0.0, due - now) for due in self._pulses.values()]
            if self.state == "active" and self._aim is not None:
                if self._has_pending():
                    waits.append(max(0.0, self._aim_last_send + self._interval - now))
                elif self._aim_pos is not None:
                    waits.append(max(0.0, self._aim_last_move + AIM_IDLE_LIFT - now, 0.02))
            return min(waits) if waits else None

    def _release_pulses(self) -> None:
        if not self._pulses:
            return
        now = self.clock()
        for pointer_id, due in list(self._pulses.items()):
            if now >= due - 1e-9:
                del self._pulses[pointer_id]
                self._up(pointer_id)

    def screen_changed(self) -> None:
        """La resolución o la orientación cambió: los dedos apoyados ya no
        están donde el juego cree, así que se sueltan todos."""
        with self._lock:
            self.release_all()

    def release_all(self) -> None:
        with self._lock:
            for pointer_id in list(self._down):
                self._up(pointer_id)
            self._directions.clear()
            self._pulses.clear()
            self._aim_frozen = False
            self._lift_aim()
            self._aim_pending = [0.0, 0.0]

    # ------------------------------------------------------------- cámara
    def _has_pending(self) -> bool:
        return self._aim_pending[0] != 0 or self._aim_pending[1] != 0

    def _aim_geometry(self) -> tuple[tuple[float, float] | None, float]:
        screen = self.sink.screen
        if not screen or self._aim is None:
            return None, 0.0
        width, height = screen
        center = (self._aim.x * width, self._aim.y * height)
        return center, max(self._aim.radius * height, 20.0)

    def _land(self, point: tuple[float, float]) -> tuple[float, float]:
        """Dónde apoyar el dedo de la cámara: lo más cerca posible de ``point``
        pero dentro de los márgenes y fuera de los botones del juego."""
        width, height = self.sink.screen
        margin_x, margin_y = SCREEN_MARGIN_X * width, SCREEN_MARGIN_Y * height
        x, y = point
        for _ in range(4):  # empujar fuera de un botón puede meterlo en otro
            moved = False
            for kx, ky, kr in self._keepouts:
                cx, cy, radius = kx * width, ky * height, kr * height
                distance = math.hypot(x - cx, y - cy)
                if distance < radius:
                    if distance < 1e-6:
                        x, y, distance = cx + 1.0, cy, 1.0
                    x = cx + (x - cx) / distance * radius
                    y = cy + (y - cy) / distance * radius
                    moved = True
            x = min(max(x, margin_x), width - margin_x)
            y = min(max(y, margin_y), height - margin_y)
            if not moved:
                break
        return x, y

    def _flush_aim(self, now: float) -> None:
        center, limit = self._aim_geometry()
        if center is None:
            return
        self._aim_last_send = now
        if self._aim_pos is None:
            # Se apoya el dedo; el movimiento acumulado va en el próximo envío.
            self._aim_pos = self._land(center)
            self._touch(ACTION_DOWN, AIM_POINTER_ID, *self._aim_pos)
            return
        width, height = self.sink.screen
        step = math.hypot(*self._aim_pending)
        if step > AIM_MAX_STEP * limit:
            self._aim_pending = [0.0, 0.0]  # salto absurdo del mouse: se descarta
            return
        x = self._aim_pos[0] + self._aim_pending[0]
        y = self._aim_pos[1] + self._aim_pending[1]
        margin_x, margin_y = SCREEN_MARGIN_X * width, SCREEN_MARGIN_Y * height
        inside = margin_x <= x <= width - margin_x and margin_y <= y <= height - margin_y
        if not inside or math.dist((x, y), center) > limit:
            # Sin recorrido: se levanta y se vuelve a apoyar cerca del centro;
            # lo acumulado se aplica en el próximo envío.
            self._touch(ACTION_UP, AIM_POINTER_ID, *self._aim_pos)
            length = math.hypot(*self._aim_pending) or 1.0
            back = AIM_RECENTER_BACK * limit / length
            start = self._land((
                center[0] - self._aim_pending[0] * back,
                center[1] - self._aim_pending[1] * back,
            ))
            self._aim_pos = start
            self._touch(ACTION_DOWN, AIM_POINTER_ID, *start)
            return
        self._aim_pending = [0.0, 0.0]
        self._aim_pos = (x, y)
        self._touch(ACTION_MOVE, AIM_POINTER_ID, x, y)

    def _lift_aim(self) -> None:
        if self._aim_pos is not None:
            self._touch(ACTION_UP, AIM_POINTER_ID, *self._aim_pos)
            self._aim_pos = None

    # ------------------------------------------------------------- toques
    def _touch(self, action: int, pointer_id: int, x: float, y: float) -> None:
        self.sink.touch(action, pointer_id, x, y)

    def _to_px(self, nx: float, ny: float) -> tuple[float, float] | None:
        screen = self.sink.screen
        if not screen:
            return None
        return nx * screen[0], ny * screen[1]

    def _press(self, pointer_id: int, nx: float, ny: float) -> None:
        if pointer_id in self._down:
            return
        pos = self._to_px(nx, ny)
        if pos is None:
            return
        self._down[pointer_id] = pos
        self._touch(ACTION_DOWN, pointer_id, *pos)

    def _up(self, pointer_id: int) -> None:
        pos = self._down.pop(pointer_id, None)
        if pos is not None:
            self._touch(ACTION_UP, pointer_id, *pos)

    def _joystick_key(self, control: Control, pointer_id: int, key: str, pressed: bool) -> None:
        direction = next((d for d, k in control.keys.items() if k == key), None)
        if direction is None:
            return
        if pressed:
            self._directions.add(direction)
        else:
            self._directions.discard(direction)

        vx = sum(_DIRECTION_VECTORS[d][0] for d in self._directions)
        vy = sum(_DIRECTION_VECTORS[d][1] for d in self._directions)
        if vx == 0 and vy == 0:
            self._up(pointer_id)
            return
        screen = self.sink.screen
        if not screen:
            return
        width, height = screen
        length = math.hypot(vx, vy)
        reach = control.radius * height
        cx, cy = control.x * width, control.y * height
        target = (cx + vx / length * reach, cy + vy / length * reach)

        start = self._down.get(pointer_id)
        if start is None:
            self._down[pointer_id] = (cx, cy)
            self._touch(ACTION_DOWN, pointer_id, cx, cy)
            start = (cx, cy)
        for step in range(1, JOYSTICK_STEPS + 1):
            t = step / JOYSTICK_STEPS
            x = start[0] + (target[0] - start[0]) * t
            y = start[1] + (target[1] - start[1]) * t
            self._touch(ACTION_MOVE, pointer_id, x, y)
        self._down[pointer_id] = target
