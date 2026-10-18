import pytest

from openaten import appmsg, clipboard
from openaten.clipboard import PNG, TEXT, Clip, ClipboardSync


class FakeBackend:
    def __init__(self, clip=None):
        self.clip, self.written = clip, []

    def read(self):
        return self.clip

    def write(self, clip):
        self.written.append(clip)
        self.clip = clip  # a real clipboard now holds what we wrote


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make(initial=None, up=True, **kw):
    backend, sent, clock = FakeBackend(initial), [], Clock()

    def send(data):
        if up:
            sent.append(appmsg.unpack(data))
        return up

    sync = ClipboardSync(backend, send, interval=0.5, now=clock, **kw)
    return sync, backend, sent, clock


def tick(sync, clock):
    clock.t += 1
    sync.poll()


def test_existing_clipboard_is_never_sent_but_later_changes_are():
    sync, backend, sent, clock = make(Clip(TEXT, b"secret already there"))
    tick(sync, clock)
    tick(sync, clock)
    assert sent == []
    backend.clip = Clip(TEXT, b"new")
    tick(sync, clock)
    header, payload = sent[0]
    assert header["kind"] == "clip" and header["mime"] == TEXT and payload == b"new"
    tick(sync, clock)
    assert len(sent) == 1  # unchanged clipboard is not resent


def test_polling_is_rate_limited():
    sync, backend, sent, clock = make()
    tick(sync, clock)
    backend.clip = Clip(TEXT, b"x")
    clock.t += 0.1
    sync.poll()
    assert sent == []
    clock.t += 1
    sync.poll()
    assert len(sent) == 1


def test_received_clip_is_written_once_and_not_echoed_back():
    sync, backend, sent, clock = make()
    tick(sync, clock)
    clip = Clip(PNG, b"\x89PNG...")
    sync.on_message(*appmsg.unpack(appmsg.pack("clip", clip.data, mime=PNG, sha=clip.digest)))
    assert backend.written == [clip]
    tick(sync, clock)
    assert sent == []


def test_bad_messages_are_ignored():
    sync, backend, sent, clock = make()
    good = Clip(TEXT, b"hi")
    for header, payload in [
        ({"kind": "clip", "mime": "application/x-evil", "sha": good.digest}, b"hi"),
        ({"kind": "clip", "mime": TEXT, "sha": "wrong"}, b"hi"),
        ({"kind": "clip", "mime": TEXT, "sha": good.digest}, b""),
        ({"kind": "other", "mime": TEXT, "sha": good.digest}, b"hi"),
    ]:
        sync.on_message(header, payload)
    sync.max_bytes = 1
    sync.on_message({"kind": "clip", "mime": TEXT, "sha": good.digest}, b"hi")
    assert backend.written == []


def test_oversize_clip_is_skipped_once_with_a_warning(caplog):
    sync, backend, sent, clock = make(max_bytes=4)
    tick(sync, clock)
    backend.clip = Clip(TEXT, b"too long for the limit")
    tick(sync, clock)
    tick(sync, clock)
    assert sent == [] and caplog.text.count("not shared") == 1


def test_clip_is_retried_while_the_link_is_down():
    sync, backend, sent, clock = make(up=False)
    tick(sync, clock)
    backend.clip = Clip(TEXT, b"hello")
    tick(sync, clock)
    assert sent == []
    sync.send = lambda data: sent.append(appmsg.unpack(data)) or True
    tick(sync, clock)
    assert sent and sent[0][1] == b"hello"


@pytest.mark.parametrize(
    "types,expected",
    [(["text/plain;charset=utf-8", "image/png"], TEXT), (["UTF8_STRING"], TEXT), (["image/png"], PNG), (["image/jpeg"], None), ([], None)],
)
def test_type_selection_prefers_text(types, expected):
    assert clipboard._pick(types) == expected


def test_command_backend_reads_through_the_tool(monkeypatch):
    calls = []

    def fake_run(argv, capture_output, timeout):
        calls.append(argv)

        class R:
            returncode = 0
            stdout = b"text/plain;charset=utf-8\nimage/png\n" if "--list-types" in argv or "TARGETS" in argv else b"hello"

        return R()

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard.CommandBackend(wayland=True).read() == Clip(TEXT, b"hello")
    assert calls[1] == ["wl-paste", "--no-newline", "--type", TEXT]
    assert clipboard.CommandBackend(wayland=False).read() == Clip(TEXT, b"hello")
    assert calls[-1][:3] == ["xclip", "-selection", "clipboard"] and "UTF8_STRING" in calls[-1]


def test_detect_without_tools_or_display_returns_none(monkeypatch, caplog):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert clipboard.detect() is None and "disabled" in caplog.text


def test_failing_clipboard_tool_backs_off_instead_of_stalling_the_loop(caplog):
    class Broken:
        calls = 0

        def read(self):
            self.calls += 1
            raise TimeoutError("wl-paste did not answer")

        def write(self, clip):
            raise AssertionError

    backend, clock = Broken(), Clock()
    sync = ClipboardSync(backend, lambda d: True, interval=0.5, now=clock)
    for _ in range(40):
        clock.t += 1
        sync.poll()
    assert backend.calls < 15  # the retry delay grew towards the 30 s cap
    assert caplog.text.count("retrying less often") == 1
    backend.read = lambda: Clip(TEXT, b"x")  # the tool recovers
    clock.t += 31
    sync.poll()
    assert sync._delay == 0.5


def test_command_backend_raises_on_a_hung_tool(monkeypatch):
    def hang(argv, capture_output, timeout):
        raise clipboard.subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(clipboard.subprocess, "run", hang)
    with pytest.raises(TimeoutError):
        clipboard.CommandBackend(wayland=True).read()
