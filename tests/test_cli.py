import pytest

from openaten import cli, ends


@pytest.fixture(autouse=True)
def no_bridge(monkeypatch):
    monkeypatch.setattr(cli, "find_ends", lambda: [])
    monkeypatch.setattr(ends, "find_ends", lambda: [])
    ends.OPENED.clear()
    ends.STUCK_THREADS.clear()


def test_list_without_a_bridge_reports_and_fails(capsys):
    assert cli.main(["list"]) == 1
    assert "no bridge ends found" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", ["loopback", "hid"])
def test_selftests_need_two_ends(cmd, capsys):
    assert cli.main([cmd]) == 1
    assert "need two bridge ends" in capsys.readouterr().out


def test_ctl_reports_a_missing_daemon(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert cli.main(["ctl", "status"]) == 1
    assert "daemon not running" in capsys.readouterr().out


def test_unknown_command_and_bad_log_level_are_rejected():
    with pytest.raises(SystemExit):
        cli.main(["nope"])
    with pytest.raises(SystemExit):
        cli.main(["--log-level", "loud", "list"])
