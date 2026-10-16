# Cambios

## Sin publicar

### Corregido (mapeo de teclas)
- Disparo/botones que se quedaban pegados y retraso al apretar teclas: el mapeo corre
  ahora en un proceso aparte (`keymap/service.py`), los toques se mandan desde su propio
  hilo, y se detectan los eventos de entrada perdidos (`SYN_DROPPED`) y los «soltar» que
  no llegaron, comparando con lo que el kernel dice que está apretado.
- Una misma pulsación que llega por varios dispositivos (teclado/mouse con varios nodos
  en /dev/input) se procesaba dos veces: pausa y cursor se anulaban solos.
- Saltos enormes del mouse (botones laterales que mandan movimiento) ya no lanzan el dedo
  de la cámara por la pantalla; el dedo no se acerca a los bordes (notificaciones/atrás).
- `python -m keymap --sniff 15` muestra los eventos crudos de teclado y mouse.

### Nuevo
- **Rueda del mouse mapeable** (`Rueda ↑` / `Rueda ↓` como tecla de un control): cada clic
  de la rueda da un toque corto, por ejemplo para cambiar de arma.
- **Ajustes rápidos en juego** (F6 por defecto): sensibilidad de la cámara y su frecuencia
  con el cambio aplicado al instante; al cerrar vuelves al modo juego.
- **Cámara: tecla para congelarla** (en el control de cámara del editor) sin soltar el
  mouse, mantenida o alternando; **modo del cursor** (automático, alternar o mantener) y
  **frecuencia de envío de la cámara** configurable (125 Hz por defecto).
- **Mapeo de teclado y mouse a toques**, como en los emuladores: joystick con WASD,
  cámara con el mouse, teclas y botones del mouse sobre los botones del juego. Los toques
  van por una sesión de `scrcpy-server` solo de control: no se instala nada en el celular.
- Teclas especiales configurables (también botones del mouse): F9 activar/desactivar,
  F8 pausar, Alt cursor, F10 abre el editor en pleno juego (se aplica al guardar, sin
  relanzar) y F7 muestra/oculta las teclas.
- **Teclas dibujadas sobre el juego**, como BlueStacks (gtk-layer-shell, Hyprland).
- **Editor visual** de mapas sobre una captura del celular: captura de teclas, tamaño de
  cada tecla (rueda del mouse), sensibilidad horizontal y vertical, opacidad.
- Cámara fluida: el movimiento del mouse se agrupa y se manda a ritmo fijo, el dedo
  virtual tiene una zona grande y se reapoya hacia atrás del giro o cuando el mouse está
  quieto.
- Mapa de fábrica para **Free Fire** y opción de crear mapas para otros juegos.
- `python -m keymap` para usar el mapeo sin la interfaz.
- Dependencia nueva: `evdev` (leer teclado y mouse en Wayland).

## 1.1.0

### Nuevo
- **Detección de celulares múltiples**: selector con modelo y tipo de conexión; detecta
  solo al enchufar y avisa al desenchufar.
- **Telemetría** del celular: batería y temperatura, con aviso si se calienta.
- **Capturas de pantalla** (`F12`, botón de cámara, `--screenshot`) que se copian al
  portapapeles.
- **Grabación** de la sesión (mkv/mp4) con botón «Grabar» y `--record`.
- **Perfiles propios**: guardar los ajustes actuales como una tarjeta más y eliminarlos.
- Opciones de scrcpy nuevas: ventana siempre encima, sin bordes, título, orientación de
  captura, mantener despierto, apagar la pantalla del celular, mostrar toques, apagar al
  cerrar, buffer de video, gamepad.
- **Atajos de teclado**, ventana «Acerca de» (versiones, rutas, copiar datos para
  soporte), geometría de ventana y secciones recordadas entre sesiones.
- **Registro de actividad** en `~/.local/state/nexohub/nexohub.log` (rotativo).
- Línea de comandos: `--version`, `--list-devices`, `--serial`, `--screenshot`, `--record`.
- Pruebas automáticas (unitarias y de interfaz con `adb`/`scrcpy` simulados) y CI.

### Interfaz
- Rediseño completo: una sola pantalla con tarjeta del celular, perfiles como tarjetas,
  secciones plegables con resumen, barra de acciones fija, avisos tipo «toast» con acción
  y chip de estado en vivo (tiempo y FPS).
- Íconos vectoriales propios en lugar de emojis, y logo.
- El código de la interfaz se separó en el paquete `ui/` (antes, un solo `gui.py` de
  3 000 líneas).

### Configuración
- Dos capas: `config.yaml` de fábrica (solo lectura, con comentarios) y
  `~/.config/nexohub/config.yaml` con lo que cambia el usuario. «Guardar» ya no borra
  los comentarios del archivo. `install.sh` migra los ajustes de versiones anteriores.

### Correcciones
- `audio_route: off` sin comillas se leía como booleano y la app activaba el audio de
  scrcpy sin que lo pidieras.
- Los valores «automático» del formulario podían ser pisados por valores viejos de la
  configuración.
- El error real de la detección se perdía y se mostraba un mensaje genérico.
- Un perfil con `audio: true` era anulado por `audio_route: off` en la CLI.
- Cerrar la ventana con una transmisión activa podía destruir un hilo en ejecución.
- Un `adb` colgado ya no congela la detección (tiempo de espera con mensaje claro).
- Si `screeninfo` no puede leer el monitor (Wayland puro), la GUI usa los datos de Qt y la
  CLI asume 1920×1080 en lugar de fallar. `--screenshot` ya no necesita detectar el monitor.
- Ortografía: tildes y mensajes de error unificados.

## 1.0.0
- Primera versión: detección de celular, USB, monitor y CPU; constructor de comandos;
  GUI con Dependencias, WiFi, audio por Bluetooth/WiFi, resolución del celular y overlay
  de FPS.
