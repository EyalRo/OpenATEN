# OpenATEN Noctalia widget

Bar widget for [Noctalia](https://noctalia.dev) v5: shows bridge/forwarding state from `openaten daemon` and toggles forwarding on click.

1. Install `openaten` at `~/.local/bin/openaten` and keep `openaten daemon` running (see `docs/setup.md`).
2. Add this directory as a plugin source and enable it:
   `noctalia msg plugins source add openaten path /path/to/OpenATEN/noctalia && noctalia msg plugins enable stags/openaten`
3. Add `stags/openaten:widget` to a bar.

Colours: dim = no daemon or no bridge, normal = connected, primary = forwarding, red = error.
