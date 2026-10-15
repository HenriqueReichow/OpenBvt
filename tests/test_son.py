"""Compares the reader with the reference metadata (samples/reference/*.json)."""
import json
import os
import sys
import unittest

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from opensonar import SonFile  # noqa: E402

SAMPLES = ("swimmer", "cut1", "exp1")
HAS_DATA = all(os.path.exists(os.path.join(ROOT, "samples", n + ".son")) and
               os.path.exists(os.path.join(ROOT, "samples", "reference", n + ".json")) for n in SAMPLES)
SKIP = unittest.skipUnless(HAS_DATA, "samples/ and samples/reference/ not available")


def reference(name):
    with open(os.path.join(ROOT, "samples", "reference", name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def son(name):
    return SonFile(os.path.join(ROOT, "samples", name + ".son"))


@SKIP
class TestContainer(unittest.TestCase):
    def test_revision(self):
        self.assertEqual(son("swimmer").revision, 13)
        for name in ("cut1", "exp1", "exp2", "exp3", "exp4", "test2"):
            self.assertEqual(son(name).revision, 14, name)

    def test_same_ping_in_cut1_and_exp1(self):
        a, b = son("cut1").ping(0), son("exp1").ping(1)
        self.assertEqual(a.timestamp, b.timestamp)
        for x, y in zip(a.signals, b.signals):
            np.testing.assert_array_equal(x, y)


@SKIP
class TestReference(unittest.TestCase):
    def test_sonar(self):
        for name in SAMPLES:
            with self.subTest(name):
                g, s = reference(name)["sonar"], son(name)
                self.assertEqual(s.serial, g["serial_number"])
                self.assertEqual(str(s.firmware), g["firmware_revision"])
                self.assertEqual(s.heads[0].model, g["sonar_model_name"])
                self.assertEqual(len(s.heads), g["head_count"])

    def test_head(self):
        for name in SAMPLES:
            with self.subTest(name):
                g, c, s = reference(name)["heads"][0], son(name).heads[0], son(name)
                self.assertEqual(c.model, g["head_name"])
                self.assertAlmostEqual(c.range_min, g["minimum_range"], places=6)
                self.assertAlmostEqual(c.range_max, g["maximum_range"], places=6)
                self.assertAlmostEqual(c.gain, g["gain_adjustment"], places=6)
                self.assertAlmostEqual(c.tvg_slope, g["tvg_slope"], places=6)
                self.assertEqual(c.center_freq, g["center_freq"])
                self.assertEqual(c.sound_speed, g["sound_speed"])
                self.assertEqual(len(c.transducers), g["transducer_count"])
                self.assertEqual(s.n_pings(), g["ping_count"])

    def test_pings(self):
        for name in SAMPLES:
            s, gh = son(name), reference(name)["heads"][0]
            for i, g in enumerate(gh["pings"]):
                with self.subTest(file=name, ping=i):
                    p = s.ping(i)
                    self.assertEqual(p.number, g["ping_number"])
                    self.assertAlmostEqual(p.timestamp, g["timestamp"], places=3)
                    self.assertEqual(p.tz_offset, g["time_zone_offset"])
                    self.assertAlmostEqual(p.range_start, g["start_range"], places=6)
                    self.assertAlmostEqual(p.range_stop, g["stop_range"], places=6)
                    self.assertEqual(son(name).heads[0].sound_speed, g["sound_speed"])

    def test_max_signal_9_pings(self):
        n = 0
        for name in SAMPLES:
            s = son(name)
            for i, g in enumerate(reference(name)["heads"][0]["pings"]):
                with self.subTest(file=name, ping=i):
                    self.assertAlmostEqual(s.ping(i).max_signal(), g["max_return_signal"], places=6)
                    n += 1
        self.assertEqual(n, 9)

    def test_samples_match_range(self):
        # number of samples = (stop range - start range) * 2 / c * sampling rate
        for name in SAMPLES:
            s = son(name)
            c, p = s.heads[0], s.ping(0)
            expected = (p.range_stop - p.range_start) * 2 / c.sound_speed * c.transducers[0].high_freq
            for x in p.signals:
                self.assertLess(abs(x.size - expected), 2, name)


if __name__ == "__main__":
    unittest.main()
