"""The service with sonars that hand out frames: a .aris file and the live .aris emulator."""
import os
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
from test_aris import write_aris  # noqa: E402
from opensonar.aris_net import PING  # noqa: E402

try:
    import zmq  # noqa: F401
    from opensonar.client import Client, Subscriber  # noqa: E402
    from opensonar.server import Server  # noqa: E402
except ImportError:
    zmq = None


@unittest.skipUnless(zmq is not None, "no pyzmq")
class TestServiceAris(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.aris")
        rng = np.random.default_rng(2)
        self.data = [rng.integers(0, 256, (200, 48), dtype=np.uint8) for _ in range(6)]
        write_aris(self.path, self.data, system_type=2)
        self.srv = Server("127.0.0.1", 0, 0)
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.srv.serve, args=(self.done,), daemon=True)
        self.thread.start()
        self.c = Client(f"tcp://127.0.0.1:{self.srv.rep_port}")
        self.pub = f"tcp://127.0.0.1:{self.srv.pub_port}"

    def tearDown(self):
        self.c.close()
        self.done.set()
        self.thread.join(10)
        self.dir.cleanup()

    def _receive(self, n, record=None, formats=("son",)):
        with Subscriber(self.pub) as sub:
            time.sleep(0.3)
            if record:
                self.c.record(record, formats)
            self.c.start([0], image=["xy", "rtheta"])
            got = []
            while len(got) < n:
                m = sub.recv(timeout=10)
                self.assertIsNotNone(m, "no ping published")
                got.append(m)
            self.c.stop()
        return got

    def test_file_heads_config_and_images(self):
        info = self.c.connect(self.path)
        h = info["heads"][0]
        self.assertEqual((h["model"], h["n_pings"], h["transducers"]), ("1200", 6, 0))
        cfg = self.c.config()[0]
        self.assertEqual((cfg["family"], cfg["beams"]), ("aris", 48))
        # played as fast as possible: only the newest frame of each batch is rendered
        m = next(m for m in reversed(self._receive(6)) if m.meta["images"])
        self.assertIsNone(self.c.status()["error"])
        self.assertEqual(len(m.meta["images"]), 2)
        self.assertEqual((m.meta["bins"], m.meta["beams"]), (200, 48))
        ref = opensonar.open(self.path).heads[0]
        f = ref.ping(m.ping.number)
        np.testing.assert_array_equal(m.images["rtheta"][0], ref.image(f, "rtheta")[0])
        np.testing.assert_array_equal(m.image, ref.image(f, "xy")[0])
        self.assertAlmostEqual(m.ping.range_start, f.range_start)

    def test_raw_recording_is_aris(self):
        self.c.connect(self.path)
        rec_path = os.path.join(self.dir.name, "rec.son")
        self._receive(4, rec_path, ("son", "npz"))
        rec = self.c.stop_record()
        self.assertTrue(rec["path"].endswith("rec.aris"))
        with opensonar.open(rec["path"]) as s:
            frames = list(s.heads[0].pings())
        self.assertEqual(len(frames), rec["pings"])
        for f in frames:
            np.testing.assert_array_equal(f.intensity, self.data[f.number][:, ::-1])
        self.assertTrue(os.path.isdir(os.path.join(self.dir.name, "rec_npz")))

    def test_live_emulator(self):
        from aris_emulator import Emulator
        emu = Emulator(self.path, frame_rate=40)
        threading.Thread(target=emu.serve_tcp, args=(0, "127.0.0.1"), daemon=True).start()
        while emu.tcp_port is None:
            time.sleep(0.01)
        try:
            info = self.c.connect("127.0.0.1", family="aris", system_type=2, command_port=emu.tcp_port,
                                  check_busy=False, frame_port=0)
            self.assertTrue(info["live"])
            self.c.range(0, 2.0, 8.0)
            self.c.gain(0, 10)
            got = self._receive(5)
            self.assertEqual(emu.settings["receiver_gain"], 10.0)
            self.assertEqual(got[-1].meta["gain"], 10.0)
            self.assertIsNone(self.c.status()["error"])
            self.assertTrue(any(m.image is not None for m in got))      # rendering may skip older ones
        finally:
            emu.stop()
            self.c.disconnect()


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(zmq is not None, "no pyzmq")
class TestServiceArisLink(unittest.TestCase):
    """The service as the only client: keepalive without subscribers, reconnection reported in status."""

    def setUp(self):
        from aris_emulator import Emulator
        self.dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.dir.name, "t.aris")
        write_aris(path, [np.zeros((200, 48), np.uint8)] * 3, system_type=2)
        self.emu = Emulator(path, frame_rate=20)
        threading.Thread(target=self.emu.serve_tcp, args=(0, "127.0.0.1"), daemon=True).start()
        while self.emu.tcp_port is None:
            time.sleep(0.01)
        self.srv = Server("127.0.0.1", 0, 0)
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.srv.serve, args=(self.done,), daemon=True)
        self.thread.start()
        self.c = Client(f"tcp://127.0.0.1:{self.srv.rep_port}")

    def tearDown(self):
        self.c.disconnect()
        self.c.close()
        self.done.set()
        self.thread.join(10)
        self.emu.stop()
        self.dir.cleanup()

    def test_keepalive_and_reconnection(self):
        self.c.connect("127.0.0.1", family="aris", system_type=2, command_port=self.emu.tcp_port,
                       check_busy=False, frame_port=0)
        time.sleep(2.5)                                        # not started, no subscriber, no GUI
        st = self.c.status()
        self.assertGreaterEqual(self.emu.commands.count(PING), 2)
        self.assertGreater(st["link"]["frames"], 10)
        self.assertIsNone(st["warning"])
        self.emu.drop()
        deadline = time.time() + 6
        while time.time() < deadline and "reconnected" not in (self.c.status()["warning"] or ""):
            time.sleep(0.1)
        st = self.c.status()
        self.assertIn("reconnected", st["warning"])
        self.assertEqual(st["link"]["reconnects"], 1)
        self.assertEqual(self.emu.clients, 2)
