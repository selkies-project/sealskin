import logging
import sys
from .settings import settings


def setup_logging():
    """Configures the root logger based on application settings."""
    log_level = settings.log_level.upper()
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    if log_level != "DEBUG":
        for lib in ["uvicorn", "websockets", "docker"]:
            logging.getLogger(lib).setLevel(logging.WARNING)

    logging.info(f"Logging configured with level: {log_level}")
