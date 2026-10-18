import socket

from openaten import hotplug

BRIDGE = b"05e3/627/6893"


def uevent(action=b"add", product=BRIDGE, subsystem=b"usb", devtype=b"usb_device"):
    fields = [b"%s@/devices/pci0000:00/usb1/1-2" % action, b"ACTION=" + action, b"SUBSYSTEM=" + subsystem, b"DEVTYPE=" + devtype, b"PRODUCT=" + product]
    return b"\0".join(fields) + b"\0"


def test_bridge_add_and_remove_are_recognised():
    assert hotplug.parse(uevent(b"add")) == "add"
    assert hotplug.parse(uevent(b"remove")) == "remove"


def test_other_devices_actions_and_subsystems_are_ignored():
    assert hotplug.parse(uevent(product=b"557/2414/100")) is None  # the switch's hub, not the bridge
    assert hotplug.parse(uevent(b"change")) is None
    assert hotplug.parse(uevent(devtype=b"usb_interface")) is None
    assert hotplug.parse(uevent(subsystem=b"block")) is None


def test_garbage_and_udev_rebroadcasts_are_ignored():
    assert hotplug.parse(b"") is None
    assert hotplug.parse(b"libudev\0\xfe\xed\xca\xfe" + b"\0" * 40) is None
    assert hotplug.parse(b"add@/x\0SUBSYSTEM=usb\0DEVTYPE=usb_device\0ACTION=add\0\0") is None  # no PRODUCT
    assert hotplug.parse(b"add@/x\0SUBSYSTEM=usb\0DEVTYPE=usb_device\0ACTION=add\0PRODUCT=zz/yy\0") is None


def test_start_falls_back_when_netlink_is_unavailable(monkeypatch, caplog):
    def refuse(*a, **k):
        raise OSError("Operation not permitted")

    monkeypatch.setattr(socket, "socket", refuse)
    assert hotplug.start(lambda action: None) is None
    assert "falling back to polling" in caplog.text
