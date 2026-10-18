import logging

from openaten import log as logsetup


def test_setup_level_precedence(monkeypatch):
    root = logging.getLogger("openaten")
    monkeypatch.setenv(logsetup.ENV_VAR, "ERROR")
    logsetup.setup()
    assert root.level == logging.ERROR
    logsetup.setup("debug")  # explicit level wins and is case-insensitive
    assert root.level == logging.DEBUG
    monkeypatch.delenv(logsetup.ENV_VAR)
    logsetup.setup("bogus")  # unknown names fall back to INFO instead of crashing
    assert root.level == logging.INFO


def test_setup_is_idempotent():
    root = logging.getLogger("openaten")
    logsetup.setup()
    n = len(root.handlers)
    logsetup.setup("WARNING")
    assert len(root.handlers) == n
