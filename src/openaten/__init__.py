"""OpenATEN: open tooling for the Genesys Logic host bridge in ATEN US3342-class switches."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("openaten")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
