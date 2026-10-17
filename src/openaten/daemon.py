"""Background service: owns the input forwarder, reports status, and takes commands over a Unix socket.

Files (in ``$XDG_RUNTIME_DIR/openaten/``, mode 0700):

* ``status.json``  rewritten atomically whenever the status changes and at least every ``HEARTBEAT`` seconds (``ts`` shows liveness)
* ``ctl.sock``     one JSON request per connection, e.g. ``{"cmd": "toggle"}``; the reply is the status as one JSON line

Commands: ``status``, ``on``, ``off``, ``toggle``, ``quit``. Status ``bridge`` is ``present`` (a bridge end is enumerated), ``absent`` or
``error``; the bridge end is only claimed while forwarding is on or pending, so other tools can use it the rest of the time.
"""
from __future__ import annotations

import json
import os
import queue
import socketserver
import threading
import time
from pathlib import Path
from typing import Callable, Optional

HEARTBEAT = 2.0
RELEASE_AFTER = 2.0  # give the bridge end back this long after forwarding turned off


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/openaten-{os.getuid()}"
    return Path(base) / "openaten"


def status_path() -> Path:
    return runtime_dir() / "status.json"


def socket_path() -> Path:
    return runtime_dir() / "ctl.sock"


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        daemon: "Daemon" = self.server.daemon  # type: ignore[attr-defined]
        try:
            req = json.loads(self.rfile.readline(4096) or b"{}")
            cmd = req.get("cmd", "status")
        except (ValueError, AttributeError):
            self.wfile.write(b'{"ok": false, "error": "bad request"}\n')
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
        port: Optional[str] = None,
        port_of: Callable[[object], str] = lambda d: "",
        hotkey: str = "",
        log: Callable[[str], None] = print,
        directory: Optional[Path] = None,
    ):
        self.dir = directory or runtime_dir()
        self.make_runner, self.find_ends, self.open_end = make_runner, find_ends, open_end
        self.port, self.port_of, self.hotkey, self.log = port, port_of, hotkey, log
        self.commands: "queue.Queue[str]" = queue.Queue()
        self.runner = None
        self._end = None
        self._end_idle_since: Optional[float] = None
        self._lock = threading.Lock()
        self._snap: dict = {}
        self._written: Optional[dict] = None
        self._written_at = 0.0
        self.error: Optional[str] = None
        self.stop_flag = threading.Event()
        self._server: Optional[_Server] = None

    # -- bridge end ---------------------------------------------------------------------
    def _candidates(self) -> list:
        ends = sorted(self.find_ends(), key=lambda d: (getattr(d, "bus", 0), self.port_of(d)))
        return [d for d in ends if not self.port or self.port_of(d) == self.port]

    def _send_report(self, report: bytes) -> None:
        """Inject a HID report, claiming the bridge end on first use."""
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
            "error": r.get("error") or self.error,
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
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                break
            if cmd == "quit":
                self.stop_flag.set()
            elif cmd == "toggle":
                self.runner.request(None)
            else:
                self.runner.request(cmd == "on")
        self.runner.step(timeout)
        busy = self.runner.status()["forwarding"] or self.runner.status()["pending"]
        if busy:
            self._end_idle_since = None
        elif self._end is not None:
            now = time.monotonic()
            self._end_idle_since = self._end_idle_since or now
            if now - self._end_idle_since >= RELEASE_AFTER:
                self._close_end()
                self._end_idle_since = None
        self._publish()

    def run(self) -> int:
        self.start_server()
        self._publish(force=True)
        self.log(f"openaten daemon running; status: {self.dir / 'status.json'}, control: {self.dir / 'ctl.sock'}")
        try:
            while not self.stop_flag.is_set():
                self.run_once()
        finally:
            if self.runner is not None:
                self.runner.close()
            self._close_end()
            if self._server is not None:
                self._server.shutdown()
            for name in ("ctl.sock", "status.json"):
                try:
                    (self.dir / name).unlink()
                except FileNotFoundError:
                    pass
        return 0


def request(cmd: str, timeout: float = 2.0, directory: Optional[Path] = None) -> dict:
    """Client side: send one command to the running daemon and return its status reply."""
    import socket

    path = (directory or runtime_dir()) / "ctl.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall(json.dumps({"cmd": cmd}).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data)
