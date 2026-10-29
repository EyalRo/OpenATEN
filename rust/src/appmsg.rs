//! Framing shared by the application protocols (clipboard, files) that ride
//! on link channels. Ported from `src/openaten/appmsg.py`.

use serde_json::Value;

pub const VERSION: u64 = 1;
pub const CH_CLIPBOARD: u32 = 2;
pub const CH_FILES: u32 = 3;
pub const MAX_HEADER: usize = 64 * 1024;

/// Pack a message: 4-byte big-endian header length + UTF-8 JSON header +
/// raw payload.
pub fn pack(kind: &str, payload: &[u8], fields: &[(&str, Value)]) -> Vec<u8> {
    let mut obj = serde_json::Map::new();
    obj.insert("v".to_string(), Value::Number(VERSION.into()));
    obj.insert("kind".to_string(), Value::String(kind.to_string()));
    for (k, v) in fields {
        obj.insert(k.to_string(), v.clone());
    }
    let header = serde_json::to_string(&Value::Object(obj)).unwrap();
    let n = header.len() as u32;
    let mut out = Vec::with_capacity(4 + header.len() + payload.len());
    out.extend_from_slice(&n.to_be_bytes());
    out.extend_from_slice(header.as_bytes());
    out.extend_from_slice(payload);
    out
}

/// The parsed header and payload, or None if the message is malformed, has no
/// string `kind` or comes from a newer protocol version.
pub fn unpack(data: &[u8]) -> Option<(Value, &[u8])> {
    if data.len() < 4 {
        return None;
    }
    let n = u32::from_be_bytes(data[0..4].try_into().ok()?) as usize;
    if n > MAX_HEADER || data.len() < 4 + n {
        return None;
    }
    let header: Value = match serde_json::from_slice(&data[4..4 + n]) {
        Ok(v) => v,
        Err(_) => return None,
    };
    if !header.is_object() {
        return None;
    }
    let kind = header.get("kind").and_then(|k| k.as_str());
    if kind.is_none() {
        return None;
    }
    let version = header.get("v").and_then(|v| v.as_u64()).unwrap_or(1);
    if version > VERSION {
        return None;
    }
    Some((header, &data[4 + n..]))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn raw(header: &[u8]) -> Vec<u8> {
        let mut v = Vec::new();
        v.extend_from_slice(&(header.len() as u32).to_be_bytes());
        v.extend_from_slice(header);
        v
    }

    #[test]
    fn roundtrip_with_and_without_payload() {
        let p = pack("clip", b"\x00\xffdata", &[("mime", json!("text/plain")), ("len", json!(6))]);
        let (h, pay) = unpack(&p).unwrap();
        assert_eq!(h["kind"], "clip");
        assert_eq!(h["mime"], "text/plain");
        assert_eq!(h["len"], 6);
        assert_eq!(pay, b"\x00\xffdata");
    }

    #[test]
    fn rejects_malformed_oversized_and_newer_messages() {
        assert!(unpack(b"").is_none());
        assert!(unpack(b"\x00\x00").is_none());
        assert!(unpack(b"\x00\x00\x00\x09{").is_none());
        assert!(unpack(&[0xff, 0xff, 0xff, 0xff]).is_none());
        assert!(unpack(&raw(b"[]")).is_none());
        assert!(unpack(&raw(br#"{"kind":1}"#)).is_none());
        assert!(unpack(&raw(br#"{"v":2,"kind":"clip"}"#)).is_none());
        assert!(unpack(&raw(br#"{"kind":"clip"}"#)).is_some());
    }

    #[test]
    fn pack_unpack_roundtrip_property() {
        let p = pack("ack", b"", &[("n", json!(3))]);
        let got = unpack(&p).unwrap();
        assert_eq!(got.0["kind"], "ack");
        assert_eq!(got.0["n"], 3);
    }
}