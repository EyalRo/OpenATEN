# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
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
- `BridgeEnd` commands raise typed `TransportError`s (`openaten.errors`) instead of pyusb's `USBError`.
- Nix package runs the test suite and takes its version from `pyproject.toml`.
- `cli.py` split: hardware self-tests in `selftest.py`, bridge-end bookkeeping in `ends.py`.
- Version is read from package metadata.

## 0.1.0

Initial experimental release: transport, reliable link, HID injection, input forwarding, edge hand-off, daemon and Noctalia widget.
