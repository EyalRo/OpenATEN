"""Placing this computer's own pointer at an exact position (issue #21): the extension point for a real backend.

niri has no pointer-warp IPC, so hand-off currently *approximates* the entry point: the sender slams the other computer's pointer into a corner and
moves it by dead reckoning (:meth:`openaten.edges.EdgeSwitch.entering`), and the sender's own pointer stays where it left. A :class:`Warp` backend
(a uinput absolute pointer device, or libei through the RemoteDesktop portal) would let each agent put its own pointer exactly where the other one
crossed. The daemon calls :meth:`Warp.place`

* when control arrives (``take-control``): shortly *after* the sender's approximate placement reports, so the exact position wins, and
* when control comes back (``give-back``): at the row where the pointer returned, instead of where it originally left.

Positions are in the shared logical coordinate space of ``screens.toml``. The only backend today is :class:`NullWarp`, which declines, leaving the
approximate behaviour unchanged.
"""
from __future__ import annotations

from typing import Protocol


class Warp(Protocol):
    def place(self, x: float, y: float) -> bool:
        """Move this computer's pointer to (x, y) in logical pixels. False if it could not (unsupported, no permission, off-screen)."""
        ...


class NullWarp:
    """Does nothing: hand-off keeps its approximate placement."""

    def place(self, x: float, y: float) -> bool:
        return False
