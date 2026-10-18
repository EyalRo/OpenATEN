import logging

import pytest

from openaten import cli, config


def write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_missing_default_file_is_fine_but_a_missing_explicit_file_is_an_error(tmp_path):
    assert config.load(tmp_path / "nope.toml") == {}
    with pytest.raises(ValueError, match="not found"):
        config.load(tmp_path / "nope.toml", explicit=True)


def test_valid_file_is_parsed_and_bad_values_are_skipped_with_a_warning(tmp_path, caplog):
    p = write(tmp_path, 'log_level = "debug"\n[daemon]\nrole = "master"\nhotkey = 5\nmax_active = 30\nbogus = 1\n[nonsense]\nx = 1\n')
    with caplog.at_level(logging.WARNING, logger="openaten.config"):
        cfg = config.load(p)
    assert cfg == {"log_level": "DEBUG", "daemon": {"role": "master", "max_active": 30}}
    text = caplog.text
    assert "hotkey must be str" in text and "unknown key [daemon] bogus" in text and "unknown key nonsense" in text


def test_invalid_role_is_rejected(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="openaten.config"):
        assert config.load(write(tmp_path, '[daemon]\nrole = "boss"\n')) == {"daemon": {}}
    assert "role must be one of" in caplog.text


def test_malformed_file_is_skipped_by_default_and_fatal_when_explicit(tmp_path, caplog):
    p = write(tmp_path, "this is = = not toml")
    with caplog.at_level(logging.WARNING, logger="openaten.config"):
        assert config.load(p) == {}
    with pytest.raises(ValueError):
        config.load(p, explicit=True)


def test_screens_path_is_expanded(tmp_path):
    cfg = config.load(write(tmp_path, '[layout]\nscreens = "~/s.toml"\n'))
    assert not cfg["layout"]["screens"].startswith("~")


def run_cli_capturing_daemon_args(monkeypatch, argv):
    seen = {}
    monkeypatch.setattr(cli, "cmd_daemon", lambda args: seen.update(vars(args)) or 0)
    # main() bound the function at parser-build time via set_defaults(fn=cmd_daemon), so patch before it builds
    assert cli.main(argv) == 0
    return seen


def test_flags_override_environment_which_overrides_file(tmp_path, monkeypatch):
    p = write(tmp_path, 'log_level = "ERROR"\n[daemon]\nrole = "slave"\nhotkey = "ctrl+alt+k"\nmax_active = 7\n')
    monkeypatch.delenv("OPENATEN_LOG", raising=False)
    got = run_cli_capturing_daemon_args(monkeypatch, ["--config", str(p), "daemon", "--role", "master"])
    assert got["role"] == "master"  # flag beats file
    assert got["hotkey"] == "ctrl+alt+k" and got["max_active"] == 7  # file beats built-in default
    assert logging.getLogger("openaten").level == logging.ERROR  # file level when no flag/env
    monkeypatch.setenv("OPENATEN_LOG", "DEBUG")
    run_cli_capturing_daemon_args(monkeypatch, ["--config", str(p), "daemon"])
    assert logging.getLogger("openaten").level == logging.DEBUG  # env beats file
    run_cli_capturing_daemon_args(monkeypatch, ["--config", str(p), "--log-level", "warning", "daemon"])
    assert logging.getLogger("openaten").level == logging.WARNING  # flag beats env


def test_explicit_missing_config_exits(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--config", str(tmp_path / "nope.toml"), "list"])
