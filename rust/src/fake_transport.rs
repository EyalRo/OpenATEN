//! In-memory model of the two bridge ends, enforcing the firmware's turn
//! rules, for integration tests. Ported from `tests/fake_transport.py`.
//! A single bridge-wide mutex guards all end state, mirroring the Python
//! original's single `b.cond`.

use crate::errors;
use std::sync::{Arc, Condvar, Mutex};

#[derive(Default)]
pub struct EndState {
    pub last: Option<&'static str>, // 'tx' or 'rx': the next command must be the other one
    pub armed: Option<usize>,
    pub inbox: Option<Vec<u8>>,
}

pub struct BridgeInner {
    pub stall_after: f64,
    pub a: EndState,
    pub b: EndState,
    pub hid_in_a: Vec<Vec<u8>>,
    pub hid_in_b: Vec<Vec<u8>>,
}

pub struct FakeBridge {
    pub inner: Mutex<BridgeInner>,
    pub cv: Condvar,
}

impl FakeBridge {
    pub fn new(stall_after: f64) -> Self {
        Self {
            inner: Mutex::new(BridgeInner {
                stall_after,
                a: EndState::default(),
                b: EndState::default(),
                hid_in_a: Vec::new(),
                hid_in_b: Vec::new(),
            }),
            cv: Condvar::new(),
        }
    }
}

pub struct FakeTransport {
    pub bridge: Arc<FakeBridge>,
    pub side: &'static str, // "a" or "b"
}

impl FakeTransport {
    pub fn new(bridge: Arc<FakeBridge>, side: &'static str) -> Self {
        Self { bridge, side }
    }

    fn end<'g>(&self, g: &'g mut BridgeInner, side: &str) -> &'g mut EndState {
        if side == "a" {
            &mut g.a
        } else {
            &mut g.b
        }
    }

    fn peer_name(&self) -> &'static str {
        if self.side == "a" {
            "b"
        } else {
            "a"
        }
    }

    /// Return (my end state, peer end state) as mutable refs. a and b are
    /// distinct fields of BridgeInner, so this split borrow is sound.
    fn split_ends<'g>(&self, g: &'g mut BridgeInner) -> (&'g mut EndState, &'g mut EndState) {
        if self.side == "a" {
            (&mut g.a, &mut g.b)
        } else {
            (&mut g.b, &mut g.a)
        }
    }
}

fn timeout_err(msg: &str) -> errors::TransportError {
    errors::TransportError::new(msg)
}

impl crate::link::Transport for FakeTransport {
    fn send(&mut self, data: &[u8], timeout: f64) -> Result<(), errors::TransportError> {
        let b = self.bridge.clone();
        let mut g = b.inner.lock().unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout);
        loop {
            // refused while out of turn, or until the peer has an RX armed
            let refused = {
                let (my, peer) = self.split_ends(&mut g);
                my.last == Some("tx") || peer.armed.is_none()
            };
            if refused {
                let left = deadline.saturating_duration_since(std::time::Instant::now());
                if left.is_zero() {
                    return Err(timeout_err("send refused"));
                }
                let (s, _) = b.cv.wait_timeout(g, left).unwrap();
                g = s;
                continue;
            }
            break;
        }
        {
            let (my, peer) = self.split_ends(&mut g);
            if peer.armed != Some(data.len()) {
                return Err(errors::TransportError::new(format!(
                    "length mismatch: sent {}, peer armed {}",
                    data.len(),
                    peer.armed.unwrap_or(0)
                )));
            }
            peer.inbox = Some(data.to_vec());
            peer.armed = None;
            peer.last = Some("rx");
            my.last = Some("tx");
        }
        b.cv.notify_all();
        Ok(())
    }

    fn recv(&mut self, size: usize, timeout: f64) -> Result<Vec<u8>, errors::TransportError> {
        let b = self.bridge.clone();
        let mut g = b.inner.lock().unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout);
        loop {
            let blocked = {
                let my = self.end(&mut g, self.side);
                my.last == Some("rx")
            };
            if blocked {
                let left = deadline.saturating_duration_since(std::time::Instant::now());
                if left.is_zero() {
                    return Err(timeout_err("recv refused"));
                }
                let (s, _) = b.cv.wait_timeout(g, left).unwrap();
                g = s;
                continue;
            }
            break;
        }
        {
            let my = self.end(&mut g, self.side);
            my.armed = Some(size);
        }
        let stall_after = g.stall_after;
        let stall_at = std::time::Instant::now() + std::time::Duration::from_secs_f64(stall_after);
        loop {
            let has = {
                let my = self.end(&mut g, self.side);
                my.inbox.is_some()
            };
            if has {
                break;
            }
            let now = std::time::Instant::now();
            if now >= stall_at {
                let my = self.end(&mut g, self.side);
                my.armed = None;
                return Err(errors::TransportError::new("idle receive stalled by firmware"));
            }
            if now >= deadline {
                return Err(errors::TransportError::new("recv timed out"));
            }
            let wait_until = std::time::Instant::min(stall_at, deadline);
            let d = wait_until.saturating_duration_since(now);
            let (s, _) = b.cv.wait_timeout(g, d).unwrap();
            g = s;
        }
        {
            let my = self.end(&mut g, self.side);
            let data = my.inbox.take();
            my.armed = None;
            if let Some(d) = data {
                b.cv.notify_all();
                return Ok(d);
            }
        }
        Err(timeout_err("recv lost data"))
    }

    fn reset(&mut self) {
        let b = self.bridge.clone();
        let mut g = b.inner.lock().unwrap();
        let my = self.end(&mut g, self.side);
        my.armed = None;
        b.cv.notify_all();
    }

    fn hid(&mut self, report: &[u8], _timeout: f64) -> Result<(), errors::TransportError> {
        let b = self.bridge.clone();
        let mut g = b.inner.lock().unwrap();
        let peer_name = self.peer_name();
        let peer_in = if peer_name == "a" { &mut g.hid_in_a } else { &mut g.hid_in_b };
        peer_in.push(report.to_vec());
        Ok(())
    }
}