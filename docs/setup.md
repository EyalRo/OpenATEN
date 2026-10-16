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
openaten forward --max-active 600                  # real run; ctrl+alt+scrolllock toggles; auto-returns control after 10 min
```

Reading and grabbing keyboards and mice needs access to `/dev/input/event*`: add your user to the `input` group
(NixOS: `users.users.<name>.extraGroups = [ "input" ];`). While forwarding is on the local devices are grabbed, so nothing reaches
this desktop; toggling off (or any exit) releases the grab and releases every key/button on the other computer. The other
computer needs no OpenATEN software for this: it sees an ordinary USB keyboard and mouse.

Also recommended: stop the kernel from probing the bridge's storage function (see [hardware.md](hardware.md)):
`boot.kernelParams = [ "usb-storage.quirks=05e3:0627:i" ];` on NixOS.
