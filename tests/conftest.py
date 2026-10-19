import pytest


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Never read the developer's own ~/.config/openaten (a real screens.toml or config.toml would change what the CLI tests see)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
