# Application protocols: clipboard and files

Both ride on the reliable link (see [design.md](design.md)) as typed messages on their own channels and are **on by default**: clipboard sharing is enabled and files are received into `~/Downloads` (`--no-clipboard` turns the clipboard off); nothing is shared
until the daemon runs in role mode. They need the daemon in role mode (`openaten daemon --role master|slave` on both computers).

| Channel | Used for | Module |
|---|---|---|
| 1 | hand-off control (JSON) | `control.py` |
| 2 | clipboard | `clipboard.py` |
| 3 | files | `transfer.py` |

Channels 2 and 3 share one framing (`appmsg.py`): a 4-byte big-endian header length, a UTF-8 JSON header, then the raw payload. The header always has
`kind` and `v` (the protocol version, currently 1). Messages that are malformed, have no string `kind`, or have a newer `v` are dropped.

## Clipboard (`openaten daemon --clipboard`)

- Needs `wl-clipboard` (Wayland) or `xclip` (X11) in a graphical session; without them sharing is disabled with a WARNING.
- Shares `text/plain` and `image/png`, up to 8 MiB per clip (larger clips are skipped with a WARNING). Text wins when both are offered.
- The daemon polls the local clipboard every 0.5 s and sends changes. **What was on the clipboard at start-up is never sent**, and a clip that
  arrived from the other computer is not echoed back. If the link is down, a changed clip is sent once it is up again.
- Message: `clip {mime, sha}` + data; the receiver checks the SHA-256 and the MIME type before writing it to its clipboard.
- Both computers must run with `--clipboard` to share in both directions; a computer without it ignores incoming clips.

## Files and directories (`openaten send PATH...`, `openaten daemon --receive-dir DIR`)

```
sender   -> receiver   offer   {id, name, size}
receiver -> sender     accept  {id}                 or   reject {id, reason}
sender   -> receiver   chunk   {id, n} + data       (4 MiB chunks, at most 4 unacknowledged)
receiver -> sender     ack     {id, n}
sender   -> receiver   done    {id, sha256, size}
receiver -> sender     result  {id, ok, error}
either                 cancel  {id}
```

- The receiving computer must opt in with `--receive-dir DIR`; otherwise offers are rejected ("receiving is not enabled").
- Receiver safety: the name is reduced to a plain file name (no directories, no control characters, no leading dots); files are written to
  `.openaten-<id>.part` (mode 0600) and only linked to their final name after the SHA-256 and size match, **never overwriting** (`name (1).ext` is used instead);
  limits: 16 GiB per file, 4 simultaneous incoming transfers, free disk space is checked, transfers idle for 60 s fail. A failed or cancelled transfer leaves nothing behind.
- The sender streams the file in chunks (memory use is at most about 16 MiB), so file size is not limited by RAM. If the file changes size while it is being sent the transfer fails.
- `openaten ctl status` (and `status.json`) list `transfers` with `id, name, direction, size, done, state, error`; `openaten send` prints the transfer id.
  A link failure fails every transfer in flight; nothing is resumed.
- Transfers can be cancelled with the control socket command `cancel {id}`.

### Directories

`openaten send DIR` sends every regular file below `DIR` as its own transfer. Symlinks (also symlinked directories) and special files are skipped, empty directories
are not sent, and a directory may hold at most 10 000 files (send an archive instead). The offers carry `batch` (one id for the whole directory), `top` (the directory's name)
and `rel` (the file's path components below it). The receiver recreates the tree under `top` inside `--receive-dir` (`top (1)`, `top (2)` ... if it exists: it never merges into
or overwrites an existing directory); dotfiles keep their names, `.`/`..`/separators in a component reject the offer, and paths deeper than 32 components are refused.
The sender keeps two files of a directory in flight at a time and stops the rest if the receiver rejects one (for example when receiving is not enabled). `openaten ctl status`
shows one summary entry per directory (`files`, `files_done`, `size`, `done`) next to the individual files. A failed or cancelled directory leaves the files that already arrived in place.
A peer running an older version treats each file as a plain file named after its last component.
