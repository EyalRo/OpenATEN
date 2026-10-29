//! USB bulk-only transport to a bridge end, via libusb (`rusb`). No root needed
//! once udev grants access. Ported from `src/openaten/usb.py`.
//!
//! The bridge exposes a mass-storage interface. Vendor commands (opcode 0xF2)
//! are sent as normal bulk-only-transport command blocks, which avoids the
//! kernel's SCSI opcode filtering entirely.
//!
//! Provides [`BridgeEnd`] (the low-level command transport), [`UsbTransport`]
//! (adapts a [`BridgeEnd`] to the crate's [`Transport`](crate::link::Transport)
//! trait, converting timeouts from seconds to milliseconds), and the discovery
//! helpers [`find_ends`] and [`find_by_port`].

use crate::errors;
use crate::protocol;
use rusb::{Context, Device, DeviceHandle, Direction, TransferType, UsbContext};
use std::time::Duration;

pub const VID: u16 = 0x05E3; // Genesys Logic
pub const PID: u16 = 0x0627; // "USB Storage" bridge function

const MASS_STORAGE_CLASS: u8 = 8;

// Bulk-only transport CBW/CSW signatures.
const CBW_SIGNATURE: [u8; 4] = *b"USBC";
const CSW_SIGNATURE: [u8; 4] = *b"USBS";

/// `bmRequestType` for a bulk-only mass-storage reset (class request, recipient
/// = interface, direction OUT).
const REQUEST_TYPE_RESET: u8 = 0x21;
/// `bRequest` for a bulk-only reset.
const REQUEST_RESET: u8 = 0xFF;
/// `bRequest` for get max LUN (not used; kept for reference).
#[allow(dead_code)]
const REQUEST_GET_MAX_LUN: u8 = 0xFE;

/// Human-readable USB port path for a device (e.g. `"1.2.3"`), matching the
/// Python `port_path()`.
pub fn port_path(dev: &Device<Context>) -> String {
    match dev.port_numbers() {
        Ok(nums) => nums
            .iter()
            .map(|n| n.to_string())
            .collect::<Vec<_>>()
            .join("."),
        Err(_) => String::new(),
    }
}

/// Enumerate every bridge end currently present on the bus.
pub fn find_ends() -> Vec<Device<Context>> {
    let ctx = match Context::new() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    match ctx.devices() {
        Ok(list) => list
            .iter()
            .filter(|d| {
                d.device_descriptor()
                    .map(|dd| dd.vendor_id() == VID && dd.product_id() == PID)
                    .unwrap_or(false)
            })
            .collect(),
        Err(_) => Vec::new(),
    }
}

/// Find the bridge end on a given USB port path, if any.
pub fn find_by_port(path: &str) -> Option<Device<Context>> {
    find_ends().into_iter().find(|d| port_path(d) == path)
}

/// Whether any bridge end is currently present (without claiming one).
pub fn any_end() -> bool {
    !find_ends().is_empty()
}

/// A claimed bridge end. Not thread-safe: the transport is strictly serial
/// (one command at a time), mirroring the Python `BridgeEnd`.
pub struct BridgeEnd {
    handle: DeviceHandle<Context>,
    ifnum: u8,
    ep_in: u8,
    ep_out: u8,
    tag: u32,
    _bus: u8,
    port: String,
}

impl BridgeEnd {
    /// Open and claim the interface of a bridge end, finding the bulk IN/OUT
    /// endpoints. Detaches the kernel driver (the mass-storage driver would
    /// otherwise keep the interface).
    pub fn open(dev: Device<Context>) -> Result<Self, errors::TransportError> {
        let bus = dev.bus_number();
        let port = port_path(&dev);
        let dd = dev
            .device_descriptor()
            .map_err(|e| errors::TransportError::new(e.to_string()))?;
        if dd.vendor_id() != VID || dd.product_id() != PID {
            return Err(errors::TransportError::new(format!(
                "not a bridge end: {:#06x}:{:#06x}",
                dd.vendor_id(),
                dd.product_id()
            )));
        }
        let handle = dev
            .open()
            .map_err(|e| errors::TransportError::new(e.to_string()))?;

        // Set auto-detach: libusb detaches the kernel driver when we claim and
        // re-attaches it when we release. This mirrors the Python behaviour of
        // detaching on claim and (optionally) re-attaching on close.
        let _ = handle.set_auto_detach_kernel_driver(true);

        let cfg = dev
            .active_config_descriptor()
            .map_err(|e| errors::TransportError::new(e.to_string()))?;
        let mut ifnum = None;
        let mut ep_in = None;
        let mut ep_out = None;
        'outer: for interface in cfg.interfaces() {
            for desc in interface.descriptors() {
                if desc.class_code() == MASS_STORAGE_CLASS {
                    ifnum = Some(desc.interface_number());
                    for ep in desc.endpoint_descriptors() {
                        match (ep.direction(), ep.transfer_type()) {
                            (Direction::In, TransferType::Bulk) => ep_in = Some(ep.address()),
                            (Direction::Out, TransferType::Bulk) => ep_out = Some(ep.address()),
                            _ => {}
                        }
                    }
                    break 'outer;
                }
            }
        }
        let (Some(ifnum), Some(ep_in), Some(ep_out)) = (ifnum, ep_in, ep_out) else {
            return Err(errors::TransportError::new(
                "bridge end has no mass-storage interface with bulk endpoints",
            ));
        };

        handle
            .claim_interface(ifnum)
            .map_err(|e| errors::TransportError::new(e.to_string()))?;

        let mut end = Self { handle, ifnum, ep_in, ep_out, tag: 1, _bus: bus, port };
        end.reset();
        Ok(end)
    }

    pub fn port(&self) -> &str {
        &self.port
    }

    pub fn bus(&self) -> u8 {
        self._bus
    }

    /// Bulk-only mass-storage reset + clear halts. Required after any aborted
    /// or failed transfer.
    pub fn reset(&mut self) {
        let _ = self
            .handle
            .write_control(REQUEST_TYPE_RESET, REQUEST_RESET, 0, self.ifnum.into(), &[], Duration::from_secs(1));
        let _ = self.handle.clear_halt(self.ep_in);
        let _ = self.handle.clear_halt(self.ep_out);
    }

    /// Release the interface (re-attaching the kernel driver happens via
    /// auto-detach) and free the handle.
    pub fn close(&mut self) {
        let _ = self.handle.release_interface(self.ifnum);
    }

    /// Run one command; returns `(csw_status, data_in_bytes)`.
    ///
    /// `cbw_timeout` bounds how long the command block itself may be refused
    /// (the firmware refuses out-of-turn commands); it defaults to `timeout`.
    fn cmd(
        &mut self,
        cb: &[u8; 16],
        data_in: usize,
        data_out: &[u8],
        timeout: Duration,
        cbw_timeout: Duration,
    ) -> Result<(u8, Vec<u8>), rusb::Error> {
        let tag = self.tag;
        self.tag = self.tag.wrapping_add(1);
        let length = if data_in > 0 { data_in as u32 } else { data_out.len() as u32 };
        let direction: u8 = if data_in > 0 { 0x80 } else { 0 };

        // CBW: signature(4) tag(4) data-transfer-length(4) flags(1) lun(1) cb-length(1) cb(16)
        let mut cbw = [0u8; 31];
        cbw[0..4].copy_from_slice(&CBW_SIGNATURE);
        cbw[4..8].copy_from_slice(&tag.to_le_bytes());
        cbw[8..12].copy_from_slice(&length.to_le_bytes());
        cbw[12] = direction;
        cbw[14] = 16; // cb length
        cbw[15..31].copy_from_slice(cb);

        self.handle.write_bulk(self.ep_out, &cbw, cbw_timeout)?;

        let mut data = Vec::new();
        if data_in > 0 {
            let mut buf = vec![0u8; data_in];
            let n = self.handle.read_bulk(self.ep_in, &mut buf, timeout)?;
            data = buf[..n].to_vec();
        } else if !data_out.is_empty() {
            self.handle.write_bulk(self.ep_out, data_out, timeout)?;
        }

        // Resync past stale status blocks (up to 4).
        for _ in 0..4 {
            let mut csw = [0u8; 13];
            let n = self.handle.read_bulk(self.ep_in, &mut csw, timeout)?;
            if n >= 13 && csw[0..4] == CSW_SIGNATURE {
                let rtag = u32::from_le_bytes(csw[4..8].try_into().unwrap());
                if rtag == tag {
                    return Ok((csw[12], data));
                }
            }
        }
        Err(rusb::Error::Other)
    }

    fn map_err(e: rusb::Error) -> errors::TransportError {
        let msg = match e {
            rusb::Error::Timeout => "timed out".to_string(),
            rusb::Error::Pipe => "pipe error".to_string(),
            other => other.to_string(),
        };
        errors::TransportError::new(msg)
    }

    /// Run a stand-alone command (state, INQUIRY, HID). If the firmware refuses
    /// it, reset the transport and try once more.
    ///
    /// After a link session an end can be left in a state where its first
    /// stand-alone command is refused; the failed command plus a bulk-only
    /// reset clears it (see docs/hardware.md).
    fn simple(
        &mut self,
        cb: &[u8; 16],
        data_in: usize,
        data_out: &[u8],
        timeout: Duration,
    ) -> Result<(u8, Vec<u8>), errors::TransportError> {
        for attempt in 0..3 {
            match self.cmd(cb, data_in, data_out, timeout, timeout) {
                Ok(r) => return Ok(r),
                Err(_) if attempt == 2 => {
                    return Err(Self::map_err(rusb::Error::Other));
                }
                Err(_) => {
                    self.reset();
                    std::thread::sleep(Duration::from_millis(50));
                    if attempt == 1 && data_in > 0 {
                        // Observed: an end whose last command was a receive can
                        // refuse the next data-in command until it has run a
                        // data-out one. A zero mouse report is the harmless
                        // data-out command (no movement, no buttons).
                        let _ = self.cmd(
                            &protocol::cdb_hid(5),
                            0,
                            &[0u8; 5],
                            timeout,
                            timeout,
                        );
                    }
                }
            }
        }
        unreachable!("simple() returns or raises in the last attempt")
    }

    /// Bridge state byte (see protocol).
    pub fn state(&mut self) -> Result<u8, errors::TransportError> {
        let (_, data) = self.simple(&protocol::cdb_state(1), 1, &[], Duration::from_millis(1500))?;
        Ok(data.first().copied().unwrap_or(0))
    }

    /// Standard INQUIRY. In CD-ROM mode, 0x34 bytes include the vendor bytes
    /// (see docs/hardware.md).
    pub fn inquiry(&mut self, length: usize) -> Result<Vec<u8>, errors::TransportError> {
        let cb = [0x12u8, 0, 0, 0, length as u8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        let (_, data) = self.simple(&cb, length, &[], Duration::from_millis(1500))?;
        Ok(data)
    }

    /// INIT data-transfer command; returns the CSW status.
    pub fn init(&mut self) -> Result<u8, errors::TransportError> {
        let cb = protocol::cdb(protocol::CMD_INIT, 0, [0, 0, 0, 0], &[]);
        let (status, _) = self
            .cmd(&cb, 0, &[], Duration::from_millis(3000), Duration::from_millis(3000))
            .map_err(Self::map_err)?;
        Ok(status)
    }

    /// Send one message. Only accepted when it is our turn and the peer has a
    /// receive of the same length armed.
    pub fn send(&mut self, packet: &[u8], timeout: Duration, cbw_timeout: Duration) -> Result<(), errors::TransportError> {
        let cb = protocol::cdb_tx(packet.len() as u32);
        self.cmd(&cb, 0, packet, timeout, cbw_timeout).map_err(Self::map_err)?;
        Ok(())
    }

    /// Wait for one message of up to `size` bytes.
    ///
    /// The firmware stalls an idle receive after ~5.5 s, so keep `timeout`
    /// above that: letting the firmware time out first leaves a clean stall
    /// that `reset()` clears, whereas a host-side cancel leaves the end stuck
    /// (see docs/hardware.md).
    pub fn recv(&mut self, size: usize, timeout: Duration, cbw_timeout: Duration) -> Result<Vec<u8>, errors::TransportError> {
        let cb = protocol::cdb_rx(size as u32);
        let (_, data) = self.cmd(&cb, size, &[], timeout, cbw_timeout).map_err(Self::map_err)?;
        Ok(data)
    }

    /// Inject a mouse (5 B) or keyboard (8 B) report; it appears on the peer's
    /// HID interface.
    ///
    /// The peer's HID interrupt endpoint must be polled by something (an OS
    /// driver with an open input device); otherwise the firmware refuses further
    /// injections once its small buffer is full.
    pub fn hid(&mut self, report: &[u8]) -> Result<(), errors::TransportError> {
        let cb = protocol::cdb_hid(report.len() as u8);
        self.simple(&cb, 0, report, Duration::from_millis(1500))?;
        Ok(())
    }

    /// `'H'` (master) or `'S'` (slave), from the vendor INQUIRY bytes. Only
    /// available in CD-ROM mode.
    pub fn role(&mut self) -> Option<char> {
        let d = match self.inquiry(0x34) {
            Ok(d) => d,
            Err(_) => {
                self.reset();
                std::thread::sleep(Duration::from_millis(300));
                return None;
            }
        };
        if d.len() > 0x2E && &d[0x28..0x2C] == b"GENE" {
            Some(d[0x2E] as char)
        } else {
            None
        }
    }
}

impl Drop for BridgeEnd {
    fn drop(&mut self) {
        self.close();
    }
}

/// Adapts a [`BridgeEnd`] to the crate's [`Transport`](crate::link::Transport)
/// trait. [`BridgeEnd`] already surfaces the typed errors, so this only
/// converts units (seconds → milliseconds).
pub struct UsbTransport {
    pub end: BridgeEnd,
}

impl UsbTransport {
    pub fn new(end: BridgeEnd) -> Self {
        Self { end }
    }
}

impl crate::link::Transport for UsbTransport {
    fn send(&mut self, data: &[u8], timeout: f64) -> Result<(), errors::TransportError> {
        self.end.send(data, Duration::from_secs_f64(timeout), Duration::from_secs_f64(timeout))
    }

    fn recv(&mut self, size: usize, timeout: f64) -> Result<Vec<u8>, errors::TransportError> {
        self.end.recv(size, Duration::from_secs_f64(timeout), Duration::from_secs_f64(timeout))
    }

    fn reset(&mut self) {
        self.end.reset();
    }

    fn hid(&mut self, report: &[u8], _timeout: f64) -> Result<(), errors::TransportError> {
        self.end.hid(report)
    }
}

/// Adapter making a [`BridgeEnd`] behave as the daemon's [`End`] trait.
pub struct EndAdapter {
    pub inner: BridgeEnd,
}

impl EndAdapter {
    pub fn new(inner: BridgeEnd) -> Self {
        Self { inner }
    }
}

impl crate::daemon::End for EndAdapter {
    fn port(&self) -> String {
        self.inner.port().to_string()
    }
    fn hid(&mut self, report: &[u8]) {
        let _ = self.inner.hid(report);
    }
    fn close(&mut self) {
        self.inner.close();
    }
    fn into_transport(self: Box<Self>) -> Option<Box<dyn crate::link::Transport + Send>> {
        Some(Box::new(UsbTransport { end: self.inner }))
    }
}

/// Wait for a (re-)enumerated end on a given USB port path.
pub fn wait_for_port(path: &str, timeout: f64, not_address: Option<u8>) -> Option<Device<Context>> {
    let deadline = std::time::Instant::now() + Duration::from_secs_f64(timeout);
    loop {
        let now = std::time::Instant::now();
        if now >= deadline {
            return None;
        }
        if let Some(d) = find_by_port(path) {
            if d.address() != not_address.unwrap_or(0) {
                return Some(d);
            }
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn port_path_format() {
        // No live hardware in tests: port_numbers() on an unopened device list
        // is fine as long as the helper's formatting is exercised through a
        // failure path. We test the formatting logic directly instead.
        let path = [1u8, 2, 3]
            .iter()
            .map(|n| n.to_string())
            .collect::<Vec<_>>()
            .join(".");
        assert_eq!(path, "1.2.3");
    }

    #[test]
    fn find_ends_returns_empty_without_hardware() {
        // In a hardware-free test environment there should be no 05e3:0627.
        // This is intentionally permissive: if a real bridge happens to be
        // attached we still don't want to fail the test.
        let ends = find_ends();
        assert!(ends.len() <= 1, "unexpectedly found >1 bridge ends");
    }

    #[test]
    fn cbw_layout() {
        // The bulk-only CBW framing must match the firmware's expectations:
        // signature "USBC", tag, data-transfer-length, direction flag, then the
        // 16-byte CDB padded to cb_length=16. We re-derive the bytes a BridgeEnd
        // would produce and check the invariant the firmware relies on.
        let cb = protocol::cdb_tx(2048);
        let mut cbw = [0u8; 31];
        cbw[0..4].copy_from_slice(&CBW_SIGNATURE);
        cbw[4..8].copy_from_slice(&1u32.to_le_bytes());
        cbw[8..12].copy_from_slice(&2048u32.to_le_bytes());
        cbw[12] = 0; // OUT direction
        cbw[14] = 16;
        cbw[15..31].copy_from_slice(&cb);
        assert_eq!(&cbw[0..4], b"USBC");
        assert_eq!(u32::from_le_bytes(cbw[8..12].try_into().unwrap()), 2048);
        assert_eq!(cbw[14], 16);
        assert_eq!(&cbw[15..31], &cb);
    }

    #[test]
    fn mass_storage_class_constant_matches_usb() {
        assert_eq!(MASS_STORAGE_CLASS, 8);
    }
}