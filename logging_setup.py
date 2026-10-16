"""Registro a archivo (rotativo) para poder diagnosticar problemas después de
cerrar la app. La pestaña "Comando y registro" muestra lo mismo en vivo."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config_store
from version import APP_ID

LOGGER_NAME = APP_ID


def log_file_path() -> Path:
    return config_store.state_home() / f"{APP_ID}.log"


def setup_logging() -> logging.Logger:
    """Idempotente: se puede llamar varias veces sin duplicar handlers."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        path = log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            path, maxBytes=512_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    except OSError:
        # Carpeta de estado sin permisos: la app sigue, solo sin archivo.
        handler = logging.NullHandler()
    logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
