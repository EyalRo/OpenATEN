//! What the daemon's status file says, reduced to what the tray shows. Pure functions only (no D-Bus, no files), so they are unit-tested.
//!
//! The daemon (`openaten daemon`) rewrites `$XDG_RUNTIME_DIR/openaten/status.json` on every change and at least every few seconds. The
//! classification mirrors the old Noctalia widget: a missing or stale file means the daemon is not running.

use serde::Deserialize;

/// A status file older than this (seconds) means the daemon is gone.
pub const STALE_SECONDS: f64 = 6.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// Daemon not running, or running without a bridge.
    Offline,
    /// Bridge present, forwarding off.
    Connected,
    /// Keyboard and mouse are going to the other computer.
    Forwarding,
    Error,
}

impl State {
    /// Themed icon name (installed under share/icons/hicolor/scalable/apps).
    pub fn icon_name(self) -> &'static str {
        match self {
            State::Offline => "openaten-offline",
            State::Connected => "openaten-connected",
            State::Forwarding => "openaten-forwarding",
            State::Error => "openaten-error",
        }
    }

    /// Label of the menu entry that flips forwarding, and whether it can work (the daemon must be running).
    pub fn toggle_label(self) -> (&'static str, bool) {
        match self {
            State::Offline => ("Toggle forwarding", false),
            State::Connected | State::Error => ("Start forwarding", true),
            State::Forwarding => ("Return control to this computer", true),
        }
    }

    pub fn headline(self) -> &'static str {
        match self {
            State::Offline => "Offline",
            State::Connected => "Connected, forwarding off",
            State::Forwarding => "Forwarding to the other computer",
            State::Error => "Error",
        }
    }
}

/// The fields of status.json the tray uses. Every field is optional: older daemons report fewer.
#[derive(Debug, Default, Deserialize)]
pub struct Status {
    pub ts: Option<f64>,
    pub bridge: Option<String>,
    pub forwarding: Option<bool>,
    pub error: Option<String>,
    pub role: Option<String>,
    pub hotkey: Option<String>,
    pub edges: Option<String>,
    pub warp: Option<String>,
    pub uinput: Option<String>,
    pub devices: Option<Vec<String>>,
    pub transfers: Option<Vec<serde_json::Value>>,
}

/// Whether the daemon's user can create uinput devices (what the pointer warp needs), as the daemon reports it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Uinput {
    Ok,
    Denied,
    Missing,
    /// The daemon is not running or did not say.
    Unknown,
}

impl Uinput {
    fn parse(s: Option<&str>) -> Uinput {
        match s {
            Some("ok") => Uinput::Ok,
            Some("denied") => Uinput::Denied,
            Some("missing") => Uinput::Missing,
            _ => Uinput::Unknown,
        }
    }
}

/// What the tray and the window display: the state, the tooltip rows (label, value) and the pointer-warp facts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub state: State,
    pub rows: Vec<(String, String)>,
    pub uinput: Uinput,
    /// The running daemon uses the uinput pointer warp.
    pub warp_running: bool,
}

impl Snapshot {
    pub fn offline(reason: &str) -> Self {
        Snapshot { state: State::Offline, rows: vec![("State".into(), reason.into())], uinput: Uinput::Unknown, warp_running: false }
    }
}

fn nonempty(s: &Option<String>) -> Option<&str> {
    s.as_deref().filter(|v| !v.is_empty())
}

/// Classify a status file's text. `now` is the current Unix time in seconds.
pub fn classify(text: Option<&str>, now: f64) -> Snapshot {
    let Some(text) = text else { return Snapshot::offline("daemon not running") };
    let Ok(st) = serde_json::from_str::<Status>(text) else { return Snapshot::offline("unreadable status file") };
    if now - st.ts.unwrap_or(0.0) > STALE_SECONDS {
        return Snapshot::offline("daemon not running");
    }
    let state = if nonempty(&st.error).is_some() {
        State::Error
    } else if st.forwarding == Some(true) {
        State::Forwarding
    } else if st.bridge.as_deref() == Some("present") {
        State::Connected
    } else {
        State::Offline
    };
    let mut rows = vec![("State".to_string(), state.headline().to_string())];
    rows.push((
        "Bridge".into(),
        if st.bridge.as_deref() == Some("present") { "connected" } else { "not found" }.into(),
    ));
    if let Some(role) = nonempty(&st.role) {
        rows.push(("Role".into(), role.into()));
    }
    if let Some(n) = st.devices.as_ref().map(Vec::len).filter(|n| *n > 0) {
        rows.push(("Input devices".into(), n.to_string()));
    }
    if let Some(key) = nonempty(&st.hotkey) {
        rows.push(("Hotkey".into(), key.into()));
    }
    if let Some(edges) = nonempty(&st.edges).filter(|e| *e != "off") {
        rows.push(("Edge hand-off".into(), edges.into()));
    }
    if let (Some(warp), Some(uinput)) = (nonempty(&st.warp), nonempty(&st.uinput)) {
        if warp != "none" || uinput != "ok" {
            rows.push(("Pointer warp".into(), format!("{warp} (/dev/uinput: {uinput})")));
        }
    }
    if let Some(n) = st.transfers.as_ref().map(Vec::len).filter(|n| *n > 0) {
        rows.push(("Transfers".into(), format!("{n} active")));
    }
    if let Some(err) = nonempty(&st.error) {
        rows.push(("Error".into(), err.into()));
    }
    Snapshot { state, rows, uinput: Uinput::parse(st.uinput.as_deref()), warp_running: st.warp.as_deref() == Some("uinput") }
}

/// Tooltip body: one "label: value" line per row after the headline.
pub fn tooltip_body(snapshot: &Snapshot) -> String {
    snapshot.rows.iter().skip(1).map(|(k, v)| format!("{k}: {v}")).collect::<Vec<_>>().join("\n")
}

/// Convert RGBA8 pixels to the ARGB32, network byte order, that StatusNotifierItem pixmaps use.
pub fn rgba_to_argb(rgba: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(rgba.len());
    for px in rgba.chunks_exact(4) {
        out.extend_from_slice(&[px[3], px[0], px[1], px[2]]);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: f64 = 1_000_000.0;

    fn status(extra: &str) -> String {
        format!(r#"{{"ts": {NOW}, {extra}}}"#)
    }

    #[test]
    fn a_missing_or_unreadable_file_means_offline() {
        assert_eq!(classify(None, NOW).state, State::Offline);
        assert_eq!(classify(Some("{not json"), NOW).state, State::Offline);
    }

    #[test]
    fn a_stale_status_means_the_daemon_is_gone() {
        let text = format!(r#"{{"ts": {}, "bridge": "present"}}"#, NOW - STALE_SECONDS - 1.0);
        let snap = classify(Some(&text), NOW);
        assert_eq!(snap.state, State::Offline);
        assert_eq!(snap.rows[0].1, "daemon not running");
        let fresh = format!(r#"{{"ts": {}, "bridge": "present"}}"#, NOW - STALE_SECONDS + 1.0);
        assert_eq!(classify(Some(&fresh), NOW).state, State::Connected);
    }

    #[test]
    fn the_state_follows_error_then_forwarding_then_bridge() {
        assert_eq!(classify(Some(&status(r#""bridge": "present", "forwarding": true, "error": "boom""#)), NOW).state, State::Error);
        assert_eq!(classify(Some(&status(r#""bridge": "present", "forwarding": true"#)), NOW).state, State::Forwarding);
        assert_eq!(classify(Some(&status(r#""bridge": "present", "forwarding": false"#)), NOW).state, State::Connected);
        assert_eq!(classify(Some(&status(r#""bridge": "absent""#)), NOW).state, State::Offline);
        assert_eq!(classify(Some(&status(r#""bridge": "present", "error": """#)), NOW).state, State::Connected, "an empty error is no error");
    }

    #[test]
    fn rows_include_only_what_the_daemon_reported() {
        let old = classify(Some(&status(r#""bridge": "present", "hotkey": "ctrl+alt+shift+k", "devices": ["a", "b"]"#)), NOW);
        let labels: Vec<_> = old.rows.iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(labels, ["State", "Bridge", "Input devices", "Hotkey"]);
        let new = classify(
            Some(&status(
                r#""bridge": "present", "role": "master", "edges": "ready", "warp": "uinput", "uinput": "denied", "transfers": [{}, {}], "devices": []"#,
            )),
            NOW,
        );
        let rows: std::collections::HashMap<_, _> = new.rows.iter().cloned().collect();
        assert_eq!(rows["Role"], "master");
        assert_eq!(rows["Edge hand-off"], "ready");
        assert_eq!(rows["Pointer warp"], "uinput (/dev/uinput: denied)");
        assert_eq!(rows["Transfers"], "2 active");
        assert!(!rows.contains_key("Input devices"), "no devices, no row");
    }

    #[test]
    fn warp_is_only_mentioned_when_it_is_on_or_cannot_work() {
        let quiet = classify(Some(&status(r#""bridge": "present", "warp": "none", "uinput": "ok""#)), NOW);
        assert!(quiet.rows.iter().all(|(k, _)| k != "Pointer warp"));
        let denied = classify(Some(&status(r#""bridge": "present", "warp": "none", "uinput": "denied""#)), NOW);
        assert!(denied.rows.iter().any(|(k, _)| k == "Pointer warp"));
    }

    #[test]
    fn the_snapshot_carries_the_uinput_access_and_whether_warp_is_running() {
        let on = classify(Some(&status(r#""bridge": "present", "warp": "uinput", "uinput": "ok""#)), NOW);
        assert_eq!((on.uinput, on.warp_running), (Uinput::Ok, true));
        let denied = classify(Some(&status(r#""bridge": "present", "warp": "none", "uinput": "denied""#)), NOW);
        assert_eq!((denied.uinput, denied.warp_running), (Uinput::Denied, false));
        let missing = classify(Some(&status(r#""bridge": "present", "uinput": "missing""#)), NOW);
        assert_eq!(missing.uinput, Uinput::Missing);
        let old = classify(Some(&status(r#""bridge": "present""#)), NOW);
        assert_eq!((old.uinput, old.warp_running), (Uinput::Unknown, false), "an older daemon does not say");
        assert_eq!(classify(None, NOW).uinput, Uinput::Unknown);
    }

    #[test]
    fn tooltip_body_skips_the_headline() {
        let snap = classify(Some(&status(r#""bridge": "present", "hotkey": "k""#)), NOW);
        assert_eq!(tooltip_body(&snap), "Bridge: connected\nHotkey: k");
    }

    #[test]
    fn pixels_become_argb_in_network_order() {
        assert_eq!(rgba_to_argb(&[1, 2, 3, 4, 10, 20, 30, 255]), vec![4, 1, 2, 3, 255, 10, 20, 30]);
    }

    #[test]
    fn the_toggle_entry_says_what_it_will_do_and_is_off_without_a_daemon() {
        assert_eq!(State::Offline.toggle_label(), ("Toggle forwarding", false));
        assert_eq!(State::Connected.toggle_label(), ("Start forwarding", true));
        assert_eq!(State::Forwarding.toggle_label(), ("Return control to this computer", true));
        assert!(State::Error.toggle_label().1, "forwarding can still be switched off after an error");
    }

    #[test]
    fn every_state_has_its_own_icon_name() {
        let names = [State::Offline, State::Connected, State::Forwarding, State::Error].map(State::icon_name);
        let unique: std::collections::HashSet<_> = names.iter().collect();
        assert_eq!(unique.len(), 4);
    }
}
