"""opensonar service (ZeroMQ) against the emulated sonar: two subscribers, commands and gap-free recording."""
import contextlib
import io
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
import opensonar  # noqa: E402

try:
    import zmq  # noqa: F401
    from opensonar.client import Client, ServerError, Subscriber, decode  # noqa: E402
    from opensonar.server import Server, ping_message  # noqa: E402
except ImportError:
    zmq = None

FILE = os.path.join(ROOT, "samples", "exp1.son")


def _emulator(path):
    from sonar_emulator import Emulator
    emu = Emulator(path, compress_signal=True)
    emu.quiet = True
    threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
    while emu.tcp_port is None:
        time.sleep(0.01)
    return emu


@unittest.skipUnless(zmq is not None, "no pyzmq")
class TestMessage(unittest.TestCase):
    def test_round_trip(self):
        p = opensonar.son.Ping(7, 1791981334.525, -10800, 0.5, 20.0,
                             [np.arange(5, dtype=np.uint16), np.arange(3, dtype=np.uint16)], [])
        geo = {"res": 0.01, "origin_row": 3, "origin_col": 2}
        img = np.arange(12, dtype=np.uint16).reshape(3, 4)
        m = decode(ping_message("2920060", 3, p, 41, (img, geo)))
        self.assertEqual(m.topic, "ping/2920060/3")
        self.assertEqual((m.meta["seq"], m.meta["head"], m.ping.number), (41, 3, 7))
        self.assertEqual(m.meta["utc"], "2026-10-14T12:35:34.525000Z")
        self.assertEqual(m.ping.timestamp, p.timestamp)
        self.assertTrue(all(np.array_equal(a, b) for a, b in zip(m.ping.signals, p.signals)))
        self.assertTrue(np.array_equal(m.image, img))
        self.assertIsNone(decode(ping_message("1", 0, p, 0)).image)


@unittest.skipUnless(zmq is not None, "no pyzmq")
@unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
class TestService(unittest.TestCase):
    def setUp(self):
        self.emu = _emulator(FILE)
        self.srv = Server("127.0.0.1", 0, 0)
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.srv.serve, args=(self.done,), daemon=True)
        self.thread.start()
        self.rep = f"tcp://127.0.0.1:{self.srv.rep_port}"
        self.pub = f"tcp://127.0.0.1:{self.srv.pub_port}"
        self.c = Client(self.rep)

    def tearDown(self):
        self.c.close()
        self.done.set()
        self.thread.join(10)

    def _connect(self):
        return self.c.connect(f"127.0.0.1:{self.emu.tcp_port}")

    def test_errors_are_replies(self):
        with self.assertRaises(ServerError):
            self.c.call("nonsense")
        with self.assertRaises(ServerError):
            self.c.range(0, 1, 5)                                   # not connected yet
        self._connect()
        with self.assertRaises(ServerError):
            self.c.start([5])                                       # the emulator has one head
        self.assertFalse(self.c.status()["running"])

    def test_heads_and_settings(self):
        info = self._connect()
        self.assertEqual(info["serial"], "2058")
        self.assertEqual([h["model"] for h in info["heads"]], ["P900-130"])
        self.c.range(0, 1.0, 5.0)
        self.c.gain(0, 4.0)
        self.c.tvg(0, 0.5)
        with Subscriber(self.pub, head=0) as sub:
            time.sleep(0.3)                                         # SUB connects before the first ping
            self.c.start([0])
            m = sub.recv(timeout=10)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m.ping.range_stop, 5.0, delta=0.05)          # cut at a sample boundary
        self.c.stop()
        self.assertFalse(self.c.status()["running"])

    def test_two_subscribers_and_gap_free_recording(self):
        self._connect()
        n = 40
        got = {"a": [], "b": []}
        with Subscriber(self.pub) as a, Subscriber(self.pub, serial="2058", head=0) as b, \
                tempfile.TemporaryDirectory() as d:
            time.sleep(0.3)
            path = os.path.join(d, "rec.son")
            self.c.record(path)
            self.c.start([0], image=True)
            for name, sub in (("a", a), ("b", b)):
                while len(got[name]) < n:
                    m = sub.recv(timeout=10)
                    self.assertIsNotNone(m, f"subscriber {name} stopped receiving")
                    got[name].append(m)
            self.c.stop()
            st = self.c.status()
            rec = self.c.stop_record()
            self.assertEqual(rec["pings"], st["published"])         # every published ping was recorded
            for name in got:
                seq = [m.meta["seq"] for m in got[name]]
                self.assertEqual(seq, list(range(seq[0], seq[0] + n)), f"subscriber {name} lost pings")
            self.assertEqual([m.meta["seq"] for m in got["a"]], [m.meta["seq"] for m in got["b"]])
            # the recording has every ping, in order, and the same images the subscribers received
            s = opensonar.open(path)
            h = s.head(0)
            self.assertEqual(h.n_pings, rec["pings"])
            numbers = [h.ping(i).number for i in range(h.n_pings)]
            self.assertEqual(numbers, [(numbers[0] + i) % 806 for i in range(h.n_pings)])
            first = got["a"][0]
            k = numbers.index(first.ping.number)
            p = h.ping(k)
            self.assertTrue(all(np.array_equal(x, y) for x, y in zip(p.signals, first.ping.signals)))
            self.assertTrue(np.array_equal(h.image(p, "xy", crop=True)[0], first.image))   # published cropped
            orig = opensonar.open(FILE)
            self.assertTrue(np.array_equal(first.image, orig.head(0).image(orig.head(0).ping(first.ping.number), crop=True)[0]))
            s.close()

    def test_recording_survives_without_subscribers(self):
        self._connect()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rec.son")
            self.c.record(path)
            self.c.start([0])
            time.sleep(1.0)                                         # nobody listening
            self.c.stop()
            rec = self.c.stop_record()
            self.assertGreater(rec["pings"], 3)
            self.assertEqual(opensonar.open(path).head(0).n_pings, rec["pings"])

    def test_record_status_and_event_marks(self):
        from opensonar.client import EventSubscriber
        self._connect()
        with tempfile.TemporaryDirectory() as d, EventSubscriber(self.pub) as ev:
            path = os.path.join(d, "rec.son")
            self.c.record(path)
            self.c.start([0])
            time.sleep(0.6)
            r = self.c.mark("diver at 3 m")
            self.assertEqual(ev.recv(timeout=5)["text"], "diver at 3 m")           # published live
            time.sleep(0.4)
            st = self.c.status()["record"]
            self.assertEqual(st["path"], path)
            self.assertGreater(st["pings"], 2)
            self.assertGreater(st["bytes"], 4096)
            self.assertGreater(st["elapsed"], 0.9)
            self.assertEqual(st["marks"], 1)
            self.c.mark("second")
            self.c.stop()
            rec = self.c.stop_record()
            self.assertEqual((rec["marks"], rec["events"]), (2, os.path.join(d, "rec.events.csv")))
            import csv
            rows = list(csv.DictReader(open(rec["events"])))
            self.assertEqual([x["text"] for x in rows], ["diver at 3 m", "second"])
            self.assertTrue(rows[0]["utc"].endswith("Z") and r["mark"]["utc"] == rows[0]["utc"])
            self.assertLessEqual(int(rows[0]["recorded_pings"]), int(rows[1]["recorded_pings"]))
            self.assertIsNone(self.c.status()["record"])
            self.assertIsNone(self.c.mark("not recording")["file"])

    def test_utc_is_corrected_when_the_sonar_clock_is_in_1970(self):
        import csv
        from sonar_emulator import Emulator
        emu = Emulator(FILE, compress_signal=True, clock_offset=-1791981159.0)   # like the real sonar
        emu.quiet = True
        threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
        while emu.tcp_port is None:
            time.sleep(0.01)
        self.c.connect(f"127.0.0.1:{emu.tcp_port}")
        self.assertLess(self.c.status()["clock_offset"], -1.7e9)
        with tempfile.TemporaryDirectory() as d, Subscriber(self.pub) as sub:
            time.sleep(0.3)
            base = os.path.join(d, "rec")
            self.c.record(base, ["son", "npy"])
            self.c.start([0])
            m = sub.recv(timeout=10)
            self.c.mark("now")
            time.sleep(0.5)
            self.c.stop()
            rec = self.c.stop_record()
            now = time.time()
            # the emulator replays recorded pings with their own times shifted into the sonar clock; once
            # corrected they must be back to the recording's times (2025/2026), never near 1970
            orig = opensonar.open(FILE).head(0)
            t_orig = {orig.ping(i).number: orig.ping(i).timestamp for i in range(orig.n_pings)}
            self.assertAlmostEqual(m.ping.timestamp, t_orig[m.ping.number], delta=5)
            s = opensonar.open(rec["path"]).head(0)
            for i in range(s.n_pings):
                p = s.ping(i)
                self.assertAlmostEqual(p.timestamp, t_orig[p.number], delta=5)            # .son
            rows = list(csv.DictReader(open(base + "_npy/h0/index.csv")))
            self.assertEqual(len(rows), rec["pings"])
            for r in rows:
                self.assertAlmostEqual(float(r["timestamp"]), t_orig[int(r["number"])], delta=5)   # index.csv
                self.assertFalse(r["utc"].startswith("1970"))
            ev = list(csv.DictReader(open(rec["events"])))
            self.assertAlmostEqual(float(ev[0]["timestamp"]), now, delta=5)                # .events.csv: PC clock

    def test_config_rtheta_and_reprocessing(self):
        from opensonar.son import config_from_dict
        self._connect()
        [cfg] = self.c.config()
        self.assertEqual((cfg["model"], cfg["beams"], len(cfg["beam_angles"])), ("P900-130", 768, 768))
        with Subscriber(self.pub) as sub:
            time.sleep(0.3)
            self.c.start([0], image=["xy", "rtheta"])
            m = sub.recv(timeout=10)
            self.c.stop()
        self.assertEqual(sorted(m.images), ["rtheta", "xy"])
        self.assertEqual(m.meta["config_id"], cfg["config_id"])
        rt, im = m.images["rtheta"]
        self.assertEqual((rt.shape, m.meta["bins"], m.meta["beams"]), ((m.meta["bins"], 768), rt.shape[0], 768))
        self.assertAlmostEqual(m.meta["res"], im["res"], places=6)
        # the raw ping + the published configuration rebuild the same images
        from opensonar import image
        rebuilt = config_from_dict(cfg)
        self.assertTrue(np.array_equal(image.rtheta(rebuilt, m.ping)[0], rt))
        self.assertTrue(np.array_equal(image.xy(rebuilt, m.ping, crop=True)[0], m.image))

    def test_ctl_command_line(self):
        from opensonar.cli import main
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["ctl", "--address", self.rep, "connect", f"127.0.0.1:{self.emu.tcp_port}"]), 0)
            self.assertEqual(main(["ctl", "--address", self.rep, "range", "0", "3"]), 0)
            self.assertEqual(main(["ctl", "--address", self.rep, "status"]), 0)
        self.assertIn('"connected": true', buf.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["ctl", "--address", self.rep, "range"]), 1)


@unittest.skipUnless(zmq is not None, "no pyzmq")
@unittest.skipUnless(os.path.exists(FILE), "no samples/exp1.son")
class TestFileSource(unittest.TestCase):
    def test_seek_and_step(self):
        srv = Server("127.0.0.1", 0, 0)
        done = threading.Event()
        t = threading.Thread(target=srv.serve, args=(done,), daemon=True)
        t.start()
        try:
            with Client(f"tcp://127.0.0.1:{srv.rep_port}") as c, \
                    Subscriber(f"tcp://127.0.0.1:{srv.pub_port}") as sub:
                c.connect(FILE)
                time.sleep(0.3)
                orig = opensonar.open(FILE).head(0)
                c.seek(100)
                self.assertEqual(c.step(1)["number"], orig.ping(100).number)         # shows ping 100
                self.assertEqual(sub.recv(timeout=5).ping.number, orig.ping(100).number)
                self.assertEqual(c.step(1)["number"], orig.ping(101).number)
                self.assertEqual(c.step(-1)["number"], orig.ping(100).number)
                self.assertEqual(c.status()["playback"], {"position": 101, "n_pings": 806})
                with self.assertRaises(ServerError):
                    c.seek(0)
                    c.step(-1)                                                       # before the first ping
                c.seek(805)
                c.start([0], speed=None)
                end = time.time() + 10
                while c.status()["running"] and time.time() < end:
                    time.sleep(0.05)
                self.assertEqual(c.status()["playback"]["position"], 806)            # played to the end
        finally:
            done.set()
            t.join(10)

    def test_file_plays_to_the_end(self):
        srv = Server("127.0.0.1", 0, 0)
        done = threading.Event()
        t = threading.Thread(target=srv.serve, args=(done,), daemon=True)
        t.start()
        try:
            with Client(f"tcp://127.0.0.1:{srv.rep_port}") as c:
                c.connect(FILE)
                c.start([0])
                end = time.time() + 30
                while c.status()["running"] and time.time() < end:
                    time.sleep(0.1)
                st = c.status()
            self.assertEqual((st["published"], st["error"]), (806, None))
        finally:
            done.set()
            t.join(10)


if __name__ == "__main__":
    unittest.main()
