//! Clipboard sharing over the link. Ported from `src/openaten/clipboard.py`.

use crate::appmsg;
use serde_json::Value;
use sha2::{Digest, Sha256};

pub const TEXT: &str = "text/plain";
pub const PNG: &str = "image/png";
pub const MAX_BYTES: usize = 8 << 20;
pub const MAX_BACKOFF: f64 = 30.0;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Clip {
    pub mime: String,
    pub data: Vec<u8>,
}

impl Clip {
    pub fn digest(&self) -> String {
        let mut h = Sha256::new();
        h.update(self.mime.as_bytes());
        h.update(b"\0");
        h.update(&self.data);
        let out = h.finalize();
        out.iter().map(|b| format!("{:02x}", b)).collect()
    }
}

/// The shared MIME type to read, given what the clipboard offers: text wins
/// over an image.
pub fn pick(types: &[String]) -> Option<&'static str> {
    let lowered: Vec<String> = types.iter().map(|t| t.to_lowercase()).collect();
    if lowered.iter().any(|t| t.starts_with("text/plain") || t == "utf8_string" || t == "string") {
        return Some(TEXT);
    }
    if lowered.iter().any(|t| t == PNG) {
        return Some(PNG);
    }
    None
}

/// The clipboard state machine. `send` queues a link message; `poll` is called
/// regularly from the daemon's main loop.
pub struct ClipboardSync<SendFn>
where
    SendFn: FnMut(&[u8]) -> bool,
{
    pub send: SendFn,
    pub max_bytes: usize,
    interval: f64,
    now: Box<dyn Fn() -> f64>,
    _next: f64,
    _delay: f64,
    _last: Option<String>,
    _primed: bool,
    // reads/writes delegate to a backend closure supplied by the platform layer
    read_clip: Box<dyn Fn() -> Result<Option<Clip>, String>>,
    write_clip: Box<dyn Fn(&Clip) -> Result<(), String>>,
}

impl<SendFn> ClipboardSync<SendFn>
where
    SendFn: FnMut(&[u8]) -> bool,
{
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        send: SendFn,
        read: Box<dyn Fn() -> Result<Option<Clip>, String>>,
        write: Box<dyn Fn(&Clip) -> Result<(), String>>,
        max_bytes: usize,
        interval: f64,
        now: Box<dyn Fn() -> f64>,
    ) -> Self {
        Self { send, max_bytes, interval, now, _next: 0.0, _delay: interval, _last: None, _primed: false, read_clip: read, write_clip: write }
    }

    pub fn poll(&mut self) {
        let t = (self.now)();
        if t < self._next {
            return;
        }
        let clip = match (self.read_clip)() {
            Ok(c) => c,
            Err(_) => {
                self._delay = (self._delay * 2.0).min(MAX_BACKOFF);
                self._next = t + self._delay;
                return;
            }
        };
        self._delay = self.interval;
        self._next = t + self.interval;
        if !self._primed {
            self._primed = true;
            self._last = clip.as_ref().map(|c| c.digest());
            return;
        }
        let clip = match clip {
            Some(c) => c,
            None => return,
        };
        if clip.digest() == self._last.as_deref().unwrap_or("") {
            return;
        }
        if clip.data.len() > self.max_bytes {
            self._last = Some(clip.digest());
            return;
        }
        let digest = clip.digest();
        let fields = vec![
            ("mime", Value::String(clip.mime.clone())),
            ("sha", Value::String(digest.clone())),
        ];
        let msg = appmsg::pack("clip", &clip.data, &fields);
        if (self.send)(&msg) {
            self._last = Some(digest);
        }
    }

    /// Handle an incoming clipboard message from the peer.
    pub fn on_message(&mut self, header: &Value, payload: &[u8]) {
        if header.get("kind").and_then(|k| k.as_str()) != Some("clip") {
            return;
        }
        let mime = header.get("mime").and_then(|m| m.as_str()).unwrap_or("");
        if (mime != TEXT && mime != PNG) || payload.len() > self.max_bytes || payload.is_empty() {
            return;
        }
        let clip = Clip { mime: mime.to_string(), data: payload.to_vec() };
        if header.get("sha").and_then(|s| s.as_str()) != Some(&clip.digest()) {
            return;
        }
        self._last = Some(clip.digest());
        if (self.write_clip)(&clip).is_err() {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn type_selection_prefers_text() {
        assert_eq!(pick(&["text/plain;charset=utf-8".into(), "image/png".into()]), Some(TEXT));
        assert_eq!(pick(&["UTF8_STRING".into()]), Some(TEXT));
        assert_eq!(pick(&["image/png".into()]), Some(PNG));
        assert_eq!(pick(&["image/jpeg".into()]), None);
        assert_eq!(pick(&[]), None);
    }

    #[test]
    fn clip_digest() {
        let c1 = Clip { mime: TEXT.into(), data: b"hi".to_vec() };
        let c2 = Clip { mime: PNG.into(), data: b"hi".to_vec() };
        assert_ne!(c1.digest(), c2.digest());
        assert_eq!(c1.digest(), c1.digest());
    }
}