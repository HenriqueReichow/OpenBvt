"""Live viewer (no screen: Agg backend), over a file, the emulated sonar and the separate process."""
import dataclasses
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import opensonar  # noqa: E402
from opensonar.viewer import Viewer, crop_to_range, extent, for_display  # noqa: E402

FILE = os.path.join(ROOT, "samples", "exp1.son")
SWIMMER = os.path.join(ROOT, "samples", "swimmer.son")


class TestFunctions(unittest.TestCase):
    def test_extent(self):
        geo = {"res": 0.5, "origin_col": 10, "origin_row": 40, "cols_xy": 21, "rows_xy": 41}
        self.assertEqual(extent(geo), (-5.0, 5.5, -0.5, 20.0))   # the sonar (origin) sits at x=0, y=0

    def test_for_display(self):
        img = np.array([[0, 1, 65535]], np.uint16)
        self.assertEqual(float(for_display(img).max()), 1.0)
        self.assertTrue(np.allclose(for_display(img, log=False), [[0.0, 1 / 65535, 1.0]]))
        self.assertEqual(for_display(np.zeros((2, 2), np.uint16)).max(), 0)


@unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
class TestCrop(unittest.TestCase):
    def test_crops_to_the_ping_range_without_losing_pixels(self):
        with opensonar.open(FILE) as s:
            h = s.head(0)
            p = h.ping(0)
            short = dataclasses.replace(p, range_stop=p.range_stop / 2)
            for ping in (p, short):
                img, geo = h.image(ping, "xy")
                c, g = crop_to_range(img, geo, ping.range_stop)
                self.assertLessEqual(c.shape[0], img.shape[0])
                self.assertEqual(int(c.astype(np.int64).sum()), int(img.astype(np.int64).sum()))  # nothing lost
                r0, c0 = geo["origin_row"] - g["origin_row"], geo["origin_col"] - g["origin_col"]
                self.assertTrue(np.array_equal(c, img[r0:r0 + g["rows_xy"], c0:c0 + g["cols_xy"]]))
                rmax = float(np.sqrt(g["X"] ** 2 + g["Y"] ** 2).max())
                self.assertLess(rmax, np.hypot(ping.range_stop, ping.range_stop) + 2 * g["res"])
            img, geo = h.image(short, "xy")
            _, g = crop_to_range(img, geo, short.range_stop)
            self.assertLess(g["rows_xy"] * g["res"], short.range_stop + 2 * g["res"])   # height in metres
            self.assertLess(g["rows_xy"], geo["rows_xy"])


def _key(v, k):
    v._key(SimpleNamespace(key=k))


@unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
class TestFile(unittest.TestCase):
    def test_shows_the_ping_image_and_keys(self):
        received = []
        with opensonar.open(FILE) as s, tempfile.TemporaryDirectory() as d:
            v = Viewer(s, 0, on_ping=received.append, real_time=False, png_dir=d, show=False)
            p = None
            while p is None:
                p = v.update(timeout=5)
            img, _ = crop_to_range(*s.head(0).image(p, "xy"), p.range_stop)
            self.assertTrue(np.array_equal(v.last_image, img))
            self.assertTrue(np.allclose(v._im.get_array(), for_display(img)))
            _key(v, "s")                                         # lossless 16-bit PNG
            from PIL import Image
            saved = np.asarray(Image.open(os.path.join(d, f"fan_{p.number:06d}.png")))
            self.assertTrue(np.array_equal(saved, img))
            _key(v, "c")
            self.assertEqual(v._im.get_cmap().name, "copper")
            _key(v, "l")
            self.assertFalse(v.log)
            _key(v, " ")
            self.assertIsNone(v.update(timeout=0.1))             # paused
            _key(v, " ")
            v.close()
        numbers = [q.number for q in received]
        self.assertEqual(numbers, list(range(len(numbers))))     # on_ping sees all of them, in order

    def test_run_ends_at_end_of_file(self):
        with opensonar.open(FILE) as s:
            h = s.head(0)
            h.ping(h.n_pings - 6)                                # only the last 5
            v = Viewer(s, 0, real_time=False, show=False)
            n = v.run()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(v.last_ping.number, h.n_pings - 1)


@unittest.skipUnless(os.path.exists(SWIMMER), "no samples/swimmer.son")
class TestSeparateProcess(unittest.TestCase):
    def test_records_everything_and_shows(self):
        from opensonar.process import RemoteSonar
        from opensonar import SonFile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.son")
            s = RemoteSonar(SWIMMER, record=path)
            self.assertFalse(s.live)
            self.assertEqual(s.head(0).model, "MB2250-45")
            v = Viewer(s, 0, show=False)
            n = v.run()                                          # until the end of the file
            s.close()
            self.assertGreaterEqual(n, 1)
            orig, rec = SonFile(SWIMMER), SonFile(path)
            self.assertEqual(rec.n_pings(), orig.n_pings())      # all recorded, even when skipped on screen
            p = v.last_ping
            self.assertTrue(np.array_equal(v.last_image, crop_to_range(*s.head(0).image(p), p.range_stop)[0]))
            self.assertTrue(np.array_equal(v.last_image, crop_to_range(*opensonar.xy(orig.heads[0], orig.ping(p.number)),
                                                                       p.range_stop)[0]))

    def test_connection_error_reaches_the_window(self):
        from opensonar.process import RemoteSonar
        t = socket.socket()
        t.bind(("127.0.0.1", 0))
        port = t.getsockname()[1]
        t.close()
        with self.assertRaises(opensonar.OpenSonarError):
            RemoteSonar(f"127.0.0.1:{port}")


@unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
class TestLive(unittest.TestCase):
    def test_emulator(self):
        from sonar_emulator import Emulator
        emu = Emulator(FILE, compress_signal=True)
        emu.quiet = True
        threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
        while emu.tcp_port is None:
            time.sleep(0.01)
        received = []
        with opensonar.open(f"127.0.0.1:{emu.tcp_port}") as s:
            v = Viewer(s, 0, on_ping=received.append, show=False)
            seen = []
            while len(seen) < 4:
                p = v.update(timeout=5)
                if p is not None:
                    seen.append(p.number)
                    time.sleep(0.2)                              # screen slower than acquisition
            self.assertEqual(seen, sorted(seen))
            self.assertGreater(len(received), len(seen))         # pings skipped on screen, not in recording
            numbers = [q.number for q in received]
            self.assertEqual(numbers, list(range(numbers[0], numbers[0] + len(numbers))))
            _key(v, "down")                                      # range −20 %
            stop = v.last_ping.range_stop / 1.2
            t = time.time()
            while time.time() - t < 10:
                p = v.update(timeout=5)
                if p is not None and abs(p.range_stop - stop) < 0.05:
                    break
            self.assertAlmostEqual(v.last_ping.range_stop, stop, delta=0.05)
            v.close()


if __name__ == "__main__":
    unittest.main()
