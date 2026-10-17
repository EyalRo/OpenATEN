import pytest

from openaten import hid
from openaten.forward import Forwarder, parse_hotkey

CTRL, ALT, SCROLL, A = 29, 56, 70, 30


def press(code):
    return (hid.EV_KEY, code, 1)


def release(code):
    return (hid.EV_KEY, code, 0)


CHORD_DOWN = [press(CTRL), press(ALT), press(SCROLL)]
CHORD_UP = [release(SCROLL), release(ALT), release(CTRL)]


def chord(f):
    """A complete hotkey press: down and up."""
    f.handle(CHORD_DOWN + CHORD_UP)


def make(keys_down=None):
    sent, states = [], []
    return Forwarder(sent.append, on_state=states.append, keys_down=keys_down), sent, states


def test_parse_hotkey():
    codes, trigger = parse_hotkey("ctrl+alt+scrolllock")
    assert codes == {29, 56, 70} and trigger == 70
    with pytest.raises(ValueError):
        parse_hotkey("scrolllock")
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+bogus")


def test_nothing_is_forwarded_until_toggled_on():
    f, sent, states = make()
    f.handle([press(A), release(A)])
    assert sent == [] and states == []


def test_hotkey_toggles_and_is_not_forwarded():
    f, sent, states = make()
    chord(f)
    assert f.forwarding and states == [True] and sent == []  # the chord itself produces no reports
    f.handle([press(A), release(A)])
    assert sent == [bytes([2, 0, 0x04, 0, 0, 0, 0, 0]), bytes([2, 0, 0, 0, 0, 0, 0, 0])]
    chord(f)
    assert not f.forwarding and states == [True, False]


def test_grab_waits_until_every_key_is_up():
    """Grabbing while a key is down would leave the desktop with a stuck key."""
    f, sent, states = make()
    f.handle(CHORD_DOWN)
    assert f.desired and not f.active and states == []  # requested, but ctrl/alt/scrolllock are still down
    f.handle([release(SCROLL), release(ALT)])
    assert states == []  # ctrl still down
    f.handle([release(CTRL)])
    assert f.active and states == [True]


def test_ungrab_waits_until_every_key_is_up_but_peer_is_released_at_once():
    f, sent, states = make()
    chord(f)
    f.handle([press(A), (hid.EV_KEY, hid.BTN_LEFT, 1), (hid.EV_SYN, 0, 0)])
    sent.clear()
    f.handle(CHORD_DOWN)  # turn off
    assert bytes([2, 0, 0, 0, 0, 0, 0, 0]) in sent and bytes([1, 0, 0, 0, 0]) in sent  # the other computer's keys/buttons are released at once
    assert f.active and states == [True]  # ...but the grab stays while keys are physically down
    f.handle(CHORD_UP + [release(A)])
    assert f.active  # the mouse button is still down
    f.handle([(hid.EV_KEY, hid.BTN_LEFT, 0), (hid.EV_SYN, 0, 0)])
    assert not f.active and states == [True, False]


def test_events_between_request_and_grab_are_not_forwarded():
    f, sent, states = make()
    f.handle(CHORD_DOWN + [press(A)])  # 'a' pressed before the grab was applied
    f.handle(CHORD_UP + [release(A)])
    assert f.active and sent == []  # the 'a' stayed local


def test_keys_already_down_at_start_delay_the_grab():
    down = {"v": True}
    f, sent, states = make(keys_down=lambda: down["v"])
    chord(f)
    assert f.desired and not f.active  # the kernel reports a key held that we never saw
    down["v"] = False
    f.tick()
    assert f.active and states == [True]


def test_mouse_motion_is_forwarded_when_active():
    f, sent, _ = make()
    chord(f)
    f.handle([(hid.EV_REL, hid.REL_X, 4), (hid.EV_REL, hid.REL_Y, 2), (hid.EV_SYN, 0, 0)])
    assert sent == [bytes([1, 0, 0, 4, 2])]


def test_partial_chord_does_not_toggle():
    f, sent, states = make()
    f.handle([press(ALT), press(SCROLL), release(SCROLL), release(ALT)])  # ctrl missing
    assert not f.desired and states == []


def test_stop_always_releases_even_with_keys_down():
    f, sent, states = make()
    chord(f)
    f.handle([press(A)])
    sent.clear()
    f.stop()
    assert not f.active and states == [True, False] and bytes([2, 0, 0, 0, 0, 0, 0, 0]) in sent
    f.stop()
    assert states == [True, False]


def test_touchpad_events_are_forwarded_as_mouse_motion_and_block_the_grab_while_touching():
    from openaten.hid import ABS_X, ABS_Y, BTN_TOUCH, EV_ABS

    f, sent, states = make()
    chord(f)
    assert f.forwarding
    f.handle([(hid.EV_KEY, BTN_TOUCH, 1), (EV_ABS, ABS_X, 10), (EV_ABS, ABS_Y, 10), (hid.EV_SYN, 0, 0),
              (EV_ABS, ABS_X, 30), (EV_ABS, ABS_Y, 10), (hid.EV_SYN, 0, 0)], source="pad", touchpad=True)
    assert sent and sent[-1][3] > 0  # moved right (dx is the fourth byte)
    sent.clear()
    f.handle(CHORD_DOWN)  # toggle off while the finger is still on the pad
    f.handle(CHORD_UP)
    assert f.active  # grab stays until the finger lifts
    f.handle([(hid.EV_KEY, BTN_TOUCH, 0), (hid.EV_SYN, 0, 0)], source="pad", touchpad=True)
    assert not f.active and states == [True, False]
