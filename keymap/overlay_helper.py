#!/usr/bin/env python3
"""
Proceso auxiliar: dibuja las teclas del mapa encima del juego, como el
overlay de controles de BlueStacks.

Igual que fps_overlay_helper.py, es un proceso GTK3 aparte porque en Wayland
solo wlr-layer-shell garantiza quedar por encima de todo (incluida la
ventana de scrcpy en pantalla completa) y GtkLayerShell no tiene bindings
para Qt. La superficie cubre el monitor, no recibe clics (pasan a scrcpy) y
solo pinta sobre la zona donde scrcpy muestra la pantalla del celular, que
se ubica preguntándole a Hyprland (``hyprctl clients``) cada 250 ms.

Recibe por stdin una línea JSON por mensaje:

    {"type": "keymap", "controls": [...], "keys": {...}, "opacity": 0.7}
    {"type": "screen", "width": 2712, "height": 1220}
    {"type": "state", "state": "off|active|cursor|suspended"}
    {"type": "visible", "value": true}

y se cierra cuando stdin llega a EOF (NexoHub cerró el mapeo).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

POLL_MS = 250
ACCENT = (0.878, 0.188, 0.247)  # #e0303f, el rojo de NexoHub
GOLD = (0.91, 0.72, 0.29)
_HYPRCTL = shutil.which("hyprctl")


def _hyprctl(*args: str):
    if _HYPRCTL is None:
        return None
    try:
        result = subprocess.run([_HYPRCTL, "-j", *args], capture_output=True, text=True, timeout=1)
        return json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def scrcpy_geometry():
    """(monitor_x, monitor_y, x, y, ancho, alto) de la ventana de scrcpy en
    coordenadas del monitor, o None si no está visible."""
    clients = _hyprctl("clients")
    monitors = _hyprctl("monitors")
    if not clients or not monitors:
        return None
    active = {m["id"]: m for m in monitors}
    for client in clients:
        names = (str(client.get("class", "")) + str(client.get("initialClass", ""))).lower()
        if "scrcpy" not in names or not client.get("mapped", True) or client.get("hidden"):
            continue
        monitor = active.get(client.get("monitor"))
        if monitor is None:
            continue
        workspace = client.get("workspace", {}).get("id")
        visible = workspace in (
            monitor.get("activeWorkspace", {}).get("id"),
            monitor.get("specialWorkspace", {}).get("id"),
        )
        if not visible:
            continue
        x, y = client["at"]
        width, height = client["size"]
        return (monitor["x"], monitor["y"], x - monitor["x"], y - monitor["y"], width, height)
    return None


def key_label(name: str) -> str:
    # Copia mínima de keymap.model.key_label (este proceso no importa el
    # paquete para arrancar rápido y no depender del cwd).
    labels = {
        "BTN_LEFT": "Clic", "BTN_RIGHT": "Clic der.", "BTN_MIDDLE": "Medio",
        "BTN_SIDE": "M4", "BTN_EXTRA": "M5", "KEY_SPACE": "Espacio",
        "KEY_LEFTALT": "Alt", "KEY_LEFTCTRL": "Ctrl", "KEY_LEFTSHIFT": "Shift",
        "KEY_TAB": "Tab", "KEY_CAPSLOCK": "Mayús", "KEY_GRAVE": "º",
        "KEY_SEMICOLON": "Ñ", "KEY_ESC": "Esc", "KEY_ENTER": "Enter",
        "WHEEL_UP": "Rueda ↑", "WHEEL_DOWN": "Rueda ↓",
    }
    if not name:
        return ""
    if name in labels:
        return labels[name]
    rest = name.split("_", 1)[-1]
    return rest if len(rest) <= 3 else rest.title()


class KeymapOverlay:
    def __init__(self) -> None:
        self.controls: list[dict] = []
        self.keys: dict[str, str] = {}
        self.opacity = 0.7
        self.screen = (2712, 1220)
        self.state = "off"
        self.visible = True
        self.geometry = None
        self.monitor_origin = None

        self.window = Gtk.Window()
        self.window.set_app_paintable(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual is not None:
            self.window.set_visual(visual)
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_namespace(self.window, "nexohub-keymap")
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_exclusive_zone(self.window, -1)
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self.window, edge, True)
        self.window.connect("draw", self._on_draw)
        self.window.connect("realize", lambda *_: self._make_click_through())
        self.window.connect("size-allocate", lambda *_: self._make_click_through())
        GLib.timeout_add(POLL_MS, self._poll)

    # ----------------------------------------------------------- mensajes
    def handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "keymap":
            self.controls = message.get("controls") or []
            self.keys = message.get("keys") or {}
            self.opacity = float(message.get("opacity", self.opacity))
        elif kind == "screen":
            self.screen = (int(message["width"]), int(message["height"]))
        elif kind == "state":
            self.state = str(message.get("state", "off"))
        elif kind == "visible":
            self.visible = bool(message.get("value"))
        self._refresh()

    # ------------------------------------------------------- visibilidad
    def _should_show(self) -> bool:
        return self.visible and self.state != "off" and self.geometry is not None

    def _refresh(self) -> None:
        if self._should_show():
            if not self.window.get_visible():
                self.window.show_all()
            self.window.queue_draw()
        elif self.window.get_visible():
            self.window.hide()

    def _poll(self) -> bool:
        # Sin Hyprland no se sabe dónde está scrcpy, así que no se dibuja.
        found = scrcpy_geometry()
        geometry = found[2:] if found else None
        origin = found[:2] if found else None
        if origin != self.monitor_origin and origin is not None:
            self.monitor_origin = origin
            self._set_monitor(origin)
        if geometry != self.geometry:
            self.geometry = geometry
            self._refresh()
        return True

    def _set_monitor(self, origin) -> None:
        display = Gdk.Display.get_default()
        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            area = monitor.get_geometry()
            if (area.x, area.y) == tuple(origin):
                was_visible = self.window.get_visible()
                if was_visible:
                    self.window.hide()
                GtkLayerShell.set_monitor(self.window, monitor)
                if was_visible:
                    self.window.show_all()
                return

    def _make_click_through(self) -> None:
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)

    # ------------------------------------------------------------- dibujo
    def _content_rect(self):
        """Zona de la ventana de scrcpy donde está la imagen del celular
        (scrcpy deja barras negras si la ventana no tiene su proporción)."""
        x, y, width, height = self.geometry
        sw, sh = self.screen
        scale = min(width / sw, height / sh)
        cw, ch = sw * scale, sh * scale
        return x + (width - cw) / 2, y + (height - ch) / 2, cw, ch

    def _on_draw(self, _widget, cr) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if not self._should_show():
            return False
        ox, oy, cw, ch = self._content_rect()
        dim = 0.35 if self.state == "suspended" else 1.0
        alpha = self.opacity * dim
        font = max(ch * 0.024, 10)
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(font)

        for control in self.controls:
            cx, cy = ox + control["x"] * cw, oy + control["y"] * ch
            kind = control.get("kind")
            if kind == "tap":
                radius = max(control.get("size", 0.045) * ch, font * 0.9)
                self._disc(cr, cx, cy, radius, alpha)
                self._text(cr, cx, cy, key_label(control.get("key", "")), alpha)
            elif kind == "joystick":
                radius = control.get("radius", 0.12) * ch
                cr.set_line_width(2)
                cr.set_source_rgba(1, 1, 1, 0.55 * alpha)
                cr.new_path()  # si no, Cairo une el círculo con el texto anterior
                cr.arc(cx, cy, radius, 0, 2 * math.pi)
                cr.stroke()
                keys = control.get("keys") or {}
                for direction, (dx, dy) in {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}.items():
                    px, py = cx + dx * radius, cy + dy * radius
                    self._disc(cr, px, py, font * 1.05, alpha)
                    self._text(cr, px, py, key_label(keys.get(direction, "")), alpha)
            elif kind == "aim":
                arm = font * 0.9
                cr.set_line_width(2)
                cr.set_source_rgba(*ACCENT, 0.9 * alpha)
                cr.new_path()
                cr.move_to(cx - arm, cy)
                cr.line_to(cx + arm, cy)
                cr.move_to(cx, cy - arm)
                cr.line_to(cx, cy + arm)
                cr.stroke()

        self._status_pill(cr, ox, oy, cw, font)
        return False

    @staticmethod
    def _disc(cr, x, y, radius, alpha) -> None:
        cr.set_source_rgba(0, 0, 0, 0.55 * alpha)
        cr.new_path()  # si no, Cairo une el círculo con el texto anterior
        cr.arc(x, y, radius, 0, 2 * math.pi)
        cr.fill_preserve()
        cr.set_line_width(1.6)
        cr.set_source_rgba(1, 1, 1, 0.75 * alpha)
        cr.stroke()

    @staticmethod
    def _text(cr, x, y, text, alpha, color=(1, 1, 1)) -> None:
        if not text:
            return
        extents = cr.text_extents(text)
        cr.set_source_rgba(*color, min(1.0, alpha * 1.3))
        cr.move_to(x - extents.width / 2 - extents.x_bearing, y - extents.height / 2 - extents.y_bearing)
        cr.show_text(text)

    def _status_pill(self, cr, ox, oy, cw, font) -> None:
        label = key_label
        if self.state == "suspended":
            text, color = f"En pausa · {label(self.keys.get('suspend_key', ''))} para seguir", GOLD
        elif self.state == "cursor":
            text, color = f"Cursor libre · {label(self.keys.get('cursor_key', ''))} para la cámara", GOLD
        else:
            text, color = f"Modo juego · {label(self.keys.get('toggle_key', ''))} para salir", ACCENT
        cr.set_font_size(font * 0.85)
        extents = cr.text_extents(text)
        width, height = extents.width + font * 1.4, font * 1.6
        x, y = ox + (cw - width) / 2, oy + font * 0.6
        radius = height / 2
        cr.new_path()
        cr.arc(x + radius, y + radius, radius, math.pi / 2, 3 * math.pi / 2)
        cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(0, 0, 0, 0.65)
        cr.fill_preserve()
        cr.set_source_rgba(*color, 0.9)
        cr.set_line_width(1.4)
        cr.stroke()
        self._text(cr, x + width / 2, y + height / 2, text, 1.0)


def main() -> None:
    overlay = KeymapOverlay()
    buffer = b""

    def on_stdin(_channel, _condition) -> bool:
        # Se lee del descriptor y no con sys.stdin.readline(): si llegan
        # varias líneas juntas, readline dejaría las demás en el buffer de
        # Python y GLib no volvería a avisar hasta que llegara algo más.
        nonlocal buffer
        chunk = os.read(sys.stdin.fileno(), 65536)
        if not chunk:
            Gtk.main_quit()
            return False
        buffer += chunk
        *lines, buffer = buffer.split(b"\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                overlay.handle(json.loads(line))
            except (ValueError, KeyError, TypeError) as exc:
                print(f"overlay: mensaje inválido: {exc}", file=sys.stderr)
        return True

    channel = GLib.IOChannel.unix_new(sys.stdin.fileno())
    GLib.io_add_watch(channel, GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP, on_stdin)
    Gtk.main()


if __name__ == "__main__":
    main()
