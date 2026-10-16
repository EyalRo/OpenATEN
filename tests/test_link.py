import os
import threading
import time

import pytest

from fake_transport import FakeBridge
from openaten import link
from openaten.link import Frame, LinkSession, SLOT, wire_len


def test_frame_roundtrip():
    f = Frame(link.KIND_DATA, 7, 3, 4096, 2, 9, 0, 5, b"hello")
    raw = f.encode()
    assert len(raw) == SLOT
    g = Frame.decode(raw)
    assert (g.kind, g.seq, g.ack, g.next_len, g.channel, g.msg_id, g.total, g.payload) == (1, 7, 3, 4096, 2, 9, 5, b"hello")


def test_frame_detects_corruption():
    raw = bytearray(Frame(link.KIND_DATA, payload=b"hello", total=5).encode())
    raw[link._OVERHEAD + 1] ^= 0xFF
    with pytest.raises(ValueError):
        Frame.decode(bytes(raw))


def test_wire_len_rounds_to_slots():
    assert wire_len(0) == SLOT
    assert wire_len(SLOT - link._OVERHEAD) == SLOT
    assert wire_len(SLOT - link._OVERHEAD + 1) == 2 * SLOT


class Pair:
    def __init__(self, bridge=None, **kw):
        self.bridge = bridge or FakeBridge()
        self.got = {"m": [], "s": []}
        self.errors = []
        common = dict(poll_idle=0.01, io_timeout=2.0, send_timeout=0.2, on_error=self.errors.append, **kw)
        self.master = LinkSession(self.bridge.a, True, lambda c, i, d: self.got["m"].append((c, d)), **common)
        self.slave = LinkSession(self.bridge.b, False, lambda c, i, d: self.got["s"].append((c, d)), **common)

    def __enter__(self):
        self.slave.start()
        self.master.start()
        return self

    def __exit__(self, *exc):
        self.master.stop(5)
        self.slave.stop(5)
        assert not self.errors, self.errors

    def wait(self, side, n, timeout=10.0):
        t0 = time.time()
        while len(self.got[side]) < n and time.time() - t0 < timeout:
            time.sleep(0.01)
        assert len(self.got[side]) >= n, f"{side} got {len(self.got[side])}/{n}"


def test_messages_both_directions():
    with Pair() as p:
        p.master.send_message(1, b"to slave")
        p.slave.send_message(2, b"to master")
        p.wait("s", 1)
        p.wait("m", 1)
    assert p.got["s"] == [(1, b"to slave")] and p.got["m"] == [(2, b"to master")]


def test_many_small_messages_keep_order():
    with Pair() as p:
        for i in range(50):
            p.master.send_message(1, i.to_bytes(2, "big"))
            p.slave.send_message(1, i.to_bytes(2, "big"))
        p.wait("s", 50)
        p.wait("m", 50)
    assert [int.from_bytes(d, "big") for _, d in p.got["s"]] == list(range(50))
    assert [int.from_bytes(d, "big") for _, d in p.got["m"]] == list(range(50))


def test_large_messages_are_chunked_and_intact():
    big_m, big_s = os.urandom(3 * link.MAX_PAYLOAD + 123), os.urandom(link.MAX_PAYLOAD + 5)
    with Pair() as p:
        p.master.send_message(3, big_m)
        p.slave.send_message(4, big_s)
        p.wait("s", 1)
        p.wait("m", 1)
    assert p.got["s"] == [(3, big_m)] and p.got["m"] == [(4, big_s)]


def test_idle_stalls_do_not_break_the_session():
    bridge = FakeBridge(stall_after=0.05)
    with Pair(bridge) as p:
        time.sleep(0.4)  # several firmware stalls on the idle slave RX
        p.master.send_message(1, b"after idle")
        p.wait("s", 1)
    assert p.got["s"] == [(1, b"after idle")]


def test_resync_slave_owes_reply():
    bridge = FakeBridge()
    bridge.b.last = "rx"  # slave received something earlier and never answered
    bridge.a.last = "tx"  # master sent it and (as far as the firmware knows) now expects the reply
    with Pair(bridge) as p:
        p.master.send_message(1, b"hello")
        p.wait("s", 1)
    assert p.got["s"] == [(1, b"hello")]


def test_master_started_before_slave():
    bridge = FakeBridge(stall_after=0.1)
    p = Pair(bridge)
    p.master.start()
    p.master.send_message(1, b"early")
    time.sleep(0.4)  # the master has no peer yet; its sends are refused and its receives stall
    p.slave.start()
    p.slave.send_message(2, b"late")
    p.wait("s", 1)
    p.wait("m", 1)
    p.master.stop(5)
    p.slave.stop(5)
    assert p.got["s"] == [(1, b"early")] and p.got["m"] == [(2, b"late")]
    assert not p.errors


def test_stats_count_traffic():
    with Pair() as p:
        p.master.send_message(1, b"abc")
        p.wait("s", 1)
    assert p.master.stats["frames_tx"] >= 1 and p.slave.stats["bytes_rx"] == 3
