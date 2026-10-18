# Security policy

OpenATEN forwards keyboard and mouse input between computers and runs a local control socket, so security reports matter.

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab of this repository), not as a public issue.
Include the version or commit, what an attacker needs (local user, another host on the link, physical access to the switch), and a reproduction.

## Scope

- The daemon's control socket (`$XDG_RUNTIME_DIR/openaten/ctl.sock`): mode 0600 in a 0700 directory, peer uid checked.
- The link and hand-off protocols between the two computers: the link is a private USB path, but treat the peer's messages as untrusted input.
- Clipboard sharing and file transfer (`clipboard.py`, `transfer.py`): both are opt-in. Incoming file names, sizes and chunks come from the other computer and are validated (see docs/applications.md); a path escape, an overwrite or an unbounded write there is a vulnerability.
- The udev rule grants the logged-in user access to the bridge device; it should not grant more.

Out of scope: the switch's own firmware and vendor software.
