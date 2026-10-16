"""openaten command line: inspect the bridge and run the verified loopback tests."""
from __future__ import annotations

import argparse
import sys
import threading
import time

import usb.core
import usb.util

from . import protocol as pr
from .usb import BridgeEnd, find_ends, port_path


def _open_all() -> list[BridgeEnd]:
    ends = [BridgeEnd(d) for d in find_ends()]
    return sorted(ends, key=lambda e: port_path(e.dev))


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


def cmd_hid(_args) -> int:
    """Inject mouse/keyboard reports on one end and read them back from the other end's HID interface."""
    ends = _open_all()
    if len(ends) < 2:
        print("need two bridge ends")
        return 1
    src, dst = ends[0], ends[1]
    cfg = dst.dev.get_active_configuration()
    hid = next(i for i in cfg if i.bInterfaceClass == 3)
    if dst.dev.is_kernel_driver_active(hid.bInterfaceNumber):
        dst.dev.detach_kernel_driver(hid.bInterfaceNumber)  # keep the test input off the real desktop
    usb.util.claim_interface(dst.dev, hid.bInterfaceNumber)
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
    sub.add_parser("hid", help="HID injection round-trip test between two ends on this host").set_defaults(fn=cmd_hid)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
