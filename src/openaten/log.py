"""Logging setup shared by the commands: standard levels, one line per record, on stderr (journald-friendly)."""
from __future__ import annotations

import logging
import os
import sys

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
ENV_VAR = "OPENATEN_LOG"


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stderr`` is *now* (it may be redirected or replaced after the handler was created)."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property  # type: ignore[override]
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:
        pass


def setup(level: str | None = None, fallback: str | None = None) -> None:
    """Configure the ``openaten`` logger. Precedence: ``level`` (flag), ``$OPENATEN_LOG``, ``fallback`` (config file), INFO."""
    name = (level or os.environ.get(ENV_VAR) or fallback or "INFO").upper()
    if name not in LEVELS:
        name = "INFO"
    root = logging.getLogger("openaten")
    root.setLevel(name)
    if not root.handlers:
        handler = _StderrHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
