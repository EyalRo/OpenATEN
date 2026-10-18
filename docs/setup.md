# Setup

## Requirements

- Python 3.10+, `pyusb`, and the `libusb-1.0` library (Debian/Ubuntu: `libusb-1.0-0`; Fedora: `libusb1`). Keyboard and mouse forwarding also needs `evdev`,
  which builds a C extension (Python headers) or comes from your distribution's `python-evdev` package.
- An ATEN US3342-class switch with **both** upstream USB-C ports connected to hosts (for development you can connect both
  to two ports of the same computer).

## Permissions (Linux)

Install the udev rule so your user can open the bridge's USB nodes without root:

```sh
sudo install -m 0644 udev/70-openaten.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=05e3
```

On NixOS:

```nix
services.udev.extraRules = ''
  SUBSYSTEM=="usb", ATTR{idVendor}=="05e3", ATTR{idProduct}=="0627", TAG+="uaccess"
'';
```

The rule file name/number matters: the `uaccess` tag is only honoured if applied before `73-seat-late.rules`.

### Other distributions, and keeping the kernel away from the bridge

The `uaccess` rule above needs systemd-logind. Without it, use a group instead, for example
`SUBSYSTEM=="usb", ATTR{idVendor}=="05e3", ATTR{idProduct}=="0627", MODE="0660", GROUP="plugdev"` in `/etc/udev/rules.d/70-openaten.rules`, and add your user to that group.

The kernel's `usb-storage` driver must not probe the bridge's mass-storage function (it can wedge the firmware, see [hardware.md](hardware.md)). Either add
`usb-storage.quirks=05e3:0627:i` to the kernel command line, or create `/etc/modprobe.d/openaten.conf` containing `options usb-storage quirks=05e3:0627:i`
(then rebuild the initramfs if your distribution loads `usb-storage` from it). The NixOS module below sets the kernel parameter for you.

If anything hangs or the switch stops answering, power-cycle it (its power and both USB-C cables); [troubleshooting.md](troubleshooting.md) lists the symptoms.

## Install and try

```sh
pip install -e .            # or: nix develop
# or install the command only, without a checkout:
pipx install 'git+https://github.com/EyalRo/OpenATEN'          # add [forward] for keyboard/mouse forwarding: '...OpenATEN[forward]'
openaten list               # shows the bridge ends and their USB port paths
openaten state              # role (H/S) and link state of each end
openaten loopback -n 5      # packet round-trip between the two ends on this host
openaten hid                # HID injection round-trip between the two ends
openaten link --mib 64      # link-layer self-test: messages both ways + 64 MiB each way, checksum-verified
```

`loopback` and `hid` are the hardware self-tests. If either hangs or fails, see the rules in [hardware.md](hardware.md) and
power-cycle the switch.

## Roles: master and slave

Pick one computer to run `openaten daemon --role master` and the other `--role slave`. These are *application* roles for the link (the master polls, the slave
answers; see [design.md](design.md)). They are independent of the `H`/`S` role that `openaten state` reports for each physical end of the switch, which did not
constrain who may initiate in our tests ([hardware.md](hardware.md)), so either computer can be either. Everything works in both directions whichever you pick;
the only difference is latency: the master sends immediately, while the slave's messages wait for the next poll (fast while anything is flowing, at most about a
second when idle). Choose the computer you type on most as the master.

## Input forwarding (keyboard and mouse to the other computer)

```sh
pip install evdev                                  # or: nix develop (includes it)
openaten forward --dry-run                         # prints the HID reports; nothing is grabbed or injected
openaten forward --max-active 600                  # real run; ctrl+alt+shift+k toggles; auto-returns control after 10 min
```

Reading and grabbing keyboards and mice needs access to `/dev/input/event*`: add your user to the `input` group
(NixOS: `users.users.<name>.extraGroups = [ "input" ];`). While forwarding is on the local devices are grabbed, so nothing reaches
this desktop. The grab only starts after every key and button is physically released (press the hotkey, then let go of all keys), and
it ends only once everything is released again; otherwise the desktop would miss a key release and show stuck modifiers. Turning forwarding
off (or any exit) releases every key and button on the other computer at once. If a modifier ever does look stuck, tap Ctrl, Shift, Alt and Super once.
Avoid hotkeys that other programs use (for example `ctrl+g`); the default is `ctrl+alt+shift+k`. The other
computer needs no OpenATEN software to receive input: it sees an ordinary USB keyboard and mouse. (Edge hand-off, below, uses an agent on both.)

### Edge hand-off (push the pointer through the screen border)

Describe both computers' screens in `~/.config/openaten/screens.toml` (the same file works on both; only `local` differs, and defaults to the hostname).
Coordinates are niri *logical* pixels in one shared space, y growing downwards. Screens that touch exactly form a border; only the overlapping part of
the shared edge hands off. Example: the xps15 to the left of the 590i and a bit below it:

```toml
local = "590i"
local_scale = 1.0     # logical pixels per mouse count here (1000/dpi with niri's flat profile)
remote_scale = 1.0    # the same for the bridge's virtual mouse on the other computer
push = 40             # pixels of pushing against the edge before control switches

[[screen]]
host = "590i"
name = "DP-1"
x = 0
y = 0
width = 2560
height = 1440

[[screen]]
host = "xps15"
name = "eDP-1"
x = -1920
y = 300
width = 1920
height = 1200
```

Two optional keys make the file shorter and let touchpads work in motion mode:

```toml
local_outputs = "niri"   # this computer's screens come from `niri msg --json outputs`; list only the other computer's screens in the file
touchpad_scale = 1.0     # logical pixels per count of touchpad motion (motion mode only; libinput accelerates touchpads, so tune it)
```

With `local_outputs = "niri"` the `[[screen]]` entries for this host are replaced by niri's enabled outputs (logical position and size); if niri cannot be
queried the file's entries are used and a WARNING is logged. The other computer's screens must be given in the same coordinate space as niri's logical
space. The parser follows niri's documented JSON shape but has not been run against a real niri yet. In motion mode a touchpad now moves the dead-reckoned
pointer too: pushing through the border starts forwarding once the finger lifts, and the next touch moves the other computer's pointer.

`openaten layout` prints the borders it derived. `openaten forward` and `openaten daemon` pick the file up automatically (or pass `--screens`).

Two ways to detect the edge push, chosen with `source` in the layout file:

- `source = "motion"` (default): each computer's pointer position is dead-reckoned from relative motion. It needs no extra software but is approximate:
  set `accel-profile "flat"` for pointers in niri's `input` config on both computers, push the pointer into a screen edge once after starting
  (status shows `edges: "calibrating"` until then), and the entry row on the other computer is only as accurate as its flat-profile motion.
- `source = "strip"`: exact. Both computers run `openaten daemon --role ...` plus `openaten strip` (below). Invisible 1-pixel layer-shell strips along each
  shared border report the exact row where the pointer enters, the agents hand control over with messages on the link, and the receiving
  agent hands it back when its own pointer pushes back through the border. No calibration, no `scale` settings, no flat-acceleration requirement
  (the receiving pointer is still positioned by a HID slam to its top-left corner and move to the row, so entry still assumes a flat profile there).

Running the exact mode on both computers (the same `screens.toml`, `source = "strip"`):

```sh
openaten daemon --role master     # on one computer; --role slave on the other. Holds the bridge end and the link all the time.
openaten-strip                    # `nix run .#strip`; GTK4 + gtk4-layer-shell, started from the niri session on each computer
```

Screen names in `screens.toml` must be the output connector names (`niri msg outputs`, e.g. `DP-1`, `eDP-1`) for the local screens. The strip was
checked in a headless sway session (enter/leave with exact rows at the shared segment, nothing outside it); it has not been run under niri or with
two real computers yet.

What to know:

- When control switches, the local pointer stays where it left (niri cannot warp it), so it comes back at the row where it went out, not at the row
  where the other pointer returned.
- In `motion` mode only mice drive the edge detection (touchpads still forward but cannot trigger a switch); in `strip` mode any pointer works.
- The hotkey and the Noctalia button always work, in both modes.
- In `strip` mode the bridge end is held permanently (the link needs it), so other tools cannot use it while the daemon runs.
- Several local screens work as long as they are listed. In `motion` mode the other computer's pointer position is exact only for a single screen.

Also recommended: stop the kernel from probing the bridge's storage function (see [hardware.md](hardware.md)):
`boot.kernelParams = [ "usb-storage.quirks=05e3:0627:i" ];` on NixOS.

## Background daemon and Noctalia widget

`openaten daemon` owns the input forwarder and publishes `$XDG_RUNTIME_DIR/openaten/status.json`; `openaten ctl {status,on,off,toggle,quit}`
controls it over `ctl.sock`. The bridge end is only claimed while forwarding is on. Build with `nix build .#default` (or `nix run . -- daemon`).

A Noctalia v5 bar widget lives in [`noctalia/`](../noctalia/README.md): it shows bridge/forwarding state and toggles on click
(add `stags/openaten:widget` to a bar). The widget runs `openaten ctl toggle`, so `openaten` must be on the shell's PATH.

## Logging

Commands log to stderr with standard levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`), so the daemon's output lands in the journal when run
under systemd. Choose the verbosity with `openaten --log-level DEBUG <command>` or `OPENATEN_LOG=DEBUG`; the default is `INFO`. Hardware
self-tests and `list`/`state`/`ctl` still print their results to stdout.

## Development checks

```sh
pip install -e ".[dev]"
ruff check . && mypy && pytest
```

CI (`.github/workflows/ci.yml`) runs the same three on Python 3.10-3.12.

## Run the daemon as a service

On NixOS use the module below instead. `contrib/systemd/openaten.service` is a systemd *user* unit; the header of the file has the install steps. Override `ExecStart`
with `systemctl --user edit openaten` to pick the `--role` and `--screens`, and read the logs with `journalctl --user -u openaten`.
The unit is untested on real hardware.

## Config file

Options can be set once in `~/.config/openaten/config.toml` (or `$XDG_CONFIG_HOME/openaten/config.toml`, or `openaten --config PATH ...`) instead of on
every command line. See `contrib/config.example.toml` for the keys: `log_level` at the top level and per-command tables `[daemon]`, `[forward]`,
`[strip]` and `[layout]` with the same names as the flags (`role`, `hotkey`, `port`, `max_active`, `screens`). Precedence: command-line flag >
`OPENATEN_LOG` (log level only) > file > built-in default. Unknown keys and bad values are skipped with a WARNING; a missing default file is fine,
but a file named with `--config` must exist and parse. The systemd unit's `ExecStart` can then stay `openaten daemon`.

## Clipboard and file transfer

Both are opt-in and need `--role` on both computers; details in [applications.md](applications.md).

```sh
openaten daemon --role master --clipboard --receive-dir ~/Downloads/OpenATEN    # computer A
openaten daemon --role slave  --clipboard --receive-dir ~/Downloads/OpenATEN    # computer B
openaten send photo.jpg report.pdf                                              # on either: sends to the other computer
```

Install `wl-clipboard` (Wayland) or `xclip` (X11) for clipboard sharing. Put the options in `config.toml` (`clipboard = true`, `receive_dir = "..."`) to keep them out of the service unit.

## NixOS module

The flake exports a NixOS module that installs the package, the udev rule and a systemd user service, and adds your user to the `input` group:

```nix
{
  inputs.openaten.url = "github:EyalRo/OpenATEN";
  # in your NixOS configuration:
  imports = [ openaten.nixosModules.default ];
  services.openaten = {
    enable = true;
    role = "master";                      # "slave" on the other computer
    users = [ "me" ];                     # added to the input group
    clipboard = true;                     # optional, see applications.md
    receiveDir = "%h/Downloads/OpenATEN"; # optional: accept files from the other computer
    # logLevel = "INFO"; extraArgs = [ "--hotkey" "ctrl+alt+k" ]; usbStorageQuirk = true;
  };
}
```

`usbStorageQuirk` (default on) adds `usb-storage.quirks=05e3:0627:i` to the kernel command line so the kernel never probes the bridge's mass-storage
function (see [hardware.md](hardware.md)). `nix flake check` builds the package, which runs the test suite, and evaluates this module with a sample configuration.
The module has been evaluated but not yet run on a real NixOS machine with a switch.
