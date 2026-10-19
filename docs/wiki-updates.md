# Wiki updates to sync

Pending changes for the [GitHub wiki](https://github.com/EyalRo/OpenATEN/wiki), written here because the Claude Code web session
cannot push to `OpenATEN.wiki.git` (the git proxy only authorizes the main repository). Delete this file once the wiki is updated.

Prepared against the wiki as of 2026-10-18 (updated after PR #38 merged: clipboard sharing and file transfer). Each section below is one wiki page; the fenced block is the full file content for new pages
and the full replacement for `Home`. Copy it as is (the fences use `~~~` so the content can contain code blocks).

| Wiki page | Action |
|---|---|
| `Engineering-and-CI.md` | **new** |
| `Architecture-and-decisions.md` | **new** |
| `Clipboard-sharing.md` | **new** |
| `File-transfer.md` | **new** |
| `Home.md` | **replace** (adds four page links, updates the status line and "Where things live") |
| `Dev-environment-and-test-rigs.md` | **edit one paragraph** (the "Unit tests without hardware" section) |

Sync with:

```sh
git clone https://github.com/EyalRo/OpenATEN.wiki.git
cd OpenATEN.wiki
# create/replace the files from the sections below, then:
git add -A && git commit -m "Wiki: engineering/CI, architecture, clipboard sharing, file transfer; home links" && git push
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
| `ci.yml` | `test` (ruff, mypy, pytest + coverage on Python 3.10/3.11/3.12), `package` (sdist+wheel, `twine check`), `nix` (`nix flake check`: builds the package, which runs the test suite inside the Nix build, and evaluates the NixOS module `nixosModules.default` with a sample config) |
| `codeql.yml` | CodeQL for Python on push, pull requests and weekly |
| `release.yml` | On a `v*` tag: tag must equal the package version, checks run, build, GitHub release with artifacts |

- All Actions are pinned by **commit SHA** with the version in a trailing comment; Dependabot (monthly, `github-actions` and `pip`) keeps them current. We pinned the latest release within the major version already in use, so inputs did not change.
- CI cancels superseded runs (`concurrency`), so pushing while jobs run cancels them.
- Version has a single source: `pyproject.toml`. `openaten.__version__` reads package metadata; `flake.nix` reads `pyproject.toml`.

## Gotchas learned
- **Dependency review** (`actions/dependency-review-action`) fails with "not supported on this repository" until the *Dependency graph* is enabled in repo settings. It was removed from the first PR for that reason; re-add it after enabling the setting.
- The release workflow has `contents: write`: keep its Actions SHA-pinned.
- `nix flake check` only **evaluates** packages. To make CI build the package (and so run its tests inside the Nix build) the flake lists it under `checks.package`; before that, a missing test dependency (`hypothesis`) went unnoticed. The Nix build uses nixpkgs' newest Python (3.14 at the time), which also exposed a test that depended on Python's default file buffering.
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
- **Link channels**: 1 = hand-off control (JSON), 2 = clipboard, 3 = files (channels 2 and 3 share the framing in `appmsg.py`; see [Clipboard sharing](Clipboard-sharing) and [File transfer](File-transfer)). A peer that does not know a channel ignores it.
- **Mouse coalescing**: the link merges backlogged mouse reports with the same button state (motion summed, split at signed-byte range; keys and button changes never merged). Stat `hid_coalesced`. Verified with fakes only, not yet on hardware.
- **Testing the wire format**: hypothesis tests assert frames round-trip, any single corrupted byte is rejected by the CRC, decoders only raise `ValueError`, and the hand-off state machine always recovers on `release`.

See also: [Edge hand-off](Edge-hand-off), [Open questions](Open-questions), and `docs/troubleshooting.md` (symptom → cause → fix table).
~~~

---

## 3. `Clipboard-sharing.md` (new)

~~~markdown
# Clipboard sharing

Status: **built and unit-tested against a fake backend and fake link; not yet verified on hardware** (checklist: issue #27). Reference: `docs/applications.md` in the repo.

## Use
```sh
openaten daemon --role master      # computer A  (clipboard sharing is on by default; --no-clipboard turns it off)
openaten daemon --role slave       # computer B
```
On by default on each computer; needs the link (`--role`) and, in a graphical session, `wl-clipboard` (Wayland) or `xclip` (X11). Without a tool, sharing is disabled with a WARNING.

## Behaviour
- Shares `text/plain` and `image/png` (text wins when both are offered), up to 8 MiB per clip; larger clips are skipped with a WARNING.
- The daemon polls the local clipboard every 0.5 s and sends changes as `clip {mime, sha}` + data on link channel 2.
- **What was on the clipboard at start-up is never sent** (only later changes), so a password copied before starting the daemon stays local.
- A clip that arrived from the other computer is written to the local clipboard and **not echoed back**.
- The receiver checks the SHA-256 and the MIME type (only the two above) before writing.
- If the link is down when the clipboard changes, the clip is sent once it is up again.
- A failing or hung clipboard tool (2 s timeout) makes the poller back off (up to 30 s) so it can never delay the hotkey.

## Gotchas
- Both computers need `--clipboard` for two-way sharing; a computer without it ignores incoming clips.
- `wl-copy` / `xclip -i` stay alive to serve the selection; that is expected.
- An empty clipboard makes `wl-paste` exit non-zero; that is treated as "nothing to share", not as an error.

## Open
Verify on the rig: both directions, text and PNG, no start-up leak, no echo, behaviour with clipboard managers, large images. See issue #27.
~~~

---

## 4. `File-transfer.md` (new)

~~~markdown
# File transfer

Status: **built and unit-tested with in-memory wiring and a fake link; not yet verified on hardware** (checklist: issue #27). Reference: `docs/applications.md` in the repo.

## Use
```sh
openaten daemon --role slave                  # receiving computer: files go to ~/Downloads (--receive-dir or receive_dir in config.toml to change)
openaten send photo.jpg report.pdf                                 # on the other computer, through its running daemon
openaten ctl status                                                # "transfers": id, name, direction, size, done, state, error
```
`openaten send` takes files or directories (a directory is sent as one transfer per regular file, symlinks skipped, recreated under a fresh `name`/`name (1)` directory on the receiver, never merged) and prints a transfer id (a batch id for a directory); the control-socket command `cancel {id}` cancels it. Without `--receive-dir` the receiver rejects offers with "receiving is not enabled".

## Protocol (link channel 3)
```
sender   -> receiver   offer   {id, name, size}
receiver -> sender     accept  {id}                 or   reject {id, reason}
sender   -> receiver   chunk   {id, n} + data       (4 MiB chunks, at most 4 unacknowledged)
receiver -> sender     ack     {id, n}
sender   -> receiver   done    {id, sha256, size}
receiver -> sender     result  {id, ok, error}
either                 cancel  {id}
```
Framing (all application channels, `appmsg.py`): 4-byte big-endian header length, JSON header (`kind`, `v`), raw payload. Malformed messages and messages from a newer `v` are dropped.

## Receiver safety (why it is built this way)
- Files are accepted into `~/Downloads` by default; nothing is accepted without a running daemon in role mode.
- The offered name is reduced to a plain file name: no directories (both `/` and `\`), no control characters, no leading dots, at most 200 bytes.
- The transfer id becomes part of a temp file name, so ids are restricted to `[0-9A-Za-z_-]{1,64}`; anything else (including non-strings) is ignored. This closed a path-traversal hole found in review.
- Data goes to `.openaten-<id>.part` (mode 0600) and is **hard-linked** to its final name only after the SHA-256 and size match, so it **never overwrites** (`name (1).ext` is used on collisions); filesystems without hard links fall back to a checked rename.
- Limits: 16 GiB per file, 4 simultaneous incoming transfers, free disk space checked up front, 60 s idle timeout.
- A failed, cancelled or timed-out transfer leaves nothing behind; a link failure fails every transfer in flight (nothing is resumed).
- The daemon logs and survives any error raised by a hostile peer message.

## Sender
- Streams the file in chunks with an ack window, so memory use is about 16 MiB whatever the file size. The whole-file SHA-256 is computed on the fly and sent in `done`.
- If the file changes size while it is being sent, the transfer fails.

## Open
Verify on the rig: small and multi-GB files (throughput, flat memory), unplugging mid-transfer (clean failure, no `.part`), name collisions. Possible later work: resume, directories, drag and drop (issue #7).
~~~

---

## 5. `Home.md` (replace)

~~~markdown
# OpenATEN wiki

OpenATEN is open tooling for the **host-to-host bridge** inside ATEN US3342-class USB-C sharing switches (Genesys Logic, USB ID `05e3:0627`).
Goal: cross-screen keyboard/mouse hand-off, clipboard and file transfer between two computers over the switch (no network, VPN-proof), starting with Linux.

**Status (v0.1, experimental):** transport, keyboard/mouse injection and a master-driven polling link (2 ms round trips, ~116 MB/s pushes) are verified on hardware in loopback; the link layer is done and edge hand-off (cross-screen) is built: motion-based and an exact mode using layer-shell border strips. The strip is verified in a headless compositor only; clipboard sharing and file transfer are built and unit-tested against fakes but not yet verified on hardware.
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
- [Clipboard sharing](Clipboard-sharing)
- [File transfer](File-transfer)
- [Edge hand-off](Edge-hand-off)
- [Open questions](Open-questions)

## Ground rules
- Mark claims as **verified** (seen on hardware) or **inferred** (from interoperability research only).
- No vendor binaries, firmware or Windows images in the repo or wiki.
- Never leave a vendor `TX` pending without a receiver armed on the peer, and never let the kernel `usb-storage` driver re-probe the bridge while testing — both can wedge the switch until power-cycled.
~~~

---

## 6. `Dev-environment-and-test-rigs.md` (edit)

In the section **"Unit tests without hardware"**, replace the paragraph starting `` `tests/fake_transport.py` models ``... with:

~~~markdown
`tests/fake_transport.py` models the firmware's turn rules (strict TX/RX alternation, TX needs an armed RX of equal length, idle RX stalls). The link layer's tests (`tests/test_link.py`) run against it: chunking, ordering, idle stalls, resync, late-starting slave. Run with `pip install -e ".[dev]" && pytest` or `nix develop -c pytest`; the full local check is `ruff check . && mypy && pytest` (see [Engineering and CI](Engineering-and-CI)).
~~~

Nothing else on that page changes.
