"""Application logging setup.

Logs go to stdout (which Render/Koyeb capture in their dashboards) and, when
LOG_FILE is set (default `server.log`), to a size-rotated file for local
inspection. Idempotent: repeated app creation (tests, --reload) won't stack
duplicate handlers.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
MAX_LOG_BYTES = 5 * 1024 * 1024  # rotate at 5 MB, keep 3 old files
BACKUP_COUNT = 3


def setup_logging() -> None:
    settings = get_settings()
    level = logging.DEBUG if settings.debug else logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        formatter = logging.Formatter(LOG_FORMAT)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

        if settings.log_file:
            file_handler = RotatingFileHandler(
                settings.log_file,
                maxBytes=MAX_LOG_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
    root.setLevel(level)
