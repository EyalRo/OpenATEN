//! `openaten-panel`: the OpenATEN tray icon. It shows the state of the running `openaten daemon` (read from its status file) and toggles
//! forwarding on click. The settings panel (layout editor, permissions) will live in this same app; see docs.
//!
//! Built like the other tray apps here: ksni (StatusNotifierItem over D-Bus), a polling thread, and nothing computed inside `update()`
//! (it runs in ksni's own runtime).

mod icons;
mod state;

use std::path::PathBuf;
use std::process::Command;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ksni::blocking::TrayMethods;
use ksni::menu::StandardItem;
use ksni::{MenuItem, ToolTip};
use state::{classify, tooltip_body, Snapshot};

const POLL: Duration = Duration::from_secs(1);

fn status_path() -> PathBuf {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"));
    runtime.join("openaten").join("status.json")
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn read_snapshot() -> Snapshot {
    classify(std::fs::read_to_string(status_path()).ok().as_deref(), now())
}

/// The `openaten` command: `$OPENATEN_BIN`, else `~/.local/bin/openaten` when present, else whatever is on PATH.
fn openaten_bin() -> PathBuf {
    if let Some(bin) = std::env::var_os("OPENATEN_BIN") {
        return PathBuf::from(bin);
    }
    if let Some(home) = std::env::var_os("HOME") {
        let local = PathBuf::from(home).join(".local/bin/openaten");
        if local.exists() {
            return local;
        }
    }
    PathBuf::from("openaten")
}

fn toggle_forwarding() {
    match Command::new(openaten_bin()).args(["ctl", "toggle"]).spawn() {
        Ok(mut child) => {
            thread::spawn(move || {
                let _ = child.wait(); // reap it
            });
        }
        Err(e) => eprintln!("openaten-panel: cannot run `openaten ctl toggle`: {e}"),
    }
}

struct OpenAtenTray {
    snapshot: Snapshot,
}

impl ksni::Tray for OpenAtenTray {
    fn id(&self) -> String {
        "openaten".into()
    }
    fn title(&self) -> String {
        "OpenATEN".into()
    }
    fn icon_name(&self) -> String {
        self.snapshot.state.icon_name().into()
    }
    fn icon_pixmap(&self) -> Vec<ksni::Icon> {
        icons::pixmaps(self.snapshot.state)
    }
    fn tool_tip(&self) -> ToolTip {
        ToolTip {
            title: format!("OpenATEN: {}", self.snapshot.state.headline()),
            description: tooltip_body(&self.snapshot),
            icon_name: String::new(),
            icon_pixmap: Vec::new(),
        }
    }
    fn activate(&mut self, _x: i32, _y: i32) {
        toggle_forwarding();
    }
    fn menu(&self) -> Vec<MenuItem<Self>> {
        vec![
            StandardItem { label: self.snapshot.state.headline().into(), enabled: false, ..Default::default() }.into(),
            MenuItem::Separator,
            StandardItem {
                label: "Toggle forwarding".into(),
                icon_name: "input-keyboard".into(),
                activate: Box::new(|_| toggle_forwarding()),
                ..Default::default()
            }
            .into(),
            MenuItem::Separator,
            StandardItem {
                label: "Quit".into(),
                icon_name: "application-exit".into(),
                activate: Box::new(|_| std::process::exit(0)),
                ..Default::default()
            }
            .into(),
        ]
    }
}

fn main() -> anyhow::Result<()> {
    if std::env::args().any(|a| a == "--status") {
        let snap = read_snapshot();
        println!("{:?} ({})", snap.state, snap.state.icon_name());
        for (k, v) in &snap.rows {
            println!("  {k}: {v}");
        }
        return Ok(());
    }
    let mut last = read_snapshot();
    let tray = OpenAtenTray { snapshot: last.clone() }.assume_sni_available(true).spawn()?;
    loop {
        thread::sleep(POLL);
        let snapshot = read_snapshot(); // read here, outside update(): that closure runs inside ksni's runtime
        if snapshot != last {
            let shown = snapshot.clone();
            tray.update(move |t| t.snapshot = shown);
            last = snapshot;
        }
    }
}
