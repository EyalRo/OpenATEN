# Hardware behaviour and hazards

Tested with an ATEN US3342 (Genesys GL3590 hub + bridge, firmware revision 6891, bridge `bcdDevice` 0x6893) on a Dell XPS 15 9500
running NixOS, with both upstream cables connected to the same computer.

## What enumerates

- Two hubs (`0557:2414` USB 2, `0557:2415` USB 3) for the shared peripherals. These are ordinary hubs and need no driver.
- With **both** upstream ports connected: two bridge devices `05e3:0627` ("590A01 / USB Storage").
  Each has a mass-storage interface (a virtual CD-ROM LUN by default) and a HID mouse+keyboard interface.
- With only one port connected the bridge device does not appear.
- INQUIRY (CD-ROM mode, 0x34 bytes) carries vendor bytes: `+0x24 05 e3 06 27`, `+0x28 "GENE"`, `+0x2E 'H'|'S'` role,
  `+0x2F 02` firmware protocol, `+0x30 'E'` chip version, `+0x31 u16` firmware version. In disk mode only the standard 36 bytes are returned.

## Rules we learned the hard way

1. **Never let the kernel's `usb-storage` driver keep probing the bridge while you issue vendor commands.**
   Repeated re-attach/scan cycles wedged the firmware: processes hung for minutes in `usb_stor_disconnect`, and only a
   switch power-cycle cleared it. OpenATEN detaches the kernel driver and keeps it detached while it runs.
2. **A TX with no RX pending on the peer blocks** (the command block itself is not accepted, then the pipe stalls
   after ~5.5 s). Always have the receiver armed first.
3. **Issue a bulk-only reset (class request 0xFF) and clear both halts after any failed or timed-out transfer.**
4. **`SWITCH` (0x07) re-enumerates both ends at once** and flips the LUN between CD-ROM and disk mode. Ports keep their
   path (e.g. `1.2`, `2`) but get new device addresses; find ends by port path, not address.
5. **Keep the kernel's `usb-storage` driver away from the bridge's mass-storage function** (kernel parameter
   `usb-storage.quirks=05e3:0627:i`). When the kernel binds at plug-in or boot it scans the firmware with its own commands; repeated
   re-binds wedged the firmware in our early tests. (We could not show that it is the cause of the refusal described in 9.)
6. **The peer's HID interface must be polled.** HID injection only works while something polls the receiving end's interrupt
   endpoint (an OS driver with an input device open). If a test tool detaches `usbhid` and exits without re-attaching it, further
   injections are refused. `openaten hid` restores the driver when it finishes.
7. A udev rule numbered below 73 (see `udev/70-openaten.rules`) is needed for the `uaccess` tag to give your user
   access to the USB node without root.
8. If anything misbehaves, power-cycle the switch (power and both USB-C cables).
9. **Open question:** after a link session, the first stand-alone data-in command (state, INQUIRY) from a *new process* on one end is
   sometimes refused (twice in a row), while a data-out command (HID) is accepted and afterwards the data-in command works. `BridgeEnd.simple()`
   works around it (retry after reset, then a zero mouse report). HID injection itself has been reliable.

## Flow control: strict turn-taking (verified)

Each end's transport is one serial pipe, and the firmware enforces a request/response rhythm:

1. A **TX completes only after the peer's RX command has finished completely**, including the host reading that RX's
   status block (CSW). A receiver must therefore read its CSW immediately after its data arrives.
2. A TX command block is not accepted (the host's write times out) until the peer has an RX armed.
3. **Strict alternation:** one end does *TX then RX*, the other does *RX then TX*. Any out-of-turn command (for example a
   second RX with no TX in between) is refused. This state lives in the firmware and survives process exits, BOT resets and
   USB device resets. It is cleared by completing the owed exchange (the owing end sends its reply while the other end has an RX
   armed) or by power-cycling.
4. A pending RX cannot be abandoned from the host: cancelling the transfer or doing a BOT reset does not free that end. Let the
   firmware's own ~5.5 s idle stall expire (use host timeouts above 6 s), then reset and re-arm.
5. Either physical end can take the "TX first" role; the `H`/`S` INQUIRY byte did not constrain who may initiate in our tests.

**Master-driven polling works well:** the slave keeps an RX armed; the master sends, then arms an RX; the slave replies as soon
as its RX completes and re-arms. In loopback: 200/200 round trips at about 477 per second, 2 ms latency (p95 2.1 ms).

### Measured throughput (loopback, checksum-verified)

| message size | echo (both directions combined) | one-way push with 2 KiB ack |
|---|---|---|
| 2 KiB | 2.0 MB/s | |
| 8 KiB | 7.6 MB/s | |
| 32 KiB | 28 MB/s | |
| 64 KiB | 48 MB/s | 28 MB/s |
| 128 KiB | 79 MB/s | |
| 256 KiB | 106 MB/s | 61 MB/s |
| 512 KiB | | 83 MB/s |
| 1 MiB | | 101 MB/s |
| 2 MiB | | 116 MB/s |

Only transfers whose length equals the armed receive buffer were tested; shorter-than-buffer transfers are not verified yet.

See [design.md](design.md) for the link design built on these rules.

## Notes on the vendor software

The vendor's Windows application was run in a QEMU/KVM VM with one bridge end passed through. As the *slave* it initialised
fine and then waited for the master to connect. As the *master* its mode-switch command timed out through the VM's USB
passthrough (Windows reset the device about every 8 s). Whether that is a passthrough quirk or a real incompatibility is
unresolved; an interop test on two physical computers is the next useful experiment.
