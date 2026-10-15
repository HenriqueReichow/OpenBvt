"""Display mapping (docs/DISPLAY.md): colormaps, thresholds, gamma, auto intensity, fan crop. No Qt needed."""
import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from opensonar import display  # noqa: E402


class TestColormaps(unittest.TestCase):
    def test_builtin(self):
        for name, f in display.BUILTIN.items():
            c = f()
            self.assertEqual(c.shape, (display.N_COLORS, 3), name)
            self.assertEqual(c.dtype, np.uint8)
        cu = display.colormap("Copper")
        self.assertEqual(cu[0].tolist(), [0, 0, 0])
        self.assertEqual(cu[-1].tolist(), [255, 199, 127])

    def test_cmap_file_is_blue_green_red(self):
        with tempfile.TemporaryDirectory() as d:
            raw = np.zeros((4096, 3), np.uint8)
            raw[:, 0] = 200                                   # blue in the file
            raw.tofile(os.path.join(d, "copper.cmap"))
            np.zeros(5, np.uint8).tofile(os.path.join(d, "broken.cmap"))
            c = display.load_cmap(os.path.join(d, "copper.cmap"))
            self.assertEqual(c[0].tolist(), [0, 0, 200])      # RGB: blue last
            maps = display.colormaps([d])
            self.assertTrue(np.array_equal(maps["Copper"](), c))   # the file replaces the built-in map
            with self.assertRaises(ValueError):
                maps["Broken"]()


class TestMapper(unittest.TestCase):
    def test_lut(self):
        colors = np.stack([np.arange(4096) % 256] * 3, 1).astype(np.uint8)
        m = display.ColorMapper(colors, gamma=0.5, auto=False, sensitivity=100, intensity=65535 - 1100 + 1000)
        self.assertEqual((m.bottom, m.top), (100, 1100))
        lut = m.lut()
        self.assertEqual(lut[0].tolist(), [0, 0, 0, 0])                    # no data → transparent
        self.assertEqual(lut[50].tolist(), list(colors[0]) + [255])        # under the threshold
        self.assertEqual(lut[5000].tolist(), list(colors[-1]) + [255])     # saturated
        v = 350                                                             # (350−100)/1000 = 0.25 → √ = 0.5
        self.assertEqual(lut[v].tolist(), list(colors[round(0.5 * 4095)]) + [255])
        self.assertEqual(m.intensity, 65535 - 1100 + 1000)

    def test_sliders_like_viewer(self):
        m = display.ColorMapper(auto=False)
        self.assertEqual((m.sensitivity, m.top), (20, 56535))             # defaults: 20 and intensity 10000
        m.set_manual(intensity=20000)
        self.assertEqual(m.top, 46535)                                      # more intensity → lower top
        m.set_manual(sensitivity=5000)
        self.assertEqual(m.bottom, 999)                                     # clamped to the slider range

    def test_auto_intensity(self):
        rng = np.random.default_rng(1)
        img = rng.integers(1, 20000, (200, 300)).astype(np.uint16)
        img[:10] = 0
        m = display.ColorMapper(auto=True)
        m.map(img)
        nz = np.sort(img[img > 0])
        self.assertLessEqual(abs(m.top - int(nz[int(len(nz) * 0.9999)])), 32)  # 99.99 % (in 16-wide bins)
        self.assertLessEqual(m.bottom, 32)
        top1 = m.top
        m.map((img // 2).astype(np.uint16))                                 # darker image
        self.assertLess(m.top, top1)
        self.assertGreater(m.top, top1 * 0.8)                               # only 1/5 of the way
        m.map(np.full((10, 10), 3, np.uint16))
        self.assertGreaterEqual(m.top, display.ColorMapper.AUTO_TOP_MIN)
        self.assertEqual(m.map(np.zeros((4, 4), np.uint16)).shape, (4, 4, 4))


class TestCrop(unittest.TestCase):
    def test_crop_fan(self):
        img = np.arange(100 * 200, dtype=np.uint16).reshape(100, 200)
        im = {"rows": 100, "cols": 200, "res": 0.1, "origin_row": 99, "origin_col": 100}
        out, m = display.crop_fan(img, im, 2.0)
        self.assertEqual(out.shape, (22, 43))
        self.assertEqual(out[m["origin_row"], m["origin_col"]], img[99, 100])   # the sonar stays put
        x0, x1, y0, y1 = display.fan_extent(m)
        self.assertAlmostEqual(y1, 2.1)
        self.assertAlmostEqual(-x0, 2.1)


if __name__ == "__main__":
    unittest.main()
