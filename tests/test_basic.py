"""Tests that run without samples/: synthetic inputs for each stage."""
import os
import struct
import sys
import tempfile
import unittest
import zlib

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from opensonar import SonFile, SonError  # noqa: E402
from opensonar.son import Reader, records, _read_channel  # noqa: E402
from opensonar.image import hann_window, beam_gain, auto_hop, box3x3  # noqa: E402


class TestContainer(unittest.TestCase):
    def test_invalid_signature(self):
        with tempfile.NamedTemporaryFile(suffix=".son", delete=False) as f:
            f.write(b"not a son file" * 100)
        try:
            with self.assertRaises(SonError):
                SonFile(f.name)
        finally:
            os.unlink(f.name)

    def test_records(self):
        data = struct.pack(">HHI", 14, 2, 12) + b"abcd" + struct.pack(">HHI", 14, 0x1F, 8)
        self.assertEqual(list(records(data)), [(14, 2, b"abcd")])

    def test_record_without_end(self):
        with self.assertRaises(SonError):
            list(records(struct.pack(">HHI", 14, 2, 12) + b"abcd"))

    def test_reader_big_endian(self):
        r = Reader(struct.pack(">HIf", 13, 1, 2.5) + struct.pack(">I", 3) + b"abc")
        self.assertEqual((r.u16(), r.u32(), r.f32(), r.string()), (13, 1, 2.5, "abc"))

    def test_channel_xor_zlib(self):
        samples = np.arange(100, dtype="<u2") + 2000
        comp = bytes(np.frombuffer(zlib.compress(samples.tobytes()), np.uint8) ^ 0x4C)
        data = struct.pack(">II", samples.size, len(comp)) + comp
        np.testing.assert_array_equal(_read_channel(Reader(data), 2, 0), samples)
        np.testing.assert_array_equal(
            _read_channel(Reader(struct.pack(">II", samples.size, 200) + samples.tobytes()), 0, 0), samples)


class TestImageBasics(unittest.TestCase):
    def test_window(self):
        w = hann_window(256)
        self.assertEqual(w.size, 256)
        self.assertGreater(w.min(), 0)
        np.testing.assert_allclose(w, w[::-1], atol=1e-12)

    def test_constant_gain(self):
        np.testing.assert_allclose(beam_gain(np.full(128, 2.0, np.float32)), 2.0, rtol=1e-6)

    def test_auto_hop(self):
        self.assertEqual(auto_hop(0.6, 4.98), 25)
        self.assertEqual(auto_hop(0.248165, 10.0), 32)
        self.assertEqual(auto_hop(0.0, 150.0), 166)

    def test_box3x3(self):
        img = np.zeros((5, 5), np.uint16)
        img[2, 2] = 90
        out = box3x3(img)
        self.assertEqual(out[2, 2], 10)
        self.assertEqual(out[1, 1], 10)
        self.assertEqual(out[0, 0], 0)

    def test_steering(self):
        # f = c / (4 d sin θ): at the upper frequency limit the angle is asin(c / (4 d f))
        c, d, f = 1540.0, 0.00089, 600000.0
        th = np.arcsin(c / (4 * d * f))
        self.assertAlmostEqual(c / (4 * d * np.sin(th)), f, places=3)
        self.assertAlmostEqual(np.rad2deg(th) + 21.5, 67.635, places=2)


if __name__ == "__main__":
    unittest.main()
