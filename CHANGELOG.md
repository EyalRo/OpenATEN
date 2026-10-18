# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- `openaten send DIR` sends a directory tree (one transfer per regular file, symlinks skipped, never merged into an existing directory); status shows a summary per directory.
- Edge hand-off: `local_outputs = "niri"` reads this computer's screens from `niri msg --json outputs`; touchpads now drive the pointer tracker in motion mode (`touchpad_scale`). Not yet run against a real niri.
- NixOS module (`nixosModules.default`, `services.openaten`): package, udev rule, kernel quirk, `input` group and a systemd user service; evaluated by `nix flake check`.
- Release workflow publishes to PyPI via trusted publishing once `PUBLISH_TO_PYPI` is set (skipped until then); release steps in CONTRIBUTING.
- Clipboard sharing (`daemon --clipboard`; text and PNG, wl-clipboard/xclip) and file transfer (`openaten send`, `daemon --receive-dir`) over the link, opt-in; see docs/applications.md. `status.json` gains `clipboard` and `transfers`. Not yet verified on hardware.
- Daemon reconnects immediately when the bridge re-enumerates (kernel uevent netlink listener, `openaten.hotplug`); falls back to polling where unavailable.
- `hardware` pytest marker (`pytest -m hardware`) running the list/state/loopback/link/hid self-tests against a real switch.
- Optional `config.toml` for command defaults (`--config`, `contrib/config.example.toml`).
- CI (ruff, mypy, pytest on Python 3.10-3.12), pre-commit, Dependabot, issue/PR templates, SECURITY and CONTRIBUTING.
- Structured logging: `--log-level` / `OPENATEN_LOG`, standard levels on stderr.
- Hand-off messages carry a protocol version (`v`); newer versions and unknown kinds are ignored.
- Control socket: request timeout and peer-uid check.
- Link restart in the daemon backs off from 2 s to 30 s.

- CodeQL workflow; GitHub Actions pinned by commit SHA. (A dependency-review workflow needs the repository's Dependency graph enabled; add `actions/dependency-review-action` on `pull_request` once it is.)
- systemd user unit (`contrib/systemd/openaten.service`) and docs.
- Property-based tests (hypothesis) for the link frame format and the hand-off state machine.

- Mouse-report coalescing for backlogs (`protocol.coalesce_mouse`, stat `hid_coalesced`).
- Docs: architecture, troubleshooting, ADRs; coverage floor in CI.

### Fixed
- Nix package tests now run (they failed for lack of `hypothesis`, and `nix flake check` did not build the package); the sender reads files unbuffered so a file that changes while being sent is detected.

### Changed
- `BridgeEnd` commands raise typed `TransportError`s (`openaten.errors`) instead of pyusb's `USBError`.
- Nix package runs the test suite and takes its version from `pyproject.toml`.
- `cli.py` split: hardware self-tests in `selftest.py`, bridge-end bookkeeping in `ends.py`.
- Version is read from package metadata.

## 0.1.0

Initial experimental release: transport, reliable link, HID injection, input forwarding, edge hand-off, daemon and Noctalia widget.
