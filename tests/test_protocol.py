import struct

import pytest

from openaten import protocol as pr


def test_cdb_is_16_bytes_and_sums_to_zero():
    for c in (pr.cdb_state(), pr.cdb_switch_ufp(2), pr.cdb_tx(), pr.cdb_rx(), pr.cdb_hid(5), pr.cdb(pr.CMD_INIT)):
        assert len(c) == 16 and c[0] == 0xF2 and sum(c) & 0xFF == 0


def test_known_state_cdb():
    # GetState, 1 byte in: checksum = -(0xF2 + 0x04 + 0x01) & 0xFF = 0x09 (matches the bytes seen on real hardware)
    assert pr.cdb_state().hex(" ") == "f2 09 04 00 00 00 00 01 00 00 00 00 00 00 00 00"


def test_tx_length_is_big_endian():
    assert pr.cdb_tx(2048)[4:8] == bytes([0, 0, 8, 0])


def test_packet_roundtrip():
    p = pr.packet(pr.T_DATA, b"hello", stream=7)
    assert len(p) == pr.PKT_LEN
    d = pr.parse(p)
    assert (d["type"], d["stream"], d["length"], d["flags"]) == (pr.T_DATA, 7, 2048, 1)
    assert d["payload"].startswith(b"hello")


def test_payload_too_large():
    with pytest.raises(ValueError):
        pr.packet(pr.T_DATA, bytes(pr.PKT_LEN))


def test_connect_fields():
    d = pr.parse(pr.connect(layout=1, conn_type=2))
    pl = d["payload"]
    assert d["type"] == pr.T_CONNECT
    assert struct.unpack_from("<I", pl, 0)[0] == 0x10000  # +0x10 max transfer
    assert struct.unpack_from("<H", pl, 4)[0] == 0x300  # +0x14
    assert struct.unpack_from("<H", pl, 6)[0] == 0x0102  # +0x16
    assert (pl[9], pl[10]) == (1, 2)  # +0x19 layout, +0x1A connection type


def test_mouse_cross_layout():
    pl = pr.parse(pr.mouse_cross(640, 360, 2))["payload"]
    assert struct.unpack_from("<HHB", pl, 0) == (640, 360, 2)


def test_hid_reports():
    assert pr.mouse_report(0, 5, 0, 0, 0) == bytes([0, 5, 0, 0, 0])
    assert pr.key_report(0, 0x04) == bytes([0, 0, 4, 0, 0, 0, 0, 0])
    assert len(pr.key_report(2, 1, 2, 3, 4, 5, 6, 7)) == 8
