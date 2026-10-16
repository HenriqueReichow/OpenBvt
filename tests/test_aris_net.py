"""Live .aris driver against the emulator (tools/aris_emulator.py), over a synthetic recording."""
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
sys.path.insert(0, os.path.dirname(__file__))
import opensonar  # noqa: E402
from opensonar import aris_net  # noqa: E402
from opensonar.formats import aris  # noqa: E402
from test_aris import write_aris  # noqa: E402


class TestCodec(unittest.TestCase):
    def test_round_trip(self):
        msg = aris_net.encode([(1, 300), (2, 2.5), (3, b"abc"), (4, "x")])
        f = aris_net.decode(msg)
        self.assertEqual(f[1], 300)
        self.assertEqual(struct.unpack("<f", f[2])[0], 2.5)
        self.assertEqual((f[3], f[4]), (b"abc", b"x"))

    def test_command_prefix(self):
        c = aris_net.command(aris_net.PING)
        self.assertEqual(struct.unpack(">I", c[:4])[0], len(c) - 4)
        self.assertEqual(aris_net.decode(c[4:])[1], aris_net.PING)

    def test_reorder_inverts_unorder(self):
        for mode in (1, 3, 6, 9):
            n = aris.beams_from_ping_mode(mode)
            a = np.random.default_rng(mode).integers(0, 256, (37, n), dtype=np.uint8)
            raw = aris.unorder(a, mode)
            self.assertNotEqual(raw, a.tobytes())
            np.testing.assert_array_equal(aris.reorder(raw, mode, 37), a)


class TestLive(unittest.TestCase):
    def setUp(self):
        from aris_emulator import Emulator
        self.dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.dir.name, "t.aris")
        rng = np.random.default_rng(1)
        self.data = [rng.integers(0, 256, (300, 48), dtype=np.uint8) for _ in range(4)]
        write_aris(path, self.data, system_type=2)
        self.emu = Emulator(path, frame_rate=50)
        threading.Thread(target=self.emu.serve_tcp, args=(0, "127.0.0.1"), daemon=True).start()
        while self.emu.tcp_port is None:
            time.sleep(0.01)

    def tearDown(self):
        self.emu.stop()
        self.dir.cleanup()

    def open(self, **kw):
        kw = dict(dict(system_type=2, check_busy=False, frame_port=0), **kw)
        return opensonar.open("127.0.0.1", family="aris", command_port=self.emu.tcp_port, **kw)

    def test_frames_arrive_ordered(self):
        with self.open() as s:
            h = s.heads[0]
            self.assertEqual((s.model, s.serial), ("1200", 42))
            frames = list(h.pings(n=5))
        got = {f.number: f for f in frames}
        for f in frames:
            # the emulator numbers frames from 1 and loops over the file
            np.testing.assert_array_equal(f.intensity, self.data[(f.number - 1) % 4][:, ::-1])
        self.assertGreaterEqual(len(got), 5)
        self.assertEqual(self.emu.commands[:5], [aris_net.SET_FRAMESTREAM_RECEIVER, aris_net.SET_DATETIME,
                                                 aris_net.SET_ACOUSTICS, aris_net.SET_SALINITY,
                                                 aris_net.SET_FOCUS])

    def test_adjust_range_and_gain(self):
        with self.open() as s:
            h = s.heads[0]
            h.adjust_range(2.0, 10.0)
            h.adjust_gain(7)
            time.sleep(0.2)
        st = self.emu.settings
        self.assertEqual(st["cookie"], 3)
        self.assertAlmostEqual(st["receiver_gain"], 7.0)
        self.assertEqual(st["sample_start_delay"], round(2 * 2.0 / 1500 * 1e6))
        self.assertGreaterEqual(st["sample_period"] * st["samples_per_beam"] * 1500 / 2e6, 8.0)
        # the validity rules of the settings
        self.assertGreaterEqual(st["cycle_period"],
                                st["sample_start_delay"] + st["sample_period"] * st["samples_per_beam"] + 360)
        self.assertGreater(1e6 / st["frame_rate"], st["cycle_period"] * 3)
        self.assertTrue(128 <= st["samples_per_beam"] <= 4096 and 4 <= st["sample_period"] <= 100)

    def test_long_range_raises_the_sample_period(self):
        with self.open() as s:
            s.heads[0].adjust_range(1.0, 80.0)
            time.sleep(0.2)
        st = self.emu.settings
        self.assertEqual(st["samples_per_beam"] * st["sample_period"] >= 2 * 79 / 1500 * 1e6, True)
        self.assertLessEqual(st["samples_per_beam"], 4096)
        self.assertGreaterEqual(st["frame_rate"], 1.0)

    def test_keepalive(self):
        with self.open() as s:
            s.heads[0].ping()
            time.sleep(1.3)
        self.assertIn(aris_net.PING, self.emu.commands)

    def test_keepalive_without_reading_frames(self):
        with self.open() as s:
            time.sleep(2.5)                                    # nobody reads: PING and reception go on
            pings = self.emu.commands.count(aris_net.PING)
            status = s.link_status()
        self.assertGreaterEqual(pings, 2)
        self.assertGreater(status["frames"], 10)
        self.assertLess(status["since_last_frame"], 1.0)

    def test_reconnects_after_a_drop(self):
        with self.open() as s:
            h = s.heads[0]
            h.adjust_gain(9)
            h.ping(timeout=5)
            self.emu.drop()                                    # the sonar closes the command stream
            deadline = time.time() + 6
            while self.emu.clients < 2 and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(self.emu.clients, 2)
            s.drop_queued()
            f = h.ping(timeout=5)                              # frames flow again
            self.assertIsNotNone(f)
            self.assertIn("reconnected", s.warning)
            self.assertEqual(s.link_status()["reconnects"], 1)
        # the starting sequence was sent again, with the settings in use (gain 9, a newer cookie)
        self.assertEqual(self.emu.commands.count(aris_net.SET_FRAMESTREAM_RECEIVER), 2)
        self.assertEqual(self.emu.settings["receiver_gain"], 9.0)
        self.assertEqual(self.emu.settings["cookie"], 3)

    def test_ping_timeout(self):
        with self.open() as s:
            self.emu.stop()                                    # no more frames
            time.sleep(0.3)
            s.drop_queued()
            with self.assertRaises(opensonar.NoPing):
                s.heads[0].ping(timeout=0.5)

    def test_busy_sonar_is_refused(self):
        port = _free_udp_port()
        threading.Thread(target=self.emu.serve_beacons, args=("127.0.0.1", port, 0.1), daemon=True).start()
        with self.open(beacon_port=port, check_busy=True):    # free: connects, and the sonar is now busy
            with self.assertRaises(aris_net.ArisNetError) as e:
                self.open(beacon_port=port, check_busy=True)
            self.assertIn("busy", str(e.exception))
        self.assertEqual(self.emu.refused, 0)                  # refused by us, before connecting

    def test_beacon_discovery(self):
        port = _free_udp_port()
        t = threading.Thread(target=self.emu.serve_beacons, args=("127.0.0.1", port, 0.1), daemon=True)
        t.start()
        found = aris_net.discover(0.5, port)
        self.assertEqual(found, [dict(host="127.0.0.1", serial=42, system_type=2, busy=False)])


def _free_udp_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


if __name__ == "__main__":
    unittest.main()
