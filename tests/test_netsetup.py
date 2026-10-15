"""opensonar-net profile: the nmcli calls (NetworkManager is mocked; nothing on this machine changes)."""
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from opensonar import netsetup  # noqa: E402


class FakeNM:
    def __init__(self, profiles=(), devices="enp4s0:ethernet:connected\nwlo1:wifi:connected\n"):
        self.profiles, self.devices, self.calls = list(profiles), devices, []

    def __call__(self, *args, timeout=20):
        self.calls.append(args)
        if args[:4] == ("-t", "-f", "DEVICE,TYPE,STATE", "device"):
            return self.devices
        if args[:4] == ("-t", "-f", "NAME", "connection"):
            return "\n".join(self.profiles) + "\n"
        if args[:2] == ("connection", "add"):
            self.profiles.append(args[args.index("con-name") + 1])
        return ""


def test_setup_creates_a_user_profile_once_and_activates_it(monkeypatch):
    nm = FakeNM(profiles=["Wired connection 1"])
    monkeypatch.setattr(netsetup, "_nmcli", nm)
    monkeypatch.setattr(netsetup, "ping_sonar", lambda *a, **k: True)
    msg = netsetup.setup(user="lh")
    add = next(c for c in nm.calls if c[:2] == ("connection", "add"))
    opts = dict(zip(add[2::2], add[3::2]))
    assert opts["con-name"] == "opensonar-net" and opts["ifname"] == "enp4s0"           # the wired card
    assert opts["ipv4.addresses"] == "192.168.1.10/24" and opts["ipv4.method"] == "manual"
    assert opts["ipv4.never-default"] == "yes" and opts["connection.autoconnect"] == "no"
    assert opts["connection.permissions"] == "user:lh"
    assert ("connection", "up", "opensonar-net") in nm.calls
    assert "created" in msg and "answers" in msg
    nm.calls.clear()
    assert "created" not in netsetup.setup(user="lh")                                 # exists: only activate
    assert not any(c[:2] == ("connection", "add") for c in nm.calls)


def test_no_wired_card(monkeypatch):
    monkeypatch.setattr(netsetup, "_nmcli", FakeNM(devices="wlo1:wifi:connected\n"))
    try:
        netsetup.create()
    except netsetup.NetSetupError as e:
        assert "wired" in str(e)
    else:
        raise AssertionError("expected NetSetupError")
