from openaten import control as c
from openaten.edges import EdgeSwitch, Screen

DESK = Screen("DP-1", "590i", 0, 0, 2560, 1440)
LAPTOP = Screen("eDP-1", "xps15", -1920, 300, 1920, 1200)
KEY = "590i:DP-1~xps15:eDP-1"


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make(settle=0.5):
    clock, log = Clock(), []
    h = c.Handoff(lambda b, r: log.append(("push", b, r)), lambda: log.append(("pull",)), lambda d: log.append(("send", c.decode(d))), settle, clock)
    return h, clock, log


def test_encode_decode_roundtrip_and_garbage():
    assert c.decode(c.encode("give-back", border=KEY, row=5.0)) == {"kind": "give-back", "border": KEY, "row": 5.0}
    assert c.decode(b"not json") is None and c.decode(b"[1]") is None


def test_edge_enter_on_the_sending_side_starts_forwarding():
    h, _, log = make()
    h.edge(KEY, 600.0)
    assert log == [("push", KEY, 600.0)]
    h.started(KEY, 600.0)
    h.stopped()
    assert log[1:] == [("send", {"kind": "take-control", "border": KEY, "row": 600.0}), ("send", {"kind": "release"})]


def test_receiver_gives_control_back_only_after_leaving_the_strip_and_settling():
    h, clock, log = make()
    h.message({"kind": "take-control", "border": KEY, "row": 600.0})
    assert h.receiving
    h.edge(KEY, 600.0, enter=True)  # the pointer was just placed on the strip
    clock.t = 1.0
    h.edge(KEY, 600.0, enter=True)  # still not left since arrival
    assert h.receiving and log == []
    h.edge(KEY, 600.0, enter=False)  # moves into the screen...
    h.edge(KEY, 700.0, enter=True)  # ...and pushes back
    assert not h.receiving and log == [("send", {"kind": "give-back", "border": KEY, "row": 700.0})]


def test_events_during_the_settle_window_are_ignored():
    h, clock, log = make(settle=0.5)
    h.message({"kind": "take-control"})
    h.edge(KEY, 0, enter=False)
    clock.t = 0.2
    h.edge(KEY, 0, enter=False)
    clock.t = 1.0
    h.edge(KEY, 0, enter=True)  # never armed: the leave events came too early
    assert h.receiving and log == []


def test_give_back_and_release_messages():
    h, _, log = make()
    h.message({"kind": "give-back", "border": KEY, "row": 1})
    assert log == [("pull",)]
    h.message({"kind": "take-control"})
    h.message({"kind": "release"})
    assert not h.receiving


def test_border_keys_agree_on_both_computers():
    a = EdgeSwitch([DESK, LAPTOP], "590i", source="strip")
    b = EdgeSwitch([DESK, LAPTOP], "xps15", source="strip")
    assert a.keys == b.keys == [KEY]


def test_strip_source_ignores_motion_and_accepts_reported_rows():
    sw = EdgeSwitch([DESK, LAPTOP], "590i", source="strip")
    assert not sw.local_motion([(2, 0, -5000), (2, 1, -5000)]) and sw.state == "calibrating"
    assert not sw.border_pushed("nope", 5)
    assert sw.border_pushed(KEY, 800.0)
    assert sw.remote_report(bytes([1, 0, 0, 0x9C, 0])) is False
    reports = sw.entering()
    assert reports and sw.remote.lo == [0, 800.0]
