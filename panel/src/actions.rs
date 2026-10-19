//! Things the window does by running other programs: the daemon's control command, the daemon's restart, the warp test and the one privileged
//! step (granting /dev/uinput access). All of them are explicit user actions; nothing here runs at start-up.

use std::path::PathBuf;
use std::process::Command;
use std::thread;

use crate::perm;

/// The `openaten` command: `$OPENATEN_BIN`, else `~/.local/bin/openaten` when present, else whatever is on PATH.
pub fn openaten_bin() -> PathBuf {
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

/// Start a child and reap it from a thread, so it never lingers as a zombie. Returns an error text when it cannot be started.
fn spawn_detached(mut command: Command, what: &str) -> Result<(), String> {
    let mut child = command.spawn().map_err(|e| format!("cannot run {what}: {e}"))?;
    thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

pub fn toggle_forwarding() {
    let mut command = Command::new(openaten_bin());
    command.args(["ctl", "toggle"]);
    if let Err(e) = spawn_detached(command, "`openaten ctl toggle`") {
        eprintln!("openaten-panel: {e}");
    }
}

/// `openaten warp-test`: moves the pointer to four places so the user can watch it. Returns at once.
pub fn warp_test() -> Result<(), String> {
    let mut command = Command::new(openaten_bin());
    command.arg("warp-test");
    spawn_detached(command, "`openaten warp-test`")
}

/// Restart the user service so a changed config.toml takes effect. Blocks until systemctl returns: call it from a worker thread.
pub fn restart_daemon() -> Result<(), String> {
    let out = Command::new("systemctl")
        .args(["--user", "restart", "openaten-daemon.service"])
        .output()
        .map_err(|e| format!("cannot run systemctl: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!("systemctl failed: {}", String::from_utf8_lossy(&out.stderr).trim()))
    }
}

/// The first `setfacl` on PATH, as an absolute path (pkexec runs with its own PATH, so it must be spelled out).
pub fn find_setfacl() -> Option<String> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|dir| dir.join("setfacl")).find(|p| p.is_file()).map(|p| p.to_string_lossy().into_owned())
}

pub fn current_user() -> String {
    std::env::var("USER")
        .ok()
        .filter(|u| !u.is_empty())
        .or_else(|| Command::new("id").arg("-un").output().ok().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()))
        .unwrap_or_default()
}

/// The text for the confirmation dialog, or an error when the tools to do it are missing.
pub fn grant_confirmation() -> Result<String, String> {
    let setfacl = find_setfacl().ok_or("`setfacl` (the acl package) was not found on PATH")?;
    let user = current_user();
    if user.is_empty() {
        return Err("cannot tell which user you are".into());
    }
    Ok(perm::confirmation_text(&setfacl, &user))
}

/// Run the privileged command shown in the confirmation: `pkexec setfacl -m u:<user>:rw /dev/uinput`. Blocks while the system asks for the
/// password: call it from a worker thread, and only after the user confirmed.
pub fn grant_uinput_access() -> Result<(), String> {
    let setfacl = find_setfacl().ok_or("`setfacl` (the acl package) was not found on PATH")?;
    let user = current_user();
    if user.is_empty() {
        return Err("cannot tell which user you are".into());
    }
    let out = Command::new("pkexec").args(perm::setfacl_args(&setfacl, &user)).output().map_err(|e| format!("cannot run pkexec: {e}"))?;
    match out.status.code() {
        Some(0) => Ok(()),
        Some(126) | Some(127) => Err("authentication was cancelled or not possible".into()),
        _ => Err(format!("pkexec failed: {}", String::from_utf8_lossy(&out.stderr).trim())),
    }
}
