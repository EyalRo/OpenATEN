import struct

from hypothesis import given
from hypothesis import strategies as st

from openaten import appmsg as m


def raw(header: bytes) -> bytes:
    return struct.pack(">I", len(header)) + header


def test_roundtrip_with_and_without_payload():
    assert m.unpack(m.pack("clip", b"\x00\xffdata", mime="text/plain", len=6)) == ({"v": 1, "kind": "clip", "mime": "text/plain", "len": 6}, b"\x00\xffdata")
    assert m.unpack(m.pack("ack", n=3)) == ({"v": 1, "kind": "ack", "n": 3}, b"")


def test_rejects_malformed_oversized_and_newer_messages():
    assert m.unpack(b"") is None
    assert m.unpack(b"\x00\x00") is None
    assert m.unpack(b"\x00\x00\x00\x09{") is None  # truncated header
    assert m.unpack(b"\xff\xff\xff\xff" + b"x" * 10) is None  # absurd header length
    assert m.unpack(b"\x00\x00\x00\x02[]") is None  # not an object
    assert m.unpack(b"\x00\x00\x00\x0a{\"kind\":1}") is None  # kind must be a string
    assert m.unpack(b"\x00\x00\x00\x02\xff\xfe") is None  # not UTF-8
    assert m.unpack(raw(b'{"v":2,"kind":"clip"}')) is None
    assert m.unpack(raw(b'{"kind":"clip"}')) is not None  # unversioned counts as 1


@given(st.binary(max_size=2048))
def test_unpack_is_total(blob):
    assert m.unpack(blob) is None or isinstance(m.unpack(blob)[0]["kind"], str)


@given(st.text(min_size=1, max_size=20), st.binary(max_size=2048))
def test_pack_unpack_roundtrip_property(kind, payload):
    got = m.unpack(m.pack(kind, payload))
    assert got is not None and got[0]["kind"] == kind and got[1] == payload
