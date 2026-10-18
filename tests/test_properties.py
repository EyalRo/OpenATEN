"""Property-based tests: wire formats must round-trip and reject corruption, and the hand-off machine must never wedge."""
import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from openaten import control as c
from openaten import link as lk

u32 = st.integers(0, 2**32 - 1)
frames = st.builds(
    lk.Frame,
    kind=st.sampled_from([lk.KIND_POLL, lk.KIND_DATA]),
    flags=st.integers(0, 255),
    seq=u32,
    ack=u32,
    next_len=st.integers(1, 2**20).map(lk.wire_len),
    channel=u32,
    msg_id=u32,
    offset=u32,
    total=u32,
    payload=st.binary(max_size=8192),
)


@given(frames)
def test_frame_roundtrip(f):
    wire = f.encode()
    assert len(wire) % lk.SLOT == 0
    assert lk.Frame.decode(wire) == f


@given(frames, st.data())
def test_any_single_corrupted_byte_in_the_frame_is_rejected(f, data):
    wire = bytearray(f.encode())
    used = lk._OVERHEAD + len(f.payload)  # padding after the payload is not covered by the checksum
    i = data.draw(st.integers(0, used - 1))
    wire[i] ^= data.draw(st.integers(1, 255))
    with pytest.raises(ValueError):
        lk.Frame.decode(bytes(wire))


@given(st.binary(max_size=4096))
def test_decode_never_raises_anything_but_valueerror(blob):
    try:
        lk.Frame.decode(blob)
    except ValueError:
        pass
    except struct.error:
        pytest.fail("short input must be a ValueError, not struct.error")


@given(st.binary(max_size=512))
def test_control_decode_is_total(blob):
    msg = c.decode(blob)
    assert msg is None or isinstance(msg["kind"], str)


kinds = st.sampled_from(["take-control", "release", "give-back", "from-the-future"])
messages = st.fixed_dictionaries({"kind": kinds}, optional={"border": st.text(max_size=8), "row": st.floats(-1e6, 1e6)})
edge_events = st.tuples(st.just("edge"), st.text(max_size=4), st.floats(0, 1e4), st.booleans())
peer_events = st.tuples(st.just("peer"), messages)


@settings(max_examples=200)
@given(st.lists(st.one_of(edge_events, peer_events), max_size=40))
def test_handoff_survives_any_event_sequence_and_recovers_on_release(events):
    t = [0.0]
    h = c.Handoff(lambda b, r: None, lambda: None, lambda d: None, settle=0.5, now=lambda: t[0])
    for ev in events:
        t[0] += 0.3
        if ev[0] == "edge":
            h.edge(ev[1], ev[2], ev[3])
        else:
            h.message(ev[1])
    h.message({"kind": "release"})
    assert not h.receiving
