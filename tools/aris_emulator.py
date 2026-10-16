"""Emulated sonar for the .aris live protocol (docs/ARIS_LIVE.md): replays the frames of a .aris file.

- Beacons: availability every second to UDP `beacon_port` at `beacon_host`.
- Commands: TCP; the settings received are kept in `settings` (they do not change the replayed data).
- Frames: once a receiver port and the acoustic settings are known, the file frames (looping) are sent
  to it in parts, with the samples unordered as a real sonar sends them. It stops when no command
  (keepalive) arrives for `silence` s.

Usage: python tools/aris_emulator.py FILE.aris [--port 56888] [--beacon-host 255.255.255.255] [-v]
"""
import argparse
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from opensonar.formats import aris  # noqa: E402
from opensonar.aris_net import (ACOUSTIC_FIELDS, BEACON_PORT, COMMAND_PORT, PING,  # noqa: E402
                                SET_ACOUSTICS, SET_FRAMESTREAM_RECEIVER, decode, encode)

PART_SIZE = 1400                             # samples per frame part (fits a 1500-byte datagram)


class Emulator:
    def __init__(self, path, verbose=False, silence=3.0, frame_rate=None):
        self.file = aris.ArisFile(path)
        h = self.file.first
        self.system_type, self.serial = h["system_type"], h["serial"]
        self.verbose, self.silence, self.frame_rate = verbose, silence, frame_rate
        self.settings, self.commands = {}, []
        self.receiver = None
        self.tcp_port = None
        self.sent = 0
        self._last_command = 0.0
        self._stop = threading.Event()

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    # --- commands ---
    def serve_tcp(self, port=COMMAND_PORT, host=""):
        srv = socket.create_server((host, port), reuse_port=False)
        self.tcp_port = srv.getsockname()[1]
        srv.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            self.log("client", addr)
            threading.Thread(target=self._client, args=(conn, addr), daemon=True).start()
        srv.close()

    def _client(self, conn, addr):
        conn.settimeout(0.5)
        buf = b""
        stream = None
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 4 and len(buf) >= 4 + struct.unpack(">I", buf[:4])[0]:
                n = struct.unpack(">I", buf[:4])[0]
                self._command(decode(buf[4:4 + n]), addr)
                buf = buf[4 + n:]
                if stream is None and self.receiver and self.settings:
                    stream = threading.Thread(target=self._stream, daemon=True)
                    stream.start()
        conn.close()

    def _command(self, msg, addr):
        kind = msg.get(1, 0)
        self._last_command = time.time()
        self.commands.append(kind)
        if kind == SET_FRAMESTREAM_RECEIVER:
            f = decode(msg.get(3, b""))
            ip = f[2].decode() if 2 in f else addr[0]
            self.receiver = (ip, f.get(1, 0))
            self.log("receiver", self.receiver)
        elif kind == SET_ACOUSTICS:
            f = decode(msg.get(5, b""))
            for name, (num, k) in ACOUSTIC_FIELDS.items():
                if num in f:
                    self.settings[name] = struct.unpack("<f", f[num])[0] if k == "f" else f[num]
            self.log("acoustics", self.settings)
        elif kind != PING:
            self.log("command", kind)

    # --- frames ---
    def _stream(self):
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rate = self.frame_rate or self.settings.get("frame_rate") or 10.0
        i = 0
        while not self._stop.is_set() and time.time() - self._last_command < self.silence:
            self._send_frame(out, i % self.file.n_frames, i + 1)
            i += 1
            time.sleep(1.0 / rate)
        self.log("stream stopped")
        out.close()

    def _send_frame(self, out, k, index):
        self.file._seek(k)
        raw = self.file._f.read(self.file.frame_size)
        header = bytearray(raw[:aris.HEADER_SIZE])
        struct.pack_into("<I", header, 0, index)
        struct.pack_into("<Q", header, 4, int(time.time() * 1e6))
        struct.pack_into("<I", header, 516, 0)                       # live samples are unordered
        h = aris.read_frame_header(raw)
        n = h["samples_per_beam"]
        ordered = aris.np.frombuffer(raw, aris.np.uint8, offset=aris.HEADER_SIZE).reshape(n, -1)
        data = aris.unorder(ordered, h["ping_mode"])
        for off in range(0, len(data), PART_SIZE):
            fields = [(1, index), (2, len(data))]
            if off == 0:
                fields.append((3, bytes(header)))
            fields += [(4, data[off:off + PART_SIZE]), (5, off)]
            out.sendto(encode(fields), self.receiver)
        self.sent += 1

    # --- beacons ---
    def serve_beacons(self, host="255.255.255.255", port=BEACON_PORT, period=1.0):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not self._stop.is_set():
            busy = int(self.receiver is not None)
            s.sendto(encode([(1, self.serial), (2, self.system_type), (4, busy)]), (host, port))
            self._stop.wait(period)
        s.close()

    def stop(self):
        self._stop.set()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--port", type=int, default=COMMAND_PORT)
    ap.add_argument("--beacon-host", default="255.255.255.255")
    ap.add_argument("--beacon-port", type=int, default=BEACON_PORT)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    emu = Emulator(a.file, verbose=True if a.verbose else False)
    threading.Thread(target=emu.serve_beacons, args=(a.beacon_host, a.beacon_port), daemon=True).start()
    print(f"emulated .aris sonar (type {emu.system_type}, serial {emu.serial}) on TCP {a.port}", flush=True)
    try:
        emu.serve_tcp(a.port)
    except KeyboardInterrupt:
        emu.stop()


if __name__ == "__main__":
    main()
