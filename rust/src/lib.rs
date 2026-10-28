//! OpenATEN core library: the wire protocol, reliable link, application
//! protocols and the daemon logic, ported from the Python reference
//! (`src/openaten/`). Hardware-bound modules (USB, evdev, GTK) are left out;
//! they plug in through the traits in this crate.

pub mod appmsg;
pub mod cli;
pub mod clipboard;
pub mod config;
pub mod control;
pub mod daemon;
pub mod errors;
pub mod hotplug;
pub mod link;
pub mod log;
pub mod protocol;
pub mod transfer;
pub mod warp;

#[cfg(test)]
mod fake_transport;