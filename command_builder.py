"""
Arma el comando scrcpy final a partir de lo detectado (celular, USB,
monitor, CPU). En esta v1 las reglas son fijas en Python (no un motor
generico todavia), pero CUALQUIER valor se puede sobreescribir desde
config.yaml sin tocar codigo -- ese es el contrato de esta función.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from detectors.android_device import AndroidDevice
from detectors.cpu import CpuStatus
from detectors.monitor import Monitor
from detectors.usb_topology import UsbTopology
from detectors.wireless import is_wireless_serial


@dataclass
class BuildResult:
    args: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_command(self, scrcpy_bin: str = "scrcpy") -> list[str]:
        return [scrcpy_bin, *self.args]

    def as_string(self, scrcpy_bin: str = "scrcpy") -> str:
        # shlex.join entrecomilla lo que lo necesite (ej. un título de
        # ventana con espacios), así el comando se puede pegar tal cual en
        # una terminal.
        return shlex.join(self.as_command(scrcpy_bin))


# Defaults que siempre se aplican. --stay-awake ya no está aquí: ahora es la
# opción "stay_awake" (activada por defecto) para poder apagarla desde la GUI.
BASE_FLAGS = ["--disable-screensaver"]
# Valor por defecto de scrcpy: pedirlo explícitamente no cambia nada.
SCRCPY_DEFAULT_AUDIO_BUFFER_MS = 50

# Valores válidos de --capture-orientation (con o sin "@" delante, que fija
# la rotación) y modos de --gamepad.
_ORIENTATION_RE = re.compile(r"^@?(flip)?(0|90|180|270)?$")
VALID_GAMEPAD_MODES = ("disabled", "uhid", "aoa")
VALID_RECORD_FORMATS = ("mkv", "mp4")


def _android_major_version(version: str) -> int | None:
    """Extrae el número de version mayor de algo tipo '16', '11', '7.1.2'."""
    try:
        return int(str(version).split(".")[0])
    except (ValueError, TypeError):
        return None


def build_audio_sidecar(serial: str, overrides: dict | None = None) -> BuildResult:
    """
    Arma el comando de la instancia "solo audio" de scrcpy para la ruta
    'wifi_dual': el video y el control siguen por USB en el comando
    principal (con --no-audio), y esta segunda instancia, aparte, solo
    trae el audio -- por WiFi, un canal de radio completamente distinto
    del cable, para que no compita con el video/input por el mismo
    tunel ni por el mismo encoder de video.

    --no-window evita que aparezca una segunda ventana de scrcpy: no hay
    nada que mirar (es audio puro), así que corre invisible en segundo
    plano. Requiere Android 11+ en el celular (API de captura de audio
    de reproducción); Android va a pedir confirmar un permiso en pantalla
    la primera vez que arranca cada instancia.
    """
    overrides = overrides or {}
    result = BuildResult(
        args=[
            "-s", serial,
            "--no-video",
            "--no-control",
            "--no-window",
            "--audio-source=playback",
        ]
    )

    audio_codec = overrides.get("audio_codec")
    result.args.append(f"--audio-codec={audio_codec if audio_codec and audio_codec != 'auto' else 'opus'}")

    audio_bitrate = overrides.get("audio_bitrate")
    if audio_bitrate:
        result.args.append(f"--audio-bit-rate={audio_bitrate}")

    audio_buffer = overrides.get("audio_buffer")
    if audio_buffer not in (None, ""):
        try:
            audio_buffer_ms = int(audio_buffer)
            if audio_buffer_ms >= 0:
                result.args.append(f"--audio-buffer={audio_buffer_ms}")
        except (TypeError, ValueError):
            pass

    return result


def build(
    device: AndroidDevice,
    usb: UsbTopology,
    monitor: Monitor,
    cpu: CpuStatus,
    overrides: dict | None = None,
) -> BuildResult:
    """
    overrides: dict cargado de config.yaml. Cualquier clave presente ahí
    gana sobre lo detectado automáticamente. Claves soportadas:
      video_codec, video_encoder, bitrate_max, max_size, max_fps,
      video_buffer, fps_overlay, fullscreen, input_mode, gamepad,
      stay_awake, turn_screen_off, show_touches, power_off_on_close,
      always_on_top, borderless, window_title, capture_orientation,
      audio, audio_route, audio_codec, audio_bitrate, audio_buffer,
      record, record_path, record_format, extra_flags (list)
    """
    overrides = overrides or {}
    result = BuildResult(args=list(BASE_FLAGS))

    # --- serial explicito ---
    # Siempre se fija -s <serial>, incluso con un solo celular conectado.
    # Es imprescindible en modo WiFi: mientras se activa la conexion por
    # WiFi el cable USB puede seguir puesto, asi que ADB ve DOS entradas
    # para el mismo telefono (la USB y la ip:puerto) y scrcpy fallaria
    # con "more than one device/emulator" si no se le dice cual usar.
    wireless = is_wireless_serial(device.serial)
    result.args = ["-s", device.serial, *result.args]

    # --- codec / encoder ---
    # IMPORTANTE (portabilidad): nunca fijar un nombre de encoder a mano en
    # config.yaml/perfiles para uso general -- cada chipset (MediaTek,
    # Qualcomm, Exynos, Unisoc...) expone sus encoders de hardware con
    # nombres propios (c2.mtk.avc.encoder, c2.qti.avc.encoder, etc.). Si el
    # usuario no fuerza un video_encoder explicito, SIEMPRE se detecta el
    # mejor encoder disponible en ESE celular para el codec elegido.
    video_codec = overrides.get("video_codec")
    video_encoder = overrides.get("video_encoder")

    if video_codec is None:
        encoder = device.best_encoder
        if encoder is None:
            result.warnings.append(
                "No se detectó ningún encoder de hardware; scrcpy usará el "
                "encoder por defecto (probablemente software, más lento)."
            )
        else:
            video_codec = encoder.codec
            if video_encoder is None:
                video_encoder = encoder.name
            if encoder.kind == "sw":
                result.warnings.append(
                    f"El único encoder disponible ({encoder.name}) es software. "
                    f"El rendimiento puede ser menor al esperado."
                )
    elif video_encoder is None:
        # Se pidio un codec concreto (a mano o via perfil): buscar el mejor
        # encoder de ESTE celular para ESE codec, nunca reusar el nombre de
        # otro chipset ni el "mejor encoder global" (que podria ser de otro
        # codec y generar un --video-encoder incompatible con --video-codec).
        encoder = device.best_encoder_for_codec(video_codec)
        if encoder is None:
            result.warnings.append(
                f"El celular no reporta ningún encoder para el codec "
                f"'{video_codec}'; scrcpy intentara con el default del "
                f"sistema, lo que puede fallar en este dispositivo."
            )
        else:
            video_encoder = encoder.name
            if encoder.kind == "sw":
                result.warnings.append(
                    f"El único encoder para '{video_codec}' en este celular "
                    f"({encoder.name}) es software. El rendimiento puede ser "
                    f"menor al esperado."
                )

    if video_codec:
        result.args.append(f"--video-codec={video_codec}")
    if video_encoder:
        result.args.append(f"--video-encoder={video_encoder}")

    # --- bitrate segun velocidad USB negociada (no la del cable) ---
    # En WiFi no aplica la velocidad USB (puede venir de una deteccion
    # vieja, de antes de pasar a inalambrico) -- se usa un techo mas
    # conservador salvo que el usuario fuerce uno propio, porque el
    # ancho de banda real de WiFi es mas variable y compartido que el
    # de un cable.
    bitrate_max = overrides.get("bitrate_max")
    if bitrate_max is None:
        if wireless:
            bitrate_max = "12M"
        else:
            speed = usb.negotiated_speed
            if speed == "480":  # USB 2.0
                bitrate_max = "16M"
            elif speed in ("5000", "10000"):  # USB 3.x
                bitrate_max = "32M"
            else:
                bitrate_max = "8M"
                result.warnings.append(
                    f"Velocidad USB no reconocida ({speed}); usando bitrate "
                    f"conservador de {bitrate_max}."
                )
    result.args.append(f"--video-bit-rate={bitrate_max}")

    # --- tamano de video segun el monitor destino ---
    max_size = overrides.get("max_size")
    if max_size is None:
        max_size = monitor.long_side
    result.args.append(f"--max-size={max_size}")

    # --- pantalla completa (jugar en el monitor principal) ---
    # --fullscreen por si solo no garantiza el monitor correcto en un
    # setup con varios: SDL (lo que usa scrcpy por dentro) pone la
    # ventana en pantalla completa en el monitor donde YA este
    # posicionada, que por defecto suele ser donde este el mouse/la
    # ventana enfocada en ese momento -- no necesariamente el monitor
    # detectado como destino. Por eso primero se manda la ventana con
    # --window-x/--window-y a las coordenadas de ese monitor (screeninfo
    # ya las da) y recien ahi se pide --fullscreen.
    if overrides.get("fullscreen"):
        result.args.append(f"--window-x={monitor.x}")
        result.args.append(f"--window-y={monitor.y}")
        result.args.append(f"--window-width={monitor.width}")
        result.args.append(f"--window-height={monitor.height}")
        result.args.append("--fullscreen")
        if max_size < monitor.long_side:
            result.warnings.append(
                f"Pantalla completa con Max size ({max_size}px) por debajo "
                f"de la resolución del monitor ({monitor.long_side}px): la "
                f"imagen se va a ver escalada/borrosa. Usa el botón 'Usar "
                f"resolución de mi monitor' en Opciones avanzadas para que "
                f"coincidan."
            )

    # --- FPS de transmision ---
    # max_fps limita el frame rate de CAPTURA en el celular (0/vacio =
    # sin limite, usa lo que entregue la pantalla). print_fps le pide a
    # scrcpy que imprima el FPS real cada segundo por stdout ("INFO: N
    # fps"); la GUI parsea esas lineas para alimentar el overlay flotante,
    # asi que se activa automaticamente si el overlay esta prendido.
    max_fps = overrides.get("max_fps")
    if max_fps:
        try:
            max_fps_int = int(max_fps)
        except (TypeError, ValueError):
            max_fps_int = 0
            result.warnings.append(
                f"max_fps '{max_fps}' no es un número válido; se ignora."
            )
        if max_fps_int > 0:
            result.args.append(f"--max-fps={max_fps_int}")

    if overrides.get("fps_overlay"):
        result.args.append("--print-fps")

    # --- buffer de video ---
    # 0 (default de scrcpy) = latencia mínima. Un buffer de unos pocos ms
    # suaviza los tirones cuando la red es irregular (WiFi), a costa de delay.
    video_buffer = 0
    raw_buffer = overrides.get("video_buffer")
    if raw_buffer not in (None, ""):
        try:
            video_buffer = int(raw_buffer)
            if video_buffer < 0:
                raise ValueError
        except (TypeError, ValueError):
            video_buffer = 0
            result.warnings.append(
                f"video_buffer '{raw_buffer}' no es un número válido; se usa 0."
            )
    result.args.append(f"--video-buffer={video_buffer}")
    if video_buffer > 0 and not wireless:
        result.warnings.append(
            f"Buffer de video de {video_buffer} ms: por cable normalmente no "
            f"hace falta y solo suma delay."
        )

    # --- avisos que no cambian el comando pero el usuario deberia ver ---
    if wireless:
        result.warnings.append(
            "Conexión por WiFi/LAN: hay más latencia y variabilidad que por "
            "cable, y más competencia si otros dispositivos usan la misma "
            "red. Para uso competitivo se recomienda seguir por USB."
        )
    elif usb.any_autosuspend:
        result.warnings.append(
            "Hay nodos USB en power/control=auto (autosuspend). Esto puede "
            "causar desconexiones o caídas de fps. Usa --fix-usb-power para "
            "forzarlos a 'on'."
        )
    if cpu.governor not in ("performance",):
        result.warnings.append(
            f"El CPU governor actual es '{cpu.governor}', no 'performance'. "
            f"Usa --fix-cpu-governor para cambiarlo (requiere sudo)."
        )

    # --- modo de entrada (teclado/mouse) ---
    # "sdk" es el default de scrcpy (inyeccion via ADB) y varios juegos
    # competitivos lo detectan como entrada sintetica y lo ignoran/bloquean.
    # "uhid" emula un dispositivo HID real a nivel de kernel; "aoa" emula
    # el protocolo USB real (Android Open Accessory), aun mas dificil de
    # detectar. Configurable en config.yaml con la clave "input_mode".
    input_mode = overrides.get("input_mode", "uhid")
    valid_modes = {"sdk", "uhid", "aoa", "disabled"}
    if input_mode not in valid_modes:
        result.warnings.append(
            f"input_mode '{input_mode}' no es válido ({valid_modes}); "
            f"usando 'uhid' por defecto."
        )
        input_mode = "uhid"
    control_enabled = input_mode != "disabled"
    if not control_enabled:
        result.args.append("--no-control")
    elif input_mode != "sdk":
        result.args.append(f"--keyboard={input_mode}")
        # Con el mapeo de teclas, el mouse en modo uhid/aoa se volvería un
        # puntero capturado por la ventana de scrcpy; en sdk sus clics son
        # toques, que es lo que se quiere al liberar el cursor (Alt).
        mouse_mode = "sdk" if overrides.get("keymap_enabled") else input_mode
        result.args.append(f"--mouse={mouse_mode}")

    # --- gamepad físico del PC -> celular ---
    # scrcpy 3+/4 puede reenviar un mando conectado al PC como gamepad HID
    # del celular (--gamepad). Útil para juegos con soporte de mando.
    gamepad = overrides.get("gamepad") or "disabled"
    if gamepad not in VALID_GAMEPAD_MODES:
        result.warnings.append(
            f"gamepad '{gamepad}' no es válido ({VALID_GAMEPAD_MODES}); se ignora."
        )
        gamepad = "disabled"
    if gamepad != "disabled":
        if not control_enabled:
            result.warnings.append(
                "Gamepad ignorado: el modo de entrada es 'disabled' (sin control)."
            )
        else:
            result.args.append(f"--gamepad={gamepad}")
            if gamepad == "aoa" and wireless:
                result.warnings.append(
                    "Gamepad en modo AOA: solo funciona por cable USB; por "
                    "WiFi usa 'uhid'."
                )

    # --- pantalla del celular y ventana de scrcpy ---
    # Estas opciones le piden cosas al celular por el canal de control, así
    # que scrcpy las rechaza con --no-control; en ese caso se omiten con aviso
    # en vez de dejar que scrcpy ni arranque.
    control_options = (
        ("stay_awake", True, "--stay-awake", "mantener el celular despierto"),
        ("turn_screen_off", False, "--turn-screen-off", "apagar la pantalla del celular"),
        ("show_touches", False, "--show-touches", "mostrar toques"),
        ("power_off_on_close", False, "--power-off-on-close", "apagar la pantalla al cerrar"),
    )
    for key, default, flag, label in control_options:
        if not overrides.get(key, default):
            continue
        if not control_enabled:
            if key == "stay_awake":
                result.warnings.append(
                    "Modo de entrada 'disabled': se quito --stay-awake porque "
                    "scrcpy no permite mantener la pantalla despierta sin canal "
                    "de control. El celular puede bloquear pantalla solo."
                )
            else:
                result.warnings.append(
                    f"Modo de entrada 'disabled': se omitió '{label}' ({flag}) "
                    f"porque scrcpy lo necesita con control activo."
                )
            continue
        result.args.append(flag)

    if overrides.get("always_on_top"):
        result.args.append("--always-on-top")
    if overrides.get("borderless"):
        result.args.append("--window-borderless")

    window_title = str(overrides.get("window_title") or "").strip()
    if window_title:
        result.args.append(f"--window-title={window_title}")

    orientation = overrides.get("capture_orientation")
    if orientation not in (None, "", "auto"):
        orientation = str(orientation)
        if orientation == "@" or re.match(r"^@?(flip)?(0|90|180|270)$", orientation):
            result.args.append(f"--capture-orientation={orientation}")
        else:
            result.warnings.append(
                f"capture_orientation '{orientation}' no es válido "
                f"(0, 90, 180, 270, flip0..., con '@' opcional); se ignora."
            )

    # --- audio ---
    # "audio_route" decide POR DONDE viaja el audio del juego:
    #   - "scrcpy" (default): audio normal de scrcpy, mismo tunel que
    #     video/control (ver "audio" para prenderlo/apagarlo y afinarlo).
    #   - "bluetooth": el celular se empareja directo con el PC por
    #     Bluetooth (Ajustes de Android > Bluetooth > elegir el PC como
    #     salida de audio) y manda el audio ahi -- nunca pasa por scrcpy,
    #     asi que no compite con el video/input por CPU ni por el cable.
    #     Aca solo se agrega --no-audio; el emparejamiento en si se hace
    #     desde el sistema operativo, no desde esta app.
    #   - "wifi_dual": el video/control siguen por USB (--no-audio aca
    #     tambien) y una SEGUNDA instancia de scrcpy, separada, trae el
    #     audio por WiFi (ver build_audio_sidecar). El celular sigue
    #     codificando audio igual, pero por un canal de radio aparte del
    #     cable, asi que no le agrega carga al tunel de video/input.
    audio_route = overrides.get("audio_route", "scrcpy")
    if audio_route not in ("off", "scrcpy", "bluetooth", "wifi_dual"):
        result.warnings.append(
            f"audio_route '{audio_route}' no es válido; usando 'scrcpy'."
        )
        audio_route = "scrcpy"

    android_major = _android_major_version(device.android_version)
    scrcpy_audio_on = False

    if audio_route == "off":
        result.args.append("--no-audio")
    elif audio_route == "bluetooth":
        result.args.append("--no-audio")
        result.warnings.append(
            "Audio por Bluetooth: el comando de scrcpy va sin audio a "
            "propósito. Empareja el celular con el PC por Bluetooth y, en "
            "Android, elige el PC como salida de audio del juego (Ajustes "
            "> Bluetooth). Espera 100-150ms de desfasaje audio/video típico "
            "de A2DP; no afecta el input, que sigue 100% por USB."
        )
    elif audio_route == "wifi_dual":
        result.args.append("--no-audio")
        result.warnings.append(
            "Audio por WiFi (dual): este comando va sin audio a propósito. "
            "Corre en paralelo una segunda instancia de scrcpy, solo audio, "
            "por WiFi (la GUI/CLI la lanza sola junto con esta)."
        )
        if android_major is not None and android_major < 11:
            result.warnings.append(
                f"El celular tiene Android {device.android_version}: el "
                f"audio por WiFi (dual) necesita Android 11 o superior "
                f"(API de captura de audio de reproducción). No va a andar."
            )
    elif not overrides.get("audio", True):
        result.args.append("--no-audio")
    else:
        scrcpy_audio_on = True
        if android_major is not None and android_major < 11:
            result.warnings.append(
                f"El celular tiene Android {device.android_version}: el "
                f"audio de scrcpy necesita Android 11 o superior. Puede "
                f"que scrcpy arranque sin audio o falle esa parte."
            )
        audio_codec = overrides.get("audio_codec")
        if audio_codec and audio_codec != "auto":
            result.args.append(f"--audio-codec={audio_codec}")

        audio_bitrate = overrides.get("audio_bitrate")
        if audio_bitrate:
            result.args.append(f"--audio-bit-rate={audio_bitrate}")

        audio_buffer = overrides.get("audio_buffer")
        if audio_buffer not in (None, ""):
            try:
                audio_buffer_ms = int(audio_buffer)
            except (TypeError, ValueError):
                audio_buffer_ms = None
                result.warnings.append(
                    f"audio_buffer '{audio_buffer}' no es un número válido; se ignora."
                )
            if audio_buffer_ms is not None and audio_buffer_ms >= 0:
                if audio_buffer_ms != SCRCPY_DEFAULT_AUDIO_BUFFER_MS:
                    result.args.append(f"--audio-buffer={audio_buffer_ms}")
                if audio_buffer_ms < 20:
                    result.warnings.append(
                        f"Buffer de audio muy bajo ({audio_buffer_ms}ms): menos "
                        f"delay, pero más riesgo de cortes/glitches si el "
                        f"celular no llega a tiempo."
                    )

        if not wireless and usb.negotiated_speed == "480":
            result.warnings.append(
                "Audio activado sobre USB 2.0: es la causa más común de "
                "lag/microcortes en competitivo, porque el celular tiene que "
                "codificar audio y video al mismo tiempo por el mismo cable. "
                "Si el lag persiste probando 'Audio codec=aac', bajando "
                "'Audio bitrate' y 'Audio buffer' en Opciones avanzadas, "
                "considera la ruta de audio 'Bluetooth' o 'WiFi (dual)' en "
                "vez de apagar el audio del todo."
            )

    # --- grabación de la sesión ---
    # scrcpy graba lo mismo que transmite: sin audio de scrcpy, el archivo
    # sale sin sonido (el audio por Bluetooth o por WiFi dual no entra).
    if overrides.get("record"):
        record_path = str(overrides.get("record_path") or "").strip()
        if not record_path:
            result.warnings.append("Se pidió grabar pero no hay ruta de archivo; se ignora.")
        else:
            result.args.append(f"--record={record_path}")
            record_format = overrides.get("record_format")
            if record_format in VALID_RECORD_FORMATS:
                result.args.append(f"--record-format={record_format}")
            if not scrcpy_audio_on:
                result.warnings.append(
                    "La grabación saldrá SIN audio: solo la ruta de audio "
                    "'Por USB, junto con video' se graba."
                )

    # --- flags extra definidos por el usuario en config.yaml ---
    for flag in overrides.get("extra_flags") or []:
        result.args.append(flag)

    return result
