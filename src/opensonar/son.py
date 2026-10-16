""".son file reader.

Implemented from docs/SON_FORMAT.md. The structure is big-endian; signal samples are u16
little-endian (unless the byte-swap flag is set).
"""
from dataclasses import dataclass, field
import struct
import zlib

import numpy as np

SIGNATURE = b"\x89SON\r\n\x1a\n"
BLOCK_SIZE = 4096
SUPPORTED_REVISIONS = (7, 10, 13, 14)   # tested; each record parser follows that record's revision

REC_FEATURES, REC_SONAR, REC_TRANSDUCER, REC_HEAD_SETTINGS = 1, 2, 3, 4
REC_PING_INFO, REC_ORIENTATION, REC_SIGNAL, REC_END = 0x10, 0x11, 0x13, 0x1F
ORIENT_TRANSDUCER = 6


class OpenSonarError(Exception):
    """Base class of every opensonar error (file, network, API)."""


class SonError(OpenSonarError):
    pass


class Reader:
    """Big-endian cursor over a buffer."""

    def __init__(self, data, pos=0):
        self.d, self.pos = data, pos

    def _read(self, fmt):
        v = struct.unpack_from(">" + fmt, self.d, self.pos)
        self.pos += struct.calcsize(">" + fmt)
        return v if len(v) > 1 else v[0]

    def u8(self): return self._read("B")
    def u16(self): return self._read("H")
    def u32(self): return self._read("I")
    def i32(self): return self._read("i")
    def f32(self): return self._read("f")
    def f64(self): return self._read("d")

    def f32s(self, n):
        v = np.frombuffer(self.d, ">f4", n, self.pos).astype(np.float32)
        self.pos += 4 * n
        return v

    def bytes(self, n):
        v = self.d[self.pos:self.pos + n]
        self.pos += n
        return v

    def string(self):
        return self.bytes(self.u32()).decode("latin1")


def records(data, pos=0):
    """Iterates (rev, type, content) until the end record."""
    while pos + 8 <= len(data):
        rev, kind, size = struct.unpack_from(">HHI", data, pos)
        if kind == REC_END:
            return
        if size < 8 or pos + size > len(data):
            raise SonError(f"invalid record at {pos}: type={kind} size={size}")
        yield rev, kind, data[pos + 8:pos + size]
        pos += size
    raise SonError("record sequence without an end record")


@dataclass
class Transducer:
    index: int
    high_freq: int                     # Hz, upper frequency of the sector
    low_freq: int                      # Hz, lower frequency of the sector
    flags: int                         # orientation: 0 RIGHT, 1 LEFT, 0x10 RIGHTALT, 0x11 LEFTALT
    position: tuple = (0.0, 0.0, 0.0)  # m
    rotation: tuple = (0.0, 0.0, 0.0)  # degrees
    oriented: bool = False             # True if the file has a per-transducer orientation (subtype 6)
    angle: float = 0.0                 # record f32 (MountingAngle; radians internally)
    n_elements: int = 0xFFFF           # final u16 of the record (NumElements)
    raw_orientation: bytes = None      # raw subtype 6 orientation record (rev ≥ 11), to write back


@dataclass
class HeadConfig:
    model: str
    range_min: float
    range_max: float
    gain: float
    tvg_slope: float
    center_freq: int
    sound_speed: int
    pulse: np.ndarray             # complex64, N values (chirp replica: part a + j part b)
    normalization: np.ndarray     # float32, M values (per-beam gain)
    raw: dict = field(default_factory=dict)                   # fields without a known meaning yet
    transducers: list = field(default_factory=list)
    raw_orientations: list = field(default_factory=list)      # raw head orientations (subtypes 1, 2)


@dataclass
class Ping:
    number: int
    timestamp: float              # Unix s (UTC)
    tz_offset: int                # s
    range_start: float
    range_stop: float
    signals: list                 # one np.uint16 per transducer (I channel)
    signals_q: list
    raw_orientations: list = field(default_factory=list)      # raw ping orientations (subtypes 3, 4)
    # read from a file: a signal shorter than the window is padded to it (IMAGING.md §3.1). Live
    # pings are not padded: the reference software only does it for recordings.
    pad_to_window: bool = True

    def max_signal(self):
        """Maximum signal: largest excursion from 2048, over 2047."""
        v = np.concatenate([s for s in self.signals + self.signals_q if s.size])
        return float(max(int(v.max()) - 2048, 2047 - int(v.min())) * 0.0004885198)


def _read_head_settings(c, rev):
    """HEAD_SETTINGS. rev >= 12, 8-10 and 6-7."""
    if not (6 <= rev <= 10 or rev >= 12):
        raise SonError(f"HEAD_SETTINGS rev {rev} not supported")
    r = Reader(c)
    raw = {"u16_a": r.u16(), "u8_a": r.u8()}
    model = r.string()
    if rev <= 7:
        raw["str_b"] = r.bytes(16).split(b"\0")[0].decode("latin1")   # fixed 16-byte field
    else:
        raw["str_b"] = r.string()
    raw["f_a"] = r.f32()
    range_min, range_max, gain = r.f32(), r.f32(), r.f32()
    tvg = r.u16() * (1.0 if rev <= 7 else 0.001)   # rev <= 7 stores the TVG without the 0.001 factor
    raw["u8_b"], raw["u8_c"] = r.u8(), r.u8()
    raw["u32_a"], raw["u32_b"] = r.u32(), r.u32()
    freq, ss = r.u32(), r.u16()
    if rev >= 12:
        raw["u16_bcdef"] = [r.u16() for _ in range(5)]
        raw["u32_c"] = r.u32()
        raw["f_bcde"] = [r.f32() for _ in range(4)]
        raw["str_c"], raw["str_d"] = r.string(), r.string()
        raw["f_f"] = r.f32()
    raw["u32_d"], raw["u32_e"] = r.u32(), r.u32()
    raw["f_g"] = r.u32() if rev <= 7 else r.f32()
    n = r.u32()
    a, b = r.f32s(n), r.f32s(n)
    if rev <= 7:
        norm = r.f32s(128)                          # no count; if none is > 0, it becomes 1.0
        if not (norm > 0).any():
            norm = np.ones(128, np.float32)
    else:
        norm = r.f32s(r.u32())
    if r.pos != len(c):
        raise SonError(f"HEAD_SETTINGS: {len(c) - r.pos} bytes left over")
    return HeadConfig(model, range_min, range_max, gain, tvg, freq, ss,
                      (a + 1j * b).astype(np.complex64), norm, raw)


def _read_channel(r, mode, swap):
    n_samples, n_bytes = r.u32(), r.u32()
    data = r.bytes(n_bytes)
    if mode == 2:
        data = bytes(np.frombuffer(data, np.uint8) ^ 0x4C)
    if mode in (1, 2):
        data = zlib.decompress(data)
    elif mode != 0:
        raise SonError(f"unknown compression: {mode}")
    s = np.frombuffer(data, ">u2" if swap else "<u2").astype(np.uint16)
    if s.size != n_samples:
        raise SonError(f"expected {n_samples} samples, read {s.size}")
    return s


class SonFile:
    def __init__(self, path):
        with open(path, "rb") as f:
            self._b = f.read()
        b = self._b
        if b[:8] != SIGNATURE:
            raise SonError(".son signature missing")
        r = Reader(b, 0x18)
        self.revision = r.u16()
        if self.revision not in SUPPORTED_REVISIONS:
            raise SonError(f"revision {self.revision} not supported")
        cfg_block = r.u32()
        self.indices = {}
        for _ in range(r.u16()):
            kind, block = r.u16(), r.u32()
            self.indices[kind] = block
        self.serial = self.firmware = self.sonar_type = self.name = None
        self.fpga = 0
        self.heads = []
        self._read_configuration(self.read_object(cfg_block))
        self._pings = self._read_ping_index(self.read_object(self.indices[1]))

    # --- block layer ---
    def chain(self, start):
        blocks, n = [], start
        while n:
            if n in blocks or (n + 1) * BLOCK_SIZE > len(self._b):
                raise SonError(f"invalid block chain at {n}")
            blocks.append(n)
            n = struct.unpack_from(">I", self._b, n * BLOCK_SIZE)[0]
        return blocks

    def read_object(self, start):
        return b"".join(self._b[n * BLOCK_SIZE + 8:(n + 1) * BLOCK_SIZE] for n in self.chain(start))

    # --- configuration ---
    def _read_configuration(self, data):
        for rev, kind, c in records(data):
            if kind == REC_SONAR and rev <= 7 and len(c) >= 32:
                # u16 type, 12 bytes, serial in a fixed 16-byte field, u16 firmware, u16
                r = Reader(c)
                self.sonar_type = r.u16()
                self.name = ""
                self.serial = c[14:30].split(b"\0")[0].decode("latin1")
                self.firmware = struct.unpack_from(">H", c, 30)[0]
                self.fpga = struct.unpack_from(">H", c, 32)[0] if len(c) >= 34 else 0
            elif kind == REC_SONAR and rev >= 10:
                # u16 type, string name, 8 bytes, string serial, 4 bytes, u16 firmware, u16
                r = Reader(c)
                self.sonar_type = r.u16()
                self.name = r.string()
                r.pos += 8
                self.serial = r.string()
                r.pos += 4
                self.firmware = r.u16()
                self.fpga = r.u16() if r.pos + 2 <= len(c) else 0
            elif kind == REC_HEAD_SETTINGS:
                self.heads.append(_read_head_settings(c, rev))
            elif kind == REC_TRANSDUCER:
                r = Reader(c)
                idx, hi, lo, ang, flags = (r.u8(), r.u32(), r.u32(), r.f32(), r.u16())
                n_el = r.u16() if r.pos + 2 <= len(c) else 0xFFFF
                self.heads[-1].transducers.append(Transducer(idx, hi, lo, flags, angle=ang, n_elements=n_el))
            elif kind == REC_ORIENTATION and rev >= 11 and struct.unpack_from(">H", c)[0] == ORIENT_TRANSDUCER:
                v = struct.unpack_from(">6d", c, 14)
                t = self.heads[-1].transducers[-1]
                t.position, t.rotation = v[:3], v[3:]
                t.oriented = True
                t.raw_orientation = c
            elif kind == REC_ORIENTATION and rev >= 11 and self.heads and not self.heads[-1].transducers:
                self.heads[-1].raw_orientations.append(c)

    def _read_ping_index(self, data):
        r = Reader(data)
        return [[r.u32() for _ in range(r.u32())] for _ in range(r.u32())]

    # --- pings ---
    def n_pings(self, head=0):
        return len(self._pings[head])

    def ping(self, i, head=0):
        data = self.read_object(self._pings[head][i])
        cfg = self.heads[head]
        # the ping inherits the head range; rev > 7 stores its own range
        p = Ping(None, None, 0, cfg.range_min, cfg.range_max, [], [])
        ping_rev = struct.unpack_from(">H", data)[0]
        for rev, kind, c in records(data, 16 if ping_rev > 6 else 12):
            r = Reader(c)
            if kind == REC_PING_INFO:
                p.number = r.u32()
                s, ms = r.u32(), r.u16()
                p.timestamp = s + ms / 1000.0
                if rev > 6:
                    p.tz_offset = r.i32()
                if rev > 7:
                    p.range_start, p.range_stop = r.f32(), r.f32()
            elif kind == REC_ORIENTATION and rev >= 11:
                p.raw_orientations.append(c)
            elif kind == REC_SIGNAL:
                swap, mode = r.u8(), r.u32()
                if rev >= 10:
                    p.signals.append(_read_channel(r, mode, swap))
                    p.signals_q.append(_read_channel(r, mode, swap))
                elif 2 <= rev <= 7:                 # a single channel
                    p.signals.append(_read_channel(r, mode, swap))
                    p.signals_q.append(np.zeros(0, np.uint16))
                else:
                    raise SonError(f"signal record rev {rev} not supported")
        return p


def config_from_dict(d):
    """Rebuilds a HeadConfig from the `config` command of the service (opensonar.server.head_config), or from
    the same fields in opensonar_msgs/SonarInfo, so the images can be made again from a recorded raw signal."""
    import base64
    pulse = d["pulse"]
    norm = d["normalization"]
    pulse = np.frombuffer(base64.b64decode(pulse), "<c8") if isinstance(pulse, str) else np.asarray(pulse, np.complex64)
    norm = np.frombuffer(base64.b64decode(norm), "<f4") if isinstance(norm, str) else np.asarray(norm, np.float32)
    trs = [Transducer(t["index"], t["high_freq"], t["low_freq"], t["flags"], tuple(t["position"]),
                      tuple(t["rotation"]), bool(t["oriented"])) for t in d["transducers"]]
    raw = {"u32_a": int(d["sample_rate"]), "u32_b": int(d["pulse_sample_rate"]), "f_a": np.float32(d["element_spacing"])}
    return HeadConfig(d["model"], float(d["range_min"]), float(d["range_max"]), float(d.get("gain", 0.0)),
                      float(d.get("tvg", 0.0)), int(d["center_freq"]), int(d["sound_speed"]),
                      pulse.astype(np.complex64), norm.astype(np.float32), raw, trs)
