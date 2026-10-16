"""
Pantalla principal de NexoHub: el controlador que une el panel del celular,
los perfiles, las secciones de ajustes y la barra de acciones.

Una sola pantalla continua (sin pasos obligatorios): arriba el celular y el
perfil de uso, abajo los ajustes finos como acordeones plegables. La barra
de acciones (Generar / Guardar / Grabar / Detener / Lanzar) no entra en este
widget: queda en ``self.action_bar`` para que la ventana la fije abajo,
fuera del scroll.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QImage
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import adb_tools
import autoconfig
import command_builder
import config_store
import logging_setup
from detectors import android_device, dependencies, resolution, wireless
from detectors.monitor import Monitor

from . import helpers
from .device_panel import DevicePanel
from .dependencies_page import DependenciesWidget
from .fps_overlay import FpsOverlay
from .helpers import PROFILE_KEYS, RESET_ON_PROFILE, SESSION_ONLY_KEYS
from .pages import (
    AdvancedPage,
    AudioPage,
    InputPage,
    LogPage,
    RecordingPage,
    VideoPage,
    WindowPage,
)
from .profiles import CUSTOM_PROFILE_KEY, ProfileSelector
from .theme import COLORS, apply_shadow
from .widgets import Banner, CollapsibleSection, icon_button
from .workers import (
    AdbRestartWorker,
    BluetoothCheckWorker,
    DetectWorker,
    KeymapWorker,
    LaunchWorker,
    PhoneResolutionWorker,
    ScreenshotWorker,
    StatusPollWorker,
    WifiDisableWorker,
    WifiEnableWorker,
)

POLL_INTERVAL_MS = 4000
BATTERY_EVERY_N_POLLS = 4  # la batería se lee cada ~16 s
MISSES_BEFORE_LOST = 2  # sondeos seguidos sin ver el celular antes de darlo por perdido


class ConfigWidget(QWidget):
    status_message = pyqtSignal(str)
    device_status = pyqtSignal(str, bool)  # (texto, conectado)
    streaming_changed = pyqtSignal(bool)
    fps_changed = pyqtSignal(float)
    toast_requested = pyqtSignal(str, str, str, object)  # mensaje, tipo, texto de acción, acción
    scroll_to_requested = pyqtSignal(object)

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("NexoHub", "NexoHub")
        self._logger = logging_setup.get_logger()

        self.config, config_warnings = config_store.load_config_safe()
        self.builtin_profile_keys = set((config_store.load_bundled().get("profiles") or {}).keys())

        # estado de la detección
        self.setup: autoconfig.SetupInfo | None = None
        self.device = None
        self.usb = None
        self.monitor: Monitor | None = None
        self.cpu_status = None
        self.usb_serial: str | None = None
        self.connection_mode = "usb"  # "usb" o "wifi"
        self.audio_wifi_address: str | None = None  # canal de audio dual, aparte del principal
        self._last_audio_route = "off"

        # sondeo en segundo plano
        self._polling_enabled = True
        self._poll_ticks = 0
        self._misses = 0
        self._battery = None
        self._hot_warned = False
        self._auto_detect_failed: set[str] = set()
        self._detect_is_auto = False
        self._resolution_action: str | None = None
        self._current_recording: Path | None = None

        # hilos
        self._threads: list = []
        self.detect_worker: DetectWorker | None = None
        self.poll_worker: StatusPollWorker | None = None
        self.launch_worker: LaunchWorker | None = None
        self.audio_launch_worker: LaunchWorker | None = None
        self.keymap_worker: KeymapWorker | None = None
        self._keymap_labels: dict[str, str] = {}  # teclas especiales del mapa en uso, legibles
        self._keymap_editor_open = False
        self.wifi_worker = None
        self.audio_wifi_worker: WifiEnableWorker | None = None
        self.adb_restart_worker: AdbRestartWorker | None = None
        self.bt_worker: BluetoothCheckWorker | None = None
        self.resolution_worker: PhoneResolutionWorker | None = None
        self.screenshot_worker: ScreenshotWorker | None = None
        self.fps_overlay_window: FpsOverlay | None = None

        self._build_ui()
        self._load_config_into_form()
        for warning in config_warnings:
            self._log(f"Aviso de configuración: {warning}", "warning")
            self._toast(warning, "error")

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start()
        QTimer.singleShot(400, self._poll_status)

    # ======================================================================
    # Construcción de la interfaz
    # ======================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        self.deps_banner = Banner("", "Abrir Dependencias", kind="warn")
        self.deps_banner.hide()
        self.deps_banner.action_clicked.connect(self._open_dependencies)
        root.addWidget(self.deps_banner)

        # --- celular ---
        self.device_panel = DevicePanel()
        self.device_panel.detect_clicked.connect(lambda: self.on_detect())
        self.device_panel.wifi_clicked.connect(self.on_toggle_wifi)
        self.device_panel.screenshot_clicked.connect(self.on_screenshot)
        self.device_panel.restart_adb_clicked.connect(self.on_restart_adb)
        self.device_panel.refresh_clicked.connect(lambda: self._poll_status(force_battery=True))
        self.device_panel.device_chosen.connect(self._on_device_chosen)
        res = self.device_panel.resolution_section
        res.use_monitor_clicked.connect(self.on_resolution_use_monitor)
        res.refresh_clicked.connect(lambda: self._run_resolution_worker("get"))
        res.apply_clicked.connect(lambda w, h: self._run_resolution_worker("set", w, h))
        res.reset_clicked.connect(lambda: self._run_resolution_worker("reset"))
        root.addWidget(self.device_panel)

        # --- perfiles ---
        self.profile_selector = ProfileSelector()
        self.profile_selector.selected.connect(self._on_profile_selected)
        self.profile_selector.save_requested.connect(self.on_save_profile)
        self.profile_selector.delete_requested.connect(self.on_delete_profile)
        root.addWidget(self.profile_selector)

        self.launch_summary_label = QLabel("")
        self.launch_summary_label.setWordWrap(True)
        self.launch_summary_label.setProperty("role", "hint")
        root.addWidget(self.launch_summary_label)

        self.stream_warning = QLabel("")
        self.stream_warning.setWordWrap(True)
        self.stream_warning.setStyleSheet(f"color: {COLORS['yellow']};")
        self.stream_warning.hide()
        root.addWidget(self.stream_warning)

        # --- páginas de ajustes ---
        self.video_page = VideoPage()
        self.window_page = WindowPage()
        self.audio_page = AudioPage()
        self.input_page = InputPage()
        self.recording_page = RecordingPage()
        self.advanced_page = AdvancedPage()
        self.log_page = LogPage()
        self.dependencies_widget = DependenciesWidget()

        self.fields = {}
        for page in (
            self.video_page, self.window_page, self.audio_page,
            self.input_page, self.recording_page, self.advanced_page,
        ):
            self.fields.update(page.fields)
            page.edited.connect(self._on_form_edited)

        self.video_page.match_monitor_clicked.connect(self.on_match_monitor_resolution)
        self.audio_page.route_changed.connect(self._on_audio_route_changed)
        self.audio_page.bluetooth_clicked.connect(self.on_check_bluetooth)
        self.input_page.edit_keymap_clicked.connect(self.on_edit_keymap)
        self.input_page.new_keymap_clicked.connect(self.on_new_keymap)
        self.recording_page.open_recordings_clicked.connect(
            lambda: self._open_folder(config_store.recordings_dir(self._build_overrides()))
        )
        self.recording_page.open_captures_clicked.connect(
            lambda: self._open_folder(config_store.captures_dir(self._build_overrides()))
        )
        self.advanced_page.reset_clicked.connect(self.on_reset_settings)
        self.advanced_page.open_config_clicked.connect(
            lambda: self._open_folder(config_store.config_home())
        )
        self.log_page.toast.connect(lambda msg, kind: self._toast(msg, kind))
        self.dependencies_widget.checked.connect(self._on_dependencies_checked)

        self.sections: dict[str, CollapsibleSection] = {}
        self._add_section("video", "monitor", "Video", self.video_page, expanded=True)
        self._add_section("window", "window", "Ventana y pantalla", self.window_page)
        self._add_section("audio", "volume", "Audio", self.audio_page)
        self._add_section("input", "gamepad", "Teclado, mouse y mando", self.input_page)
        self._add_section("recording", "film", "Grabación y capturas", self.recording_page)
        self._add_section("advanced", "sliders", "Avanzado", self.advanced_page)
        self._add_section("log", "terminal", "Comando y registro", self.log_page)
        self._add_section("deps", "package", "Dependencias", self.dependencies_widget)
        root.addStretch(1)

        self.action_bar = self._build_action_bar()

    def _add_section(self, name: str, icon_name: str, title: str, content: QWidget, expanded: bool = False) -> None:
        stored = self.settings.value(f"accordion/{name}", expanded, type=bool)
        section = CollapsibleSection(icon_name, title, expanded=bool(stored))
        apply_shadow(section)
        inner = QVBoxLayout(section.content)
        inner.setContentsMargins(18, 6, 18, 18)
        inner.addWidget(content)
        section.toggled.connect(lambda value, n=name: self.settings.setValue(f"accordion/{n}", value))
        self.sections[name] = section
        self.layout().addWidget(section)

    def _build_action_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("actionBar")
        apply_shadow(bar)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)

        self.build_btn = icon_button(
            "terminal", "Generar comando",
            tooltip="Arma el comando sin lanzarlo, para revisarlo antes (se ve en 'Comando y registro').",
        )
        self.build_btn.clicked.connect(self.on_build_command)
        self.save_btn = icon_button(
            "save", "Guardar ajustes",
            tooltip="Guarda estos valores como default para la próxima vez.  (Ctrl+S)",
        )
        self.save_btn.clicked.connect(self.on_save_config)

        self.record_btn = icon_button(
            "record", "Grabar", color=COLORS["red"],
            tooltip=(
                "Graba la sesión en un archivo (mkv/mp4) mientras transmites. "
                "Se activa al lanzar scrcpy; carpeta y formato en 'Grabación y capturas'."
            ),
            object_name="recordToggle",
        )
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(lambda *_: (self._update_summaries(), self._update_launch_summary()))

        self.stop_btn = icon_button(
            "stop", "Detener", color=COLORS["red"],
            tooltip="Corta scrcpy (SIGTERM) sin cerrar NexoHub.  (Ctrl+.)",
            object_name="bigAction",
        )
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)

        self.launch_btn = icon_button(
            "play", "Lanzar scrcpy", color="#ffffff",
            tooltip=(
                "Requiere una detección exitosa y vigente. Si cambiaste de "
                "celular, vuelve a pulsar 'Detectar' antes de lanzar.  (Ctrl+Enter)"
            ),
            object_name="primary",
        )
        self.launch_btn.setStyleSheet("padding: 13px 28px; font-size: 13px; border-radius: 10px;")
        apply_shadow(self.launch_btn, blur=24, alpha=90)
        self.launch_btn.setEnabled(False)
        self.launch_btn.clicked.connect(self.on_launch)

        layout.addWidget(self.build_btn)
        layout.addWidget(self.save_btn)
        layout.addStretch(1)
        layout.addWidget(self.record_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(self.launch_btn)
        return bar

    # ======================================================================
    # Formulario <-> configuración
    # ======================================================================
    def _load_config_into_form(self) -> None:
        self._apply_to_form(self.config, fill_defaults=True)
        self.record_btn.setChecked(False)  # grabar nunca arranca activado
        active = self.config.get("active_profile")
        profiles = self.config.get("profiles") or {}
        self.profile_selector.current = active if active in profiles else CUSTOM_PROFILE_KEY
        self.profile_selector.set_profiles(profiles, self.builtin_profile_keys)
        self._check_stream_thresholds()
        self._update_summaries()

    def _apply_to_form(self, cfg: dict, *, fill_defaults: bool = False) -> None:
        cfg = dict(cfg)
        if "audio" in cfg and "audio_route" not in cfg:
            cfg["audio_route"] = "scrcpy" if cfg["audio"] else "off"
        if cfg.get("audio_route") == "wifi_dual":
            # No se re-provisiona el canal de audio por WiFi solo por cargar
            # el archivo o un perfil (requiere el celular detectado por USB);
            # se cae a "off" hasta que el usuario la vuelva a elegir.
            cfg["audio_route"] = "off"
        for key, field in self.fields.items():
            if key in cfg:
                field.write(cfg[key])
            elif fill_defaults:
                field.write(None)
        route = self.audio_page.route()
        self.audio_page.update_fine_enabled(route)
        self._last_audio_route = route

    def _form_to_overrides(self) -> dict:
        overrides = {key: field.read() for key, field in self.fields.items()}
        route = overrides.get("audio_route") or "off"
        overrides["audio"] = route == "scrcpy"  # retrocompatibilidad con config.yaml viejos
        overrides["record"] = self.record_btn.isChecked()
        return overrides

    def _build_overrides(self) -> dict:
        overrides = dict(self.config)
        overrides.update(self._form_to_overrides())
        return overrides

    def on_save_config(self) -> None:
        merged = dict(self.config)
        for key, value in self._form_to_overrides().items():
            if key not in SESSION_ONLY_KEYS:
                merged[key] = value
        current = self.profile_selector.current
        merged["active_profile"] = None if current == CUSTOM_PROFILE_KEY else current
        try:
            path = config_store.save_user_config(merged)
        except OSError as exc:
            self._log(f"No se pudo guardar la configuración: {exc}", "error")
            self._toast(f"No se pudo guardar: {exc}", "error")
            return
        self.config, _warnings = config_store.load_config_safe()
        self._log(f"Ajustes guardados en {path}")
        self.status_message.emit("Ajustes guardados")
        self._toast("Ajustes guardados", "success")

    def on_reset_settings(self) -> None:
        confirm = QMessageBox.question(
            self, "Restablecer ajustes",
            "Se borrarán tus ajustes guardados y tus perfiles propios, y NexoHub "
            "volverá a los valores de fábrica. ¿Continuar?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            config_store.reset_user_config()
        except OSError as exc:
            self._toast(f"No se pudo restablecer: {exc}", "error")
            return
        self.config, _warnings = config_store.load_config_safe()
        self._load_config_into_form()
        self._log("Ajustes restablecidos a los valores de fábrica.")
        self._toast("Ajustes restablecidos", "success")

    # ======================================================================
    # Perfiles
    # ======================================================================
    def _profile_overlay(self, key: str) -> dict:
        profile = (self.config.get("profiles") or {}).get(key) or {}
        overlay = {k: v for k, v in profile.items() if k in PROFILE_KEYS or k == "audio"}
        if "audio" in overlay and "audio_route" not in overlay:
            overlay["audio_route"] = "scrcpy" if overlay["audio"] else "off"
        overlay.pop("audio", None)
        return overlay

    def _on_profile_selected(self, key: str) -> None:
        self.profile_selector.select(key)
        if key != CUSTOM_PROFILE_KEY:
            reset = {k: None for k in RESET_ON_PROFILE}
            self._apply_to_form({**reset, **self._profile_overlay(key)})
            self._log(f"Perfil '{self.profile_selector.title_of(key)}' aplicado.")
        self._check_stream_thresholds()
        self._update_summaries()
        self._update_launch_summary()

    def _on_form_edited(self, key: str) -> None:
        # Editar a mano algo que forma parte de un perfil pasa a "Personalizado".
        if key in PROFILE_KEYS and self.profile_selector.current != CUSTOM_PROFILE_KEY:
            self.profile_selector.select(CUSTOM_PROFILE_KEY)
        self._check_stream_thresholds()
        self._update_summaries()
        self._update_launch_summary()

    def on_save_profile(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Guardar como perfil", "Nombre del perfil:", text=""
        )
        name = name.strip()
        if not ok or not name:
            return
        slug = helpers.slugify(name)
        if not slug:
            self._toast("Usa letras o números en el nombre del perfil.", "error")
            return
        profiles = dict(self.config.get("profiles") or {})
        if slug in self.builtin_profile_keys or slug in ("custom",):
            QMessageBox.information(
                self, "Nombre en uso",
                "Ya existe un perfil de fábrica con ese nombre. Elige otro distinto.",
            )
            return
        if slug in profiles:
            confirm = QMessageBox.question(
                self, "Reemplazar perfil", f"Ya tienes un perfil «{profiles[slug].get('label', slug)}». ¿Reemplazarlo?"
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        form = self._form_to_overrides()
        snapshot = {k: v for k, v in form.items() if k in PROFILE_KEYS and v not in (None, [])}
        profiles[slug] = {
            "label": name,
            "description": helpers.describe_profile(snapshot),
            **snapshot,
        }
        self.config["profiles"] = profiles
        try:
            config_store.save_user_config(self.config)
        except OSError as exc:
            self._toast(f"No se pudo guardar el perfil: {exc}", "error")
            return
        self.profile_selector.current = slug
        self.profile_selector.set_profiles(profiles, self.builtin_profile_keys)
        self._log(f"Perfil propio '{name}' guardado.")
        self._toast(f"Perfil «{name}» guardado", "success")
        self._update_launch_summary()

    def on_delete_profile(self, key: str) -> None:
        profiles = dict(self.config.get("profiles") or {})
        label = (profiles.get(key) or {}).get("label", key)
        confirm = QMessageBox.question(self, "Eliminar perfil", f"¿Eliminar el perfil «{label}»?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        profiles.pop(key, None)
        self.config["profiles"] = profiles
        try:
            config_store.save_user_config(self.config)
        except OSError as exc:
            self._toast(f"No se pudo eliminar: {exc}", "error")
            return
        if self.profile_selector.current == key:
            self.profile_selector.current = CUSTOM_PROFILE_KEY
        self.profile_selector.set_profiles(profiles, self.builtin_profile_keys)
        self._log(f"Perfil '{label}' eliminado.")
        self._update_launch_summary()

    # ======================================================================
    # Avisos y resúmenes
    # ======================================================================
    def _check_stream_thresholds(self, *_args) -> None:
        """Si se pide FPS o bitrate por encima de lo razonable para USB 2.0/WiFi,
        avisa: la calidad mejora, pero el delay o la fluidez pueden resentirse."""
        fields = self.fields
        reasons = helpers.stream_warning_reasons(
            fields["max_fps"].widget.value(), fields["bitrate_max"].widget.text()
        )
        if reasons:
            self.stream_warning.setText(
                "⚠ " + " y ".join(reasons) + ": la calidad de imagen va a "
                "mejorar, pero el delay o la fluidez de la transmisión pueden "
                "verse afectados según tu USB/WiFi y el celular."
            )
            self.stream_warning.show()
        else:
            self.stream_warning.hide()

    def _update_summaries(self) -> None:
        ov = self._form_to_overrides()
        video = [
            ov.get("video_codec") or "auto",
            ov.get("bitrate_max") or "auto",
            f"{ov['max_size']} px" if ov.get("max_size") else "auto",
        ]
        if ov.get("max_fps"):
            video.append(f"{ov['max_fps']} FPS")
        self.sections["video"].set_summary(" · ".join(video))

        window_flags = [
            label for key, label in (
                ("fullscreen", "pantalla completa"), ("always_on_top", "siempre encima"),
                ("borderless", "sin bordes"), ("turn_screen_off", "pantalla del celular apagada"),
                ("show_touches", "toques"),
            ) if ov.get(key)
        ]
        self.sections["window"].set_summary(", ".join(window_flags) or "predeterminado")

        route_labels = {"off": "sin audio", "scrcpy": "por USB", "bluetooth": "Bluetooth", "wifi_dual": "WiFi dual"}
        self.sections["audio"].set_summary(route_labels.get(ov.get("audio_route"), ""))

        mode = ov.get("input_mode") or "uhid"
        pad = ov.get("gamepad")
        self.sections["input"].set_summary(f"{mode} + mando {pad}" if pad else mode)

        recording = f"grabando · {ov.get('record_format') or 'mkv'}" if ov.get("record") else "sin grabar"
        self.sections["recording"].set_summary(recording)

    def _update_launch_summary(self) -> None:
        if not self.device:
            self.launch_summary_label.setText("")
            return
        profile = self.profile_selector.title_of(self.profile_selector.current)
        text = f"{self.device.brand.title()} {self.device.model}   ·   perfil: {profile}"
        if self.record_btn.isChecked():
            text += "   ·   ● se grabará esta sesión"
        self.launch_summary_label.setText(text)

    # ======================================================================
    # Sondeo en segundo plano (enchufar/desenchufar, batería, temperatura)
    # ======================================================================
    def _poll_status(self, force_battery: bool = False) -> None:
        if not self._polling_enabled:
            return
        if self.poll_worker is not None and self.poll_worker.isRunning():
            return
        serial = self.device.serial if self.device else self.device_panel.selected_serial()
        self._poll_ticks += 1
        with_battery = bool(serial) and (
            force_battery or self._battery is None or self._poll_ticks % BATTERY_EVERY_N_POLLS == 0
        )
        worker = StatusPollWorker(serial, with_battery)
        worker.done.connect(self._on_poll_done)
        worker.failed.connect(self._on_poll_failed)
        self.poll_worker = worker
        self._start(worker)

    def _on_poll_failed(self, message: str) -> None:
        # Casi siempre es "adb no está instalado": se deja de sondear hasta
        # que la verificación de dependencias diga que ya está.
        self._polling_enabled = False
        self.device_panel.set_devices([])
        self._log(f"Sondeo de celulares detenido: {message}", "warning")

    def _on_poll_done(self, devices: list, battery) -> None:
        shown = [d for d in devices if d.serial != self.audio_wifi_address]
        keep = self.device.serial if self.device else None
        self.device_panel.set_devices(shown, keep)

        if battery is not None:
            self._battery = battery
            self.device_panel.set_telemetry(battery)
            self._warn_if_hot(battery)

        ready = {d.serial for d in shown if d.is_ready}
        wifi_busy = self.wifi_worker is not None and self.wifi_worker.isRunning()

        if self.device is not None and not wifi_busy:
            if self.device.serial in ready:
                self._misses = 0
            else:
                self._misses += 1
                if self._misses >= MISSES_BEFORE_LOST:
                    self._handle_device_lost()
            return

        # Sin celular detectado: ¿conviene detectar solo?
        if self.device is None and self.fields["auto_detect"].read():
            usb_ready = [d for d in shown if d.is_ready and d.transport == "usb"]
            candidates = usb_ready or [d for d in shown if d.is_ready]
            busy = self.detect_worker is not None and self.detect_worker.isRunning()
            if len(candidates) == 1 and not busy and candidates[0].serial not in self._auto_detect_failed:
                self._log(f"Celular nuevo conectado ({candidates[0].label}): detectando…")
                self.on_detect(auto=True)

    def _warn_if_hot(self, battery) -> None:
        if battery.temperature_c is None:
            return
        if battery.temperature_c >= helpers.HOT_TEMP_C and not self._hot_warned:
            self._hot_warned = True
            self._toast(
                f"El celular está a {battery.temperature_c:.1f} °C: puede bajar el rendimiento. "
                f"Considera bajar el bitrate o apagar su pantalla.",
                "error",
            )
            self._log(f"Temperatura alta del celular: {battery.temperature_c:.1f} °C", "warning")
        elif battery.temperature_c < helpers.HOT_TEMP_C - 3:
            self._hot_warned = False

    def _handle_device_lost(self) -> None:
        name = f"{self.device.brand.title()} {self.device.model}" if self.device else "El celular"
        self._clear_detection()
        self.status_message.emit("Celular desconectado")
        self.device_status.emit("Sin conectar", False)
        self._log(f"{name} ya no aparece conectado.", "warning")
        self._toast(f"{name} se desconectó", "info")

    def _clear_detection(self) -> None:
        """Invalida cualquier detección previa: si no se limpia, 'Lanzar scrcpy'
        seguiría usando datos viejos (el serial de un celular que ya no está)."""
        self.device = None
        self.usb = None
        self.setup = None
        self.usb_serial = None if self.connection_mode == "usb" else self.usb_serial
        self._battery = None
        self._misses = 0
        self.launch_btn.setEnabled(False)
        self.device_panel.show_disconnected()
        self._update_launch_summary()

    def _on_dependencies_checked(self, report) -> None:
        required = [b for b in dependencies.REQUIRED_BINARIES if b in report.missing_binaries]
        if required:
            self.deps_banner.set_text(
                "Faltan herramientas obligatorias: <b>" + ", ".join(required) + "</b>. "
                "NexoHub las necesita para hablar con el celular; se instalan con un botón."
            )
            self.deps_banner.show()
            self._polling_enabled = False
            self.sections["deps"].set_summary("faltan: " + ", ".join(required))
        else:
            self.deps_banner.hide()
            if not self._polling_enabled:
                self._polling_enabled = True
                QTimer.singleShot(200, self._poll_status)
            optional = len(report.missing_binaries)
            self.sections["deps"].set_summary("todo listo" if report.all_ok else f"{optional} opcional(es) sin instalar")

    def _open_dependencies(self) -> None:
        self.sections["deps"].set_expanded(True)
        self.scroll_to_requested.emit(self.sections["deps"])

    # ======================================================================
    # Detección
    # ======================================================================
    def _qt_monitors(self) -> list[Monitor]:
        """Pantallas según Qt, por si screeninfo no puede leerlas (Wayland puro)."""
        primary = QGuiApplication.primaryScreen()
        monitors = []
        for screen in QGuiApplication.screens():
            geo = screen.geometry()
            ratio = screen.devicePixelRatio()
            monitors.append(
                Monitor(
                    name=screen.name() or "unknown",
                    width=round(geo.width() * ratio),
                    height=round(geo.height() * ratio),
                    is_primary=screen is primary,
                    x=geo.x(),
                    y=geo.y(),
                )
            )
        return monitors

    def _on_device_chosen(self, serial: str) -> None:
        current = self.device.serial if self.device else None
        if serial != current:
            self.on_detect()

    def on_detect(self, auto: bool = False) -> None:
        if self.detect_worker is not None and self.detect_worker.isRunning():
            return
        self._detect_is_auto = auto
        self.device_panel.set_detecting(True)
        self._log("Detectando setup...")
        self.status_message.emit("Detectando celular, USB, monitor y CPU...")
        self.device_status.emit("Detectando...", False)
        serial = self.device_panel.selected_serial()
        worker = DetectWorker(self.config, serial=serial, fallback_monitors=self._qt_monitors())
        worker.done.connect(self._on_detect_done)
        worker.failed.connect(self._on_detect_failed)
        self.detect_worker = worker
        self._start(worker)

    def _on_detect_done(self, info: autoconfig.SetupInfo) -> None:
        self.setup = info
        self.device, self.usb, self.monitor, self.cpu_status = info.device, info.usb, info.monitor, info.cpu
        self._misses = 0
        self._hot_warned = False
        wifi = wireless.is_wireless_serial(self.device.serial)
        self.connection_mode = "wifi" if wifi else "usb"
        if not wifi:
            self.usb_serial = self.device.serial
        name = f"{self.device.brand.title()} {self.device.model}"
        subtitle = f"Android {self.device.android_version}  ·  {self.device.chipset}"
        self.device_panel.set_detecting(False)
        self.device_panel.show_connected(name, subtitle, wifi)
        self.device_panel.select_serial(self.device.serial)
        self.launch_btn.setEnabled(True)
        self._fill_diagnosis()
        for note in info.notes:
            self._log(f"Aviso: {note}", "warning")
        self._log("Detección completa.")
        self.status_message.emit(f"Detectado: {name}")
        self.device_status.emit(f"{name} · {'WiFi' if wifi else 'USB'}", True)
        self._update_launch_summary()
        if self._detect_is_auto:
            self._toast(f"{name} detectado", "success")
        self.on_resolution_refresh()
        self._poll_status(force_battery=True)

    def _on_detect_failed(self, message: str) -> None:
        self.device_panel.set_detecting(False)
        self._clear_detection()
        serial = self.device_panel.selected_serial()
        if self._detect_is_auto and serial:
            self._auto_detect_failed.add(serial)  # no insistir solo en bucle
        self._log(f"Error: {message}", "error")
        self.status_message.emit("Error al detectar el setup")
        self.device_status.emit("Sin conectar", False)
        self.device_panel.show_disconnected(message)
        if self._detect_is_auto:
            self._toast(message, "error")
        else:
            QMessageBox.warning(self, "Error de detección", message)

    def _fill_diagnosis(self) -> None:
        device = self.device
        encoder = device.best_encoder
        conexion = f"WiFi ({device.serial})" if wireless.is_wireless_serial(device.serial) else "USB"
        self.device_panel.set_diagnosis(
            {
                "Conexión": conexion,
                "Celular": f"{device.brand} {device.model} (Android {device.android_version})",
                "Chipset": device.chipset,
                "Mejor encoder": f"{encoder.name} ({encoder.codec}, {encoder.kind})" if encoder else "no detectado",
                "Velocidad USB": self.usb.speed_label,
                "USB autosuspend": "sí" if self.usb.any_autosuspend else "no",
                "Monitor": f"{self.monitor.name} {self.monitor.width}x{self.monitor.height}",
                "CPU governor": self.cpu_status.governor,
            }
        )

    def on_restart_adb(self) -> None:
        self.device_panel.restart_btn.setEnabled(False)
        self.device_panel.detect_btn.setEnabled(False)
        self._log("Reiniciando servidor ADB (kill-server + start-server)...")
        self.status_message.emit("Reiniciando ADB...")
        worker = AdbRestartWorker()
        worker.done.connect(self._on_adb_restart_done)
        worker.failed.connect(self._on_adb_restart_failed)
        self.adb_restart_worker = worker
        self._start(worker)

    def _on_adb_restart_done(self, message: str) -> None:
        self.device_panel.restart_btn.setEnabled(True)
        self._log(f"ADB reiniciado: {message}")
        self.status_message.emit("ADB reiniciado. Detectando de nuevo...")
        self._toast("ADB reiniciado", "success")
        self._auto_detect_failed.clear()
        # El reinicio del demonio invalida cualquier detección previa (el
        # celular que estaba antes puede ya no ser el que está conectado
        # ahora), así que se relanza "Detectar" en vez de dejar datos viejos.
        self.on_detect()

    def _on_adb_restart_failed(self, message: str) -> None:
        self.device_panel.restart_btn.setEnabled(True)
        self.device_panel.detect_btn.setEnabled(True)
        self._log(f"Error reiniciando ADB: {message}", "error")
        self.status_message.emit("Error al reiniciar ADB")
        self._toast(f"No se pudo reiniciar ADB: {message}", "error")

    # ======================================================================
    # Captura de pantalla
    # ======================================================================
    def on_screenshot(self) -> None:
        if not self.device:
            self._toast("Detecta el celular primero para sacar una captura.", "info")
            return
        if self.screenshot_worker is not None and self.screenshot_worker.isRunning():
            return
        directory = config_store.captures_dir(self._build_overrides())
        self.device_panel.screenshot_btn.setEnabled(False)
        self.status_message.emit("Capturando pantalla...")
        worker = ScreenshotWorker(self.device.serial, directory)
        worker.done.connect(self._on_screenshot_done)
        worker.failed.connect(self._on_screenshot_failed)
        self.screenshot_worker = worker
        self._start(worker)

    def _on_screenshot_done(self, path: Path) -> None:
        self.device_panel.screenshot_btn.setEnabled(self.device is not None)
        image = QImage(str(path))
        if not image.isNull():
            QGuiApplication.clipboard().setImage(image)
        self._log(f"Captura guardada: {path}")
        self.status_message.emit("Captura guardada")
        self._toast(
            "Captura guardada y copiada al portapapeles", "success",
            "Abrir carpeta", lambda d=path.parent: self._open_folder(d),
        )

    def _on_screenshot_failed(self, message: str) -> None:
        self.device_panel.screenshot_btn.setEnabled(self.device is not None)
        self._log(f"Captura fallida: {message}", "error")
        self._toast(message, "error")

    # ======================================================================
    # WiFi / LAN
    # ======================================================================
    def on_toggle_wifi(self) -> None:
        if self.connection_mode == "wifi":
            self.on_disable_wifi()
        else:
            self.on_enable_wifi()

    def on_enable_wifi(self) -> None:
        if not self.device:
            self._toast("Primero pulsa 'Detectar' con el celular conectado por USB.", "info")
            return
        if wireless.is_wireless_serial(self.device.serial):
            return
        self.usb_serial = self.device.serial
        self.device_panel.wifi_btn.setEnabled(False)
        self._log("Activando modo WiFi/LAN (no desconectes el cable todavía)...")
        self.status_message.emit("Cambiando a WiFi... no desconectes el cable todavía")
        worker = WifiEnableWorker(self.usb_serial, self.fields["wifi_port"].widget.value())
        worker.done.connect(self._on_wifi_enabled)
        worker.failed.connect(self._on_wifi_failed)
        self.wifi_worker = worker
        self._start(worker)

    def _on_wifi_enabled(self, address: str) -> None:
        self.device.serial = address
        self.connection_mode = "wifi"
        self._misses = 0
        self.device_panel.set_connection_mode(True)
        self.device_panel.wifi_btn.setEnabled(True)
        self.device_panel.select_serial(address)
        self._fill_diagnosis()
        self._log(f"Conectado por WiFi en {address}. Ya puedes desconectar el cable USB.")
        self.status_message.emit(f"Conectado por WiFi: {address}")
        self.device_status.emit(f"{self.device.brand.title()} {self.device.model} · WiFi", True)
        self._toast("Conectado por WiFi. Ya puedes desenchufar el cable.", "success")

    def on_disable_wifi(self) -> None:
        if not self.device:
            return
        self.device_panel.wifi_btn.setEnabled(False)
        self._log("Cerrando la conexión WiFi...")
        worker = WifiDisableWorker(self.device.serial)
        worker.done.connect(self._on_wifi_disabled)
        worker.failed.connect(self._on_wifi_failed)
        self.wifi_worker = worker
        self._start(worker)

    def _on_wifi_disabled(self) -> None:
        self.connection_mode = "usb"
        self._log("Conexión WiFi cerrada. Reconecta el cable USB y pulsa 'Detectar' de nuevo.")
        self.status_message.emit("Reconecta el cable USB y detecta de nuevo")
        self._clear_detection()
        self.device_status.emit("Sin conectar", False)
        QMessageBox.information(
            self, "Volver a USB",
            "Se cerró la conexión WiFi. Reconecta el cable USB y pulsa "
            "\"Detectar\" de nuevo para refrescar el setup.",
        )

    def _on_wifi_failed(self, message: str) -> None:
        self.device_panel.wifi_btn.setEnabled(self.device is not None)
        self._log(f"Error de WiFi: {message}", "error")
        self.status_message.emit("Error en la conexión WiFi")
        QMessageBox.warning(self, "No se pudo completar la operación WiFi", message)

    # ======================================================================
    # Audio
    # ======================================================================
    def _revert_audio_route(self) -> None:
        self.audio_page.set_route(self._last_audio_route)

    def _on_audio_route_changed(self) -> None:
        route = self.audio_page.route()
        self.audio_page.update_fine_enabled(route)

        if route == "wifi_dual":
            if self.connection_mode == "wifi":
                QMessageBox.information(
                    self, "WiFi (dual) no aplica",
                    "El video/control ya están yendo por WiFi, así que una segunda "
                    "instancia de audio por WiFi no separa nada (sería el mismo "
                    "canal de radio). Esta ruta sirve cuando el video va por USB y "
                    "solo el audio se manda aparte por WiFi.",
                )
                self._revert_audio_route()
                return
            if not self.usb_serial:
                QMessageBox.information(
                    self, "Falta detectar",
                    "Primero pulsa 'Detectar' con el celular conectado por USB.",
                )
                self._revert_audio_route()
                return
            if self.audio_wifi_address:
                self._last_audio_route = route
                return
            self._log("Preparando canal de audio por WiFi (dual)...")
            self.status_message.emit("Preparando audio por WiFi...")
            self.audio_page.route_combo.setEnabled(False)
            worker = WifiEnableWorker(self.usb_serial, self.fields["wifi_port"].widget.value())
            worker.done.connect(self._on_audio_wifi_ready)
            worker.failed.connect(self._on_audio_wifi_failed)
            self.audio_wifi_worker = worker
            self._start(worker)
            return  # _last_audio_route se fija cuando el worker termine

        if route == "bluetooth":
            self._log(
                "Audio por Bluetooth: empareja el PC desde Android (Ajustes > "
                "Bluetooth) eligiéndolo como salida de audio. Pulsa el botón "
                "'Bluetooth' para ver el estado del adaptador del PC."
            )
        self._last_audio_route = route

    def _on_audio_wifi_ready(self, address: str) -> None:
        self.audio_wifi_address = address
        self.audio_page.route_combo.setEnabled(True)
        self._last_audio_route = "wifi_dual"
        self._log(f"Canal de audio por WiFi listo en {address}.")
        self.status_message.emit("Audio por WiFi listo")

    def _on_audio_wifi_failed(self, message: str) -> None:
        self.audio_page.route_combo.setEnabled(True)
        self._log(f"No se pudo preparar el audio por WiFi: {message}", "error")
        self._revert_audio_route()
        QMessageBox.warning(self, "Audio por WiFi (dual)", message)

    def on_check_bluetooth(self) -> None:
        self.audio_page.bt_btn.setEnabled(False)
        self._log("Revisando Bluetooth del PC...")
        worker = BluetoothCheckWorker()
        worker.done.connect(self._on_bluetooth_checked)
        worker.failed.connect(self._on_bluetooth_failed)
        self.bt_worker = worker
        self._start(worker)

    def _on_bluetooth_checked(self, status) -> None:
        self.audio_page.bt_btn.setEnabled(True)
        if not status.available:
            QMessageBox.information(
                self, "Bluetooth",
                "No se detectó ningún adaptador Bluetooth en el PC. Si el "
                "hardware no tiene uno integrado, un dongle USB Bluetooth "
                "anda bien y son baratos.",
            )
            return
        lines = [f"Adaptador: {'encendido' if status.powered else 'apagado'}."]
        if not status.powered:
            lines.append("Préndelo desde el applet de Bluetooth del sistema.")
        if status.connected_devices:
            lines.append("Conectado ahora: " + ", ".join(status.connected_devices))
        elif status.connected_unknown and status.paired_devices:
            lines.append("Emparejados: " + ", ".join(status.paired_devices))
            lines.append("(no se pudo saber cuáles están conectados ahora mismo)")
        elif status.paired_devices:
            lines.append("Emparejados (ninguno conectado ahora): " + ", ".join(status.paired_devices))
        else:
            lines.append("No hay ningún dispositivo emparejado todavía.")
        lines.append("")
        lines.append(
            "Para usar el PC como salida de audio del juego: empareja el PC "
            "desde Ajustes > Bluetooth en Android, y elígelo ahí como "
            "dispositivo de audio (igual que unos parlantes Bluetooth)."
        )
        self._log("Bluetooth: " + " ".join(lines[:2]))
        QMessageBox.information(self, "Estado de Bluetooth", "\n".join(lines))

    def _on_bluetooth_failed(self, message: str) -> None:
        self.audio_page.bt_btn.setEnabled(True)
        self._log(f"No se pudo revisar Bluetooth: {message}", "error")
        QMessageBox.warning(self, "Bluetooth", message)

    # ======================================================================
    # Resolución
    # ======================================================================
    def on_match_monitor_resolution(self) -> None:
        if not self.monitor:
            self._toast("Primero pulsa 'Detectar' para leer la resolución de tu monitor.", "info")
            return
        self.fields["max_size"].widget.setValue(self.monitor.long_side)
        self._log(
            f"Tamaño máximo ajustado a {self.monitor.long_side}px "
            f"(resolución de {self.monitor.name}: {self.monitor.width}x{self.monitor.height})."
        )

    def on_resolution_use_monitor(self) -> None:
        if self.monitor is None:
            self._toast("Detecta el celular primero para conocer el monitor destino.", "info")
            return
        width, height = resolution.size_for_monitor(self.monitor.width, self.monitor.height)
        self.device_panel.resolution_section.set_size(width, height)
        self._log(
            f"Sugerido {width}x{height} a partir del monitor "
            f"{self.monitor.width}x{self.monitor.height}. Revisa y presiona 'Aplicar' "
            f"para mandarlo al celular."
        )

    def on_resolution_refresh(self) -> None:
        self._run_resolution_worker("get")

    def _run_resolution_worker(self, action: str, width: int = 0, height: int = 0) -> None:
        if self.device is None:
            self._toast("Primero presiona 'Detectar' con el celular conectado.", "info")
            return
        section = self.device_panel.resolution_section
        section.set_busy(True)
        self._resolution_action = action
        if action == "set":
            self._log(f"Cambiando resolución del celular a {width}x{height}...")
        elif action == "reset":
            self._log("Restableciendo resolución del celular a la nativa...")
        worker = PhoneResolutionWorker(self.device.serial, action, width, height)
        worker.done.connect(self._on_resolution_done)
        worker.failed.connect(self._on_resolution_failed)
        self.resolution_worker = worker
        self._start(worker)

    def _on_resolution_done(self, size) -> None:
        section = self.device_panel.resolution_section
        section.set_busy(False)
        state = "forzada" if size.is_override else "nativa"
        section.set_status(f"Resolución actual del celular: {size} ({state}).")
        self._log(f"Resolución del celular: {size} ({state}).")
        # El refresco silencioso ("get") no merece una notificación: nadie
        # pidió nada. "set"/"reset" sí, porque el usuario quiere confirmación.
        if self._resolution_action == "set":
            self._toast(f"Resolución aplicada: {size}", "success")
        elif self._resolution_action == "reset":
            self._toast(f"Resolución restablecida a la nativa: {size}", "success")
        self._resolution_action = None

    def _on_resolution_failed(self, message: str) -> None:
        self.device_panel.resolution_section.set_busy(False)
        self._log(f"Error de resolución del celular: {message}", "error")
        if self._resolution_action in ("set", "reset"):
            self._toast(f"No se pudo cambiar la resolución: {message}", "error")
        else:
            QMessageBox.warning(self, "Resolución del celular", message)
        self._resolution_action = None

    # ======================================================================
    # Comando y lanzamiento
    # ======================================================================
    def _need_detection(self) -> bool:
        if self.device is None:
            self._toast("Primero pulsa 'Detectar' para leer tu setup.", "info")
            return True
        return False

    def _combined_command_text(self, result, overrides: dict) -> str:
        text = "$ " + result.as_string()
        if overrides.get("audio_route") == "wifi_dual" and self.audio_wifi_address:
            sidecar = command_builder.build_audio_sidecar(self.audio_wifi_address, overrides)
            text += "\n\n# Audio por WiFi (instancia aparte, en paralelo):\n$ " + sidecar.as_string()
        return text

    def _overrides_for_run(self) -> dict:
        """Overrides del formulario + la ruta del archivo si se va a grabar."""
        overrides = self._build_overrides()
        self._current_recording = None
        if overrides.get("record"):
            directory = config_store.recordings_dir(overrides)
            fmt = overrides.get("record_format") or "mkv"
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._toast(f"No se pudo crear la carpeta de grabaciones: {exc}", "error")
                overrides["record"] = False
            else:
                path = adb_tools.timestamped_path(directory, "NexoHub", f".{fmt}")
                overrides["record_path"] = str(path)
                self._current_recording = path
        return overrides

    def on_build_command(self) -> None:
        if self._need_detection():
            return
        overrides = self._build_overrides()
        if overrides.get("record"):
            # Solo para mostrar: no se crea la carpeta ni se reserva el archivo.
            directory = config_store.recordings_dir(overrides)
            overrides["record_path"] = str(directory / f"NexoHub_<fecha>.{overrides.get('record_format') or 'mkv'}")
        result = command_builder.build(self.device, self.usb, self.monitor, self.cpu_status, overrides=overrides)
        for warning in result.warnings:
            self._log(f"Aviso: {warning}", "warning")
        self.log_page.set_command(self._combined_command_text(result, overrides))
        self.sections["log"].set_expanded(True)
        self.scroll_to_requested.emit(self.sections["log"])

    def _verify_device_still_connected(self) -> bool:
        """Antes de lanzar, confirma que el celular detectado sigue siendo el que
        está conectado ahora mismo (si se cambió de celular sin volver a
        detectar, 'Lanzar' usaría el serial VIEJO y fallaría sin explicar nada)."""
        try:
            connected = android_device.list_connected_serials(target=self.device.serial)
        except android_device.AndroidDeviceError as exc:
            QMessageBox.warning(
                self, "El celular ya no está disponible",
                f"{exc}\n\nSi cambiaste de celular, prueba \"Reiniciar ADB\" y después \"Detectar\" de nuevo.",
            )
            return False
        if self.device.serial not in connected:
            QMessageBox.warning(
                self, "El celular ya no está disponible",
                f"El celular detectado antes ({self.device.brand} {self.device.model}, "
                f"serial {self.device.serial}) ya no aparece conectado. Si lo cambiaste "
                f"por otro celular o reconectaste el cable, pulsa \"Detectar\" de nuevo "
                f"antes de lanzar (o \"Reiniciar ADB\" si sigue sin aparecer).",
            )
            return False
        return True

    def is_streaming(self) -> bool:
        return self.launch_worker is not None and self.launch_worker.isRunning()

    def on_launch(self) -> None:
        if self._need_detection() or self.is_streaming():
            return
        if not self._verify_device_still_connected():
            return
        overrides = self._overrides_for_run()
        result = command_builder.build(self.device, self.usb, self.monitor, self.cpu_status, overrides=overrides)
        for warning in result.warnings:
            self._log(f"Aviso: {warning}", "warning")

        sidecar_command = None
        if overrides.get("audio_route") == "wifi_dual":
            if not self.audio_wifi_address:
                QMessageBox.warning(
                    self, "Audio por WiFi (dual)",
                    "Elegiste audio por WiFi (dual) pero el canal todavía no está "
                    "listo. Vuelve a elegir esa opción en el combo de Audio y espera "
                    "a que confirme antes de lanzar.",
                )
                return
            sidecar = command_builder.build_audio_sidecar(self.audio_wifi_address, overrides)
            for warning in sidecar.warnings:
                self._log(f"Aviso (audio WiFi): {warning}", "warning")
            sidecar_command = sidecar.as_command()

        self.log_page.set_command(self._combined_command_text(result, overrides))

        fix_usb = bool(overrides.get("auto_fix_usb_power"))
        fix_cpu = bool(overrides.get("auto_fix_cpu_governor"))
        if fix_usb or fix_cpu:
            self.status_message.emit("Esperando autenticación gráfica...")

        self.launch_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        worker = LaunchWorker(result.as_command(), self.usb, fix_usb, fix_cpu)
        worker.log.connect(self._log)
        worker.fps_update.connect(self._on_fps_update)
        worker.finished_ok.connect(self._on_launch_done)
        worker.failed.connect(self._on_launch_failed)
        self.launch_worker = worker
        self._start(worker)
        self.streaming_changed.emit(True)
        self.status_message.emit("Transmitiendo")
        if self._current_recording:
            self._log(f"Grabando en {self._current_recording}")

        if sidecar_command is not None:
            self._log("Lanzando audio por WiFi en paralelo...")
            audio_worker = LaunchWorker(sidecar_command, self.usb, False, False)
            audio_worker.log.connect(lambda line: self._log(f"[audio] {line}"))
            audio_worker.finished_ok.connect(lambda: self._log("Instancia de audio por WiFi cerrada."))
            audio_worker.failed.connect(lambda msg: self._log(f"Audio por WiFi terminó con error: {msg}", "warning"))
            self.audio_launch_worker = audio_worker
            self._start(audio_worker)

        if overrides.get("keymap_enabled"):
            self._start_keymap(str(overrides.get("keymap") or "freefire"))

        if overrides.get("fps_overlay"):
            if self.fps_overlay_window is None:
                self.fps_overlay_window = FpsOverlay(log=self._log)
            self.fps_overlay_window.set_fps(0)
            self.fps_overlay_window.show(overrides.get("fps_overlay_position") or "bottom-left")

    def on_stop(self) -> None:
        if not self.is_streaming():
            return
        self.stop_btn.setEnabled(False)
        self._log("Deteniendo transmisión...")
        self.status_message.emit("Deteniendo transmisión...")
        self.launch_worker.stop()
        if self.audio_launch_worker is not None and self.audio_launch_worker.isRunning():
            self.audio_launch_worker.stop()
        self._stop_keymap()
        # Si scrcpy no cierra solo en unos segundos (colgado, etc.), se fuerza
        # el cierre para que el botón nunca quede "trabado".
        QTimer.singleShot(4000, self._force_kill_if_needed)

    def _force_kill_if_needed(self) -> None:
        for worker in (self.launch_worker, self.audio_launch_worker):
            if worker is not None and worker.process is not None and worker.process.poll() is None:
                self._log("scrcpy no respondió a tiempo; forzando cierre.", "warning")
                worker.kill()

    def _on_fps_update(self, value: float) -> None:
        self.fps_changed.emit(value)
        if self.fps_overlay_window is not None:
            self.fps_overlay_window.set_fps(value)

    def _finish_stream(self) -> None:
        self._stop_keymap()
        self.launch_btn.setEnabled(self.device is not None)
        self.stop_btn.setEnabled(False)
        if self.fps_overlay_window is not None:
            self.fps_overlay_window.hide()
        self.streaming_changed.emit(False)

    def _on_launch_done(self) -> None:
        self._finish_stream()
        self.status_message.emit("scrcpy cerrado")
        recording, self._current_recording = self._current_recording, None
        if recording is not None:
            if recording.exists() and recording.stat().st_size > 0:
                self._log(f"Grabación guardada: {recording}")
                self._toast(
                    "Grabación guardada", "success",
                    "Abrir carpeta", lambda d=recording.parent: self._open_folder(d),
                )
            else:
                self._log("La grabación no generó ningún archivo.", "warning")

    def _on_launch_failed(self, message: str) -> None:
        self._finish_stream()
        self._current_recording = None
        self._log(f"Error al lanzar: {message}", "error")
        self.status_message.emit("Error al lanzar scrcpy")
        QMessageBox.warning(self, "Error al lanzar scrcpy", message)

    # ======================================================================
    # Mapeo de teclas
    # ======================================================================
    def _start_keymap(self, name: str) -> None:
        from keymap import model  # noqa: PLC0415

        if self.keymap_worker is not None and self.keymap_worker.isRunning():
            return
        try:
            keymap = model.load(name)
        except model.KeymapError as exc:
            self._on_keymap_failed(str(exc))
            return
        self._keymap_labels = {attr: model.key_label(key) for attr, key in keymap.special_keys().items()}
        overlay = bool(self._build_overrides().get("keymap_overlay", True))
        worker = KeymapWorker(self.device.serial, name, overlay=overlay)
        worker.state_changed.connect(self._on_keymap_state)
        worker.command.connect(self._on_keymap_command)
        worker.warning.connect(lambda msg: (self._log(f"Mapeo de teclas: {msg}", "warning"), self._toast(msg, "error")))
        worker.failed.connect(self._on_keymap_failed)
        self.keymap_worker = worker
        self._start(worker)
        self._on_keymap_state("off", announce=False)

    def _stop_keymap(self) -> None:
        if self.keymap_worker is not None and self.keymap_worker.isRunning():
            self.keymap_worker.stop()
            self.input_page.set_keymap_state("Desactivado")

    def _keymap_running(self) -> bool:
        return self.keymap_worker is not None and self.keymap_worker.isRunning()

    def _on_keymap_state(self, state: str, announce: bool = True) -> None:
        k = self._keymap_labels
        texts = {
            "off": (f"Listo · {k.get('toggle_key', 'F9')} activa · {k.get('editor_key', 'F10')} abre el editor",
                    COLORS["subtext"], "Mapeo desactivado: teclado y mouse vuelven al PC.", "info"),
            "active": (f"Modo juego · {k.get('suspend_key', 'F8')} pausa · {k.get('cursor_key', 'Alt')} cursor",
                       COLORS["green"],
                       f"Modo juego: {k.get('toggle_key', 'F9')} desactiva, {k.get('suspend_key', 'F8')} pausa, "
                       f"{k.get('cursor_key', 'Alt')} muestra el cursor.", "success"),
            "cursor": (f"Cursor libre · {k.get('cursor_key', 'Alt')} vuelve a la cámara", COLORS["yellow"], "", ""),
            "suspended": (f"En pausa · {k.get('suspend_key', 'F8')} para seguir", COLORS["yellow"],
                          "Mapeo en pausa: teclado y mouse vuelven al PC.", "info"),
        }
        label, color, toast, kind = texts.get(state, texts["off"])
        self.input_page.set_keymap_state(label, color)
        if announce and toast:
            self._toast(toast, kind)

    def _on_keymap_command(self, name: str) -> None:
        if name == "editor" and not self._keymap_editor_open:
            self.on_edit_keymap(self.keymap_worker.keymap_name if self.keymap_worker else "")
        elif name == "quick" and not self._keymap_editor_open:
            self._open_quick_settings()

    def _open_quick_settings(self) -> None:
        """Ajustes rápidos en pleno juego: cambia la cámara en vivo y, al
        cerrar, guarda el mapa y vuelve al modo juego."""
        from keymap import model  # noqa: PLC0415

        from .keymap_quick import QuickSettings  # noqa: PLC0415

        worker = self.keymap_worker
        if worker is None or not worker.isRunning():
            return
        try:
            keymap = model.load(worker.keymap_name)
        except model.KeymapError as exc:
            self._toast(str(exc), "error")
            return
        dialog = QuickSettings(keymap, worker.reload, self)
        self._keymap_editor_open = True
        QTimer.singleShot(150, self._focus_keymap_editor)
        try:
            dialog.exec()
        finally:
            self._keymap_editor_open = False
        if dialog.changed:
            model.save(keymap)
        if self._keymap_running():
            worker.set_active(True)

    def _on_keymap_failed(self, message: str) -> None:
        self._log(f"Mapeo de teclas: {message}", "error")
        self.input_page.set_keymap_state("Error (ver registro)", COLORS["red"])
        self._toast(f"Mapeo de teclas: {message}", "error")

    def _open_keymap_editor(self, keymap) -> bool:
        from keymap import model  # noqa: PLC0415

        from .keymap_editor import KeymapEditor  # noqa: PLC0415

        serial = self.device.serial if self.device is not None else None
        editor = KeymapEditor(keymap, serial, self)
        self._keymap_editor_open = True
        QTimer.singleShot(150, self._focus_keymap_editor)
        try:
            editor.exec()
        finally:
            self._keymap_editor_open = False
        if editor.saved:
            self.input_page.reload_keymaps(select=keymap.name)
            running = self._keymap_running() and self.keymap_worker.keymap_name == keymap.name
            if running:
                saved = model.load(keymap.name)
                self._keymap_labels = {a: model.key_label(k) for a, k in saved.special_keys().items()}
                self.keymap_worker.reload(saved)
                self._on_keymap_state("off", announce=False)
                self._toast(
                    f"Mapa «{keymap.display_name}» guardado y aplicado: "
                    f"{self._keymap_labels['toggle_key']} para volver al juego.", "success",
                )
            else:
                self._toast(f"Mapa «{keymap.display_name}» guardado.", "success")
        return editor.saved

    @staticmethod
    def _focus_keymap_editor() -> None:
        """Si se abrió con la tecla del editor mientras scrcpy estaba en primer
        plano (o en pantalla completa), Hyprland no le da el foco solo."""
        import shutil  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        if shutil.which("hyprctl"):
            subprocess.run(
                ["hyprctl", "dispatch", "focuswindow", "title:^(Mapa de teclas)"],
                capture_output=True, timeout=2, check=False,
            )

    def on_edit_keymap(self, name: str) -> None:
        from keymap import model  # noqa: PLC0415

        try:
            keymap = model.load(name or "freefire")
        except model.KeymapError as exc:
            QMessageBox.warning(self, "Mapa de teclas", str(exc))
            return
        self._open_keymap_editor(keymap)

    def on_new_keymap(self, base_name: str) -> None:
        from keymap import model  # noqa: PLC0415

        title, ok = QInputDialog.getText(
            self, "Nuevo mapa de teclas",
            "Nombre del juego (se copia el mapa elegido como punto de partida):",
        )
        title = title.strip()
        if not ok or not title:
            return
        name = model.slugify(title)
        if name in model.available():
            QMessageBox.warning(self, "Nuevo mapa de teclas", f"Ya existe un mapa llamado «{name}».")
            return
        try:
            base = model.load(base_name) if base_name else model.Keymap(name=name)
        except model.KeymapError:
            base = model.Keymap(name=name)
        keymap = model.Keymap(
            name=name, title=title, toggle_key=base.toggle_key,
            cursor_key=base.cursor_key, controls=base.controls,
        )
        self._open_keymap_editor(keymap)

    # ======================================================================
    # Utilidades
    # ======================================================================
    def _start(self, worker) -> None:
        """Arranca un hilo y lo recuerda para poder esperarlo al cerrar."""
        self._threads = [t for t in self._threads if t.isRunning()]
        self._threads.append(worker)
        worker.start()

    def _open_folder(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._toast(f"No se pudo abrir la carpeta: {exc}", "error")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _log(self, message: str, level: str = "info") -> None:
        self.log_page.append(message)
        getattr(self._logger, level, self._logger.info)(message)

    def _toast(self, message: str, kind: str = "info", action_text: str = "", action=None) -> None:
        """Notificación chica y no bloqueante (ver Toast). Complementa el
        registro: esto es lo que se ve al toque, sin ir a buscarlo."""
        self.toast_requested.emit(message, kind, action_text, action)

    # -- cierre ------------------------------------------------------------
    def confirm_close(self) -> bool:
        """False si el usuario prefiere no cerrar porque hay una transmisión activa."""
        if not self.is_streaming():
            return True
        confirm = QMessageBox.question(
            self, "Transmisión en curso",
            "scrcpy sigue transmitiendo. Si cierras NexoHub, la transmisión se detiene. ¿Salir?",
        )
        return confirm == QMessageBox.StandardButton.Yes

    def cleanup(self) -> None:
        """Se llama al cerrar: detiene sondeos y procesos auxiliares y espera
        a los hilos, para no dejar nada huérfano ni destruir un QThread vivo."""
        self._poll_timer.stop()
        self._polling_enabled = False
        for worker in (self.launch_worker, self.audio_launch_worker):
            if worker is not None and worker.isRunning():
                worker.stop()
        self._stop_keymap()
        if self.fps_overlay_window is not None:
            self.fps_overlay_window.close()
        for worker in self._threads:
            if worker.isRunning() and not worker.wait(3000):
                if isinstance(worker, LaunchWorker):
                    worker.kill()
                    if worker.wait(1500):
                        continue
                worker.terminate()
                worker.wait(1000)

