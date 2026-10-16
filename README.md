# OpenATEN

Open tooling for the **host-to-host bridge** built into ATEN US3342-class USB-C sharing switches
(Genesys Logic controller, USB ID `05e3:0627`).

These switches let two computers share keyboard, mouse and other USB devices. Behind the scenes they also contain a
private link between the two computers, which the vendor's software ("BEZEL X") uses for seamless mouse/keyboard hand-off,
clipboard sharing and file transfer, and which only works on Windows and macOS. OpenATEN documents that link and aims to
provide the same features on Linux, **without the network**, so they keep working with a VPN up.

> **Status: experimental research code (v0.1).** The transport works on real hardware in loopback: packets cross the
> bridge byte-for-byte in both directions, and keyboard/mouse reports injected on one end arrive on the other.
> The cross-screen, clipboard and file-transfer applications are **not built yet**. Interoperability with the vendor's
> own software has **not** been demonstrated. Read [docs/hardware.md](docs/hardware.md) before running anything: a few
> command patterns can wedge the switch's firmware until it is power-cycled.

## What works today

| | |
|---|---|
| Discover the bridge ends and their master/slave roles | `openaten list`, `openaten state` |
| Packet round-trip between the two ends (both on one host) | `openaten loopback` |
| Keyboard/mouse injection onto the other end's HID interface | `openaten hid` |
| Documented command and packet formats | [docs/protocol.md](docs/protocol.md) |

## Quick start

```sh
sudo install -m 0644 udev/70-openaten.rules /etc/udev/rules.d/   # then reload udev (see docs/setup.md)
pip install -e .
openaten list
openaten loopback -n 5
```

Connect **both** upstream USB-C ports of the switch to the computer (two ports of the same machine is fine for development).
The bridge device only appears when both upstream ports are connected.

## Plan

See [docs/design.md](docs/design.md). In short: keep the verified data channel and HID injection, design our own framed
messaging on top (turn-taking, because each end's pipe is serial), then build input forwarding, clipboard and files.

## Disclaimer

OpenATEN is an independent project, created through observation of the hardware and interoperability research. It is
**not affiliated with, endorsed by, or supported by ATEN International or Genesys Logic**; those names and "BEZEL X" are
trademarks of their respective owners. No vendor software, firmware or other proprietary files are included in this
repository. Use at your own risk; sending vendor commands to the switch can leave it unresponsive until power-cycled.

## License

MIT. See [LICENSE](LICENSE).
