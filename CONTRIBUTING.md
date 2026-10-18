# Contributing

OpenATEN is experimental research code; small, well-tested changes are easiest to review.

## Setup and checks

```sh
pip install -e ".[dev]"
pre-commit install            # optional: runs ruff on commit
ruff check . && mypy && pytest
```

CI runs the same checks on Python 3.10-3.12. The tests use a fake transport that models the firmware's turn-taking, so they need no hardware.

## Layout

| Layer | Modules |
|---|---|
| USB transport and packet formats | `usb.py`, `protocol.py` |
| Reliable link | `link.py` |
| Input capture and injection | `hid.py`, `forward.py`, `edges.py` |
| Hand-off between computers | `control.py` |
| Background service and UI | `daemon.py`, `strip.py`, `noctalia/` |
| Command line and hardware self-tests | `cli.py`, `selftest.py`, `ends.py` |

Keep lower layers free of imports from higher ones, and keep hardware access behind the `Transport` / bridge-end seams so the logic stays testable.

## Conventions

- Logging uses `logging.getLogger(__name__)` with standard levels; no `print` except for command output.
- Do not change the wire formats in `docs/protocol.md` or the control socket's JSON replies without noting it in the CHANGELOG.
- Add a CHANGELOG entry under "Unreleased" for user-visible changes.
- Read [docs/hardware.md](docs/hardware.md) before running anything on a real switch: some command patterns wedge the firmware until power-cycle.
- Never commit vendor software or firmware.
