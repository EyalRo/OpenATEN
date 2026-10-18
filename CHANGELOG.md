# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- CI (ruff, mypy, pytest on Python 3.10-3.12), pre-commit, Dependabot, issue/PR templates, SECURITY and CONTRIBUTING.
- Structured logging: `--log-level` / `OPENATEN_LOG`, standard levels on stderr.
- Hand-off messages carry a protocol version (`v`); newer versions and unknown kinds are ignored.
- Control socket: request timeout and peer-uid check.
- Link restart in the daemon backs off from 2 s to 30 s.

### Changed
- `cli.py` split: hardware self-tests in `selftest.py`, bridge-end bookkeeping in `ends.py`.
- Version is read from package metadata.

## 0.1.0

Initial experimental release: transport, reliable link, HID injection, input forwarding, edge hand-off, daemon and Noctalia widget.
