"""
Mapeo de teclado y mouse a toques, al estilo de los emuladores de Android.

    keymap/model.py     el mapa de teclas (controles, posiciones) y su YAML
    keymap/control.py   sesión de control con scrcpy-server: manda toques
    keymap/engine.py    traduce teclas/mouse a toques (lógica pura)
    keymap/inputs.py    lee teclado y mouse del kernel con evdev
    keymap/runner.py    une todo: lo que corre mientras se juega

No se instala nada en el celular: se usa el mismo scrcpy-server que trae
scrcpy, en una segunda sesión solo de control (sin video ni audio).
"""
