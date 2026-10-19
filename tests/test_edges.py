import pytest

from openaten import hid
from openaten.edges import EdgeSwitch, Screen, borders
from openaten.forward import Forwarder

# 590i (local) at the origin; xps15 (remote) to its left and a bit below.
DESK = Screen("DP-1", "590i", 0, 0, 2560, 1440)
LAPTOP = Screen("eDP-1", "xps15", -1920, 300, 1920, 1200)


def switch(**kw):
    return EdgeSwitch([DESK, LAPTOP], "590i", push=kw.pop("push", 40), **kw)


def rel(dx=0, dy=0):
    return [(hid.EV_REL, hid.REL_X, dx)] if dx else [(hid.EV_REL, hid.REL_Y, dy)]


def syn(events):
    return events + [(hid.EV_SYN, hid.SYN_REPORT, 0)]


def calibrate(sw):
    """Slam into the top-left corner: both axes clamp."""
    sw.local_motion(rel(dx=-5000) + rel(dy=-5000))
    assert sw.state == "ready"


def test_borders_cover_only_the_overlap():
    (local, remote), = borders([DESK, LAPTOP], "590i")
    assert (local.axis, local.sign, local.line, local.lo, local.hi) == (0, -1, 0, 300, 1440)  # xps spans y 300..1500, desk 0..1440
    assert (remote.axis, remote.sign, remote.far) == (0, +1, "DP-1")


def test_no_shared_border_is_an_error():
    with pytest.raises(ValueError):
        EdgeSwitch([DESK, Screen("far", "xps15", -3000, 0, 1920, 1080)], "590i")


def test_position_is_unknown_until_clamped():
    sw = switch()
    assert sw.state == "calibrating"
    assert not sw.local_motion(rel(dx=-3000))  # pushing left alone is not enough: y is still unknown
    assert sw.state == "calibrating"
    sw.local_motion(rel(dy=-3000))
    assert sw.state == "ready"


def test_push_through_the_border_inside_the_shared_segment():
    sw = switch()
    calibrate(sw)  # at (0, 0): above the laptop's top (y 300), so not on the border
    assert not sw.local_motion(rel(dx=-500))  # outside the overlap: just the desktop's edge
    sw.local_motion(rel(dy=600))  # y=600, inside 300..1440
    assert not sw.local_motion(rel(dx=-30))  # below the push threshold
    assert sw.local_motion(rel(dx=-30))


def test_inward_motion_resets_the_push():
    sw = switch()
    calibrate(sw)
    sw.local_motion(rel(dy=600))
    assert not sw.local_motion(rel(dx=-30))
    sw.local_motion(rel(dx=10))
    assert not sw.local_motion(rel(dx=-30))


def test_entry_slams_to_corner_then_moves_to_the_matching_row():
    sw = switch()
    calibrate(sw)
    sw.local_motion(rel(dy=600))
    assert sw.local_motion(rel(dx=-60))
    reports = sw.entering()
    dx = sum(r[3] - 256 if r[3] > 127 else r[3] for r in reports)
    dy = sum(r[4] - 256 if r[4] > 127 else r[4] for r in reports)
    # net: from the corner (-1920, 300) to the border at the same virtual row (x=0, y=600)
    assert (dx, dy) == (round(0 - (-1920)) + (-(1920 + 200)), 300 + (-(1200 + 200)))
    assert sw.remote.known and sw.remote.lo == [0, 600]


def test_remote_pointer_pushing_back_hands_control_back():
    sw = switch()
    calibrate(sw)
    sw.local_motion(rel(dy=600))
    sw.local_motion(rel(dx=-60))
    sw.entering()
    move = lambda dx: bytes([1, 0, 0, dx & 0xFF, 0])
    assert not sw.remote_report(move(-100))  # into the laptop screen
    assert not sw.remote_report(move(100))  # ...and back to the border row: exactly at the edge, no overshoot yet
    assert not sw.remote_report(move(30))
    assert sw.remote_report(move(30))


class Placer:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def place(self, x, y):
        self.calls.append((x, y))
        return self.ok


def pushed_back(placer):
    sw = switch()
    sw.warp = placer
    calibrate(sw)
    sw.local_motion(rel(dy=600))
    sw.local_motion(rel(dx=-60))  # out at row 600
    sw.entering()
    move = lambda dx: bytes([1, 0, 0, dx & 0xFF, 0])
    for dx in (-100, 100, 30):
        sw.remote_report(move(dx))
    assert sw.remote_report(move(30))
    return sw


def test_pushing_back_places_the_local_pointer_where_the_other_one_came_back():
    placer = Placer()
    sw = pushed_back(placer)
    sw.leaving()
    assert placer.calls == [(0, 600)]  # the last column of the desk, at the row the other pointer crossed
    assert sw.local.known and sw.local.lo == [0, 600]  # known exactly now: no recalibration
    sw.leaving()
    assert len(placer.calls) == 1  # once per return


def test_a_declining_backend_leaves_the_local_pointer_as_it_was():
    placer = Placer(ok=False)
    sw = pushed_back(placer)
    before = list(sw.local.lo)
    sw.leaving()
    assert placer.calls == [(0, 600)] and sw.local.lo == before


def test_returning_by_hotkey_or_without_a_backend_places_nothing():
    placer = Placer()
    sw = switch()
    sw.warp = placer
    calibrate(sw)
    sw.local_motion(rel(dy=600))
    sw.local_motion(rel(dx=-60))
    sw.entering()
    sw.leaving()  # no push-back happened: the hotkey returned control
    assert placer.calls == []
    sw2 = pushed_back(Placer())
    sw2.warp = None
    sw2.leaving()  # no backend: nothing to call, nothing raised


def test_hotkey_entry_leaves_the_remote_position_unknown():
    sw = switch()
    assert sw.entering() == []
    assert not sw.remote.known


class Rig:
    def __init__(self):
        self.sent, self.states = [], []
        self.f = Forwarder(self.sent.append, hotkey="ctrl+alt+k", on_state=self.states.append, edges=switch())
        self.f.handle(rel(dx=-5000) + rel(dy=-5000), source=1)
        self.f.handle(rel(dy=600), source=1)


def test_forwarder_hands_off_at_the_edge_and_back():
    r = Rig()
    r.f.handle(rel(dx=-60), source=1)
    assert r.f.forwarding and r.states == [True]
    assert r.sent  # slam + positioning reports
    r.sent.clear()
    r.f.handle(syn(rel(dx=-100)), source=1)  # moves the remote pointer into its screen
    r.f.handle(syn(rel(dx=100)), source=1)
    r.f.handle(syn(rel(dx=70)), source=1)  # pushes back through
    assert not r.f.forwarding and r.states == [True, False]


def test_forwarder_defers_the_handoff_while_a_button_is_held():
    held = {"v": True}
    sent, states = [], []
    f = Forwarder(sent.append, hotkey="ctrl+alt+k", on_state=states.append, keys_down=lambda: held["v"], edges=switch())
    f.handle(rel(dx=-5000) + rel(dy=-5000), source=1)
    f.handle(rel(dy=600), source=1)
    f.handle(rel(dx=-60), source=1)
    assert f.desired and not f.active and states == []
    held["v"] = False
    f.tick()
    assert f.forwarding and states == [True]
