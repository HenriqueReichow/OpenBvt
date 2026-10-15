"""Tests the opensonar export command."""
import os
import sys
import tempfile
import unittest
import warnings

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from opensonar.cli import main  # noqa: E402

SON = os.path.join(ROOT, "samples", "cut1.son")


@unittest.skipUnless(os.path.exists(SON), "samples/cut1.son not available")
class TestCli(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore")

    def test_formats(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["export", SON, d, "--ping", "0"]), 0)
            self.assertEqual(main(["export", SON, d, "--format", "tiff", "--type", "rtheta", "--ping", "1"]), 0)
            self.assertEqual(main(["export", SON, d, "--format", "npz", "--ping", "2"]), 0)
            png = np.array(Image.open(os.path.join(d, "cut1_xy_00000.png")))
            tif = np.array(Image.open(os.path.join(d, "cut1_rtheta_00001.tif")))
            z = np.load(os.path.join(d, "cut1_xy_00002.npz"))
            self.assertEqual(png.dtype, np.uint16)
            self.assertEqual(tif.shape, (271, 768))
            self.assertEqual(z["image"].shape, png.shape)

    def test_invalid_ping(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["export", SON, d, "--ping", "99"]), 1)


if __name__ == "__main__":
    unittest.main()
