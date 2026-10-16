"""
Detecta el celular Android conectado por ADB: modelo, marca, chipset,
version de Android y encoders de video disponibles vía scrcpy.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

import adb_tools
from detectors.wireless import is_wireless_serial


@dataclass
class Encoder:
    name: str
    codec: str  # "h264", "h265", "av1"
    kind: str   # "hw" o "sw"


@dataclass
class AndroidDevice:
    serial: str
    model: str = "unknown"
    brand: str = "unknown"
    android_version: str = "unknown"
    chipset: str = "unknown"
    encoders: list[Encoder] = field(default_factory=list)

    @property
    def best_encoder(self) -> Encoder | None:
        """Prioriza h264 hw > h265 hw > cualquier hw > software."""
        if not self.encoders:
            return None
        priority = {
            ("h264", "hw"): 0,
            ("h265", "hw"): 1,
        }

        def score(enc: Encoder) -> tuple:
            return (priority.get((enc.codec, enc.kind), 2), enc.kind != "hw")

        return sorted(self.encoders, key=score)[0]

    def best_encoder_for_codec(self, codec: str) -> Encoder | None:
        """
        Mejor encoder disponible PARA UN CODEC ESPECIFICO (prioriza hw sobre
        sw dentro de ese mismo codec). Esto es clave para la portabilidad:
        cada chipset (MediaTek, Qualcomm, Exynos, etc.) expone sus encoders
        de hardware con nombres propios (c2.mtk.*, c2.qti.*, OMX.qcom.*...),
        así que nunca hay que asumir un nombre fijo -- siempre se pregunta
        "cual es el mejor encoder para este codec en ESTE celular".
        """
        candidates = [enc for enc in self.encoders if enc.codec == codec]
        if not candidates:
            return None
        return sorted(candidates, key=lambda e: e.kind != "hw")[0]


class AndroidDeviceError(RuntimeError):
    pass


def _check_binaries() -> None:
    for binary in ("adb", "scrcpy"):
        if shutil.which(binary) is None:
            raise AndroidDeviceError(
                f"No se encontró el binario '{binary}' en el PATH. "
                f"Instalalo antes de continuar (paquete: android-tools / scrcpy)."
            )


def _run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False
        )
    except FileNotFoundError as exc:
        raise AndroidDeviceError(f"No se pudo ejecutar: {' '.join(cmd)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AndroidDeviceError(
            f"Se agotó el tiempo de espera ejecutando: {' '.join(cmd)}. "
            f"Si el celular está bloqueado o pide un permiso en pantalla, "
            f"acéptalo y vuelve a intentar."
        ) from exc
    return result.stdout


def list_connected_serials(target: str | None = None) -> list[str]:
    """
    Devuelve los seriales de los dispositivos ADB autorizados y conectados.

    Antes esta función abortaba con AndroidDeviceError apenas encontraba
    UNA línea "unauthorized" en `adb devices -l`, sin importar si ese
    celular era el que interesaba. Eso rompia el caso común de "tengo un
    celular ya autorizado y pruebo con otro nuevo": mientras el celular
    nuevo no acepta el diálogo de depuración en pantalla, la detección
    del celular viejo (que si está autorizado) también fallaba.

    Ahora solo se lanza el error de "no autorizado" cuando de verdad
    bloquea lo que se pidió: si se pasó `target` y ESE serial especifico
    aparece como unauthorized, o si no se pasó `target` y no hay NINGÚN
    celular autorizado (solo unauthorized). Un unauthorized "de otro
    celular" que no es el target ya no bloquea nada.
    """
    _check_binaries()
    entries = adb_tools.parse_devices(_run(["adb", "devices", "-l"]))
    authorized = [e.serial for e in entries if e.state == "device"]
    unauthorized = [e.serial for e in entries if e.state == "unauthorized"]

    blocked = (target is not None and target in unauthorized) or (
        target is None and not authorized and unauthorized
    )
    if blocked:
        raise AndroidDeviceError(
            "Celular detectado pero no autorizado para depuración USB. "
            "Revisa la pantalla del celular y acepta el permiso de ADB."
        )
    return authorized


def restart_adb_server() -> str:
    """
    Reinicia el servidor adb ('adb kill-server' + 'adb start-server').

    Sirve para destrabar el demonio adb cuando queda en un estado raro
    -- común al alternar entre varios celulares distintos, cuando
    'adb devices' no refleja lo que hay realmente conectado, o cuando un
    celular queda marcado 'unauthorized'/'offline' de forma persistente
    aunque este bien conectado y desbloqueado.
    """
    _check_binaries()
    try:
        subprocess.run(
            ["adb", "kill-server"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        result = subprocess.run(
            ["adb", "start-server"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except FileNotFoundError as exc:
        raise AndroidDeviceError("No se pudo ejecutar 'adb'.") from exc
    output = (result.stdout + result.stderr).strip()
    return output or "Servidor ADB reiniciado."


def _getprop(serial: str, prop: str) -> str:
    return _run(["adb", "-s", serial, "shell", "getprop", prop]).strip()


def _parse_encoders(serial: str) -> list[Encoder]:
    """Corre `scrcpy --list-encoders` y parsea la salida en objetos Encoder."""
    output = _run(["scrcpy", "-s", serial, "--list-encoders"])
    encoders: list[Encoder] = []
    # Formato real de scrcpy 4.1 (sin comillas, con (hw)/(sw) explicito):
    #   --video-codec=h264 --video-encoder=c2.mtk.avc.encoder    (hw) [vendor]
    #   --video-codec=h264 --video-encoder=OMX.MTK...HEVC        (hw) [vendor] (alias for ...)
    #   --video-codec=h264 --video-encoder=c2.android.avc.encoder (sw)
    # OJO: solo interesan encoders de VIDEO; el output tambien lista audio
    # encoders mas abajo con --audio-codec=/--audio-encoder=, que este
    # patron no matchea por buscar especificamente "--video-".
    pattern = re.compile(
        r"--video-codec=(?P<codec>\w+)\s+--video-encoder=(?P<name>\S+)"
        r"\s+\((?P<kind>hw|sw)\)"
    )
    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        encoders.append(
            Encoder(
                name=match.group("name"),
                codec=match.group("codec"),
                kind=match.group("kind"),
            )
        )
    return encoders


def pick_serial(serial: str | None = None) -> str:
    """Elige el celular a usar: el pedido, o el único (preferiblemente por
    USB) si no se pidió ninguno. Lanza AndroidDeviceError si no hay o es ambiguo."""
    serials = list_connected_serials(target=serial)
    if not serials:
        raise AndroidDeviceError(
            "No se detectó ningún celular Android conectado por USB con "
            "depuración habilitada."
        )
    if serial is None:
        if len(serials) > 1:
            usb_serials = [s for s in serials if not is_wireless_serial(s)]
            if len(usb_serials) == 1:
                serial = usb_serials[0]
            else:
                raise AndroidDeviceError(
                    f"Hay {len(serials)} celulares conectados: {serials}. "
                    f"Especifica cual usar (preferred_device_serial en "
                    f"config.yaml)."
                )
        else:
            serial = serials[0]
    elif serial not in serials:
        raise AndroidDeviceError(f"El serial {serial} no está conectado.")
    return serial


def detect(serial: str | None = None) -> AndroidDevice:
    """
    Detecta el dispositivo Android conectado. Si hay más de uno y no se
    especifica serial, lanza AndroidDeviceError para que el caller decida
    -- EXCEPTO cuando la ambigüedad es "USB + una conexión WiFi del mismo
    teléfono": ahí se prioriza el serial USB "de fábrica" sin más (es lo
    que se espera al pulsar "Detectar", que asume USB como punto de
    entrada; el modo WiFi -- de video o de audio dual -- se activa aparte,
    explicitamente).

    IMPORTANTE: esto NUNCA desconecta la sesión WiFi que haya activa.
    Antes lo hacia (para "limpiar" conexiones viejas colgadas), pero esa
    limpieza no tiene forma de distinguir una sesión vieja sin uso de una
    que la GUI está usando activamente para el audio dual -- cortarla a
    ciegas rompia el audio por WiFi cada vez que se volvia a pulsar
    "Detectar" con esa función activa. Si de verdad queda una conexión
    WiFi vieja bloqueando algo, se cierra a mano con 'adb disconnect
    ip:puerto' (o el botón "Volver a USB" de la GUI).
    """
    serial = pick_serial(serial)

    device = AndroidDevice(
        serial=serial,
        model=_getprop(serial, "ro.product.model") or "unknown",
        brand=_getprop(serial, "ro.product.brand") or "unknown",
        android_version=_getprop(serial, "ro.build.version.release") or "unknown",
        chipset=(
            _getprop(serial, "ro.board.platform")
            or _getprop(serial, "ro.hardware")
            or "unknown"
        ),
    )
    device.encoders = _parse_encoders(serial)
    return device
