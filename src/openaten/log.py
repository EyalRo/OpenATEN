"""Logging setup shared by the commands: standard levels, one line per record, on stderr (journald-friendly)."""
from __future__ import annotations

import logging
import os
import sys

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
ENV_VAR = "OPENATEN_LOG"


def setup(level: str | None = None) -> None:
    """Configure the ``openaten`` logger. ``level`` wins over ``$OPENATEN_LOG``; the default is INFO."""
    name = (level or os.environ.get(ENV_VAR) or "INFO").upper()
    if name not in LEVELS:
        name = "INFO"
    root = logging.getLogger("openaten")
    root.setLevel(name)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
