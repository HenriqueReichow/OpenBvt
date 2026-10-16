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
FRAME_PORT = 56125                           # UDP port where we ask for the frames (fixed, for firewalls)
RECONNECT_DELAY = 1.0                        # s between reconnection attempts
QUEUE = 30                                   # frames kept while nobody reads them

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

# starting settings per sonar type (system type: 0, 1, 2), valid examples for each one in fresh water:
# 0 → 1.5–7.5 m, 1 → 1.5–5 m, 2 → 4–24 m; focus at the middle of the window
DEFAULTS = {
    0: dict(high_freq=1, ping_mode=3, samples_per_beam=1014, sample_period=8, sample_start_delay=2028,
            cycle_period=10500, pulse_width=11, frame_rate=15.0, receiver_gain=18.0, focus=4.5),
    1: dict(high_freq=1, ping_mode=9, samples_per_beam=946, sample_period=5, sample_start_delay=2028,
            cycle_period=7118, pulse_width=10, frame_rate=15.0, receiver_gain=12.0, focus=3.25),
    2: dict(high_freq=1, ping_mode=1, samples_per_beam=1082, sample_period=25, sample_start_delay=5408,
            cycle_period=32818, pulse_width=24, frame_rate=10.0, receiver_gain=20.0, focus=14.0),
}
# valid ranges of the acoustic settings (the sonar ignores invalid ones)
SAMPLE_PERIOD = (4, 100)                     # µs
SAMPLES_PER_BEAM = (128, 4096)
START_DELAY = (930, 60000)                   # µs
CYCLE_PERIOD = (1802, 150000)                # µs
FRAME_RATE = (1.0, 15.0)
GAIN = (0.0, 24.0)                           # dB
CYCLE_MARGIN = 360                           # µs after the last sample before the next ping
PINGS_PER_FRAME = {1: 3, 3: 6, 6: 4, 9: 8}
NOMINAL_SOUND_SPEED = 1500.0                 # m/s, until the first frame tells the real one


class ArisNetError(OpenSonarError):
    pass


class NoFrame(ArisNetError):
    """No frame arrived within the timeout given to `frame()`."""


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


DEFAULTS_RATE = 15.0                         # frames/s asked for; lowered when the cycle does not allow it


def _clamp(v, lo, hi):
    return min(max(v, lo), hi)


# ---------------------------------------------------------------- the connection

class ArisNet:
    """Connection to one sonar: settings, keepalive, automatic reconnection and frame assembly.

    Two threads run while it is open, whether or not anyone reads frames: one receives and assembles
    the frames (keeping the latest QUEUE), the other sends PING every second and, when the command
    connection drops, reconnects and sends the starting sequence again with the current settings.

    system_type: 0, 1 or 2 (selects the starting settings and the beam table); None = from the
    sonar's beacon. A beacon saying the sonar is busy (another program connected) is refused.
    frame_port: UDP port for the frames (0 = any free one).
    """

    def __init__(self, host, system_type=None, timeout=5.0, command_port=COMMAND_PORT,
                 beacon_port=BEACON_PORT, frame_ip=None, frame_port=FRAME_PORT, check_busy=True):
        import collections
        self.host, self.timeout, self.command_port, self.frame_ip = host, timeout, command_port, frame_ip
        heard = []
        if system_type is None or check_busy:
            wait = timeout if system_type is None else min(timeout, 1.5)       # beacons come every second
            try:
                heard = [b for b in discover(wait, beacon_port) if b["host"] == host]
            except OSError:                                    # beacon port taken by another program
                heard = []
        if heard and heard[0]["busy"]:
            raise ArisNetError(f"the sonar at {host} is busy: another program is connected to it "
                               "(only one client at a time; close the other program)")
        if system_type is None:
            if not heard:
                raise ArisNetError(f"no beacon from {host} in {timeout:.0f} s; pass system_type")
            system_type = heard[0]["system_type"]
        self.serial = heard[0]["serial"] if heard else 0
        self.system_type = system_type
        self.model = aris.SYSTEM_TYPES.get(system_type, f"type {system_type}")
        d = dict(DEFAULTS.get(system_type, DEFAULTS[0]))
        self.focus = d.pop("focus")
        self.settings = dict(d, cookie=0, enable_transmit=1, enable_150v=1)
        self.sound_speed = NOMINAL_SOUND_SPEED
        self.frames_received, self.last_frame_time, self.reconnects = 0, None, 0
        self.connected_at, self.warning = time.time(), None
        self._parts = {}                     # frame index → [header, bytearray, received bytes]
        self._frames = collections.deque(maxlen=QUEUE)
        self._new = threading.Condition()
        self._lock = threading.Lock()        # one command at a time on the TCP stream
        self._closed = threading.Event()
        self._tcp = None

        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        try:
            self._udp.bind(("", frame_port))
        except OSError as e:
            self._udp.close()
            raise ArisNetError(f"UDP port {frame_port} for the frames is in use ({e}); "
                               "is another opensonar connected?") from None
        self.frame_port = self._udp.getsockname()[1]
        self._udp.settimeout(0.5)
        try:
            self._open()
        except OSError as e:
            self._udp.close()
            raise ArisNetError(f"cannot connect to {host}:{command_port}: {e}") from None
        self._threads = [threading.Thread(target=f, daemon=True) for f in (self._receive, self._link)]
        for t in self._threads:
            t.start()

    # --- commands ---
    def _open(self):
        """Opens the command stream and sends the starting sequence (also on every reconnection)."""
        tcp = socket.create_connection((self.host, self.command_port), timeout=self.timeout)
        with self._lock:
            old, self._tcp = self._tcp, tcp
        if old is not None:
            old.close()
        receiver = [(1, self.frame_port)] + ([(2, self.frame_ip)] if self.frame_ip else [])
        self._send(SET_FRAMESTREAM_RECEIVER, encode(receiver))
        # English month names whatever the locale
        t = time.localtime()
        months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        self._send(SET_DATETIME, encode([(1, f"{t.tm_year}-{months[t.tm_mon - 1]}-{t.tm_mday:02d} "
                                            f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}")]))
        self._send_acoustics()
        self._send(SET_SALINITY, encode([(1, 0)]))
        self._send(SET_FOCUS, encode([(2, float(self.focus))]))

    def _send(self, kind, sub=b""):
        with self._lock:
            if self._tcp is None:
                raise OSError("not connected")
            self._tcp.sendall(command(kind, sub))

    def _send_acoustics(self):
        self.settings["cookie"] += 1
        self._send(SET_ACOUSTICS, acoustics_message(self.settings))

    def _alive(self):
        """False when the sonar closed the command stream (it says nothing otherwise)."""
        import select
        with self._lock:
            tcp = self._tcp
        if tcp is None:
            return False
        try:
            readable, _, _ = select.select([tcp], [], [], 0)
            return not (readable and tcp.recv(4096, socket.MSG_PEEK) == b"")
        except OSError:
            return False

    def _link(self):
        """PING every second; on a dropped connection, reconnect until it works or we are closed."""
        while not self._closed.wait(KEEPALIVE):
            try:
                if not self._alive():
                    raise OSError("connection closed by the sonar")
                self._send(PING)
                continue
            except OSError as e:
                self.warning = f"connection to the sonar lost ({e}); reconnecting"
            while not self._closed.is_set():
                try:
                    self._open()
                    self.reconnects += 1
                    self.warning = f"reconnected to the sonar at {time.strftime('%H:%M:%S')} " \
                                   f"({self.reconnects} reconnection(s))"
                    break
                except OSError:
                    self._closed.wait(RECONNECT_DELAY)

    def limits(self):
        """(minimum, maximum) range in m: start delay ≥ 930 µs; end ≤ 60000 µs + 4096 samples × 100 µs."""
        c = self.sound_speed
        return START_DELAY[0] * 1e-6 * c / 2, (START_DELAY[1] + SAMPLES_PER_BEAM[1] * SAMPLE_PERIOD[1]) * 1e-6 * c / 2

    def window(self):
        """(start, stop) in m of the current settings."""
        c, s = self.sound_speed, self.settings
        start = s["sample_start_delay"] * 1e-6 * c / 2
        return start, start + s["sample_period"] * s["samples_per_beam"] * 1e-6 * c / 2

    def adjust_range(self, start, stop):
        """Sample window [start, stop] m, keeping the sample period and changing the sample count; the
        period grows only when 4096 samples are not enough. The cycle period and frame rate follow."""
        c, s = self.sound_speed, self.settings
        delay = _clamp(int(round(2 * start / c * 1e6)), *START_DELAY)
        length = max(2 * (stop - start) / c * 1e6, 1.0)                 # µs of sampling
        period = _clamp(max(s["sample_period"], int(np.ceil(length / SAMPLES_PER_BEAM[1]))), *SAMPLE_PERIOD)
        samples = _clamp(int(np.ceil(length / period)), *SAMPLES_PER_BEAM)
        s.update(sample_start_delay=delay, sample_period=period, samples_per_beam=samples)
        self._fit_timing()
        self._send_acoustics()

    def adjust_gain(self, gain):
        self.settings["receiver_gain"] = float(_clamp(float(gain), *GAIN))
        self._send_acoustics()

    def _fit_timing(self):
        """Cycle period ≥ delay + samples × period + 360 µs, and frame period > cycle × pings per frame."""
        s = self.settings
        cycle = _clamp(s["sample_start_delay"] + s["sample_period"] * s["samples_per_beam"] + CYCLE_MARGIN,
                       *CYCLE_PERIOD)
        pings = PINGS_PER_FRAME.get(s["ping_mode"], 8)
        fastest = 1e6 / (cycle * pings + 1)
        s.update(cycle_period=cycle, frame_rate=float(_clamp(min(DEFAULTS_RATE, fastest), *FRAME_RATE)))

    # --- frames ---
    def _receive(self):
        while not self._closed.is_set():
            try:
                data = self._udp.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                frame = self._part(data)
            except (ArisNetError, aris.ArisError):
                continue
            if frame is not None:
                with self._new:
                    self._frames.append(frame)
                    self.frames_received += 1
                    self.last_frame_time = time.time()
                    self._new.notify_all()

    def _part(self, data):
        """Adds one frame part; returns the frame when it is complete. Incomplete frames are dropped."""
        f = decode(data)
        index, total, offset = f.get(1, 0), f.get(2, 0), f.get(5, 0)
        payload = f.get(4, b"")
        if 3 in f:
            for old in [k for k in self._parts if k < index]:       # a newer frame began
                del self._parts[old]
            self._parts[index] = [bytes(f[3]), bytearray(total), 0]
        part = self._parts.get(index)
        if part is None or offset + len(payload) > len(part[1]):
            return None
        part[1][offset:offset + len(payload)] = payload
        part[2] += len(payload)
        if part[2] < len(part[1]):
            return None
        del self._parts[index]
        return self._finish(*part[:2])

    def frame(self, timeout=None):
        """Oldest frame not read yet (opensonar.frame.Frame). timeout: s to wait (None = until one
        arrives); then NoFrame. ArisNetError once closed."""
        end = None if timeout is None else time.time() + timeout
        with self._new:
            while not self._frames:
                if self._closed.is_set():
                    raise ArisNetError("the connection is closed")
                left = None if end is None else end - time.time()
                if left is not None and left <= 0:
                    raise NoFrame(f"no frame from {self.host} in {timeout:g} s")
                self._new.wait(0.5 if left is None else min(left, 0.5))
            return self._frames.popleft()

    def wait_first(self, timeout):
        """Waits up to `timeout` s for the first frame (it brings the serial number); True if it came."""
        end = time.time() + timeout
        with self._new:
            while not self.frames_received and not self._closed.is_set() and time.time() < end:
                self._new.wait(min(0.2, max(0.0, end - time.time())))
        return bool(self.frames_received)

    def drop_queued(self):
        """Forgets the frames received while nobody was reading."""
        with self._new:
            self._frames.clear()

    def nominal_frame(self):
        """An empty frame with the geometry of the current settings (before any frame arrives)."""
        from .frame import Frame
        s = self.settings
        n = s["samples_per_beam"]
        start, stop = self.window()
        h = dict(ping_mode=s["ping_mode"], system_type=self.system_type, large_lens=0)
        return Frame(number=0, timestamp=time.time(), intensity=np.zeros((n, aris.beams_from_ping_mode(s["ping_mode"])),
                     np.uint8), ranges=np.linspace(start, stop, n, endpoint=False),
                     beam_angles=aris.beam_angles_for(h), sound_speed=self.sound_speed)

    def link_status(self):
        age = None if self.last_frame_time is None else time.time() - self.last_frame_time
        return {"frames": self.frames_received, "since_last_frame": age, "reconnects": self.reconnects,
                "connected_for": time.time() - self.connected_at, "warning": self.warning,
                "frame_port": self.frame_port}

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
        return aris.build_frame(h, ordered, aris.beam_angles_for(h), raw_header)

    def close(self):
        self._closed.set()
        with self._new:
            self._new.notify_all()
        with self._lock:
            tcp, self._tcp = self._tcp, None
        for s in (tcp, self._udp):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass
        for t in getattr(self, "_threads", ()):
            t.join(2)
