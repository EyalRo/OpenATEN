"""Bridge ends opened by one CLI invocation, and the cleanup they need on exit."""
from __future__ import annotations

from .usb import BridgeEnd, find_ends, port_path

OPENED: list[BridgeEnd] = []
STUCK_THREADS: list[bool] = []  # set if a link thread is still blocked inside libusb


def open_all() -> list[BridgeEnd]:
    """Open every bridge end, ordered by (bus, port path). They are closed by :func:`close_all`."""
    ends = sorted(find_ends(), key=lambda d: (d.bus, port_path(d)))
    opened = [BridgeEnd(d) for d in ends]
    OPENED.extend(opened)
    return opened


def close_all() -> None:
    """Close what was opened, unless a thread is still inside libusb (tearing libusb down then would crash)."""
    if not STUCK_THREADS:
        for end in OPENED:
            end.close()
