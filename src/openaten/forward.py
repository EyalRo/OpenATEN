"""Forward this computer's keyboard and mouse to the other computer through the bridge's HID interface.

The other computer sees an ordinary USB keyboard and mouse (its OpenATEN agent is only needed for hand-off at screen edges). A hotkey toggles forwarding; while it is on, the local
devices are grabbed (so nothing reaches this desktop) and every event becomes a HID report.

:class:`Forwarder` is the testable core (no device access). :func:`run_evdev` is the thin Linux layer around it.
"""
from __future__ import annotations

import logging
import select
import signal
import time
from collections.abc import Callable, Iterable
from typing import Any

from . import hid as H
from .edges import EdgeSwitch

log = logging.getLogger(__name__)

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
DEFAULT_HOTKEY = "ctrl+alt+shift+k"


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
        on_state: Callable[[bool], None] | None = None,
        keys_down: Callable[[], bool] | None = None,
        edges: EdgeSwitch | None = None,
    ):
        self.send_report = send_report
        self.edges = edges
        self.on_state = on_state or (lambda grabbed: None)
        self.keys_down = keys_down or (lambda: False)
        self.chord, self.trigger = parse_hotkey(hotkey)
        self._swallow_trigger = False  # the press of the trigger that toggled is still down: swallow its repeats and release
        self.desired = False  # what the user asked for
        self.active = False  # devices are grabbed (the request has been applied)
        self._held: set[int] = set()  # physical keys currently down (tracked from events)
        self._translator = H.Translator()
        self._pads: dict[object, H.Touchpad] = {}

    @property
    def forwarding(self) -> bool:
        return self.desired and self.active

    def request(self, on: bool) -> None:
        if on == self.desired:
            return
        self.desired = on
        if not on and self.active:
            self._release_peer()
        self.tick()

    def _release_peer(self) -> None:
        """The other computer must never be left with keys or buttons held."""
        for report in self._translator.release_all():
            self.send_report(report)
        for pad in self._pads.values():
            for report in pad.release_all():
                self.send_report(report)

    def tick(self) -> None:
        """Apply a pending on/off request once nothing is physically held."""
        if self.desired == self.active or self._held or self.keys_down():
            return
        if self.desired:
            self._translator = H.Translator()
            self._pads = {}
        self.active = self.desired
        self.on_state(self.active)
        if self.edges is not None:
            if self.active:
                for report in self.edges.entering():  # put the other pointer at the border we crossed
                    self.send_report(report)
            else:
                self.edges.leaving()

    def handle(self, events: Iterable[tuple[int, int, int]], source: object = None, touchpad: bool = False) -> None:
        """Process one batch from one device. ``touchpad`` marks absolute pointing devices, which keep per-device state."""
        if self.edges is not None and not self.desired and not self.active:
            if not touchpad and self.edges.local_motion(events):
                self.request(True)  # the pointer pushed through the border
                events = [e for e in events if e[0] != H.EV_REL]  # the push itself must not move the other pointer
            elif touchpad and self.edges.local_touchpad(events, source):
                self.request(True)
                events = []  # the touch that pushed through is consumed; forwarding starts with the next touch
        forward: list[tuple[int, int, int]] = []
        for etype, code, value in events:
            if etype == H.EV_KEY and code == self.trigger and self._swallow_trigger:
                # The press that toggled is never forwarded, nor are its auto-repeats or its release. Only that press: the trigger is often a
                # typing key (the default hotkey ends in k), and every other press of it is an ordinary key for the other computer.
                if value == 0:
                    self._swallow_trigger = False
                    self._held.discard(code)
                continue
            if etype == H.EV_KEY and value in (0, 1):
                (self._held.add if value else self._held.discard)(code)
                if value == 1 and code == self.trigger and self.chord <= self._held:
                    self._swallow_trigger = True
                    self.request(not self.desired)
                    continue  # the toggle press itself is never forwarded
            if self.forwarding:
                forward.append((etype, code, value))
        if forward:
            translator = self._pads.setdefault(source, H.Touchpad()) if touchpad else self._translator
            for report in translator.feed(forward):
                if not self.forwarding:
                    break
                self.send_report(report)
                if self.edges is not None and self.edges.remote_report(report):
                    self.request(False)  # the other pointer pushed back through the border
        self.tick()

    def stop(self) -> None:
        """Turn everything off immediately (shutdown path): release the other computer's keys and the grab."""
        if self.active:
            self._release_peer()
        self.desired = False
        if self.active:
            self.active = False
            self.on_state(False)


def _device_kind(dev, exclude_vendors: set[int]) -> str | None:
    """'keyboard', 'mouse', 'touchpad' or None (ignore). The bridge's own HID devices are always ignored."""
    import evdev

    if dev.info.vendor in exclude_vendors:  # never read the bridge's own HID devices (would loop in loopback setups)
        return None
    caps = dev.capabilities()
    keys = caps.get(evdev.ecodes.EV_KEY, [])
    rels = caps.get(evdev.ecodes.EV_REL, [])
    abss = [a[0] if isinstance(a, tuple) else a for a in caps.get(evdev.ecodes.EV_ABS, [])]
    if evdev.ecodes.KEY_A in keys and evdev.ecodes.KEY_SPACE in keys:
        return "keyboard"
    if evdev.ecodes.REL_X in rels and evdev.ecodes.BTN_LEFT in keys:
        return "mouse"
    if evdev.ecodes.ABS_X in abss and evdev.ecodes.ABS_Y in abss and evdev.ecodes.BTN_TOUCH in keys:
        return "touchpad"
    return None


def _held_now(dev) -> bool:
    """True if the kernel says any key or button on this device is currently down."""
    try:
        return bool(dev.active_keys())
    except OSError:
        return False


class EvdevRunner:
    """Reads the local keyboards, mice and touchpads with evdev and drives a :class:`Forwarder`.

    Step-based so a daemon can run it from its own loop: call :meth:`step` repeatedly, :meth:`request` to switch forwarding, :meth:`close` at the
    end. A failing ``send_report`` (for example the bridge was unplugged) returns control immediately and is reported in :meth:`status`.
    """

    def __init__(
        self,
        send_report: Callable[[bytes], None],
        hotkey: str = DEFAULT_HOTKEY,
        dry_run: bool = False,
        max_active: float | None = None,
        exclude_vendors: set[int] | None = None,
        edges: EdgeSwitch | None = None,
    ):
        self.edges = edges
        self.dry_run, self.max_active, self.hotkey = dry_run, max_active, hotkey
        self._exclude = {0x05E3, 0x0557} | (exclude_vendors or set())
        self._send_report = send_report
        self.devices: list = []
        self.kinds: dict[int, str] = {}
        self._by_fd: dict[int, Any] = {}
        self._grabbed: list = []
        self._active_since = 0.0
        self._abort = False
        self.error: str | None = None
        send = (lambda r: log.info("report %s", r.hex(" "))) if dry_run else self._send
        self.fwd = Forwarder(send, hotkey, self._on_state, keys_down=lambda: any(_held_now(d) for d in self.devices), edges=edges)

    # -- devices ----------------------------------------------------------------------
    def open(self) -> bool:
        """(Re)scan for local input devices. Returns False if none are readable."""
        import evdev

        self.devices, self.kinds, self._by_fd = [], {}, {}
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            kind = _device_kind(dev, self._exclude)
            if kind:
                self.devices.append(dev)
                self.kinds[dev.fd] = kind
                self._by_fd[dev.fd] = dev
                log.info("watching %s (%s) as %s", dev.name, dev.path, kind)
        if not self.devices:
            log.warning("no readable keyboard/mouse devices found (are you in the 'input' group?)")
        return bool(self.devices)

    # -- control ----------------------------------------------------------------------
    def request(self, on: bool | None = None) -> None:
        """Turn forwarding on or off; ``None`` toggles. The change applies once every key is physically up."""
        on = (not self.fwd.desired) if on is None else on
        if on:
            self.error = None
        self.fwd.request(on)

    def border_pushed(self, key: str, row: float) -> None:
        """An external edge source (the strip) reports the pointer pushed through border ``key`` at ``row``: start forwarding."""
        if self.edges is not None and self.edges.border_pushed(key, row):
            self.fwd.request(True)

    def status(self) -> dict:
        return {
            "forwarding": self.fwd.forwarding,
            "pending": self.fwd.desired != self.fwd.active,
            "devices": [d.name for d in self.devices],
            "hotkey": self.hotkey,
            "edges": self.edges.state if self.edges is not None else "off",
            "error": self.error,
        }

    def _send(self, report: bytes) -> None:
        if self._abort:
            return
        try:
            self._send_report(report)
        except Exception as e:  # bridge unplugged, firmware refusing...: give control back instead of dying
            self.error = f"injection failed: {e}"
            log.error("%s", self.error)
            self._abort = True

    def _on_state(self, grabbed: bool) -> None:
        if grabbed:
            if not self.dry_run:
                for d in self.devices:
                    try:
                        d.grab()
                        self._grabbed.append(d)
                    except OSError as e:
                        log.warning("could not grab %s: %s", d.name, e)
                        self.error = f"could not grab {d.name}: {e} (is another openaten instance running?)"
                        self._abort = True  # step() returns control: never forward while the desktop still sees the input
                        return
            self.error = None  # a stale failure from an earlier session (hotkey and edge pushes do not go through request())
            self._active_since = time.monotonic()
            log.info("devices grabbed: forwarding ON  (press the hotkey again to return control)")
        else:
            while self._grabbed:
                try:
                    self._grabbed.pop().ungrab()
                except OSError:
                    pass
            log.info("devices released: forwarding OFF")

    # -- loop -------------------------------------------------------------------------
    def step(self, timeout: float = 0.25) -> None:
        """Wait up to ``timeout`` for input events, process them, apply pending changes and safety rules."""
        if self._by_fd:
            ready, _, _ = select.select(list(self._by_fd), [], [], timeout)
        else:
            ready = []
            time.sleep(timeout)
        for fd in ready:
            try:
                events = [(e.type, e.code, e.value) for e in self._by_fd[fd].read()]
            except OSError:
                self._by_fd.pop(fd, None)  # device unplugged
                continue
            self.fwd.handle(events, source=fd, touchpad=self.kinds[fd] == "touchpad")
        self.fwd.tick()
        if self._abort:  # a report could not be delivered: return control
            self._abort = False
            self.fwd.request(False)
            self._abort = False
        if self.fwd.forwarding and self.max_active and time.monotonic() - self._active_since > self.max_active:
            log.warning("safety timeout (%.0fs): returning control", self.max_active)
            self.fwd.request(False)

    def close(self) -> None:
        """Hand the devices back cleanly: keep the grab until every key is physically up (max 3 s), then release everything."""
        self.fwd.request(False)
        deadline = time.monotonic() + 3.0
        while self.fwd.active and time.monotonic() < deadline:
            # wake as soon as a device has events (a key release) instead of sleeping blindly; 0.02 s bounds the recheck
            ready = select.select(list(self._by_fd), [], [], 0.02)[0] if self._by_fd else []
            if not ready and not self._by_fd:
                time.sleep(0.02)
            for fd in list(self._by_fd):
                try:
                    list(self._by_fd[fd].read())
                except (OSError, BlockingIOError):
                    pass
            self.fwd.handle([])
        self.fwd.stop()


def run_evdev(
    send_report: Callable[[bytes], None],
    hotkey: str = DEFAULT_HOTKEY,
    dry_run: bool = False,
    max_active: float | None = None,
    exclude_vendors: set[int] | None = None,
    edges: EdgeSwitch | None = None,
) -> int:
    """Interactive forwarding: runs until interrupted (SIGINT/SIGTERM)."""
    runner = EvdevRunner(send_report, hotkey, dry_run, max_active, exclude_vendors, edges)
    if not runner.open():
        return 1
    stop = {"flag": False}
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.__setitem__("flag", True))
    log.info("hotkey: %s%s", hotkey, "  [dry run: devices are not grabbed, nothing is injected]" if dry_run else "")
    try:
        while not stop["flag"]:
            runner.step()
            if not runner._by_fd:
                log.warning("all input devices disappeared")
                return 1
    finally:
        runner.close()
    return 0
