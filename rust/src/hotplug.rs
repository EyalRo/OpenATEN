//! Bridge hotplug events from the kernel's uevent netlink socket.
//! Ported from `src/openaten/hotplug.py`. The parse function is pure; the
//! monitor is hardware-dependent and provided via the daemon's `hotplug`
//! hook (which the Rust CLI supplies from the platform layer).

pub const VID: u16 = 0x05E3;
pub const PID: u16 = 0x0627;

/// 'add' or 'remove' if this uevent is the bridge device appearing or
/// disappearing, else None.
pub fn parse(message: &[u8]) -> Option<&'static str> {
    let fields: Vec<&[u8]> = message.split(|&b| b == 0).collect();
    if fields.is_empty() {
        return None;
    }
    let head = fields[0];
    if !head.iter().any(|&b| b == b'@') {
        return None;
    }
    let mut props: std::collections::HashMap<&[u8], &[u8]> = std::collections::HashMap::new();
    for f in fields.iter().skip(1) {
        if let Some(pos) = f.iter().position(|&b| b == b'=') {
            props.insert(&f[..pos], &f[pos + 1..]);
        }
    }
    if props.get(b"SUBSYSTEM".as_slice()).copied() != Some(&b"usb"[..]) {
        return None;
    }
    if props.get(b"DEVTYPE".as_slice()).copied() != Some(&b"usb_device"[..]) {
        return None;
    }
    let action = props.get(b"ACTION".as_slice()).map(|a| std::str::from_utf8(a).unwrap_or("")).unwrap_or("");
    if action != "add" && action != "remove" {
        return None;
    }
    let product = props.get(b"PRODUCT".as_slice()).map(|p| std::str::from_utf8(p).unwrap_or("")).unwrap_or("");
    let parts: Vec<&str> = product.split('/').collect();
    if parts.len() < 2 {
        return None;
    }
    let vid = match u16::from_str_radix(parts[0], 16) {
        Ok(v) => v,
        Err(_) => return None,
    };
    let pid = match u16::from_str_radix(parts[1], 16) {
        Ok(v) => v,
        Err(_) => return None,
    };
    if (vid, pid) == (VID, PID) {
        Some(if action == "add" { "add" } else { "remove" })
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn uevent(action: &[u8], product: &[u8], subsystem: &[u8], devtype: &[u8]) -> Vec<u8> {
        let mut v = Vec::new();
        v.extend_from_slice(action);
        v.extend_from_slice(b"@/devices/pci0000:00/usb1/1-2\0ACTION=");
        v.extend_from_slice(action);
        v.extend_from_slice(b"\0SUBSYSTEM=");
        v.extend_from_slice(subsystem);
        v.extend_from_slice(b"\0DEVTYPE=");
        v.extend_from_slice(devtype);
        v.extend_from_slice(b"\0PRODUCT=");
        v.extend_from_slice(product);
        v.push(0);
        v
    }

    #[test]
    fn bridge_add_and_remove_are_recognised() {
        let bridge = b"05e3/627/6893";
        assert_eq!(parse(&uevent(b"add", bridge, b"usb", b"usb_device")), Some("add"));
        assert_eq!(parse(&uevent(b"remove", bridge, b"usb", b"usb_device")), Some("remove"));
    }

    #[test]
    fn other_devices_actions_and_subsystems_are_ignored() {
        let bridge = b"05e3/627/6893";
        assert_eq!(parse(&uevent(b"add", b"557/2414/100", b"usb", b"usb_device")), None);
        assert_eq!(parse(&uevent(b"change", bridge, b"usb", b"usb_device")), None);
        assert_eq!(parse(&uevent(b"add", bridge, b"usb", b"usb_interface")), None);
        assert_eq!(parse(&uevent(b"add", bridge, b"block", b"usb_device")), None);
    }

    #[test]
    fn garbage_and_udev_rebroadcasts_are_ignored() {
        assert_eq!(parse(b""), None);
        assert_eq!(parse(b"add@/x\0SUBSYSTEM=usb\0DEVTYPE=usb_device\0ACTION=add\0\0"), None);
        assert_eq!(parse(b"add@/x\0SUBSYSTEM=usb\0DEVTYPE=usb_device\0ACTION=add\0PRODUCT=zz/yy\0"), None);
    }
}