"""Edge hand-off: decide from relative mouse motion when the pointer pushes through a border shared with the other computer.

Neither compositor tells us where the pointer is (niri has no pointer query, Wayland clients cannot read it) and the other computer runs no
software, so each side's pointer is *dead-reckoned* from relative motion in logical pixels. Clamping makes that self-correcting: a
pointer pushed into a screen edge ends up exactly on it whatever the drift. Until the first clamp the position is only known to lie in an
interval ("calibrating"); hand-off needs a known position, so push the pointer into a screen edge once after start-up.

The motion-to-pixel ``scale`` is only constant with a flat acceleration profile (niri: ``accel-profile "flat"``) on both computers.

Everything here is pure logic over screen geometry; :class:`EdgeSwitch` is what :class:`openaten.forward.Forwarder` talks to.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from . import hid as H

EPS = 0.5  # pixels: positions closer than this count as equal


@dataclass(frozen=True)
class Screen:
    """One output in the shared virtual coordinate space (logical pixels; y grows downwards)."""

    name: str
    host: str
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


@dataclass(frozen=True)
class Edge:
    """A border segment seen from one side: leaving across it means moving along ``axis`` (0 = x, 1 = y) in direction ``sign``."""

    axis: int
    sign: int
    line: float  # coordinate of the border along ``axis``
    lo: float  # extent of the shared segment along the other axis
    hi: float
    far: str  # name of the screen on the other side

    def mirror(self, far: str) -> "Edge":
        return Edge(self.axis, -self.sign, self.line, self.lo, self.hi, far)


def borders(screens: list[Screen], local_host: str) -> list[tuple[Edge, Edge]]:
    """Every border between a local and a remote screen, as ``(edge seen from local, edge seen from remote)``."""
    out = []
    for a in (s for s in screens if s.host == local_host):
        for b in (s for s in screens if s.host != local_host):
            x_lo, x_hi = max(a.x, b.x), min(a.right, b.right)  # overlap along x (for top/bottom borders)
            y_lo, y_hi = max(a.y, b.y), min(a.bottom, b.bottom)
            candidates = [
                (abs(b.x - a.right) < EPS, Edge(0, +1, a.right, y_lo, y_hi, b.name)),
                (abs(b.right - a.x) < EPS, Edge(0, -1, a.x, y_lo, y_hi, b.name)),
                (abs(b.y - a.bottom) < EPS, Edge(1, +1, a.bottom, x_lo, x_hi, b.name)),
                (abs(b.bottom - a.y) < EPS, Edge(1, -1, a.y, x_lo, x_hi, b.name)),
            ]
            for adjacent, edge in candidates:
                if adjacent and edge.hi - edge.lo > EPS:
                    out.append((edge, edge.mirror(a.name)))
    return out


def _nearest(screens: list[Screen], p: list[float]) -> list[float]:
    """``p`` if it lies on a screen, else the closest point of the closest screen."""
    best, best_d = p, math.inf
    for s in screens:
        q = [min(max(p[0], s.x), s.right), min(max(p[1], s.y), s.bottom)]
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        if d < best_d:
            best, best_d = q, d
    return list(best)


class PointerTracker:
    """Dead-reckoned pointer position on one computer's screens, plus push-through detection on a set of :class:`Edge` s."""

    def __init__(self, screens: list[Screen], scale: float, edges: list[Edge], push: float):
        self.screens, self.scale, self.edges, self.push_px = screens, scale, edges, push
        self.box = [min(s.x for s in screens), min(s.y for s in screens), max(s.right for s in screens), max(s.bottom for s in screens)]
        self.reset()

    def reset(self) -> None:
        """Forget the position: it could be anywhere on the screens until something clamps it."""
        self.lo, self.hi = [self.box[0], self.box[1]], [self.box[2], self.box[3]]
        self._push = [0.0] * len(self.edges)

    def set(self, point: list[float]) -> None:
        self.lo, self.hi = list(point), list(point)
        self._push = [0.0] * len(self.edges)

    @property
    def known(self) -> bool:
        return all(self.hi[a] - self.lo[a] <= EPS for a in (0, 1))

    def clear_push(self) -> None:
        self._push = [0.0] * len(self.edges)

    def move(self, dx: float, dy: float) -> int | None:
        """Apply a motion in device counts. Returns the index of the edge the pointer was pushed through, if any."""
        d = (dx * self.scale, dy * self.scale)
        was_known = self.known
        raw = [self.lo[a] + d[a] for a in (0, 1)]
        for a in (0, 1):
            self.lo[a] = min(max(self.lo[a] + d[a], self.box[a]), self.box[a + 2])
            self.hi[a] = min(max(self.hi[a] + d[a], self.box[a]), self.box[a + 2])
        if self.known:
            self.lo = _nearest(self.screens, self.lo)
            self.hi = list(self.lo)
        if not was_known:
            return None
        for i, e in enumerate(self.edges):
            over = e.sign * (raw[e.axis] - e.line)
            along = self.lo[1 - e.axis]
            if over > 0 and e.lo - EPS <= along <= e.hi + EPS:
                self._push[i] += min(over, e.sign * d[e.axis])
                if self._push[i] >= self.push_px:
                    self.clear_push()
                    return i
            elif e.sign * d[e.axis] < 0:
                self._push[i] = 0.0
        return None


class EdgeSwitch:
    """Hand-off between this computer's screens and the other's. See the module docstring for the model and its limits."""

    def __init__(self, screens: list[Screen], local_host: str, local_scale: float = 1.0, remote_scale: float = 1.0, push: float = 40.0):
        local = [s for s in screens if s.host == local_host]
        remote = [s for s in screens if s.host != local_host]
        if not local or not remote:
            raise ValueError(f"need screens on this computer ({local_host!r}) and on the other one")
        self.pairs = borders(screens, local_host)
        if not self.pairs:
            raise ValueError("no local screen shares a border with a remote screen: check x/y/width/height (edges must touch exactly)")
        self.local = PointerTracker(local, local_scale, [p[0] for p in self.pairs], push)
        self.remote = PointerTracker(remote, remote_scale, [p[1] for p in self.pairs], push)
        self._exit: tuple[int, float] | None = None  # border index and position along it where we left

    @property
    def state(self) -> str:
        return "ready" if self.local.known else "calibrating"

    def local_motion(self, events) -> bool:
        """Feed raw events of a mouse while the pointer is on this computer. True when it pushed through a border to the other computer."""
        for etype, code, value in events:
            if etype == H.EV_REL and code in (H.REL_X, H.REL_Y):
                i = self.local.move(value if code == H.REL_X else 0, value if code == H.REL_Y else 0)
                if i is not None:
                    edge = self.pairs[i][0]
                    self._exit = (i, self.local.lo[1 - edge.axis])
                    return True
        return False

    def entering(self) -> list[bytes]:
        """Called when forwarding starts: reports that put the other computer's pointer at the border we crossed (none after a hotkey)."""
        crossing, self._exit = self._exit, None
        self.remote.reset()
        self.local.clear_push()
        if crossing is None:
            return []
        i, along = crossing
        edge = self.pairs[i][1]
        entry = [0.0, 0.0]
        entry[edge.axis] = edge.line
        entry[1 - edge.axis] = min(max(along, edge.lo), edge.hi)
        # Slam into the top-left corner (the compositor clamps there whatever the start), then move to the entry point.
        corner = _nearest(self.remote.screens, [self.remote.box[0], self.remote.box[1]])
        s = self.remote.scale
        w, h = self.remote.box[2] - self.remote.box[0], self.remote.box[3] - self.remote.box[1]
        slam = H.move_reports(-int(math.ceil(w / s)) - 200, -int(math.ceil(h / s)) - 200)
        go = H.move_reports(round((entry[0] - corner[0]) / s), round((entry[1] - corner[1]) / s))
        self.remote.set(_nearest(self.remote.screens, entry))
        return slam + go

    def leaving(self) -> None:
        """Called when forwarding stops. The local pointer stayed frozen where it was, so its position is still valid."""
        self._exit = None
        self.local.clear_push()

    def remote_report(self, report: bytes) -> bool:
        """Feed a mouse report just sent to the other computer. True when its pointer pushed back through the border."""
        if len(report) == 5 and report[0] == 1:
            dx, dy = (b - 256 if b > 127 else b for b in report[3:5])
            return self.remote.move(dx, dy) is not None
        return False


def default_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openaten" / "screens.toml"


def load(path: Path, local: str | None = None) -> EdgeSwitch:
    """Build an :class:`EdgeSwitch` from a TOML file (see docs/setup.md). ``local`` overrides the file's ``local`` key and the hostname."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    import socket

    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    host = local or cfg.get("local") or socket.gethostname()
    try:
        screens = [Screen(str(s["name"]), str(s["host"]), s["x"], s["y"], s["width"], s["height"]) for s in cfg.get("screen", [])]
    except KeyError as e:
        raise ValueError(f"{path}: every [[screen]] needs name, host, x, y, width and height (missing {e.args[0]})") from None
    return EdgeSwitch(screens, host, float(cfg.get("local_scale", 1.0)), float(cfg.get("remote_scale", 1.0)), float(cfg.get("push", 40.0)))
