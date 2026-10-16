""".ddf reader: a synthetic version-3 file built here, plus samples/ddf/*.ddf when present."""
import calendar
import glob
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import opensonar  # noqa: E402
from opensonar.formats import ddf  # noqa: E402

SAMPLES = sorted(glob.glob(os.path.join(ROOT, "samples", "ddf", "*.ddf")))


def write_ddf(path, frames, n_beams=96, high_res=1, sample_rate=37286.9, serial=523, window_start=2,
              local=(2016, 6, 5, 15, 51, 25), zone=-4 * 3600):
    """Version-3 file: frames are (samples, beams) u8 arrays, beam 0 = right-most."""
    samples = frames[0].shape[0]
    with open(path, "wb") as f:
        head = bytearray(512)
        head[:4] = b"DDF\x03"
        for off, (name, fmt) in ddf.FILE_FIELDS.items():
            value = dict(n_frames=len(frames), frame_rate=10, high_res=high_res, n_beams=n_beams,
                         sample_rate=sample_rate, samples=samples, window_start=window_start, serial=serial)
            if name != "signature":
                struct.pack_into("<" + fmt, head, off, value.get(name, 0))
        f.write(head)
        base = calendar.timegm(local)
        for i, data in enumerate(frames):
            h = bytearray(256)
            t = list(local)
            t[5] += i                                    # one frame per second, 25 hundredths
            values = dict(index=i, frame_time=base - zone + i, signature=b"DDF\x03", year=t[0], month=t[1],
                          day=t[2], hour=t[3], minute=t[4], second=t[5], hsecond=25, window_start=window_start,
                          window_length=3)
            for off, (name, fmt) in ddf.FRAME_FIELDS.items():
                struct.pack_into("<" + fmt, h, off, values.get(name, 0))
            f.write(h + data.astype(np.uint8).tobytes())


class TestSynthetic(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.ddf")
        rng = np.random.default_rng(4)
        self.data = [rng.integers(0, 256, (512, 96), dtype=np.uint8) for _ in range(3)]
        write_ddf(self.path, self.data)

    def tearDown(self):
        self.dir.cleanup()

    def test_frames_ranges_time(self):
        with opensonar.open(self.path) as s:
            h = s.heads[0]
            self.assertEqual((s.sonar_type, s.serial, h.n_pings), ("ddf", 523, 3))
            f = h.ping(1)
        np.testing.assert_array_equal(f.intensity, self.data[1][:, ::-1])      # beam 0 is the right-most
        step = 1457 / (2 * 37286.9)
        self.assertAlmostEqual(f.ranges[0], 2 * 0.000572 * 1457 / 2 + 0.5 * step, places=5)
        self.assertAlmostEqual(f.ranges[-1] - f.ranges[0], 511 * step, places=4)    # 10 m window
        # UTC from the local fields plus their offset to frame_time, with the hundredths
        self.assertAlmostEqual(f.timestamp, calendar.timegm((2016, 6, 5, 19, 51, 26)) + 0.25)
        self.assertAlmostEqual(f.vertical_fov, np.deg2rad(14.0))

    def test_sound_speed_option(self):
        with opensonar.open(self.path, sound_speed=1500.0) as s:
            f = s.heads[0].ping(0)
        self.assertEqual(f.sound_speed, 1500.0)
        self.assertAlmostEqual(f.ranges[1] - f.ranges[0], 1500 / (2 * 37286.9))

    def test_beam_angles(self):
        for n in (48, 96):
            a = np.rad2deg(ddf.beam_angles(n))
            self.assertEqual(a.size, n)
            self.assertTrue(np.all(np.diff(a) > 0))
            self.assertAlmostEqual(a[0], -a[-1], delta=0.2)                     # nearly symmetric fan
            self.assertAlmostEqual(a[-1] - a[0], 28.5, delta=0.6)               # ~29° field of view

    def test_wrong_size_and_signature(self):
        with open(self.path, "ab") as f:
            f.write(b"x")
        with self.assertRaises(ddf.DdfError):
            opensonar.open(self.path)
        with open(self.path, "r+b") as f:
            f.write(b"XXXX")
        with self.assertRaises(ddf.DdfError):
            opensonar.open(self.path)


@unittest.skipUnless(SAMPLES, "no samples/ddf/*.ddf")
class TestRecordings(unittest.TestCase):
    def test_every_frame_reads(self):
        for path in SAMPLES:
            with opensonar.open(path) as s:
                h = s.heads[0]
                frames = list(h.pings())
                img, geo = h.image(frames[0], "xy")
            self.assertEqual(len(frames), h.n_pings)
            self.assertEqual(frames[0].intensity.shape, (512, 96))
            self.assertAlmostEqual(frames[0].ranges[-1] - frames[0].ranges[0], 10.0, delta=0.05)
            stamps = [f.timestamp for f in frames]
            self.assertTrue(all(b >= a for a, b in zip(stamps, stamps[1:])))
            self.assertGreater(img.max(), 0)


if __name__ == "__main__":
    unittest.main()
