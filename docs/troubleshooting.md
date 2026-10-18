# Troubleshooting

First turn up the logging: `openaten --log-level DEBUG <command>` (or `OPENATEN_LOG=DEBUG`); under systemd use `journalctl --user -u openaten`.
The firmware rules behind most of these are explained in [hardware.md](hardware.md).

| Symptom | Likely cause | What to do |
|---|---|---|
| `openaten list` finds no bridge | Only one upstream USB-C port is connected, or the udev rule is missing | Connect **both** upstream ports (the bridge only appears then); install `udev/70-openaten.rules` and reload udev |
| Permission denied opening the device | The `uaccess` rule is not applied | Rule must be numbered below 73; re-plug the switch after installing it |
| `openaten forward` finds no devices | No read access to `/dev/input` | Join the `input` group or run with equivalent access |
| Commands hang or fail repeatedly, processes stuck in `usb_stor_disconnect` | The kernel `usb-storage` driver is probing the bridge and wedged the firmware | Power-cycle the switch (power and both USB-C cables); add `usb-storage.quirks=05e3:0627:i` to the kernel command line |
| `link not up (retrying)` in the daemon log | No bridge end yet, or the firmware refused the first command | Wait: retries back off from 2 s to 30 s, but a bridge hotplug event (switch re-enumerates, cable re-plugged) triggers an immediate retry. If it persists, check `openaten list`, then power-cycle |
| `link not up (retrying)` forever after re-plugging the cable, with `port = "..."` set in the config or `--port` given | The pinned USB port path no longer matches: after a re-plug the bridge can come back at a different path (seen: `9.2` before, `9` after, on one host), because it hangs off a different hub position | Run `openaten list` for the current path and update `port`, or drop the setting: with no port the daemon takes the first end it finds, which is the right choice with a single bridge |
| Injected keys/mouse stop working on the other computer | Nothing polls that end's HID interface (e.g. a test tool left `usbhid` detached) | Re-attach the `usbhid` driver or re-plug; `openaten hid` restores it itself |
| `forwarding` turns itself off with "injection failed" | Bridge unplugged or the firmware refused the report | Check the log line for the cause; hand control back with the hotkey, then retry |
| First `state`/`inquiry` after a link session is refused | Known firmware quirk (hardware.md, point 9) | Handled automatically by a reset and retry; no action needed |
| Pointer stuck on the other computer | Hand-off peer is not running or the link is down | Press the hotkey (`ctrl+alt+shift+k` by default) to return control; use `--max-active` as a safety net |

If nothing else helps, power-cycle the switch. Please include `openaten state`, the switch firmware and a DEBUG log when filing an issue.
