# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

## 0.2.0 - 2026-10-19

### Added
- `openaten-panel` (`panel/`, `nix run .#panel`): a tray icon that follows the daemon's status file (offline, connected, forwarding, error, with a detailed tooltip and menu) and a GTK4/libadwaita setup window opened by left-click. The window mirrors the daemon, has the forwarding button and a Pointer warp section: `/dev/uinput` access in green/red with an explicit confirmation before the one privileged step (`pkexec setfacl`), the warp setting saved to `config.toml` (formatting kept, `.bak` written), Restart to apply and a Test button. It is meant to replace the Noctalia widget, which stays for now.
- Pointer warp (`daemon --warp uinput`, `warp = "uinput"` in `config.toml`, off by default): a virtual absolute pointer places this computer's pointer where the other one came back after a hand-off, also in `motion` mode (`EdgeSwitch.leaving`). `status.json` reports `warp` and `uinput` (`ok`/`denied`/`missing`); `openaten warp-test` moves the pointer to four places to check it. See docs/setup.md, Pointer warp.
- `LinkSession.flush()`: wait until every queued chunk has been sent.
- Noctalia widget 0.2.0: detailed tooltip rows and a configurable `openaten` path (setting).
- `openaten.warp.Warp` extension point for exact pointer placement on hand-off (the daemon calls it after `take-control` and on `give-back`), `EdgeSwitch.entry_point`; the uinput backend is listed under 0.2.0.
- Docs: roles (master/slave vs the firmware's H/S), other-distribution udev/kernel-quirk steps, pipx install.
- `openaten send DIR` sends a directory tree (one transfer per regular file, symlinks skipped, never merged into an existing directory); status shows a summary per directory.
- Edge hand-off: `local_outputs = "niri"` reads this computer's screens from `niri msg --json outputs`; touchpads now drive the pointer tracker in motion mode (`touchpad_scale`). Not yet run against a real niri.
- NixOS module (`nixosModules.default`, `services.openaten`): package, udev rule, kernel quirk, `input` group and a systemd user service; evaluated by `nix flake check`.
- Release workflow publishes to PyPI via trusted publishing once `PUBLISH_TO_PYPI` is set (skipped until then); release steps in CONTRIBUTING.
- Clipboard sharing (`daemon --clipboard`; text and PNG, wl-clipboard/xclip) and file transfer (`openaten send`, `daemon --receive-dir`) over the link (on by default since 0.2.0); see docs/applications.md. `status.json` gains `clipboard` and `transfers`. Not yet verified on hardware.
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

### Changed
- Clipboard sharing is on by default (`--no-clipboard` turns it off) and received files go to `~/Downloads` (`--receive-dir` to change, empty to turn receiving off); the NixOS module follows (`clipboard` defaults to true, `receiveDir` to `%h/Downloads`).
- The peer link waits on its stop event instead of sleeping, and `Forwarder.close` waits on `select` for key releases (#24).
- The dev shell includes `hypothesis`, so `nix develop -c pytest` collects every test.
- Tests no longer read the developer's own `~/.config/openaten` (an autouse fixture isolates `XDG_CONFIG_HOME`).
- `BridgeEnd` commands raise typed `TransportError`s (`openaten.errors`) instead of pyusb's `USBError`.
- Nix package runs the test suite and takes its version from `pyproject.toml`.
- `cli.py` split: hardware self-tests in `selftest.py`, bridge-end bookkeeping in `ends.py`.
- Version is read from package metadata.

### Fixed
- While forwarding, the hotkey's trigger key (the letter `k` in the default `ctrl+alt+shift+k`) never reached the other computer: every press of it was swallowed, not only the toggling one.
- A control request arriving before the daemon's first loop iteration got an empty status (made `test_control_socket_roundtrip` flaky): the daemon publishes an initial status before serving the control socket.
- The two-computer link self-test (`link --role`): the slave reported `delivered=False` after 60 s because the master stopped with its own last chunk still queued (#44).
- Nix package tests now run (they failed for lack of `hypothesis`, and `nix flake check` did not build the package); the sender reads files unbuffered so a file that changes while being sent is detected.

## 0.1.0

Initial experimental release: transport, reliable link, HID injection, input forwarding, edge hand-off, daemon and Noctalia widget.
