"""Background service: owns the input forwarder, reports status, and takes commands over a Unix socket.

Files (in ``$XDG_RUNTIME_DIR/openaten/``, mode 0700):

* ``status.json``  rewritten atomically whenever the status changes and at least every ``HEARTBEAT`` seconds (``ts`` shows liveness)
* ``ctl.sock``     one JSON request per connection, e.g. ``{"cmd": "toggle"}``; the reply is the status as one JSON line

Commands: ``status``, ``on``, ``off``, ``toggle``, ``quit``, and ``edge`` (``border``, ``row``, ``enter``: reported by ``openaten strip``). Status ``bridge`` is ``present`` (a bridge end is enumerated), ``absent`` or
``error``; the bridge end is only claimed while forwarding is on or pending, so other tools can use it the rest of the time.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import socket
import socketserver
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import appmsg, control

log = logging.getLogger(__name__)

HEARTBEAT = 2.0
LINK_RETRY_MIN = 2.0  # first retry after a link failure; doubles per consecutive failure up to LINK_RETRY_MAX
LINK_RETRY_MAX = 30.0
CLIENT_TIMEOUT = 5.0  # seconds a control-socket client may take to send its request
RELEASE_AFTER = 2.0  # give the bridge end back this long after forwarding turned off


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/openaten-{os.getuid()}"
    return Path(base) / "openaten"


def status_path() -> Path:
    return runtime_dir() / "status.json"


def socket_path() -> Path:
    return runtime_dir() / "ctl.sock"


def _peer_uid(conn: socket.socket) -> int | None:
    """uid of the process on the other end of a Unix socket (Linux SO_PEERCRED); None where unavailable."""
    if not hasattr(socket, "SO_PEERCRED"):
        return None
    _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid


class _Handler(socketserver.StreamRequestHandler):
    timeout = CLIENT_TIMEOUT  # a client that connects and stays silent must not pin a thread

    def handle(self) -> None:
        daemon: Daemon = self.server.daemon  # type: ignore[attr-defined]
        uid = _peer_uid(self.request)
        if uid is not None and uid not in (0, os.getuid()):  # defence in depth: the socket is already 0600 in a 0700 directory
            log.warning("rejected control connection from uid %d", uid)
            return
        try:
            req = json.loads(self.rfile.readline(4096) or b"{}")
            cmd = req.get("cmd", "status")
        except (ValueError, AttributeError, TimeoutError):
            self.wfile.write(b'{"ok": false, "error": "bad request"}\n')
            return
        if cmd == "edge":
            try:
                daemon.commands.put(("edge", str(req["border"]), float(req["row"]), bool(req.get("enter", True))))
            except (KeyError, TypeError, ValueError):
                self.wfile.write(b'{"ok": false, "error": "edge needs border and row"}\n')
                return
            self.wfile.write(json.dumps(dict(daemon.snapshot(), ok=True)).encode() + b"\n")
            return
        if cmd in ("send", "cancel"):
            self.wfile.write(json.dumps(daemon.transfer_request(cmd, req)).encode() + b"\n")
            return
        if cmd not in ("status", "on", "off", "toggle", "quit"):
            self.wfile.write(json.dumps({"ok": False, "error": f"unknown command {cmd!r}"}).encode() + b"\n")
            return
        if cmd != "status":
            daemon.commands.put(cmd)
        reply = dict(daemon.snapshot(), ok=True)
        self.wfile.write(json.dumps(reply).encode() + b"\n")


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Daemon:
    """``make_runner(send_report)`` builds the input runner; ``find_ends()``/``open_end(dev)`` locate and claim bridge ends.

    All three are injectable so the daemon can be tested without hardware.
    """

    def __init__(
        self,
        make_runner: Callable[[Callable[[bytes], None]], object],
        find_ends: Callable[[], list],
        open_end: Callable[[object], object],
        port: str | None = None,
        port_of: Callable[[object], str] = lambda d: "",
        hotkey: str = "",
        directory: Path | None = None,
        role: str | None = None,
        make_link: Callable[..., object] | None = None,
        hotplug: Callable[[Callable[[str], None]], Any] | None = None,
        make_clipboard: Callable[[Callable[[bytes], bool]], Any] | None = None,
        make_transfers: Callable[[Callable[[bytes], bool]], Any] | None = None,
    ):
        self.dir = directory or runtime_dir()
        self.make_runner, self.find_ends, self.open_end = make_runner, find_ends, open_end
        self.port, self.port_of, self.hotkey = port, port_of, hotkey
        self.commands: queue.Queue = queue.Queue()
        self.role, self.make_link = role, make_link
        self.hotplug = hotplug  # starts a bridge add/remove event source: hotplug(callback) -> handle with stop() (or None)
        self._hotplug_handle: Any = None
        self.link: Any = None  # persistent link session (role mode): HID reports and hand-off messages ride on it
        self._link_error: str | None = None
        self._link_retry_at = 0.0
        self._link_retry_delay = LINK_RETRY_MIN
        self.handoff = control.Handoff(self._push, self._pull, self._send_control)
        # Application protocols on the link (opt-in; None when disabled). Each is handed a send function that returns False while the link is down.
        self.clipboard: Any = make_clipboard(lambda data: self._send_app(appmsg.CH_CLIPBOARD, data)) if make_clipboard else None
        self.transfers: Any = make_transfers(lambda data: self._send_app(appmsg.CH_FILES, data)) if make_transfers else None
        self._pending: tuple[str, float] | None = None  # border/row of a push waiting for forwarding to start
        self._was_forwarding = False
        self.runner: Any = None
        self._end: Any = None
        self._end_idle_since: float | None = None
        self._lock = threading.Lock()
        self._snap: dict = {}
        self._written: dict | None = None
        self._written_at = 0.0
        self.error: str | None = None
        self.stop_flag = threading.Event()
        self._server: _Server | None = None

    # -- bridge end ---------------------------------------------------------------------
    def _candidates(self) -> list:
        ends = sorted(self.find_ends(), key=lambda d: (getattr(d, "bus", 0), self.port_of(d)))
        return [d for d in ends if not self.port or self.port_of(d) == self.port]

    def _send_report(self, report: bytes) -> None:
        """Inject a HID report (through the link in role mode), claiming the bridge end on first use."""
        if self.link is not None:
            self.link.inject_hid(report)
            return
        if self._end is None:
            cands = self._candidates()
            if not cands:
                raise RuntimeError("no bridge end connected")
            self._end = self.open_end(cands[0])
        try:
            self._end.hid(report)
        except Exception:
            self._close_end()  # re-open on the next report (the switch may have re-enumerated)
            raise

    # -- link and hand-off ----------------------------------------------------------------
    def _push(self, border: str, row: float) -> None:
        self._pending = (border, row)
        if hasattr(self.runner, "border_pushed"):
            self.runner.border_pushed(border, row)

    def _pull(self) -> None:
        self.runner.request(False)

    def _send_control(self, data: bytes) -> None:
        if self.link is not None:
            self.link.send_message(control.CH_CONTROL, data)

    def _send_app(self, channel: int, data: bytes) -> bool:
        link = self.link
        if link is None or self._link_error is not None:
            return False
        link.send_message(channel, data)
        return True

    def _on_link_message(self, channel: int, msg_id: int, data: bytes) -> None:
        """Link thread: hand everything to the main loop, where all state is touched."""
        if channel == control.CH_CONTROL:
            msg = control.decode(data)
            if msg is not None:
                self.commands.put(("peer", msg))
        elif channel in (appmsg.CH_CLIPBOARD, appmsg.CH_FILES):
            self.commands.put(("app", channel, data))

    def _on_app_message(self, channel: int, data: bytes) -> None:
        parsed = appmsg.unpack(data)
        if parsed is None:
            log.debug("dropped a malformed message on channel %d", channel)
            return
        target = self.clipboard if channel == appmsg.CH_CLIPBOARD else self.transfers
        if target is None:
            if channel == appmsg.CH_FILES and parsed[0].get("kind") == "offer" and isinstance(parsed[0].get("id"), str):
                self._send_app(channel, appmsg.pack("reject", id=parsed[0]["id"], reason="file transfer is not enabled on the other computer"))
            return
        try:
            target.on_message(*parsed)
        except Exception:  # the peer's messages are untrusted: whatever they do, the daemon (and the hotkey) must keep running
            log.exception("error handling a message on channel %d", channel)

    def transfer_request(self, cmd: str, req: dict) -> dict:
        """Control-socket thread: run a send/cancel on the main loop and return its outcome."""
        if self.transfers is None:
            return {"ok": False, "error": "file transfer is not available (the daemon needs --role)"}
        reply: queue.Queue = queue.Queue(1)
        self.commands.put(("transfer", cmd, req, reply))
        try:
            return reply.get(timeout=3)
        except queue.Empty:
            return {"ok": False, "error": "the daemon did not answer in time"}

    def _run_transfer_command(self, cmd: str, req: dict, reply: queue.Queue) -> None:
        try:
            if cmd == "send":
                path = req.get("path")
                if not isinstance(path, str) or not os.path.isabs(path):
                    raise ValueError("send needs an absolute path")
                reply.put({"ok": True, "id": self.transfers.send_file(path)})
            else:
                tid = req.get("id")
                reply.put({"ok": bool(isinstance(tid, str) and self.transfers.cancel(tid)), "error": None})
        except ValueError as e:
            reply.put({"ok": False, "error": str(e)})

    def _on_link_error(self, e: Exception) -> None:
        self._link_error = f"link: {e}"
        log.error("link session ended: %s", e, exc_info=e)

    def _ensure_link(self) -> None:
        """Role mode: keep the bridge end claimed and a link session running (restarted after errors)."""
        if self.role is None or self.make_link is None:
            return
        if self.link is not None and self._link_error is None:
            return
        now = time.monotonic()
        if now < self._link_retry_at:
            return
        self._link_retry_at = now + self._link_retry_delay
        self._link_retry_delay = min(self._link_retry_delay * 2, LINK_RETRY_MAX)
        self._drop_link()
        try:
            cands = self._candidates()
            if not cands:
                raise RuntimeError("no bridge end connected")
            self._end = self.open_end(cands[0])
            self._link_error = None
            self.link = self.make_link(self._end, self.role == "master", self._on_link_message, self._on_link_error)
            self.link.start()
            self._link_retry_delay = LINK_RETRY_MIN
            log.info("link up as %s", self.role)
        except Exception as e:
            self._drop_link()
            msg = f"link: {e}"
            if msg != self._link_error:  # retried every few seconds: report each distinct failure once
                log.warning("link not up (retrying): %s", e)
            self._link_error = msg

    def _on_hotplug(self, action: str) -> None:
        """The bridge appeared or disappeared: reconnect now instead of waiting out the retry backoff."""
        log.info("bridge %s", action)
        self._link_retry_at, self._link_retry_delay = 0.0, LINK_RETRY_MIN
        if action == "remove" and self.link is not None and self._link_error is None:
            self._link_error = "link: bridge removed"  # the session is rebuilt on the next loop (failing until the add event)
        if action == "add":
            self._ensure_link()

    def _drop_link(self) -> None:
        link, self.link = self.link, None
        if self.transfers is not None:
            self.transfers.link_lost()  # nothing in flight survives a new link session
        if link is not None:
            try:
                link.stop(timeout=3.0)
            except Exception:
                pass
        self._close_end()

    def _close_end(self) -> None:
        end, self._end = self._end, None
        if end is not None:
            try:
                end.close()
            except Exception:
                pass

    # -- status -------------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snap)

    def _build_status(self) -> dict:
        r = self.runner.status() if self.runner is not None else {"forwarding": False, "pending": False, "devices": [], "hotkey": self.hotkey, "edges": "off", "error": None}
        try:
            present = bool(self._candidates()) if self._end is None else True
            bridge = "present" if present else "absent"
        except Exception as e:
            bridge, r["error"] = "error", r.get("error") or str(e)
        return {
            "version": 1,
            "pid": os.getpid(),
            "bridge": bridge,
            "end": self.port_of(self._end.dev) if self._end is not None and hasattr(self._end, "dev") else None,
            "forwarding": bool(r["forwarding"]),
            "pending": bool(r["pending"]),
            "hotkey": r.get("hotkey", self.hotkey),
            "edges": r.get("edges", "off"),
            "devices": r["devices"],
            "role": self.role,
            "receiving": self.handoff.receiving,
            "clipboard": self.clipboard is not None,
            "transfers": self.transfers.status() if self.transfers is not None else [],
            "error": r.get("error") or self.error or self._link_error,
        }

    def _publish(self, force: bool = False) -> None:
        st = self._build_status()
        with self._lock:
            self._snap = dict(st, ts=time.time())
        body = {k: v for k, v in st.items()}
        if force or body != self._written or time.monotonic() - self._written_at >= HEARTBEAT:
            tmp = self.dir / "status.json.tmp"
            tmp.write_text(json.dumps(dict(body, ts=time.time())))
            os.replace(tmp, self.dir / "status.json")
            self._written, self._written_at = body, time.monotonic()

    # -- main loop ----------------------------------------------------------------------
    def start_server(self) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        sock = self.dir / "ctl.sock"
        if sock.exists():
            sock.unlink()
        self._server = _Server(str(sock), _Handler)
        self._server.daemon = self  # type: ignore[attr-defined]
        os.chmod(sock, 0o600)
        threading.Thread(target=self._server.serve_forever, name="openaten-ctl", daemon=True).start()

    def run_once(self, timeout: float = 0.1) -> None:
        """One loop iteration (public for tests)."""
        if self.runner is None:
            self.runner = self.make_runner(self._send_report)
            if not self.runner.open():
                self.error = "no readable keyboard/mouse devices (is the user in the 'input' group?)"
        self._ensure_link()
        if self._link_error is not None and self.transfers is not None:
            self.transfers.link_lost()  # fail in-flight transfers now rather than after their idle timeout
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                break
            if isinstance(cmd, tuple):
                if cmd[0] == "edge":
                    self.handoff.edge(cmd[1], cmd[2], cmd[3])
                elif cmd[0] == "peer":
                    self.handoff.message(cmd[1])
                elif cmd[0] == "hotplug":
                    self._on_hotplug(cmd[1])
                elif cmd[0] == "app":
                    self._on_app_message(cmd[1], cmd[2])
                elif cmd[0] == "transfer":
                    self._run_transfer_command(cmd[1], cmd[2], cmd[3])
            elif cmd == "quit":
                self.stop_flag.set()
            elif cmd == "toggle":
                self.runner.request(None)
            else:
                self.runner.request(cmd == "on")
        self.runner.step(timeout)
        if self.clipboard is not None and self.link is not None:
            self.clipboard.poll()
        if self.transfers is not None:
            self.transfers.pump()
        forwarding = self.runner.status()["forwarding"]
        if forwarding != self._was_forwarding:
            self._was_forwarding = forwarding
            if forwarding:
                self.handoff.started(*(self._pending or (None, None)))
            else:
                self.handoff.stopped()
            self._pending = None
        busy = forwarding or self.runner.status()["pending"]
        if busy:
            self._end_idle_since = None
        elif self._end is not None and self.link is None:
            now = time.monotonic()
            self._end_idle_since = self._end_idle_since or now
            if now - self._end_idle_since >= RELEASE_AFTER:
                self._close_end()
                self._end_idle_since = None
        self._publish()

    def run(self) -> int:
        self.start_server()
        if self.hotplug is not None:
            self._hotplug_handle = self.hotplug(lambda action: self.commands.put(("hotplug", action)))
        self._publish(force=True)
        log.info("openaten daemon running; status: %s, control: %s", self.dir / "status.json", self.dir / "ctl.sock")
        try:
            while not self.stop_flag.is_set():
                self.run_once()
        finally:
            if self._hotplug_handle is not None:
                self._hotplug_handle.stop()
            if self.runner is not None:
                self.runner.close()
            self._drop_link()
            self._close_end()
            if self._server is not None:
                self._server.shutdown()
            for name in ("ctl.sock", "status.json"):
                try:
                    (self.dir / name).unlink()
                except FileNotFoundError:
                    pass
        return 0


def request(cmd: str, timeout: float = 2.0, directory: Path | None = None, **fields) -> dict:
    """Client side: send one command (with optional fields) to the running daemon and return its status reply."""
    import socket

    path = (directory or runtime_dir()) / "ctl.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(json.dumps({"cmd": cmd, **fields}).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data)
