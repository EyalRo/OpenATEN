"""BridgeEnd against a fake libusb endpoint: errors are typed and the retry/reset sequence is as documented."""
import pytest
import usb.core

from openaten import errors
from openaten import protocol as pr
from openaten.usb import BridgeEnd


class FakeEp:
    def __init__(self, script):
        self.script = list(script)  # items: bytes to return/accept, or an exception to raise
        self.calls = 0

    def _next(self):
        self.calls += 1
        item = self.script.pop(0) if self.script else b""
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, data, timeout=None):
        self._next()
        return len(data)

    def read(self, size, timeout=None):
        return self._next()


class FakeDev:
    def __init__(self):
        self.resets = 0

    def ctrl_transfer(self, *a):
        self.resets += 1

    def clear_halt(self, ep):
        pass


def make_end(out=(), inn=()):
    end = object.__new__(BridgeEnd)  # skip __init__: it claims a real device
    end.dev, end._tag, end.ifnum = FakeDev(), 1, 0
    end.ep_out, end.ep_in = FakeEp(out), FakeEp(inn)
    return end


def csw(tag, status=0):
    import struct

    return struct.pack("<4sIIB", b"USBS", tag, 0, status)


@pytest.mark.parametrize(
    "message,expected",
    [("Operation timed out", errors.TransportTimeout), ("[Errno 32] Pipe error", errors.TransportStall), ("No such device", errors.TransportError)],
)
def test_classify_maps_libusb_messages(message, expected):
    assert type(errors.classify(usb.core.USBError(message))) is expected


def test_send_raises_typed_errors_not_usberror():
    end = make_end(out=[usb.core.USBError("Operation timed out")])
    with pytest.raises(errors.TransportTimeout):
        end.send(b"x" * 16, timeout=10, cbw_timeout=10)


def test_recv_stall_is_a_transport_stall():
    end = make_end(out=[b""], inn=[usb.core.USBError("Pipe error")])
    with pytest.raises(errors.TransportStall):
        end.recv(16, timeout=10)


def test_simple_resets_and_retries_then_succeeds():
    # attempt 1: the command block is refused; attempt 2 (after a bulk-only reset): accepted, data + status come back
    end = make_end(out=[usb.core.USBError("Operation timed out"), b""], inn=[bytes([7]), csw(2)])  # the retry is the second command, so tag 2
    status, data = end.simple(pr.cdb_state(), data_in=1)
    assert data == bytes([7]) and status == 0
    assert end.dev.resets == 1  # one bulk-only reset between the attempts


def test_simple_gives_up_after_three_attempts_with_a_typed_error():
    boom = usb.core.USBError("Operation timed out")
    end = make_end(out=[boom] * 20, inn=[boom] * 20)
    with pytest.raises(errors.TransportTimeout):
        end.simple(pr.cdb_state(), data_in=1)


def test_role_returns_none_when_the_inquiry_fails():
    end = make_end(out=[usb.core.USBError("Operation timed out")] * 10)
    assert end.role() is None
