import json
import threading
import time

import pytest

from openaten import daemon as dm


class FakeRunner:
    def __init__(self, send_report):
        self.send_report, self.opened, self.on, self.pending, self.closed = send_report, False, False, False, False
        self.requests = []

    def open(self):
        self.opened = True
        return True

    def request(self, on=None):
        self.requests.append(on)
        self.on = (not self.on) if on is None else on

    def status(self):
        return {"forwarding": self.on, "pending": self.pending, "devices": ["kbd"], "hotkey": "ctrl+alt+k", "error": None}

    def step(self, timeout):
        time.sleep(0)

    def close(self):
        self.closed = True


class FakeEnd:
    def __init__(self, dev):
        self.dev, self.reports, self.closed = dev, [], False

    def hid(self, r):
        self.reports.append(r)

    def close(self):
        self.closed = True


class Dev:
    def __init__(self, port):
        self.port, self.bus = port, 2


def make(tmp_path, ends=None, **kw):
    ends = [Dev("1")] if ends is None else ends
    runners = []

    def mk(send):
        r = FakeRunner(send)
        runners.append(r)
        return r

    d = dm.Daemon(mk, lambda: list(ends), FakeEnd, port_of=lambda d: d.port, hotkey="ctrl+alt+k", directory=tmp_path, **kw)
    return d, runners, ends


def test_status_file_reports_bridge_and_forwarding(tmp_path):
    d, runners, ends = make(tmp_path)
    d.dir.mkdir(exist_ok=True)
    d.run_once()
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["bridge"] == "present" and st["forwarding"] is False and st["devices"] == ["kbd"] and st["hotkey"] == "ctrl+alt+k"
    ends.clear()
    d.run_once()
    assert d.snapshot()["bridge"] == "absent"


def test_commands_drive_the_runner(tmp_path):
    d, runners, _ = make(tmp_path)
    d.dir.mkdir(exist_ok=True)
    d.commands.put("on")
    d.run_once()
    assert runners[0].requests == [True] and d.snapshot()["forwarding"] is True
    d.commands.put("toggle")
    d.run_once()
    assert runners[0].requests == [True, None] and d.snapshot()["forwarding"] is False


def test_bridge_end_is_claimed_lazily_and_released_when_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RELEASE_AFTER", 0.0)
    d, runners, _ = make(tmp_path)
    d.dir.mkdir(exist_ok=True)
    d.run_once()
    assert d._end is None  # nothing claimed while idle
    d._send_report(b"x")  # first injection claims the end
    assert d._end is not None and d._end.reports == [b"x"]
    runners[0].on = True
    d.run_once()
    assert d._end is not None  # busy: keep it
    end = d._end
    runners[0].on = False
    d.run_once()  # starts the idle timer
    d.run_once()  # RELEASE_AFTER is 0: released now
    assert d._end is None and end.closed


def test_injection_without_a_bridge_raises_so_the_runner_can_give_control_back(tmp_path):
    d, _, ends = make(tmp_path, ends=[])
    with pytest.raises(RuntimeError):
        d._send_report(b"x")


def test_control_socket_roundtrip(tmp_path):
    d, runners, _ = make(tmp_path)
    d.start_server()
    t = threading.Thread(target=lambda: [d.run_once(0.01) for _ in iter(lambda: d.stop_flag.is_set(), True)])
    t.start()
    try:
        reply = dm.request("on", directory=tmp_path)
        assert reply["ok"] is True and reply["bridge"] == "present"
        deadline = time.time() + 3
        while d.snapshot().get("forwarding") is not True and time.time() < deadline:
            time.sleep(0.02)
        assert dm.request("status", directory=tmp_path)["forwarding"] is True
        assert dm.request("bogus", directory=tmp_path)["ok"] is False
    finally:
        dm.request("quit", directory=tmp_path)
        t.join(5)
        d._server.shutdown()
    assert not t.is_alive()


def test_quit_stops_the_loop(tmp_path):
    d, runners, _ = make(tmp_path)
    d.dir.mkdir(exist_ok=True)
    d.commands.put("quit")
    d.run_once()
    assert d.stop_flag.is_set()


def test_runtime_dir_prefers_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert dm.runtime_dir() == tmp_path / "openaten"


class FakeLink:
    def __init__(self, end, master, on_message, on_error):
        self.end, self.master, self.on_message, self.on_error = end, master, on_message, on_error
        self.hid, self.messages, self.raw, self.started, self.stopped = [], [], [], False, False

    def start(self):
        self.started = True

    def stop(self, timeout=0):
        self.stopped = True

    def inject_hid(self, report):
        self.hid.append(report)

    def send_message(self, channel, data):
        self.raw.append((channel, data))
        if channel == 1:  # hand-off control messages are plain JSON
            self.messages.append((channel, json.loads(data)))


def make_linked(tmp_path, role="master", **kw):
    links = []

    def mk_link(*a):
        links.append(FakeLink(*a))
        return links[-1]

    d, runners, _ = make(tmp_path, role=role, make_link=mk_link, **kw)
    d.dir.mkdir(exist_ok=True)
    return d, runners, links


def test_role_mode_keeps_the_end_and_injects_through_the_link(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RELEASE_AFTER", 0.0)
    d, runners, links = make_linked(tmp_path)
    d.run_once()
    d.run_once()
    assert links[0].started and links[0].master and d._end is not None  # held even when idle
    d._send_report(b"x")
    assert links[0].hid == [b"x"]
    assert d.snapshot()["role"] == "master"


def test_forwarding_start_and_stop_are_announced_to_the_peer(tmp_path):
    d, runners, links = make_linked(tmp_path)
    d.run_once()
    d.commands.put("on")
    d.run_once()
    d.commands.put("off")
    d.run_once()
    assert [m for _, m in links[0].messages] == [{"v": 1, "kind": "take-control", "border": None, "row": None}, {"v": 1, "kind": "release"}]


def test_peer_take_control_then_strip_enter_after_leaving_sends_give_back(tmp_path):
    d, runners, links = make_linked(tmp_path)
    d.run_once()
    d.handoff.settle = 0.0
    links[0].on_message(1, 1, json.dumps({"kind": "take-control", "border": "k", "row": 1.0}).encode())
    d.commands.put(("edge", "k", 5.0, False))
    d.commands.put(("edge", "k", 6.0, True))
    d.run_once()
    assert d.snapshot()["receiving"] is False
    assert links[0].messages[-1] == (1, {"v": 1, "kind": "give-back", "border": "k", "row": 6.0})


def test_peer_give_back_stops_forwarding(tmp_path):
    d, runners, links = make_linked(tmp_path)
    d.run_once()
    d.commands.put("on")
    d.run_once()
    links[0].on_message(1, 2, json.dumps({"kind": "give-back", "border": "k", "row": 1.0}).encode())
    d.run_once()
    assert runners[0].requests[-1] is False


def test_edge_command_over_the_socket(tmp_path):
    d, runners, _ = make(tmp_path)
    d.start_server()
    try:
        assert dm.request("edge", directory=tmp_path, border="k", row=3.0)["ok"] is True
        assert dm.request("edge", directory=tmp_path, border="k")["ok"] is False
        assert d.commands.get(timeout=2) == ("edge", "k", 3.0, True)
    finally:
        d._server.shutdown()


def test_link_retry_backs_off_while_the_bridge_is_missing_and_resets_once_up(tmp_path):
    ends: list = []
    d, _, _ = make_linked(tmp_path)
    d.find_ends = lambda: list(ends)
    delays = []
    for _ in range(8):
        d._link_retry_at = 0.0  # make the next attempt due now
        d._ensure_link()
        delays.append(d._link_retry_delay)
    assert delays == [4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 30.0, 30.0]
    ends.append(Dev("1"))
    d._link_retry_at = 0.0
    d._ensure_link()
    assert d.link is not None and d._link_retry_delay == dm.LINK_RETRY_MIN


def test_silent_control_client_times_out(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(dm._Handler, "timeout", 0.2)
    d, _, _ = make(tmp_path)
    d.start_server()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(str(d.dir / "ctl.sock"))
            assert b"bad request" in s.recv(200)  # the handler gave up on the silent client instead of waiting forever
    finally:
        d._server.shutdown()


def test_hotplug_add_skips_the_retry_backoff_and_reconnects(tmp_path):
    ends: list = []
    sources: list = []

    class Source:
        def __init__(self, callback):
            self.callback, self.stopped = callback, False
            sources.append(self)

        def stop(self):
            self.stopped = True

    d, _, links = make_linked(tmp_path)
    d.find_ends = lambda: list(ends)
    d.hotplug = Source
    d.start_server()
    d._hotplug_handle = d.hotplug(lambda action: d.commands.put(("hotplug", action)))
    try:
        d._ensure_link()
        d._ensure_link()  # still inside the backoff window: no second attempt
        assert d.link is None and d._link_retry_delay > dm.LINK_RETRY_MIN
        ends.append(Dev("1"))
        sources[0].callback("add")
        d.run_once(0.01)
        assert d.link is not None and links and links[0].started  # reconnected at once
        sources[0].callback("remove")
        d.run_once(0.01)  # processes the event (marks the session dead) ...
        d.run_once(0.01)  # ... and the next loop tears it down
        assert links[0].stopped
        assert len(links) >= 2 and d.link is links[-1]  # and rebuilt (the fake bridge is still listed)
    finally:
        d._server.shutdown()


def app_kinds(link, channel):
    from openaten import appmsg

    return [appmsg.unpack(d)[0]["kind"] for c, d in link.raw if c == channel]


def test_clipboard_changes_are_sent_and_received_clips_are_written(tmp_path):
    from openaten import appmsg
    from openaten.clipboard import TEXT, Clip, ClipboardSync

    class Backend:
        clip, written = None, []

        def read(self):
            return self.clip

        def write(self, clip):
            self.written.append(clip)
            self.clip = clip

    backend = Backend()
    d, _, links = make_linked(tmp_path, make_clipboard=lambda send: ClipboardSync(backend, send, interval=0))
    d.dir.mkdir(exist_ok=True)
    d.run_once()  # link comes up; baseline taken
    backend.clip = Clip(TEXT, b"copied here")
    d.run_once()
    assert app_kinds(links[0], appmsg.CH_CLIPBOARD) == ["clip"]
    clip = Clip(TEXT, b"copied there")
    links[0].on_message(appmsg.CH_CLIPBOARD, 7, appmsg.pack("clip", clip.data, mime=TEXT, sha=clip.digest))
    d.run_once()
    assert backend.written == [clip]
    assert app_kinds(links[0], appmsg.CH_CLIPBOARD) == ["clip"]  # not echoed back
    assert d.snapshot()["clipboard"] is True


def test_file_send_runs_through_the_daemon_and_unwanted_offers_are_rejected(tmp_path):
    from openaten import appmsg
    from openaten.transfer import TransferManager

    f = tmp_path / "a.txt"
    f.write_text("hello")
    d, _, links = make_linked(tmp_path, make_transfers=lambda send: TransferManager(send, None))
    d.dir.mkdir(exist_ok=True)
    d.run_once()
    import threading

    result = {}
    t = threading.Thread(target=lambda: result.update(d.transfer_request("send", {"path": str(f)})))
    t.start()
    for _ in range(50):
        d.run_once(0.01)
        if not t.is_alive():
            break
    t.join(5)
    assert result["ok"] is True and "id" in result
    assert app_kinds(links[0], appmsg.CH_FILES) == ["offer"]
    d.run_once()
    assert d.snapshot()["transfers"][0]["state"] == "offered"
    # the peer refuses (it is not receiving): the transfer ends as rejected, with the reason in the status
    offer = appmsg.unpack([data for c, data in links[0].raw if c == appmsg.CH_FILES][0])[0]
    links[0].on_message(appmsg.CH_FILES, 8, appmsg.pack("reject", id=offer["id"], reason="receiving is not enabled"))
    d.run_once()
    assert d.snapshot()["transfers"][0]["state"] == "rejected"
    # an incoming offer when receiving is off is declined
    links[0].on_message(appmsg.CH_FILES, 9, appmsg.pack("offer", id="zz", name="x", size=1))
    d.run_once()
    assert app_kinds(links[0], appmsg.CH_FILES)[-1] == "reject"


def test_transfer_requests_without_a_manager_or_with_bad_paths_fail_cleanly(tmp_path):
    d, _, _ = make(tmp_path)
    assert d.transfer_request("send", {"path": "/x"})["ok"] is False
    from openaten.transfer import TransferManager

    d2, _, _ = make_linked(tmp_path, make_transfers=lambda send: TransferManager(send, None))
    import queue

    q: queue.Queue = queue.Queue(1)
    d2._run_transfer_command("send", {"path": "relative.txt"}, q)
    assert q.get_nowait() == {"ok": False, "error": "send needs an absolute path"}
    d2._run_transfer_command("send", {"path": str(tmp_path / "missing")}, q)
    assert q.get_nowait()["ok"] is False
    d2._run_transfer_command("cancel", {"id": "nope"}, q)
    assert q.get_nowait()["ok"] is False


def test_losing_the_link_fails_transfers_in_flight(tmp_path):
    from openaten.transfer import TransferManager

    f = tmp_path / "a.txt"
    f.write_text("hello")
    d, _, links = make_linked(tmp_path, make_transfers=lambda send: TransferManager(send, None))
    d.dir.mkdir(exist_ok=True)
    d.run_once()
    d.transfers.send_file(f)
    links[0].on_error(RuntimeError("usb gone"))
    d.run_once()  # the link is rebuilt; the old session's transfers cannot continue
    assert d.snapshot()["transfers"][0]["state"] == "failed"


def test_send_over_the_control_socket_end_to_end(tmp_path):
    from openaten import appmsg
    from openaten.transfer import TransferManager

    f = tmp_path / "doc.txt"
    f.write_text("payload")
    d, _, links = make_linked(tmp_path, make_transfers=lambda send: TransferManager(send, None))
    d.start_server()
    t = threading.Thread(target=lambda: [d.run_once(0.01) for _ in iter(lambda: d.stop_flag.is_set(), True)])
    t.start()
    try:
        deadline = time.time() + 3
        while not links and time.time() < deadline:
            time.sleep(0.02)
        reply = dm.request("send", directory=tmp_path, path=str(f))
        assert reply["ok"] is True
        assert dm.request("send", directory=tmp_path, path="relative")["ok"] is False
        assert app_kinds(links[0], appmsg.CH_FILES) == ["offer"]
        assert dm.request("cancel", directory=tmp_path, id=reply["id"])["ok"] is True
        assert dm.request("status", directory=tmp_path)["transfers"][-1]["state"] == "cancelled"
    finally:
        dm.request("quit", directory=tmp_path)
        t.join(5)
        d._server.shutdown()
    assert not t.is_alive()


def test_cli_send_without_a_daemon_reports_it(tmp_path, monkeypatch, capsys):
    from openaten import cli

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert cli.main(["send", "whatever.txt"]) == 1
    assert "daemon not running" in capsys.readouterr().out


def test_a_hostile_file_message_cannot_crash_the_daemon(tmp_path):
    from openaten import appmsg
    from openaten.transfer import TransferManager

    d, _, links = make_linked(tmp_path, make_transfers=lambda send: TransferManager(send, tmp_path / "in"))
    d.dir.mkdir(exist_ok=True)
    d.run_once()
    for header in ({"kind": "chunk", "id": ["x"], "n": 0}, {"kind": "offer", "id": {"a": 1}, "name": 5, "size": "big"}, {"kind": "ack", "id": None}):
        links[0].on_message(appmsg.CH_FILES, 5, appmsg.pack(header.pop("kind"), **header))
    links[0].on_message(appmsg.CH_FILES, 6, b"\xff\xff\xff\xffgarbage")
    d.run_once()
    d.run_once()
    assert not d.stop_flag.is_set()
