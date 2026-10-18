"""Framing shared by the application protocols (clipboard, files) that ride on link channels.

One link message = 4-byte big-endian header length + UTF-8 JSON header + raw payload. The header always carries ``kind`` and ``v`` (the
protocol version; messages from a newer version are rejected, like the hand-off messages in :mod:`openaten.control`).
"""
from __future__ import annotations

import json
import struct
from typing import Any

VERSION = 1
CH_CLIPBOARD = 2
CH_FILES = 3
MAX_HEADER = 64 * 1024


def pack(kind: str, payload: bytes = b"", **fields: Any) -> bytes:
    header = json.dumps({"v": VERSION, "kind": kind, **fields}, separators=(",", ":")).encode()
    return struct.pack(">I", len(header)) + header + payload


def unpack(data: bytes) -> tuple[dict, bytes] | None:
    """(header, payload), or None if the message is malformed, has no string ``kind`` or comes from a newer protocol version."""
    if len(data) < 4:
        return None
    (n,) = struct.unpack_from(">I", data)
    if n > MAX_HEADER or len(data) < 4 + n:
        return None
    try:
        header = json.loads(data[4 : 4 + n])
    except ValueError:  # includes bad UTF-8
        return None
    if not isinstance(header, dict) or not isinstance(header.get("kind"), str):
        return None
    version = header.get("v", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version > VERSION:
        return None
    return header, data[4 + n :]
