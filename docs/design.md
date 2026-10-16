# OpenATEN MVP design

Goal: a small cross-platform program, run on **both** computers, that provides what the vendor's BEZEL X offers
without depending on it, and without using the network (so it keeps working while a VPN is active):

1. keyboard/mouse hand-off between the computers ("cross screen"),
2. clipboard sharing,
3. file transfer.

We do **not** aim for wire compatibility with BEZEL X. We reuse the bridge hardware and the parts of its protocol that
are verified to work, and design the rest ourselves.

## Building blocks (verified)

- **Data channel:** packets of 2 KiB (larger transfers are plausible) between the two ends, byte-identical.
- **Input injection:** `HID` command; the receiving computer sees an ordinary USB keyboard/mouse, so **no software is needed
  on the receiving side** for sending input.

## Cross screen

The sender watches its own pointer. When it reaches the configured screen edge it grabs the local input devices
(evdev on Linux, raw input on Windows) and forwards relative motion, buttons and keys as HID reports. A hotkey (or, with a
helper running on the other computer, the far edge) returns control.

## Clipboard and files

Both need the data channel in both directions, which runs into the flow-control limitation in [hardware.md](hardware.md):
each end can either wait for data or send, not both. The MVP therefore uses a **turn-taking** scheme:

- both ends idle in receive;
- a side with something to send aborts its pending receive (bulk-only reset), sends, and re-arms;
- messages are framed (type, length, sequence, checksum) with acknowledgements and retries, so a dropped hand-over only
  costs latency.

Expected cost: about a second of latency per hand-over with the current windows, which is fine for clipboard and
acceptable for files if transfers are large and one-directional. Open questions that could improve this:

- how the vendor software keeps a read pending while also sending (state bits 4 and 7);
- whether larger transfers (the vendor software uses chunks bigger than 2 KiB) raise throughput substantially;
- whether the other computer's driver stack (Windows `usbstor`) behaves better than a VM passthrough.

## Roadmap

1. [done] Transport, packet builders, unit tests, loopback and HID self-tests.
2. Receive service with auto re-acquire after re-enumeration; turn-taking messaging layer with tests against a fake transport.
3. Input forwarding (Linux sender first).
4. Clipboard (Wayland and X11), then files.
5. Second client for the other computer (Linux first; Windows needs SCSI pass-through instead of libusb).
