"""Single API (opensonar.open): the same code over a .son file and over the emulated sonar."""
import os
import socket
import sys
import threading
import time
import unittest

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import opensonar  # noqa: E402
from opensonar import ApiError, OpenSonarError  # noqa: E402

FILE = os.path.join(ROOT, "samples", "exp1.son")


def _emulator(path, **kw):
    from sonar_emulator import Emulator
    emu = Emulator(path, **kw)
    emu.quiet = True
    threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
    while emu.tcp_port is None:
        time.sleep(0.01)
    return emu


class TestWithoutData(unittest.TestCase):
    def test_errors_share_a_base(self):
        self.assertTrue(issubclass(opensonar.SonError, OpenSonarError))
        self.assertTrue(issubclass(ApiError, OpenSonarError))
        from opensonar.net import NetError
        self.assertTrue(issubclass(NetError, OpenSonarError))

    def test_missing_file(self):
        with self.assertRaises(OSError):
            opensonar.open(os.path.join(ROOT, "does_not_exist.son"))

    def test_network_without_sonar(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(OpenSonarError):
            opensonar.open(f"127.0.0.1:{port}")


@unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
class TestContract(unittest.TestCase):
    """A file and an emulator fed by the same file must behave the same."""

    def setUp(self):
        self.emu = _emulator(FILE, compress_signal=True)
        self.file = opensonar.open(FILE)
        self.live = opensonar.open(f"127.0.0.1:{self.emu.tcp_port}")

    def tearDown(self):
        self.file.close()
        self.live.close()

    def test_metadata(self):
        a, v = self.file, self.live
        self.assertFalse(a.live)
        self.assertTrue(v.live)
        self.assertEqual((a.sonar_type, a.serial, a.firmware, a.model), (v.sonar_type, v.serial, v.firmware, v.model))
        self.assertEqual(len(a.heads), len(v.heads))
        ha, hv = a.head(0), v.head(0)
        self.assertEqual((ha.model, ha.limits, ha.sound_speed, ha.center_freq, len(ha.transducers)),
                         (hv.model, hv.limits, hv.sound_speed, hv.center_freq, len(hv.transducers)))
        self.assertEqual(ha.n_pings, self.file._son.n_pings())
        self.assertIsNone(hv.n_pings)
        with self.assertRaises(ApiError):
            a.head(5)

    def test_same_images(self):
        ha, hv = self.file.head(0), self.live.head(0)
        for pa, pv in zip(ha.pings(n=3), hv.pings(n=3)):
            self.assertEqual(pa.number, pv.number)
            for kind in ("xy", "rtheta"):
                with self.subTest(ping=pa.number, kind=kind):
                    self.assertTrue(np.array_equal(ha.image(pa, kind)[0], hv.image(pv, kind)[0]))

    def test_file_random_access_and_end(self):
        h = self.file.head(0)
        self.assertEqual(h.ping(5).number, 5)
        self.assertEqual(h.ping().number, 6)            # the next one continues from the last
        n = h.n_pings
        self.assertEqual(len(list(h.pings(start=n - 2))), 2)
        with self.assertRaises(ApiError):
            h.ping(n)
        with self.assertRaises(ApiError):
            h.image(h.ping(0), "3d")

    def test_settings(self):
        with self.assertRaises(ApiError):
            self.file.head(0).adjust_range(1, 3)
        h = self.live.head(0)
        h.adjust_range(0.0, 3.0)                        # the start is clamped to the minimum range
        h.adjust_gain(6.0)
        h.adjust_tvg(0.5)
        p = h.ping()
        self.assertAlmostEqual(p.range_stop, 3.0, places=2)
        self.assertAlmostEqual(p.range_start, h.limits[0], places=4)
        self.assertEqual((self.emu.settings["Gain"], self.emu.settings["TVG"]), (6.0, 0.5))
        self.assertEqual((h.gain, h.tvg), (6.0, 0.5))

    def test_stream_on_demand(self):
        h = self.live.head(0)
        with h.stream() as f:                           # default without on_ping: one ping per read
            p1 = f.latest(timeout=10)
            time.sleep(0.5)
            p2 = f.latest(timeout=10)
            self.assertLessEqual(p2.number - p1.number, 2)
            self.assertLessEqual(f.received, 3)

    def test_live_stream_hands_out_the_latest(self):
        h = self.live.head(0)
        with h.stream(on_demand=False) as f:
            p1 = f.latest(timeout=10)
            time.sleep(0.5)                             # several pings arrive while nobody consumes
            p2 = f.latest(timeout=10)
            self.assertIsNotNone(p1)
            self.assertGreater(p2.number, p1.number + 1)
            self.assertGreater(f.received, 2)

    def test_file_stream_ends(self):
        h = self.file.head(0)
        h.ping(h.n_pings - 4)                           # the next one becomes n − 3
        with h.stream() as f:
            seen = [p.number for p in f]
        self.assertTrue(seen and seen[-1] == h.n_pings - 1)
        self.assertFalse(f.active)


class TestDiscovery(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
    def test_open_without_source(self):
        try:
            t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            t.bind(("", 1149))
            t.close()
        except OSError:
            self.skipTest("UDP port 1149 busy")
        emu = _emulator(FILE)
        stop = threading.Event()
        t = threading.Thread(target=emu.serve_discovery, args=(emu.tcp_port, stop), daemon=True)
        t.start()
        self.addCleanup(t.join, 2)
        self.addCleanup(stop.set)
        time.sleep(0.3)
        from opensonar.net import discover
        found = [a for a in discover(1.5) if int(a.get("Port", 1149)) == emu.tcp_port]   # only the emulator
        self.assertEqual(len(found), 1)                                                # (a real sonar may answer too)
        with opensonar.open(f"{found[0]['Host']}:{found[0]['Port']}") as s:
            self.assertTrue(s.live)
            self.assertEqual(s.head(0).model, "P900-130")


if __name__ == "__main__":
    unittest.main()
