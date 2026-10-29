//! Hand-off protocol between the two OpenATEN agents, carried as small JSON
//! messages on the link's control channel. Ported from `src/openaten/control.py`.

use serde_json::Value;

pub const CH_CONTROL: u32 = 1;
pub const VERSION: u64 = 1;

/// Encode a control message.
pub fn encode(kind: &str, fields: &[(&str, Value)]) -> Vec<u8> {
    let mut obj = serde_json::Map::new();
    obj.insert("v".to_string(), Value::Number(VERSION.into()));
    obj.insert("kind".to_string(), Value::String(kind.to_string()));
    for (k, v) in fields {
        obj.insert(k.to_string(), v.clone());
    }
    serde_json::to_vec(&Value::Object(obj)).unwrap()
}

/// Decode a control message; None if it is malformed or from a newer version.
pub fn decode(data: &[u8]) -> Option<Value> {
    let msg: Value = serde_json::from_slice(data).ok()?;
    if !msg.is_object() {
        return None;
    }
    if !msg.get("kind").and_then(|k| k.as_str()).is_some() {
        return None;
    }
    let version = match msg.get("v") {
        Some(v) => match v.as_u64() {
            Some(n) => n,
            None => return None, // present but not an int
        },
        None => 1, // peers from before the field existed speak version 1
    };
    if version <= VERSION {
        Some(msg)
    } else {
        None
    }
}

type NowFn = Box<dyn Fn() -> f64>;

/// Who is receiving, and what a pointer entering or leaving a border strip
/// means right now.
pub struct Handoff {
    push: Box<dyn Fn(&str, f64)>,
    pull: Box<dyn Fn()>,
    send: Box<dyn Fn(&[u8])>,
    pub settle: f64,
    now: NowFn,
    pub receiving: bool,
    _armed: bool,
    _since: f64,
}

impl Handoff {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        push: Box<dyn Fn(&str, f64)>,
        pull: Box<dyn Fn()>,
        send: Box<dyn Fn(&[u8])>,
        settle: f64,
        now: Box<dyn Fn() -> f64>,
    ) -> Self {
        Self { push, pull, send, settle, now, receiving: false, _armed: false, _since: 0.0 }
    }

    pub fn edge(&mut self, border: &str, row: f64, enter: bool) {
        if !self.receiving {
            if enter {
                (self.push)(border, row);
            }
            return;
        }
        if (self.now)() - self._since < self.settle {
            return;
        }
        if !enter {
            self._armed = true;
        } else if self._armed {
            self.receiving = false;
            let d = encode("give-back", &[("border", Value::String(border.to_string())), ("row", Value::from(row))]);
            (self.send)(&d);
        }
    }

    pub fn started(&mut self, border: Option<&str>, row: Option<f64>) {
        let mut fields = Vec::new();
        if let Some(b) = border {
            fields.push(("border", Value::String(b.to_string())));
            fields.push(("row", Value::from(row.unwrap_or(0.0))));
        }
        let d = encode("take-control", &fields);
        (self.send)(&d);
    }

    pub fn stopped(&mut self) {
        let d = encode("release", &[]);
        (self.send)(&d);
    }

    pub fn message(&mut self, msg: &Value) {
        let kind = msg.get("kind").and_then(|k| k.as_str());
        match kind {
            Some("take-control") => {
                self.receiving = true;
                self._armed = false;
                self._since = (self.now)();
            }
            Some("release") => {
                self.receiving = false;
            }
            Some("give-back") => {
                (self.pull)();
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make(settle: f64) -> (Handoff, std::cell::RefCell<f64>, std::rc::Rc<std::cell::RefCell<Vec<String>>>) {
        let t = std::cell::RefCell::new(0.0f64);
        let log = std::rc::Rc::new(std::cell::RefCell::new(Vec::<String>::new()));
        let t2 = t.clone();
        let log2 = log.clone();
        let log3 = log.clone();
        let log_send = log.clone();
        let h = Handoff::new(
            Box::new(move |b: &str, r: f64| log2.borrow_mut().push(format!("push {} {}", b, r))),
            Box::new(move || log3.borrow_mut().push("pull".to_string())),
            Box::new(move |d: &[u8]| {
                let m = decode(d).unwrap();
                log_send.borrow_mut().push(format!("send {}", serde_json::to_string(&m).unwrap()));
            }),
            settle,
            Box::new(move || *t2.borrow()),
        );
        (h, t, log)
    }

    #[test]
    fn encode_decode_roundtrip_and_garbage() {
        assert!(decode(&encode("give-back", &[("border", json!("B")), ("row", json!(5.0))])).is_some());
        assert!(decode(b"not json").is_none());
        assert!(decode(b"[1]").is_none());
    }

    #[test]
    fn edge_enter_on_the_sending_side_starts_forwarding() {
        let (mut h, _, log) = make(0.5);
        h.edge("B", 600.0, true);
        assert_eq!(*log.borrow(), vec!["push B 600".to_string()]);
        h.started(Some("B"), Some(600.0));
        h.stopped();
        let l = log.borrow();
        assert!(l[1].contains("take-control"));
        assert!(l[2].contains("release"));
    }

    #[test]
    fn messages_from_a_newer_protocol_version_are_ignored() {
        assert!(decode(br#"{"v": 2, "kind": "take-control"}"#).is_none());
        assert!(decode(br#"{"v": "x", "kind": "release"}"#).is_none());
        assert!(decode(br#"{"kind": "release"}"#).is_some());
    }
}