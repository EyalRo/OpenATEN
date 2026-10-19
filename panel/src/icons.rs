//! The tray icons embedded in the binary, as StatusNotifierItem pixmaps.
//!
//! The themed icon name (see `State::icon_name`) is preferred by hosts that can resolve it; these pixmaps are the fallback so the icon shows
//! up even when the package's icons are not in the host's icon search path.

use crate::state::{rgba_to_argb, State};

const SIZES: [(i32, [&[u8]; 4]); 2] = [
    (
        32,
        [
            include_bytes!("../assets/png/openaten-offline-32.png"),
            include_bytes!("../assets/png/openaten-connected-32.png"),
            include_bytes!("../assets/png/openaten-forwarding-32.png"),
            include_bytes!("../assets/png/openaten-error-32.png"),
        ],
    ),
    (
        64,
        [
            include_bytes!("../assets/png/openaten-offline-64.png"),
            include_bytes!("../assets/png/openaten-connected-64.png"),
            include_bytes!("../assets/png/openaten-forwarding-64.png"),
            include_bytes!("../assets/png/openaten-error-64.png"),
        ],
    ),
];

fn index(state: State) -> usize {
    match state {
        State::Offline => 0,
        State::Connected => 1,
        State::Forwarding => 2,
        State::Error => 3,
    }
}

/// The 64 px PNG of a state's icon (for the settings window header, which must not depend on the icon theme).
pub fn png64(state: State) -> &'static [u8] {
    SIZES[1].1[index(state)]
}

/// Decode one PNG (8-bit RGBA, as written by `assets/render-png.sh`) into a pixmap of the given size.
pub fn decode(bytes: &[u8], size: i32) -> Option<ksni::Icon> {
    let mut decoder = png::Decoder::new(bytes);
    decoder.set_transformations(png::Transformations::EXPAND);
    let mut reader = decoder.read_info().ok()?;
    let mut buf = vec![0; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buf).ok()?;
    if info.color_type != png::ColorType::Rgba || info.bit_depth != png::BitDepth::Eight {
        return None;
    }
    if info.width as i32 != size || info.height as i32 != size {
        return None;
    }
    Some(ksni::Icon { width: size, height: size, data: rgba_to_argb(&buf[..info.buffer_size()]) })
}

/// All embedded sizes of the icon for a state.
pub fn pixmaps(state: State) -> Vec<ksni::Icon> {
    SIZES.iter().filter_map(|(size, pngs)| decode(pngs[index(state)], *size)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_state_has_both_sizes_with_the_right_pixel_count() {
        for state in [State::Offline, State::Connected, State::Forwarding, State::Error] {
            let icons = pixmaps(state);
            assert_eq!(icons.len(), 2, "{state:?}: both sizes must decode");
            for icon in icons {
                assert_eq!(icon.data.len(), (icon.width * icon.height * 4) as usize);
            }
        }
    }

    #[test]
    fn the_icon_is_not_blank_and_has_transparent_corners() {
        let icon = pixmaps(State::Connected).remove(0); // 32 px
        let alpha = |x: usize, y: usize| icon.data[(y * icon.width as usize + x) * 4];
        assert_eq!(alpha(0, 0), 0, "the top-left corner is outside the drawing");
        assert!(icon.data.chunks_exact(4).any(|px| px[0] == 255), "something is drawn");
    }

    #[test]
    fn a_wrong_size_or_garbage_is_rejected() {
        assert!(decode(b"not a png", 32).is_none());
        assert!(decode(include_bytes!("../assets/png/openaten-connected-64.png"), 32).is_none());
    }
}
