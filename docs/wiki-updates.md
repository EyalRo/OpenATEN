# Wiki updates to sync

Pending changes for the [GitHub wiki](https://github.com/EyalRo/OpenATEN/wiki), written here because the Claude Code web session
cannot push to `OpenATEN.wiki.git` (the git proxy only authorizes the main repository). Delete this file once the wiki is updated.

Prepared against the wiki as of 2026-10-18. Each section below is one wiki page; the fenced block is the full file content for new pages
and the full replacement for `Home`. Copy it as is (the fences use `~~~` so the content can contain code blocks).

| Wiki page | Action |
|---|---|
| `Engineering-and-CI.md` | **new** |
| `Architecture-and-decisions.md` | **new** |
| `Home.md` | **replace** (adds two page links and updates "Where things live") |
| `Dev-environment-and-test-rigs.md` | **edit one paragraph** (the "Unit tests without hardware" section) |

Sync with:

```sh
git clone https://github.com/EyalRo/OpenATEN.wiki.git
cd OpenATEN.wiki
# create/replace the files from the sections below, then:
git add -A && git commit -m "Wiki: engineering/CI conventions, architecture and decisions; home links" && git push
```

---

## 1. `Engineering-and-CI.md` (new)

~~~markdown
# Engineering, CI/CD and repository conventions

Reference for how the repository is built, checked and released. The files themselves are the source of truth; this page records the *why* and the gotchas.

## Local checks
```sh
pip install -e ".[dev]"      # pytest, pytest-cov, hypothesis, ruff, mypy
ruff check . && mypy && pytest
```
- No hardware needed: tests run against `tests/fake_transport.py`, which models the firmware's turn-taking.
- `ruff format` is deliberately **not** enforced (the code uses long lines; enforcing it would reformat most files).
- Coverage floor is 60% (`[tool.coverage.report] fail_under`); the hardware-bound modules `usb.py`, `selftest.py` and `strip.py` are mostly untestable in CI. Raise it as coverage grows.

## Workflows (`.github/workflows/`)
| Workflow | Does |
|---|---|
| `ci.yml` | `test` (ruff, mypy, pytest + coverage on Python 3.10/3.11/3.12), `package` (sdist+wheel, `twine check`), `nix` (`nix flake check`, which also runs the tests in the Nix build) |
| `codeql.yml` | CodeQL for Python on push, pull requests and weekly |
| `release.yml` | On a `v*` tag: tag must equal the package version, checks run, build, GitHub release with artifacts |

- All Actions are pinned by **commit SHA** with the version in a trailing comment; Dependabot (monthly, `github-actions` and `pip`) keeps them current. We pinned the latest release within the major version already in use, so inputs did not change.
- CI cancels superseded runs (`concurrency`), so pushing while jobs run cancels them.
- Version has a single source: `pyproject.toml`. `openaten.__version__` reads package metadata; `flake.nix` reads `pyproject.toml`.

## Gotchas learned
- **Dependency review** (`actions/dependency-review-action`) fails with "not supported on this repository" until the *Dependency graph* is enabled in repo settings. It was removed from the first PR for that reason; re-add it after enabling the setting.
- The release workflow has `contents: write`: keep its Actions SHA-pinned.
- Systemd hardening such as `ProtectSystem=strict` breaks the daemon, because `$XDG_RUNTIME_DIR` (status file and control socket) becomes read-only. The shipped unit only sets `NoNewPrivileges`.

## Repo settings to apply by hand
Branch protection on `main` (require `test` and `package`, pull request before merge), CodeQL / code scanning, private vulnerability reporting (referenced by `SECURITY.md`), Dependency graph.

## Logging
Standard levels on stderr; `openaten --log-level DEBUG <command>` or `OPENATEN_LOG=DEBUG`; default INFO. Modules use `logging.getLogger(__name__)`. Only command results (`list`, `state`, `ctl`, self-tests) use `print`. Under systemd: `journalctl --user -u openaten`.
~~~

---

## 2. `Architecture-and-decisions.md` (new)

~~~markdown
# Architecture and design decisions

Layers (details and diagram: `docs/architecture.md` in the repo):
`usb` → `protocol` → `link` → `hid`/`edges` → `forward` → `control` → `daemon` → `strip`/Noctalia, with `cli`/`selftest`/`ends` as the command line. Lower layers never import higher ones; hardware is reached only through injectable seams (`find_ends`, `open_end`, `make_link`) so everything is testable with the fake transport.

## Decisions (ADRs live in `docs/adr/`)
1. **Master-driven polling link** — the firmware enforces strict turn-taking (a TX needs an armed RX of the same length; an idle RX stalls after ~5.5 s), so one end polls and the other answers every frame. Slave-to-master latency is bounded by the poll interval; a want-poll flag speeds it up while input flows.
2. **No wire compatibility with the vendor software** — only hardware commands verified by observation are reused; our own protocols on top. Interop with BEZEL X is not a goal.
3. **GitHub is the only home of this repository** — it is not on Forgejo and not in the Ops in Net sourcecode module; do not mirror it unless asked.

## Daemon and protocol behaviour worth knowing
- **Hand-off messages are versioned**: JSON with `"v"` (current 1). Newer versions and unknown `kind`s are ignored; messages without `v` count as version 1. Bump `control.VERSION` on an incompatible change.
- **Link restart backoff**: 2 s doubling to 30 s per consecutive failure, reset when the link is up; each distinct failure is logged once.
- **Control socket**: mode 0600 in a 0700 directory, 5 s request timeout, peer uid checked (`SO_PEERCRED`). The status/ctl JSON is a public contract for the widget — flag changes.
- **Mouse coalescing**: the link merges backlogged mouse reports with the same button state (motion summed, split at signed-byte range; keys and button changes never merged). Stat `hid_coalesced`. Verified with fakes only, not yet on hardware.
- **Testing the wire format**: hypothesis tests assert frames round-trip, any single corrupted byte is rejected by the CRC, decoders only raise `ValueError`, and the hand-off state machine always recovers on `release`.

See also: [Edge hand-off](Edge-hand-off), [Open questions](Open-questions), and `docs/troubleshooting.md` (symptom → cause → fix table).
~~~

---

## 3. `Home.md` (replace)

~~~markdown
# OpenATEN wiki

OpenATEN is open tooling for the **host-to-host bridge** inside ATEN US3342-class USB-C sharing switches (Genesys Logic, USB ID `05e3:0627`).
Goal: cross-screen keyboard/mouse hand-off, clipboard and file transfer between two computers over the switch (no network, VPN-proof), starting with Linux.

**Status (v0.1, experimental):** transport, keyboard/mouse injection and a master-driven polling link (2 ms round trips, ~116 MB/s pushes) are verified on hardware in loopback; the link layer is done and edge hand-off (cross-screen) is built: motion-based and an exact mode using layer-shell border strips. The strip is verified in a headless compositor only; clipboard and file transfer are not built yet.
Interop with the vendor software (BEZEL X) is *not* demonstrated and is *not* a goal of the MVP.

## Where things live
- **This wiki**: knowledge base — what we learned, how to reproduce it, why we decided things.
- **[Issues](https://github.com/EyalRo/OpenATEN/issues)**: all work items and next steps (labels: `mvp`, `research`, `protocol`, `help wanted`).
- **`docs/` in the repo**: the reference docs that ship with the code (protocol, hardware, design, setup, architecture, troubleshooting, `adr/`). The roadmap lives in `docs/design.md`.
- **Source control**: GitHub only (`EyalRo/OpenATEN`); not mirrored elsewhere.

## Pages
- [Hardware and topology](Hardware-and-topology)
- [Protocol cheat sheet](Protocol-cheat-sheet)
- [Experiment log](Experiment-log)
- [Vendor software behaviour](Vendor-software-behaviour)
- [Dev environment and test rigs](Dev-environment-and-test-rigs)
- [Engineering, CI/CD and repository conventions](Engineering-and-CI)
- [Architecture and design decisions](Architecture-and-decisions)
- [Edge hand-off](Edge-hand-off)
- [Open questions](Open-questions)

## Ground rules
- Mark claims as **verified** (seen on hardware) or **inferred** (from interoperability research only).
- No vendor binaries, firmware or Windows images in the repo or wiki.
- Never leave a vendor `TX` pending without a receiver armed on the peer, and never let the kernel `usb-storage` driver re-probe the bridge while testing — both can wedge the switch until power-cycled.
~~~

---

## 4. `Dev-environment-and-test-rigs.md` (edit)

In the section **"Unit tests without hardware"**, replace the paragraph starting `` `tests/fake_transport.py` models ``... with:

~~~markdown
`tests/fake_transport.py` models the firmware's turn rules (strict TX/RX alternation, TX needs an armed RX of equal length, idle RX stalls). The link layer's tests (`tests/test_link.py`) run against it: chunking, ordering, idle stalls, resync, late-starting slave. Run with `pip install -e ".[dev]" && pytest` or `nix develop -c pytest`; the full local check is `ruff check . && mypy && pytest` (see [Engineering and CI](Engineering-and-CI)).
~~~

Nothing else on that page changes.
