//! Placing this computer's own pointer at an exact position (issue #21): the
//! extension point and its status helper. Ported from `src/openaten/warp.py`.
//!
//! The uinput *backend* (a virtual absolute pointer device) is hardware-bound
//! and provided by the platform layer; this module defines the [`Warp`] trait
//! the daemon uses, the no-op [`NullWarp`], and the pure helpers
//! ([`uinput_state`], [`abs_position`]) that do not need live hardware.

/// A backend that can move this computer's pointer to an exact logical point.
/// Returns false if it could not (unsupported, no permission, off-screen).
pub trait Warp {
    fn name(&self) -> &str;
    fn place(&mut self, x: f64, y: f64) -> bool;
}

/// Does nothing: hand-off keeps its approximate placement.
pub struct NullWarp;

impl Warp for NullWarp {
    fn name(&self) -> &str {
        "none"
    }
    fn place(&mut self, _x: f64, _y: f64) -> bool {
        false
    }
}

pub const UINPUT: &str = "/dev/uinput";
pub const ABS_MAX: i64 = 65535; // range of the virtual absolute axes

/// 'ok' when this process can create uinput devices, 'denied' when the node
/// exists but is not writable, 'missing' when it does not exist.
pub fn uinput_state(path: &str) -> String {
    if !std::path::Path::new(path).exists() {
        return "missing".to_string();
    }
    let rw = std::fs::OpenOptions::new().write(true).open(path).is_ok();
    if rw {
        "ok".to_string()
    } else {
        "denied".to_string()
    }
}

/// Map a logical point onto the virtual device's absolute axes. `box` is
/// (left, top, right, bottom) of the local screens' bounding box.
pub fn abs_position(x: f64, y: f64, b: (f64, f64, f64, f64), max_value: i64) -> (i64, i64) {
    let (left, top, right, bottom) = b;
    let fx = (x - left) / ((right - left - 1.0).max(1.0));
    let fy = (y - top) / ((bottom - top - 1.0).max(1.0));
    let mx = fx.clamp(0.0, 1.0) * max_value as f64;
    let my = fy.clamp(0.0, 1.0) * max_value as f64;
    (mx.round() as i64, my.round() as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn null_warp_declines() {
        let mut w = NullWarp;
        assert_eq!(w.name(), "none");
        assert!(!w.place(100.0, 100.0));
    }

    #[test]
    fn abs_position_maps_and_clamps() {
        let box4 = (0.0, 0.0, 100.0, 100.0);
        assert_eq!(abs_position(0.0, 0.0, box4, 65535), (0, 0));
        assert_eq!(abs_position(99.0, 99.0, box4, 65535), (65535, 65535));
        // off-screen clamps to the edge
        assert_eq!(abs_position(-50.0, 500.0, box4, 65535), (0, 65535));
        let center = abs_position(50.0, 50.0, box4, 65535);
        assert!((center.0 - 33098).abs() <= 1, "center.x={}", center.0);
        assert!((center.1 - 33098).abs() <= 1, "center.y={}", center.1);
    }

    #[test]
    fn uinput_state_reports_missing_for_a_bad_path() {
        assert_eq!(uinput_state("/definitely/not/a/uinput-node"), "missing");
    }
}