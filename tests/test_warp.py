import time

import pytest

from openaten import daemon as dm
from openaten import warp
from openaten.edges import EdgeSwitch, Screen
from test_daemon import make

DESK = Screen("DP-1", "desk", 0, 0, 2560, 1440)
LAPTOP = Screen("eDP-1", "laptop", -1920, 300, 1920, 1200)  # to the left of the desk, shifted down


def test_null_warp_declines():
    assert warp.NullWarp().place(10, 20) is False


def test_entry_point_is_the_last_pixel_inside_the_border():
    desk_side = EdgeSwitch([DESK, LAPTOP], "desk")
    laptop_side = EdgeSwitch([DESK, LAPTOP], "laptop")
    (key,) = desk_side.keys
    assert key in laptop_side.keys  # both computers derive the same key
    # the shared segment is y 300..1440 on the line x = 0: the desk's first column is x = 0, the laptop's last column is x = -1
    assert desk_side.entry_point(key, 700) == (0, 700)
    assert laptop_side.entry_point(key, 700) == (-1, 700)
    assert desk_side.entry_point(key, 50) == (0, 300)  # clamped to the shared segment
    assert desk_side.entry_point(key, 99999) == (0, 1440)
    assert desk_side.entry_point("no-such-border", 700) is None


def test_entry_point_for_a_border_below():
    top = Screen("A", "desk", 0, 0, 1000, 500)
    below = Screen("B", "laptop", 200, 500, 1000, 500)
    sw = EdgeSwitch([top, below], "desk")
    (key,) = sw.keys
    assert sw.entry_point(key, 600) == (600, 499)  # the desk's bottom row
    assert EdgeSwitch([top, below], "laptop").entry_point(key, 600) == (600, 500)


class Recorder:
    def __init__(self, ok=True, boom=False):
        self.calls, self.ok, self.boom = [], ok, boom

    def place(self, x, y):
        if self.boom:
            raise RuntimeError("uinput gone")
        self.calls.append((x, y))
        return self.ok


def daemon_with_edges(tmp_path, recorder):
    d, runners, _ = make(tmp_path, warp=recorder)
    d.dir.mkdir(exist_ok=True)
    d.run_once()
    sw = EdgeSwitch([DESK, LAPTOP], "desk")
    runners[0].edges = sw
    return d, sw


def peer(d, **msg):
    d.commands.put(("peer", msg))
    d.run_once(0.01)


def test_take_control_places_the_pointer_after_a_short_delay(tmp_path):
    rec = Recorder()
    d, sw = daemon_with_edges(tmp_path, rec)
    (key,) = sw.keys
    peer(d, kind="take-control", border=key, row=700.0)
    assert rec.calls == []  # not yet: the sender's approximate placement reports must land first
    time.sleep(dm.WARP_DELAY + 0.05)
    d.run_once(0.01)
    assert rec.calls == [(0, 700.0)]
    d.run_once(0.01)
    assert len(rec.calls) == 1  # once


def test_give_back_places_the_pointer_at_the_return_row_at_once(tmp_path):
    rec = Recorder()
    d, sw = daemon_with_edges(tmp_path, rec)
    (key,) = sw.keys
    peer(d, kind="give-back", border=key, row=900.0)
    assert rec.calls == [(0, 900.0)]


def test_give_back_cancels_a_pending_placement(tmp_path):
    rec = Recorder()
    d, sw = daemon_with_edges(tmp_path, rec)
    (key,) = sw.keys
    peer(d, kind="take-control", border=key, row=700.0)
    peer(d, kind="give-back", border=key, row=900.0)
    time.sleep(dm.WARP_DELAY + 0.05)
    d.run_once(0.01)
    assert rec.calls == [(0, 900.0)]


@pytest.mark.parametrize(
    "msg",
    [
        {"kind": "take-control", "border": None, "row": None},  # started by hotkey: nothing to place
        {"kind": "take-control", "border": "unknown", "row": 5.0},
        {"kind": "take-control", "border": 5, "row": 5.0},
        {"kind": "give-back", "border": "x", "row": "high"},
        {"kind": "give-back", "border": "x", "row": True},
        {"kind": "release"},
    ],
)
def test_messages_without_a_usable_border_and_row_place_nothing(tmp_path, msg):
    rec = Recorder()
    d, _ = daemon_with_edges(tmp_path, rec)
    peer(d, **msg)
    time.sleep(dm.WARP_DELAY + 0.05)
    d.run_once(0.01)
    assert rec.calls == []


def test_a_failing_or_declining_backend_does_not_disturb_the_daemon(tmp_path, caplog):
    for rec in (Recorder(boom=True), Recorder(ok=False)):
        d, sw = daemon_with_edges(tmp_path, rec)
        (key,) = sw.keys
        peer(d, kind="give-back", border=key, row=900.0)
        assert not d.stop_flag.is_set()
    assert "pointer placement failed" in caplog.text


def test_without_a_layout_nothing_is_placed(tmp_path):
    rec = Recorder()
    d, runners, _ = make(tmp_path, warp=rec)
    d.dir.mkdir(exist_ok=True)
    d.run_once()  # the fake runner has no .edges
    peer(d, kind="give-back", border="k", row=1.0)
    assert rec.calls == []
