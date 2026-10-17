# Setup

## Requirements

- Python 3.10+, `pyusb`, and the `libusb-1.0` library.
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

## Install and try

```sh
pip install -e .            # or: nix develop
openaten list               # shows the bridge ends and their USB port paths
openaten state              # role (H/S) and link state of each end
openaten loopback -n 5      # packet round-trip between the two ends on this host
openaten hid                # HID injection round-trip between the two ends
openaten link --mib 64      # link-layer self-test: messages both ways + 64 MiB each way, checksum-verified
```

`loopback` and `hid` are the hardware self-tests. If either hangs or fails, see the rules in [hardware.md](hardware.md) and
power-cycle the switch.

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
computer needs no OpenATEN software for this: it sees an ordinary USB keyboard and mouse.

Also recommended: stop the kernel from probing the bridge's storage function (see [hardware.md](hardware.md)):
`boot.kernelParams = [ "usb-storage.quirks=05e3:0627:i" ];` on NixOS.

## Background daemon and Noctalia widget

`openaten daemon` owns the input forwarder and publishes `$XDG_RUNTIME_DIR/openaten/status.json`; `openaten ctl {status,on,off,toggle,quit}`
controls it over `ctl.sock`. The bridge end is only claimed while forwarding is on. Build with `nix build .#default` (or `nix run . -- daemon`).

A Noctalia v5 bar widget lives in the `stags/openaten` plugin of the personal plugin repo: it shows bridge/forwarding state and toggles on click
(add `stags/openaten:widget` to a bar). The widget runs `openaten ctl toggle`, so `openaten` must be on the shell's PATH.
