# Rust core port

A standalone Rust reimplementation of OpenATEN's **core daemon and CLI** lives in
[`rust/`](../rust/), next to the Python reference. It is a faithful port of the parts
that do not need live hardware: the wire protocol, the reliable link, the application
protocols, the daemon, the config loader and the `list`/`state`/`daemon`/`send`/`ctl`
commands. The USB transport (`05e3:0627`, bulk-only via libusb) is ported too and wired
into `list`/`state`/`daemon`; the remaining hardware-bound layer (evdev runner, uinput
warp backend, netlink hotplug, GTK strips) is still left out and plugs in through the
traits described below.

The crate is `openaten-core` (a library) plus an `openaten` binary. All unit tests run
without hardware (`cargo test`, 109 tests).

## Status

**Experimental.** The core logic is ported and unit-tested against the same fake transport
the Python tests use, and the USB transport has been exercised against a live switch
(`openaten list` enumerates a real bridge end). The full `daemon` link session has **not**
yet been validated end-to-end against a switch with the other side live. Nothing here
changes the wire formats or the control socket's JSON replies defined by the Python
reference.

## Where things are

| Module | What it ports |
|---|---|
| `protocol.rs` | framing, checksums, message codes |
| `appmsg.rs` | clipboard / file channel framing and packing |
| `control.rs` | hand-off control protocol and the `Handoff` state machine |
| `link.rs` | reliable peer link on top of one end |
| `transfer.rs` | file transfer manager (receive/send, offers, chunks, acks) |
| `clipboard.rs` | clipboard sync (send changes, write received clips) |
| `config.rs` | `config.toml` loader/merger |
| `hotplug.rs` | hotplug source abstraction |
| `usb.rs` | libusb bulk-only transport for `05e3:0627`, `BridgeEnd` + `UsbTransport` + `EndAdapter` |
| `warp.rs` | pointer-warp trait, uinput access probe |
| `daemon.rs` | background service: runner, bridge end, link, `status.json` + `ctl.sock`, retry/backoff |
| `cli.rs` | argument parsing and the `list`/`state`/`daemon`/`send`/`ctl` commands |
| `log.rs` | structured logging levels |

The Python modules that have **no** Rust counterpart (`hid.py`, `edges.py`,
`forward.py`, `outputs.py`, `strip.py`, `noctalia/`, `selftest.py`, `ends.py`) stay Python:
they need a real switch, a display, or the window system. `usb.py` **is** ported
(`rust/src/usb.rs`); `ends.py`'s open/close bookkeeping is folded into the daemon's
`EndHandle`.

## Hardware plug-in seams

The daemon never touches hardware directly; every device-facing dependency is injected as a
trait object at construction, so the whole core runs and is tested with no devices present.

| Trait / seam | Python equivalent | Hardware backend to write |
|---|---|---|
| `End` | `BridgeEnd` (`usb.py`) | ✅ ported (`usb.rs`): libusb bulk-only transport, `05e3:0627` |
| `Runner` | `EvdevRunner` / `Forwarder` (`forward.py`) | evdev capture and injection |
| `Warp` | `Warp` (`warp.py`) | uinput absolute pointer |
| `HotplugSource` | `hotplug.py` | kernel uevent netlink listener |
| `ClipboardLike` / `TransfersLike` | `clipboard.py` / `transfer.py` | host clipboard backends |
| `Link` | `LinkSession` (`link.py`) | built-in; the daemon drives it with `UsbLink`; a `FakeLink` drives the tests |

The daemon ships a no-op `NullRunner`/`NullWarp` so it still runs the core loop (creates
`ctl.sock` and `status.json`) with no bridge attached. With a bridge present and a `--role`,
the `End` seam (`EndAdapter::into_transport`) supplies a real `UsbTransport`, and `UsbLink`
drives a `LinkSession` on a dedicated thread.

## Building and testing

```sh
cd rust
nix shell nixpkgs#cargo nixpkgs#rustc nixpkgs#gcc nixpkgs#pkg-config nixpkgs#libusb1 -c cargo test   # 109 tests, no hardware
cargo build --release && ./target/release/openaten list
```

No `cargo` toolchain is required system-wide; the `nix shell` line brings the compiler in
without touching your environment. The `pkg-config` and `libusb1` packages are needed for
the `rusb` crate to find the system libusb. (The flake's `rust-core` package wires these
automatically.)

## CLI

`openaten` supports the subcommands that need no hardware:

```sh
openaten list            # discover bridge ends (reports no bridge when none present)
openaten state           # read each end's role and state byte (needs the interface unclaimed)
openaten daemon [flags]  # run the core daemon (creates ctl.sock + status.json; links when --role + a bridge)
openaten send PATH...    # send file(s) to the other computer
openaten ctl CMD         # talk to a running daemon: status | on | off | toggle | quit
```

The hardware self-test commands (`loopback`, `link`, `forward`, `strip`, `warp-test`,
`layout`, `hid`) are not ported.

## Scope of this port

Ported and tested:

- protocol, appmsg, control, link, transfer, clipboard, config, hotplug, warp, daemon, cli.
- USB transport (`usb.rs`): bulk-only CBW/CSW transport, end discovery, `End`/`Transport`
  adapters. Wired into `list`/`state` and the daemon's link seam.

Not ported (needs live hardware, a display, or the window system):

- HID/evdev runner, uinput warp backend, netlink hotplug, GTK strips, compositor output
  discovery, hardware self-tests.