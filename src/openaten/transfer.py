"""File transfer over the link (opt-in on the receiving side: ``openaten daemon --receive-dir DIR``; send with ``openaten send FILE``).

Protocol (messages on :data:`appmsg.CH_FILES`, framing in :mod:`openaten.appmsg`)::

    sender   -> receiver   offer   {id, name, size}
    receiver -> sender     accept  {id}                 or   reject {id, reason}
    sender   -> receiver   chunk   {id, n} + data       (at most ``window`` chunks unacknowledged)
    receiver -> sender     ack     {id, n}              (after the chunk is written)
    sender   -> receiver   done    {id, sha256, size}   (after the last chunk)
    receiver -> sender     result  {id, ok, error}
    either                 cancel  {id}

The link already delivers messages reliably and in order; the per-transfer SHA-256 and the ack window exist for end-to-end integrity and so
that a large file never sits in memory (the sender holds at most ``window * chunk`` bytes in flight). The receiver never overwrites: the file
is written to a private ``.openaten-<id>.part`` and linked to ``name`` (or ``name (1)`` ...) only after the checksum matches.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import appmsg

log = logging.getLogger(__name__)

CHUNK = 4 << 20
WINDOW = 4
IDLE_TIMEOUT = 60.0
MAX_SIZE = 16 << 30
MAX_INCOMING = 4
MAX_CHUNK = 16 << 20  # the largest chunk a receiver accepts, whatever the sender's chunk size
KEEP_FINISHED = 10
DISK_RESERVE = 1 << 20

ID_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")  # ids become part of a file name: nothing the peer sends may contain a path separator

ACTIVE = ("offered", "sending", "finishing", "receiving")


def safe_name(name: Any) -> str | None:
    """A plain file name made from the peer's suggestion: no directories, no control characters, no leading dots; None if nothing is left."""
    if not isinstance(name, str):
        return None
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable()).strip().lstrip(".").strip()
    name = name.encode()[:200].decode(errors="ignore")
    return name or None


@dataclass
class _Out:
    id: str
    path: Path
    name: str
    size: int
    fh: Any
    state: str = "offered"
    error: str | None = None
    hasher: Any = field(default_factory=hashlib.sha256)
    next_n: int = 0
    acked_n: int = 0
    sent: int = 0
    acked_bytes: int = 0
    sizes: dict[int, int] = field(default_factory=dict)
    last: float = 0.0
    all_sent: bool = False


@dataclass
class _In:
    id: str
    name: str
    size: int
    part: Path
    fh: Any
    state: str = "receiving"
    error: str | None = None
    hasher: Any = field(default_factory=hashlib.sha256)
    next_n: int = 0
    received: int = 0
    last: float = 0.0
    final: Path | None = None


class TransferManager:
    """Both directions. ``send(bytes) -> bool`` queues a link message (False: link down). Call :meth:`pump` regularly and feed
    :meth:`on_message` the files channel; everything runs on one thread."""

    def __init__(
        self,
        send: Callable[[bytes], bool],
        receive_dir: Path | None = None,
        max_size: int = MAX_SIZE,
        chunk: int = CHUNK,
        window: int = WINDOW,
        idle_timeout: float = IDLE_TIMEOUT,
        now: Callable[[], float] = time.monotonic,
    ):
        self.send, self.receive_dir, self.max_size = send, receive_dir, max_size
        self.chunk, self.window, self.idle_timeout, self.now = chunk, window, idle_timeout, now
        self._out: dict[str, _Out] = {}
        self._in: dict[str, _In] = {}
        self._finished: list[dict] = []

    # -- sending ----------------------------------------------------------------------
    def send_file(self, path: str | os.PathLike) -> str:
        """Offer a file to the peer; returns the transfer id. Raises ValueError if it cannot be offered."""
        p = Path(path)
        try:
            if not p.is_file():
                raise ValueError(f"not a regular file: {p}")
            size = p.stat().st_size
            fh = open(p, "rb")
        except OSError as e:
            raise ValueError(f"cannot read {p}: {e.strerror or e}") from e
        t = _Out(secrets.token_hex(8), p, safe_name(p.name) or "file", size, fh, last=self.now())
        if not self.send(appmsg.pack("offer", id=t.id, name=t.name, size=size)):
            fh.close()
            raise ValueError("the link to the other computer is down")
        self._out[t.id] = t
        log.info("offered %s (%d bytes) to the peer", t.name, size)
        return t.id

    def cancel(self, tid: str) -> bool:
        if tid in self._out and self._out[tid].state in ACTIVE:
            self.send(appmsg.pack("cancel", id=tid))
            self._finish_out(self._out[tid], "cancelled", "cancelled")
            return True
        if tid in self._in and self._in[tid].state in ACTIVE:
            self.send(appmsg.pack("cancel", id=tid))
            self._finish_in(self._in[tid], "cancelled", "cancelled")
            return True
        return False

    def pump(self) -> None:
        """Send chunks within the window and fail transfers that have gone quiet."""
        t = self.now()
        for out in list(self._out.values()):
            if out.state == "sending":
                self._pump_out(out)
            if out.state in ACTIVE and t - out.last > self.idle_timeout:
                self.send(appmsg.pack("cancel", id=out.id))
                self._finish_out(out, "failed", "timed out waiting for the peer")
        for inc in list(self._in.values()):
            if inc.state in ACTIVE and t - inc.last > self.idle_timeout:
                self.send(appmsg.pack("cancel", id=inc.id))
                self._finish_in(inc, "failed", "timed out waiting for the sender")

    def _pump_out(self, out: _Out) -> None:
        eof = False
        while out.state == "sending" and not out.all_sent and out.next_n - out.acked_n < self.window:
            try:
                data = out.fh.read(self.chunk)
            except OSError as e:
                self.send(appmsg.pack("cancel", id=out.id))
                self._finish_out(out, "failed", f"read error: {e.strerror or e}")
                return
            if not data:
                eof = True
                break
            if not self.send(appmsg.pack("chunk", data, id=out.id, n=out.next_n)):
                out.fh.seek(-len(data), os.SEEK_CUR)  # link down: try this chunk again later
                return
            out.hasher.update(data)
            out.sizes[out.next_n] = len(data)
            out.next_n += 1
            out.sent += len(data)
            out.last = self.now()
        if not eof or out.state != "sending" or out.all_sent:
            return
        if out.sent != out.size:
            self.send(appmsg.pack("cancel", id=out.id))
            self._finish_out(out, "failed", "the file changed while it was being sent")
        elif self.send(appmsg.pack("done", id=out.id, sha256=out.hasher.hexdigest(), size=out.size)):
            out.all_sent, out.state, out.last = True, "finishing", self.now()

    def _finish_out(self, out: _Out, state: str, error: str | None) -> None:
        out.state, out.error = state, error if state != "done" else None
        try:
            out.fh.close()
        except OSError:
            pass
        self._record(out.id, out.name, "send", out.size, out.size if state == "done" else out.acked_bytes, state, out.error)
        del self._out[out.id]
        (log.info if state == "done" else log.warning)("send of %s: %s%s", out.name, state, f" ({error})" if error and state != "done" else "")

    # -- receiving --------------------------------------------------------------------
    def _reject(self, tid: Any, reason: str) -> None:
        log.warning("rejected incoming file: %s", reason)
        self.send(appmsg.pack("reject", id=tid, reason=reason))

    def _offer(self, h: dict) -> None:
        tid, size, name = h.get("id"), h.get("size"), safe_name(h.get("name"))
        if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
            return
        if self.receive_dir is None:
            return self._reject(tid, "receiving is not enabled on the other computer")
        if tid in self._in:
            return
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or name is None:
            return self._reject(tid, "malformed offer")
        if size > self.max_size:
            return self._reject(tid, f"file is larger than the receiver's limit ({self.max_size} bytes)")
        if sum(1 for i in self._in.values() if i.state in ACTIVE) >= MAX_INCOMING:
            return self._reject(tid, "too many transfers in progress")
        try:
            self.receive_dir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.receive_dir).free < size + DISK_RESERVE:
                return self._reject(tid, "not enough disk space")
            part = self.receive_dir / f".openaten-{tid}.part"
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except (OSError, ValueError) as e:
            return self._reject(tid, f"cannot write to the receive directory: {getattr(e, 'strerror', None) or e}")
        self._in[tid] = _In(tid, name, size, part, os.fdopen(fd, "wb"), last=self.now())
        self.send(appmsg.pack("accept", id=tid))
        log.info("receiving %s (%d bytes)", name, size)

    def _chunk(self, h: dict, payload: bytes) -> None:
        inc = self._in.get(h.get("id"))  # type: ignore[arg-type]
        if inc is None or inc.state != "receiving":
            return
        if h.get("n") != inc.next_n or inc.received + len(payload) > inc.size or len(payload) > MAX_CHUNK:
            self.send(appmsg.pack("result", id=inc.id, ok=False, error="chunk out of order or too large"))
            return self._finish_in(inc, "failed", "chunk out of order or too large")
        try:
            inc.fh.write(payload)
        except OSError as e:
            self.send(appmsg.pack("result", id=inc.id, ok=False, error="write failed"))
            return self._finish_in(inc, "failed", f"write failed: {e.strerror or e}")
        inc.hasher.update(payload)
        inc.received += len(payload)
        inc.last = self.now()
        self.send(appmsg.pack("ack", id=inc.id, n=inc.next_n))
        inc.next_n += 1

    def _done(self, h: dict) -> None:
        inc = self._in.get(h.get("id"))  # type: ignore[arg-type]
        if inc is None or inc.state != "receiving":
            return
        if inc.received != inc.size or h.get("size") != inc.size or h.get("sha256") != inc.hasher.hexdigest():
            self.send(appmsg.pack("result", id=inc.id, ok=False, error="checksum or size mismatch"))
            return self._finish_in(inc, "failed", "checksum or size mismatch")
        try:
            inc.fh.close()
            final = self._publish(inc)
        except OSError as e:
            self.send(appmsg.pack("result", id=inc.id, ok=False, error="could not save the file"))
            return self._finish_in(inc, "failed", f"could not save the file: {e.strerror or e}")
        inc.final = final
        self.send(appmsg.pack("result", id=inc.id, ok=True))
        self._finish_in(inc, "done", None)

    def _publish(self, inc: _In) -> Path:
        assert self.receive_dir is not None
        stem, suffix = os.path.splitext(inc.name)
        for i in range(1000):
            cand = self.receive_dir / (inc.name if i == 0 else f"{stem} ({i}){suffix}")
            try:
                os.link(inc.part, cand)  # atomic and never overwrites
            except FileExistsError:
                continue
            except OSError:  # no hard links on this filesystem
                if cand.exists():
                    continue
                os.rename(inc.part, cand)
                break
            os.unlink(inc.part)
            break
        else:
            raise OSError("too many files with this name")
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(cand, 0o666 & ~umask)
        return cand

    def _finish_in(self, inc: _In, state: str, error: str | None) -> None:
        inc.state, inc.error = state, error
        try:
            inc.fh.close()
        except OSError:
            pass
        if state != "done":
            try:
                os.unlink(inc.part)
            except OSError:
                pass
        self._record(inc.id, inc.name, "receive", inc.size, inc.received if state != "done" else inc.size, state, error)
        del self._in[inc.id]
        if state == "done":
            log.info("received %s (%d bytes) as %s", inc.name, inc.size, inc.final)
        else:
            log.warning("receive of %s: %s (%s)", inc.name, state, error)

    # -- dispatch ---------------------------------------------------------------------
    def on_message(self, header: dict, payload: bytes) -> None:
        kind, tid = header.get("kind"), header.get("id")
        if not isinstance(tid, str):
            return
        if kind == "offer":
            return self._offer(header)
        if kind == "chunk":
            return self._chunk(header, payload)
        if kind == "done":
            return self._done(header)
        out = self._out.get(tid)  # type: ignore[arg-type]
        if kind == "cancel":
            if out is not None:
                self._finish_out(out, "cancelled", "cancelled by the peer")
            elif tid in self._in:
                self._finish_in(self._in[tid], "cancelled", "cancelled by the sender")  # type: ignore[index]
            return
        if out is None:
            return
        out.last = self.now()
        if kind == "accept" and out.state == "offered":
            out.state = "sending"
            self._pump_out(out)
        elif kind == "reject":
            self._finish_out(out, "rejected", str(header.get("reason", "rejected")))
        elif kind == "ack" and isinstance(header.get("n"), int) and out.acked_n <= header["n"] < out.next_n:
            for n in range(out.acked_n, header["n"] + 1):
                out.acked_bytes += out.sizes.pop(n, 0)
            out.acked_n = header["n"] + 1
            if out.state == "sending":
                self._pump_out(out)
        elif kind == "result" and out.state == "finishing":
            if header.get("ok") is True:
                self._finish_out(out, "done", None)
            else:
                self._finish_out(out, "failed", str(header.get("error", "the receiver reported an error")))

    def link_lost(self) -> None:
        """The link went down: nothing in flight can be resumed."""
        for out in list(self._out.values()):
            self._finish_out(out, "failed", "the link went down")
        for inc in list(self._in.values()):
            self._finish_in(inc, "failed", "the link went down")

    def close(self) -> None:
        self.link_lost()

    # -- status -----------------------------------------------------------------------
    def _record(self, tid: str, name: str, direction: str, size: int, done: int, state: str, error: str | None) -> None:
        self._finished.append({"id": tid, "name": name, "direction": direction, "size": size, "done": done, "state": state, "error": error})
        del self._finished[:-KEEP_FINISHED]

    def status(self) -> list[dict]:
        active = [
            {"id": o.id, "name": o.name, "direction": "send", "size": o.size, "done": o.acked_bytes, "state": o.state, "error": None} for o in self._out.values()
        ] + [{"id": i.id, "name": i.name, "direction": "receive", "size": i.size, "done": i.received, "state": i.state, "error": None} for i in self._in.values()]
        return active + list(self._finished)
