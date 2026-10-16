import pytest

import config_store


def test_defaults_come_from_bundled_file():
    config = config_store.load_config()
    assert config["input_mode"] == "uhid"
    assert {"competitive", "quality", "streaming"} <= set(config["profiles"])


def test_save_only_writes_differences(tmp_path):
    config = config_store.load_config()
    config["max_fps"] = 90
    config["video_codec"] = "h265"
    config["video_encoder"] = None  # None = sin definir, no se guarda
    path = config_store.save_user_config(config)
    assert path == config_store.user_config_path()

    text = path.read_text(encoding="utf-8")
    assert "max_fps: 90" in text and "video_codec: h265" in text
    assert "input_mode" not in text  # igual al default: no se guarda
    assert "video_encoder" not in text
    assert "competitive" not in text  # perfiles de fábrica intactos

    reloaded = config_store.load_config()
    assert reloaded["max_fps"] == 90 and reloaded["video_codec"] == "h265"
    assert "competitive" in reloaded["profiles"]


def test_bundled_config_is_never_rewritten():
    before = config_store.BUNDLED_CONFIG_PATH.read_text(encoding="utf-8")
    config_store.save_user_config({**config_store.load_config(), "max_fps": 120})
    assert config_store.BUNDLED_CONFIG_PATH.read_text(encoding="utf-8") == before
    assert "# " in before  # los comentarios siguen ahí


def test_user_profiles_are_added_and_can_be_removed():
    config = config_store.load_config()
    config["profiles"] = {
        **config["profiles"],
        "mi_perfil": {"label": "Mi perfil", "max_size": 1000},
    }
    config_store.save_user_config(config)
    assert "mi_perfil" in config_store.load_config()["profiles"]

    config = config_store.load_config()
    del config["profiles"]["mi_perfil"]
    config_store.save_user_config(config)
    assert "mi_perfil" not in config_store.load_config()["profiles"]


def test_reverting_to_default_removes_override():
    config = config_store.load_config()
    config_store.save_user_config({**config, "input_mode": "sdk"})
    assert config_store.load_config()["input_mode"] == "sdk"
    config_store.save_user_config({**config_store.load_config(), "input_mode": "uhid"})
    assert config_store.load_config()["input_mode"] == "uhid"


def test_corrupt_user_file(tmp_path):
    path = config_store.user_config_path()
    path.parent.mkdir(parents=True)
    path.write_text("esto: [no cierra", encoding="utf-8")
    with pytest.raises(config_store.ConfigError):
        config_store.load_config()
    config, warnings = config_store.load_config_safe()
    assert config["input_mode"] == "uhid"  # cae a los defaults
    assert warnings


def test_non_mapping_user_file():
    path = config_store.user_config_path()
    path.parent.mkdir(parents=True)
    path.write_text("- una\n- lista\n", encoding="utf-8")
    with pytest.raises(config_store.ConfigError):
        config_store.load_config()


def test_reset_user_config():
    assert config_store.reset_user_config() is False
    config_store.save_user_config({**config_store.load_config(), "max_fps": 75})
    assert config_store.reset_user_config() is True
    assert config_store.load_config()["max_fps"] == 0


def test_output_dirs():
    assert config_store.captures_dir().name == "NexoHub"
    assert config_store.recordings_dir({"recordings_dir": "~/clips"}).name == "clips"


def test_yaml_off_is_not_read_as_false(tmp_path):
    """`audio_route: off` sin comillas es un booleano en YAML 1.1: no debe
    llegar así al constructor del comando (se vería como una ruta inválida)."""
    path = tmp_path / "cfg.yaml"
    path.write_text("audio_route: off\nprofiles:\n  x:\n    audio_route: off\n", encoding="utf-8")
    data = config_store._read_yaml(path)
    assert data["audio_route"] == "off"
    assert data["profiles"]["x"]["audio_route"] == "off"
    assert config_store.load_bundled()["audio_route"] == "off"


def test_migrate_legacy_config_keeps_only_user_changes(tmp_path):
    legacy = tmp_path / "old_config.yaml"
    defaults = config_store.load_bundled()
    legacy.write_text(
        "max_fps: 90\nbitrate_max: 12M\nwifi_port: %d\n"
        "profiles:\n  mio:\n    label: Mio\n    video_codec: h265\n" % defaults["wifi_port"],
        encoding="utf-8",
    )
    assert config_store.migrate_legacy_config(legacy) is True
    user = config_store.load_user()
    assert user["max_fps"] == 90 and user["bitrate_max"] == "12M"
    assert "wifi_port" not in user  # igual al valor de fábrica
    assert user["profiles"]["mio"]["video_codec"] == "h265"
    # No pisa un archivo de usuario ya existente.
    legacy.write_text("max_fps: 30\n", encoding="utf-8")
    assert config_store.migrate_legacy_config(legacy) is False
    assert config_store.load_user()["max_fps"] == 90


def test_migrate_legacy_config_without_changes(tmp_path):
    assert config_store.migrate_legacy_config(config_store.BUNDLED_CONFIG_PATH) is False
    assert not config_store.user_config_path().exists()
    assert config_store.migrate_legacy_config(tmp_path / "no_existe.yaml") is False
