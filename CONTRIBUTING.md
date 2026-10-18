# Contributing

OpenATEN is experimental research code; small, well-tested changes are easiest to review.

## Setup and checks

```sh
pip install -e ".[dev]"
pre-commit install            # optional: runs ruff on commit
ruff check . && mypy && pytest
```

Tests that need a real switch are marked `hardware` and skipped by default; run them with `pytest -m hardware` (both upstream ports connected,
see [docs/hardware.md](docs/hardware.md) for the hazards).

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

Where things are documented: the **README** is the entry point; **docs/** holds reference docs that ship with the code (setup, protocol,
hardware hazards, architecture, troubleshooting, ADRs); the **wiki** holds findings and how-tos (what we learned on hardware); **issues** are the task list.
Architecture notes are in [docs/architecture.md](docs/architecture.md); design decisions are recorded in [docs/adr/](docs/adr/). The roadmap lives
in [docs/design.md](docs/design.md); individual tasks are GitHub issues.

## Conventions

- Logging uses `logging.getLogger(__name__)` with standard levels; no `print` except for command output.
- Do not change the wire formats in `docs/protocol.md` or the control socket's JSON replies without noting it in the CHANGELOG.
- Add a CHANGELOG entry under "Unreleased" for user-visible changes.
- Read [docs/hardware.md](docs/hardware.md) before running anything on a real switch: some command patterns wedge the firmware until power-cycle.
- Never commit vendor software or firmware.

## Releasing

1. Bump `version` in `pyproject.toml` (the package and the Nix flake read it from there) and move the CHANGELOG's "Unreleased" entries under the new version.
2. Merge, then tag `vX.Y.Z` on `main` and push the tag. The `Release` workflow checks that the tag equals the package version, runs the checks,
   builds, and creates the GitHub release with the sdist and wheel attached.
3. PyPI (one-time set-up, then automatic): on pypi.org add a *trusted publisher* for this repository (workflow `release.yml`, environment `pypi`),
   create the `pypi` environment in the repository settings, and set the repository variable `PUBLISH_TO_PYPI` to `true`. Until then the `publish` job is skipped.
