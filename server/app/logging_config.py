"""Logging configuration."""

from __future__ import annotations

import logging
import sys

from .settings import settings


def setup_logging() -> None:
    """Configure the root logger from `settings.log_level`."""
    log_level = settings.log_level.upper()
    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    if log_level != "DEBUG":
        for lib in ["uvicorn", "websockets", "docker", "watchfiles"]:
            logging.getLogger(lib).setLevel(logging.WARNING)
    logging.info("Logging configured with level: %s", log_level)
