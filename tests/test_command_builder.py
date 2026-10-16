import command_builder
from detectors.android_device import AndroidDevice


def build(phone, usb, monitor, cpu, **overrides):
    return command_builder.build(phone, usb, monitor, cpu, overrides=overrides)


def test_basic_usb_command(phone, usb3, monitor, cpu_perf):
    result = build(phone, usb3, monitor, cpu_perf)
    args = result.args
    assert args[:2] == ["-s", "R58M12ABCD"]
    assert "--video-codec=h264" in args
    assert "--video-encoder=c2.qti.avc.encoder" in args
    assert "--video-bit-rate=32M" in args  # USB 3.x
    assert "--max-size=1920" in args  # lado largo del monitor
    assert "--stay-awake" in args  # activado por defecto
    assert "--video-buffer=0" in args
    assert "--keyboard=uhid" in args and "--mouse=uhid" in args


def test_keymap_switches_mouse_to_sdk(phone, usb3, monitor, cpu_perf):
    args = build(phone, usb3, monitor, cpu_perf, keymap_enabled=True).args
    assert "--keyboard=uhid" in args and "--mouse=sdk" in args


def test_usb2_bitrate_and_governor_warning(phone, usb2, monitor):
    from detectors.cpu import CpuStatus

    result = build(phone, usb2, monitor, CpuStatus("powersave", 800, 4000))
    assert "--video-bit-rate=16M" in result.args
    assert any("powersave" in w for w in result.warnings)


def test_wifi_uses_conservative_bitrate(phone, usb3, monitor, cpu_perf):
    phone.serial = "192.168.1.23:5555"
    result = build(phone, usb3, monitor, cpu_perf)
    assert "--video-bit-rate=12M" in result.args
    assert any("WiFi" in w for w in result.warnings)


def test_requested_codec_picks_encoder_of_that_codec(phone, usb3, monitor, cpu_perf):
    result = build(phone, usb3, monitor, cpu_perf, video_codec="h265")
    assert "--video-codec=h265" in result.args
    assert "--video-encoder=c2.qti.hevc.encoder" in result.args


def test_stay_awake_can_be_disabled(phone, usb3, monitor, cpu_perf):
    result = build(phone, usb3, monitor, cpu_perf, stay_awake=False)
    assert "--stay-awake" not in result.args


def test_screen_and_window_options(phone, usb3, monitor, cpu_perf):
    result = build(
        phone, usb3, monitor, cpu_perf,
        turn_screen_off=True, show_touches=True, power_off_on_close=True,
        always_on_top=True, borderless=True, window_title="Mi juego",
        capture_orientation="@90",
    )
    for flag in (
        "--turn-screen-off", "--show-touches", "--power-off-on-close",
        "--always-on-top", "--window-borderless", "--window-title=Mi juego",
        "--capture-orientation=@90",
    ):
        assert flag in result.args
    # el título con espacios se entrecomilla al mostrarlo
    assert "'--window-title=Mi juego'" in result.as_string()


def test_control_options_dropped_without_control(phone, usb3, monitor, cpu_perf):
    result = build(
        phone, usb3, monitor, cpu_perf,
        input_mode="disabled", turn_screen_off=True, show_touches=True,
        gamepad="uhid",
    )
    assert "--no-control" in result.args
    for flag in ("--stay-awake", "--turn-screen-off", "--show-touches"):
        assert flag not in result.args
    assert not any(a.startswith("--gamepad") for a in result.args)
    assert len(result.warnings) >= 3


def test_gamepad_modes(phone, usb3, monitor, cpu_perf):
    assert "--gamepad=uhid" in build(phone, usb3, monitor, cpu_perf, gamepad="uhid").args
    assert not any(
        a.startswith("--gamepad") for a in build(phone, usb3, monitor, cpu_perf).args
    )
    bad = build(phone, usb3, monitor, cpu_perf, gamepad="raro")
    assert not any(a.startswith("--gamepad") for a in bad.args)
    assert any("gamepad" in w for w in bad.warnings)


def test_gamepad_aoa_over_wifi_warns(phone, usb3, monitor, cpu_perf):
    phone.serial = "192.168.1.23:5555"
    result = build(phone, usb3, monitor, cpu_perf, gamepad="aoa")
    assert "--gamepad=aoa" in result.args
    assert any("AOA" in w for w in result.warnings)


def test_invalid_orientation_is_ignored(phone, usb3, monitor, cpu_perf):
    result = build(phone, usb3, monitor, cpu_perf, capture_orientation="45")
    assert not any(a.startswith("--capture-orientation") for a in result.args)
    assert any("capture_orientation" in w for w in result.warnings)
    for ok in ("@", "0", "270", "flip90", "@flip180"):
        assert f"--capture-orientation={ok}" in build(
            phone, usb3, monitor, cpu_perf, capture_orientation=ok
        ).args
    assert not any(
        a.startswith("--capture-orientation")
        for a in build(phone, usb3, monitor, cpu_perf, capture_orientation="auto").args
    )


def test_recording(phone, usb3, monitor, cpu_perf):
    result = build(
        phone, usb3, monitor, cpu_perf,
        record=True, record_path="/tmp/x.mkv", record_format="mkv", audio_route="off",
    )
    assert "--record=/tmp/x.mkv" in result.args
    assert "--record-format=mkv" in result.args
    assert any("SIN audio" in w for w in result.warnings)


def test_recording_with_scrcpy_audio_has_no_audio_warning(phone, usb3, monitor, cpu_perf):
    result = build(
        phone, usb3, monitor, cpu_perf,
        record=True, record_path="/tmp/x.mkv", audio_route="scrcpy",
    )
    assert not any("SIN audio" in w for w in result.warnings)


def test_recording_without_path_is_ignored(phone, usb3, monitor, cpu_perf):
    result = build(phone, usb3, monitor, cpu_perf, record=True)
    assert not any(a.startswith("--record") for a in result.args)
    assert result.warnings


def test_video_buffer(phone, usb3, monitor, cpu_perf):
    assert "--video-buffer=40" in build(phone, usb3, monitor, cpu_perf, video_buffer=40).args
    result = build(phone, usb3, monitor, cpu_perf, video_buffer="abc")
    assert "--video-buffer=0" in result.args and result.warnings


def test_audio_routes(phone, usb3, monitor, cpu_perf):
    assert "--no-audio" in build(phone, usb3, monitor, cpu_perf, audio_route="off").args
    assert "--no-audio" in build(phone, usb3, monitor, cpu_perf, audio_route="bluetooth").args
    on = build(
        phone, usb3, monitor, cpu_perf,
        audio_route="scrcpy", audio_codec="aac", audio_bitrate="64K", audio_buffer=30,
    )
    assert "--no-audio" not in on.args
    assert "--audio-codec=aac" in on.args
    assert "--audio-bit-rate=64K" in on.args
    assert "--audio-buffer=30" in on.args


def test_audio_sidecar():
    result = command_builder.build_audio_sidecar(
        "192.168.1.5:5556", {"audio_codec": "aac", "audio_buffer": 20}
    )
    assert result.args[:2] == ["-s", "192.168.1.5:5556"]
    for flag in ("--no-video", "--no-control", "--no-window", "--audio-codec=aac", "--audio-buffer=20"):
        assert flag in result.args


def test_fullscreen_targets_monitor(phone, usb3, monitor, cpu_perf):
    monitor.x, monitor.y = 1920, 0
    result = build(phone, usb3, monitor, cpu_perf, fullscreen=True)
    assert "--window-x=1920" in result.args and "--fullscreen" in result.args


def test_extra_flags_none_is_tolerated(phone, usb3, monitor, cpu_perf):
    assert build(phone, usb3, monitor, cpu_perf, extra_flags=None).args
    assert "--foo" in build(phone, usb3, monitor, cpu_perf, extra_flags=["--foo"]).args


def test_no_encoder_warns(usb3, monitor, cpu_perf):
    bare = AndroidDevice(serial="X1")
    result = build(bare, usb3, monitor, cpu_perf)
    assert any("encoder" in w for w in result.warnings)
