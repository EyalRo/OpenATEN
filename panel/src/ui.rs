//! The setup window: a libadwaita window that mirrors the daemon's state and has the forwarding button. It is created hidden at start-up and shown
//! or hidden by the tray's left-click; closing the window only hides it. The screen layout editor and the permission rows will be added here.

use std::cell::RefCell;
use std::rc::Rc;

use adw::prelude::*;
use gtk::glib;

use crate::icons;
use crate::state::{Snapshot, State};

pub struct Ui {
    pub window: adw::ApplicationWindow,
    icon: gtk::Image,
    headline: gtk::Label,
    toggle: gtk::Button,
    group: adw::PreferencesGroup,
    rows: RefCell<Vec<adw::ActionRow>>,
}

/// The state's icon from the embedded PNG, so the header never depends on the icon theme finding it.
fn icon_texture(state: State) -> gtk::gdk::Texture {
    gtk::gdk::Texture::from_bytes(&glib::Bytes::from_static(icons::png64(state))).expect("embedded icon is a valid PNG")
}

impl Ui {
    pub fn new(app: &adw::Application, snapshot: &Snapshot, on_toggle: impl Fn() + 'static) -> Rc<Ui> {
        let window = adw::ApplicationWindow::builder()
            .application(app)
            .title("OpenATEN")
            .default_width(420)
            .default_height(560)
            .hide_on_close(true)
            .build();

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
        toggle.connect_clicked(move |_| on_toggle());
        let group = adw::PreferencesGroup::builder().title("Status").build();

        let content = gtk::Box::new(gtk::Orientation::Vertical, 18);
        content.set_margin_top(24);
        content.set_margin_bottom(24);
        content.set_margin_start(18);
        content.set_margin_end(18);
        content.append(&icon);
        content.append(&headline);
        content.append(&toggle);
        content.append(&group);

        let clamp = adw::Clamp::builder().maximum_size(480).child(&content).build();
        let scroll = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).child(&clamp).build();
        let view = adw::ToolbarView::new();
        view.add_top_bar(&adw::HeaderBar::new());
        view.set_content(Some(&scroll));
        window.set_content(Some(&view));

        let ui = Rc::new(Ui { window, icon, headline, toggle, group, rows: RefCell::new(Vec::new()) });
        ui.update(snapshot);
        ui
    }

    /// Show the daemon's current state: icon, headline, forwarding button and the detail rows.
    pub fn update(&self, snapshot: &Snapshot) {
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
    }
}
