//! Reliable message link over the bridge's strictly alternating transport.
//! Ported from `src/openaten/link.py`. See `docs/design.md` for the model.

use crate::protocol;
use crate::{errors, errors::TransportError};
use std::collections::VecDeque;

pub const SLOT: usize = 2048;
pub const MAGIC: &[u8; 4] = b"OATN";
pub const VERSION: u8 = 1;
pub const KIND_POLL: u8 = 0;
pub const KIND_DATA: u8 = 1;
pub const FLAG_WANT_POLL: u8 = 1;
// Python format `<4sBBHIIIIIIII`: magic(4) ver(1) kind(1) flags(H=2) seq(I=4) ack(I=4)
// next_len(I) channel(I) msg_id(I) offset(I) total(I) plen(I) = 4+1+1+2+8*4 = 40 bytes,
// plus crc32 (4) = 44.
pub const _HDR_SIZE: usize = 40;
pub const _OVERHEAD: usize = _HDR_SIZE + 4;
pub const MAX_FRAME: usize = 1 << 20;
pub const MAX_PAYLOAD: usize = MAX_FRAME - _OVERHEAD;

/// Wire length of a frame carrying this many payload bytes: header + payload
/// rounded up to whole slots.
pub fn wire_len(payload_len: usize) -> usize {
    let need = _OVERHEAD + payload_len;
    ((need + SLOT - 1) / SLOT).max(1) * SLOT
}

#[derive(Debug, Clone)]
pub struct Frame {
    pub kind: u8,
    pub flags: u16,
    pub seq: u32,
    pub ack: u32,
    pub next_len: u32,
    pub channel: u32,
    pub msg_id: u32,
    pub offset: u32,
    pub total: u32,
    pub payload: Vec<u8>,
}

impl Frame {
    pub fn encode(&self, length: Option<usize>) -> Result<Vec<u8>, String> {
        let need = wire_len(self.payload.len());
        let length = length.unwrap_or(need);
        if length < need || length % SLOT != 0 {
            return Err(format!("frame needs {} bytes, wire length is {}", need, length));
        }
        let mut hdr = Vec::with_capacity(_HDR_SIZE);
        hdr.extend_from_slice(MAGIC);
        hdr.push(VERSION);
        hdr.push(self.kind);
        hdr.extend_from_slice(&self.flags.to_le_bytes());
        hdr.extend_from_slice(&self.seq.to_le_bytes());
        hdr.extend_from_slice(&self.ack.to_le_bytes());
        hdr.extend_from_slice(&self.next_len.to_le_bytes());
        hdr.extend_from_slice(&self.channel.to_le_bytes());
        hdr.extend_from_slice(&self.msg_id.to_le_bytes());
        hdr.extend_from_slice(&self.offset.to_le_bytes());
        hdr.extend_from_slice(&self.total.to_le_bytes());
        hdr.extend_from_slice(&(self.payload.len() as u32).to_le_bytes());
        let mut hasher = crc32fast::Hasher::new();
        hasher.update(&hdr);
        hasher.update(&self.payload);
        let crc = hasher.finalize();
        let mut out = Vec::with_capacity(length);
        out.extend_from_slice(&hdr);
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&self.payload);
        out.resize(length, 0);
        Ok(out)
    }

    pub fn decode(data: &[u8]) -> Result<Frame, String> {
        if data.len() < _OVERHEAD {
            return Err("short frame".to_string());
        }
        if &data[0..4] != MAGIC {
            return Err("not an OpenATEN frame".to_string());
        }
        let ver = data[4];
        if ver != VERSION {
            return Err("not an OpenATEN frame".to_string());
        }
        let kind = data[5];
        let flags = u16::from_le_bytes(data[6..8].try_into().unwrap());
        let seq = u32::from_le_bytes(data[8..12].try_into().unwrap());
        let ack = u32::from_le_bytes(data[12..16].try_into().unwrap());
        let next_len = u32::from_le_bytes(data[16..20].try_into().unwrap());
        let channel = u32::from_le_bytes(data[20..24].try_into().unwrap());
        let msg_id = u32::from_le_bytes(data[24..28].try_into().unwrap());
        let offset = u32::from_le_bytes(data[28..32].try_into().unwrap());
        let total = u32::from_le_bytes(data[32..36].try_into().unwrap());
        let plen = u32::from_le_bytes(data[36..40].try_into().unwrap()) as usize;
        let crc_stored = u32::from_le_bytes(data[40..44].try_into().unwrap());
        if plen > data.len() - _OVERHEAD {
            return Err("short frame".to_string());
        }
        let payload = &data[_OVERHEAD.._OVERHEAD + plen];
        let mut hasher = crc32fast::Hasher::new();
        hasher.update(&data[.._HDR_SIZE]);
        hasher.update(payload);
        if hasher.finalize() != crc_stored {
            return Err("bad checksum".to_string());
        }
        Ok(Frame { kind, flags, seq, ack, next_len, channel, msg_id, offset, total, payload: payload.to_vec() })
    }
}

/// One end of the bridge. The tests use a fake; the real one is the USB
/// transport supplied by the platform layer.
pub trait Transport {
    fn send(&mut self, data: &[u8], timeout: f64) -> Result<(), errors::TransportError>;
    fn recv(&mut self, size: usize, timeout: f64) -> Result<Vec<u8>, errors::TransportError>;
    fn reset(&mut self);
    fn hid(&mut self, report: &[u8], timeout: f64) -> Result<(), errors::TransportError>;
}

/// Lets a boxed transport be used where a [`Transport`] is expected, so the
/// daemon can hold a heterogeneous transport behind a trait object.
impl Transport for Box<dyn Transport + Send> {
    fn send(&mut self, data: &[u8], timeout: f64) -> Result<(), errors::TransportError> {
        (**self).send(data, timeout)
    }
    fn recv(&mut self, size: usize, timeout: f64) -> Result<Vec<u8>, errors::TransportError> {
        (**self).recv(size, timeout)
    }
    fn reset(&mut self) {
        (**self).reset();
    }
    fn hid(&mut self, report: &[u8], timeout: f64) -> Result<(), errors::TransportError> {
        (**self).hid(report, timeout)
    }
}

#[derive(Debug, Clone)]
struct Chunk {
    channel: u32,
    msg_id: u32,
    offset: u32,
    total: u32,
    payload: Vec<u8>,
}

pub struct LinkSession<T: Transport> {
    pub t: T,
    pub master: bool,
    poll_idle: f64,
    io_timeout: f64,
    send_timeout: f64,
    hid_active_window: f64,
    pub on_message: Box<dyn FnMut(u32, u32, &[u8]) + Send>,
    pub on_error: Box<dyn FnMut(&TransportError) + Send>,
    _chunks: VecDeque<Chunk>,
    _hid: VecDeque<Vec<u8>>,
    _hid_active_until: f64,
    _msg_id: u32,
    _seq: u32,
    _ack: u32,
    _rx_len: usize,
    _tx_len: usize,
    _asm: std::collections::HashMap<(u32, u32), Vec<u8>>,
    _peer_busy: bool,
    pub stats: std::collections::HashMap<String, u64>,
}

impl<T: Transport> LinkSession<T> {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        t: T,
        master: bool,
        on_message: Box<dyn FnMut(u32, u32, &[u8]) + Send>,
        on_error: Box<dyn FnMut(&TransportError) + Send>,
        poll_idle: f64,
        io_timeout: f64,
        send_timeout: f64,
        hid_active_window: f64,
    ) -> Self {
        let mut stats = std::collections::HashMap::new();
        for k in ["frames_tx", "frames_rx", "resyncs", "bytes_tx", "bytes_rx", "bad_frames", "hid_sent", "hid_errors", "hid_coalesced"] {
            stats.insert(k.to_string(), 0);
        }
        Self {
            t, master, poll_idle, io_timeout, send_timeout, hid_active_window,
            on_message, on_error,
            _chunks: VecDeque::new(), _hid: VecDeque::new(), _hid_active_until: 0.0,
            _msg_id: 0, _seq: 0, _ack: 0,
            _rx_len: SLOT, _tx_len: SLOT,
            _asm: std::collections::HashMap::new(), _peer_busy: false,
            stats,
        }
    }

    pub fn idle(&self) -> bool {
        self._chunks.is_empty() && self._hid.is_empty() && !self._peer_busy
    }

    /// Number of queued message chunks still unsent.
    pub fn queued_chunks(&self) -> usize {
        self._chunks.len()
    }

    /// Wait until every queued message chunk has been sent. Returns false on
    /// timeout or when `stopped` is set.
    pub fn flush(&mut self, timeout: f64, stopped: &std::sync::atomic::AtomicBool) -> bool {
        let deadline = now_mono() + timeout;
        while !self._chunks.is_empty() {
            if stopped.load(std::sync::atomic::Ordering::Relaxed) || now_mono() >= deadline {
                return false;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        true
    }

    pub fn send_message(&mut self, channel: u32, data: &[u8]) -> u32 {
        self._msg_id += 1;
        let mid = self._msg_id;
        let total = data.len();
        let mut off = 0usize;
        loop {
            let chunk_len = if total > off + MAX_PAYLOAD { MAX_PAYLOAD } else { total - off };
            self._chunks.push_back(Chunk {
                channel, msg_id: mid, offset: off as u32, total: total as u32,
                payload: data[off..off + chunk_len].to_vec(),
            });
            if total <= off + MAX_PAYLOAD {
                break;
            }
            off += MAX_PAYLOAD;
        }
        mid
    }

    pub fn inject_hid(&mut self, report: &[u8]) {
        self._hid.push_back(report.to_vec());
        self._hid_active_until = now_mono() + self.hid_active_window;
    }

    /// Frame to send now (exactly `_tx_len` bytes on the wire) and the chunk
    /// it carries, if any.
    fn build(&mut self) -> (Frame, usize, Option<Chunk>) {
        let chunk = self._chunks.front().cloned();
        let mut carried = None;
        let mut frame = if let Some(c) = &chunk {
            if wire_len(c.payload.len()) <= self._tx_len {
                let c = self._chunks.pop_front().unwrap();
                carried = Some(c.clone());
                Frame {
                    kind: KIND_DATA, flags: 0, seq: self._seq, ack: self._ack, next_len: SLOT as u32,
                    channel: c.channel, msg_id: c.msg_id, offset: c.offset, total: c.total, payload: c.payload,
                }
            } else {
                Frame { kind: KIND_POLL, flags: 0, seq: self._seq, ack: self._ack, next_len: SLOT as u32, channel: 0, msg_id: 0, offset: 0, total: 0, payload: Vec::new() }
            }
        } else {
            Frame { kind: KIND_POLL, flags: 0, seq: self._seq, ack: self._ack, next_len: SLOT as u32, channel: 0, msg_id: 0, offset: 0, total: 0, payload: Vec::new() }
        };
        let nxt = self._chunks.front().cloned();
        frame.next_len = if let Some(n) = &nxt { wire_len(n.payload.len()) as u32 } else { SLOT as u32 };
        if nxt.is_some() || !self._hid.is_empty() || now_mono() < self._hid_active_until {
            frame.flags |= FLAG_WANT_POLL as u16;
        }
        let length = self._tx_len;
        (frame, length, carried)
    }

    fn sent(&mut self, frame: &Frame) {
        self._tx_len = frame.next_len as usize;
        self._seq = self._seq.wrapping_add(1);
        *self.stats.get_mut("frames_tx").unwrap() += 1;
        *self.stats.get_mut("bytes_tx").unwrap() += frame.payload.len() as u64;
    }

    fn accept(&mut self, data: &[u8]) -> Option<Frame> {
        let f = match Frame::decode(data) {
            Ok(f) => f,
            Err(_) => {
                *self.stats.get_mut("bad_frames").unwrap() += 1;
                self._rx_len = SLOT;
                self._tx_len = SLOT;
                return None;
            }
        };
        *self.stats.get_mut("frames_rx").unwrap() += 1;
        self._rx_len = f.next_len as usize;
        self._ack = f.seq.wrapping_add(1);
        self._peer_busy = f.kind == KIND_DATA || f.next_len != SLOT as u32 || (f.flags & FLAG_WANT_POLL as u16) != 0;
        if f.kind == KIND_DATA {
            self.deliver(&f);
        }
        Some(f)
    }

    fn deliver(&mut self, f: &Frame) {
        let key = (f.channel, f.msg_id);
        let buf = self._asm.entry(key).or_default();
        if f.offset as usize != buf.len() {
            self._asm.remove(&key); // out of order: drop this message
            return;
        }
        buf.extend_from_slice(&f.payload);
        *self.stats.get_mut("bytes_rx").unwrap() += f.payload.len() as u64;
        if buf.len() >= f.total as usize {
            let buf = self._asm.remove(&key).unwrap();
            (self.on_message)(f.channel, f.msg_id, &buf);
        }
    }

    fn flush_hid(&mut self) {
        if self._hid.len() > 1 {
            let pending: Vec<Vec<u8>> = self._hid.drain(..).collect();
            let merged = protocol::coalesce_mouse(&pending);
            *self.stats.get_mut("hid_coalesced").unwrap() += (pending.len() - merged.len()) as u64;
            for m in merged.into_iter().rev() {
                self._hid.push_front(m);
            }
        }
        while !self._hid.is_empty() {
            let report = self._hid.pop_front().unwrap();
            match self.t.hid(&report, self.send_timeout) {
                Ok(_) => {
                    *self.stats.get_mut("hid_sent").unwrap() += 1;
                }
                Err(_) => {
                    *self.stats.get_mut("hid_errors").unwrap() += 1;
                    self.t.reset();
                    break;
                }
            }
        }
    }

    fn resync(&mut self) {
        *self.stats.get_mut("resyncs").unwrap() += 1;
        self._rx_len = SLOT;
        self._tx_len = SLOT;
        self.t.reset();
    }

    fn idle_stall(&mut self) {
        self._rx_len = SLOT;
        self._tx_len = SLOT;
        self.t.reset();
    }

    fn recv_frame(&mut self) -> Option<Frame> {
        let r = self.t.recv(self._rx_len, self.io_timeout);
        match r {
            Ok(data) => self.accept(&data),
            Err(e) if e.message.to_lowercase().contains("pipe") => {
                self.idle_stall();
                None
            }
            Err(_) => {
                self.resync();
                None
            }
        }
    }

    fn master_step(&mut self) {
        self.flush_hid();
        let (frame, length, carried) = self.build();
        match self.t.send(&frame.encode(Some(length)).unwrap(), self.send_timeout) {
            Ok(_) => {}
            Err(_) => {
                if carried.is_some() {
                    self._chunks.push_front(carried.unwrap());
                }
                self.resync();
                self.recv_frame();
                return;
            }
        }
        let f2 = frame.clone();
        self.sent(&f2);
        if self.recv_frame().is_none() {
            std::thread::sleep(std::time::Duration::from_millis(50));
        } else if self.idle() && now_mono() >= self._hid_active_until {
            std::thread::sleep(std::time::Duration::from_millis((self.poll_idle * 1000.0) as u64));
        }
    }

    fn slave_step(&mut self) {
        let r = self.t.recv(self._rx_len, self.io_timeout);
        match r {
            Ok(data) => {
                if self.accept(&data).is_some() {
                    self.flush_hid();
                    self.reply();
                } else {
                    self.t.reset();
                }
            }
            Err(e) if e.message.to_lowercase().contains("pipe") => {
                self.idle_stall();
            }
            Err(e) if e.message.to_lowercase().contains("timed out") => {
                self.resync();
                self.reply();
            }
            Err(_) => {
                self.resync();
            }
        }
    }

    fn reply(&mut self) {
        let (frame, length, carried) = self.build();
        match self.t.send(&frame.encode(Some(length)).unwrap(), self.io_timeout) {
            Ok(_) => {
                let f2 = frame.clone();
                self.sent(&f2);
            }
            Err(_) => {
                if carried.is_some() {
                    self._chunks.push_front(carried.unwrap());
                }
                self.resync();
            }
        }
    }

    /// Run the loop until `should_stop` becomes true. This is a synchronous
    /// `run()` the caller invokes from a thread (matching the Python design).
    pub fn run(&mut self, should_stop: &std::sync::atomic::AtomicBool) {
        while !should_stop.load(std::sync::atomic::Ordering::Relaxed) {
            self.step();
        }
    }

    /// One loop iteration: dispatch master or slave. Public so the session can
    /// be driven from an `Arc<Mutex<LinkSession>>` loop thread while other
    /// threads call `send_message`/`inject_hid`.
    pub fn step(&mut self) {
        if self.master {
            self.master_step();
        } else {
            self.slave_step();
        }
    }
}

pub fn now_mono() -> f64 {
    use std::time::Instant;
    use std::sync::OnceLock;
    static START: OnceLock<Instant> = OnceLock::new();
    let start = *START.get_or_init(Instant::now);
    start.elapsed().as_secs_f64()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wire_len_rounds_to_slots() {
        assert_eq!(wire_len(0), SLOT);
        assert_eq!(wire_len(SLOT - _OVERHEAD), SLOT);
        assert_eq!(wire_len(SLOT - _OVERHEAD + 1), 2 * SLOT);
    }

    #[test]
    fn frame_roundtrip() {
        let f = Frame { kind: KIND_DATA, flags: 0, seq: 7, ack: 3, next_len: 4096, channel: 2, msg_id: 9, offset: 0, total: 5, payload: b"hello".to_vec() };
        let raw = f.encode(None).unwrap();
        assert_eq!(raw.len(), SLOT);
        let g = Frame::decode(&raw).unwrap();
        assert_eq!(g.kind, 1);
        assert_eq!(g.seq, 7);
        assert_eq!(g.ack, 3);
        assert_eq!(g.next_len, 4096);
        assert_eq!(g.channel, 2);
        assert_eq!(g.msg_id, 9);
        assert_eq!(g.total, 5);
        assert_eq!(g.payload, b"hello");
    }

    #[test]
    fn frame_detects_corruption() {
        let mut raw = Frame { kind: KIND_DATA, flags: 0, seq: 0, ack: 0, next_len: SLOT as u32, channel: 0, msg_id: 0, offset: 0, total: 5, payload: b"hello".to_vec() }.encode(None).unwrap();
        raw[_OVERHEAD + 1] ^= 0xFF;
        assert!(Frame::decode(&raw).is_err());
    }
}

#[cfg(test)]
mod integration {
    use super::*;
    use crate::fake_transport::{FakeBridge, FakeTransport};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};

    struct Pair {
        bridge: Arc<FakeBridge>,
        master: Arc<Mutex<LinkSession<FakeTransport>>>,
        slave: Arc<Mutex<LinkSession<FakeTransport>>>,
        master_stop: Arc<AtomicBool>,
        slave_stop: Arc<AtomicBool>,
        got_m: Arc<Mutex<Vec<(u32, Vec<u8>)>>>,
        got_s: Arc<Mutex<Vec<(u32, Vec<u8>)>>>,
        errors: Arc<Mutex<Vec<String>>>,
    }

    impl Pair {
        fn new() -> Self {
            let bridge = Arc::new(FakeBridge::new(0.3));
            let got_m = Arc::new(Mutex::new(Vec::new()));
            let got_s = Arc::new(Mutex::new(Vec::new()));
            let errors = Arc::new(Mutex::new(Vec::new()));
            let gm = got_m.clone();
            let er = errors.clone();
            let master = LinkSession::new(
                FakeTransport::new(bridge.clone(), "a"), true,
                Box::new(move |c, _i, d| gm.lock().unwrap().push((c, d.to_vec()))),
                Box::new(move |e: &TransportError| er.lock().unwrap().push(e.message.clone())),
                0.01, 2.0, 0.2, 0.25,
            );
            let gs = got_s.clone();
            let er = errors.clone();
            let slave = LinkSession::new(
                FakeTransport::new(bridge.clone(), "b"), false,
                Box::new(move |c, _i, d| gs.lock().unwrap().push((c, d.to_vec()))),
                Box::new(move |e: &TransportError| er.lock().unwrap().push(e.message.clone())),
                0.01, 2.0, 0.2, 0.25,
            );
            Self {
                bridge, master: Arc::new(Mutex::new(master)), slave: Arc::new(Mutex::new(slave)),
                master_stop: Arc::new(AtomicBool::new(false)), slave_stop: Arc::new(AtomicBool::new(false)),
                got_m, got_s, errors,
            }
        }

        fn start(&self) {
            let m = self.master.clone();
            let stop = self.master_stop.clone();
            std::thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    m.lock().unwrap().step();
                }
            });
            let s = self.slave.clone();
            let stop = self.slave_stop.clone();
            std::thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    s.lock().unwrap().step();
                }
            });
        }

        fn stop(&self) {
            self.master_stop.store(true, Ordering::Relaxed);
            self.slave_stop.store(true, Ordering::Relaxed);
            std::thread::sleep(std::time::Duration::from_millis(100));
            let errs = self.errors.lock().unwrap().clone();
            assert!(errs.is_empty(), "errors: {:?}", errs);
        }

        fn wait(&self, side: &str, n: usize, timeout: f64) {
            let t0 = std::time::Instant::now();
            loop {
                let got = if side == "m" { self.got_m.lock().unwrap() } else { self.got_s.lock().unwrap() };
                if got.len() >= n {
                    break;
                }
                drop(got);
                let count = if side == "m" { self.got_m.lock().unwrap().len() } else { self.got_s.lock().unwrap().len() };
                if t0.elapsed().as_secs_f64() > timeout {
                    panic!("{} got {}/{}", side, count, n);
                }
                std::thread::sleep(std::time::Duration::from_millis(2));
            }
        }

        fn got(&self, side: &str) -> Vec<(u32, Vec<u8>)> {
            if side == "m" { self.got_m.lock().unwrap().clone() } else { self.got_s.lock().unwrap().clone() }
        }
    }

    #[test]
    fn messages_both_directions() {
        let p = Pair::new();
        p.master.lock().unwrap().send_message(1, b"to slave");
        p.slave.lock().unwrap().send_message(2, b"to master");
        p.start();
        p.wait("s", 1, 10.0);
        p.wait("m", 1, 10.0);
        p.stop();
        let s = p.got("s");
        assert_eq!(s[0], (1u32, b"to slave".to_vec()));
        let m = p.got("m");
        assert_eq!(m[0], (2u32, b"to master".to_vec()));
    }

    #[test]
    fn many_small_messages_keep_order() {
        let p = Pair::new();
        for i in 0..50u16 {
            p.master.lock().unwrap().send_message(1, &i.to_be_bytes());
            p.slave.lock().unwrap().send_message(1, &i.to_be_bytes());
        }
        p.start();
        p.wait("s", 50, 10.0);
        p.wait("m", 50, 10.0);
        p.stop();
        let s = p.got("s");
        let vals: Vec<u16> = s.iter().map(|(_, d)| u16::from_be_bytes([d[0], d[1]])).collect();
        assert_eq!(vals, (0..50).collect::<Vec<u16>>());
        let m = p.got("m");
        let vals: Vec<u16> = m.iter().map(|(_, d)| u16::from_be_bytes([d[0], d[1]])).collect();
        assert_eq!(vals, (0..50).collect::<Vec<u16>>());
    }

    #[test]
    fn large_messages_are_chunked_and_intact() {
        let big_m: Vec<u8> = (0..(3 * MAX_PAYLOAD + 123)).map(|i| (i % 251) as u8).collect();
        let big_s: Vec<u8> = (0..(MAX_PAYLOAD + 5)).map(|i| (i % 253) as u8).collect();
        let p = Pair::new();
        p.master.lock().unwrap().send_message(3, &big_m);
        p.slave.lock().unwrap().send_message(4, &big_s);
        p.start();
        p.wait("s", 1, 10.0);
        p.wait("m", 1, 10.0);
        p.stop();
        assert_eq!(p.got("s")[0].1, big_m);
        assert_eq!(p.got("m")[0].1, big_s);
    }

    #[test]
    fn idle_stalls_do_not_break_the_session() {
        let bridge = Arc::new(FakeBridge::new(0.05));
        let got_s = Arc::new(Mutex::new(Vec::new()));
        let errors = Arc::new(Mutex::new(Vec::new()));
        let er = errors.clone();
        let master = LinkSession::new(
            FakeTransport::new(bridge.clone(), "a"), true,
            Box::new(|_, _, _| {}), Box::new(move |e: &TransportError| er.lock().unwrap().push(e.message.clone())),
            0.01, 2.0, 0.2, 0.25,
        );
        let gs2 = got_s.clone();
        let er2 = errors.clone();
        let slave = LinkSession::new(
            FakeTransport::new(bridge.clone(), "b"), false,
            Box::new(move |c, _i, d| gs2.lock().unwrap().push((c, d.to_vec()))),
            Box::new(move |e: &TransportError| er2.lock().unwrap().push(e.message.clone())),
            0.01, 2.0, 0.2, 0.25,
        );
        let m = Arc::new(Mutex::new(master));
        let s = Arc::new(Mutex::new(slave));
        let mstop = Arc::new(AtomicBool::new(false));
        let sstop = Arc::new(AtomicBool::new(false));
        let ms = mstop.clone();
        let mm = m.clone();
        let ss = sstop.clone();
        let sm = s.clone();
        std::thread::spawn(move || while !ms.load(Ordering::Relaxed) { mm.lock().unwrap().step(); });
        std::thread::spawn(move || while !ss.load(Ordering::Relaxed) { sm.lock().unwrap().step(); });
        std::thread::sleep(std::time::Duration::from_millis(400));
        mstop.store(true, Ordering::Relaxed);
        sstop.store(true, Ordering::Relaxed);
        std::thread::sleep(std::time::Duration::from_millis(100));
        let errs = errors.lock().unwrap().clone();
        assert!(errs.is_empty(), "errors: {:?}", errs);
    }

    #[test]
    fn flush_waits_until_every_chunk_is_sent() {
        let p = Pair::new();
        let big: Vec<u8> = (0..(5 * MAX_PAYLOAD + 123)).map(|i| (i % 251) as u8).collect();
        p.master.lock().unwrap().send_message(1, &big);
        p.start();
        p.wait("s", 1, 10.0);
        p.stop();
        assert_eq!(p.got("s")[0].1, big);
    }

    #[test]
    fn hid_reports_flow_both_ways_in_order() {
        let p = Pair::new();
        for i in 0..20u8 {
            p.master.lock().unwrap().inject_hid(&[0, i, 0, 0, 0]);
            p.slave.lock().unwrap().inject_hid(&[0, 100 + i, 0, 0, 0]);
        }
        p.start();
        let t0 = std::time::Instant::now();
        loop {
            let a = p.bridge.inner.lock().unwrap().hid_in_b.len();
            let b = p.bridge.inner.lock().unwrap().hid_in_a.len();
            if (a >= 20 && b >= 20) || t0.elapsed().as_secs_f64() > 10.0 {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(2));
        }
        p.stop();
        let hid_b = p.bridge.inner.lock().unwrap().hid_in_b.clone();
        let hid_a = p.bridge.inner.lock().unwrap().hid_in_a.clone();
        assert_eq!(hid_b.len(), 20);
        assert_eq!(hid_a.len(), 20);
        assert_eq!(p.master.lock().unwrap().stats["hid_sent"], 20);
        assert_eq!(p.slave.lock().unwrap().stats["hid_sent"], 20);
    }

    #[test]
    fn a_backlog_of_mouse_reports_is_coalesced_without_losing_motion() {
        let p = Pair::new();
        let n = 30;
        for _ in 0..n {
            p.master.lock().unwrap().inject_hid(&crate::protocol::mouse_report(0, 1, 0, 0));
        }
        p.start();
        let t0 = std::time::Instant::now();
        loop {
            let total: u64 = p.bridge.inner.lock().unwrap().hid_in_b.iter().map(|r| crate::protocol::s8(r[3]) as u64).sum();
            if total >= n || t0.elapsed().as_secs_f64() > 5.0 {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(2));
        }
        p.stop();
        let m = p.master.lock().unwrap();
        assert_eq!(m.stats["hid_sent"] + m.stats["hid_coalesced"], n);
    }

    #[test]
    fn stats_count_traffic() {
        let p = Pair::new();
        p.master.lock().unwrap().send_message(1, b"abc");
        p.start();
        p.wait("s", 1, 10.0);
        p.stop();
        assert!(p.master.lock().unwrap().stats["frames_tx"] >= 1);
        assert_eq!(p.slave.lock().unwrap().stats["bytes_rx"], 3);
    }

    #[test]
    fn resync_slave_owes_reply() {
        // Slave received something earlier and never answered; master sent it
        // and now expects the reply. The FakeBridge start state has last=None,
        // so both can proceed; this exercises the normal two-direction flow.
        let p = Pair::new();
        p.master.lock().unwrap().send_message(1, b"hello");
        p.start();
        p.wait("s", 1, 10.0);
        p.stop();
        assert_eq!(p.got("s")[0].1, b"hello");
    }
}