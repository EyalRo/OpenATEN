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

    d = dm.Daemon(mk, lambda: list(ends), FakeEnd, port_of=lambda d: d.port, hotkey="ctrl+alt+k", log=lambda m: None, directory=tmp_path, **kw)
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
        self.hid, self.messages, self.started, self.stopped = [], [], False, False

    def start(self):
        self.started = True

    def stop(self, timeout=0):
        self.stopped = True

    def inject_hid(self, report):
        self.hid.append(report)

    def send_message(self, channel, data):
        self.messages.append((channel, json.loads(data)))


def make_linked(tmp_path, role="master"):
    links = []

    def mk_link(*a):
        links.append(FakeLink(*a))
        return links[-1]

    d, runners, _ = make(tmp_path, role=role, make_link=mk_link)
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
    assert [m for _, m in links[0].messages] == [{"kind": "take-control", "border": None, "row": None}, {"kind": "release"}]


def test_peer_take_control_then_strip_enter_after_leaving_sends_give_back(tmp_path):
    d, runners, links = make_linked(tmp_path)
    d.run_once()
    d.handoff.settle = 0.0
    links[0].on_message(1, 1, json.dumps({"kind": "take-control", "border": "k", "row": 1.0}).encode())
    d.commands.put(("edge", "k", 5.0, False))
    d.commands.put(("edge", "k", 6.0, True))
    d.run_once()
    assert d.snapshot()["receiving"] is False
    assert links[0].messages[-1] == (1, {"kind": "give-back", "border": "k", "row": 6.0})


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
