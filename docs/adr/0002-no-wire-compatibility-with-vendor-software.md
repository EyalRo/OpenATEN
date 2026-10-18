# 2. No wire compatibility with the vendor software

Status: accepted

## Context
The vendor's BEZEL X uses the same bridge for hand-off, clipboard and file transfer on Windows and macOS. Reproducing its protocol would
mean reverse-engineering proprietary software, and nothing we need depends on it.

## Decision
OpenATEN reuses only the hardware commands verified by observation (bulk-only SCSI 0xF2 transport and the HID interface) and defines its
own link and application protocols. No vendor software or firmware is included in the repository.

## Consequences
- Both computers must run OpenATEN; interoperability with BEZEL X is not a goal and has not been demonstrated.
- We are free to version and evolve our own protocols (hand-off messages carry a version field).
