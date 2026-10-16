"""Native network driver for P900 and M900 sonars (protocol in docs/PROTOCOL.md).

Speaks the protocol directly, multicast discovery, TCP 1149, configuration
(<Sonar>), range/gain/TVG adjustments and pings. Pings become the same `Ping` the .son reader
produces, so `opensonar.image` renders them unchanged.

    with NetSonar("192.168.1.45") as s:
        s.adjust_range(0, 1.0, 20.0)
        img, geo = image.xy(s.heads[0], s.ping(0))
"""
import socket
import struct
import threading
import time
import xml.etree.ElementTree as ET

import numpy as np

from .son import HeadConfig, OpenSonarError, Ping, Transducer

PORT = 1149
DISCOVERY_GROUP = "224.192.45.34"

# message types (PROTOCOL.md §3.3)
ECHO_REQ, ECHO_RES, SETTINGS_REQ, SETTINGS_UPDATE = 1, 2, 3, 4
DISCOVERY_REQ, DISCOVERY_RES, SETTINGS_COMMIT, TIME_REQ, TIME_RES = 5, 6, 7, 8, 9
STATUS_REQ, STATUS_RES, PING_REQ, PING_RES = 0x10, 0x11, 0x30, 0x31

MASK_ALL = 0x2F              # sonar attributes, heads, pulse, normalization, network
ALL_HEADS = 0xFF
PING_FLAGS = 0x21            # ping request flags (PROTOCOL.md §4.3)

ORIENTATION = {"RIGHT": 0, "LEFT": 1, "RIGHTALT": 0x10, "LEFTALT": 0x11}


class NetError(OpenSonarError):
    pass


# ---------------------------------------------------------------- messages (§3)

def blob(b):
    return struct.pack(">I", len(b)) + b


def message(kind, body=b"", ident=0):
    """14-byte header: total size, version 1, type, reserved, ID."""
    return struct.pack(">IHHHI", 14 + len(body), 1, kind, 0, ident) + body


def xml_message(kind, element, attachments=(), ident=0):
    """Every XML message has a <message> root (§3.4)."""
    root = ET.Element("message")
    root.append(element)
    return message(kind, blob(ET.tostring(root, encoding="utf-8")) + b"".join(blob(a) for a in attachments), ident)


def read_header(b):
    """→ (size, version, type, ID)."""
    size, version, kind, _res, ident = struct.unpack_from(">IHHHI", b)
    return size, version, kind, ident


def read_xml(body):
    """XML message body → (element inside <message>, [attachments])."""
    n = struct.unpack_from(">I", body)[0]
    root = ET.fromstring(body[4:4 + n])
    attachments, pos = [], 4 + n
    while pos + 4 <= len(body):
        m = struct.unpack_from(">I", body, pos)[0]
        attachments.append(body[pos + 4:pos + 4 + m])
        pos += 4 + m
    elem = root[0] if root.tag == "message" and len(root) else root
    return elem, attachments


def udp_envelope(counter, msg):
    """8-byte envelope of the plain UDP messages (§3.2)."""
    return struct.pack(">IHH", counter, 0, 1) + msg


# ---------------------------------------------------------------- 5/7-bit delta codec (§7)

def decompress(words, n):
    """Decodes the delta codec into n u16 samples (missing → 0x800; extra → cut)."""
    w = np.asarray(words, np.uint16)
    out = np.empty(3 * len(w) + 1, np.uint16)
    k, mode, base = 0, 0, 0
    for v in w.tolist():
        if not v & 0x8000:
            m = (v >> 12) & 7
            if m == 4 or m == 6:
                mode, base = (5 if m == 4 else 7), (v & 0x1FFF) * 2
            else:
                out[k] = v
                k += 1
        elif mode == 5:
            out[k:k + 3] = (base + (v & 31), base + (v >> 5 & 31), base + (v >> 10 & 31))
            k += 3
        elif mode == 7:
            out[k:k + 2] = (base + (v & 0x7F), base + (v >> 8 & 0x7F))
            k += 2
        else:
            raise NetError(f"delta word {v:#06x} without a mode")
    s = out[:min(k, n)]
    return s if k >= n else np.concatenate([s, np.full(n - k, 0x800, np.uint16)])


def compress(samples):
    """Encodes 12-bit samples in the same format (used by the emulator and the tests)."""
    a = np.asarray(samples, np.int64)
    out, i, n = [], 0, len(a)
    mode = base = None
    while i < n:
        done = False
        for bits, k, code in ((5, 3, 0x4000), (7, 2, 0x6000)):
            block = a[i:i + k]
            if len(block) < k:
                continue
            lim = 1 << bits
            b = int(block.min()) // 2 * 2
            if (block - b).max() >= lim:
                continue
            if mode != bits or (block - base).min() < 0 or (block - base).max() >= lim:
                mode, base = bits, b
                out.append(code | base // 2)
            d = (block - base).tolist()
            out.append(0x8000 | d[0] | d[1] << (5 if bits == 5 else 8) | (d[2] << 10 if bits == 5 else 0))
            i += k
            done = True
            break
        if not done:
            v = int(a[i])
            if v >= 0x8000 or (v >> 12) & 7 in (4, 6):
                raise ValueError(f"sample {v:#x} does not fit as a literal")
            out.append(v)
            i += 1
    return np.array(out, np.uint16)


# ---------------------------------------------------------------- configuration XML (§5)

def _f(el, name, default=0.0):
    v = el.get(name)
    return float(v) if v is not None else default


def _i(el, name, default=0):
    v = el.get(name)
    return int(float(v)) if v is not None else default


def _f32_attachment(attachments, el, expected=None):
    k = el.get("Attachment")
    if k is None or int(k) >= len(attachments):
        raise NetError(f"<{el.tag}> without an attachment")
    v = np.frombuffer(attachments[int(k)], ">f4").astype(np.float32)
    if expected is not None and v.size != expected:
        raise NetError(f"<{el.tag}>: expected {expected} values, got {v.size}")
    return v


def head_from_xml(head, attachments):
    """<Head> → HeadConfig, with the same fields the .son reader fills (mapping in §5)."""
    st = head.find("Settings")
    st = st if st is not None else ET.Element("Settings")
    name = head.findtext("Name") or head.get("Name") or f"Head {head.get('id')}"
    pulse = head.find("Pulse")
    if pulse is None:
        raise NetError(f"head {head.get('id')} without <Pulse>")
    p = _f32_attachment(attachments, pulse, 2 * _i(pulse, "Samples"))
    norm = head.find("Normalization")
    normalization = _f32_attachment(attachments, norm, _i(norm, "Len")) if norm is not None else np.ones(128, np.float32)
    raw = {
        "u16_a": _i(head, "HeadType"), "u32_a": _i(head, "RxSampFreq"), "u32_b": _i(head, "TxSampFreq"),
        "f_a": np.float32(_f(head, "ElementSpacing")), "u32_d": _i(pulse, "StartFreq"),
        "u32_e": _i(pulse, "StopFreq"), "f_g": np.float32(_f(pulse, "TBW")),
        "u16_bcdef": [_i(head, "MatchedFilterEnable"), _i(head, "Points"), _i(head, "WindowPoints"), 0, 0],
    }
    cfg = HeadConfig(name, _f(head, "MinRange"), _f(head, "MaxRange"), _f(st, "Gain"), _f(st, "TVG"),
                     _i(head, "CenterFreq"), _i(st, "SoundSpeed", 1500),
                     (p[0::2] + 1j * p[1::2]).astype(np.complex64), normalization, raw)
    for t in head.findall("Transducer"):
        tr = Transducer(_i(t, "id"), _i(t, "HighFreq"), _i(t, "LowFreq"),
                        ORIENTATION.get((t.get("Orientation") or "RIGHT").upper(), 0))
        mo = t.find("MountingOrientation")
        if mo is None:
            mo = next((x for x in t if x.tag.lower() == "mountingorientation"), None)
        if mo is not None:
            tr.position = tuple(_f(mo, k) for k in ("X_Offset", "Y_Offset", "Z_Offset"))
            tr.rotation = tuple(_f(mo, k) for k in ("X_Rotation", "Y_Rotation", "Z_Rotation"))
            tr.oriented = True
        cfg.transducers.append(tr)
    cfg.transducers.sort(key=lambda t: t.index)
    return cfg


# ---------------------------------------------------------------- PingResponse (§6)

def _samples(attachments, k, little, n, compressed):
    b = attachments[int(k)]
    v = np.frombuffer(b, "<u2" if little else ">u2").astype(np.uint16)
    return decompress(v, n) if compressed else v


def ping_from_xml(el, attachments, head, clock_offset=0.0):
    """<Ping> → Ping (I channels in `signals`, Q in `signals_q`, in the head's transducer order)."""
    data = el.find("Data")
    if data is None:
        raise NetError("ping without <Data>")
    n = _i(data, "Samples")
    channels = {}
    for t in data.findall("Transducer"):
        little = (t.get("ByteOrder") or "").upper() == "LITTLE"
        i = q = None
        if t.get("AttachmentCompressed") is not None:
            i = _samples(attachments, t.get("AttachmentCompressed"), little, n, True)
        elif t.get("I_AttachmentCompressed") is not None:
            i = _samples(attachments, t.get("I_AttachmentCompressed"), little, n, True)
            q = _samples(attachments, t.get("Q_AttachmentCompressed"), little, n, True)
        elif t.get("Attachment") is not None:
            i = _samples(attachments, t.get("Attachment"), little, n, False)
        elif t.get("I_Attachment") is not None:
            i = _samples(attachments, t.get("I_Attachment"), little, n, False)
            q = _samples(attachments, t.get("Q_Attachment"), little, n, False)
        channels[_i(t, "ID")] = (i, q if q is not None else np.zeros(0, np.uint16))
    empty = (np.zeros(0, np.uint16), np.zeros(0, np.uint16))
    order = [channels.get(t.index, empty) for t in head.transducers] if head.transducers else list(channels.values())
    ts = round(_f(el, "Timestamp") - clock_offset, 3)       # rounded to ms (§6)
    return Ping(_i(el, "Number"), ts, -time.timezone, _f(el, "WindowStart"), _f(el, "WindowStop"),
                [c[0] for c in order], [c[1] for c in order], pad_to_window=False)


# ---------------------------------------------------------------- discovery (§8)

def _local_ips():
    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 1))      # sends nothing; only picks the outgoing interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        import fcntl
        for _, name in socket.if_nameindex():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                r = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))   # SIOCGIFADDR
                ips.add(socket.inet_ntoa(r[20:24]))
            except OSError:
                pass
            finally:
                s.close()
    except (ImportError, AttributeError, OSError):
        pass
    return sorted(ips)


def discover(timeout=2.0, port=PORT):
    """Sends a DiscoveryRequest on every interface → list of dicts (Host, Port, UUID…), one per UUID."""
    found, socks = {}, []
    for ip in _local_ips():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            s.bind((ip, 0))
            s.sendto(udp_envelope(0, message(DISCOVERY_REQ)), (DISCOVERY_GROUP, port))
            s.setblocking(False)
            socks.append(s)
        except OSError:
            s.close()
    end = time.time() + timeout
    while time.time() < end and socks:
        for s in socks:
            try:
                d, addr = s.recvfrom(4096)
            except (BlockingIOError, OSError):
                continue
            if len(d) < 22 or read_header(d[8:])[2] != DISCOVERY_RES:
                continue
            el, _ = read_xml(d[22:])
            info = dict(el.attrib)
            info.setdefault("Host", addr[0])
            found.setdefault(info.get("UUID") or info["Host"], info)
        time.sleep(0.02)
    for s in socks:
        s.close()
    return list(found.values())


# ---------------------------------------------------------------- driver

class NetSonar:
    """TCP connection to a sonar (real or emulated). Reader thread + wait by message ID (§4)."""

    def __init__(self, host, port=PORT, timeout=10.0, sync_clock=True):
        if ":" in host:
            host, p = host.rsplit(":", 1)
            port = int(p)
        self.host, self.port, self.timeout = host, port, timeout
        try:
            self._sock = socket.create_connection((host, port), timeout=5.0)
        except OSError as e:
            raise NetError(f"cannot connect to the sonar at {host}:{port}: {e}") from None
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(None)
        self._next_id = 1
        self._lock = threading.Lock()
        self._waiting = {}                # id → [Event, response]
        self._closed = False
        self._error = None
        self.clock_offset = 0.0           # sonar clock − local clock (s)
        self.heads, self.config = [], None
        self.sonar_type = self.name = self.serial = self.firmware = None
        self.fpga = 0
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            self._configure(self.request(SETTINGS_REQ, struct.pack(">IB", MASK_ALL, ALL_HEADS)))
            if sync_clock:
                self.sync_clock()
        except Exception:
            self.close()
            raise

    # --- context ---
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    # --- transport ---
    def _recv(self, n):
        b = b""
        while len(b) < n:
            d = self._sock.recv(n - len(b))
            if not d:
                raise NetError("connection closed by the sonar")
            b += d
        return b

    def _read_loop(self):
        try:
            while not self._closed:
                hdr = self._recv(4)
                size = struct.unpack(">I", hdr)[0]
                if size < 14:
                    raise NetError(f"message with size {size}")
                msg = hdr + self._recv(size - 4)
                _s, version, kind, ident = read_header(msg)
                if version != 1:
                    continue
                with self._lock:
                    target = self._waiting.get(ident) if ident else None
                if target:
                    target[1] = (kind, msg[14:])
                    target[0].set()
                elif kind == SETTINGS_UPDATE:           # unsolicited update from the sonar
                    try:
                        self._configure((kind, msg[14:]), partial=True)
                    except Exception:
                        pass
        except Exception as e:  # noqa: BLE001 — passed on to whoever is waiting
            if not self._closed:
                self._error = e
            with self._lock:
                for target in self._waiting.values():
                    target[0].set()

    def send(self, kind, body=b"", ident=0):
        self._sock.sendall(message(kind, body, ident))

    def request(self, kind, body=b"", timeout=None):
        """Sends with a new ID and waits for the response with the same ID → (type, body)."""
        with self._lock:
            ident = self._next_id
            self._next_id += 1
            target = self._waiting[ident] = [threading.Event(), None]
        try:
            self.send(kind, body, ident)
            if not target[0].wait(timeout or self.timeout):
                raise NetError(f"no response to request {kind:#x} (ID {ident})")
            if target[1] is None:
                raise NetError(f"connection lost: {self._error}")
            return target[1]
        finally:
            with self._lock:
                self._waiting.pop(ident, None)

    # --- configuration ---
    def _configure(self, response, partial=False):
        kind, body = response
        if kind != SETTINGS_UPDATE:
            raise NetError(f"expected SettingsUpdate, got type {kind:#x}")
        sonar, attachments = read_xml(body)
        if sonar.tag != "Sonar":
            raise NetError(f"expected <Sonar>, got <{sonar.tag}>")
        if partial:
            for head in sonar.findall("Head"):
                i = _i(head, "id")
                st = head.find("Settings")
                if st is not None and i < len(self.heads):
                    self._apply_settings(self.heads[i], st.attrib)
            return
        self.config = sonar
        self.sonar_type = _i(sonar, "Type") if sonar.get("Type") is not None else None
        self.name = sonar.findtext("Name") or ""
        self.serial = sonar.get("SerialNumber")
        rev = sonar.find("Revision")
        self.firmware = _i(rev, "Firmware") if rev is not None else None
        self.fpga = _i(rev, "FPGA") if rev is not None else 0
        heads = sorted(sonar.findall("Head"), key=lambda h: _i(h, "id"))
        if not heads:
            raise NetError("the sonar reported no heads")
        self.heads = [head_from_xml(h, attachments) for h in heads]

    @staticmethod
    def _apply_settings(cfg, attrs):
        if "Gain" in attrs:
            cfg.gain = float(attrs["Gain"])
        if "TVG" in attrs:
            cfg.tvg_slope = float(attrs["TVG"])
        if "SoundSpeed" in attrs:
            cfg.sound_speed = int(float(attrs["SoundSpeed"]))

    def sync_clock(self):
        """TimeRequest: offset = sonar clock − local midpoint; ping times are corrected by it (§4.4)."""
        t0 = time.time()
        kind, body = self.request(TIME_REQ, struct.pack(">IH", int(t0), int(t0 % 1 * 1000)))
        t1 = time.time()
        if kind != TIME_RES or len(body) < 12:
            raise NetError("invalid TimeResponse")
        s, ms = struct.unpack_from(">IH", body, 6)
        self.clock_offset = (s + ms / 1000.0) - (t0 + t1) / 2
        return self.clock_offset

    def _settings(self, head, **attrs):
        sonar = ET.Element("Sonar")
        h = ET.SubElement(sonar, "Head", id=str(head))
        ET.SubElement(h, "Settings", **{k: f"{v:.6f}" if isinstance(v, float) else str(v)
                                        for k, v in attrs.items()})
        self._sock.sendall(xml_message(SETTINGS_UPDATE, sonar))

    def adjust_range(self, head, start, stop):
        """Settings update with the range: the sonar does not reply (§4.2)."""
        self._settings(head, WindowStart=float(start), WindowStop=float(stop))

    def adjust_gain(self, head, gain):
        self._settings(head, Gain=float(gain))
        self.heads[head].gain = float(gain)

    def adjust_tvg(self, head, tvg):
        self._settings(head, TVG=float(tvg))
        self.heads[head].tvg_slope = float(tvg)

    # --- pings ---
    def ping(self, head=0, flags=PING_FLAGS, parameter=0xFFFFFFFF):
        """PingRequest over TCP → Ping (§4.3)."""
        kind, body = self.request(PING_REQ, struct.pack(">III", head, flags, parameter))
        if kind != PING_RES:
            raise NetError(f"expected PingResponse, got type {kind:#x}")
        el, attachments = read_xml(body)
        if el.tag != "Ping":
            raise NetError(f"expected <Ping>, got <{el.tag}>")
        cfg = self.heads[_i(el, "Head", head)]
        if el.get("SoundSpeed") is not None:
            cfg.sound_speed = _i(el, "SoundSpeed")
        return ping_from_xml(el, attachments, cfg, self.clock_offset)

    def pings(self, head=0, n=None):
        """Generator of continuous pings."""
        k = 0
        while n is None or k < n:
            yield self.ping(head)
            k += 1
