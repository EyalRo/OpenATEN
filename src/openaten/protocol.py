"""Wire format of the Genesys Logic "Host Bridge" found in ATEN US3342-class switches.

Everything here was derived by observing the hardware and by interoperability research; see docs/protocol.md.
No packet layout in this module has been validated against a third-party implementation, only against the
hardware itself (host-to-host loopback).
"""
from __future__ import annotations

import struct

PKT_LEN = 2048  # control packets are one 2 KiB "sector"

# Packet types (byte 0x0C of the packet header)
T_CLIPBOARD = 0x41
T_PASTE_FILES = 0x42
T_ABORT_FILES = 0x43
T_MOUSE_CROSS = 0x50
T_MOUSE_CROSS_RSP = 0x51
T_CHG_ORIEN = 0x53
T_DATA = 0x88
T_RESPONSE = 0x85
T_CONNECT = 0xC1
T_DISCONNECT = 0xC2
T_ALIVE = 0xC3

# Vendor SCSI commands (CDB byte 2); CDB byte 0 is always 0xF2
CMD_TX = 0x00  # data-out: send one packet to the peer
CMD_RX = 0x01  # data-in: receive one packet from the peer
CMD_HID = 0x02  # data-out: inject a HID report on the peer
CMD_STATE = 0x04  # data-in (1 byte): link state
CMD_INIT = 0x06  # init data transfer
CMD_UFP = 0x07  # switch mode (CDB byte 3 = mode); re-enumerates both ends


def cdb(cmd: int, b3: int = 0, b4_7: tuple[int, int, int, int] | bytes = (0, 0, 0, 0), tail: bytes = b"") -> bytes:
    """Build a 16-byte vendor CDB.

    byte 1 is a checksum: the negated sum of all other bytes, so the whole CDB sums to 0 mod 256.
    """
    c = bytearray(16)
    c[0], c[2], c[3] = 0xF2, cmd, b3
    c[4:8] = bytes(b4_7)
    c[8 : 8 + len(tail)] = tail
    c[1] = (-sum(c)) & 0xFF
    return bytes(c)


def cdb_tx(length: int = PKT_LEN) -> bytes:
    return cdb(CMD_TX, b4_7=struct.pack(">I", length))


def cdb_rx(length: int = PKT_LEN) -> bytes:
    return cdb(CMD_RX, b4_7=struct.pack(">I", length))


def cdb_hid(report_len: int) -> bytes:
    return cdb(CMD_HID, b4_7=(0, 0, 0, report_len))


def cdb_state(length: int = 1) -> bytes:
    return cdb(CMD_STATE, b4_7=(0, 0, 0, length))


def cdb_switch_ufp(mode: int) -> bytes:
    return cdb(CMD_UFP, b3=mode)


def packet(ptype: int, payload: bytes = b"", stream: int = 0, resp: int = 0, flags: int = 1) -> bytes:
    """Build a 2 KiB packet: header (+0x00 length, +0x04 rx length, +0x08 stream, +0x0C type, +0x0D resp, +0x0E flags)."""
    if len(payload) > PKT_LEN - 0x10:
        raise ValueError("payload too large for a control packet")
    p = bytearray(PKT_LEN)
    struct.pack_into("<IIIBBB", p, 0, PKT_LEN, 0, stream, ptype, resp, flags)
    p[0x10 : 0x10 + len(payload)] = payload
    return bytes(p)


def parse(p: bytes) -> dict:
    length, rxlen, stream, ptype, resp, flags = struct.unpack_from("<IIIBBB", p, 0)
    return dict(length=length, rxlen=rxlen, stream=stream, type=ptype, resp=resp, flags=flags, payload=p[0x10:])


def connect(layout: int = 1, conn_type: int = 2, flag: int = 0, max_xfer: int = 0x10000) -> bytes:
    pl = bytearray(0x0B)
    struct.pack_into("<IH", pl, 0, max_xfer, 0x300)  # +0x10 max transfer, +0x14 constant
    struct.pack_into("<H", pl, 6, 0x0102)  # +0x16 protocol tag
    pl[8], pl[9], pl[10] = flag, layout, conn_type  # +0x18 flag, +0x19 layout, +0x1A connection type
    return packet(T_CONNECT, bytes(pl))


def alive() -> bytes:
    return packet(T_ALIVE)


def disconnect() -> bytes:
    return packet(T_DISCONNECT)


def mouse_cross(x: int, y: int, edge: int) -> bytes:
    return packet(T_MOUSE_CROSS, struct.pack("<HHB", x, y, edge))


def mouse_cross_rsp(status: int, flag: int = 1) -> bytes:
    return packet(T_MOUSE_CROSS_RSP, bytes([status, flag]))


def chg_orien(layout: int, flag: int) -> bytes:
    return packet(T_CHG_ORIEN, bytes([layout, flag]))


MOUSE_REPORT_ID, KEYBOARD_REPORT_ID = 1, 2


def mouse_report(buttons: int = 0, dx: int = 0, dy: int = 0, wheel: int = 0, pan: int = 0) -> bytes:
    """5-byte mouse report for CMD_HID: ``[id=1, buttons, wheel, dx, dy]``.

    This is the layout of the bridge's HID report descriptor (report ID 1, five buttons, then wheel, X, Y as signed bytes);
    note the wheel comes *before* X/Y and there is no horizontal scroll, so ``pan`` is accepted but ignored.
    """
    return bytes([MOUSE_REPORT_ID, buttons & 0x1F, wheel & 0xFF, dx & 0xFF, dy & 0xFF])


def _split_axis(total: int) -> list[int]:
    """Signed-byte steps that add up to ``total`` (a single step when it fits)."""
    steps = []
    while total:
        step = max(-127, min(127, total))
        steps.append(step)
        total -= step
    return steps


def coalesce_mouse(reports: list[bytes]) -> list[bytes]:
    """Merge runs of consecutive mouse reports that share a button state into as few reports as carry the same motion.

    Relative motion and wheel are summed (split again when a sum exceeds a signed byte), so a backlog from a fast mouse costs
    fewer bridge commands. Reports of any other kind, and any change of button state, keep their position and are never merged across.

    Motion is also never merged across a reversal of direction on an axis. The other computer's pointer clamps at its screen edges after
    every report, so only same-direction runs end where the originals would (clamping is monotone). Merging a reversal changes the result:
    the edge hand-off slams the pointer far past the top-left corner and then moves it to the entry row, and a merged slam-and-move would
    leave it near the top instead of on the row (and make the dead-reckoned position wrong afterwards).
    """
    out: list[bytes] = []
    run: list[int] | None = None  # [buttons, wheel, dx, dy] of the mouse run being merged

    def flush() -> None:
        nonlocal run
        if run is None:
            return
        buttons, wheel, dx, dy = run
        parts = [_split_axis(wheel), _split_axis(dx), _split_axis(dy)]
        for i in range(max(1, *(len(a) for a in parts))):
            w, x, y = (a[i] if i < len(a) else 0 for a in parts)
            out.append(mouse_report(buttons, x, y, w))
        run = None

    for r in reports:
        if len(r) == 5 and r[0] == MOUSE_REPORT_ID:
            buttons, wheel, dx, dy = r[1], _s8(r[2]), _s8(r[3]), _s8(r[4])
            if run is not None and run[0] == buttons and run[2] * dx >= 0 and run[3] * dy >= 0:  # no reversal on either axis
                run[1] += wheel
                run[2] += dx
                run[3] += dy
            else:
                flush()
                run = [buttons, wheel, dx, dy]
        else:
            flush()
            out.append(r)
    flush()
    return out


def _s8(b: int) -> int:
    return b - 256 if b > 127 else b


def key_report(mods: int = 0, *keys: int) -> bytes:
    """8-byte keyboard report for CMD_HID: ``[id=2, modifiers, key1..key6]`` (no reserved byte)."""
    return bytes([KEYBOARD_REPORT_ID, mods, *keys[:6]]).ljust(8, b"\0")
