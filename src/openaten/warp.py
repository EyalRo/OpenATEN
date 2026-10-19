"""Placing this computer's own pointer at an exact position (issue #21): the extension point and its uinput backend.

niri has no pointer-warp IPC, so hand-off *approximates* the entry point: the sender slams the other computer's pointer into a corner and
moves it by dead reckoning (:meth:`openaten.edges.EdgeSwitch.entering`), and the sender's own pointer stays where it left. A :class:`Warp` backend
lets each agent put its own pointer exactly where the other one crossed. It is called

* when control arrives (``take-control``, agent link): shortly *after* the sender's approximate placement reports, so the exact position wins,
* when control comes back (``give-back`` on the agent link, or the dead-reckoned push-back in ``motion`` mode): at the row where the pointer returned,
  instead of where it originally left.

Positions are in the shared logical coordinate space of ``screens.toml``. Backends: :class:`NullWarp` (declines; approximate behaviour) and
:class:`UinputWarp` (a virtual absolute pointer device; needs write access to ``/dev/uinput``, see :func:`uinput_state`). The warp is opt-in
(``--warp uinput`` or ``warp = "uinput"`` in the config file) because it needs that permission.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from typing import Any, Protocol

log = logging.getLogger(__name__)

UINPUT = "/dev/uinput"
ABS_MAX = 65535  # range of the virtual absolute axes; the compositor scales it onto the output
EV_ABS, ABS_X, ABS_Y = 0x03, 0x00, 0x01  # linux/input-event-codes.h (kept here so placement needs no evdev import)


class Warp(Protocol):
    name: str

    def place(self, x: float, y: float) -> bool:
        """Move this computer's pointer to (x, y) in logical pixels. False if it could not (unsupported, no permission, off-screen)."""
        ...


class NullWarp:
    """Does nothing: hand-off keeps its approximate placement."""

    name = "none"

    def place(self, x: float, y: float) -> bool:
        return False


def uinput_state(path: str = UINPUT) -> str:
    """'ok' when this process can create uinput devices, 'denied' when the node exists but is not writable, 'missing' when it does not exist."""
    if not os.path.exists(path):
        return "missing"
    return "ok" if os.access(path, os.W_OK) else "denied"


def abs_position(x: float, y: float, box: Sequence[float], max_value: int = ABS_MAX) -> tuple[int, int]:
    """Map a logical point onto the virtual device's absolute axes. ``box`` is (left, top, right, bottom) of the local screens' bounding box."""
    left, top, right, bottom = box
    fx = (x - left) / max(right - left - 1, 1)  # the last pixel column/row is at left+width-1
    fy = (y - top) / max(bottom - top - 1, 1)
    return round(min(max(fx, 0.0), 1.0) * max_value), round(min(max(fy, 0.0), 1.0) * max_value)


class UinputWarp:
    """A virtual absolute pointer (like a tablet or a VM's pointer device): writing a position moves the compositor's pointer there.

    The compositor maps the device onto the outputs. With a single output that is the whole screen; with several, the mapping is the
    compositor's choice, which has not been verified (the bounding box of the local screens is assumed).
    """

    name = "uinput"

    def __init__(self, box: Sequence[float], device_factory: Callable[[], Any] | None = None):
        self.box = tuple(box)
        self._dev = (device_factory or self._create)()  # created now, not on first use: the compositor needs a moment to notice a new device

    @staticmethod
    def _create() -> Any:
        from evdev import AbsInfo, UInput
        from evdev import ecodes as e

        caps = {
            e.EV_KEY: [e.BTN_LEFT],  # a button makes libinput treat it as a pointer rather than an unknown device
            e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)), (e.ABS_Y, AbsInfo(0, 0, ABS_MAX, 0, 0, 0))],
        }
        return UInput(caps, name="openaten-warp")

    def place(self, x: float, y: float) -> bool:
        ax, ay = abs_position(x, y, self.box)
        try:
            self._dev.write(EV_ABS, ABS_X, ax)
            self._dev.write(EV_ABS, ABS_Y, ay)
            self._dev.syn()
        except OSError as err:
            log.warning("pointer placement failed: %s", err)
            return False
        return True

    def close(self) -> None:
        try:
            self._dev.close()
        except OSError:
            pass


def make(kind: str, box: Sequence[float] | None) -> Warp:
    """The backend for ``--warp``: 'none', or 'uinput' (falls back to :class:`NullWarp` with a WARNING when it cannot be used)."""
    if kind != "uinput":
        return NullWarp()
    state = uinput_state()
    if state != "ok":
        log.warning("--warp uinput: %s is %s; pointer placement stays approximate (see docs/setup.md, Pointer warp)", UINPUT, state)
        return NullWarp()
    if box is None:
        log.warning("--warp uinput needs a screen layout (screens.toml) to know the local screens; pointer placement stays approximate")
        return NullWarp()
    try:
        return UinputWarp(box)
    except (ImportError, OSError) as err:
        log.warning("--warp uinput: cannot create the virtual pointer (%s); pointer placement stays approximate", err)
        return NullWarp()
