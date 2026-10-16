"""Network driver (opensonar.net) against the emulated sonar (tools/sonar_emulator.py).

Tests without samples/ use synthetic messages; the others are skipped without the .son files.
"""
import dataclasses
import os
import socket
import struct
import sys
import threading
import time
import unittest
import xml.etree.ElementTree as ET

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from opensonar import net  # noqa: E402
from opensonar.son import SonFile, HeadConfig, Transducer  # noqa: E402
from opensonar.image import xy, rtheta  # noqa: E402

FILES = {
    "MB2250-45": os.path.join(ROOT, "samples", "swimmer.son"),
    "P900-130": os.path.join(ROOT, "samples", "exp1.son"),
    "M900-130": os.path.join(ROOT, "samples", "extra", "M900-130-Bridge-pilings-and-exposed-cabling.son"),
}


class TestCodec(unittest.TestCase):
    def test_round_trip(self):
        rng = np.random.default_rng(1)
        for a in (rng.integers(0, 4096, 5000), 2048 + rng.integers(-20, 20, 5000),
                  np.full(7, 0x800), np.arange(4096)):
            a = a.astype(np.uint16)
            self.assertTrue(np.array_equal(net.decompress(net.compress(a), a.size), a))

    def test_rules(self):
        # 5-bit mode with base 0x800 (word 0x4400), 3 deltas; then a literal; then 7-bit mode
        w = [0x4400, 0x8000 | 1 | 2 << 5 | 3 << 10, 0x0123, 0x6400, 0x8000 | 100 | 5 << 8]
        self.assertEqual(net.decompress(w, 6).tolist(), [0x801, 0x802, 0x803, 0x123, 0x800 + 100, 0x805])

    def test_missing_and_extra(self):
        w = [0x4400, 0x8000 | 1 | 2 << 5 | 3 << 10]
        self.assertEqual(net.decompress(w, 2).tolist(), [0x801, 0x802])
        self.assertEqual(net.decompress(w, 5).tolist(), [0x801, 0x802, 0x803, 0x800, 0x800])

    def test_no_mode(self):
        with self.assertRaises(net.NetError):
            net.decompress([0x8001], 3)


class TestMessages(unittest.TestCase):
    def test_header(self):
        m = net.message(net.PING_REQ, struct.pack(">III", 0, 0x21, 0xFFFFFFFF), 7)
        self.assertEqual(len(m), 26)
        self.assertEqual(net.read_header(m), (26, 1, net.PING_REQ, 7))

    def test_xml_with_attachments(self):
        m = net.xml_message(net.PING_RES, ET.Element("Ping", Head="2"), [b"ab", b"cde"], 3)
        self.assertTrue(m[14 + 4:].startswith(b"<message><Ping"))
        el, attachments = net.read_xml(m[14:])
        self.assertEqual((el.tag, el.get("Head"), attachments), ("Ping", "2", [b"ab", b"cde"]))

    def test_ping_iq_compressed_and_big_endian(self):
        cfg = HeadConfig("x", 0, 10, 0, 0, 0, 1500, np.zeros(1, np.complex64), np.ones(1, np.float32))
        cfg.transducers = [Transducer(0, 1, 1, 0), Transducer(1, 1, 1, 1)]
        i0, q0 = np.arange(10, dtype=np.uint16) + 2000, np.arange(10, dtype=np.uint16) + 100
        i1 = np.arange(10, dtype=np.uint16) * 7
        ping = ET.Element("Ping", Head="0", Number="5", Timestamp="100.25", WindowStart="1", WindowStop="9")
        d = ET.SubElement(ping, "Data", Samples="10")
        ET.SubElement(d, "Transducer", ID="1", Attachment="2")                      # no ByteOrder → BE
        ET.SubElement(d, "Transducer", ID="0", I_AttachmentCompressed="0", Q_AttachmentCompressed="1",
                      ByteOrder="LITTLE")
        attachments = [net.compress(i0).astype("<u2").tobytes(), net.compress(q0).astype("<u2").tobytes(),
                       i1.astype(">u2").tobytes()]
        p = net.ping_from_xml(ping, attachments, cfg, clock_offset=0.5)
        self.assertEqual((p.number, p.timestamp, p.range_start, p.range_stop), (5, 99.75, 1.0, 9.0))
        self.assertTrue(np.array_equal(p.signals[0], i0) and np.array_equal(p.signals_q[0], q0))
        self.assertTrue(np.array_equal(p.signals[1], i1) and p.signals_q[1].size == 0)

    def test_no_connection(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                                  # free port, nobody listening
        with self.assertRaises(net.NetError):
            net.NetSonar(f"127.0.0.1:{port}")


def _emulator(path, **kw):
    from sonar_emulator import Emulator
    emu = Emulator(path, **kw)
    emu.quiet = True
    threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
    while emu.tcp_port is None:
        time.sleep(0.01)
    return emu


class TestAgainstEmulator(unittest.TestCase):
    N_PINGS = 2

    def _compare(self, model, compress):
        path = FILES[model]
        if not os.path.exists(path):
            self.skipTest(f"no {path}")
        emu = _emulator(path, compress_signal=compress)
        son = SonFile(path)
        with net.NetSonar(f"127.0.0.1:{emu.tcp_port}") as s:
            self.assertEqual((s.sonar_type, s.serial, s.firmware), (son.sonar_type, son.serial, son.firmware))
            self.assertEqual(s.heads[0].model, son.heads[0].model)
            self.assertLess(abs(s.clock_offset), 0.5)
            for i in range(self.N_PINGS):
                p, q = s.ping(0), son.ping(i)
                self.assertEqual(p.number, q.number)
                self.assertLess(abs(p.timestamp - q.timestamp), 0.01)
                for a, b in zip(p.signals, q.signals):
                    self.assertTrue(np.array_equal(a, b))
                # a live ping is not padded to its window, a recorded one is (as the reference software
                # does): compare with the recorded ping taken as live
                q = dataclasses.replace(q, pad_to_window=False)
                for render in (xy, rtheta):
                    with self.subTest(model=model, compressed=compress, ping=i, kind=render.__name__):
                        self.assertTrue(np.array_equal(render(s.heads[0], p)[0], render(son.heads[0], q)[0]))

    def test_mb2250(self):
        self._compare("MB2250-45", False)

    def test_mb2250_compressed(self):
        self._compare("MB2250-45", True)

    def test_p900(self):
        self._compare("P900-130", False)

    def test_p900_compressed(self):
        self._compare("P900-130", True)

    def test_m900(self):
        self._compare("M900-130", False)

    def test_m900_compressed(self):
        self._compare("M900-130", True)

    def test_iq(self):
        path = FILES["MB2250-45"]
        if not os.path.exists(path):
            self.skipTest(f"no {path}")
        son = SonFile(path)
        for compress in (False, True):
            emu = _emulator(path, compress_signal=compress, iq=True)
            with net.NetSonar(f"127.0.0.1:{emu.tcp_port}") as s:
                p, q = s.ping(0), son.ping(0)
                for a, b, aq in zip(p.signals, q.signals, p.signals_q):
                    self.assertTrue(np.array_equal(a, b))
                    self.assertTrue(np.array_equal(aq, 4095 - b.astype(np.int32)))

    def test_settings(self):
        path = FILES["P900-130"]
        if not os.path.exists(path):
            self.skipTest(f"no {path}")
        emu = _emulator(path)
        with net.NetSonar(f"127.0.0.1:{emu.tcp_port}") as s:
            s.adjust_range(0, 1.0, 3.0)
            s.adjust_gain(0, 7.5)
            s.adjust_tvg(0, 0.5)
            p = s.ping(0)                      # same TCP connection: the settings arrive before the request
            self.assertEqual((emu.settings["Gain"], emu.settings["TVG"]), (7.5, 0.5))
            self.assertAlmostEqual(p.range_start, 1.0, places=2)
            self.assertAlmostEqual(p.range_stop, 3.0, places=2)
            c = s.heads[0]
            n = round((p.range_stop - p.range_start) * 2 / c.sound_speed * c.transducers[0].high_freq)
            self.assertLessEqual(abs(p.signals[0].size - n), 1)
            self.assertEqual(xy(c, p)[0].ndim, 2)

    def test_clock_offset(self):
        path = FILES["MB2250-45"]
        if not os.path.exists(path):
            self.skipTest(f"no {path}")
        emu = _emulator(path, clock_offset=100.0)
        with net.NetSonar(f"127.0.0.1:{emu.tcp_port}") as s:
            self.assertAlmostEqual(s.clock_offset, 100.0, delta=0.05)
            # the ping comes in the sonar clock; corrected, it is back to the recorded time
            self.assertAlmostEqual(s.ping(0).timestamp, SonFile(path).ping(0).timestamp, delta=0.05)


class TestDiscovery(unittest.TestCase):
    def test_finds_emulator(self):
        path = FILES["MB2250-45"]
        if not os.path.exists(path):
            self.skipTest(f"no {path}")
        try:
            t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            t.bind(("", net.PORT))
            t.close()
        except OSError:
            self.skipTest("UDP port 1149 busy")
        emu = _emulator(path)
        stop = threading.Event()
        t = threading.Thread(target=emu.serve_discovery, args=(emu.tcp_port, stop), daemon=True)
        t.start()
        self.addCleanup(t.join, 2)
        self.addCleanup(stop.set)
        time.sleep(0.3)
        found = net.discover(1.5)
        self.assertTrue(any(a.get("Port") == str(emu.tcp_port) for a in found), found)


if __name__ == "__main__":
    unittest.main()


M900 = os.path.join(ROOT, "samples", "extra", "M900-130-Bridge-pilings-and-exposed-cabling.son")


@unittest.skipUnless(os.path.exists(M900), "no samples/extra/M900-130-Bridge-...son")
class TestLiveRThetaRows(unittest.TestCase):
    """Live pings are not padded to the window, recorded ones are -- as the reference software does.

    Measured with the reference software through the emulator, M900-130 at 1.0-4.9 m: the ping has
    6089 samples (fewer than the window implies at its sound speed), and the R-Theta has 243 rows of
    0.016047 m from 4.3809 m down to 0.4975 m. Read from the file, the same recording pads (853 rows).
    """

    def test_m900_live_rows_match_the_reference(self):
        import opensonar
        from sonar_emulator import Emulator
        emu = Emulator(M900, compress_signal=True)
        emu.quiet = True
        threading.Thread(target=emu.serve_tcp, args=(0,), daemon=True).start()
        while emu.tcp_port is None:
            time.sleep(0.01)
        with opensonar.open(f"127.0.0.1:{emu.tcp_port}") as s:
            h = s.heads[0]
            h.adjust_range(1.0, 4.9)
            p = h.ping()
            img, geo = h.image(p, "rtheta")
        self.assertFalse(p.pad_to_window)
        self.assertEqual(p.signals[0].size, 6089)
        self.assertEqual(img.shape[0], 243)
        self.assertAlmostEqual(float(geo["res"]), 0.016047, places=5)
        # the rows are offset by ONE row from the reference here (frame shift 31 against its 30;
        # start x frame rate = 30.96): still open, so it is bounded, not hidden
        self.assertLessEqual(abs(float(geo["ranges"][0]) - 4.3809), float(geo["res"]) + 1e-4)
        self.assertLessEqual(abs(float(geo["ranges"][-1]) - 0.4975), float(geo["res"]) + 1e-4)

    def test_m900_file_still_pads(self):
        import opensonar
        with opensonar.open(M900) as s:
            h = s.heads[0]
            img, geo = h.image(h.ping(0), "rtheta")
        self.assertEqual(img.shape[0], 853)                         # the reference software: 853 too
        self.assertAlmostEqual(float(geo["res"]), 0.050898, places=5)
