"""openaten command line: inspect the bridge and run the verified loopback tests."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import clipboard, config, ends, hotplug, selftest, warp
from . import log as logsetup
from .clipboard import ClipboardSync
from .errors import TransportError
from .forward import DEFAULT_HOTKEY
from .link import LinkSession, UsbTransport
from .transfer import TransferManager
from .usb import BridgeEnd, find_ends, port_path

log = logging.getLogger(__name__)


def cmd_list(_args) -> int:
    found = find_ends()
    if not found:
        print("no bridge ends found (needs both upstream ports connected to a host; see docs/hardware.md)")
        return 1
    for d in found:
        print(f"port {port_path(d)}  bus {d.bus} addr {d.address}  {d.idVendor:04x}:{d.idProduct:04x}")
    return 0


def cmd_state(_args) -> int:
    for e in ends.open_all():
        try:
            role = e.role()
            print(f"port {port_path(e.dev)}: role={role} state={e.state():#04x}")
        except TransportError as err:
            e.reset()
            print(f"port {port_path(e.dev)}: error {err}")
    return 0






def _load_edges(args):
    """The edge hand-off configured by --screens or the default screens.toml, or None when there is none."""
    from . import edges

    path = Path(args.screens) if args.screens else edges.default_path()
    if not path.exists():
        if args.screens:
            raise SystemExit(f"screen layout not found: {path}")
        return None
    try:
        sw = edges.load(path)
    except (ValueError, OSError) as e:
        raise SystemExit(f"{path}: {e}") from e
    log.info("edge hand-off on (%d border(s) from %s); push the pointer into a screen edge once to calibrate", len(sw.pairs), path)
    return sw


def cmd_layout(args) -> int:
    """Print the borders derived from the screen layout."""
    from . import edges

    path = Path(args.screens) if args.screens else edges.default_path()
    try:
        sw = edges.load(path)
    except (ValueError, OSError) as e:
        print(f"{path}: {e}")
        return 1
    for local, remote in sw.pairs:
        axis = "xy"[local.axis]
        print(f"leave via {axis}{'+' if local.sign > 0 else '-'} at {axis}={local.line:g}, {'xy'[1 - local.axis]} {local.lo:g}..{local.hi:g}: into {local.far}; back from {remote.far}")
    return 0


def cmd_forward(args) -> int:
    """Forward this computer's keyboard and mouse to the other computer (needs read access to /dev/input)."""
    from .forward import run_evdev

    candidates = sorted(find_ends(), key=lambda d: (d.bus, port_path(d)))
    if args.port:
        candidates = [d for d in candidates if port_path(d) == args.port]
    if not candidates:
        log.error("no bridge end found (see `openaten list`)")
        return 1
    end = BridgeEnd(candidates[0])  # claim only the end we inject through; the other one stays free for other tools
    ends.OPENED.append(end)
    log.info("injecting through the end on port %s", port_path(end.dev))
    try:
        return run_evdev(end.hid, args.hotkey, dry_run=args.dry_run, max_active=args.max_active or None, edges=_load_edges(args))
    except ImportError:
        log.error("python-evdev is required: pip install evdev")
        return 1


def _make_clipboard(send):
    backend = clipboard.detect()
    return ClipboardSync(backend, send) if backend is not None else None


def cmd_send(args) -> int:
    """Ask the running daemon to send files or directories to the other computer (the other daemon must run with --receive-dir)."""
    from . import daemon as dm

    rc = 0
    for name in args.files:
        path = os.path.abspath(name)
        try:
            reply = dm.request("send", path=path)
        except OSError:
            print(json.dumps({"ok": False, "error": "daemon not running"}))
            return 1
        print(f"{name}: " + (f"offered (transfer {reply['id']})" if reply.get("ok") else f"failed: {reply.get('error')}"))
        rc = rc or (0 if reply.get("ok") else 1)
    return rc


def cmd_daemon(args) -> int:
    """Run the background service: input forwarder + status file + control socket (see openaten.daemon)."""
    from . import daemon as dm
    from .forward import EvdevRunner

    sw = _load_edges(args)
    backend = warp.make(args.warp, sw.local.box if sw is not None else None)
    if sw is not None:
        sw.warp = backend
    d = dm.Daemon(
        lambda send: EvdevRunner(send, args.hotkey, max_active=args.max_active or None, edges=sw),
        find_ends,
        BridgeEnd,
        port=args.port,
        port_of=port_path,
        hotkey=args.hotkey,
        role=args.role,
        hotplug=hotplug.start,
        warp=backend,
        make_clipboard=(lambda send: _make_clipboard(send)) if args.clipboard else None,
        make_transfers=lambda send: TransferManager(send, Path(args.receive_dir).expanduser() if args.receive_dir else None),
        make_link=lambda end, master, on_message, on_error: LinkSession(UsbTransport(end), master, on_message, poll_idle=0.02, on_error=on_error),
    )
    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: d.stop_flag.set())
    return d.run()


def cmd_strip(args) -> int:
    """Run the border strips (needs GTK4 and gtk4-layer-shell): report pointer enter/leave on each border to the running daemon."""
    from . import edges, strip

    path = Path(args.screens) if args.screens else edges.default_path()
    try:
        sw = edges.load(path)
    except (ValueError, OSError) as e:
        print(f"{path}: {e}")
        return 1
    return strip.run(sw)


def cmd_ctl(args) -> int:
    """Send a command to the running daemon and print its status as JSON."""
    from . import daemon as dm

    try:
        reply = dm.request(args.command)
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        print(json.dumps({"ok": False, "error": "daemon not running"}))
        return 1
    print(json.dumps(reply))
    return 0 if reply.get("ok") else 1



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="openaten", description=__doc__)
    ap.add_argument("--config", help=f"config file with option defaults (default: {config.default_path()} if it exists)")
    ap.add_argument("--log-level", choices=logsetup.LEVELS, type=str.upper, help=f"log verbosity (default: ${logsetup.ENV_VAR} or INFO)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list bridge ends").set_defaults(fn=cmd_list)
    sub.add_parser("state", help="read role and link state of each end").set_defaults(fn=cmd_state)
    lb = sub.add_parser("loopback", help="packet round-trip test between two ends on this host")
    lb.add_argument("-n", "--count", type=int, default=5)
    lb.set_defaults(fn=selftest.cmd_loopback)
    lk = sub.add_parser("link", help="link-layer self-test (messages both ways + bulk); with --role, one side of a two-computer test")
    lk.add_argument("--role", choices=["master", "slave"], help="run only this side (start it on both computers, within a few seconds)")
    lk.add_argument("--timeout", type=float, default=60, help="seconds to wait for the peer (with --role)")
    lk.add_argument("--mib", type=int, default=8, help="size of the bulk message in MiB (each direction)")
    lk.set_defaults(fn=selftest.cmd_link)
    fw = sub.add_parser("forward", help="forward this computer's keyboard/mouse to the other computer (hotkey toggles)")
    fw.add_argument("--port", help="USB port path of the end to inject through (default: first end)")
    fw.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="chord that toggles forwarding")
    fw.add_argument("--dry-run", action="store_true", help="print the reports instead of grabbing devices and injecting")
    fw.add_argument("--max-active", type=float, default=0, help="safety: return control after this many seconds (0 = never)")
    fw.add_argument("--screens", help="screen layout TOML for edge hand-off (default: ~/.config/openaten/screens.toml if it exists)")
    fw.set_defaults(fn=cmd_forward)
    dm_ = sub.add_parser("daemon", help="background service: forwarder + status file + control socket (for the Noctalia widget)")
    dm_.add_argument("--port", help="USB port path of the end to inject through (default: first end)")
    dm_.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="chord that toggles forwarding")
    dm_.add_argument("--max-active", type=float, default=0, help="safety: return control after this many seconds (0 = never)")
    dm_.add_argument("--role", choices=["master", "slave"], help="keep a link to the other computer's agent (run master on one computer, slave on the other); needed for the strip's exact hand-off")
    dm_.add_argument("--screens", help="screen layout TOML for edge hand-off (default: ~/.config/openaten/screens.toml if it exists)")
    dm_.add_argument("--warp", choices=["none", "uinput"], default="none", help="place this computer's pointer exactly where the other one came back (uinput needs write access to /dev/uinput; see docs/setup.md)")
    dm_.add_argument("--clipboard", action=argparse.BooleanOptionalAction, default=True, help="share the clipboard (text, PNG) with the other computer (needs --role; on by default, --no-clipboard turns it off)")
    dm_.add_argument("--receive-dir", default="~/Downloads", help="accept files sent with `openaten send` on the other computer into this directory (default: ~/Downloads)")
    dm_.set_defaults(fn=cmd_daemon)
    sp = sub.add_parser("strip", help="invisible border strips that report the exact pointer position at the shared screen borders")
    sp.add_argument("--screens", help="screen layout TOML (default: ~/.config/openaten/screens.toml)")
    sp.set_defaults(fn=cmd_strip)
    ly = sub.add_parser("layout", help="show the borders derived from the screen layout")
    ly.add_argument("--screens", help="screen layout TOML (default: ~/.config/openaten/screens.toml)")
    ly.set_defaults(fn=cmd_layout)
    sd = sub.add_parser("send", help="send files or directories to the other computer through the running daemon")
    sd.add_argument("files", nargs="+")
    sd.set_defaults(fn=cmd_send)
    ct = sub.add_parser("ctl", help="control the running daemon")
    ct.add_argument("command", choices=["status", "on", "off", "toggle", "quit"])
    ct.set_defaults(fn=cmd_ctl)
    sub.add_parser("hid", help="HID injection round-trip test between two ends on this host").set_defaults(fn=selftest.cmd_hid)
    pre, _ = ap.parse_known_args(argv)  # the config file's values become the defaults the real parse then lets flags override
    try:
        cfg = config.load(Path(pre.config) if pre.config else None, explicit=bool(pre.config))
    except ValueError as e:
        raise SystemExit(str(e)) from e
    for name, section in cfg.items():
        if name in sub.choices:
            sub.choices[name].set_defaults(**section)
    args = ap.parse_args(argv)
    logsetup.setup(args.log_level, fallback=cfg.get("log_level"))
    try:
        rc = args.fn(args)
    finally:
        ends.close_all()
    if ends.STUCK_THREADS:
        # A thread is blocked inside libusb; tearing libusb down now would crash, so leave without cleanup.
        sys.stdout.flush()
        os._exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
