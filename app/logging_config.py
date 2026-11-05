"""Application-wide console and timestamped file logging."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configure_logging(log_dir: str | Path | None = None) -> Path:
    """Configure root logging and return this run's log file path."""
    root_logger = logging.getLogger()
    existing_path = getattr(root_logger, "trading_agent_log_path", None)
    if existing_path:
        return Path(existing_path)

    output_dir = Path(log_dir) if log_dir is not None else PROJECT_ROOT / "logs"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    log_path = output_dir / f"trading-agent_{timestamp}.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.trading_agent_log_path = str(log_path)
    return log_path
