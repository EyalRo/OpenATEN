from openaten import hid
from openaten.hid import EV_KEY, EV_REL, EV_SYN, Keyboard, Translator


def test_letters_and_digits_map_to_usages():
    k = Keyboard()
    assert k.event(30, 1) == bytes([2, 0, 0x04, 0, 0, 0, 0, 0])  # KEY_A
    assert k.event(44, 1) == bytes([2, 0, 0x04, 0x1D, 0, 0, 0, 0])  # + KEY_Z
    assert k.event(30, 0) == bytes([2, 0, 0x1D, 0, 0, 0, 0, 0])
    assert k.event(2, 1)[2:4] == bytes([0x1D, 0x1E])  # KEY_1
    assert hid.KEY_TO_USAGE[11] == 0x27  # KEY_0


def test_modifiers_are_bits_not_keys():
    k = Keyboard()
    assert k.event(29, 1) == bytes([2, 0x01, 0, 0, 0, 0, 0, 0])  # left ctrl
    assert k.event(42, 1)[1] == 0x03  # + left shift
    assert k.event(30, 1) == bytes([2, 0x03, 0x04, 0, 0, 0, 0, 0])
    assert k.event(29, 0)[1] == 0x02


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
    assert k.event(34, 1) == bytes([2, 0] + [hid.ROLLOVER] * 6)
    assert k.event(34, 0) is not None and hid.ROLLOVER not in k.report()


def test_mouse_motion_buttons_and_splitting():
    t = Translator()
    out = list(t.feed([(EV_REL, hid.REL_X, 5), (EV_REL, hid.REL_Y, -3), (EV_SYN, 0, 0)]))
    assert out == [bytes([1, 0, 0, 5, 0xFD])]
    out = list(t.feed([(EV_KEY, hid.BTN_LEFT, 1), (EV_SYN, 0, 0)]))
    assert out == [bytes([1, 1, 0, 0, 0])]
    out = list(t.feed([(EV_REL, hid.REL_X, 300), (EV_SYN, 0, 0)]))
    assert [r[3] for r in out] == [127, 127, 46] and all(r[1] == 1 for r in out)
    assert list(t.feed([(EV_SYN, 0, 0)])) == []  # nothing pending


def test_wheel_is_reported_and_horizontal_scroll_is_not_supported():
    t = Translator()
    out = list(t.feed([(EV_REL, hid.REL_WHEEL, -1), (EV_REL, hid.REL_HWHEEL, 2), (EV_SYN, 0, 0)]))
    assert out == [bytes([1, 0, 0xFF, 0, 0])]  # [id, buttons, wheel, dx, dy]; the pan is dropped


def test_mouse_buttons_do_not_reach_the_keyboard():
    t = Translator()
    assert list(t.feed([(EV_KEY, hid.BTN_RIGHT, 1)])) == []
    assert t.mouse.buttons == 2 and t.keyboard.keys == []


def test_release_all_clears_held_state():
    t = Translator()
    list(t.feed([(EV_KEY, 29, 1), (EV_KEY, 30, 1), (EV_KEY, hid.BTN_LEFT, 1), (EV_SYN, 0, 0)]))
    rel = t.release_all()
    assert bytes([2, 0, 0, 0, 0, 0, 0, 0]) in rel and bytes([1, 0, 0, 0, 0]) in rel
    assert t.release_all() == []


# --- touchpad -----------------------------------------------------------------------------------------------------------------------
from openaten.hid import ABS_X, ABS_Y, BTN_TOOL_DOUBLETAP, BTN_TOUCH, EV_ABS, Touchpad  # noqa: E402


def pad_events(*points, touch=True):
    out = [(EV_KEY, BTN_TOUCH, 1)] if touch else []
    for x, y in points:
        out += [(EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y), (EV_SYN, 0, 0)]
    return out


def test_touchpad_motion_becomes_relative_mouse_motion():
    clock = {"t": 0.0}
    pad = Touchpad(speed=1.0, now=lambda: clock["t"])
    reports = list(pad.feed(pad_events((100, 100), (110, 95), (130, 95))))
    # first sample anchors; then dx=10,dy=-5 and dx=20,dy=0
    assert reports == [bytes([1, 0, 0, 10, 0xFB]), bytes([1, 0, 0, 20, 0])]


def test_touchpad_speed_scales_and_keeps_fractions():
    pad = Touchpad(speed=0.5, now=lambda: 0.0)
    out = list(pad.feed(pad_events((0, 0), (1, 0), (2, 0), (3, 0), (4, 0))))
    assert sum(r[3] for r in out) == 2  # 4 units * 0.5, no drift from rounding


def test_touchpad_tap_is_a_click_but_a_drag_is_not():
    t = {"v": 0.0}
    pad = Touchpad(speed=1.0, now=lambda: t["v"])
    list(pad.feed(pad_events((50, 50))))
    t["v"] = 0.1
    out = list(pad.feed([(EV_KEY, BTN_TOUCH, 0), (EV_SYN, 0, 0)]))
    assert out == [bytes([1, 1, 0, 0, 0]), bytes([1, 0, 0, 0, 0])]
    list(pad.feed(pad_events((50, 50), (200, 200))))
    t["v"] = 0.2
    assert list(pad.feed([(EV_KEY, BTN_TOUCH, 0), (EV_SYN, 0, 0)])) == []  # moved too far: not a tap


def test_touchpad_two_finger_scroll():
    pad = Touchpad(now=lambda: 0.0)
    list(pad.feed([(EV_KEY, BTN_TOOL_DOUBLETAP, 1)] + pad_events((100, 100), (100, 100 + 2 * Touchpad.SCROLL_UNITS_PER_STEP))))
    out = list(pad.feed([(EV_ABS, ABS_Y, 100 + 2 * Touchpad.SCROLL_UNITS_PER_STEP), (EV_SYN, 0, 0)]))
    assert out == []  # no further movement
    pad2 = Touchpad(now=lambda: 0.0)
    out = list(pad2.feed([(EV_KEY, BTN_TOOL_DOUBLETAP, 1)] + pad_events((100, 100), (100, 100 + 2 * Touchpad.SCROLL_UNITS_PER_STEP))))
    assert out and out[0][2] == (-2) & 0xFF  # wheel (third byte) moved by two steps


def test_touchpad_button_is_left_click_and_release_all_clears_it():
    pad = Touchpad(now=lambda: 0.0)
    out = list(pad.feed([(EV_KEY, hid.BTN_LEFT, 1), (EV_SYN, 0, 0)]))
    assert out == [bytes([1, 1, 0, 0, 0])]
    assert pad.release_all() == [bytes([1, 0, 0, 0, 0])]
