"""Optional ``config.toml``: defaults for command-line options. Precedence: command line > environment > file > built-in default.

    log_level = "INFO"

    [daemon]            # also [forward], [strip], [layout]
    role = "master"
    hotkey = "ctrl+alt+shift+k"
    port = "1.2"
    max_active = 0
    screens = "~/.config/openaten/screens.toml"
    warp = "none"                         # "uinput": place the pointer exactly where the other one came back (needs /dev/uinput access)
    clipboard = true                      # share the clipboard with the other computer (text and PNG images); on by default
    receive_dir = "~/Downloads"           # accept files sent from the other computer into this directory (the default)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from . import log as logsetup

log = logging.getLogger(__name__)

# Per-command options a file may set: key -> accepted types (the option's argparse ``dest``).
COMMAND_KEYS: dict[str, dict[str, tuple[type, ...]]] = {
    "forward": {"port": (str,), "hotkey": (str,), "max_active": (int, float), "screens": (str,)},
    "daemon": {
        "port": (str,),
        "hotkey": (str,),
        "max_active": (int, float),
        "screens": (str,),
        "role": (str,),
        "clipboard": (bool,),
        "warp": (str,),
        "receive_dir": (str,),
    },
    "strip": {"screens": (str,)},
    "layout": {"screens": (str,)},
}
CHOICES = {"role": ("master", "slave")}


def default_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openaten" / "config.toml"


def _toml():
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib


def _valid(where: str, key: str, value: Any, types: tuple[type, ...]) -> bool:
    if (isinstance(value, bool) and bool not in types) or not isinstance(value, types):
        log.warning("config: %s%s must be %s, ignoring it", where, key, "/".join(t.__name__ for t in types))
        return False
    if key in CHOICES and value not in CHOICES[key]:
        log.warning("config: %s%s must be one of %s, ignoring it", where, key, ", ".join(CHOICES[key]))
        return False
    return True


def load(path: Path | None = None, explicit: bool = False) -> dict[str, Any]:
    """Return ``{"log_level": ..., "<command>": {dest: value}}`` from the file; a missing default file is not an error.

    A bad value is skipped with a WARNING (the rest of the file still applies); unknown keys are reported the same way.
    An unreadable or malformed file raises ``ValueError`` when it was asked for explicitly, else it is skipped with a WARNING.
    """
    path = path or default_path()
    if not path.exists():
        if explicit:
            raise ValueError(f"config file not found: {path}")
        return {}
    try:
        with open(path, "rb") as f:
            raw = _toml().load(f)
    except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError is a ValueError
        if explicit:
            raise ValueError(f"{path}: {e}") from e
        log.warning("config: ignoring %s: %s", path, e)
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "log_level":
            if isinstance(value, str) and value.upper() in logsetup.LEVELS:
                out["log_level"] = value.upper()
            else:
                log.warning("config: log_level must be one of %s, ignoring it", ", ".join(logsetup.LEVELS))
        elif key in COMMAND_KEYS and isinstance(value, dict):
            known = COMMAND_KEYS[key]
            section: dict[str, Any] = {}
            for k, v in value.items():
                if k not in known:
                    log.warning("config: unknown key [%s] %s", key, k)
                elif _valid(f"[{key}] ", k, v, known[k]):
                    section[k] = os.path.expanduser(v) if k in ("screens", "receive_dir") else v
            out[key] = section
        else:
            log.warning("config: unknown key %s", key)
    return out
