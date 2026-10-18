import hashlib
import os
from collections import deque

import pytest

from openaten import appmsg
from openaten.transfer import TransferManager, safe_name


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Pair:
    """Two managers joined by in-memory queues; ``tamper(direction, header, payload)`` may alter or drop (return None) a message."""

    def __init__(self, tmp_path, receive=True, **kw):
        self.clock = Clock()
        self.a_to_b: deque = deque()
        self.b_to_a: deque = deque()
        self.up = True  # whether the sending side's link accepts messages (the receiver's always does)
        self.tamper = None
        self.log: list = []
        self.a = TransferManager(lambda d: self._put(self.a_to_b, "a", d), None, now=self.clock, **kw)
        self.b = TransferManager(lambda d: self._put(self.b_to_a, "b", d), tmp_path / "in" if receive else None, now=self.clock, **kw)

    def _put(self, q, who, data):
        if who == "a" and not self.up:
            return False
        q.append(data)
        return True

    def step(self):
        moved = False
        for q, dst in ((self.a_to_b, self.b), (self.b_to_a, self.a)):
            while q:
                header, payload = appmsg.unpack(q.popleft())
                self.log.append(header["kind"])
                if self.tamper:
                    got = self.tamper(header, payload)
                    if got is None:
                        continue
                    header, payload = got
                dst.on_message(header, payload)
                moved = True
        self.a.pump()
        self.b.pump()
        return moved

    def run(self, limit=10000):
        for _ in range(limit):
            if not self.step() and not self.a_to_b and not self.b_to_a:
                return
        raise AssertionError("did not settle")


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_file_arrives_intact_across_many_chunks(tmp_path):
    data = os.urandom(10_000)
    p = Pair(tmp_path, chunk=1000, window=3)
    p.a.send_file(write(tmp_path, "photo.bin", data))
    p.run()
    got = (tmp_path / "in" / "photo.bin").read_bytes()
    assert got == data
    assert [s["state"] for s in p.a.status()] == ["done"] and [s["state"] for s in p.b.status()] == ["done"]
    assert p.a.status()[0]["done"] == len(data)
    assert not list((tmp_path / "in").glob(".openaten-*")), "no partial file left behind"


def test_window_limits_unacknowledged_chunks(tmp_path):
    p = Pair(tmp_path, chunk=100, window=2)
    p.a.send_file(write(tmp_path, "f", b"x" * 1000))
    p.step()  # offer -> accept
    p.step()
    in_flight = 0
    while p.a_to_b or p.b_to_a:
        in_flight = max(in_flight, sum(1 for m in p.a_to_b if appmsg.unpack(m)[0]["kind"] == "chunk"))
        p.step()
    assert 1 <= in_flight <= 2
    assert (tmp_path / "in" / "f").read_bytes() == b"x" * 1000


def test_empty_file(tmp_path):
    p = Pair(tmp_path)
    p.a.send_file(write(tmp_path, "empty", b""))
    p.run()
    assert (tmp_path / "in" / "empty").read_bytes() == b""
    assert p.a.status()[0]["state"] == "done"


def test_receiving_disabled_rejects(tmp_path):
    p = Pair(tmp_path, receive=False)
    p.a.send_file(write(tmp_path, "f", b"data"))
    p.run()
    s = p.a.status()[0]
    assert s["state"] == "rejected" and "not enabled" in s["error"]
    assert not (tmp_path / "in").exists()


def test_too_large_is_rejected(tmp_path):
    p = Pair(tmp_path, max_size=10)
    p.b.max_size = 10
    p.a.send_file(write(tmp_path, "f", b"x" * 11))
    p.run()
    assert p.a.status()[0]["state"] == "rejected" and "larger" in p.a.status()[0]["error"]


def test_existing_files_are_never_overwritten(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "report.txt").write_text("precious")
    p = Pair(tmp_path)
    for body in (b"one", b"two"):
        p.a.send_file(write(tmp_path, "report.txt", body))
        p.run()
    assert (tmp_path / "in" / "report.txt").read_text() == "precious"
    assert sorted(f.read_bytes() for f in (tmp_path / "in").iterdir() if f.name != "report.txt") == [b"one", b"two"]
    assert {f.name for f in (tmp_path / "in").iterdir()} == {"report.txt", "report (1).txt", "report (2).txt"}


@pytest.mark.parametrize(
    "given,expected",
    [("a.txt", "a.txt"), ("../../etc/passwd", "passwd"), ("..\\..\\win\\system32\\x.dll", "x.dll"), (".bashrc", "bashrc"), ("..", None), ("", None), ("/", None), ("a\x00b\nc.txt", "abc.txt"), (5, None), ("é" * 300, "é" * 100)],
)
def test_safe_name(given, expected):
    assert safe_name(given) == expected


def test_hostile_offer_names_cannot_escape_the_receive_dir(tmp_path):
    p = Pair(tmp_path)
    p.b.on_message({"kind": "offer", "id": "abc", "name": "../../evil", "size": 1}, b"")
    p.b.on_message({"kind": "chunk", "id": "abc", "n": 0}, b"x")
    p.b.on_message({"kind": "done", "id": "abc", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}, b"")
    assert [f.name for f in (tmp_path / "in").iterdir()] == ["evil"]
    assert not (tmp_path.parent / "evil").exists()


def test_corrupted_chunk_fails_the_transfer_and_leaves_nothing(tmp_path):
    p = Pair(tmp_path, chunk=100)
    p.tamper = lambda h, d: (h, b"X" + d[1:]) if h["kind"] == "chunk" and h["n"] == 1 else (h, d)
    p.a.send_file(write(tmp_path, "f", b"y" * 500))
    p.run()
    assert p.a.status()[0]["state"] == "failed" and "checksum" in p.a.status()[0]["error"]
    assert not list((tmp_path / "in").iterdir())


def test_out_of_order_or_oversized_chunks_fail(tmp_path):
    p = Pair(tmp_path)
    p.b.on_message({"kind": "offer", "id": "a1", "name": "f", "size": 3}, b"")
    p.b.on_message({"kind": "chunk", "id": "a1", "n": 5}, b"abc")
    assert p.b.status()[0]["state"] == "failed"
    p.b.on_message({"kind": "offer", "id": "a2", "name": "g", "size": 3}, b"")
    p.b.on_message({"kind": "chunk", "id": "a2", "n": 0}, b"abcd")  # more than announced
    assert p.b.status()[-1]["state"] == "failed"
    assert not [f for f in (tmp_path / "in").iterdir()]


def test_sender_cancel_cleans_up_both_sides(tmp_path):
    p = Pair(tmp_path, chunk=100, window=1)
    tid = p.a.send_file(write(tmp_path, "f", b"z" * 1000))
    p.step()
    p.step()
    assert p.a.cancel(tid)
    p.run()
    assert p.a.status()[0]["state"] == "cancelled" and p.b.status()[0]["state"] == "cancelled"
    assert not list((tmp_path / "in").iterdir())
    assert not p.a.cancel("nope")


def test_link_loss_fails_everything_in_flight(tmp_path):
    p = Pair(tmp_path, chunk=100, window=1)
    p.a.send_file(write(tmp_path, "f", b"z" * 1000))
    p.step()
    p.step()
    p.a.link_lost()
    p.b.link_lost()
    assert {s["state"] for s in p.a.status() + p.b.status()} == {"failed"}
    assert not list((tmp_path / "in").iterdir())


def test_stalled_peer_times_out(tmp_path):
    p = Pair(tmp_path, idle_timeout=30)
    p.a.send_file(write(tmp_path, "f", b"data"))
    p.a_to_b.clear()  # the offer is lost
    p.clock.t += 31
    p.a.pump()
    assert p.a.status()[0]["state"] == "failed" and "timed out" in p.a.status()[0]["error"]


def test_chunks_wait_while_the_link_is_down_and_resume(tmp_path):
    p = Pair(tmp_path, chunk=100, window=4)
    data = os.urandom(1000)
    p.a.send_file(write(tmp_path, "f", data))
    p.step()
    p.step()  # accept arrives, first chunks queued
    p.up = False
    for _ in range(5):
        p.step()
    p.up = True
    p.run()
    assert (tmp_path / "in" / "f").read_bytes() == data


def test_send_file_errors(tmp_path):
    p = Pair(tmp_path)
    with pytest.raises(ValueError, match="not a regular file"):
        p.a.send_file(tmp_path)
    with pytest.raises(ValueError, match="not a regular file"):
        p.a.send_file(tmp_path / "missing")
    p.up = False
    with pytest.raises(ValueError, match="link"):
        p.a.send_file(write(tmp_path, "f", b"x"))


def test_unknown_ids_and_garbage_are_ignored(tmp_path):
    p = Pair(tmp_path)
    for kind in ("accept", "reject", "ack", "result", "cancel", "chunk", "done", "bogus"):
        p.a.on_message({"kind": kind, "id": "nope", "n": 0}, b"x")
        p.b.on_message({"kind": kind, "id": "nope", "n": 0}, b"x")
    p.b.on_message({"kind": "offer", "id": 5, "name": "f", "size": 1}, b"")
    p.b.on_message({"kind": "offer", "id": "ok", "name": "f", "size": -1}, b"")
    assert p.a.status() == [] and [s["state"] for s in p.b.status()] == []


def test_file_that_shrinks_while_sending_fails(tmp_path):
    p = Pair(tmp_path, chunk=100, window=1)
    f = write(tmp_path, "f", b"q" * 100_000)
    p.a.send_file(f)
    p.step()
    p.step()
    f.write_bytes(b"q" * 150)  # truncated mid-transfer (same inode: the open handle sees it)
    p.run()
    assert p.a.status()[0]["state"] == "failed"
    assert not list((tmp_path / "in").iterdir())
