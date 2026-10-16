"""The "opensonar-net" NetworkManager profile: fixed IPs on the wired card, in the sonars' subnets.

Two addresses on the same card, so one cable serves both sonar families: 192.168.1.10/24 for the
P900/M900 (factory IP 192.168.1.45) and 169.254.10.10/16 for the .aris family (link-local when no DHCP
server answers). No gateway and no default route.

The .deb creates it at install time (the only moment with a password) and installs a polkit rule, so
activating it never asks for one. Without the .deb (AppImage, pip), `create()` makes a profile owned by
the current user, which NetworkManager also allows without a password in a local session.
"""
import shutil
import subprocess

PROFILE = "opensonar-net"
ADDRESSES = ("192.168.1.10/24", "169.254.10.10/16")
ADDRESS = ",".join(ADDRESSES)
SONAR_IP = "192.168.1.45"


class NetSetupError(Exception):
    pass


def _nmcli(*args, timeout=20):
    if shutil.which("nmcli") is None:
        raise NetSetupError("NetworkManager (nmcli) is not available")
    r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise NetSetupError((r.stderr or r.stdout).strip() or f"nmcli {' '.join(args)} failed")
    return r.stdout


def wired_devices():
    """Ethernet devices known to NetworkManager: [(device, state)]."""
    out = _nmcli("-t", "-f", "DEVICE,TYPE,STATE", "device")
    devs = []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == "ethernet":
            devs.append((parts[0], parts[2]))
    return devs


def exists():
    return PROFILE in _nmcli("-t", "-f", "NAME", "connection", "show").splitlines()


def create(device=None, user=None):
    """Creates the profile (manual IPv4 ADDRESSES, no gateway or DNS, no IPv6, not autoconnecting).
    user: make it private to that user (no root needed)."""
    if device is None:
        devs = wired_devices()
        if not devs:
            raise NetSetupError("no wired (Ethernet) card found")
        device = devs[0][0]
    args = ["connection", "add", "type", "ethernet", "con-name", PROFILE, "ifname", device,
            "ipv4.method", "manual", "ipv4.addresses", ADDRESS, "ipv4.never-default", "yes",
            "ipv6.method", "disabled", "connection.autoconnect", "no"]
    if user:
        args += ["connection.permissions", f"user:{user}"]
    _nmcli(*args)
    return device


def activate(device=None):
    """Brings the profile up (on `device`, or on the card it was created for)."""
    args = ["connection", "up", PROFILE] + (["ifname", device] if device else [])
    _nmcli(*args, timeout=40)


def ping_sonar(ip=SONAR_IP, count=2):
    r = subprocess.run(["ping", "-c", str(count), "-W", "1", ip], capture_output=True, text=True)
    return r.returncode == 0


def setup(user=None):
    """Creates the profile if needed, activates it and checks the sonar. Returns a message."""
    import getpass
    created = False
    if not exists():
        create(user=user or getpass.getuser())
        created = True
    activate()
    ok = ping_sonar()
    return (("created and " if created else "") + f"activated {PROFILE} ({ADDRESS}); sonar {SONAR_IP} "
            + ("answers" if ok else "does not answer yet (power on? cable? it takes ~40 s to boot)"))
