import hashlib
import os
from collections import deque

import pytest

from openaten import appmsg, transfer
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


@pytest.mark.parametrize("bad_id", ["../../evil", "a/b", "a\\b", "", "x" * 65, ".", "..", "a b", 5, None, ["x"], {"a": 1}])
def test_hostile_transfer_ids_are_ignored_not_trusted(tmp_path, bad_id):
    p = Pair(tmp_path)
    p.b.on_message({"kind": "offer", "id": bad_id, "name": "f", "size": 1}, b"")
    p.b.on_message({"kind": "chunk", "id": bad_id, "n": 0}, b"x")
    p.b.on_message({"kind": "done", "id": bad_id, "size": 1, "sha256": "0"}, b"")
    p.b.on_message({"kind": "cancel", "id": bad_id}, b"")
    p.a.on_message({"kind": "ack", "id": bad_id, "n": 0}, b"")
    assert p.b.status() == [] and not (tmp_path / "in").exists() and not (tmp_path / "evil").exists()


# -- directories ---------------------------------------------------------------------------


def make_tree(root):
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"alpha")
    (root / ".hidden").write_bytes(b"dot")
    (root / "empty").write_bytes(b"")
    (root / "ünï.bin").write_bytes(os.urandom(1500))
    (root / "sub" / "b.txt").write_bytes(b"beta")
    (root / "sub" / "deeper" / "c.txt").write_bytes(b"gamma")
    (root / "emptydir").mkdir()
    return root


def tree_of(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_directory_arrives_with_its_structure(tmp_path):
    src = make_tree(tmp_path / "photos")
    p = Pair(tmp_path, chunk=500, window=2)
    bid = p.a.send_directory(src)
    p.run()
    got = tmp_path / "in" / "photos"
    assert tree_of(got) == tree_of(src)  # including the dotfile, the empty file and the unicode name
    assert not (got / "emptydir").exists()  # empty directories are not sent
    assert not list((tmp_path / "in").glob(".openaten-*"))
    batch = [s for s in p.a.status() if s["id"] == bid][0]
    assert batch["state"] == "done" and batch["files"] == 6 and batch["files_done"] == 6 and batch["name"] == "photos/"
    assert batch["size"] == sum(len(v) for v in tree_of(src).values())
    assert {s["name"] for s in p.b.status()} >= {"photos/sub/deeper/c.txt", "photos/.hidden"}


def test_send_path_picks_file_or_directory(tmp_path):
    p = Pair(tmp_path)
    src = make_tree(tmp_path / "d")
    p.a.send_path(src / "a.txt")
    p.a.send_path(src)
    p.run()
    assert (tmp_path / "in" / "a.txt").read_bytes() == b"alpha"
    assert (tmp_path / "in" / "d" / "sub" / "b.txt").read_bytes() == b"beta"


def test_existing_directory_is_never_merged_into(tmp_path):
    (tmp_path / "in" / "photos").mkdir(parents=True)
    (tmp_path / "in" / "photos" / "keep.txt").write_text("mine")
    p = Pair(tmp_path)
    src = make_tree(tmp_path / "photos")
    p.a.send_directory(src)
    p.run()
    assert (tmp_path / "in" / "photos" / "keep.txt").read_text() == "mine"
    assert sorted(os.listdir(tmp_path / "in" / "photos")) == ["keep.txt"]
    assert tree_of(tmp_path / "in" / "photos (1)") == tree_of(src)


def test_only_a_few_files_of_a_directory_are_in_flight(tmp_path):
    src = tmp_path / "many"
    src.mkdir()
    for i in range(12):
        (src / f"f{i}").write_bytes(b"x" * 300)
    p = Pair(tmp_path, chunk=100, window=1)
    p.a.send_directory(src)
    most = 0
    for _ in range(2000):
        most = max(most, len(p.a._out))
        if not p.step() and not p.a_to_b and not p.b_to_a:
            break
    assert 1 <= most <= transfer.BATCH_CONCURRENCY
    assert len(tree_of(tmp_path / "in" / "many")) == 12


def test_symlinks_and_special_files_are_not_followed(tmp_path):
    src = tmp_path / "d"
    src.mkdir()
    (src / "real.txt").write_text("real")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (src / "link.txt").symlink_to(outside)
    (src / "linkdir").symlink_to(tmp_path / "in", target_is_directory=True)
    os.mkfifo(src / "pipe")
    p = Pair(tmp_path)
    p.a.send_directory(src)
    p.run()
    assert list(tree_of(tmp_path / "in" / "d")) == ["real.txt"]


def test_unusable_directories_are_refused_up_front(tmp_path, monkeypatch):
    p = Pair(tmp_path)
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no regular files"):
        p.a.send_directory(tmp_path / "empty")
    with pytest.raises(ValueError):
        p.a.send_directory(tmp_path / "missing")
    big = tmp_path / "big"
    big.mkdir()
    for i in range(4):
        (big / str(i)).write_bytes(b"x")
    monkeypatch.setattr(transfer, "MAX_BATCH_FILES", 3)
    with pytest.raises(ValueError, match="more than 3 files"):
        p.a.send_directory(big)
    monkeypatch.undo()
    p.up = False
    with pytest.raises(ValueError, match="link"):
        p.a.send_directory(make_tree(tmp_path / "d"))
    assert p.a.status() == []


def test_receiver_that_refuses_stops_the_whole_directory(tmp_path):
    p = Pair(tmp_path, receive=False)
    src = make_tree(tmp_path / "d")
    bid = p.a.send_directory(src)
    p.run()
    batch = [s for s in p.a.status() if s["id"] == bid][0]
    assert batch["state"] == "failed" and "not enabled" in batch["error"]
    assert p.log.count("offer") <= transfer.BATCH_CONCURRENCY  # the rest was never offered


def test_cancel_stops_a_directory_in_flight(tmp_path):
    src = tmp_path / "d"
    src.mkdir()
    for i in range(6):
        (src / f"f{i}").write_bytes(b"x" * 2000)
    p = Pair(tmp_path, chunk=100, window=1)
    bid = p.a.send_directory(src)
    p.step()
    p.step()
    assert p.a.cancel(bid)
    p.run()
    batch = [s for s in p.a.status() if s["id"] == bid][0]
    assert batch["state"] == "cancelled"
    assert not p.a._out and not list((tmp_path / "in").glob(".openaten-*"))
    assert len(tree_of(tmp_path / "in" / "d")) < 6


def test_link_loss_fails_a_directory(tmp_path):
    p = Pair(tmp_path, chunk=100, window=1)
    src = make_tree(tmp_path / "d")
    bid = p.a.send_directory(src)
    p.step()
    p.step()
    p.a.link_lost()
    batch = [s for s in p.a.status() if s["id"] == bid][0]
    assert batch["state"] == "failed" and "link went down" in batch["error"]
    assert not p.a._batches and not p.a._out


@pytest.mark.parametrize(
    "extra",
    [
        {"batch": "b1", "top": "d", "rel": ["..", "x"]},
        {"batch": "b1", "top": "d", "rel": ["a/b"]},
        {"batch": "b1", "top": "d", "rel": ["a\\b"]},
        {"batch": "b1", "top": "..", "rel": ["x"]},
        {"batch": "b1", "top": "d/e", "rel": ["x"]},
        {"batch": "b1", "top": "d", "rel": []},
        {"batch": "b1", "top": "d", "rel": "x"},
        {"batch": "b1", "top": "d", "rel": ["x"] * 33},
        {"batch": "../b", "top": "d", "rel": ["x"]},
        {"batch": 5, "top": "d", "rel": ["x"]},
        {"batch": "b1", "top": 5, "rel": ["x"]},
    ],
)
def test_hostile_directory_offers_are_rejected_and_write_nothing(tmp_path, extra):
    p = Pair(tmp_path)
    p.b.on_message({"kind": "offer", "id": "t1", "name": "x", "size": 1, **extra}, b"")
    assert [appmsg.unpack(m)[0]["kind"] for m in p.b_to_a] == ["reject"]
    assert p.b.status() == []
    assert not [f for f in tmp_path.rglob("*") if f.name not in ("in",) and tmp_path / "in" in f.parents]
    assert not (tmp_path.parent / "x").exists()


def test_a_directory_offer_cannot_escape_through_a_later_file(tmp_path):
    p = Pair(tmp_path)
    for i, rel in enumerate((["ok", "one.txt"], ["ok", "two.txt"])):
        p.b.on_message({"kind": "offer", "id": f"t{i}", "name": "x", "size": 1, "batch": "b1", "top": "T", "rel": rel}, b"")
        p.b.on_message({"kind": "chunk", "id": f"t{i}", "n": 0}, b"z")
        p.b.on_message({"kind": "done", "id": f"t{i}", "size": 1, "sha256": hashlib.sha256(b"z").hexdigest()}, b"")
    assert sorted(tree_of(tmp_path / "in" / "T")) == ["ok/one.txt", "ok/two.txt"]  # both files share one top directory
