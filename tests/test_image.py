"""Compares the image generator with the reference images (samples/reference/*.npz)."""
import os
import sys
import unittest
import warnings

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from opensonar import SonFile  # noqa: E402
from opensonar.image import box3x3, rtheta_geometry, rtheta, xy  # noqa: E402

# test2: range > 5 m (another branch of the hop policy); extra/*: image method 0 (older files)
SAMPLES = ("swimmer", "exp1", "test2", "extra/P900-AcousticShadowDiver",
           "extra/DP900-SunkenRaft", "extra/P450-PipelineSurvey")
HAS_DATA = all(os.path.exists(os.path.join(ROOT, "samples", n + ".son")) and
               os.path.exists(os.path.join(ROOT, "samples", "reference", os.path.basename(n) + ".npz"))
               for n in SAMPLES)


def data(name):
    s = SonFile(os.path.join(ROOT, "samples", name + ".son"))
    z = np.load(os.path.join(ROOT, "samples", "reference", os.path.basename(name) + ".npz"))
    return s.heads[0], s.ping(0), z


@unittest.skipUnless(HAS_DATA, "samples/ and samples/reference/ not available")
class TestImage(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore")

    def test_geometry(self):
        for name in SAMPLES:
            with self.subTest(name):
                c, p, z = data(name)
                g = rtheta_geometry(c, p)
                ref = z["head0_ping0_rtheta"]
                self.assertEqual((g["rows"], g["cols"]), ref.shape)
                if "head0_ping0_rtheta_range_m" not in z:
                    continue
                np.testing.assert_allclose(g["ranges"], z["head0_ping0_rtheta_range_m"][:, 0], atol=1e-5)
                np.testing.assert_allclose(np.rad2deg(g["angles"]), z["head0_ping0_rtheta_angle_deg"][0], atol=1e-4)

    def test_crop_and_smoothing_options(self):
        for name in SAMPLES:
            with self.subTest(name):
                c, p, _ = data(name)
                full, G = xy(c, p)
                part, g = xy(c, p, crop=True)
                r0, c0 = G["origin_row"] - g["origin_row"], G["origin_col"] - g["origin_col"]
                window = full[r0:r0 + part.shape[0], c0:c0 + part.shape[1]]
                self.assertEqual(window.shape, part.shape)
                self.assertTrue(np.array_equal(window[1:-1, 1:-1], part[1:-1, 1:-1]))   # borders: filter edge
                self.assertEqual(int(full.astype(np.int64).sum()), int(window.astype(np.int64).sum()))
                raw, _ = xy(c, p, smooth=False)
                self.assertTrue(np.array_equal(box3x3(raw), full))         # smoothing = the 3x3 mean only

    def test_rtheta_against_reference(self):
        for name in SAMPLES:
            with self.subTest(name):
                c, p, z = data(name)
                img, _ = rtheta(c, p)
                ref = z["head0_ping0_rtheta"]
                m = ref > 0
                r = np.corrcoef(np.log10(img[m] + 1.0), np.log10(ref[m] + 1.0))[0, 1]
                self.assertGreater(r, 0.95)
                self.assertGreater(np.mean(img == ref), 0.90)

    def test_xy_against_reference(self):
        for name in SAMPLES:
            with self.subTest(name):
                c, p, z = data(name)
                img, g = xy(c, p)
                ref = z["head0_ping0_xy"]
                self.assertEqual(img.shape, ref.shape)
                m = ref > 0
                r = np.corrcoef(np.log10(img[m] + 1.0), np.log10(ref[m] + 1.0))[0, 1]
                self.assertGreater(r, 0.95)
                self.assertGreater(np.mean(img == ref), 0.90)


if __name__ == "__main__":
    unittest.main()
