"""EvdevRunner against fake devices (real pipe fds so select() works) and a fake ``evdev`` module."""
import os
import sys
import types
from types import SimpleNamespace

from openaten import forward, hid

CTRL, ALT, SCROLL, A = 29, 56, 70, 30
HOTKEY = "ctrl+alt+scrolllock"


class FakeDevice:
    def __init__(self, name="kbd", vendor=0x1234, caps=None):
        self.name, self.path, self.info = name, f"/dev/input/{name}", SimpleNamespace(vendor=vendor)
        self._r, self._w = os.pipe()
        self.fd = self._r
        self._queue: list[SimpleNamespace] = []
        self.grabbed = self.fail_grab = self.unplugged = False
        self.held = False
        self._caps = caps or {}

    def push(self, *events):
        self._queue += [SimpleNamespace(type=t, code=c, value=v) for t, c, v in events]
        os.write(self._w, b"x")

    def read(self):
        if self.unplugged:
            raise OSError("No such device")
        out, self._queue = self._queue, []
        try:
            os.read(self._r, 64)
        except BlockingIOError:
            pass
        return out

    def grab(self):
        if self.fail_grab:
            raise OSError("Device or resource busy")
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def active_keys(self):
        return [A] if self.held else []

    def capabilities(self):
        return self._caps

    def close(self):
        os.close(self._r)
        os.close(self._w)


def make(max_active=None):
    sent = []
    runner = forward.EvdevRunner(sent.append, HOTKEY, max_active=max_active)
    kbd = FakeDevice()
    runner.devices, runner.kinds, runner._by_fd = [kbd], {kbd.fd: "keyboard"}, {kbd.fd: kbd}
    return runner, kbd, sent


def toggle(kbd):
    kbd.push((hid.EV_KEY, CTRL, 1), (hid.EV_KEY, ALT, 1), (hid.EV_KEY, SCROLL, 1), (hid.EV_KEY, SCROLL, 0), (hid.EV_KEY, ALT, 0), (hid.EV_KEY, CTRL, 0))


def test_hotkey_grabs_and_forwards_then_releases():
    runner, kbd, sent = make()
    toggle(kbd)
    runner.step(0.05)
    assert kbd.grabbed and runner.status()["forwarding"]
    kbd.push((hid.EV_KEY, A, 1), (hid.EV_KEY, A, 0))
    runner.step(0.05)
    assert sent, "key reports were injected while forwarding"
    toggle(kbd)
    runner.step(0.05)
    assert not kbd.grabbed and not runner.status()["forwarding"]
    kbd.close()


def test_failed_grab_returns_control_and_reports_the_error():
    runner, kbd, sent = make()
    kbd.fail_grab = True
    toggle(kbd)
    runner.step(0.05)
    runner.step(0.05)
    assert not runner.status()["forwarding"] and "could not grab" in (runner.status()["error"] or "")
    kbd.close()


def test_injection_failure_aborts_forwarding_instead_of_dying():
    runner, kbd, _ = make()

    def boom(report):
        raise RuntimeError("bridge unplugged")

    runner._send_report = boom
    toggle(kbd)
    runner.step(0.05)
    kbd.push((hid.EV_KEY, A, 1), (hid.EV_KEY, A, 0))
    runner.step(0.05)
    runner.step(0.05)
    assert not runner.status()["forwarding"] and "injection failed" in runner.status()["error"]
    assert not kbd.grabbed
    kbd.close()


def test_unplugged_device_is_dropped_from_the_loop():
    runner, kbd, _ = make()
    kbd.unplugged = True
    kbd.push((hid.EV_KEY, A, 1))
    runner.step(0.05)
    assert not runner._by_fd
    kbd.close()


def test_safety_timeout_returns_control(monkeypatch):
    runner, kbd, _ = make(max_active=5)
    toggle(kbd)
    runner.step(0.05)
    assert runner.status()["forwarding"]
    base = runner._active_since
    monkeypatch.setattr(forward.time, "monotonic", lambda: base + 6)
    runner.step(0.01)
    assert not runner.status()["forwarding"]
    kbd.close()


def test_request_toggles_and_clears_the_error():
    runner, kbd, _ = make()
    runner.error = "stale"
    runner.request(True)
    runner.step(0.01)
    assert runner.status()["forwarding"] and runner.error is None
    runner.request()
    runner.step(0.01)
    assert not runner.status()["forwarding"]
    kbd.close()


def test_close_releases_the_grab_once_keys_are_up():
    runner, kbd, _ = make()
    runner.request(True)
    runner.step(0.01)
    assert kbd.grabbed
    runner.close()
    assert not kbd.grabbed
    kbd.close()


def test_open_picks_keyboards_and_mice_and_skips_the_bridge(monkeypatch):
    ecodes = SimpleNamespace(EV_KEY=1, EV_REL=2, EV_ABS=3, KEY_A=A, KEY_SPACE=57, REL_X=0, BTN_LEFT=272, ABS_X=0, ABS_Y=1, BTN_TOUCH=330)
    kbd = FakeDevice("kbd", caps={1: [A, 57]})
    mouse = FakeDevice("mouse", caps={1: [272], 2: [0]})
    bridge = FakeDevice("bridge", vendor=0x05E3, caps={1: [A, 57]})
    by_path = {d.path: d for d in (kbd, mouse, bridge)}
    fake = types.ModuleType("evdev")
    fake.ecodes, fake.list_devices, fake.InputDevice = ecodes, lambda: list(by_path), lambda path: by_path[path]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "evdev", fake)
    runner = forward.EvdevRunner(lambda r: None, HOTKEY)
    assert runner.open()
    assert sorted(runner.status()["devices"]) == ["kbd", "mouse"]
    assert set(runner.kinds.values()) == {"keyboard", "mouse"}
    by_path.clear()
    assert not runner.open()  # nothing readable
    for d in (kbd, mouse, bridge):
        d.close()
