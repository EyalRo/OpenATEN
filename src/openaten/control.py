"""Hand-off protocol between the two OpenATEN agents, carried as small JSON messages on the link's control channel.

Both computers run an agent. The one whose user is pushing the pointer toward the other is the *sender* (it grabs its input devices and
injects HID reports); the other is *receiving*. Borders are named by a key that both agents derive from the shared screen layout, and rows
are coordinates in that layout's shared virtual space, so the two agents agree on exact positions without sharing a screen model.

    sender  -> receiver   take-control {border, row}   pointer pushed through ``border`` at ``row``
    receiver -> sender    give-back    {border, row}   pointer pushed back through the border: stop forwarding
    sender  -> receiver   release      {}              forwarding ended (hotkey, widget, error): the receiver is no longer receiving

:class:`Handoff` is the pure state machine that turns local edge events and peer messages into those actions.
"""
from __future__ import annotations

import json
import time
from typing import Callable

CH_CONTROL = 1  # link channel carrying these messages


def encode(kind: str, **fields) -> bytes:
    return json.dumps({"kind": kind, **fields}, separators=(",", ":")).encode()


def decode(data: bytes) -> dict | None:
    try:
        msg = json.loads(data)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) and isinstance(msg.get("kind"), str) else None


class Handoff:
    """Who is receiving, and what a pointer entering or leaving a border strip means right now.

    ``push(border, row)`` starts forwarding, ``pull()`` stops it, ``send(bytes)`` talks to the peer. While receiving, a strip *enter* only counts
    as the user pushing back once the pointer has left the strip after control arrived (it lands on the strip when it is placed there) and the
    placement has settled (``settle`` seconds).
    """

    def __init__(
        self,
        push: Callable[[str, float], None],
        pull: Callable[[], None],
        send: Callable[[bytes], None],
        settle: float = 0.5,
        now: Callable[[], float] = time.monotonic,
    ):
        self.push, self.pull, self.send, self.settle, self.now = push, pull, send, settle, now
        self.receiving = False
        self._armed = False
        self._since = 0.0

    def edge(self, border: str, row: float, enter: bool = True) -> None:
        """The local pointer entered (or left) the strip on ``border`` at ``row``."""
        if not self.receiving:
            if enter:
                self.push(border, row)  # also announces take-control once forwarding actually starts: see :meth:`started`
            return
        if self.now() - self._since < self.settle:
            return
        if not enter:
            self._armed = True
        elif self._armed:
            self.receiving = False
            self.send(encode("give-back", border=border, row=row))

    def started(self, border: str | None, row: float | None) -> None:
        """Forwarding really began (it can be deferred while keys are held); ``border`` is None when it was started by hotkey."""
        self.send(encode("take-control", border=border, row=row))

    def stopped(self) -> None:
        self.send(encode("release"))

    def message(self, msg: dict) -> None:
        kind = msg.get("kind")
        if kind == "take-control":
            self.receiving, self._armed, self._since = True, False, self.now()
        elif kind == "release":
            self.receiving = False
        elif kind == "give-back":
            self.pull()
