//! Optional `config.toml`: defaults for command-line options. Precedence:
//! command line > environment > file > built-in default.
//! Ported from `src/openaten/config.py`.

use serde_json::{json, Value};
use std::path::PathBuf;

/// Per-command options a file may set: key -> accepted JSON value kinds.
pub fn command_keys() -> Vec<(&'static str, Vec<(&'static str, &'static [&'static str])>)> {
    // kind strings: "str" | "num" (int/float) | "bool"
    vec![
        ("forward", vec![("port", &["str"] as &[&str]), ("hotkey", &["str"]), ("max_active", &["num"]), ("screens", &["str"])]),
        (
            "daemon",
            vec![
                ("port", &["str"]),
                ("hotkey", &["str"]),
                ("max_active", &["num"]),
                ("screens", &["str"]),
                ("role", &["str"]),
                ("clipboard", &["bool"]),
                ("warp", &["str"]),
                ("receive_dir", &["str"]),
            ],
        ),
        ("strip", vec![("screens", &["str"])]),
        ("layout", vec![("screens", &["str"])]),
    ]
}

pub fn choices(key: &str) -> Option<&'static [&'static str]> {
    match key {
        "role" => Some(&["master", "slave"]),
        _ => None,
    }
}

pub fn default_path() -> PathBuf {
    let base = std::env::var("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("~")).join(".config"));
    base.join("openaten").join("config.toml")
}

/// Validate a value against the accepted kinds; returns whether it is valid.
fn valid(key: &str, value: &toml::Value, kinds: &[&str]) -> bool {
    let ok = match value {
        toml::Value::String(_) => kinds.contains(&"str"),
        toml::Value::Integer(_) | toml::Value::Float(_) => kinds.contains(&"num"),
        toml::Value::Boolean(_) => kinds.contains(&"bool"),
        _ => false,
    };
    if !ok {
        return false;
    }
    if let Some(ch) = choices(key) {
        let v = value.as_str().unwrap_or("");
        if !ch.contains(&v) {
            return false;
        }
    }
    true
}

/// `(log_level, {command: {dest: value}})` parsed from the file; an empty map
/// when the default file is missing. Bad values are skipped; an unreadable or
/// malformed file returns None (caller decides whether that is fatal).
pub fn load(path: &PathBuf) -> Option<(Option<String>, std::collections::BTreeMap<String, Value>)> {
    if !path.exists() {
        return None;
    }
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return None,
    };
    let doc: toml::Table = match text.parse() {
        Ok(d) => d,
        Err(_) => return None,
    };
    let mut log_level: Option<String> = None;
    let mut out: std::collections::BTreeMap<String, Value> = std::collections::BTreeMap::new();
    let keys = command_keys();
    for (raw_key, value) in doc.iter() {
        if raw_key == "log_level" {
            if let Some(s) = value.as_str() {
                let up = s.to_uppercase();
                if super::log::LEVELS.contains(&up.as_str()) {
                    log_level = Some(up);
                }
            }
        } else if let Some((_, known)) = keys.iter().find(|(k, _)| *k == raw_key) {
            if let Some(section) = value.as_table() {
                let mut m = serde_json::Map::new();
                for (k, v) in section.iter() {
                    if let Some((_, kinds)) = known.iter().find(|(kk, _)| *kk == k) {
                        if valid(k, v, kinds) {
                            let val = if k == "screens" || k == "receive_dir" {
                                json!(expanduser(v.as_str().unwrap_or("")))
                            } else {
                                toml_value_to_json(v)
                            };
                            m.insert(k.clone(), val);
                        }
                    }
                }
                out.insert(raw_key.clone(), Value::Object(m));
            }
        }
    }
    Some((log_level, out))
}

/// Expand `~` in a screens/receive_dir value.
pub fn expanduser(p: &str) -> String {
    if let Some(rest) = p.strip_prefix("~/") {
        let home = std::env::var("HOME").unwrap_or_default();
        format!("{}/{}", home, rest)
    } else {
        p.to_string()
    }
}

/// A toml Value to a serde_json Value (toml 0.8 doesn't impl From directly for all).
#[allow(dead_code)]
fn toml_value_to_json(v: &toml::Value) -> Value {
    match v {
        toml::Value::String(s) => json!(s),
        toml::Value::Integer(i) => json!(i),
        toml::Value::Float(f) => json!(f),
        toml::Value::Boolean(b) => json!(b),
        toml::Value::Datetime(d) => json!(d.to_string()),
        toml::Value::Array(a) => Value::Array(a.iter().map(toml_value_to_json).collect()),
        toml::Value::Table(t) => {
            let mut m = serde_json::Map::new();
            for (k, v) in t {
                m.insert(k.clone(), toml_value_to_json(v));
            }
            Value::Object(m)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(dir: &std::path::Path, text: &str) -> PathBuf {
        std::fs::create_dir_all(dir).unwrap();
        let p = dir.join("config.toml");
        std::fs::write(&p, text).unwrap();
        p
    }

    #[test]
    fn missing_file_is_fine() {
        let p = std::env::temp_dir().join(format!("none-{}", std::process::id()));
        let r = load(&p);
        assert!(r.is_none());
    }

    #[test]
    fn valid_file_is_parsed_and_bad_values_are_skipped() {
        let dir = std::env::temp_dir().join(format!("cfg-{}", std::process::id()));
        let p = write(&dir, "log_level = \"debug\"\n[daemon]\nrole = \"master\"\nhotkey = 5\nmax_active = 30\nbogus = 1\n[nonsense]\nx = 1\n");
        let (ll, cfg) = load(&p).unwrap();
        assert_eq!(ll.unwrap(), "DEBUG");
        assert_eq!(cfg["daemon"]["role"], "master");
        assert_eq!(cfg["daemon"]["max_active"], 30);
        assert!(cfg["daemon"].get("hotkey").is_none());
        assert!(cfg["daemon"].get("bogus").is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn invalid_role_is_rejected() {
        let dir = std::env::temp_dir().join(format!("cfg2-{}", std::process::id()));
        let p = write(&dir, "[daemon]\nrole = \"boss\"\n");
        let (_, cfg) = load(&p).unwrap();
        assert!(cfg["daemon"].get("role").is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn malformed_file_is_skipped() {
        let dir = std::env::temp_dir().join(format!("cfg3-{}", std::process::id()));
        let p = write(&dir, "this is = = not toml");
        assert!(load(&p).is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn screens_path_is_expanded() {
        let dir = std::env::temp_dir().join(format!("cfg4-{}", std::process::id()));
        let p = write(&dir, "[layout]\nscreens = \"~/s.toml\"\n");
        let (_, cfg) = load(&p).unwrap();
        let home = std::env::var("HOME").unwrap();
        assert_eq!(cfg["layout"]["screens"], json!(format!("{}/s.toml", home)));
        let _ = std::fs::remove_dir_all(&dir);
    }
}