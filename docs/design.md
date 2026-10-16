# OpenATEN MVP design

Goal: a small cross-platform program, run on **both** computers, that provides what the vendor's BEZEL X offers
without depending on it, and without using the network (so it keeps working while a VPN is active):

1. keyboard/mouse hand-off between the computers ("cross screen"),
2. clipboard sharing,
3. file transfer.

We do **not** aim for wire compatibility with BEZEL X. We reuse the bridge hardware and the parts of its protocol that
are verified to work, and design the rest ourselves.

## Building blocks (verified, see hardware.md)

- **Data channel** with strict turn-taking: 2 ms round trips; up to ~116 MB/s one-way with 2 MiB messages.
- **Input injection** (`HID` command): the receiving computer sees an ordinary USB keyboard/mouse, so no software is
  needed on the receiving side for sending input.

## Link layer: master-driven polling

One end is the **link master** (application role; either physical end can be it), the other the **link slave**.

```
master:  TX(frame) -> RX(reply)  ...repeat
slave :  RX(frame) -> TX(reply)  ...repeat
```

- The master polls at ~20-50 Hz while anything is active and at least once a second when idle (this also keeps the slave's
  armed RX from hitting the firmware's 5.5 s idle stall).
- Every master frame gets exactly one slave reply. A reply carries queued slave-to-master data, or is empty.
- Frames: magic, version, type, sequence, acknowledged sequence, payload length, CRC32, payload. Bulk data uses large frames
  (hundreds of KiB); control traffic uses small ones.
- **Resync at start-up** (firmware turn state survives crashes): each end first tries its expected command with a short
  timeout; if refused it does the other half of the exchange (master arms RX, slave sends an empty reply), until the pair is in step.
- Never abandon a pending RX from the host; use timeouts above the firmware stall (> 6 s), then BOT-reset and re-arm.

## Cross screen

The sender watches its own pointer. At the configured edge (or on a hotkey) it grabs the local input devices
(evdev on Linux, raw input on Windows) and forwards relative motion, buttons and keys as HID reports. A hotkey (or, with a
helper running on the other computer, the far edge) returns control. HID commands also obey the turn rule, so they ride
on poll cycles (a few ms each).

## Clipboard and files

Both use the link layer: clipboard text/images as typed messages; files as chunked streams with per-chunk checksums,
progress and cancel, using large frames to reach tens of MB/s.

## Roadmap

1. [done] Transport, packet builders, unit tests, loopback and HID self-tests.
2. [done] Flow-control research: turn rules, polling, throughput (hardware.md).
3. [done] Link layer (`openaten.link`): framing with CRC and announced next-frame lengths, chunked messages up to any size, master/slave loops,
   self-resync, tests against a fake transport that models the turn rules, `openaten link` hardware self-test; survives SIGKILL of either side mid-transfer.
4. Input forwarding (Linux sender first).
5. Clipboard (Wayland and X11), then files.
6. Second client for the other computer (Linux first; Windows needs SCSI pass-through instead of libusb).
