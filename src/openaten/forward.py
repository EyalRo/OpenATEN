"""Forward this computer's keyboard and mouse to the other computer through the bridge's HID interface.

The other computer needs no software: it sees an ordinary USB keyboard and mouse. A hotkey toggles forwarding; while it is on, the local
devices are grabbed (so nothing reaches this desktop) and every event becomes a HID report.

:class:`Forwarder` is the testable core (no device access). :func:`run_evdev` is the thin Linux layer around it.
"""
from __future__ import annotations

import select
import signal
import time
from typing import Callable, Iterable, Optional

from . import hid as H

KEY_NAMES = {
    "ctrl": 29, "leftctrl": 29, "rightctrl": 97, "shift": 42, "leftshift": 42, "rightshift": 54,
    "alt": 56, "leftalt": 56, "rightalt": 100, "meta": 125, "leftmeta": 125, "super": 125, "rightmeta": 126,
    "scrolllock": 70, "pause": 119, "sysrq": 99, "printscreen": 99, "insert": 110, "space": 57,
    "esc": 1, "tab": 15, "enter": 28, "backspace": 14, "grave": 41, "bracketleft": 26, "bracketright": 27,
    **{f"f{i}": c for i, c in zip(range(1, 11), range(59, 69))}, "f11": 87, "f12": 88,
    **{ch: code for ch, code in zip("qwertyuiop", range(16, 26))},
    **{ch: code for ch, code in zip("asdfghjkl", range(30, 39))},
    **{ch: code for ch, code in zip("zxcvbnm", range(44, 51))},
}
DEFAULT_HOTKEY = "ctrl+alt+scrolllock"


def parse_hotkey(spec: str) -> tuple[frozenset[int], int]:
    """'ctrl+alt+scrolllock' -> (all key codes, trigger code). The trigger is the last key: pressing it completes the chord."""
    names = [n.strip().lower() for n in spec.split("+") if n.strip()]
    if len(names) < 2:
        raise ValueError("a hotkey needs at least one modifier and one key, e.g. ctrl+alt+scrolllock")
    try:
        codes = [KEY_NAMES[n] for n in names]
    except KeyError as e:
        raise ValueError(f"unknown key name {e.args[0]!r}; known: {', '.join(sorted(KEY_NAMES))}") from None
    return frozenset(codes), codes[-1]


class Forwarder:
    """Feed it events with :meth:`handle`; HID reports go to ``send_report`` while forwarding is on.

    Turning forwarding on or off is *deferred until every key and button is physically up*. Grabbing a device while a key is down
    would leave the local desktop believing that key is still held (it never sees the release): sticky modifiers. ``on_state(grabbed)``
    is called when the devices should actually be grabbed or released. ``keys_down()`` may report extra physical state (for example
    keys that were already down before we started watching); call :meth:`tick` periodically so a pending change applies once they are up.
    """

    def __init__(
        self,
        send_report: Callable[[bytes], None],
        hotkey: str = DEFAULT_HOTKEY,
        on_state: Optional[Callable[[bool], None]] = None,
        keys_down: Optional[Callable[[], bool]] = None,
    ):
        self.send_report = send_report
        self.on_state = on_state or (lambda grabbed: None)
        self.keys_down = keys_down or (lambda: False)
        self.chord, self.trigger = parse_hotkey(hotkey)
        self.desired = False  # what the user asked for
        self.active = False  # devices are grabbed (the request has been applied)
        self._held: set[int] = set()  # physical keys currently down (tracked from events)
        self._translator = H.Translator()

    @property
    def forwarding(self) -> bool:
        return self.desired and self.active

    def request(self, on: bool) -> None:
        if on == self.desired:
            return
        self.desired = on
        if not on and self.active:
            for report in self._translator.release_all():  # the other computer must never be left with keys held
                self.send_report(report)
        self.tick()

    def tick(self) -> None:
        """Apply a pending on/off request once nothing is physically held."""
        if self.desired == self.active or self._held or self.keys_down():
            return
        if self.desired:
            self._translator = H.Translator()
        self.active = self.desired
        self.on_state(self.active)

    def handle(self, events: Iterable[tuple[int, int, int]]) -> None:
        forward: list[tuple[int, int, int]] = []
        for etype, code, value in events:
            if etype == H.EV_KEY and value in (0, 1):
                (self._held.add if value else self._held.discard)(code)
                if value == 1 and code == self.trigger and self.chord <= self._held:
                    self.request(not self.desired)
                    continue  # the toggle press itself is never forwarded
                if code == self.trigger:  # also swallow the trigger's release
                    continue
            if self.forwarding:
                forward.append((etype, code, value))
        if forward:
            for report in self._translator.feed(forward):
                self.send_report(report)
        self.tick()

    def stop(self) -> None:
        """Turn everything off immediately (shutdown path): release the other computer's keys and the grab."""
        if self.active:
            for report in self._translator.release_all():
                self.send_report(report)
        self.desired = False
        if self.active:
            self.active = False
            self.on_state(False)


def _is_input_device(dev, exclude_vendors: set[int]) -> bool:
    import evdev

    if dev.info.vendor in exclude_vendors:  # never read the bridge's own HID devices (would loop in loopback setups)
        return False
    caps = dev.capabilities()
    keys = caps.get(evdev.ecodes.EV_KEY, [])
    rels = caps.get(evdev.ecodes.EV_REL, [])
    is_keyboard = evdev.ecodes.KEY_A in keys and evdev.ecodes.KEY_SPACE in keys
    is_mouse = evdev.ecodes.REL_X in rels and evdev.ecodes.BTN_LEFT in keys
    return is_keyboard or is_mouse


def _held_now(dev) -> bool:
    """True if the kernel says any key or button on this device is currently down."""
    try:
        return bool(dev.active_keys())
    except OSError:
        return False


def run_evdev(
    send_report: Callable[[bytes], None],
    hotkey: str = DEFAULT_HOTKEY,
    dry_run: bool = False,
    max_active: float | None = None,
    exclude_vendors: set[int] | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Read all local keyboards/mice with evdev and forward while the hotkey toggle is on. Returns when interrupted."""
    import evdev

    exclude = {0x05E3, 0x0557} | (exclude_vendors or set())
    devices = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if _is_input_device(dev, exclude):
            devices.append(dev)
    if not devices:
        log("no readable keyboard/mouse devices found (are you in the 'input' group?)")
        return 1
    for d in devices:
        log(f"watching {d.name} ({d.path})")

    grabbed: list = []

    def on_state(active: bool) -> None:
        if active:
            if not dry_run:
                for d in devices:
                    try:
                        d.grab()
                        grabbed.append(d)
                    except OSError as e:
                        log(f"could not grab {d.name}: {e}")
            log("devices grabbed: forwarding ON  (press the hotkey again to return control)")
        else:
            while grabbed:
                try:
                    grabbed.pop().ungrab()
                except OSError:
                    pass
            log("devices released: forwarding OFF")

    send = (lambda r: log(f"report {r.hex(' ')}")) if dry_run else send_report
    fwd = Forwarder(send, hotkey, on_state, keys_down=lambda: any(_held_now(d) for d in devices))
    stop = {"flag": False}
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.__setitem__("flag", True))
    by_fd = {d.fd: d for d in devices}
    active_since = 0.0
    log(f"hotkey: {hotkey}" + ("  [dry run: devices are not grabbed, nothing is injected]" if dry_run else ""))
    try:
        while not stop["flag"]:
            ready, _, _ = select.select(list(by_fd), [], [], 0.25)
            for fd in ready:
                try:
                    events = [(e.type, e.code, e.value) for e in by_fd[fd].read()]
                except OSError:
                    by_fd.pop(fd, None)  # device unplugged
                    continue
                was = fwd.forwarding
                fwd.handle(events)
                if fwd.forwarding and not was:
                    active_since = time.monotonic()
            fwd.tick()  # applies a pending on/off once the physical keys are up
            if fwd.forwarding and max_active and time.monotonic() - active_since > max_active:
                log(f"safety timeout ({max_active:.0f}s): returning control")
                fwd.request(False)
            if not by_fd:
                log("all input devices disappeared")
                return 1
    finally:
        # Hand the devices back cleanly: stop forwarding, then keep the grab until every key is physically up so the desktop
        # never misses a release; give up after 3 s rather than hang.
        fwd.request(False)
        deadline = time.monotonic() + 3.0
        while fwd.active and time.monotonic() < deadline:
            for fd in list(by_fd):
                try:
                    list(by_fd[fd].read())
                except (OSError, BlockingIOError):
                    pass
            fwd.handle([])
            time.sleep(0.02)
        fwd.stop()
    return 0
