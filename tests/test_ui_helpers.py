import pytest

from ui import helpers


@pytest.mark.parametrize(
    "text, expected",
    [
        ("32M", 32.0),
        ("12m", 12.0),
        ("500K", 0.5),
        ("8000000", 8.0),
        ("", None),
        ("   ", None),
        (None, None),
        ("rápido", None),
        ("12X", None),
    ],
)
def test_parse_bitrate_mbps(text, expected):
    assert helpers.parse_bitrate_mbps(text) == expected


def test_stream_warnings_only_when_too_high():
    assert helpers.stream_warning_reasons(60, "20M") == []
    assert helpers.stream_warning_reasons(0, "") == []
    assert helpers.stream_warning_reasons(120, "8M") == ["120 FPS"]
    assert helpers.stream_warning_reasons(144, "40M") == ["144 FPS", "40 Mbps de bitrate"]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Mi perfil", "mi_perfil"),
        ("  Ñandú rápido!! ", "nandu_rapido"),
        ("Juego #1 (WiFi)", "juego_1_wifi"),
        ("!!!", ""),
        ("", ""),
    ],
)
def test_slugify(name, expected):
    assert helpers.slugify(name) == expected


def test_describe_profile():
    assert helpers.describe_profile(
        {"video_codec": "h264", "bitrate_max": "6M", "max_size": 1280, "max_fps": 90}
    ) == "h264 · 6M · 1280 px · 90 FPS"
    assert helpers.describe_profile({}) == "Perfil propio"


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "00:00"), (59.9, "00:59"), (61, "01:01"), (3600, "01:00:00"), (3725, "01:02:05"), (-5, "00:00")],
)
def test_format_elapsed(seconds, expected):
    assert helpers.format_elapsed(seconds) == expected


def test_profile_keys_cover_reset_keys():
    assert set(helpers.RESET_ON_PROFILE) <= set(helpers.PROFILE_KEYS)
    assert not set(helpers.SESSION_ONLY_KEYS) & set(helpers.PROFILE_KEYS)
