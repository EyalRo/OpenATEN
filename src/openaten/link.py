"""Reliable message link over the bridge's strictly alternating transport (see docs/design.md).

Hardware rules this module is built around (docs/hardware.md):

* each end alternates TX and RX; the *master* does TX then RX, the *slave* RX then TX;
* a sender's length must equal the length the receiver armed, so every frame announces the wire length of the sender's *next* frame;
* an armed RX must never be abandoned from the host; an idle RX is allowed to stall inside the firmware (~5.5 s).

A :class:`Transport` is one end of the bridge. :class:`UsbTransport` wraps :class:`openaten.usb.BridgeEnd`;
the tests use a fake that models the same rules.
"""
from __future__ import annotations

import struct
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

SLOT = 2048  # wire lengths are multiples of this; also the default frame size
MAGIC = b"OATN"
VERSION = 1
KIND_POLL = 0  # empty frame (poll / keep-alive / empty reply)
KIND_DATA = 1  # carries one chunk of a message
_HDR = struct.Struct("<4sBBHIIIIIIII")  # magic ver kind flags seq ack next_len channel msg_id offset total plen
_OVERHEAD = _HDR.size + 4  # header + crc32
MAX_FRAME = 1 << 20  # 1 MiB frames (the hardware handled 2 MiB in tests)
MAX_PAYLOAD = MAX_FRAME - _OVERHEAD


class TransportError(Exception):
    """A transport command failed."""


class TransportTimeout(TransportError):
    """The command was not accepted (out of turn / peer not ready) or did not finish in time."""


class TransportStall(TransportError):
    """The firmware stalled the pipe (an idle RX expired); reset and retry."""


class Transport(Protocol):
    def send(self, data: bytes, timeout: float) -> None: ...
    def recv(self, size: int, timeout: float) -> bytes: ...
    def reset(self) -> None: ...


def wire_len(payload_len: int) -> int:
    """Wire length of a frame carrying this many payload bytes: header + payload rounded up to whole slots."""
    return max(SLOT, -(-(_OVERHEAD + payload_len) // SLOT) * SLOT)


@dataclass
class Frame:
    kind: int = KIND_POLL
    seq: int = 0
    ack: int = 0
    next_len: int = SLOT  # wire length of the sender's next frame
    channel: int = 0
    msg_id: int = 0
    offset: int = 0
    total: int = 0
    payload: bytes = b""

    def encode(self, length: int | None = None) -> bytes:
        need = wire_len(len(self.payload))
        length = need if length is None else length
        if length < need or length % SLOT:
            raise ValueError(f"frame needs {need} bytes, wire length is {length}")
        hdr = _HDR.pack(MAGIC, VERSION, self.kind, 0, self.seq, self.ack, self.next_len, self.channel,
                        self.msg_id, self.offset, self.total, len(self.payload))
        crc = zlib.crc32(self.payload, zlib.crc32(hdr))
        return (hdr + struct.pack("<I", crc) + self.payload).ljust(length, b"\0")

    @staticmethod
    def decode(data: bytes) -> "Frame":
        if len(data) < _OVERHEAD:
            raise ValueError("short frame")
        magic, ver, kind, _flags, seq, ack, next_len, channel, msg_id, offset, total, plen = _HDR.unpack_from(data)
        if magic != MAGIC or ver != VERSION:
            raise ValueError("not an OpenATEN frame")
        (crc,) = struct.unpack_from("<I", data, _HDR.size)
        payload = bytes(data[_OVERHEAD : _OVERHEAD + plen])
        if len(payload) != plen or zlib.crc32(payload, zlib.crc32(data[: _HDR.size])) != crc:
            raise ValueError("bad checksum")
        return Frame(kind, seq, ack, next_len, channel, msg_id, offset, total, payload)


@dataclass
class _Chunk:
    channel: int
    msg_id: int
    offset: int
    total: int
    payload: bytes


class LinkSession:
    """One end of a link. ``start()``, then ``send_message()``; the peer's messages arrive via ``on_message``.

    The master polls (every ``poll_idle`` seconds when quiet, back-to-back while either side has data); the slave answers every
    frame. Either side resynchronises itself after crashes or unanswered frames: a master whose send is refused takes the receive it
    owes, a slave whose receive is refused sends the reply it owes.
    """

    def __init__(
        self,
        transport: Transport,
        master: bool,
        on_message: Callable[[int, int, bytes], None] | None = None,
        poll_idle: float = 0.25,
        io_timeout: float = 8.0,
        send_timeout: float = 1.0,
        on_error: Callable[[Exception], None] | None = None,
    ):
        self.t, self.master = transport, master
        self.on_message = on_message or (lambda channel, msg_id, data: None)
        self.on_error = on_error or (lambda e: None)
        self.poll_idle, self.io_timeout, self.send_timeout = poll_idle, io_timeout, send_timeout
        self._chunks: deque[_Chunk] = deque()
        self._msg_id = 0
        self._id_lock = threading.Lock()
        self._seq = 0
        self._ack = 0
        self._rx_len = SLOT  # wire length of the peer's next frame (what we arm)
        self._tx_len = SLOT  # wire length our next frame must have (announced to us earlier)
        self._asm: dict[tuple[int, int], bytearray] = {}
        self._peer_busy = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.stats = dict(frames_tx=0, frames_rx=0, resyncs=0, bytes_tx=0, bytes_rx=0, bad_frames=0)

    # ---- public API ------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="openaten-link", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> bool:
        """Ask the loop to end and wait for it. Returns False if the thread is still blocked in the transport."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def idle(self) -> bool:
        return not self._chunks and not self._peer_busy

    def send_message(self, channel: int, data: bytes) -> int:
        """Queue a message (any size). Returns its message id."""
        with self._id_lock:
            self._msg_id += 1
            mid = self._msg_id
            data = bytes(data)
            total = len(data)
            offsets = range(0, max(total, 1), MAX_PAYLOAD)
            for off in offsets:
                self._chunks.append(_Chunk(channel, mid, off, total, data[off : off + MAX_PAYLOAD]))
        return mid

    # ---- frame construction -----------------------------------------------------------
    def _build(self) -> tuple[Frame, int, Optional[_Chunk]]:
        """Frame to send now (exactly ``_tx_len`` bytes on the wire) and the chunk it carries, if any."""
        chunk = self._chunks[0] if self._chunks else None
        carried = None
        if chunk is not None and wire_len(len(chunk.payload)) <= self._tx_len:
            carried = self._chunks.popleft()
            frame = Frame(KIND_DATA, self._seq, self._ack, SLOT, carried.channel, carried.msg_id, carried.offset,
                          carried.total, carried.payload)
        else:
            frame = Frame(KIND_POLL, self._seq, self._ack)
        nxt = self._chunks[0] if self._chunks else None
        frame.next_len = wire_len(len(nxt.payload)) if nxt is not None else SLOT
        return frame, self._tx_len, carried

    def _sent(self, frame: Frame) -> None:
        self._tx_len = frame.next_len
        self._seq += 1
        self.stats["frames_tx"] += 1
        self.stats["bytes_tx"] += len(frame.payload)

    def _give_back(self, carried: Optional[_Chunk]) -> None:
        if carried is not None:
            self._chunks.appendleft(carried)

    def _accept(self, data: bytes) -> Optional[Frame]:
        try:
            f = Frame.decode(data)
        except ValueError:
            self.stats["bad_frames"] += 1
            self._rx_len = self._tx_len = SLOT
            return None
        self.stats["frames_rx"] += 1
        self._rx_len = f.next_len
        self._ack = f.seq + 1
        self._peer_busy = f.kind == KIND_DATA or f.next_len != SLOT
        if f.kind == KIND_DATA:
            self._deliver(f)
        return f

    def _deliver(self, f: Frame) -> None:
        key = (f.channel, f.msg_id)
        buf = self._asm.setdefault(key, bytearray())
        if f.offset != len(buf):
            self._asm.pop(key, None)  # out of order: drop this message
            return
        buf += f.payload
        self.stats["bytes_rx"] += len(f.payload)
        if len(buf) >= f.total:
            del self._asm[key]
            self.on_message(f.channel, f.msg_id, bytes(buf))

    # ---- loops -------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._master_step() if self.master else self._slave_step()
        except Exception as e:  # reported to the owner; the loop ends
            self.on_error(e)

    def _resync(self) -> None:
        """Forget announced lengths and clear the pipe; the peer does the same when its own transfers fail."""
        self.stats["resyncs"] += 1
        self._rx_len = self._tx_len = SLOT
        self.t.reset()

    def _idle_stall(self) -> None:
        """An armed RX expired inside the firmware: nothing is coming, so assume the peer restarted (default lengths)."""
        self._rx_len = self._tx_len = SLOT
        self.t.reset()

    def _recv_frame(self) -> Optional[Frame]:
        try:
            return self._accept(self.t.recv(self._rx_len, timeout=self.io_timeout))
        except TransportStall:
            self._idle_stall()
        except TransportError:  # refused, overflow (peer sent more than we armed), I/O error
            self._resync()
        return None

    def _master_step(self) -> None:
        frame, length, carried = self._build()
        try:
            self.t.send(frame.encode(length), timeout=self.send_timeout)  # refused quickly when out of turn
        except TransportError:
            self._give_back(carried)
            self._resync()
            self._recv_frame()  # we probably owe a receive: take the peer's pending reply
            return
        self._sent(frame)
        if self._recv_frame() is None:
            time.sleep(0.05)
        elif self.idle():
            self._stop.wait(self.poll_idle)

    def _slave_step(self) -> None:
        try:
            data = self.t.recv(self._rx_len, timeout=self.io_timeout)
        except TransportStall:
            self._idle_stall()
            return
        except TransportTimeout:
            # Receive refused: we owe the peer a reply. Send one so the exchange completes.
            self._resync()
            self._reply()
            return
        except TransportError:  # overflow / I/O error: the peer's frame did not match what we armed
            self._resync()
            return
        if self._accept(data) is not None:
            self._reply()
        else:
            self.t.reset()

    def _reply(self) -> None:
        frame, length, carried = self._build()
        try:
            self.t.send(frame.encode(length), timeout=self.io_timeout)
        except TransportError:
            self._give_back(carried)
            self._resync()
            return
        self._sent(frame)


class UsbTransport:
    """Adapts :class:`openaten.usb.BridgeEnd` to the :class:`Transport` protocol (timeouts in seconds)."""

    def __init__(self, end):
        import usb.core  # imported here so link.py works without pyusb (tests use a fake transport)

        self.end, self._usb = end, usb.core

    def send(self, data: bytes, timeout: float) -> None:
        try:
            self.end.send(data, timeout=int(timeout * 1000))
        except self._usb.USBError as e:
            raise self._map(e) from e

    def recv(self, size: int, timeout: float) -> bytes:
        try:
            return self.end.recv(size, timeout=int(timeout * 1000))
        except self._usb.USBError as e:
            raise self._map(e) from e

    def reset(self) -> None:
        self.end.reset()

    @staticmethod
    def _map(e: Exception) -> TransportError:
        text = str(e).lower()
        if "timed out" in text:
            return TransportTimeout(str(e))
        if "pipe" in text:
            return TransportStall(str(e))
        return TransportError(str(e))
