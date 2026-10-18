"""CLI commands that wire the real modules together, with hardware, evdev and signals replaced by stubs."""
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from openaten import cli, clipboard, ends
from openaten import daemon as dm
from openaten import forward as fw
from openaten.transfer import TransferManager

SCREENS = """
local = "desk"
[[screen]]
name = "DP-1"
host = "desk"
x = 0
y = 0
width = 2560
height = 1440

[[screen]]
name = "eDP-1"
host = "laptop"
x = -1920
y = 300
width = 1920
height = 1200
"""


@pytest.fixture(autouse=True)
def no_bridge(monkeypatch):
    monkeypatch.setattr(cli, "find_ends", lambda: [])
    monkeypatch.setattr(ends, "find_ends", lambda: [])
    ends.OPENED.clear()
    ends.STUCK_THREADS.clear()


def screens_file(tmp_path, text=SCREENS):
    p = tmp_path / "screens.toml"
    p.write_text(text)
    return str(p)


def test_layout_prints_the_borders(tmp_path, capsys):
    assert cli.main(["layout", "--screens", screens_file(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "leave via x-" in out and "into eDP-1" in out


@pytest.mark.parametrize("cmd", ["layout", "strip"])
def test_layout_and_strip_report_a_bad_or_missing_screens_file(cmd, tmp_path, capsys):
    assert cli.main([cmd, "--screens", str(tmp_path / "missing.toml")]) == 1
    assert "missing.toml" in capsys.readouterr().out
    assert cli.main([cmd, "--screens", screens_file(tmp_path, "[[screen]]\nname = 'x'\n")]) == 1
    assert "needs name, host" in capsys.readouterr().out


def test_forward_without_a_bridge_fails(caplog):
    assert cli.main(["forward"]) == 1
    assert "no bridge end found" in caplog.text


def fake_end(monkeypatch):
    class Dev:
        bus, address = 1, 2
        port_numbers = (1, 2)

    class End:
        def __init__(self, dev):
            self.dev = dev
            self.hid = lambda report: None

        def close(self):
            pass

    monkeypatch.setattr(cli, "find_ends", lambda: [Dev()])
    monkeypatch.setattr(cli, "BridgeEnd", End)
    return End


def test_forward_runs_the_evdev_loop_with_the_chosen_options(monkeypatch):
    fake_end(monkeypatch)
    seen = {}

    def run_evdev(send, hotkey, dry_run, max_active, edges):
        seen.update(hotkey=hotkey, dry_run=dry_run, max_active=max_active, edges=edges)
        return 7

    monkeypatch.setattr(fw, "run_evdev", run_evdev)
    assert cli.main(["forward", "--hotkey", "ctrl+alt+k", "--dry-run", "--max-active", "30"]) == 7
    assert seen == {"hotkey": "ctrl+alt+k", "dry_run": True, "max_active": 30.0, "edges": None}


def test_forward_reports_a_missing_evdev(monkeypatch, caplog):
    fake_end(monkeypatch)

    def no_evdev(*a, **k):
        raise ImportError("evdev")

    monkeypatch.setattr(fw, "run_evdev", no_evdev)
    assert cli.main(["forward"]) == 1
    assert "python-evdev is required" in caplog.text


def test_forward_can_select_the_end_by_port(monkeypatch, caplog):
    fake_end(monkeypatch)
    monkeypatch.setattr(fw, "run_evdev", lambda *a, **k: 0)
    assert cli.main(["forward", "--port", "9.9"]) == 1  # no end on that port
    assert cli.main(["forward", "--port", "1.2"]) == 0


def test_explicit_missing_screens_file_exits_instead_of_running_without_edges(tmp_path, monkeypatch):
    fake_end(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["forward", "--screens", str(tmp_path / "missing.toml")])


def test_daemon_wires_options_into_the_daemon(monkeypatch, tmp_path):
    captured = {}

    class StubDaemon:
        def __init__(self, make_runner, find_ends, open_end, **kw):
            captured.update(kw, make_runner=make_runner)
            self.stop_flag = SimpleNamespace(set=lambda: None)

        def run(self):
            return 0

    handlers = {}
    monkeypatch.setattr(dm, "Daemon", StubDaemon)
    monkeypatch.setattr(signal, "signal", lambda sig, fn: handlers.update({sig: fn}))
    assert cli.main(["daemon", "--role", "slave", "--hotkey", "ctrl+alt+k", "--receive-dir", str(tmp_path / "in"), "--port", "1.2"]) == 0
    assert captured["role"] == "slave" and captured["hotkey"] == "ctrl+alt+k" and captured["port"] == "1.2"
    assert captured["make_clipboard"] is None  # opt-in
    manager = captured["make_transfers"](lambda data: True)
    assert isinstance(manager, TransferManager) and manager.receive_dir == tmp_path / "in"
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
    handlers[signal.SIGTERM](signal.SIGTERM, None)  # the handler asks the daemon to stop


def test_daemon_without_receive_dir_can_send_but_not_receive(monkeypatch):
    captured = {}

    class StubDaemon:
        def __init__(self, make_runner, find_ends, open_end, **kw):
            captured.update(kw)
            self.stop_flag = SimpleNamespace(set=lambda: None)

        def run(self):
            return 0

    monkeypatch.setattr(dm, "Daemon", StubDaemon)
    monkeypatch.setattr(signal, "signal", lambda *a: None)
    assert cli.main(["daemon", "--clipboard"]) == 0
    assert captured["make_transfers"](lambda data: True).receive_dir is None
    assert callable(captured["make_clipboard"])


def test_make_clipboard_is_none_without_a_supported_tool(monkeypatch):
    monkeypatch.setattr(clipboard, "detect", lambda: None)
    assert cli._make_clipboard(lambda data: True) is None
    backend = object()
    monkeypatch.setattr(clipboard, "detect", lambda: backend)
    assert cli._make_clipboard(lambda data: True).backend is backend


def test_send_reports_each_file(monkeypatch, tmp_path, capsys):
    replies = iter([{"ok": True, "id": "abc"}, {"ok": False, "error": "not a regular file"}])
    monkeypatch.setattr(dm, "request", lambda cmd, **fields: next(replies))
    assert cli.main(["send", "a.txt", "b.txt"]) == 1
    out = capsys.readouterr().out
    assert "a.txt: offered (transfer abc)" in out and "b.txt: failed: not a regular file" in out


def test_default_config_path_is_under_xdg(monkeypatch, tmp_path):
    from openaten import config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.default_path() == Path(tmp_path) / "openaten" / "config.toml"
