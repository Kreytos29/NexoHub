"""Mapeo de teclas: protocolo, modelo y motor (sin celular ni evdev)."""
import struct

import pytest

from keymap import model
from keymap.control import ACTION_DOWN, ACTION_MOVE, ACTION_UP, encode_touch, parse_screen
from keymap.engine import CURSOR_HOLD, AIM_IDLE_LIFT, AIM_IDLE_LIFT_ANY, AIM_INTERVAL, AIM_POINTER_ID, Engine
from keymap.inputs import KeyTracker
from keymap.model import Control, Keymap


class FakeSink:
    def __init__(self, screen=(2000, 1000)):
        self.screen = screen
        self.events = []

    def touch(self, action, pointer_id, x, y):
        self.events.append((action, pointer_id, round(x), round(y)))

    def take(self):
        events, self.events = self.events, []
        return events


def make_keymap():
    return Keymap(
        name="test",
        controls=[
            Control(kind="joystick", x=0.2, y=0.7, radius=0.1,
                    keys={"up": "KEY_W", "left": "KEY_A", "down": "KEY_S", "right": "KEY_D"}),
            Control(kind="aim", x=0.5, y=0.5, radius=0.2, sensitivity=2.0, sensitivity_y=2.0),
            Control(kind="tap", key="BTN_LEFT", x=0.9, y=0.6, name="Disparar"),
            Control(kind="tap", key="KEY_SPACE", x=0.9, y=0.8, name="Saltar"),
        ],
    )


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def engine():
    sink = FakeSink()
    states, commands = [], []
    clock = FakeClock()
    eng = Engine(make_keymap(), sink, states.append, commands.append, clock=clock)
    eng.states, eng.commands, eng.clock_ = states, commands, clock
    eng.set_active(True)
    sink.take()
    return eng


# --- protocolo -------------------------------------------------------------
def test_encode_touch_layout():
    data = encode_touch(ACTION_DOWN, 12, 100, 200, 2712, 1220)
    assert len(data) == 32
    fields = struct.unpack(">BBqiiHHHii", data)
    assert fields == (2, ACTION_DOWN, 12, 100, 200, 2712, 1220, 0xFFFF, 0, 0)


def test_encode_touch_clamps_to_screen_and_up_has_no_pressure():
    fields = struct.unpack(">BBqiiHHHii", encode_touch(ACTION_UP, 1, -5, 5000, 100, 50))
    assert fields[3:5] == (0, 49)
    assert fields[7] == 0 and fields[9] == 0


@pytest.mark.parametrize(
    "output, expected",
    [
        ("Physical size: 1220x2712\nmCurrentRotation=ROTATION_90", (2712, 1220)),
        ("Physical size: 1220x2712\nmCurrentRotation=ROTATION_0", (1220, 2712)),
        ("Physical size: 1220x2712\nOverride size: 1080x1920\nmCurrentRotation=ROTATION_270", (1920, 1080)),
        ("nada", None),
    ],
)
def test_parse_screen(output, expected):
    assert parse_screen(output) == expected


# --- modelo ----------------------------------------------------------------
def test_freefire_preset_loads_without_conflicts():
    keymap = model.load("freefire")
    assert keymap.package == "com.dts.freefireth"
    assert keymap.aim() is not None
    assert keymap.conflicts() == []


def test_save_roundtrip_and_user_copy_wins():
    keymap = model.load("freefire")
    keymap.controls[2].x = 0.5
    path = model.save(keymap)
    assert path.parent == model.user_dir()
    assert model.has_user_copy("freefire")
    assert model.load("freefire").controls[2].x == 0.5
    assert model.load_preset("freefire").controls[2].x != 0.5
    assert model.delete_user_copy("freefire")
    assert model.load("freefire").controls[2].x != 0.5


def test_conflicts_detected():
    keymap = make_keymap()
    keymap.controls.append(Control(kind="tap", key="KEY_W", x=0, y=0, name="Otro"))
    keymap.controls.append(Control(kind="tap", key="KEY_F10", x=0, y=0, name="Malo"))
    keymap.suspend_key = "KEY_F9"
    problems = keymap.conflicts()
    assert any("KEY_W" in p for p in problems)
    assert any("KEY_F10" in p for p in problems)
    assert any("KEY_F9" in p for p in problems)


def test_bad_control_kind_raises():
    with pytest.raises(model.KeymapError):
        Control.from_dict({"kind": "laser", "x": 0, "y": 0})


# --- motor -----------------------------------------------------------------
def release(eng, key):
    eng.handle_key(key, True)
    eng.handle_key(key, False)


def test_inactive_ignores_mapped_keys_and_toggle_acts_on_release():
    sink = FakeSink()
    eng = Engine(make_keymap(), sink)
    assert eng.handle_key("KEY_SPACE", True) is False
    assert eng.handle_key("KEY_F9", True) is True
    assert eng.state == "off"  # todavía apretada
    eng.handle_key("KEY_F9", False)
    assert eng.state == "active"
    assert sink.events == []


def test_tap_press_and_release(engine):
    assert engine.handle_key("KEY_SPACE", True)
    assert engine.handle_key("KEY_SPACE", False)
    (down, up) = engine.sink.take()
    assert down[0] == ACTION_DOWN and up[0] == ACTION_UP
    assert down[2:] == (1800, 800) == up[2:]
    assert down[1] == up[1]


def test_unmapped_key_not_consumed(engine):
    assert engine.handle_key("KEY_ESC", True) is False
    assert engine.sink.events == []


def test_joystick_moves_and_diagonal(engine):
    engine.handle_key("KEY_W", True)
    events = engine.sink.take()
    assert events[0][0] == ACTION_DOWN and events[0][2:] == (400, 700)
    assert events[-1][0] == ACTION_MOVE and events[-1][2:] == (400, 600)  # radio 0.1*1000
    engine.handle_key("KEY_D", True)
    assert engine.sink.take()[-1][2:] == (471, 629)  # diagonal normalizada
    engine.handle_key("KEY_W", False)
    assert engine.sink.take()[-1][2:] == (500, 700)
    engine.handle_key("KEY_D", False)
    assert engine.sink.take() == [(ACTION_UP, 10, 500, 700)]


def test_aim_puts_finger_down_then_moves_on_next_tick(engine):
    engine.handle_mouse(10, 5)
    # Primero solo se apoya el dedo: el juego ignora el movimiento del
    # cuadro en que empieza el toque.
    assert engine.sink.take() == [(ACTION_DOWN, AIM_POINTER_ID, 1000, 500)]
    assert engine.next_deadline() == pytest.approx(AIM_INTERVAL)
    engine.clock_.advance(AIM_INTERVAL)
    engine.tick()
    assert engine.sink.take() == [(ACTION_MOVE, AIM_POINTER_ID, 1020, 510)]  # sensibilidad 2


def test_aim_coalesces_fast_mouse_events(engine):
    engine.handle_mouse(1, 0)
    engine.clock_.advance(AIM_INTERVAL)
    engine.tick()
    engine.sink.take()
    # 1000 Hz: muchos eventos dentro del mismo intervalo -> un solo envío.
    for _ in range(3):
        engine.clock_.advance(AIM_INTERVAL / 4)
        engine.handle_mouse(1, 0)
    engine.clock_.advance(AIM_INTERVAL / 4)
    engine.handle_mouse(1, 0)  # ya pasó el intervalo: se manda todo junto
    assert engine.sink.take() == [(ACTION_MOVE, AIM_POINTER_ID, 1010, 500)]


def test_aim_recenters_when_out_of_range(engine):
    engine.handle_mouse(0, 0.0001)
    for _ in range(2):
        engine.clock_.advance(AIM_INTERVAL)
        engine.handle_mouse(40, 0)  # 80 px por paso
    engine.sink.take()
    engine.clock_.advance(AIM_INTERVAL)
    engine.handle_mouse(40, 0)  # se pasa del radio (200)
    events = engine.sink.take()
    assert [e[0] for e in events] == [ACTION_UP, ACTION_DOWN]
    # Se reapoya medio radio "atrás" del giro (hacia la izquierda): más recorrido.
    assert events[1][2:] == (900, 500)
    engine.clock_.advance(AIM_INTERVAL)
    engine.tick()  # lo acumulado se aplica desde ahí
    assert engine.sink.take() == [(ACTION_MOVE, AIM_POINTER_ID, 980, 500)]


def test_mouse_spike_is_discarded_instead_of_flinging_the_finger(engine):
    engine.handle_mouse(0, 0.0001)
    engine.clock_.advance(AIM_INTERVAL)
    engine.sink.take()
    engine.handle_mouse(0, -4000)  # un botón del mouse que manda un salto enorme
    assert engine.sink.take() == []
    engine.clock_.advance(AIM_INTERVAL)
    engine.handle_mouse(3, 0)
    assert engine.sink.take() == [(ACTION_MOVE, AIM_POINTER_ID, 1006, 500)]


def test_cursor_key_tap_toggles_and_hold_is_temporary(engine):
    engine.handle_key("KEY_LEFTALT", True)
    assert engine.state == "cursor"  # se libera en cuanto se aprieta
    engine.clock_.advance(CURSOR_HOLD + 0.1)
    engine.handle_key("KEY_LEFTALT", False)
    assert engine.state == "active"  # mantenida: al soltar vuelve al juego
    release(engine, "KEY_LEFTALT")
    assert engine.state == "cursor"  # toque corto: queda libre
    engine.clock_.advance(0.5)
    release(engine, "KEY_LEFTALT")
    assert engine.state == "active"


def test_aim_rate_comes_from_the_keymap():
    keymap = make_keymap()
    keymap.aim_rate = 50
    eng = Engine(keymap, FakeSink())
    eng.set_active(True)
    eng.handle_mouse(1, 0)
    assert eng.next_deadline() == pytest.approx(0.02, abs=0.005)
    assert Keymap.from_dict("x", {"aim_rate": 99999}).aim_rate == model.AIM_RATE_RANGE[1]


def test_aim_lifts_when_idle_far_from_center(engine):
    engine.handle_mouse(0, 0.0001)
    engine.clock_.advance(AIM_INTERVAL)
    engine.handle_mouse(50, 0)  # 100 px del centro (> 35% de 200)
    engine.sink.take()
    engine.clock_.advance(0.1)
    engine.tick()
    assert engine.sink.take() == []
    engine.clock_.advance(0.2)
    engine.tick()
    assert engine.sink.take() == [(ACTION_UP, AIM_POINTER_ID, 1100, 500)]
    assert engine.next_deadline() is None


def test_cursor_mode_lifts_aim_and_frees_mouse_buttons(engine):
    engine.handle_mouse(5, 0)
    engine.handle_key("BTN_LEFT", True)
    engine.sink.take()
    release(engine, "KEY_LEFTALT")
    assert engine.state == "cursor"
    assert not engine.captures_mouse and engine.captures_keyboard
    actions = {(e[0], e[1]) for e in engine.sink.take()}
    assert (ACTION_UP, AIM_POINTER_ID) in actions
    assert any(a == ACTION_UP and p != AIM_POINTER_ID for a, p in actions)
    # Con cursor: el mouse no mueve la cámara y sus botones van al PC.
    engine.handle_mouse(5, 5)
    assert engine.sink.take() == []
    assert engine.handle_key("BTN_LEFT", True) is False
    # El teclado sigue mapeado.
    assert engine.handle_key("KEY_SPACE", True) is True
    assert engine.states[-1] == "cursor"


def test_suspend_with_mouse_button_releases_everything_and_resumes():
    keymap = make_keymap()
    keymap.suspend_key = "BTN_SIDE"
    sink = FakeSink()
    clock = FakeClock()
    eng = Engine(keymap, sink, clock=clock)
    eng.set_active(True)
    eng.handle_key("KEY_W", True)
    sink.take()
    release(eng, "BTN_SIDE")
    assert eng.state == "suspended"
    assert not eng.captures_keyboard and not eng.captures_mouse
    assert [e[0] for e in sink.take()] == [ACTION_UP]
    assert eng.handle_key("KEY_SPACE", True) is False  # en pausa las teclas van al PC
    clock.advance(1)
    release(eng, "BTN_SIDE")
    assert eng.state == "active"


def test_special_key_repeated_by_another_device_acts_once(engine):
    # Un teclado que aparece como dos dispositivos manda la pulsación dos veces.
    release(engine, "KEY_F8")
    release(engine, "KEY_F8")
    assert engine.state == "suspended"
    engine.clock_.advance(0.5)
    release(engine, "KEY_F8")
    assert engine.state == "active"


def test_aim_finger_lifts_after_idle_even_at_center(engine):
    engine.handle_mouse(0, 0.0001)
    engine.clock_.advance(AIM_INTERVAL)
    engine.handle_mouse(2, 0)  # casi en el centro
    engine.sink.take()
    engine.clock_.advance(AIM_IDLE_LIFT + 0.05)
    engine.tick()
    assert engine.sink.take() == []  # cerca del centro: aún apoyado
    engine.clock_.advance(AIM_IDLE_LIFT_ANY)
    engine.tick()
    assert [e[0] for e in engine.sink.take()] == [ACTION_UP]


def test_key_tracker_counts_devices():
    tracker = KeyTracker()
    assert tracker.press("BTN_LEFT", "a") is True
    assert tracker.press("BTN_LEFT", "b") is False
    assert tracker.release("BTN_LEFT", "a") is False  # b todavía la tiene
    assert tracker.release("BTN_LEFT", "b") is True
    assert tracker.release("BTN_LEFT", "b") is True  # nunca vista: cuenta como última
    tracker.press("KEY_W", "a")
    assert tracker.drop_source("a") == ["KEY_W"]  # el dispositivo se desconectó


def test_no_finger_left_down_after_random_input(engine):
    import random

    rng = random.Random(7)
    keys = ["BTN_LEFT", "KEY_SPACE", "KEY_W", "KEY_A", "KEY_S", "KEY_D", "KEY_F8", "KEY_LEFTALT"]
    down = set()
    for _ in range(3000):
        roll = rng.random()
        if roll < 0.5:
            engine.handle_mouse(rng.randint(-60, 60), rng.randint(-60, 60))
        elif roll < 0.8:
            key = rng.choice(keys)
            pressed = key not in down
            (down.add if pressed else down.discard)(key)
            engine.handle_key(key, pressed)
        engine.clock_.advance(rng.choice([0.001, 0.01, 0.3]))
        engine.tick()
    engine.set_active(False)
    fingers = set()
    for action, pointer, _x, _y in engine.sink.take():
        if action == ACTION_DOWN:
            fingers.add(pointer)
        elif action == ACTION_UP:
            fingers.discard(pointer)
    assert fingers == set()


def test_editor_key_turns_off_and_requests_editor(engine):
    engine.handle_key("KEY_SPACE", True)
    engine.sink.take()
    release(engine, "KEY_F10")
    assert engine.state == "off"
    assert engine.commands == ["editor"]
    assert [e[0] for e in engine.sink.take()] == [ACTION_UP]


def test_overlay_key_only_while_active(engine):
    release(engine, "KEY_F7")
    assert engine.commands == ["overlay"]
    engine.set_active(False)
    assert engine.handle_key("KEY_F7", False) is False


def test_set_keymap_live_reload(engine):
    engine.handle_key("KEY_SPACE", True)
    engine.sink.take()
    new = make_keymap()
    new.controls[3].key = "KEY_J"
    engine.set_keymap(new)
    assert [e[0] for e in engine.sink.take()] == [ACTION_UP]
    assert engine.state == "active"
    assert engine.handle_key("KEY_SPACE", True) is False
    assert engine.handle_key("KEY_J", True) is True


def test_deactivate_releases_everything(engine):
    engine.handle_key("KEY_W", True)
    engine.handle_key("KEY_SPACE", True)
    engine.handle_mouse(3, 3)
    engine.sink.take()
    release(engine, "KEY_F9")
    ups = [e for e in engine.sink.take() if e[0] == ACTION_UP]
    assert len(ups) == 3
    assert engine.states[-1] == "off"


# --- migración -------------------------------------------------------------
def test_v1_keymap_is_migrated():
    old = {
        "toggle_key": "KEY_F10",
        "cursor_key": "KEY_LEFTALT",
        "controls": [
            {"kind": "aim", "x": 0.6, "y": 0.4, "radius": 0.3, "sensitivity": 1.0},
            {"kind": "tap", "key": "KEY_R", "x": 0.1, "y": 0.2},
        ],
    }
    keymap = Keymap.from_dict("viejo", old)
    assert keymap.editor_key == "KEY_F10" and keymap.toggle_key == "KEY_F9"
    aim = keymap.aim()
    assert (aim.radius, aim.sensitivity, aim.sensitivity_y) == (0.45, 0.5, 0.5)
    assert keymap.controls[1].size == pytest.approx(0.045)
    assert keymap.conflicts() == []


def test_tuned_aim_is_not_migrated():
    data = {"controls": [{"kind": "aim", "x": 0.6, "y": 0.4, "radius": 0.5, "sensitivity": 0.8}]}
    aim = Keymap.from_dict("x", data).aim()
    assert (aim.radius, aim.sensitivity, aim.sensitivity_y) == (0.5, 0.8, 0.8)


def test_service_reports_a_missing_map_over_the_protocol():
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "keymap.service", "--serial", "x", "--map", "no-existe-este-mapa"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    message = json.loads(result.stdout.strip().splitlines()[-1])
    assert message["type"] == "failed" and "no-existe-este-mapa" in message["message"]


def test_aim_suspend_key_freezes_camera_while_held_and_toggles():
    keymap = make_keymap()
    aim = keymap.aim()
    aim.suspend_key = "KEY_LEFTSHIFT"
    sink, clock = FakeSink(), FakeClock()
    eng = Engine(keymap, sink, clock=clock)
    eng.set_active(True)
    eng.handle_mouse(5, 0)
    assert sink.take()  # el dedo se apoyó
    assert eng.handle_key("KEY_LEFTSHIFT", True) is True
    assert [e[0] for e in sink.take()] == [ACTION_UP]  # se levanta al congelar
    clock.advance(AIM_INTERVAL)
    eng.handle_mouse(50, 50)
    assert sink.take() == []  # congelada: el mouse no mueve nada
    assert eng.captures_mouse  # pero sigue capturado (no es el cursor libre)
    eng.handle_key("KEY_LEFTSHIFT", False)
    clock.advance(AIM_INTERVAL)
    eng.handle_mouse(5, 0)
    assert sink.take()  # vuelve a mover

    aim.suspend_mode = "toggle"
    eng.set_keymap(keymap)
    sink.take()
    release(eng, "KEY_LEFTSHIFT")
    sink.take()  # al congelar se levanta el dedo
    clock.advance(1)
    eng.handle_mouse(5, 0)
    assert sink.take() == []
    release(eng, "KEY_LEFTSHIFT")
    clock.advance(1)
    eng.handle_mouse(5, 0)
    assert sink.take()


def test_cursor_mode_toggle_and_hold(engine):
    engine.keymap.cursor_mode = "toggle"
    engine.handle_key("KEY_LEFTALT", True)
    assert engine.state == "active"  # en toggle apretar no hace nada
    engine.handle_key("KEY_LEFTALT", False)
    assert engine.state == "cursor"
    engine.clock_.advance(1)
    release(engine, "KEY_LEFTALT")
    assert engine.state == "active"
    engine.keymap.cursor_mode = "hold"
    engine.clock_.advance(1)
    engine.handle_key("KEY_LEFTALT", True)
    assert engine.state == "cursor"
    engine.handle_key("KEY_LEFTALT", False)  # aunque sea un toque corto
    assert engine.state == "active"


def test_wheel_taps_for_a_short_moment_and_is_consumed():
    keymap = make_keymap()
    keymap.controls.append(Control(kind="tap", key="WHEEL_UP", x=0.3, y=0.3, name="Arma siguiente"))
    sink, clock = FakeSink(), FakeClock()
    eng = Engine(keymap, sink, clock=clock)
    assert eng.handle_wheel(1) is False  # desactivado: la rueda va al PC
    eng.set_active(True)
    sink.take()
    assert eng.handle_wheel(-1) is False  # sin control para «abajo»
    assert eng.handle_wheel(1) is True
    assert [e[0] for e in sink.take()] == [ACTION_DOWN]
    assert eng.next_deadline() == pytest.approx(0.04, abs=0.001)
    eng.tick()
    assert sink.take() == []  # todavía no
    clock.advance(0.05)
    eng.tick()
    assert [e[0] for e in sink.take()] == [ACTION_UP]
    assert eng.next_deadline() is None


def test_wheel_pulse_is_cancelled_by_pause():
    keymap = make_keymap()
    keymap.controls.append(Control(kind="tap", key="WHEEL_DOWN", x=0.3, y=0.3))
    sink = FakeSink()
    eng = Engine(keymap, sink, clock=FakeClock())
    eng.set_active(True)
    eng.handle_wheel(-1)
    sink.take()
    eng.set_state("suspended")
    assert [e[0] for e in sink.take()] == [ACTION_UP]
    assert eng.next_deadline() is None


def test_quick_key_turns_off_and_requests_quick_settings(engine):
    release(engine, "KEY_F6")
    assert engine.state == "off"
    assert engine.commands[-1] == "quick"


def test_camera_finger_never_lands_on_a_game_button():
    import math
    import random

    keymap = make_keymap()
    # «Disparar» justo en el centro de la cámara: el dedo no puede apoyarse ahí.
    keymap.controls.append(Control(kind="tap", key="KEY_R", x=0.5, y=0.5, size=0.045, name="Recargar"))
    sink, clock = FakeSink(), FakeClock()
    eng = Engine(keymap, sink, clock=clock)
    eng.set_active(True)
    rng = random.Random(3)
    downs = []
    for _ in range(400):
        clock.advance(rng.choice([0.01, 0.3]))
        eng.handle_mouse(rng.randint(-40, 40), rng.randint(-40, 40))
        eng.tick()
        downs += [e for e in sink.take() if e[0] == ACTION_DOWN and e[1] == AIM_POINTER_ID]
    assert downs
    taps = [(0.9 * 2000, 0.6 * 1000), (0.9 * 2000, 0.8 * 1000), (0.5 * 2000, 0.5 * 1000)]
    for _a, _p, x, y in downs:
        for tx, ty in taps:
            assert math.hypot(x - tx, y - ty) >= 0.045 * 1000, (x, y, tx, ty)


def test_pause_button_held_is_temporary_and_a_tap_toggles():
    keymap = make_keymap()
    keymap.suspend_key = "BTN_EXTRA"  # como un botón lateral del mouse
    sink, clock = FakeSink(), FakeClock()
    eng = Engine(keymap, sink, clock=clock)
    eng.set_active(True)
    eng.handle_key("BTN_EXTRA", True)
    assert eng.state == "suspended"  # el mouse queda libre en cuanto se aprieta
    clock.advance(1.0)
    eng.handle_key("BTN_EXTRA", False)
    assert eng.state == "active"  # mantenida: al soltar vuelve al juego
    clock.advance(1.0)
    release(eng, "BTN_EXTRA")
    assert eng.state == "suspended"  # toque corto: sigue en pausa
    clock.advance(1.0)
    release(eng, "BTN_EXTRA")
    assert eng.state == "active"
