"""Shared application logging setup."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_configured = False
_log_path: Path | None = None
_loguru: Any | None = None


def default_log_path() -> Path:
    if path := os.environ.get("BASEMODE_LOG"):
        return Path(path).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "basemode-loom" / "loom.log"


def configure_logging(app_name: str = "basemode-loom") -> Path:
    global _configured, _log_path, _loguru
    if _configured and _log_path is not None:
        return _log_path

    path = default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from loguru import logger as loguru_logger  # type: ignore
    except Exception:
        logger = logging.getLogger("basemode_loom")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = RotatingFileHandler(
                path,
                maxBytes=10 * 1024 * 1024,
                backupCount=10,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s [%(process)d] %(message)s"
                )
            )
            logger.addHandler(handler)
            logger.propagate = False
        logger.info("%s logging initialized: %s", app_name, path)
    else:
        if not _configured:
            loguru_logger.add(
                path,
                level="INFO",
                rotation="10 MB",
                retention="14 days",
                compression="zip",
                enqueue=True,
                backtrace=True,
                diagnose=False,
            )
        _loguru = loguru_logger
        loguru_logger.bind(component=app_name).info("logging initialized: {}", path)

    _configured = True
    _log_path = path
    return path


def get_logger(name: str):
    configure_logging()
    if _loguru is not None:
        return _loguru.bind(module=name)
    return logging.getLogger(name)
