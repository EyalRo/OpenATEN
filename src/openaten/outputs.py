"""This computer's screens from the compositor, so ``screens.toml`` only needs the other computer's screens (``local_outputs = "niri"``).

niri prints its outputs as JSON (``niri msg --json outputs``): an object keyed by connector name whose ``logical`` member has the position and
size in logical pixels (``null`` for a disabled output). The parser also accepts a plain list of such objects. The logical coordinates are the
space ``screens.toml`` already uses, so the other computer's screens are placed relative to them.
"""
from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from typing import Any

from .edges import Screen

log = logging.getLogger(__name__)

NIRI_COMMAND = ["niri", "msg", "--json", "outputs"]


def parse_niri(text: str, host: str) -> list[Screen]:
    """Screens on ``host`` from niri's JSON; raises ValueError if the text is not the expected shape. Disabled outputs are skipped."""
    data: Any = json.loads(text)
    items = list(data.values()) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("expected an object or a list of outputs")
    screens = []
    for out in items:
        if not isinstance(out, dict) or not isinstance(out.get("name"), str):
            raise ValueError("an output has no name")
        logical = out.get("logical")
        if logical is None:  # disabled output
            continue
        try:
            x, y, w, h = (float(logical[k]) for k in ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"output {out['name']!r} has no usable logical geometry") from e
        if w <= 0 or h <= 0:
            raise ValueError(f"output {out['name']!r} has an empty logical size")
        screens.append(Screen(out["name"], host, x, y, w, h))
    return screens


def niri_outputs(host: str, run: Callable[..., Any] = subprocess.run) -> list[Screen] | None:
    """The enabled outputs of the running niri, or None (with a WARNING) when niri cannot be queried so the caller falls back to the file."""
    try:
        done = run(NIRI_COMMAND, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("cannot query niri for the screen layout (%s); using the screens in the layout file", e)
        return None
    if done.returncode != 0:
        log.warning("`niri msg outputs` failed (%s); using the screens in the layout file", (done.stderr or "").strip() or done.returncode)
        return None
    try:
        screens = parse_niri(done.stdout, host)
    except ValueError as e:  # includes json.JSONDecodeError
        log.warning("unexpected output from `niri msg --json outputs` (%s); using the screens in the layout file", e)
        return None
    if not screens:
        log.warning("niri reports no enabled outputs; using the screens in the layout file")
        return None
    return screens
