from __future__ import annotations

import logging
import os
from pathlib import Path


LOG_PATH = Path(os.getenv("RTST_LOG_FILE", "rtst_debug.log"))


def configure_logging() -> None:
    logger = logging.getLogger("rtst")
    if logger.handlers:
        return

    level_name = os.getenv("RTST_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"rtst.{name}")


def clip_text(text: str, limit: int = 260) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
