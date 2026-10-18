"""Hardware self-tests: exercise the bridge, the link layer and HID injection on real switches (see docs/hardware.md)."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

import usb.core
import usb.util

from . import ends as bridge_ends
from . import protocol as pr
from .link import LinkSession, UsbTransport
from .usb import port_path

log = logging.getLogger(__name__)


def cmd_loopback(args) -> int:
    """Send packets from one end to the other (both ends on this machine) and verify they arrive intact."""
    ends = bridge_ends.open_all()
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

def _pattern(tag: bytes, n: int) -> bytes:
    """Deterministic bulk payload so two computers can verify each other's data without sharing a file."""
    out, block = bytearray(), tag
    while len(out) < n:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out[:n])

def cmd_link_peer(args) -> int:
    """One side of the two-computer link test: run `link --role master` on one computer and `--role slave` on the other."""
    ends = bridge_ends.open_all()
    if not ends:
        print("no bridge end found (see `openaten list`)")
        return 1
    master = args.role == "master"
    peer = "slave" if master else "master"
    got: list = []
    done = threading.Event()

    def on_message(channel, msg_id, data):
        got.append((channel, hashlib.sha256(data).hexdigest(), len(data)))
        if len(got) >= 2:
            done.set()

    errors: list = []
    link = LinkSession(UsbTransport(ends[0]), master, on_message, poll_idle=0.02, on_error=errors.append)
    small, big = f"hello from {args.role}".encode(), _pattern(args.role.encode(), args.mib << 20)
    link.start()
    t0 = time.time()
    link.send_message(1, small)
    link.send_message(2, big)
    ok = done.wait(args.timeout)
    dt = time.time() - t0
    if not link.stop():
        bridge_ends.STUCK_THREADS.append(True)
    want_small, want_big = f"hello from {peer}".encode(), _pattern(peer.encode(), args.mib << 20)
    expect = {(1, hashlib.sha256(want_small).hexdigest(), len(want_small)), (2, hashlib.sha256(want_big).hexdigest(), len(want_big))}
    intact = set(got) == expect
    print(f"{args.role}: delivered={ok} intact={intact} errors={errors} in {dt:.2f}s")
    print(link.stats)
    return 0 if ok and intact and not errors else 2

def cmd_link(args) -> int:
    """Run both link roles over the two ends on this host: messages both ways plus a large transfer."""
    if args.role:
        return cmd_link_peer(args)
    ends = bridge_ends.open_all()
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
        bridge_ends.STUCK_THREADS.append(True)
    expect = {(1, hashlib.sha256(small_s).hexdigest(), len(small_s)), (2, hashlib.sha256(big).hexdigest(), len(big))}
    expect_s = {(1, hashlib.sha256(small_m).hexdigest(), len(small_m)), (2, hashlib.sha256(big).hexdigest(), len(big))}
    intact = set(got["master"]) == expect and set(got["slave"]) == expect_s
    moved = 2 * len(big) / 1e6
    print(f"delivered={ok} intact={intact} errors={errors} in {dt:.2f}s ({moved / dt:.1f} MB/s both directions combined)")
    print("master", master.stats)
    print("slave ", slave.stats)
    return 0 if ok and intact and not errors else 2

def cmd_hid(_args) -> int:
    """Inject mouse/keyboard reports on one end and read them back from the other end's HID interface."""
    ends = bridge_ends.open_all()
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
