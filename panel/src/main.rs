//! `openaten-panel`: the OpenATEN tray icon and setup window. The tray shows the state of the running `openaten daemon` (read from its status file);
//! left-click shows or hides the setup window and right-click is the host's menu, the same split as the `mediawatch` tray app.
//!
//! Built like the other tray apps here: ksni (StatusNotifierItem over D-Bus) on its own thread, GTK4 + libadwaita on the main thread, an `mpsc`
//! channel from the tray callbacks to a glib timer, and nothing computed inside `update()` (it runs in ksni's own runtime).

mod icons;
mod state;
mod ui;

use std::cell::RefCell;
use std::path::PathBuf;
use std::process::Command;
use std::rc::Rc;
use std::sync::mpsc::{self, Sender};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use adw::prelude::*;
use gtk::glib;
use ksni::blocking::TrayMethods;
use ksni::menu::StandardItem;
use ksni::{MenuItem, ToolTip};
use state::{classify, tooltip_body, Snapshot};

const APP_ID: &str = "com.openaten.Panel";

enum UiCommand {
    /// Left-click: show the window, or hide it when it is showing.
    Toggle,
    /// Menu entry: show the window.
    Show,
}

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
    sender: Sender<UiCommand>,
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
        let _ = self.sender.send(UiCommand::Toggle);
    }
    fn menu(&self) -> Vec<MenuItem<Self>> {
        let show = self.sender.clone();
        let (toggle_label, toggle_enabled) = self.snapshot.state.toggle_label();
        let mut items: Vec<MenuItem<Self>> = vec![StandardItem { label: self.snapshot.state.headline().into(), enabled: false, ..Default::default() }.into()];
        // The details, read-only, so the menu shows the whole picture without hovering for the tooltip.
        for (key, value) in self.snapshot.rows.iter().skip(1) {
            items.push(StandardItem { label: format!("{key}: {value}"), enabled: false, ..Default::default() }.into());
        }
        items.push(MenuItem::Separator);
        items.push(
            StandardItem {
                label: "Open OpenATEN".into(),
                icon_name: "preferences-system".into(),
                activate: Box::new(move |_| {
                    let _ = show.send(UiCommand::Show);
                }),
                ..Default::default()
            }
            .into(),
        );
        items.push(
            StandardItem {
                label: toggle_label.into(),
                icon_name: "input-keyboard".into(),
                enabled: toggle_enabled,
                activate: Box::new(|_| toggle_forwarding()),
                ..Default::default()
            }
            .into(),
        );
        items.push(MenuItem::Separator);
        items.push(
            StandardItem {
                label: "Quit".into(),
                icon_name: "application-exit".into(),
                activate: Box::new(|_| std::process::exit(0)),
                ..Default::default()
            }
            .into(),
        );
        items
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

    adw::init()?;
    let app = adw::Application::builder().application_id(APP_ID).build();
    let _hold = app.hold(); // stay alive in the tray while the window is hidden
    let (sender, receiver) = mpsc::channel();
    let first = read_snapshot();
    let tray = OpenAtenTray { snapshot: first.clone(), sender }.assume_sni_available(true).spawn()?;
    let receiver = Rc::new(receiver);
    let window: Rc<RefCell<Option<Rc<ui::Ui>>>> = Rc::new(RefCell::new(None));

    app.connect_activate(move |app| {
        if let Some(ui) = window.borrow().as_ref() {
            ui.window.present(); // a second launch (from a launcher) shows the window
            return;
        }
        // The first activation is the start-up: stay in the tray, window hidden until asked for.
        let ui = ui::Ui::new(app, &first, toggle_forwarding);
        *window.borrow_mut() = Some(ui.clone());

        let commands = receiver.clone();
        let win = ui.window.clone();
        glib::timeout_add_local(Duration::from_millis(100), move || {
            while let Ok(command) = commands.try_recv() {
                match command {
                    UiCommand::Toggle if win.is_visible() => win.set_visible(false),
                    UiCommand::Toggle | UiCommand::Show => win.present(),
                }
            }
            glib::ControlFlow::Continue
        });

        let tray = tray.clone();
        let last = Rc::new(RefCell::new(first.clone()));
        glib::timeout_add_seconds_local(1, move || {
            let snapshot = read_snapshot(); // read here, outside update(): that closure runs inside ksni's runtime
            if snapshot != *last.borrow() {
                let shown = snapshot.clone();
                tray.update(move |t| t.snapshot = shown);
                ui.update(&snapshot);
                *last.borrow_mut() = snapshot;
            }
            glib::ControlFlow::Continue
        });
    });
    let _ = app.run_with_args::<&str>(&[]);
    Ok(())
}
