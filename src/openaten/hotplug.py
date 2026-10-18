"""Bridge hotplug events from the kernel's uevent netlink socket (no extra dependency; Linux only).

The kernel multicasts a message for every USB device that appears or disappears; unprivileged processes may listen. We keep only the
bridge function (``PRODUCT=5e3/627/...``) so the daemon can reconnect the moment the switch re-enumerates instead of waiting for its next
retry.
"""
from __future__ import annotations

import logging
import select
import socket
import threading
from collections.abc import Callable

from .usb import PID, VID

log = logging.getLogger(__name__)

NETLINK_KOBJECT_UEVENT = 15
KERNEL_GROUP = 1  # uevents as emitted by the kernel (group 2 is udev's re-broadcast)


def parse(message: bytes) -> str | None:
    """'add' or 'remove' if this uevent is the bridge device appearing or disappearing, else None."""
    fields = message.split(b"\0")
    if not fields or b"@" not in fields[0]:  # "<action>@<devpath>" header; udev's own messages start with a libudev magic instead
        return None
    props = dict(f.split(b"=", 1) for f in fields[1:] if b"=" in f)
    if props.get(b"SUBSYSTEM") != b"usb" or props.get(b"DEVTYPE") != b"usb_device":
        return None
    action = props.get(b"ACTION", b"").decode()
    if action not in ("add", "remove"):
        return None
    try:
        vid, pid = (int(x, 16) for x in props.get(b"PRODUCT", b"").decode().split("/")[:2])
    except ValueError:  # missing or malformed PRODUCT: not ours (and never worth killing the listener thread)
        return None
    return action if (vid, pid) == (VID, PID) else None


class HotplugMonitor:
    """Calls ``on_event('add' | 'remove')`` from a background thread until :meth:`stop`."""

    def __init__(self, on_event: Callable[[str], None]):
        self.on_event = on_event
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
        self._sock.bind((0, KERNEL_GROUP))
        self._thread = threading.Thread(target=self._run, name="openaten-hotplug", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self._sock], [], [], 0.5)
            if not ready:
                continue
            try:
                action = parse(self._sock.recv(8192))
            except OSError:
                break
            if action:
                log.debug("bridge %s", action)
                self.on_event(action)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


def start(on_event: Callable[[str], None]) -> HotplugMonitor | None:
    """Start listening; None (with a WARNING) where netlink uevents are unavailable, so the daemon falls back to polling."""
    try:
        return HotplugMonitor(on_event)
    except (OSError, AttributeError) as e:
        log.warning("hotplug events unavailable, falling back to polling: %s", e)
        return None
