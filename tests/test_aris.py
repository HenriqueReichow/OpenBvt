""".aris reader: a synthetic file built here, plus samples/sample.aris when present."""
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import opensonar  # noqa: E402
from opensonar.formats import aris  # noqa: E402

SAMPLE = os.path.join(ROOT, "samples", "sample.aris")


def write_aris(path, frames, ping_mode=1, system_type=0, large_lens=0, sample_period=10,
               start_delay=2000, sound_speed=1500.0):
    """frames: list of (samples, beams) u8 arrays, beam 0 = right-most."""
    with open(path, "wb") as f:
        f.write(struct.pack("<I", aris.SIGNATURE).ljust(aris.HEADER_SIZE, b"\0"))
        for i, data in enumerate(frames):
            h = bytearray(aris.HEADER_SIZE)
            values = dict(index=i, frame_time=1_700_000_000_000_000 + i * 100_000, version=aris.SIGNATURE,
                          ping_mode=ping_mode, sample_period=sample_period, sound_speed=sound_speed,
                          samples_per_beam=data.shape[0], sample_start_delay=start_delay,
                          large_lens=large_lens, system_type=system_type, serial=42, reordered=1)
            for off, (name, fmt) in aris.FRAME_FIELDS.items():
                struct.pack_into("<" + fmt, h, off, values.get(name, 0))
            f.write(h + data.astype(np.uint8).tobytes())


class TestSynthetic(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.aris")
        rng = np.random.default_rng(0)
        self.data = [rng.integers(0, 256, (100, 48), dtype=np.uint8) for _ in range(3)]
        write_aris(self.path, self.data)

    def tearDown(self):
        self.dir.cleanup()

    def test_frames(self):
        with opensonar.open(self.path) as s:
            h = s.heads[0]
            self.assertEqual((s.model, s.serial, h.n_pings), ("1800", 42, 3))
            frames = list(h.pings())
        f = frames[1]
        self.assertEqual(f.number, 1)
        self.assertAlmostEqual(f.timestamp, 1_700_000_000.1)
        # beam 0 is right-most: it becomes the last column (largest angle)
        np.testing.assert_array_equal(f.intensity, self.data[1][:, ::-1])
        self.assertAlmostEqual(f.ranges[0], 2000e-6 * 1500 / 2)
        self.assertAlmostEqual(f.ranges[1] - f.ranges[0], 10e-6 * 1500 / 2)
        self.assertTrue(np.all(np.diff(f.beam_angles) > 0))
        self.assertAlmostEqual(np.rad2deg(f.beam_angles[-1]), aris.BEAM_ANGLES["standard_48"][0])

    def test_lens_tables(self):
        self.assertEqual(aris.lens_table(48, 2, 1), "telephoto_48")
        self.assertEqual(aris.lens_table(96, 0, 0), "standard_96")
        self.assertEqual(aris.lens_table(128, 1, 0), "high_128")
        for k, v in aris.BEAM_ANGLES.items():
            self.assertEqual(len(v), int(k.split("_")[1]), k)
            self.assertTrue(np.all(np.diff(v) < 0), k)

    def test_xy_image(self):
        with opensonar.open(self.path) as s:
            h = s.heads[0]
            img, geo = h.image(h.ping(0), "xy")
        self.assertEqual(img.shape, (geo["rows"], geo["cols"]))
        self.assertEqual(img[geo["origin_row"], geo["origin_col"]], 0)     # inside the blind zone

    def test_bad_signature(self):
        with open(self.path, "r+b") as f:
            f.write(b"XXXX")
        with self.assertRaises(aris.ArisError):
            opensonar.open(self.path)


@unittest.skipUnless(os.path.exists(SAMPLE), "no samples/sample.aris")
class TestSample(unittest.TestCase):
    def test_sample(self):
        with opensonar.open(SAMPLE) as s:
            h = s.heads[0]
            self.assertEqual(h.n_pings, 6)
            f = h.ping(5)
        self.assertEqual(f.intensity.shape, (2000, 48))
        self.assertAlmostEqual(f.ranges[0], 3.298, places=3)
        self.assertAlmostEqual(np.rad2deg(f.beam_angles[-1]), aris.BEAM_ANGLES["telephoto_48"][0])


if __name__ == "__main__":
    unittest.main()
