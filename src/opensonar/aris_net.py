"""Live driver for sonars that record .aris files (docs/ARIS_LIVE.md).

- Discovery: the sonar broadcasts availability beacons to UDP 56124.
- Commands: TCP 56888, each one a protobuf message preceded by its size (u32, big-endian).
- Frames: UDP to the port we announce; each frame comes in parts (protobuf), the first one with the
  frame header, and with the samples unordered (formats.aris.reorder).
- The sonar stops when it hears nothing: a PING command is sent every second.

The protobuf messages are few and small, so they are encoded here by hand (no dependency).
"""
import socket
import struct
import threading
import time

import numpy as np

from .formats import aris
from .son import OpenSonarError

BEACON_PORT = 56124
COMMAND_PORT = 56888
KEEPALIVE = 1.0                              # s between PING commands

# command types
SET_DATETIME, SET_FRAMESTREAM_RECEIVER, SET_ACOUSTICS, SET_SALINITY, SET_FOCUS, PING = 0, 1, 3, 4, 6, 14
# command field holding the sub-message of each type
_COMMAND_FIELD = {SET_DATETIME: 2, SET_FRAMESTREAM_RECEIVER: 3, SET_ACOUSTICS: 5, SET_SALINITY: 6,
                  SET_FOCUS: 8, PING: 16}

# acoustic settings fields: name → (field number, kind)
ACOUSTIC_FIELDS = {
    "cookie": (1, "u"), "frame_rate": (2, "f"), "samples_per_beam": (3, "u"), "sample_start_delay": (4, "u"),
    "cycle_period": (5, "u"), "sample_period": (6, "u"), "pulse_width": (7, "u"), "ping_mode": (8, "u"),
    "enable_transmit": (9, "u"), "high_freq": (10, "u"), "enable_150v": (11, "u"), "receiver_gain": (12, "f"),
}

# starting settings per sonar type (system type: 0, 1, 2), as suggested for each one
DEFAULTS = {
    0: dict(high_freq=1, ping_mode=3, samples_per_beam=1000, sample_period=4, sample_start_delay=1360,
            cycle_period=5720, pulse_width=6, frame_rate=12.0, receiver_gain=18.0, focus=2.5),
    1: dict(high_freq=1, ping_mode=9, samples_per_beam=800, sample_period=4, sample_start_delay=1360,
            cycle_period=4920, pulse_width=6, frame_rate=12.0, receiver_gain=12.0, focus=2.2),
    2: dict(high_freq=1, ping_mode=1, samples_per_beam=512, sample_period=4, sample_start_delay=1333,
            cycle_period=3750, pulse_width=5, frame_rate=8.0, receiver_gain=20.0, focus=1.75),
}
SAMPLE_PERIOD = (4, 100)                     # µs, allowed range
CYCLE_MARGIN = 360                           # µs after the last sample before the next ping
NOMINAL_SOUND_SPEED = 1500.0                 # m/s, until the first frame tells the real one


class ArisNetError(OpenSonarError):
    pass


# ---------------------------------------------------------------- protobuf (the subset used)

def _varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def encode(fields):
    """[(field number, value)] → bytes. int → varint, float → fixed32, bytes/str → length-delimited."""
    out = bytearray()
    for num, v in fields:
        if isinstance(v, bool) or isinstance(v, int):
            out += _varint(num << 3) + _varint(int(v) & 0xFFFFFFFFFFFFFFFF)
        elif isinstance(v, float):
            out += _varint(num << 3 | 5) + struct.pack("<f", v)
        else:
            v = v.encode() if isinstance(v, str) else bytes(v)
            out += _varint(num << 3 | 2) + _varint(len(v)) + v
    return bytes(out)


def decode(data):
    """bytes → {field number: value} (last one wins). varint → int, fixed32 → bytes, length-delimited → bytes."""
    fields, i = {}, 0
    while i < len(data):
        key, i = _read_varint(data, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _read_varint(data, i)
        elif wire == 5:
            v, i = data[i:i + 4], i + 4
        elif wire == 1:
            v, i = data[i:i + 8], i + 8
        elif wire == 2:
            n, i = _read_varint(data, i)
            v, i = data[i:i + n], i + n
        else:
            raise ArisNetError(f"protobuf wire type {wire} not supported")
        fields[num] = v
    return fields


def _read_varint(data, i):
    n = shift = 0
    while True:
        if i >= len(data):
            raise ArisNetError("truncated protobuf")
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def command(kind, sub=b""):
    """Command message with its size prefix, ready for the TCP socket."""
    body = encode([(1, kind), (_COMMAND_FIELD[kind], sub)])
    return struct.pack(">I", len(body)) + body


def acoustics_message(settings):
    return encode([(num, (float if kind == "f" else int)(settings[name]))
                   for name, (num, kind) in ACOUSTIC_FIELDS.items() if name in settings])


def read_beacon(data):
    """Availability beacon → dict(serial, system_type, busy)."""
    f = decode(data)
    return dict(serial=f.get(1, 0), system_type=f.get(2, 0), busy=bool(f.get(4, 0)))


def discover(timeout=2.0, port=BEACON_PORT):
    """Sonars heard during `timeout` s: [dict(host, serial, system_type, busy)]."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    found, end = {}, time.time() + timeout
    try:
        while (left := end - time.time()) > 0:
            s.settimeout(left)
            try:
                data, (host, _) = s.recvfrom(256)
            except socket.timeout:
                break
            try:
                found[host] = dict(host=host, **read_beacon(data))
            except ArisNetError:
                continue
    finally:
        s.close()
    return list(found.values())


# ---------------------------------------------------------------- the connection

class ArisNet:
    """Connection to one sonar: settings, keepalive and frame assembly.

    system_type: 0, 1 or 2 (selects the starting settings and the beam table); None = wait for the
    sonar's beacon to find it out.
    """

    def __init__(self, host, system_type=None, timeout=5.0, command_port=COMMAND_PORT,
                 beacon_port=BEACON_PORT, frame_ip=None):
        self.host, self.timeout = host, timeout
        if system_type is None:
            heard = [b for b in discover(timeout, beacon_port) if b["host"] == host]
            if not heard:
                raise ArisNetError(f"no beacon from {host} in {timeout:.0f} s; pass system_type")
            system_type, self.serial = heard[0]["system_type"], heard[0]["serial"]
        else:
            self.serial = 0
        self.system_type = system_type
        self.model = aris.SYSTEM_TYPES.get(system_type, f"type {system_type}")
        d = dict(DEFAULTS.get(system_type, DEFAULTS[0]))
        self.focus = d.pop("focus")
        self.settings = dict(d, cookie=0, enable_transmit=1, enable_150v=1)
        self.sound_speed = NOMINAL_SOUND_SPEED
        self._parts = {}                     # frame index → [header, bytearray, received bytes]
        self._lock = threading.Lock()
        self._closed = threading.Event()

        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self._udp.bind(("", 0))
        self._udp.settimeout(timeout)
        self._tcp = socket.create_connection((host, command_port), timeout=timeout)
        self._send(SET_DATETIME, encode([(1, time.strftime("%Y-%b-%d %H:%M:%S"))]))
        receiver = [(1, self._udp.getsockname()[1])] + ([(2, frame_ip)] if frame_ip else [])
        self._send(SET_FRAMESTREAM_RECEIVER, encode(receiver))
        self._send_acoustics()
        self._send(SET_SALINITY, encode([(1, 0)]))
        self._send(SET_FOCUS, encode([(2, float(self.focus))]))
        self._keepalive = threading.Thread(target=self._keep, daemon=True)
        self._keepalive.start()

    # --- commands ---
    def _send(self, kind, sub=b""):
        with self._lock:
            self._tcp.sendall(command(kind, sub))

    def _send_acoustics(self):
        self.settings["cookie"] += 1
        self._send(SET_ACOUSTICS, acoustics_message(self.settings))

    def _keep(self):
        while not self._closed.wait(KEEPALIVE):
            try:
                self._send(PING)
            except OSError:
                return

    def limits(self):
        """(minimum, maximum) range in m that the ping cycle allows (start delay ≥ 930 µs, cycle ≤ 65535 µs)."""
        c = self.sound_speed
        return 930e-6 * c / 2, (65535 - CYCLE_MARGIN) * 1e-6 * c / 2

    def window(self):
        """(start, stop) in m of the current settings."""
        c, s = self.sound_speed, self.settings
        start = s["sample_start_delay"] * 1e-6 * c / 2
        return start, start + s["sample_period"] * s["samples_per_beam"] * 1e-6 * c / 2

    def adjust_range(self, start, stop):
        """Sample window: start delay from `start`, sample period (4–100 µs) to cover up to `stop`."""
        c, s = self.sound_speed, self.settings
        delay = max(930, int(round(2 * start / c * 1e6)))
        period = int(np.ceil(2 * (stop - start) / c * 1e6 / s["samples_per_beam"]))
        period = min(max(period, SAMPLE_PERIOD[0]), SAMPLE_PERIOD[1])
        s.update(sample_start_delay=delay, sample_period=period,
                 cycle_period=min(65535, delay + period * s["samples_per_beam"] + CYCLE_MARGIN))
        self._send_acoustics()

    def adjust_gain(self, gain):
        self.settings["receiver_gain"] = float(gain)
        self._send_acoustics()

    # --- frames ---
    def frame(self):
        """Next complete frame (opensonar.frame.Frame); incomplete frames are dropped."""
        while True:
            try:
                data = self._udp.recv(65536)
            except socket.timeout:
                raise ArisNetError(f"no frame from {self.host} in {self.timeout:.0f} s") from None
            f = decode(data)
            index, total, offset = f.get(1, 0), f.get(2, 0), f.get(5, 0)
            payload = f.get(4, b"")
            if 3 in f:
                for old in [k for k in self._parts if k < index]:       # a newer frame began
                    del self._parts[old]
                self._parts[index] = [bytes(f[3]), bytearray(total), 0]
            part = self._parts.get(index)
            if part is None or offset + len(payload) > len(part[1]):
                continue
            part[1][offset:offset + len(payload)] = payload
            part[2] += len(payload)
            if part[2] >= len(part[1]):
                del self._parts[index]
                return self._finish(*part[:2])

    def _finish(self, raw_header, data):
        h = aris.read_frame_header(raw_header.ljust(aris.HEADER_SIZE, b"\0"))
        n = h["samples_per_beam"]
        if h["reordered"]:
            ordered = np.frombuffer(bytes(data), np.uint8).reshape(n, -1)
        else:
            ordered = aris.reorder(bytes(data), h["ping_mode"], n)
            h["reordered"] = 1
        self.sound_speed = h["sound_speed"] or self.sound_speed
        self.serial = self.serial or h["serial"]
        return aris.build_frame(h, ordered, aris.beam_angles_for(h))

    def close(self):
        self._closed.set()
        for s in (self._tcp, self._udp):
            try:
                s.close()
            except OSError:
                pass
