"""Writing .son files (revision 14, SON_FORMAT.md §2–6, §9).

    with SonWriter("out.son", heads, serial="2058", sonar_type=291, firmware=6262) as w:
        for p in pings:
            w.add(p)                # each ping goes to disk immediately; indices on close()

    copy_son("source.son", "copy.son")      # conversion to rev 14 (also from older revisions)

Layout: block 0 = header; configuration from block 1; one block chain per ping, in arrival order;
indices 1–5 at the end. Each block: u32 next, u32 previous, 4088 data bytes.
Without `close()` the file has no index.
"""
import os
import struct
import time
import zlib

import numpy as np

from .son import (SIGNATURE, BLOCK_SIZE, REC_FEATURES, REC_SONAR, REC_TRANSDUCER, REC_HEAD_SETTINGS,
                  REC_PING_INFO, REC_ORIENTATION, REC_SIGNAL, REC_END, ORIENT_TRANSDUCER, SonFile)

REVISION = 14
BLOCK_DATA = BLOCK_SIZE - 8
ORIENTATION_SIZE = 82
ORIENT_NO_TRANSDUCER = 5


# ---------------------------------------------------------------- serialization

def _s(text):
    b = (text or "").encode("latin1", "replace")
    return struct.pack(">I", len(b)) + b


def _rec(kind, content):
    return struct.pack(">HHI", REVISION, kind, 8 + len(content)) + content


def _f32_bits(v):
    """f_g of revisions ≤ 7 comes as a u32 holding the bits of a float."""
    if isinstance(v, (int, np.integer)) and not isinstance(v, bool):
        return struct.unpack(">f", struct.pack(">I", int(v)))[0]
    return float(v)


def _orientation(subtype, fields12=b"", values=None):
    c = struct.pack(">H", subtype) + fields12.ljust(12, b"\0")
    if values is not None:
        c += struct.pack(">6d", *values)
    return c.ljust(ORIENTATION_SIZE, b"\0")


# defaults when the source has no orientation (live session)
DEFAULT_HEAD_ORIENTATIONS = (_orientation(1), _orientation(2))
DEFAULT_PING_ORIENTATIONS = (_orientation(3, bytes.fromhex("0000000000000000" "00010002")),
                             _orientation(4, bytes.fromhex("0000000000000000" "00010004")))


def head_settings(cfg):
    """HEAD_SETTINGS rev ≥ 12 (35 items, SON_FORMAT.md §5). Fields missing from the source (older
    revisions or live XML) get the values observed in rev 13/14 recordings."""
    b = cfg.raw
    bcdef = list(b.get("u16_bcdef", [0, 256, 256, 0, 0])) + [0] * 5
    f_bcde = list(b.get("f_bcde", [0.0] * 4)) + [0.0] * 4
    z = np.asarray(cfg.pulse, np.complex64)
    norm = np.asarray(cfg.normalization, np.float32)
    return b"".join([
        struct.pack(">HB", b.get("u16_a", 0), b.get("u8_a", 0)),
        _s(cfg.model), _s(b.get("str_b", "0")),
        struct.pack(">ffff", b.get("f_a", 0.0), cfg.range_min, cfg.range_max, cfg.gain),
        struct.pack(">HBB", int(round(cfg.tvg_slope * 1000)), b.get("u8_b", 0), b.get("u8_c", 0)),
        struct.pack(">IIIH", b.get("u32_a", 0), b.get("u32_b", 0), cfg.center_freq, cfg.sound_speed),
        struct.pack(">5H", *bcdef[:5]), struct.pack(">I", b.get("u32_c", 0)),
        struct.pack(">4f", *f_bcde[:4]), _s(b.get("str_c", "")), _s(b.get("str_d", "")),
        struct.pack(">f", b.get("f_f", 0.0)),
        struct.pack(">IIf", b.get("u32_d", 0), b.get("u32_e", 0), _f32_bits(b.get("f_g", 0.0))),
        struct.pack(">I", z.size), z.real.astype(">f4").tobytes(), z.imag.astype(">f4").tobytes(),
        struct.pack(">I", norm.size), norm.astype(">f4").tobytes(),
    ])


def _transducer(t):
    return struct.pack(">BIIfHH", t.index, t.high_freq, t.low_freq, t.angle, t.flags, t.n_elements)


def configuration(heads, sonar_type=None, name="", serial="", firmware=0, fpga=0):
    """Configuration record chain."""
    recs = [_rec(REC_FEATURES, bytes(28)),
            _rec(REC_SONAR, struct.pack(">H", 0xFFFF if sonar_type is None else sonar_type) + _s(name) + bytes(8)
                 + _s(str(serial or "")) + bytes(4) + struct.pack(">HH", firmware or 0, fpga or 0))]
    for cfg in heads:
        recs.append(_rec(REC_HEAD_SETTINGS, head_settings(cfg)))
        orient = list(cfg.raw_orientations) if len(cfg.raw_orientations) == 2 else list(DEFAULT_HEAD_ORIENTATIONS)
        recs += [_rec(REC_ORIENTATION, o) for o in orient]
        for t in cfg.transducers:
            recs.append(_rec(REC_TRANSDUCER, _transducer(t)))
            if t.raw_orientation is not None:
                o = t.raw_orientation
            elif t.oriented:
                o = _orientation(ORIENT_TRANSDUCER, b"", tuple(t.position) + tuple(t.rotation))
            else:
                # no per-transducer orientation (older recordings): a zeroed subtype 5,
                # and the file keeps the old image mapping method
                o = _orientation(ORIENT_NO_TRANSDUCER)
            recs.append(_rec(REC_ORIENTATION, o))
    recs.append(_rec(REC_END, b""))
    return b"".join(recs)


def _channel(samples):
    """n samples, n bytes, data XOR 0x4C over zlib (mode 2)."""
    a = np.asarray(samples, np.uint16)
    d = np.frombuffer(zlib.compress(a.astype("<u2").tobytes()), np.uint8) ^ 0x4C
    return struct.pack(">II", a.size, d.size) + d.tobytes()


def ping_records(p, order_in_file, write_time=None):
    """A ping chain: 16-byte header + info + orientations + one signal per transducer + end."""
    t = time.time() if write_time is None else write_time
    tz = time.altzone if time.localtime(t).tm_isdst > 0 else time.timezone   # s west of UTC
    hdr = struct.pack(">HIHiI", REVISION, int(t), int(t % 1 * 1000), tz, order_in_file)
    s = int(np.floor(p.timestamp + 1e-6))
    ms = int(round((p.timestamp - s) * 1000))
    if ms >= 1000:
        s, ms = s + 1, ms - 1000
    info = struct.pack(">IIHiffHI", p.number or 0, s, ms, p.tz_offset or 0, p.range_start, p.range_stop, 0, 0)
    recs = [hdr, _rec(REC_PING_INFO, info)]
    orient = list(p.raw_orientations) if len(p.raw_orientations) == 2 else list(DEFAULT_PING_ORIENTATIONS)
    recs += [_rec(REC_ORIENTATION, o) for o in orient]
    empty = np.zeros(0, np.uint16)
    qs = list(p.signals_q) + [empty] * (len(p.signals) - len(p.signals_q))
    for i, q in zip(p.signals, qs):
        recs.append(_rec(REC_SIGNAL, struct.pack(">BI", 0, 2) + _channel(i) + _channel(q)))
    recs.append(_rec(REC_END, b""))
    return b"".join(recs)


# ---------------------------------------------------------------- file

class SonWriter:
    def __init__(self, path, heads, sonar_type=None, name="", serial="", firmware=0, fpga=0):
        self.path = path
        self.n_heads = len(heads)
        self._f = open(path, "wb")
        self._next_block = 1
        self._f.write(b"\xcd" * BLOCK_SIZE)             # final header written on close()
        self._pings = [[] for _ in heads]               # first block of each ping, per head
        self._order = []                                # (ping order in the file, head, block)
        self._cfg_block = self._write_chain(configuration(heads, sonar_type, name, serial, firmware, fpga))

    @classmethod
    def for_sonar(cls, path, sonar):
        """Writer with the configuration of an `opensonar.sdk.Sonar` (file or live)."""
        return cls(path, [h.config for h in sonar.heads], sonar.sonar_type, sonar.name or "",
                   sonar.serial or "", sonar.firmware or 0, sonar.fpga or 0)

    def _write_chain(self, data):
        """Writes `data` in consecutive blocks; the rest of the last block gets rev+0x1F and zeros."""
        n = max(1, -(-len(data) // BLOCK_DATA))
        full = (data + struct.pack(">HH", REVISION, REC_END)).ljust(n * BLOCK_DATA, b"\0")[:n * BLOCK_DATA]
        start = self._next_block
        parts = []
        for k in range(n):
            nxt = start + k + 1 if k + 1 < n else 0
            prev = start + k - 1 if k > 0 else 0
            parts.append(struct.pack(">II", nxt, prev) + full[k * BLOCK_DATA:(k + 1) * BLOCK_DATA])
        self._f.seek(start * BLOCK_SIZE)
        self._f.write(b"".join(parts))
        self._next_block += n
        return start

    def add(self, ping, head=0):
        """Writes one ping (opensonar.son.Ping) of the given head."""
        if not 0 <= head < self.n_heads:
            raise ValueError(f"head {head} does not exist (there are {self.n_heads})")
        k = len(self._order)
        block = self._write_chain(ping_records(ping, k))
        self._pings[head].append(block)
        self._order.append((k, head, block))

    def close(self):
        if self._f.closed:
            return
        idx1 = struct.pack(">I", self.n_heads) + b"".join(
            struct.pack(f">I{len(l)}I", len(l), *l) for l in self._pings)
        idx2 = struct.pack(">I", len(self._order)) + b"".join(struct.pack(">IHI", *e) for e in self._order)
        indices = [(1, self._write_chain(idx1)), (2, self._write_chain(idx2))]
        indices += [(t, self._write_chain(struct.pack(">I", 0))) for t in (3, 4, 5)]
        hdr = (SIGNATURE + struct.pack(">H", 1) + bytes.fromhex("000000001000") + b"\xff" * 4 + bytes(4)
               + struct.pack(">HIH", REVISION, self._cfg_block, len(indices))
               + b"".join(struct.pack(">HI", t, b) for t, b in indices))
        self._f.seek(0)
        self._f.write(hdr)
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def copy_son(source, destination, pings=None):
    """Rewrites a .son as rev 14 (every head; `pings` = indices to copy, default all)."""
    son = SonFile(source)
    with SonWriter(destination, son.heads, son.sonar_type, son.name or "", son.serial or "",
                   son.firmware or 0, son.fpga) as w:
        for h in range(len(son.heads)):
            for i in (range(son.n_pings(h)) if pings is None else pings):
                if i < son.n_pings(h):
                    w.add(son.ping(i, h), h)
    return destination


def raw_writer(path, sonar):
    """(writer, path) for the lossless recording of `sonar` in its own format: .son for raw-signal
    sonars, .aris for the ones whose frames come from .aris data. `path` may have either extension
    or none; the right one is put in."""
    base = os.path.splitext(path)[0] if os.path.splitext(path)[1].lower() in (".son", ".aris") else path
    if getattr(sonar, "sonar_type", None) == "aris":
        from .formats.aris import ArisWriter
        return ArisWriter(base + ".aris"), base + ".aris"
    return SonWriter.for_sonar(base + ".son", sonar), base + ".son"
