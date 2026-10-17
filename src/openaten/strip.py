"""Border strips: 1-pixel invisible layer-shell windows along each shared screen border that report the exact pointer position.

niri cannot tell clients where the pointer is, but a surface can tell when the pointer *enters* it, with exact coordinates. A strip hugs the
screen edge over the part that borders the other computer's screen; the compositor clamps the pointer at the edge, so pushing through the
border always lands in the strip. Each enter/leave is sent to the running daemon (``openaten daemon``), which decides what it means
(:class:`openaten.control.Handoff`): start forwarding, or, while receiving, give control back.

Needs GTK 4, gtk4-layer-shell and PyGObject. Not exercised by the test suite (it needs a Wayland compositor).
"""
from __future__ import annotations

import sys

from . import daemon
from .edges import EdgeSwitch


def _report(key: str, row: float, enter: bool) -> None:
    try:
        daemon.request("edge", border=key, row=row, enter=enter)
    except OSError as e:
        print(f"daemon not reachable: {e}", file=sys.stderr, flush=True)


def run(sw: EdgeSwitch) -> int:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk, Gtk, Gtk4LayerShell as LayerShell
    except (ImportError, ValueError) as e:
        print(f"GTK4, gtk4-layer-shell and PyGObject are required: {e}", file=sys.stderr)
        return 1

    if Gdk.Display.get_default() is None:
        print("no Wayland display: run this inside the compositor session", file=sys.stderr)
        return 1
    local = {s.name: s for s in sw.local.screens}  # keyed by output connector name
    app = Gtk.Application(application_id="com.openaten.Strip")

    def activate(app) -> None:
        monitors = {m.get_connector(): m for m in Gdk.Display.get_default().get_monitors()}
        # Not fully transparent: GTK never resizes a window it considers invisible, and the surface would stay 200x200.
        css = Gtk.CssProvider()
        css.load_from_string("window.openaten-strip { background: rgba(0,0,0,0.01); }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for key, (edge, back) in zip(sw.keys, sw.pairs):
            screen, monitor = local[back.far], monitors.get(back.far)  # back.far is this computer's screen at the border
            if monitor is None:
                print(f"no output named {screen.name} for border {key}; skipping", file=sys.stderr, flush=True)
                continue
            win = Gtk.Window(application=app)
            win.add_css_class("openaten-strip")
            LayerShell.init_for_window(win)
            LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
            LayerShell.set_monitor(win, monitor)
            LayerShell.set_namespace(win, "openaten-strip")
            LayerShell.set_exclusive_zone(win, -1)
            LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
            along_origin = screen.y if edge.axis == 0 else screen.x
            length = int(edge.hi - edge.lo)
            margin = int(edge.lo - along_origin)
            if edge.axis == 0:  # vertical strip on the left/right edge
                LayerShell.set_anchor(win, LayerShell.Edge.RIGHT if edge.sign > 0 else LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
                LayerShell.set_margin(win, LayerShell.Edge.TOP, margin)
                win.set_size_request(1, length)
                win.set_default_size(1, length)
            else:  # horizontal strip on the top/bottom edge
                LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM if edge.sign > 0 else LayerShell.Edge.TOP, True)
                LayerShell.set_anchor(win, LayerShell.Edge.LEFT, True)
                LayerShell.set_margin(win, LayerShell.Edge.LEFT, margin)
                win.set_size_request(length, 1)
                win.set_default_size(length, 1)
            motion = Gtk.EventControllerMotion()

            def row_of(x: float, y: float, edge=edge) -> float:
                return edge.lo + (y if edge.axis == 0 else x)

            motion.connect("enter", lambda c, x, y, key=key, row_of=row_of: _report(key, row_of(x, y), True))
            motion.connect("leave", lambda c, key=key, edge=edge: _report(key, edge.lo, False))
            win.add_controller(motion)
            win.present()

    app.connect("activate", activate)
    return app.run(None)

