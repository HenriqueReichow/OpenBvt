"""Writing .son files (opensonar.writer): round trip through the reader and live recording from the emulator.
"""
import glob
import os
import struct
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import opensonar  # noqa: E402
from opensonar import SonFile  # noqa: E402
from opensonar.son import HeadConfig, Ping, Transducer, BLOCK_SIZE  # noqa: E402
from opensonar.writer import SonWriter, copy_son  # noqa: E402
from opensonar.image import xy, rtheta  # noqa: E402

SAMPLES = sorted(glob.glob(os.path.join(ROOT, "samples", "*.son")) +
                 glob.glob(os.path.join(ROOT, "samples", "extra", "*.son")))


def _synthetic_head(n_tr=2, oriented=True):
    rng = np.random.default_rng(3)
    z = (rng.normal(size=50) + 1j * rng.normal(size=50)).astype(np.complex64)
    c = HeadConfig("TEST-1", 0.5, 30.0, 4.0, 0.125, 900000, 1500, z, rng.random(128).astype(np.float32),
                   {"u16_a": 4, "u32_a": 600000, "u32_b": 24000000, "f_a": np.float32(0.00089)})
    for i in range(n_tr):
        t = Transducer(i, 1200000, 600000, (0, 1, 0x10, 0x11)[i % 4], angle=0.25 * i)
        if oriented:
            t.position, t.rotation, t.oriented = (0.01 * i, 0.075, 0.0), (0.0, 0.0, 20.0 * i), True
        c.transducers.append(t)
    return c


def _synthetic_ping(n, k, n_tr=2, iq=False):
    rng = np.random.default_rng(k)
    signals = [rng.integers(0, 4096, n).astype(np.uint16) for _ in range(n_tr)]
    qs = [rng.integers(0, 4096, n).astype(np.uint16) for _ in range(n_tr)] if iq else \
        [np.zeros(0, np.uint16) for _ in range(n_tr)]
    return Ping(k, 1700000000.125 + k, 10800, 0.5, 12.0 + k, signals, qs)


class TestWithoutData(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_synthetic_round_trip(self):
        heads = [_synthetic_head(2), _synthetic_head(3, oriented=False)]
        heads[1].model = "TEST-2"
        path = os.path.join(self.dir.name, "s.son")
        pings = {0: [_synthetic_ping(5000, k) for k in range(3)],
                 1: [_synthetic_ping(9000, 10 + k, 3, True) for k in range(2)]}
        with SonWriter(path, heads, sonar_type=291, name="NAME", serial="1234", firmware=6262, fpga=7) as w:
            for k in range(3):                          # interleaved heads, as live
                w.add(pings[0][k], 0)
                if k < 2:
                    w.add(pings[1][k], 1)
        b = open(path, "rb").read()
        self.assertEqual(len(b) % BLOCK_SIZE, 0)
        self.assertEqual(b[:8], b"\x89SON\r\n\x1a\n")
        s = SonFile(path)
        self.assertEqual((s.revision, s.sonar_type, s.name, s.serial, s.firmware, s.fpga),
                         (14, 291, "NAME", "1234", 6262, 7))
        self.assertEqual([s.n_pings(0), s.n_pings(1)], [3, 2])
        for h, cfg in enumerate(heads):
            c = s.heads[h]
            self.assertEqual((c.model, c.range_min, c.range_max, c.gain, c.sound_speed, c.center_freq),
                             (cfg.model, cfg.range_min, cfg.range_max, cfg.gain, cfg.sound_speed, cfg.center_freq))
            self.assertAlmostEqual(c.tvg_slope, cfg.tvg_slope)
            self.assertTrue(np.array_equal(c.pulse, cfg.pulse))
            self.assertTrue(np.array_equal(c.normalization, cfg.normalization))
            self.assertEqual(c.raw["u32_a"], 600000)
            for ta, tb in zip(cfg.transducers, c.transducers):
                self.assertEqual((ta.index, ta.high_freq, ta.low_freq, ta.flags, ta.oriented),
                                 (tb.index, tb.high_freq, tb.low_freq, tb.flags, tb.oriented))
                self.assertAlmostEqual(ta.angle, tb.angle, places=6)
                if ta.oriented:
                    self.assertEqual((tuple(ta.position), tuple(ta.rotation)), (tuple(tb.position), tuple(tb.rotation)))
            for k, p in enumerate(pings[h]):
                q = s.ping(k, h)
                self.assertEqual((q.number, q.timestamp, q.tz_offset, q.range_start, q.range_stop),
                                 (p.number, p.timestamp, p.tz_offset, p.range_start, p.range_stop))
                for x, y in zip(p.signals + p.signals_q, q.signals + q.signals_q):
                    self.assertTrue(np.array_equal(x, y))

    def test_chains_and_indices(self):
        path = os.path.join(self.dir.name, "c.son")
        with SonWriter(path, [_synthetic_head()]) as w:
            for k in range(2):
                w.add(_synthetic_ping(7000, k))         # > 1 block per ping
        s = SonFile(path)
        for start in [1] + s._pings[0]:
            blocks = s.chain(start)
            self.assertEqual(blocks, list(range(start, start + len(blocks))))   # consecutive blocks
            for k, n in enumerate(blocks):
                nxt, prev = struct.unpack_from(">II", s._b, n * BLOCK_SIZE)
                self.assertEqual(prev, blocks[k - 1] if k else 0)
        idx = {t: b for t, b in (struct.unpack_from(">HI", s._b, 0x20 + 6 * i) for i in range(5))}
        self.assertEqual(sorted(idx), [1, 2, 3, 4, 5])
        times = s.read_object(idx[2])
        self.assertEqual(struct.unpack_from(">I", times)[0], 2)
        self.assertEqual(struct.unpack_from(">IHI", times, 4), (0, 0, s._pings[0][0]))

    def test_missing_head(self):
        with SonWriter(os.path.join(self.dir.name, "x.son"), [_synthetic_head()]) as w:
            with self.assertRaises(ValueError):
                w.add(_synthetic_ping(10, 0), 1)


@unittest.skipUnless(SAMPLES, "no samples/")
class TestCopy(unittest.TestCase):
    def test_copy_of_every_sample(self):
        """rev 7, 10, 13 and 14 → rev 14: same fields, signals and images."""
        with tempfile.TemporaryDirectory() as d:
            for f in SAMPLES:
                dst = copy_son(f, os.path.join(d, os.path.basename(f)), pings=range(2))
                a, b = SonFile(f), SonFile(dst)
                self.assertEqual(b.revision, 14)
                self.assertEqual((a.serial, a.firmware, a.sonar_type), (b.serial, b.firmware, b.sonar_type))
                ha, hb = a.heads[0], b.heads[0]
                for i in range(min(2, a.n_pings())):
                    pa, pb = a.ping(i), b.ping(i)
                    with self.subTest(file=os.path.basename(f), ping=i):
                        self.assertEqual((pa.number, pa.range_start, pa.range_stop), (pb.number, pb.range_start, pb.range_stop))
                        self.assertAlmostEqual(pa.timestamp, pb.timestamp, places=3)
                        self.assertTrue(all(np.array_equal(x, y) for x, y in zip(pa.signals, pb.signals)))
                        self.assertTrue(np.array_equal(xy(ha, pa)[0], xy(hb, pb)[0]))
                        self.assertTrue(np.array_equal(rtheta(ha, pa)[0], rtheta(hb, pb)[0]))


class TestLiveRecording(unittest.TestCase):
    FILE = os.path.join(ROOT, "samples", "exp1.son")

    @unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
    def test_emulator_to_son(self):
        from sonar_emulator import Emulator
        emu = Emulator(self.FILE, compress_signal=True)
        emu.quiet = True
        threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
        while emu.tcp_port is None:
            time.sleep(0.01)
        with tempfile.TemporaryDirectory() as d:
            dst = os.path.join(d, "live.son")
            with opensonar.open(f"127.0.0.1:{emu.tcp_port}") as s, SonWriter.for_sonar(dst, s) as w:
                for p in s.head(0).pings(n=3):
                    w.add(p)
            a, b = SonFile(self.FILE), SonFile(dst)
            self.assertEqual((a.sonar_type, a.serial, a.firmware, a.fpga), (b.sonar_type, b.serial, b.firmware, b.fpga))
            self.assertEqual(a.heads[0].raw, b.heads[0].raw)
            for i in range(3):
                self.assertTrue(np.array_equal(xy(a.heads[0], a.ping(i))[0], xy(b.heads[0], b.ping(i))[0]))


if __name__ == "__main__":
    unittest.main()
