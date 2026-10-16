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
5. A udev rule numbered below 73 (see `udev/70-openaten.rules`) is needed for the `uaccess` tag to give your user
   access to the USB node without root.
6. If anything misbehaves, power-cycle the switch (power and both USB-C cables).

## Flow control (the open problem)

- With a long-lived RX pending on one end, TX from the other completes immediately and the packet arrives
  byte-identical. **[verified, both directions, both modes]**
- Each end's transport is a single serial pipe, so an end cannot hold an RX pending *and* send at the same time.
- Polling with short receive windows does not work: in loopback, windows of 400 ms or less delivered **0 of 5** packets;
  windows of 1 s or more delivered all of them, but slowly (about 1.6 s per packet).
- The state byte did not show "data waiting" or "grant" bits during any of this, so we do not yet know how the vendor
  software coordinates the two directions at speed.

See [design.md](design.md) for how the MVP deals with this.

## Notes on the vendor software

The vendor's Windows application was run in a QEMU/KVM VM with one bridge end passed through. As the *slave* it initialised
fine and then waited for the master to connect. As the *master* its mode-switch command timed out through the VM's USB
passthrough (Windows reset the device about every 8 s). Whether that is a passthrough quirk or a real incompatibility is
unresolved; an interop test on two physical computers is the next useful experiment.
