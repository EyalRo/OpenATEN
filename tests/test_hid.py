from openaten import hid
from openaten.hid import EV_KEY, EV_REL, EV_SYN, Keyboard, Mouse, Translator


def test_letters_and_digits_map_to_usages():
    k = Keyboard()
    assert k.event(30, 1) == bytes([0, 0, 0x04, 0, 0, 0, 0, 0])  # KEY_A
    assert k.event(44, 1) == bytes([0, 0, 0x04, 0x1D, 0, 0, 0, 0])  # + KEY_Z
    assert k.event(30, 0) == bytes([0, 0, 0x1D, 0, 0, 0, 0, 0])
    assert k.event(2, 1)[2:4] == bytes([0x1D, 0x1E])  # KEY_1
    assert hid.KEY_TO_USAGE[11] == 0x27  # KEY_0


def test_modifiers_are_bits_not_keys():
    k = Keyboard()
    assert k.event(29, 1) == bytes([0x01, 0, 0, 0, 0, 0, 0, 0])  # left ctrl
    assert k.event(42, 1)[0] == 0x03  # + left shift
    assert k.event(30, 1) == bytes([0x03, 0, 0x04, 0, 0, 0, 0, 0])
    assert k.event(29, 0)[0] == 0x02


def test_autorepeat_and_unknown_keys_produce_nothing():
    k = Keyboard()
    k.event(30, 1)
    assert k.event(30, 2) is None
    assert k.event(0x2FF, 1) is None
    assert k.event(30, 1) is None  # already held


def test_rollover_when_more_than_six_keys():
    k = Keyboard()
    for code in (30, 48, 46, 32, 18, 33):
        k.event(code, 1)
    assert len(k.keys) == 6
    assert k.event(34, 1) == bytes([0, 0] + [hid.ROLLOVER] * 6)
    assert k.event(34, 0) is not None and hid.ROLLOVER not in k.report()


def test_mouse_motion_buttons_and_splitting():
    t = Translator()
    out = list(t.feed([(EV_REL, hid.REL_X, 5), (EV_REL, hid.REL_Y, -3), (EV_SYN, 0, 0)]))
    assert out == [bytes([0, 5, 0xFD, 0, 0])]
    out = list(t.feed([(EV_KEY, hid.BTN_LEFT, 1), (EV_SYN, 0, 0)]))
    assert out == [bytes([1, 0, 0, 0, 0])]
    out = list(t.feed([(EV_REL, hid.REL_X, 300), (EV_SYN, 0, 0)]))
    assert [r[1] for r in out] == [127, 127, 46] and all(r[0] == 1 for r in out)
    assert list(t.feed([(EV_SYN, 0, 0)])) == []  # nothing pending


def test_wheel_and_pan():
    t = Translator()
    out = list(t.feed([(EV_REL, hid.REL_WHEEL, -1), (EV_REL, hid.REL_HWHEEL, 2), (EV_SYN, 0, 0)]))
    assert out == [bytes([0, 0, 0, 0xFF, 2])]


def test_mouse_buttons_do_not_reach_the_keyboard():
    t = Translator()
    assert list(t.feed([(EV_KEY, hid.BTN_RIGHT, 1)])) == []
    assert t.mouse.buttons == 2 and t.keyboard.keys == []


def test_release_all_clears_held_state():
    t = Translator()
    list(t.feed([(EV_KEY, 29, 1), (EV_KEY, 30, 1), (EV_KEY, hid.BTN_LEFT, 1), (EV_SYN, 0, 0)]))
    rel = t.release_all()
    assert bytes(8) in rel and bytes(5) in rel
    assert t.release_all() == []
