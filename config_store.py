"""
Carga y guardado de la configuración de NexoHub.

Hay dos capas:

  1. ``config.yaml`` junto al programa: valores por defecto + documentación.
     La app NUNCA lo reescribe, así que sus comentarios sobreviven (antes,
     "Guardar" lo regeneraba con yaml.safe_dump y borraba toda la ayuda).
  2. ``~/.config/nexohub/config.yaml`` (respeta XDG_CONFIG_HOME): lo que el
     usuario guarda desde la GUI. Solo contiene lo que difiere de la capa 1
     y gana sobre ella.

Las carpetas de capturas y grabaciones también se resuelven aquí.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from version import APP_ID

BUNDLED_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

USER_FILE_HEADER = (
    "# Ajustes de usuario de NexoHub (generado por la app).\n"
    "# Solo contiene lo que difiere de los valores por defecto del config.yaml\n"
    "# que viene con el programa. Puedes editarlo a mano; borrarlo restablece\n"
    "# todo a los valores por defecto.\n\n"
)


class ConfigError(RuntimeError):
    """El archivo de configuración existe pero no se pudo leer."""


# --------------------------------------------------------------------------
# Rutas (se calculan en cada llamada para poder cambiar el entorno en tests)
# --------------------------------------------------------------------------
def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_ID


def state_home() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / APP_ID


def user_config_path() -> Path:
    return config_home() / "config.yaml"


def default_captures_dir() -> Path:
    return Path.home() / "Pictures" / "NexoHub"


def default_recordings_dir() -> Path:
    return Path.home() / "Videos" / "NexoHub"


def captures_dir(config: dict | None = None) -> Path:
    custom = (config or {}).get("captures_dir")
    return Path(str(custom)).expanduser() if custom else default_captures_dir()


def recordings_dir(config: dict | None = None) -> Path:
    custom = (config or {}).get("recordings_dir")
    return Path(str(custom)).expanduser() if custom else default_recordings_dir()


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------
def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"No se pudo leer {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} no tiene el formato esperado (debe ser un mapa clave: valor).")
    return _normalize(data)


def _normalize(data: dict) -> dict:
    """YAML 1.1 lee ``off`` / ``on`` sin comillas como booleanos. Como
    ``audio_route: off`` es justo un valor válido (y el de fábrica), se
    devuelve a su forma de texto para que nunca se confunda con «falso»."""
    def fix(section: dict) -> None:
        if section.get("audio_route") is False:
            section["audio_route"] = "off"

    fix(data)
    for profile in (data.get("profiles") or {}).values():
        if isinstance(profile, dict):
            fix(profile)
    return data


def load_bundled() -> dict:
    return _read_yaml(BUNDLED_CONFIG_PATH)


def load_user() -> dict:
    return _read_yaml(user_config_path())


def merge_configs(defaults: dict, user: dict) -> dict:
    """Mezcla superficial, salvo ``profiles``: los perfiles del usuario se
    suman (o reemplazan al de igual nombre) a los que vienen de fábrica."""
    merged = dict(defaults)
    for key, value in user.items():
        if key == "profiles" and isinstance(value, dict):
            profiles = dict(defaults.get("profiles") or {})
            profiles.update(value)
            merged["profiles"] = profiles
        else:
            merged[key] = value
    return merged


def load_config() -> dict:
    """Configuración efectiva: defaults del programa + ajustes del usuario."""
    return merge_configs(load_bundled(), load_user())


def load_config_safe() -> tuple[dict, list[str]]:
    """Como load_config(), pero nunca lanza: si el archivo del usuario está
    dañado se usa solo la capa de fábrica y se devuelve un aviso."""
    warnings: list[str] = []
    try:
        defaults = load_bundled()
    except ConfigError as exc:
        warnings.append(str(exc))
        defaults = {}
    try:
        user = load_user()
    except ConfigError as exc:
        warnings.append(f"{exc} Se usan los valores por defecto.")
        user = {}
    return merge_configs(defaults, user), warnings


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------
def diff_from_defaults(config: dict, defaults: dict) -> dict:
    """Deja solo lo que cambia respecto a los valores de fábrica."""
    result: dict[str, Any] = {}
    for key, value in config.items():
        if value is None:
            continue
        if key == "profiles":
            default_profiles = defaults.get("profiles") or {}
            changed = {
                name: profile
                for name, profile in (value or {}).items()
                if default_profiles.get(name) != profile
            }
            if changed:
                result["profiles"] = changed
            continue
        if key not in defaults or defaults[key] != value:
            result[key] = value
    return result


def save_user_config(config: dict) -> Path:
    """Guarda ``config`` (solo las diferencias con los defaults) de forma
    atómica en el archivo del usuario y devuelve su ruta."""
    path = user_config_path()
    data = diff_from_defaults(config, load_bundled())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(USER_FILE_HEADER)
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def migrate_legacy_config(old_path: Path | str) -> bool:
    """Antes de la 1.1 los ajustes se editaban dentro del propio ``config.yaml``
    de la instalación. Esto copia lo que el usuario había cambiado a su
    archivo en ``~/.config`` (solo las diferencias) para no perderlo al
    actualizar. No hace nada si ya existe un archivo de usuario. Devuelve
    True si migró algo."""
    if user_config_path().exists():
        return False
    try:
        old = _read_yaml(Path(old_path))
        defaults = load_bundled()
    except ConfigError:
        return False
    changes = diff_from_defaults(old, defaults)
    if not changes:
        return False
    save_user_config(merge_configs(defaults, changes))
    return True


def reset_user_config() -> bool:
    """Borra los ajustes del usuario. True si había algo que borrar."""
    path = user_config_path()
    if path.exists():
        path.unlink()
        return True
    return False
