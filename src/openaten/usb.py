"""USB bulk-only transport to a bridge end, via libusb (no root needed once udev grants access).

The bridge exposes a mass-storage interface. Vendor commands (opcode 0xF2) are sent as normal
bulk-only-transport command blocks, which avoids the kernel's SCSI opcode filtering entirely.
"""
from __future__ import annotations

import struct
import time

import usb.core
import usb.util

from . import protocol as pr

VID, PID = 0x05E3, 0x0627  # Genesys Logic "USB Storage" bridge function


def port_path(dev: usb.core.Device) -> str:
    return ".".join(map(str, dev.port_numbers or ()))


def find_ends() -> list[usb.core.Device]:
    return list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID))


def find_by_port(path: str) -> usb.core.Device | None:
    for d in find_ends():
        if port_path(d) == path:
            return d
    return None


class BridgeEnd:
    """One end of the bridge. Not thread-safe: the transport is strictly serial (one command at a time)."""

    def __init__(self, dev: usb.core.Device, keep_kernel_driver_detached: bool = True):
        self.dev = dev
        self._tag = 1
        cfg = dev.get_active_configuration()
        intf = next(i for i in cfg if i.bInterfaceClass == 8)
        self.ifnum = intf.bInterfaceNumber
        self._reattach = dev.is_kernel_driver_active(self.ifnum) and not keep_kernel_driver_detached
        if dev.is_kernel_driver_active(self.ifnum):
            dev.detach_kernel_driver(self.ifnum)
        usb.util.claim_interface(dev, self.ifnum)

        def ep(direction):
            return usb.util.find_descriptor(
                intf,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == direction
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK,
            )

        self.ep_in, self.ep_out = ep(usb.util.ENDPOINT_IN), ep(usb.util.ENDPOINT_OUT)
        self.reset()

    # -- housekeeping -----------------------------------------------------------------
    def reset(self) -> None:
        """Bulk-only mass-storage reset + clear halts. Required after any aborted or failed transfer."""
        try:
            self.dev.ctrl_transfer(0x21, 0xFF, 0, self.ifnum, None)
            self.dev.clear_halt(self.ep_in)
            self.dev.clear_halt(self.ep_out)
        except usb.core.USBError:
            pass

    def close(self) -> None:
        try:
            usb.util.release_interface(self.dev, self.ifnum)
            if self._reattach:
                self.dev.attach_kernel_driver(self.ifnum)
        except usb.core.USBError:
            pass

    # -- bulk-only transport ----------------------------------------------------------
    def cmd(self, cb: bytes, data_in: int = 0, data_out: bytes = b"", timeout: int = 3000) -> tuple[int, bytes]:
        """Run one command; returns (csw_status, data_in_bytes). Raises usb.core.USBError on stall/timeout."""
        tag, self._tag = self._tag, self._tag + 1
        length = data_in or len(data_out)
        cbw = struct.pack("<4sIIBBB", b"USBC", tag, length, 0x80 if data_in else 0, 0, len(cb)) + cb.ljust(16, b"\0")
        self.ep_out.write(cbw, timeout)
        data = b""
        if data_in:
            data = bytes(self.ep_in.read(data_in, timeout))
        elif data_out:
            self.ep_out.write(data_out, timeout)
        for _ in range(4):  # resync past stale status blocks
            sig, rtag, _residue, status = struct.unpack("<4sIIB", bytes(self.ep_in.read(13, timeout)))
            if sig == b"USBS" and rtag == tag:
                return status, data
        raise usb.core.USBError("no matching CSW")

    # -- bridge commands --------------------------------------------------------------
    def state(self) -> int:
        return self.cmd(pr.cdb_state(), data_in=1)[1][0]

    def inquiry(self, length: int = 0x34) -> bytes:
        """Standard INQUIRY. In CD-ROM mode, 0x34 bytes include the vendor bytes (see docs/hardware.md)."""
        return self.cmd(bytes([0x12, 0, 0, 0, length, 0]), data_in=length)[1]

    def init(self) -> int:
        return self.cmd(pr.cdb(pr.CMD_INIT))[0]

    def send(self, packet: bytes, timeout: int = 3000) -> None:
        """Send one packet. Only completes if the peer has a receive pending (see docs/hardware.md)."""
        self.cmd(pr.cdb_tx(len(packet)), data_out=packet, timeout=timeout)

    def recv(self, timeout: int = 5000) -> bytes:
        """Wait for one packet. Times out (and needs reset()) if the peer sends nothing."""
        return self.cmd(pr.cdb_rx(), data_in=pr.PKT_LEN, timeout=timeout)[1]

    def hid(self, report: bytes) -> None:
        """Inject a mouse (5 B) or keyboard (8 B) report; it appears on the peer's HID interface."""
        self.cmd(pr.cdb_hid(len(report)), data_out=report)

    def role(self) -> str | None:
        """'H' (master) or 'S' (slave), from the vendor INQUIRY bytes. Only available in CD-ROM mode."""
        try:
            d = self.inquiry(0x34)
        except usb.core.USBError:
            self.reset()
            time.sleep(0.3)
            return None
        return chr(d[0x2E]) if len(d) > 0x2E and d[0x28:0x2C] == b"GENE" else None


def wait_for_port(path: str, timeout: float = 15.0, not_address: int | None = None) -> usb.core.Device | None:
    """Wait for a (re-)enumerated end on a given USB port path."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = find_by_port(path)
        if d is not None and d.address != not_address:
            return d
        time.sleep(0.02)
    return None
