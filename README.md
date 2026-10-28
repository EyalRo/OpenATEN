# OpenATEN

Open tooling for the **host-to-host bridge** built into ATEN US3342-class USB-C sharing switches
(Genesys Logic controller, USB ID `05e3:0627`).

These switches let two computers share keyboard, mouse and other USB devices. Behind the scenes they also contain a
private link between the two computers, which the vendor's software ("BEZEL X") uses for seamless mouse/keyboard hand-off,
clipboard sharing and file transfer, and which only works on Windows and macOS. OpenATEN documents that link and aims to
provide the same features on Linux, **without the network**, so they keep working with a VPN up.

![ATEN US3342 overview: host-visible USB devices, how OpenATEN uses the bridge (SCSI 0xF2 over bulk-only transport plus the HID interface), and the features carried on top](docs/images/us3342-bridge-overview.webp)

> **Status: experimental research code (v0.1).** The transport and a reliable message link work on real hardware in loopback:
> messages flow both ways at ~75-90 MB/s, survive process crashes (self-resync), and keyboard/mouse reports injected on one end
> arrive on the other. Edge hand-off, clipboard sharing and file transfer sit on the link; the last two are built and unit-tested against a fake link but have **not** been run on hardware yet. Interoperability with the vendor's
> own software has **not** been demonstrated. Read [docs/hardware.md](docs/hardware.md) before running anything: a few
> command patterns can wedge the switch's firmware until it is power-cycled.

## What works today

| | |
|---|---|
| Discover the bridge ends and their master/slave roles | `openaten list`, `openaten state` |
| Packet round-trip between the two ends (both on one host) | `openaten loopback` |
| Reliable messaging link (framing, CRC, chunking, polling, auto-resync) | `openaten link --mib 64` |
| Keyboard/mouse injection onto the other end's HID interface | `openaten hid` |
| Edge hand-off between the computers' screens (mouse pushes through the shared border) | [docs/setup.md](docs/setup.md#edge-hand-off-push-the-pointer-through-the-screen-border) |
| Clipboard sharing and file transfer between the computers (on by default, **untested on hardware**) | [docs/applications.md](docs/applications.md) |
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

## Where things live

- **Roadmap and design**: [docs/design.md](docs/design.md). Done: the link layer, input forwarding and edge hand-off. Built but not yet run on hardware:
  clipboard sharing and file transfer ([docs/applications.md](docs/applications.md)). Open: Windows client, hardware verification.
- **Rust core port**: [docs/rust.md](docs/rust.md) documents the standalone Rust reimplementation of the core daemon and CLI in [`rust/`](rust/).
- **Tasks**: [GitHub issues](https://github.com/EyalRo/OpenATEN/issues). **Findings and how-tos**: the [wiki](https://github.com/EyalRo/OpenATEN/wiki).
- **Reference docs** that ship with the code: [docs/](docs/) (protocol, hardware, setup, architecture, troubleshooting, decision records in `docs/adr/`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and the module layout, [CHANGELOG.md](CHANGELOG.md) for what changed, and
[SECURITY.md](SECURITY.md) to report a vulnerability.

## Disclaimer

OpenATEN is an independent project, created through observation of the hardware and interoperability research. It is
**not affiliated with, endorsed by, or supported by ATEN International or Genesys Logic**; those names and "BEZEL X" are
trademarks of their respective owners. No vendor software, firmware or other proprietary files are included in this
repository. Use at your own risk; sending vendor commands to the switch can leave it unresponsive until power-cycled.

## License

MIT. See [LICENSE](LICENSE).
