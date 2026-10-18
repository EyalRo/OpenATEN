"""Transport errors shared by every layer, so only :mod:`openaten.usb` needs to know about pyusb's exceptions."""
from __future__ import annotations


class TransportError(Exception):
    """A transport command failed."""


class TransportTimeout(TransportError):
    """The command was not accepted (out of turn / peer not ready) or did not finish in time."""


class TransportStall(TransportError):
    """The firmware stalled the pipe (an idle RX expired); reset and retry."""


def classify(e: Exception) -> TransportError:
    """Map a libusb error (by its message; pyusb exposes no stable code across backends) to the matching transport error."""
    text = str(e).lower()
    if "timed out" in text:
        return TransportTimeout(str(e))
    if "pipe" in text:
        return TransportStall(str(e))
    return TransportError(str(e))
