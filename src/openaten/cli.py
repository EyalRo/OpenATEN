"""openaten command line: inspect the bridge and run the verified loopback tests."""
from __future__ import annotations

import argparse
from .forward import DEFAULT_HOTKEY
import os
import sys
import threading
import time

import usb.core
import usb.util

from . import protocol as pr
from .link import LinkSession, UsbTransport
from .usb import BridgeEnd, find_ends, port_path


_OPENED: list[BridgeEnd] = []
_STUCK_THREADS: list[bool] = []  # set if a link thread is still blocked inside libusb


def _open_all() -> list[BridgeEnd]:
    """Open every bridge end, ordered by (bus, port path). They are closed when the command finishes."""
    ends = sorted(find_ends(), key=lambda d: (d.bus, port_path(d)))
    opened = [BridgeEnd(d) for d in ends]
    _OPENED.extend(opened)
    return opened


def cmd_list(_args) -> int:
    ends = find_ends()
    if not ends:
        print("no bridge ends found (needs both upstream ports connected to a host; see docs/hardware.md)")
        return 1
    for d in ends:
        print(f"port {port_path(d)}  bus {d.bus} addr {d.address}  {d.idVendor:04x}:{d.idProduct:04x}")
    return 0


def cmd_state(_args) -> int:
    for e in _open_all():
        try:
            role = e.role()
            print(f"port {port_path(e.dev)}: role={role} state={e.state():#04x}")
        except usb.core.USBError as err:
            e.reset()
            print(f"port {port_path(e.dev)}: error {err}")
    return 0


def cmd_loopback(args) -> int:
    """Send packets from one end to the other (both ends on this machine) and verify they arrive intact."""
    ends = _open_all()
    if len(ends) < 2:
        print("need two bridge ends (connect both switch upstream ports to this machine)")
        return 1
    src, dst = ends[0], ends[1]
    ok = 0
    for i in range(args.count):
        pkt = pr.packet(pr.T_DATA, i.to_bytes(4, "little"), stream=1)
        got: dict = {}

        def rx():
            try:
                got["p"] = dst.recv(timeout=4000)
            except usb.core.USBError as e:
                got["e"] = e

        th = threading.Thread(target=rx)
        th.start()
        time.sleep(0.1)  # the receiver must be pending before the sender starts
        try:
            src.send(pkt)
        except usb.core.USBError as e:
            got.setdefault("e", e)
            src.reset()
        th.join()
        if got.get("p") == pkt:
            ok += 1
        else:
            print(f"packet {i}: FAILED {got.get('e')}")
            src.reset()
            dst.reset()
    print(f"{ok}/{args.count} packets delivered intact ({port_path(src.dev)} -> {port_path(dst.dev)})")
    return 0 if ok == args.count else 2


def cmd_link(args) -> int:
    """Run both link roles over the two ends on this host: messages both ways plus a large transfer."""
    import hashlib
    import os

    ends = _open_all()
    if len(ends) < 2:
        print("need two bridge ends")
        return 1
    got: dict[str, list] = {"master": [], "slave": []}
    done = {"master": threading.Event(), "slave": threading.Event()}
    want = {"master": 2, "slave": 2}

    def collector(name):
        def on_message(channel, msg_id, data):
            got[name].append((channel, hashlib.sha256(data).hexdigest(), len(data)))
            if len(got[name]) >= want[name]:
                done[name].set()
        return on_message

    errors: list = []
    master = LinkSession(UsbTransport(ends[0]), True, collector("master"), poll_idle=0.02, on_error=errors.append)
    slave = LinkSession(UsbTransport(ends[1]), False, collector("slave"), poll_idle=0.02, on_error=errors.append)
    slave.start()
    master.start()
    big = os.urandom(args.mib << 20)
    small_m, small_s = b"hello from master", b"hello from slave"
    t0 = time.time()
    master.send_message(1, small_m)
    slave.send_message(1, small_s)
    master.send_message(2, big)
    slave.send_message(2, big)
    ok = all(done[n].wait(60) for n in done)
    dt = time.time() - t0
    stopped = master.stop() & slave.stop()
    if not stopped:
        _STUCK_THREADS.append(True)
    expect = {(1, hashlib.sha256(small_s).hexdigest(), len(small_s)), (2, hashlib.sha256(big).hexdigest(), len(big))}
    expect_s = {(1, hashlib.sha256(small_m).hexdigest(), len(small_m)), (2, hashlib.sha256(big).hexdigest(), len(big))}
    intact = set(got["master"]) == expect and set(got["slave"]) == expect_s
    moved = 2 * len(big) / 1e6
    print(f"delivered={ok} intact={intact} errors={errors} in {dt:.2f}s ({moved / dt:.1f} MB/s both directions combined)")
    print("master", master.stats)
    print("slave ", slave.stats)
    return 0 if ok and intact and not errors else 2


def cmd_forward(args) -> int:
    """Forward this computer's keyboard and mouse to the other computer (needs read access to /dev/input)."""
    from .forward import run_evdev

    candidates = sorted(find_ends(), key=lambda d: (d.bus, port_path(d)))
    if args.port:
        candidates = [d for d in candidates if port_path(d) == args.port]
    if not candidates:
        print("no bridge end found (see `openaten list`)")
        return 1
    end = BridgeEnd(candidates[0])  # claim only the end we inject through; the other one stays free for other tools
    _OPENED.append(end)
    print(f"injecting through the end on port {port_path(end.dev)}")
    try:
        return run_evdev(end.hid, args.hotkey, dry_run=args.dry_run, max_active=args.max_active or None)
    except ImportError:
        print("python-evdev is required: pip install evdev")
        return 1


def cmd_daemon(args) -> int:
    """Run the background service: input forwarder + status file + control socket (see openaten.daemon)."""
    from . import daemon as dm
    from .forward import EvdevRunner

    d = dm.Daemon(
        lambda send: EvdevRunner(send, args.hotkey, max_active=args.max_active or None, log=lambda m: print(m, flush=True)),
        find_ends,
        BridgeEnd,
        port=args.port,
        port_of=port_path,
        hotkey=args.hotkey,
        log=lambda m: print(m, flush=True),
    )
    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: d.stop_flag.set())
    return d.run()


def cmd_ctl(args) -> int:
    """Send a command to the running daemon and print its status as JSON."""
    import json

    from . import daemon as dm

    try:
        reply = dm.request(args.command)
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        print(json.dumps({"ok": False, "error": "daemon not running"}))
        return 1
    print(json.dumps(reply))
    return 0 if reply.get("ok") else 1


def cmd_hid(_args) -> int:
    """Inject mouse/keyboard reports on one end and read them back from the other end's HID interface."""
    ends = _open_all()
    if len(ends) < 2:
        print("need two bridge ends")
        return 1
    src, dst = ends[0], ends[1]
    cfg = dst.dev.get_active_configuration()
    hid = next(i for i in cfg if i.bInterfaceClass == 3)
    hid_if = hid.bInterfaceNumber
    had_driver = dst.dev.is_kernel_driver_active(hid_if)
    if had_driver:
        dst.dev.detach_kernel_driver(hid_if)  # keep the test input off the real desktop
    usb.util.claim_interface(dst.dev, hid_if)
    try:
        ep = next(e for e in hid if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        reports = [pr.mouse_report(dx=5), pr.mouse_report(), pr.key_report(0, 0x04), pr.key_report()]
        got: list = []

        def reader():
            try:
                for _ in reports:
                    got.append(bytes(dst.dev.read(ep.bEndpointAddress, 64, 1500)))
            except usb.core.USBError as e:
                got.append(str(e))

        th = threading.Thread(target=reader)
        th.start()
        time.sleep(0.2)
        for r in reports:
            src.hid(r)
            time.sleep(0.1)
        th.join()
    finally:
        # Put the kernel HID driver back: with nothing polling the interrupt endpoint the firmware refuses further injections.
        usb.util.release_interface(dst.dev, hid_if)
        if had_driver:
            dst.dev.attach_kernel_driver(hid_if)
    ok = got == reports
    print("sent    :", [r.hex(" ") for r in reports])
    print("received:", [g.hex(" ") if isinstance(g, bytes) else g for g in got])
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="openaten", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list bridge ends").set_defaults(fn=cmd_list)
    sub.add_parser("state", help="read role and link state of each end").set_defaults(fn=cmd_state)
    lb = sub.add_parser("loopback", help="packet round-trip test between two ends on this host")
    lb.add_argument("-n", "--count", type=int, default=5)
    lb.set_defaults(fn=cmd_loopback)
    lk = sub.add_parser("link", help="link-layer self-test (messages both ways + bulk) between two ends on this host")
    lk.add_argument("--mib", type=int, default=8, help="size of the bulk message in MiB (each direction)")
    lk.set_defaults(fn=cmd_link)
    fw = sub.add_parser("forward", help="forward this computer's keyboard/mouse to the other computer (hotkey toggles)")
    fw.add_argument("--port", help="USB port path of the end to inject through (default: first end)")
    fw.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="chord that toggles forwarding")
    fw.add_argument("--dry-run", action="store_true", help="print the reports instead of grabbing devices and injecting")
    fw.add_argument("--max-active", type=float, default=0, help="safety: return control after this many seconds (0 = never)")
    fw.set_defaults(fn=cmd_forward)
    dm_ = sub.add_parser("daemon", help="background service: forwarder + status file + control socket (for the Noctalia widget)")
    dm_.add_argument("--port", help="USB port path of the end to inject through (default: first end)")
    dm_.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="chord that toggles forwarding")
    dm_.add_argument("--max-active", type=float, default=0, help="safety: return control after this many seconds (0 = never)")
    dm_.set_defaults(fn=cmd_daemon)
    ct = sub.add_parser("ctl", help="control the running daemon")
    ct.add_argument("command", choices=["status", "on", "off", "toggle", "quit"])
    ct.set_defaults(fn=cmd_ctl)
    sub.add_parser("hid", help="HID injection round-trip test between two ends on this host").set_defaults(fn=cmd_hid)
    args = ap.parse_args(argv)
    try:
        rc = args.fn(args)
    finally:
        if not _STUCK_THREADS:
            for end in _OPENED:
                end.close()
    if _STUCK_THREADS:
        # A thread is blocked inside libusb; tearing libusb down now would crash, so leave without cleanup.
        sys.stdout.flush()
        os._exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
