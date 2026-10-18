# 1. Master-driven polling for the link layer

Status: accepted

## Context
The bridge firmware enforces strict turn-taking: a transmit is only accepted when the peer has a receive of the same length armed, an
idle receive stalls after about 5.5 s, and a transmit with no receive pending blocks. A symmetric, free-running protocol would
deadlock or wedge the firmware (see docs/hardware.md).

## Decision
One end is the link master and sends a frame (a poll when idle, at least once a second); the slave answers every frame with exactly one
reply. Frames announce the length of the next one so the receiver can arm the right size. Either side resynchronises itself after a crash.

## Consequences
- Slave-to-master latency is bounded by the poll interval; a want-poll flag speeds this up while input is flowing.
- The roles are an application choice, independent of which physical end is "master" in the firmware.
- Detail and measurements: docs/design.md.
