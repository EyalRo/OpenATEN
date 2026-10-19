# openaten-panel

The OpenATEN tray icon: a StatusNotifierItem (ksni) that follows the running `openaten daemon` and toggles forwarding on click. It is meant to replace
the Noctalia bar widget in [`../noctalia`](../noctalia), and to grow into the settings panel (screen layout editor, permissions, pointer warp).

- **State** is read once a second from `$XDG_RUNTIME_DIR/openaten/status.json` (the daemon's status file). A missing or stale file means the daemon is not running.
  Icons: offline (grey), connected (green dot), forwarding (orange dot, yellow pointer), error (red dot).
- **Tooltip** rows: state, bridge, role, input devices, hotkey, edge hand-off, pointer warp (only when on or unable to work), transfers, error. Rows the
  daemon does not report are left out, so older daemons work.
- **Click / "Toggle forwarding"** runs `openaten ctl toggle` (`$OPENATEN_BIN`, else `~/.local/bin/openaten`, else `openaten` on PATH).
- `openaten-panel --status` prints what the tray would show and exits; handy over SSH.

```sh
nix build .#panel      # builds and runs the cargo tests; installs the icons under share/icons/hicolor/scalable/apps
nix run .#panel        # starts the tray in the current session
```

Install it so the icons are on the icon search path, and run it as a user service (see `docs/setup.md`). The icon is also embedded as a pixmap, so the tray
shows it even when the themed name cannot be resolved.

## Icons

`assets/gen.sh` draws all variants from one drawing (two screens in the desk arrangement, a pointer crossing the gap, a status dot); `assets/render-png.sh`
renders the embedded PNG fallbacks (`nix shell nixpkgs#librsvg -c assets/render-png.sh`). Edit the generator, rerun both, commit the results.
