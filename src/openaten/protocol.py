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


def mouse_report(buttons: int = 0, dx: int = 0, dy: int = 0, wheel: int = 0, pan: int = 0) -> bytes:
    """5-byte boot-style mouse report as accepted by CMD_HID."""
    return struct.pack("<Bbbbb", buttons, dx, dy, wheel, pan)


def key_report(mods: int = 0, *keys: int) -> bytes:
    """8-byte boot keyboard report as accepted by CMD_HID (up to 6 simultaneous keys)."""
    return bytes([mods, 0, *keys[:6]]).ljust(8, b"\0")
