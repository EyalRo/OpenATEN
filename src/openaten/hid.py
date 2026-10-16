"""Translate Linux input events into the USB HID boot reports the bridge injects on the peer.

Pure logic, no device access: feed it ``(type, code, value)`` tuples (the shape evdev yields) and it returns reports.
Mouse report: 5 bytes (buttons, dx, dy, wheel, pan). Keyboard report: 8 bytes (modifiers, reserved, up to 6 key usages).
"""
from __future__ import annotations

from typing import Iterable, Iterator

# Linux event types / codes used here (linux/input-event-codes.h)
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114

MOUSE_BUTTON_BITS = {BTN_LEFT: 1, BTN_RIGHT: 2, BTN_MIDDLE: 4, BTN_SIDE: 8, BTN_EXTRA: 16}

MODIFIER_BITS = {
    29: 0x01, 42: 0x02, 56: 0x04, 125: 0x08,  # left ctrl, shift, alt, meta
    97: 0x10, 54: 0x20, 100: 0x40, 126: 0x80,  # right ctrl, shift, alt, meta
}

# Linux key code -> HID keyboard usage
KEY_TO_USAGE: dict[int, int] = {
    **{c: 0x04 + i for i, c in enumerate([30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44])},  # a..z
    **{c: 0x1E + i for i, c in enumerate(range(2, 11))},  # 1..9
    11: 0x27,  # 0
    28: 0x28, 1: 0x29, 14: 0x2A, 15: 0x2B, 57: 0x2C, 12: 0x2D, 13: 0x2E, 26: 0x2F, 27: 0x30, 43: 0x31,
    39: 0x33, 40: 0x34, 41: 0x35, 51: 0x36, 52: 0x37, 53: 0x38, 58: 0x39,
    **{c: 0x3A + i for i, c in enumerate(range(59, 69))},  # F1..F10
    87: 0x44, 88: 0x45, 99: 0x46, 70: 0x47, 119: 0x48, 110: 0x49, 102: 0x4A, 104: 0x4B, 111: 0x4C, 107: 0x4D, 109: 0x4E,
    106: 0x4F, 105: 0x50, 108: 0x51, 103: 0x52, 69: 0x53, 98: 0x54, 55: 0x55, 74: 0x56, 78: 0x57, 96: 0x58,
    **{c: 0x59 + i for i, c in enumerate([79, 80, 81, 75, 76, 77, 71, 72, 73])},  # keypad 1..9
    82: 0x62, 83: 0x63, 86: 0x64, 127: 0x65,
}

ROLLOVER = 0x01  # usage placed in every slot when more than six keys are held


class Keyboard:
    """Tracks held keys and produces an 8-byte report after each change."""

    def __init__(self) -> None:
        self.mods = 0
        self.keys: list[int] = []  # usages in press order

    def event(self, code: int, value: int) -> bytes | None:
        """Feed a key event (value 1 press, 0 release, 2 autorepeat). Returns a report if the state changed."""
        if value == 2:
            return None
        before = (self.mods, tuple(self.keys))
        if code in MODIFIER_BITS:
            self.mods = self.mods | MODIFIER_BITS[code] if value else self.mods & ~MODIFIER_BITS[code]
        elif code in KEY_TO_USAGE:
            usage = KEY_TO_USAGE[code]
            if value and usage not in self.keys:
                self.keys.append(usage)
            elif not value and usage in self.keys:
                self.keys.remove(usage)
        else:
            return None
        return self.report() if (self.mods, tuple(self.keys)) != before else None

    def report(self) -> bytes:
        slots = self.keys[:6] if len(self.keys) <= 6 else [ROLLOVER] * 6
        return bytes([self.mods, 0, *slots]).ljust(8, b"\0")

    def release_all(self) -> bytes | None:
        """Report with nothing held, or None if nothing was held."""
        if not self.mods and not self.keys:
            return None
        self.mods, self.keys = 0, []
        return self.report()


class Mouse:
    """Accumulates relative motion and button state; ``flush`` produces report(s) at the end of an input frame."""

    def __init__(self) -> None:
        self.buttons = 0
        self.dx = self.dy = self.wheel = self.pan = 0
        self._dirty = False

    def key_event(self, code: int, value: int) -> bool:
        bit = MOUSE_BUTTON_BITS.get(code)
        if bit is None or value == 2:
            return False
        new = self.buttons | bit if value else self.buttons & ~bit
        if new != self.buttons:
            self.buttons, self._dirty = new, True
        return True

    def rel_event(self, code: int, value: int) -> bool:
        if code == REL_X:
            self.dx += value
        elif code == REL_Y:
            self.dy += value
        elif code == REL_WHEEL:
            self.wheel += value
        elif code == REL_HWHEEL:
            self.pan += value
        else:
            return False
        self._dirty = True
        return True

    def flush(self) -> list[bytes]:
        """Reports for everything accumulated since the last flush (deltas beyond +-127 are split)."""
        if not self._dirty:
            return []
        out = []
        dx, dy, wheel, pan = self.dx, self.dy, self.wheel, self.pan
        self.dx = self.dy = self.wheel = self.pan = 0
        self._dirty = False
        while True:
            sx, sy = max(-127, min(127, dx)), max(-127, min(127, dy))
            sw, sp = max(-127, min(127, wheel)), max(-127, min(127, pan))
            out.append(bytes([self.buttons, sx & 0xFF, sy & 0xFF, sw & 0xFF, sp & 0xFF]))
            dx, dy, wheel, pan = dx - sx, dy - sy, wheel - sw, pan - sp
            if not (dx or dy or wheel or pan):
                return out

    def release_all(self) -> bytes | None:
        if not self.buttons:
            return None
        self.buttons = 0
        return bytes(5)


class Translator:
    """Combines a keyboard and a mouse; call :meth:`feed` with events, collect the returned HID reports."""

    def __init__(self) -> None:
        self.keyboard, self.mouse = Keyboard(), Mouse()

    def feed(self, events: Iterable[tuple[int, int, int]]) -> Iterator[bytes]:
        for etype, code, value in events:
            if etype == EV_KEY:
                if not self.mouse.key_event(code, value):
                    report = self.keyboard.event(code, value)
                    if report is not None:
                        yield report
            elif etype == EV_REL:
                self.mouse.rel_event(code, value)
            elif etype == EV_SYN and code == SYN_REPORT:
                yield from self.mouse.flush()

    def release_all(self) -> list[bytes]:
        """Reports that release every held key and button (call when forwarding stops)."""
        out = [r for r in (self.keyboard.release_all(), self.mouse.release_all()) if r is not None]
        return out
