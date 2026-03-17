"""Centralized logging setup with optional JSON structured output.

Usage in main.py:
    from .core.logging_config import setup_logging
    setup_logging(log_level="INFO", log_format="json")
"""
from __future__ import annotations

import logging
import sys


def setup_logging(log_level: str = "INFO", log_format: str = "text") -> None:
    """Configure root logger. Call once at startup.

    Args:
        log_level: Standard Python log level (DEBUG, INFO, WARNING, ERROR).
        log_format: "text" for human-readable, "json" for structured JSON output.
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if log_format.lower() == "json":
        from pythonjsonlogger import jsonlogger
        formatter = jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s \u2014 %(message)s"
        )

    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(log_level)
