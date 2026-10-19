//! The /dev/uinput permission: what to tell the user and the exact command that grants it. Pure text and argument building, unit-tested.
//!
//! Nothing here runs anything. The window shows the command in a confirmation dialog first and only then spawns it (see `actions.rs`).

use crate::state::Uinput;

pub const UINPUT: &str = "/dev/uinput";

/// How the permission is shown: a short status word, the libadwaita style class (colour) and an explanation.
pub struct Display {
    pub label: &'static str,
    /// `success` (green), `error` (red) or `dim-label` (grey).
    pub css: &'static str,
    pub detail: &'static str,
}

pub fn describe(access: Uinput) -> Display {
    match access {
        Uinput::Ok => Display { label: "Granted", css: "success", detail: "The daemon can create the virtual pointer, so pointer warp can work." },
        Uinput::Denied => Display {
            label: "Denied",
            css: "error",
            detail: "Your user cannot write to /dev/uinput. Pointer warp needs it; grant access below, or see docs/setup.md for a permanent rule.",
        },
        Uinput::Missing => Display {
            label: "Missing",
            css: "error",
            detail: "/dev/uinput does not exist: the uinput kernel module is not loaded.",
        },
        Uinput::Unknown => Display { label: "Unknown", css: "dim-label", detail: "The daemon is not running, so the permission cannot be checked." },
    }
}

/// The arguments after `pkexec`: give `user` read/write access to the device through an ACL (only that user; it lasts until the node is recreated).
pub fn setfacl_args(setfacl: &str, user: &str) -> Vec<String> {
    vec![setfacl.into(), "-m".into(), format!("u:{user}:rw"), UINPUT.into()]
}

/// The command as shown to the user.
pub fn command_line(setfacl: &str, user: &str) -> String {
    let mut parts = vec!["pkexec".to_string()];
    parts.extend(setfacl_args(setfacl, user));
    parts.join(" ")
}

/// The dialog body for the confirmation: the command, what it does, how long it lasts, and the permanent alternatives.
pub fn confirmation_text(setfacl: &str, user: &str) -> String {
    format!(
        "This will run:\n\n    {}\n\nIt gives only your user ({user}) write access to {UINPUT}, the device that lets a program create virtual input devices. \
         It lasts until the device is recreated (a reboot). The system will ask for your password.\n\n\
         For a permanent setup use a udev rule or your NixOS configuration instead (docs/setup.md, Pointer warp).",
        command_line(setfacl, user)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_command_names_the_user_and_only_the_device() {
        assert_eq!(setfacl_args("/usr/bin/setfacl", "stags"), ["/usr/bin/setfacl", "-m", "u:stags:rw", "/dev/uinput"]);
        assert_eq!(command_line("setfacl", "stags"), "pkexec setfacl -m u:stags:rw /dev/uinput");
    }

    #[test]
    fn denied_and_missing_are_red_granted_is_green_and_unknown_is_grey() {
        assert_eq!(describe(Uinput::Ok).css, "success");
        assert_eq!(describe(Uinput::Denied).css, "error");
        assert_eq!(describe(Uinput::Missing).css, "error");
        assert_eq!(describe(Uinput::Unknown).css, "dim-label");
    }

    #[test]
    fn the_confirmation_shows_the_exact_command_scope_and_lifetime() {
        let text = confirmation_text("setfacl", "stags");
        assert!(text.contains("pkexec setfacl -m u:stags:rw /dev/uinput"));
        assert!(text.contains("only your user (stags)"));
        assert!(text.contains("until the device is recreated"));
        assert!(text.contains("udev rule"));
    }
}
