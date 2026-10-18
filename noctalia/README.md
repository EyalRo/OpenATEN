# OpenATEN Noctalia widget

Bar widget for [Noctalia](https://noctalia.dev) v5: shows bridge/forwarding state from `openaten daemon` and toggles forwarding on click.

1. Install `openaten` at `~/.local/bin/openaten` and keep `openaten daemon` running (see `docs/setup.md`).
2. Add this directory as a plugin source and enable it:
   `noctalia msg plugins source add openaten path /path/to/OpenATEN/noctalia && noctalia msg plugins enable stags/openaten`
3. Add `stags/openaten:widget` to a bar.

Colours: dim = no daemon or no bridge, normal = connected, primary = forwarding, red = error.

Hover for details: state, bridge (and the USB port of the claimed end), role, number of input devices, hotkey, edge hand-off state, active transfers and any error. Rows the daemon does not report (older versions) are left out.

Setting **openaten command** (widget settings): path to the `openaten` binary used for the click toggle; default `~/.local/bin/openaten`.
