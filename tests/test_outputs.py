import json
import logging
import subprocess
from types import SimpleNamespace

import pytest

from openaten import edges, outputs
from openaten import hid as H
from openaten.edges import EdgeSwitch, Screen
from openaten.forward import Forwarder

NIRI = json.dumps(
    {
        "DP-1": {"name": "DP-1", "make": "Dell", "logical": {"x": 0, "y": 0, "width": 2560, "height": 1440, "scale": 1.0, "transform": "Normal"}},
        "HDMI-A-1": {"name": "HDMI-A-1", "logical": {"x": 2560, "y": 200, "width": 1920, "height": 1080, "scale": 1.0}},
        "eDP-9": {"name": "eDP-9", "logical": None},  # disabled
    }
)


def test_parse_niri_object_list_and_disabled_outputs():
    got = outputs.parse_niri(NIRI, "desk")
    assert got == [Screen("DP-1", "desk", 0, 0, 2560, 1440), Screen("HDMI-A-1", "desk", 2560, 200, 1920, 1080)]
    as_list = json.dumps(list(json.loads(NIRI).values()))
    assert outputs.parse_niri(as_list, "desk") == got


@pytest.mark.parametrize(
    "text",
    ["not json", "42", '{"a": 5}', '[{"logical": {"x": 0}}]', '{"A": {"name": "A", "logical": {"x": 0, "y": 0}}}', '{"A": {"name": "A", "logical": {"x": 0, "y": 0, "width": 0, "height": 5}}}'],
)
def test_parse_niri_rejects_unexpected_shapes(text):
    with pytest.raises(ValueError):
        outputs.parse_niri(text, "desk")


def fake_run(stdout="", returncode=0, stderr="", raises=None):
    def run(argv, capture_output, text, timeout):
        assert argv == ["niri", "msg", "--json", "outputs"]
        if raises:
            raise raises
        return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    return run


def test_niri_outputs_success_and_every_failure_falls_back_to_none(caplog):
    assert len(outputs.niri_outputs("desk", fake_run(NIRI))) == 2
    for run in (
        fake_run(raises=FileNotFoundError("niri")),
        fake_run(raises=subprocess.TimeoutExpired("niri", 3)),
        fake_run(returncode=1, stderr="niri is not running"),
        fake_run("garbage"),
        fake_run("{}"),
    ):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="openaten.outputs"):
            assert outputs.niri_outputs("desk", run) is None
        assert "layout file" in caplog.text


REMOTE_ONLY = """
local = "desk"
local_outputs = "niri"
[[screen]]
name = "eDP-1"
host = "laptop"
x = -1920
y = 300
width = 1920
height = 1200
"""


def write(tmp_path, text):
    p = tmp_path / "screens.toml"
    p.write_text(text)
    return p


def test_load_takes_the_local_screens_from_the_compositor(tmp_path):
    sw = edges.load(write(tmp_path, REMOTE_ONLY), outputs=lambda host: outputs.parse_niri(NIRI, host))
    assert [(a.far, b.far) for a, b in sw.pairs] == [("eDP-1", "DP-1")]  # laptop touches DP-1's left edge


def test_compositor_screens_replace_the_files_local_entries(tmp_path):
    text = REMOTE_ONLY + '\n[[screen]]\nname = "DP-1"\nhost = "desk"\nx = 999\ny = 999\nwidth = 10\nheight = 10\n'
    sw = edges.load(write(tmp_path, text), outputs=lambda host: [Screen("DP-1", host, 0, 0, 2560, 1440)])
    assert sw.local.screens == [Screen("DP-1", "desk", 0, 0, 2560, 1440)]


def test_file_screens_are_used_when_the_compositor_cannot_be_read(tmp_path):
    text = REMOTE_ONLY + '\n[[screen]]\nname = "DP-1"\nhost = "desk"\nx = 0\ny = 0\nwidth = 2560\nheight = 1440\n'
    sw = edges.load(write(tmp_path, text), outputs=lambda host: None)
    assert sw.local.screens == [Screen("DP-1", "desk", 0, 0, 2560, 1440)]
    with pytest.raises(ValueError, match="need screens on this computer"):
        edges.load(write(tmp_path, REMOTE_ONLY), outputs=lambda host: None)  # nothing to fall back to


def test_unknown_local_outputs_source_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="local_outputs"):
        edges.load(write(tmp_path, REMOTE_ONLY.replace('"niri"', '"sway"')))


# -- touchpad in motion mode --------------------------------------------------------------

DESK = Screen("DP-1", "desk", 0, 0, 2560, 1440)
LAPTOP = Screen("eDP-1", "laptop", -1920, 300, 1920, 1200)


def pad_events(x, y, touch=None):
    ev = []
    if touch is not None:
        ev.append((H.EV_KEY, H.BTN_TOUCH, touch))
    ev += [(H.EV_ABS, H.ABS_X, x), (H.EV_ABS, H.ABS_Y, y), (H.EV_SYN, H.SYN_REPORT, 0)]
    return ev


def drag_left(sw, steps, y=600, start=4000, step=100, source="pad"):
    """A finger dragging left across the pad; returns True when the pointer pushed through the border."""
    x = start
    assert not sw.local_touchpad(pad_events(x, y, touch=1), source)
    for _ in range(steps):
        x -= step
        if sw.local_touchpad(pad_events(x, y), source):
            return True
    return False


def test_touchpad_motion_drives_the_pointer_to_the_border_and_pushes_through():
    sw = EdgeSwitch([DESK, LAPTOP], "desk", push=40, touchpad_scale=1.0)
    sw.local.set([200.0, 600.0])
    assert drag_left(sw, steps=40)  # 100 pad units * 0.6 speed = 60 counts per step: out of the screen in a few steps
    assert sw._exit is not None and sw._exit[0] == 0
    assert sw._exit[1] == pytest.approx(600.0)  # left the desk at the row where it was


def test_touchpad_scale_changes_how_far_a_swipe_goes():
    slow = EdgeSwitch([DESK, LAPTOP], "desk", push=40, touchpad_scale=0.1)
    slow.local.set([200.0, 600.0])
    assert not drag_left(slow, steps=5)  # 5 steps * 60 counts * 0.1 = 30 px: still on the screen
    assert 150 < slow.local.lo[0] < 200


def test_touchpad_does_nothing_when_the_pointer_is_not_known_or_in_strip_mode():
    sw = EdgeSwitch([DESK, LAPTOP], "desk", push=40)
    assert not drag_left(sw, steps=60)  # calibrating: no position yet, so no hand-off
    strip = EdgeSwitch([DESK, LAPTOP], "desk", push=40, source="strip")
    strip.local.set([200.0, 600.0])
    assert not drag_left(strip, steps=60)


def test_forwarder_starts_forwarding_when_a_touchpad_pushes_through():
    sw = EdgeSwitch([DESK, LAPTOP], "desk", push=40)
    sw.local.set([200.0, 600.0])
    sent, states = [], []
    f = Forwarder(sent.append, hotkey="ctrl+alt+k", on_state=states.append, edges=sw)
    f.handle(pad_events(4000, 600, touch=1), source="pad", touchpad=True)
    x = 4000
    for _ in range(40):
        x -= 100
        f.handle(pad_events(x, 600), source="pad", touchpad=True)
        if f.desired:
            break
    assert f.desired and not f.active, "the finger is still down: forwarding waits until it lifts (every key must be up)"
    f.handle([(H.EV_KEY, H.BTN_TOUCH, 0), (H.EV_SYN, H.SYN_REPORT, 0)], source="pad", touchpad=True)
    assert f.active and states == [True]
    assert sent, "entering() placed the other computer's pointer"
    sent.clear()
    f.handle(pad_events(2000, 600, touch=1), source="pad", touchpad=True)  # the next touch moves the other computer's pointer
    f.handle(pad_events(1800, 600), source="pad", touchpad=True)
    assert sent and sent[-1][0] == 1
