"""Emulated P900/M900 sonar: speaks the network protocol (docs/PROTOCOL.md) and sends the pings of a .son.

- Discovery: UDP 1149 on group 224.192.45.34 (DiscoveryRequest → DiscoveryResponse).
- Control: TCP 1149. SettingsRequest → SettingsUpdate with the <Sonar> XML built from the .son;
  client SettingsUpdate (range, gain, TVG…) → applied to the state; PingRequest → PingResponse
  with the samples of the next ping in the file (looping); Time/Status/Echo answered.

Usage: python tools/sonar_emulator.py FILE.son [--port 1149] [--compress] [-v]
"""
import argparse
import os
import socket
import struct
import sys
import threading
import time
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from opensonar.son import SonFile  # noqa: E402

from opensonar.net import (DISCOVERY_GROUP, PORT, ECHO_REQ, ECHO_RES, SETTINGS_REQ,  # noqa: E402
                         SETTINGS_UPDATE, DISCOVERY_REQ, DISCOVERY_RES, TIME_REQ, TIME_RES,
                         STATUS_REQ, STATUS_RES, PING_REQ, PING_RES, message, xml_message,
                         read_header, read_xml, udp_envelope, compress)

NAMES = {1: "EchoReq", 2: "EchoRes", 3: "SettingsReq", 4: "SettingsUpdate", 5: "DiscoveryReq",
         6: "DiscoveryRes", 7: "SettingsCommit", 8: "TimeReq", 9: "TimeRes", 0x10: "StatusReq",
         0x11: "StatusRes", 0x20: "EnableTrigger", 0x21: "DisableTrigger", 0x22: "EnableTriggerOutput",
         0x30: "PingReq", 0x31: "PingRes", 0x1001: "FeatureListReq"}

ORIENTATION = {0: "RIGHT", 1: "LEFT", 0x10: "RIGHTALT", 0x11: "LEFTALT"}


# ---------------------------------------------------------------- the sonar


def crop(ping, start, stop):
    """Cuts the samples to the window requested by the client (inside the window stored in the .son).

    Samples are uniform in time between the ping's range_start and range_stop (SON_FORMAT.md).
    """
    r0, r1 = float(ping.range_start), float(ping.range_stop)
    n = ping.signals[0].size
    a = max(r0, min(float(start), r1))
    b = max(a, min(float(stop), r1))
    i0, i1 = round((a - r0) / (r1 - r0) * n), round((b - r0) / (r1 - r0) * n)
    if (i0, i1) == (0, n):
        return ping.signals, r0, r1
    return [s[i0:i1] for s in ping.signals], r0 + i0 * (r1 - r0) / n, r0 + i1 * (r1 - r0) / n


class Emulator:
    def __init__(self, path, compress_signal=False, verbose=False, clock_offset=0.0, omit=(), iq=False):
        self.son = SonFile(path)
        self.head = self.son.heads[0]
        self.compress = compress_signal
        self.iq = iq          # sends I/Q; the sample .son files only have I, so Q = 4095 − I (tests)
        self.tcp_port = None
        self.verbose = verbose
        self.clock_offset = clock_offset   # s added to the clock reported in TimeResponse
        self.omit = list(omit)               # "Elem" or "Elem@attribute" to remove from <Sonar> (tests)
        self.next_ping = 0
        p0 = self.son.ping(0)
        # state the client can adjust
        self.settings = dict(WindowStart=p0.range_start, WindowStop=p0.range_stop,
                             Gain=self.head.gain, TVG=self.head.tvg_slope,
                             SoundSpeed=self.head.sound_speed, PingInterval=0)
        self.lock = threading.Lock()

    quiet = False   # tests

    def log(self, *a):
        if self.quiet:
            return
        print(time.strftime("%H:%M:%S"), *a, flush=True)

    # --- configuration XML (SettingsUpdate) ---
    def sonar_xml(self):
        c, b = self.head, self.head.raw
        attachments = []
        sonar = ET.Element("Sonar", SerialNumber=str(self.son.serial or "0"), Mode="Normal")
        if self.son.sonar_type is not None:
            sonar.set("Type", str(self.son.sonar_type))
        if self.son.name:
            ET.SubElement(sonar, "Name").text = self.son.name
        ET.SubElement(sonar, "Revision", Firmware=str(self.son.firmware or 0), FPGA=str(self.son.fpga or 0))
        head = ET.SubElement(sonar, "Head", id="0", Enabled="1",
                             CenterFreq=str(c.center_freq), RxSampFreq=str(b["u32_a"]),
                             TxSampFreq=str(b["u32_b"]), ElementSpacing=repr(float(b["f_a"])),
                             MaxRange=repr(float(c.range_max)), MinRange=repr(float(c.range_min)),
                             HeadType=str(b["u16_a"]))
        if "u16_bcdef" in b:
            head.set("MatchedFilterEnable", str(int(b["u16_bcdef"][0] != 0)))
            head.set("Points", str(b["u16_bcdef"][1]))
            head.set("WindowPoints", str(b["u16_bcdef"][2]))
        ET.SubElement(head, "Name").text = c.model
        ET.SubElement(head, "Settings", **{k: repr(float(v)) if isinstance(v, float) else str(v)
                                            for k, v in self.settings.items()})
        for t in c.transducers:
            el = ET.SubElement(head, "Transducer", id=str(t.index), HighFreq=str(t.high_freq),
                               LowFreq=str(t.low_freq), MountingAngle="0",
                               Orientation=ORIENTATION.get(t.flags, "RIGHT"))
            if t.oriented:
                ET.SubElement(el, "MountingOrientation",
                              **dict(zip(("X_Offset", "Y_Offset", "Z_Offset"), map(repr, t.position))),
                              **dict(zip(("X_Rotation", "Y_Rotation", "Z_Rotation"), map(repr, t.rotation))))
        # pulse: N interleaved complex values (re, im) as f32 BE; normalization: M f32 BE
        z = c.pulse
        pulse = np.empty(2 * len(z), ">f4")
        pulse[0::2], pulse[1::2] = z.real, z.imag
        attachments.append(pulse.tobytes())
        ET.SubElement(head, "Pulse", StartFreq=str(b["u32_d"]), StopFreq=str(b["u32_e"]),
                      TBW=repr(float(b["f_g"])), Samples=str(len(z)), Attachment=str(len(attachments) - 1))
        attachments.append(np.asarray(c.normalization, ">f4").tobytes())
        ET.SubElement(head, "Normalization", Len=str(len(c.normalization)), Attachment=str(len(attachments) - 1))
        for target in self.omit:
            elem, _, attr = target.partition("@")
            for parent in [sonar] + list(sonar.iter()):
                for child in list(parent):
                    if child.tag == elem and not attr:
                        parent.remove(child)
                    elif child.tag == elem and attr in child.attrib:
                        del child.attrib[attr]
            if elem == "Sonar" and attr in sonar.attrib:
                del sonar.attrib[attr]
        return sonar, attachments

    def apply_settings(self, root):
        for s in root.iter("Settings"):
            with self.lock:
                for k, v in s.attrib.items():
                    old = self.settings.get(k)
                    self.settings[k] = float(v) if "." in v or k in ("WindowStart", "WindowStop") else int(v)
                    self.log(f"setting {k}: {old} → {self.settings[k]}")

    # --- ping ---
    def ping_xml(self, head):
        with self.lock:
            i = self.next_ping
            self.next_ping = (i + 1) % self.son.n_pings()
            st = dict(self.settings)
        p = self.son.ping(i)
        signals, r0, r1 = crop(p, st["WindowStart"], st["WindowStop"])
        n = signals[0].size
        # like the real sonar, the ping time is in the sonar's own clock (clock_offset ahead of the PC)
        ping = ET.Element("Ping", Head=str(head), Number=str(p.number), Timestamp=repr(p.timestamp + self.clock_offset),
                          SoundSpeed=str(st["SoundSpeed"]), WindowStart=repr(r0),
                          WindowStop=repr(r1), Gain=repr(float(st["Gain"])),
                          TVG=repr(float(st["TVG"])))
        data = ET.SubElement(ping, "Data", Samples=str(n))
        attachments = []
        for t, s in zip(self.head.transducers, signals):
            if self.iq:
                q = (4095 - s.astype(np.int32)).astype(np.uint16)
                enc = compress if self.compress else (lambda x: x)
                attachments += [enc(s).astype("<u2").tobytes(), enc(q).astype("<u2").tobytes()]
                suf = "Compressed" if self.compress else ""
                ET.SubElement(data, "Transducer", ID=str(t.index), ByteOrder="LITTLE",
                              **{f"I_Attachment{suf}": str(len(attachments) - 2),
                                 f"Q_Attachment{suf}": str(len(attachments) - 1)})
            elif self.compress:
                attachments.append(compress(s).astype("<u2").tobytes())
                ET.SubElement(data, "Transducer", ID=str(t.index),
                              AttachmentCompressed=str(len(attachments) - 1), ByteOrder="LITTLE")
            else:
                attachments.append(s.astype("<u2").tobytes())
                ET.SubElement(data, "Transducer", ID=str(t.index), Attachment=str(len(attachments) - 1),
                              ByteOrder="LITTLE")
        return i, ping, attachments

    # --- TCP ---
    def serve_client(self, conn, addr):
        self.log(f"client {addr[0]}:{addr[1]} connected")
        buf = b""
        try:
            while True:
                d = conn.recv(65536)
                if not d:
                    break
                buf += d
                while len(buf) >= 4:
                    size = struct.unpack_from(">I", buf)[0]
                    if size < 14:
                        raise ValueError(f"message with size {size}")
                    if len(buf) < size:
                        break
                    msg, buf = buf[:size], buf[size:]
                    reply = self.handle(msg)
                    if reply:
                        conn.sendall(reply)
        except (OSError, ValueError) as e:
            self.log(f"error with {addr[0]}: {e}")
        finally:
            conn.close()
            self.log(f"client {addr[0]}:{addr[1]} disconnected")

    def handle(self, msg):
        _size, version, kind, ident = read_header(msg)
        body = msg[14:]
        if self.verbose:
            self.log(f"← {NAMES.get(kind, hex(kind))} id={ident} version={version} {len(body)} B")
        if kind == SETTINGS_REQ:
            mask, head = struct.unpack_from(">IB", body) if len(body) >= 5 else (0xFFFFFFFF, 0xFF)
            root, attachments = self.sonar_xml()
            self.log(f"SettingsRequest mask={mask:#x} head={head} → <Sonar> ({len(attachments)} attachments)")
            return xml_message(SETTINGS_UPDATE, root, attachments, ident)
        if kind == SETTINGS_UPDATE:
            root, _ = read_xml(body)
            if self.verbose:
                self.log("  " + ET.tostring(root, encoding="unicode"))
            self.apply_settings(root)
            return None
        if kind == PING_REQ:
            head, flags, param = struct.unpack_from(">III", body)
            i, root, attachments = self.ping_xml(head)
            if self.verbose:
                self.log(f"PingRequest head={head} flags={flags:#x} param={param:#x} → ping {i}")
            return xml_message(PING_RES, root, attachments, ident)
        if kind == TIME_REQ:
            now = time.time() + self.clock_offset
            return message(TIME_RES, body[:6] + struct.pack(">IH", int(now), int(now % 1 * 1000)), ident)
        if kind == STATUS_REQ:
            return xml_message(STATUS_RES, ET.Element("Status", temp="20"), (), ident)
        if kind == ECHO_REQ:
            return message(ECHO_RES, b"", ident)
        self.log(f"message ignored: {NAMES.get(kind, hex(kind))} ({len(body)} B)")
        return None

    def serve_tcp(self, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(4)
        self.tcp_port = srv.getsockname()[1]
        self.log(f"TCP {port}: {self.head.model}, {len(self.head.transducers)} transducers, "
                 f"{self.son.n_pings()} pings{' (compressed)' if self.compress else ''}")
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self.serve_client, args=(conn, addr), daemon=True).start()

    # --- discovery ---
    def serve_discovery(self, tcp_port, stop=None):
        """Answers discovery until `stop` (threading.Event) is set."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", PORT))
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         socket.inet_aton(DISCOVERY_GROUP) + socket.inet_aton("0.0.0.0"))
        except OSError as e:
            self.log(f"no discovery multicast: {e}")
        s.settimeout(0.2)
        counter = 0
        while not (stop and stop.is_set()):
            try:
                d, addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            if len(d) < 8 + 14:
                continue
            _size, _v, kind, ident = read_header(d[8:])
            if kind != DISCOVERY_REQ:
                continue
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect((addr[0], 1))
            host = probe.getsockname()[0]
            probe.close()
            root = ET.Element("DiscoveryResponse", Host=host, Port=str(tcp_port), UUID="opensonar-emulator",
                              MulticastIP="0.0.0.0", MulticastPort="0")
            s.sendto(udp_envelope(counter, xml_message(DISCOVERY_RES, root, (), ident)), addr)
            counter += 1
            self.log(f"discovery from {addr[0]}:{addr[1]} → {host}:{tcp_port}")
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help=".son with the pings to send")
    ap.add_argument("--port", type=int, default=PORT, help="TCP port (default 1149)")
    ap.add_argument("--compress", action="store_true", help="send the signal with the 5/7-bit delta codec")
    ap.add_argument("--iq", action="store_true", help="send I/Q channels (synthetic Q = 4095 − I)")
    ap.add_argument("--clock-offset", type=float, default=0.0,
                    help="advance the sonar clock by S seconds (clients correct the ping times)")
    ap.add_argument("--omit", default="", help="comma list of Elem or Elem@attribute to drop from the configuration XML (tests)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every message")
    args = ap.parse_args(argv)
    emu = Emulator(args.file, args.compress, args.verbose, args.clock_offset,
                   [x for x in args.omit.split(",") if x], args.iq)
    threading.Thread(target=emu.serve_discovery, args=(args.port,), daemon=True).start()
    try:
        emu.serve_tcp(args.port)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
