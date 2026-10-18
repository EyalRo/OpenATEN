"""Clipboard sharing over the link (opt-in: ``openaten daemon --clipboard``).

:class:`ClipboardSync` watches the local clipboard through a backend and sends changes to the other computer; clips that arrive from the
other computer are written to the local clipboard. Only ``text/plain`` and ``image/png`` are shared. What was on the clipboard when the
daemon started is never sent (only later changes are), and a clip written from the peer is not echoed back.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from . import appmsg

log = logging.getLogger(__name__)

TEXT, PNG = "text/plain", "image/png"
MIMES = (TEXT, PNG)
MAX_BYTES = 8 << 20


@dataclass(frozen=True)
class Clip:
    mime: str
    data: bytes

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.mime.encode() + b"\0" + self.data).hexdigest()


class Backend(Protocol):
    def read(self) -> Clip | None: ...
    def write(self, clip: Clip) -> None: ...


def _pick(types: list[str]) -> str | None:
    """The shared MIME type to read, given what the clipboard offers: text wins over an image."""
    lowered = [t.lower() for t in types]
    if any(t.startswith("text/plain") or t in ("utf8_string", "string") for t in lowered):
        return TEXT
    if PNG in lowered:
        return PNG
    return None


class CommandBackend:
    """Clipboard through command-line tools: ``wl-clipboard`` on Wayland, ``xclip`` on X11."""

    def __init__(self, wayland: bool):
        self.wayland = wayland

    def _run(self, argv: list[str]) -> bytes | None:
        try:
            done = subprocess.run(argv, capture_output=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.debug("%s failed: %s", argv[0], e)
            return None
        return done.stdout if done.returncode == 0 else None

    def _types(self) -> list[str]:
        argv = ["wl-paste", "--list-types"] if self.wayland else ["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"]
        out = self._run(argv)
        return out.decode(errors="replace").split() if out else []

    def read(self) -> Clip | None:
        mime = _pick(self._types())
        if mime is None:
            return None
        if self.wayland:
            argv = ["wl-paste", "--no-newline", "--type", mime]
        else:
            argv = ["xclip", "-selection", "clipboard", "-o", "-t", "UTF8_STRING" if mime == TEXT else mime]
        data = self._run(argv)
        return Clip(mime, data) if data else None

    def write(self, clip: Clip) -> None:
        # Both tools stay alive to serve the selection (xclip) or fork to the background (wl-copy), so do not wait for them.
        argv = ["wl-copy", "--type", clip.mime] if self.wayland else ["xclip", "-selection", "clipboard", "-i", "-t", "UTF8_STRING" if clip.mime == TEXT else clip.mime]
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert proc.stdin is not None
        try:
            proc.stdin.write(clip.data)
        finally:
            proc.stdin.close()


def detect() -> Backend | None:
    """The backend for this session, or None (with a WARNING) when no supported clipboard tool is available."""
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste") and shutil.which("wl-copy"):
        return CommandBackend(wayland=True)
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        return CommandBackend(wayland=False)
    log.warning("clipboard sharing needs wl-clipboard (Wayland) or xclip (X11) in a graphical session; it is disabled")
    return None


class ClipboardSync:
    """``send(bytes) -> bool`` queues a link message (False if the link is down: the clip is retried); call :meth:`poll` regularly."""

    def __init__(self, backend: Backend, send: Callable[[bytes], bool], max_bytes: int = MAX_BYTES, interval: float = 0.5, now: Callable[[], float] = time.monotonic):
        self.backend, self.send, self.max_bytes, self.interval, self.now = backend, send, max_bytes, interval, now
        self._next = 0.0
        self._last: str | None = None  # digest of the clip already handled (sent, received or the start-up baseline)
        self._primed = False
        self._warned: str | None = None

    def poll(self) -> None:
        t = self.now()
        if t < self._next:
            return
        self._next = t + self.interval
        clip = self.backend.read()
        if not self._primed:  # whatever is on the clipboard at start-up stays private
            self._primed = True
            self._last = clip.digest if clip else None
            return
        if clip is None or clip.digest == self._last:
            return
        if len(clip.data) > self.max_bytes:
            if self._warned != clip.digest:
                self._warned = clip.digest
                log.warning("clipboard content is %d bytes (limit %d): not shared", len(clip.data), self.max_bytes)
            self._last = clip.digest
            return
        if self.send(appmsg.pack("clip", clip.data, mime=clip.mime, sha=clip.digest)):
            self._last = clip.digest
            log.debug("clipboard sent (%s, %d bytes)", clip.mime, len(clip.data))

    def on_message(self, header: dict, payload: bytes) -> None:
        if header.get("kind") != "clip":
            return
        mime = header.get("mime")
        if mime not in MIMES or len(payload) > self.max_bytes or not payload:
            log.debug("ignoring clipboard message (mime %r, %d bytes)", mime, len(payload))
            return
        clip = Clip(mime, payload)
        if header.get("sha") != clip.digest:
            log.warning("clipboard message failed its checksum; ignored")
            return
        self._last = clip.digest  # so the next poll does not send it straight back
        try:
            self.backend.write(clip)
        except OSError as e:
            log.warning("could not write the clipboard: %s", e)
            return
        log.debug("clipboard received (%s, %d bytes)", clip.mime, len(payload))
