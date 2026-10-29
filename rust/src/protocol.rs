//! Wire format of the Genesys Logic "Host Bridge" found in ATEN US3342-class
//! switches. Ported from `src/openaten/protocol.py`.

/// Control packets are one 2 KiB "sector".
pub const PKT_LEN: usize = 2048;

// Packet types (byte 0x0C of the packet header)
pub const T_CLIPBOARD: u8 = 0x41;
pub const T_PASTE_FILES: u8 = 0x42;
pub const T_ABORT_FILES: u8 = 0x43;
pub const T_MOUSE_CROSS: u8 = 0x50;
pub const T_MOUSE_CROSS_RSP: u8 = 0x51;
pub const T_CHG_ORIEN: u8 = 0x53;
pub const T_DATA: u8 = 0x88;
pub const T_RESPONSE: u8 = 0x85;
pub const T_CONNECT: u8 = 0xC1;
pub const T_DISCONNECT: u8 = 0xC2;
pub const T_ALIVE: u8 = 0xC3;

// Vendor SCSI commands (CDB byte 2); CDB byte 0 is always 0xF2
pub const CMD_TX: u8 = 0x00;
pub const CMD_RX: u8 = 0x01;
pub const CMD_HID: u8 = 0x02;
pub const CMD_STATE: u8 = 0x04;
pub const CMD_INIT: u8 = 0x06;
pub const CMD_UFP: u8 = 0x07;

/// Build a 16-byte vendor CDB. byte 1 is a checksum: the negated sum of all
/// other bytes, so the whole CDB sums to 0 mod 256.
pub fn cdb(cmd: u8, b3: u8, b4_7: [u8; 4], tail: &[u8]) -> [u8; 16] {
    let mut c = [0u8; 16];
    c[0] = 0xF2;
    c[2] = cmd;
    c[3] = b3;
    c[4..8].copy_from_slice(&b4_7);
    let tlen = tail.len().min(8);
    c[8..8 + tlen].copy_from_slice(&tail[..tlen]);
    let sum: u32 = c.iter().map(|&b| b as u32).sum();
    c[1] = (256 - (sum & 0xFF)) as u8 & 0xFF;
    c
}

pub fn cdb_tx(length: u32) -> [u8; 16] {
    cdb(CMD_TX, 0, length.to_be_bytes(), &[])
}

pub fn cdb_rx(length: u32) -> [u8; 16] {
    cdb(CMD_RX, 0, length.to_be_bytes(), &[])
}

pub fn cdb_hid(report_len: u8) -> [u8; 16] {
    cdb(CMD_HID, 0, [0, 0, 0, report_len], &[])
}

pub fn cdb_state(length: u8) -> [u8; 16] {
    cdb(CMD_STATE, 0, [0, 0, 0, length], &[])
}

pub fn cdb_switch_ufp(mode: u8) -> [u8; 16] {
    cdb(CMD_UFP, mode, [0, 0, 0, 0], &[])
}

/// A parsed control packet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet {
    pub length: u32,
    pub rxlen: u32,
    pub stream: u32,
    pub ptype: u8,
    pub resp: u8,
    pub flags: u8,
    pub payload: Vec<u8>,
}

/// Build a 2 KiB packet: header (+0x00 length, +0x04 rx length, +0x08 stream,
/// +0x0C type, +0x0D resp, +0x0E flags).
pub fn packet(ptype: u8, payload: &[u8], stream: u32, resp: u8, flags: u8) -> Result<Vec<u8>, String> {
    if payload.len() > PKT_LEN - 0x10 {
        return Err("payload too large for a control packet".to_string());
    }
    let mut p = vec![0u8; PKT_LEN];
    p[0..4].copy_from_slice(&(PKT_LEN as u32).to_le_bytes());
    p[4..8].copy_from_slice(&0u32.to_le_bytes());
    p[8..12].copy_from_slice(&stream.to_le_bytes());
    p[12] = ptype;
    p[13] = resp;
    p[14] = flags;
    p[0x10..0x10 + payload.len()].copy_from_slice(payload);
    Ok(p)
}

pub fn parse(p: &[u8]) -> Packet {
    Packet {
        length: u32::from_le_bytes(p[0..4].try_into().unwrap()),
        rxlen: u32::from_le_bytes(p[4..8].try_into().unwrap()),
        stream: u32::from_le_bytes(p[8..12].try_into().unwrap()),
        ptype: p[12],
        resp: p[13],
        flags: p[14],
        payload: p[0x10..].to_vec(),
    }
}

/// Build a T_CONNECT packet.
pub fn connect(layout: u8, conn_type: u8, flag: u8, max_xfer: u32) -> Result<Vec<u8>, String> {
    let mut pl = vec![0u8; 0x0B];
    pl[0..4].copy_from_slice(&max_xfer.to_le_bytes());
    pl[4..6].copy_from_slice(&0x300u16.to_le_bytes());
    pl[6..8].copy_from_slice(&0x0102u16.to_le_bytes());
    pl[8] = flag;
    pl[9] = layout;
    pl[10] = conn_type;
    packet(T_CONNECT, &pl, 0, 0, 1)
}

pub fn alive() -> Result<Vec<u8>, String> {
    packet(T_ALIVE, &[], 0, 0, 1)
}

pub fn disconnect() -> Result<Vec<u8>, String> {
    packet(T_DISCONNECT, &[], 0, 0, 1)
}

pub fn mouse_cross(x: u16, y: u16, edge: u8) -> Result<Vec<u8>, String> {
    let mut pl = [0u8; 5];
    pl[0..2].copy_from_slice(&x.to_le_bytes());
    pl[2..4].copy_from_slice(&y.to_le_bytes());
    pl[4] = edge;
    packet(T_MOUSE_CROSS, &pl, 0, 0, 1)
}

pub fn mouse_cross_rsp(status: u8, flag: u8) -> Result<Vec<u8>, String> {
    packet(T_MOUSE_CROSS_RSP, &[status, flag], 0, 0, 1)
}

pub fn chg_orien(layout: u8, flag: u8) -> Result<Vec<u8>, String> {
    packet(T_CHG_ORIEN, &[layout, flag], 0, 0, 1)
}

pub const MOUSE_REPORT_ID: u8 = 1;
pub const KEYBOARD_REPORT_ID: u8 = 2;

/// 5-byte mouse report for CMD_HID: ``[id=1, buttons, wheel, dx, dy]``.
pub fn mouse_report(buttons: u8, dx: i8, dy: i8, wheel: i8) -> [u8; 5] {
    [
        MOUSE_REPORT_ID,
        buttons & 0x1F,
        wheel as u8,
        dx as u8,
        dy as u8,
    ]
}

pub fn s8(b: u8) -> i16 {
    if b > 127 {
        b as i16 - 256
    } else {
        b as i16
    }
}

fn split_axis(total: i16) -> Vec<i8> {
    let mut steps = Vec::new();
    let mut total = total;
    while total != 0 {
        let step = total.clamp(-127, 127) as i8;
        steps.push(step);
        total -= step as i16;
    }
    steps
}

/// Merge runs of consecutive mouse reports that share a button state into as
/// few reports as carry the same motion. See the Python original for the
/// reasoning about screen-edge clamping.
pub fn coalesce_mouse(reports: &[Vec<u8>]) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    // run is Some((buttons, wheel, dx, dy))
    let mut run: Option<(u8, i16, i16, i16)> = None;

    let flush = |out: &mut Vec<Vec<u8>>, run: &mut Option<(u8, i16, i16, i16)>| {
        if let Some((buttons, wheel, dx, dy)) = run.take() {
            let parts = [split_axis(wheel), split_axis(dx), split_axis(dy)];
            let maxlen = parts.iter().map(|a| a.len()).max().unwrap_or(0);
            for i in 0..maxlen {
                let w = parts[0].get(i).copied().unwrap_or(0);
                let x = parts[1].get(i).copied().unwrap_or(0);
                let y = parts[2].get(i).copied().unwrap_or(0);
                out.push(mouse_report(buttons, x, y, w).to_vec());
            }
        }
    };

    for r in reports {
        if r.len() == 5 && r[0] == MOUSE_REPORT_ID {
            let buttons = r[1];
            let wheel = s8(r[2]);
            let dx = s8(r[3]);
            let dy = s8(r[4]);
            if let Some((rb, _, rdx, rdy)) = &run {
                if *rb == buttons && *rdx * dx >= 0 && *rdy * dy >= 0 {
                    let r = run.as_mut().unwrap();
                    r.1 += wheel;
                    r.2 += dx;
                    r.3 += dy;
                    continue;
                }
            }
            flush(&mut out, &mut run);
            run = Some((buttons, wheel, dx, dy));
        } else {
            flush(&mut out, &mut run);
            out.push(r.clone());
        }
    }
    flush(&mut out, &mut run);
    out
}

/// 8-byte keyboard report for CMD_HID: ``[id=2, modifiers, key1..key6]``.
pub fn key_report(mods: u8, keys: &[u8]) -> [u8; 8] {
    let mut r = [0u8; 8];
    r[0] = KEYBOARD_REPORT_ID;
    r[1] = mods;
    for (i, k) in keys.iter().take(6).enumerate() {
        r[2 + i] = *k;
    }
    r
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cdb_is_16_bytes_and_sums_to_zero() {
        let cmds = [
            cdb_state(1),
            cdb_switch_ufp(2),
            cdb_tx(PKT_LEN as u32),
            cdb_rx(PKT_LEN as u32),
            cdb_hid(5),
            cdb(CMD_INIT, 0, [0, 0, 0, 0], &[]),
        ];
        for c in cmds {
            assert_eq!(c.len(), 16);
            assert_eq!(c[0], 0xF2);
            assert_eq!(c.iter().map(|&b| b as u32).sum::<u32>() & 0xFF, 0);
        }
    }

    #[test]
    fn known_state_cdb() {
        let c = cdb_state(1);
        // checksum = -(0xF2 + 0x04 + 0x01) & 0xFF = 0x09
        assert_eq!(c[1], 0x09);
        assert_eq!(&c[0..8], &[0xF2, 0x09, 0x04, 0x00, 0x00, 0x00, 0x00, 0x01]);
    }

    #[test]
    fn tx_length_is_big_endian() {
        let c = cdb_tx(2048);
        assert_eq!(&c[4..8], &[0, 0, 8, 0]);
    }

    #[test]
    fn packet_roundtrip() {
        let p = packet(T_DATA, b"hello", 7, 0, 1).unwrap();
        assert_eq!(p.len(), PKT_LEN);
        let d = parse(&p);
        assert_eq!(d.ptype, T_DATA);
        assert_eq!(d.stream, 7);
        assert_eq!(d.length, 2048);
        assert_eq!(d.flags, 1);
        assert!(d.payload.starts_with(b"hello"));
    }

    #[test]
    fn payload_too_large() {
        assert!(packet(T_DATA, &vec![0u8; PKT_LEN], 0, 0, 1).is_err());
    }

    #[test]
    fn connect_fields() {
        let d = parse(&connect(1, 2, 0, 0x10000).unwrap());
        let pl = &d.payload;
        assert_eq!(d.ptype, T_CONNECT);
        assert_eq!(u32::from_le_bytes(pl[0..4].try_into().unwrap()), 0x10000);
        assert_eq!(u16::from_le_bytes(pl[4..6].try_into().unwrap()), 0x300);
        assert_eq!(u16::from_le_bytes(pl[6..8].try_into().unwrap()), 0x0102);
        assert_eq!((pl[9], pl[10]), (1, 2));
    }

    #[test]
    fn mouse_cross_layout() {
        let pl = parse(&mouse_cross(640, 360, 2).unwrap()).payload;
        assert_eq!(u16::from_le_bytes(pl[0..2].try_into().unwrap()), 640);
        assert_eq!(u16::from_le_bytes(pl[2..4].try_into().unwrap()), 360);
        assert_eq!(pl[4], 2);
    }

    #[test]
    fn hid_reports() {
        assert_eq!(mouse_report(0, 5, 0, 0), [1, 0, 0, 5, 0]);
        assert_eq!(key_report(0, &[0x04]), [2, 0, 4, 0, 0, 0, 0, 0]);
        assert_eq!(key_report(2, &[1, 2, 3, 4, 5, 6, 7]).len(), 8);
    }

    fn m(b: u8, dx: i8, dy: i8, w: i8) -> Vec<u8> {
        mouse_report(b, dx, dy, w).to_vec()
    }
    fn k(mods: u8, keys: &[u8]) -> Vec<u8> {
        key_report(mods, keys).to_vec()
    }

    #[test]
    fn coalesce_sums_motion_within_a_button_state_and_splits_overflow() {
        assert_eq!(coalesce_mouse(&[m(0, 5, 1, 0), m(0, 2, 3, 0), m(0, 1, 0, 0)]), vec![m(0, 8, 4, 0)]);
        assert_eq!(coalesce_mouse(&[m(0, 100, 0, 0), m(0, 100, 0, 0)]), vec![m(0, 127, 0, 0), m(0, 73, 0, 0)]);
        assert_eq!(coalesce_mouse(&[m(0, 0, 0, 1), m(0, 0, 0, 1)]), vec![m(0, 0, 0, 2)]);
    }

    #[test]
    fn coalesce_never_merges_across_a_button_change_or_other_report_kinds() {
        let seq = vec![m(0, 3, 0, 0), m(1, 3, 0, 0), m(1, 2, 0, 0), k(0, &[4]), m(1, 1, 0, 0), m(0, 1, 0, 0)];
        assert_eq!(
            coalesce_mouse(&seq),
            vec![m(0, 3, 0, 0), m(1, 5, 0, 0), k(0, &[4]), m(1, 1, 0, 0), m(0, 1, 0, 0)]
        );
        assert_eq!(coalesce_mouse(&[k(0, &[4]), k(0, &[])]), vec![k(0, &[4]), k(0, &[])]);
        assert!(coalesce_mouse(&[]).is_empty());
    }

    #[test]
    fn coalesce_preserves_total_motion_and_button_sequence() {
        let reports: Vec<Vec<u8>> = [
            m(0, 3, 0, 0), m(1, 3, 0, 0), m(1, 2, 0, 0), m(0, 1, 0, 0),
        ]
        .to_vec();
        let out = coalesce_mouse(&reports);

        fn totals(rs: &[Vec<u8>]) -> std::collections::HashMap<u8, [i32; 3]> {
            let mut acc = std::collections::HashMap::new();
            for r in rs {
                if r.len() == 5 && r[0] == MOUSE_REPORT_ID {
                    let e = acc.entry(r[1]).or_insert([0, 0, 0]);
                    for (i, v) in [r[2], r[3], r[4]].iter().enumerate() {
                        e[i] += if *v > 127 { *v as i32 - 256 } else { *v as i32 };
                    }
                }
            }
            acc
        }

        fn dedup(rs: &[Vec<u8>]) -> Vec<u8> {
            let mut out: Vec<u8> = Vec::new();
            for r in rs {
                if out.last().copied() != Some(r[1]) {
                    out.push(r[1]);
                }
            }
            out
        }

        assert!(out.len() <= reports.len() || reports.is_empty());
        assert_eq!(totals(&out), totals(&reports));
        assert_eq!(dedup(&out), dedup(&reports));
    }

    #[test]
    fn motion_that_reverses_direction_is_kept_apart() {
        assert_eq!(coalesce_mouse(&[m(0, 100, 0, 0), m(0, -100, 0, 0)]), vec![m(0, 100, 0, 0), m(0, -100, 0, 0)]);
        assert_eq!(coalesce_mouse(&[m(0, 0, 5, 0), m(0, 0, -5, 0)]), vec![m(0, 0, 5, 0), m(0, 0, -5, 0)]);
        assert_eq!(coalesce_mouse(&[m(0, 3, 0, 0), m(0, 0, -2, 0), m(0, 4, 0, 0)]), vec![m(0, 7, -2, 0)]);
        assert_eq!(
            coalesce_mouse(&[m(0, 100, 0, 0), m(0, 100, 0, 0), m(0, -1, 0, 0)]),
            vec![m(0, 127, 0, 0), m(0, 73, 0, 0), m(0, -1, 0, 0)]
        );
    }
}