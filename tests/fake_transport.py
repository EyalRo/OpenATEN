"""In-memory model of the two bridge ends, enforcing the firmware's turn rules (docs/hardware.md)."""
from __future__ import annotations

import threading
import time

from openaten.link import TransportError, TransportStall, TransportTimeout


class FakeEnd:
    def __init__(self, bridge: FakeBridge, name: str):
        self.bridge, self.name = bridge, name
        self.last: str | None = None  # 'tx' or 'rx': the next command must be the other one
        self.armed: int | None = None
        self.inbox: bytes | None = None
        self.peer: FakeEnd
        self.hid_in: list[bytes] = []  # HID reports injected on this end by the peer
        self.hid_times: list[float] = []

    def send(self, data: bytes, timeout: float) -> None:
        b = self.bridge
        with b.cond:
            deadline = time.monotonic() + timeout
            # refused while out of turn, or until the peer has an RX armed
            while self.last == "tx" or self.peer.armed is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TransportTimeout("send refused")
                b.cond.wait(left)
            if self.peer.armed != len(data):
                raise TransportError(f"length mismatch: sent {len(data)}, peer armed {self.peer.armed}")
            self.peer.inbox, self.peer.armed, self.peer.last = bytes(data), None, "rx"
            self.last = "tx"
            b.cond.notify_all()

    def recv(self, size: int, timeout: float) -> bytes:
        b = self.bridge
        with b.cond:
            deadline = time.monotonic() + timeout
            while self.last == "rx":
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TransportTimeout("recv refused")
                b.cond.wait(left)
            self.armed = size
            b.cond.notify_all()
            stall_at = time.monotonic() + b.stall_after
            while self.inbox is None:
                now = time.monotonic()
                if now >= stall_at:
                    self.armed = None
                    raise TransportStall("idle receive stalled by firmware")
                if now >= deadline:
                    raise TransportTimeout("recv timed out")  # host-side abort (the real hardware gets stuck here)
                b.cond.wait(min(stall_at, deadline) - now)
            data, self.inbox = self.inbox, None
            b.cond.notify_all()
            return data

    def hid(self, report: bytes, timeout: float) -> None:
        with self.bridge.cond:
            self.peer.hid_in.append(bytes(report))
            self.peer.hid_times.append(time.monotonic())

    def reset(self) -> None:
        with self.bridge.cond:
            self.armed = None


class FakeBridge:
    def __init__(self, stall_after: float = 0.3):
        self.cond = threading.Condition()
        self.stall_after = stall_after
        self.a, self.b = FakeEnd(self, "a"), FakeEnd(self, "b")
        self.a.peer, self.b.peer = self.b, self.a
