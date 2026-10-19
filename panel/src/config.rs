//! Reading and writing the one setting the window edits: `warp` in `[daemon]` of `~/.config/openaten/config.toml`.
//!
//! The file is the user's: it may hold other options and comments, so it is edited in place with `toml_edit` (formatting preserved) and a
//! `.bak` copy of the previous version is written before every change. The text functions are pure and unit-tested.

use std::path::{Path, PathBuf};

use toml_edit::{value, DocumentMut, Item, Table};

pub const WARP_ON: &str = "uinput";
pub const WARP_OFF: &str = "none";

/// `$XDG_CONFIG_HOME/openaten/config.toml`, else `~/.config/openaten/config.toml` (the same place the daemon reads).
pub fn config_path() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("openaten").join("config.toml")
}

/// Whether `[daemon] warp` asks for the uinput pointer warp. A missing or unreadable file means no.
pub fn get_warp(text: &str) -> bool {
    // `get`, not indexing: toml_edit panics on a missing key when indexed
    text.parse::<DocumentMut>().ok().and_then(|doc| doc.get("daemon")?.get("warp")?.as_str().map(|v| v == WARP_ON)).unwrap_or(false)
}

/// The file's text with `[daemon] warp` set, everything else untouched. Errors when the text is not valid TOML or `daemon` is not a table.
pub fn set_warp(text: &str, on: bool) -> Result<String, String> {
    let mut doc: DocumentMut = text.parse().map_err(|e| format!("config.toml is not valid TOML: {e}"))?;
    if doc.get("daemon").is_none() {
        doc["daemon"] = Item::Table(Table::new());
    }
    let Some(daemon) = doc["daemon"].as_table_mut() else {
        return Err("`daemon` in config.toml is not a table".into());
    };
    daemon["warp"] = value(if on { WARP_ON } else { WARP_OFF });
    Ok(doc.to_string())
}

/// Read the file (empty when it does not exist), set `warp`, and write it back after copying the old version to `config.toml.bak`.
pub fn save_warp(path: &Path, on: bool) -> Result<(), String> {
    let old = match std::fs::read_to_string(path) {
        Ok(text) => Some(text),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(format!("cannot read {}: {e}", path.display())),
    };
    let new = set_warp(old.as_deref().unwrap_or(""), on)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    }
    if let Some(old) = &old {
        let mut backup = path.as_os_str().to_owned();
        backup.push(".bak");
        std::fs::write(&backup, old).map_err(|e| format!("cannot write the backup: {e}"))?;
    }
    std::fs::write(path, new).map_err(|e| format!("cannot write {}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn warp_defaults_to_off() {
        assert!(!get_warp(""));
        assert!(!get_warp("[daemon]\nrole = \"master\"\n"));
        assert!(!get_warp("[daemon]\nwarp = \"none\"\n"));
        assert!(!get_warp("not toml ["), "an unreadable file means off");
        assert!(get_warp("[daemon]\nwarp = \"uinput\"\n"));
    }

    #[test]
    fn setting_warp_on_an_empty_file_creates_the_table() {
        let out = set_warp("", true).unwrap();
        assert!(get_warp(&out));
        assert!(out.contains("[daemon]"));
    }

    #[test]
    fn other_keys_and_comments_survive_a_change() {
        let before = "log_level = \"DEBUG\"  # chatty\n\n[daemon]\n# which side is this\nrole = \"master\"\nwarp = \"none\"  # off for now\nclipboard = true\n\n[forward]\nhotkey = \"ctrl+alt+k\"\n";
        let after = set_warp(before, true).unwrap();
        assert!(after.contains("log_level = \"DEBUG\"  # chatty"));
        assert!(after.contains("# which side is this"));
        assert!(after.contains("role = \"master\""));
        assert!(after.contains("clipboard = true"));
        assert!(after.contains("[forward]"));
        assert!(after.contains("hotkey = \"ctrl+alt+k\""));
        assert!(get_warp(&after));
        assert_eq!(set_warp(&after, false).unwrap().replace("warp = \"none\"", "warp = \"uinput\""), after, "flipping back changes only the value");
    }

    #[test]
    fn invalid_files_and_a_non_table_daemon_are_refused_not_overwritten() {
        assert!(set_warp("this is [ not toml", true).unwrap_err().contains("not valid TOML"));
        assert!(set_warp("daemon = 3\n", true).unwrap_err().contains("not a table"));
    }

    #[test]
    fn saving_writes_a_backup_of_the_previous_file() {
        let dir = std::env::temp_dir().join(format!("openaten-panel-test-{}", std::process::id()));
        let path = dir.join("openaten").join("config.toml");
        let _ = std::fs::remove_dir_all(&dir);
        save_warp(&path, true).unwrap(); // no file yet: created, no backup
        assert!(get_warp(&std::fs::read_to_string(&path).unwrap()));
        assert!(!dir.join("openaten").join("config.toml.bak").exists());
        let first = std::fs::read_to_string(&path).unwrap();
        save_warp(&path, false).unwrap();
        assert!(!get_warp(&std::fs::read_to_string(&path).unwrap()));
        assert_eq!(std::fs::read_to_string(dir.join("openaten").join("config.toml.bak")).unwrap(), first, "the backup is the version before the change");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
