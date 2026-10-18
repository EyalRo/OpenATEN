"""Self-tests against a real switch. Skipped unless requested: ``pytest -m hardware``.

Needs both upstream USB-C ports of the switch connected to this machine and the udev rule installed. Read docs/hardware.md first: a few command
patterns can wedge the firmware until it is power-cycled. Run these one process at a time, with nothing else using the bridge.
"""
import pytest

from openaten import cli, ends

pytestmark = pytest.mark.hardware


@pytest.fixture(autouse=True)
def two_ends():
    try:
        found = ends.find_ends()
    except Exception as e:  # e.g. libusb is not installed
        pytest.skip(f"cannot enumerate USB devices: {e}")
    if len(found) < 2:
        pytest.skip("needs both bridge ends (connect both upstream ports to this machine)")
    ends.OPENED.clear()
    ends.STUCK_THREADS.clear()
    yield
    ends.close_all()


def test_list_and_state():
    assert cli.main(["list"]) == 0
    assert cli.main(["state"]) == 0


def test_loopback_packets_arrive_intact():
    assert cli.main(["loopback", "-n", "3"]) == 0


def test_link_exchanges_messages_both_ways():
    assert cli.main(["link", "--mib", "4"]) == 0


def test_hid_injection_round_trip():
    assert cli.main(["hid"]) == 0
