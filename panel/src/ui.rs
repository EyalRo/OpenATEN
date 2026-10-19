//! The setup window: a libadwaita window that mirrors the daemon's state and holds the settings. It is created hidden at start-up and shown or
//! hidden by the tray's left-click; closing the window only hides it.
//!
//! Sections: the daemon's state and forwarding button, the status rows, and Pointer warp (permission row with an explicit grant dialog, the warp
//! setting saved to config.toml with a backup, a Restart-to-apply button, and a Test button). The screen layout editor is still to come.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use adw::prelude::*;
use gtk::{gio, glib};

use crate::state::{Snapshot, State, Uinput};
use crate::{actions, config, icons, perm};

pub struct Ui {
    pub window: adw::ApplicationWindow,
    toasts: adw::ToastOverlay,
    icon: gtk::Image,
    headline: gtk::Label,
    toggle: gtk::Button,
    group: adw::PreferencesGroup,
    rows: RefCell<Vec<adw::ActionRow>>,
    perm_status: gtk::Label,
    grant: gtk::Button,
    warp_switch: adw::SwitchRow,
    restart_row: adw::ActionRow,
    test: gtk::Button,
    /// Programmatic changes to the switch must not look like the user clicking it.
    updating: Cell<bool>,
    last: RefCell<Snapshot>,
}

/// The state's icon from the embedded PNG, so the header never depends on the icon theme finding it.
fn icon_texture(state: State) -> gtk::gdk::Texture {
    gtk::gdk::Texture::from_bytes(&glib::Bytes::from_static(icons::png64(state))).expect("embedded icon is a valid PNG")
}

fn saved_warp() -> bool {
    config::get_warp(&std::fs::read_to_string(config::config_path()).unwrap_or_default())
}

impl Ui {
    pub fn new(app: &adw::Application, snapshot: &Snapshot) -> Rc<Ui> {
        let window = adw::ApplicationWindow::builder().application(app).title("OpenATEN").default_width(420).default_height(640).hide_on_close(true).build();

        let icon = gtk::Image::from_paintable(Some(&icon_texture(snapshot.state)));
        icon.set_pixel_size(72);
        let headline = gtk::Label::new(None);
        headline.add_css_class("title-2");
        headline.set_wrap(true);
        headline.set_justify(gtk::Justification::Center);
        let toggle = gtk::Button::new();
        toggle.add_css_class("suggested-action");
        toggle.add_css_class("pill");
        toggle.set_halign(gtk::Align::Center);
        toggle.connect_clicked(|_| actions::toggle_forwarding());
        let group = adw::PreferencesGroup::builder().title("Status").build();

        // -- Pointer warp --------------------------------------------------------------------------------------------------------------
        let warp_group = adw::PreferencesGroup::builder()
            .title("Pointer warp")
            .description("After a hand-off, put this computer's pointer where the other one came back instead of where it left.")
            .build();
        let perm_status = gtk::Label::new(None);
        perm_status.set_valign(gtk::Align::Center);
        let grant = gtk::Button::with_label("Grant access…");
        grant.set_valign(gtk::Align::Center);
        let perm_row = adw::ActionRow::builder().title("Access to /dev/uinput").build();
        perm_row.add_suffix(&perm_status);
        perm_row.add_suffix(&grant);
        let warp_switch = adw::SwitchRow::builder().title("Exact pointer placement").subtitle("Saved in config.toml; applies after the daemon restarts").build();
        warp_switch.set_active(saved_warp());
        let restart = gtk::Button::with_label("Restart daemon");
        restart.set_valign(gtk::Align::Center);
        restart.add_css_class("suggested-action");
        let restart_row = adw::ActionRow::builder().title("Restart to apply").subtitle("The saved setting differs from what the daemon is running").build();
        restart_row.add_suffix(&restart);
        restart_row.set_visible(false);
        let test = gtk::Button::with_label("Test");
        test.set_valign(gtk::Align::Center);
        let test_row = adw::ActionRow::builder().title("Test pointer warp").subtitle("Moves the pointer to four places so you can watch it").build();
        test_row.add_suffix(&test);
        warp_group.add(&perm_row);
        warp_group.add(&warp_switch);
        warp_group.add(&restart_row);
        warp_group.add(&test_row);

        let content = gtk::Box::new(gtk::Orientation::Vertical, 18);
        content.set_margin_top(24);
        content.set_margin_bottom(24);
        content.set_margin_start(18);
        content.set_margin_end(18);
        content.append(&icon);
        content.append(&headline);
        content.append(&toggle);
        content.append(&group);
        content.append(&warp_group);

        let clamp = adw::Clamp::builder().maximum_size(480).child(&content).build();
        let scroll = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).child(&clamp).build();
        let toasts = adw::ToastOverlay::new();
        toasts.set_child(Some(&scroll));
        let view = adw::ToolbarView::new();
        view.add_top_bar(&adw::HeaderBar::new());
        view.set_content(Some(&toasts));
        window.set_content(Some(&view));

        let ui = Rc::new(Ui {
            window,
            toasts,
            icon,
            headline,
            toggle,
            group,
            rows: RefCell::new(Vec::new()),
            perm_status,
            grant: grant.clone(),
            warp_switch: warp_switch.clone(),
            restart_row,
            test: test.clone(),
            updating: Cell::new(false),
            last: RefCell::new(snapshot.clone()),
        });

        grant.connect_clicked({
            let ui = ui.clone();
            move |_| ui.confirm_grant()
        });
        warp_switch.connect_active_notify({
            let ui = ui.clone();
            move |switch| {
                if !ui.updating.get() {
                    ui.save_warp(switch.is_active());
                }
            }
        });
        restart.connect_clicked({
            let ui = ui.clone();
            move |_| ui.restart_daemon()
        });
        test.connect_clicked({
            let ui = ui.clone();
            move |_| match actions::warp_test() {
                Ok(()) => ui.toast("Watch the pointer: it moves to four places"),
                Err(e) => ui.toast(&e),
            }
        });
        ui.update(snapshot);
        ui
    }

    fn toast(&self, text: &str) {
        self.toasts.add_toast(adw::Toast::new(text));
    }

    /// Show what the daemon reports now: icon, headline, buttons, status rows and the pointer warp section.
    pub fn update(&self, snapshot: &Snapshot) {
        *self.last.borrow_mut() = snapshot.clone();
        self.icon.set_paintable(Some(&icon_texture(snapshot.state)));
        self.headline.set_label(snapshot.state.headline());
        let (label, enabled) = snapshot.state.toggle_label();
        self.toggle.set_label(label);
        self.toggle.set_sensitive(enabled);

        let mut rows = self.rows.borrow_mut();
        for row in rows.drain(..) {
            self.group.remove(&row);
        }
        for (key, value) in snapshot.rows.iter().skip(1) {
            let row = adw::ActionRow::builder().title(key.as_str()).subtitle(value.as_str()).build();
            row.add_css_class("property");
            self.group.add(&row);
            rows.push(row);
        }
        drop(rows);

        let shown = perm::describe(snapshot.uinput);
        self.perm_status.set_label(shown.label);
        for class in ["success", "error", "dim-label"] {
            self.perm_status.remove_css_class(class);
        }
        self.perm_status.add_css_class(shown.css);
        self.perm_status.set_tooltip_text(Some(shown.detail));
        self.grant.set_visible(matches!(snapshot.uinput, Uinput::Denied));
        self.test.set_sensitive(snapshot.uinput == Uinput::Ok);
        self.restart_row.set_visible(saved_warp() != snapshot.warp_running && snapshot.state != State::Offline);
    }

    /// Ask before running anything privileged: show the exact command and what it does.
    fn confirm_grant(self: &Rc<Self>) {
        let body = match actions::grant_confirmation() {
            Ok(body) => body,
            Err(e) => return self.toast(&e),
        };
        let dialog = adw::AlertDialog::new(Some("Grant access to /dev/uinput?"), Some(&body));
        dialog.add_responses(&[("cancel", "Cancel"), ("run", "Run command")]);
        dialog.set_response_appearance("run", adw::ResponseAppearance::Suggested);
        dialog.set_default_response(Some("cancel"));
        dialog.set_close_response("cancel");
        let ui = self.clone();
        dialog.choose(Some(&self.window), gio::Cancellable::NONE, move |response| {
            if response != "run" {
                return;
            }
            let ui = ui.clone();
            glib::spawn_future_local(async move {
                match gio::spawn_blocking(actions::grant_uinput_access).await {
                    Ok(Ok(())) => ui.toast("Access granted. The status updates in a moment."),
                    Ok(Err(e)) => ui.toast(&e),
                    Err(_) => ui.toast("The command stopped unexpectedly"),
                }
            });
        });
    }

    fn save_warp(&self, on: bool) {
        match config::save_warp(&config::config_path(), on) {
            Ok(()) => {
                self.toast(if on { "Saved: pointer warp on. Restart the daemon to apply." } else { "Saved: pointer warp off. Restart the daemon to apply." });
                let snapshot = self.last.borrow().clone();
                self.update(&snapshot);
            }
            Err(e) => {
                self.toast(&e);
                self.updating.set(true);
                self.warp_switch.set_active(saved_warp()); // show what is really on disk
                self.updating.set(false);
            }
        }
    }

    fn restart_daemon(self: &Rc<Self>) {
        let ui = self.clone();
        glib::spawn_future_local(async move {
            match gio::spawn_blocking(actions::restart_daemon).await {
                Ok(Ok(())) => ui.toast("Daemon restarted"),
                Ok(Err(e)) => ui.toast(&e),
                Err(_) => ui.toast("The restart stopped unexpectedly"),
            }
        });
    }
}
