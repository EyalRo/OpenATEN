import pytest

from openaten import hid
from openaten.forward import Forwarder, parse_hotkey

CTRL, ALT, SCROLL, A = 29, 56, 70, 30


def press(code):
    return (hid.EV_KEY, code, 1)


def release(code):
    return (hid.EV_KEY, code, 0)


def chord(f):
    f.handle([press(CTRL), press(ALT), press(SCROLL), release(SCROLL), release(ALT), release(CTRL)])


def make():
    sent, states = [], []
    return Forwarder(sent.append, on_state=states.append), sent, states


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
    assert f.active and states == [True] and sent == []  # the chord itself produces no reports
    f.handle([press(A), release(A)])
    assert sent == [bytes([0, 0, 0x04, 0, 0, 0, 0, 0]), bytes(8)]
    chord(f)
    assert not f.active and states == [True, False]


def test_toggle_off_releases_held_keys_and_buttons():
    f, sent, states = make()
    chord(f)
    f.handle([press(A), (hid.EV_KEY, hid.BTN_LEFT, 1), (hid.EV_SYN, 0, 0)])
    sent.clear()
    chord(f)
    assert bytes(8) in sent and bytes(5) in sent  # keyboard and mouse released


def test_mouse_motion_is_forwarded_when_active():
    f, sent, _ = make()
    chord(f)
    f.handle([(hid.EV_REL, hid.REL_X, 4), (hid.EV_REL, hid.REL_Y, 2), (hid.EV_SYN, 0, 0)])
    assert sent == [bytes([0, 4, 2, 0, 0])]


def test_partial_chord_does_not_toggle():
    f, sent, states = make()
    f.handle([press(ALT), press(SCROLL), release(SCROLL), release(ALT)])  # ctrl missing
    assert not f.active and states == []


def test_stop_always_turns_off():
    f, sent, states = make()
    chord(f)
    f.stop()
    assert not f.active and states == [True, False]
    f.stop()
    assert states == [True, False]
