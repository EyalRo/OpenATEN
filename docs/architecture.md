# Architecture

OpenATEN is layered; each layer only imports from the ones above it in this list.

```
usb.py ........ libusb access to one bridge end (BridgeEnd): bulk-only commands, resets, role/state, HID injection
protocol.py ... command/packet builders, HID report layouts, mouse-report coalescing (pure, no I/O)
link.py ....... reliable message link on top of one end: framing + CRC, chunking, master-driven polling, self-resync
hid.py, edges.py
               input event decoding / key chords (hid.py) and the screen-layout geometry for edge hand-off (edges.py); pure logic
forward.py .... evdev capture: grabs the local keyboard/mouse and turns events into HID reports (EvdevRunner, Forwarder)
appmsg.py ..... framing for the clipboard and file channels; clipboard.py / transfer.py are the two application protocols on top
control.py .... hand-off protocol between the two agents (JSON on link channel 1) and the Handoff state machine
daemon.py ..... background service: owns the runner, the bridge end and the link; status.json + ctl.sock; restarts the link with backoff
strip.py ...... GTK4 layer-shell border strips that report exact pointer position to the daemon (separate process)
cli.py ........ argument parsing and commands; selftest.py = hardware self-tests; ends.py = opened-ends bookkeeping
noctalia/ ..... bar widget that reads status.json and talks to ctl.sock
```

## Two computers, one link

```
 computer A                                         computer B
 ┌───────────────────────┐                          ┌───────────────────────┐
 │ evdev → forward.py    │                          │ (receives HID like an │
 │   ↓ HID reports       │      ATEN US3342         │  ordinary keyboard/   │
 │ daemon.py ── link.py ─┼─ bridge (USB) ───────────┼─ mouse; agent only    │
 │   ↑ control.py        │  SCSI 0xF2 + HID         │  needed for hand-off) │
 │ strip.py (edges)      │                          │ daemon.py ── link.py  │
 └───────────────────────┘                          └───────────────────────┘
```

- The **link** carries typed messages (channel 1 = hand-off control). One end is the master and polls; the other answers.
- **HID injection** rides the same turn-taking, so reports are queued in the link and flushed between exchanges. Backlogs of mouse
  motion are coalesced there (`protocol.coalesce_mouse`).
- The **daemon** is the only long-lived owner of the bridge end in role mode. Everything hardware-specific is reached through the
  injectable `find_ends` / `open_end` / `make_link` seams, which is how the tests run without a switch (`tests/fake_transport.py`).

## Where to look

| To change | Start in |
|---|---|
| A bridge command or packet | `protocol.py`, `docs/protocol.md` |
| Retry/reset behaviour for the firmware | `usb.py` (`BridgeEnd.simple`), `docs/hardware.md` |
| Message framing, polling, resync | `link.py`, `docs/design.md` |
| What happens at a screen edge | `edges.py`, `control.py`, `daemon.py` |
| Status/ctl JSON seen by the widget | `daemon.py` (treat as a public contract) |
